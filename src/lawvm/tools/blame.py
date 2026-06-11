"""lawvm blame — per-provision last-modification trace.

Like `git blame` for provisions. Each provision is annotated with the
amendment that last modified it and the sequence number of that op.

Usage:
    lawvm blame <statute_id>                               # all provisions
    lawvm blame <statute_id> --address "section:9a"        # single provision
    lawvm blame <statute_id> --source 2017/794             # filter by amendment
"""
from __future__ import annotations

from typing import Dict, Literal, Optional, Tuple

from lxml import etree

from lawvm.tools.section_keys import (
    display_section_key,
    extract_ir_sections,
    normalize_address_filter,
    norm_section_label,
    section_key_from_compiled_scope_row,
    section_key_from_target_dict,
    section_key_matches_filter,
    section_key_sort_key,
)
from lawvm.tools.timeline_integrity import (
    TimelineBreak,
    attach_effective_dates,
    break_matches_section,
    sorted_breaks,
    timeline_breaks_from_findings,
)
from lawvm.finland.grafter import (
    replay_xml,
)


# ---------------------------------------------------------------------------
# Helpers
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
    return norm_section_label(s)


def _section_sort_key(key: str):
    return section_key_sort_key(key)


def _display_section(num: str) -> str:
    if num.endswith("§"):
        return num
    return f"{num} §" if not num.startswith("§") else num


# ---------------------------------------------------------------------------
# Build blame map from compiled_ops
# ---------------------------------------------------------------------------

def _build_blame_map(compiled_ops: list) -> Dict[str, dict]:
    """Build {norm_section_num: last_op_dict} from compiled ops list.

    Later ops overwrite earlier ops for the same section, giving us
    the LAST amendment to touch each provision.

    Compiled-op rows are flat (``target_unit_kind``/``target_norm``/
    ``target_chapter`` fields); the legacy nested ``target`` dict shape is
    still accepted for older callers.
    """
    blame: Dict[str, dict] = {}

    for op in compiled_ops:
        key = section_key_from_target_dict(op.get("target") or {})
        if not key:
            key = section_key_from_compiled_scope_row(op)
        if not key:
            continue
        blame[key] = op

    return blame


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _fmt_source(op: dict) -> str:
    src = op.get("source_statute", "?")
    title = op.get("source_title", "")[:40]
    seq = op.get("sequence", "?")
    action = op.get("action", "?").upper()
    return f"{src}  [{seq:>3}] {action:<7}  {title}"


def _key_labels(key: str) -> Tuple[str, str]:
    """Extract (section_label, chapter_label) from a section key string."""
    section = ""
    chapter = ""
    for part in key.split("/"):
        if part.startswith("section:"):
            section = part.split(":", 1)[1]
        elif part.startswith("chapter:"):
            chapter = part.split(":", 1)[1]
    return section, chapter


def _op_for_section_key(
    key: str,
    blame_map: Dict[str, dict],
    all_section_keys: list,
) -> Optional[dict]:
    """Look up the blame op for one IR section key.

    Exact key match first. Compiled ops whose target carries no container
    produce section-only keys (``section:22``) while the IR section key is
    chaptered (``chapter:2/section:22``); attribute such an op ONLY when the
    section label is unique across the statute (mirrors the seam's
    unique-suffix discipline — never an arbitrary pick).
    """
    op = blame_map.get(key)
    if op is not None:
        return op
    section, chapter = _key_labels(key)
    if not section or not chapter:
        return None
    same_label = [k for k in all_section_keys if _key_labels(k)[0] == section]
    if len(same_label) != 1:
        return None
    return blame_map.get(f"section:{section}")


def _matching_address_break(
    key: str, address_breaks: Tuple[TimelineBreak, ...]
) -> Optional[TimelineBreak]:
    section, chapter = _key_labels(key)
    for item in address_breaks:
        if break_matches_section(item, section_label=section, chapter_label=chapter):
            return item
    return None


def _print_timeline_breaks(
    statute_breaks: Tuple[TimelineBreak, ...],
    address_breaks: Tuple[TimelineBreak, ...],
) -> None:
    seen: set = set()
    for item in statute_breaks:
        display_key = (item.amendment_id, item.diagnostic_code, item.effective)
        if display_key in seen:
            continue
        seen.add(display_key)
        effective = f", effective {item.effective}" if item.effective else ""
        print(f"  TIMELINE BROKEN at {item.amendment_id} ({item.diagnostic_code}{effective})")
    if statute_breaks:
        print(
            "    Compiled state from the breaking amendment onward is UNPROVEN."
            " Absence of ops below is NOT evidence that a provision is unamended."
        )
    if address_breaks:
        targets = ", ".join(
            f"{item.amendment_id} §{item.target_section or item.target_chapter}"
            f" ({item.reason})" if item.reason else
            f"{item.amendment_id} §{item.target_section or item.target_chapter}"
            for item in address_breaks
        )
        print(
            f"  FAILED OPS: {len(address_breaks)} compiled op(s) could not be applied"
            f" — affected provisions are unverifiable: {targets}"
        )
    if statute_breaks or address_breaks:
        print()


