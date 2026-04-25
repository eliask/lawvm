"""lawvm timeline — Finnish temporal versioning query tool (Phase 7).

Builds ProvisionTimelines from Finnish statute replay and supports:
  - Summary: provision count, amendment count, operation stats
  - List: all addressable provisions with version counts
  - Lineage: complete version history of one provision
  - PIT materialization: reconstruct statute text at a past date
  - Export: dump all timelines as JSON for external use

Usage:
    lawvm timeline 2009/953
    lawvm timeline 2009/953 --list
    lawvm timeline 2009/953 --provision section:4
    lawvm timeline 2009/953 --provision chapter:1/section:4
    lawvm timeline 2009/953 --as-of 2015-06-01
    lawvm timeline 2009/953 --export timelines.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from lawvm.core.ir import LegalAddress


def main(args) -> None:
    asyncio.run(_main(args))


async def _main(args) -> None:
    from lawvm.core.ir import IRStatute
    from lawvm.core.timeline import materialize_pit
    from lawvm.core.ir_helpers import irnode_to_text as _irnode_to_text
    from lawvm.finland.grafter import replay_xml

    sid = args.statute_id
    print(f"Replaying {sid}...", file=sys.stderr)

    lo_ops_out: List = []
    master = replay_xml(sid, quiet=True, lo_ops_out=lo_ops_out)
    timelines = master.timelines
    base_ir = IRStatute(statute_id=sid, title=master.title, body=master.ctx.base_ir)

    query_type = getattr(args, "query_type", "governing") or "governing"

    if args.list:
        _cmd_list(timelines)
    elif args.provision:
        _cmd_provision(timelines, args.provision)
    elif args.as_of:
        _cmd_as_of(timelines, args.as_of, base_ir, _irnode_to_text, materialize_pit, query_type)
    elif args.export:
        _cmd_export(timelines, args.export)
    else:
        _cmd_summary(timelines, lo_ops_out, sid, master.title)


def _addr_str(path: tuple) -> str:
    return "/".join(f"{k}:{v}" for k, v in path)


def _parse_addr(addr_str: str) -> Optional["LegalAddress"]:
    from lawvm.core.ir import LegalAddress
    pairs: List[Tuple[str, str]] = []
    for part in addr_str.split("/"):
        if ":" in part:
            k, v = part.split(":", 1)
            pairs.append((k.strip(), v.strip()))
    if not pairs:
        return None
    return LegalAddress(path=tuple(pairs))


def _find_in_timelines(timelines, addr_str: str):
    """Find timeline by exact address or suffix match."""
    target = _parse_addr(addr_str)
    if target is None:
        return None, None
    # Exact match
    if target in timelines:
        return target, timelines[target]
    # Suffix match (handles flat PEG paths vs hierarchical base addresses)
    suffix = target.path
    for addr, tl in timelines.items():
        if addr.path[-len(suffix):] == suffix:
            return addr, tl
    return None, None


def _cmd_summary(timelines, lo_ops, sid: str, title: str) -> None:
    n_provisions = len(timelines)
    amended = sum(1 for tl in timelines.values() if len(tl.versions) > 1)
    n_ops = len(lo_ops)
    with_payload = sum(1 for lo in lo_ops if lo.payload is not None)
    actions: Dict[str, int] = {}
    for lo in lo_ops:
        actions[lo.action] = actions.get(lo.action, 0) + 1

    print(f"Statute   : {sid}")
    if title:
        print(f"Title     : {title[:80]}")
    print(f"Provisions: {n_provisions}  (amended: {amended})")
    print(f"Operations: {n_ops}  (with payload: {with_payload})")
    for action, count in sorted(actions.items()):
        print(f"  {action}: {count}")


def _cmd_list(timelines) -> None:
    rows = []
    for addr, tl in timelines.items():
        rows.append((_addr_str(addr.path), len(tl.versions)))
    rows.sort(key=lambda r: r[0])
    for path_str, n_versions in rows:
        version_tag = f"({n_versions}v)" if n_versions > 1 else ""
        print(f"{path_str:<50} {version_tag}")


def _cmd_provision(timelines, addr_str: str) -> None:
    addr, tl = _find_in_timelines(timelines, addr_str)
    if tl is None:
        print(f"Provision not found: {addr_str}", file=sys.stderr)
        return

    full_addr = _addr_str(addr.path)
    print(f"Lineage of {full_addr}  ({len(tl.versions)} version(s))")
    for v in tl.versions:
        if v.content is None:
            content_info = "TOMBSTONE (repealed)"
        else:
            child_count = len(v.content.children)
            content_info = f"{v.content.kind} ({child_count} children)"
        source_str = f"  [{v.source.statute_id}]" if v.source else ""
        print(f"  {v.effective or '0000-00-00'}  {content_info}{source_str}")


def _cmd_as_of(timelines, date: str, base_ir, _irnode_to_text, materialize_pit, query_type: str = "governing") -> None:
    pit = materialize_pit(timelines, as_of=date, base=base_ir, query_type=query_type)
    text = _irnode_to_text(pit.body)
    qt_note = f" [{query_type}]" if query_type != "governing" else ""
    print(f"=== {pit.statute_id} at {date}{qt_note} ===")
    print(text)


def _cmd_export(timelines, path: str) -> None:
    data: Dict = {}
    for addr, tl in timelines.items():
        key = _addr_str(addr.path)
        data[key] = [
            {
                "effective": v.effective,
                "enacted": v.enacted,
                "source": v.source.statute_id if v.source else None,
                "content": v.content.to_dict() if v.content else None,
            }
            for v in tl.versions
        ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(data)} timelines → {path}")
