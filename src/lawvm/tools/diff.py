"""lawvm diff — provision-level diff: replay vs oracle.

Shows which specific sections diverge between the replayed statute and the
consolidated oracle, and by how much. Where batch_test.py gives one number
per statute, this gives a per-provision map of where the problems are.

Usage:
    lawvm diff <statute_id>                         # all provisions, worst first
    lawvm diff <statute_id> --address "section:9a"  # single provision
    lawvm diff <statute_id> --threshold 0.95        # only show sections below 95%
    lawvm diff <statute_id> --all                   # include perfect sections too
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Literal, Optional, Tuple

import Levenshtein
from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import xml_element_to_text
from lawvm.core.compile_result import StrictProfile
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools._compile_report_record import report_record_from_facade
from lawvm.tools.editorial_hygiene import normalize_kumottu_stubs, strip_editorial_annotations
from lawvm.tools.section_keys import (
    display_section_key,
    extract_ir_sections,
    extract_oracle_sections,
    norm_section_label,
    reconcile_unique_unscoped_aliases,
    normalize_address_filter,
    section_key_matches_filter,
    section_key_sort_key,
)
from lawvm.tools.divergence_heuristics import oracle_text_reduces_to_bare_section_stub
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.grafter import (
    _oracle_version_label,
    get_consolidated_oracle_context,
    get_corpus,
    replay_xml,
)
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1


_LATEST_CONSOLIDATED_SELECTOR = ConsolidatedArtifactSelector.latest_cached_editorial()


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _norm_num(s: str) -> str:
    """Normalize section num for matching: strip §, spaces, periods, non-alphanum."""
    return norm_section_label(s)


def _el_text(el: etree._Element) -> str:
    """Extract all text from an element, consistent with irnode_to_text.

    Uses xml_element_to_text so oracle section text extraction is consistent
    with replay IR text extraction in comparison paths.
    """
    return xml_element_to_text(el)


def _normalize_kumottu(t: str) -> str:
    """Strip kumottu-editorial annotations before scoring.

    Mirrors bench.py ``_normalize`` so that repeal placeholders (which carry
    no editorial "on kumottu" text) compare correctly against oracle sections
    that do carry the attribution (e.g. "1 § on kumottu A:lla 18.2.2000/172.").
    """
    t = normalize_kumottu_stubs(t)
    # Date annotations and historical wording markers
    t = re.sub(r"\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)", "", t)
    t = re.sub(r"Aiempi sanamuoto kuuluu:", "", t)
    return strip_editorial_annotations(t)


def _clean(text: str) -> str:
    """Mirror of batch_test.py clean — strip non-alphanumeric for scoring."""
    return re.sub(r'[^a-z0-9äöå]', '', _normalize_kumottu(text).lower())


# ---------------------------------------------------------------------------
# Section extraction from XML tree
# ---------------------------------------------------------------------------

def _extract_sections(root: etree._Element) -> Dict[str, etree._Element]:
    """Extract all sections from a body/root element, keyed by normalized path."""
    return extract_oracle_sections(root)


def _extract_sections_ir(root: IRNode) -> Dict[str, IRNode]:
    """Legacy extractor keyed by bare section label.

    Many existing tests and utilities still expect this contract. Path-aware
    comparison code should use ``extract_ir_sections`` from ``section_keys``.
    """
    sections: Dict[str, IRNode] = {}
    def _walk(node: IRNode):
        if node.kind is IRNodeKind.SECTION and node.label:
            if node.label not in sections:
                sections[node.label] = node
        for c in node.children:
            _walk(c)
    _walk(root)
    return sections


def _irnode_text(node: IRNode) -> str:
    """Extract all text content from an IRNode."""
    return irnode_to_text(node)


def _diff_sections_ir_vs_xml(
    replay_ir: IRNode,
    oracle_root: etree._Element,
    address_filter: Optional[Tuple[str, str]],
    threshold: float,
    show_all: bool,
    show_text: bool = False,
) -> None:
    """Compare IRNode replay sections against lxml oracle sections."""
    replay_secs = extract_ir_sections(replay_ir)
    oracle_secs = _extract_sections(oracle_root)
    replay_secs, oracle_secs = reconcile_unique_unscoped_aliases(replay_secs, oracle_secs)

    all_keys = sorted(set(replay_secs) | set(oracle_secs), key=section_key_sort_key)

    if address_filter:
        all_keys = [k for k in all_keys if section_key_matches_filter(k, address_filter)]

    results: List[Tuple[float, str, str]] = []
    compared = 0
    perfect = 0
    missing_replay = 0
    extra_replay = 0
    editorial_stub: set = set()

    editorial_kumottu: set = set()  # keys where diff is editorial (kumottu)

    for key in all_keys:
        r_node = replay_secs.get(key)
        o_el = oracle_secs.get(key)

        if r_node is None:
            if o_el is not None and oracle_text_reduces_to_bare_section_stub(_el_text(o_el)):
                editorial_stub.add(key)
                results.append((0.0, key, "editorial_stub"))
            else:
                results.append((0.0, key, "missing_replay"))
                missing_replay += 1
        elif o_el is None:
            results.append((-1.0, key, "extra_replay"))
            extra_replay += 1
        else:
            r_text = _clean(_irnode_text(r_node))
            o_text = _clean(_el_text(o_el))
            if not r_text and not o_text:
                score = 1.0
            elif not r_text or not o_text:
                score = 0.0
            else:
                score = Levenshtein.ratio(r_text, o_text)
            # Detect editorial kumottu: replay is a repeal placeholder and
            # oracle contains "kumottu" attribution text.  After kumottu
            # normalization both sides reduce to (near-)identical bare labels.
            if (
                score < 0.9999
                and r_node.attrs.get("lawvm_repeal_placeholder") == "1"
                and "kumottu" in _el_text(o_el).lower()
            ):
                editorial_kumottu.add(key)
                score = 1.0
            results.append((score, key, "compared"))
            compared += 1
            if score >= 0.9999:
                perfect += 1

    def sort_key(t):
        score, key, status = t
        if status == "extra_replay":
            return (2, key)
        return (0 if score < 1.0 else 1, score, key)

    results.sort(key=sort_key)

    # Print summary
    n_editorial_kumottu = len(editorial_kumottu)
    scores_compared = [s for s, _, st in results if st == "compared"]
    mean_score = sum(scores_compared) / len(scores_compared) if scores_compared else 0.0
    summary_parts = [
        f"{compared} compared",
        f"{perfect} perfect",
        f"{missing_replay} missing from replay",
        f"{extra_replay} extra in replay",
    ]
    editorial_stub_count = len([r for r in results if r[2] == "editorial_stub"])
    if n_editorial_kumottu:
        summary_parts.append(f"{n_editorial_kumottu} editorial (kumottu)")
    if editorial_stub_count:
        summary_parts.append(f"{editorial_stub_count} editorial (stub)")
    print(f"Sections : {'  '.join(summary_parts)}")
    print(f"Score    : {mean_score:.2%}  (mean similarity of compared sections)")
    print()

    for score, key, status in results:
        if status == "compared":
            is_editorial = key in editorial_kumottu
            if is_editorial and not show_all:
                continue
            if score >= 0.9999 and not show_all:
                continue
            if score >= threshold and not show_all:
                continue
            o_el = oracle_secs[key]
            r_node = replay_secs[key]
            r_text = _irnode_text(r_node)
            o_text = _el_text(o_el)
            display = display_section_key(key, o_el)
            if is_editorial:
                print(f"  editorial (kumottu)  {display}")
            else:
                print(f"  {score:5.1%}  {display:<14}  {_bar(score)}")
                if show_text:
                    print("  --- replay ---")
                    print(f"  {' '.join(r_text.split())}")
                    print("  --- oracle ---")
                    print(f"  {' '.join(o_text.split())}")
                else:
                    print(f"  replay : {' '.join(r_text.split())[:80]}…" if len(r_text) > 80 else f"  replay : {' '.join(r_text.split())}")
                    print(f"  oracle : {' '.join(o_text.split())[:80]}…" if len(o_text) > 80 else f"  oracle : {' '.join(o_text.split())}")
        elif status == "missing_replay":
            o_el = oracle_secs[key]
            display = display_section_key(key, o_el)
            o_text = _el_text(o_el)
            print(f"  MISSING  {display:<14}  (in oracle, not in replay)")
            print(f"           oracle: {' '.join(o_text.split())[:80]}…" if len(o_text) > 80 else f"           oracle: {' '.join(o_text.split())}")
        elif status == "editorial_stub":
            o_el = oracle_secs[key]
            display = display_section_key(key, o_el)
            o_text = _el_text(o_el)
            print(f"  editorial (stub)  {display:<14}  (oracle stub only)")
            print(f"           oracle: {' '.join(o_text.split())[:80]}…" if len(o_text) > 80 else f"           oracle: {' '.join(o_text.split())}")
        elif status == "extra_replay":
            r_node = replay_secs[key]
            r_text = _irnode_text(r_node)
            print(f"  EXTRA    {key:<14}  (in replay, not in oracle)")
            print(f"           replay: {' '.join(r_text.split())[:80]}…" if len(r_text) > 80 else f"           replay: {' '.join(r_text.split())}")


def _extract_chapters(root: etree._Element) -> Dict[str, etree._Element]:
    """Extract all chapters, keyed by normalized num."""
    from typing import cast as _cast
    chapters: Dict[str, etree._Element] = {}
    for ch in _cast(List[etree._Element], root.xpath(".//*[local-name()='chapter']")):
        num = _num_text(ch)
        if not num:
            continue
        key = _norm_num(num)
        if key and key not in chapters:
            chapters[key] = ch
    return chapters


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_pair(replay_el: etree._Element, oracle_el: etree._Element) -> float:
    r = _clean(_el_text(replay_el))
    o = _clean(_el_text(oracle_el))
    if not r and not o:
        return 1.0
    if not r or not o:
        return 0.0
    return Levenshtein.ratio(r, o)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _display_num(key: str, el: etree._Element) -> str:
    """Human-readable section label from the element's num text."""
    raw = _num_text(el) or key
    if raw.endswith("§"):
        return raw
    return f"{raw} §" if not raw.startswith("§") else raw


