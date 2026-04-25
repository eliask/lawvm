"""lawvm phase-witness — machine-readable Finland replay phase witness.

This command is a bounded Tranche 0 debug artifact: for one parent statute and
one amendment, emit the source-lane decision, clause/effect surface summary,
payload/elaboration state, lowered ops, replay-fold witness, and materialized
product summary as one JSON-ready bundle.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from lxml import etree

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.finland.acquisition import build_amendment_acquisition_result
from lawvm.finland.grafter import (
    _resolve_applicable_amendment_records,
    _tree_title,
    get_corpus,
    replay_xml,
)
from lawvm.finland.johtolause.api import parse_clause
from lawvm.tools._section_debug import summarize_node
from lawvm.tools.inspect_amendment import build_amendment_bundle
from lawvm.tools.product_debug import build_product_debug_bundle
from lawvm.tools.snapshot_debug import build_snapshot_debug_bundle


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _jsonable(vars(value))
    return str(value)


def _collapse_ws(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _build_acquisition_witness(
    *,
    parent_id: str,
    parent_title: str,
    source_id: str,
    source_title: str,
    xml_bytes: bytes,
) -> dict[str, Any]:
    result = build_amendment_acquisition_result(
        xml_bytes=xml_bytes,
        parent_id=parent_id,
        amendment_id=source_id,
        source_title=source_title,
        parent_title=parent_title,
    )

    return {
        "source_lane_used": result.decision.selected_lane,
        "chosen_operative_text": result.decision.chosen_normalized_text,
        "route": {
            "should_apply": result.decision.should_apply,
            "reason": result.decision.route_reason,
            "target_amendment_id": result.decision.route_target_amendment_id,
        },
        "raw_operative_text_candidates": [_jsonable(candidate) for candidate in result.candidates],
        "rejected_lanes": [
            {"lane": lane, "reason": reason}
            for lane, reason in result.rejected_lanes
        ],
    }


def _serialize_clause_surface(clause_result: Any) -> dict[str, Any]:
    clause_ast = clause_result.clause_ast
    return {
        "parse_error": clause_result.parse_error,
        "diagnostics": list(clause_result.diagnostics),
        "residuals": _jsonable(clause_result.residuals),
        "verb_group_count": len(tuple(getattr(clause_ast, "verb_groups", ()) or ())),
        "parsed_op_count": len(list(clause_result.parsed_ops or [])),
        "parsed_ops": _jsonable(clause_result.parsed_ops),
        "meta_clauses": _jsonable(clause_result.meta_clauses),
        "supplementary_clauses": _jsonable(clause_result.supplementary_clauses),
        "clause_ast": _jsonable(clause_ast),
    }


def _scope_text_for_group(group: dict[str, Any]) -> str:
    unit_kind = str(group.get("target_unit_kind") or "")
    target_norm = str(group.get("target_norm") or "")
    chapter = str(group.get("target_chapter") or "")
    part = str(group.get("target_part") or "")
    bits: list[str] = []
    if part:
        bits.append(f"part:{part}")
    if chapter:
        bits.append(f"chapter:{chapter}")
    if unit_kind and target_norm:
        bits.append(f"{unit_kind}:{target_norm}")
    return "/".join(bits)


def _group_matches_target(group: dict[str, Any], target_path: str) -> bool:
    if not target_path:
        return True
    target_norm = target_path.strip().lower()
    scope = _scope_text_for_group(group).lower()
    return target_norm in scope or scope in target_norm


def _serialize_payload_groups(groups: Iterable[dict[str, Any]], target_path: str = "") -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for group in groups:
        if not _group_matches_target(group, target_path):
            continue
        serialized.append(
            {
                "scope": _scope_text_for_group(group),
                "raw_payload": group.get("raw_payload"),
                "prepared_payload": group.get("prepared_payload"),
                "normalized_payload": group.get("normalized_payload"),
                "ops_raw": list(group.get("ops_raw") or []),
                "ops_final": list(group.get("ops_final") or []),
                "sparse_slot_bindings": list(group.get("sparse_slot_bindings") or []),
                "source_pathologies": list(group.get("source_pathologies") or []),
                "elaboration_observations": list(group.get("elaboration_observations") or []),
            }
        )
    return serialized


def _matches_target_text(value: str, target_path: str) -> bool:
    if not target_path:
        return True
    target_norm = target_path.strip().lower()
    value_norm = value.strip().lower()
    return target_norm in value_norm or value_norm in target_norm


def _filter_compiled_ops(
    compiled_ops: Iterable[dict[str, Any]],
    *,
    source_id: str,
    target_path: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for op in compiled_ops:
        if str(op.get("source_statute") or "") != source_id:
            continue
        if target_path and not _matches_target_text(str(op.get("target_text") or op.get("target") or ""), target_path):
            continue
        filtered.append(dict(op))
    return filtered


def _filter_failed_ops(
    failed_ops: Iterable[Any],
    *,
    source_id: str,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for op in failed_ops:
        if str(getattr(op, "source_statute", getattr(op, "amendment_id", "")) or "") != source_id:
            continue
        filtered.append(_jsonable(op))
    return filtered


def _filter_temporal_events(
    temporal_events: Iterable[Any],
    *,
    source_id: str,
    target_path: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for event in temporal_events:
        source = getattr(event, "source", None)
        if str(getattr(source, "statute_id", "") or "") != source_id:
            continue
        event_payload = _jsonable(event)
        if target_path and not _matches_target_text(json.dumps(event_payload, ensure_ascii=False, sort_keys=True), target_path):
            continue
        filtered.append(event_payload)
    return filtered


def _filter_migration_events(
    migration_events: Iterable[Any],
    *,
    source_id: str,
    target_path: str = "",
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for event in migration_events:
        if str(getattr(event, "source_statute", "") or "") != source_id:
            continue
        payload = _jsonable(event)
        if target_path and not _matches_target_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), target_path):
            continue
        filtered.append(payload)
    return filtered


def _filter_findings(findings: Iterable[Any], *, source_id: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for finding in findings:
        detail_text = json.dumps(_jsonable(getattr(finding, "detail", {})), ensure_ascii=False, sort_keys=True)
        if str(getattr(finding, "source_statute", "") or "") != source_id and source_id not in detail_text:
            continue
        filtered.append(_jsonable(finding))
    return filtered


def _finding_role_counts(findings: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        role = str(getattr(finding, "role", "") or "unknown")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _state_target_witness(state: Any, target_path: str) -> dict[str, Any]:
    if not target_path:
        return {
            "tree_summary": summarize_node(state.ir),
            "text_preview": irnode_to_text(state.ir)[:240],
        }
    from lawvm.core.tree_ops import find as _find, resolve as _resolve

    target_parts: list[tuple[str, str]] = []
    for part in target_path.split("/"):
        if ":" not in part:
            continue
        kind, label = part.split(":", 1)
        target_parts.append((kind.strip(), label.strip()))
    if not target_parts:
        return {
            "tree_summary": summarize_node(state.ir),
            "text_preview": irnode_to_text(state.ir)[:240],
        }
    leaf_kind, leaf_label = target_parts[-1]
    scope_kind: Optional[str] = None
    scope_label: Optional[str] = None
    if len(target_parts) >= 2:
        scope_kind, scope_label = target_parts[-2]
    path = _find(
        state.ir,
        leaf_kind,
        leaf_label,
        scope_kind=scope_kind,
        scope_label=scope_label,
    )
    if path is None:
        return {
            "matched": False,
            "target_path": target_path,
        }
    node = _resolve(state.ir, path)
    if node is None:
        return {
            "matched": False,
            "target_path": target_path,
        }
    return {
        "matched": True,
        "target_path": target_path,
        "node_summary": summarize_node(node),
        "text_preview": irnode_to_text(node)[:240],
    }


def _comparison_summary(master: Any) -> dict[str, Any]:
    source_adjudication = getattr(master, "source_adjudication", None)
    oracle_selector_info = getattr(master, "oracle_selector_info", None)
    return {
        "source_adjudication": _jsonable(source_adjudication),
        "oracle_selector_info": _jsonable(oracle_selector_info),
        "projection_rows_count": len(tuple(master.projection_rows() or ())),
    }


def build_phase_witness_bundle(
    statute_id: str,
    source_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    target_path: str = "",
) -> dict[str, Any]:
    records, _cutoff, _oracle_version = _resolve_applicable_amendment_records(statute_id, mode)
    amendment_ids = [str(record["statute_id"]) for record in records]
    if source_id not in amendment_ids:
        raise SystemExit(f"amendment {source_id!r} not in replay chain for {statute_id!r}")
    source_idx = amendment_ids.index(source_id)
    next_mid = amendment_ids[source_idx + 1] if source_idx + 1 < len(amendment_ids) else ""

    before_master = replay_xml(statute_id, mode=mode, stop_before=source_id, quiet=True)
    source_xml = get_corpus().read_source(source_id)
    if source_xml is None:
        raise SystemExit(f"amendment not found in corpus: {source_id!r}")
    source_tree = etree.fromstring(source_xml)
    source_title = _tree_title(source_tree)

    acquisition = _build_acquisition_witness(
        parent_id=statute_id,
        parent_title=before_master.title,
        source_id=source_id,
        source_title=source_title,
        xml_bytes=source_xml,
    )
    clause_surface = _serialize_clause_surface(parse_clause(acquisition["chosen_operative_text"], statute_id=source_id))

    amendment_bundle = build_amendment_bundle(
        statute_id=statute_id,
        source_id=source_id,
        mode=mode,
    )

    compiled_ops_out: list[dict[str, Any]] = []
    replay_meta_out: dict[str, Any] = {}
    lo_ops_out: list[Any] = []
    failed_ops_out: list[Any] = []
    temporal_events_out: list[Any] = []
    after_master = replay_xml(
        statute_id,
        mode=mode,
        compiled_ops_out=compiled_ops_out,
        replay_meta_out=replay_meta_out,
        lo_ops_out=lo_ops_out,
        stop_before=next_mid,
        failed_ops_out=failed_ops_out,
        temporal_events_out=temporal_events_out,
        quiet=True,
    )

    snapshot_bundle = build_snapshot_debug_bundle(
        statute_id=statute_id,
        source_id=source_id,
        mode=mode,
        target_path=target_path,
    )
    product_bundle = build_product_debug_bundle(
        statute_id=statute_id,
        source_id=source_id,
        mode=mode,
        target_path=target_path,
    )
    source_findings = _filter_findings(after_master.findings, source_id=source_id)
    projection_rows = [dict(row) for row in (after_master.projection_rows() or ())]
    source_projection_rows = [
        row
        for row in projection_rows
        if str(row.get("source") or "") == source_id
    ]

    return {
        "schema": "lawvm.phase_witness.v1",
        "statute_id": statute_id,
        "source_id": source_id,
        "mode": mode,
        "target_path": target_path or "",
        "amendment_index": source_idx,
        "total_amendments": len(amendment_ids),
        "source_title": source_title,
        "acquisition": acquisition,
        "clause_effect_surface": clause_surface,
        "payload_surface": {
            "route": dict(amendment_bundle.get("route") or {}),
            "source_payload": dict(amendment_bundle.get("source_payload") or {}),
            "groups": _serialize_payload_groups(amendment_bundle.get("groups") or [], target_path=target_path),
        },
        "lowered_ops": {
            "compiled_ops": _filter_compiled_ops(compiled_ops_out, source_id=source_id, target_path=target_path),
            "failed_ops": _filter_failed_ops(failed_ops_out, source_id=source_id),
        },
        "findings_emitted_so_far": {
            "cumulative_count": len(tuple(after_master.findings or ())),
            "source_local_count": len(source_findings),
            "cumulative_role_counts": _finding_role_counts(after_master.findings or ()),
            "source_local": source_findings,
            "source_local_projection_rows": source_projection_rows,
        },
        "migration_events": _filter_migration_events(after_master.migration_events or (), source_id=source_id, target_path=target_path),
        "temporal_events": _filter_temporal_events(temporal_events_out, source_id=source_id, target_path=target_path),
        "replay_fold_witness": {
            "snapshot_witness": snapshot_bundle,
            "replay_fold_state": _state_target_witness(after_master.replay_fold_state, target_path),
            "replay_meta": _jsonable(replay_meta_out),
        },
        "materialization_selection_result": {
            "product_debug": product_bundle,
            "materialization_spec": _jsonable(after_master.materialization_spec),
            "materialized_state": _state_target_witness(after_master.state, target_path),
        },
        "oracle_witness_comparison_summary": _comparison_summary(after_master),
    }


def _format_text(bundle: dict[str, Any]) -> str:
    acquisition = bundle["acquisition"]
    payload_surface = bundle["payload_surface"]
    findings = bundle["findings_emitted_so_far"]
    lowered_ops = bundle["lowered_ops"]
    replay_fold = bundle["replay_fold_witness"]
    materialization = bundle["materialization_selection_result"]
    lines = [
        f"Statute      : {bundle['statute_id']}",
        f"Amendment    : {bundle['source_id']}  (#{bundle['amendment_index'] + 1} / {bundle['total_amendments']})",
        f"Mode         : {bundle['mode']}",
        f"Target       : {bundle['target_path'] or '(all)'}",
        f"Source lane  : {acquisition['source_lane_used']}",
        f"Route        : {'apply' if acquisition['route']['should_apply'] else 'skip'} ({acquisition['route']['reason']})",
        f"Clause groups: {bundle['clause_effect_surface']['verb_group_count']}",
        f"Parsed ops   : {bundle['clause_effect_surface']['parsed_op_count']}",
        f"Payload groups: {len(payload_surface['groups'])}",
        f"Lowered ops  : {len(lowered_ops['compiled_ops'])}",
        f"Findings     : {findings['source_local_count']} source-local / {findings['cumulative_count']} cumulative",
        f"Temporal evts: {len(bundle['temporal_events'])}",
        f"Migration evts: {len(bundle['migration_events'])}",
        f"Snapshots    : {replay_fold['snapshot_witness']['matched_lo_ops']}",
        f"Timeline hits: {materialization['product_debug']['timeline_entries_count']}",
    ]
    chosen_text = str(acquisition.get("chosen_operative_text") or "")
    if chosen_text:
        lines.extend(
            [
                "",
                "Chosen operative text:",
                f"  {chosen_text[:220]}{'…' if len(chosen_text) > 220 else ''}",
            ]
        )
    rejected = acquisition.get("rejected_lanes") or []
    if rejected:
        lines.append("")
        lines.append("Rejected lanes:")
        for item in rejected:
            lines.append(f"  {item['lane']}: {item['reason']}")
    return "\n".join(lines)


def main(args: Any) -> None:
    bundle = build_phase_witness_bundle(
        statute_id=args.statute_id,
        source_id=args.source,
        mode=getattr(args, "mode", "legal_pit"),
        target_path=getattr(args, "target", "") or "",
    )
    output_path = str(getattr(args, "output", "") or "").strip()
    if output_path:
        Path(output_path).write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, default=_jsonable) + "\n",
            encoding="utf-8",
        )
    if getattr(args, "json", False) or output_path:
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=_jsonable))
        return
    print(_format_text(bundle))
