"""lawvm replay-debug -- Finland replay diagnostics for source-filtered ops.

Useful for replacing ad hoc `python -c` probes that:
1. replay a Finnish parent statute,
2. filter compiled ops to one source amendment or target address,
3. and optionally print the amendment's working clause text,
4. and optionally trace replay metadata / event logs for one statute or section.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Literal, Optional, Protocol

from lxml import etree

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.finland.grafter import replay_xml
from lawvm.tools.inspect_amendment import build_amendment_bundle
from lawvm.tools.ops import _fmt_target, _matches_source, _matches_target
from lawvm.finland.corpus import get_corpus


class _ReplayOpLike(Protocol):
    op_id: str
    sequence: int
    action: str
    target: LegalAddress
    payload: IRNode | None
    source: OperationSource | None


def _serialize_replay_op(op: _ReplayOpLike) -> Dict[str, Any]:
    target = {
        "path": list(op.target.path),
        "special": op.target.special,
    }
    payload = op.payload
    payload_text = ""
    payload_preview = ""
    if payload is not None:
        payload_text = irnode_to_text(payload).strip()
        payload_preview = payload_text[:160]
    source = op.source
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": op.action,
        "target": target,
        "source_statute": source.statute_id if source is not None else "",
        "payload_kind": payload.kind if payload is not None else "",
        "payload_label": payload.label if payload is not None else "",
        "payload_preview": payload_preview,
        "is_repeal_placeholder": bool(
            payload is not None and payload.attrs.get("lawvm_repeal_placeholder") == "1"
        ),
    }


def _matches_replay_target(op: dict[str, Any], target: str) -> bool:
    haystack = " / ".join(
        f"{kind}:{label}" for kind, label in (op.get("target", {}) or {}).get("path", [])
    )
    special = (op.get("target", {}) or {}).get("special") or ""
    target_norm = (target or "").strip().lower()
    return target_norm in haystack.lower() or target_norm == special.lower()


def _matches_contains(payload: dict[str, Any], needle: str) -> bool:
    needle_norm = needle.strip().lower()
    if not needle_norm:
        return True
    haystack = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    return needle_norm in haystack


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _jsonable(vars(value))
    return value


def _matches_payload(payload: Any, source: Optional[str], target: Optional[str], contains: Optional[str]) -> bool:
    serialized = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, default=str).lower()
    if source and source.lower() not in serialized:
        return False
    if target and target.lower() not in serialized:
        return False
    if contains and contains.lower() not in serialized:
        return False
    return True


def _filter_payload_items(items: list[Any], source: Optional[str], target: Optional[str], contains: Optional[str]) -> list[Any]:
    filtered = [_jsonable(item) for item in items if _matches_payload(item, source, target, contains)]
    return filtered


def _load_source_blocks(source_id: str) -> list[dict[str, str]]:
    xml_bytes = get_corpus().read_source(source_id)
    if xml_bytes is None:
        return []
    tree = etree.fromstring(xml_bytes)
    blocks: list[dict[str, str]] = []
    for block in tree.findall(".//{*}block"):
        name = str(block.get("name") or "").strip()
        raw_text = " ".join(str(part) for part in block.itertext())
        text = " ".join(raw_text.split())
        if not name or not text:
            continue
        blocks.append({"name": name, "text": text})
    return blocks


def build_replay_debug_bundle(
    statute_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    *,
    source: Optional[str] = None,
    target: Optional[str] = None,
    show_clause_text: bool = False,
    show_source_blocks: bool = False,
    show_replay_ops: bool = False,
    show_replay_meta: bool = False,
    show_temporal_events: bool = False,
    show_failed_ops: bool = False,
    show_findings: bool = False,
    contains: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Build a replay-debug report for one Finnish statute."""
    compiled_ops: list[dict[str, Any]] = []
    lo_ops_out: list[Any] | None = [] if show_replay_ops else None
    replay_meta_out: dict[str, Any] | None = {} if show_replay_meta else None
    temporal_events_out: list[Any] | None = [] if show_temporal_events else None
    failed_ops_out: list[Any] | None = [] if show_failed_ops else None
    master = replay_xml(
        statute_id,
        mode=mode,
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta_out,
        lo_ops_out=lo_ops_out,
        failed_ops_out=failed_ops_out,
        temporal_events_out=temporal_events_out,
        quiet=True,
        build_full_products=False,
    )

    ops = compiled_ops
    if source:
        ops = [op for op in ops if _matches_source(op, source)]
    if target:
        ops = [op for op in ops if _matches_target(op, target)]
    if contains:
        ops = [op for op in ops if _matches_contains(op, contains)]

    replay_ops: list[dict[str, Any]] = []
    if lo_ops_out is not None:
        replay_ops = [_serialize_replay_op(op) for op in lo_ops_out]
        if source:
            replay_ops = [op for op in replay_ops if op.get("source_statute") == source]
        if target:
            replay_ops = [op for op in replay_ops if _matches_replay_target(op, target)]
        if contains:
            replay_ops = [op for op in replay_ops if _matches_contains(op, contains)]

    replay_meta: Dict[str, Any] | None = None
    if replay_meta_out is not None:
        replay_meta = {}
        for key, value in replay_meta_out.items():
            if isinstance(value, list):
                filtered_items = _filter_payload_items(list(value), source, target, contains)
                if filtered_items:
                    replay_meta[key] = filtered_items[:limit]
                elif not (source or target or contains):
                    replay_meta[key] = [_jsonable(item) for item in value[:limit]]
            else:
                replay_meta[key] = _jsonable(value)

    temporal_events: list[dict[str, Any]] = []
    if temporal_events_out is not None:
        temporal_events = _filter_payload_items(list(temporal_events_out), source, target, contains)[:limit]

    failed_ops: list[Any] = []
    if failed_ops_out is not None:
        failed_ops = _filter_payload_items(list(failed_ops_out), source, target, contains)[:limit]

    findings: list[Any] = []
    if show_findings:
        findings = _filter_payload_items(list(getattr(master, "findings", ()) or ()), source, target, contains)[:limit]

    report: Dict[str, Any] = {
        "statute_id": statute_id,
        "title": master.title,
        "mode": mode,
        "source": source or "",
        "target": target or "",
        "contains": contains or "",
        "ops_total": len(compiled_ops),
        "ops_shown": len(ops),
        "compiled_ops": ops,
        "replay_ops": replay_ops,
    }
    if replay_meta is not None:
        report["replay_meta"] = replay_meta
    if temporal_events:
        report["temporal_events"] = temporal_events
    if failed_ops:
        report["failed_ops"] = failed_ops
    if findings:
        report["findings"] = findings

    if show_clause_text or show_source_blocks:
        if not source:
            raise SystemExit("--show-clause-text/--show-source-blocks requires --source")
        source_bundle = build_amendment_bundle(statute_id, source, mode)
        report["source_title"] = source_bundle.get("source_title", "")
        if show_clause_text:
            report["source_clause_text"] = source_bundle.get("johtolause", "")
        if show_source_blocks:
            report["source_blocks"] = _load_source_blocks(source)
        report["source_route"] = dict(source_bundle.get("route", {}) or {})
        report["source_used_sec1_fallback"] = bool(source_bundle.get("used_sec1_fallback"))

    return report