def _snippet(el: etree._Element, chars: int = 80) -> str:
    text = " ".join(_el_text(el).split())
    if len(text) > chars:
        return text[:chars] + "…"
    return text


def _bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    return "[" + "█" * filled + "·" * (width - filled) + "]"


# ---------------------------------------------------------------------------
# Core diff logic
# ---------------------------------------------------------------------------

def _diff_sections(
    replay_root: etree._Element,
    oracle_root: etree._Element,
    address_filter: Optional[Tuple[str, str]],
    threshold: float,
    show_all: bool,
) -> None:
    replay_secs = _extract_sections(replay_root)
    oracle_secs = _extract_sections(oracle_root)

    all_keys = sorted(set(replay_secs) | set(oracle_secs), key=section_key_sort_key)

    # Apply address filter
    if address_filter:
        all_keys = [k for k in all_keys if section_key_matches_filter(k, address_filter)]

    results: List[Tuple[float, str, str]] = []  # (score, key, status)
    editorial_stub: set[str] = set()

    for key in all_keys:
        r_el = replay_secs.get(key)
        o_el = oracle_secs.get(key)

        if r_el is None:
            if o_el is not None and oracle_text_reduces_to_bare_section_stub(_el_text(o_el)):
                editorial_stub.add(key)
                results.append((0.0, key, "editorial_stub"))
            else:
                results.append((0.0, key, "missing_replay"))
        elif o_el is None:
            results.append((-1.0, key, "extra_replay"))
        else:
            score = _score_pair(r_el, o_el)
            results.append((score, key, "compared"))

    # Sort: missing (0.0) and low-score first; extras last
    def sort_key(t):
        score, key, status = t
        if status == "extra_replay":
            return (2, key)
        return (0 if score < 1.0 else 1, score, key)

    results.sort(key=sort_key)

    # Print
    total = len([r for r in results if r[2] == "compared"])
    perfect = len([r for r in results if r[2] == "compared" and r[0] >= 0.9999])
    missing = len([r for r in results if r[2] == "missing_replay"])
    extras = len([r for r in results if r[2] == "extra_replay"])
    editorial_stub_count = len(editorial_stub)

    compared_scores = [r[0] for r in results if r[2] == "compared"]
    mean_score = sum(compared_scores) / len(compared_scores) if compared_scores else 0.0

    print(f"Sections : {total} compared  {perfect} perfect  "
          f"{missing} missing from replay  {extras} extra in replay"
          f"{'  ' + str(editorial_stub_count) + ' editorial stub' if editorial_stub_count else ''}")
    print(f"Score    : {mean_score:.2%}  (mean similarity of compared sections)")
    print()

    shown = 0
    for score, key, status in results:
        r_el = replay_secs.get(key)
        o_el = oracle_secs.get(key)
        display = display_section_key(key, r_el if r_el is not None else o_el)

        if status == "missing_replay":
            print(f"  MISSING  {display:<12}  (in oracle, not in replay)")
            if o_el is not None:
                print(f"           oracle: {_snippet(o_el)}")
            shown += 1
        elif status == "editorial_stub":
            print(f"  editorial (stub)  {display:<12}  (oracle stub only)")
            if o_el is not None:
                print(f"           oracle: {_snippet(o_el)}")
            shown += 1
        elif status == "extra_replay":
            print(f"  EXTRA    {display:<12}  (in replay, not in oracle)")
            shown += 1
        else:
            if score >= 0.9999 and not show_all:
                continue
            if score >= threshold and not show_all:
                continue
            pct = f"{score * 100:.1f}%"
            bar = _bar(score)
            print(f"  {pct:>6}  {display:<12}  {bar}")
            if score < 0.90 and r_el is not None and o_el is not None:
                print(f"  replay : {_snippet(r_el)}")
                print(f"  oracle : {_snippet(o_el)}")
            shown += 1

    if shown == 0:
        if show_all:
            print("  (no sections to show)")
        else:
            print(f"  All {total} sections at or above threshold ({threshold:.0%}) — no divergence found")
    print()


