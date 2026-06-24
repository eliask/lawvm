"""Payload-normalization helpers for the Finnish amendment pipeline.

This module owns the stage between:

- amendment-body extraction (`_find_muutos_ir`)
- deterministic apply

It exists because some Finland replay semantics depend on interpreting sparse or
malformed amendment payloads against the live replay state. That logic is not
pure extraction and not pure apply, so keeping it explicit is architecturally
clearer than scattering it inside `grafter.py`.

CONSTITUTION: No ambient master access in this module.
All live-state reads go through PayloadElaborationContext.
See notes/LAWVM_CONSTITUTION.md §3 (Phase Ownership Rules).
"""

from __future__ import annotations
from typing_extensions import override

from collections.abc import MutableMapping, Iterator
import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Literal, Mapping, Optional, Set, Tuple

from lawvm.core.compile_result import AdmissibleBindingCoverage, SourcePathology
from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.payload_elaboration import (
    PayloadCompletenessWitness,
    PayloadElaborationResult,
    SlotBinding,
    SlotBindingReport,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.helpers import _norm_num_token, _norm_row_anchor_text
from lawvm.finland.labels import leaf_label_identity_key
from lawvm.finland.source_pathology import build_container_membership_mismatch_pathology
from lawvm.finland.source_pathology import build_destructive_shape_loss_risk_pathology
from lawvm.finland.source_pathology import build_sparse_item_body_missing_pathology
from lawvm.finland.merge import (
    _drop_suspicious_partial_subsection_shell_replaces,
    _drop_suspicious_partial_whole_section_replaces,
    _pre_resolve_omissions,
)
from lawvm.finland.ops import AmendmentOp, FailedOp, _lo_with_path_update, _op_target_subsection_label
from lawvm.finland.sparse_tail_claims import (
    SPARSE_OMISSION_TAIL_PRUNE_RULE,
    SparseOmissionTailClaim,
    prune_sparse_tail_claims_from_carrier,
)
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.table_target_merge import merge_numbered_table_targets_into_live_section
from lawvm.finland.table_target_merge import mentioned_numbered_table_labels

from lawvm.core.elaboration_context import PayloadElaborationContext
from lawvm.core.payload_surface import PayloadSurface, TargetUnitKind

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile
    from lawvm.finland.ops import ReplayProfile


_NUMBERED_TABLE_XML_SUBSECTION_OFFSET_RULE = "ELAB.NUMBERED_TABLE_XML_SUBSECTION_OFFSET"
_INTERNAL_ORDERED_LIST_INSERT_RULE = "ELAB.INTERNAL_ORDERED_LIST_INSERT_REWRITE"
_PRESERVE_IMPLICIT_PARAGRAPH_NUMBER_ATTR = "lawvm_preserve_implicit_paragraph_numbering"
_CONTINUATION_ROW_PREFIX_RE = re.compile(r"^(\d+)\.\s+")


class SubsectionSlotMap(MutableMapping[int, IRNode]):
    """Typed subsection-slot assignments keyed by op identity.

    This wrapper keeps backward-compatible dict-like behavior for existing code
    and tests, while giving the elaboration boundary a named semantic object.
    """

    def __init__(
        self,
        by_op_id: Optional[Dict[int, IRNode]] = None,
        *,
        by_stable_op_id: Optional[Dict[str, IRNode]] = None,
        stable_id_by_identity_key: Optional[Dict[int, str]] = None,
    ) -> None:
        self._by_op_id: Dict[int, IRNode] = dict(by_op_id or {})
        self._by_stable_op_id: Dict[str, IRNode] = dict(by_stable_op_id or {})
        self._stable_id_by_identity_key: Dict[int, str] = dict(stable_id_by_identity_key or {})

    @staticmethod
    def _coerce_key(op_or_id: object) -> int:
        if isinstance(op_or_id, int):
            return op_or_id
        return id(op_or_id)

    def assign(self, op: AmendmentOp, subsection: IRNode) -> None:
        identity_key = id(op)
        self._by_op_id[identity_key] = subsection
        if op.op_id:
            self._by_stable_op_id[op.op_id] = subsection
            self._stable_id_by_identity_key[identity_key] = op.op_id

    def bind_stable_op_id(
        self,
        op_id: Optional[str],
        subsection: IRNode,
        *,
        identity_key: int | None = None,
    ) -> None:
        if not op_id:
            return
        self._by_stable_op_id[op_id] = subsection
        if identity_key is not None:
            self._stable_id_by_identity_key[identity_key] = op_id

    def for_op(self, op: AmendmentOp) -> Optional[IRNode]:
        mapped = self._by_op_id.get(id(op))
        if mapped is not None:
            return mapped
        if op.op_id:
            return self._by_stable_op_id.get(op.op_id)
        return None

    def for_stable_op_id(self, op_id: Optional[str]) -> Optional[IRNode]:
        if not op_id:
            return None
        return self._by_stable_op_id.get(op_id)

    def copy(self) -> "SubsectionSlotMap":
        return SubsectionSlotMap(
            self._by_op_id,
            by_stable_op_id=self._by_stable_op_id,
            stable_id_by_identity_key=self._stable_id_by_identity_key,
        )

    @override
    def __getitem__(self, key: object) -> IRNode:
        return self._by_op_id[self._coerce_key(key)]

    @override
    def __setitem__(self, key: object, value: IRNode) -> None:
        coerced = self._coerce_key(key)
        self._by_op_id[coerced] = value
        stable_id = self._stable_id_by_identity_key.get(coerced)
        if stable_id is not None:
            self._by_stable_op_id[stable_id] = value

    @override
    def __delitem__(self, key: object) -> None:
        coerced = self._coerce_key(key)
        del self._by_op_id[coerced]
        stable_id = self._stable_id_by_identity_key.pop(coerced, None)
        if stable_id is not None:
            self._by_stable_op_id.pop(stable_id, None)

    @override
    def __iter__(self) -> Iterator[int]:
        return iter(self._by_op_id)

    @override
    def __len__(self) -> int:
        return len(self._by_op_id)

    @override
    def items(self):
        return self._by_op_id.items()


@dataclass(frozen=True)
class ElaborationObservation:
    """Typed frontend/elaboration observation emitted before replay apply."""

    kind: str
    stage: str
    detail: Dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.detail is None:
            object.__setattr__(self, "detail", {})


@dataclass(frozen=True)
class GroupPayloadNormalizationResult:
    """Normalized amendment payload and group ops for one target group."""

    muutos_ir: Optional[IRNode]
    group_ops: tuple[AmendmentOp, ...]
    subsec_map: SubsectionSlotMap
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None
    sparse_slot_bindings: "tuple[SparsePayloadSlotBinding, ...] | None" = None
    payload_pruned: bool = False
    unassigned_sparse_payload_slots: tuple[str, ...] | None = None
    rejected_ops: tuple[FailedOp, ...] = ()
    source_pathologies: tuple[SourcePathology, ...] | None = None
    elaboration_observations: tuple[ElaborationObservation, ...] | None = None
    payload_completeness: "PayloadCompletenessWitness | None" = None

    def __post_init__(self) -> None:
        if self.sparse_slot_bindings is None:
            object.__setattr__(self, "sparse_slot_bindings", ())
        if self.unassigned_sparse_payload_slots is None:
            object.__setattr__(self, "unassigned_sparse_payload_slots", ())
        if self.source_pathologies is None:
            object.__setattr__(self, "source_pathologies", ())
        if self.elaboration_observations is None:
            object.__setattr__(self, "elaboration_observations", ())


@dataclass(frozen=True, slots=True)
class TableRowRewriteResult:
    """Result of resolving named/partial table-row operations to row targets."""

    group_ops: tuple[AmendmentOp, ...]
    muutos_ir: Optional[IRNode]
    rewritten: bool = False


@dataclass(frozen=True, slots=True)
class PrunedPayloadSectionWitness:
    """Evidence for one section removed from a container payload."""

    label: str
    path: str
    structural_hash: str

    def to_detail(self) -> dict[str, str]:
        return {
            "label": self.label,
            "path": self.path,
            "structural_hash": self.structural_hash,
        }


@dataclass(frozen=True, slots=True)
class ContainerPayloadPruningResult:
    """Typed result for container payload pruning.

    ``__iter__`` preserves the historical ``(muutos_ir, changed, pruned)``
    unpacking surface while new callers consume named evidence fields.
    """

    muutos_ir: Optional[IRNode]
    changed: bool
    pruned_labels: tuple[str, ...] = ()
    before_child_paths: tuple[str, ...] = ()
    after_child_paths: tuple[str, ...] = ()
    pruned_section_witnesses: tuple[PrunedPayloadSectionWitness, ...] = ()

    def __iter__(self) -> Iterator[object]:
        yield self.muutos_ir
        yield self.changed
        yield list(self.pruned_labels)


@dataclass(frozen=True)
class SparseSubsectionElaborationResult:
    """Typed sparse subsection elaboration output before replay apply."""

    muutos_ir: Optional[IRNode]
    group_ops: tuple[AmendmentOp, ...]
    subsec_map: SubsectionSlotMap
    source_pathologies: tuple[SourcePathology, ...]
    rejected_ops: tuple[FailedOp, ...] = ()
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None
    sparse_slot_bindings: "tuple[SparsePayloadSlotBinding, ...] | None" = None
    unassigned_sparse_payload_slots: tuple[str, ...] | None = None
    elaboration_observations: tuple[ElaborationObservation, ...] | None = None
    payload_completeness: "PayloadCompletenessWitness | None" = None

    def __post_init__(self) -> None:
        if self.sparse_slot_bindings is None:
            object.__setattr__(self, "sparse_slot_bindings", ())
        if self.unassigned_sparse_payload_slots is None:
            object.__setattr__(self, "unassigned_sparse_payload_slots", ())
        if self.elaboration_observations is None:
            object.__setattr__(self, "elaboration_observations", ())


def payload_elaboration_projection_from_group_result(
    result: GroupPayloadNormalizationResult,
    *,
    subject_id: str,
    result_id: str = "",
    jurisdiction: str = "fi",
) -> PayloadElaborationResult:
    """Project Finland payload normalization output into a shared report object."""

    completeness = result.payload_completeness
    completeness_kind = completeness.kind if completeness is not None else "unknown"
    slot_report = _slot_binding_report_from_assignment(
        result.slot_assignment,
        subject_id=subject_id,
        jurisdiction=jurisdiction,
        completeness_kind=completeness_kind,
    )
    status = _payload_projection_status(result, completeness_kind)
    return PayloadElaborationResult(
        result_id=result_id or f"{jurisdiction}:payload_elaboration:{subject_id}",
        jurisdiction=jurisdiction,
        owner_phase="payload_elaboration",
        elaboration_status=status,
        payload_surface_kind=type(result.muutos_ir).__name__ if result.muutos_ir is not None else "missing_payload_ir",
        completeness_kind=completeness_kind,
        elaborated_op_count=len(result.group_ops),
        rejected_op_count=len(result.rejected_ops),
        source_pathology_count=len(result.source_pathologies or ()),
        observation_count=len(result.elaboration_observations or ()),
        payload_completeness=completeness,
        slot_binding_report=slot_report,
        replay_authorized=False,
        authorization_status="projection_only_not_replay_authority",
        safe_default="record_without_replay_authority",
        forbidden_shortcuts=("treat_payload_projection_as_replay_authorization",),
        detail={
            "payload_pruned": result.payload_pruned,
            "unassigned_sparse_payload_slots": tuple(result.unassigned_sparse_payload_slots or ()),
            "payload_completeness_reasons": completeness.reasons if completeness is not None else (),
            "tail_policy": completeness.tail_policy if completeness is not None else "",
        },
    )


def _payload_projection_status(
    result: GroupPayloadNormalizationResult,
    completeness_kind: str,
) -> str:
    if result.rejected_ops:
        return "rejected_ops_present"
    if result.source_pathologies:
        return "source_pathology_present"
    if completeness_kind in {"fragmentary", "unsupported"}:
        return completeness_kind
    return "elaborated"


def _slot_binding_report_from_assignment(
    assignment: "SubsectionSlotAssignmentResult | None",
    *,
    subject_id: str,
    jurisdiction: str,
    completeness_kind: str,
) -> SlotBindingReport | None:
    if assignment is None:
        return None
    bindings = tuple(
        SlotBinding(
            binding_id=f"{subject_id}:slot:{idx}",
            source_slot_id=str(binding.payload_slot_label),
            target_slot_id=_slot_binding_target_id(binding),
            binding_status="bound",
            operation_id=str(binding.op_description),
            binding_rule_id=str(binding.op_type),
            detail={
                "payload_slot_index": binding.payload_slot_index,
                "target_paragraph": binding.target_paragraph,
                "target_item": binding.target_item,
                "target_special": binding.target_special,
            },
        )
        for idx, binding in enumerate(assignment.sparse_slot_bindings)
    )
    status = "complete" if not assignment.unassigned_payload_slots else "unassigned_source_slots"
    return SlotBindingReport(
        subject_id=subject_id,
        jurisdiction=jurisdiction,
        owner_phase="payload_elaboration",
        binding_status=status,
        completeness_kind=completeness_kind,
        bindings=bindings,
        unassigned_source_slots=assignment.unassigned_payload_slots,
        detail={
            "used_sub_count": len(assignment.used_subs),
            "binding_certificate_count": len(assignment.binding_certificates),
            "binding_observation_count": len(assignment.binding_observations),
        },
    )


def _slot_binding_target_id(binding: "SparsePayloadSlotBinding") -> str:
    if binding.target_item:
        return f"subsection:{binding.target_paragraph or ''}/item:{binding.target_item}"
    if binding.target_special:
        return f"subsection:{binding.target_paragraph or ''}/{binding.target_special}"
    return f"subsection:{binding.target_paragraph or ''}"


@dataclass(frozen=True)
class SubsectionSlotInputs:
    """Typed sparse subsection-slot inputs collected from payload and ops."""

    amend_subs: tuple[IRNode, ...]
    has_omission_slots: bool
    payload_subsec_ops: tuple[AmendmentOp, ...]
    intro_subsec_ops: tuple[AmendmentOp, ...]
    renumber_subsec_ops: tuple[AmendmentOp, ...]
    duplicate_targets: tuple[int, ...]


@dataclass(frozen=True)
class SparsePayloadSlotBinding:
    """Typed binding from one logical changed moment to one payload slot."""

    op_description: str
    op_type: str
    target_paragraph: int | None
    target_item: str | None
    target_special: str | None
    payload_slot_index: int
    payload_slot_label: str


@dataclass(frozen=True)
class SubsectionSlotAssignmentResult:
    """Sparse subsection-slot assignment output before replay apply."""

    subsec_map: SubsectionSlotMap
    sparse_slot_bindings: tuple[SparsePayloadSlotBinding, ...]
    used_subs: tuple[int, ...]
    unassigned_payload_slots: tuple[str, ...]
    binding_certificates: tuple[AdmissibleBindingCoverage, ...] = ()
    binding_observations: tuple[ElaborationObservation, ...] = ()
    binding_admissibility_by_op_id: tuple[tuple[str, str], ...] = ()

    def for_op(self, op: AmendmentOp) -> Optional[IRNode]:
        return self.subsec_map.for_op(op)

    def has_op(self, op: AmendmentOp) -> bool:
        return self.for_op(op) is not None

    def for_stable_op_id(self, op_id: Optional[str]) -> Optional[IRNode]:
        return self.subsec_map.for_stable_op_id(op_id)

    def has_stable_op_id(self, op_id: Optional[str]) -> bool:
        return self.for_stable_op_id(op_id) is not None

    def has_binding(self, op_id: Optional[str], op: Optional[AmendmentOp] = None) -> bool:
        if self.has_stable_op_id(op_id):
            return True
        return op is not None and self.has_op(op)

    def resolve_for_op(self, op: AmendmentOp, fallback: Optional[IRNode] = None) -> Optional[IRNode]:
        mapped = self.for_op(op)
        if mapped is not None:
            return mapped
        return fallback

    def resolve_for_stable_op_id(self, op_id: Optional[str], fallback: Optional[IRNode] = None) -> Optional[IRNode]:
        mapped = self.for_stable_op_id(op_id)
        if mapped is not None:
            return mapped
        return fallback

    def resolve_apply_subsection_ir(
        self,
        op: AmendmentOp,
        fallback: Optional[IRNode] = None,
        muutos_ir: Optional[IRNode] = None,
    ) -> Optional[IRNode]:
        """Return the effective subsection payload for late replay consumers."""
        mapped = self.for_op(op)
        if mapped is not None:
            return mapped
        if fallback is not None:
            return fallback
        return None

    def resolve_apply_subsection_ir_for_stable_op_id(
        self,
        op_id: Optional[str],
        fallback: Optional[IRNode] = None,
        muutos_ir: Optional[IRNode] = None,
    ) -> Optional[IRNode]:
        """Return the effective subsection payload for late-waist consumers keyed by stable op id."""
        mapped = self.for_stable_op_id(op_id)
        if mapped is not None:
            return mapped
        if fallback is not None:
            return fallback
        return None

    def resolve_apply_subsection_ir_for_binding(
        self,
        op_id: Optional[str],
        op: Optional[AmendmentOp],
        fallback: Optional[IRNode] = None,
    ) -> Optional[IRNode]:
        """Return the effective subsection payload for late-waist consumers.

        Precedence is:
        1. stable op id mapping
        2. legacy object-identity mapping for blank-id transitional ops
        3. explicit fallback
        """
        mapped = self.for_stable_op_id(op_id)
        if mapped is not None:
            return mapped
        if op is not None:
            mapped = self.for_op(op)
            if mapped is not None:
                return mapped
        if fallback is not None:
            return fallback
        return None

    def summary(self, *, include_leftover_slot_count: bool = False) -> Dict[str, Any]:
        return summarize_slot_assignment(
            self.sparse_slot_bindings,
            self.unassigned_payload_slots,
            include_leftover_slot_count=include_leftover_slot_count,
        )

    def binding_admissibility_for_stable_op_id(self, op_id: Optional[str]) -> Optional[str]:
        if not (op_id or "").strip():
            return None
        return dict(self.binding_admissibility_by_op_id).get(str(op_id))

    def has_owned_bound_payload_for_stable_op_id(self, op_id: Optional[str]) -> bool:
        return self.binding_admissibility_for_stable_op_id(op_id) == "single"

    def with_subsec_map(self, subsec_map: SubsectionSlotMap) -> "SubsectionSlotAssignmentResult":
        return dc_replace(self, subsec_map=subsec_map)


def _classify_payload_completeness(
    *,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    assignment: Optional[SubsectionSlotAssignmentResult],
    source_pathologies: List[SourcePathology],
    observations: List[ElaborationObservation],
) -> PayloadCompletenessWitness:
    reasons: list[str] = []
    detail: dict[str, Any] = {}
    pathology_codes = {str(pathology.code or "") for pathology in source_pathologies}
    observation_kinds = {str(obs.kind or "") for obs in observations}
    targets_item_level = any(bool(op.target_item) for op in group_ops)
    has_whole_section_op = any(
        op.target_paragraph is None and not op.target_item and not op.target_special
        for op in group_ops
    )
    has_descendant_scoped_op = any(
        op.target_paragraph is not None or bool(op.target_item) or bool(op.target_special)
        for op in group_ops
    )
    amend_subsection_count = (
        len([child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION])
        if muutos_ir is not None
        else 0
    )
    has_omission = bool(muutos_ir is not None and any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children))
    mapped_tail_omission = bool(
        assignment is not None
        and any(any(child.kind is IRNodeKind.OMISSION for child in mapped.children) for mapped in assignment.subsec_map.values())
    )
    payloadless_repeal_group = bool(
        muutos_ir is None
        and group_ops
        and all(op.op_type == "REPEAL" for op in group_ops)
    )

    if payloadless_repeal_group:
        reasons.append("payloadless_repeal_group")
        return PayloadCompletenessWitness(
            kind="complete",
            reasons=tuple(reasons),
            tail_policy="replace_if_target_scope_requires",
            detail=detail,
        )

    if muutos_ir is None:
        reasons.append("missing_payload_ir")
        return PayloadCompletenessWitness(
            kind="unsupported",
            reasons=tuple(reasons),
            tail_policy="classify_only",
            detail=detail,
        )

    if targets_item_level and (
        "SPARSE_ITEM_BODY_MISSING" in pathology_codes or "ITEM_TARGET_STRUCTURE_ABSENT" in pathology_codes
    ):
        reasons.append("item_structure_not_explicit")
        return PayloadCompletenessWitness(
            kind="inline_enum_candidate",
            reasons=tuple(reasons),
            tail_policy="classify_or_conservative_lift",
            detail={"pathology_codes": sorted(code for code in pathology_codes if code)},
        )

    if pathology_codes & {"DESTRUCTIVE_SHAPE_LOSS_RISK", "CONTAINER_MEMBERSHIP_MISMATCH"}:
        reasons.extend(sorted(pathology_codes & {"DESTRUCTIVE_SHAPE_LOSS_RISK", "CONTAINER_MEMBERSHIP_MISMATCH"}))
        return PayloadCompletenessWitness(
            kind="unsupported",
            reasons=tuple(reasons),
            tail_policy="classify_only",
            detail={"pathology_codes": sorted(code for code in pathology_codes if code)},
        )

    if assignment is not None and assignment.unassigned_payload_slots:
        reasons.append("unassigned_sparse_payload_slots")
        detail["unassigned_payload_slots"] = list(assignment.unassigned_payload_slots)
        return PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=tuple(reasons),
            tail_policy="preserve_unstated_tail",
            detail=detail,
        )

    if "ELAB.AMBIGUOUS_BINDING" in observation_kinds:
        reasons.append("ambiguous_binding")
        return PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=tuple(reasons),
            tail_policy="preserve_unstated_tail",
            detail=detail,
        )

    if has_whole_section_op and has_descendant_scoped_op and amend_subsection_count == 1:
        reasons.append("same_group_descendant_scoped_single_subsection_shell")
        detail["amend_subsection_count"] = amend_subsection_count
        detail["descendant_scoped_op_count"] = sum(
            1
            for op in group_ops
            if op.target_paragraph is not None or bool(op.target_item) or bool(op.target_special)
        )
        return PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=tuple(reasons),
            tail_policy="preserve_unstated_tail",
            detail=detail,
        )

    if (
        has_omission
        or mapped_tail_omission
        or observation_kinds
        & {
            "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
            "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
            "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
            _SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL_RULE,
            _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
            _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
            _FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE,
            "ELAB.CONTAINER_PRUNED_SHADOWED",
        }
    ):
        if has_omission:
            reasons.append("omission_marked_sparse_payload")
        if mapped_tail_omission:
            reasons.append("mapped_tail_omission")
        sparse_reasons = sorted(
            observation_kinds
            & {
                "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
                _SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL_RULE,
                _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
                _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
                _FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE,
                "ELAB.CONTAINER_PRUNED_SHADOWED",
            }
        )
        reasons.extend(sparse_reasons)
        return PayloadCompletenessWitness(
            kind="sparse_certified",
            reasons=tuple(reasons),
            tail_policy="preserve_unstated_tail",
            detail=detail,
        )

    return PayloadCompletenessWitness(
        kind="complete",
        reasons=("no_sparse_or_fragmentary_signals",),
        tail_policy="replace_if_target_scope_requires",
        detail=detail,
    )


def _unsupported_payload_rejected_ops(
    *,
    group_ops: List[AmendmentOp],
    rejected_ops: List[FailedOp],
    payload_completeness: PayloadCompletenessWitness,
) -> tuple[FailedOp, ...]:
    if payload_completeness.kind != "unsupported":
        return ()
    existing = {
        (
            failed.description,
            failed.target_unit_kind,
            failed.target_section,
            failed.target_chapter or "",
        )
        for failed in rejected_ops
    }
    reason_code = "UNSUPPORTED_PAYLOAD_" + (
        "_".join(str(part).upper() for part in payload_completeness.reasons if str(part)) or "CLASSIFY_ONLY"
    )
    reason = "ELAB." + reason_code
    generated: list[FailedOp] = []
    for op in group_ops:
        if op.op_type == "RENUMBER":
            continue
        key = (
            op.description(),
            op.target_unit_kind,
            str(op.target_section or ""),
            op.target_chapter or "",
        )
        if key in existing:
            continue
        generated.append(
            FailedOp.from_scope(
                amendment_id=op.source_statute or "",
                description=op.description(),
                reason=reason,
                reason_code=reason_code,
                target_section=str(op.target_section or ""),
                target_unit_kind=op.target_unit_kind,
                target_chapter=op.target_chapter,
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_item,
            )
        )
    return tuple(generated)


def summarize_slot_assignment(
    sparse_slot_bindings: Iterable["SparsePayloadSlotBinding | Mapping[str, Any]"],
    unassigned_payload_slots: Iterable[str],
    *,
    leftover_count: int | None = None,
    include_leftover_slot_count: bool = False,
) -> Dict[str, Any]:
    bindings = list(sparse_slot_bindings)
    binding_labels = [
        str(
            binding.payload_slot_label
            if isinstance(binding, SparsePayloadSlotBinding)
            else binding.get("payload_slot_label", "") or ""
        )
        for binding in bindings
        if str(
            binding.payload_slot_label
            if isinstance(binding, SparsePayloadSlotBinding)
            else binding.get("payload_slot_label", "") or ""
        )
    ]
    leftover_labels = [str(label) for label in unassigned_payload_slots if str(label)]
    summary = {
        "binding_count": len(bindings),
        "leftover_count": len(leftover_labels) if leftover_count is None else leftover_count,
        "binding_labels": binding_labels,
        "leftover_labels": leftover_labels,
    }
    if include_leftover_slot_count:
        summary["leftover_slot_count"] = len(leftover_labels)
    return summary


@dataclass
class SubsectionSlotAssignmentState:
    """Mutable typed state for sparse subsection-slot assignment phases."""

    subsec_map: SubsectionSlotMap
    used_subs: Set[int]
    sub_idx: int = 0
    prev_mom: Optional[int] = None
    binding_rule_by_op_id: Dict[int, str] = field(default_factory=dict)
    binding_observations: List[ElaborationObservation] = field(default_factory=list)


def _obs(kind: str, stage: str, **detail: Any) -> ElaborationObservation:
    return ElaborationObservation(kind=kind, stage=stage, detail=detail)


def _slot_ir_has_item(node: IRNode, target: str) -> bool:
    target_key = leaf_label_identity_key(target)
    compound_match = re.fullmatch(r"(\d+)([a-z])", target_key)
    compound_parent_seen = False
    compound_flat_subitem_seen = False
    for child in node.children:
        if child.kind is IRNodeKind.PARAGRAPH and child.label and leaf_label_identity_key(child.label) == target_key:
            return True
        if (
            compound_match
            and child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and leaf_label_identity_key(child.label) == compound_match.group(1)
        ):
            compound_parent_seen = True
            subitem_key = leaf_label_identity_key(compound_match.group(2), "subitem")
            if any(
                grandchild.kind is IRNodeKind.SUBPARAGRAPH
                and grandchild.label
                and leaf_label_identity_key(grandchild.label, "subitem") == subitem_key
                for grandchild in child.children
            ):
                return True
        if (
            compound_match
            and child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and leaf_label_identity_key(child.label, "subitem") == leaf_label_identity_key(compound_match.group(2), "subitem")
        ):
            compound_flat_subitem_seen = True
        if child.kind is IRNodeKind.PARAGRAPH:
            for grandchild in child.children:
                if (
                    grandchild.kind is IRNodeKind.SUBPARAGRAPH
                    and grandchild.label
                    and leaf_label_identity_key(grandchild.label, "subitem") == target_key
                ):
                    return True
    if compound_parent_seen and compound_flat_subitem_seen:
        return True
    sub_text = (node.text or " ".join(child.text or "" for child in node.children)).strip()
    # lawvm-regex: owning_parser P-item-prefix flat ``N)`` item-label predicate over IR sub text; lexer-shaped, drives slot assignment, no op
    m = re.match(r"^(\d+[a-zA-Z]*)\)", sub_text)
    return bool(m and leaf_label_identity_key(m.group(1)) == target_key)


def _slot_ir_has_omission(node: IRNode) -> bool:
    return any(child.kind is IRNodeKind.OMISSION for child in node.children)


def _slot_ir_is_in_place_merge(node: IRNode) -> bool:
    return node.attrs.get("lawvm_in_place_merge") == "1"


def _slot_ir_is_intro_only_fragment(node: IRNode) -> bool:
    if any(child.kind is IRNodeKind.PARAGRAPH for child in node.children):
        return False
    text = irnode_to_text(node).strip()
    return bool(text and text.endswith(":"))


def _slot_ir_has_paragraph_row(node: IRNode) -> bool:
    return any(child.kind is IRNodeKind.PARAGRAPH for child in node.children)


def _slot_ir_carries_item_row_marker(node: IRNode, target_item: str) -> bool:
    """Return whether ``node``'s flat content carries the item's own row marker.

    A content-only subsection can hold the source-owned text for an item under
    its own row marker:

    * a lettered fee-table row ``H. Poronlihan ...`` for item ``h`` (addressed
      ``N kohta h``); or
    * a flat ``8 a) meriliikenteen ...`` item-prefix row for item ``8a``
      (addressed ``N momentin 8 a kohta``), where the digit/letter compound may
      carry an internal space in the source text.

    When the candidate slot literally carries the item's own marker, the item op
    is NOT being mis-bound to an unrelated intro/body fragment — the slot IS the
    item's content — so the sparse-fallback guard must not drop it. Matching is
    restricted to a leading/standalone ``LABEL.`` or ``LABEL)`` marker so an
    incidental letter inside prose does not falsely exempt the op.
    """
    item_key = leaf_label_identity_key(str(target_item or ""))
    if not item_key:
        return False
    text = irnode_to_text(node).strip()
    if not text:
        return False
    # ``LABEL.``/``LABEL)`` row-prefix predicate over flat content (compound
    # ``N a`` markers may carry an internal space); lexer-shaped, drives slot
    # assignment exemption only, no op.
    # lawvm-regex: owning_parser P-item-row-marker lettered/numbered
    for match in re.finditer(r"(?:^|\s)([0-9]+\s*[a-zA-Z]*|[a-zA-Z])[.)]\s", text):
        marker = "".join(match.group(1).split())
        if leaf_label_identity_key(marker) == item_key:
            return True
    return False


def _assign_duplicate_target_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    for target in slot_inputs.duplicate_targets:
        insert_ops = [
            op
            for op in slot_inputs.payload_subsec_ops
            if op.target_paragraph == target and op.op_type == "INSERT" and not op.target_item
        ]
        replace_ops = [
            op
            for op in slot_inputs.payload_subsec_ops
            if op.target_paragraph == target and op.op_type == "REPLACE" and not op.target_item
        ]
        if len(insert_ops) != 1 or len(replace_ops) != 1:
            continue
        exact_idx = next(
            (
                idx
                for idx, sub in enumerate(slot_inputs.amend_subs)
                if idx not in state.used_subs and _norm_num_token(sub.label or "") == str(target)
            ),
            None,
        )
        if exact_idx is None:
            continue
        shifted_idx = next(
            (idx for idx in range(exact_idx + 1, len(slot_inputs.amend_subs)) if idx not in state.used_subs),
            None,
        )
        if shifted_idx is None:
            continue
        state.subsec_map.assign(insert_ops[0], slot_inputs.amend_subs[exact_idx])
        state.used_subs.add(exact_idx)
        state.subsec_map.assign(replace_ops[0], slot_inputs.amend_subs[shifted_idx])
        state.used_subs.add(shifted_idx)


