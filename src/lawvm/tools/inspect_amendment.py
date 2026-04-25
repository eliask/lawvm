from __future__ import annotations

import json
import re
from typing import Any, Dict, Literal, cast

from lxml import etree

from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.grafter import (
    _FilterCtx,
    _coalesce_same_target_mixed_scope_section_groups,
    compile_amendment_ops,
    _find_muutos_ir,
    _filter_ops_by_constraints,
    _group_ops_by_target,
    _group_shadow_pruning_foreign_scoped_section_targets,
    _group_shadow_pruning_section_targets,
    _normalize_johtolause_verbs,
    _resolve_group_surface_scope,
    _restrict_sec1_fallback_to_parent,
    _tree_title,
    get_corpus,
    get_johtolause,
    get_replay_profile,
    normalize_and_compile_ops,
    replay_xml,
    route_amendment,
    should_use_sec1_fallback_post_routing,
    should_use_sec1_fallback_pre_routing,
)
from lawvm.finland.citation_routing import extract_pending_amendment_target_id
from lawvm.finland.corrigendum import extract_inline_corrections, get_patch_table
from lawvm.finland.helpers import _norm_row_anchor_text
from lawvm.core.elaboration_context import (
    build_payload_elaboration_context,
    snapshot_replay_lookups,
    snapshot_target_context,
    TargetUnitKind,
)
from lawvm.finland.payload_normalize import (
    elaborate_payload_against_live,
    prepare_payload_surface,
)
from lawvm.finland.ops import legacy_target_kind_for_unit_kind, scope_authority_parity_for_op
from lawvm.finland.projection_rows import projection_row_from_finding
from lawvm.tools._section_debug import summarize_node


def _serialize_source_normalization_fact(fact: Any) -> dict:
    return {
        "statute_id": fact.statute_id,
        "kind": fact.kind_value,
        "basis": fact.basis_value,
        "before": fact.before,
        "after": fact.after,
        "explanation": fact.explanation,
        "path": list(fact.path or ()),
        "confidence": float(fact.confidence or 0.0),
    }

def _presented_target_kind_for_unit_kind(target_unit_kind: str) -> str:
    unit_kind = str(target_unit_kind or "").strip()
    if unit_kind == "section":
        return legacy_target_kind_for_unit_kind("section").value
    if unit_kind == "chapter":
        return legacy_target_kind_for_unit_kind("chapter").value
    if unit_kind == "part":
        return legacy_target_kind_for_unit_kind("part").value
    return ""


def _scope_bundle(
    *,
    target_unit_kind: str = "",
    target_norm: str = "",
    target_label: str = "",
    target_chapter: str = "",
    target_part: str = "",
) -> dict[str, str]:
    """Return the inspect-facing target scope bundle.

    This is an internal inspect bundle. Neutral scope stays primary here;
    compat target-kind projection belongs only at presentation time.
    """
    return {
        "target_unit_kind": str(target_unit_kind or ""),
        "target_norm": str(target_norm or ""),
        "target_label": str(target_label or ""),
        "target_chapter": str(target_chapter or ""),
        "target_part": str(target_part or ""),
    }


def _serialize_pathology(pathology: Any) -> dict:
    scope_detail = dict(pathology.scope_detail() or {})
    scope = _scope_bundle(
        target_unit_kind=str(scope_detail.get("target_unit_kind") or pathology.target_unit_kind or ""),
        target_label=str(scope_detail.get("target_label") or pathology.target_label or ""),
    )
    return {
        "code": pathology.code,
        "source_statute": pathology.source_statute,
        "message": pathology.message,
        **scope,
        "detail": dict(pathology.detail or {}),
    }


def _present_scope_for_output(scope: dict[str, Any]) -> dict[str, Any]:
    """Project neutral inspect scope to presentation-only compat codes."""
    presented = dict(scope)
    compat_target_kind = _presented_target_kind_for_unit_kind(str(scope.get("target_unit_kind") or ""))
    if compat_target_kind:
        presented["target_kind"] = compat_target_kind
    return presented


