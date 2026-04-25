"""lawvm verify-chain — per-amendment PIT checkpoint verification.

For each amendment in a statute's chain, compare LawVM's intermediate replay
state against the Finlex PIT XML snapshot (fin@YYYYNNNN) in consolidated corpus.
Produces a blame matrix showing where divergence first appears.

PIT identifier format: YYYY + zero-padded 4-digit number.
  Amendment 680/2021  -> fin@20210680
  Amendment 46/2026   -> fin@20260046
  Amendment 1268/2020 -> fin@20201268

Usage:
    lawvm verify-chain 2020/369
    lawvm verify-chain 2020/369 --no-html
    lawvm verify-chain 2020/369 2018/1121
    lawvm verify-chain 2020/369 --output /tmp/mydir/
"""
from __future__ import annotations

import contextlib
import json
import io
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode  # noqa: F401

import Levenshtein
from lxml import etree

from lawvm.finland.grafter import (
    get_corpus,
    _resolve_applicable_amendment_records,
    process_muutoslaki,
)
from lawvm.finland.corpus import list_cached_consolidated_pit_locators
from lawvm.finland.statute import StatuteContext, ReplayState
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.tools.editorial_hygiene import normalize_kumottu_stubs

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent   # src/lawvm/tools/ -> LawVM/
_TMP_DIR = _LAWVM_DIR / ".tmp"
_SCRIPTS_DIR = _LAWVM_DIR / "scripts"
_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# ---------------------------------------------------------------------------
# Import proper HTML section extractor from scripts/
# ---------------------------------------------------------------------------
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from html_section_extractor import extract_sections_from_html as _extract_sections_from_html  # ty: ignore[unresolved-import]
    _HTML_EXTRACTOR_AVAILABLE = True
except ImportError:
    _HTML_EXTRACTOR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Helpers: text normalization and scoring
# ---------------------------------------------------------------------------

def _normalize_text(t: str) -> str:
    """Strip editorial annotations that appear in oracles but not in replay."""
    t = normalize_kumottu_stubs(t)
    t = re.sub(r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)', '', t)
    t = re.sub(r'Aiempi sanamuoto kuuluu:', '', t)
    return t


def _clean(t: str) -> str:
    return re.sub(r'[^a-z0-9äöå]', '', _normalize_text(t).lower())


def _el_text(el: etree._Element) -> str:
    return etree.tostring(el, method="text", encoding="unicode").strip()


def _score_texts(a: str, b: str) -> float:
    ca, cb = _clean(a), _clean(b)
    if not ca and not cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    return Levenshtein.ratio(ca, cb)


# ---------------------------------------------------------------------------
# Helpers: section extraction from XML trees
# ---------------------------------------------------------------------------

_EID_VERSION_RE = re.compile(r'^(sec_\d+[a-z]?)v\d+$')
_EID_PLAIN_RE = re.compile(r'^sec_(\d+)([a-z]?)$')
# Matches the section part of a compound eId like 'part_1__chp_1v20xx__sec_3v20xx'
_EID_COMPOUND_SEC_RE = re.compile(r'(?:^|__)sec_(\d+[a-z]?)(?:v\d+)?$')


def _strip_version_suffix(eid: str) -> str:
    """Strip version tag from eId.

    Simple eIds:
      'sec_8av20200680' -> 'sec_8a'
      'sec_5v20200680'  -> 'sec_5'
      'sec_1'           -> 'sec_1'

    Compound eIds (nested chapters/parts):
      'part_1__chp_1__sec_3v20191505' -> 'sec_3'
      'part_2__chp_2v20201256__sec_6av20210299' -> 'sec_6a'
    """
    # Compound eId: extract the sec_ component at the end
    if '__sec_' in eid or eid.startswith('sec_'):
        # Find the last sec_ segment
        # Split on __ and find the sec_ part
        parts = eid.split('__')
        for part in reversed(parts):
            # Strip version suffix from this segment
            m = re.match(r'^(sec_\d+[a-z]?)(?:v\d+)?$', part)
            if m:
                return m.group(1)
    return eid