def _assign_insert_before_moved_same_target_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind ``INSERT N`` before the source slot for an explicitly moved ``REPLACE N``.

    Finnish formulas can say ``lisätään N momentti, jolloin muutettu N momentti
    ja nykyinen ... siirtyvät ...``. The body then serializes the new N slot
    immediately before the amended old N slot. This is not the ordinary
    duplicate-target shape where the insert owns exact slot N and the replace
    owns the following slot.
    """
    occupied_targets = {
        int(op.target_paragraph)
        for op in slot_inputs.payload_subsec_ops
        if op.target_paragraph is not None
    }
    renumber_destinations = {
        int(op.target_paragraph): int(destination)
        for op in slot_inputs.renumber_subsec_ops
        if (
            op.target_paragraph is not None
            and _renumber_consumes_source_payload(op)
            and (destination := _destination_subsection_label_for_renumber(op)).isdigit()
        )
    }
    intro_reserved_targets = {
        int(op.target_paragraph)
        for op in slot_inputs.intro_subsec_ops
        if op.target_paragraph is not None and op.target_special == "johd"
    }
    for target in slot_inputs.duplicate_targets:
        if renumber_destinations.get(target) != target + 1:
            continue
        insert_ops = [
            op
            for op in slot_inputs.payload_subsec_ops
            if op.target_paragraph == target and op.op_type == "INSERT" and not op.target_item
        ]
        replace_ops = [
            op
            for op in slot_inputs.payload_subsec_ops
            if op.target_paragraph == target and op.op_type == "REPLACE" and not op.target_item
        ]
        if len(insert_ops) != 1 or len(replace_ops) != 1:
            continue
        exact_idx = next(
            (
                idx
                for idx, sub in enumerate(slot_inputs.amend_subs)
                if idx not in state.used_subs and _norm_num_token(sub.label or "") == str(target)
            ),
            None,
        )
        if exact_idx is None:
            continue
        if _slot_ir_is_in_place_merge(slot_inputs.amend_subs[exact_idx]):
            continue
        prior_idx = exact_idx - 1
        if prior_idx < 0 or prior_idx in state.used_subs:
            continue
        prior_label = _norm_num_token(slot_inputs.amend_subs[prior_idx].label or "")
        if (
            not prior_label.isdigit()
            or int(prior_label) in occupied_targets
            or int(prior_label) in intro_reserved_targets
            or int(prior_label) != target - 1
        ):
            continue
        state.subsec_map.assign(insert_ops[0], slot_inputs.amend_subs[prior_idx])
        state.used_subs.add(prior_idx)
        state.subsec_map.assign(replace_ops[0], slot_inputs.amend_subs[exact_idx])
        state.used_subs.add(exact_idx)
        state.binding_rule_by_op_id[id(insert_ops[0])] = "insert_before_moved_same_target_slot"
        state.binding_rule_by_op_id[id(replace_ops[0])] = "insert_before_moved_same_target_slot"
        state.binding_observations.append(
            _obs(
                "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT",
                "sparse_subsection_elaboration",
                target_paragraph=target,
                insert_payload_slot_label=str(slot_inputs.amend_subs[prior_idx].label or ""),
                replace_payload_slot_label=str(slot_inputs.amend_subs[exact_idx].label or ""),
                renumber_destination=renumber_destinations[target],
                insert_op_description=insert_ops[0].description(),
                replace_op_description=replace_ops[0].description(),
            )
        )

    renumber_source_targets = {
        int(op.target_paragraph)
        for op in slot_inputs.renumber_subsec_ops
        if (
            op.target_paragraph is not None
            and _renumber_consumes_source_payload(op)
            and _destination_subsection_label_for_renumber(op).isdigit()
            and int(_destination_subsection_label_for_renumber(op)) == int(op.target_paragraph) + 1
        )
    }
    for target in sorted(renumber_source_targets):
        insert_ops = [
            op
            for op in slot_inputs.payload_subsec_ops
            if (
                op.target_paragraph == target
                and op.op_type == "INSERT"
                and not op.target_item
                and not op.target_special
                and op not in state.subsec_map
            )
        ]
        renumber_ops = [
            op
            for op in slot_inputs.renumber_subsec_ops
            if op.target_paragraph == target and op not in state.subsec_map
        ]
        if len(insert_ops) != 1 or len(renumber_ops) != 1:
            continue
        exact_idx = next(
            (
                idx
                for idx, sub in enumerate(slot_inputs.amend_subs)
                if idx not in state.used_subs and _norm_num_token(sub.label or "") == str(target)
            ),
            None,
        )
        if exact_idx is None:
            continue
        if _slot_ir_is_in_place_merge(slot_inputs.amend_subs[exact_idx]):
            continue
        prior_idx = exact_idx - 1
        if prior_idx < 0 or prior_idx in state.used_subs:
            continue
        prior_label = _norm_num_token(slot_inputs.amend_subs[prior_idx].label or "")
        if (
            not prior_label.isdigit()
            or int(prior_label) in occupied_targets
            or int(prior_label) in intro_reserved_targets
            or int(prior_label) != target - 1
        ):
            continue
        state.subsec_map.assign(insert_ops[0], slot_inputs.amend_subs[prior_idx])
        state.used_subs.add(prior_idx)
        state.subsec_map.assign(renumber_ops[0], slot_inputs.amend_subs[exact_idx])
        state.used_subs.add(exact_idx)
        state.binding_rule_by_op_id[id(insert_ops[0])] = "insert_before_moved_same_target_slot"
        state.binding_rule_by_op_id[id(renumber_ops[0])] = "insert_before_moved_same_target_slot"
        state.binding_observations.append(
            _obs(
                "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT",
                "sparse_subsection_elaboration",
                target_paragraph=target,
                insert_payload_slot_label=str(slot_inputs.amend_subs[prior_idx].label or ""),
                replace_payload_slot_label=str(slot_inputs.amend_subs[exact_idx].label or ""),
                renumber_destination=int(_destination_subsection_label_for_renumber(renumber_ops[0])),
                insert_op_description=insert_ops[0].description(),
                replace_op_description=renumber_ops[0].description(),
            )
        )


def _assign_multi_paragraph_item_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind item-targeted ops that span several momentit to per-momentti slots.

    When one amendment changes items in two or more distinct live momentit
    (e.g. ``26 §:n 4 momentin 3 kohta ... 5 momenttiin uusi 4 ja 5 kohta``),
    the sparse payload typically carries one subsection per amended momentti, in
    source order. Pure item-number matching scrambles these bindings: two
    momentit can each contain a ``3) ...`` / ``4) ...`` item, so an op for the
    first momentti can consume the second momentti's slot (and vice versa).

    Bind by source order instead: when the item-targeted ops cover exactly N
    distinct momentit and there are exactly N still-unused payload subsections,
    pair the momentit (ascending) with the subsections (source order) and route
    every op for a momentti to its paired slot. This is only applied when N >= 2,
    so single-momentti payloads keep their existing item-matched binding.
    """
    item_ops = [
        op
        for op in slot_inputs.payload_subsec_ops
        if op.target_item and op.target_paragraph is not None and op not in state.subsec_map
    ]
    if not item_ops:
        return
    ordered_paragraphs = sorted({int(op.target_paragraph or 0) for op in item_ops})
    if len(ordered_paragraphs) < 2:
        return
    free_slots = [(idx, sub) for idx, sub in enumerate(slot_inputs.amend_subs) if idx not in state.used_subs]
    if len(free_slots) != len(ordered_paragraphs):
        return
    paragraph_to_slot = dict(zip(ordered_paragraphs, free_slots, strict=True))
    for op in item_ops:
        idx, sub = paragraph_to_slot[int(op.target_paragraph or 0)]
        state.subsec_map.assign(op, sub)
        state.used_subs.add(idx)


def _table_rows_from_content_only_subsection(subsection: IRNode) -> tuple[str, ...]:
    if subsection.kind is not IRNodeKind.SUBSECTION:
        return ()
    content_children = [
        child
        for child in subsection.children
        if child.kind is IRNodeKind.CONTENT and irnode_to_text(child).strip()
    ]
    if len(content_children) != 1:
        return ()
    if any(
        child.kind is not IRNodeKind.CONTENT and child.kind is not IRNodeKind.OMISSION
        for child in subsection.children
    ):
        return ()
    tables = [
        child
        for child in content_children[0].children
        if child.kind is IRNodeKind.TABLE
    ]
    if len(tables) != 1:
        return ()
    rows: list[str] = []
    for row in tables[0].children:
        if row.kind is not IRNodeKind.ROW:
            return ()
        text = " ".join(irnode_to_text(row).split())
        if not text:
            return ()
        rows.append(text)
    return tuple(rows)


def _item_table_row_payload_subsection(
    source_subsection: IRNode,
    op: AmendmentOp,
    row_text: str,
) -> IRNode:
    item_label = str(op.target_item or "")
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=str(op.target_paragraph or source_subsection.label or ""),
        attrs=dict(source_subsection.attrs),
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=item_label,
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=f"{item_label})"),
                    IRNode(kind=IRNodeKind.CONTENT, text=row_text),
                ),
            ),
        ),
    )


def _assign_unlabeled_table_item_row_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind same-moment item ops to unlabeled source table rows by source order."""
    item_ops = [
        op
        for op in slot_inputs.payload_subsec_ops
        if (
            op not in state.subsec_map
            and op.target_item
            and op.target_paragraph is not None
            and op.op_type in {"REPLACE", "INSERT"}
        )
    ]
    if len(item_ops) < 2:
        return

    paragraphs = sorted({int(op.target_paragraph or 0) for op in item_ops})
    for paragraph in paragraphs:
        ops_for_paragraph = [
            op for op in item_ops if int(op.target_paragraph or 0) == paragraph
        ]
        if len(ops_for_paragraph) < 2:
            continue
        free_slots = [
            (idx, sub)
            for idx, sub in enumerate(slot_inputs.amend_subs)
            if idx not in state.used_subs
        ]
        if len(free_slots) != 1:
            continue
        idx, sub = free_slots[0]
        rows = _table_rows_from_content_only_subsection(sub)
        if len(rows) != len(ops_for_paragraph):
            continue
        for op, row_text in zip(ops_for_paragraph, rows, strict=True):
            state.subsec_map.assign(
                op,
                _item_table_row_payload_subsection(sub, op, row_text),
            )
            state.binding_rule_by_op_id[id(op)] = "unlabeled_table_item_row_source_order"
            state.binding_observations.append(
                _obs(
                    "ELAB.UNLABELED_TABLE_ITEM_ROW_SOURCE_ORDER",
                    "sparse_subsection_elaboration",
                    target_paragraph=op.target_paragraph,
                    target_item=str(op.target_item or ""),
                    payload_slot_label=str(sub.label or ""),
                    op_description=op.description(),
                )
            )
        state.used_subs.add(idx)


def _assign_item_matched_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    for op in slot_inputs.payload_subsec_ops:
        if op in state.subsec_map:
            # Already bound by an earlier, more specific pass (e.g. per-momentti
            # source-order binding). Do not let item-number matching overwrite it.
            continue
        target_tok = str(op.target_item or "")
        if not target_tok:
            continue
        same_target_slot = None
        has_same_target_intro_op = any(
            intro.target_paragraph == op.target_paragraph
            and intro.target_special == "johd"
            for intro in slot_inputs.intro_subsec_ops
        )
        has_plain_subsection_op = any(
            candidate.target_paragraph is not None
            and not candidate.target_item
            and not candidate.target_special
            for candidate in slot_inputs.payload_subsec_ops
        )
        for other in slot_inputs.payload_subsec_ops:
            if other is op or other.target_paragraph != op.target_paragraph:
                continue
            shared_slot = state.subsec_map.for_op(other)
            if (
                has_same_target_intro_op
                and not has_plain_subsection_op
                and shared_slot is not None
                and _slot_ir_is_in_place_merge(shared_slot)
                and _slot_ir_has_item(shared_slot, target_tok)
            ):
                same_target_slot = shared_slot
                break
        if same_target_slot is not None:
            state.subsec_map.assign(op, same_target_slot)
            state.binding_rule_by_op_id[id(op)] = "same_target_item_slot_sharing"
            state.binding_observations.append(
                _obs(
                    "ELAB.SAME_TARGET_ITEM_SLOT_SHARING",
                    "sparse_subsection_elaboration",
                    target_paragraph=op.target_paragraph,
                    target_item=str(op.target_item or ""),
                    payload_slot_label=str(same_target_slot.label or ""),
                    op_description=op.description(),
                )
            )
            continue
        # First pass: look for the item in an unused slot.
        found = False
        for idx, sub in enumerate(slot_inputs.amend_subs):
            if idx in state.used_subs:
                continue
            if _slot_ir_has_item(sub, target_tok):
                state.subsec_map.assign(op, sub)
                state.used_subs.add(idx)
                found = True
                break
        if found:
            continue
        # Second pass: allow sharing a slot already claimed by another
        # item-targeted op.  Two ops targeting different items within the same
        # subsection (e.g. REPLACE 28 kohta + INSERT 29 kohta in a single
        # payload subsection) must map to the same slot.
        for idx, sub in enumerate(slot_inputs.amend_subs):
            if idx not in state.used_subs:
                continue
            if _slot_ir_has_item(sub, target_tok):
                state.subsec_map.assign(op, sub)
                # Slot index already in used_subs; do not add again.
                break


def _assign_shared_sparse_item_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    for op in slot_inputs.payload_subsec_ops:
        if op in state.subsec_map or not op.target_item or op.target_paragraph is None:
            continue
        for other in slot_inputs.payload_subsec_ops:
            if other is op:
                continue
            shared = state.subsec_map.for_op(other)
            if shared is not None and (_slot_ir_has_omission(shared) or _slot_ir_has_item(shared, str(op.target_item))):
                state.subsec_map.assign(op, shared)
                break


def _assign_dense_local_target_groups(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind local dense payload slots by source order across logical moments.

    Some sparse amendment excerpts label their payload subsections locally
    (`1`, `2`, ...) even though the live target paragraphs are later in the
    section (`2`, `3` or `2`, `4`). In that shape, exact numeric label matching
    is wrong: the first local slot still belongs to the first changed logical
    moment in source order.

    When all remaining amendment slots form a dense local 1..N sequence and the
    remaining logical target paragraphs do not match that sequence, bind the
    slots by source-order target paragraph instead. Intro + plain operations for
    the same target paragraph share the same assigned slot.
    """
    remaining_pairs = [
        (idx, sub)
        for idx, sub in enumerate(slot_inputs.amend_subs)
        if idx not in state.used_subs and (sub.label or "").isdigit()
    ]
    if not remaining_pairs:
        return

    remaining_labels = [int(sub.label or "0") for _, sub in remaining_pairs]
    if remaining_labels != list(range(1, len(remaining_labels) + 1)):
        return

    remaining_ops = [
        op
        for op in [*slot_inputs.payload_subsec_ops, *slot_inputs.intro_subsec_ops]
        if op not in state.subsec_map and op.target_paragraph is not None and not op.target_item
    ]
    if not remaining_ops:
        return

    target_groups: dict[int, list[AmendmentOp]] = {}
    for op in remaining_ops:
        target_groups.setdefault(int(op.target_paragraph or 0), []).append(op)
    ordered_targets = sorted(target_groups)
    if len(ordered_targets) != len(remaining_pairs):
        return

    if ordered_targets == remaining_labels:
        return

    # Source-order rebinding is only justified when all visible payload slots
    # are clearly local numbering for later live moments. If there is only one
    # slot, or any target still exactly matches its visible local label, leave
    # ownership to the later exact/trailing rules instead of consuming the
    # sparse slots here.
    if len(remaining_pairs) < 2:
        return
    if any(target <= label for target, label in zip(ordered_targets, remaining_labels, strict=True)):
        return

    for (_idx, sub), target in zip(remaining_pairs, ordered_targets, strict=True):
        for op in sorted(
            target_groups[target],
            key=lambda current: (current.target_special != "johd", current.op_type),
        ):
            state.subsec_map.assign(op, sub)
            state.binding_rule_by_op_id[id(op)] = "local_dense_subsection_numbering"
        state.used_subs.add(_idx)
    state.binding_observations.append(
        _obs(
            "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING",
            "sparse_subsection_elaboration",
            target_paragraphs=ordered_targets,
            payload_slot_labels=[str(sub.label or "") for _, sub in remaining_pairs],
            op_descriptions=[op.description() for op in remaining_ops],
        )
    )


def _assign_dense_local_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    remaining_plain_ops = [
        op
        for op in slot_inputs.payload_subsec_ops
        if op not in state.subsec_map and not op.target_item and op.target_paragraph is not None
    ]
    remaining_sub_pairs = [
        (idx, sub)
        for idx, sub in enumerate(slot_inputs.amend_subs)
        if idx not in state.used_subs and (sub.label or "").isdigit()
    ]
    if not remaining_plain_ops or len(remaining_plain_ops) != len(remaining_sub_pairs):
        return
    sorted_ops = sorted(remaining_plain_ops, key=lambda op: op.target_paragraph or 0)
    sorted_subs = sorted(remaining_sub_pairs, key=lambda pair: int(pair[1].label or "0"))
    offsets = {int(op.target_paragraph or 0) - int(sub.label or "0") for op, (_, sub) in zip(sorted_ops, sorted_subs, strict=True)}
    if len(offsets) != 1:
        return
    offset = next(iter(offsets))
    if offset <= 0:
        return
    bound_targets: List[int] = []
    bound_labels: List[str] = []
    for op, (idx, sub) in zip(sorted_ops, sorted_subs, strict=True):
        state.subsec_map.assign(op, sub)
        state.binding_rule_by_op_id[id(op)] = "local_dense_subsection_numbering"
        state.used_subs.add(idx)
        if op.target_paragraph is not None:
            bound_targets.append(int(op.target_paragraph))
        bound_labels.append(str(sub.label or ""))
    state.binding_observations.append(
        _obs(
            "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING",
            "sparse_subsection_elaboration",
            target_paragraphs=bound_targets,
            payload_slot_labels=bound_labels,
            offset=offset,
            op_descriptions=[op.description() for op in sorted_ops],
        )
    )


def _assign_exact_plain_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    plain_ops = [
        op
        for op in slot_inputs.payload_subsec_ops
        if op not in state.subsec_map and not op.target_item and op.target_paragraph is not None
    ]
    plain_ops.sort(key=lambda op: op.target_paragraph or 0)
    for pos, op in enumerate(plain_ops):
        remaining_after = len(plain_ops) - pos - 1
        all_pairs = list(enumerate(slot_inputs.amend_subs))
        exact_idx = next(
            (idx for idx, sub in all_pairs if _norm_num_token(sub.label or "") == str(op.target_paragraph)),
            None,
        )
        if exact_idx is None:
            continue
        if exact_idx not in state.used_subs:
            later_slots = sum(1 for idx, _ in all_pairs if idx > exact_idx and idx not in state.used_subs)
            if later_slots < remaining_after:
                continue
            state.used_subs.add(exact_idx)
        state.subsec_map.assign(op, slot_inputs.amend_subs[exact_idx])


