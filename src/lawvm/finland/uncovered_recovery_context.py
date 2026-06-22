"""Context extraction for Finnish uncovered-body recovery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic
from lawvm.finland.johto_scope_mentions import (
    collect_johto_chapter_scope_mentions,
    collect_johto_insert_section_targets,
    collect_johto_insert_subsection_section_targets,
    collect_johto_mentioned_section_labels,
    collect_johto_moment_targets,
    collect_johto_named_subprovision_section_targets,
    collect_johto_numbered_table_targets_by_section,
    collect_johto_whole_section_targets,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.uncovered_recovery_state import (
    UncoveredSectionKey,
    uncovered_section_key,
)


@dataclass(frozen=True, slots=True)
class UncoveredRecoveryContext:
    """Preamble and same-wave ownership context for uncovered-body recovery."""

    johto_mentioned_labels: frozenset[str]
    johto_mentioned_replaced_chapters: frozenset[str]
    moved_section_destinations: dict[str, str]
    relabel_destination_sections: frozenset[UncoveredSectionKey]
    owned_chapter_labels: frozenset[str]
    source_owned_insert_chapter_labels: frozenset[str]
    part_insert_labels: frozenset[str]
    johto_whole_section_targets: frozenset[str]
    johto_insert_section_targets: frozenset[str]
    johto_named_subprovision_section_targets: frozenset[str]
    johto_insert_subsection_section_targets: frozenset[str]
    johto_moment_targets: dict[str, frozenset[int]]
    johto_numbered_table_targets: dict[str, frozenset[str]]


def _part_insert_labels_from_ops(ops: Iterable[AmendmentOp]) -> frozenset[str]:
    labels: set[str] = set()
    for op in ops:
        if op.op_type != "INSERT" or op.target_unit_kind != "part":
            continue
        label = _norm_num_token(str(op.target_section or op.target_part or ""))
        if label:
            labels.add(label)
    return frozenset(labels)


def build_uncovered_recovery_context(
    *,
    preamble_text: str,
    ops: Iterable[AmendmentOp],
    new_chapter_labels: set[str] | None,
) -> UncoveredRecoveryContext:
    """Extract read-only johto/relabel context used by uncovered recovery.

    This owns the preamble-derived section/chapter mentions and same-wave
    section-renumber destinations that pre-resolution guards use. It does not
    decide whether to recover a candidate; it only materializes the evidence
    surface into named fields.
    """
    johto_mentioned_labels: set[str] = set()
    johto_whole_section_targets: frozenset[str] = frozenset()
    johto_insert_section_targets: frozenset[str] = frozenset()
    johto_named_subprovision_section_targets: frozenset[str] = frozenset()
    johto_insert_subsection_section_targets: frozenset[str] = frozenset()
    johto_moment_targets: dict[str, frozenset[int]] = {}
    johto_numbered_table_targets: dict[str, frozenset[str]] = {}
    johto_mentioned_new_chapters: set[str] = set()
    johto_mentioned_replaced_chapters: set[str] = set()
    moved_section_destinations: dict[str, str] = {}
    relabel_destination_sections: set[UncoveredSectionKey] = set()
    owned_chapter_labels: set[str] = set(new_chapter_labels or ())

    for op in ops:
        if (
            op.op_type != "RENUMBER"
            or op.target_unit_kind != "section"
            or op.target_paragraph is not None
            or op.target_item
            or op.target_special
            or op.lo is None
            or op.lo.destination is None
            or not op.lo.destination.path
        ):
            continue
        dest_map = {
            kind: _norm_num_token(label)
            for kind, label in op.lo.destination.path
            if label
        }
        dest_section = dest_map.get("section")
        dest_chapter = dest_map.get("chapter") or _norm_num_token(op.target_chapter or "")
        dest_part = dest_map.get("part") or _norm_num_token(op.target_part or "")
        if dest_part:
            dest_part_arabic = _roman_label_to_arabic(dest_part)
            if dest_part_arabic is not None:
                dest_part = str(dest_part_arabic)
        if not dest_section or not dest_chapter:
            continue
        relabel_destination_sections.add(
            uncovered_section_key(
                part=dest_part,
                chapter=dest_chapter,
                section=dest_section,
            )
        )

    johto_text = preamble_text
    if johto_text:
        johto_mentioned_labels.update(collect_johto_mentioned_section_labels(johto_text))
        johto_whole_section_targets = collect_johto_whole_section_targets(johto_text)
        johto_insert_section_targets = collect_johto_insert_section_targets(johto_text)
        johto_named_subprovision_section_targets = (
            collect_johto_named_subprovision_section_targets(johto_text)
        )
        johto_whole_section_targets = (
            johto_whole_section_targets - johto_named_subprovision_section_targets
        )
        johto_insert_subsection_section_targets = (
            collect_johto_insert_subsection_section_targets(johto_text)
        )
        johto_moment_targets = collect_johto_moment_targets(johto_text)
        johto_numbered_table_targets = collect_johto_numbered_table_targets_by_section(johto_text)
        chapter_mentions = collect_johto_chapter_scope_mentions(johto_text)
        johto_mentioned_new_chapters.update(chapter_mentions.new_chapter_labels)
        owned_chapter_labels.update(chapter_mentions.moved_destination_chapter_labels)
        johto_mentioned_replaced_chapters.update(chapter_mentions.replaced_chapter_labels)
        moved_section_destinations.update(
            {
                moved.section_label: moved.destination_chapter_label
                for moved in chapter_mentions.moved_section_destinations
            }
        )
    owned_chapter_labels.update(johto_mentioned_new_chapters)

    return UncoveredRecoveryContext(
        johto_mentioned_labels=frozenset(johto_mentioned_labels),
        johto_mentioned_replaced_chapters=frozenset(johto_mentioned_replaced_chapters),
        moved_section_destinations=moved_section_destinations,
        relabel_destination_sections=frozenset(relabel_destination_sections),
        owned_chapter_labels=frozenset(owned_chapter_labels),
        source_owned_insert_chapter_labels=frozenset(johto_mentioned_new_chapters),
        part_insert_labels=_part_insert_labels_from_ops(ops),
        johto_whole_section_targets=johto_whole_section_targets,
        johto_insert_section_targets=johto_insert_section_targets,
        johto_named_subprovision_section_targets=johto_named_subprovision_section_targets,
        johto_insert_subsection_section_targets=johto_insert_subsection_section_targets,
        johto_moment_targets=johto_moment_targets,
        johto_numbered_table_targets=johto_numbered_table_targets,
    )