def _eid_to_label(eid: str) -> str:
    """Convert eId (possibly compound) to display label.

    'sec_8a' -> '§8a'
    'sec_11' -> '§11'
    'part_1__chp_1__sec_3v20191505' -> '§3'
    """
    base = _strip_version_suffix(eid)
    m = _EID_PLAIN_RE.match(base)
    if m:
        num, suffix = m.group(1), m.group(2)
        return f"§{num}{suffix}"
    return eid


def _extract_sections_from_xml(data: bytes) -> Dict[str, str]:
    """Extract {canonical_eid -> text} from AKN XML bytes.

    Version suffixes and compound path prefixes are stripped for cross-PIT
    comparison. Returns dict keyed by canonical 'sec_N' or 'sec_Na'.

    Handles both simple eIds ('sec_8av20200680') and compound eIds
    ('part_1__chp_1__sec_3v20191505').
    """
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return {}
    result: Dict[str, str] = {}
    for sec in root.findall(f".//{{{_AKN_NS}}}section"):
        eid = sec.get("eId", "")
        if not eid:
            continue
        canonical = _strip_version_suffix(eid)
        if not canonical.startswith("sec_"):
            continue  # skip non-section elements caught by findall
        if canonical not in result:
            result[canonical] = _el_text(sec)
    return result


def _extract_sections_from_ir(ir: "IRNode") -> Dict[str, str]:
    """Extract {canonical_eid -> text} from an IRNode tree."""
    from lawvm.core.ir_helpers import irnode_to_text
    result: Dict[str, str] = {}
    def _walk(node: IRNode):
        if node.kind == 'section' and node.label:
            eid = node.attrs.get('eId', '') or f"sec_{node.label}"
            canonical = _strip_version_suffix(eid)
            if canonical not in result:
                result[canonical] = irnode_to_text(node)
        for c in node.children:
            _walk(c)
    _walk(ir)
    return result


# ---------------------------------------------------------------------------
# Helpers: kumottu detection and live-section counting
# ---------------------------------------------------------------------------

_KUMOTTU_MAX_LEN = 200  # chars; short sections containing "kumottu" are repealed stubs


def _is_kumottu(text: str) -> bool:
    """Return True if this section is a repealed (kumottu) stub."""
    return len(text) < _KUMOTTU_MAX_LEN and "kumottu" in text.lower()


def count_live_sections(xml_bytes: bytes) -> Tuple[int, int]:
    """Count non-kumottu sections in AKN XML.

    Returns (live_count, total_count).
    Kumottu = text is short (<200 chars) and contains 'kumottu'.
    """
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return 0, 0
    ns = {"akn": _AKN_NS}
    total = 0
    live = 0
    for sec in tree.findall(".//akn:section", ns):
        total += 1
        txt = etree.tostring(sec, method="text", encoding="unicode").strip()
        if not _is_kumottu(txt):
            live += 1
    return live, total


# ---------------------------------------------------------------------------
# Helpers: HTML label normalisation
# ---------------------------------------------------------------------------

_HTML_LABEL_RE = re.compile(r'^(\d+)\s*([a-z]?)\s*§$', re.IGNORECASE)


def _normalise_html_label(raw: str) -> str:
    """Convert extractor label format to verify_chain format.

    html_section_extractor returns labels like '1 §', '2 a §', '13 b §'.
    verify_chain uses '§1', '§2a', '§13b'.
    """
    m = _HTML_LABEL_RE.match(raw.strip())
    if m:
        num, suffix = m.group(1), m.group(2).lower()
        return f"§{num}{suffix}"
    return raw  # pass through unchanged if unexpected format


# ---------------------------------------------------------------------------
# Helpers: PIT map
# ---------------------------------------------------------------------------

