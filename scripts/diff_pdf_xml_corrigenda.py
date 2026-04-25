"""Diff PDF vs XML text for corrigendum-affected amendments in statute.zip.

The PDFs in statute.zip have corrigenda silently applied; the XMLs do NOT.
This gives us free ground truth for validating the corrigendum patch pipeline.

Concrete proof case: amendment 1184/2018 (johtolause correction)
  XML: "17, 18 a–18 e"  (§18 missing — erroneous source)
  PDF: "17, 18, 18 a–18 e" (§18 present — corrected)

Usage:
    uv run python scripts/diff_pdf_xml_corrigenda.py
    uv run python scripts/diff_pdf_xml_corrigenda.py --output .tmp/pdf_xml_diffs.jsonl
    uv run python scripts/diff_pdf_xml_corrigenda.py --limit 50
    uv run lawvm corrigendum diff-pdf
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from lawvm.finland.corrigendum_records import load_patch_records

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent

_STATUTE_ZIP = _LAWVM_DIR / "data" / "zips" / "statute.zip"
_OFFICIAL_RECORDS = _LAWVM_DIR / "data" / "finland" / "corrigendum_official_fi.jsonl"
_DEFAULT_OUTPUT = _LAWVM_DIR / ".tmp" / "pdf_xml_diffs.jsonl"

# AKN namespace used in Finnish statute XML
_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Marker phrases that signal the start of the substantive johtolause text in
# Finnish statute PDFs (after the SUOMEN SÄÄDÖSKOKOELMA header block).
_JOHTOLAUSE_ANCHORS = [
    "päätöksen mukaisesti",     # most VN asetukset
    "esityksestä säädetään",    # eduskuntalait
    "päätöksensä mukaisesti",
    "suostumuksella säädetään",
    "ehdotuksesta säädetään",
    "mukaisesti säädetään",
    "mukaisesti muutetaan",
    "mukaisesti kumotaan",
    "mukaisesti lisätään",
    "valtioneuvoston",          # broad fallback
]

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(rb"<[^>]+>")


def _normalize(text: str) -> str:
    """Collapse whitespace and strip."""
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _pdf_bytes_to_text(pdf_bytes: bytes) -> Optional[str]:
    """Run pdftotext on in-memory bytes, return plain text or None."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name
        result = subprocess.run(
            ["pdftotext", tmp_path, "-"],
            capture_output=True,
            timeout=30,
        )
        Path(tmp_path).unlink(missing_ok=True)
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _extract_pdf_preamble(pdf_bytes: bytes, chars: int = 800) -> Optional[str]:
    """Return the substantive johtolause text from the PDF, skipping the header banner.

    Finnish statute PDFs start with a SUOMEN SÄÄDÖSKOKOELMA header block.  We
    skip past that and return the first `chars` normalised characters of the
    actual legal text, aligned with what the XML preamble contains.
    """
    text = _pdf_bytes_to_text(pdf_bytes)
    if text is None:
        return None
    norm = _normalize(text)
    # Try each anchor phrase to find where the substantive text begins
    norm_lower = norm.lower()
    for anchor in _JOHTOLAUSE_ANCHORS:
        idx = norm_lower.find(anchor.lower())
        if idx != -1:
            # Back up a little to capture the clause subject ("Eduskunnan …" etc.)
            start = max(0, idx - 100)
            return _normalize(norm[start:start + chars])
    # Fallback: return from beginning (header noise included)
    return norm[:chars]


# ---------------------------------------------------------------------------
# XML extraction
# ---------------------------------------------------------------------------

def _extract_xml_preamble(xml_bytes: bytes, chars: int = 600) -> Optional[str]:
    """Extract text content of <preamble> element from AKN XML.

    Falls back to stripping all tags from the first 2 KB of the file if
    lxml is unavailable or the element is missing.
    """
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
        # Try AKN namespace first
        preambles = root.findall(f".//{{{_AKN_NS}}}preamble")
        if not preambles:
            # Namespace-agnostic fallback
            preambles = root.findall(".//preamble")
        if preambles:
            text = etree.tostring(preambles[0], method="text", encoding="unicode")
            return _normalize(text)[:chars]
        # No <preamble> — fall through to tag-stripping
    except Exception:
        pass

    # Fallback: strip XML tags from raw bytes
    plain = _TAG_RE.sub(b" ", xml_bytes[:4096]).decode("utf-8", errors="replace")
    return _normalize(plain)[:chars]


