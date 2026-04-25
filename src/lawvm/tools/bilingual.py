"""lawvm bilingual — structural comparison of Finnish and Swedish statute versions.

Finnish legislation is constitutionally bilingual: every statute exists in both
Finnish (fin) and Swedish (swe). The two versions are structurally isomorphic —
same sections, same chapters, same parts. Structural divergence is a bug signal
in the source XML or the pipeline.

This tool reads source statute XMLs from the Farchive corpus store for both
languages and compares section/chapter/part counts and labels.

Usage:
    lawvm bilingual 2009/953              # single statute
    lawvm bilingual --all                 # corpus-wide scan
    lawvm bilingual --all --divergences   # only show diverged statutes
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:
    from lxml import etree as _ET
    _LXML = True
except ImportError:
    import xml.etree.ElementTree as _ET  # type: ignore[no-redef]
    _LXML = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent  # src/lawvm/tools/ -> LawVM/

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Matches the leading ordinal token in a chapter/part label:
#   "1 luku"  -> "1"
#   "1 kap."  -> "1"
#   "IV osa"  -> "IV"
#   "Del I"   -> "I"   (leading Roman after word)
#   "1. del"  -> "1"
_ORDINAL_RE = re.compile(r'^([IVXivx]+|\d+)')


def _ordinal(label: str) -> str:
    """Extract the leading ordinal token from a chapter/part label."""
    m = _ORDINAL_RE.search(label.strip())
    return m.group(1).upper() if m else label


def _ordinals(labels: list[str]) -> list[str]:
    return [_ordinal(lb) for lb in labels]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StructInfo:
    """Structural counts and labels extracted from one XML version."""
    sections: list[str] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    @property
    def n_sections(self) -> int:
        return len(self.sections)

    @property
    def n_chapters(self) -> int:
        return len(self.chapters)

    @property
    def n_parts(self) -> int:
        return len(self.parts)


@dataclass
class BilingualResult:
    """Comparison result for one statute."""
    sid: str
    fin: StructInfo | None = None   # None if XML absent
    swe: StructInfo | None = None   # None if XML absent
    error: str | None = None        # parse/read error

    @property
    def both_present(self) -> bool:
        return self.fin is not None and self.swe is not None

    @property
    def match(self) -> bool:
        """True when both present and structurally identical.

        Counts must match for all structural levels.  Section labels (§ numbers)
        must match — they are language-neutral ordinals.  Chapter and part labels
        are intentionally translated (Finnish 'luku' vs Swedish 'kap.', etc.),
        so we only compare their *count* and their *ordinal number prefix* (the
        digit/Roman part before the word), not the full label text.
        """
        if not self.both_present:
            return False
        assert self.fin is not None and self.swe is not None
        f, s = self.fin, self.swe
        if f.n_sections != s.n_sections:
            return False
        if f.n_chapters != s.n_chapters:
            return False
        if f.n_parts != s.n_parts:
            return False
        # Section labels must be identical (§ numbers are language-neutral)
        if f.sections != s.sections:
            return False
        # Chapter/part ordinals must match (ignore translated word suffix)
        if _ordinals(f.chapters) != _ordinals(s.chapters):
            return False
        if _ordinals(f.parts) != _ordinals(s.parts):
            return False
        return True

    @property
    def divergences(self) -> list[str]:
        """Human-readable list of divergence descriptions."""
        if not self.both_present:
            if self.fin is None and self.swe is None:
                return ["both fin and swe absent"]
            if self.fin is None:
                return ["fin XML absent"]
            return ["swe XML absent"]
        assert self.fin is not None and self.swe is not None
        f, s = self.fin, self.swe
        out: list[str] = []
        if f.n_sections != s.n_sections:
            out.append(f"sections: fin={f.n_sections} swe={s.n_sections}")
            missing = sorted(set(f.sections) - set(s.sections))
            extra = sorted(set(s.sections) - set(f.sections))
            if missing:
                out.append(f"  sections in fin not in swe: {missing}")
            if extra:
                out.append(f"  sections in swe not in fin: {extra}")
        elif f.sections != s.sections:
            # Same count but different § labels — ordering/numbering mismatch
            mismatched = [
                f"pos {i+1}: fin={fl!r} swe={sl!r}"
                for i, (fl, sl) in enumerate(zip(f.sections, s.sections))
                if fl != sl
            ]
            if mismatched:
                out.append("section label mismatches (same count):")
                out.extend(f"  {m}" for m in mismatched[:10])
        if f.n_chapters != s.n_chapters:
            out.append(f"chapters: fin={f.n_chapters} swe={s.n_chapters}")
        elif _ordinals(f.chapters) != _ordinals(s.chapters):
            mismatched = [
                f"pos {i+1}: fin={fl!r} swe={sl!r}"
                for i, (fl, sl) in enumerate(zip(f.chapters, s.chapters))
                if _ordinal(fl) != _ordinal(sl)
            ]
            if mismatched:
                out.append("chapter ordinal mismatches (same count):")
                out.extend(f"  {m}" for m in mismatched[:10])
        if f.n_parts != s.n_parts:
            out.append(f"parts: fin={f.n_parts} swe={s.n_parts}")
        elif _ordinals(f.parts) != _ordinals(s.parts):
            mismatched = [
                f"pos {i+1}: fin={fl!r} swe={sl!r}"
                for i, (fl, sl) in enumerate(zip(f.parts, s.parts))
                if _ordinal(fl) != _ordinal(sl)
            ]
            if mismatched:
                out.append("part ordinal mismatches (same count):")
                out.extend(f"  {m}" for m in mismatched[:10])
        return out


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _extract_labels(tree: Any, tag: str) -> list[str]:
    """Extract text of <num> children from all <{tag}> elements."""
    ns = _AKN_NS
    out: list[str] = []
    for el in tree.findall(f".//{{{ns}}}{tag}"):
        num_el = el.find(f"{{{ns}}}num")
        if num_el is not None and num_el.text:
            out.append(num_el.text.strip())
        else:
            out.append("?")
    return out


def parse_struct(xml_bytes: bytes) -> StructInfo:
    """Parse statute XML and return structural counts + labels."""
    tree = _ET.fromstring(xml_bytes)
    return StructInfo(
        sections=_extract_labels(tree, "section"),
        chapters=_extract_labels(tree, "chapter"),
        parts=_extract_labels(tree, "part"),
    )


# ---------------------------------------------------------------------------
# Source: Farchive-backed bilingual reader
# ---------------------------------------------------------------------------

class _FarchiveBilingualReader:
    """Read fin and swe source XMLs from the Farchive corpus store."""

    def __init__(self) -> None:
        from lawvm.corpus_store import get_corpus_store
        self._cs = get_corpus_store()

    def _swe_url(self, sid: str) -> str:
        return f"finlex://sd/{sid}/swe/main.xml"

    def _list_swe_sids(self) -> set[str]:
        """List all statute IDs that have a Swedish version in farchive."""
        archive = getattr(self._cs, "_archive", None)
        if archive is None:
            return set()
        swe_urls = archive.locators("finlex://sd/%/swe/main.xml")
        swe_pat = re.compile(r"finlex://sd/(\d{4}/[^/]+)/swe/main\.xml$")
        return {m.group(1) for u in swe_urls if (m := swe_pat.match(u))}

    def list_bilingual_sids(self) -> list[str]:
        """Return statute IDs that have both fin and swe XMLs."""
        fin_sids = set(self._cs.list_statute_ids())
        swe_sids = self._list_swe_sids()
        return sorted(fin_sids & swe_sids)

    def list_all_sids(self) -> list[str]:
        """Return all statute IDs that have at least fin or swe XML."""
        fin_sids = set(self._cs.list_statute_ids())
        swe_sids = self._list_swe_sids()
        return sorted(fin_sids | swe_sids)

    def read_lang(self, sid: str, lang: str) -> bytes | None:
        """Read XML bytes for sid in lang ('fin' or 'swe'). None if absent."""
        if lang == "fin":
            return self._cs.read_source(sid)
        # Swedish: fetch from farchive directly via swe URL
        archive = getattr(self._cs, "_archive", None)
        if archive is None:
            return None
        url = self._swe_url(sid)
        return archive.get(url)

    def close(self) -> None:
        archive = getattr(self._cs, "_archive", None)
        if archive is not None:
            try:
                archive.close()
            except Exception:
                pass


def _get_reader(zip_path: Path | None = None, archive_db: Path | None = None):
    """Return a Farchive-backed bilingual reader.

    The zip_path and archive_db arguments are accepted for backward compatibility
    but are ignored — the Finland pipeline uses Farchive exclusively.
    """
    return _FarchiveBilingualReader()


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------

def compare_statute(sid: str, reader) -> BilingualResult:
    """Compare Finnish and Swedish versions of a single statute."""
    result = BilingualResult(sid=sid)
    try:
        fin_bytes = reader.read_lang(sid, "fin")
        swe_bytes = reader.read_lang(sid, "swe")
        result.fin = parse_struct(fin_bytes) if fin_bytes is not None else None
        result.swe = parse_struct(swe_bytes) if swe_bytes is not None else None
    except Exception as exc:
        result.error = str(exc)
    return result


def compare_corpus(
    sids: Sequence[str],
    reader,
    verbose: bool = False,
) -> list[BilingualResult]:
    """Compare all statutes in sids. Returns results in order."""
    results: list[BilingualResult] = []
    for i, sid in enumerate(sids):
        r = compare_statute(sid, reader)
        results.append(r)
        if verbose and (i + 1) % 5000 == 0:
            print(f"  {i+1:,}/{len(sids):,}...", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Formatting / reporting
# ---------------------------------------------------------------------------

def print_single(r: BilingualResult) -> None:
    """Print a detailed comparison for one statute."""
    print(f"Statute: {r.sid}")
    if r.error:
        print(f"  ERROR: {r.error}")
        return
    if r.fin is None:
        print("  fin XML: ABSENT")
    else:
        print(f"  fin: {r.fin.n_sections} sections, {r.fin.n_chapters} chapters, {r.fin.n_parts} parts")
    if r.swe is None:
        print("  swe XML: ABSENT")
    else:
        print(f"  swe: {r.swe.n_sections} sections, {r.swe.n_chapters} chapters, {r.swe.n_parts} parts")

    if not r.both_present:
        return

    if r.match:
        print("  MATCH: structurally identical")
    else:
        print("  DIVERGENCE:")
        for d in r.divergences:
            print(f"    {d}")


def print_summary(results: list[BilingualResult], divergences_only: bool = False) -> None:
    """Print corpus-wide summary table."""
    n_total = len(results)
    n_both = sum(1 for r in results if r.both_present)
    n_match = sum(1 for r in results if r.match)
    n_diverged = sum(1 for r in results if r.both_present and not r.match)
    n_fin_only = sum(1 for r in results if r.fin is not None and r.swe is None)
    n_swe_only = sum(1 for r in results if r.swe is not None and r.fin is None)
    n_errors = sum(1 for r in results if r.error)

    print("\nBilingual corpus comparison summary")
    print(f"  Total statutes checked:  {n_total:,}")
    print(f"  Both fin+swe present:    {n_both:,}")
    print(f"    Structurally identical:  {n_match:,}")
    print(f"    Diverged:                {n_diverged:,}  ({n_diverged/n_both*100:.1f}% of bilingual)" if n_both else "    Diverged:                0")
    print(f"  fin only (no swe):       {n_fin_only:,}")
    print(f"  swe only (no fin):       {n_swe_only:,}")
    if n_errors:
        print(f"  Parse errors:            {n_errors:,}")

    diverged = [r for r in results if r.both_present and not r.match]
    if not diverged:
        return

    print(f"\nDivergences ({len(diverged)} statutes):")
    for r in diverged:
        divs = r.divergences
        # First line of description (counts)
        first = divs[0] if divs else "?"
        print(f"  {r.sid:<20}  {first}")
        for d in divs[1:]:
            if not d.startswith("  "):
                print(f"  {' ':20}  {d}")
            elif divergences_only:
                # full detail when --divergences flag
                print(f"  {' ':20}{d}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:  # args from argparse
    zip_path = getattr(args, "zip", None)
    archive_db = getattr(args, "archive_db", None)
    divergences_only = getattr(args, "divergences", False)

    reader = _get_reader(
        zip_path=Path(zip_path) if zip_path else None,
        archive_db=Path(archive_db) if archive_db else None,
    )

    if args.all:
        # Corpus mode
        all_sids = reader.list_all_sids()
        print(f"Scanning {len(all_sids):,} statutes...", file=sys.stderr)
        results = compare_corpus(all_sids, reader, verbose=True)
        print_summary(results, divergences_only=divergences_only)
    else:
        # Single statute
        sid = args.statute_id
        if sid is None:
            print("ERROR: provide a statute_id or --all", file=sys.stderr)
            sys.exit(1)
        # Normalize: accept both YEAR/NUM and NUM/YEAR style
        r = compare_statute(sid, reader)
        print_single(r)
        if r.both_present and not r.match:
            sys.exit(2)  # non-zero exit on divergence

    reader.close()