def _build_pit_map(sid: str) -> Dict[str, str]:
    """Map amendment_id -> farchive locator for PIT snapshots of sid.

    Returns: {'2020/680': 'finlex://sd-cons/YYYY/NUM/fin@20200680/main.xml', ...}

    PIT identifier format: YYYY + zero-padded 4-digit statute number.
    E.g. amendment 680/2021 -> fin@20210680; amendment 46/2026 -> fin@20260046.
    """
    cs = get_corpus()
    if getattr(cs, "_archive", None) is None:
        return {}

    # Enumerate versioned PIT locators for this statute
    pit_re = re.compile(r"/fin@(\d{8})/main\.xml$")

    pit_map: Dict[str, str] = {}
    for locator in list_cached_consolidated_pit_locators(cs, sid):
        m = pit_re.search(locator)
        if not m:
            continue
        pit_id = m.group(1)          # e.g. '20200680'
        year = pit_id[:4]            # '2020'
        num = int(pit_id[4:])        # 680 (strips leading zeros)
        amid = f"{year}/{num}"       # '2020/680'
        pit_map[amid] = locator
    return pit_map


# ---------------------------------------------------------------------------
# Helpers: HTML section extraction
# ---------------------------------------------------------------------------

_HTML_SECTION_RE = re.compile(r'(\d+)\s*([a-h]?)\s*§')
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _finlex_html_url(sid: str) -> Optional[str]:
    """Return Finlex ajantasa URL, or None if sid lacks a numeric statute number."""
    year, num = sid.split("/")
    try:
        base_num = num.split("-", 1)[0]
        return f"https://www.finlex.fi/fi/laki/ajantasa/{year}/{year}{int(base_num):04d}"
    except ValueError:
        return None


def _fetch_html_sections(sid: str, archive_db: Optional[Path] = None) -> Tuple[List[str], str]:
    """Fetch Finlex HTML and return (section_labels, error_or_empty).

    Uses Farchive for caching if archive_db is provided.
    Returns (list_of_labels, error_string).
    """
    url = _finlex_html_url(sid)
    if url is None:
        return [], f"non-numeric statute num in '{sid}' — HTML fetch skipped"

    if archive_db is not None:
        try:
            from farchive import Farchive
            arch = Farchive(archive_db)
            raw = arch.get(url)
            if raw is None:
                return [], f"fetch failed ({url})"
            html = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return [], f"archive fetch error: {e}"
    else:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                enc = resp.headers.get_content_charset("utf-8")
                html = raw.decode(enc, errors="replace")
        except Exception as e:
            return [], f"fetch failed: {e}"

    if _HTML_EXTRACTOR_AVAILABLE:
        raw_labels = _extract_sections_from_html(raw)
        labels = [_normalise_html_label(lbl) for lbl in raw_labels]
    else:
        # Fallback: crude regex (body text references may inflate counts)
        found: Dict[Tuple[int, str], str] = {}
        for m in _HTML_SECTION_RE.finditer(html):
            num = int(m.group(1))
            suffix = m.group(2).strip().lower()
            key = (num, suffix)
            if key not in found:
                label = f"§{num}{suffix}"
                found[key] = label
        labels = [v for _, v in sorted(found.items())]
    return labels, ""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SectionResult:
    eid: str                    # canonical eId, e.g. 'sec_8a'
    label: str                  # display label, e.g. '§8a'
    replay_text: Optional[str]  # text from replay at this checkpoint (None = absent)
    pit_text: Optional[str]     # text from PIT XML (None = absent or no PIT)
    score: Optional[float]      # similarity score (None if either side absent)
    is_new: bool = False        # first appeared at this checkpoint
    modified: bool = False      # existed before, text changed at this checkpoint