def _present_pathology_for_output(pathology: dict[str, Any]) -> dict[str, Any]:
    return _present_scope_for_output(pathology)


def _present_group_for_output(group: dict[str, Any]) -> dict[str, Any]:
    presented = _present_scope_for_output(group)
    source_pathologies = group.get("source_pathologies")
    if isinstance(source_pathologies, list):
        presented["source_pathologies"] = [
            _present_pathology_for_output(pathology)
            for pathology in source_pathologies
            if isinstance(pathology, dict)
        ]
    return presented


def _present_bundle_for_output(bundle: Dict[str, Any]) -> Dict[str, Any]:
    presented = dict(bundle)
    groups = bundle.get("groups")
    if isinstance(groups, list):
        presented["groups"] = [
            _present_group_for_output(group)
            for group in groups
            if isinstance(group, dict)
        ]
    return presented


def _serialize_observation(observation: Any) -> dict:
    return {
        "kind": observation.kind,
        "stage": observation.stage,
        "detail": dict(observation.detail or {}),
    }


def _serialize_scope_confidence(confidence: Any) -> dict[str, Any] | None:
    if confidence is None:
        return None
    return {
        "tag": str(confidence.tag or ""),
        "source": str(confidence.source or ""),
        "confidence": str(confidence.confidence or ""),
        "resolved_chapter": str(confidence.resolved_chapter or ""),
        "fallback_reason": str(confidence.fallback_reason or ""),
    }


def _serialize_scope_authority_parity(op: Any) -> dict[str, Any]:
    parity = scope_authority_parity_for_op(op)
    return {
        "op": op.description(),
        "matches": bool(parity.matches),
        "mismatch_kind": str(parity.mismatch_kind or ""),
        "runtime": _serialize_scope_confidence(parity.runtime),
        "projection": _serialize_scope_confidence(parity.projection),
    }


