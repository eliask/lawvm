"""Stage 1 payload-surface extraction for Finland compile groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_surface import GroupSurface, build_group_surface as _build_group_surface_factory
from lawvm.core.phase_result import Finding, PhaseBuilder, PhaseResult
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.amendment_payload_lookup import _find_muutos_ir
from lawvm.finland.helpers import _is_omission_ir, _norm_num_token
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.source_normalize import normalize_source_ir


def _renumber_destination_section_label(group_ops: list[AmendmentOp]) -> Optional[str]:
    labels = {
        dest_path["section"]
        for op in group_ops
        if op.op_type == "RENUMBER"
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


@dataclass(frozen=True, slots=True)
class BuildGroupSurfaceRequest:
    """Typed inputs for compile-group payload surface extraction."""

    group_ops: list[AmendmentOp]
    muutos_tree: etree._Element
    target_unit_kind: str
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]


def build_group_surface(request: BuildGroupSurfaceRequest) -> PhaseResult[GroupSurface]:
    """Stage 1: extract amendment-body payload. Pure of live state.

    Returns a PhaseResult where:
    - ``output``       — ``GroupSurface`` (muutos_ir + cross_ir + source info)
    - ``observations`` — malformed payload shape (missing muutos_ir despite
                         non-trivial ops)
    - ``obligations``  — none produced at this stage
    """
    group_ops = request.group_ops
    muutos_tree = request.muutos_tree
    target_unit_kind = request.target_unit_kind
    target_norm = request.target_norm
    target_chapter = request.target_chapter
    target_part = request.target_part

    source_statute = next(
        (str(op.source_statute or "") for op in group_ops if op.source_statute),
        "",
    )
    surface_findings: list[Finding] = []

    muutos_ir, cross_ir = _find_muutos_ir(
        muutos_tree,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
    )
    if target_unit_kind == "section":
        destination_section = _renumber_destination_section_label(group_ops)
        has_same_group_relabel = any(op.op_type == "RENUMBER" for op in group_ops)
        has_followup_payload_op = any(
            op.op_type != "RENUMBER"
            and op.target_unit_kind == "section"
            and not (
                has_same_group_relabel
                and op.op_type == "REPLACE"
                and _norm_num_token(op.target_section or "") == target_norm
                and op.target_paragraph is None
                and not op.target_item
                and not op.target_special
                and op.target_chapter == target_chapter
                and op.target_part == target_part
            )
            for op in group_ops
        )
        source_shell = _is_sparse_source_shell(muutos_ir)
        source_surface = "missing" if muutos_ir is None else "sparse_omission_shell"
        if destination_section is not None and has_followup_payload_op and (muutos_ir is None or source_shell):
            destination_ir, destination_cross_ir = _find_muutos_ir(
                muutos_tree,
                target_unit_kind,
                destination_section,
                None,
                None,
            )
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
        muutos_ir, _ = normalize_source_ir(muutos_ir, source_statute)
    group_surface = _build_group_surface_factory(
        body_ir=muutos_ir,
        cross_heading_ir=cross_ir,
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
    )

    b = PhaseBuilder()
    if surface_findings:
        b.add_findings(tuple(surface_findings))
    if group_surface.body_ir is None and any(op.op_type not in ("REPEAL", "ADD_HEADING") for op in group_ops):
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