@dataclass
class AmendmentResult:
    amendment_id: str
    pit_path: Optional[str]       # path in ZIP, or None if no PIT
    pit_label: Optional[str]      # e.g. 'fin@20200680'
    replay_section_count: int = 0
    pit_section_count: int = 0
    overall_score: Optional[float] = None   # mean score across matched sections
    new_sections: List[str] = field(default_factory=list)   # labels
    modified_sections: List[str] = field(default_factory=list)
    sections: Dict[str, SectionResult] = field(default_factory=dict)
    error: str = ""


@dataclass
class HtmlComparison:
    html_sections: int = 0
    html_labels: List[str] = field(default_factory=list)
    replay_sections: int = 0
    replay_labels: List[str] = field(default_factory=list)
    missing_from_replay: List[str] = field(default_factory=list)  # in HTML, not replay
    extra_in_replay: List[str] = field(default_factory=list)      # in replay, not HTML
    # PIT kumottu breakdown (from the latest PIT XML snapshot available)
    pit_total: int = 0        # total sections in latest PIT
    pit_live: int = 0         # non-kumottu sections in latest PIT
    pit_kumottu: int = 0      # kumottu (repealed stub) sections in latest PIT
    error: str = ""


@dataclass
class ChainVerificationResult:
    statute_id: str
    verified_at: str
    base_section_count: int
    amendment_results: List[AmendmentResult] = field(default_factory=list)
    html_comparison: Optional[HtmlComparison] = None
    pit_coverage: int = 0     # how many amendments have PIT versions
    total_amendments: int = 0


# ---------------------------------------------------------------------------
# Core verification logic
# ---------------------------------------------------------------------------