def _format_compiled_op_row(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").upper()
    target_unit_kind = str(row.get("target_unit_kind") or "")
    target_norm = str(row.get("target_norm") or row.get("target_label") or "")
    target_chapter = str(row.get("target_chapter") or "")
    target_paragraph_raw = row.get("target_paragraph")
    target_item = str(row.get("target_item") or "")
    target_special = str(row.get("target_special") or "")
    if target_unit_kind == "section":
        if target_chapter:
            description = f"{action} {target_chapter} luku {target_norm} §"
        else:
            description = f"{action} {target_norm} §"
    elif target_unit_kind == "chapter":
        description = f"{action} {target_norm} luku"
    elif target_unit_kind == "part":
        description = f"{action} {target_norm} osa"
    else:
        description = f"{action} {target_norm}".strip()
    if target_special:
        description += f" {target_special}"
    elif target_paragraph_raw not in (None, ""):
        description += f" {target_paragraph_raw} mom"
    if target_item:
        description += f" {target_item} kohta"
    return description.strip()


def _payload_completeness_observation(group: dict[str, Any]) -> dict[str, Any]:
    for observation in group.get("elaboration_observations") or []:
        if not isinstance(observation, dict):
            continue
        if str(observation.get("kind") or "") != "ELAB.PAYLOAD_COMPLETENESS":
            continue
        detail = observation.get("detail") or {}
        if not isinstance(detail, dict):
            continue
        return detail
    return {}


def _unassigned_sparse_slots_observation(group: dict[str, Any]) -> dict[str, Any]:
    for observation in group.get("elaboration_observations") or []:
        if not isinstance(observation, dict):
            continue
        if str(observation.get("kind") or "") != "ELAB.UNASSIGNED_SPARSE_SLOTS":
            continue
        detail = observation.get("detail") or {}
        if not isinstance(detail, dict):
            continue
        return detail
    return {}


def _working_johtolause(
    parent_id: str,
    parent_title: str,
    source_id: str,
    xml_bytes: bytes,
    source_title: str,
) -> tuple[etree._Element, str, bool, bool, str]:
    johto = get_johtolause(xml_bytes)
    citation_guard_johto = _normalize_johtolause_verbs(johto or "")
    citation_guard_sec1 = ""
    muutos_tree = etree.fromstring(xml_bytes)

    used_sec1_fallback = False
    sec1_text = ""
    sec1_el = muutos_tree.find(".//{*}section[@eId='sec_1']")
    if sec1_el is not None:
        sec1_text = etree.tostring(sec1_el, method="text", encoding="unicode").strip()
        sec1_text = re.sub(r"^\d+\s*[a-zäöå]?\s*§\s*", "", sec1_text).strip()
        sec1_text = _restrict_sec1_fallback_to_parent(sec1_text, parent_id)

    if should_use_sec1_fallback_pre_routing(johto):
        if sec1_text:
            johto = sec1_text
            used_sec1_fallback = True
    elif sec1_text:
        citation_guard_sec1 = _normalize_johtolause_verbs(sec1_text)

    johto = _normalize_johtolause_verbs(johto)
    should_apply, route_reason = route_amendment(
        citation_guard_johto=citation_guard_johto,
        citation_guard_sec1=citation_guard_sec1,
        johto=johto,
        parent_id=parent_id,
        amendment_id=source_id,
        source_title=source_title,
        parent_title=parent_title,
    )
    if (
        should_apply
        and sec1_text
        and should_use_sec1_fallback_post_routing(johto, _normalize_johtolause_verbs(sec1_text))
    ):
        johto = _normalize_johtolause_verbs(sec1_text)
        used_sec1_fallback = True
    return muutos_tree, johto, used_sec1_fallback, should_apply, route_reason


def build_amendment_bundle(
    statute_id: str,
    source_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
) -> Dict[str, Any]:
    before_master = replay_xml(statute_id, mode=mode, stop_before=source_id, quiet=True)
    cs = get_corpus()
    xml_bytes = cs.read_source(source_id)
    if xml_bytes is None:
        raise SystemExit(f"amendment not found in corpus: {source_id}")
    _, xml_bytes = extract_inline_corrections(xml_bytes, source_id)
    xml_bytes, _ = get_patch_table().patch_source_xml(xml_bytes, source_id)
    xml_bytes, _ = get_patch_table().patch_source_body_xml(xml_bytes, source_id)

    muutos_tree = etree.fromstring(xml_bytes)
    source_title = _tree_title(muutos_tree)
    muutos_tree, johto, used_sec1_fallback, should_apply, route_reason = _working_johtolause(
        statute_id,
        before_master.title,
        source_id,
        xml_bytes,
        source_title,
    )

    route_target_amendment_id = ""
    if route_reason == "pending_amendment_of_parent_skip":
        route_target_amendment_id = (
            extract_pending_amendment_target_id(
                get_johtolause(xml_bytes),
                source_id,
                source_title,
                before_master.title,
            )
            or ""
        )

    bundle: Dict[str, Any] = {
        "statute_id": statute_id,
        "source_id": source_id,
        "mode": mode,
        "source_title": source_title,
        "johtolause": johto,
        "used_sec1_fallback": used_sec1_fallback,
        "route": {
            "should_apply": should_apply,
            "reason": route_reason,
            "target_amendment_id": route_target_amendment_id,
        },
        "source_payload": {},
        "compile_projection_rows": [],
        "compiled_ops": [],
        "groups": [],
    }
    raw_source_ir = fi_xml_to_ir_node(muutos_tree)
    normalized_source_ir, source_normalization_facts = normalize_source_ir(raw_source_ir, source_id)
    bundle["source_payload"] = {
        "raw_ir": summarize_node(raw_source_ir),
        "normalized_ir": summarize_node(normalized_source_ir),
        "source_normalization_facts": [
            _serialize_source_normalization_fact(fact) for fact in source_normalization_facts
        ],
    }
    if not should_apply:
        return bundle

    _naco_result = normalize_and_compile_ops(
        johto,
        muutos_tree,
        before_master.replay_fold_state,
        source_id,
        source_title=source_title,
        used_sec1_fallback=used_sec1_fallback,
        parent_id=statute_id,
        strict_profile=None,
    )
    ops = _naco_result.output
    bundle["compile_projection_rows"] = [
        dict(projection_row_from_finding(finding))
        for finding in _naco_result.findings()
        if finding.role == "obligation"
    ]
    _compiled_rows: list[dict[str, Any]] = []
    _resolved_compile_result = compile_amendment_ops(
        before_master.replay_fold_state,
        ops,
        muutos_tree,
        johto,
        mode,
        compiled_ops_out=_compiled_rows,
        strict_profile=None,
        source_ref=source_id,
        source_title=source_title,
        target_statute=statute_id,
    )
    _compiled_group_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in _compiled_rows:
        _compiled_group_rows.setdefault(
            (
                str(row.get("target_unit_kind") or ""),
                str(row.get("target_norm") or ""),
                str(row.get("target_chapter") or ""),
                str(row.get("target_part") or ""),
            ),
            [],
        ).append(row)
    bundle["compiled_ops"] = [_format_compiled_op_row(row) for row in _compiled_rows]

    section_groups = _coalesce_same_target_mixed_scope_section_groups(
        _group_ops_by_target(ops),
        master=before_master.replay_fold_state,
        muutos_tree=muutos_tree,
    )
    for (target_unit_kind, target_norm, target_chapter, target_part), group_ops in section_groups.items():
        target_unit_kind_value = cast(TargetUnitKind, target_unit_kind.value)
        surface_target_chapter, surface_target_part = _resolve_group_surface_scope(
            muutos_tree=muutos_tree,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
        )
        raw_muutos_ir, cross_ir = _find_muutos_ir(
            muutos_tree,
            target_unit_kind_value,
            target_norm,
            surface_target_chapter,
            surface_target_part,
        )
        master = before_master.replay_fold_state
        lookups = snapshot_replay_lookups(master)
        target_ctx = snapshot_target_context(
            master,
            target_unit_kind_value,
            target_norm,
            target_chapter,
            lookups,
            target_part=target_part,
        )
        payload_ctx = build_payload_elaboration_context(
            target_ctx,
            lookups,
            row_anchor_normalizer=_norm_row_anchor_text,
        )
        standalone_section_targets = _group_shadow_pruning_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_part=target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_standalone_section_targets = _group_shadow_pruning_foreign_scoped_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_part=target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        prepared = prepare_payload_surface(
            payload_ctx,
            group_ops,
            raw_muutos_ir,
            profile=get_replay_profile(mode),
            strict_profile=None,
        )
        fctx = _FilterCtx(muutos_ir=prepared, muutos_tree=muutos_tree, johto=johto)
        rejected_pre = []
        filtered_pre = _filter_ops_by_constraints(group_ops, fctx, rejected_ops_out=rejected_pre)
        payload_norm = elaborate_payload_against_live(
            payload_ctx,
            filtered_pre,
            prepared,
            standalone_section_targets,
            foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
        )
        slot_assignment = payload_norm.slot_assignment
        fctx.slot_assignment = slot_assignment
        rejected_post = []
        filtered_post = _filter_ops_by_constraints(
            list(payload_norm.group_ops),
            fctx,
            rejected_ops_out=rejected_post,
        )
        _final_op_descriptions = [
            _format_compiled_op_row(row)
            for row in _compiled_group_rows.get(
                (
                    target_unit_kind_value,
                    target_norm,
                    str(target_chapter or ""),
                    str(target_part or ""),
                ),
                [],
            )
        ] or [op.description() for op in filtered_post]
        group_bundle = {
            **_scope_bundle(
                target_unit_kind=target_unit_kind_value,
                target_norm=target_norm,
                target_chapter=target_chapter or "",
                target_part=target_part or "",
            ),
            "cross_heading": summarize_node(cross_ir),
            "raw_payload": summarize_node(raw_muutos_ir),
            "prepared_payload": summarize_node(prepared),
            "normalized_payload": summarize_node(payload_norm.muutos_ir),
            "ops_raw": [op.description() for op in group_ops],
            "ops_after_constraints": [op.description() for op in filtered_pre],
            "rejected_ops_pre_constraints": [failed.as_detail() for failed in rejected_pre],
            "ops_after_normalization": [op.description() for op in payload_norm.group_ops],
            "ops_final": _final_op_descriptions,
            "scope_authority_parity": [
                _serialize_scope_authority_parity(op) for op in filtered_post
            ],
            "rejected_ops_post_constraints": [failed.as_detail() for failed in rejected_post],
            "subsection_map": [
                {
                    # Slot assignment is keyed to the elaborated group-op order,
                    # while compiled rows are emitted in apply order. Do not zip
                    # the two by index or the debug view attributes payloads to
                    # the wrong operation.
                    "op": op.description(),
                    "mapped_payload": summarize_node(slot_assignment.resolve_for_op(op)),
                }
                for op in payload_norm.group_ops
            ]
            if slot_assignment is not None
            else [],
            "sparse_slot_bindings": [
                {
                    "op": binding.op_description,
                    "slot_index": binding.payload_slot_index,
                    "slot_label": binding.payload_slot_label,
                    "target_paragraph": binding.target_paragraph,
                    "target_item": binding.target_item or "",
                    "target_special": binding.target_special or "",
                }
                for binding in slot_assignment.sparse_slot_bindings
            ]
            if slot_assignment is not None
            else [],
            "source_pathologies": [
                _serialize_pathology(pathology) for pathology in (payload_norm.source_pathologies or [])
            ],
            "elaboration_observations": [
                _serialize_observation(observation) for observation in (payload_norm.elaboration_observations or [])
            ],
        }
        bundle["groups"].append(group_bundle)

    return bundle


def _format_text(bundle: Dict[str, Any]) -> str:
    stage = str(bundle.get("_stage", "all") or "all")
    show_source_normalization_facts = bool(bundle.get("_show_source_normalization_facts", False))
    lines = [
        f"Statute      : {bundle['statute_id']}",
        f"Amendment    : {bundle['source_id']}",
        f"Mode         : {bundle['mode']}",
        f"Title        : {bundle.get('source_title', '')}",
        f"Route        : {'apply' if bundle['route']['should_apply'] else 'skip'} ({bundle['route']['reason']})",
        f"Sec1 fallback: {'yes' if bundle.get('used_sec1_fallback') else 'no'}",
        "",
    ]
    route_target_amendment_id = str(bundle.get("route", {}).get("target_amendment_id") or "")
    if route_target_amendment_id:
        lines.insert(5, f"Route target : {route_target_amendment_id}")
    source_payload = bundle.get("source_payload") or {}
    if stage in {"all", "source"}:
        lines.extend(
            [
                "Johtolause:",
                *(f"  {line}" for line in (bundle.get("johtolause") or "").splitlines()),
                "",
                "Source payload:",
                f"  Raw IR        : {source_payload.get('raw_ir', {})}",
                f"  Normalized IR : {source_payload.get('normalized_ir', {})}",
            ]
        )
        if show_source_normalization_facts and source_payload.get("source_normalization_facts"):
            lines.append("  Source normalization facts:")
            for fact in source_payload["source_normalization_facts"]:
                path = "/".join(fact.get("path") or [])
                lines.append(
                    f"    {fact['kind']} {fact['basis']} path={path or '(root)'} confidence={fact['confidence']:.2f}"
                )
                if fact.get("before"):
                    lines.append(f"      before: {fact['before']}")
                if fact.get("after"):
                    lines.append(f"      after : {fact['after']}")
                if fact.get("explanation"):
                    lines.append(f"      why   : {fact['explanation']}")
        lines.append("")
    if stage in {"all", "compile"}:
        if bundle.get("compile_projection_rows"):
            lines.append("Compile projection rows:")
            for adj in bundle["compile_projection_rows"]:
                lines.append(f"  {adj['kind']}: {adj['message']}")
            lines.append("")
        lines.append(f"Compiled ops ({len(bundle.get('compiled_ops', []))}):")
        for op in bundle.get("compiled_ops", []):
            lines.append(f"  {op}")
    for idx, group in enumerate(bundle.get("groups", []), start=1):
        if stage not in {"all", "groups"}:
            continue
        scope = f" / chapter {group['target_chapter']}" if group.get("target_chapter") else ""
        target_kind_label = (
            _presented_target_kind_for_unit_kind(str(group.get("target_unit_kind") or ""))
            or "?"
        )
        lines.extend(
            [
                "",
                f"Group {idx}: {target_kind_label} {group['target_norm']}{scope}",
                f"  Raw payload       : {group['raw_payload']}",
                f"  Prepared payload  : {group['prepared_payload']}",
                f"  Normalized payload: {group['normalized_payload']}",
                "  Final ops:",
            ]
        )
        for op in group.get("ops_final", []):
            lines.append(f"    {op}")
        if group.get("subsection_map"):
            lines.append("  Subsection map:")
            for entry in group["subsection_map"]:
                lines.append(f"    {entry['op']} -> {entry['mapped_payload']}")
        if group.get("sparse_slot_bindings"):
            lines.append("  Sparse slot bindings:")
            for entry in group["sparse_slot_bindings"]:
                lines.append(f"    {entry['op']} -> slot {entry['slot_index']}:{entry['slot_label'] or '(unlabeled)'}")
        sparse_slot_bindings = group.get("sparse_slot_bindings") or []
        unassigned_sparse_slots = _unassigned_sparse_slots_observation(group)
        unassigned_sparse_payload_slots = [
            str(slot)
            for slot in (unassigned_sparse_slots.get("unassigned_slots") or [])
            if str(slot)
        ]
        if sparse_slot_bindings or unassigned_sparse_payload_slots:
            lines.append(
                "  Slot assignment summary: "
                f"bindings={len(sparse_slot_bindings)} "
                f"leftovers={len(unassigned_sparse_payload_slots)}"
            )
        if unassigned_sparse_payload_slots:
            lines.append("  Unassigned sparse payload slots: " + ", ".join(unassigned_sparse_payload_slots))
        payload_completeness = _payload_completeness_observation(group)
        if payload_completeness:
            payload_kind = str(
                payload_completeness.get("payload_completeness_kind")
                or payload_completeness.get("kind")
                or ""
            )
            lines.append(
                "  Payload completeness: "
                f"{payload_kind} "
                f"tail_policy={payload_completeness.get('tail_policy', '')}"
            )
            reasons = payload_completeness.get("reasons") or []
            if reasons:
                lines.append("    Reasons: " + ", ".join(str(reason) for reason in reasons))
            detail = payload_completeness.get("detail") or {}
            if detail:
                lines.append("    Detail: " + json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str))
        if group.get("source_pathologies"):
            lines.append("  Source pathologies:")
            for pathology in group["source_pathologies"]:
                lines.append(f"    {pathology['code']} {pathology['source_statute']} {pathology['target_label']}")
        if group.get("elaboration_observations"):
            lines.append("  Elaboration observations:")
            for observation in group["elaboration_observations"]:
                detail = observation.get("detail") or {}
                detail_text = ""
                if detail:
                    detail_text = " " + json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
                lines.append(f"    {observation.get('stage', '')}:{observation.get('kind', '')}{detail_text}")
    return "\n".join(lines)


def main(args) -> None:
    bundle = build_amendment_bundle(
        statute_id=args.statute_id,
        source_id=args.source,
        mode=getattr(args, "mode", "legal_pit"),
    )
    bundle["_stage"] = getattr(args, "stage", "all")
    bundle["_show_source_normalization_facts"] = bool(
        getattr(args, "show_source_normalization_facts", False)
    )
    if getattr(args, "json", False):
        print(json.dumps(_present_bundle_for_output(bundle), ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle))
