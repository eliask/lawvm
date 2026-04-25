"""lawvm freshness — freshness audit comparing ZIP vs API vs HTML oracles.

Compares section counts from three sources for each statute:
  1. corpus oracle   — local consolidated corpus (fast, no network)
  2. API PIT      — opendata.finlex.fi PIT-versioned XML (optional, network)
  3. HTML         — finlex.fi website (ground truth, optional, network)

Classifications:
  FRESH      ZIP == HTML (or HTML unavailable)
  ZIP_STALE  ZIP < HTML (ZIP is behind the website)
  API_STALE  API < HTML (API is behind the website, API available)
  NO_PIT     No PIT API versions available for this statute
  MATCH      ZIP == API == HTML (all sources agree)

Usage:
    lawvm freshness --sample 50 --label fresh_v1
    lawvm freshness --corpus --label fresh_full
    lawvm freshness --sample 20 --no-html --no-api --label zip_only
"""
from __future__ import annotations

import csv
import logging
import re
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lxml import etree

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent   # src/lawvm/tools/ → LawVM/
_DEFAULT_CORPUS = _LAWVM_DIR / "data" / "finland" / "bench_corpus.csv"
_FALLBACK_CORPUS = _LAWVM_DIR / ".tmp" / "batch_test_list.csv"
_REPORT_DIR = _LAWVM_DIR / "data" / "freshness_reports"
_STRICT_RUNS_DIR = _LAWVM_DIR / "data" / "strict_runs"

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FreshnessRecord:
    statute_id: str
    zip_sections: int = -1        # -1 = not available
    api_sections: int = -1        # -1 = not checked / not available
    html_sections: int = -1       # -1 = not checked / not available
    replay_sections: int = -1     # -1 = not checked / not available
    pit_version: str = ""         # e.g. "20210680" or "" if none
    classification: str = "UNKNOWN"
    zip_error: str = ""
    api_error: str = ""
    html_error: str = ""
    replay_error: str = ""


# ---------------------------------------------------------------------------
# Replay section counting (CPU-intensive, local, no network)
# ---------------------------------------------------------------------------

def _replay_section_count(sid: str) -> tuple[int, str]:
    """Return (section_count, error_msg) from a full replay_xml() call.

    Counts IRNodes with kind == 'section' in master.ir.
    Sequential only — caller must not run this in a process pool alongside
    other replay calls (high memory usage per worker).
    """
    try:
        from lawvm.finland.grafter import replay_xml
        previous_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            master = replay_xml(sid, quiet=True)
        finally:
            logging.disable(previous_disable)
        if master.ir is None:
            return -1, "no_ir"

        def _count(node: object) -> int:
            from lawvm.core.ir import IRNode
            if not isinstance(node, IRNode):
                return 0
            n = 1 if node.kind == "section" else 0
            for c in node.children:
                n += _count(c)
            return n

        return _count(master.ir), ""
    except Exception as exc:
        return -1, str(exc)[:80]


# ---------------------------------------------------------------------------
# ZIP section counting (fast, local, no network)
# ---------------------------------------------------------------------------

def _count_sections_xml(data: bytes) -> int:
    """Count <section> elements in AKN XML bytes. Returns -1 on parse error."""
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return -1
    sections = root.findall(f".//{{{_AKN_NS}}}section")
    return len(sections)


def _zip_section_count(sid: str) -> tuple[int, str]:
    """Return (section_count, error_msg) from the corpus oracle.

    Uses get_ground_truth_tree logic: highest PIT version or fin@.
    Runs in-process — caller is typically a worker process.
    """
    from lawvm.corpus_store import get_corpus_store
    try:
        cs = get_corpus_store()
        data = cs.read_oracle(sid)
        if data is None:
            return -1, "oracle_absent"
        n = _count_sections_xml(data)
        if n < 0:
            return -1, "xml_parse_error"
        return n, ""
    except Exception as exc:
        return -1, str(exc)