def verify_chain(
    sid: str,
    skip_html: bool = False,
    output_dir: Optional[Path] = None,
    mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
) -> ChainVerificationResult:
    """Verify a statute's amendment chain against PIT XML checkpoints.

    Returns ChainVerificationResult with per-amendment section-level data.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = ChainVerificationResult(statute_id=sid, verified_at=now, base_section_count=0)

    cs = get_corpus()

    # --- Load base statute ---
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: statute {sid} not found in corpus", file=sys.stderr)
        return result

    # Apply Population A corrigendum (same as replay_xml)
    try:
        from lawvm.finland.corrigendum import extract_inline_corrections as _extract_inline_corr
        _, xml_bytes = _extract_inline_corr(xml_bytes, sid)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)

    # Base section count (before any amendments)
    base_sections = _extract_sections_from_ir(state.ir)
    result.base_section_count = len(base_sections)
    prev_sections: Dict[str, str] = dict(base_sections)

    # --- Resolve amendment chain ---
    amendment_records, cutoff_date, oracle_version_amendment_id = _resolve_applicable_amendment_records(
        sid, mode
    )
    result.total_amendments = len(amendment_records)

    # --- Build PIT map ---
    pit_map = _build_pit_map(sid)
    result.pit_coverage = sum(1 for rec in amendment_records
                              if str(rec["statute_id"]) in pit_map)

    print(f"Statute   : {sid}")
    print(f"Mode      : {mode}")
    print(f"Base secs : {result.base_section_count}")
    print(f"Amendments: {result.total_amendments}")
    print(f"PIT cover : {result.pit_coverage}/{result.total_amendments}")
    print()

    # --- Sequential replay with checkpoints ---
    latest_pit_bytes: Optional[bytes] = None   # most recent PIT XML; used for kumottu counts
    for rec in amendment_records:
        mid = str(rec["statute_id"])

        # Apply this amendment — pure fold; state is updated in-place below
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            state = process_muutoslaki(
                mid, state, ctx,
                replay_mode=mode,
                parent_id=sid,
            ).output

        # Snapshot the replay state after this amendment
        current_sections = _extract_sections_from_ir(state.ir)

        am_result = AmendmentResult(
            amendment_id=mid,
            pit_path=pit_map.get(mid),
            pit_label=_pit_label(pit_map.get(mid)) if pit_map.get(mid) else None,
            replay_section_count=len(current_sections),
        )

        # --- Compare against PIT XML if available ---
        if am_result.pit_path:
            try:
                archive = getattr(cs, "_archive", None)
                if archive is not None:
                    pit_data = archive.get(am_result.pit_path)
                else:
                    pit_data = None
                latest_pit_bytes = pit_data
                pit_sections = _extract_sections_from_xml(pit_data) if pit_data is not None else {}
                am_result.pit_section_count = len(pit_sections)

                # Build per-section results
                all_eids = sorted(
                    set(current_sections) | set(pit_sections),
                    key=_eid_sort_key,
                )
                scores: List[float] = []
                for eid in all_eids:
                    replay_text = current_sections.get(eid)
                    pit_text = pit_sections.get(eid)
                    label = _eid_to_label(eid)

                    score: Optional[float] = None
                    if replay_text is not None and pit_text is not None:
                        score = _score_texts(replay_text, pit_text)
                        scores.append(score)

                    is_new = (eid not in prev_sections)
                    modified = (
                        not is_new
                        and eid in prev_sections
                        and eid in current_sections
                        and _clean(current_sections[eid]) != _clean(prev_sections[eid])
                    )

                    sec_r = SectionResult(
                        eid=eid,
                        label=label,
                        replay_text=replay_text,
                        pit_text=pit_text,
                        score=score,
                        is_new=is_new,
                        modified=modified,
                    )
                    am_result.sections[eid] = sec_r

                    if is_new:
                        am_result.new_sections.append(label)
                    elif modified:
                        am_result.modified_sections.append(label)

                if scores:
                    am_result.overall_score = sum(scores) / len(scores)

            except KeyError:
                am_result.error = f"PIT path not found in ZIP: {am_result.pit_path}"
            except Exception as e:
                am_result.error = str(e)
        else:
            # No PIT: still record which sections are new/modified
            for eid, text in current_sections.items():
                label = _eid_to_label(eid)
                is_new = (eid not in prev_sections)
                modified = (
                    not is_new
                    and _clean(text) != _clean(prev_sections.get(eid, ""))
                )
                sec_r = SectionResult(
                    eid=eid, label=label,
                    replay_text=text, pit_text=None,
                    score=None,
                    is_new=is_new, modified=modified,
                )
                am_result.sections[eid] = sec_r
                if is_new:
                    am_result.new_sections.append(label)
                elif modified:
                    am_result.modified_sections.append(label)

        result.amendment_results.append(am_result)
        prev_sections = dict(current_sections)

        # Progress
        pit_indicator = f"[PIT: {am_result.pit_label}]" if am_result.pit_label else "[no PIT]"
        score_str = (f"  score={am_result.overall_score:.1%}"
                     if am_result.overall_score is not None else "")
        print(f"  {mid:<14} {pit_indicator:<22} replay={am_result.replay_section_count}"
              f"  pit={am_result.pit_section_count or '-':>3}{score_str}")

    # --- HTML comparison ---
    if not skip_html:
        print()
        print("Fetching HTML...")
        labels, err = _fetch_html_sections(sid, archive_db=None)

        final_sections = _extract_sections_from_ir(state.ir)
        replay_labels = sorted(
            [_eid_to_label(eid) for eid in final_sections],
            key=lambda lbl: _label_sort_key(lbl),
        )
        replay_label_set = set(replay_labels)
        html_label_set = set(labels)

        # Kumottu breakdown from the latest PIT snapshot
        pit_live, pit_total = 0, 0
        if latest_pit_bytes is not None:
            pit_live, pit_total = count_live_sections(latest_pit_bytes)

        hc = HtmlComparison(
            html_sections=len(labels),
            html_labels=labels,
            replay_sections=len(final_sections),
            replay_labels=replay_labels,
            missing_from_replay=[l for l in labels if l not in replay_label_set],
            extra_in_replay=[l for l in replay_labels if l not in html_label_set],
            pit_total=pit_total,
            pit_live=pit_live,
            pit_kumottu=pit_total - pit_live,
            error=err,
        )
        result.html_comparison = hc

    return result


def _pit_label(path: Optional[str]) -> Optional[str]:
    """Extract 'fin@YYYYNNNN' label from a ZIP path."""
    if path is None:
        return None
    m = re.search(r'(fin@\d+)', path)
    return m.group(1) if m else None


def _eid_sort_key(eid: str) -> Tuple:
    """Sort eIds numerically: sec_1 < sec_1a < sec_2 < sec_9a < sec_10."""
    base = _strip_version_suffix(eid)
    m = _EID_PLAIN_RE.match(base)
    if m:
        return (int(m.group(1)), m.group(2) or "")
    return (99999, eid)


def _label_sort_key(label: str) -> Tuple:
    """Sort display labels: §1 < §1a < §2 < §9a < §10."""
    m = re.match(r'^§(\d+)([a-z]*)$', label)
    if m:
        return (int(m.group(1)), m.group(2) or "")
    return (99999, label)


# ---------------------------------------------------------------------------
# Output: blame matrix printer
# ---------------------------------------------------------------------------

def _print_blame_matrix(result: ChainVerificationResult) -> None:
    sid = result.statute_id
    n = len(result.amendment_results)
    pit_n = result.pit_coverage

    print()
    print(f"=== Amendment Chain Verification: {sid} ===")
    print(f"Oracle base   : fin@ ({result.base_section_count} sections)")
    print(f"Amendments    : {n}")
    print(f"PIT checkpts  : {pit_n}/{n}")
    print()

    # --- Per-amendment summary table ---
    _hdr = (
        f"{'Amendment':<14}  {'PIT exists?':<22}  {'Secs (replay/PIT)':<20}  "
        f"{'Score':>7}  {'Changes'}"
    )
    print(_hdr)
    print("\u2500" * len(_hdr))

    for am in result.amendment_results:
        pit_col = f"yes {am.pit_label}" if am.pit_label else "no"
        secs_col = (
            f"{am.replay_section_count}/{am.pit_section_count}"
            if am.pit_label
            else f"{am.replay_section_count}/-"
        )
        score_col = f"{am.overall_score:.1%}" if am.overall_score is not None else "  n/a"
        changes = []
        if am.new_sections:
            changes.append(f"+{','.join(am.new_sections)}")
        if am.modified_sections:
            changes.append(f"mod:{','.join(am.modified_sections[:3])}"
                           + ("..." if len(am.modified_sections) > 3 else ""))
        changes_col = "  ".join(changes) if changes else "(none)"
        if am.error:
            changes_col = f"ERROR: {am.error}"

        print(f"{am.amendment_id:<14}  {pit_col:<22}  {secs_col:<20}  {score_col:>7}  {changes_col}")

    print()

    # --- HTML comparison ---
    if result.html_comparison:
        hc = result.html_comparison
        if hc.error:
            print(f"Final state vs HTML: fetch error — {hc.error}")
        else:
            print("Final state vs HTML:")
            print(f"  Replay : {hc.replay_sections} sections")
            if hc.pit_total > 0:
                delta = hc.pit_live - hc.html_sections
                delta_str = f"Δ={delta:+d}" if delta != 0 else "Δ=0"
                print(f"  PIT    : {hc.pit_total} total  "
                      f"({hc.pit_live} live, {hc.pit_kumottu} kumottu)")
                print(f"  HTML   : {hc.html_sections} sections")
                print(f"  Live PIT vs HTML: {delta_str} "
                      f"(PIT has {abs(delta)} {'more' if delta > 0 else 'fewer'} "
                      f"live sections than HTML)")
            else:
                print(f"  HTML   : {hc.html_sections} sections")
            match = not hc.missing_from_replay and not hc.extra_in_replay
            if match:
                print("  Replay vs HTML: MATCH")
            if hc.missing_from_replay:
                print(f"  Missing from replay: {', '.join(hc.missing_from_replay)}")
            if hc.extra_in_replay:
                print(f"  Extra in replay (not in HTML): {', '.join(hc.extra_in_replay)}")
        print()

    # --- Per-section blame matrix ---
    # Collect all section eIds seen across all checkpoints
    all_eids: List[str] = []
    seen: set = set()
    for am in result.amendment_results:
        for eid in sorted(am.sections.keys(), key=_eid_sort_key):
            if eid not in seen:
                all_eids.append(eid)
                seen.add(eid)

    if not all_eids:
        return

    # Build kumottu set: track the last-known PIT text per eid across all checkpoints.
    # A section is kumottu if its most recent pit_text is a short repealed stub.
    _last_pit_text: Dict[str, str] = {}
    for am in result.amendment_results:
        for eid, sr in am.sections.items():
            if sr.pit_text is not None:
                _last_pit_text[eid] = sr.pit_text
    kumottu_eids: set = {
        eid for eid, txt in _last_pit_text.items() if _is_kumottu(txt)
    }

    # Build column headers
    col_width = 10  # per amendment column
    sec_width = 10  # section column
    amend_ids = [am.amendment_id for am in result.amendment_results]

    print("Per-section blame matrix:")
    header_parts = [f"{'Section':<{sec_width}}", "base"]
    for amid in amend_ids:
        # Shorten: e.g. '2020/680' -> '680/2020'
        header_parts.append(_short_amid(amid))
    if result.html_comparison and not result.html_comparison.error:
        header_parts.append("HTML")
    header_parts.append("Status")
    print("  " + "  ".join(f"{p:<{col_width}}" for p in header_parts))
    print("  " + "\u2500" * (sec_width + (col_width + 2) * len(header_parts) + 2))

    # For each section: determine base state and state after each amendment
    # Build per-eid per-amendment presence/score map
    for eid in all_eids:
        label = _eid_to_label(eid)

        # Was this section in base?
        # A section is in base if it appears in the FIRST amendment's sections
        # and is NOT marked as new there (or if it's in no amendment's sections at all)
        base_present = False
        for am in result.amendment_results:
            if eid in am.sections:
                base_present = not am.sections[eid].is_new
                break

        cols = [f"{label:<{sec_width}}", "base" if base_present else "-   "]

        for am in result.amendment_results:
            sr = am.sections.get(eid)
            if sr is None:
                # Not mentioned in this amendment's section set at all
                # Check if it existed in a prior checkpoint — if so, assume unchanged
                cols.append("-         ")
            elif sr.replay_text is None:
                # Absent from replay at this point
                cols.append("MISSING   ")
            elif sr.is_new:
                if sr.score is not None and sr.score >= 0.95:
                    cols.append("NEW(ok)   ")
                elif sr.score is not None:
                    cols.append(f"NEW({sr.score:.0%}) ")
                else:
                    cols.append("NEW       ")
            elif sr.modified:
                if sr.score is not None:
                    cols.append(f"mod({sr.score:.0%})")
                else:
                    cols.append("modified  ")
            else:
                # Present and unchanged — show score vs PIT
                if sr.score is not None:
                    marker = "ok" if sr.score >= 0.95 else f"!{sr.score:.0%}"
                    cols.append(f"{marker:<10}")
                else:
                    cols.append("ok        ")

        # HTML column
        if result.html_comparison and not result.html_comparison.error:
            html_labels_set = set(result.html_comparison.html_labels)
            lbl = _eid_to_label(eid)
            if lbl in html_labels_set:
                cols.append("ok")
            else:
                cols.append("MISS")

        # Status column: kumottu or live
        status_col = "kumottu" if eid in kumottu_eids else "live"
        cols.append(status_col)

        annotation = ""
        # Flag if section appears in HTML but not in final replay
        if (result.html_comparison
                and not result.html_comparison.error
                and _eid_to_label(eid) in result.html_comparison.missing_from_replay):
            annotation = " <- HTML only"

        print("  " + "  ".join(f"{c:<{col_width}}" for c in cols) + annotation)

    print()


def _short_amid(amid: str) -> str:
    """Shorten amendment ID for column headers: '2020/680' -> '680/20'."""
    parts = amid.split("/")
    if len(parts) == 2:
        year, num = parts[0], parts[1]
        return f"{num}/{year[2:]}"  # e.g. '680/20'
    return amid


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _result_to_json(result: ChainVerificationResult) -> dict:
    """Serialize ChainVerificationResult to a JSON-compatible dict."""
    amendments_list = []
    for am in result.amendment_results:
        per_section = {}
        for eid, sr in am.sections.items():
            per_section[eid] = {
                "label": sr.label,
                "replay_hash": _text_hash(sr.replay_text) if sr.replay_text else None,
                "pit_hash": _text_hash(sr.pit_text) if sr.pit_text else None,
                "score": round(sr.score, 4) if sr.score is not None else None,
                "is_new": sr.is_new,
                "modified": sr.modified,
                "match": sr.score is not None and sr.score >= 0.95,
            }
        amendments_list.append({
            "amendment_id": am.amendment_id,
            "pit_path": am.pit_path,
            "pit_label": am.pit_label,
            "pit_exists": am.pit_path is not None,
            "replay_sections": am.replay_section_count,
            "pit_sections": am.pit_section_count,
            "overall_score": (round(am.overall_score, 4)
                              if am.overall_score is not None else None),
            "new_sections": am.new_sections,
            "modified_sections": am.modified_sections,
            "error": am.error,
            "per_section": per_section,
        })

    html_cmp = None
    if result.html_comparison:
        hc = result.html_comparison
        html_cmp = {
            "html_sections": hc.html_sections,
            "replay_sections": hc.replay_sections,
            "pit_total": hc.pit_total,
            "pit_live": hc.pit_live,
            "pit_kumottu": hc.pit_kumottu,
            "missing_from_replay": hc.missing_from_replay,
            "extra_in_replay": hc.extra_in_replay,
            "error": hc.error,
        }

    return {
        "statute_id": result.statute_id,
        "verified_at": result.verified_at,
        "base_section_count": result.base_section_count,
        "total_amendments": result.total_amendments,
        "pit_coverage": result.pit_coverage,
        "amendments": amendments_list,
        "html_comparison": html_cmp,
    }


def _text_hash(text: str) -> str:
    """Short hash of cleaned text for comparison."""
    import hashlib
    return hashlib.sha1(_clean(text).encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    sids = args.sids
    skip_html = getattr(args, "no_html", False)
    output_dir_arg = getattr(args, "output", None)

    if output_dir_arg:
        output_dir = Path(output_dir_arg)
    else:
        output_dir = _TMP_DIR / "verify_chain"
    output_dir.mkdir(parents=True, exist_ok=True)

    for sid in sids:
        # Normalize: accept both 2020/369 and 369/2020
        if "/" in sid:
            parts = sid.split("/")
            if len(parts[0]) != 4:
                sid = f"{parts[1]}/{parts[0]}"
        else:
            print(f"ERROR: invalid statute ID '{sid}'", file=sys.stderr)
            continue

        print(f"Verifying chain: {sid}")
        print("=" * 60)

        result = verify_chain(
            sid=sid,
            skip_html=skip_html,
            mode="finlex_oracle",
        )

        _print_blame_matrix(result)

        # Save JSON
        sid_safe = sid.replace("/", "_")
        json_path = output_dir / f"{sid_safe}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(_result_to_json(result), f, ensure_ascii=False, indent=2)
        print(f"Results written to: {json_path}")
        print()
