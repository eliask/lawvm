"""Uncovered ``kumotaan`` repeal recovery for Finland replay."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Set

from lawvm.core import tree_ops as _tops
from lawvm.core.compile_result import SourcePathology
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.ir import LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.apply_ir_ops import _build_repeal_placeholder_ir
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.kumotaan import (
    _extract_kumotaan_chapter_section_map,
    _extract_kumotaan_container_refs,
)
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.source_pathology import build_same_effective_container_repeal_shadowed_pathology
from lawvm.finland.uncovered_recovery_findings import KumotaanRecoveryFindingEmitter

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState, StatuteContext

FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID = "fi.recovery.uncovered_kumotaan"


@dataclass(frozen=True, slots=True)
class CoveredContainerKey:
    """Container already owned by a parsed repeal operation."""

    target_unit_kind: str
    label: str


@dataclass(frozen=True, slots=True)
class KumotaanRecoveryRequest:
    """Semantic inputs for uncovered ``kumotaan`` recovery."""

    state: "ReplayState"
    ctx: "StatuteContext"
    ops: List[AmendmentOp]
    johto: str
    amendment_id: str
    op_source: Optional[OperationSource] = None


@dataclass(frozen=True, slots=True)
class KumotaanRecoverySinks:
    """Mutable evidence/output channels for uncovered ``kumotaan`` recovery."""

    lo_ops_out: Optional[List[_LegalOperation]] = None
    findings_out: Optional[List[Finding]] = None
    source_pathologies_out: Optional[List[SourcePathology]] = None


@dataclass(frozen=True, slots=True)
class KumotaanRecoveryResult:
    """Result of uncovered ``kumotaan`` recovery."""

    state: "ReplayState"


def _same_amendment_non_repeal_section_labels(
    *,
    lo_ops_out: Optional[List[_LegalOperation]],
    amendment_id: str,
) -> Set[str]:
    labels: Set[str] = set()
    if lo_ops_out is None:
        return labels
    for lo in lo_ops_out:
        if lo.source is None or lo.source.statute_id != amendment_id:
            continue
        if lo.action is StructuralAction.REPEAL:
            continue
        if not lo.target.path or lo.target.path[-1][0] != "section":
            continue
        labels.add(_norm_num_token(lo.target.path[-1][1]))
    return labels


def _prior_same_effective_container_replacement(
    *,
    lo_ops_out: Optional[List[_LegalOperation]],
    op_source: Optional[OperationSource],
    amendment_id: str,
    target_path: tuple[tuple[str, str], ...],
) -> _LegalOperation | None:
    if lo_ops_out is None or op_source is None or not op_source.effective:
        return None
    for prior in reversed(lo_ops_out):
        if prior.action not in (StructuralAction.INSERT, StructuralAction.REPLACE):
            continue
        if prior.target is None or tuple(prior.target.path) != target_path:
            continue
        prior_source = prior.source
        if prior_source is None:
            continue
        if prior_source.effective != op_source.effective:
            continue
        if prior_source.statute_id == amendment_id:
            continue
        return prior
    return None


def _apply_uncovered_kumotaan_typed(
    request: KumotaanRecoveryRequest,
    sinks: Optional[KumotaanRecoverySinks] = None,
) -> KumotaanRecoveryResult:
    """Apply uncovered repeals from kumotaan clauses."""
    sinks = sinks or KumotaanRecoverySinks()
    state = request.state
    ctx = request.ctx
    ops = request.ops
    johto = request.johto
    amendment_id = request.amendment_id
    lo_ops_out = sinks.lo_ops_out
    op_source = request.op_source
    findings_out = sinks.findings_out
    source_pathologies_out = sinks.source_pathologies_out

    vts_section_refs = [
        _norm_num_token(op.target_section)
        for op in ops
        if (
            op.voimaantulo_repeal
            and op.target_unit_kind == "section"
            and op.target_section
            and not op.target_paragraph
            and not op.target_item
            and not op.target_special
        )
    ]
    vts_granular_section_refs = {
        _norm_num_token(op.target_section)
        for op in ops
        if (
            op.voimaantulo_repeal
            and op.target_unit_kind == "section"
            and op.target_section
            and (op.target_paragraph or op.target_item or op.target_special)
        )
    }
    vts_container_refs: dict[TargetUnitKind, list[str]] = {"chapter": [], "part": []}
    for op in ops:
        if not op.voimaantulo_repeal or not op.target_section:
            continue
        if op.target_unit_kind in {"chapter", "part"}:
            vts_container_refs[op.target_unit_kind].append(_norm_num_token(op.target_section))

    if not johto or "kumotaan" not in johto.lower():
        if not vts_section_refs and not vts_container_refs["chapter"] and not vts_container_refs["part"]:
            return KumotaanRecoveryResult(state=state)

    has_peg_repeals = any(op.op_type == OpType.REPEAL for op in ops)
    has_vts_repeals = bool(vts_section_refs or vts_container_refs["chapter"] or vts_container_refs["part"])
    # lawvm-regex: prefilter cheap presence GATE (does this johto mention kumotaan?); actual targets come from typed _extract_kumotaan_* maps + VTS ops, every injected op witnessed
    if not has_peg_repeals and not has_vts_repeals and not re.search(r"\bkumotaan\b", johto, re.IGNORECASE):
        return KumotaanRecoveryResult(state=state)

    covered_labels: Set[str] = set()
    covered_containers: Set[CoveredContainerKey] = set()
    for op in ops:
        if op.voimaantulo_repeal:
            continue
        if op.target_unit_kind == "section" and op.target_section:
            if op.target_special in {"otsikko", "otsikko_edella"}:
                continue
            covered_labels.add(_norm_num_token(op.target_section))
        elif op.target_unit_kind in {"chapter", "part"} and op.target_section:
            covered_containers.add(
                CoveredContainerKey(
                    target_unit_kind=op.target_unit_kind,
                    label=_norm_num_token(op.target_section),
                )
            )
    covered_labels |= _same_amendment_non_repeal_section_labels(
        lo_ops_out=lo_ops_out,
        amendment_id=amendment_id,
    )

    kumotaan_chap_map = _extract_kumotaan_chapter_section_map(johto)
    kumotaan_section_targets: List[tuple[Optional[str], str]] = []
    seen_section_targets: Set[tuple[Optional[str], str]] = set()
    for chapter_label, labels in kumotaan_chap_map.items():
        for label in labels:
            target = (chapter_label, label)
            if label and target not in seen_section_targets:
                kumotaan_section_targets.append(target)
                seen_section_targets.add(target)
    for label in vts_section_refs:
        target = (None, label)
        if label and target not in seen_section_targets:
            kumotaan_section_targets.append(target)
            seen_section_targets.add(target)
    kumotaan_containers = _extract_kumotaan_container_refs(johto)
    for kind_name, labels in vts_container_refs.items():
        if labels:
            kumotaan_containers.setdefault(kind_name, [])
            for label in labels:
                if label and label not in kumotaan_containers[kind_name]:
                    kumotaan_containers[kind_name].append(label)

    repealed: List[str] = []
    finding_emitter = KumotaanRecoveryFindingEmitter(
        amendment_id=amendment_id,
        findings_out=findings_out,
    )

    for chapter_label, ref in kumotaan_section_targets:
        label = _norm_num_token(ref)
        if not label:
            finding_emitter.append_skip(target_norm=str(ref), reason="kumotaan_empty_section_ref")
            continue
        if label in covered_labels:
            finding_emitter.append_skip(target_norm=label, reason="kumotaan_section_already_covered")
            continue
        if label in vts_granular_section_refs and label not in vts_section_refs:
            finding_emitter.append_skip(target_norm=label, reason="kumotaan_granular_vts_repeal")
            continue
        covered_labels.add(label)

        sec_path = state.find_section_path(label, chapter_label)
        if sec_path is None:
            finding_emitter.append_skip(target_norm=label, reason="kumotaan_missing_section_target")
            continue

        sec_node = _tops.resolve(state.ir, sec_path)
        assert sec_node is not None, f"resolve failed for {sec_path}"
        base_path = _tops.find(
            ctx.base_ir,
            "section",
            label,
            scope_kind=IRNodeKind.CHAPTER.value if chapter_label else None,
            scope_label=chapter_label,
        )
        base_sec = _tops.resolve(ctx.base_ir, base_path) if base_path is not None else None
        if base_sec is not None:
            issue = None
            if op_source and op_source.enacted:
                try:
                    issue = dt.date.fromisoformat(op_source.enacted)
                except ValueError:
                    pass
            title = op_source.title if op_source else ""
            placeholder = _build_repeal_placeholder_ir(sec_node, label, amendment_id, issue, title)
            state = state.with_ir(
                _tops.replace_at(state.ir, sec_path, placeholder),
                preserve_provision_index=True,
            )
            repealed.append(label)
            op_payload = placeholder
            op_action = StructuralAction.REPLACE
        else:
            state = state.with_ir(_tops.remove_at(state.ir, sec_path))
            repealed.append(f"{label} (drop)")
            op_payload = None
            op_action = StructuralAction.REPEAL

        op_id = f"uncovered_repeal_{label}"
        if lo_ops_out is not None:
            tl_path = tuple((k, v) for k, v in sec_path if v)
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=op_action,
                    target=LegalAddress(path=tl_path),
                    payload=op_payload,
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id}",
                    witness_rule_id=FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID,
                )
            )
        finding_emitter.append(
            op_id=op_id,
            target_unit_kind="section",
            target_norm=label,
        )

    repealed_containers: List[str] = []

    for target_unit_kind, refs in kumotaan_containers.items():
        kind_name = "luku" if target_unit_kind == "chapter" else "osa"
        node_kind = "chapter" if target_unit_kind == "chapter" else "part"
        for ref in refs:
            label = _norm_num_token(ref)
            if not label:
                finding_emitter.append_skip(
                    target_norm=str(ref),
                    reason=f"kumotaan_empty_{target_unit_kind}_ref",
                )
                continue
            existing_path = state.find(node_kind, label)
            covered_key = CoveredContainerKey(target_unit_kind=target_unit_kind, label=label)
            if covered_key in covered_containers and existing_path is None:
                finding_emitter.append_skip(
                    target_norm=label,
                    reason=f"kumotaan_{target_unit_kind}_covered_absent",
                )
                continue
            covered_containers.add(covered_key)

            if existing_path is None:
                finding_emitter.append_skip(
                    target_norm=label,
                    reason=f"kumotaan_missing_{target_unit_kind}_target",
                )
                continue

            tl_path = tuple((k, v) for k, v in existing_path if v)
            shadow = _prior_same_effective_container_replacement(
                lo_ops_out=lo_ops_out,
                op_source=op_source,
                amendment_id=amendment_id,
                target_path=tl_path,
            )
            if shadow is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_same_effective_container_repeal_shadowed_pathology(
                            source_statute=amendment_id,
                            target_unit_kind=target_unit_kind,
                            target_label=f"{label} {kind_name}",
                            prior_source_statute=shadow.source.statute_id if shadow.source else "",
                            effective=op_source.effective if op_source is not None else "",
                        )
                    )
                continue

            state = state.with_ir(_tops.remove_at(state.ir, existing_path))
            repealed_containers.append(f"{label} {kind_name}")

            op_id = f"uncovered_repeal_{target_unit_kind}_{label}"
            if lo_ops_out is not None:
                lo_ops_out.append(
                    _LegalOperation(
                        op_id=op_id,
                        sequence=0,
                        action=StructuralAction.REPEAL,
                        target=LegalAddress(path=tl_path),
                        payload=None,
                        source=op_source,
                        group_id=f"finland-johto:{amendment_id}",
                        witness_rule_id=FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID,
                    )
                )
            finding_emitter.append(
                op_id=op_id,
                target_unit_kind=target_unit_kind,
                target_norm=label,
            )

    if repealed:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan: {repealed}")
    if repealed_containers:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan containers: {repealed_containers}")
    return KumotaanRecoveryResult(state=state)
