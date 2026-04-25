"""lawvm product-debug — inspect timeline and materialization for one amendment.

For one statute and one amendment, shows the timeline entries that affect a
target address and the active version selection at the replay cutoff date.

This diagnoses the "direct_applied_state clean, materialized bad" class of bugs
where the violation is introduced by timeline compilation or materialization
rather than the apply phase.

Output surfaces:
  timeline entries   — all ProvisionVersions recorded for the target address
  active version     — which version select_active_version_ex picks at cutoff
  migration events   — address rekey/renumber events affecting the target
  materialized text  — IRNode text at the cutoff date

Usage:
    lawvm product-debug 1995/398 --source 2013/982 --target section:20
    lawvm product-debug 1995/398 --source 2013/982 --target chapter:4/section:20
    lawvm product-debug 1995/398 --source 2013/982 --json
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Tuple


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _parse_target_path(target_path: str) -> Tuple[Tuple[str, str], ...]:
    """Parse 'chapter:4/section:20' into (('chapter','4'),('section','20'))."""
    parts = []
    for seg in target_path.split("/"):
        seg = seg.strip()
        if ":" in seg:
            k, v = seg.split(":", 1)
            parts.append((k.strip(), v.strip()))
    return tuple(parts)


def _address_matches_target(address: Any, target_tuples: Tuple[Tuple[str, str], ...]) -> bool:
    """True if address.path contains target_tuples as a contiguous suffix or subsequence."""
    if not target_tuples:
        return True
    addr_path = address.path
    n, m = len(addr_path), len(target_tuples)
    for i in range(n - m + 1):
        if addr_path[i : i + m] == target_tuples:
            return True
    return False


def _addr_str(path: Tuple[Tuple[str, str], ...]) -> str:
    return "/".join(f"{k}:{v}" for k, v in path)


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------

def build_product_debug_bundle(
    statute_id: str,
    source_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    target_path: str = "",
) -> Dict[str, Any]:
    """Produce a timeline+materialization diagnostic bundle for one amendment."""
    from lawvm.finland.grafter import (
        _resolve_applicable_amendment_records,
        replay_xml,
    )
    from lawvm.core.timeline import select_active_version_ex
    from lawvm.core.ir_helpers import irnode_to_text

    records, cutoff_date, _ov = _resolve_applicable_amendment_records(statute_id, mode)
    amendment_ids = [str(r["statute_id"]) for r in records]

    if source_id not in amendment_ids:
        raise SystemExit(f"amendment {source_id!r} not in chain for {statute_id!r}")

    source_idx = amendment_ids.index(source_id)

    # Run replay_xml stopped after source_id (before next amendment)
    next_mid = amendment_ids[source_idx + 1] if source_idx + 1 < len(amendment_ids) else ""
    if next_mid:
        master = replay_xml(statute_id, mode=mode, stop_before=next_mid, quiet=True)
    else:
        master = replay_xml(statute_id, mode=mode, quiet=True)

    timelines = master.timelines or {}
    migration_events = list(master.migration_events or [])
    materialized_state = master.state

    # Determine cutoff date for selection
    cutoff_str = cutoff_date.isoformat() if cutoff_date else "9999-12-31"

    # Parse target
    target_tuples = _parse_target_path(target_path)

    # Find all timeline entries matching the target
    matched_timelines: List[Dict[str, Any]] = []
    for addr, tl in timelines.items():
        if not _address_matches_target(addr, target_tuples):
            continue
        addr_str = _addr_str(addr.path)

        # Serialize versions
        versions_info: List[Dict[str, Any]] = []
        for v in tl.versions:
            text_preview = ""
            if v.content is not None:
                full_text = irnode_to_text(v.content)
                text_preview = full_text[:150]
            source_info: Dict[str, Any] = {}
            if v.source is not None:
                source_info = {
                    "statute_id": v.source.statute_id or "",
                    "effective": v.source.effective or "",
                    "expires": v.source.expires or "",
                    "enacted": v.source.enacted or "",
                }
            versions_info.append({
                "effective": v.effective,
                "expires": v.expires or "",
                "enacted": v.enacted or "",
                "variant_kind": v.variant_kind,
                "is_tombstone": v.content is None,
                "source": source_info,
                "text_preview": text_preview,
            })

        # Find active version at cutoff
        try:
            sel = select_active_version_ex(tl, as_of=cutoff_str)
            active_status = sel.status
            active_source = ""
            if sel.version is not None and sel.version.source:
                active_source = sel.version.source.statute_id or ""
        except Exception as exc:
            active_status = f"error: {exc}"
            active_source = ""

        matched_timelines.append({
            "address": addr_str,
            "version_count": len(tl.versions),
            "active_status": active_status,
            "active_source": active_source,
            "versions": versions_info,
        })

    # Find migration events affecting the target
    migration_info: List[Dict[str, Any]] = []
    for event in migration_events:
        from_matches = _address_matches_target(event.from_address, target_tuples)
        to_matches = _address_matches_target(event.to_address, target_tuples)
        if from_matches or to_matches:
            migration_info.append({
                "event_id": event.event_id,
                "kind": event.kind,
                "from_address": _addr_str(event.from_address.path),
                "to_address": _addr_str(event.to_address.path),
                "effective": event.effective or "",
                "source_statute": event.source_statute or "",
            })

    # Materialized text at cutoff for target
    materialized_text = ""
    if target_tuples and materialized_state is not None:
        from lawvm.core.tree_ops import find as _find, resolve as _resolve
        # Try to find the target node in the materialized tree
        ir = materialized_state.ir
        # Find by kind + label from the last part of target_tuples
        leaf_kind, leaf_label = target_tuples[-1]
        scope_kind, scope_label = (target_tuples[-2] if len(target_tuples) >= 2 else (None, None))
        path = _find(
            ir, leaf_kind, leaf_label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )
        if path is not None:
            node = _resolve(ir, path)
            if node is not None:
                materialized_text = irnode_to_text(node)[:300]

    return {
        "statute_id": statute_id,
        "source_id": source_id,
        "mode": mode,
        "target_path": target_path or "(all)",
        "amendment_index": source_idx,
        "total_amendments": len(amendment_ids),
        "cutoff_date": cutoff_str,
        "timeline_entries": matched_timelines,
        "timeline_entries_count": len(matched_timelines),
        "migration_events": migration_info,
        "materialized_text_preview": materialized_text,
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def _format_text(bundle: Dict[str, Any]) -> str:
    lines = [
        f"Statute    : {bundle['statute_id']}",
        f"Amendment  : {bundle['source_id']}  "
        f"(#{bundle['amendment_index'] + 1} / {bundle['total_amendments']})",
        f"Mode       : {bundle['mode']}",
        f"Target     : {bundle['target_path']}",
        f"Cutoff     : {bundle['cutoff_date']}",
        f"Timeline entries matched: {bundle['timeline_entries_count']}",
        "",
    ]

    for tl_entry in bundle["timeline_entries"]:
        lines.append(f"Address: {tl_entry['address']}")
        lines.append(
            f"  Active at cutoff : {tl_entry['active_status']}"
            + (f" (from {tl_entry['active_source']})" if tl_entry["active_source"] else "")
        )
        lines.append(f"  Version count    : {tl_entry['version_count']}")
        for v in tl_entry["versions"]:
            tombstone = " TOMBSTONE" if v["is_tombstone"] else ""
            variant = f" [{v['variant_kind']}]" if v["variant_kind"] != "permanent" else ""
            expires = f" → {v['expires']}" if v["expires"] else ""
            src = f"  [{v['source'].get('statute_id', '?')}]" if v["source"] else ""
            lines.append(
                f"    {v['effective'] or '0000-00-00'}{expires}{variant}{tombstone}{src}"
            )
            if v.get("text_preview"):
                lines.append(f"      {v['text_preview'][:100]!r}...")
        lines.append("")

    if bundle["migration_events"]:
        lines.append("Migration events (address changes):")
        for ev in bundle["migration_events"]:
            lines.append(
                f"  {ev['kind']}  {ev['from_address']} → {ev['to_address']}  "
                f"[{ev['source_statute']}  {ev['effective']}]"
            )
        lines.append("")

    if bundle["materialized_text_preview"]:
        lines.append("Materialized text (at cutoff):")
        lines.append(f"  {bundle['materialized_text_preview'][:200]!r}...")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    bundle = build_product_debug_bundle(
        statute_id=args.statute_id,
        source_id=args.source,
        mode=getattr(args, "mode", "legal_pit"),
        target_path=getattr(args, "target", "") or "",
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle))