def _section_sort_key(key: str):
    """Sort section keys numerically where possible (9 < 9a < 10)."""
    return section_key_sort_key(key)


# ---------------------------------------------------------------------------
# Temporal activation display
# ---------------------------------------------------------------------------

_TEMPORAL_STATUS_LABELS: Dict[str, str] = {
    "scheduled": "[myöhemmin voimaantuleva]",
    "pending_external_resolution": "[voimaantulo asetuksella]",
    "inactive": "[ei voimassa]",
}


def _print_temporal_status(master: Any) -> None:
    """Print statute-level temporal activation status if non-trivial."""
    temporal_events = getattr(master, "temporal_events", ()) or ()
    if not temporal_events:
        return
    from lawvm.finland.temporal_lowering import lower_temporal_events_to_activation_rules
    from lawvm.core.temporal import project_temporal_status

    rules = lower_temporal_events_to_activation_rules(temporal_events)
    if not rules:
        return
    status = project_temporal_status(rules, [], "9999-12-31")
    label = _TEMPORAL_STATUS_LABELS.get(status, "")
    if not label:
        return
    date_info = ""
    if status == "scheduled":
        for rule in rules:
            if rule.kind == "fixed_date" and rule.effective_date:
                parts = rule.effective_date.split("-")
                if len(parts) == 3:
                    date_info = f" (voimaan {int(parts[2])}.{int(parts[1])}.{parts[0]})"
                break
    print(f"Temporal: {label}{date_info}")