def _assign_numbered_table_offset_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind legal moment targets shifted by Finlex table-as-subsection markup.

    Some Finlex XML serializes a numbered table as a ``subsection``. The
    johtolause still speaks in legal moment numbers, so a target like
    ``taulukko 11 ja 2 momentti`` maps to the XML subsection immediately after
    the table, commonly label ``3``. This rule only binds the single +1 shape
    and only for ops already carrying the explicit numbered-table witness.
    """
    for op in slot_inputs.payload_subsec_ops:
        if (
            op in state.subsec_map
            or not op.numbered_table_targets
            or op.target_paragraph is None
            or op.target_item
            or op.target_special
        ):
            continue
        target = int(op.target_paragraph)
        if any(
            idx not in state.used_subs and _norm_num_token(sub.label or "") == str(target)
            for idx, sub in enumerate(slot_inputs.amend_subs)
        ):
            continue
        shifted_label = str(target + 1)
        shifted_candidates = [
            (idx, sub)
            for idx, sub in enumerate(slot_inputs.amend_subs)
            if idx not in state.used_subs and _norm_num_token(sub.label or "") == shifted_label
        ]
        if len(shifted_candidates) != 1:
            continue
        idx, sub = shifted_candidates[0]
        state.subsec_map.assign(op, sub)
        state.used_subs.add(idx)
        state.binding_rule_by_op_id[id(op)] = "numbered_table_xml_subsection_offset"
        state.binding_observations.append(
            _obs(
                _NUMBERED_TABLE_XML_SUBSECTION_OFFSET_RULE,
                "sparse_subsection_elaboration",
                source_target_paragraph=target,
                structural_payload_label=shifted_label,
                numbered_table_targets=list(op.numbered_table_targets),
                op_description=op.description(),
            )
        )


def _assign_numbered_table_companion_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind the sole non-table slot paired with an explicit numbered-table target.

    Omission alignment can relabel an amendment-local table and its companion
    moment to live-relative structural labels.  Once the explicitly cited table
    slot has been filtered out of ``amend_subs``, a single remaining slot is the
    only payload witness for the same johtolause clause and should stay owned by
    the legal moment target rather than becoming a phantom subsection.
    """
    candidates = [
        op
        for op in slot_inputs.payload_subsec_ops
        if (
            op not in state.subsec_map
            and op.numbered_table_targets
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    remaining_slots = [
        (idx, sub)
        for idx, sub in enumerate(slot_inputs.amend_subs)
        if idx not in state.used_subs
    ]
    if len(candidates) != 1 or len(remaining_slots) != 1:
        return

    op = candidates[0]
    if any(
        idx not in state.used_subs and _norm_num_token(sub.label or "") == str(op.target_paragraph)
        for idx, sub in enumerate(slot_inputs.amend_subs)
    ):
        return

    idx, sub = remaining_slots[0]
    state.subsec_map.assign(op, sub)
    state.used_subs.add(idx)
    state.binding_rule_by_op_id[id(op)] = "numbered_table_companion_subsection_binding"
    state.binding_observations.append(
        _obs(
            "ELAB.NUMBERED_TABLE_COMPANION_SUBSECTION_BINDING",
            "sparse_subsection_elaboration",
            source_target_paragraph=int(op.target_paragraph or 0),
            structural_payload_label=str(sub.label or ""),
            numbered_table_targets=list(op.numbered_table_targets),
            op_description=op.description(),
        )
    )


def _plain_target_is_inadmissible_for_positional_fallback(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
    op: AmendmentOp,
) -> bool:
    target = op.target_paragraph
    if target is None or op.target_item or op.target_special:
        return False

    remaining_numeric_labels = [
        int(norm_label)
        for idx, sub in enumerate(slot_inputs.amend_subs)
        if idx not in state.used_subs and (norm_label := _norm_num_token(sub.label or "")).isdigit()
    ]
    if not remaining_numeric_labels or target in remaining_numeric_labels:
        return False
    if _should_bind_lone_insert_to_trailing_slot(slot_inputs, state, op, remaining_numeric_labels):
        # A lone sparse insert whose target lies beyond the visible payload
        # labels should bind to the trailing sparse slot, not steal the first
        # slot by positional fallback.
        return True
    if any(label < target for label in remaining_numeric_labels) and any(
        label > target for label in remaining_numeric_labels
    ):
        return True
    # When every remaining explicit numeric slot starts at least two labels
    # above the requested target, the numbering domain is almost certainly not a
    # sparse subsection continuation anymore. Binding `3 mom` to slot `23`
    # should stay unbound and surface as source/front-end residue.
    return min(remaining_numeric_labels) > target + 1


def _should_bind_lone_insert_to_trailing_slot(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
    op: AmendmentOp,
    remaining_numeric_labels: list[int] | None = None,
) -> bool:
    if op.op_type != "INSERT" or op.target_paragraph is None or op.target_item or op.target_special:
        return False
    remaining_plain_ops = [
        candidate
        for candidate in slot_inputs.payload_subsec_ops
        if (
            candidate not in state.subsec_map
            and candidate.target_paragraph is not None
            and not candidate.target_item
            and not candidate.target_special
        )
    ]
    if len(remaining_plain_ops) != 1 or len(slot_inputs.amend_subs) <= 1:
        return False
    labels = remaining_numeric_labels
    if labels is None:
        labels = [
            int(norm_label)
            for idx, sub in enumerate(slot_inputs.amend_subs)
            if idx not in state.used_subs and (norm_label := _norm_num_token(sub.label or "")).isdigit()
        ]
    if not labels:
        return False
    return op.target_paragraph > max(labels)


def _assign_fallback_plain_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    # Pre-compute labels of slots that belong to unassigned intro (johd) ops
    # and have an exact label match still available.  The sequential fallback
    # scan must not steal those slots — _assign_intro_slot_ops will claim them
    # later by exact label.  Without this guard a higher-numbered INSERT op
    # (e.g. INSERT mom 5) grabs the johd slot by position, leaving the johd op
    # with no content and the actual INSERT slot (label "3") unassigned.
    reserved_intro_labels: Set[str] = {
        str(op.target_paragraph)
        for op in slot_inputs.intro_subsec_ops
        if op.target_paragraph is not None
        and op not in state.subsec_map
        and any(
            idx not in state.used_subs and _norm_num_token(sub.label or "") == str(op.target_paragraph)
            for idx, sub in enumerate(slot_inputs.amend_subs)
        )
    }
    for op in slot_inputs.payload_subsec_ops:
        if op in state.subsec_map:
            state.prev_mom = op.target_paragraph
            continue
        shared = next(
            (
                state.subsec_map.for_op(other)
                for other in slot_inputs.payload_subsec_ops
                if other is not op
                and other.target_paragraph == op.target_paragraph
                and state.subsec_map.for_op(other) is not None
            ),
            None,
        )
        if shared is not None:
            state.subsec_map.assign(op, shared)
            state.prev_mom = op.target_paragraph
            continue
        candidate_sub_idx = state.sub_idx
        while candidate_sub_idx < len(slot_inputs.amend_subs) and (
            candidate_sub_idx in state.used_subs
            or _norm_num_token(slot_inputs.amend_subs[candidate_sub_idx].label or "") in reserved_intro_labels
        ):
            candidate_sub_idx += 1
        candidate_is_intro_only = (
            candidate_sub_idx < len(slot_inputs.amend_subs)
            and _slot_ir_is_intro_only_fragment(slot_inputs.amend_subs[candidate_sub_idx])
        )
        candidate_has_no_paragraph_rows = (
            candidate_sub_idx < len(slot_inputs.amend_subs)
            and not _slot_ir_has_paragraph_row(slot_inputs.amend_subs[candidate_sub_idx])
        )
        has_plain_payload_owner = any(
            candidate.target_paragraph is not None
            and not candidate.target_item
            and not candidate.target_special
            for candidate in slot_inputs.payload_subsec_ops
        )
        candidate_carries_item_row = (
            candidate_sub_idx < len(slot_inputs.amend_subs)
            and _slot_ir_carries_item_row_marker(
                slot_inputs.amend_subs[candidate_sub_idx], str(op.target_item or "")
            )
        )
        if op.target_item and not candidate_carries_item_row and (
            slot_inputs.has_omission_slots
            or candidate_is_intro_only
            or (candidate_has_no_paragraph_rows and not has_plain_payload_owner)
        ):
            # Item-level targets need item-local evidence (_assign_item_matched_slot_ops,
            # table-row binding, item-prefix binding, or same-slot sharing).  Positional
            # subsection fallback is only admissible for dense same-moment payloads;
            # in omission-sparse bodies or content-only subsection slots, an
            # intro/body fragment can otherwise silently become an item row.
            state.prev_mom = op.target_paragraph
            continue
        mom = op.target_paragraph
        if mom != state.prev_mom and state.prev_mom is not None:
            state.sub_idx += 1
        while state.sub_idx < len(slot_inputs.amend_subs) and (
            state.sub_idx in state.used_subs
            or _norm_num_token(slot_inputs.amend_subs[state.sub_idx].label or "") in reserved_intro_labels
        ):
            state.sub_idx += 1
        if _plain_target_is_inadmissible_for_positional_fallback(slot_inputs, state, op):
            state.prev_mom = mom
            continue
        if state.sub_idx < len(slot_inputs.amend_subs):
            state.subsec_map.assign(op, slot_inputs.amend_subs[state.sub_idx])
            state.used_subs.add(state.sub_idx)
        state.prev_mom = mom


def _assign_item_prefix_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    for op in slot_inputs.payload_subsec_ops:
        if op in state.subsec_map or not op.target_item:
            continue
        item_norm = leaf_label_identity_key(str(op.target_item))
        for idx, sub in enumerate(slot_inputs.amend_subs):
            if idx in state.used_subs:
                continue
            sub_text = (sub.text or " ".join(child.text or "" for child in sub.children)).strip()
            # P-item-prefix flat ``N)`` item-label predicate over IR sub text;
            # lexer-shaped. The digit/letter compound may carry an internal space
            # in the source (``8 a)``) — normalize it away before comparing to the
            # addressed item label.
            # lawvm-regex: owning_parser P-item-prefix flat ``N)`` item-label
            m = re.match(r"^(\d+\s*[a-zA-Z]*)\)", sub_text)
            if m and leaf_label_identity_key("".join(m.group(1).split())) == item_norm:
                state.subsec_map.assign(op, sub)
                state.used_subs.add(idx)
                break


def _assign_highest_insert_slot_op(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    if not slot_inputs.amend_subs:
        return
    insert_ops = [
        op
        for op in slot_inputs.payload_subsec_ops
        if op.op_type == "INSERT" and op.target_paragraph and not op.target_item
    ]
    if not insert_ops:
        return
    highest = max(insert_ops, key=lambda op: op.target_paragraph or 0)
    if highest not in state.subsec_map:
        if _plain_target_is_inadmissible_for_positional_fallback(slot_inputs, state, highest):
            if not _should_bind_lone_insert_to_trailing_slot(slot_inputs, state, highest):
                return
            last_unused_idx = next(
                (idx for idx in range(len(slot_inputs.amend_subs) - 1, -1, -1) if idx not in state.used_subs),
                None,
            )
            if last_unused_idx is None:
                return
            state.subsec_map.assign(highest, slot_inputs.amend_subs[last_unused_idx])
            state.binding_rule_by_op_id[id(highest)] = "trailing_sparse_insert_binding"
            state.used_subs.add(last_unused_idx)
            state.binding_observations.append(
                _obs(
                    "ELAB.TRAILING_SPARSE_INSERT_BINDING",
                    "sparse_subsection_elaboration",
                    target_paragraph=int(highest.target_paragraph or 0),
                    payload_slot_label=str(slot_inputs.amend_subs[last_unused_idx].label or ""),
                    op_description=highest.description(),
                )
            )
            return
        # Positional fallback to the trailing payload slot. Only bind when that
        # slot has not already been consumed by an earlier op (e.g. a REPLACE
        # that claimed the lone sparse paragraph). Reusing a consumed slot
        # fabricates duplicate content: a `muutetaan N momentti, lisätään uusi
        # M momentti` group whose published body carries a single paragraph
        # would otherwise emit that paragraph twice (once for the replace, once
        # for the insert). Leave the insert unbound so it surfaces as
        # missing-payload residue instead.
        last_idx = len(slot_inputs.amend_subs) - 1
        if last_idx in state.used_subs:
            return
        state.subsec_map.assign(highest, slot_inputs.amend_subs[last_idx])
        state.used_subs.add(last_idx)


def _assign_intro_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    intro_ops = [op for op in slot_inputs.intro_subsec_ops if op.target_paragraph is not None]
    if not intro_ops:
        return

    # Prefer exact labels before positional fallback. Dense-local numbering
    # cases have already been assigned by _assign_dense_local_target_groups; if
    # target labels and payload labels really match, source order is not
    # authority to move a johdantokappale to another moment.
    mixed_intro_group = len(intro_ops) > 1 or bool(slot_inputs.payload_subsec_ops)

    for op in intro_ops:
        if op.target_paragraph is None:
            continue
        shared = next(
            (
                state.subsec_map.for_op(other)
                for other in slot_inputs.payload_subsec_ops
                if other.target_paragraph == op.target_paragraph and state.subsec_map.for_op(other) is not None
            ),
            None,
        )
        if shared is not None:
            state.subsec_map.assign(op, shared)
            continue
        exact_idx = next(
            (
                idx
                for idx, sub in enumerate(slot_inputs.amend_subs)
                if idx not in state.used_subs and _norm_num_token(sub.label or "") == str(op.target_paragraph)
            ),
            None,
        )
        if exact_idx is not None:
            state.subsec_map.assign(op, slot_inputs.amend_subs[exact_idx])
            state.used_subs.add(exact_idx)
            continue
        if mixed_intro_group:
            for idx, sub in enumerate(slot_inputs.amend_subs):
                if idx in state.used_subs:
                    continue
                state.subsec_map.assign(op, sub)
                state.used_subs.add(idx)
                break
            continue


def _assign_remaining_insert_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind leftover insert ops to leftover payload slots when counts align.

    Some sparse amendment bodies carry a mixed replace+insert pair where the
    leading payload subsection matches the replace target and the trailing
    payload subsection is the inserted moment. Earlier assignment passes can
    resolve the replace but leave the insert unbound because its normalized
    label no longer matches the sparse payload order. When the remaining
    unbound INSERT ops and unused payload subsections line up 1:1, keep the
    payload instead of classifying the insert as phantom.
    """
    remaining_inserts = [
        op
        for op in slot_inputs.payload_subsec_ops
        if (
            op.op_type == "INSERT"
            and op not in state.subsec_map
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    remaining_slots = [(idx, sub) for idx, sub in enumerate(slot_inputs.amend_subs) if idx not in state.used_subs]
    if not remaining_inserts or len(remaining_inserts) != len(remaining_slots):
        return

    remaining_inserts.sort(key=lambda op: op.target_paragraph or 0)
    remaining_slots.sort(
        key=lambda pair: (
            int(_norm_num_token(pair[1].label or "0") or "0"),
            pair[0],
        )
    )
    for op, (idx, sub) in zip(remaining_inserts, remaining_slots, strict=True):
        state.subsec_map.assign(op, sub)
        state.used_subs.add(idx)


def _destination_subsection_label_for_renumber(op: AmendmentOp) -> str:
    if op.op_type != "RENUMBER" or op.lo is None or op.lo.destination is None:
        return ""
    return _norm_num_token(
        next((label for kind, label in reversed(op.lo.destination.path) if kind == "subsection"), "")
    )


def _renumber_consumes_source_payload(op: AmendmentOp) -> bool:
    return "jolloin_moment_renumber_supplement" not in op.extraction_provenance_tags


def _assign_renumber_destination_slot_ops(
    slot_inputs: SubsectionSlotInputs,
    state: SubsectionSlotAssignmentState,
) -> None:
    """Bind carried source payload slots to explicit subsection renumber destinations."""
    for op in slot_inputs.renumber_subsec_ops:
        if (
            op in state.subsec_map
            or op.target_paragraph is None
            or op.target_item
            or op.target_special
            or not _renumber_consumes_source_payload(op)
        ):
            continue
        destination_label = _destination_subsection_label_for_renumber(op)
        if not destination_label:
            continue
        candidates = [
            (idx, sub)
            for idx, sub in enumerate(slot_inputs.amend_subs)
            if idx not in state.used_subs and _norm_num_token(sub.label or "") == destination_label
        ]
        if len(candidates) != 1:
            continue
        idx, sub = candidates[0]
        state.subsec_map.assign(op, sub)
        state.used_subs.add(idx)
        state.binding_rule_by_op_id[id(op)] = "renumber_destination_payload_slot"
        state.binding_observations.append(
            _obs(
                "ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT",
                "sparse_subsection_elaboration",
                source_target_paragraph=int(op.target_paragraph),
                destination_paragraph=int(destination_label),
                payload_slot_label=str(sub.label or ""),
                op_description=op.description(),
            )
        )


def _normalize_assigned_intro_subparagraphs(subsec_map: SubsectionSlotMap) -> None:
    for op_id, sub in list(subsec_map.items()):
        if not any(child.kind is IRNodeKind.PARAGRAPH for child in sub.children):
            continue
        new_children: List[IRNode] = []
        skip_indices: Set[int] = set()
        for i, child in enumerate(sub.children):
            if i in skip_indices:
                continue
            if (
                child.kind is IRNodeKind.PARAGRAPH
                and child.label
                and child.label.isdigit()
                and irnode_to_text(child).strip().endswith(":")
            ):
                to_reparent = []
                for j in range(i + 1, len(sub.children)):
                    sibling = sub.children[j]
                    if sibling.kind is IRNodeKind.PARAGRAPH and sibling.label and not sibling.label.isdigit():
                        to_reparent.append(sibling)
                        skip_indices.add(j)
                    elif sibling.kind is IRNodeKind.OMISSION:
                        continue
                    else:
                        break
                if to_reparent:
                    new_subparagraphs = [
                        IRNode(
                            kind=IRNodeKind.SUBPARAGRAPH,
                            label=sibling.label,
                            text=sibling.text,
                            attrs=dict(sibling.attrs),
                            children=tuple(sibling.children),
                        )
                        for sibling in to_reparent
                    ]
                    new_children.append(
                        IRNode(
                            kind=child.kind,
                            label=child.label,
                            text=child.text,
                            attrs=dict(child.attrs),
                            children=tuple(child.children) + tuple(new_subparagraphs),
                        )
                    )
                else:
                    new_children.append(child)
            else:
                new_children.append(child)
        if len(new_children) != len(sub.children):
            subsec_map[op_id] = _tops._with_children(sub, new_children)


def prepare_payload_surface(
    ctx: PayloadElaborationContext,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
    profile: "ReplayProfile",
    strict_profile: Optional["StrictProfile"],
) -> Optional[IRNode]:
    """Expand live-state-dependent omission payloads before group filtering.

    Uses ``PayloadElaborationContext`` instead of raw ``master``.  All live
    state access goes through ``ctx.live_node`` (local subtree) or
    ``ctx.parent_node`` (for fallback section lookup in omission resolution).
    """
    target_unit_kind = ctx.target_unit_kind
    table_labels = tuple(
        dict.fromkeys(
            label
            for op in group_ops
            for label in op.numbered_table_targets
            if label
        )
    )
    if table_labels:
        has_non_table_child_targets = any(
            op.target_paragraph is not None or op.target_item or op.target_special
            for op in group_ops
        )
        if not has_non_table_child_targets:
            table_merge = merge_numbered_table_targets_into_live_section(
                ctx.live_node,
                muutos_ir,
                table_labels,
            )
            if table_merge.rewritten:
                return table_merge.node
    muutos_ir = _prepare_sparse_subsection_payload_ir(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    muutos_ir = _fold_continuation_row_subsections_into_previous_subsection(
        ctx,
        target_unit_kind,
        muutos_ir,
    )
    if _has_historical_top_level_kohta_subsection_ops(group_ops):
        return _split_historical_top_level_kohta_payload_ir(target_unit_kind, group_ops, muutos_ir)
    omission_allowed = strict_profile is None or strict_profile.allows_omission_expansion
    if _single_plain_insert_sparse_payload_is_self_contained(target_unit_kind, group_ops, muutos_ir):
        omission_allowed = False
    if omission_allowed:
        return _pre_resolve_omissions(
            ctx,
            muutos_ir,
            target_unit_kind,
            ctx.target_norm,
            ctx.target_chapter,
            group_ops,
            profile,
        )
    return muutos_ir


def _single_plain_insert_sparse_payload_is_self_contained(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> bool:
    """Return True when omission expansion would only import carried live slots.

    A source body shaped as ``omission + one subsection`` under a single explicit
    ``lisätään uusi N momentti`` already owns the inserted slot body.  Expanding
    the omission against live state before sparse-slot assignment can admit a
    stale same-numbered live subsection and let exact-label binding pick that
    carried text instead of the source-owned insert payload.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return False
    plain_insert_ops = [
        op
        for op in group_ops
        if (
            op.op_type == "INSERT"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(group_ops) != 1 or len(plain_insert_ops) != 1:
        return False
    slot_kinds = [
        child.kind
        for child in muutos_ir.children
        if child.kind in {IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}
    ]
    return slot_kinds.count(IRNodeKind.SUBSECTION) == 1 and IRNodeKind.OMISSION in slot_kinds


def _prepare_sparse_subsection_payload_ir(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Prepare sparse subsection payload shapes before omission expansion.

    This groups the current intro/list subsection folds behind one explicit
    sparse-payload preparation boundary so later typed elaboration can replace
    the individual shape fixes more systematically.
    """
    muutos_ir = _collapse_intro_list_subsections_inside_section_ir(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    muutos_ir = _fold_intro_list_continuation_subsection_before_omission(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    muutos_ir = _fold_split_omission_subsection_prefix_into_following_intro_list(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    return _fold_text_table_row_subsections_into_target_subsection(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )


def _fold_intro_list_continuation_subsection_before_omission(
    target_unit_kind: TargetUnitKind,
    group_ops: Optional[List[AmendmentOp]],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Fold content-only continuation subsections into a preceding intro-list subsection.

    Some sparse section payloads serialize one changed moment as:
    - subsection N with intro + numbered paragraphs
    - subsection N+1 with only continuation content
    - omission
    - later changed subsection(s)

    The continuation block is not a separate live moment; it completes the
    preceding changed subsection. Folding it here prevents sparse omission
    alignment from mis-binding later `REPLACE <mom>` ops to the continuation
    payload.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir

    plain_subsection_targets = sorted(
        {
            int(op.target_paragraph)
            for op in (group_ops or [])
            if (op.target_paragraph is not None and not op.target_item and op.op_type in ("REPLACE", "INSERT"))
        }
    )
    subsection_payload_count = sum(1 for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION)
    item_subsection_targets = {
        int(op.target_paragraph)
        for op in (group_ops or [])
        if (op.target_paragraph is not None and bool(op.target_item) and op.op_type in ("REPLACE", "INSERT"))
    }

    children = list(muutos_ir.children)
    new_children: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind is not IRNodeKind.SUBSECTION or i + 2 >= len(children):
            new_children.append(child)
            i += 1
            continue

        continuation = children[i + 1]
        continuation_text = (
            _content_only_non_item_subsection_text(continuation) if continuation.kind is IRNodeKind.SUBSECTION else None
        )
        later_subsection_exists = any(later.kind is IRNodeKind.SUBSECTION for later in children[i + 2 :])
        omission_follows = children[i + 2].kind is IRNodeKind.OMISSION
        if continuation_text is None:
            new_children.append(child)
            i += 1
            continue
        intro_list_shape = _is_intro_list_subsection_ir(child)
        intro_single_item_tail_shape = _is_intro_single_item_subsection_ir(child) and continuation_text[:1].islower()
        if intro_single_item_tail_shape and not _single_item_tail_matches_explicit_target(child, group_ops or []):
            intro_single_item_tail_shape = False
        if omission_follows:
            if not intro_list_shape and not intro_single_item_tail_shape:
                new_children.append(child)
                i += 1
                continue
        else:
            if not later_subsection_exists or not intro_single_item_tail_shape:
                new_children.append(child)
                i += 1
                continue
        if not intro_list_shape and not intro_single_item_tail_shape:
            new_children.append(child)
            i += 1
            continue
        # Do not fold if the continuation subsection is an explicit REPLACE/INSERT
        # target in its own right.  The fold is only safe for encoding artifacts
        # where the continuation prose completes the *same* moment as `child`.
        # When the PEG parser already emitted an op for the continuation's
        # positional label, the subsection is a real independent moment and must
        # not be absorbed.
        if continuation.label is not None and continuation.label.isdigit():
            continuation_is_tail_artifact = (
                later_subsection_exists
                and continuation_text[:1].islower()
            )
            if len(plain_subsection_targets) >= 2 and subsection_payload_count == len(plain_subsection_targets):
                new_children.append(child)
                i += 1
                continue
            if int(continuation.label) in plain_subsection_targets and not continuation_is_tail_artifact:
                new_children.append(child)
                i += 1
                continue
        # Preserve a real later target moment when the same sparse section body
        # mixes explicit item targets under one moment and one plain target
        # under a different moment. Folding the continuation would collapse the
        # plain target's prose into the item-targeted moment before slot
        # alignment (for example `1 momentin kohdat` + `4 momentti`).
        if (
            omission_follows
            and len(plain_subsection_targets) == 1
            and item_subsection_targets
            and item_subsection_targets != {plain_subsection_targets[0]}
        ):
            new_children.append(child)
            i += 1
            continue
        if omission_follows and not any(later.kind is IRNodeKind.SUBSECTION for later in children[i + 3 :]):
            # Without a later changed subsection, this shape can be a real next
            # moment followed by a terminal omission rather than a continuation
            # block. Do not erase that live moment.
            if len(plain_subsection_targets) != 1:
                child_label = _norm_num_token(child.label or "")
                item_targets_same_subsection = (
                    intro_single_item_tail_shape
                    and child_label.isdigit()
                    and item_subsection_targets == {int(child_label)}
                )
                if not item_targets_same_subsection:
                    new_children.append(child)
                    i += 1
                    continue

        if (
            intro_single_item_tail_shape
            and _single_item_tail_paragraph_label(child) != "1"
            and continuation_text.rstrip().endswith(";")
        ):
            merged_children = _append_tail_to_single_item_subsection(child, continuation_text)
        else:
            merged_children = list(child.children) + [IRNode(kind=IRNodeKind.CONTENT, text=continuation_text)]
        new_children.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(merged_children),
            )
        )
        changed = True
        i += 2
        continue

    if not changed:
        return muutos_ir
    return _tops._with_children(muutos_ir, new_children)


def _split_fused_restarted_subsection_across_consecutive_replaces(
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> Tuple[Optional[IRNode], bool]:
    """Split one subsection when consecutive changed moments restart numbering inside it.

    Some malformed amendment payloads serialize two consecutive changed moments
    as one subsection:
    - intro + numbered paragraphs for moment N
    - a content-only paragraph that is actually the intro of moment N+1
    - a restarted numbered paragraph sequence 1), 2), ...
    - optional terminal omission

    Normalize that to two subsections before slot assignment so consecutive
    `REPLACE <mom>` ops do not collapse onto the first payload only.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, False

    amend_subs = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subs) != 1:
        return muutos_ir, False

    replace_ops = sorted(
        [
            op
            for op in group_ops
            if (
                op.op_type == "REPLACE"
                and op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        ],
        key=lambda op: op.target_paragraph or 0,
    )
    if len(replace_ops) < 2:
        return muutos_ir, False

    targets = [int(op.target_paragraph or 0) for op in replace_ops]
    if any(curr != prev + 1 for prev, curr in pairwise(targets)):
        return muutos_ir, False

    merged_sub = amend_subs[0]
    children = list(merged_sub.children)

    def _is_numbered_paragraph(node: IRNode) -> bool:
        return (
            node.kind is IRNodeKind.PARAGRAPH and node.label is not None and any(grand.kind is IRNodeKind.NUM for grand in node.children)
        )

    split_idx: Optional[int] = None
    for idx in range(1, len(children) - 1):
        child = children[idx]
        if child.kind is not IRNodeKind.PARAGRAPH or _is_numbered_paragraph(child):
            continue
        intro_text = irnode_to_text(child).strip()
        if not intro_text or not intro_text.endswith(":"):
            continue
        prev = children[idx - 1]
        nxt = children[idx + 1]
        if not _is_numbered_paragraph(prev) or _norm_num_token(prev.label or "") != "2":
            continue
        if not _is_numbered_paragraph(nxt) or _norm_num_token(nxt.label or "") != "1":
            continue
        prior_numbered = [c for c in children[:idx] if _is_numbered_paragraph(c)]
        restarted_numbered = [c for c in children[idx + 1 :] if c.kind is not IRNodeKind.OMISSION]
        if len(prior_numbered) < 2 or not restarted_numbered:
            continue
        if not all(_is_numbered_paragraph(c) for c in restarted_numbered):
            continue
        restarted_labels = [_norm_num_token(c.label or "") for c in restarted_numbered]
        if len(restarted_labels) < 2 or restarted_labels[:2] != ["1", "2"]:
            continue
        split_idx = idx
        break

    if split_idx is None:
        return muutos_ir, False

    tail_intro = irnode_to_text(children[split_idx]).strip()
    first_children = children[:split_idx]
    tail_children = [IRNode(kind=IRNodeKind.INTRO, text=tail_intro)] + [child for child in children[split_idx + 1 :]]
    replacement_subs = [
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(targets[0]),
            attrs=dict(merged_sub.attrs),
            children=tuple(first_children),
        ),
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(targets[1]),
            attrs=dict(merged_sub.attrs),
            children=tuple(tail_children),
        ),
    ]
    new_children: List[IRNode] = []
    replaced = False
    for child in muutos_ir.children:
        if child is merged_sub:
            new_children.extend(replacement_subs)
            replaced = True
            continue
        new_children.append(child)
    if not replaced:
        return muutos_ir, False
    return _tops._with_children(muutos_ir, new_children), True


def _split_flattened_insert_subsection_tail(
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> Tuple[Optional[IRNode], bool]:
    """Split an explicit multi-moment INSERT payload flattened into one subsection.

    Some amendment XML serializes ``uusi 2 ja 3 momentti`` as one subsection
    containing an intro/list for the first new moment and a terminal wrap-up
    paragraph for the second. The parsed targets already identify the legal
    moments; this rule only reshapes the source payload to those explicit
    targets before the item-like fallback can reclassify them as kohta ops.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, False

    insert_ops = sorted(
        [
            op
            for op in group_ops
            if (
                op.op_type == "INSERT"
                and op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        ],
        key=lambda op: op.target_paragraph or 0,
    )
    if len(insert_ops) != 2:
        return muutos_ir, False
    targets = [int(op.target_paragraph or 0) for op in insert_ops]
    if targets[1] != targets[0] + 1:
        return muutos_ir, False

    amend_subs = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subs) != 1 or not any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children):
        return muutos_ir, False

    merged_sub = amend_subs[0]
    children = list(merged_sub.children)
    paragraph_indexes = [idx for idx, child in enumerate(children) if child.kind is IRNodeKind.PARAGRAPH]
    if len(paragraph_indexes) < 2:
        return muutos_ir, False
    para_labels = [_norm_num_token(children[idx].label or "") for idx in paragraph_indexes]
    if para_labels[:2] != ["1", "2"]:
        return muutos_ir, False

    split_idx = paragraph_indexes[-1] + 1
    first_children = children[:split_idx]
    tail_children = [child for child in children[split_idx:] if child.kind is not IRNodeKind.OMISSION]
    if not first_children or not tail_children:
        return muutos_ir, False
    if any(child.kind is IRNodeKind.PARAGRAPH for child in tail_children):
        return muutos_ir, False

    replacement_subs = [
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(targets[0]),
            attrs=dict(merged_sub.attrs),
            children=tuple(first_children),
        ),
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(targets[1]),
            attrs=dict(merged_sub.attrs),
            children=tuple(tail_children),
        ),
    ]
    new_children: List[IRNode] = []
    replaced = False
    for child in muutos_ir.children:
        if child is merged_sub:
            new_children.extend(replacement_subs)
            replaced = True
            continue
        new_children.append(child)
    if not replaced:
        return muutos_ir, False
    return _tops._with_children(muutos_ir, new_children), True


def _fold_single_insert_subsection_list_tail(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> tuple[Optional[IRNode], dict[str, object] | None]:
    """Fold a sibling tail into the one explicitly inserted list-shaped moment.

    Some historical Finlex XML serializes ``lisätään ... uusi N momentti`` as
    two post-omission subsections: the first carries an intro/list, while the
    second is a content-only condition or wrap-up for the last list item.  The
    preamble still claims exactly one new moment, so the tail is source-owned
    by that moment; expanding it into ``N+1 momentti`` invents legal structure.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, None

    plain_insert_ops = [
        op
        for op in group_ops
        if (op.op_type == "INSERT" and op.target_paragraph is not None and not op.target_item and not op.target_special)
    ]
    if len(plain_insert_ops) != 1:
        return muutos_ir, None

    master_sec = ctx.live_node
    if master_sec is None or master_sec.kind is not IRNodeKind.SECTION:
        return muutos_ir, None
    live_subsecs = [child for child in master_sec.children if child.kind == IRNodeKind.SUBSECTION]
    target = int(plain_insert_ops[0].target_paragraph or 0)
    if target != len(live_subsecs) + 1:
        return muutos_ir, None

    omission_idx = next(
        (idx for idx, child in enumerate(muutos_ir.children) if child.kind == IRNodeKind.OMISSION),
        None,
    )
    if omission_idx is None:
        return muutos_ir, None
    trailing_pairs = [
        (idx, child)
        for idx, child in enumerate(muutos_ir.children[omission_idx + 1 :], start=omission_idx + 1)
        if child.kind is IRNodeKind.SUBSECTION
    ]
    if len(trailing_pairs) != 2:
        return muutos_ir, None

    (prefix_idx, prefix_sub), (tail_idx, tail_sub) = trailing_pairs
    if tail_idx != prefix_idx + 1:
        return muutos_ir, None
    trailing_labels = [(sub.label or "").strip() for _, sub in trailing_pairs]
    if any(label and not label.isdigit() for label in trailing_labels):
        return muutos_ir, None
    if any(trailing_labels):
        dense_local = ["1", "2"]
        dense_target = [str(target), str(target + 1)]
        if trailing_labels not in (dense_local, dense_target):
            return muutos_ir, None

    if not _is_intro_list_subsection_ir(prefix_sub):
        return muutos_ir, None
    tail_text = _content_only_non_item_subsection_text(tail_sub)
    if tail_text is None:
        return muutos_ir, None

    folded = _with_payload_normalization_rule(
        IRNode(
            kind=prefix_sub.kind,
            label=str(target),
            text=prefix_sub.text,
            attrs=dict(prefix_sub.attrs),
            children=tuple(prefix_sub.children)
            + (IRNode(kind=IRNodeKind.WRAP_UP, text=tail_text),),
        ),
        _FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE,
    )
    new_children = [
        folded if idx == prefix_idx else child
        for idx, child in enumerate(muutos_ir.children)
        if idx != tail_idx
    ]
    detail: dict[str, object] = {
        "target_paragraph": target,
        "prefix_payload_slot_label": str(prefix_sub.label or ""),
        "tail_payload_slot_label": str(tail_sub.label or ""),
        "tail_text_chars": len(tail_text),
        "rule": _FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE,
    }
    return _tops._with_children(muutos_ir, new_children), detail


@dataclass(frozen=True, slots=True)
class FlattenedListRow:
    """One content-only subsection row from a flattened list payload."""

    label: str
    text: str
    is_lettered: bool


@dataclass(frozen=True, slots=True)
class HeadingTaggedSubsectionPayloadResult:
    """Result for repairing a subsection body mis-tagged as a section heading."""

    muutos_ir: Optional[IRNode]
    rewritten: bool = False
    target_paragraph: int | None = None
    heading_text_chars: int = 0


@dataclass(frozen=True, slots=True)
class LeadingSubsectionHeadingPayloadResult:
    """Result for repairing a section heading mis-tagged as first subsection."""

    muutos_ir: Optional[IRNode]
    rewritten: bool = False
    heading_text_chars: int = 0
    shifted_subsection_count: int = 0


_PAYLOAD_NORMALIZATION_RULE_ATTR = "lawvm_payload_normalization_rule"
_COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST_RULE = "ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST"
_SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE = "ELAB.SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL"
_FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE = "ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS"
_SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION_RULE = "ELAB.SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION"
_FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE = "ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL"
_HEADING_TAGGED_SUBSECTION_PAYLOAD_RULE = "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD"
_LEADING_SUBSECTION_HEADING_PAYLOAD_RULE = "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
_TEXT_TABLE_ROW_CONTINUATION_RULE = "ELAB.TEXT_TABLE_ROW_CONTINUATION"


def _flattened_list_row_from_text(text: str) -> Optional[FlattenedListRow]:
    flat_text = " ".join(text.split())
    # lawvm-regex: owning_parser P-item-prefix flattened-list-row recognizer over normalized local text; lexer-shaped
    m = re.match(r"^(\d+|[a-z])\s*[\).]\s*(.+)$", flat_text, flags=re.I)
    if m is None:
        return None
    raw_label = m.group(1).lower()
    label = _norm_num_token(raw_label)
    text = m.group(2).strip()
    return FlattenedListRow(label=label, text=text, is_lettered=not label.isdigit())


def _flattened_list_rows_from_subsection_ir(sub_ir: IRNode) -> Optional[list[FlattenedListRow]]:
    if any(c.kind is IRNodeKind.SUBSECTION for c in sub_ir.children):
        return None
    if any(c.kind is IRNodeKind.PARAGRAPH for c in sub_ir.children):
        rows: list[FlattenedListRow] = []
        for child in sub_ir.children:
            if child.kind not in {IRNodeKind.CONTENT, IRNodeKind.INTRO, IRNodeKind.PARAGRAPH}:
                return None
            row = _flattened_list_row_from_text(irnode_to_text(child))
            if row is None:
                return None
            rows.append(row)
        return rows or None
    row = _flattened_list_row_from_text(irnode_to_text(sub_ir))
    return [row] if row is not None else None


def _flattened_list_row_from_subsection_ir(sub_ir: IRNode) -> Optional[FlattenedListRow]:
    rows = _flattened_list_rows_from_subsection_ir(sub_ir)
    return rows[0] if rows is not None and len(rows) == 1 else None


def _paragraph_from_flattened_list_row(row: FlattenedListRow) -> IRNode:
    child_kind = IRNodeKind.INTRO if row.text.endswith(":") else IRNodeKind.CONTENT
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=row.label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{row.label})"),
            IRNode(kind=child_kind, text=row.text),
        ),
    )


def _subparagraph_from_flattened_list_row(row: FlattenedListRow) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBPARAGRAPH,
        label=row.label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{row.label})"),
            IRNode(kind=IRNodeKind.CONTENT, text=row.text),
        ),
    )


def _collapse_flattened_list_rows(rows: List[FlattenedListRow]) -> Optional[List[IRNode]]:
    paragraphs: List[IRNode] = []
    numeric_labels: List[str] = []
    current_numeric_idx: Optional[int] = None
    current_accepts_subitems = False

    for row in rows:
        if not row.is_lettered:
            numeric_labels.append(row.label)
            paragraphs.append(_paragraph_from_flattened_list_row(row))
            current_numeric_idx = len(paragraphs) - 1
            current_accepts_subitems = row.text.endswith(":")
            continue
        if current_numeric_idx is None or not current_accepts_subitems:
            return None
        current = paragraphs[current_numeric_idx]
        paragraphs[current_numeric_idx] = IRNode(
            kind=current.kind,
            label=current.label,
            text=current.text,
            attrs=dict(current.attrs),
            children=tuple(current.children) + (_subparagraph_from_flattened_list_row(row),),
        )

    if len(numeric_labels) < 2 or numeric_labels[:2] != ["1", "2"]:
        return None
    return paragraphs


def _append_subparagraph_to_paragraph(paragraph: IRNode, row: FlattenedListRow) -> IRNode:
    return IRNode(
        kind=paragraph.kind,
        label=paragraph.label,
        text=paragraph.text,
        attrs=dict(paragraph.attrs),
        children=tuple(paragraph.children) + (_subparagraph_from_flattened_list_row(row),),
    )


def _collapse_flattened_list_rows_after_existing_paragraphs(
    existing_paragraphs: List[IRNode],
    rows: List[FlattenedListRow],
) -> Optional[List[IRNode]]:
    paragraphs = list(existing_paragraphs)
    numeric_labels = [
        _norm_num_token(paragraph.label or "")
        for paragraph in paragraphs
        if paragraph.label and _norm_num_token(paragraph.label or "").isdigit()
    ]
    current_numeric_idx: Optional[int] = None
    current_accepts_subitems = False
    if paragraphs:
        for idx in range(len(paragraphs) - 1, -1, -1):
            label = _norm_num_token(paragraphs[idx].label or "")
            if label.isdigit():
                current_numeric_idx = idx
                current_accepts_subitems = irnode_to_text(paragraphs[idx]).strip().endswith(":")
                break

    for row in rows:
        if not row.is_lettered:
            numeric_labels.append(row.label)
            paragraphs.append(_paragraph_from_flattened_list_row(row))
            current_numeric_idx = len(paragraphs) - 1
            current_accepts_subitems = row.text.endswith(":")
            continue
        if current_numeric_idx is None or not current_accepts_subitems:
            return None
        paragraphs[current_numeric_idx] = _append_subparagraph_to_paragraph(
            paragraphs[current_numeric_idx],
            row,
        )

    if not numeric_labels or numeric_labels[0] != "1":
        return None
    if numeric_labels != [str(idx) for idx in range(1, len(numeric_labels) + 1)]:
        return None
    return paragraphs


def _with_payload_normalization_rule(node: IRNode, rule_id: str) -> IRNode:
    existing = node.attrs.get(_PAYLOAD_NORMALIZATION_RULE_ATTR, ())
    rules = tuple(existing) if isinstance(existing, tuple) else ((str(existing),) if existing else ())
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs={**dict(node.attrs), _PAYLOAD_NORMALIZATION_RULE_ATTR: tuple(dict.fromkeys((*rules, rule_id)))},
        children=tuple(node.children),
    )


