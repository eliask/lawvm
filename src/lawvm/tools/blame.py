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
    section_key_from_target_dict,
    section_key_matches_filter,
    section_key_sort_key,
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
    """
    blame: Dict[str, dict] = {}

    for op in compiled_ops:
        key = section_key_from_target_dict(op.get("target", {}))
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


def _blame_sync(
    sid: str,
    address_filter: Optional[Tuple[str, str]],
    source_filter: Optional[str],
    mode: Literal["finlex_oracle", "legal_pit"],
) -> None:
    compiled_ops: list = []
    master = replay_xml(sid, mode=mode, quiet=True, compiled_ops_out=compiled_ops)

    blame_map = _build_blame_map(compiled_ops)

    # Collect sections from replayed IRNode tree (for ordering and display)
    replay_secs_ir = extract_ir_sections(master.ir)
    unique_sections = sorted(replay_secs_ir, key=_section_sort_key)

    # Apply filters
    if address_filter:
        unique_sections = [k for k in unique_sections if section_key_matches_filter(k, address_filter)]

    if source_filter:
        # Only show sections whose last-touching amendment matches source_filter
        unique_sections = [
            k for k in unique_sections
            if blame_map.get(k, {}).get("source_statute", "").strip() == source_filter.strip()
        ]

    print(f"Statute : {sid}")
    print(f"Title   : {master.title}")
    if address_filter:
        print(f"Address : {address_filter[0]}:{address_filter[1]}")
    if source_filter:
        print(f"Source  : {source_filter}")
    print(f"Ops     : {len(compiled_ops)} compiled")
    print()

    col_w = 12
    unblamed = []
    for key in unique_sections:
        display = display_section_key(key)
        op = blame_map.get(key)
        if op is None:
            unblamed.append(display)
            continue
        print(f"  {display:<{col_w}}  {_fmt_source(op)}")

    if unblamed:
        print()
        print("  (unmodified — base statute text, no op compiled:)")
        for display in unblamed:
            print(f"    {display}")

    print()
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
    mode = getattr(args, "mode", "finlex_oracle")

    _blame_sync(
        sid=args.statute_id,
        address_filter=address_filter,
        source_filter=source_filter,
        mode=mode,
    )