# ---------------------------------------------------------------------------
# Main async entry
# ---------------------------------------------------------------------------

def _print_compile_summary(
    *,
    report_record: Any,
) -> None:
    """Print the short summary from the facade-backed report record."""
    projection = getattr(report_record, "projection_rows", None)
    if not callable(projection):
        raise TypeError("report_record must expose projection_rows()")
    projection_rows = list(projection() or [])
    canonical_ops = list(getattr(report_record, "canonical_ops", []) or [])
    failed_ops = list(getattr(report_record, "failed_ops", []) or [])
    source_pathologies = [
        {
            "code": str((row.get("detail") or {}).get("code") or ""),
        }
        for row in projection_rows
        if isinstance(row, dict)
        and str(row.get("kind") or "") == "source_pathology"
        and isinstance(row.get("detail"), dict)
        and str((row.get("detail") or {}).get("code") or "")
    ]
    n_canonical = len(canonical_ops)
    n_failed = len(failed_ops)
    n_projection_rows = len(projection_rows)
    strict_fail_reasons = list(getattr(report_record, "strict_fail_reasons", []) or [])
    pass_label = "YES" if not bool(strict_fail_reasons) else "NO"
    print(
        f"Compile summary: strict={pass_label}  canonical={n_canonical}  "
        f"failed={n_failed}  projection_rows={n_projection_rows}"
    )
    fail_reasons = strict_fail_reasons
    if fail_reasons:
        print(f"  Fail reasons : {', '.join(fail_reasons)}")
    if projection_rows:
        kinds = sorted(
            {
                str((a.get("kind") if isinstance(a, dict) else getattr(a, "kind", "")) or "")
                for a in projection_rows
            }
        )
        print(f"  Projection rows: {', '.join(kinds)}")
    if source_pathologies:
        codes = sorted({str(p.get("code") or "") for p in source_pathologies if isinstance(p, dict) and str(p.get("code") or "")})
        print(f"  Pathologies  : {', '.join(codes)}")
    print()