def _normalize_heading_tagged_subsection_payload(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> HeadingTaggedSubsectionPayloadResult:
    """Admit a body-bearing section heading as an explicitly targeted subsection.

    Historical Finlex XML can encode a changed ``N § M momentti`` body as:

    ``section(num, heading(text), omission)``

    even though the johtolause explicitly targets the subsection, not the
    section heading facet.  This rule is deliberately narrow: it fires only
    when there is exactly one plain subsection replacement, no same-group
    heading-facet op, an omission shell, no subsection slots already, and the
    only body-bearing source child is one heading node.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)
    if any(child.kind is IRNodeKind.SUBSECTION for child in muutos_ir.children):
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)
    if not any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children):
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)
    if any(op.target_special in {"otsikko", "otsikko_edella"} for op in group_ops):
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)

    subsection_ops = [
        op
        for op in group_ops
        if (
            op.op_type == "REPLACE"
            and op.target_unit_kind == "section"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(subsection_ops) != 1:
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)

    allowed_shell_kinds = {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}
    if any(child.kind not in allowed_shell_kinds for child in muutos_ir.children):
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)

    heading_children = [
        child
        for child in muutos_ir.children
        if child.kind is IRNodeKind.HEADING and irnode_to_text(child).strip()
    ]
    if len(heading_children) != 1:
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)

    target_paragraph = subsection_ops[0].target_paragraph
    heading = heading_children[0]
    heading_text = " ".join(irnode_to_text(heading).split())
    subsection = _with_payload_normalization_rule(
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(target_paragraph),
            children=(IRNode(kind=IRNodeKind.CONTENT, text=heading_text),),
        ),
        _HEADING_TAGGED_SUBSECTION_PAYLOAD_RULE,
    )
    new_children: list[IRNode] = []
    replaced_heading = False
    for child in muutos_ir.children:
        if child is heading:
            new_children.append(subsection)
            replaced_heading = True
        else:
            new_children.append(child)
    if not replaced_heading:
        return HeadingTaggedSubsectionPayloadResult(muutos_ir=muutos_ir)
    return HeadingTaggedSubsectionPayloadResult(
        muutos_ir=_tops._with_children(muutos_ir, new_children),
        rewritten=True,
        target_paragraph=target_paragraph,
        heading_text_chars=len(heading_text),
    )


def _plain_content_only_subsection_text(subsection: IRNode) -> str | None:
    if subsection.kind is not IRNodeKind.SUBSECTION:
        return None
    if len(subsection.children) != 1:
        return None
    child = subsection.children[0]
    if child.kind is not IRNodeKind.CONTENT:
        return None
    if child.children:
        return None
    text = " ".join(irnode_to_text(child).split())
    return text or None


def _plain_content_only_subsection_has_inline_markup(subsection: IRNode) -> bool:
    if subsection.kind is not IRNodeKind.SUBSECTION or len(subsection.children) != 1:
        return False
    child = subsection.children[0]
    return bool(child.attrs.get("lawvm_source_inline_tags"))


def _looks_like_orphan_section_heading_text(text: str) -> bool:
    if len(text) > 160:
        return False
    if text.endswith((".", ":", ";", "!", "?")):
        return False
    words = text.split()
    return 1 <= len(words) <= 18


def _looks_like_body_subsection_text(text: str) -> bool:
    return bool(text) and text.endswith((".", "!", "?"))


def _normalize_leading_subsection_heading_payload(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> LeadingSubsectionHeadingPayloadResult:
    """Promote a whole-section payload's orphan first subsection into a heading.

    Some Finlex amendment XML encodes a newly inserted section title as the
    first content-only subsection, followed by the real first body subsection.
    The consolidated oracle projects the first child as the section heading.
    This rule is whole-section only and refuses mixed same-group descendant or
    heading-facet authority, so it cannot absorb an explicit subsection target.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if any(op.target_special in {"otsikko", "otsikko_edella"} for op in group_ops):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    whole_section_ops = [
        op
        for op in group_ops
        if (
            op.op_type in {"INSERT", "REPLACE"}
            and op.target_unit_kind == "section"
            and op.target_paragraph is None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(whole_section_ops) != 1 or len(group_ops) != 1:
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if any(child.kind is IRNodeKind.HEADING for child in muutos_ir.children):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if any(child.kind not in {IRNodeKind.NUM, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION} for child in muutos_ir.children):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)

    subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(subsections) < 2:
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)

    heading_text = _plain_content_only_subsection_text(subsections[0])
    first_body_text = _plain_content_only_subsection_text(subsections[1])
    if heading_text is None or first_body_text is None:
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if _plain_content_only_subsection_has_inline_markup(subsections[0]):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if not _looks_like_orphan_section_heading_text(heading_text):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    if not _looks_like_body_subsection_text(first_body_text):
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)

    rewritten_subsections = {
        id(subsection): IRNode(
            kind=subsection.kind,
            label=str(index),
            text=subsection.text,
            attrs=dict(subsection.attrs),
            children=tuple(subsection.children),
        )
        for index, subsection in enumerate(subsections[1:], start=1)
    }
    heading = _with_payload_normalization_rule(
        IRNode(kind=IRNodeKind.HEADING, text=heading_text),
        _LEADING_SUBSECTION_HEADING_PAYLOAD_RULE,
    )
    new_children: list[IRNode] = []
    heading_inserted = False
    for child in muutos_ir.children:
        if child is subsections[0]:
            new_children.append(heading)
            heading_inserted = True
            continue
        replacement = rewritten_subsections.get(id(child))
        new_children.append(replacement if replacement is not None else child)
    if not heading_inserted:
        return LeadingSubsectionHeadingPayloadResult(muutos_ir=muutos_ir)
    return LeadingSubsectionHeadingPayloadResult(
        muutos_ir=_tops._with_children(muutos_ir, new_children),
        rewritten=True,
        heading_text_chars=len(heading_text),
        shifted_subsection_count=len(rewritten_subsections),
    )


