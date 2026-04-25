"""Finland-local elaboration carrier for Stage-2 payload processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode

if TYPE_CHECKING:
    from lawvm.core.payload_surface import PayloadSurface
    from lawvm.finland.ops import AmendmentOp
    from lawvm.finland.payload_normalize import PayloadCompletenessWitness, SubsectionSlotAssignmentResult


@dataclass(frozen=True)
class ElaboratedGroup:
    """Finland elaboration output, ready for frontend-local lowering."""

    muutos_ir: Optional[IRNode]
    cross_ir: Optional[IRNode]
    group_ops: Tuple["AmendmentOp", ...]
    remapped_target_norm: str
    slot_assignment: "SubsectionSlotAssignmentResult | None"
    source_pathologies: Tuple[SourcePathology, ...]
    was_filtered: bool = False
    payload_surface: Optional["PayloadSurface"] = None
    payload_completeness: "PayloadCompletenessWitness | None" = None


def build_elaborated_group(
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode],
    group_ops: list["AmendmentOp"],
    remapped_target_norm: str,
    slot_assignment: "SubsectionSlotAssignmentResult | None",
    source_pathologies: list[SourcePathology] | None = None,
    was_filtered: bool = False,
    payload_surface: Optional["PayloadSurface"] = None,
    payload_completeness: "PayloadCompletenessWitness | None" = None,
) -> ElaboratedGroup:
    """Build the Finland-local elaboration carrier from Stage-2 outputs."""
    return ElaboratedGroup(
        muutos_ir=muutos_ir,
        cross_ir=cross_ir,
        group_ops=tuple(group_ops),
        remapped_target_norm=remapped_target_norm,
        slot_assignment=slot_assignment,
        source_pathologies=tuple(source_pathologies or []),
        was_filtered=was_filtered,
        payload_surface=payload_surface,
        payload_completeness=payload_completeness,
    )