def _diff_sync(
    sid: str,
    address_filter: Optional[Tuple[str, str]],
    threshold: float,
    show_all: bool,
    mode: Literal["finlex_oracle", "legal_pit"],
    show_compile_summary: bool = False,
    strict_profile: Optional["StrictProfile"] = None,
    show_text: bool = False,
) -> None:
    if show_compile_summary:
        from lawvm.finland.compile import compile_fi_facade_from_replay

        compiled_ops: list[dict[str, object]] = []
        replay_meta: dict[str, object] = {}
        canonical_ops: list[Any] = []
        failed_ops: list[Any] = []
        master = replay_xml(
            sid,
            mode=mode,
            quiet=True,
            strict_profile=strict_profile,
            compiled_ops_out=compiled_ops,
            replay_meta_out=replay_meta,
            lo_ops_out=canonical_ops,
            failed_ops_out=failed_ops,
        )
        facade = compile_fi_facade_from_replay(
            parent_id=sid,
            replay_result=master,
            replay_mode=mode,
            strict_profile=strict_profile,
            compiled_ops=compiled_ops,
            replay_meta=replay_meta,
            canonical_ops=canonical_ops,
            failed_ops=failed_ops,
        )
        oracle_ctx = get_consolidated_oracle_context(
            sid,
            selector=_LATEST_CONSOLIDATED_SELECTOR,
        )
        oracle_root = None
        if oracle_ctx.locator:
            corpus = get_corpus()
            oracle_bytes = corpus.read_locator(oracle_ctx.locator)
            if oracle_bytes is not None:
                oracle_root = etree.fromstring(oracle_bytes)
        if oracle_root is None:
            print(f"ERROR: no oracle found for {sid}", file=sys.stderr)
            sys.exit(1)
        oracle_ver = _oracle_version_label(oracle_ctx.locator) if oracle_ctx.locator else "absent"
        print(f"Statute : {sid}")
        print(f"Title   : {master.title}")
        print(f"Mode    : {mode}")
        if strict_profile is not None:
            print(f"Profile : {strict_profile.name} (strict)")
        print(f"Oracle  : {oracle_ver}")
        if address_filter:
            print(f"Address : {address_filter[0]}:{address_filter[1]}")
        _print_temporal_status(master)
        print()
        _print_compile_summary(
            report_record=report_record_from_facade(
                statute_id=sid,
                facade=facade,
                compiled_ops=compiled_ops,
                failed_ops=failed_ops,
                source_adjudication=master.source_adjudication,
            ),
        )
        _diff_sections_ir_vs_xml(master.ir, oracle_root, address_filter, threshold, show_all, show_text=show_text)
        return

    master = replay_xml(sid, mode=mode, quiet=True, strict_profile=strict_profile)
    oracle_ctx = get_consolidated_oracle_context(
        sid,
        selector=_LATEST_CONSOLIDATED_SELECTOR,
    )
    oracle_root = None
    if oracle_ctx.locator:
        corpus = get_corpus()
        oracle_bytes = corpus.read_locator(oracle_ctx.locator)
        if oracle_bytes is not None:
            oracle_root = etree.fromstring(oracle_bytes)

    if oracle_root is None:
        print(f"ERROR: no oracle found for {sid}", file=sys.stderr)
        sys.exit(1)

    oracle_ver = _oracle_version_label(oracle_ctx.locator) if oracle_ctx.locator else "absent"

    print(f"Statute : {sid}")
    print(f"Title   : {master.title}")
    print(f"Mode    : {mode}")
    if strict_profile is not None:
        print(f"Profile : {strict_profile.name} (strict)")
    print(f"Oracle  : {oracle_ver}")
    if address_filter:
        print(f"Address : {address_filter[0]}:{address_filter[1]}")
    _print_temporal_status(master)
    print()

    # Use IRNode-based diff (master.tree is no longer mutated during replay)
    _diff_sections_ir_vs_xml(master.ir, oracle_root, address_filter, threshold, show_all, show_text=show_text)


def _parse_address(address: Optional[str]) -> Optional[Tuple[str, str]]:
    if not address or ":" not in address:
        return None
    if "/" in address:
        return ("path", normalize_address_filter(address))
    kind, num = address.split(":", 1)
    return (kind.strip(), num.strip())


def main(args) -> None:
    address_filter = _parse_address(getattr(args, "address", None))
    threshold = getattr(args, "threshold", 1.0)  # default: only show imperfect
    show_all = getattr(args, "all", False)
    mode = getattr(args, "mode", "finlex_oracle")
    show_compile_summary = getattr(args, "compile_summary", False)
    use_strict = getattr(args, "strict", False)
    strict_profile = FINLAND_INGESTION_V1 if use_strict else None

    _diff_sync(
        sid=args.statute_id,
        address_filter=address_filter,
        threshold=threshold,
        show_all=show_all,
        mode=mode,
        show_compile_summary=show_compile_summary,
        strict_profile=strict_profile,
        show_text=getattr(args, "show_text", False),
    )