def _collapse_intro_list_subsections_inside_section_ir(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Collapse intro-plus-numbered sibling subsections into one subsection payload.

    Some whole-section amendment bodies encode a numbered list as:
    - subsection N: intro text ending with ':'
    - subsection N+1..M: content-only siblings starting with `1)`, `2)`, ...

    Finlex consolidated output materializes that as one subsection containing
    paragraph items, not as separate later moments. Normalize that shape here
    before whole-section replacement.
    """
    if muutos_ir is None or target_unit_kind != "section" or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir
    has_whole_section_replace = any(
        op.op_type == "REPLACE"
        and op.target_unit_kind == target_unit_kind
        and op.target_paragraph is None
        and not op.target_item
        for op in group_ops
    )
    has_first_subsection_replace = any(
        op.op_type == "REPLACE"
        and op.target_unit_kind == target_unit_kind
        and op.target_paragraph == 1
        and not op.target_item
        and not op.target_special
        for op in group_ops
    )
    if not has_whole_section_replace and not has_first_subsection_replace:
        return muutos_ir

    new_children: List[IRNode] = []
    changed = False
    i = 0
    children = list(muutos_ir.children)
    seen_subsection = False
    while i < len(children):
        child = children[i]
        if child.kind is not IRNodeKind.SUBSECTION:
            new_children.append(child)
            i += 1
            continue
        if has_first_subsection_replace and not has_whole_section_replace and seen_subsection:
            new_children.append(child)
            i += 1
            continue
        seen_subsection = True
        intro_text = " ".join((c.text or "").strip() for c in child.children if c.kind in {IRNodeKind.CONTENT, IRNodeKind.INTRO}).strip()
        if not intro_text.endswith(":"):
            new_children.append(child)
            i += 1
            continue

        existing_paragraphs = [c for c in child.children if c.kind is IRNodeKind.PARAGRAPH]
        rows: List[FlattenedListRow] = []
        j = i + 1
        while j < len(children):
            row_group = None
            if children[j].kind is IRNodeKind.SUBSECTION:
                row_group = _flattened_list_rows_from_subsection_ir(children[j])
            if row_group is None:
                break
            rows.extend(row_group)
            j += 1
        if not rows:
            new_children.append(child)
            i += 1
            continue
        paras = (
            _collapse_flattened_list_rows_after_existing_paragraphs(existing_paragraphs, rows)
            if existing_paragraphs
            else _collapse_flattened_list_rows(rows)
        )
        if paras is None:
            new_children.append(child)
            i += 1
            continue

        non_paragraph_children = tuple(c for c in child.children if c.kind is not IRNodeKind.PARAGRAPH)
        collapsed = IRNode(
            kind=child.kind,
            label=child.label,
            text=child.text,
            attrs={
                **dict(child.attrs),
                _PAYLOAD_NORMALIZATION_RULE_ATTR: (
                    _COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST_RULE,
                ),
            },
            children=non_paragraph_children + tuple(paras),
        )
        collapsed = _with_payload_normalization_rule(
            collapsed,
            _COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST_RULE,
        )
        new_children.append(collapsed)
        changed = True
        i = j

    if not changed:
        return muutos_ir
    return _tops._with_children(muutos_ir, new_children)


def _append_tail_to_single_item_subsection(sub: IRNode, continuation_text: str) -> list[IRNode]:
    paragraphs = [child for child in sub.children if child.kind is IRNodeKind.PARAGRAPH]
    if len(paragraphs) != 1:
        return list(sub.children) + [IRNode(kind=IRNodeKind.CONTENT, text=continuation_text)]
    target = paragraphs[0]
    target_index = list(sub.children).index(target)
    paragraph_children = list(target.children)
    content_indexes = [
        idx for idx, child in enumerate(paragraph_children) if child.kind is IRNodeKind.CONTENT
    ]
    if content_indexes:
        content_index = content_indexes[-1]
        content = paragraph_children[content_index]
        paragraph_children[content_index] = IRNode(
            kind=content.kind,
            label=content.label,
            text=f"{(content.text or '').rstrip()} {continuation_text}".strip(),
            attrs=dict(content.attrs),
            children=tuple(content.children),
        )
    else:
        paragraph_children.append(IRNode(kind=IRNodeKind.CONTENT, text=continuation_text))
    merged = list(sub.children)
    merged[target_index] = IRNode(
        kind=target.kind,
        label=target.label,
        text=target.text,
        attrs=dict(target.attrs),
        children=tuple(paragraph_children),
    )
    return merged


def _single_item_tail_paragraph_label(sub: IRNode) -> str:
    paragraph_labels = [
        _norm_num_token(child.label or "")
        for child in sub.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    if len(paragraph_labels) != 1:
        return ""
    return paragraph_labels[0]


def _single_item_tail_matches_explicit_target(
    sub: IRNode,
    group_ops: List[AmendmentOp],
) -> bool:
    child_label = _norm_num_token(sub.label or "")
    if not child_label.isdigit():
        return False
    paragraph_label = _single_item_tail_paragraph_label(sub)
    if not paragraph_label:
        return False
    return any(
        op.target_paragraph == int(child_label)
        and bool(op.target_item)
        and leaf_label_identity_key(str(op.target_item), "item") == leaf_label_identity_key(paragraph_label, "item")
        and op.op_type in ("REPLACE", "INSERT")
        for op in group_ops
    )


def _is_intro_list_subsection_ir(sub: IRNode) -> bool:
    """Return True when a subsection looks like intro + numbered list payload."""
    has_intro = any(child.kind is IRNodeKind.INTRO for child in sub.children)
    para_labels = [
        _norm_num_token(child.label or "") for child in sub.children if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    return has_intro and len(para_labels) >= 2 and para_labels[:2] == ["1", "2"]


def _is_intro_single_item_subsection_ir(sub: IRNode) -> bool:
    """Return True for intro + one changed item payloads that may carry a tail fragment."""
    has_intro = any(child.kind is IRNodeKind.INTRO for child in sub.children)
    para_labels = [
        _norm_num_token(child.label or "") for child in sub.children if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    return has_intro and len(para_labels) == 1


def _subsection_numbered_paragraph_labels(sub: IRNode) -> tuple[str, ...]:
    return tuple(
        _norm_num_token(child.label or "")
        for child in sub.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    )


def _has_numbered_paragraph_sequence(sub: IRNode) -> bool:
    labels = _subsection_numbered_paragraph_labels(sub)
    return len(labels) >= 2 and labels[:2] == ("1", "2")


def _content_only_non_item_subsection_text(sub: IRNode) -> Optional[str]:
    """Return unlabeled content-only subsection text, excluding list-item starts."""
    if len(sub.children) != 1:
        return None
    child = sub.children[0]
    if child.kind is not IRNodeKind.CONTENT:
        return None
    text = irnode_to_text(child).strip()
    # lawvm-regex: owning_parser P-item-prefix numbered-prefix guard over the normalizer's OWN child IR text (irnode_to_text self-read, §1.12); lexer-shaped, no op
    if not text or re.match(r"^\d+[.)]\s+", text):
        return None
    return text


def _fold_split_target_subsection_intro_list_tail(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> tuple[Optional[IRNode], bool, dict[str, object]]:
    """Fold a source-split legal moment back into the explicitly targeted slot.

    Historical Finlex XML can serialize one legal ``N momentti`` as two adjacent
    AKN subsections: a content-only prefix, followed by an intro/list body.  The
    source preamble still targets only ``N momentti``.  This rule is deliberately
    narrow and requires the live target moment to already be a numbered-list
    moment, so a real sibling moment is not absorbed merely because it is next.
    """
    empty_detail: dict[str, object] = {}
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, False, empty_detail

    plain_replace_ops = [
        op
        for op in group_ops
        if (
            op.op_type == "REPLACE"
            and op.target_unit_kind == "section"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(plain_replace_ops) != 1 or len(group_ops) != 1:
        return muutos_ir, False, empty_detail
    target = int(plain_replace_ops[0].target_paragraph or 0)

    live_target = ctx.subsection_by_label.get(str(target))
    if live_target is None or not _has_numbered_paragraph_sequence(live_target):
        return muutos_ir, False, empty_detail

    subsection_pairs = [
        (idx, child)
        for idx, child in enumerate(muutos_ir.children)
        if child.kind is IRNodeKind.SUBSECTION
    ]
    if len(subsection_pairs) != 2:
        return muutos_ir, False, empty_detail

    (prefix_idx, prefix_sub), (tail_idx, tail_sub) = subsection_pairs
    if tail_idx != prefix_idx + 1:
        return muutos_ir, False, empty_detail
    prefix_label = _norm_num_token(prefix_sub.label or "")
    tail_label = _norm_num_token(tail_sub.label or "")
    if prefix_label != str(target) or tail_label != str(target + 1):
        return muutos_ir, False, empty_detail
    if _content_only_non_item_subsection_text(prefix_sub) is None:
        return muutos_ir, False, empty_detail
    if not _is_intro_list_subsection_ir(tail_sub):
        return muutos_ir, False, empty_detail

    folded = _with_payload_normalization_rule(
        IRNode(
            kind=prefix_sub.kind,
            label=prefix_sub.label,
            text=prefix_sub.text,
            attrs=dict(prefix_sub.attrs),
            children=tuple(prefix_sub.children) + tuple(tail_sub.children),
        ),
        _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
    )
    new_children = [
        folded if idx == prefix_idx else child
        for idx, child in enumerate(muutos_ir.children)
        if idx != tail_idx
    ]
    detail: dict[str, object] = {
        "target_paragraph": target,
        "prefix_payload_slot_label": str(prefix_sub.label or ""),
        "tail_payload_slot_label": str(tail_sub.label or ""),
        "live_target_item_labels": _subsection_numbered_paragraph_labels(live_target),
        "rule": _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
    }
    return _tops._with_children(muutos_ir, new_children), True, detail


def _fold_multi_target_subsection_list_wrapups(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> tuple[Optional[IRNode], dict[str, object] | None]:
    """Fold content-only wrap-up slots into multiple explicit list moments.

    Some section payloads serialize each changed legal moment as two adjacent
    source subsections: intro/list body followed by a content-only wrap-up.
    When the preamble explicitly replaces ``N ja M momentti``, those wrap-up
    slots complete the targeted moments; binding them as separate moments loses
    owned legal text.  This repair requires one intro/list + wrap-up pair for
    every explicit replacement and live evidence that every target is already a
    list-shaped moment.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, None

    plain_replace_ops = [
        op
        for op in group_ops
        if (
            op.op_type == "REPLACE"
            and op.target_unit_kind == "section"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(plain_replace_ops) < 2:
        return muutos_ir, None
    if any(
        op.op_type not in {"REPLACE", "REPEAL"}
        or (
            op.op_type == "REPLACE"
            and (
                op.target_unit_kind != "section"
                or op.target_paragraph is None
                or bool(op.target_item)
                or bool(op.target_special)
            )
        )
        for op in group_ops
    ):
        return muutos_ir, None

    targets = sorted({int(op.target_paragraph or 0) for op in plain_replace_ops})
    if len(targets) != len(plain_replace_ops):
        return muutos_ir, None
    if targets != list(range(targets[0], targets[-1] + 1)):
        return muutos_ir, None
    for target in targets:
        live_target = ctx.subsection_by_label.get(str(target))
        if live_target is None or not _has_numbered_paragraph_sequence(live_target):
            return muutos_ir, None

    subsection_pairs = [
        (idx, child)
        for idx, child in enumerate(muutos_ir.children)
        if child.kind is IRNodeKind.SUBSECTION
    ]
    if len(subsection_pairs) != len(targets) * 2:
        return muutos_ir, None

    folded_by_index: dict[int, IRNode] = {}
    remove_indexes: set[int] = set()
    prefix_labels: list[str] = []
    tail_labels: list[str] = []
    tail_text_chars: list[int] = []
    for target, ((prefix_idx, prefix_sub), (tail_idx, tail_sub)) in zip(
        targets,
        zip(subsection_pairs[0::2], subsection_pairs[1::2], strict=True),
        strict=True,
    ):
        if tail_idx != prefix_idx + 1:
            return muutos_ir, None
        if not _is_intro_list_subsection_ir(prefix_sub):
            return muutos_ir, None
        tail_text = _content_only_non_item_subsection_text(tail_sub)
        if tail_text is None:
            return muutos_ir, None
        folded_by_index[prefix_idx] = _with_payload_normalization_rule(
            IRNode(
                kind=prefix_sub.kind,
                label=str(target),
                text=prefix_sub.text,
                attrs=dict(prefix_sub.attrs),
                children=tuple(prefix_sub.children)
                + (IRNode(kind=IRNodeKind.WRAP_UP, text=tail_text),),
            ),
            _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
        )
        remove_indexes.add(tail_idx)
        prefix_labels.append(str(prefix_sub.label or ""))
        tail_labels.append(str(tail_sub.label or ""))
        tail_text_chars.append(len(tail_text))

    new_children = [
        folded_by_index.get(idx, child)
        for idx, child in enumerate(muutos_ir.children)
        if idx not in remove_indexes
    ]
    detail: dict[str, object] = {
        "target_paragraphs": targets,
        "prefix_payload_slot_labels": prefix_labels,
        "tail_payload_slot_labels": tail_labels,
        "tail_text_chars": tail_text_chars,
        "rule": _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
    }
    return _tops._with_children(muutos_ir, new_children), detail


def _split_detached_sentence_from_final_list_item(text: str) -> tuple[str, str] | None:
    """Split a final list-item sentence tail using the shared FI sentence substrate."""
    normalized = text.strip()
    if not normalized:
        return None
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index("fi.payload.final_list_item_tail", normalized)
    if len(index.sentences) < 2:
        return None
    first = index.sentences[0]
    prefix = normalized[: first.char_end].strip()
    tail = normalized[first.char_end :].strip()
    if not prefix or not tail:
        return None
    if not tail[:1].isupper():
        return None
    return prefix, tail


def _last_numbered_paragraph_content_child(subsection: IRNode) -> tuple[int, IRNode, int, IRNode] | None:
    for paragraph_index in range(len(subsection.children) - 1, -1, -1):
        paragraph = subsection.children[paragraph_index]
        if paragraph.kind is not IRNodeKind.PARAGRAPH or not paragraph.label:
            continue
        if not _norm_num_token(paragraph.label).isdigit():
            continue
        for child_index in range(len(paragraph.children) - 1, -1, -1):
            child = paragraph.children[child_index]
            if child.kind is IRNodeKind.CONTENT and child.text and not child.children:
                return paragraph_index, paragraph, child_index, child
    return None


def _shift_subsection_label(subsection: IRNode, *, at_or_after: int) -> IRNode:
    label = _norm_num_token(subsection.label or "")
    if not label.isdigit() or int(label) < at_or_after:
        return subsection
    return IRNode(
        kind=subsection.kind,
        label=str(int(label) + 1),
        text=subsection.text,
        attrs=dict(subsection.attrs),
        children=tuple(subsection.children),
    )


def _split_final_list_item_trailing_subsection(
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> tuple[Optional[IRNode], dict[str, object] | None]:
    """Promote a detached sentence glued to the final list item into a moment.

    Historical amendment XML can encode a whole-section replacement as:

    - subsection 1: intro + numbered paragraphs, with the next legal moment
      glued after the final list item's first sentence;
    - subsection 2: the following real legal moment.

    The source order is still complete, but the AKN subsection boundary is one
    sentence late.  Split only that exact whole-section payload shape; descendant
    scoped groups keep their existing sparse-slot machinery.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, None
    whole_section_ops = [
        op
        for op in group_ops
        if (
            op.op_type in {"INSERT", "REPLACE"}
            and op.target_unit_kind == "section"
            and op.target_paragraph is None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(whole_section_ops) != 1 or len(group_ops) != 1:
        return muutos_ir, None

    subsection_positions = [
        (idx, child)
        for idx, child in enumerate(muutos_ir.children)
        if child.kind is IRNodeKind.SUBSECTION
    ]
    if len(subsection_positions) < 2:
        return muutos_ir, None
    first_idx, first_subsection = subsection_positions[0]
    following_idx, following_subsection = subsection_positions[1]
    if following_idx != first_idx + 1:
        return muutos_ir, None
    first_label = _norm_num_token(first_subsection.label or "")
    following_label = _norm_num_token(following_subsection.label or "")
    if not first_label.isdigit() or not following_label.isdigit():
        return muutos_ir, None
    new_label_int = int(first_label) + 1
    if int(following_label) != new_label_int:
        return muutos_ir, None
    if not _is_intro_list_subsection_ir(first_subsection):
        return muutos_ir, None
    if _plain_content_only_subsection_text(following_subsection) is None:
        return muutos_ir, None

    content_slot = _last_numbered_paragraph_content_child(first_subsection)
    if content_slot is None:
        return muutos_ir, None
    paragraph_index, paragraph, content_index, content = content_slot
    split = _split_detached_sentence_from_final_list_item(content.text or "")
    if split is None:
        return muutos_ir, None
    list_item_text, detached_text = split

    paragraph_children = list(paragraph.children)
    paragraph_children[content_index] = IRNode(
        kind=content.kind,
        label=content.label,
        text=list_item_text,
        attrs=dict(content.attrs),
        children=tuple(content.children),
    )
    subsection_children = list(first_subsection.children)
    subsection_children[paragraph_index] = IRNode(
        kind=paragraph.kind,
        label=paragraph.label,
        text=paragraph.text,
        attrs=dict(paragraph.attrs),
        children=tuple(paragraph_children),
    )
    rewritten_first = IRNode(
        kind=first_subsection.kind,
        label=first_subsection.label,
        text=first_subsection.text,
        attrs=dict(first_subsection.attrs),
        children=tuple(subsection_children),
    )
    inserted_subsection = _with_payload_normalization_rule(
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(new_label_int),
            children=(IRNode(kind=IRNodeKind.CONTENT, text=detached_text),),
        ),
        _SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION_RULE,
    )

    new_children: list[IRNode] = []
    shifted_count = 0
    inserted = False
    for idx, child in enumerate(muutos_ir.children):
        if idx == first_idx:
            new_children.append(rewritten_first)
            new_children.append(inserted_subsection)
            inserted = True
            continue
        if inserted and child.kind is IRNodeKind.SUBSECTION:
            shifted = _shift_subsection_label(child, at_or_after=new_label_int)
            shifted_count += int(shifted is not child)
            new_children.append(shifted)
            continue
        new_children.append(child)
    if not inserted:
        return muutos_ir, None

    detail: dict[str, object] = {
        "source_subsection_label": str(first_subsection.label or ""),
        "inserted_subsection_label": str(new_label_int),
        "shifted_subsection_count": shifted_count,
        "detached_text_chars": len(detached_text),
        "rule": _SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION_RULE,
    }
    return _tops._with_children(muutos_ir, new_children), detail


def _fold_split_omission_subsection_prefix_into_following_intro_list(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Fold split omission-bracketed subsection prefixes into one subsection.

    Some sparse whole-section payloads serialize one changed subsection as:
    - omission
    - subsection N containing only a content prefix
    - subsection N+1 containing the trailing intro/list body

    Treat that as one subsection payload before sparse omission alignment so the
    mapper does not reinterpret the split fragments as two separate live
    moments.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir
    if not any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children):
        return muutos_ir

    plain_subsection_ops = [
        op
        for op in group_ops
        if (op.target_paragraph is not None and not op.target_item and op.op_type in ("REPLACE", "INSERT"))
    ]
    plain_subsection_targets = sorted(
        {int(op.target_paragraph) for op in plain_subsection_ops if op.target_paragraph is not None}
    )
    if len(plain_subsection_targets) >= 2:
        return muutos_ir
    item_subsection_targets = sorted(
        {
            int(op.target_paragraph)
            for op in group_ops
            if op.target_paragraph is not None and bool(op.target_item)
        }
    )
    if (
        plain_subsection_targets
        and item_subsection_targets
        and any(target not in set(plain_subsection_targets) for target in item_subsection_targets)
    ):
        return muutos_ir
    # When all plain subsection ops are INSERTs, the body subsections after the
    # omission are genuinely new moments.  Folding them together would lose a
    # new subsection — skip the fold entirely.
    if plain_subsection_ops and all(op.op_type == "INSERT" for op in plain_subsection_ops):
        return muutos_ir

    numbered_table_targets = frozenset(
        _norm_num_token(label)
        for op in group_ops
        for label in op.numbered_table_targets
        if _norm_num_token(label)
    )
    children = list(muutos_ir.children)
    new_children: List[IRNode] = []
    changed = False
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind is not IRNodeKind.SUBSECTION:
            new_children.append(child)
            i += 1
            continue
        if i == 0 or children[i - 1].kind is not IRNodeKind.OMISSION or i + 1 >= len(children):
            new_children.append(child)
            i += 1
            continue

        prefix_text = _content_only_non_item_subsection_text(child)
        nxt = children[i + 1]
        if prefix_text is None or nxt.kind is not IRNodeKind.SUBSECTION or not _is_intro_list_subsection_ir(nxt):
            new_children.append(child)
            i += 1
            continue
        if numbered_table_targets and mentioned_numbered_table_labels(child) & numbered_table_targets:
            new_children.append(child)
            i += 1
            continue

        merged_intro_done = False
        merged_children: List[IRNode] = []
        for grandchild in nxt.children:
            if not merged_intro_done and grandchild.kind is IRNodeKind.INTRO:
                merged_children.append(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        label=grandchild.label,
                        text=f"{prefix_text} {irnode_to_text(grandchild).strip()}".strip(),
                        attrs=dict(grandchild.attrs),
                        children=tuple(grandchild.children),
                    )
                )
                merged_intro_done = True
                continue
            merged_children.append(grandchild)
        if not merged_intro_done:
            new_children.append(child)
            i += 1
            continue

        new_children.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(merged_children),
            )
        )
        changed = True
        i += 2

    if not changed:
        return muutos_ir
    return _tops._with_children(muutos_ir, new_children)


def _prune_carried_subsections_outside_single_target_moment_ir(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> tuple[Optional[IRNode], tuple[str, ...]]:
    """Prune carried sibling moments from a section payload that only owns one moment.

    Some Finlex amendment XML serializes a whole current section even when the
    johtolause only changes one subsection/item family inside it. In that shape,
    later sibling subsections are carried context, not owned payload. Keeping
    them inside the amendment payload lets sparse elaboration treat them as
    candidate slots for the changed moment, which then leaks stale carried text
    into replay.

    Keep this rule narrow:
    - section target only
    - exactly one plain subsection target
    - item ops exist only under that same subsection target
    - the targeted subsection payload is already self-contained (has paragraph
      items), so later sibling subsections are not needed to express the change
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, ()

    plain_targets = sorted(
        {
            int(op.target_paragraph)
            for op in group_ops
            if (
                op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
                and op.op_type in ("REPLACE", "INSERT")
            )
        }
    )
    if len(plain_targets) != 1:
        return muutos_ir, ()
    target_paragraph = plain_targets[0]

    item_targets = {
        int(op.target_paragraph)
        for op in group_ops
        if (
            op.target_paragraph is not None
            and bool(op.target_item)
            and op.op_type in ("REPLACE", "INSERT")
        )
    }
    if not item_targets or item_targets != {target_paragraph}:
        return muutos_ir, ()

    amend_subs = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subs) < 2:
        return muutos_ir, ()

    target_sub = next((child for child in amend_subs if _norm_num_token(child.label or "") == str(target_paragraph)), None)
    if target_sub is None:
        return muutos_ir, ()
    if not any(child.kind is IRNodeKind.PARAGRAPH for child in target_sub.children):
        return muutos_ir, ()

    explicitly_targeted_labels = {str(target_paragraph)}
    carried_subs = [
        child
        for child in amend_subs
        if _norm_num_token(child.label or "") not in explicitly_targeted_labels
    ]
    if not carried_subs:
        return muutos_ir, ()

    new_children: List[IRNode] = []
    for child in muutos_ir.children:
        if child.kind is IRNodeKind.SUBSECTION and child in carried_subs:
            continue
        new_children.append(child)
    return _tops._with_children(muutos_ir, new_children), tuple(
        str(child.label or "") for child in carried_subs if child.label
    )


def _fold_continuation_row_subsections_into_previous_subsection(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Fold row-like continuation subsections into the preceding subsection.

    Some Finnish amendment bodies encode a changed moment as:
    - subsection heading/content
    - followed by several unlabeled sibling subsections whose text is row-like
      (`1. ...`, `2. ...`, ...)
    - with omission markers bracketing the sparse payload.

    In live replay those trailing sibling subsections are not separate moments;
    they are content rows belonging to the preceding changed subsection.
    Folding them here prevents omission resolution from treating them as fake
    subsections 4..N and discarding them.

    Uses ``ctx.live_node`` (Class 2: local subtree) for existence check.
    """
    if target_unit_kind != "section" or muutos_ir is None:
        return muutos_ir
    if not any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children):
        return muutos_ir
    if ctx.live_node is None:
        return muutos_ir

    def _content_text(sub: IRNode) -> str:
        if len(sub.children) != 1:
            return ""
        child = sub.children[0]
        if child.kind is not IRNodeKind.CONTENT:
            return ""
        return irnode_to_text(child).strip()

    new_children: List[IRNode] = []
    changed = False
    i = 0
    while i < len(muutos_ir.children):
        child = muutos_ir.children[i]
        if child.kind is not IRNodeKind.SUBSECTION:
            new_children.append(child)
            i += 1
            continue

        base_text = _content_text(child)
        # lawvm-regex: owning_parser P-continuation continuation-row prefix predicate over IR content text; lexer-shaped
        if not base_text or _CONTINUATION_ROW_PREFIX_RE.match(base_text):
            new_children.append(child)
            i += 1
            continue

        continuation_rows: List[IRNode] = []
        j = i + 1
        while j < len(muutos_ir.children):
            sibling = muutos_ir.children[j]
            if sibling.kind is not IRNodeKind.SUBSECTION:
                break
            row_text = _content_text(sibling)
            # lawvm-regex: owning_parser P-continuation continuation-row prefix predicate over IR content text; lexer-shaped
            m = _CONTINUATION_ROW_PREFIX_RE.match(row_text)
            if not m:
                break
            continuation_rows.append(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label=m.group(1),
                    text=row_text,
                )
            )
            j += 1

        if continuation_rows:
            folded_children = list(child.children) + continuation_rows
            if j < len(muutos_ir.children) and muutos_ir.children[j].kind is IRNodeKind.OMISSION:
                folded_children.append(muutos_ir.children[j])
                j += 1
            new_children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(folded_children),
                )
            )
            changed = True
            i = j
            continue

        new_children.append(child)
        i += 1

    if not changed:
        return muutos_ir
    return _tops._with_children(muutos_ir, new_children)


def _looks_like_text_table_row(text: str) -> bool:
    if not text or text[0].isdigit() or text[0].isspace() or "\n" in text:
        return False
    if len(text) > 120:
        return False
    head, sep, amount = text.rpartition(" ")
    if not sep or not head:
        return False
    amount = amount.replace(",", ".", 1)
    if amount.count(".") > 1:
        return False
    whole, dot, frac = amount.partition(".")
    if not whole.isdigit() or len(whole) > 12:
        return False
    return not dot or (frac.isdigit() and 1 <= len(frac) <= 6)


def _content_only_subsection_text(sub: IRNode) -> str:
    if sub.kind is not IRNodeKind.SUBSECTION or len(sub.children) != 1:
        return ""
    child = sub.children[0]
    if child.kind is not IRNodeKind.CONTENT:
        return ""
    return " ".join(irnode_to_text(child).split())


def _is_text_table_row_subsection(sub: IRNode) -> bool:
    text = _content_only_subsection_text(sub)
    if not text:
        return False
    if "." in text or ":" in text:
        return False
    return _looks_like_text_table_row(text)


def _fold_text_table_row_subsections_into_target_subsection(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Fold textual table-row sibling subsections into a single targeted moment."""
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir
    subsection_ops = [
        op
        for op in group_ops
        if (
            op.op_type in {"REPLACE", "INSERT"}
            and op.target_unit_kind == "section"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(subsection_ops) != 1:
        return muutos_ir
    target_label = str(subsection_ops[0].target_paragraph)
    children = list(muutos_ir.children)
    target_index = next(
        (
            idx
            for idx, child in enumerate(children)
            if child.kind is IRNodeKind.SUBSECTION and _norm_num_token(child.label or "") == target_label
        ),
        None,
    )
    if target_index is None:
        return muutos_ir

    target_sub = children[target_index]
    target_text = " ".join(irnode_to_text(target_sub).split()).lower()
    if "seuraavasti:" not in target_text:
        return muutos_ir

    row_indices: list[int] = []
    row_paragraphs: list[IRNode] = []
    for idx in range(target_index + 1, len(children)):
        sibling = children[idx]
        if sibling.kind is not IRNodeKind.SUBSECTION:
            break
        if not _is_text_table_row_subsection(sibling):
            break
        row_text = _content_only_subsection_text(sibling)
        row_indices.append(idx)
        row_paragraphs.append(IRNode(kind=IRNodeKind.PARAGRAPH, text=row_text))

    if len(row_paragraphs) < 2:
        return muutos_ir

    folded = _with_payload_normalization_rule(
        IRNode(
            kind=target_sub.kind,
            label=target_sub.label,
            text=target_sub.text,
            attrs=dict(target_sub.attrs),
            children=tuple(target_sub.children) + tuple(row_paragraphs),
        ),
        _TEXT_TABLE_ROW_CONTINUATION_RULE,
    )
    row_index_set = set(row_indices)
    new_children = [
        folded if idx == target_index else child
        for idx, child in enumerate(children)
        if idx not in row_index_set
    ]
    return _tops._with_children(muutos_ir, new_children)


def _normalize_item_like_target(
    ctx: PayloadElaborationContext,
    op: AmendmentOp,
    muutos_ir: Optional[IRNode],
    group_ops: list[AmendmentOp] | None = None,
) -> AmendmentOp:
    """Rewrite obvious item-like numeric subsection targets before apply ordering.

    Uses ``ctx.live_node`` (Class 2: local subtree) — the op's target section
    is the group's target section which is ``ctx.live_node``.
    """

    def _sub_text(sub: IRNode) -> str:
        return (sub.text or " ".join(c.text or "" for c in sub.children)).strip()

    def _is_flat_numbered_item_sub(sub: IRNode) -> bool:
        if any(c.kind is IRNodeKind.PARAGRAPH for c in sub.children):
            return False
        # lawvm-regex: owning_parser P-item-prefix flat-numbered-item predicate over IR sub text; lexer-shaped
        return re.match(r"^(\d+[a-zA-Z]*)\)", _sub_text(sub)) is not None

    if (
        op.target_unit_kind != "section"
        or not op.target_paragraph
        or op.target_item
        or op.target_special
    ):
        return op
    if group_ops is not None and any(other is not op and bool(other.target_item) for other in group_ops):
        return op
    master_sec = ctx.live_node
    if master_sec is None:
        return op
    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    if len(master_subsecs) != 1:
        return op
    if not any(c.kind is IRNodeKind.PARAGRAPH for c in master_subsecs[0].children):
        return op
    amend_subs = [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION] if muutos_ir is not None else []
    if len(amend_subs) > 1 and not all(_is_flat_numbered_item_sub(sub) for sub in amend_subs):
        return op
    if amend_subs and any(
        _norm_num_token(sub.label or "") == str(op.target_paragraph)
        for sub in amend_subs
    ):
        return op
    if amend_subs and op.target_paragraph <= len(amend_subs):
        return op
    if amend_subs and not any(c.kind is IRNodeKind.PARAGRAPH for c in amend_subs[0].children):
        flat_match = False
        for sub in amend_subs:
            # lawvm-regex: owning_parser P-item-prefix flat-match slot routing predicate over IR sub text; lexer-shaped
            m = re.match(r"^(\d+[a-zA-Z]*)\)", _sub_text(sub))
            if m and _norm_num_token(m.group(1)) == str(op.target_paragraph):
                flat_match = True
                break
        if not flat_match:
            return op
    if op.target_paragraph <= len(master_subsecs):
        return op
    new_lo = _lo_with_path_update(op.lo, subsection="1", item=str(op.target_paragraph)) if op.lo else None
    return dc_replace(
        op,
        lo=new_lo,
        target_guessing_provenance_tags=tuple(
            dict.fromkeys((*op.target_guessing_provenance_tags, "normalize_item_like_target"))
        ),
    )


def _prune_container_payload_sections_shadowed_by_standalone_targets(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    muutos_ir: Optional[IRNode],
    standalone_section_targets: Set[str],
    *,
    foreign_scoped_standalone_section_targets: Set[str] | None = None,
    foreign_scoped_descendant_section_targets: Set[str] | None = None,
    foreign_scoped_replace_section_targets: Set[str] | None = None,
    foreign_scoped_replace_section_target_scopes: (
        frozenset[StandaloneSectionTarget] | None
    ) = None,
    recodification_transfer_context: bool = False,
    expected_heading_only: bool = False,
    preserve_dense_new_container_payload: bool = False,
) -> ContainerPayloadPruningResult:
    """Drop malformed container payload sections that are targeted separately.

    Uses ``ctx.live_node`` (Class 2: local subtree) for the container, and
    ``ctx.lookups`` (Class 3: topology) for fallback section scope resolution.
    """
    if muutos_ir is None or target_unit_kind not in ("chapter", "part") or not standalone_section_targets:
        return ContainerPayloadPruningResult(muutos_ir=muutos_ir, changed=False)
    standalone_section_targets = {_norm_num_token(label) for label in standalone_section_targets if label}
    foreign_scoped_standalone_section_targets = {
        _norm_num_token(label) for label in (foreign_scoped_standalone_section_targets or ()) if label
    }
    foreign_scoped_descendant_section_targets = {
        _norm_num_token(label) for label in (foreign_scoped_descendant_section_targets or ()) if label
    }
    foreign_scoped_replace_section_targets = {
        _norm_num_token(label) for label in (foreign_scoped_replace_section_targets or ()) if label
    }
    foreign_scoped_replace_section_target_scopes = frozenset(
        target
        for target in (foreign_scoped_replace_section_target_scopes or frozenset())
        if target.label
    )

    def _foreign_scope_is_base_of_target(target: StandaloneSectionTarget) -> bool:
        base = target.chapter if target_unit_kind == "chapter" else target.part
        if not base or base == target_norm or not target_norm.startswith(base):
            return False
        if (
            target_unit_kind == "chapter"
            and ctx.target_part is not None
            and target.part != ctx.target_part
        ):
            return False
        suffix = target_norm[len(base):]
        return bool(suffix) and suffix.isalpha()

    def _scope_label_from_unique_path(section_label: str, scope_kind: str) -> Optional[str]:
        """Look up a section's enclosing scope label from lookups.

        First tries a chapter-scoped match using the container's target_norm
        (since we know which chapter/part this container belongs to). Falls
        back to unscoped lookup for unique sections, then to family-base
        fallback — matching the old ``find_family`` behavior.
        """
        # Try chapter-scoped lookup first (container's scope is known)
        path = ctx.lookups.unique_section_paths.get((section_label, target_norm))
        if path is not None:
            for kind, label in path:
                if kind == scope_kind:
                    return label or None

        # Try unscoped lookup (works for unique sections)
        path = ctx.lookups.unique_section_paths.get((section_label, None))
        if path is not None:
            for kind, label in path:
                if kind == scope_kind:
                    return label or None

        # Family-base fallback: '20a' → try '20'
        # lawvm-regex: owning_parser family-base ``20a``→``20`` fallback lexer over a normalized label
        m = re.match(r"^(\d+)[a-z]", _norm_num_token(section_label))
        if m:
            base = m.group(1)
            # Try scoped first
            path = ctx.lookups.unique_section_paths.get((base, target_norm))
            if path is None:
                path = ctx.lookups.unique_section_paths.get((base, None))
            if path is not None:
                for kind, label in path:
                    if kind == scope_kind:
                        return label or None

        return None

    # For container targets (L/O), `container_member_labels` is the authority
    # for which section labels belong to the live container.
    master_container = ctx.live_node
    live_member_labels: Set[str] = {
        _norm_num_token(label) for label in (ctx.container_member_labels or ()) if label
    }

    changed = False
    pruned_labels: List[str] = []
    pruned_witnesses: list[PrunedPayloadSectionWitness] = []
    before_child_paths = _container_payload_section_child_paths(
        muutos_ir,
        target_unit_kind,
        target_norm,
    )
    payload_section_labels = {
        _norm_num_token(child.label or "")
        for child in muutos_ir.children
        if child.kind is IRNodeKind.SECTION and child.label
    }
    payload_has_suffixed_section_labels = any(
        not label.isdigit() for label in payload_section_labels
    )
    payload_numeric_labels = {
        int(label) for label in payload_section_labels if label.isdigit()
    }
    foreign_replace_numeric_labels = {
        int(label)
        for label in foreign_scoped_replace_section_targets
        if label in payload_section_labels and label.isdigit()
    }
    foreign_replace_base_scope_labels = {
        target.label
        for target in foreign_scoped_replace_section_target_scopes
        if target.label in payload_section_labels
        and _foreign_scope_is_base_of_target(target)
    }
    has_dense_foreign_replace_bridge = False
    if foreign_replace_numeric_labels:
        lo_foreign = min(foreign_replace_numeric_labels)
        hi_foreign = max(foreign_replace_numeric_labels)
        has_dense_foreign_replace_bridge = (
            lo_foreign - 1 in payload_numeric_labels
            and hi_foreign + 1 in payload_numeric_labels
            and all(n in payload_numeric_labels for n in range(lo_foreign, hi_foreign + 1))
        )
    new_children: List[IRNode] = []
    for child in muutos_ir.children:
        child_label = _norm_num_token(child.label or "")
        if child.kind is not IRNodeKind.SECTION or not child_label or child_label not in standalone_section_targets:
            new_children.append(child)
            continue

        child_path = _container_payload_section_child_path(
            target_unit_kind,
            target_norm,
            child_label,
        )
        child_witness = PrunedPayloadSectionWitness(
            label=child_label,
            path=child_path,
            structural_hash=structural_subtree_hash(child),
        )
        if master_container is not None:
            if child_label not in live_member_labels:
                # Foreign-scoped section: belongs to another chapter/part.
                # Prune it even though it's not in the live container.
                if (
                    child_label in foreign_scoped_standalone_section_targets
                    or child_label in foreign_scoped_descendant_section_targets
                    or (
                        child_label in foreign_scoped_replace_section_targets
                        and not has_dense_foreign_replace_bridge
                    )
                ):
                    changed = True
                    pruned_labels.append(child_label)
                    pruned_witnesses.append(child_witness)
                    continue
                # NEW section: keep in container — it's being introduced
                # by the amendment.  The standalone PEG op handles its own
                # targeting, but the container payload is the authoritative
                # source for new sections being added to the chapter.
                new_children.append(child)
                continue
            changed = True
            pruned_labels.append(child_label)
            pruned_witnesses.append(child_witness)
            continue

        # No live container exists yet: this is a new chapter/part payload.
        # Only prune labels that belong to the same new container split lane.
        # Foreign-scoped standalone section targets elsewhere in the amendment
        # (for example another chapter's "1 §") must not delete a new
        # container member that just happens to share the same section label.
        # When the same payload has already exposed foreign-scoped REPLACE
        # members, however, the container is carrying overwrapped context from
        # outside the new chapter/part; a separately owned foreign-scoped
        # INSERT belongs to that context too and must not be smuggled into the
        # new container.
        if child_label in foreign_scoped_descendant_section_targets:
            changed = True
            pruned_labels.append(child_label)
            pruned_witnesses.append(child_witness)
            continue
        if child_label in foreign_scoped_standalone_section_targets:
            if not (
                preserve_dense_new_container_payload
                and has_dense_foreign_replace_bridge
            ):
                changed = True
                pruned_labels.append(child_label)
                pruned_witnesses.append(child_witness)
                continue
            new_children.append(child)
            continue
        if (
            child_label in foreign_scoped_replace_section_targets
            and not has_dense_foreign_replace_bridge
            and not recodification_transfer_context
            and child_label in foreign_replace_base_scope_labels
            and not (
                child_label.isdigit()
                and payload_has_suffixed_section_labels
            )
        ):
            # A separately targeted REPLACE scoped to another live container
            # does not authorize pruning the same-numbered member of this new
            # chapter/part when the overlap is isolated by label rather than a
            # dense or suffix-delimited carried-context bridge from that
            # foreign scope.
            new_children.append(child)
            continue
        changed = True
        pruned_labels.append(child_label)
        pruned_witnesses.append(child_witness)

    if not changed:
        return ContainerPayloadPruningResult(
            muutos_ir=muutos_ir,
            changed=False,
            before_child_paths=before_child_paths,
            after_child_paths=before_child_paths,
        )
    new_muutos_ir = _tops._with_children(muutos_ir, new_children)
    return ContainerPayloadPruningResult(
        muutos_ir=new_muutos_ir,
        changed=True,
        pruned_labels=tuple(pruned_labels),
        before_child_paths=before_child_paths,
        after_child_paths=_container_payload_section_child_paths(
            new_muutos_ir,
            target_unit_kind,
            target_norm,
        ),
        pruned_section_witnesses=tuple(pruned_witnesses),
    )


def _container_payload_section_child_path(
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    section_label: str,
) -> str:
    return f"{target_unit_kind}:{target_norm}/section:{section_label}"


def _container_payload_section_child_paths(
    muutos_ir: IRNode,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
) -> tuple[str, ...]:
    return tuple(
        _container_payload_section_child_path(
            target_unit_kind,
            target_norm,
            _norm_num_token(child.label or ""),
        )
        for child in muutos_ir.children
        if child.kind is IRNodeKind.SECTION and child.label
    )


def _container_pruning_is_expected_heading_only(group_ops: List[AmendmentOp]) -> bool:
    """Return True when container-pruned sections are expected, not pathological.

    A normal Finnish pattern is:
    - chapter/part heading is replaced, and
    - one or more member sections are also targeted separately in the same
      amendment body.

    In that case payload normalization should still prune the shadowed member
    sections from the container payload, but it should not classify the prune as
    a malformed container-membership pathology. The prune remains observable via
    ``ELAB.CONTAINER_PRUNED_SHADOWED``.
    """
    if not group_ops:
        return False
    for op in group_ops:
        if op.op_type != "REPLACE":
            return False
        if op.target_unit_kind in {"chapter", "part"} and not op.target_paragraph and not op.target_item:
            continue
        if op.target_special not in {"otsikko", "otsikko_edella"}:
            return False
    return True


def _container_pruning_is_expected_frontend_split(
    ctx: PayloadElaborationContext,
    group_ops: List[AmendmentOp],
) -> bool:
    """Return True when container pruning is frontend-owned split behavior.

    New chapter/part payloads can legitimately carry both:

    - the container payload, and
    - separately emitted standalone section targets

    In that case normalization still needs to prune the shadowed section bodies
    from the container payload, but the prune is not evidence that the payload
    mismatched an existing live container. Keep the prune observable via
    ``ELAB.CONTAINER_PRUNED_SHADOWED`` without promoting it to
    ``CONTAINER_MEMBERSHIP_MISMATCH``.
    """
    if ctx.live_node is None:
        return True
    return _container_pruning_is_expected_heading_only(group_ops)


_ORIGINAL_SPARSE_SUBSECTION_LABEL_ATTR = "original_sparse_subsection_label"
_HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID = "fi.historical_top_level_kohta_as_subsection"


def _has_historical_top_level_kohta_subsection_ops(group_ops: Iterable[AmendmentOp]) -> bool:
    return any(
        _HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID in op.extraction_provenance_tags
        for op in group_ops
    )


def _split_historical_top_level_kohta_payload_ir(
    target_unit_kind: TargetUnitKind,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    if (
        target_unit_kind != "section"
        or muutos_ir is None
        or muutos_ir.kind is not IRNodeKind.SECTION
        or not _has_historical_top_level_kohta_subsection_ops(group_ops)
    ):
        return muutos_ir

    target_labels = {
        str(op.target_paragraph)
        for op in group_ops
        if (
            op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
            and _HISTORICAL_TOP_LEVEL_KOHTA_SUBSECTION_RULE_ID in op.extraction_provenance_tags
        )
    }
    if not target_labels:
        return muutos_ir

    subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return muutos_ir

    source_subsection = subsections[0]
    split_subsections: list[IRNode] = []
    for child in source_subsection.children:
        if child.kind is not IRNodeKind.PARAGRAPH:
            continue
        text = irnode_to_text(child).strip()
        # lawvm-regex: owning_parser P-item-prefix parenthesized ``(N)`` label predicate over the normalizer's OWN child IR text (irnode_to_text self-read, §1.12); lexer-shaped
        match = re.match(r"^\((\d+(?:\s*[a-z])?)\)", text)
        if match is None:
            continue
        label = _norm_num_token(match.group(1))
        if label not in target_labels:
            continue
        split_subsections.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=label,
                attrs=dict(child.attrs),
                children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
            )
        )

    if {str(child.label or "") for child in split_subsections} != target_labels:
        return muutos_ir

    retained = [
        child
        for child in muutos_ir.children
        if child.kind is not IRNodeKind.SUBSECTION and child.kind is not IRNodeKind.OMISSION
    ]
    return IRNode(
        kind=muutos_ir.kind,
        label=muutos_ir.label,
        text=muutos_ir.text,
        attrs=dict(muutos_ir.attrs),
        children=tuple(retained + split_subsections),
    )


def _align_sparse_omission_subsections_to_live(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: Optional[List[AmendmentOp]] = None,
) -> Tuple[Optional[IRNode], bool]:
    """Align omission-marked subsection payload labels to live subsection slots.

    Uses ``ctx.live_node`` (Class 2: local subtree).

    Some amendment bodies encode partial section replaces as sparse subsection
    payloads such as ``1 mom / omission / 2 mom`` even when the trailing real
    payload should land on live subsection 3. Normalize those labels here so
    apply-time subsection override maps target the right slots.
    """
    if target_unit_kind != "section" or muutos_ir is None:
        return muutos_ir, False

    amend_slots = [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION or c.kind is IRNodeKind.OMISSION]
    if not amend_slots or not any(c.kind is IRNodeKind.OMISSION for c in amend_slots):
        return muutos_ir, False

    amend_subsecs = [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
    if group_ops is not None:
        has_item_targets = any(
            op.target_paragraph is not None and bool(op.target_item)
            for op in group_ops
        )
        has_plain_targets = any(
            op.target_paragraph is not None
            and not op.target_item
            and op.op_type in ("REPLACE", "INSERT")
            for op in group_ops
        )
        # Item-only sparse payloads are not moment-slot rewrites. Relabeling
        # their subsection shells to live slot numbers fabricates fake
        # subsection identity (for example turning a local list-item payload
        # into subsection:3), which can later leak as duplicate replay-fold
        # structure. Leave those payload-local labels untouched and let sparse
        # item binding work from the original payload shape.
        if has_item_targets and not has_plain_targets:
            return muutos_ir, False
    ordered_targets = [
        op.target_paragraph
        for op in (group_ops or [])
        if (op.target_paragraph is not None and not op.target_item and op.op_type in ("REPLACE", "INSERT"))
    ]
    ordered_logical_targets = list(
        dict.fromkeys(
            op.target_paragraph
            for op in (group_ops or [])
            if (op.target_paragraph is not None and op.op_type in ("REPLACE", "INSERT"))
        )
    )
    explicit_targets = sorted(
        {
            op.target_paragraph
            for op in (group_ops or [])
            if (op.target_paragraph is not None and not op.target_item and op.op_type in ("REPLACE", "INSERT"))
        }
    )
    if amend_subsecs and len(ordered_logical_targets) == len(amend_subsecs):
        changed = False
        # Sort ascending so that labels are assigned to body subsections in
        # position order.  Amendment bodies always present subsections from
        # lower to higher positions; the preamble may list ops in a different
        # order (e.g. "muutetaan 5 mom" before "lisätään uusi 3 mom"), which
        # would assign wrong labels without sorting here.
        target_iter = iter(str(label) for label in sorted(ordered_logical_targets))
        new_children: List[IRNode] = []
        for child in muutos_ir.children:
            if child.kind is not IRNodeKind.SUBSECTION:
                new_children.append(child)
                continue
            desired_label = next(target_iter)
            if child.label != desired_label:
                child = IRNode(
                    kind=child.kind,
                    label=desired_label,
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
                changed = True
            new_children.append(child)
        if changed:
            return _tops._with_children(muutos_ir, new_children), True
        return muutos_ir, False
    if amend_subsecs and len(ordered_targets) == len(amend_subsecs):
        changed = False
        desired_labels: List[str] = []
        next_floor = 0
        for target in sorted(ordered_targets):
            desired = max(int(target), next_floor + 1)
            desired_labels.append(str(desired))
            next_floor = desired
        target_iter = iter(desired_labels)
        new_children: List[IRNode] = []
        for child in muutos_ir.children:
            if child.kind is not IRNodeKind.SUBSECTION:
                new_children.append(child)
                continue
            desired_label = next(target_iter)
            if child.label != desired_label:
                child = IRNode(
                    kind=child.kind,
                    label=desired_label,
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
                changed = True
            new_children.append(child)
        if changed:
            return _tops._with_children(muutos_ir, new_children), True
        return muutos_ir, False
    if amend_subsecs and len(explicit_targets) == len(amend_subsecs):
        changed = False
        target_iter = iter(str(label) for label in explicit_targets)
        new_children: List[IRNode] = []
        for child in muutos_ir.children:
            if child.kind is not IRNodeKind.SUBSECTION:
                new_children.append(child)
                continue
            desired_label = next(target_iter)
            if child.label != desired_label:
                child = IRNode(
                    kind=child.kind,
                    label=desired_label,
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
                changed = True
            new_children.append(child)
        if changed:
            return _tops._with_children(muutos_ir, new_children), True
        return muutos_ir, False

    master_sec = ctx.live_node
    if master_sec is None:
        return muutos_ir, False
    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    if not master_subsecs:
        return muutos_ir, False

    total_live = len(master_subsecs)
    total_slots = len(amend_slots)
    logical_idx = 0
    slot_idx = 0
    changed = False
    new_children: List[IRNode] = []

    for child in muutos_ir.children:
        if child.kind is IRNodeKind.OMISSION:
            if total_slots < total_live:
                remaining_real = sum(1 for c in amend_slots[slot_idx + 1 :] if c.kind is IRNodeKind.SUBSECTION)
                logical_idx = total_live - remaining_real
            else:
                logical_idx += 1
            new_children.append(child)
            slot_idx += 1
            continue

        if child.kind is not IRNodeKind.SUBSECTION:
            new_children.append(child)
            continue

        desired_label = str(logical_idx + 1)
        if (child.label or "").isdigit() and child.label != desired_label:
            new_attrs = dict(child.attrs)
            if child.label:
                new_attrs.setdefault(_ORIGINAL_SPARSE_SUBSECTION_LABEL_ATTR, child.label)
            child = IRNode(
                kind=child.kind,
                label=desired_label,
                text=child.text,
                attrs=new_attrs,
                children=tuple(child.children),
            )
            changed = True
        new_children.append(child)
        logical_idx += 1
        slot_idx += 1

    if not changed:
        return muutos_ir, False
    return _tops._with_children(muutos_ir, new_children), True


def _rebase_item_targets_to_sparse_slot_labels(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> Tuple[List[AmendmentOp], bool]:
    """Rebase item-targeted ops to the plain moment that shares their sparse slot."""
    changed = False
    rebased: List[AmendmentOp] = []
    for op in group_ops:
        mapped = assignment.for_op(op)
        if (
            mapped is not None
            and op.target_item
            and op.target_paragraph is not None
        ):
            binding = next(
                (
                    candidate
                    for candidate in assignment.sparse_slot_bindings
                    if (
                        candidate.op_type == str(op.op_type or "")
                        and candidate.target_paragraph == op.target_paragraph
                        and (candidate.target_item or "") == str(op.target_item or "")
                        and (candidate.target_special or "") == str(op.target_special or "")
                    )
                ),
                None,
            )
            sibling_plain_targets = (
                sorted(
                    {
                        int(candidate.target_paragraph)
                        for candidate in assignment.sparse_slot_bindings
                        if (
                            candidate.payload_slot_index == binding.payload_slot_index
                            and candidate.target_paragraph is not None
                            and not candidate.target_item
                        )
                    }
                )
                if binding is not None
                else []
            )
            # Preserve explicit source paragraph authority. This rebase rail is
            # only for item-like targets that were already heuristically
            # normalized into subsection/item shape earlier in elaboration.
            if "normalize_item_like_target" not in op.target_guessing_provenance_tags:
                rebased.append(op)
                continue
            if len(sibling_plain_targets) != 1 or sibling_plain_targets[0] == int(op.target_paragraph):
                rebased.append(op)
                continue
            new_paragraph = sibling_plain_targets[0]
            rebased.append(
                dc_replace(
                    op,
                    target_paragraph=new_paragraph,
                    lo=(
                        _lo_with_path_update(op.lo, subsection=str(new_paragraph))
                        if op.lo is not None
                        else None
                    ),
                    target_guessing_provenance_tags=tuple(
                        dict.fromkeys((*op.target_guessing_provenance_tags, "normalize_item_like_target"))
                    ),
                )
            )
            changed = True
            continue
        rebased.append(op)
    return rebased, changed


def _rebase_numbered_table_offset_targets_to_sparse_slot_labels(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> tuple[list[AmendmentOp], bool, list[dict[str, object]]]:
    """Rebase legal moment targets over Finlex table-as-subsection offsets."""
    changed = False
    rebased: list[AmendmentOp] = []
    details: list[dict[str, object]] = []
    for op in group_ops:
        mapped = assignment.for_op(op)
        mapped_label = _norm_num_token(mapped.label or "") if mapped is not None else ""
        original_sparse_label = (
            _norm_num_token(str(mapped.attrs.get("original_sparse_subsection_label") or ""))
            if mapped is not None
            else ""
        )
        if (
            mapped is None
            or not op.numbered_table_targets
            or op.target_paragraph is None
            or op.target_item
            or op.target_special
        ):
            rebased.append(op)
            continue
        source_target = int(op.target_paragraph)
        new_paragraph: int | None = None
        if mapped_label.isdigit() and int(mapped_label) == source_target + 1:
            new_paragraph = int(mapped_label)
        elif original_sparse_label == str(source_target):
            new_paragraph = source_target + 1
        if new_paragraph is None:
            rebased.append(op)
            continue
        rebased.append(
            dc_replace(
                op,
                target_paragraph=new_paragraph,
                lo=(
                    _lo_with_path_update(op.lo, subsection=str(new_paragraph))
                    if op.lo is not None
                    else None
                ),
                target_guessing_provenance_tags=tuple(
                    dict.fromkeys(
                        (
                            *op.target_guessing_provenance_tags,
                            "numbered_table_xml_subsection_offset",
                        )
                    )
                ),
            )
        )
        details.append(
            {
                "op_description": op.description(),
                "source_target_paragraph": int(op.target_paragraph),
                "structural_target_paragraph": new_paragraph,
                "payload_slot_label": str(mapped.label or ""),
                "original_sparse_subsection_label": original_sparse_label,
                "numbered_table_targets": list(op.numbered_table_targets),
            }
        )
        changed = True
    return rebased, changed, details


def _rebase_sparse_stale_predecessor_replace(
    ctx: PayloadElaborationContext,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> Tuple[List[AmendmentOp], bool, Dict[str, Any] | None]:
    """Rebase one sparse plain REPLACE when the live predecessor owns the slot.

    Some omission-preserving one-slot payloads target an official moment label
    that no longer exists as a visible live slot because an earlier repeal
    collapsed the visible subsection numbering. In that shape, the sparse
    payload body still belongs to the live predecessor slot, not the nominal
    same-numbered visible slot.

    Keep this rule intentionally narrow:
    - section target only
    - one plain REPLACE op only
    - payload shape exactly omission / subsection / omission
    - predecessor text clearly matches the replacement while the nominal target
      does not
    """
    if ctx.target_unit_kind != "section" or ctx.live_node is None or muutos_ir is None:
        return group_ops, False, None

    slot_kinds = [
        child.kind
        for child in muutos_ir.children
        if child.kind in {IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}
    ]
    if slot_kinds != [IRNodeKind.OMISSION, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION]:
        return group_ops, False, None

    plain_replace_ops = [
        op
        for op in group_ops
        if (
            op.op_type == "REPLACE"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    ]
    if len(group_ops) != 1 or len(plain_replace_ops) != 1:
        return group_ops, False, None

    op = plain_replace_ops[0]
    if op.target_paragraph is None or op.target_paragraph <= 1:
        return group_ops, False, None

    mapped = assignment.for_op(op)
    if mapped is None:
        return group_ops, False, None

    live_subsecs = [child for child in ctx.live_node.children if child.kind is IRNodeKind.SUBSECTION]
    exact_idx = next(
        (
            idx
            for idx, sub in enumerate(live_subsecs)
            if sub.label and normalized_label_key(sub.label) == str(op.target_paragraph)
        ),
        None,
    )
    if exact_idx is None or exact_idx == 0:
        return group_ops, False, None

    predecessor = live_subsecs[exact_idx - 1]
    nominal_target = live_subsecs[exact_idx]
    replacement_text = " ".join(irnode_to_text(mapped).split()).strip()
    predecessor_text = " ".join(irnode_to_text(predecessor).split()).strip()
    target_text = " ".join(irnode_to_text(nominal_target).split()).strip()
    if not replacement_text or len(replacement_text) < 40 or len(predecessor_text) < 40 or len(target_text) < 20:
        return group_ops, False, None

    prefix = replacement_text[:40]
    if not predecessor_text.startswith(prefix) or target_text.startswith(prefix):
        return group_ops, False, None

    pred_score = SequenceMatcher(None, replacement_text[:200], predecessor_text[:200]).ratio()
    target_score = SequenceMatcher(None, replacement_text[:200], target_text[:200]).ratio()
    if pred_score < 0.60 or pred_score <= target_score + 0.10:
        return group_ops, False, None

    new_paragraph = op.target_paragraph - 1
    rebased = dc_replace(
        op,
        target_paragraph=new_paragraph,
        lo=(
            _lo_with_path_update(op.lo, subsection=str(new_paragraph))
            if op.lo is not None
            else None
        ),
        target_guessing_provenance_tags=tuple(
            dict.fromkeys((*op.target_guessing_provenance_tags, "rebase_sparse_stale_predecessor"))
        ),
    )
    return (
        [rebased],
        True,
        {
            "from_paragraph": op.target_paragraph,
            "to_paragraph": new_paragraph,
            "predecessor_label": predecessor.label,
            "nominal_label": nominal_target.label,
            "pred_score": round(pred_score, 3),
            "target_score": round(target_score, 3),
            "op_description": op.description(),
        },
    )


def _rebase_duplicate_target_shifted_replace(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> Tuple[List[AmendmentOp], bool, Dict[str, Any] | None]:
    """Rebase duplicate-target sparse REPLACE ops onto their shifted successor slot.

    `_assign_duplicate_target_slot_ops()` already encodes one narrow legal shape:
    within the same group, one plain `INSERT N mom` and one plain
    `REPLACE N mom` can share the visible target so that:

    - the insert owns payload slot `N`, and
    - the replace owns the next payload slot (the successor moment after a
      same-group renumber/move of the old live `N mom`).

    Before this rebase the assignment exists only in the payload-slot map while
    the executable REPLACE op still carries the stale visible target paragraph.
    Replay then mutates the wrong live moment. Rebase only when the group also
    contains a plain `RENUMBER N mom`, which makes the shifted-successor family
    explicit rather than guessed.
    """
    renumber_targets = {
        int(op.target_paragraph)
        for op in group_ops
        if (
            op.op_type == "RENUMBER"
            and op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
        )
    }
    if not renumber_targets:
        return group_ops, False, None

    changed = False
    detail: Dict[str, Any] | None = None
    rebased_ops: List[AmendmentOp] = []
    for op in group_ops:
        if (
            op.op_type != "REPLACE"
            or op.target_paragraph is None
            or op.target_item
            or op.target_special
            or int(op.target_paragraph) not in renumber_targets
        ):
            rebased_ops.append(op)
            continue

        mapped = assignment.for_op(op)
        mapped_label = _norm_num_token(mapped.label or "") if mapped is not None else ""
        if not mapped_label.isdigit():
            rebased_ops.append(op)
            continue

        mapped_paragraph = int(mapped_label)
        if mapped_paragraph <= int(op.target_paragraph):
            rebased_ops.append(op)
            continue

        same_target_insert = next(
            (
                candidate
                for candidate in group_ops
                if (
                    candidate.op_type == "INSERT"
                    and candidate.target_paragraph == op.target_paragraph
                    and not candidate.target_item
                    and not candidate.target_special
                )
            ),
            None,
        )
        insert_mapped = assignment.for_op(same_target_insert) if same_target_insert is not None else None
        insert_label = _norm_num_token(insert_mapped.label or "") if insert_mapped is not None else ""
        if not insert_label.isdigit() or int(insert_label) != int(op.target_paragraph):
            rebased_ops.append(op)
            continue

        rebased = dc_replace(
            op,
            target_paragraph=mapped_paragraph,
            lo=_lo_with_path_update(op.lo, subsection=str(mapped_paragraph)) if op.lo is not None else None,
            target_guessing_provenance_tags=tuple(
                dict.fromkeys((*op.target_guessing_provenance_tags, "rebase_duplicate_target_shifted_replace"))
            ),
        )
        rebased_ops.append(rebased)
        changed = True
        if detail is None:
            detail = {
                "from_paragraph": op.target_paragraph,
                "to_paragraph": mapped_paragraph,
                "payload_slot_label": mapped_label,
                "op_description": op.description(),
            }

    return rebased_ops, changed, detail


def _expand_post_omission_tail_insert_subsections(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> List[AmendmentOp]:
    """Expand lone tail subsection inserts across unlabeled post-omission siblings.

    Uses ``ctx.live_node`` (Class 2: local subtree) to count live subsections.

    Some amendment bodies serialize the post-amendment tail of a section as:
    - omission marker for the preserved prefix
    - several unlabeled sibling subsections

    When the johtolause compiles to exactly one plain subsection INSERT at the
    live tail, binding only the first amendment subsection silently drops the
    remaining serialized siblings. Treat that shape as consecutive inserted
    moments so the existing subsection override map can preserve them.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return group_ops

    plain_insert_ops = [
        op
        for op in group_ops
        if (op.op_type == "INSERT" and op.target_paragraph is not None and not op.target_item and not op.target_special)
    ]
    if len(plain_insert_ops) != 1:
        return group_ops

    omission_idx = next(
        (idx for idx, child in enumerate(muutos_ir.children) if child.kind == IRNodeKind.OMISSION),
        None,
    )
    if omission_idx is None:
        return group_ops

    trailing_subs = [child for child in muutos_ir.children[omission_idx + 1 :] if child.kind == IRNodeKind.SUBSECTION]
    if len(trailing_subs) < 2:
        return group_ops

    master_sec = ctx.live_node
    if master_sec is None:
        return group_ops
    live_subsecs = [child for child in master_sec.children if child.kind == IRNodeKind.SUBSECTION]

    base_op = plain_insert_ops[0]
    if base_op.target_paragraph is None or base_op.target_paragraph != len(live_subsecs) + 1:
        return group_ops

    trailing_labels = [(child.label or "").strip() for child in trailing_subs]
    if any(label and not label.isdigit() for label in trailing_labels):
        return group_ops
    if any(trailing_labels):
        dense_local = [str(idx) for idx in range(1, len(trailing_subs) + 1)]
        dense_target = [str(base_op.target_paragraph + idx) for idx in range(len(trailing_subs))]
        if trailing_labels not in (dense_local, dense_target):
            return group_ops

    expanded_insert_ops: List[AmendmentOp] = []
    for idx in range(len(trailing_subs)):
        target_paragraph = base_op.target_paragraph + idx
        lo = (
            _lo_with_path_update(base_op.lo, subsection=str(target_paragraph), item=None)
            if base_op.lo is not None
            else None
        )
        expanded_insert_ops.append(
            dc_replace(
                base_op,
                op_id=base_op.op_id if idx == 0 else f"{base_op.op_id}_tail_{idx + 1}",
                target_paragraph=target_paragraph,
                lo=lo,
            )
        )
    expanded_ops: List[AmendmentOp] = []
    for op in group_ops:
        if op is base_op:
            expanded_ops.extend(expanded_insert_ops)
            continue
        expanded_ops.append(op)
    return expanded_ops


def _split_sparse_omission_single_subsection_across_consecutive_replaces(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> Tuple[Optional[IRNode], bool]:
    """Split one merged sparse payload subsection across consecutive replaces.

    Uses ``ctx.live_node`` (Class 2: local subtree) to access live subsections
    for text-anchor splitting.

    Some amendment bodies serialize a partial section replace as an omission
    marker plus one subsection whose text actually contains several consecutive
    changed live moments. If we bind that payload only to the trailing target,
    replay leaves the earlier live moment in place and duplicates the shared
    tract across neighboring subsections.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, False

    amend_subs = [child for child in muutos_ir.children if child.kind == IRNodeKind.SUBSECTION]
    if len(amend_subs) != 1 or not any(child.kind == IRNodeKind.OMISSION for child in muutos_ir.children):
        return muutos_ir, False

    replace_ops = sorted(
        [
            op
            for op in group_ops
            if (
                op.op_type == "REPLACE"
                and op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        ],
        key=lambda op: op.target_paragraph or 0,
    )
    if len(replace_ops) < 2:
        return muutos_ir, False

    targets = [int(op.target_paragraph or 0) for op in replace_ops]
    if any(curr != prev + 1 for prev, curr in pairwise(targets)):
        return muutos_ir, False

    master_sec = ctx.live_node
    if master_sec is None:
        return muutos_ir, False
    live_subs = {
        int(sub.label): sub
        for sub in master_sec.children
        if sub.kind == IRNodeKind.SUBSECTION and sub.label is not None and sub.label.isdigit()
    }
    if any(target not in live_subs for target in targets[1:]):
        return muutos_ir, False

    def _collapse_ws(text: str) -> str:
        return " ".join(text.split()).strip()

    def _sentence_start_offsets(text: str) -> list[int]:
        starts = [0]
        idx = 0
        while idx < len(text):
            if text[idx] not in ".!?":
                idx += 1
                continue
            cursor = idx + 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor].isupper():
                starts.append(cursor)
            idx = cursor
        return starts

    def _first_sentence(text: str) -> str:
        starts = _sentence_start_offsets(text)
        if len(starts) < 2:
            return text.strip()
        return text[: starts[1]].strip()

    def _sentence_at(text: str, start: int, starts: list[int]) -> str:
        next_start = next((candidate for candidate in starts if candidate > start), len(text))
        return text[start:next_start].strip()

    def _shared_word_prefix_len(left: str, right: str) -> int:
        count = 0
        for left_word, right_word in zip(left.split(), right.split(), strict=False):
            if left_word != right_word:
                break
            count += 1
        return count

    def _find_changed_live_sentence_anchor(
        payload: str,
        live_first_sentence: str,
        *,
        after: int,
    ) -> int | None:
        starts = _sentence_start_offsets(payload)
        for candidate in starts:
            if candidate <= after:
                continue
            payload_sentence = _sentence_at(payload, candidate, starts)
            if not payload_sentence:
                continue
            if _shared_word_prefix_len(payload_sentence, live_first_sentence) >= 8:
                return candidate
            if (
                len(payload_sentence) >= 48
                and len(live_first_sentence) >= 48
                and SequenceMatcher(None, payload_sentence, live_first_sentence).ratio() >= 0.72
            ):
                return candidate
        return None

    payload_text = _collapse_ws(irnode_to_text(amend_subs[0]))
    if not payload_text:
        return muutos_ir, False

    split_points: List[int] = []
    search_start = 0
    for target in targets[1:]:
        live_text = _collapse_ws(irnode_to_text(live_subs[target]))
        if not live_text:
            return muutos_ir, False
        first_sentence = _first_sentence(live_text)
        anchor = first_sentence if len(first_sentence) >= 32 else " ".join(live_text.split()[:8])
        if len(anchor.split()) < 4:
            return muutos_ir, False
        pos = payload_text.find(anchor, search_start + 1)
        if pos <= search_start:
            pos = _find_changed_live_sentence_anchor(payload_text, first_sentence, after=search_start)
            if pos is None:
                return muutos_ir, False
        if pos <= search_start:
            return muutos_ir, False
        split_points.append(pos)
        search_start = pos

    chunks: List[str] = []
    start = 0
    for pos in split_points + [len(payload_text)]:
        chunk = payload_text[start:pos].strip()
        if not chunk:
            return muutos_ir, False
        chunks.append(chunk)
        start = pos
    if len(chunks) != len(targets):
        return muutos_ir, False

    merged_sub = amend_subs[0]
    replacement_subs = [
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=str(target),
            attrs=dict(merged_sub.attrs),
            children=(IRNode(kind=IRNodeKind.CONTENT, text=chunk),),
        )
        for target, chunk in zip(targets, chunks, strict=True)
    ]
    new_children: List[IRNode] = []
    replaced = False
    for child in muutos_ir.children:
        if child is merged_sub:
            new_children.extend(replacement_subs)
            replaced = True
            continue
        new_children.append(child)
    if not replaced:
        return muutos_ir, False
    return _tops._with_children(muutos_ir, new_children), True


_SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL_RULE = (
    "ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL"
)


def _split_single_target_subsection_carried_live_tail(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> tuple[Optional[IRNode], dict[str, object] | None]:
    """Trim carried later live moments out of one explicitly targeted moment.

    Some historical Finlex amendment XML wraps the changed first moment together
    with the unchanged following moment in one source ``subsection`` even though
    the johtolause targets only ``N §:n 1 momentti`` and the source section then
    carries an omission marker.  After omission alignment, the following live
    moment also appears as its own subsection.  Keeping it inside the target slot
    makes replay duplicate that text under PIT materialization.

    This rule only uses typed scope and exact live-sibling text as evidence.  It
    does not infer from Finnish prose substrings.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, None
    if ctx.live_node is None or ctx.live_node.kind is not IRNodeKind.SECTION:
        return muutos_ir, None

    plain_targets = sorted(
        {
            int(op.target_paragraph)
            for op in group_ops
            if (
                op.op_type in {"REPLACE", "INSERT"}
                and op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        }
    )
    if len(plain_targets) != 1:
        return muutos_ir, None
    if any(
        op.target_paragraph is not None
        and (
            bool(op.target_item)
            or bool(op.target_special)
            or int(op.target_paragraph) != plain_targets[0]
        )
        for op in group_ops
    ):
        return muutos_ir, None

    target_label = str(plain_targets[0])
    amend_subs = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION and child.label]
    if len(amend_subs) != 1:
        return muutos_ir, None
    target_sub = next((child for child in amend_subs if _norm_num_token(child.label or "") == target_label), None)
    if target_sub is None:
        return muutos_ir, None
    target_index = next(idx for idx, child in enumerate(muutos_ir.children) if child is target_sub)
    if not any(child.kind is IRNodeKind.OMISSION for child in muutos_ir.children[target_index + 1 :]):
        return muutos_ir, None
    if len(target_sub.children) != 1 or target_sub.children[0].kind is not IRNodeKind.CONTENT:
        return muutos_ir, None

    def _collapse_ws(text: str) -> str:
        return " ".join(text.split()).strip()

    target_text = _collapse_ws(irnode_to_text(target_sub))
    if not target_text:
        return muutos_ir, None

    live_subs = [
        child
        for child in ctx.live_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label and _norm_num_token(child.label).isdigit()
    ]
    live_by_label = {_norm_num_token(child.label or ""): child for child in live_subs}
    carried_anchors: list[tuple[str, int]] = []
    for live_label, live_sub in live_by_label.items():
        if int(live_label) <= plain_targets[0]:
            continue
        live_text = _collapse_ws(irnode_to_text(live_sub))
        if not live_text:
            continue
        pos = target_text.find(live_text)
        if pos > 0:
            carried_anchors.append((live_label, pos))
    if not carried_anchors:
        return muutos_ir, None

    carried_label, split_pos = min(carried_anchors, key=lambda item: item[1])
    retained_text = target_text[:split_pos].strip()
    if not retained_text:
        return muutos_ir, None

    replacement_target = IRNode(
        kind=target_sub.kind,
        label=target_sub.label,
        text=target_sub.text,
        attrs=dict(target_sub.attrs),
        children=(IRNode(kind=IRNodeKind.CONTENT, text=retained_text),),
    )
    new_children = [replacement_target if child is target_sub else child for child in muutos_ir.children]
    return _tops._with_children(muutos_ir, new_children), {
        "target_subsection": target_label,
        "first_carried_subsection": carried_label,
        "carried_subsections": [label for label, _pos in carried_anchors],
    }


def _build_subsection_override_map(
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> SubsectionSlotMap:
    """Backward-compatible wrapper for the richer slot-assignment product."""
    return _build_subsection_slot_assignment(muutos_ir, group_ops).subsec_map


def _empty_subsection_slot_assignment() -> SubsectionSlotAssignmentResult:
    return SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
        binding_observations=(),
    )


def _build_subsection_slot_assignment(
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    surface: "PayloadSurface | None" = None,
) -> SubsectionSlotAssignmentResult:
    """Build the typed sparse subsection assignment product for one group.

    When ``surface`` is provided, passes it to ``_collect_subsection_slot_inputs``
    so pre-computed structural facts are used instead of re-scanning raw IRNode
    children.
    """
    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, group_ops, surface)
    if slot_inputs is None:
        return _empty_subsection_slot_assignment()
    return _assign_subsection_slots(slot_inputs)


def _assign_subsection_slots(
    slot_inputs: SubsectionSlotInputs,
) -> SubsectionSlotAssignmentResult:
    """Assign sparse subsection payload slots to logical changed moments."""
    state = SubsectionSlotAssignmentState(
        subsec_map=SubsectionSlotMap(),
        used_subs=set(),
    )

    _assign_insert_before_moved_same_target_slot_ops(slot_inputs, state)
    _assign_duplicate_target_slot_ops(slot_inputs, state)
    _assign_multi_paragraph_item_slot_ops(slot_inputs, state)
    _assign_unlabeled_table_item_row_ops(slot_inputs, state)
    _assign_item_matched_slot_ops(slot_inputs, state)
    _assign_shared_sparse_item_slot_ops(slot_inputs, state)
    _assign_dense_local_target_groups(slot_inputs, state)
    _assign_dense_local_slot_ops(slot_inputs, state)
    _assign_intro_slot_ops(slot_inputs, state)
    _assign_exact_plain_slot_ops(slot_inputs, state)
    _assign_numbered_table_offset_slot_ops(slot_inputs, state)
    _assign_numbered_table_companion_slot_ops(slot_inputs, state)
    _assign_fallback_plain_slot_ops(slot_inputs, state)
    _assign_item_prefix_slot_ops(slot_inputs, state)
    _assign_highest_insert_slot_op(slot_inputs, state)
    _assign_remaining_insert_slot_ops(slot_inputs, state)
    _assign_renumber_destination_slot_ops(slot_inputs, state)
    assigned_indices_by_op_id = {
        op_id: next(
            (idx for idx, sub in enumerate(slot_inputs.amend_subs) if sub is assigned_sub),
            -1,
        )
        for op_id, assigned_sub in state.subsec_map.items()
    }
    _normalize_assigned_intro_subparagraphs(state.subsec_map)

    all_slot_ops = sorted(
        [*slot_inputs.payload_subsec_ops, *slot_inputs.intro_subsec_ops, *slot_inputs.renumber_subsec_ops],
        key=lambda op: (
            op.target_paragraph or 0,
            op.target_item or "",
            op.target_special or "",
            op.op_type,
        ),
    )
    sparse_slot_bindings = [
        SparsePayloadSlotBinding(
            op_description=op.description(),
            op_type=str(op.op_type or ""),
            target_paragraph=op.target_paragraph,
            target_item=str(op.target_item or "") or None,
            target_special=str(op.target_special or "") or None,
            payload_slot_index=assigned_idx + 1,
            payload_slot_label=str(slot_inputs.amend_subs[assigned_idx].label or ""),
        )
        for op in all_slot_ops
        if (assigned_idx := assigned_indices_by_op_id.get(id(op), -1)) >= 0
    ]

    unassigned_payload_slots = [
        (f"{idx + 1}:{sub.label}" if str(sub.label or "") else f"{idx + 1}:(unlabeled)")
        for idx, sub in enumerate(slot_inputs.amend_subs)
        if idx not in state.used_subs
    ]

    # --- Admissible binding certificates ---
    # For each assigned binding, count how many payload slots share the same
    # normalized label as the assigned slot.  candidate_count == 1 means the
    # binding was deterministic ("single admissible"); > 1 means there were
    # competing slots with the same label ("ambiguous").  Ops assigned through
    # sequential/positional fallback (no exact label match) get "fallback".
    binding_certificates: List[AdmissibleBindingCoverage] = []
    binding_admissibility_by_op_id: Dict[str, str] = {}
    # Pre-build label → count map across all payload slots
    label_counts: Dict[str, int] = {}
    for sub in slot_inputs.amend_subs:
        norm_label = _norm_num_token(sub.label or "")
        if norm_label:
            label_counts[norm_label] = label_counts.get(norm_label, 0) + 1
    binding_rule_by_key: Dict[Tuple[int | None, str | None, str | None, int], str] = {}
    for op in all_slot_ops:
        assigned_idx = assigned_indices_by_op_id.get(id(op), -1)
        if assigned_idx < 0:
            continue
        key = (
            op.target_paragraph,
            str(op.target_item or "") or None,
            str(op.target_special or "") or None,
            assigned_idx + 1,
        )
        binding_rule = state.binding_rule_by_op_id.get(id(op))
        if binding_rule:
            binding_rule_by_key[key] = binding_rule

    for binding in sparse_slot_bindings:
        slot_label_norm = _norm_num_token(binding.payload_slot_label)
        target_str = str(binding.target_paragraph or "")
        # Determine if this was an exact-label match or a positional fallback
        binding_rule = binding_rule_by_key.get(
            (
                binding.target_paragraph,
                binding.target_item,
                binding.target_special,
                binding.payload_slot_index,
            )
        )
        if binding_rule in {
            "local_dense_subsection_numbering",
            "numbered_table_xml_subsection_offset",
            "numbered_table_companion_subsection_binding",
            "trailing_sparse_insert_binding",
            "renumber_destination_payload_slot",
        }:
            count = 1
            admissibility: Literal["single", "ambiguous", "fallback"] = "single"
        elif binding.target_item:
            # Item-targeted sparse payloads commonly reproduce one local
            # subsection slot that contains the changed item body plus omission
            # markers. In that shape the amendment-local slot label ("1") does
            # not encode the live target paragraph ("2"), so label mismatch is
            # not itself evidence of an ambiguous/fallback binding.
            count = 1
            admissibility = "single"
        elif slot_label_norm and slot_label_norm == target_str:
            count = label_counts.get(slot_label_norm, 1)
            admissibility = "single" if count == 1 else "ambiguous"
        else:
            # Positional/fallback assignment — label didn't match target
            count = len(slot_inputs.amend_subs)  # all slots were candidates
            admissibility = "fallback"
        source_statute = ""
        for op in all_slot_ops:
            if op.target_paragraph == binding.target_paragraph and str(op.target_item or "") == str(
                binding.target_item or ""
            ):
                source_statute = str(op.source_statute or "")
                break
        binding_certificates.append(
            AdmissibleBindingCoverage(
                slot_id=binding.payload_slot_index,
                amendment_id=source_statute,
                candidate_count=count,
                admissibility=admissibility,
            )
        )
        for op in all_slot_ops:
            if (
                (op.op_id or "").strip()
                and op.target_paragraph == binding.target_paragraph
                and str(op.target_item or "") == str(binding.target_item or "")
                and str(op.target_special or "") == str(binding.target_special or "")
                and assigned_indices_by_op_id.get(id(op), -1) + 1 == binding.payload_slot_index
            ):
                binding_admissibility_by_op_id[str(op.op_id)] = admissibility

    return SubsectionSlotAssignmentResult(
        subsec_map=state.subsec_map,
        sparse_slot_bindings=tuple(sparse_slot_bindings),
        used_subs=tuple(sorted(state.used_subs)),
        unassigned_payload_slots=tuple(unassigned_payload_slots),
        binding_certificates=tuple(binding_certificates),
        binding_observations=tuple(state.binding_observations),
        binding_admissibility_by_op_id=tuple(sorted(binding_admissibility_by_op_id.items())),
    )


def _collect_subsection_slot_inputs(
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    surface: "PayloadSurface | None" = None,
) -> Optional[SubsectionSlotInputs]:
    """Collect the typed sparse subsection-slot inputs for one group.

    When ``surface`` is provided, uses its pre-computed structural facts
    (``subsection_count``, ``omission_positions``, ``source_shape``) to
    short-circuit re-scanning.  Falls back to raw IRNode inspection for
    backward compatibility.
    """
    subsec_ops = [
        op
        for op in group_ops
        if (
            op.target_paragraph
            and op.op_type in ("REPLACE", "INSERT")
            and (not op.target_special or op.target_special == "johd")
        )
    ]
    renumber_subsec_ops = [
        op
        for op in group_ops
        if (
            op.target_paragraph
            and op.op_type == "RENUMBER"
            and not op.target_item
            and not op.target_special
            and _destination_subsection_label_for_renumber(op)
        )
    ]
    subsec_ops.sort(key=lambda op: (op.target_paragraph or 0, op.target_item or ""))
    renumber_subsec_ops.sort(
        key=lambda op: (
            int(_destination_subsection_label_for_renumber(op) or "0"),
            op.target_paragraph or 0,
        )
    )
    payload_subsec_ops = [op for op in subsec_ops if op.target_special != "johd"]
    intro_subsec_ops = [op for op in subsec_ops if op.target_special == "johd"]
    if muutos_ir is None or not (subsec_ops or renumber_subsec_ops):
        return None
    amend_subs = [child for child in muutos_ir.children if child.kind == IRNodeKind.SUBSECTION]
    table_labels_for_child_binding = frozenset(
        _norm_num_token(label)
        for op in subsec_ops
        for label in op.numbered_table_targets
        if _norm_num_token(label)
    )
    if table_labels_for_child_binding:
        amend_subs = [
            child
            for child in amend_subs
            if not (mentioned_numbered_table_labels(child) & table_labels_for_child_binding)
        ]
    # ``surface`` may describe the pre-normalized body.  Later payload
    # normalization can create owned subsection slots from malformed source
    # markup, so only the no-subsection case can short-circuit here.
    if surface is not None and surface.subsection_count == 0 and not amend_subs:
        return None
    duplicate_targets = sorted(
        {
            op.target_paragraph
            for op in payload_subsec_ops
            if op.target_paragraph is not None
            and any(
                other is not op and other.target_paragraph == op.target_paragraph and not other.target_item
                for other in payload_subsec_ops
            )
        }
    )
    return SubsectionSlotInputs(
        amend_subs=tuple(amend_subs),
        has_omission_slots=any(
            child.kind is IRNodeKind.OMISSION for child in (muutos_ir.children if muutos_ir else ())
        ),
        payload_subsec_ops=tuple(payload_subsec_ops),
        intro_subsec_ops=tuple(intro_subsec_ops),
        renumber_subsec_ops=tuple(renumber_subsec_ops),
        duplicate_targets=tuple(duplicate_targets),
    )


def _attach_terminal_section_omission_to_tail_subsection(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> Tuple[Optional[IRNode], SubsectionSlotAssignmentResult]:
    """Carry a section-level terminal omission onto the true live tail moment.

    Uses ``ctx.live_node`` (Class 2: local subtree).

    This is only sound when the highest targeted plain moment is also the live
    last subsection. Then a terminal section-level omission means "preserve the
    remaining tail of this moment", not "preserve later sibling moments".
    """
    if (
        target_unit_kind != "section"
        or muutos_ir is None
        or not muutos_ir.children
        or muutos_ir.children[-1].kind != IRNodeKind.OMISSION
    ):
        return muutos_ir, assignment

    # Guard: if the amendment section has a leading omission (before the first
    # explicit subsection), the terminal omission is a structural section-level
    # placeholder marking the end of the amendment's scope — not a subsection-level
    # tail.  Attaching it to the tail subsection in that case re-splices the old
    # subsection content (duplication).  Pattern: [omission, ..., subsection, ...,
    # omission] where the first slot is an omission.
    amend_slots = [c for c in muutos_ir.children if c.kind in (IRNodeKind.SUBSECTION, IRNodeKind.OMISSION)]
    if amend_slots and amend_slots[0].kind == IRNodeKind.OMISSION:
        return muutos_ir, assignment

    master_sec = ctx.live_node
    if master_sec is None:
        return muutos_ir, assignment
    live_subsecs = [child for child in master_sec.children if child.kind == IRNodeKind.SUBSECTION]
    if not live_subsecs:
        return muutos_ir, assignment

    highest_plain = max(
        (op for op in group_ops if not op.target_item and op.target_paragraph is not None),
        key=lambda op: op.target_paragraph or 0,
        default=None,
    )
    if highest_plain is None or highest_plain.target_paragraph != len(live_subsecs):
        return muutos_ir, assignment

    mapped = assignment.for_op(highest_plain)
    if mapped is None or any(child.kind == IRNodeKind.OMISSION for child in mapped.children):
        return muutos_ir, assignment

    patched = assignment.subsec_map.copy()
    patched[highest_plain] = _tops._with_children(
        mapped,
        list(mapped.children) + [IRNode(kind=IRNodeKind.OMISSION)],
    )
    return (
        _tops._with_children(muutos_ir, list(muutos_ir.children[:-1])),
        assignment.with_subsec_map(patched),
    )


def _drop_item_replaces_missing_from_sparse_payload(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> Tuple[List[AmendmentOp], List[SourcePathology], List[FailedOp]]:
    """Drop item replaces that have no recoverable body in sparse omission payloads.

    Real Finland amendment bodies sometimes combine:
    - a list-shaped section payload with omission markers
    - some item-level replaces from johtolause
    - but no reproduced amendment paragraph for one of those target items

    Carrying such an op into apply produces a deterministic failure even though
    the live section is addressable and the sparse payload can still be merged
    for the reproduced items. Treat the missing item body as source-incomplete
    sparse payload and drop only that item-level replace.
    """

    def _sub_has_item(sub: IRNode, item_norm: str) -> bool:
        compound_match = re.fullmatch(r"(\d+)([a-z])", item_norm)
        compound_parent_seen = False
        compound_flat_subitem_seen = False
        for child in sub.children:
            if child.kind == IRNodeKind.PARAGRAPH and child.label and leaf_label_identity_key(child.label) == item_norm:
                return True
            if (
                compound_match
                and child.kind == IRNodeKind.PARAGRAPH
                and child.label
                and leaf_label_identity_key(child.label) == compound_match.group(1)
            ):
                compound_parent_seen = True
                subitem_key = leaf_label_identity_key(compound_match.group(2), "subitem")
                if any(
                    grandchild.kind == IRNodeKind.SUBPARAGRAPH
                    and grandchild.label
                    and leaf_label_identity_key(grandchild.label, "subitem") == subitem_key
                    for grandchild in child.children
                ):
                    return True
            if (
                compound_match
                and child.kind == IRNodeKind.PARAGRAPH
                and child.label
                and leaf_label_identity_key(child.label, "subitem")
                == leaf_label_identity_key(compound_match.group(2), "subitem")
            ):
                compound_flat_subitem_seen = True
            if child.kind == IRNodeKind.PARAGRAPH:
                for grandchild in child.children:
                    if (
                        grandchild.kind == IRNodeKind.SUBPARAGRAPH
                        and grandchild.label
                        and leaf_label_identity_key(grandchild.label, "subitem") == item_norm
                    ):
                        return True
        if compound_parent_seen and compound_flat_subitem_seen:
            return True
        return False

    filtered: List[AmendmentOp] = []
    pathologies: List[SourcePathology] = []
    rejected: List[FailedOp] = []
    for op in group_ops:
        if op.op_type != "REPLACE" or not op.target_item:
            filtered.append(op)
            continue
        amend_sub = assignment.for_op(op)
        if amend_sub is None:
            filtered.append(op)
            continue
        has_omission = any(child.kind == IRNodeKind.OMISSION for child in amend_sub.children)
        if not has_omission:
            filtered.append(op)
            continue
        item_norm = leaf_label_identity_key(str(op.target_item))
        if _sub_has_item(amend_sub, item_norm):
            filtered.append(op)
            continue
        pathologies.append(
            build_sparse_item_body_missing_pathology(
                source_statute=op.source_statute,
                target_section=op.target_section,
                target_paragraph=str(op.target_paragraph or ""),
                target_item=str(op.target_item or ""),
            )
        )
        rejected.append(
            FailedOp.from_scope(
                amendment_id=op.source_statute or "",
                description=op.description(),
                reason="ELAB.DROP_ITEM_REPLACES_MISSING",
                target_section=str(op.target_section or ""),
                target_unit_kind=op.target_unit_kind,
                target_chapter=op.target_chapter,
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_item,
            )
        )
        continue

    return filtered, pathologies, rejected


def _drop_redundant_item_ops_claimed_by_sparse_slot(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
    live_sec: Optional[IRNode] = None,
) -> Tuple[List[AmendmentOp], List[FailedOp]]:
    """Drop redundant item INSERTs when the same slot already carries the item.

    An INSERT is redundant when another op in the same group will write the
    item as part of its own apply, so replay would otherwise insert it twice
    (deterministic duplicate-label corruption).

    Owner rules depend on slot shape and live-state:

    Plain moment REPLACE (no target_item, no target_special, same paragraph):
    owns the whole slot regardless of OMISSION presence.

    Sparse slot (has OMISSION children) + any co-slot REPLACE: only when the
    live target subsection has no paragraph items, and only when that co-slot
    REPLACE is a plain whole-moment owner. Intro/johd or item-specific REPLACE
    ops do not carry sibling items. If the live target already has items, the
    "content-only target, multi-para merge" apply path does not fire and the
    INSERT is needed.

    Non-omission slot + lettered-family base REPLACE: REPLACE "7" carries
    INSERT "7a" via the single-item content-only-target path.  An unrelated
    numeric REPLACE (e.g. REPLACE "8" alongside INSERT "10") is item-specific
    and does NOT constitute a slot owner.
    """

    def _live_para_has_items(target_para: int) -> bool:
        """Return True if the live subsection target_para has paragraph-level items."""
        if live_sec is None:
            return False
        label = str(target_para)
        for child in live_sec.children:
            if child.kind == IRNodeKind.SUBSECTION and child.label == label:
                return any(c.kind == IRNodeKind.PARAGRAPH for c in child.children)
        return False

    def _live_para_has_specific_item(target_para: int, item_norm: str) -> bool:
        """Return True if the live subsection target_para already has the specific item."""
        if live_sec is None:
            return False
        label = str(target_para)
        for child in live_sec.children:
            if child.kind == IRNodeKind.SUBSECTION and child.label == label:
                for c in child.children:
                    if c.kind == IRNodeKind.PARAGRAPH and c.label and leaf_label_identity_key(c.label) == item_norm:
                        return True
        return False

    def _sub_has_item(sub: IRNode, item_norm: str) -> bool:
        for child in sub.children:
            if child.kind == IRNodeKind.PARAGRAPH and child.label and leaf_label_identity_key(child.label) == item_norm:
                return True
            if child.kind == IRNodeKind.PARAGRAPH:
                for grandchild in child.children:
                    if (
                        grandchild.kind == IRNodeKind.SUBPARAGRAPH
                        and grandchild.label
                        and leaf_label_identity_key(grandchild.label, "subitem") == item_norm
                    ):
                        return True
        return False

    filtered: List[AmendmentOp] = []
    dropped: List[FailedOp] = []
    for op in group_ops:
        if op.op_type != "INSERT" or not op.target_item or op.target_paragraph is None:
            filtered.append(op)
            continue
        mapped = assignment.for_op(op)
        if mapped is None:
            filtered.append(op)
            continue

        item_norm = leaf_label_identity_key(str(op.target_item))
        if not _sub_has_item(mapped, item_norm):
            filtered.append(op)
            continue

        # When the payload slot has OMISSION markers, a REPLACE op will trigger
        # the "content-only target, multi-para merge" apply path whenever the
        # live target subsection is content-only, which applies ALL items from
        # the slot — including the INSERT target.  Any REPLACE co-owner in the
        # same slot is therefore sufficient to make the INSERT redundant.
        # Without OMISSION markers the multi-para merge path cannot fire; only a
        # plain moment REPLACE (no target_item, writes the whole slot) or a
        # REPLACE whose item is the lettered-family base of the INSERT target
        # (e.g. REPLACE "7" carries INSERT "7a" via the single-item slot path)
        # constitute a slot owner.  An unrelated numeric REPLACE (e.g. REPLACE
        # "8" alongside INSERT "10") is item-specific and does NOT carry the
        # INSERT target.
        slot_has_omission = any(child.kind == IRNodeKind.OMISSION for child in mapped.children)
        has_slot_owner = any(
            other is not op
            and assignment.for_op(other) is mapped
            and (
                (
                    # Plain moment REPLACE (no target_item) owns the whole slot.
                    # A plain moment INSERT does NOT carry the slot.
                    not other.target_item
                    and other.target_special is None
                    and other.op_type == "REPLACE"
                    and other.target_paragraph == op.target_paragraph
                )
                or (
                    # Sparse slot with OMISSION: a plain whole-moment REPLACE
                    # in the same slot can trigger the "content-only target,
                    # multi-para merge" apply path, but ONLY when the live
                    # target subsection has no paragraph items. Intro/johd or
                    # item-specific REPLACE ops do not carry sibling items.
                    slot_has_omission
                    and op.target_paragraph is not None
                    and not _live_para_has_items(op.target_paragraph)
                    and other.op_type == "REPLACE"
                    and not other.target_item
                    and other.target_special is None
                )
                or (
                    # Non-omission slot: REPLACE of the lettered-family base
                    # item carries the lettered-suffix via the single-item
                    # content-only-target path (e.g. REPLACE "7" + INSERT "7a").
                    # Item/kohta labels in Finnish law are digits + plain ASCII
                    # letter only (äöå never appear in section/kohta numbers).
                    # Guard: only suppress when the item ALREADY EXISTS in live
                    # state — a genuinely new item ("3a" inserted for the first
                    # time) is never carried by the base REPLACE and must be
                    # applied by its own INSERT op.
                    not slot_has_omission
                    and op.target_paragraph is not None
                    and _live_para_has_specific_item(op.target_paragraph, item_norm)
                    and other.op_type == "REPLACE"
                    and other.target_item is not None
                    # lawvm-regex: owning_parser compound-item-label shape predicate over a normalized label; lexer-shaped
                    and re.match(r"^\d+[a-z]$", item_norm)
                    and item_norm.rstrip("abcdefghijklmnopqrstuvwxyz")
                    == leaf_label_identity_key(str(other.target_item))
                )
            )
            for other in group_ops
        )
        if not has_slot_owner:
            filtered.append(op)
            continue

        dropped.append(
            FailedOp.from_scope(
                amendment_id=op.source_statute or "",
                description=op.description(),
                reason="ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT",
                target_section=str(op.target_section or ""),
                target_unit_kind=op.target_unit_kind,
                target_chapter=op.target_chapter,
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_item,
            )
        )

    return filtered, dropped


def _mixed_sparse_slot_cross_paragraph_bindings(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> List[ElaborationObservation]:
    """Report one sparse slot reused across item ops and a different plain moment.

    This shape is a strong signal that one reproduced sparse subsection is
    trying to carry both:
    - item-level edits for one logical subsection, and
    - a different plain subsection moment

    That is usually an elaboration boundary problem, not something replay/apply
    should rediscover from the final tree.
    """
    ops_by_slot: Dict[str, List[AmendmentOp]] = {}
    for op in group_ops:
        mapped = assignment.for_op(op)
        if mapped is None:
            continue
        slot_key = str(mapped.label or "")
        if not slot_key:
            continue
        ops_by_slot.setdefault(slot_key, []).append(op)

    observations: List[ElaborationObservation] = []
    for slot_label, slot_ops in sorted(ops_by_slot.items()):
        paragraphs = sorted({int(op.target_paragraph or 0) for op in slot_ops if op.target_paragraph is not None})
        if len(paragraphs) < 2:
            continue
        item_ops = [op for op in slot_ops if op.target_item]
        plain_ops = [op for op in slot_ops if not op.target_item and op.target_paragraph is not None]
        if not item_ops or not plain_ops:
            continue
        observations.append(
            _obs(
                "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
                "sparse_subsection_elaboration",
                slot_label=slot_label,
                target_paragraphs=paragraphs,
                item_ops=[op.description() for op in item_ops],
                plain_ops=[op.description() for op in plain_ops],
            )
        )
    return observations


def _split_mixed_sparse_slot_cross_paragraph_payloads(
    group_ops: List[AmendmentOp],
    assignment: SubsectionSlotAssignmentResult,
) -> tuple[SubsectionSlotAssignmentResult, list[ElaborationObservation]]:
    """Keep cross-paragraph item bodies out of a plain moment replacement.

    A sparse Finnish amendment body can print one amendment-local subsection
    slot that contains both:
    - a plain moment replacement, and
    - item bodies explicitly targeted to another moment by the preamble.

    Sharing the raw slot with the plain moment would authorize the item bodies
    to be written under that moment too. The item operations keep the original
    slot; only the plain moment receives a pruned payload.
    """

    ops_by_slot_identity: dict[int, list[AmendmentOp]] = {}
    slot_by_identity: dict[int, IRNode] = {}
    for op in group_ops:
        mapped = assignment.for_op(op)
        if mapped is None:
            continue
        slot_id = id(mapped)
        ops_by_slot_identity.setdefault(slot_id, []).append(op)
        slot_by_identity[slot_id] = mapped

    patched_map = assignment.subsec_map.copy()
    observations: list[ElaborationObservation] = []
    changed = False
    for slot_id, slot_ops in sorted(
        ops_by_slot_identity.items(),
        key=lambda pair: str(slot_by_identity[pair[0]].label or ""),
    ):
        mapped = slot_by_identity[slot_id]
        item_ops = [
            op
            for op in slot_ops
            if op.target_item and op.target_paragraph is not None
        ]
        plain_ops = [
            op
            for op in slot_ops
            if (
                op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        ]
        if not item_ops or not plain_ops:
            continue

        for plain_op in plain_ops:
            cross_item_labels = {
                leaf_label_identity_key(str(item_op.target_item))
                for item_op in item_ops
                if item_op.target_paragraph != plain_op.target_paragraph
            }
            if not cross_item_labels:
                continue
            kept_children: list[IRNode] = []
            removed_labels: list[str] = []
            for child in mapped.children:
                if (
                    child.kind is IRNodeKind.PARAGRAPH
                    and child.label
                    and leaf_label_identity_key(child.label) in cross_item_labels
                ):
                    removed_labels.append(str(child.label))
                    continue
                kept_children.append(child)
            if not removed_labels:
                continue
            pruned_slot = _tops._with_children(mapped, kept_children)
            # Record that this plain-moment slot was carved out of a slot whose
            # remaining (cross-paragraph) item bodies belong to ANOTHER moment.
            # A language-variant ("ruotsinkielinen sanamuoto") plain replace that
            # only rides the shared item payload owns no genuine content of its
            # own; the downstream language-variant shadow constraint matches the
            # plain op against the item op's slot, which the split would otherwise
            # sever by re-binding to this pruned copy. The marker lets the
            # constraint still recognize the shadowing relationship.
            pruned_slot = IRNode(
                kind=pruned_slot.kind,
                label=pruned_slot.label,
                text=pruned_slot.text,
                attrs={**pruned_slot.attrs, "lawvm_split_from_shared_item_slot": "1"},
                children=pruned_slot.children,
            )
            patched_map.assign(plain_op, pruned_slot)
            changed = True
            observations.append(
                _obs(
                    "ELAB.SPLIT_MIXED_SPARSE_SLOT_CROSS_PARAGRAPH_PAYLOAD",
                    "sparse_subsection_elaboration",
                    slot_label=str(mapped.label or ""),
                    plain_op=plain_op.description(),
                    plain_target_paragraph=plain_op.target_paragraph,
                    removed_item_labels=removed_labels,
                    item_ops=[op.description() for op in item_ops],
                )
            )

    if not changed:
        return assignment, observations
    return assignment.with_subsec_map(patched_map), observations


def _elaborate_sparse_subsection_payload(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
    source_pathologies: List[SourcePathology],
    surface: Optional[PayloadSurface] = None,
    prior_observations_for_completeness: tuple[ElaborationObservation, ...] = (),
) -> SparseSubsectionElaborationResult:
    """Run the sparse subsection elaboration tail as one typed phase.

    Uses ``ctx`` for live state access (Class 2: local subtree).

    When ``surface`` is provided, passes it to ``_build_subsection_slot_assignment``
    so pre-computed structural facts are used instead of re-scanning raw IRNode
    children.
    """
    observations: List[ElaborationObservation] = []
    rejected_ops: List[FailedOp] = []
    muutos_ir, removed_carried_subsections = _prune_carried_subsections_outside_single_target_moment_ir(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    if removed_carried_subsections:
        observations.append(
            _obs(
                "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT",
                "sparse_subsection_elaboration",
                removed_subsections=list(removed_carried_subsections),
            )
        )
    assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
    muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
        assignment,
    )
    group_ops, rebased_stale_predecessor, stale_predecessor_detail = _rebase_sparse_stale_predecessor_replace(
        ctx,
        muutos_ir,
        group_ops,
        assignment,
    )
    if rebased_stale_predecessor:
        observations.append(
            _obs(
                "ELAB.REBASE_SPARSE_STALE_PREDECESSOR",
                "sparse_subsection_elaboration",
                **(stale_predecessor_detail or {}),
            )
        )
        assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
        muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
            assignment,
        )
    group_ops, rebased_duplicate_target, duplicate_target_detail = _rebase_duplicate_target_shifted_replace(
        group_ops,
        assignment,
    )
    if rebased_duplicate_target:
        observations.append(
            _obs(
                "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE",
                "sparse_subsection_elaboration",
                **(duplicate_target_detail or {}),
            )
        )
        assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
        muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
            assignment,
        )
    rebased_ops, rebased_item_targets = _rebase_item_targets_to_sparse_slot_labels(group_ops, assignment)
    if rebased_item_targets:
        group_ops = rebased_ops
        assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
        muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
            assignment,
        )
    (
        rebased_ops,
        rebased_numbered_table_offset,
        numbered_table_offset_details,
    ) = _rebase_numbered_table_offset_targets_to_sparse_slot_labels(group_ops, assignment)
    if rebased_numbered_table_offset:
        group_ops = rebased_ops
        observations.append(
            _obs(
                _NUMBERED_TABLE_XML_SUBSECTION_OFFSET_RULE,
                "sparse_subsection_elaboration",
                rebased_count=len(numbered_table_offset_details),
                rebases=numbered_table_offset_details,
            )
        )
        assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
        muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
            assignment,
        )
    filtered_ops, sparse_item_pathologies, sparse_item_rejected_ops = _drop_item_replaces_missing_from_sparse_payload(
        group_ops,
        assignment,
    )
    if sparse_item_pathologies:
        rejected_ops.extend(sparse_item_rejected_ops)
        source_pathologies = list(source_pathologies) + sparse_item_pathologies
        dropped_labels = [pathology.target_label for pathology in sparse_item_pathologies if pathology.target_label]
        observations.append(
            _obs(
                "ELAB.DROP_ITEM_REPLACES_MISSING",
                "sparse_subsection_elaboration",
                dropped_count=len(sparse_item_pathologies),
                dropped_targets=dropped_labels,
            )
        )
    filtered_ops, redundant_item_ops = _drop_redundant_item_ops_claimed_by_sparse_slot(
        filtered_ops,
        assignment,
        ctx.live_node,
    )
    if redundant_item_ops:
        rejected_ops.extend(redundant_item_ops)
        observations.append(
            _obs(
                "ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT",
                "sparse_subsection_elaboration",
                dropped_count=len(redundant_item_ops),
                dropped_ops=[failed.description for failed in redundant_item_ops],
            )
        )
    if len(filtered_ops) != len(group_ops):
        group_ops = filtered_ops
        assignment = _build_subsection_slot_assignment(muutos_ir, group_ops, surface)
        muutos_ir, assignment = _attach_terminal_section_omission_to_tail_subsection(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
            assignment,
        )
    assignment, split_observations = _split_mixed_sparse_slot_cross_paragraph_payloads(
        group_ops,
        assignment,
    )
    observations.extend(split_observations)
    observations.extend(assignment.binding_observations)
    observations.extend(_mixed_sparse_slot_cross_paragraph_bindings(group_ops, assignment))
    # Emit observations for ambiguous bindings (C2: admissible binding certificate)
    ambiguous_certs = [cert for cert in assignment.binding_certificates if cert.admissibility != "single"]
    if ambiguous_certs:
        for cert in ambiguous_certs:
            observations.append(
                _obs(
                    "ELAB.AMBIGUOUS_BINDING",
                    "sparse_subsection_elaboration",
                    slot_id=cert.slot_id,
                    amendment_id=cert.amendment_id,
                    candidate_count=cert.candidate_count,
                    admissibility=cert.admissibility,
                )
            )
    if assignment.unassigned_payload_slots:
        observations.append(
            _obs(
                "ELAB.UNASSIGNED_SPARSE_SLOTS",
                "sparse_subsection_elaboration",
                unassigned_slots=assignment.unassigned_payload_slots,
                unassigned_count=len(assignment.unassigned_payload_slots),
            )
        )
    payload_completeness = _classify_payload_completeness(
        muutos_ir=muutos_ir,
        group_ops=group_ops,
        assignment=assignment,
        source_pathologies=source_pathologies,
        observations=[*prior_observations_for_completeness, *observations],
    )
    rejected_ops.extend(
        _unsupported_payload_rejected_ops(
            group_ops=group_ops,
            rejected_ops=rejected_ops,
            payload_completeness=payload_completeness,
        )
    )
    return SparseSubsectionElaborationResult(
        muutos_ir=muutos_ir,
        group_ops=tuple(group_ops),
        subsec_map=assignment.subsec_map,
        slot_assignment=assignment,
        sparse_slot_bindings=assignment.sparse_slot_bindings,
        unassigned_sparse_payload_slots=assignment.unassigned_payload_slots,
        source_pathologies=tuple(source_pathologies),
        rejected_ops=tuple(rejected_ops),
        elaboration_observations=tuple(observations),
        payload_completeness=payload_completeness,
    )


def _row_anchor(paragraph: IRNode) -> str:
    return _norm_row_anchor_text(paragraph.attrs.get("row_anchor", ""))


def _paragraph_text_row_anchor(paragraph: IRNode) -> str:
    return _norm_row_anchor_text(irnode_to_text(paragraph))


def _match_live_row_anchor(anchor: str, live_by_anchor: Dict[str, IRNode]) -> Optional[IRNode]:
    key = _norm_row_anchor_text(anchor)
    if not key:
        return None
    candidates = [key]
    if key.endswith("joen"):
        candidates.append(key[:-4] + "joki")
    if key.endswith("den"):
        candidates.append(key[:-3] + "s")
    if key.endswith("en"):
        candidates.append(key[:-2] + "i")
    if key.endswith("in"):
        candidates.append(key[:-2] + "i")
    if key.endswith("n"):
        candidates.append(key[:-1])
    seen_candidates: Set[str] = set()
    for candidate in candidates:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        exact = live_by_anchor.get(candidate)
        if exact is not None:
            return exact

    best: Optional[IRNode] = None
    best_score = 0.0
    second_best = 0.0
    for live_key, paragraph in live_by_anchor.items():
        if not live_key:
            continue
        for candidate in seen_candidates:
            score = SequenceMatcher(None, candidate, live_key).ratio()
            if score > best_score:
                second_best = best_score
                best_score = score
                best = paragraph
            elif score > second_best:
                second_best = score

    if best is None or best_score < 0.80 or best_score - second_best < 0.05:
        return None
    return best


def _single_subsection_row_table(section: Optional[IRNode]) -> Optional[IRNode]:
    if section is None or section.kind != IRNodeKind.SECTION:
        return None
    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return None
    subsection = subsections[0]
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    if not paragraphs:
        return None
    if not all(_row_anchor(paragraph) for paragraph in paragraphs):
        return None
    return subsection


def _single_subsection_paragraph_row_list(section: Optional[IRNode]) -> Optional[IRNode]:
    """Return a single-subsection paragraph-list when text can be row anchors.

    This is intentionally separate from ``_single_subsection_row_table``:
    paragraph text anchors are only used by explicit named-row operations, not
    by generic sparse payload alignment or broad section replacement.
    """
    if section is None or section.kind != IRNodeKind.SECTION:
        return None
    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return None
    subsection = subsections[0]
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    if not paragraphs:
        return None
    if any(_row_anchor(paragraph) for paragraph in paragraphs):
        return None
    if not all(_paragraph_text_row_anchor(paragraph) for paragraph in paragraphs):
        return None
    return subsection


def _strip_table_header_prefix(text: str) -> str:
    flat = " ".join((text or "").split())
    patterns = [
        r"^Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:\s*Käräjäoikeus\s+Kanslia(?:\s*\(s\s*=\s*sivukanslia\))?\s+Istunnot\s+",
        r"^Käräjäoikeus\s+Kanslia(?:\s*\(s\s*=\s*sivukanslia\)|\s*\(s=sivukanslia\))?\s+Istunnot\s+",
        r"^Käräjäoikeus\s+Kanslia\s+Istunnot(?:\s*\(s\s*=\s*sivukanslia\)|\s*\(s=sivukanslia\))?\s+",
    ]
    for pattern in patterns:
        stripped = re.sub(pattern, "", flat, flags=re.I)
        if stripped != flat:
            flat = stripped.strip()
            break
    flat = re.sub(r"^\(s\s*=\s*sivukanslia\)\s*", "", flat, flags=re.I)
    flat = re.sub(r"^\(s=sivukanslia\)\s*", "", flat, flags=re.I)
    return flat


def _table_row_cell_texts(row: IRNode) -> List[str]:
    return [irnode_to_text(cell).strip() for cell in row.children if cell.kind == IRNodeKind.CELL]


def _province_anchor_from_header(header: str) -> str:
    norm = _norm_row_anchor_text(header)
    for suffix in (" lääniä", " lääni"):
        if norm.endswith(suffix):
            return norm[: -len(suffix)].strip()
    return norm


def _section_province_table(section: Optional[IRNode]) -> Optional[IRNode]:
    if section is None or section.kind is not IRNodeKind.SECTION:
        return None
    subsections = [child for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return None
    tables: List[IRNode] = []
    for child in subsections[0].children:
        if child.kind != IRNodeKind.CONTENT:
            continue
        tables.extend(grandchild for grandchild in child.children if grandchild.kind == IRNodeKind.TABLE)
    if len(tables) != 1:
        return None
    table = tables[0]
    rows = [child for child in table.children if child.kind == IRNodeKind.ROW]
    if not rows:
        return None
    if not any(
        len(_table_row_cell_texts(row)) == 1 and "lääni" in _table_row_cell_texts(row)[0].lower()
        for row in rows
    ):
        return None
    return table


@dataclass(frozen=True, slots=True)
class _ProvinceTableLayout:
    prefix_rows: tuple[IRNode, ...]
    blocks: tuple[tuple[str, tuple[IRNode, ...]], ...]


def _layout_province_table(table: IRNode) -> Optional[_ProvinceTableLayout]:
    rows = tuple(child for child in table.children if child.kind == IRNodeKind.ROW)
    prefix_rows: List[IRNode] = []
    blocks: List[tuple[str, tuple[IRNode, ...]]] = []
    current_anchor: Optional[str] = None
    current_block: List[IRNode] = []
    for row in rows:
        cells = _table_row_cell_texts(row)
        if len(cells) == 1 and cells[0] and "lääni" in cells[0].lower():
            if current_anchor is not None:
                blocks.append((current_anchor, tuple(current_block)))
            current_anchor = _province_anchor_from_header(cells[0])
            current_block = [row]
            continue
        if current_anchor is None:
            prefix_rows.append(row)
            continue
        current_block.append(row)
    if current_anchor is not None:
        blocks.append((current_anchor, tuple(current_block)))
    if not blocks:
        return None
    return _ProvinceTableLayout(tuple(prefix_rows), tuple(blocks))


def _merge_province_table_rows(
    live_layout: _ProvinceTableLayout,
    amend_layout: _ProvinceTableLayout,
    named_rows: tuple[str, ...],
) -> Optional[tuple[IRNode, ...]]:
    named_set = {_norm_row_anchor_text(row) for row in named_rows if row}
    if not named_set:
        return None
    amend_by_anchor = {anchor: block for anchor, block in amend_layout.blocks}
    merged_rows: List[IRNode] = list(live_layout.prefix_rows or amend_layout.prefix_rows)
    changed = False
    for anchor, live_block in live_layout.blocks:
        if anchor in named_set and anchor in amend_by_anchor:
            merged_rows.extend(amend_by_anchor[anchor])
            changed = True
        else:
            merged_rows.extend(live_block)
    if not changed:
        return None
    return tuple(merged_rows)


def _replace_section_table_rows(section: IRNode, merged_rows: tuple[IRNode, ...]) -> IRNode:
    rebuilt_children: List[IRNode] = []
    for child in section.children:
        if child.kind != IRNodeKind.SUBSECTION:
            rebuilt_children.append(child)
            continue
        rebuilt_sub_children: List[IRNode] = []
        for sub_child in child.children:
            if sub_child.kind != IRNodeKind.CONTENT:
                rebuilt_sub_children.append(sub_child)
                continue
            rebuilt_content_children: List[IRNode] = []
            for content_child in sub_child.children:
                if content_child.kind != IRNodeKind.TABLE:
                    rebuilt_content_children.append(content_child)
                    continue
                rebuilt_content_children.append(
                    IRNode(
                        kind=IRNodeKind.TABLE,
                        attrs=dict(content_child.attrs),
                        children=merged_rows,
                    )
                )
            rebuilt_sub_children.append(
                IRNode(
                    kind=IRNodeKind.CONTENT,
                    text=sub_child.text,
                    attrs=dict(sub_child.attrs),
                    children=tuple(rebuilt_content_children),
                )
            )
        rebuilt_children.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(rebuilt_sub_children),
            )
        )
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=section.label,
        text=section.text,
        attrs=dict(section.attrs),
        children=tuple(rebuilt_children),
    )


def _rewrite_named_row_province_table_replaces(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> TableRowRewriteResult:
    """Merge partial province-table payloads into the live province layout.

    Regional ``lääniä koskevat kohdat`` formulas often ship sparse table bodies
    that only carry the claimed province blocks.  When ``named_row_targets`` are
    already owned on the op, rebuild the amendment section by substituting only
    those province blocks from the payload and retaining the unclaimed live
    blocks.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_table = _section_province_table(ctx.live_node)
    amend_table = _section_province_table(muutos_ir)
    if live_table is None or amend_table is None:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_layout = _layout_province_table(live_table)
    amend_layout = _layout_province_table(amend_table)
    if live_layout is None or amend_layout is None:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    rewritten = False
    rebuilt_muutos_ir = muutos_ir
    for op in group_ops:
        if not (
            op.op_type == "REPLACE"
            and op.target_paragraph is None
            and op.target_item is None
            and not op.target_special
        ):
            continue
        named_rows = tuple(row for row in op.named_row_targets if row)
        if not named_rows:
            continue
        merged_rows = _merge_province_table_rows(live_layout, amend_layout, named_rows)
        if merged_rows is None:
            continue
        rebuilt_muutos_ir = _replace_section_table_rows(rebuilt_muutos_ir, merged_rows)
        rewritten = True

    return TableRowRewriteResult(tuple(group_ops), rebuilt_muutos_ir, rewritten)


def _rewrite_named_row_table_replaces(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> TableRowRewriteResult:
    """Resolve named row-table replaces into concrete row item targets.

    Uses ``ctx.live_node`` (Class 2: local subtree).
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_sub = _single_subsection_row_table(ctx.live_node)
    if live_sub is None:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)
    amend_sub = _single_subsection_row_table(muutos_ir)

    live_by_anchor = {
        _row_anchor(paragraph): paragraph
        for paragraph in live_sub.children
        if paragraph.kind == IRNodeKind.PARAGRAPH and _row_anchor(paragraph)
    }
    amend_by_anchor = (
        {
            _row_anchor(paragraph): paragraph
            for paragraph in amend_sub.children
            if paragraph.kind == IRNodeKind.PARAGRAPH and _row_anchor(paragraph)
        }
        if amend_sub is not None
        else {}
    )
    content_only_children = [
        child for child in muutos_ir.children if child.kind == IRNodeKind.CONTENT and irnode_to_text(child).strip()
    ]
    if not live_by_anchor or (not amend_by_anchor and len(content_only_children) != 1):
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    rewritten_ops: List[AmendmentOp] = []
    relabelled_paragraphs: List[IRNode] = []
    changed = False
    for op in group_ops:
        if not (
            op.op_type == "REPLACE" and op.target_paragraph is None and op.target_item is None and not op.target_special
        ):
            rewritten_ops.append(op)
            continue

        named_rows = [row for row in op.named_row_targets if row]
        if not named_rows:
            rewritten_ops.append(op)
            continue

        matched = False
        content_text = (
            _strip_table_header_prefix(irnode_to_text(content_only_children[0]))
            if len(content_only_children) == 1
            else ""
        )
        for named_row in named_rows:
            live_paragraph = _match_live_row_anchor(named_row, live_by_anchor)
            if live_paragraph is None or not live_paragraph.label:
                continue
            amend_paragraph = _match_live_row_anchor(named_row, amend_by_anchor)
            if amend_paragraph is not None:
                relabelled_paragraphs.append(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label=live_paragraph.label,
                        text=amend_paragraph.text,
                        attrs=dict(amend_paragraph.attrs),
                        children=tuple(amend_paragraph.children),
                    )
                )
            elif content_text:
                relabelled_paragraphs.append(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label=live_paragraph.label,
                        attrs={"row_anchor": live_paragraph.attrs.get("row_anchor", "")},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=content_text),),
                    )
                )
            else:
                continue
            rewritten_ops.append(
                dc_replace(
                    op,
                    target_paragraph=1,
                    target_item=live_paragraph.label,
                    lo=(
                        _lo_with_path_update(
                            op.lo,
                            subsection="1",
                            item=str(live_paragraph.label),
                        )
                        if op.lo is not None
                        else None
                    ),
                )
            )
            matched = True

        if matched:
            changed = True
            continue

        rewritten_ops.append(op)

    if not changed:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    rebuilt_subsection_children = [
        child for child in (amend_sub.children if amend_sub is not None else []) if child.kind != IRNodeKind.PARAGRAPH
    ] + relabelled_paragraphs
    rebuilt_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=(amend_sub.label if amend_sub is not None else "1"),
        text=(amend_sub.text if amend_sub is not None else ""),
        attrs=(dict(amend_sub.attrs) if amend_sub is not None else {}),
        children=tuple(rebuilt_subsection_children),
    )
    rebuilt_section_children: List[IRNode] = []
    replaced = False
    for child in muutos_ir.children:
        if child.kind == IRNodeKind.SUBSECTION and not replaced:
            rebuilt_section_children.append(rebuilt_subsection)
            replaced = True
            continue
        if child.kind == IRNodeKind.SUBSECTION:
            continue
        rebuilt_section_children.append(child)
    if not replaced:
        rebuilt_section_children.append(rebuilt_subsection)
    rebuilt_muutos_ir = _tops._with_children(muutos_ir, rebuilt_section_children)
    return TableRowRewriteResult(tuple(rewritten_ops), rebuilt_muutos_ir, True)


def _rewrite_partial_whole_section_table_payload(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> TableRowRewriteResult:
    """Rewrite partial table bodies into row-level replaces before broad-op drop.

    Uses ``ctx.live_node`` (Class 2: local subtree).

    Finlex source XML sometimes encodes operative row changes as a single-table
    payload under a whole-section johtolause target (for example administrative
    decision tables under one `1 §`). If both the live section and amendment
    payload preserve stable row anchors, we can safely rewrite the broad op into
    row-level item replaces instead of treating the body as a malformed whole-
    section replacement.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    whole_ops = [
        op
        for op in group_ops
        if (
            op.target_paragraph is None and op.target_item is None and not op.target_special and op.op_type == "REPLACE"
        )
    ]
    if len(whole_ops) != 1:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_sub = _single_subsection_row_table(ctx.live_node)
    amend_sub = _single_subsection_row_table(muutos_ir)
    if live_sub is None or amend_sub is None:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_paragraphs = [child for child in live_sub.children if child.kind == IRNodeKind.PARAGRAPH]
    amend_paragraphs = [child for child in amend_sub.children if child.kind == IRNodeKind.PARAGRAPH]
    if len(amend_paragraphs) >= len(live_paragraphs):
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    live_by_anchor = {_row_anchor(paragraph): paragraph for paragraph in live_paragraphs if _row_anchor(paragraph)}

    relabelled_paragraphs: List[IRNode] = []
    rewritten_ops: List[AmendmentOp] = []
    base_op = whole_ops[0]
    for amend_paragraph in amend_paragraphs:
        anchor = _row_anchor(amend_paragraph)
        live_paragraph = _match_live_row_anchor(anchor, live_by_anchor)
        if live_paragraph is None or not live_paragraph.label:
            continue
        relabelled_paragraph = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=live_paragraph.label,
            text=amend_paragraph.text,
            attrs=dict(amend_paragraph.attrs),
            children=tuple(amend_paragraph.children),
        )
        relabelled_paragraphs.append(relabelled_paragraph)
        rewritten_ops.append(
            dc_replace(
                base_op,
                op_type="REPLACE",
                target_paragraph=1,
                target_item=live_paragraph.label,
                lo=(
                    _lo_with_path_update(
                        base_op.lo,
                        subsection="1",
                        item=str(live_paragraph.label),
                    )
                    if base_op.lo is not None
                    else None
                ),
            )
        )

    if not rewritten_ops:
        return TableRowRewriteResult(tuple(group_ops), muutos_ir, False)

    rebuilt_subsection_children = [
        child for child in amend_sub.children if child.kind != IRNodeKind.PARAGRAPH
    ] + relabelled_paragraphs
    rebuilt_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=amend_sub.label,
        text=amend_sub.text,
        attrs=dict(amend_sub.attrs),
        children=tuple(rebuilt_subsection_children),
    )
    rebuilt_section_children: List[IRNode] = []
    replaced = False
    for child in muutos_ir.children:
        if child.kind == IRNodeKind.SUBSECTION and not replaced:
            rebuilt_section_children.append(rebuilt_subsection)
            replaced = True
            continue
        if child.kind == IRNodeKind.SUBSECTION:
            continue
        rebuilt_section_children.append(child)
    rebuilt_muutos_ir = _tops._with_children(muutos_ir, rebuilt_section_children)

    preserved_ops = [op for op in group_ops if op not in whole_ops]
    return TableRowRewriteResult(tuple(preserved_ops + rewritten_ops), rebuilt_muutos_ir, True)


def _ordered_list_key(text: str) -> str:
    head = text.split("(", 1)[0].strip()
    head = re.sub(r"^(?:cis|trans)\s*[-–—]\s*", "", head, flags=re.IGNORECASE)
    return re.sub(r"[^0-9a-zåäö]+", " ", head.lower()).strip()


def _source_targets_internal_ordered_list(op: AmendmentOp) -> bool:
    source = getattr(getattr(op, "lo", None), "source", None)
    raw_text = re.sub(r"\s+", " ", str(getattr(source, "raw_text", "") or "").lower()).replace("\xa0", " ")
    return "§:ssä olevaa" in raw_text and "luetteloa" in raw_text and "seuraavasti" in raw_text


def _single_live_subsection_with_list_window(
    live_section: IRNode | None,
    anchor_text: str,
) -> tuple[IRNode, int, int] | None:
    if live_section is None:
        return None
    live_subsections = [child for child in live_section.children if child.kind is IRNodeKind.SUBSECTION]
    if len(live_subsections) != 1:
        return None
    live_subsection = live_subsections[0]
    anchor_key = re.sub(r"\s+", " ", anchor_text).strip().lower()
    if not anchor_key:
        return None

    start: int | None = None
    children = tuple(live_subsection.children)
    for idx, child in enumerate(children):
        if child.kind not in {IRNodeKind.CONTENT, IRNodeKind.INTRO}:
            continue
        child_key = re.sub(r"\s+", " ", irnode_to_text(child)).strip().lower()
        if child_key == anchor_key:
            start = idx + 1
            break
    if start is None:
        return None

    end = start
    while end < len(children):
        child = children[end]
        if child.kind is IRNodeKind.PARAGRAPH:
            end += 1
            continue
        if irnode_to_text(child).strip():
            break
        end += 1
    if end <= start:
        return None
    return live_subsection, start, end


def _sparse_internal_list_payload_slots(muutos_ir: IRNode | None) -> tuple[str, tuple[IRNode, ...]] | None:
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    slots = [child for child in muutos_ir.children if child.kind in {IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}]
    if len(slots) < 2 or slots[0].kind is not IRNodeKind.SUBSECTION:
        return None
    anchor_text = re.sub(r"\s+", " ", irnode_to_text(slots[0])).strip()
    if not anchor_text:
        return None
    has_omission_slot = any(slot.kind is IRNodeKind.OMISSION for slot in slots[1:])
    source_slot_labels = tuple(str(slot.label or "") for slot in slots if slot.kind is IRNodeKind.SUBSECTION)
    has_local_sparse_slot_labels = source_slot_labels == tuple(
        str(idx) for idx in range(1, len(source_slot_labels) + 1)
    )
    if not has_omission_slot and not has_local_sparse_slot_labels:
        return None
    payload_slots = tuple(
        slot
        for slot in slots[1:]
        if slot.kind is IRNodeKind.SUBSECTION and re.sub(r"\s+", " ", irnode_to_text(slot)).strip()
    )
    if not payload_slots:
        return None
    return anchor_text, payload_slots


def _infer_ordered_list_insert_targets(
    live_subsection: IRNode,
    start: int,
    end: int,
    payload_slots: tuple[IRNode, ...],
) -> tuple[str, ...] | None:
    live_items = [
        child
        for child in live_subsection.children[start:end]
        if child.kind is IRNodeKind.PARAGRAPH and child.label and str(child.label).isdigit()
    ]
    if len(live_items) != end - start:
        return None
    if not live_items:
        return None

    virtual_keys = [_ordered_list_key(irnode_to_text(item)) for item in live_items]
    if any(not key for key in virtual_keys):
        return None

    targets: list[str] = []
    for payload_slot in payload_slots:
        payload_text = re.sub(r"\s+", " ", irnode_to_text(payload_slot)).strip()
        payload_key = _ordered_list_key(payload_text)
        if not payload_key:
            return None
        insertion_indexes = [idx for idx, key in enumerate(virtual_keys) if payload_key < key]
        insert_idx = insertion_indexes[0] if insertion_indexes else len(virtual_keys)
        prev_key = virtual_keys[insert_idx - 1] if insert_idx > 0 else ""
        next_key = virtual_keys[insert_idx] if insert_idx < len(virtual_keys) else ""
        if (prev_key and payload_key <= prev_key) or (next_key and payload_key >= next_key):
            return None
        target_label = str(insert_idx + 1)
        targets.append(target_label)
        virtual_keys.insert(insert_idx, payload_key)
    return tuple(targets)


def _rewrite_internal_ordered_list_inserts(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    muutos_ir: IRNode | None,
    group_ops: list[AmendmentOp],
) -> tuple[list[AmendmentOp], IRNode | None, ElaborationObservation | None]:
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return group_ops, muutos_ir, None
    if len(group_ops) != 1:
        return group_ops, muutos_ir, None
    [op] = group_ops
    if (
        op.op_type != "REPLACE"
        or op.target_paragraph is not None
        or op.target_item
        or op.target_special
        or not _source_targets_internal_ordered_list(op)
    ):
        return group_ops, muutos_ir, None

    payload = _sparse_internal_list_payload_slots(muutos_ir)
    if payload is None:
        return group_ops, muutos_ir, None
    anchor_text, payload_slots = payload
    live_window = _single_live_subsection_with_list_window(ctx.live_node, anchor_text)
    if live_window is None:
        return group_ops, muutos_ir, None
    live_subsection, start, end = live_window
    targets = _infer_ordered_list_insert_targets(live_subsection, start, end, payload_slots)
    if targets is None or len(targets) != len(payload_slots):
        return group_ops, muutos_ir, None

    live_subsection_label = str(live_subsection.label or "1")
    if not live_subsection_label.isdigit():
        return group_ops, muutos_ir, None
    rewritten_payload_paragraphs = tuple(
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=target_label,
            attrs={_PRESERVE_IMPLICIT_PARAGRAPH_NUMBER_ATTR: "1"},
            children=(IRNode(kind=IRNodeKind.CONTENT, text=re.sub(r"\s+", " ", irnode_to_text(payload_slot)).strip()),),
        )
        for target_label, payload_slot in zip(targets, payload_slots, strict=True)
    )
    rewritten_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=live_subsection_label,
        children=rewritten_payload_paragraphs,
    )
    rewritten_muutos_ir = _tops._with_children(
        muutos_ir,
        [
            child
            for child in muutos_ir.children
            if child.kind not in {IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}
        ]
        + [rewritten_subsection],
    )
    rewritten_ops = [
        dc_replace(
            op,
            op_type="INSERT",
            target_paragraph=int(live_subsection_label),
            target_item=target_label,
            lo=(
                _lo_with_path_update(
                    op.lo,
                    subsection=live_subsection_label,
                    item=target_label,
                )
                if op.lo is not None
                else None
            ),
        )
        for target_label in targets
    ]
    return (
        rewritten_ops,
        rewritten_muutos_ir,
        _obs(
            _INTERNAL_ORDERED_LIST_INSERT_RULE,
            "payload_elaboration",
            source_op=op.description(),
            target_section=op.target_section or "",
            target_subsection=live_subsection_label,
            inserted_item_targets=list(targets),
            payload_slot_count=len(payload_slots),
        ),
    )


def _rewrite_named_row_table_repeals(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    group_ops: List[AmendmentOp],
) -> TableRowRewriteResult:
    """Resolve named row-table repeals into concrete row item targets.

    Uses ``ctx.live_node`` (Class 2: local subtree).
    """
    if target_unit_kind != "section":
        return TableRowRewriteResult(tuple(group_ops), None, False)

    live_sub = _single_subsection_row_table(ctx.live_node)
    use_text_anchors = False
    if live_sub is None:
        live_sub = _single_subsection_paragraph_row_list(ctx.live_node)
        use_text_anchors = live_sub is not None
    if live_sub is None:
        return TableRowRewriteResult(tuple(group_ops), None, False)

    if use_text_anchors:
        live_by_anchor = {
            _paragraph_text_row_anchor(paragraph): paragraph
            for paragraph in live_sub.children
            if paragraph.kind == IRNodeKind.PARAGRAPH and _paragraph_text_row_anchor(paragraph)
        }
    else:
        live_by_anchor = {
            _row_anchor(paragraph): paragraph
            for paragraph in live_sub.children
            if paragraph.kind == IRNodeKind.PARAGRAPH and _row_anchor(paragraph)
        }
    if not live_by_anchor:
        return TableRowRewriteResult(tuple(group_ops), None, False)

    rewritten_ops: List[AmendmentOp] = []
    changed = False
    for op in group_ops:
        if not (
            op.op_type == "REPEAL" and op.target_paragraph is None and op.target_item is None and not op.target_special
        ):
            rewritten_ops.append(op)
            continue

        named_rows = [row for row in op.named_row_targets if row]
        if not named_rows:
            rewritten_ops.append(op)
            continue

        matched_rows: List[IRNode] = []
        seen_labels: Set[str] = set()
        for named_row in named_rows:
            live_paragraph = _match_live_row_anchor(named_row, live_by_anchor)
            if live_paragraph is None or not live_paragraph.label or live_paragraph.label in seen_labels:
                matched_rows = []
                break
            matched_rows.append(live_paragraph)
            seen_labels.add(live_paragraph.label)

        if not matched_rows:
            rewritten_ops.append(op)
            continue

        changed = True
        for live_paragraph in matched_rows:
            rewritten_ops.append(
                dc_replace(
                    op,
                    target_paragraph=1,
                    target_item=live_paragraph.label,
                    lo=(
                        _lo_with_path_update(
                            op.lo,
                            subsection="1",
                            item=str(live_paragraph.label),
                        )
                        if op.lo is not None
                        else None
                    ),
                )
            )

    return TableRowRewriteResult(tuple(rewritten_ops), None, changed)


def _detect_sparse_subsection_tail_preservation_risk(
    ctx: PayloadElaborationContext,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List[AmendmentOp],
) -> List[SourcePathology]:
    """Detect sparse subsection payloads that omit untouched trailing live moments.

    Uses ``ctx.live_node`` (Class 2: local subtree).

    Motivating family: `1990/1105` / `1992/272`, where the johtolause only
    changes `3 § 1 mom` and `3 mom`, but the sparse section body stops at the
    new `3 mom` and does not restate the untouched trailing `4 mom`. Replay
    should preserve the untouched tail instead of silently trusting the sparse
    body as destructive evidence.
    """
    if target_unit_kind != "section" or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return []
    if not any(child.kind == IRNodeKind.OMISSION for child in muutos_ir.children):
        return []

    master_sec = ctx.live_node
    if master_sec is None:
        return []
    live_subsecs = [child for child in master_sec.children if child.kind == IRNodeKind.SUBSECTION]
    if not live_subsecs:
        return []

    plain_subsec_targets = sorted(
        {
            int(op.target_paragraph)
            for op in group_ops
            if (
                op.op_type in ("REPLACE", "INSERT")
                and op.target_paragraph is not None
                and not op.target_item
                and not op.target_special
            )
        }
    )
    if not plain_subsec_targets:
        return []

    max_target = max(plain_subsec_targets)
    if max_target >= len(live_subsecs):
        return []

    trailing_live = live_subsecs[max_target]
    trailing_text = " ".join(irnode_to_text(trailing_live).split())
    if (
        trailing_live.kind is IRNodeKind.SUBSECTION
        and not any(child.kind is IRNodeKind.PARAGRAPH for child in trailing_live.children)
        and trailing_text
        and trailing_text[:1].isalpha()
        and trailing_text[:1].isupper()
    ):
        return []

    amend_subsecs = [child for child in muutos_ir.children if child.kind == IRNodeKind.SUBSECTION]
    if not amend_subsecs:
        return []
    last_payload_label = max(
        (int(child.label) for child in amend_subsecs if child.label is not None and child.label.isdigit()),
        default=0,
    )
    if last_payload_label > max_target:
        return []
    if muutos_ir.children and muutos_ir.children[-1].kind == IRNodeKind.OMISSION:
        return []

    source_statute = next((op.source_statute for op in group_ops if op.source_statute), "")
    trailing_live = len(live_subsecs) - max_target
    return [
        build_destructive_shape_loss_risk_pathology(
            source_statute=source_statute,
            target_unit_kind=target_unit_kind,
            target_label=f"{target_norm} §",
            recovery_kind=RecoveryKind.SPARSE_SUBSECTION_TAIL_PRESERVED,
            live_sibling_count=trailing_live,
            payload_sibling_count=0,
        )
    ]


def elaborate_payload_against_live(
    ctx: PayloadElaborationContext,
    group_ops: List[AmendmentOp],
    muutos_ir: Optional[IRNode],
    standalone_section_targets: Set[str],
    *,
    foreign_scoped_standalone_section_targets: Set[str] | None = None,
    foreign_scoped_descendant_section_targets: Set[str] | None = None,
    foreign_scoped_replace_section_targets: Set[str] | None = None,
    foreign_scoped_replace_section_target_scopes: (
        frozenset[StandaloneSectionTarget] | None
    ) = None,
    recodification_transfer_context: bool = False,
    sparse_omission_tail_claims: tuple[SparseOmissionTailClaim, ...] = (),
    surface: Optional[PayloadSurface] = None,
) -> GroupPayloadNormalizationResult:
    """Normalize one group's payload and target ops against live state.

    Uses ``PayloadElaborationContext`` instead of raw ``master``.  All live
    state access goes through ``ctx.live_node`` (Class 2: local subtree),
    ``ctx`` indexes (Class 2), or ``ctx.lookups`` (Class 3: topology).

    When ``surface`` is provided, passes it to ``_elaborate_sparse_subsection_payload``
    so pre-computed structural facts are used instead of re-scanning raw IRNode
    children.
    """
    target_unit_kind = ctx.target_unit_kind
    target_norm = ctx.target_norm
    target_chapter = ctx.target_chapter
    observations: List[ElaborationObservation] = []
    table_row_repeals = _rewrite_named_row_table_repeals(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        group_ops,
    )
    group_ops = list(table_row_repeals.group_ops)
    table_row_named_replaces = _rewrite_named_row_table_replaces(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )
    group_ops = list(table_row_named_replaces.group_ops)
    muutos_ir = table_row_named_replaces.muutos_ir
    if not table_row_named_replaces.rewritten:
        province_table_named_replaces = _rewrite_named_row_province_table_replaces(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
        )
        group_ops = list(province_table_named_replaces.group_ops)
        muutos_ir = province_table_named_replaces.muutos_ir
        if province_table_named_replaces.rewritten:
            observations.append(
                _obs(
                    "ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE",
                    "payload_elaboration",
                    target_section=target_norm,
                    named_row_targets=[
                        row
                        for op in group_ops
                        for row in op.named_row_targets
                        if row
                    ],
                )
            )
    table_row_payload = _rewrite_partial_whole_section_table_payload(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )
    group_ops = list(table_row_payload.group_ops)
    muutos_ir = table_row_payload.muutos_ir
    group_ops, muutos_ir, internal_list_observation = _rewrite_internal_ordered_list_inserts(
        ctx,
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if internal_list_observation is not None:
        observations.append(internal_list_observation)
    rejected_ops: List[FailedOp] = []
    group_ops, source_pathologies, partial_whole_section_rejected_ops = _drop_suspicious_partial_whole_section_replaces(
        ctx.live_node,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )
    rejected_ops.extend(partial_whole_section_rejected_ops)
    if group_ops:
        group_ops, extra_pathologies, partial_subsection_shell_rejected_ops = _drop_suspicious_partial_subsection_shell_replaces(
            ctx.live_node,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
        )
        if extra_pathologies:
            source_pathologies = list(source_pathologies) + extra_pathologies
        rejected_ops.extend(partial_subsection_shell_rejected_ops)
    muutos_ir, pruned_sparse_tail_claims = prune_sparse_tail_claims_from_carrier(
        muutos_ir,
        sparse_omission_tail_claims,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=ctx.target_part,
    )
    if pruned_sparse_tail_claims:
        observations.append(
            _obs(
                SPARSE_OMISSION_TAIL_PRUNE_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter or "",
                pruned_claims=[claim.detail() for claim in pruned_sparse_tail_claims],
            )
        )
    if not group_ops:
        return GroupPayloadNormalizationResult(
            muutos_ir=muutos_ir,
            group_ops=tuple(group_ops),
            subsec_map=SubsectionSlotMap(),
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=SubsectionSlotMap(),
                sparse_slot_bindings=(),
                used_subs=(),
                unassigned_payload_slots=(),
            ),
            rejected_ops=tuple(rejected_ops),
            source_pathologies=tuple(source_pathologies),
            elaboration_observations=tuple(observations),
            payload_completeness=_classify_payload_completeness(
                muutos_ir=muutos_ir,
                group_ops=group_ops,
                assignment=SubsectionSlotAssignmentResult(
                    subsec_map=SubsectionSlotMap(),
                    sparse_slot_bindings=(),
                    used_subs=(),
                    unassigned_payload_slots=(),
                ),
                source_pathologies=source_pathologies,
                observations=observations,
            ),
        )
    muutos_ir, omission_aligned = _align_sparse_omission_subsections_to_live(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )
    pruning_result = _prune_container_payload_sections_shadowed_by_standalone_targets(
        ctx,
        target_unit_kind,
        target_norm,
        muutos_ir,
        standalone_section_targets,
        foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
        foreign_scoped_descendant_section_targets=foreign_scoped_descendant_section_targets,
        foreign_scoped_replace_section_targets=foreign_scoped_replace_section_targets,
        foreign_scoped_replace_section_target_scopes=foreign_scoped_replace_section_target_scopes,
        recodification_transfer_context=recodification_transfer_context,
        expected_heading_only=_container_pruning_is_expected_heading_only(group_ops),
        preserve_dense_new_container_payload=any(
            op.op_type == "INSERT"
            and op.target_unit_kind == target_unit_kind
            and _norm_num_token(op.target_section or op.target_chapter or op.target_part or "")
            == target_norm
            for op in group_ops
        )
        and (target_unit_kind != "chapter" or ctx.target_part is not None),
    )
    muutos_ir = pruning_result.muutos_ir
    payload_pruned = pruning_result.changed
    pruned_labels = list(pruning_result.pruned_labels)
    source_pathologies.extend(
        _detect_sparse_subsection_tail_preservation_risk(
            ctx,
            target_unit_kind,
            target_norm,
            target_chapter,
            muutos_ir,
            group_ops,
        )
    )
    if payload_pruned:
        observations.append(
            _obs(
                "ELAB.CONTAINER_PRUNED_SHADOWED",
                "group_payload_normalization",
                pruned_sections=pruned_labels,
                pruned_section_witnesses=[
                    witness.to_detail()
                    for witness in pruning_result.pruned_section_witnesses
                ],
                before_child_paths=list(pruning_result.before_child_paths),
                after_child_paths=list(pruning_result.after_child_paths),
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
        if group_ops and not _container_pruning_is_expected_frontend_split(ctx, group_ops):
            source_pathologies.append(
                build_container_membership_mismatch_pathology(
                    source_statute=group_ops[0].source_statute,
                    target_unit_kind=target_unit_kind,
                    target_label=(
                        f"{target_norm} {'luku' if target_unit_kind == 'chapter' else 'osa'}"
                    ),
                    pruned_sections=pruned_labels,
                )
            )
    if omission_aligned:
        observations.append(
            _obs(
                "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
    muutos_ir, sparse_combined_split = _split_sparse_omission_single_subsection_across_consecutive_replaces(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )
    if sparse_combined_split:
        observations.append(
            _obs(
                "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
    muutos_ir, carried_live_tail_detail = _split_single_target_subsection_carried_live_tail(
        ctx,
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if carried_live_tail_detail is not None:
        observations.append(
            _obs(
                _SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                **carried_live_tail_detail,
            )
        )
    muutos_ir, fused_restart_split = _split_fused_restarted_subsection_across_consecutive_replaces(
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if fused_restart_split:
        observations.append(
            _obs(
                "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
    muutos_ir, flattened_insert_tail_split = _split_flattened_insert_subsection_tail(
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if flattened_insert_tail_split:
        observations.append(
            _obs(
                "ELAB.SPLIT_FLATTENED_INSERT_SUBSECTION_TAIL",
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
    muutos_ir, split_target_intro_list_tail, split_target_intro_list_detail = (
        _fold_split_target_subsection_intro_list_tail(
            ctx,
            target_unit_kind,
            muutos_ir,
            group_ops,
        )
    )
    if split_target_intro_list_tail:
        observations.append(
            _obs(
                _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                **split_target_intro_list_detail,
            )
        )
    muutos_ir, multi_target_list_wrapup_detail = _fold_multi_target_subsection_list_wrapups(
        ctx,
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if multi_target_list_wrapup_detail is not None:
        observations.append(
            _obs(
                _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                **multi_target_list_wrapup_detail,
            )
        )
    muutos_ir, final_list_tail_detail = _split_final_list_item_trailing_subsection(
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if final_list_tail_detail is not None:
        observations.append(
            _obs(
                _SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                **final_list_tail_detail,
            )
        )
    heading_tagged_payload = _normalize_heading_tagged_subsection_payload(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    muutos_ir = heading_tagged_payload.muutos_ir
    if heading_tagged_payload.rewritten:
        observations.append(
            _obs(
                _HEADING_TAGGED_SUBSECTION_PAYLOAD_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_paragraph=heading_tagged_payload.target_paragraph,
                heading_text_chars=heading_tagged_payload.heading_text_chars,
                rule=_HEADING_TAGGED_SUBSECTION_PAYLOAD_RULE,
            )
        )
    leading_subsection_heading_payload = _normalize_leading_subsection_heading_payload(
        target_unit_kind,
        group_ops,
        muutos_ir,
    )
    muutos_ir = leading_subsection_heading_payload.muutos_ir
    if leading_subsection_heading_payload.rewritten:
        observations.append(
            _obs(
                _LEADING_SUBSECTION_HEADING_PAYLOAD_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                heading_text_chars=leading_subsection_heading_payload.heading_text_chars,
                shifted_subsection_count=leading_subsection_heading_payload.shifted_subsection_count,
                rule=_LEADING_SUBSECTION_HEADING_PAYLOAD_RULE,
            )
        )
    normalized_group_ops: list[AmendmentOp] = []
    normalized_item_like_target_rewrites: list[dict[str, object]] = []
    for op in group_ops:
        normalized = _normalize_item_like_target(ctx, op, muutos_ir, group_ops)
        normalized_group_ops.append(normalized)
        before_tags = set(op.target_guessing_provenance_tags)
        after_tags = set(normalized.target_guessing_provenance_tags)
        if "normalize_item_like_target" in after_tags.difference(before_tags):
            normalized_item_like_target_rewrites.append(
                {
                    "op_description": normalized.description(),
                    "target_paragraph": normalized.target_paragraph,
                    "target_item": normalized.target_item,
                }
            )
    group_ops = normalized_group_ops
    if normalized_item_like_target_rewrites:
        observations.append(
            _obs(
                "ELAB.NORMALIZE_ITEM_LIKE_TARGET",
                "group_payload_normalization",
                rewrite_count=len(normalized_item_like_target_rewrites),
                rewrites=normalized_item_like_target_rewrites,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
        observations.append(
            _obs(
                "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL",
                "group_payload_normalization",
                rewrite_count=len(normalized_item_like_target_rewrites),
                rewrites=normalized_item_like_target_rewrites,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
            )
        )
    muutos_ir, single_insert_tail_detail = _fold_single_insert_subsection_list_tail(
        ctx,
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if single_insert_tail_detail is not None:
        observations.append(
            _obs(
                _FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL_RULE,
                "group_payload_normalization",
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                **single_insert_tail_detail,
            )
        )
    group_ops = _expand_post_omission_tail_insert_subsections(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
    )

    sparse_elab = _elaborate_sparse_subsection_payload(
        ctx,
        target_unit_kind,
        target_norm,
        target_chapter,
        muutos_ir,
        group_ops,
        source_pathologies,
        surface,
        tuple(
            obs
            for obs in observations
            if obs.kind
            in {
                _SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL_RULE,
                _FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS_RULE,
            }
        ),
    )
    muutos_ir = sparse_elab.muutos_ir
    group_ops = list(sparse_elab.group_ops)
    subsec_map = sparse_elab.subsec_map
    source_pathologies = sparse_elab.source_pathologies
    observations.extend(sparse_elab.elaboration_observations or [])

    return GroupPayloadNormalizationResult(
        muutos_ir=muutos_ir,
        group_ops=tuple(group_ops),
        subsec_map=subsec_map,
        slot_assignment=sparse_elab.slot_assignment,
        sparse_slot_bindings=sparse_elab.sparse_slot_bindings,
        payload_pruned=payload_pruned,
        unassigned_sparse_payload_slots=sparse_elab.unassigned_sparse_payload_slots,
        rejected_ops=tuple(rejected_ops) + tuple(sparse_elab.rejected_ops),
        source_pathologies=tuple(source_pathologies),
        elaboration_observations=tuple(observations),
        payload_completeness=sparse_elab.payload_completeness,
    )


__all__ = [
    "ContainerPayloadPruningResult",
    "GroupPayloadNormalizationResult",
    "PayloadCompletenessWitness",
    "PrunedPayloadSectionWitness",
    "SubsectionSlotMap",
    "payload_elaboration_projection_from_group_result",
    "prepare_payload_surface",
    "elaborate_payload_against_live",
    "_collapse_intro_list_subsections_inside_section_ir",
    "_align_sparse_omission_subsections_to_live",
    "_normalize_item_like_target",
    "_prune_container_payload_sections_shadowed_by_standalone_targets",
]