def _format_text(bundle: Dict[str, Any]) -> str:
    lines = [
        f"Statute  : {bundle['statute_id']}",
        f"Title    : {bundle.get('title', '')}",
        f"Mode     : {bundle['mode']}",
        f"Ops total: {bundle['ops_total']}  shown: {bundle['ops_shown']}",
    ]
    if bundle.get("source"):
        lines.append(f"Filter   : source={bundle['source']}")
    if bundle.get("target"):
        lines.append(f"Filter   : target={bundle['target']}")
    if bundle.get("contains"):
        lines.append(f"Filter   : contains={bundle['contains']}")

    source_clause_text = bundle.get("source_clause_text") or ""
    if source_clause_text:
        lines.append("")
        if bundle.get("source_title"):
            lines.append(f"Source title : {bundle['source_title']}")
        if bundle.get("source_route"):
            route = bundle["source_route"]
            lines.append(f"Source route : {'apply' if route.get('should_apply') else 'skip'} ({route.get('reason', '')})")
        if bundle.get("source_used_sec1_fallback") is not None:
            lines.append(f"Sec1 fallback: {'yes' if bundle.get('source_used_sec1_fallback') else 'no'}")
        lines.append("")
        lines.append("Source clause:")
        for line in source_clause_text.splitlines() or [""]:
            lines.append(f"  {line}")
    if bundle.get("source_blocks"):
        lines.append("")
        lines.append("Source blocks:")
        for block in bundle["source_blocks"]:
            lines.append(f"  [{block.get('name', '')}] {block.get('text', '')}")

    lines.append("")
    lines.append("Compiled ops:")
    if not bundle.get("compiled_ops"):
        lines.append("  (no compiled ops match filters)")
    else:
        current_source = None
        for op in bundle["compiled_ops"]:
            src = op.get("source_statute", "?")
            title = op.get("source_title", "")[:50]
            seq = op.get("sequence", "?")
            action = str(op.get("action", "?")).upper()
            target = op.get("target", {})
            addr = _fmt_target(target)

            if src != current_source:
                lines.append(f"--- {src}  {title}")
                current_source = src

            lines.append(f"  [{seq:3}] {action:<8}  {addr}")

    if bundle.get("replay_ops"):
        lines.append("")
        lines.append("Replay ops:")
        current_source = None
        for op in bundle["replay_ops"]:
            src = op.get("source_statute", "?")
            seq = op.get("sequence", "?")
            action = str(op.get("action", "?")).upper()
            target_bits = [
                f"{kind}:{label}" for kind, label in (op.get("target", {}) or {}).get("path", [])
            ]
            target = " / ".join(target_bits) or "?"
            if src != current_source:
                lines.append(f"--- {src}")
                current_source = src
            suffix = " [placeholder]" if op.get("is_repeal_placeholder") else ""
            lines.append(f"  [{seq:3}] {action:<8}  {target}{suffix}")
            if op.get("payload_preview"):
                lines.append(f"        {op['payload_preview']}")
    elif not bundle.get("compiled_ops"):
        lines.append("")
        lines.append("(no operations match filters)")

    if bundle.get("replay_meta"):
        lines.append("")
        lines.append("Replay meta:")
        for key, value in bundle["replay_meta"].items():
            if isinstance(value, list):
                lines.append(f"  {key} [{len(value)}]:")
                for item in value[:10]:
                    lines.append(f"    - {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)}")
                if len(value) > 10:
                    lines.append(f"    ... {len(value) - 10} more")
            else:
                lines.append(f"  {key}: {value}")

    if bundle.get("temporal_events"):
        lines.append("")
        lines.append("Temporal events:")
        for event in bundle["temporal_events"]:
            lines.append(f"  - {json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)}")

    if bundle.get("failed_ops"):
        lines.append("")
        lines.append("Failed ops:")
        for op in bundle["failed_ops"]:
            lines.append(f"  - {json.dumps(op, ensure_ascii=False, sort_keys=True, default=str)}")

    if bundle.get("findings"):
        lines.append("")
        lines.append("Findings:")
        for finding in bundle["findings"]:
            lines.append(f"  - {json.dumps(finding, ensure_ascii=False, sort_keys=True, default=str)}")

    return "\n".join(lines)


def main(args) -> None:
    bundle = build_replay_debug_bundle(
        statute_id=args.statute_id,
        mode=getattr(args, "mode", "finlex_oracle"),
        source=getattr(args, "source", None),
        target=getattr(args, "target", None),
        show_clause_text=getattr(args, "show_clause_text", False),
        show_source_blocks=getattr(args, "show_source_blocks", False),
        show_replay_ops=getattr(args, "show_replay_ops", False),
        show_replay_meta=getattr(args, "show_replay_meta", False),
        show_temporal_events=getattr(args, "show_temporal_events", False),
        show_failed_ops=getattr(args, "show_failed_ops", False),
        show_findings=getattr(args, "show_findings", False),
        contains=getattr(args, "contains", None),
        limit=getattr(args, "limit", 10),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return
    print(_format_text(bundle))
