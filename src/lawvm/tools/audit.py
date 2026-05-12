"""lawvm audit — cross-format consistency audit for Finnish statute data sources.

Detects oracle staleness: cases where the XML data sources (source corpus,
consolidated corpus, API) have not been updated with new sections that
ARE present in the HTML website and in LawVM's replay.

Subcommands:
    formats  <SID>              Full cross-format comparison for one statute
    staleness [--graph DIR]     Corpus-wide staleness scan (ZIP-only, no HTTP)
    html     <SID>              Fetch live HTML and compare vs XML

Usage:
    lawvm audit formats 2018/1121
    lawvm audit staleness --graph .tmp/corpus_graph_full/ --output .tmp/audit_staleness.csv
    lawvm audit staleness --top 20
    lawvm audit html 2018/1121
    lawvm audit html --from-file statutes.txt
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from lxml import etree

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.corrigendum_records import load_patch_records
from lawvm.finland.finlex_api import fetch_latest_pit_xml
from lawvm.finland.finlex_html import html_section_labels
from lawvm.tools.section_keys import (
    display_section_key,
    extract_oracle_sections,
    leaf_section_label,
    norm_section_label,
    reconcile_unique_unscoped_aliases,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent   # src/lawvm/tools/ → LawVM/
_TMP_DIR = _LAWVM_DIR / ".tmp"
_AUDIT_HTML_FORCE_REFRESH = False
_HTML_PRESENTATION_RANGE_RE = re.compile(
    r"^\s*\d+\s*[a-z]?\s*[–—-]\s*\d+\s*[a-z]?\s*§\s*$",
    re.IGNORECASE,
)


def _make_corpus_store():
    return get_corpus_store()

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# ---------------------------------------------------------------------------
# eId conversion helpers
# ---------------------------------------------------------------------------

_EID_RE = re.compile(r"^sec_(\d+)([a-z]?)$")


def _eid_to_human(eid: str) -> str:
    """Convert AKN eId to human-readable section label.

    sec_2   → '2 §'
    sec_2a  → '2 a §'
    """
    m = _EID_RE.match(eid)
    if not m:
        return eid
    num, suffix = m.group(1), m.group(2)
    if suffix:
        return f"{num} {suffix} §"
    return f"{num} §"


def _human_to_eid(label: str) -> str:
    """Approximate reverse: '2 a §' → 'sec_2a'."""
    s = re.sub(r'\s+', '', label.replace('§', '').strip()).lower()
    return f"sec_{s}"


# ---------------------------------------------------------------------------
# XML section extraction
# ---------------------------------------------------------------------------

def _extract_sections_xml(data: bytes) -> List[str]:
    """Return list of eIds for all <section> elements in AKN XML bytes."""
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return []
    sections = root.findall(f".//{{{_AKN_NS}}}section")
    return [s.get("eId", "") for s in sections if s.get("eId")]


def _get_date_consolidated(data: bytes) -> Optional[str]:
    """Extract dateConsolidated from AKN XML metadata."""
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return None
    for el in root.findall(f".//{{{_AKN_NS}}}FRBRdate"):
        if el.get("name") == "dateConsolidated":
            return el.get("date")
    return None


def _get_date_issued(data: bytes) -> Optional[str]:
    """Extract dateIssued from AKN XML metadata."""
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return None
    for el in root.findall(f".//{{{_AKN_NS}}}FRBRdate"):
        if el.get("name") == "dateIssued":
            return el.get("date")
    return None


# ---------------------------------------------------------------------------
# HTML section extraction
# ---------------------------------------------------------------------------

def _structured_html_section_labels(
    sid: str,
    *,
    force_refresh: bool = False,
) -> tuple[List[str], str]:
    """Fetch section labels from the structured Finlex HTML heading tree."""
    try:
        year, num = sid.split("/", 1)
    except ValueError:
        return [], f"bad statute id ({sid})"

    labels = html_section_labels(year, num, force_refresh=force_refresh)
    if labels is None:
        return [], f"fetch/parse failed ({_finlex_html_url(sid)})"
    cleaned: List[str] = []
    seen: set[str] = set()
    for label in labels:
        normalized = re.sub(r"\s+", " ", label).strip()
        if normalized and normalized not in seen:
            cleaned.append(normalized)
            seen.add(normalized)
    return cleaned, ""


def _html_section_key(label: str) -> str:
    s = re.sub(r"\s+", " ", label.strip())
    m = re.match(r"^(\d+)\s*([a-z]?)\s*§$", s, flags=re.I)
    if not m:
        return f"section:{norm_section_label(s.replace('§', ''))}"
    num = m.group(1)
    suffix = m.group(2).lower()
    return f"section:{num}{suffix}"


def _compare_html_vs_xml_sections(
    cons_data: bytes | None,
    html_labels: List[str],
) -> tuple[List[str], List[str], List[str], str]:
    """Return (cons_eids, missing_from_xml, extra_in_xml, noncommensurable_reason)."""
    cons_eids: List[str] = []
    if not cons_data:
        return cons_eids, list(html_labels), [], ""
    try:
        root = etree.fromstring(cons_data)
    except etree.XMLSyntaxError:
        return cons_eids, list(html_labels), [], ""

    oracle_sections = extract_oracle_sections(root)
    cons_eids = [
        el.get("eId", "")
        for el in oracle_sections.values()
        if isinstance(el, etree._Element) and el.get("eId")
    ]
    unscoped_counts: Dict[str, int] = {}
    for key in oracle_sections:
        label = leaf_section_label(key)
        if label:
            unscoped_counts[label] = unscoped_counts.get(label, 0) + 1
    duplicated_unscoped = {
        label
        for label, count in unscoped_counts.items()
        if count > 1
    }
    html_sections = {_html_section_key(label): label for label in html_labels}
    overlapping_duplicates = sorted(
        f"section:{label}"
        for label in duplicated_unscoped
        if f"section:{label}" in html_sections
    )
    if overlapping_duplicates:
        detail = ",".join(overlapping_duplicates[:10])
        return cons_eids, [], [], f"duplicate_unscoped_oracle_labels:{detail}"
    html_sections, oracle_sections = reconcile_unique_unscoped_aliases(html_sections, oracle_sections)

    missing_from_xml = [
        html_sections[key]
        for key in html_sections
        if key not in oracle_sections
    ]
    extra_in_xml = [
        display_section_key(key, oracle_sections[key])
        for key in oracle_sections
        if key not in html_sections
    ]
    return cons_eids, missing_from_xml, extra_in_xml, ""


def _html_has_presentation_range_heading(html_labels: List[str]) -> bool:
    """Return True when the HTML side contains a merged/range presentation label."""
    return any(_HTML_PRESENTATION_RANGE_RE.match(label or "") for label in html_labels)


def _html_presentation_range_labels(html_labels: List[str]) -> List[str]:
    """Return merged/range HTML labels that justify range-heading exclusion."""
    return [label for label in html_labels if _HTML_PRESENTATION_RANGE_RE.match(label or "")]


def _finlex_html_url(sid: str) -> str:
    """Construct Finlex HTML URL for a statute."""
    year, num = sid.split("/")
    base_num = num.split("-", 1)[0]
    return f"https://www.finlex.fi/fi/laki/ajantasa/{year}/{year}{int(base_num):04d}"


# ---------------------------------------------------------------------------
# Corrigendum lookup
# ---------------------------------------------------------------------------

def _corrigendum_count(sid: str) -> int:
    """Count official Finnish corrigendum items for a statute from the text corpus."""
    return sum(
        1
        for record in load_patch_records()
        if str(record.get("lang") or "fi").strip() == "fi"
        and str(record.get("statute_id") or "").strip() == sid
    )


# ---------------------------------------------------------------------------
# LawVM diff parsing
# ---------------------------------------------------------------------------

def _run_lawvm_diff(sid: str) -> Optional[Dict]:
    """Run lawvm diff for sid and parse its output.

    Returns dict with:
        n_compared: int
        n_extra: int
        extra_sections: List[str]   (human-readable labels)
        error: Optional[str]
    """
    import subprocess
    try:
        result = subprocess.run(
            ["uv", "run", "lawvm", "diff", sid],
            capture_output=True,
            text=True,
            cwd=str(_LAWVM_DIR),
            timeout=120,
        )
        out = result.stdout + result.stderr
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e), "n_compared": 0, "n_extra": 0, "extra_sections": []}

    # Parse "Sections : N compared  M perfect  P missing from replay  Q extra in replay"
    m = re.search(r'Sections\s*:\s*(\d+)\s+compared.*?(\d+)\s+extra in replay', out)
    n_compared = int(m.group(1)) if m else 0
    n_extra = int(m.group(2)) if m else 0

    # Parse EXTRA lines: "  EXTRA    2 a §         (in replay, not in oracle)"
    extra_sections = re.findall(r'EXTRA\s+([^\(]+?)\s+\(in replay', out)
    extra_sections = [s.strip() for s in extra_sections]

    return {
        "error": None,
        "n_compared": n_compared,
        "n_extra": n_extra,
        "extra_sections": extra_sections,
    }


# ---------------------------------------------------------------------------
# API XML fetch
# ---------------------------------------------------------------------------

def _fetch_api_xml(sid: str) -> Optional[bytes]:
    """Fetch the latest consolidated PIT XML for a statute from the API."""
    year, num = sid.split("/")
    xml, _pit_version = fetch_latest_pit_xml(year, num)
    return xml


# ---------------------------------------------------------------------------
# subcommand: formats
# ---------------------------------------------------------------------------

@dataclass
class FormatAuditResult:
    sid: str
    title: str = ""

    # Original XML (source corpus)
    orig_sections: int = 0
    orig_eids: List[str] = field(default_factory=list)
    orig_date: str = ""

    # Consolidated XML (consolidated corpus)
    cons_sections: int = 0
    cons_eids: List[str] = field(default_factory=list)
    cons_date: str = ""

    # API XML
    api_sections: int = 0
    api_eids: List[str] = field(default_factory=list)
    api_date: str = ""
    api_error: str = ""

    # HTML
    html_sections: int = 0
    html_labels: List[str] = field(default_factory=list)
    html_error: str = ""

    # LawVM replay
    replay_sections: int = 0
    replay_extra: List[str] = field(default_factory=list)
    replay_error: str = ""

    # Context
    amendments: List[str] = field(default_factory=list)
    corrigenda_count: int = 0


def _audit_formats(sid: str, skip_api: bool = False, skip_html: bool = False) -> FormatAuditResult:
    r = FormatAuditResult(sid=sid)

    cs = _make_corpus_store()

    # --- Original XML ---
    try:
        data = cs.read_source(sid)
        if data is not None:
            r.orig_eids = _extract_sections_xml(data)
            r.orig_sections = len(r.orig_eids)
            r.orig_date = _get_date_issued(data) or ""
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

    # --- Consolidated XML (consolidated corpus) ---
    try:
        data = cs.read_oracle(sid)
        if data is not None:
            r.cons_eids = _extract_sections_xml(data)
            r.cons_sections = len(r.cons_eids)
            r.cons_date = _get_date_consolidated(data) or ""
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

    # --- API XML ---
    if not skip_api:
        api_data = _fetch_api_xml(sid)
        if api_data:
            r.api_eids = _extract_sections_xml(api_data)
            r.api_sections = len(r.api_eids)
            r.api_date = _get_date_consolidated(api_data) or ""
        else:
            r.api_error = "fetch failed"

    # --- HTML ---
    if not skip_html:
        r.html_labels, r.html_error = _structured_html_section_labels(sid)
        r.html_sections = len(r.html_labels)

    # --- LawVM replay ---
    diff = _run_lawvm_diff(sid)
    if diff:
        if diff.get("error"):
            r.replay_error = diff["error"]
        else:
            r.replay_sections = diff["n_compared"] + diff["n_extra"]
            r.replay_extra = diff["extra_sections"]

    # --- Context ---
    # Load amendments from corpus graph if available
    default_graph = _LAWVM_DIR / ".tmp" / "corpus_graph_full"
    amend_path = default_graph / "amendments.json"
    if amend_path.exists():
        try:
            with open(amend_path) as f:
                amends = json.load(f)
            r.amendments = amends.get(sid, [])
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except (ValueError, OSError, KeyError):
            pass

    r.corrigenda_count = _corrigendum_count(sid)

    return r


def _print_formats_report(r: FormatAuditResult, skip_api: bool = False, skip_html: bool = False) -> None:
    sid = r.sid
    year, num = sid.split("/")

    print()
    title_part = f" ({r.title})" if r.title else ""
    print(f"=== Cross-Format Audit: {sid}{title_part} ===")
    print()

    # Table header
    col1 = 40
    col2 = 10
    col3 = 20
    header = f"{'Source':<{col1}}  {'Sections':>{col2}}  {'Date':<{col3}}"
    print(header)
    print("─" * (col1 + col2 + col3 + 4))

    def row(label, n, date, note=""):
        date_str = date if date else "(unknown)"
        n_str = str(n) if n >= 0 else "N/A"
        note_str = f"  [{note}]" if note else ""
        print(f"  {label:<{col1-2}}  {n_str:>{col2}}  {date_str:<{col3}}{note_str}")

    row("Original XML (source corpus)", r.orig_sections, r.orig_date, "enacted")
    row("Consolidated XML (cons.zip)", r.cons_sections, r.cons_date)
    if skip_api:
        row("Consolidated XML (API)", -1, "", "skipped (--no-api)")
    elif r.api_error:
        row("Consolidated XML (API)", -1, "", f"error: {r.api_error}")
    else:
        api_match = ""
        if r.api_sections >= 0 and r.cons_sections >= 0:
            api_match = "matches ZIP" if r.api_sections == r.cons_sections else "DIFFERS from ZIP"
        row("Consolidated XML (API)", r.api_sections, r.api_date, api_match)
    if skip_html:
        row("HTML website (finlex.fi)", -1, "", "skipped (--no-html)")
    elif r.html_error:
        row("HTML website (finlex.fi)", -1, "", f"error: {r.html_error}")
    else:
        row("HTML website (finlex.fi)", r.html_sections, "(live)")
    if r.replay_error:
        row("LawVM replay", -1, "", f"error: {r.replay_error}")
    else:
        row("LawVM replay", r.replay_sections, "(computed)")

    print()

    # Amendments and corrigenda
    if r.amendments:
        print(f"Amendments ({len(r.amendments)}): {', '.join(r.amendments)}")
    else:
        print("Amendments: none recorded in corpus graph")
    print(f"Corrigenda: {r.corrigenda_count} official item(s) in corrigendum text corpus")

    print()

    # Divergence analysis
    issues: List[str] = []
    ok_notes: List[str] = []

    xml_sections = r.cons_sections  # primary oracle reference

    # API vs ZIP
    if not skip_api and not r.api_error and r.api_sections >= 0 and r.cons_sections >= 0:
        if r.api_sections == r.cons_sections:
            ok_notes.append("API XML matches ZIP XML exactly")
        else:
            issues.append(
                f"API XML ({r.api_sections}) differs from ZIP XML ({r.cons_sections}) — "
                "one is more current than the other"
            )

    # HTML vs XML staleness
    if not skip_html and not r.html_error and r.html_sections > 0 and xml_sections > 0:
        xml_eid_set = set(r.cons_eids)
        missing_from_xml = [
            lbl for lbl in r.html_labels
            if _human_to_eid(lbl) not in xml_eid_set
        ]
        if missing_from_xml:
            issues.append(
                f"ORACLE STALE: HTML has {len(missing_from_xml)} section(s) not in XML:\n"
                + "  " + ", ".join(missing_from_xml)
            )
        elif r.html_sections < xml_sections:
            issues.append(
                f"HTML has fewer sections ({r.html_sections}) than consolidated XML "
                f"({xml_sections}) — XML may have extra erroneous entries"
            )
        else:
            ok_notes.append(f"HTML section count matches XML ({r.html_sections})")

    # Replay extra vs oracle staleness
    if not r.replay_error and r.replay_extra:
        issues.append(
            f"LawVM replay has {len(r.replay_extra)} EXTRA section(s) "
            "(in replay, not in XML oracle):\n"
            + "  " + ", ".join(r.replay_extra)
        )
    elif not r.replay_error and r.replay_sections > 0:
        ok_notes.append("LawVM replay section count matches XML oracle")

    # Staleness convergence: replay+HTML both disagree with XML → XML is stale
    if (
        not skip_html
        and not r.html_error
        and not r.replay_error
        and r.replay_extra
        and len(r.replay_extra) == len([
            lbl for lbl in r.html_labels
            if _human_to_eid(lbl) not in set(r.cons_eids)
        ])
    ):
        issues.append(
            "Diagnosis: XML consolidation pipeline failed to incorporate new sections\n"
            "  (replay and HTML agree; XML oracle is stale)"
        )

    for note in ok_notes:
        print(f"  OK  {note}")
    print()
    for issue in issues:
        first, *rest = issue.split("\n")
        print(f"  !!  {first}")
        for line in rest:
            print(f"      {line}")
        print()

    # Finlex URL hint
    print(f"  Finlex URL: {_finlex_html_url(sid)}")
    print(f"  API URL:    https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute-consolidated/{year}/{num}/fin@")
    print()


def cmd_formats(args) -> None:
    sid = args.statute_id
    # Normalize: accept both 2018/1121 and 1121/2018
    if "/" not in sid:
        print(f"ERROR: invalid statute ID '{sid}' (expected YEAR/NUM)", file=sys.stderr)
        sys.exit(1)
    parts = sid.split("/")
    if len(parts[0]) == 4:
        pass  # already YEAR/NUM
    else:
        sid = f"{parts[1]}/{parts[0]}"

    skip_api = getattr(args, "no_api", False)
    skip_html = getattr(args, "no_html", False)

    print(f"Auditing {sid}...")
    r = _audit_formats(sid, skip_api=skip_api, skip_html=skip_html)
    _print_formats_report(r, skip_api=skip_api, skip_html=skip_html)


# ---------------------------------------------------------------------------
# subcommand: staleness (corpus-wide ZIP-only scan)
# ---------------------------------------------------------------------------

@dataclass
class StalenessEntry:
    sid: str
    orig_sections: int
    cons_sections: int
    n_amendments: int
    latest_amendment_year: int
    section_delta: int          # cons - orig (positive = XML added sections)
    stale_flag: bool            # True if potentially stale


def _latest_amendment_year(amendments: List[str]) -> int:
    years = []
    for a in amendments:
        parts = a.split("/")
        for p in parts:
            if p.isdigit() and len(p) == 4:
                years.append(int(p))
    return max(years) if years else 0


def _corpus_staleness_scan(
    graph_dir: Path,
    min_year: int = 2020,
) -> List[StalenessEntry]:
    """Scan all statutes in the farchive and flag potentially stale ones.

    A statute is flagged as potentially stale if:
    - it has amendments
    - the consolidated XML has the SAME section count as the original
    - the latest amendment is from >= min_year

    Returns list sorted by (n_amendments DESC, latest_amendment_year DESC).
    """
    cs = _make_corpus_store()

    # Load amendments index
    amendments_index: Dict[str, List[str]] = {}
    amend_path = graph_dir / "amendments.json"
    if amend_path.exists():
        try:
            with open(amend_path) as f:
                amendments_index = json.load(f)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except (OSError, ValueError) as e:
            print(f"WARNING: could not load amendments.json: {e}", file=sys.stderr)

    print("Scanning corpus for staleness (farchive, no HTTP)...")
    print(f"  amendments from: {amend_path}")
    print()

    # Build orig section counts from source corpus
    print("  Pass 1: reading source corpus for original section counts...")
    orig_counts: Dict[str, int] = {}
    for sid in cs.list_statute_ids():
        try:
            data = cs.read_source(sid)
            if data is not None:
                orig_counts[sid] = len(_extract_sections_xml(data))
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

    print(f"  Pass 1 done: {len(orig_counts)} statutes")

    # Build consolidated section counts from consolidated corpus
    print("  Pass 2: reading consolidated corpus for consolidated section counts...")
    cons_counts: Dict[str, int] = {}
    for sid in cs.oracle_path_index():
        try:
            data = cs.read_oracle(sid)
            if data is not None:
                cons_counts[sid] = len(_extract_sections_xml(data))
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

    print(f"  Pass 2 done: {len(cons_counts)} statutes")
    print()

    # Compute staleness flags
    entries: List[StalenessEntry] = []
    for sid in sorted(cons_counts):
        orig_n = orig_counts.get(sid, -1)
        cons_n = cons_counts[sid]
        amendments = amendments_index.get(sid, [])
        n_amendments = len(amendments)
        latest_year = _latest_amendment_year(amendments)

        if orig_n < 0:
            # Not in source corpus (historical/pre-digitization) — skip
            continue

        delta = cons_n - orig_n
        stale = (
            n_amendments > 0
            and cons_n == orig_n   # XML didn't grow despite amendments
            and latest_year >= min_year
        )
        entries.append(StalenessEntry(
            sid=sid,
            orig_sections=orig_n,
            cons_sections=cons_n,
            n_amendments=n_amendments,
            latest_amendment_year=latest_year,
            section_delta=delta,
            stale_flag=stale,
        ))

    return entries


def cmd_staleness(args) -> None:
    graph_dir_arg = getattr(args, "graph", None)
    output_arg = getattr(args, "output", None)
    top_n = getattr(args, "top", None)
    min_year = getattr(args, "min_year", 2020)

    if graph_dir_arg:
        graph_dir = Path(graph_dir_arg)
    else:
        graph_dir = _LAWVM_DIR / ".tmp" / "corpus_graph_full"

    if not graph_dir.exists():
        print(f"WARNING: graph dir not found: {graph_dir} — amendments data unavailable",
              file=sys.stderr)

    entries = _corpus_staleness_scan(graph_dir, min_year=min_year)

    stale = [e for e in entries if e.stale_flag]
    stale.sort(key=lambda e: (-e.n_amendments, -e.latest_amendment_year))

    # Determine output path
    if output_arg:
        out_path = Path(output_arg)
    else:
        _TMP_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _TMP_DIR / "audit_staleness.csv"

    # Write CSV (all entries)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "statute_id", "orig_sections", "cons_sections", "section_delta",
            "n_amendments", "latest_amendment_year", "stale_flag",
        ])
        for e in entries:
            w.writerow([
                e.sid, e.orig_sections, e.cons_sections, e.section_delta,
                e.n_amendments, e.latest_amendment_year,
                "1" if e.stale_flag else "0",
            ])

    print("=== Staleness Scan Results ===")
    print()
    print(f"Total statutes scanned : {len(entries)}")
    print(f"Potentially stale      : {len(stale)} "
          f"(amended, XML section count unchanged since enactment, latest amend >= {min_year})")
    print(f"Full CSV written to    : {out_path}")
    print()

    # Show distribution
    with_amendments = [e for e in entries if e.n_amendments > 0]
    grew = [e for e in with_amendments if e.section_delta > 0]
    shrank = [e for e in with_amendments if e.section_delta < 0]
    unchanged = [e for e in with_amendments if e.section_delta == 0]
    print(f"Among amended statutes ({len(with_amendments)} total):")
    print(f"  XML grew (delta > 0) : {len(grew):5d}")
    print(f"  XML unchanged        : {len(unchanged):5d}  ← stale candidates")
    print(f"  XML shrank           : {len(shrank):5d}")
    print()

    # Show top N stale statutes
    display = stale[:top_n] if top_n else stale[:50]
    if display:
        print(f"Top {len(display)} potentially stale statutes "
              f"(sorted by amendment count):")
        print()
        hdr = f"  {'Statute':<15}  {'Orig':>6}  {'Cons':>6}  {'Delta':>6}  {'N_amend':>8}  {'LatestAmend':>12}"
        print(hdr)
        print("  " + "-" * 60)
        for e in display:
            flag = " *" if e.stale_flag else ""
            print(
                f"  {e.sid:<15}  {e.orig_sections:>6}  {e.cons_sections:>6}  "
                f"{e.section_delta:>+6}  {e.n_amendments:>8}  {e.latest_amendment_year:>12}{flag}"
            )
    else:
        print("No potentially stale statutes found with current criteria.")

    print()
    print("Note: 'stale' = n_amendments > 0 AND cons_sections == orig_sections "
          f"AND latest_amendment_year >= {min_year}")
    print("These are candidates for `lawvm audit html` verification (requires HTTP).")


# ---------------------------------------------------------------------------
# subcommand: html (fetch HTML and compare vs XML)
# ---------------------------------------------------------------------------

@dataclass
class HtmlAuditResult:
    sid: str
    cons_sections: int
    cons_eids: List[str]
    html_sections: int
    html_labels: List[str]
    html_error: str = ""
    missing_from_xml: List[str] = field(default_factory=list)  # in HTML, not in XML
    extra_in_xml: List[str] = field(default_factory=list)      # in XML, not in HTML
    noncommensurable_reason: str = ""

def _audit_html_one(sid: str) -> HtmlAuditResult:
    cons_eids: List[str] = []
    cons_n = 0
    cons_data: bytes | None = None
    try:
        from lawvm.finland.corpus import get_corpus
        cs = get_corpus()
        cons_data = cs.read_oracle(sid)
        if cons_data is not None:
            cons_eids = _extract_sections_xml(cons_data)
            cons_n = len(cons_eids)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

    html_labels: List[str] = []
    html_n = 0
    html_error = ""
    if _AUDIT_HTML_FORCE_REFRESH:
        html_labels, html_error = _structured_html_section_labels(
            sid,
            force_refresh=True,
        )
    else:
        html_labels, html_error = _structured_html_section_labels(sid)
    html_n = len(html_labels)

    _cons_eids_cmp, missing_from_xml, extra_in_xml, noncommensurable_reason = _compare_html_vs_xml_sections(
        cons_data,
        html_labels,
    )

    return HtmlAuditResult(
        sid=sid,
        cons_sections=cons_n,
        cons_eids=cons_eids,
        html_sections=html_n,
        html_labels=html_labels,
        html_error=html_error,
        missing_from_xml=missing_from_xml,
        extra_in_xml=extra_in_xml,
        noncommensurable_reason=noncommensurable_reason,
    )

def _print_html_result(r: HtmlAuditResult) -> None:
    status = "STALE" if r.missing_from_xml else ("EXTRA_IN_XML" if r.extra_in_xml else "OK")
    if r.html_error:
        status = "HTML_FAIL"
    elif r.noncommensurable_reason:
        status = "NONCOMMENSURABLE"

    print(f"  {r.sid:<16}  XML={r.cons_sections:>4}  HTML={r.html_sections:>4}  "
          f"missing_from_xml={len(r.missing_from_xml):>3}  "
          f"extra_in_xml={len(r.extra_in_xml):>3}  [{status}]")
    if r.html_error:
        print(f"    ERROR: {r.html_error}")
    if r.missing_from_xml:
        print(f"    Missing from XML: {', '.join(r.missing_from_xml)}")
    if r.extra_in_xml:
        print(f"    Extra in XML (not in HTML): {', '.join(r.extra_in_xml)}")
    if r.noncommensurable_reason:
        print(f"    Noncommensurable: {r.noncommensurable_reason}")


def cmd_html(args) -> None:
    global _AUDIT_HTML_FORCE_REFRESH
    from_file = getattr(args, "from_file", None)
    exclude_range_headings = bool(getattr(args, "exclude_range_headings", False))

    if from_file:
        with open(from_file) as f:
            sids = [line.strip() for line in f if line.strip()]
    else:
        sids = [sid for sid in getattr(args, "statute_ids", []) if sid]
        if not sids:
            print("ERROR: provide one or more statute_ids or --from-file", file=sys.stderr)
            sys.exit(1)

    prior_force_refresh = _AUDIT_HTML_FORCE_REFRESH
    _AUDIT_HTML_FORCE_REFRESH = True
    try:
        if getattr(args, "json", False):
            results = []
            skipped_range_heading_statutes = []
            for sid in sids:
                r = _audit_html_one(sid)
                if exclude_range_headings and _html_has_presentation_range_heading(r.html_labels):
                    skipped_range_heading_statutes.append(
                        {
                            "sid": r.sid,
                            "rule_id": "fi_audit_html_presentation_range_heading_excluded",
                            "phase": "adjudication",
                            "family": "presentation_cleanup",
                            "reason": "HTML range-heading presentation quirk excluded from audit denominator",
                            "html_labels": _html_presentation_range_labels(r.html_labels),
                        }
                    )
                    continue
                results.append(
                    {
                        "sid": r.sid,
                        "cons_sections": r.cons_sections,
                        "cons_eids": r.cons_eids,
                        "html_sections": r.html_sections,
                        "html_labels": r.html_labels,
                        "html_error": r.html_error,
                        "missing_from_xml": r.missing_from_xml,
                        "extra_in_xml": r.extra_in_xml,
                        "noncommensurable_reason": r.noncommensurable_reason,
                    }
                )
            if exclude_range_headings:
                print(
                    json.dumps(
                        {
                            "skipped_range_headings": len(skipped_range_heading_statutes),
                            "skipped_range_heading_statutes": skipped_range_heading_statutes,
                            "results": results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            return

        print(f"HTML vs XML audit for {len(sids)} statute(s)...")
        print()
        print(f"  {'Statute':<16}  {'XML':>7}  {'HTML':>7}  {'MissingXML':>13}  {'ExtraXML':>11}  Status")
        print("  " + "-" * 75)

        stale_count = 0
        skipped = 0
        for sid in sids:
            r = _audit_html_one(sid)
            if exclude_range_headings and _html_has_presentation_range_heading(r.html_labels):
                skipped += 1
                continue
            _print_html_result(r)
            if r.missing_from_xml:
                stale_count += 1

        print()
        if len(sids) > 1:
            if exclude_range_headings:
                denom = len(sids) - skipped
                print(
                    f"Summary: {stale_count}/{denom} statutes have sections in HTML missing from XML "
                    f"({skipped} skipped for range-heading presentation quirks)"
                )
            else:
                print(f"Summary: {stale_count}/{len(sids)} statutes have sections in HTML missing from XML")
    finally:
        _AUDIT_HTML_FORCE_REFRESH = prior_force_refresh


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_body_pairing(args) -> None:
    """Run body pairing audit for one or more statutes."""
    import json as _json

    from lawvm.finland.body_pairing import audit_statute_body_pairing

    statute_ids: list[str] = list(getattr(args, "statute_ids", []) or [])
    from_file = getattr(args, "from_file", None)
    if from_file:
        with open(from_file, encoding="utf-8") as fh:
            for line in fh:
                sid = line.strip()
                if sid and not sid.startswith("#"):
                    statute_ids.append(sid)
    if not statute_ids:
        print("ERROR: no statute IDs provided", file=sys.stderr)
        sys.exit(1)

    limit = getattr(args, "limit", 0) or 0
    if limit > 0:
        statute_ids = statute_ids[:limit]

    anomalies_only = getattr(args, "anomalies_only", False)
    emit_json = getattr(args, "json", False)

    all_results: list[dict] = []
    total_amendments = 0
    total_findings = 0
    total_foreign = 0
    total_unmatched = 0
    total_repeal_blocked = 0

    for statute_id in statute_ids:
        results = audit_statute_body_pairing(statute_id)
        for r in results:
            total_amendments += 1
            total_findings += len(r.findings)
            total_foreign += r.claimed_foreign
            total_unmatched += r.unmatched
            total_repeal_blocked += r.repeal_blocked

            if anomalies_only and not r.has_anomalies:
                continue

            if emit_json:
                all_results.append(r.to_dict())
            else:
                status_parts = []
                if r.claimed_foreign > 0:
                    status_parts.append(f"foreign={r.claimed_foreign}")
                if r.unmatched > 0:
                    status_parts.append(f"unmatched={r.unmatched}")
                if r.repeal_blocked > 0:
                    status_parts.append(f"repeal_blocked={r.repeal_blocked}")
                status = ", ".join(status_parts) if status_parts else "ok"
                print(
                    f"  {r.amendment_id}: "
                    f"inventory={r.inventory_count} "
                    f"current={r.claimed_current} "
                    f"[{status}]"
                )
                for f in r.findings:
                    print(f"    {f.kind}: {f.detail}")

    if emit_json:
        print(_json.dumps(all_results, indent=2, ensure_ascii=False))
    else:
        print()
        print("=== Body pairing audit summary ===")
        print(f"  Statutes:        {len(statute_ids)}")
        print(f"  Amendments:      {total_amendments}")
        print(f"  Foreign units:   {total_foreign}")
        print(f"  Unmatched units: {total_unmatched}")
        print(f"  Repeal blocked:  {total_repeal_blocked}")
        print(f"  Total findings:  {total_findings}")


def main(args) -> None:
    audit_cmd = getattr(args, "audit_cmd", None)

    if audit_cmd == "formats":
        cmd_formats(args)
    elif audit_cmd == "staleness":
        cmd_staleness(args)
    elif audit_cmd == "html":
        cmd_html(args)
    elif audit_cmd == "body-pairing":
        cmd_body_pairing(args)
    elif audit_cmd is None:
        print("Usage: lawvm audit <subcommand>", file=sys.stderr)
        print("  formats      <SID>               cross-format comparison for one statute", file=sys.stderr)
        print("  staleness    [--graph DIR]        corpus-wide staleness scan (ZIP-only)", file=sys.stderr)
        print("  html         <SID>               fetch HTML and compare vs XML", file=sys.stderr)
        print("  body-pairing <SID>               body-driven pairing anomaly audit", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Unknown audit subcommand: {audit_cmd}", file=sys.stderr)
        sys.exit(1)
