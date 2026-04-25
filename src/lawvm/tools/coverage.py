"""lawvm coverage — corpus coverage audit ("Is The Law Complete?").

Two scan depths:
  Fast (default): path enumeration only — detects GIFs, corrigendum PDFs,
                  annexed PDFs. No file reads. ~1 second.
  Deep (--deep):  also reads every fin@ XML to detect contentAbsent.
                  ~60s first run; result cached in .tmp/coverage_cache.json.

Usage:
    lawvm coverage                       # corpus report (fast)
    lawvm coverage --deep                # include contentAbsent detection
    lawvm coverage --statute 2007/26     # single statute, full breakdown
    lawvm coverage --gaps                # only statutes with non-cosmetic gaps
    lawvm coverage --format json         # JSON output
    lawvm coverage --rebuild             # force rebuild cache (implies --deep)

Coverage status per statute:
    COMPLETE   — XML present, no known gaps
    GIF        — has embedded GIF images (tables/formulas as scanned images)
    CORRIGENDUM — has corrigendum PDF(s) (errata, legally binding corrections)
    ANNEXED_PDF — has non-corrigendum embedded PDF(s) in the language version
    ABSENT     — contentAbsent (repealed, expired, or not digitized)
    ABSENT+    — contentAbsent with corrigendum PDFs attached (interesting edge case)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from lawvm.corpus_store import CorpusStore, get_corpus_store
from lawvm.finland.corpus import (
    get_oracle_path,
    list_cached_consolidated_locators,
)
from lawvm.finland.consolidated_artifacts import (
    ConsolidatedArtifactSelector,
    parse_consolidated_locator,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent  # src/lawvm/tools/ → LawVM/
_TMP_DIR = _LAWVM_DIR / ".tmp"
_CACHE_PATH = _TMP_DIR / "coverage_cache.json"


def _make_corpus_store() -> CorpusStore:
    return get_corpus_store()

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CoverageEntry:
    sid: str
    has_xml: bool = False           # fin@ main.xml exists
    content_absent: Optional[bool] = None  # None = not checked (fast scan)
    gif_count: int = 0              # .gif files in any lang version
    corrigendum_count: int = 0      # corrigendum PDFs
    corrigendum_pdfs: list = field(default_factory=list)
    annexed_pdf_count: int = 0      # non-corrigendum embedded PDFs

    @property
    def status(self) -> str:
        if self.content_absent:
            if self.corrigendum_count:
                return "ABSENT+"
            return "ABSENT"
        if not self.has_xml:
            return "UNKNOWN"
        flags = []
        if self.gif_count:
            flags.append("GIF")
        if self.corrigendum_count:
            flags.append("CORRIGENDUM")
        if self.annexed_pdf_count:
            flags.append("ANNEXED_PDF")
        return "+".join(flags) if flags else "COMPLETE"

    @property
    def has_gap(self) -> bool:
        """Non-cosmetic gap (not just a repealed statute)."""
        return self.gif_count > 0 or self.corrigendum_count > 0 or self.annexed_pdf_count > 0


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def _extract_sid_from_locator(locator: str) -> Optional[str]:
    parts = parse_consolidated_locator(locator)
    if parts is None:
        return None
    return parts.sid


def scan_fast(cs: CorpusStore) -> dict[str, CoverageEntry]:
    """Path-enumeration scan: GIFs, corrigendum PDFs, annexed PDFs.

    For Farchive-backed stores, only XML locators are present.  GIF/PDF
    media are not stored in farchive (Finlex Open Data API does not serve them),
    so gif_count / corrigendum_count / annexed_pdf_count will be 0 from the
    fast scan. The deep scan populates has_xml / content_absent.
    """
    entries: dict[str, CoverageEntry] = {}

    def get(sid: str) -> CoverageEntry:
        if sid not in entries:
            entries[sid] = CoverageEntry(sid=sid)
        return entries[sid]

    selector = ConsolidatedArtifactSelector.latest_cached_editorial()
    archive = getattr(cs, "_archive", None)
    if archive is None:
        # Fallback: just enumerate statute IDs from oracle index
        for sid in cs.oracle_path_index(selector=selector):
            get(sid).has_xml = True
        return entries

    # Enumerate all cached consolidated locators via the Finland access layer.
    for locator in list_cached_consolidated_locators(cs):
        sid = _extract_sid_from_locator(locator)
        if not sid:
            continue
        e = get(sid)
        if locator.endswith("/main.xml") and "/fin@" in locator:
            e.has_xml = True
        elif locator.endswith(".gif"):
            e.gif_count += 1
        elif "/corrigenda/" in locator and locator.endswith(".pdf"):
            e.corrigendum_count += 1
            e.corrigendum_pdfs.append(locator)
        elif locator.endswith(".pdf") and "/corrigenda/" not in locator and "/fin@" in locator:
            e.annexed_pdf_count += 1

    return entries


_CONTENT_ABSENT_BYTES = b"contentAbsent"


def enrich_with_content_absent(
    entries: dict[str, CoverageEntry],
    cs: CorpusStore,
    verbose: bool = True,
) -> None:
    """Read every oracle XML and detect contentAbsent. Mutates entries in place."""
    selector = ConsolidatedArtifactSelector.latest_cached_editorial()
    sids = [sid for sid, e in entries.items() if e.has_xml]
    total = len(sids)
    if verbose:
        print(f"Deep scan: reading {total} XML files...", flush=True)

    for i, sid in enumerate(sids):
        e = entries[sid]
        try:
            oracle_path = get_oracle_path(sid, cs, selector=selector)
            data = cs.read_locator(oracle_path) if oracle_path else None
            if data is None:
                continue
            absent = _CONTENT_ABSENT_BYTES in data
            if e.content_absent is None:
                e.content_absent = absent
            elif e.content_absent and not absent:
                e.content_absent = False
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        if verbose and (i + 1) % 5000 == 0:
            print(f"  {i + 1}/{total}...", flush=True)

    if verbose:
        print("  Done.", flush=True)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> Optional[dict[str, CoverageEntry]]:
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text())
        if data.get("version") != 2:
            return None
        result = {}
        for sid, d in data.get("entries", {}).items():
            e = CoverageEntry(sid=sid)
            e.has_xml = d.get("has_xml", False)
            e.content_absent = d.get("content_absent")
            e.gif_count = d.get("gif_count", 0)
            e.corrigendum_count = d.get("corrigendum_count", 0)
            e.corrigendum_pdfs = d.get("corrigendum_pdfs", [])
            e.annexed_pdf_count = d.get("annexed_pdf_count", 0)
            result[sid] = e
        return result
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except (ValueError, OSError, KeyError):
        return None


def _save_cache(entries: dict[str, CoverageEntry]) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 2,
        "entries": {
            sid: {
                "has_xml": e.has_xml,
                "content_absent": e.content_absent,
                "gif_count": e.gif_count,
                "corrigendum_count": e.corrigendum_count,
                "corrigendum_pdfs": e.corrigendum_pdfs,
                "annexed_pdf_count": e.annexed_pdf_count,
            }
            for sid, e in entries.items()
        }
    }
    _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Single-statute lookup
# ---------------------------------------------------------------------------

def lookup_statute(sid: str, cs: CorpusStore) -> CoverageEntry:
    """Full single-statute coverage check (reads oracle XML)."""
    e = CoverageEntry(sid=sid)

    selector = ConsolidatedArtifactSelector.latest_cached_editorial()
    archive = getattr(cs, "_archive", None)
    if archive is not None:
        # Enumerate via farchive locators
        locators = list_cached_consolidated_locators(cs, sid)
        if any(locator.endswith("/main.xml") and "/fin@" in locator for locator in locators):
            e.has_xml = True
        for locator in locators:
            if locator.endswith("/main.xml") and "/fin@" in locator:
                e.has_xml = True
            elif locator.endswith(".gif"):
                e.gif_count += 1
            elif "/corrigenda/" in locator and locator.endswith(".pdf"):
                e.corrigendum_count += 1
                e.corrigendum_pdfs.append(locator)
            elif locator.endswith(".pdf") and "/corrigenda/" not in locator and "/fin@" in locator:
                e.annexed_pdf_count += 1
    else:
        # Fallback: check oracle index
        idx = cs.oracle_path_index(selector=selector)
        if sid in idx:
            e.has_xml = True

    # Deep check: read oracle XML for contentAbsent
    if e.has_xml:
        try:
            oracle_path = get_oracle_path(sid, cs, selector=selector)
            data = cs.read_locator(oracle_path) if oracle_path else None
            if data is not None:
                e.content_absent = _CONTENT_ABSENT_BYTES in data
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except (OSError, ValueError):
            pass

    return e


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_corpus_report(entries: dict[str, CoverageEntry], gaps_only: bool) -> None:
    from collections import Counter
    status_counts: Counter = Counter()
    gif_total = corr_total = annexed_total = absent_total = 0
    gap_entries = []

    for sid, e in sorted(entries.items()):
        status_counts[e.status] += 1
        gif_total += e.gif_count
        corr_total += e.corrigendum_count
        annexed_total += e.annexed_pdf_count
        if e.content_absent:
            absent_total += 1
        if e.has_gap:
            gap_entries.append(e)

    n = len(entries)
    n_xml = sum(1 for e in entries.values() if e.has_xml)
    deep = any(e.content_absent is not None for e in entries.values())

    print("=== Corpus Coverage Report ===")
    print(f"  Total statutes : {n}")
    print(f"  With XML       : {n_xml}")
    if deep:
        print(f"  contentAbsent  : {absent_total}  ({absent_total/n:.1%})")
    else:
        print("  contentAbsent  : (run with --deep to detect)")
    print(f"  GIF images     : {gif_total} GIFs across {sum(1 for e in entries.values() if e.gif_count)} statutes")
    print(f"  Corrigenda     : {corr_total} PDFs across {sum(1 for e in entries.values() if e.corrigendum_count)} statutes")
    print(f"  Annexed PDFs   : {annexed_total} PDFs across {sum(1 for e in entries.values() if e.annexed_pdf_count)} statutes")
    print()
    print("  Status breakdown:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"    {status:<20} {count:>6}  ({count/n:.1%})")

    if gaps_only and gap_entries:
        print(f"\n  Statutes with gaps ({len(gap_entries)}):")
        for e in sorted(gap_entries, key=lambda x: x.sid):
            parts = []
            if e.gif_count:
                parts.append(f"{e.gif_count} GIF")
            if e.corrigendum_count:
                parts.append(f"{e.corrigendum_count} corrigendum")
            if e.annexed_pdf_count:
                parts.append(f"{e.annexed_pdf_count} annexed PDF")
            print(f"    {e.sid:<16}  [{', '.join(parts)}]")


def _print_statute_report(e: CoverageEntry) -> None:
    print(f"=== Coverage: {e.sid} ===")
    print(f"  Status         : {e.status}")
    print(f"  Has XML        : {e.has_xml}")
    print(f"  contentAbsent  : {e.content_absent}")
    print(f"  GIF count      : {e.gif_count}")
    print(f"  Corrigenda     : {e.corrigendum_count}")
    if e.corrigendum_pdfs:
        for pdf in e.corrigendum_pdfs:
            print(f"    {pdf}")
    print(f"  Annexed PDFs   : {e.annexed_pdf_count}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    fmt = getattr(args, "format", "text")
    rebuild = getattr(args, "rebuild", False)
    deep = getattr(args, "deep", False) or rebuild
    gaps_only = getattr(args, "gaps", False)
    statute_id = getattr(args, "statute_id", None)

    cs = _make_corpus_store()

    # Single-statute mode
    if statute_id:
        e = lookup_statute(statute_id, cs)
        if fmt == "json":
            print(json.dumps(asdict(e), ensure_ascii=False, indent=2))
        else:
            _print_statute_report(e)
        return

    # Corpus mode
    if rebuild or (deep and not _CACHE_PATH.exists()):
        entries = scan_fast(cs)
        enrich_with_content_absent(entries, cs, verbose=True)
        _save_cache(entries)
        print(f"Cache written: {_CACHE_PATH}", flush=True)
    elif deep and _CACHE_PATH.exists():
        entries = _load_cache()
        if entries is None:
            print("Cache invalid, rebuilding...", flush=True)
            entries = scan_fast(cs)
            enrich_with_content_absent(entries, cs, verbose=True)
            _save_cache(entries)
        else:
            print(f"Using cache: {_CACHE_PATH} ({len(entries)} entries)", flush=True)
    else:
        # Fast scan (no contentAbsent detection)
        entries = scan_fast(cs)

    if fmt == "json":
        out = {sid: asdict(e) for sid, e in sorted(entries.items())}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_corpus_report(entries, gaps_only=gaps_only)