def _extract_xml_body(xml_bytes: bytes) -> Optional[str]:
    """Extract normalised full body text from AKN XML (for non-preamble diffs)."""
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
        bodies = root.findall(f".//{{{_AKN_NS}}}body")
        if not bodies:
            bodies = root.findall(".//body")
        if bodies:
            text = etree.tostring(bodies[0], method="text", encoding="unicode")
            return _normalize(text)
    except Exception:
        pass
    # Fallback
    plain = _TAG_RE.sub(b" ", xml_bytes).decode("utf-8", errors="replace")
    return _normalize(plain)


# ---------------------------------------------------------------------------
# Amendment ID conversion
# ---------------------------------------------------------------------------

def _amendment_id_to_zip_paths(amendment_id: str) -> tuple[str, str]:
    """Convert NUM/YEAR amendment_id to zip paths for PDF and XML.

    Returns (pdf_path, xml_path).
    amendment_id format: "1184/2018" → year=2018, num=1184
    zip path: akn/fi/act/statute/{YEAR}/{NUM}/fin@/main.{pdf,xml}
    """
    num, year = amendment_id.split("/")
    base = f"akn/fi/act/statute/{year}/{num}/fin@/main"
    return f"{base}.pdf", f"{base}.xml"


# ---------------------------------------------------------------------------
# Corrigendum corpus queries
# ---------------------------------------------------------------------------

def _load_patches(records_path: Path) -> dict[str, list[dict]]:
    """Load all official Finnish corrigendum items keyed by amendment_id (NUM/YEAR).

    Returns: {amendment_id: [{"correction_type": ..., "wrong_text": ...,
                               "correct_text": ..., "verified_in_source": ...}, ...]}
    """
    if not records_path.exists():
        return {}
    rows = load_patch_records(records_path)

    result: dict[str, list[dict]] = {}
    for row in rows:
        amid = str(row.get("amendment_id") or "").strip()
        if not amid or str(row.get("lang") or "fi").strip() != "fi":
            continue
        result.setdefault(amid, []).append({
            "correction_type": str(row.get("correction_type") or ""),
            "wrong_text": str(row.get("wrong_text") or ""),
            "correct_text": str(row.get("correct_text") or ""),
            "verified_in_source": row.get("verified_in_source"),
        })
    return result


