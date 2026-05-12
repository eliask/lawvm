#!/usr/bin/env python3
"""
Extract amendment-to-parent relationships from Finlex consolidated statutes.

This tool scans the Finlex consolidated ZIP file and builds a mapping between
amendment acts (muutoslait) and their parent statutes. This is a critical
discovery step for the LawVM Finland frontend, as it allows the grafter to
identify the full chain of patches required to reconstruct a Point-In-Time state.

In LawVM terms, this is part of the 'Source Fact Extraction' layer for Finland.
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple, Set, cast

import lxml.etree as etree
from functools import lru_cache

from lawvm.corpus_store import (
    CorpusStore,
    get_corpus_store,
)
from lawvm.finland.vts import extract_voimaantulo_repeals

# Pattern for /akn/fi/act/statute-consolidated/YEAR/NUMBER...
# or /akn/fi/act/statute/YEAR/NUMBER...
REF_PATTERN = re.compile(r'/akn/fi/act/statute(?:-consolidated)?/(\d{4})/(\d+(?:-\d+)?)')

# Canonical cache path — stored in .cache (gitignored)
_DEFAULT_CACHE_CSV = Path(".cache/finland/amendment_parents.csv")
_CSV_HEADER = ["amendment_id", "parent_id", "edge_kind"]


def _append_amendment_index_diagnostic(
    diagnostics_out: list[dict[str, object]] | None,
    *,
    rule_id: str,
    phase: str,
    family: str,
    reason: str,
    detail: dict[str, object],
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": rule_id,
            "phase": phase,
            "family": family,
            "reason": reason,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            **detail,
        }
    )


def _make_statute_id(year: str, num_raw: str) -> str:
    """Normalize statute ID to YYYY/NUMBER format."""
    if '-' in num_raw:
        # Preserve sub-numbering for older statutes (e.g., 1889/39-001)
        return f"{year}/{num_raw}"
    return f"{year}/{int(num_raw)}"


def _normalize_source_citation_id(raw: str, source_year: int) -> str | None:
    """Normalize textual source citations like ``506/86`` or ``506/1986``."""
    raw = re.sub(r"\s+", "", (raw or ""))
    m = re.fullmatch(r"(\d{1,4})/(\d{2,4})", raw)
    if not m:
        return None
    left, right = m.groups()
    num = int(left)
    if len(right) == 4:
        return f"{right}/{num}"
    year_two = int(right)
    source_century = (source_year // 100) * 100
    full_year = source_century + year_two
    if full_year > source_year:
        full_year -= 100
    return f"{full_year}/{num}"


def _extract_explicit_cross_statute_vts_parents(
    xml_data: bytes,
    amendment_id: str,
    *,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> Set[str]:
    """Extract explicit parent statute IDs mentioned in VTS cross-statute clauses.

    This supplements the direct ``amendedBy``-based index with explicit source
    citations from entry-into-force / voimaantulo clauses such as:

      "Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan ..."
      "Tällä lailla kumotaan ... (785/1992) 11 § ..."

    The extractor is intentionally conservative: explicit statute citations only.
    """
    try:
        tree = etree.fromstring(xml_data)
    except etree.XMLSyntaxError as exc:
        _append_amendment_index_diagnostic(
            diagnostics_out,
            rule_id="fi_amendment_index_source_vts_xml_parse_failed",
            phase="parse",
            family="source_pathology",
            reason="Finland amendment index skipped source VTS extraction because source XML was not well-formed.",
            detail={
                "amendment_id": amendment_id,
                "edge_kind": "source_vts_explicit",
                "exception_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return set()

    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (ValueError, IndexError):
        return set()

    cited_ids: Set[str] = set()
    elements = tree.findall(".//{*}section") + tree.findall('.//{*}hcontainer[@eId="entryIntoForce"]')
    seen_texts: Set[str] = set()
    for el in elements:
        text = re.sub(r"\s+", " ", etree.tostring(el, method="text", encoding="unicode")).strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        lower = text.lower()
        is_relevant = (
            "kumotaan" in lower
            or ("jää" in lower and "lukuun ottamatta" in lower and "voimaan" in lower)
        )
        if not is_relevant:
            continue

        cut = re.search(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b", text, re.IGNORECASE)
        target_zone = text[:cut.start()] if cut else text
        for raw_citation in re.findall(r"\(\s*(\d{1,4}\s*/\s*\d{2,4})\s*\)", target_zone):
            norm = _normalize_source_citation_id(raw_citation, source_year)
            if norm and norm != amendment_id:
                cited_ids.add(norm)

    candidates: Set[str] = set()
    for parent_id in cited_ids:
        try:
            if extract_voimaantulo_repeals(xml_data, parent_id):
                candidates.add(parent_id)
        except Exception as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_parent_extraction_failed",
                phase="parse",
                family="source_pathology",
                reason="Finland amendment index skipped a candidate source VTS parent because extraction failed.",
                detail={
                    "amendment_id": amendment_id,
                    "parent_id": parent_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue
    return candidates

def build_amendment_index(
    cs: CorpusStore | None = None,
    consolidated_zip_path: Path | None = None,
    diagnostics_out: list[dict[str, object]] | None = None,
) -> List[Tuple[str, str, str]]:
    """Scan consolidated statutes and extract (amendment_id, parent_id) pairs.

    ``cs`` may be a CorpusStore or None (auto-detects via get_corpus_store()).
    ``consolidated_zip_path`` is accepted for backward-compat CLI callers but
    is ignored — the Finland pipeline uses Farchive exclusively.

    Returns:
        List of sorted (amendment_id, parent_id) tuples.
    """
    if cs is None:
        cs = get_corpus_store()

    edges: Set[Tuple[str, str, str]] = set()

    # Use oracle_path_index() to enumerate sids, read_oracle() per stat.
    oracle_index = cs.oracle_path_index()
    print(f"Scanning {len(oracle_index)} consolidated statutes for amendment metadata...")

    for sid in sorted(oracle_index):
        parts = sid.split("/", 1)
        if len(parts) != 2:
            continue
        year, num_raw = parts
        parent_id = _make_statute_id(year, num_raw)
        try:
            xml_data = cs.read_oracle(sid)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_oracle_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped consolidated oracle metadata because oracle XML bytes were missing.",
                    detail={
                        "statute_id": sid,
                        "parent_id": parent_id,
                        "edge_kind": "oracle_amendedBy",
                    },
                )
                continue
            root = etree.fromstring(xml_data)
            for ref_elem in cast(list, root.xpath('.//*[local-name()="amendedBy"]//*[local-name()="ref"]')):
                href = ref_elem.get('href', '')
                m = REF_PATTERN.search(href)
                if m:
                    amend_id = _make_statute_id(m.group(1), m.group(2))
                    if amend_id != parent_id:
                        edges.add((amend_id, parent_id, "oracle_amendedBy"))
        except (KeyError, OSError, etree.XMLSyntaxError, etree.XPathError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_oracle_artifact_skipped",
                phase="parse" if isinstance(exc, (etree.XMLSyntaxError, etree.XPathError)) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped consolidated oracle metadata because the artifact could not be read or parsed.",
                detail={
                    "statute_id": sid,
                    "parent_id": parent_id,
                    "edge_kind": "oracle_amendedBy",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    # Supplement from source VTS clauses. These are not represented by
    # consolidated amendedBy metadata when an amendment touches another statute
    # only via entry-into-force prose.
    for amendment_id in sorted(cs.list_statute_ids()):
        try:
            xml_data = cs.read_source(amendment_id)
            if xml_data is None:
                _append_amendment_index_diagnostic(
                    diagnostics_out,
                    rule_id="fi_amendment_index_source_vts_artifact_missing",
                    phase="acquisition",
                    family="source_pathology",
                    reason="Finland amendment index skipped source VTS extraction because source XML bytes were missing.",
                    detail={
                        "amendment_id": amendment_id,
                        "edge_kind": "source_vts_explicit",
                    },
                )
                continue
            for parent_id in _extract_explicit_cross_statute_vts_parents(
                xml_data,
                amendment_id,
                diagnostics_out=diagnostics_out,
            ):
                edges.add((amendment_id, parent_id, "source_vts_explicit"))
        except (KeyError, OSError, etree.XMLSyntaxError) as exc:
            _append_amendment_index_diagnostic(
                diagnostics_out,
                rule_id="fi_amendment_index_source_vts_artifact_skipped",
                phase="parse" if isinstance(exc, etree.XMLSyntaxError) else "acquisition",
                family="source_pathology",
                reason="Finland amendment index skipped source VTS extraction because the source artifact could not be read or parsed.",
                detail={
                    "amendment_id": amendment_id,
                    "edge_kind": "source_vts_explicit",
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

    return sorted(list(edges))

def _consolidated_zip_path_for_store(cs: CorpusStore) -> Path | None:
    """Return the consolidated ZIP path for cs, or None for Farchive-backed stores."""
    # Farchive-backed stores have no ZIP path.
    return None


def ensure_amendment_index(
    cs: CorpusStore | None = None,
    csv_path: Path = _DEFAULT_CACHE_CSV,
) -> None:
    """Ensure amendment_parents.csv exists and is not older than the source ZIP.

    Transparent caching: rebuilds automatically when the ZIP is newer than the
    CSV (e.g., after a data refresh) or when the CSV is missing.

    ``cs`` may be a CorpusStore (preferred) or None (auto-detects via
    get_corpus_store()).  For non-ZIP backends the mtime staleness check is
    skipped and the CSV is used as long as it exists.
    """
    should_close_cs = False
    if cs is None:
        cs = get_corpus_store()
        should_close_cs = True

    try:
        zip_path = _consolidated_zip_path_for_store(cs)

        if csv_path.exists():
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    header = next(csv.reader(f), [])
            except OSError:
                header = []
            header_is_current = header[:3] == _CSV_HEADER
            if zip_path is not None and zip_path.exists():
                if header_is_current and csv_path.stat().st_mtime >= zip_path.stat().st_mtime:
                    return  # cache is fresh
                print(f"[amendment_index] {csv_path} is stale — rebuilding from {zip_path}")
            else:
                if header_is_current:
                    return  # no ZIP to compare against; trust existing CSV
                print(f"[amendment_index] {csv_path} schema is stale — rebuilding")
        else:
            if zip_path is not None and not zip_path.exists():
                raise FileNotFoundError(
                    f"Cannot build amendment index: neither {csv_path} nor {zip_path} exist."
                )
            print(f"[amendment_index] Building {csv_path}...")

        edges = build_amendment_index(cs=cs)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADER)
            writer.writerows(edges)
        print(f"[amendment_index] Wrote {len(edges)} mappings to {csv_path}")
    finally:
        if should_close_cs:
            cs.close()


def _zip_mtime(p: Path) -> float:
    """Return mtime of p, or 0.0 if it doesn't exist."""
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _corpus_store_mtime() -> float:
    """Return mtime of the consolidated ZIP for the default corpus store.

    Used as an lru_cache key so the in-process result auto-invalidates when
    the ZIP is replaced on disk.  Returns 0.0 for non-ZIP backends (archive
    stores are always fresh from the DB).
    """
    try:
        cs = get_corpus_store()
    except (OSError, RuntimeError):
        return 0.0
    try:
        zip_path = _consolidated_zip_path_for_store(cs)
        if zip_path is None:
            return 0.0
        return _zip_mtime(zip_path)
    finally:
        cs.close()


