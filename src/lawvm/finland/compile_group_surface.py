"""Stage 1 payload-surface extraction for Finland compile groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.payload_surface import GroupSurface, build_group_surface as _build_group_surface_factory
from lawvm.core.phase_result import Finding, PhaseBuilder, PhaseResult
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _is_omission_ir, _norm_num_token
from lawvm.finland.op_provenance import Recovered
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.sparse_tail_claims import (
    SPARSE_OMISSION_TAIL_CLAIM_RULE,
    SparseOmissionTailClaim,
    sparse_tail_claim_for_target,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.source_pathology import (
    build_body_section_label_mismatch_payload_pathology,
    build_recodification_omission_only_section_shell_pathology,
)


def _renumber_destination_section_label(group_ops: list[AmendmentOp]) -> Optional[str]:
    labels = {
        dest_path["section"]
        for op in group_ops
        if op.op_type == OpType.RENUMBER
        and op.lo is not None
        and op.lo.destination is not None
        and (dest_path := dict(op.lo.destination.path)).get("section")
    }
    if len(labels) != 1:
        return None
    return next(iter(labels))


def _is_sparse_source_shell(node: IRNode | None) -> bool:
    if node is None or node.kind is not IRNodeKind.SECTION:
        return False
    has_omission = any(_is_omission_ir(child) for child in node.children)
    has_substantive_child = any(
        child.kind
        not in {
            IRNodeKind.NUM,
            IRNodeKind.HEADING,
            IRNodeKind.OMISSION,
        }
        and bool(irnode_to_text(child).strip())
        for child in node.children
    )
    return has_omission and not has_substantive_child


def collect_recodification_omission_only_section_shell_pathologies(
    *,
    group_ops: list[AmendmentOp],
    target_unit_kind: str,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    source_model: AmendmentSourceModel,
) -> tuple[SourcePathology, ...]:
    """Record source limits for renumber-only destination omission shells."""
    if target_unit_kind != "section":
        return ()
    source_statute = next(
        (str(op.source_statute or "") for op in group_ops if op.source_statute),
        "",
    )
    if not source_statute:
        return ()

    destination_section = _renumber_destination_section_label(group_ops)
    if destination_section is None:
        return ()
    has_same_group_relabel = any(op.op_type == OpType.RENUMBER for op in group_ops)
    if not has_same_group_relabel:
        return ()
    has_destination_payload_op = any(
        op.op_type != OpType.RENUMBER
        and op.target_cols.target_unit_kind == "section"
        and _norm_num_token(op.target_cols.target_section or "") == _norm_num_token(destination_section)
        for op in group_ops
    )
    if has_destination_payload_op:
        return ()

    destination_payload = source_model.lookup_payload_ir(
        target_unit_kind,
        destination_section,
        None,
        None,
    )
    if not _is_sparse_source_shell(destination_payload.payload_ir):
        return ()

    return (
        build_recodification_omission_only_section_shell_pathology(
            source_statute=source_statute,
            source_target_norm=target_norm,
            destination_target_norm=destination_section,
            target_chapter=target_chapter or "",
            target_part=target_part or "",
        ),
    )


def _is_explicit_whole_section_replace_group(group_ops: list[AmendmentOp]) -> bool:
    if len(group_ops) != 1:
        return False
    op = group_ops[0]
    return (
        op.op_type == OpType.REPLACE
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_paragraph is None
        and not op.target_cols.target_item
        and not op.target_cols.target_special
        and not isinstance(op.provenance, Recovered)
        and not op.target_guessing_provenance_tags
        and bool(op.witness_rule_id)
    )


def _single_conflicting_section_payload(
    *,
    source_model: AmendmentSourceModel,
    amendment_group_ops: tuple[AmendmentOp, ...],
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
) -> tuple[str, str, IRNode, IRNode | None] | None:
    target_label = _norm_num_token(target_norm)
    units = tuple(
        unit
        for unit in source_model.observed_body_inventory()
        if unit.kind == "section"
    )
    if len(units) != 1:
        return None
    unit = units[0]
    observed_label = _norm_num_token(unit.label)
    if not observed_label or observed_label == target_label:
        return None
    if any(
        _norm_num_token(op.target_cols.target_section or "") == observed_label
        for op in amendment_group_ops
        if op.target_cols.target_section
    ):
        return None
    unit_chapter = _norm_num_token(unit.chapter_label) if unit.chapter_label else None
    unit_part = _norm_num_token(unit.part_label) if unit.part_label else None
    if target_chapter and unit_chapter and unit_chapter != target_chapter:
        return None
    if target_part and unit_part and unit_part != target_part:
        return None

    observed_payload = source_model.lookup_payload_ir(
        "section",
        observed_label,
        unit_chapter,
        unit_part,
    )
    if observed_payload.status != "unique" or observed_payload.payload_ir is None:
        return None
    if not irnode_to_text(observed_payload.payload_ir).strip():
        return None
    return observed_label, unit.unit_id, observed_payload.payload_ir, observed_payload.cross_heading_ir


def _relabel_section_payload_root(payload: IRNode, target_norm: str) -> IRNode:
    """Return payload with its root section label aligned to the explicit target."""
    if payload.kind is not IRNodeKind.SECTION:
        return payload
    target_label = _norm_num_token(target_norm)
    children: list[IRNode] = []
    for child in payload.children:
        if child.kind is IRNodeKind.NUM:
            children.append(
                IRNode(
                    kind=child.kind,
                    label=target_label,
                    text=f"{target_label} §",
                    attrs=child.attrs,
                    children=child.children,
                )
            )
            continue
        children.append(child)
    return IRNode(
        kind=payload.kind,
        label=target_label,
        text=payload.text,
        attrs=payload.attrs,
        children=tuple(children),
    )


def _is_pure_section_renumber_group(group_ops: list[AmendmentOp]) -> bool:
    return bool(group_ops) and all(
        op.op_type == OpType.RENUMBER and op.target_cols.target_unit_kind == "section"
        for op in group_ops
    )


@dataclass(frozen=True, slots=True)
class BuildGroupSurfaceRequest:
    """Typed inputs for compile-group payload surface extraction."""

    group_ops: list[AmendmentOp]
    target_unit_kind: str
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    source_model: AmendmentSourceModel
    sparse_omission_tail_claims: tuple[SparseOmissionTailClaim, ...] = ()
    amendment_group_ops: tuple[AmendmentOp, ...] = ()


def build_group_surface(request: BuildGroupSurfaceRequest) -> PhaseResult[GroupSurface]:
    """Stage 1: extract amendment-body payload. Pure of live state.

    Returns a PhaseResult where:
    - ``output``       — ``GroupSurface`` (muutos_ir + cross_ir + source info)
    - ``observations`` — malformed payload shape (missing muutos_ir despite
                         non-trivial ops)
    - ``obligations``  — none produced at this stage
    """
    group_ops = request.group_ops
    target_unit_kind = request.target_unit_kind
    target_norm = request.target_norm
    target_chapter = request.target_chapter
    target_part = request.target_part

    source_statute = next(
        (str(op.source_statute or "") for op in group_ops if op.source_statute),
        "",
    )
    surface_findings: list[Finding] = []

    source_payload = request.source_model.lookup_payload_ir(
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
    )
    muutos_ir = source_payload.payload_ir
    cross_ir = source_payload.cross_heading_ir
    if target_unit_kind == "section" and _is_pure_section_renumber_group(group_ops):
        destination_section = _renumber_destination_section_label(group_ops)
        if destination_section and _norm_num_token(destination_section) != target_norm:
            destination_payload = request.source_model.lookup_payload_ir(
                target_unit_kind,
                destination_section,
                None,
                None,
            )
            destination_ir = destination_payload.payload_ir
            if destination_ir is not None and not _is_sparse_source_shell(destination_ir):
                muutos_ir, cross_ir = destination_ir, destination_payload.cross_heading_ir
                surface_findings.append(
                    Finding(
                        kind="ELAB.RENUMBER_DESTINATION_PAYLOAD_SURFACE",
                        role="observation",
                        stage="_build_group_surface",
                        detail={
                            "kind": "ELAB.RENUMBER_DESTINATION_PAYLOAD_SURFACE",
                            "message": (
                                "Pure renumber payload surface selected from the destination "
                                "section rather than the source label."
                            ),
                            "target_unit_kind": target_unit_kind,
                            "source_target_norm": target_norm,
                            "destination_target_norm": destination_section,
                            "target_chapter": target_chapter or "",
                            "target_part": target_part or "",
                        },
                        source_statute=source_statute,
                        blocking=False,
                    )
                )
            elif muutos_ir is not None:
                muutos_ir = None
                cross_ir = None
                surface_findings.append(
                    Finding(
                        kind="ELAB.RENUMBER_SOURCE_LABEL_PAYLOAD_NOT_CLAIMED",
                        role="observation",
                        stage="_build_group_surface",
                        detail={
                            "kind": "ELAB.RENUMBER_SOURCE_LABEL_PAYLOAD_NOT_CLAIMED",
                            "message": (
                                "Pure renumber left same-label source-body payload unclaimed "
                                "because a relabel operation can only claim destination-label text."
                            ),
                            "target_unit_kind": target_unit_kind,
                            "source_target_norm": target_norm,
                            "destination_target_norm": destination_section,
                            "target_chapter": target_chapter or "",
                            "target_part": target_part or "",
                        },
                        source_statute=source_statute,
                        blocking=False,
                    )
                )
    if (
        muutos_ir is None
        and target_unit_kind == "section"
        and _is_explicit_whole_section_replace_group(group_ops)
    ):
        conflicting_payload = _single_conflicting_section_payload(
            source_model=request.source_model,
            amendment_group_ops=request.amendment_group_ops,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        if conflicting_payload is not None:
            observed_label, source_unit_id, muutos_ir, cross_ir = conflicting_payload
            muutos_ir = _relabel_section_payload_root(muutos_ir, target_norm)
            pathology = build_body_section_label_mismatch_payload_pathology(
                source_statute=source_statute,
                target_unit_kind=cast(TargetUnitKind, target_unit_kind),
                target_section=target_norm,
                target_chapter=target_chapter or "",
                observed_section=observed_label,
                source_unit_id=source_unit_id,
            )
            surface_findings.append(
                Finding(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="_build_group_surface",
                    detail=pathology.as_detail(),
                    source_statute=source_statute,
                    blocking=False,
                )
            )
    sparse_tail_claim = sparse_tail_claim_for_target(
        request.sparse_omission_tail_claims,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    if muutos_ir is None and target_unit_kind == "section" and sparse_tail_claim is not None:
        muutos_ir = sparse_tail_claim.payload_section_ir()
        cross_ir = None
        surface_findings.append(
            Finding(
                kind=SPARSE_OMISSION_TAIL_CLAIM_RULE,
                role="observation",
                stage="_build_group_surface",
                detail={
                    "message": (
                        "Explicit descendant target uses the unique post-omission "
                        "subsection payload carried by another claimed source section."
                    ),
                    **sparse_tail_claim.detail(),
                },
                source_statute=source_statute,
                blocking=False,
            )
        )
    if target_unit_kind == "section":
        destination_section = _renumber_destination_section_label(group_ops)
        has_same_group_relabel = any(op.op_type == OpType.RENUMBER for op in group_ops)
        has_followup_payload_op = any(
            op.op_type != OpType.RENUMBER
            and op.target_cols.target_unit_kind == "section"
            and not (
                has_same_group_relabel
                and op.op_type == OpType.REPLACE
                and _norm_num_token(op.target_cols.target_section or "") == target_norm
                and op.target_cols.target_paragraph is None
                and not op.target_cols.target_item
                and not op.target_cols.target_special
                and op.target_cols.target_chapter == target_chapter
                and op.target_cols.target_part == target_part
            )
            for op in group_ops
        )
        source_shell = _is_sparse_source_shell(muutos_ir)
        source_surface = "missing" if muutos_ir is None else "sparse_omission_shell"
        if destination_section is not None and has_followup_payload_op and (muutos_ir is None or source_shell):
            destination_payload = request.source_model.lookup_payload_ir(
                target_unit_kind,
                destination_section,
                None,
                None,
            )
            destination_ir = destination_payload.payload_ir
            destination_cross_ir = destination_payload.cross_heading_ir
            if destination_ir is not None and not _is_sparse_source_shell(destination_ir):
                muutos_ir, cross_ir = destination_ir, destination_cross_ir
                surface_findings.append(
                    Finding(
                        kind="ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
                        role="observation",
                        stage="_build_group_surface",
                        detail={
                            "kind": "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
                            "message": (
                                "Same-group recodification payload surface selected from the destination "
                                "section because the source-number body is absent or an omission shell."
                            ),
                            "target_unit_kind": target_unit_kind,
                            "source_target_norm": target_norm,
                            "destination_target_norm": destination_section,
                            "target_chapter": target_chapter or "",
                            "target_part": target_part or "",
                            "source_surface": source_surface,
                        },
                        source_statute=source_statute,
                        blocking=False,
                    )
                )
    if muutos_ir is not None and source_statute:
        muutos_ir, _ = normalize_source_ir(
            muutos_ir,
            source_statute,
            allow_dotted_paragraph_subsection_promotion=False,
        )
    group_surface = _build_group_surface_factory(
        body_ir=muutos_ir,
        cross_heading_ir=cross_ir,
        source_statute=source_statute,
        target_unit_kind=cast(TargetUnitKind, target_unit_kind),
        target_norm=target_norm,
        target_chapter=target_chapter,
    )

    b = PhaseBuilder()
    if surface_findings:
        b.add_findings(tuple(surface_findings))
    if group_surface.body_ir is None and any(op.op_type not in (OpType.REPEAL, "ADD_HEADING") for op in group_ops):
        b.add_findings((
            Finding(
                kind="ELAB.MISSING_PAYLOAD_SURFACE",
                role="observation",
                stage="_build_group_surface",
                detail={
                    "target_unit_kind": target_unit_kind,
                    "target_norm": target_norm,
                    "target_chapter": target_chapter or "",
                    "op_count": len(group_ops),
                },
                source_statute=source_statute,
                blocking=False,
            ),
        ))
    return b.finish(group_surface)


_BuildGroupSurfaceRequest = BuildGroupSurfaceRequest
_build_group_surface = build_group_surface