# ---------------------------------------------------------------------------
# PIT version discovery and API fetch
# ---------------------------------------------------------------------------

# Graceful degradation if finlex_api module not yet built by another agent.
try:
    from lawvm.finland.finlex_api import fetch_latest_pit_xml as _fetch_latest_pit_xml_api  # type: ignore[import]
    _HAS_FINLEX_API = True
except ImportError:
    _HAS_FINLEX_API = False


def _discover_pit_versions_fallback(year: str, num: str) -> list[str]:
    """Discover PIT versions by fetching the directory listing from the API.

    GET https://opendata.finlex.fi/api/v2/statutes/consolidated/{year}/{num}/
    Parse for fin@YYYYNNNN entries.

    Returns list of YYYYNNNN strings (empty = no PIT versions).
    """
    url = (
        f"https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/"
        f"statute-consolidated/{year}/{num}/"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    # Response is HTML directory listing or JSON.
    # Look for fin@YYYYNNNN patterns.
    versions = re.findall(r'fin@(\d{8,})', body)
    # Deduplicate, return sorted.
    return sorted(set(versions))


def _fetch_pit_xml_fallback(year: str, num: str, pit_version: str) -> Optional[bytes]:
    """Fetch PIT XML from opendata.finlex.fi. Returns bytes or None."""
    url = (
        f"https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/"
        f"statute-consolidated/{year}/{num}/fin@{pit_version}/main.xml"
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except Exception:
        return None


def _api_section_count(sid: str) -> tuple[int, str, str]:
    """Return (section_count, pit_version, error_msg) from the PIT API.

    section_count = -1 when unavailable (no PIT versions or fetch failed).
    pit_version = "" when no PIT versions exist.
    """
    year, num = sid.split("/", 1)

    if _HAS_FINLEX_API:
        try:
            data, pit_version = _fetch_latest_pit_xml_api(year, num)
            if data is None:
                return -1, "", "no_pit_versions"
            n = _count_sections_xml(data)
            return n, pit_version, ""
        except Exception as exc:
            return -1, "", str(exc)
    else:
        versions = _discover_pit_versions_fallback(year, num)
        if not versions:
            return -1, "", "no_pit_versions"
        best = max(versions, key=lambda v: int(v))
        data = _fetch_pit_xml_fallback(year, num, best)
        if data is None:
            return -1, best, "fetch_failed"
        n = _count_sections_xml(data)
        return n, best, ""


# ---------------------------------------------------------------------------
# HTML section counting
# ---------------------------------------------------------------------------

# Graceful degradation if finlex_html module not yet built by another agent.
try:
    from lawvm.finland.finlex_html import html_section_count as _html_section_count_mod  # type: ignore[import]
    _HAS_FINLEX_HTML = True
except ImportError:
    _HAS_FINLEX_HTML = False


def _finlex_html_url(sid: str) -> str:
    year, num = sid.split("/")
    base_num = num.split("-", 1)[0]
    return f"https://www.finlex.fi/fi/laki/ajantasa/{year}/{year}{int(base_num):04d}"


def _fetch_html_raw(url: str, timeout: int = 20) -> Optional[bytes]:
    """Fetch HTML bytes using a realistic User-Agent. Returns None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _html_section_count_fallback(sid: str) -> tuple[int, str]:
    """Return (section_count, error_msg) from the Finlex HTML page.

    Uses the html_section_extractor RSC-JSON parsing logic.
    Falls back gracefully if scripts/ module unavailable.
    """
    url = _finlex_html_url(sid)
    raw = _fetch_html_raw(url)
    if raw is None:
        return -1, "fetch_failed"

    # Try finlex_html module first
    if _HAS_FINLEX_HTML:
        try:
            year, num = sid.split("/")
            n = _html_section_count_mod(year, num)
            if n is not None:
                return n, ""
            return -1, "html_parse_failed"
        except Exception as exc:
            return -1, str(exc)

    # Fall back to scripts/html_section_extractor
    try:
        import importlib.util
        extractor_path = _LAWVM_DIR / "scripts" / "html_section_extractor.py"
        spec = importlib.util.spec_from_file_location(
            "html_section_extractor", extractor_path
        )
        if spec is None or spec.loader is None:
            raise ImportError("could not load html_section_extractor")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        sections = mod.extract_sections_from_html(raw)
        return len(sections), ""
    except Exception as exc:
        return -1, f"extractor_error:{exc}"


def _html_section_count(sid: str) -> tuple[int, str]:
    """Return (count, error). Delegates to module or fallback."""
    return _html_section_count_fallback(sid)


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _classify(rec: FreshnessRecord) -> str:
    z = rec.zip_sections
    a = rec.api_sections
    h = rec.html_sections
    r = rec.replay_sections

    # No HTML reference — can't determine staleness vs HTML
    if h < 0:
        if a < 0:
            # Only ZIP available
            return "ZIP_ONLY"
        if a == z:
            return "ZIP_API_MATCH"
        if a > z:
            return "ZIP_STALE"   # API has more than ZIP
        return "API_BEHIND_ZIP"  # unusual

    # HTML available — check replay-aware classifications first
    if r >= 0:
        # Replay available: add triangulation classifications
        if abs(r - h) <= 2 and z < h:
            # Replay agrees with HTML but ZIP is behind
            return "STALE_ORACLE_CONFIRMED"
        if abs(r - z) <= 2 and z < h:
            # Replay agrees with ZIP but both are behind HTML
            return "REPLAY_BEHIND"

    # HTML available (standard path)
    if a < 0 and rec.pit_version == "" and rec.api_error == "no_pit_versions":
        # No PIT versions exist — can still compare ZIP vs HTML
        if z == h:
            return "FRESH"
        if z < h:
            return "ZIP_STALE"
        return "ZIP_AHEAD"  # unlikely but track

    if a < 0:
        # API check was skipped or failed
        if z == h:
            return "FRESH"
        if z < h:
            return "ZIP_STALE"
        return "ZIP_AHEAD"

    # All three available
    if z == h and a == h:
        return "MATCH"
    if z == h and a < h:
        return "API_STALE"
    if z < h and a == h:
        return "ZIP_STALE"
    if z < h and a < h:
        return "ZIP_STALE"   # both behind HTML
    if z == h:
        return "FRESH"
    if z < h:
        return "ZIP_STALE"
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def _load_corpus(corpus_path: Optional[str] = None) -> list[str]:
    """Load statute IDs from CSV. Returns list of IDs."""
    if corpus_path:
        p = Path(corpus_path)
    elif _DEFAULT_CORPUS.exists():
        p = _DEFAULT_CORPUS
    elif _FALLBACK_CORPUS.exists():
        p = _FALLBACK_CORPUS
    else:
        return []

    sids: list[str] = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            # Handle both "N,SID" and "SID" formats
            candidate = row[-1].strip() if len(row) > 1 else row[0].strip()
            if re.match(r'^\d{4}/\d+$', candidate):
                sids.append(candidate)
    return sids


def _load_source_incomplete_set(strict_label: Optional[str] = None) -> set[str]:
    """Load statute IDs flagged source_incomplete from a strict run CSV.

    Used to bias sample selection toward stale statutes.
    Returns empty set if no strict runs available.
    """
    if strict_label:
        pattern = f"*_{strict_label}.csv"
    else:
        pattern = "*.csv"

    runs = sorted(_STRICT_RUNS_DIR.glob(pattern), reverse=True)
    if not runs:
        return set()

    result: set[str] = set()
    with open(runs[0], newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("source_incomplete", "").strip() == "1":
                result.add(row.get("statute_id", "").strip())
    return result


# ---------------------------------------------------------------------------
# Worker function (runs in subprocess for ZIP counting)
# ---------------------------------------------------------------------------

def _worker_zip_count(sid: str) -> tuple[str, int, str]:
    """Worker: return (sid, count, error). Used with ProcessPoolExecutor."""
    n, err = _zip_section_count(sid)
    return sid, n, err


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------

def run_freshness_audit(
    sids: list[str],
    *,
    check_api: bool = True,
    check_html: bool = True,
    check_replay: bool = False,
    rate_limit: float = 1.0,
    verbose: bool = False,
    max_workers: int = 4,
) -> list[FreshnessRecord]:
    """Run freshness audit for a list of statute IDs.

    Args:
        sids:          Statute IDs to audit.
        check_api:     Whether to check the PIT API (network calls).
        check_html:    Whether to check the HTML website (network calls).
        check_replay:  Whether to run replay_xml() and count sections from IR
                       (CPU-intensive, sequential, no network).
        rate_limit:    Seconds between network requests (API + HTML combined).
        verbose:       Print progress to stderr.
        max_workers:   Workers for parallel ZIP section counting.

    Returns:
        List of FreshnessRecord, one per statute.
    """
    records: dict[str, FreshnessRecord] = {sid: FreshnessRecord(statute_id=sid) for sid in sids}

    # --- Phase 1: ZIP section counts (parallel, no network) ---
    if verbose:
        print(f"[freshness] Phase 1: ZIP section counts for {len(sids)} statutes ...",
              file=sys.stderr)

    # Use ProcessPoolExecutor for ZIP (each worker gets its own ZipCorpusStore)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker_zip_count, sid): sid for sid in sids}
        done = 0
        for fut in as_completed(futures):
            sid_r, count, err = fut.result()
            rec = records[sid_r]
            rec.zip_sections = count
            rec.zip_error = err
            done += 1
            if verbose and done % 50 == 0:
                print(f"[freshness]   ZIP: {done}/{len(sids)}", file=sys.stderr)

    if verbose:
        print("[freshness] Phase 1 done.", file=sys.stderr)

    # --- Phase 2: API PIT + HTML (sequential, rate limited) ---
    if not check_api and not check_html:
        # Classify ZIP-only
        for rec in records.values():
            if rec.zip_sections >= 0:
                rec.classification = "ZIP_ONLY"
            else:
                rec.classification = "UNKNOWN"
        return list(records.values())

    if verbose:
        print(f"[freshness] Phase 2: network checks (rate_limit={rate_limit}s) ...",
              file=sys.stderr)

    for i, sid in enumerate(sids):
        rec = records[sid]
        last_request = 0.0

        def _rate_wait() -> None:
            nonlocal last_request
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit and last_request > 0:
                time.sleep(rate_limit - elapsed)
            last_request = time.monotonic()

        if check_api:
            _rate_wait()
            api_count, pit_version, api_err = _api_section_count(sid)
            rec.api_sections = api_count
            rec.pit_version = pit_version
            rec.api_error = api_err

        if check_html:
            _rate_wait()
            html_count, html_err = _html_section_count(sid)
            rec.html_sections = html_count
            rec.html_error = html_err

        rec.classification = _classify(rec)

        if verbose:
            print(
                f"[freshness]   {i+1}/{len(sids)} {sid}: "
                f"zip={rec.zip_sections} api={rec.api_sections} html={rec.html_sections} "
                f"-> {rec.classification}",
                file=sys.stderr,
            )

    # --- Phase 3: Replay section counts (sequential, CPU-intensive) ---
    if check_replay:
        if verbose:
            print(
                f"[freshness] Phase 3: replay section counts for {len(sids)} statutes "
                f"(sequential, may be slow) ...",
                file=sys.stderr,
            )
        for i, sid in enumerate(sids):
            rec = records[sid]
            replay_count, replay_err = _replay_section_count(sid)
            rec.replay_sections = replay_count
            rec.replay_error = replay_err
            if verbose:
                print(
                    f"[freshness]   replay {i+1}/{len(sids)} {sid}: "
                    f"replay={replay_count}"
                    + (f" err={replay_err}" if replay_err else ""),
                    file=sys.stderr,
                )
        if verbose:
            print("[freshness] Phase 3 done.", file=sys.stderr)

        # Re-classify with replay data
        for rec in records.values():
            rec.classification = _classify(rec)

    # Classify any remaining (shouldn't happen but safety)
    for rec in records.values():
        if rec.classification == "UNKNOWN":
            rec.classification = _classify(rec)

    return list(records.values())


# ---------------------------------------------------------------------------
# Sampling strategy
# ---------------------------------------------------------------------------

def _sample_sids(
    all_sids: list[str],
    n: int,
    prefer_source_incomplete: bool = True,
) -> list[str]:
    """Pick up to n statute IDs, preferring source_incomplete ones.

    If prefer_source_incomplete and strict runs exist, put those first.
    """
    if n >= len(all_sids):
        return list(all_sids)

    if prefer_source_incomplete:
        incomplete = _load_source_incomplete_set()
        # Split into two lists: preferred (source_incomplete) + rest
        preferred = [s for s in all_sids if s in incomplete]
        rest = [s for s in all_sids if s not in incomplete]
        selected = preferred[:n] + rest[: max(0, n - len(preferred))]
        return selected[:n]

    return all_sids[:n]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _print_report(records: list[FreshnessRecord], label: str) -> None:
    """Print a human-readable freshness audit summary."""
    from collections import Counter

    n = len(records)
    counts: Counter[str] = Counter(r.classification for r in records)

    print(f"\n=== Freshness Audit: {label} ({n} statutes) ===\n")
    print("Classification:")
    order = ["MATCH", "FRESH", "ZIP_STALE", "API_STALE", "NO_PIT",
             "ZIP_ONLY", "ZIP_API_MATCH", "ZIP_AHEAD", "API_BEHIND_ZIP",
             "ZIP_ONLY", "UNKNOWN"]
    shown: set[str] = set()
    for cls in order:
        if cls in counts and cls not in shown:
            pct = 100 * counts[cls] / n if n else 0
            note = _classification_note(cls)
            print(f"  {cls:<18} {counts[cls]:>4} ({pct:.0f}%){note}")
            shown.add(cls)
    for cls, cnt in sorted(counts.items()):
        if cls not in shown:
            pct = 100 * cnt / n if n else 0
            print(f"  {cls:<18} {cnt:>4} ({pct:.0f}%)")

    # Worst staleness
    has_replay = any(r.replay_sections >= 0 for r in records)
    stale_classes = ("ZIP_STALE", "API_STALE", "ZIP_AHEAD",
                     "STALE_ORACLE_CONFIRMED", "REPLAY_BEHIND")
    stale = [r for r in records if r.classification in stale_classes
             and r.html_sections > 0]
    stale_by_gap = sorted(
        stale,
        key=lambda r: (r.html_sections - r.zip_sections),
        reverse=True,
    )
    if stale_by_gap:
        print("\nWorst staleness (ZIP sections < HTML sections):")
        if has_replay:
            print(f"  {'Statute':<12}  {'ZIP':>5}  {'API':>5}  {'HTML':>5}  {'RPL':>5}  {'gap':>5}  PIT")
        else:
            print(f"  {'Statute':<12}  {'ZIP':>5}  {'API':>5}  {'HTML':>5}  {'gap':>5}  PIT")
        for r in stale_by_gap[:20]:
            gap = r.html_sections - r.zip_sections
            api_s = str(r.api_sections) if r.api_sections >= 0 else "n/a"
            pit = r.pit_version or "none"
            if has_replay:
                rpl_s = str(r.replay_sections) if r.replay_sections >= 0 else "n/a"
                print(f"  {r.statute_id:<12}  {r.zip_sections:>5}  {api_s:>5}  {r.html_sections:>5}  {rpl_s:>5}  {gap:>5}  {pit}")
            else:
                print(f"  {r.statute_id:<12}  {r.zip_sections:>5}  {api_s:>5}  {r.html_sections:>5}  {gap:>5}  {pit}")

    # Impact estimate
    zip_stale = counts.get("ZIP_STALE", 0)
    if zip_stale > 0:
        print("\nImpact estimate:")
        print(f"  {zip_stale} stale corpus oracles affect bench scores for these statutes.")
        print("  Refreshing them from PIT API or HTML could improve strict pass rate.")

    print()


def _classification_note(cls: str) -> str:
    notes = {
        "MATCH":         "  — ZIP == API == HTML",
        "FRESH":         "  — ZIP == HTML",
        "ZIP_STALE":     "  — ZIP has fewer sections than HTML",
        "API_STALE":     "  — API has fewer sections than HTML",
        "NO_PIT":        "  — No PIT API versions exist",
        "ZIP_ONLY":      "  — No network check performed",
        "ZIP_API_MATCH": "  — ZIP == API (no HTML check)",
    }
    return notes.get(cls, "")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def _save_csv(records: list[FreshnessRecord], label: str) -> Path:
    """Save records to data/freshness_reports/{label}_freshness.csv."""
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"{label}_freshness.csv"
    fieldnames = [
        "statute_id", "zip_sections", "api_sections", "html_sections",
        "replay_sections", "pit_version", "classification",
        "zip_error", "api_error", "html_error", "replay_error",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            w.writerow({
                "statute_id": r.statute_id,
                "zip_sections": r.zip_sections,
                "api_sections": r.api_sections,
                "html_sections": r.html_sections,
                "replay_sections": r.replay_sections,
                "pit_version": r.pit_version,
                "classification": r.classification,
                "zip_error": r.zip_error,
                "api_error": r.api_error,
                "html_error": r.html_error,
                "replay_error": r.replay_error,
            })
    return path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: object) -> None:
    sample: Optional[int] = getattr(args, "sample", None)
    corpus_mode: bool = getattr(args, "corpus", False)
    label: str = getattr(args, "label", "fresh_v1") or "fresh_v1"
    check_api: bool = not getattr(args, "no_api", False)
    check_html: bool = not getattr(args, "no_html", False)
    check_replay: bool = getattr(args, "replay", False)
    verbose: bool = getattr(args, "verbose", False)
    corpus_path: Optional[str] = getattr(args, "corpus_path", None)
    import os as _os
    max_workers: int = getattr(args, "workers", None) or max(8, _os.cpu_count() or 4)

    # Load statute IDs
    all_sids = _load_corpus(corpus_path)
    if not all_sids:
        print("error: no statute IDs found in corpus", file=sys.stderr)
        sys.exit(1)

    # Determine which statutes to audit
    if corpus_mode:
        sids = all_sids
        print(f"[freshness] Full corpus mode: {len(sids)} statutes", file=sys.stderr)
    elif sample is not None:
        sids = _sample_sids(all_sids, sample)
        print(
            f"[freshness] Sample mode: {len(sids)}/{len(all_sids)} statutes "
            f"(preferring source_incomplete)",
            file=sys.stderr,
        )
    else:
        # Default: sample 50
        sids = _sample_sids(all_sids, 50)
        print(
            f"[freshness] Default sample: {len(sids)} statutes",
            file=sys.stderr,
        )

    # Run audit
    records = run_freshness_audit(
        sids,
        check_api=check_api,
        check_html=check_html,
        check_replay=check_replay,
        rate_limit=1.0,
        verbose=verbose,
        max_workers=max_workers,
    )

    # Print report
    _print_report(records, label)

    # Save CSV
    csv_path = _save_csv(records, label)
    print(f"Saved: {csv_path}")