def _blame_sync(
    sid: str,
    address_filter: Optional[Tuple[str, str]],
    source_filter: Optional[str],
    mode: Literal["official_consolidation", "legal_pit"],
) -> None:
    compiled_ops: list = []
    replay_meta: dict = {}
    master = replay_xml(
        sid,
        mode=mode,
        quiet=True,
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
    )
    timeline_breaks = sorted_breaks(
        attach_effective_dates(
            timeline_breaks_from_findings(getattr(master, "findings", ()) or ()),
            replay_meta.get("lineage") or (),
        )
    )
    statute_breaks = tuple(item for item in timeline_breaks if item.scope == "statute")
    address_breaks = tuple(item for item in timeline_breaks if item.scope == "address")

    blame_map = _build_blame_map(compiled_ops)

    # Collect sections from replayed IRNode tree (for ordering and display)
    replay_secs_ir = extract_ir_sections(master.ir)
    all_section_keys = sorted(replay_secs_ir, key=_section_sort_key)
    unique_sections = list(all_section_keys)

    # Apply filters
    if address_filter:
        unique_sections = [k for k in unique_sections if section_key_matches_filter(k, address_filter)]

    if source_filter:
        # Only show sections whose last-touching amendment matches source_filter
        unique_sections = [
            k for k in unique_sections
            if (_op_for_section_key(k, blame_map, all_section_keys) or {}).get(
                "source_statute", ""
            ).strip() == source_filter.strip()
        ]

    print(f"Statute : {sid}")
    print(f"Title   : {master.title}")
    if address_filter:
        print(f"Address : {address_filter[0]}:{address_filter[1]}")
    if source_filter:
        print(f"Source  : {source_filter}")
    print(f"Ops     : {len(compiled_ops)} compiled")
    print()
    _print_timeline_breaks(statute_breaks, address_breaks)

    col_w = 12
    unblamed = []
    for key in unique_sections:
        display = display_section_key(key)
        op = _op_for_section_key(key, blame_map, all_section_keys)
        if op is None:
            unblamed.append((key, display))
            continue
        failed = _matching_address_break(key, address_breaks)
        suffix = (
            f"  !! later op from {failed.amendment_id} FAILED to apply"
            f" ({failed.reason or failed.diagnostic_code}) — shown state unverifiable"
            if failed is not None
            else ""
        )
        print(f"  {display:<{col_w}}  {_fmt_source(op)}{suffix}")

    if unblamed:
        unverifiable = [
            (key, display, _matching_address_break(key, address_breaks))
            for key, display in unblamed
        ]
        failed_rows = [(d, brk) for _k, d, brk in unverifiable if brk is not None]
        clean_rows = [d for _k, d, brk in unverifiable if brk is None]
        if failed_rows:
            print()
            print("  (no op applied — op FAILED to compile; state unverifiable:)")
            for display, brk in failed_rows:
                print(
                    f"    {display}  !! op from {brk.amendment_id} FAILED to apply"
                    f" ({brk.reason or brk.diagnostic_code}) — unverifiable"
                )
        if clean_rows:
            print()
            if statute_breaks:
                first = statute_breaks[0]
                print(
                    f"  (no op compiled — UNVERIFIABLE after {first.amendment_id}"
                    f" ({first.diagnostic_code}): the timeline is broken, so 'no op'"
                    " is not evidence of 'unamended':)"
                )
            else:
                print("  (unmodified — base statute text, no op compiled:)")
            for display in clean_rows:
                print(f"    {display}")

    print()
    if statute_breaks:
        print(f"  {len(unique_sections) - len(unblamed)} provisions annotated, "
              f"{len(unblamed)} without compiled op (UNVERIFIABLE — timeline broken)")
    else:
        print(f"  {len(unique_sections) - len(unblamed)} provisions annotated, "
              f"{len(unblamed)} from base statute")


def _parse_address(address: Optional[str]) -> Optional[Tuple[str, str]]:
    if not address or ":" not in address:
        return None
    if "/" in address:
        return ("path", normalize_address_filter(address))
    kind, num = address.split(":", 1)
    return (kind.strip(), num.strip())


def main(args) -> None:
    address_filter = _parse_address(getattr(args, "address", None))
    source_filter = getattr(args, "source", None)
    mode = getattr(args, "mode", "official_consolidation")

    _blame_sync(
        sid=args.statute_id,
        address_filter=address_filter,
        source_filter=source_filter,
        mode=mode,
    )