@lru_cache(maxsize=256)
def _get_amendment_children_for_mtime(zip_mtime: float) -> Dict[str, List[str]]:
    """Inner impl keyed on ZIP mtime — auto-invalidates in-process when ZIP changes."""
    ensure_amendment_index(cs=None, csv_path=_DEFAULT_CACHE_CSV)
    mapping: Dict[str, List[str]] = {}
    with open(_DEFAULT_CACHE_CSV, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            mapping.setdefault(row[1], []).append(row[0])
    return mapping


@lru_cache(maxsize=256)
def _get_amendment_child_edges_for_mtime(zip_mtime: float) -> Dict[str, List[Tuple[str, str]]]:
    """Return cached {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    ensure_amendment_index(cs=None, csv_path=_DEFAULT_CACHE_CSV)
    mapping: Dict[str, List[Tuple[str, str]]] = {}
    with open(_DEFAULT_CACHE_CSV, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0] == "amendment_id":
                continue
            edge_kind = row[2] if len(row) >= 3 and row[2] else "oracle_amendedBy"
            mapping.setdefault(row[1], []).append((row[0], edge_kind))
    return mapping


def get_amendment_children() -> Dict[str, List[str]]:
    """Return {parent_statute_id: [amendment_id, ...]} mapping.

    Transparent caching: on-disk CSV rebuilt from the corpus store's
    consolidated ZIP when the ZIP is newer or the cache is missing.
    In-process result is keyed on the ZIP mtime so any ZIP replacement
    automatically invalidates.  Callers never need to know about the
    backing CSV.
    """
    return _get_amendment_children_for_mtime(_corpus_store_mtime())


def get_amendment_child_edges() -> Dict[str, List[Tuple[str, str]]]:
    """Return {parent_statute_id: [(amendment_id, edge_kind), ...]} mapping."""
    return _get_amendment_child_edges_for_mtime(_corpus_store_mtime())


def main():
    parser = argparse.ArgumentParser(description="Extract amendment-to-parent mapping from Finlex.")
    parser.add_argument("--out", type=Path, default=Path("data/finland/amendment_parents.csv"),
                        help="Output CSV path")
    args = parser.parse_args()

    try:
        edges = build_amendment_index()

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADER)
            writer.writerows(edges)

        print(f"Successfully wrote {len(edges)} mappings to {args.out}")
    except (FileNotFoundError, OSError, etree.XMLSyntaxError) as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
