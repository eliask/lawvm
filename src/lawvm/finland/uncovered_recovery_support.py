"""Pure support contracts for Finnish uncovered-body recovery."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, cast

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_elaboration import PayloadCompletenessWitness
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.body_pairing import should_use_body_section
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, OpType, ResolvedOp
from lawvm.finland.target_selector_facades import fi_section_target
from lawvm.finland.uncovered_recovery_state import (
    FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
    UncoveredRecoveryGuards,
)


class ChapterPayloadOutcome(Enum):
    """Disposition of a section whose chapter payload is owned by an INSERT op."""

    NOT_APPLICABLE = "not_applicable"
    ADOPT = "adopt"
    OWNED = "owned"
    FUTURE_REPEAL_SKIP = "future_repeal_skip"


@dataclass(frozen=True, slots=True)
class ChapterPayloadVerdict:
    """Typed outcome of the chapter-payload ownership phase."""

    outcome: ChapterPayloadOutcome


@dataclass(frozen=True, slots=True)
class ChapterPayloadOwnershipRequest:
    """Inputs for the chapter-payload-owned section disposition decision."""

    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    guards: UncoveredRecoveryGuards
    section_present_in_chapter: bool
    future_repealed: bool


def evaluate_chapter_payload_ownership(
    request: ChapterPayloadOwnershipRequest,
) -> ChapterPayloadVerdict:
    """Decide how a chapter-payload-owned section is disposed of."""
    label = request.label
    amend_chapter_label = request.amend_chapter_label
    amend_part_label = request.amend_part_label
    guards = request.guards
    section_present_in_chapter = request.section_present_in_chapter
    future_repealed = request.future_repealed

    if not (
        amend_chapter_label
        and guards.is_chapter_payload_owned(
            part=amend_part_label,
            chapter=amend_chapter_label,
            section=label,
        )
    ):
        return ChapterPayloadVerdict(ChapterPayloadOutcome.NOT_APPLICABLE)
    if section_present_in_chapter:
        return ChapterPayloadVerdict(ChapterPayloadOutcome.OWNED)
    if future_repealed:
        return ChapterPayloadVerdict(ChapterPayloadOutcome.FUTURE_REPEAL_SKIP)
    return ChapterPayloadVerdict(ChapterPayloadOutcome.ADOPT)


@dataclass(frozen=True, slots=True)
class PreGuardVerdict:
    """Outcome of the uncovered-candidate pre-guard filter phase."""

    proceed: bool
    skip_reason: Optional[str]
    with_part: bool

    def __post_init__(self) -> None:
        if self.proceed and self.skip_reason is not None:
            raise ValueError("a proceeding pre-guard verdict must not carry a skip reason")
        if not self.proceed and self.skip_reason is None:
            raise ValueError("a blocking pre-guard verdict must name a skip reason")


@dataclass(frozen=True, slots=True)
class PreGuardRequest:
    """Inputs for the uncovered-candidate pre-resolution guard phase."""

    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    guards: UncoveredRecoveryGuards
    already_recovered: bool
    moved_section_destinations: Dict[str, str]
    bp_assignments: object


def evaluate_pre_guards(request: PreGuardRequest) -> PreGuardVerdict:
    """Run the read-only pre-resolution filters and return one typed verdict."""
    label = request.label
    amend_chapter_label = request.amend_chapter_label
    amend_part_label = request.amend_part_label
    guards = request.guards
    already_recovered = request.already_recovered
    moved_section_destinations = request.moved_section_destinations
    bp_assignments = request.bp_assignments

    if already_recovered:
        return PreGuardVerdict(False, "duplicate_recovered_candidate", with_part=False)

    move_destination = moved_section_destinations.get(label)
    if move_destination and amend_chapter_label != move_destination:
        return PreGuardVerdict(False, "moved_destination_mismatch", with_part=False)

    if amend_chapter_label and guards.is_relabel_destination(
        part=amend_part_label,
        chapter=amend_chapter_label,
        section=label,
    ):
        return PreGuardVerdict(False, "same_wave_relabel_destination_owned", with_part=True)

    if bp_assignments and not should_use_body_section(
        label, amend_chapter_label or "", cast("list", bp_assignments)
    ):
        return PreGuardVerdict(False, "body_pairing_guard", with_part=False)

    return PreGuardVerdict(True, None, with_part=False)


@dataclass(frozen=True, slots=True)
class UncoveredRopDraft:
    """Draft fields for one synthetic uncovered-body section operation."""

    op_type: OpType
    target_label: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    muutos_ir: IRNode
    op_id: str
    cross_ir: IRNode | None = None
    move_clause_target_unit_kind: TargetUnitKind | None = None


@dataclass(frozen=True, slots=True)
class ExistingSectionCandidate:
    """Live section candidate resolved for uncovered-body recovery."""

    existing: IRNode
    existing_path: tuple[tuple[str, str], ...]
    sec_ir: IRNode
    cross_ir: IRNode | None
    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    cross_chapter: bool


@dataclass(frozen=True, slots=True)
class NewSectionCandidate:
    """Uncovered-body section candidate with no resolvable live target."""

    sec_ir: IRNode
    cross_ir: IRNode | None
    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]


def build_uncovered_rop(
    draft: UncoveredRopDraft,
    *,
    amendment_id: str,
    op_source: Optional[OperationSource],
) -> ResolvedOp:
    """Build a ResolvedOp for an uncovered-body section operation."""
    am_op = AmendmentOp(
        op_id=draft.op_id,
        op_type=draft.op_type,
        **fi_section_target(
            draft.target_label,
            chapter=draft.target_chapter,
            part=draft.target_part,
        ),
        source_statute=amendment_id,
        move_clause_target_unit_kind=draft.move_clause_target_unit_kind,
        uncovered_body_recovery=True,
        witness_rule_id=FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
    )
    return ResolvedOp.from_amendment_op(
        am_op,
        muutos_ir=draft.muutos_ir,
        cross_ir=draft.cross_ir,
        target_unit_kind="section",
        target_norm=draft.target_label,
        target_chapter=draft.target_chapter,
        payload_completeness=uncovered_section_payload_completeness(
            op_type=draft.op_type,
            muutos_ir=draft.muutos_ir,
        ),
        op_source=op_source,
        target_address=LegalAddress(
            path=(
                ((("part", draft.target_part),) if draft.target_part else ())
                + ((("chapter", draft.target_chapter),) if draft.target_chapter else ())
                + (("section", draft.target_label),)
            )
        ),
    )


def uncovered_disposition_for_op_id(op_id: str) -> tuple[str, str]:
    """Map a recovered op_id to its (disposition, reason) audit pair."""
    if op_id.startswith("uncov_chapter_adopt_"):
        return "ADOPT", "chapter_payload_adopt"
    if op_id.startswith("uncovered_move_replace_"):
        return "REPLACE", "declared_move_destination_replace"
    if op_id.startswith("uncovered_replace_"):
        return "REPLACE", "replace_existing"
    if op_id.startswith("uncovered_merge_"):
        return "MERGE", "omission_merge"
    if op_id.startswith("uncovered_table_merge_"):
        return "MERGE", "numbered_table_target_merge"
    if op_id.startswith("uncovered_insert_"):
        return "INSERT", "new_insert"
    return "INSERT", "recovered"


def section_heading_text(node: IRNode) -> str:
    """Normalized lowercase heading text of a section IR node, or empty string."""
    heading = next((c for c in node.children if c.kind is IRNodeKind.HEADING), None)
    return " ".join(irnode_to_text(heading).split()).strip().lower() if heading is not None else ""


def next_letter_label(label: str) -> Optional[str]:
    """Next letter-suffixed sibling label, e.g. ``18`` -> ``18a``."""
    norm = _norm_num_token(label)
    match = re.fullmatch(r"(\d+)([a-z]?)", norm)
    if not match:
        return None
    base, suffix = match.groups()
    if not suffix:
        return f"{base}a"
    if suffix == "z":
        return None
    return f"{base}{chr(ord(suffix) + 1)}"


def part_label_from_path(path: tuple[tuple[str, str], ...] | None) -> Optional[str]:
    """First part label in a resolved provision path, if any."""
    if not path:
        return None
    return next((lbl for kind, lbl in path if kind == "part"), None)


def section_scoped_group_ops(
    ops: list[AmendmentOp],
    *,
    label: str,
    amend_chapter_label: Optional[str],
    amend_part_label: Optional[str],
) -> list[AmendmentOp]:
    """Return paragraph-scoped compiled ops owned by one uncovered section."""
    label_norm = _norm_num_token(label)
    chapter_norm = _norm_num_token(amend_chapter_label) if amend_chapter_label else None
    part_norm = _norm_num_token(amend_part_label) if amend_part_label else None
    scoped: list[AmendmentOp] = []
    for op in ops:
        if _norm_num_token(op.target_section) != label_norm:
            continue
        if chapter_norm and op.target_chapter and _norm_num_token(op.target_chapter) != chapter_norm:
            continue
        if part_norm and op.target_part and _norm_num_token(op.target_part) != part_norm:
            continue
        if op.target_paragraph is None or op.target_item or op.target_special:
            continue
        if op.op_type not in (OpType.REPLACE, OpType.INSERT, OpType.REPEAL):
            continue
        scoped.append(op)
    return scoped


def synthetic_moment_group_ops(
    *,
    label: str,
    amend_chapter_label: Optional[str],
    amend_part_label: Optional[str],
    johto_moment_targets: dict[str, frozenset[int]],
) -> list[AmendmentOp]:
    """Synthesize paragraph-scoped REPLACE ops from johto moment mentions."""
    moments = johto_moment_targets.get(_norm_num_token(label), ())
    if not moments:
        return []
    return [
        AmendmentOp(
            op_type=OpType.REPLACE,
            **fi_section_target(
                label,
                chapter=amend_chapter_label,
                part=amend_part_label,
                subsection=moment,
            ),
        )
        for moment in sorted(moments)
    ]


def merge_group_ops_for_section(
    ops: list[AmendmentOp],
    *,
    label: str,
    amend_chapter_label: Optional[str],
    amend_part_label: Optional[str],
    johto_moment_targets: dict[str, frozenset[int]],
) -> list[AmendmentOp]:
    """Choose paragraph-scoped ops that steer section-level omission merges."""
    scoped = section_scoped_group_ops(
        ops,
        label=label,
        amend_chapter_label=amend_chapter_label,
        amend_part_label=amend_part_label,
    )
    if scoped:
        return scoped
    return synthetic_moment_group_ops(
        label=label,
        amend_chapter_label=amend_chapter_label,
        amend_part_label=amend_part_label,
        johto_moment_targets=johto_moment_targets,
    )


def uncovered_section_payload_completeness(
    *,
    op_type: OpType,
    muutos_ir: IRNode,
) -> PayloadCompletenessWitness | None:
    """Classify uncovered section-root payload ownership for replay tail masking."""
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    if op_type != "REPLACE":
        return None
    return PayloadCompletenessWitness(
        kind="complete",
        reasons=("uncovered_whole_section_replace",),
        tail_policy="replace_if_target_scope_requires",
    )


# Compatibility aliases while callers migrate to the public names above.
_evaluate_chapter_payload_ownership = evaluate_chapter_payload_ownership
_evaluate_pre_guards = evaluate_pre_guards
_build_uncovered_rop = build_uncovered_rop
_uncovered_disposition_for_op_id = uncovered_disposition_for_op_id
_section_heading_text = section_heading_text
_next_letter_label = next_letter_label
_part_label_from_path = part_label_from_path
_uncovered_section_payload_completeness = uncovered_section_payload_completeness