def _get_distinct_amendment_ids(records_path: Path) -> list[str]:
    """Return all distinct amendment_ids from the official corrigendum text corpus."""
    return sorted(_load_patches(records_path))


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def _check_patch_match(
    pdf_text: str,
    xml_text: str,
    patches: list[dict],
) -> tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Check whether a PDF↔XML diff is explained by known patches.

    Returns:
        (matches_patch_db, diff_summary, patch_wrong, patch_correct)
    """
    if not patches:
        return False, None, None, None

    for p in patches:
        wrong = _normalize(p["wrong_text"])
        correct = _normalize(p["correct_text"])
        if not wrong or not correct:
            continue
        # The XML should contain the wrong text; the PDF should contain the correct text.
        wrong_in_xml = wrong in xml_text.lower() or wrong in xml_text
        correct_in_pdf = correct in pdf_text.lower() or correct in pdf_text
        # Also try case-insensitive
        if not wrong_in_xml:
            wrong_in_xml = wrong.lower() in xml_text.lower()
        if not correct_in_pdf:
            correct_in_pdf = correct.lower() in pdf_text.lower()

        if wrong_in_xml and correct_in_pdf:
            return True, f"XML has {wrong!r}, PDF has {correct!r}", p["wrong_text"], p["correct_text"]

    return False, None, None, None


def _compute_diff_summary(pdf_text: str, xml_text: str) -> Optional[str]:
    """Produce a short human-readable summary of how PDF and XML differ.

    Works at character n-gram level — finds the first window where they diverge.
    """
    if not pdf_text or not xml_text:
        return None
    # Find first position of divergence
    min_len = min(len(pdf_text), len(xml_text))
    first_diff = min_len  # default: no diff within shared prefix
    for i in range(min_len):
        if pdf_text[i] != xml_text[i]:
            first_diff = i
            break

    if first_diff == min_len and len(pdf_text) == len(xml_text):
        return None  # identical

    ctx_start = max(0, first_diff - 30)
    ctx_end = min(first_diff + 60, min(len(pdf_text), len(xml_text)))

    pdf_ctx = pdf_text[ctx_start:ctx_end]
    xml_ctx = xml_text[ctx_start:ctx_end]

    if pdf_ctx == xml_ctx:
        # Length difference only
        return f"PDF length {len(pdf_text)} vs XML length {len(xml_text)}"

    return f"PDF: {pdf_ctx!r}  XML: {xml_ctx!r}"


# ---------------------------------------------------------------------------
# Per-amendment worker
# ---------------------------------------------------------------------------

def _process_amendment(
    amendment_id: str,
    zip_ref: zipfile.ZipFile,
    patches_for_amendment: list[dict],
) -> dict:
    """Process one amendment: extract PDF+XML texts, diff them, match against patches.

    Returns a result dict suitable for JSONL output.
    """
    pdf_path, xml_path = _amendment_id_to_zip_paths(amendment_id)
    zip_names = set(zip_ref.namelist())

    # --- Extract PDF ---
    pdf_preamble: Optional[str] = None
    pdf_missing = pdf_path not in zip_names
    if not pdf_missing:
        try:
            pdf_bytes = zip_ref.read(pdf_path)
            pdf_preamble = _extract_pdf_preamble(pdf_bytes)
        except Exception:
            pdf_preamble = None
            pdf_missing = True

    # --- Extract XML ---
    xml_preamble: Optional[str] = None
    xml_missing = xml_path not in zip_names
    if not xml_missing:
        try:
            xml_bytes = zip_ref.read(xml_path)
            xml_preamble = _extract_xml_preamble(xml_bytes)
        except Exception:
            xml_preamble = None
            xml_missing = True

    # --- Compute diff ---
    has_diff = False
    diff_summary: Optional[str] = None
    matches_patch_corpus = False
    patch_wrong: Optional[str] = None
    patch_correct: Optional[str] = None

    if pdf_preamble is not None and xml_preamble is not None:
        has_diff = pdf_preamble != xml_preamble
        if has_diff:
            diff_summary = _compute_diff_summary(pdf_preamble, xml_preamble)
            matches_patch_corpus, diff_summary_from_patch, patch_wrong, patch_correct = \
                _check_patch_match(pdf_preamble, xml_preamble, patches_for_amendment)
            if matches_patch_corpus and diff_summary_from_patch:
                diff_summary = diff_summary_from_patch

    return {
        "amendment_id": amendment_id,
        "pdf_path": pdf_path,
        "xml_path": xml_path,
        "pdf_missing": pdf_missing,
        "xml_missing": xml_missing,
        "pdf_preamble": pdf_preamble,
        "xml_preamble": xml_preamble,
        "has_diff": has_diff,
        "diff_summary": diff_summary,
        "matches_patch_corpus": matches_patch_corpus,
        "patch_corpus_wrong": patch_wrong,
        "patch_corpus_correct": patch_correct,
    }


# ---------------------------------------------------------------------------
# Thread-pool wrapper (pdftotext is I/O-bound; parallelise safely)
# ---------------------------------------------------------------------------

def _process_amendment_threadsafe(
    amendment_id: str,
    zip_path: Path,
    patches_for_amendment: list[dict],
) -> dict:
    """Open the zip independently per thread (ZipFile is not thread-safe)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return _process_amendment(amendment_id, zf, patches_for_amendment)
    except Exception as e:
        pdf_path, xml_path = _amendment_id_to_zip_paths(amendment_id)
        return {
            "amendment_id": amendment_id,
            "pdf_path": pdf_path,
            "xml_path": xml_path,
            "pdf_missing": True,
            "xml_missing": True,
            "pdf_preamble": None,
            "xml_preamble": None,
            "has_diff": False,
            "diff_summary": None,
            "matches_patch_corpus": False,
            "patch_corpus_wrong": None,
            "patch_corpus_correct": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    output_path: Path = _DEFAULT_OUTPUT,
    limit: Optional[int] = None,
    workers: int = 8,
    verbose: bool = False,
    db_path: Path = _OFFICIAL_RECORDS,
    zip_path: Path = _STATUTE_ZIP,
) -> None:
    """Run the PDF vs XML diff for all corrigendum-affected amendments."""
    if not zip_path.exists():
        print(f"ERROR: statute.zip not found at {zip_path}", file=sys.stderr)
        sys.exit(1)
    if not db_path.exists():
        print(f"ERROR: corrigendum official corpus not found at {db_path}", file=sys.stderr)
        print("Run: lawvm corrigendum classify", file=sys.stderr)
        sys.exit(1)

    # Load all classified items from the text corpus
    print(f"Loading corrigendum corpus from {db_path} ...")
    all_patches = _load_patches(db_path)
    amendment_ids = _get_distinct_amendment_ids(db_path)
    print(f"Found {len(amendment_ids)} distinct amendment_ids in corrigendum corpus")

    if limit:
        amendment_ids = amendment_ids[:limit]
        print(f"Limiting to first {limit} amendments")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Counters
    n_checked = 0
    n_pdf_missing = 0
    n_xml_missing = 0
    n_has_diff = 0
    n_matches_corpus = 0
    n_new_diff = 0               # diff found but NOT in corrigendum corpus
    n_corpus_not_confirmed = 0   # corpus item exists but diff NOT confirmed in PDF

    print(f"Diffing {len(amendment_ids)} amendments with {workers} workers ...")
    print(f"Output: {output_path}")

    with open(output_path, "w", encoding="utf-8") as out_fh:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _process_amendment_threadsafe,
                    amid,
                    zip_path,
                    all_patches.get(amid, []),
                ): amid
                for amid in amendment_ids
            }
            for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
                amid = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "amendment_id": amid,
                        "error": str(e),
                        "has_diff": False,
                        "matches_patch_db": False,
                        "pdf_missing": True,
                        "xml_missing": True,
                    }

                n_checked += 1
                if result.get("pdf_missing"):
                    n_pdf_missing += 1
                if result.get("xml_missing"):
                    n_xml_missing += 1
                if result.get("has_diff"):
                    n_has_diff += 1
                    if result.get("matches_patch_corpus"):
                        n_matches_corpus += 1
                    else:
                        n_new_diff += 1

                # Track DB entries not confirmed by PDF
                patches_for = all_patches.get(amid, [])
                if patches_for and not result.get("has_diff") and not result.get("pdf_missing") and not result.get("xml_missing"):
                    n_corpus_not_confirmed += 1

                out_fh.write(json.dumps(result, ensure_ascii=False) + "\n")

                if verbose and (result.get("has_diff") or result.get("error")):
                    status = "DIFF" if result.get("has_diff") else "ERROR"
                    match = " MATCHES_CORPUS" if result.get("matches_patch_corpus") else ""
                    diff_summary = str(result.get("diff_summary") or "")
                    print(f"  [{i:>4}] {amid:<12} {status}{match}  {diff_summary[:60]}")
                elif i % 100 == 0:
                    print(f"  ... {i}/{len(amendment_ids)} done")

    # Summary
    print()
    print("=== PDF vs XML Diff Summary ===")
    print(f"  Amendments checked          : {n_checked}")
    print(f"  PDF missing in zip          : {n_pdf_missing}")
    print(f"  XML missing in zip          : {n_xml_missing}")
    print(f"  PDF != XML (diff found)     : {n_has_diff}")
    print(f"    Matching corpus item      : {n_matches_corpus}")
    print(f"    New diff (not in corpus)  : {n_new_diff}")
    print(f"  Corpus items not confirmed  : {n_corpus_not_confirmed}")
    print(f"  Output written to           : {output_path}")

    if n_matches_corpus > 0:
        print(f"\n  Validation rate: {n_matches_corpus}/{len(amendment_ids)} ({100*n_matches_corpus/len(amendment_ids):.1f}%) amendments confirmed by PDF")

    if n_new_diff > 0:
        print(f"\n  NOTE: {n_new_diff} amendments show PDF!=XML diffs not explained by the corrigendum corpus.")
        print("        These may be corrigenda not yet classified. Inspect the JSONL for details.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args=None) -> None:
    parser = argparse.ArgumentParser(
        prog="diff_pdf_xml_corrigenda",
        description="Diff PDF vs XML text for corrigendum-affected amendments.",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        default=str(_DEFAULT_OUTPUT),
        help=f"output JSONL file (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="process only first N amendments (for testing)",
    )
    parser.add_argument(
        "--workers", type=int, default=8, metavar="N",
        help="ThreadPoolExecutor workers (default: 8)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="print each amendment with a diff",
    )
    parser.add_argument(
        "--db", metavar="PATH",
        default=str(_OFFICIAL_RECORDS),
        help=f"corrigendum official corpus path (default: {_OFFICIAL_RECORDS})",
    )
    parser.add_argument(
        "--zip", metavar="PATH",
        default=str(_STATUTE_ZIP),
        help=f"statute.zip path (default: {_STATUTE_ZIP})",
    )

    if args is None:
        parsed = parser.parse_args()
    else:
        # Called from lawvm CLI with pre-parsed args namespace
        parsed = args

    run(
        output_path=Path(getattr(parsed, "output", str(_DEFAULT_OUTPUT))),
        limit=getattr(parsed, "limit", None),
        workers=getattr(parsed, "workers", 8),
        verbose=getattr(parsed, "verbose", False),
        db_path=Path(getattr(parsed, "db", str(_OFFICIAL_RECORDS))),
        zip_path=Path(getattr(parsed, "zip", str(_STATUTE_ZIP))),
    )


if __name__ == "__main__":
    main()
