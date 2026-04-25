"""lawvm snapshot-debug — inspect timeline snapshots emitted for one amendment.

Shows the LegalOperation snapshots that process_muutoslaki emits (via lo_ops_out)
for one amendment, optionally filtered to a specific target address.  This
disambiguates between the direct-applied-state and the emitted snapshot payload
— a gap that is otherwise invisible in the replay output.

Each snapshot is a LegalOperation with:
  action    — REPLACE / INSERT / etc.
  target    — the canonical LegalAddress of the provision
  payload   — the IRNode written to the timeline
  source    — OperationSource (which amendment, effective date, expires, etc.)

Usage:
    lawvm snapshot-debug 1995/398 --source 2013/982
    lawvm snapshot-debug 1995/398 --source 2013/982 --target section:20
    lawvm snapshot-debug 1995/398 --source 2013/982 --target chapter:4/section:20
    lawvm snapshot-debug 1995/398 --source 2013/982 --json
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal


# ---------------------------------------------------------------------------
# Target matching
# ---------------------------------------------------------------------------

def _target_matches_lo(lo: Any, target_path: str) -> bool:
    """True if the LegalOperation's target contains target_path as a subsegment."""
    if not target_path:
        return True
    # Parse target_path into (kind, label) tuples
    target_parts = []
    for part in target_path.split("/"):
        if ":" in part:
            k, v = part.split(":", 1)
            target_parts.append((k.strip(), v.strip()))
    if not target_parts:
        return True
    target_tuples = tuple(target_parts)
    addr_path = lo.target.path
    n, m = len(addr_path), len(target_tuples)
    for i in range(n - m + 1):
        if addr_path[i : i + m] == target_tuples:
            return True
    return False


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------

def build_snapshot_debug_bundle(
    statute_id: str,
    source_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    target_path: str = "",
) -> Dict[str, Any]:
    """Capture lo_ops from process_muutoslaki and filter to the target address."""
    from lawvm.finland.grafter import (
        get_corpus,
        process_muutoslaki,
        _resolve_applicable_amendment_records,
        replay_xml,
    )
    from lawvm.finland.statute import StatuteContext
    from lawvm.finland.helpers import _fi_label_postprocessor
    from lawvm.core.ir_helpers import irnode_to_text

    cs = get_corpus()
    xml_bytes = cs.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"statute not found in corpus: {statute_id!r}")

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    records, _cutoff, _ov = _resolve_applicable_amendment_records(statute_id, mode)
    amendment_ids = [str(r["statute_id"]) for r in records]

    if source_id not in amendment_ids:
        raise SystemExit(f"amendment {source_id!r} not in chain for {statute_id!r}")

    source_idx = amendment_ids.index(source_id)

    # Build before_state
    before_master = replay_xml(statute_id, mode=mode, stop_before=source_id, quiet=True)
    before_state = before_master.replay_fold_state

    # Run process_muutoslaki with lo_ops captured
    lo_ops_out: List[Any] = []
    restructure_plans_out: List[Any] = []
    process_muutoslaki(
        source_id,
        before_state,
        ctx,
        replay_mode=mode,
        parent_id=statute_id,
        corpus=cs,
        lo_ops_out=lo_ops_out,
        restructure_plans_out=restructure_plans_out,
    )

    # Filter to target if specified
    filtered_ops = [lo for lo in lo_ops_out if _target_matches_lo(lo, target_path)]

    def _serialize_lo(lo: Any) -> Dict[str, Any]:
        target_str = "/".join(f"{k}:{v}" for k, v in lo.target.path)
        if lo.target.special:
            target_str += f"/{lo.target.special}"
        payload_text = ""
        payload_summary = ""
        if lo.payload is not None:
            payload_text = irnode_to_text(lo.payload)
            payload_summary = f"{lo.payload.kind} ({len(lo.payload.children)} children)"
        source_info: Dict[str, Any] = {}
        if lo.source is not None:
            source_info = {
                "statute_id": lo.source.statute_id or "",
                "effective": lo.source.effective or "",
                "expires": lo.source.expires or "",
                "enacted": lo.source.enacted or "",
            }
        return {
            "op_id": lo.op_id or "",
            "sequence": lo.sequence,
            "action": str(lo.action),
            "target": target_str,
            "payload_summary": payload_summary,
            "payload_text_preview": payload_text[:200] if payload_text else "",
            "source": source_info,
            "group_id": lo.group_id or "",
        }

    ops_serialized = [_serialize_lo(lo) for lo in filtered_ops]
    all_ops_count = len(lo_ops_out)

    return {
        "statute_id": statute_id,
        "source_id": source_id,
        "mode": mode,
        "target_path": target_path or "(all)",
        "amendment_index": source_idx,
        "total_amendments": len(amendment_ids),
        "total_lo_ops": all_ops_count,
        "matched_lo_ops": len(filtered_ops),
        "snapshots": ops_serialized,
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
        f"Total lo_ops emitted : {bundle['total_lo_ops']}",
        f"Matched (target)     : {bundle['matched_lo_ops']}",
        "",
    ]
    if not bundle["snapshots"]:
        lines.append("No snapshots emitted for this target.")
        return "\n".join(lines)

    for i, snap in enumerate(bundle["snapshots"], start=1):
        source = snap.get("source") or {}
        effective = source.get("effective") or "?"
        expires = source.get("expires") or ""
        expires_str = f" → {expires}" if expires else ""
        lines.extend([
            f"Snapshot {i}:",
            f"  action  : {snap['action']}",
            f"  target  : {snap['target']}",
            f"  source  : {source.get('statute_id', '?')}  "
            f"effective={effective}{expires_str}",
            f"  payload : {snap['payload_summary'] or '(none)'}",
        ])
        if snap.get("payload_text_preview"):
            preview = snap["payload_text_preview"][:120]
            lines.append(f"  text    : {preview!r}...")
        if snap.get("group_id"):
            lines.append(f"  group   : {snap['group_id']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    bundle = build_snapshot_debug_bundle(
        statute_id=args.statute_id,
        source_id=args.source,
        mode=getattr(args, "mode", "legal_pit"),
        target_path=getattr(args, "target", "") or "",
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle))
