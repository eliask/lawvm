"""Candidate iteration policy for Finnish uncovered-body recovery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Tuple

from lawvm.core.coverage import CoverageGap
from lawvm.finland.body_coverage import BodyCoveragePayloadRef
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, OpType


class UncoveredCandidateProcessor(Protocol):
    """The mutation surface needed by candidate iteration."""

    def record_skip(
        self,
        reason: str,
        label: str,
        amend_chapter_label: Optional[str],
        amend_part_label: Optional[str] = None,
    ) -> None: ...

    def process_section_candidate(self, candidate: "UncoveredSectionCandidate") -> None: ...


@dataclass(frozen=True, slots=True)
class PegOwnedSectionTargets:
    """Section targets already owned by PEG-compiled operations."""

    by_chapter: frozenset[Tuple[Optional[str], str]]
    labels: frozenset[str]
    descendant_by_chapter: frozenset[Tuple[Optional[str], str]]
    descendant_labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class UncoveredSectionCandidate:
    """Typed candidate identity for uncovered-body section recovery."""

    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    source_ref: BodyCoveragePayloadRef


def _is_payloadless_section_relabel_source(op: AmendmentOp, label: str) -> bool:
    """True when a whole-section RENUMBER's source label is not a payload claim."""
    if (
        op.op_type != OpType.RENUMBER
        or op.target_cols.target_unit_kind != "section"
        or op.target_cols.target_paragraph is not None
        or op.target_cols.target_item
        or op.target_cols.target_special
        or op.lo is None
        or op.lo.destination is None
        or not op.lo.destination.path
    ):
        return False
    destination_leaf = op.lo.destination.leaf_label()
    return bool(destination_leaf) and _norm_num_token(destination_leaf) != label


def peg_owned_section_targets(ops: Iterable[AmendmentOp]) -> PegOwnedSectionTargets:
    """Return section labels already owned by deterministic PEG output."""
    targeted_sections: set[Tuple[Optional[str], str]] = set()
    targeted_labels: set[str] = set()
    descendant_sections: set[Tuple[Optional[str], str]] = set()
    descendant_labels: set[str] = set()
    for op in ops:
        if op.target_cols.target_unit_kind == "section" and op.target_cols.target_section:
            label = _norm_num_token(op.target_cols.target_section)
            if _is_payloadless_section_relabel_source(op, label):
                continue
            chapter = op.target_cols.target_chapter
            if op.target_cols.target_paragraph is not None or op.target_cols.target_item or op.target_cols.target_special:
                descendant_sections.add((chapter, label))
                descendant_labels.add(label)
            else:
                targeted_sections.add((chapter, label))
                targeted_labels.add(label)
    return PegOwnedSectionTargets(
        by_chapter=frozenset(targeted_sections),
        labels=frozenset(targeted_labels),
        descendant_by_chapter=frozenset(descendant_sections),
        descendant_labels=frozenset(descendant_labels),
    )


def run_uncovered_candidate_iteration(
    *,
    supplemental_candidates: Iterable[CoverageGap],
    peg_owned_targets: PegOwnedSectionTargets,
    processor: UncoveredCandidateProcessor,
) -> None:
    """Enumerate coverage gaps and dispatch valid section candidates.

    Non-section gaps and malformed gap records are ignored. PEG-owned sections
    are recorded as explicit skip findings because deterministic PEG output
    outranks uncovered-body recovery.
    """
    for gap in supplemental_candidates:
        unit = gap.unit
        if unit.kind != "section":
            continue
        label = unit.observed_label or ""
        if not label:
            continue
        chapter = unit.parent_label
        if (chapter, label) in peg_owned_targets.by_chapter:
            processor.record_skip("peg_owned_same_chapter", label, chapter)
            continue
        if label in peg_owned_targets.labels:
            processor.record_skip("peg_owned_label_collision", label, chapter)
            continue
        if (chapter, label) in peg_owned_targets.descendant_by_chapter:
            processor.record_skip("peg_owned_descendant_same_chapter", label, chapter)
            continue
        if label in peg_owned_targets.descendant_labels:
            processor.record_skip("peg_owned_descendant_label_collision", label, chapter)
            continue
        source_ref = unit.payload_ref
        if not isinstance(source_ref, BodyCoveragePayloadRef) or source_ref.unit_kind != "section":
            processor.record_skip("missing_source_payload_ref", label, chapter)
            continue
        processor.process_section_candidate(
            UncoveredSectionCandidate(
                label=label,
                amend_chapter_label=chapter,
                amend_part_label=source_ref.part,
                source_ref=source_ref,
            )
        )
