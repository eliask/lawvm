"""Instruction-semantics work queue for New Zealand payload witnesses.

This surface is diagnostic only. It makes the next lowering work explicit
without emitting canonical effects, replaying candidates, or claiming oracle
agreement.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, assert_never

from lawvm.core.evidence_contracts import CorpusOperationEvidenceRow, CorpusRowStatus
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
    TARGET_RECOVERED,
    TARGET_RESOLVED,
    TargetResolutionCoverage,
)
from lawvm.new_zealand.effect_readiness import (
    NZEffectReadinessReport,
    NZInstructionSemanticCandidateFamily,
    NZInstructionSemanticCandidateStatus,
    build_archived_work_effect_readiness_surface,
    build_effect_readiness_surface,
)
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface
from lawvm.new_zealand.payload_surface import (
    NZPayloadInstructionSafety,
    NZPayloadInstructionShape,
    NZPayloadSurfaceReport,
    build_archived_work_payload_surface,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode, parse_archived_work_latest
from lawvm.new_zealand.text_comparison import (
    normalized_nz_inline_contains,
    normalized_nz_inline_occurrence_count,
)
from lawvm.core.quirks_disposition import QuirksDisposition


class NZWorkQueueStatus(StrEnum):
    """Closed queue disposition for an NZ instruction-workqueue row.

    A ``StrEnum`` (not a bare ``str``) so the dispatch over the upstream
    instruction-semantic candidate status is exhaustive (a new candidate status
    becomes a type error here, not a silent fall-through to ``BLOCKED``).
    Members subclass ``str`` and their ``value`` equals the legacy wire string,
    so JSON serialization (including ``Counter`` keys) stays byte-identical.
    """

    CANDIDATE = "candidate"
    REVIEW = "review"
    BLOCKED = "blocked"
    NOT_REQUIRED = "not_required"


class NZTextSubstitutionStatus(StrEnum):
    """Closed classification status produced by the text-substitution helpers.

    The vocabulary is closed and owned entirely by the ``_classify_*`` helpers
    in this module. Members subclass ``str`` so ``status.startswith(...)`` and
    ``==`` comparisons and JSON serialization stay byte-identical. ``NONE``
    (``""``) is never produced by a helper; it exists only as the falsy default
    for the row field whose later siblings carry defaults.
    """

    NONE = ""
    AMBIGUOUS = "ambiguous"
    MATCHED = "matched"
    MATCHED_IN_MULTI_CLAUSE_PAYLOAD = "matched_in_multi_clause_payload"
    MISMATCH = "mismatch"
    NO_MATCH = "no_match"
    NOT_TEXT_SUBSTITUTION_SHAPE = "not_text_substitution_shape"
    BLOCKED_MULTI_CLAUSE_NO_MATCHING_TARGET = "blocked_multi_clause_no_matching_target"
    BLOCKED_MULTI_CLAUSE_PAYLOAD = "blocked_multi_clause_payload"
    BLOCKED_MULTI_CLAUSE_TARGET_AMBIGUOUS = "blocked_multi_clause_target_ambiguous"
    BLOCKED_MULTIPLE_OCCURRENCE_TEXT_SUBSTITUTION = "blocked_multiple_occurrence_text_substitution"
    BLOCKED_OMITTING_SUBSTITUTING_PARSE_FAILED = "blocked_omitting_substituting_parse_failed"
    BLOCKED_PAYLOAD_MULTIPLICITY = "blocked_payload_multiplicity"
    BLOCKED_STRUCTURAL_OMITTING_SUBSTITUTING_PAYLOAD = "blocked_structural_omitting_substituting_payload"
    BLOCKED_STRUCTURAL_REPLACEMENT_PAYLOAD = "blocked_structural_replacement_payload"
    BLOCKED_TARGET_CITATION_MISMATCH = "blocked_target_citation_mismatch"
    BLOCKED_TEXT_SUBSTITUTION_PARSE_FAILED = "blocked_text_substitution_parse_failed"
    BLOCKED_TYPED_AMEND_IN_AMBIGUOUS_TARGET = "blocked_typed_amend_in_ambiguous_target"
    BLOCKED_TYPED_AMEND_IN_INSERT_ANCHOR_UNPARSED = "blocked_typed_amend_in_insert_anchor_unparsed"
    BLOCKED_TYPED_AMEND_IN_NOT_SUBSTITUTION_VERB = "blocked_typed_amend_in_not_substitution_verb"
    BLOCKED_TYPED_AMEND_IN_PAYLOAD_INCOMPLETE = "blocked_typed_amend_in_payload_incomplete"
    CANDIDATE_DIRECT_EACH_PLACE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION = (
        "candidate_direct_each_place_omitting_substituting_text_substitution"
    )
    CANDIDATE_DIRECT_EACH_PLACE_TEXT_SUBSTITUTION = "candidate_direct_each_place_text_substitution"
    CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_INSERT = "candidate_direct_each_place_typed_amend_in_insert"
    CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_OMIT_DELETION = (
        "candidate_direct_each_place_typed_amend_in_omit_deletion"
    )
    CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_TEXT_SUBSTITUTION = (
        "candidate_direct_each_place_typed_amend_in_text_substitution"
    )
    CANDIDATE_DIRECT_MULTI_CLAUSE_EACH_PLACE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION = (
        "candidate_direct_multi_clause_each_place_omitting_substituting_text_substitution"
    )
    CANDIDATE_DIRECT_MULTI_CLAUSE_EACH_PLACE_TEXT_SUBSTITUTION = (
        "candidate_direct_multi_clause_each_place_text_substitution"
    )
    CANDIDATE_DIRECT_MULTI_CLAUSE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION = (
        "candidate_direct_multi_clause_omitting_substituting_text_substitution"
    )
    CANDIDATE_DIRECT_MULTI_CLAUSE_TEXT_SUBSTITUTION = "candidate_direct_multi_clause_text_substitution"
    CANDIDATE_DIRECT_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION = (
        "candidate_direct_omitting_substituting_text_substitution"
    )
    CANDIDATE_DIRECT_SINGLE_TEXT_SUBSTITUTION = "candidate_direct_single_text_substitution"
    CANDIDATE_DIRECT_TYPED_AMEND_IN_INSERT = "candidate_direct_typed_amend_in_insert"
    CANDIDATE_DIRECT_TYPED_AMEND_IN_OMIT_DELETION = "candidate_direct_typed_amend_in_omit_deletion"
    CANDIDATE_DIRECT_TYPED_AMEND_IN_TEXT_SUBSTITUTION = "candidate_direct_typed_amend_in_text_substitution"


class NZTextSubstitutionSubfamily(StrEnum):
    """Closed subfamily for a text-substitution candidate. ``NONE`` (``""``) marks absent.

    The empty member is falsy, so the ``subfamily or "__none__"`` summary idiom
    and ``""`` JSON serialization stay byte-identical.
    """

    NONE = ""
    DIRECT_SINGLE_TEXT_SUBSTITUTION = "direct_single_text_substitution"
    DIRECT_EACH_PLACE_TEXT_SUBSTITUTION = "direct_each_place_text_substitution"


class NZTextSubstitutionScope(StrEnum):
    """Closed occurrence scope of a text substitution. ``NONE`` (``""``) marks absent."""

    NONE = ""
    INLINE_TEXT_SINGLE_OCCURRENCE = "inline_text_single_occurrence"
    INLINE_TEXT_EACH_PLACE = "inline_text_each_place"


class NZTargetCitationStatus(StrEnum):
    """Closed target-citation match status. ``NONE`` (``""``) marks absent."""

    NONE = ""
    MATCHED = "matched"
    MATCHED_IN_MULTI_CLAUSE_PAYLOAD = "matched_in_multi_clause_payload"
    MISMATCH = "mismatch"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"


class NZStructuralSubfamilyStatus(StrEnum):
    """Closed report-only structural-subfamily status. ``NONE`` (``""``) marks absent."""

    NONE = ""
    REVIEW_RETROSPECTIVE_INCORPORATED_PAYLOAD = "review_retrospective_incorporated_payload"
    BLOCKED_AMBIGUOUS_AMEND_REPLACE_PAYLOAD = "blocked_ambiguous_amend_replace_payload"
    BLOCKED_CROSS_HEADING_INSERT_PAYLOAD_NOT_LOWERED = "blocked_cross_heading_insert_payload_not_lowered"
    BLOCKED_DEFINITION_ALPHABETICAL_INSERT_PAYLOAD_NOT_LOWERED = (
        "blocked_definition_alphabetical_insert_payload_not_lowered"
    )
    BLOCKED_HISTORICAL_INSERTED_NOTE_PAYLOAD_NOT_LOWERED = "blocked_historical_inserted_note_payload_not_lowered"
    BLOCKED_INCORPORATED_AMENDMENT_STUB_PAYLOAD = "blocked_incorporated_amendment_stub_payload"
    BLOCKED_MIXED_REPEAL_SUBSTITUTE_PAYLOAD_NOT_LOWERED = "blocked_mixed_repeal_substitute_payload_not_lowered"
    BLOCKED_MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD_NOT_LOWERED = (
        "blocked_mixed_text_and_structural_insert_payload_not_lowered"
    )
    BLOCKED_MULTI_SECTION_REPLACE_PAYLOAD_NOT_LOWERED = "blocked_multi_section_replace_payload_not_lowered"
    BLOCKED_PARAGRAPH_AFTER_INSERT_PAYLOAD_NOT_LOWERED = "blocked_paragraph_after_insert_payload_not_lowered"
    BLOCKED_SCHEDULE_INDIRECTION_PAYLOAD = "blocked_schedule_indirection_payload"
    BLOCKED_SECTION_AFTER_INSERT_PAYLOAD_NOT_LOWERED = "blocked_section_after_insert_payload_not_lowered"
    BLOCKED_STRUCTURAL_AMEND_PAYLOAD_NOT_LOWERED = "blocked_structural_amend_payload_not_lowered"
    BLOCKED_STRUCTURAL_INSERT_PAYLOAD_NOT_LOWERED = "blocked_structural_insert_payload_not_lowered"
    BLOCKED_STRUCTURAL_REPLACE_PAYLOAD_NOT_LOWERED = "blocked_structural_replace_payload_not_lowered"
    BLOCKED_SUBSECTION_AFTER_INSERT_PAYLOAD_NOT_LOWERED = "blocked_subsection_after_insert_payload_not_lowered"
    BLOCKED_TEXT_INSERT_PAYLOAD_NOT_LOWERED = "blocked_text_insert_payload_not_lowered"
    BLOCKED_WHOLE_PROVISION_SUBSTITUTION_PAYLOAD_NOT_LOWERED = (
        "blocked_whole_provision_substitution_payload_not_lowered"
    )


class NZStructuralSubfamily(StrEnum):
    """Closed report-only structural subfamily. ``NONE`` (``""``) marks absent."""

    NONE = ""
    AMBIGUOUS_AMEND_REPLACE_PAYLOAD = "ambiguous_amend_replace_payload"
    CROSS_HEADING_INSERT_PAYLOAD = "cross_heading_insert_payload"
    DEFINITION_ALPHABETICAL_INSERT_PAYLOAD = "definition_alphabetical_insert_payload"
    DIRECT_AMEND_PAYLOAD = "direct_amend_payload"
    DIRECT_INSERT_PAYLOAD = "direct_insert_payload"
    DIRECT_REPLACE_PAYLOAD = "direct_replace_payload"
    DIRECT_TEXT_INSERT_PAYLOAD = "direct_text_insert_payload"
    HISTORICAL_INSERTED_NOTE_PAYLOAD = "historical_inserted_note_payload"
    INCORPORATED_AMENDMENT_STUB_PAYLOAD = "incorporated_amendment_stub_payload"
    MIXED_REPEAL_SUBSTITUTE_PAYLOAD = "mixed_repeal_substitute_payload"
    MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD = "mixed_text_and_structural_insert_payload"
    MULTI_SECTION_REPLACE_PAYLOAD = "multi_section_replace_payload"
    PARAGRAPH_AFTER_INSERT_PAYLOAD = "paragraph_after_insert_payload"
    RETROSPECTIVE_INCORPORATED_NOTE = "retrospective_incorporated_note"
    SCHEDULE_INDIRECTION_PAYLOAD = "schedule_indirection_payload"
    SECTION_AFTER_INSERT_PAYLOAD = "section_after_insert_payload"
    SUBSECTION_AFTER_INSERT_PAYLOAD = "subsection_after_insert_payload"
    WHOLE_PROVISION_SUBSTITUTION_PAYLOAD = "whole_provision_substitution_payload"


class NZLatestOracleTextStatus(StrEnum):
    """Closed latest-oracle text-witness status. ``NONE`` (``""``) marks absent.

    The ``oracle_*`` members are the verdict of comparing the candidate
    old/new text against the latest consolidated source node; the ``not_*`` and
    ``blocked_target_*`` members mark why the witness could not run. The rule_id
    is derived as ``f"nz_instruction_latest_oracle_text_{status}"``; because a
    ``StrEnum`` member formats to its ``value``, the rule_id stays byte-identical.
    """

    NONE = ""
    NOT_APPLICABLE_NOT_DIRECT_TEXT_SUBSTITUTION = "not_applicable_not_direct_text_substitution"
    NOT_RUN_TARGET_DOCUMENT_UNAVAILABLE = "not_run_target_document_unavailable"
    BLOCKED_TARGET_ADDRESS_UNMAPPED = "blocked_target_address_unmapped"
    BLOCKED_TARGET_GRANULARITY_NOT_INDEXED = "blocked_target_granularity_not_indexed"
    BLOCKED_TARGET_SOURCE_NODE_DELETED = "blocked_target_source_node_deleted"
    BLOCKED_TARGET_SOURCE_NODE_MISSING = "blocked_target_source_node_missing"
    BLOCKED_TARGET_SOURCE_NODE_NOT_UNIQUE = "blocked_target_source_node_not_unique"
    ORACLE_OLD_TEXT_DELETED = "oracle_old_text_deleted"
    ORACLE_OLD_TEXT_NOT_DELETED = "oracle_old_text_not_deleted"
    ORACLE_NEW_TEXT_ONLY = "oracle_new_text_only"
    ORACLE_NEW_TEXT_ONLY_EACH_PLACE = "oracle_new_text_only_each_place"
    ORACLE_NEW_TEXT_CONTAINS_OLD_TEXT = "oracle_new_text_contains_old_text"
    ORACLE_OLD_TEXT_ONLY = "oracle_old_text_only"
    ORACLE_OLD_AND_NEW_TEXT = "oracle_old_and_new_text"
    ORACLE_NEITHER_OLD_NOR_NEW_TEXT = "oracle_neither_old_nor_new_text"


class NZLatestOracleTargetResolutionStatus(StrEnum):
    """Closed latest-oracle target-resolution status. ``NONE`` (``""``) marks absent."""

    NONE = ""
    EXACT_SOURCE_PATH = "exact_source_path"
    VIA_UNLABELED_SOURCE_CARRIER = "via_unlabeled_source_carrier"


@dataclass(frozen=True)
class NZInstructionWorkQueueRow:
    row_id: str
    operation_row_id: str
    effect_readiness_row_id: str
    queue_status: NZWorkQueueStatus
    operation_family: str
    target_address: str
    # effect_readiness_status forwards the upstream effect-readiness merged
    # vocabulary (NZPayloadStatus blocked terms in union with effect-readiness
    # ready/blocked terms); kept ``str`` because it is an open/merged vocab, not
    # owned here.
    effect_readiness_status: str
    blocking_rule_id: str
    amending_work_id: str
    amending_provision_hrefs: tuple[str, ...]
    instruction_semantic_candidate_status: NZInstructionSemanticCandidateStatus
    instruction_semantic_candidate_family: NZInstructionSemanticCandidateFamily
    instruction_semantic_rule_id: str
    payload_instruction_shape: NZPayloadInstructionShape
    payload_instruction_safety: NZPayloadInstructionSafety
    payload_match_headings: tuple[str, ...]
    payload_text_snippets: tuple[str, ...]
    instruction_subfamily_status: NZTextSubstitutionStatus = NZTextSubstitutionStatus.NONE
    instruction_subfamily: NZTextSubstitutionSubfamily = NZTextSubstitutionSubfamily.NONE
    instruction_subfamily_rule_id: str = ""
    payload_structural_subfamily_status: NZStructuralSubfamilyStatus = NZStructuralSubfamilyStatus.NONE
    payload_structural_subfamily: NZStructuralSubfamily = NZStructuralSubfamily.NONE
    payload_structural_subfamily_rule_id: str = ""
    instruction_clause_count: int = 0
    explicit_target_citation: str = ""
    target_citation_status: NZTargetCitationStatus = NZTargetCitationStatus.NONE
    old_text: str = ""
    new_text: str = ""
    text_substitution_scope: NZTextSubstitutionScope = NZTextSubstitutionScope.NONE
    latest_oracle_text_status: NZLatestOracleTextStatus = NZLatestOracleTextStatus.NONE
    latest_oracle_text_rule_id: str = ""
    latest_oracle_target_resolution_status: NZLatestOracleTargetResolutionStatus = (
        NZLatestOracleTargetResolutionStatus.NONE
    )
    latest_oracle_target_resolution_rule_id: str = ""
    latest_oracle_target_source_path: tuple[str, ...] = ()
    latest_oracle_old_text_occurrences: int = 0
    latest_oracle_new_text_occurrences: int = 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "operation_row_id": self.operation_row_id,
            "effect_readiness_row_id": self.effect_readiness_row_id,
            "queue_status": self.queue_status,
            "operation_family": self.operation_family,
            "target_address": self.target_address,
            "effect_readiness_status": self.effect_readiness_status,
            "blocking_rule_id": self.blocking_rule_id,
            "amending_work_id": self.amending_work_id,
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "instruction_semantic_candidate_status": self.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": self.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": self.instruction_semantic_rule_id,
            "payload_instruction_shape": self.payload_instruction_shape,
            "payload_instruction_safety": self.payload_instruction_safety,
            "payload_match_headings": list(self.payload_match_headings),
            "payload_text_snippets": list(self.payload_text_snippets),
            "instruction_subfamily_status": self.instruction_subfamily_status,
            "instruction_subfamily": self.instruction_subfamily,
            "instruction_subfamily_rule_id": self.instruction_subfamily_rule_id,
            "payload_structural_subfamily_status": self.payload_structural_subfamily_status,
            "payload_structural_subfamily": self.payload_structural_subfamily,
            "payload_structural_subfamily_rule_id": self.payload_structural_subfamily_rule_id,
            "instruction_clause_count": self.instruction_clause_count,
            "explicit_target_citation": self.explicit_target_citation,
            "target_citation_status": self.target_citation_status,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "text_substitution_scope": self.text_substitution_scope,
            "latest_oracle_text_status": self.latest_oracle_text_status,
            "latest_oracle_text_rule_id": self.latest_oracle_text_rule_id,
            "latest_oracle_target_resolution_status": self.latest_oracle_target_resolution_status,
            "latest_oracle_target_resolution_rule_id": self.latest_oracle_target_resolution_rule_id,
            "latest_oracle_target_source_path": list(self.latest_oracle_target_source_path),
            "latest_oracle_old_text_occurrences": self.latest_oracle_old_text_occurrences,
            "latest_oracle_new_text_occurrences": self.latest_oracle_new_text_occurrences,
        }


@dataclass(frozen=True)
class NZInstructionWorkQueueReport:
    work_id: str
    rows: tuple[NZInstructionWorkQueueRow, ...]

    def summary(self) -> dict[str, Any]:
        return _summarize_rows(self.work_id, self.rows)

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        queue_status: str = "",
        instruction_family: str = "",
        instruction_shape: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
    ) -> dict[str, Any]:
        rows = _filter_rows(
            self.rows,
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )
        filters = _jsonable_filters(
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "instruction_semantics_workqueue",
            "truth_claim": "diagnostic_instruction_semantics_queue",
            "replay_claims": False,
            "canonical_effect_claims": False,
            "summary": self.summary(),
            "filters": filters,
            "filtered_summary": _summarize_rows(self.work_id, rows),
        }
        if summary_only:
            return payload
        selected_rows = rows if row_limit is None else rows[:row_limit]
        payload["rows"] = [row.to_jsonable() for row in selected_rows]
        if row_limit is not None and len(rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(rows) - row_limit
        return payload

    def operation_evidence_rows(self) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_workqueue_evidence_row(self, row) for row in self.rows)

    def filtered_rows(
        self,
        *,
        queue_status: str = "",
        instruction_family: str = "",
        instruction_shape: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
    ) -> tuple[NZInstructionWorkQueueRow, ...]:
        return _filter_rows(
            self.rows,
            queue_status=queue_status,
            instruction_family=instruction_family,
            instruction_shape=instruction_shape,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
        )

    def operation_evidence_rows_for(
        self, rows: Iterable[NZInstructionWorkQueueRow]
    ) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_workqueue_evidence_row(self, row) for row in rows)

    def frontier_work_items(self) -> tuple[Any, ...]:
        """Project blocked/review rows into shared frontier work items.

        Imported lazily to avoid a module import cycle (the frontier adapter
        depends on this module's report/row types). Returns a tuple of
        ``lawvm.core.frontier_work_item.FrontierWorkItem``.
        """
        from lawvm.new_zealand.frontier_work_items import frontier_work_items

        return frontier_work_items(self)


def _summarize_rows(work_id: str, rows: tuple[NZInstructionWorkQueueRow, ...]) -> dict[str, Any]:
    queue_status_counts = Counter(row.queue_status for row in rows)
    operation_family_counts = Counter(row.operation_family for row in rows)
    candidate_status_counts = Counter(row.instruction_semantic_candidate_status for row in rows)
    candidate_family_counts = Counter(row.instruction_semantic_candidate_family or "__none__" for row in rows)
    instruction_shape_counts = Counter(row.payload_instruction_shape or "__none__" for row in rows)
    instruction_safety_counts = Counter(row.payload_instruction_safety or "__none__" for row in rows)
    subfamily_status_counts = Counter(row.instruction_subfamily_status or "__none__" for row in rows)
    subfamily_counts = Counter(row.instruction_subfamily or "__none__" for row in rows)
    structural_subfamily_status_counts = Counter(row.payload_structural_subfamily_status or "__none__" for row in rows)
    structural_subfamily_counts = Counter(row.payload_structural_subfamily or "__none__" for row in rows)
    target_citation_status_counts = Counter(row.target_citation_status or "__none__" for row in rows)
    text_substitution_scope_counts = Counter(row.text_substitution_scope or "__none__" for row in rows)
    latest_oracle_text_status_counts = Counter(row.latest_oracle_text_status or "__none__" for row in rows)
    latest_oracle_target_resolution_counts = Counter(row.latest_oracle_target_resolution_status or "__none__" for row in rows)
    return {
        "work_id": work_id,
        "rows": len(rows),
        "queue_status_counts": dict(sorted(queue_status_counts.items())),
        "operation_family_counts": dict(sorted(operation_family_counts.items())),
        "instruction_semantic_candidate_status_counts": dict(sorted(candidate_status_counts.items())),
        "instruction_semantic_candidate_family_counts": dict(sorted(candidate_family_counts.items())),
        "payload_instruction_shape_counts": dict(sorted(instruction_shape_counts.items())),
        "payload_instruction_safety_counts": dict(sorted(instruction_safety_counts.items())),
        "instruction_subfamily_status_counts": dict(sorted(subfamily_status_counts.items())),
        "instruction_subfamily_counts": dict(sorted(subfamily_counts.items())),
        "payload_structural_subfamily_status_counts": dict(sorted(structural_subfamily_status_counts.items())),
        "payload_structural_subfamily_counts": dict(sorted(structural_subfamily_counts.items())),
        "target_citation_status_counts": dict(sorted(target_citation_status_counts.items())),
        "text_substitution_scope_counts": dict(sorted(text_substitution_scope_counts.items())),
        "latest_oracle_text_status_counts": dict(sorted(latest_oracle_text_status_counts.items())),
        "latest_oracle_target_resolution_status_counts": dict(sorted(latest_oracle_target_resolution_counts.items())),
        "direct_single_text_substitution_candidates": subfamily_counts["direct_single_text_substitution"],
        "direct_each_place_text_substitution_candidates": subfamily_counts["direct_each_place_text_substitution"],
        "candidate_instruction_rows": queue_status_counts[NZWorkQueueStatus.CANDIDATE],
        "review_instruction_rows": queue_status_counts[NZWorkQueueStatus.REVIEW],
        "blocked_instruction_rows": queue_status_counts[NZWorkQueueStatus.BLOCKED],
        "not_required_rows": queue_status_counts[NZWorkQueueStatus.NOT_REQUIRED],
        "replay_claims": False,
        "canonical_effect_claims": False,
    }


def _jsonable_filters(
    *,
    queue_status: str,
    instruction_family: str,
    instruction_shape: str,
    instruction_subfamily_status: str,
    instruction_subfamily: str,
    payload_structural_subfamily_status: str,
    payload_structural_subfamily: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "queue_status": queue_status,
            "instruction_family": instruction_family,
            "instruction_shape": instruction_shape,
            "instruction_subfamily_status": instruction_subfamily_status,
            "instruction_subfamily": instruction_subfamily,
            "payload_structural_subfamily_status": payload_structural_subfamily_status,
            "payload_structural_subfamily": payload_structural_subfamily,
        }.items()
        if value
    }


def build_instruction_workqueue(
    operation_surface: NZOperationSurfaceReport,
    payload_surface: NZPayloadSurfaceReport,
    effect_readiness: NZEffectReadinessReport | None = None,
    target_document: NZSourceDocument | None = None,
) -> NZInstructionWorkQueueReport:
    readiness = effect_readiness or build_effect_readiness_surface(operation_surface, payload_surface)
    operation_by_row_id = {row.row_id: row for row in operation_surface.rows}
    payload_by_row_id = {row.operation_row_id: row for row in payload_surface.rows}
    rows: list[NZInstructionWorkQueueRow] = []
    for index, readiness_row in enumerate(readiness.rows, start=1):
        operation_row = operation_by_row_id.get(readiness_row.operation_row_id)
        payload_row = payload_by_row_id.get(readiness_row.operation_row_id)
        payload_matches = payload_row.matches if payload_row is not None else ()
        payload_texts = tuple(match.text for match in payload_matches)
        payload_text_snippets = tuple(_snippet(match.text) for match in payload_matches)
        text_substitution = _classify_typed_amend_in_text_substitution(
            operation_family=readiness_row.operation_family,
            target_address=readiness_row.target_address,
            payload_matches=payload_matches,
        )
        if text_substitution is None:
            text_substitution = _classify_direct_single_text_substitution(
                operation_family=readiness_row.operation_family,
                target_address=readiness_row.target_address,
                payload_instruction_shape=readiness_row.payload_instruction_shape,
                amending_provision_hrefs=operation_row.amending_provision_hrefs if operation_row is not None else (),
                payload_texts=payload_texts,
            )
        structural_subfamily = _classify_report_only_structural_subfamily(
            operation_family=readiness_row.operation_family,
            target_address=readiness_row.target_address,
            payload_instruction_shape=readiness_row.payload_instruction_shape,
            text_substitution_status=text_substitution.substitution_status,
            payload_texts=payload_texts,
        )
        oracle_text_witness = _latest_oracle_text_witness(
            text_substitution=text_substitution,
            target_address=readiness_row.target_address,
            target_document=target_document,
        )
        rows.append(
            NZInstructionWorkQueueRow(
                row_id=f"nz-instruction-workqueue-{index}",
                operation_row_id=readiness_row.operation_row_id,
                effect_readiness_row_id=readiness_row.row_id,
                queue_status=_queue_status(readiness_row.instruction_semantic_candidate_status),
                operation_family=readiness_row.operation_family,
                target_address=readiness_row.target_address,
                effect_readiness_status=readiness_row.effect_readiness_status,
                blocking_rule_id=readiness_row.blocking_rule_id,
                amending_work_id=operation_row.amending_work_id if operation_row is not None else "",
                amending_provision_hrefs=operation_row.amending_provision_hrefs if operation_row is not None else (),
                instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
                instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
                instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
                payload_instruction_shape=readiness_row.payload_instruction_shape,
                payload_instruction_safety=readiness_row.payload_instruction_safety,
                payload_match_headings=tuple(match.heading for match in payload_matches),
                payload_text_snippets=payload_text_snippets,
                instruction_subfamily_status=text_substitution.substitution_status,
                instruction_subfamily=text_substitution.subfamily,
                instruction_subfamily_rule_id=text_substitution.rule_id,
                payload_structural_subfamily_status=structural_subfamily.subfamily_status,
                payload_structural_subfamily=structural_subfamily.subfamily,
                payload_structural_subfamily_rule_id=structural_subfamily.rule_id,
                instruction_clause_count=text_substitution.clause_count,
                explicit_target_citation=text_substitution.explicit_target_citation,
                target_citation_status=text_substitution.target_citation_status,
                old_text=text_substitution.old_text,
                new_text=text_substitution.new_text,
                text_substitution_scope=text_substitution.scope,
                latest_oracle_text_status=oracle_text_witness.oracle_text_status,
                latest_oracle_text_rule_id=oracle_text_witness.rule_id,
                latest_oracle_target_resolution_status=oracle_text_witness.target_resolution_status,
                latest_oracle_target_resolution_rule_id=oracle_text_witness.target_resolution_rule_id,
                latest_oracle_target_source_path=oracle_text_witness.target_source_path,
                latest_oracle_old_text_occurrences=oracle_text_witness.old_text_occurrences,
                latest_oracle_new_text_occurrences=oracle_text_witness.new_text_occurrences,
            )
        )
    return NZInstructionWorkQueueReport(work_id=operation_surface.work_id, rows=tuple(rows))


def build_archived_work_instruction_workqueue(db_path: Path, work_id: str) -> NZInstructionWorkQueueReport:
    target_document = parse_archived_work_latest(db_path, work_id)
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    payload_surface = build_archived_work_payload_surface(
        db_path,
        work_id,
        operation_surface=operation_surface,
    )
    effect_readiness = build_archived_work_effect_readiness_surface(
        db_path,
        work_id,
        operation_surface=operation_surface,
        payload_surface=payload_surface,
    )
    return build_instruction_workqueue(operation_surface, payload_surface, effect_readiness, target_document)


def _queue_status(instruction_status: NZInstructionSemanticCandidateStatus) -> NZWorkQueueStatus:
    match instruction_status:
        case NZInstructionSemanticCandidateStatus.CANDIDATE_ONLY_INSTRUCTION_SEMANTICS:
            return NZWorkQueueStatus.CANDIDATE
        case NZInstructionSemanticCandidateStatus.REVIEW_RETROSPECTIVE_INCORPORATED_NOTE:
            return NZWorkQueueStatus.REVIEW
        case NZInstructionSemanticCandidateStatus.NOT_REQUIRED_FOR_REPEAL_CANDIDATE:
            return NZWorkQueueStatus.NOT_REQUIRED
        case (
            NZInstructionSemanticCandidateStatus.BLOCKED_PAYLOAD_WITNESS_NOT_AVAILABLE
            | NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_INDIRECTION
            | NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_OPAQUE_OR_UNCLASSIFIED
            | NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_SEMANTICS_UNCLASSIFIED
        ):
            return NZWorkQueueStatus.BLOCKED
        case _ as unreachable:
            assert_never(unreachable)


def _snippet(text: str, *, limit: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _filter_rows(
    rows: tuple[NZInstructionWorkQueueRow, ...],
    *,
    queue_status: str = "",
    instruction_family: str = "",
    instruction_shape: str = "",
    instruction_subfamily_status: str = "",
    instruction_subfamily: str = "",
    payload_structural_subfamily_status: str = "",
    payload_structural_subfamily: str = "",
) -> tuple[NZInstructionWorkQueueRow, ...]:
    filtered = rows
    if queue_status:
        filtered = tuple(row for row in filtered if row.queue_status == queue_status)
    if instruction_family:
        filtered = tuple(row for row in filtered if row.instruction_semantic_candidate_family == instruction_family)
    if instruction_shape:
        filtered = tuple(row for row in filtered if row.payload_instruction_shape == instruction_shape)
    if instruction_subfamily_status:
        filtered = tuple(row for row in filtered if row.instruction_subfamily_status == instruction_subfamily_status)
    if instruction_subfamily:
        filtered = tuple(row for row in filtered if row.instruction_subfamily == instruction_subfamily)
    if payload_structural_subfamily_status:
        filtered = tuple(
            row
            for row in filtered
            if row.payload_structural_subfamily_status == payload_structural_subfamily_status
        )
    if payload_structural_subfamily:
        filtered = tuple(row for row in filtered if row.payload_structural_subfamily == payload_structural_subfamily)
    return filtered


def _workqueue_evidence_row(
    report: NZInstructionWorkQueueReport,
    row: NZInstructionWorkQueueRow,
) -> CorpusOperationEvidenceRow:
    if row.queue_status == NZWorkQueueStatus.NOT_REQUIRED:
        return CorpusOperationEvidenceRow(
            row_id=row.row_id,
            frontend_id="new_zealand",
            source_artifact_id=report.work_id or "new_zealand_instruction_workqueue",
            source_unit_id=row.operation_row_id,
            effect_family=row.operation_family,
            resolved_target=row.target_address,
            evidence_status=CorpusRowStatus.SKIPPED,
            blocking=False,
            strict_disposition="candidate_handled_elsewhere",
            quirks_disposition=QuirksDisposition.CANDIDATE_HANDLED_ELSEWHERE,
            detail=_workqueue_evidence_detail(row, reason="repeal candidate is owned by effect-candidates surface"),
        )
    reason = (
        row.instruction_subfamily_rule_id
        or row.instruction_semantic_rule_id
        or row.blocking_rule_id
        or "nz_instruction_workqueue_not_lowered"
    )
    return CorpusOperationEvidenceRow(
        row_id=row.row_id,
        frontend_id="new_zealand",
        source_artifact_id=report.work_id or "new_zealand_instruction_workqueue",
        source_unit_id=row.operation_row_id,
        effect_family=row.operation_family,
        resolved_target=row.target_address,
        evidence_status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD_INSTRUCTION_WORKQUEUE,
        detail=_workqueue_evidence_detail(row, reason=reason),
    )


def _workqueue_evidence_detail(row: NZInstructionWorkQueueRow, *, reason: str) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "reason": reason,
        "queue_status": row.queue_status,
        "effect_readiness_row_id": row.effect_readiness_row_id,
        "effect_readiness_status": row.effect_readiness_status,
        "blocking_rule_id": row.blocking_rule_id,
        "amending_work_id": row.amending_work_id,
        "amending_provision_hrefs": row.amending_provision_hrefs,
        "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
        "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
        "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
        "payload_instruction_shape": row.payload_instruction_shape,
        "payload_instruction_safety": row.payload_instruction_safety,
        "payload_match_headings": row.payload_match_headings,
        "payload_text_snippets": row.payload_text_snippets,
        "instruction_subfamily_status": row.instruction_subfamily_status,
        "instruction_subfamily": row.instruction_subfamily,
        "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
        "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
        "payload_structural_subfamily": row.payload_structural_subfamily,
        "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
        "instruction_clause_count": row.instruction_clause_count,
        "explicit_target_citation": row.explicit_target_citation,
        "target_citation_status": row.target_citation_status,
        "old_text": row.old_text,
        "new_text": row.new_text,
        "text_substitution_scope": row.text_substitution_scope,
        "latest_oracle_text_status": row.latest_oracle_text_status,
        "latest_oracle_text_rule_id": row.latest_oracle_text_rule_id,
        "latest_oracle_target_resolution_status": row.latest_oracle_target_resolution_status,
        "latest_oracle_target_resolution_rule_id": row.latest_oracle_target_resolution_rule_id,
        "latest_oracle_target_source_path": row.latest_oracle_target_source_path,
        "latest_oracle_old_text_occurrences": row.latest_oracle_old_text_occurrences,
        "latest_oracle_new_text_occurrences": row.latest_oracle_new_text_occurrences,
        "replay_claims": False,
        "canonical_effect_claims": False,
    }
    target_resolution = _latest_oracle_target_resolution_evidence(row)
    if target_resolution:
        detail["latest_oracle_target_resolution"] = target_resolution
    return detail


def _latest_oracle_target_resolution_evidence(row: NZInstructionWorkQueueRow) -> dict[str, Any]:
    if not row.latest_oracle_target_resolution_status or not row.latest_oracle_target_resolution_rule_id:
        return {}
    if not row.latest_oracle_target_source_path:
        return {}
    status = (
        TARGET_RESOLVED
        if row.latest_oracle_target_resolution_status == NZLatestOracleTargetResolutionStatus.EXACT_SOURCE_PATH
        else TARGET_RECOVERED
    )
    scope_confidence = (
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE
        if row.latest_oracle_target_resolution_status == NZLatestOracleTargetResolutionStatus.EXACT_SOURCE_PATH
        else ""
    )
    return TargetResolutionCoverage(
        rule_id=row.latest_oracle_target_resolution_rule_id,
        phase="oracle",
        reason="latest oracle source node resolved for instruction text witness",
        resolution_status=status,
        source_target=row.target_address,
        selected_target="/".join(row.latest_oracle_target_source_path),
        candidate_count=1,
        scope_confidence=scope_confidence,
        detail={
            "jurisdiction_status": row.latest_oracle_target_resolution_status,
            "source_path": row.latest_oracle_target_source_path,
        },
    ).to_diagnostic_detail()


@dataclass(frozen=True)
class _TextSubstitutionCandidate:
    substitution_status: NZTextSubstitutionStatus
    subfamily: NZTextSubstitutionSubfamily = NZTextSubstitutionSubfamily.NONE
    rule_id: str = ""
    clause_count: int = 0
    explicit_target_citation: str = ""
    target_citation_status: NZTargetCitationStatus = NZTargetCitationStatus.NONE
    old_text: str = ""
    new_text: str = ""
    scope: NZTextSubstitutionScope = NZTextSubstitutionScope.NONE


@dataclass(frozen=True)
class _StructuralInstructionSubfamily:
    subfamily_status: NZStructuralSubfamilyStatus = NZStructuralSubfamilyStatus.NONE
    subfamily: NZStructuralSubfamily = NZStructuralSubfamily.NONE
    rule_id: str = ""


@dataclass(frozen=True)
class _LatestOracleTextWitness:
    oracle_text_status: NZLatestOracleTextStatus
    rule_id: str
    target_resolution_status: NZLatestOracleTargetResolutionStatus = NZLatestOracleTargetResolutionStatus.NONE
    target_resolution_rule_id: str = ""
    target_source_path: tuple[str, ...] = ()
    old_text_occurrences: int = 0
    new_text_occurrences: int = 0


@dataclass(frozen=True)
class _LatestOracleTargetResolution:
    node: NZSourceNode
    target_resolution_status: NZLatestOracleTargetResolutionStatus
    rule_id: str


def _classify_typed_amend_in_text_substitution(
    *,
    operation_family: str,
    target_address: str,
    payload_matches: tuple[Any, ...],
) -> _TextSubstitutionCandidate | None:
    """Classify a text substitution from typed ``<amend.in>`` instructions.

    A single amending provision (one href) may hold N typed instructions, one
    per ``<text>``; the operation-witness row identifies which by its
    ``target_address``. We select the typed instruction whose ``target_citation``
    matches this row's address, then emit an exact single-occurrence
    substitution candidate from its paired ``<amend.in>`` old/new text.

    Returns ``None`` (defer to the prose path) when there are no typed
    instructions at all, or when no typed instruction targets this row — never
    regressing the existing prose emissions. Returns a typed *blocker* (not a
    guess) when the matched instruction is a not-yet-supported shape (insert,
    omit-only, each-place, structural payload, missing old/new).
    """
    typed_instructions = tuple(
        instruction for match in payload_matches for instruction in getattr(match, "amend_instructions", ())
    )
    if not typed_instructions:
        return None
    if operation_family != "amended":
        # Typed text-substitution lowering is only defined for the ``amended``
        # family; leave other families to the existing prose/structural paths.
        return None
    matched = tuple(
        instruction
        for instruction in typed_instructions
        if _target_citation_matches(instruction.target_citation, target_address)
    )
    if not matched:
        # No typed instruction is keyed to this target — defer to prose so a
        # provision whose target is only recoverable from prose is not lost.
        return None
    if len(matched) > 1:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TYPED_AMEND_IN_AMBIGUOUS_TARGET,
            rule_id="nz_instruction_semantics_blocked_typed_amend_in_ambiguous_target",
            clause_count=len(matched),
            explicit_target_citation=matched[0].target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
        )
    instruction = matched[0]
    if instruction.verb == "omitting" and instruction.omit_only:
        return _classify_typed_omit_only(instruction)
    if instruction.verb == "inserting":
        return _classify_typed_insert_after(instruction)
    if instruction.verb not in {"omitting_substituting", "replace_with"}:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TYPED_AMEND_IN_NOT_SUBSTITUTION_VERB,
            rule_id="nz_instruction_semantics_blocked_typed_amend_in_not_substitution_verb",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
        )
    old_text = " ".join(instruction.old_text.split()).strip(" ,;.")
    new_text = " ".join(instruction.new_text.split()).strip(" ,;.")
    if not old_text or not new_text:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TYPED_AMEND_IN_PAYLOAD_INCOMPLETE,
            rule_id="nz_instruction_semantics_blocked_typed_amend_in_payload_incomplete",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
            new_text=new_text,
        )
    # NB: the prose path's structural-payload guard
    # (``_looks_structural_omitting_substituting_payload``) is a defense against
    # flattening ambiguity — it is NOT applied here. Typed ``<amend.in>``
    # boundaries are exact, so the old/new text is the literal substitution; a
    # payload that is genuinely structural (e.g. "the following subsection")
    # carries no ``<amend.in>`` pair and never reaches this point. The
    # downstream single-occurrence oracle + dry-run kernel reject anything that
    # is not a clean single substitution, so a mis-shaped payload becomes an
    # honest residual rather than a false agreement.
    if instruction.each_place:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_TEXT_SUBSTITUTION,
            subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_direct_each_place_typed_amend_in_text_substitution_candidate",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
            new_text=new_text,
            scope=NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_TYPED_AMEND_IN_TEXT_SUBSTITUTION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_typed_amend_in_text_substitution_candidate",
        explicit_target_citation=instruction.target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED,
        old_text=old_text,
        new_text=new_text,
        scope=NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE,
    )


def _classify_typed_omit_only(instruction: Any) -> _TextSubstitutionCandidate:
    """Lower a typed omit-only ``<amend.in>`` instruction to a deletion.

    "is amended by omitting <amend.in>X</amend.in>" deletes the span ``X`` with
    no replacement. This lowers to a text-replace whose old text is ``X`` and
    whose new text is the empty string; the downstream single-occurrence oracle
    + dry-run kernel verify the deletion exactly the same way as a substitution.
    Refuse-don't-guess if the omitted span is empty after normalization.
    """
    old_text = " ".join(instruction.old_text.split()).strip(" ,;.")
    if not old_text:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TYPED_AMEND_IN_PAYLOAD_INCOMPLETE,
            rule_id="nz_instruction_semantics_blocked_typed_amend_in_payload_incomplete",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
        )
    if instruction.each_place:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_OMIT_DELETION,
            subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_direct_each_place_typed_amend_in_omit_deletion_candidate",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
            new_text="",
            scope=NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_TYPED_AMEND_IN_OMIT_DELETION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_typed_amend_in_omit_deletion_candidate",
        explicit_target_citation=instruction.target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED,
        old_text=old_text,
        new_text="",
        scope=NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE,
    )


def _classify_typed_insert_after(instruction: Any) -> _TextSubstitutionCandidate:
    """Lower a typed insert-relative-to-anchor ``<amend.in>`` instruction.

    "is amended by inserting, after the word <quote.in>ANCHOR</quote.in>, the
    words <amend.in>NEW</amend.in>" inserts ``NEW`` adjacent to ``ANCHOR``. Only
    the unambiguous ``<quote.in>``-anchored shape carries a parsed
    ``anchor_text`` + ``insert_position`` (see ``_insert_after_anchor_payload``);
    any other insert shape arrives with no anchor and stays a typed
    not-supported residue, never a guess about which span is the anchor.

    Lowered as a text-replace keyed on the anchor: ``after`` →
    ``ANCHOR`` → ``ANCHOR NEW``; ``before`` → ``ANCHOR`` → ``NEW ANCHOR``. The
    anchor is the matched old text so the existing single-occurrence oracle +
    dry-run kernel verify the insertion exactly.
    """
    anchor = " ".join(instruction.anchor_text.split()).strip()
    new = " ".join(instruction.new_text.split()).strip(" ,;.")
    if not anchor or not new or instruction.insert_position not in {"after", "before"}:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TYPED_AMEND_IN_INSERT_ANCHOR_UNPARSED,
            rule_id="nz_instruction_semantics_blocked_typed_amend_in_insert_anchor_unparsed",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
        )
    if instruction.insert_position == "after":
        replacement = f"{anchor} {new}"
    else:
        replacement = f"{new} {anchor}"
    if instruction.each_place:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_EACH_PLACE_TYPED_AMEND_IN_INSERT,
            subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_direct_each_place_typed_amend_in_insert_candidate",
            explicit_target_citation=instruction.target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=anchor,
            new_text=replacement,
            scope=NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_TYPED_AMEND_IN_INSERT,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_typed_amend_in_insert_candidate",
        explicit_target_citation=instruction.target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED,
        old_text=anchor,
        new_text=replacement,
        scope=NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE,
    )


def _classify_direct_single_text_substitution(
    *,
    operation_family: str,
    target_address: str,
    payload_instruction_shape: NZPayloadInstructionShape,
    amending_provision_hrefs: tuple[str, ...],
    payload_texts: tuple[str, ...],
) -> _TextSubstitutionCandidate:
    if payload_instruction_shape == NZPayloadInstructionShape.DIRECT_AMENDED_BY_INSTRUCTION:
        return _classify_omitting_substituting_text_substitution(
            operation_family=operation_family,
            target_address=target_address,
            amending_provision_hrefs=amending_provision_hrefs,
            payload_texts=payload_texts,
        )
    if payload_instruction_shape != NZPayloadInstructionShape.DIRECT_SUBSTITUTE_REPLACE_INSTRUCTION:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.NOT_TEXT_SUBSTITUTION_SHAPE,
            rule_id="nz_instruction_subfamily_not_text_substitution_shape",
        )
    text = " ".join(payload_texts)
    clause_count = _replacement_clause_count(text)
    if operation_family != "amended":
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_STRUCTURAL_REPLACEMENT_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    if len(amending_provision_hrefs) != 1 or len(payload_texts) != 1:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_PAYLOAD_MULTIPLICITY,
            rule_id="nz_instruction_semantics_blocked_payload_multiplicity",
            clause_count=clause_count,
        )
    if clause_count != 1:
        return _classify_multi_clause_direct_text_substitution(
            text=text,
            target_address=target_address,
            clause_count=clause_count,
        )
    if text.lower().startswith("replace with:"):
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_STRUCTURAL_REPLACEMENT_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    pieces = _extract_replace_with_pieces(text)
    if pieces is None:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TEXT_SUBSTITUTION_PARSE_FAILED,
            rule_id="nz_instruction_semantics_blocked_text_substitution_parse_failed",
            clause_count=clause_count,
        )
    explicit_target_citation, old_text, new_text = pieces
    cleaned_new_text, occurrence_scope = _text_substitution_scope(new_text)
    if not _target_citation_matches(explicit_target_citation, target_address):
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TARGET_CITATION_MISMATCH,
            rule_id="nz_instruction_semantics_blocked_target_citation_mismatch",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MISMATCH,
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    if occurrence_scope != NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE:
        if occurrence_scope == NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE:
            return _TextSubstitutionCandidate(
                substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
                subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
                rule_id="nz_instruction_semantics_direct_each_place_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status=NZTargetCitationStatus.MATCHED,
                old_text=old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTIPLE_OCCURRENCE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_SINGLE_TEXT_SUBSTITUTION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_single_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED,
        old_text=old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_omitting_substituting_text_substitution(
    *,
    operation_family: str,
    target_address: str,
    amending_provision_hrefs: tuple[str, ...],
    payload_texts: tuple[str, ...],
) -> _TextSubstitutionCandidate:
    text = " ".join(payload_texts)
    clause_count = _omitting_substituting_clause_count(text)
    if operation_family != "amended":
        if operation_family in {"inserted", "added"}:
            return _TextSubstitutionCandidate(
                substitution_status=NZTextSubstitutionStatus.NOT_TEXT_SUBSTITUTION_SHAPE,
                rule_id="nz_instruction_subfamily_not_text_substitution_shape",
                clause_count=clause_count,
            )
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_STRUCTURAL_REPLACEMENT_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_structural_replacement_payload",
            clause_count=clause_count,
        )
    if len(amending_provision_hrefs) != 1 or len(payload_texts) != 1:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_PAYLOAD_MULTIPLICITY,
            rule_id="nz_instruction_semantics_blocked_payload_multiplicity",
            clause_count=clause_count,
        )
    if clause_count != 1:
        return _classify_multi_clause_omitting_substituting_text_substitution(
            text=text,
            target_address=target_address,
            clause_count=clause_count,
        )
    pieces = _extract_omitting_substituting_pieces(text)
    if pieces is None:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_OMITTING_SUBSTITUTING_PARSE_FAILED,
            rule_id="nz_instruction_semantics_blocked_omitting_substituting_parse_failed",
            clause_count=clause_count,
        )
    explicit_target_citation, old_text, new_text = pieces
    if _looks_structural_omitting_substituting_payload(old_text, new_text):
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_STRUCTURAL_OMITTING_SUBSTITUTING_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_structural_omitting_substituting_payload",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            old_text=old_text,
            new_text=new_text,
        )
    cleaned_old_text, cleaned_new_text, occurrence_scope = _omitting_substitution_scope(old_text, new_text)
    if not _target_citation_matches(explicit_target_citation, target_address):
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_TARGET_CITATION_MISMATCH,
            rule_id="nz_instruction_semantics_blocked_target_citation_mismatch",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MISMATCH,
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    if occurrence_scope != NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE:
        if occurrence_scope == NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE:
            return _TextSubstitutionCandidate(
                substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_EACH_PLACE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION,
                subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
                rule_id="nz_instruction_semantics_direct_each_place_omitting_substituting_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status=NZTargetCitationStatus.MATCHED,
                old_text=cleaned_old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTIPLE_OCCURRENCE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_omitting_substituting_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED,
        old_text=cleaned_old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_report_only_structural_subfamily(
    *,
    operation_family: str,
    target_address: str,
    payload_instruction_shape: NZPayloadInstructionShape,
    text_substitution_status: NZTextSubstitutionStatus,
    payload_texts: tuple[str, ...],
) -> _StructuralInstructionSubfamily:
    if text_substitution_status.startswith("candidate_direct_"):
        return _StructuralInstructionSubfamily()
    if payload_instruction_shape == NZPayloadInstructionShape.DIRECT_INSERT_INSTRUCTION:
        if operation_family in {"amended", "inserted", "added"}:
            return _direct_insert_payload_subfamily(target_address=target_address, payload_texts=payload_texts)
    if payload_instruction_shape == NZPayloadInstructionShape.DIRECT_SUBSTITUTE_REPLACE_INSTRUCTION:
        if operation_family in {"replaced", "substituted"}:
            return _direct_replace_payload_subfamily(payload_texts)
        if operation_family == "amended":
            return _StructuralInstructionSubfamily(
                subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_AMBIGUOUS_AMEND_REPLACE_PAYLOAD,
                subfamily=NZStructuralSubfamily.AMBIGUOUS_AMEND_REPLACE_PAYLOAD,
                rule_id="nz_instruction_structural_subfamily_ambiguous_amend_replace_payload_blocked",
            )
    if payload_instruction_shape == NZPayloadInstructionShape.DIRECT_AMENDED_BY_INSTRUCTION:
        if _looks_mixed_repeal_substitute_payload(payload_texts):
            return _StructuralInstructionSubfamily(
                subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_MIXED_REPEAL_SUBSTITUTE_PAYLOAD_NOT_LOWERED,
                subfamily=NZStructuralSubfamily.MIXED_REPEAL_SUBSTITUTE_PAYLOAD,
                rule_id="nz_instruction_structural_subfamily_mixed_repeal_substitute_payload_blocked",
            )
        if operation_family in {"inserted", "added"}:
            return _direct_insert_payload_subfamily(target_address=target_address, payload_texts=payload_texts)
        if operation_family in {"replaced", "substituted"}:
            return _direct_replace_payload_subfamily(payload_texts)
        if operation_family == "amended":
            return _direct_amend_payload_subfamily(payload_texts)
    if payload_instruction_shape == NZPayloadInstructionShape.RETROSPECTIVE_INCORPORATED_NOTE:
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.REVIEW_RETROSPECTIVE_INCORPORATED_PAYLOAD,
            subfamily=NZStructuralSubfamily.RETROSPECTIVE_INCORPORATED_NOTE,
            rule_id="nz_instruction_structural_subfamily_retrospective_incorporated_note_review",
        )
    if payload_instruction_shape == NZPayloadInstructionShape.SCHEDULE_INDIRECTION:
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_SCHEDULE_INDIRECTION_PAYLOAD,
            subfamily=NZStructuralSubfamily.SCHEDULE_INDIRECTION_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_schedule_indirection_payload_blocked",
        )
    if payload_instruction_shape == NZPayloadInstructionShape.OTHER_INSTRUCTION and _looks_incorporated_amendment_stub_payload(payload_texts):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_INCORPORATED_AMENDMENT_STUB_PAYLOAD,
            subfamily=NZStructuralSubfamily.INCORPORATED_AMENDMENT_STUB_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_incorporated_amendment_stub_payload_blocked",
        )
    return _StructuralInstructionSubfamily()


def _direct_replace_payload_subfamily(payload_texts: tuple[str, ...]) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"^replace\s+with:\s+sections?\s+\S+\s+and\s+\S+\b", normalized):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_MULTI_SECTION_REPLACE_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.MULTI_SECTION_REPLACE_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_multi_section_replace_payload_blocked",
        )
    if re.search(r"\brepealed\s+and\s+the\s+following\s+sections?\s+substituted\b", normalized):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_WHOLE_PROVISION_SUBSTITUTION_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.WHOLE_PROVISION_SUBSTITUTION_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_whole_provision_substitution_payload_blocked",
        )
    return _StructuralInstructionSubfamily(
        subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_STRUCTURAL_REPLACE_PAYLOAD_NOT_LOWERED,
        subfamily=NZStructuralSubfamily.DIRECT_REPLACE_PAYLOAD,
        rule_id="nz_instruction_structural_subfamily_direct_replace_payload_blocked",
    )


def _direct_amend_payload_subfamily(payload_texts: tuple[str, ...]) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"\b(?:omitting|replace)\b.*\b(?:substitut|with)\b", normalized) and re.search(
        r"\b(?:adding|insert(?:ing)?)\s+the\s+following\s+subsections?\b",
        normalized,
    ):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked",
        )
    if _looks_direct_text_insert_payload(normalized):
        return _direct_text_insert_payload_subfamily()
    return _StructuralInstructionSubfamily(
        subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_STRUCTURAL_AMEND_PAYLOAD_NOT_LOWERED,
        subfamily=NZStructuralSubfamily.DIRECT_AMEND_PAYLOAD,
        rule_id="nz_instruction_structural_subfamily_direct_amend_payload_blocked",
    )


def _looks_mixed_repeal_substitute_payload(payload_texts: tuple[str, ...]) -> bool:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    return bool(
        re.search(
            r"\brepeal(?:ing)?\b.*\bsubstitut(?:e|ing)\s+the\s+following\s+"
            r"(?:paragraphs?|subparagraphs?|subsections?|sections?)\b",
            normalized,
        )
    )


def _looks_incorporated_amendment_stub_payload(payload_texts: tuple[str, ...]) -> bool:
    normalized = " ".join(" ".join(payload_texts).lower().split())
    return "amendment(s) incorporated in the" in normalized and "act(s)" in normalized


def _direct_insert_payload_subfamily(
    *,
    target_address: str,
    payload_texts: tuple[str, ...],
) -> _StructuralInstructionSubfamily:
    text = " ".join(payload_texts)
    normalized = " ".join(text.lower().split())
    if re.search(r"\bthis\s+(?:section|subsection|paragraph|subparagraph)\s+inserted\s+s\s*\.", normalized):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_HISTORICAL_INSERTED_NOTE_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.HISTORICAL_INSERTED_NOTE_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_historical_inserted_note_payload_blocked",
        )
    if re.search(r"\b(?:replace|omitting)\b.*\b(?:with|substituting)\b", normalized) and re.search(
        r"\binsert(?:ing)?\b", normalized
    ):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.MIXED_TEXT_AND_STRUCTURAL_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked",
        )
    if target_address.endswith("/heading") and re.search(r"^after\s*,?\s+insert:", normalized):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_CROSS_HEADING_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.CROSS_HEADING_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_cross_heading_insert_payload_blocked",
        )
    if (
        "insert in its appropriate alphabetical order" in normalized
        or "inserting the following definition in its appropriate alphabetical order" in normalized
    ):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_DEFINITION_ALPHABETICAL_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.DEFINITION_ALPHABETICAL_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_definition_alphabetical_insert_payload_blocked",
        )
    if re.search(
        r"\bafter\s+paragraph(?:\s+\([^)]+\))?\s*,?\s+(?:insert:|the\s+following\s+paragraph\b)",
        normalized,
    ):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_PARAGRAPH_AFTER_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.PARAGRAPH_AFTER_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_paragraph_after_insert_payload_blocked",
        )
    if re.search(r"\binsert(?:ing)?\s+the\s+following\s+subsections?\s+after\s+subsection\b", normalized):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_SUBSECTION_AFTER_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.SUBSECTION_AFTER_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_subsection_after_insert_payload_blocked",
        )
    if (
        re.search(r"\binsert(?:ing)?\s+the\s+following\s+sections?\s+after\s+section\b", normalized)
        or re.search(r"\bthe\s+following\s+sections?\s+is\s+inserted\s+after\s*:\s+section\b", normalized)
        or re.search(r"\bthe\s+following\s+sections?\s+are\s+inserted\s+after\s*:\s+section\b", normalized)
    ) or (
        target_address.startswith("section:")
        and "/" not in target_address
        and re.search(r"^after\s*,?\s+insert:\s+section\b", normalized)
    ):
        return _StructuralInstructionSubfamily(
            subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_SECTION_AFTER_INSERT_PAYLOAD_NOT_LOWERED,
            subfamily=NZStructuralSubfamily.SECTION_AFTER_INSERT_PAYLOAD,
            rule_id="nz_instruction_structural_subfamily_section_after_insert_payload_blocked",
        )
    if _looks_direct_text_insert_payload(normalized):
        return _direct_text_insert_payload_subfamily()
    return _StructuralInstructionSubfamily(
        subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_STRUCTURAL_INSERT_PAYLOAD_NOT_LOWERED,
        subfamily=NZStructuralSubfamily.DIRECT_INSERT_PAYLOAD,
        rule_id="nz_instruction_structural_subfamily_direct_insert_payload_blocked",
    )


def _looks_direct_text_insert_payload(normalized_payload_text: str) -> bool:
    return bool(
        re.search(r"\bafter\b.+\binsert(?:ing)?\b", normalized_payload_text)
        or re.search(r"\binsert(?:ing)?\b.+\bafter\b", normalized_payload_text)
    )


def _direct_text_insert_payload_subfamily() -> _StructuralInstructionSubfamily:
    return _StructuralInstructionSubfamily(
        subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_TEXT_INSERT_PAYLOAD_NOT_LOWERED,
        subfamily=NZStructuralSubfamily.DIRECT_TEXT_INSERT_PAYLOAD,
        rule_id="nz_instruction_structural_subfamily_direct_text_insert_payload_blocked",
    )


def _classify_multi_clause_omitting_substituting_text_substitution(
    *,
    text: str,
    target_address: str,
    clause_count: int,
) -> _TextSubstitutionCandidate:
    clauses = _numbered_instruction_clauses(text)
    if not clauses:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_OMITTING_SUBSTITUTING_PARSE_FAILED,
            rule_id="nz_instruction_semantics_blocked_omitting_substituting_parse_failed",
            clause_count=clause_count,
        )
    matches: list[tuple[str, str, str]] = []
    for clause in clauses:
        pieces = _extract_omitting_substituting_pieces(clause)
        if pieces is None:
            continue
        explicit_target_citation, _old_text, _new_text = pieces
        if _target_citation_matches(explicit_target_citation, target_address):
            matches.append(pieces)
    if not matches:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTI_CLAUSE_NO_MATCHING_TARGET,
            rule_id="nz_instruction_semantics_blocked_multi_clause_no_matching_target",
            clause_count=clause_count,
            target_citation_status=NZTargetCitationStatus.NO_MATCH,
        )
    if len(matches) != 1:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTI_CLAUSE_TARGET_AMBIGUOUS,
            rule_id="nz_instruction_semantics_blocked_multi_clause_target_ambiguous",
            clause_count=clause_count,
            target_citation_status=NZTargetCitationStatus.AMBIGUOUS,
        )
    explicit_target_citation, old_text, new_text = matches[0]
    if _looks_structural_omitting_substituting_payload(old_text, new_text):
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_STRUCTURAL_OMITTING_SUBSTITUTING_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_structural_omitting_substituting_payload",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
            old_text=old_text,
            new_text=new_text,
        )
    cleaned_old_text, cleaned_new_text, occurrence_scope = _omitting_substitution_scope(old_text, new_text)
    if occurrence_scope != NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE:
        if occurrence_scope == NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE:
            return _TextSubstitutionCandidate(
                substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_MULTI_CLAUSE_EACH_PLACE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION,
                subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
                rule_id=(
                    "nz_instruction_semantics_direct_multi_clause_each_place_omitting_substituting_"
                    "text_substitution_candidate"
                ),
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
                old_text=cleaned_old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTIPLE_OCCURRENCE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
            old_text=cleaned_old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_MULTI_CLAUSE_OMITTING_SUBSTITUTING_TEXT_SUBSTITUTION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_multi_clause_omitting_substituting_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
        old_text=cleaned_old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _classify_multi_clause_direct_text_substitution(
    *,
    text: str,
    target_address: str,
    clause_count: int,
) -> _TextSubstitutionCandidate:
    clauses = _numbered_instruction_clauses(text)
    if not clauses:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTI_CLAUSE_PAYLOAD,
            rule_id="nz_instruction_semantics_blocked_multi_clause_payload",
            clause_count=clause_count,
        )
    matches: list[tuple[str, str, str]] = []
    for clause in clauses:
        pieces = _extract_replace_with_pieces(clause)
        if pieces is None:
            continue
        explicit_target_citation, _old_text, _new_text = pieces
        if _target_citation_matches(explicit_target_citation, target_address):
            matches.append(pieces)
    if not matches:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTI_CLAUSE_NO_MATCHING_TARGET,
            rule_id="nz_instruction_semantics_blocked_multi_clause_no_matching_target",
            clause_count=clause_count,
            target_citation_status=NZTargetCitationStatus.NO_MATCH,
        )
    if len(matches) != 1:
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTI_CLAUSE_TARGET_AMBIGUOUS,
            rule_id="nz_instruction_semantics_blocked_multi_clause_target_ambiguous",
            clause_count=clause_count,
            target_citation_status=NZTargetCitationStatus.AMBIGUOUS,
        )
    explicit_target_citation, old_text, new_text = matches[0]
    cleaned_new_text, occurrence_scope = _text_substitution_scope(new_text)
    if occurrence_scope != NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE:
        if occurrence_scope == NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE:
            return _TextSubstitutionCandidate(
                substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_MULTI_CLAUSE_EACH_PLACE_TEXT_SUBSTITUTION,
                subfamily=NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
                rule_id="nz_instruction_semantics_direct_multi_clause_each_place_text_substitution_candidate",
                clause_count=clause_count,
                explicit_target_citation=explicit_target_citation,
                target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
                old_text=old_text,
                new_text=cleaned_new_text,
                scope=occurrence_scope,
            )
        return _TextSubstitutionCandidate(
            substitution_status=NZTextSubstitutionStatus.BLOCKED_MULTIPLE_OCCURRENCE_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_semantics_blocked_multiple_occurrence_text_substitution",
            clause_count=clause_count,
            explicit_target_citation=explicit_target_citation,
            target_citation_status=NZTargetCitationStatus.MATCHED,
            old_text=old_text,
            new_text=cleaned_new_text,
            scope=occurrence_scope,
        )
    return _TextSubstitutionCandidate(
        substitution_status=NZTextSubstitutionStatus.CANDIDATE_DIRECT_MULTI_CLAUSE_TEXT_SUBSTITUTION,
        subfamily=NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        rule_id="nz_instruction_semantics_direct_multi_clause_text_substitution_candidate",
        clause_count=clause_count,
        explicit_target_citation=explicit_target_citation,
        target_citation_status=NZTargetCitationStatus.MATCHED_IN_MULTI_CLAUSE_PAYLOAD,
        old_text=old_text,
        new_text=cleaned_new_text,
        scope=occurrence_scope,
    )


def _latest_oracle_text_witness(
    *,
    text_substitution: _TextSubstitutionCandidate,
    target_address: str,
    target_document: NZSourceDocument | None,
) -> _LatestOracleTextWitness:
    if text_substitution.subfamily not in {
        NZTextSubstitutionSubfamily.DIRECT_SINGLE_TEXT_SUBSTITUTION,
        NZTextSubstitutionSubfamily.DIRECT_EACH_PLACE_TEXT_SUBSTITUTION,
    }:
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.NOT_APPLICABLE_NOT_DIRECT_TEXT_SUBSTITUTION,
            rule_id="nz_instruction_latest_oracle_text_not_applicable",
        )
    if target_document is None:
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.NOT_RUN_TARGET_DOCUMENT_UNAVAILABLE,
            rule_id="nz_instruction_latest_oracle_text_target_document_unavailable",
        )
    target_node = _latest_oracle_target_node(target_document, target_address)
    if isinstance(target_node, _LatestOracleTextWitness):
        return target_node
    old_occurrences = normalized_nz_inline_occurrence_count(target_node.node.text, text_substitution.old_text)
    new_occurrences = normalized_nz_inline_occurrence_count(target_node.node.text, text_substitution.new_text)
    is_deletion = not text_substitution.new_text.strip()
    if is_deletion and old_occurrences == 0:
        # Omit-only deletion: the omitted span is absent from the latest
        # consolidated text, consistent with its deletion. ``new_text`` is empty
        # so the substitution-oriented branches below never apply.
        status = NZLatestOracleTextStatus.ORACLE_OLD_TEXT_DELETED
    elif is_deletion:
        # The span the instruction says to omit is still present in the latest
        # text — refuse rather than assert a deletion the oracle contradicts.
        status = NZLatestOracleTextStatus.ORACLE_OLD_TEXT_NOT_DELETED
    elif old_occurrences == 0 and new_occurrences == 1:
        status = NZLatestOracleTextStatus.ORACLE_NEW_TEXT_ONLY
    elif (
        text_substitution.scope == NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE
        and old_occurrences == 0
        and new_occurrences > 1
    ):
        status = NZLatestOracleTextStatus.ORACLE_NEW_TEXT_ONLY_EACH_PLACE
    elif normalized_nz_inline_contains(text_substitution.new_text, text_substitution.old_text) and new_occurrences > 0:
        status = NZLatestOracleTextStatus.ORACLE_NEW_TEXT_CONTAINS_OLD_TEXT
    elif old_occurrences == 1 and new_occurrences == 0:
        status = NZLatestOracleTextStatus.ORACLE_OLD_TEXT_ONLY
    elif old_occurrences > 0 and new_occurrences > 0:
        status = NZLatestOracleTextStatus.ORACLE_OLD_AND_NEW_TEXT
    else:
        status = NZLatestOracleTextStatus.ORACLE_NEITHER_OLD_NOR_NEW_TEXT
    return _LatestOracleTextWitness(
        oracle_text_status=status,
        rule_id=f"nz_instruction_latest_oracle_text_{status}",
        target_resolution_status=target_node.target_resolution_status,
        target_resolution_rule_id=target_node.rule_id,
        target_source_path=target_node.node.path,
        old_text_occurrences=old_occurrences,
        new_text_occurrences=new_occurrences,
    )


def _latest_oracle_target_node(
    target_document: NZSourceDocument,
    target_address: str,
) -> _LatestOracleTargetResolution | _LatestOracleTextWitness:
    suffixes = _source_path_suffix_candidates_from_target_address(target_address)
    if not suffixes:
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.BLOCKED_TARGET_ADDRESS_UNMAPPED,
            rule_id="nz_instruction_latest_oracle_text_target_address_unmapped",
        )
    matches = tuple(
        _LatestOracleTargetResolution(
            node=node,
            target_resolution_status=NZLatestOracleTargetResolutionStatus.EXACT_SOURCE_PATH,
            rule_id="nz_instruction_latest_oracle_target_exact_source_path",
        )
        for suffix in suffixes
        for node in target_document.nodes
        if node.path[-len(suffix) :] == suffix
    )
    if not matches:
        matches = tuple(
            _LatestOracleTargetResolution(
                node=node,
                target_resolution_status=NZLatestOracleTargetResolutionStatus.VIA_UNLABELED_SOURCE_CARRIER,
                rule_id="nz_instruction_latest_oracle_target_via_unlabeled_source_carrier",
            )
            for suffix in suffixes
            for node in target_document.nodes
            if _path_matches_suffix_with_unlabeled_carrier(node.path, suffix)
        )
    if not matches:
        nearest_node = _nearest_existing_source_node(target_document, suffixes[0])
        if nearest_node is not None:
            return _LatestOracleTextWitness(
                oracle_text_status=NZLatestOracleTextStatus.BLOCKED_TARGET_GRANULARITY_NOT_INDEXED,
                rule_id="nz_instruction_latest_oracle_text_target_granularity_not_indexed",
                target_source_path=nearest_node.path,
            )
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.BLOCKED_TARGET_SOURCE_NODE_MISSING,
            rule_id="nz_instruction_latest_oracle_text_target_source_node_missing",
        )
    if len(matches) > 1:
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.BLOCKED_TARGET_SOURCE_NODE_NOT_UNIQUE,
            rule_id="nz_instruction_latest_oracle_text_target_source_node_not_unique",
        )
    if matches[0].node.deletion_status:
        return _LatestOracleTextWitness(
            oracle_text_status=NZLatestOracleTextStatus.BLOCKED_TARGET_SOURCE_NODE_DELETED,
            rule_id="nz_instruction_latest_oracle_text_target_source_node_deleted",
            target_source_path=matches[0].node.path,
        )
    return matches[0]


def _nearest_existing_source_node(target_document: NZSourceDocument, suffix: tuple[str, ...]) -> NZSourceNode | None:
    for width in range(len(suffix) - 1, 0, -1):
        parent_suffix = suffix[:width]
        matches = tuple(node for node in target_document.nodes if node.path[-len(parent_suffix) :] == parent_suffix)
        if len(matches) == 1:
            return matches[0]
    return None


def _path_matches_suffix_with_unlabeled_carrier(path: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    path_index = len(path) - 1
    suffix_index = len(suffix) - 1
    used_carrier = False
    while path_index >= 0 and suffix_index >= 0:
        if path[path_index] == suffix[suffix_index]:
            path_index -= 1
            suffix_index -= 1
            continue
        if "#" in path[path_index]:
            used_carrier = True
            path_index -= 1
            continue
        return False
    return used_carrier and suffix_index < 0


def _source_path_suffix_candidates_from_target_address(target_address: str) -> tuple[tuple[str, ...], ...]:
    parts = [part for part in target_address.split("/") if ":" in part]
    suffixes: list[tuple[str, ...]] = [()]
    for part in parts:
        kind, label = part.split(":", 1)
        if not label:
            return ()
        if kind == "section":
            segment_candidates = (f"prov:{label}",)
        elif kind == "subsection":
            segment_candidates = (f"subprov:{label}", f"label-para:{label}")
        elif kind == "paragraph":
            segment_candidates = (f"label-para:{label}",)
        elif kind in {"part", "schedule"}:
            segment_candidates = (f"{kind}:{label}",)
        else:
            return ()
        suffixes = [(*suffix, segment) for suffix in suffixes for segment in segment_candidates]
    return tuple(tuple(suffix) for suffix in suffixes)


def _replacement_clause_count(text: str) -> int:
    normalized = " ".join(text.lower().split())
    return len(re.findall(r"\b(?:replace|substitute)\b", normalized))


def _omitting_substituting_clause_count(text: str) -> int:
    normalized = " ".join(text.lower().split())
    return len(re.findall(r"\bomitting\b", normalized))


def _numbered_instruction_clauses(text: str) -> tuple[str, ...]:
    normalized = " ".join(text.split())
    boundaries = list(
        re.finditer(
            r"(?:^|\s)(?P<number>\d+)\s+(?=(?:[Ii]n\b|[Rr]eplace\s+with:|[Ss]ection\b|[Ii]s\s+amended\b))",
            normalized,
        )
    )
    if len(boundaries) < 2:
        return ()
    clauses: list[str] = []
    for index, boundary in enumerate(boundaries):
        start = boundary.end()
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(normalized)
        clause = normalized[start:end].strip(" ;")
        if clause:
            clauses.append(clause)
    return tuple(clauses)


def _extract_replace_with_pieces(text: str) -> tuple[str, str, str] | None:
    normalized = " ".join(text.split())
    in_match = re.search(
        r"\bin\s+(?P<target>section\s+\S+)\s*,\s*replace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)(?:\s*\.\s*)?$",
        normalized,
        re.IGNORECASE,
    )
    if in_match is not None:
        explicit_target_citation = in_match.group("target").strip(" ,;")
        old_text = in_match.group("old").strip(" ,;")
        new_text = in_match.group("new").strip(" ,;.")
        if old_text and new_text:
            return explicit_target_citation, old_text, new_text
    match = re.search(r"\breplace\s+(?P<body>.+?)\s+with\s+(?P<new>.+?)(?:\s*\.\s*)?$", normalized, re.IGNORECASE)
    if match is None:
        return None
    body = match.group("body").strip(" ,;")
    new_text = match.group("new").strip(" ,;.")
    body_tokens = body.split()
    if len(body_tokens) < 3 or body_tokens[0].lower() != "section":
        return None
    explicit_target_citation = " ".join(body_tokens[:2]).strip()
    old_text = " ".join(body_tokens[2:]).strip(" ,;")
    if not old_text or not new_text:
        return None
    return explicit_target_citation, old_text, new_text


def _extract_omitting_substituting_pieces(text: str) -> tuple[str, str, str] | None:
    normalized = " ".join(text.split())
    match = re.search(
        r"\bomitting\s+(?P<body>.+?)\s+and\s+substituting\s+(?P<new>.+?)(?:\s*\.\s*)?$",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    prefix = normalized[: match.start()].strip(" ,;-")
    body = match.group("body").strip(" ,;")
    new_text = match.group("new").strip(" ,;.")
    if not body or not new_text:
        return None
    prefix_citation = _last_section_citation(prefix)
    if prefix_citation:
        return prefix_citation, body, new_text
    citation_match = re.match(
        r"(?:section\s+)?(?P<citation>\d+[A-Za-z]*(?:\([^)]+\))*)\s+(?P<old>.+)$",
        body,
        re.IGNORECASE,
    )
    if citation_match is None:
        return None
    explicit_target_citation = "section " + citation_match.group("citation")
    old_text = citation_match.group("old").strip(" ,;")
    if not old_text:
        return None
    return explicit_target_citation, old_text, new_text


def _last_section_citation(text: str) -> str:
    matches = tuple(
        re.finditer(
            r"\bsection\s+(?P<citation>\d+[A-Za-z]*(?:\([^)]+\))*)",
            text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return ""
    return "section " + matches[-1].group("citation")


def _looks_structural_omitting_substituting_payload(old_text: str, new_text: str) -> bool:
    normalized_old = " ".join(old_text.lower().split())
    normalized_new = " ".join(new_text.lower().split())
    if normalized_new.startswith(("the following paragraph", "the following subsection", "the following section")):
        return True
    if re.search(r"\b\d+\s+is\s+amended\b", normalized_new):
        return True
    if re.search(r";\s+and\s+[a-z]\s+by\s+(?:omitting|repealing|inserting)\b", normalized_new):
        return True
    if re.search(r"\bby\s+(?:repealing|inserting)\b", normalized_new):
        return True
    if re.search(r"\b(?:subparagraph|subsection|paragraph)\s+\([^)]+\)", normalized_new):
        return True
    return normalized_old.startswith(("paragraph ", "paragraphs ", "subsection ", "subsections "))


def _target_citation_matches(explicit_target_citation: str, target_address: str) -> bool:
    expected = _citation_from_target_address(target_address)
    return bool(expected) and _citation_key(explicit_target_citation) == _citation_key(expected)


def _text_substitution_scope(new_text: str) -> tuple[str, NZTextSubstitutionScope]:
    normalized = " ".join(new_text.split()).strip(" ,;.")
    suffix = " in each place"
    if normalized.lower().endswith(suffix):
        return normalized[: -len(suffix)].strip(" ,;."), NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE
    return normalized, NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE


def _omitting_substitution_scope(old_text: str, new_text: str) -> tuple[str, str, NZTextSubstitutionScope]:
    cleaned_old = " ".join(old_text.split()).strip(" ,;.")
    cleaned_new = " ".join(new_text.split()).strip(" ,;.")
    if re.search(r"\bin each place\b|\bwherever\b", cleaned_old, re.IGNORECASE):
        cleaned_old = re.sub(r"\s+in each place(?: where it appears)?\b.*$", "", cleaned_old, flags=re.IGNORECASE)
        cleaned_new = re.sub(r"^in each case\s+", "", cleaned_new, flags=re.IGNORECASE)
        return cleaned_old.strip(" ,;."), cleaned_new.strip(" ,;."), NZTextSubstitutionScope.INLINE_TEXT_EACH_PLACE
    return cleaned_old, cleaned_new, NZTextSubstitutionScope.INLINE_TEXT_SINGLE_OCCURRENCE


def _citation_from_target_address(target_address: str) -> str:
    parts = [part for part in target_address.split("/") if ":" in part]
    section = ""
    suffixes: list[str] = []
    for part in parts:
        kind, label = part.split(":", 1)
        if kind == "section":
            section = label
        elif kind in {"subsection", "paragraph"}:
            suffixes.append(label)
    if not section:
        return ""
    return "section " + section + "".join(f"({suffix})" for suffix in suffixes)


def _citation_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def write_evidence_jsonl(report: NZInstructionWorkQueueReport, path: Path) -> int:
    rows = [row.to_dict() for row in report.operation_evidence_rows()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main(args: Any) -> None:
    report = build_archived_work_instruction_workqueue(Path(args.db), args.work_id)
    queue_status = args.queue_status or ""
    if args.candidate_only:
        queue_status = "candidate"
    filtered_rows = report.filtered_rows(
        queue_status=queue_status,
        instruction_family=args.instruction_family,
        instruction_shape=args.instruction_shape,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
    )
    evidence_row_count: int | None = None
    if args.evidence_jsonl:
        evidence_rows = [row.to_dict() for row in report.operation_evidence_rows_for(filtered_rows)]
        output_path = Path(args.evidence_jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in evidence_rows),
            encoding="utf-8",
        )
        evidence_row_count = len(evidence_rows)
    if args.json:
        payload = report.to_jsonable(
            summary_only=args.summary_only,
            row_limit=args.limit,
            queue_status=queue_status,
            instruction_family=args.instruction_family,
            instruction_shape=args.instruction_shape,
            instruction_subfamily_status=args.instruction_subfamily_status,
            instruction_subfamily=args.instruction_subfamily,
            payload_structural_subfamily_status=args.payload_structural_subfamily_status,
            payload_structural_subfamily=args.payload_structural_subfamily,
        )
        if args.evidence_rows and not args.summary_only:
            selected_rows = filtered_rows if args.limit is None else filtered_rows[: args.limit]
            payload["evidence"] = {
                "operation_rows": [row.to_dict() for row in report.operation_evidence_rows_for(selected_rows)],
                "finding_rows": [],
            }
        if evidence_row_count is not None:
            payload["evidence_jsonl"] = {
                "path": args.evidence_jsonl,
                "rows": evidence_row_count,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if evidence_row_count is not None:
        print(f"wrote_evidence_rows={evidence_row_count} path={args.evidence_jsonl}")
    summary = report.summary()
    filters = _jsonable_filters(
        queue_status=queue_status,
        instruction_family=args.instruction_family,
        instruction_shape=args.instruction_shape,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(filtered_rows)} filters={filters} "
        f"queue_status_counts={summary['queue_status_counts']} "
        f"instruction_family_counts={summary['instruction_semantic_candidate_family_counts']} "
        f"instruction_shape_counts={summary['payload_instruction_shape_counts']} "
        f"structural_subfamily_status_counts={summary['payload_structural_subfamily_status_counts']}"
    )
    if args.summary_only:
        return
    for row in filtered_rows[: args.limit]:
        print(
            f"{row.row_id}\t{row.queue_status}\t{row.instruction_semantic_candidate_family or '-'}\t"
            f"{row.operation_family}\t{row.target_address or '-'}\t{row.amending_work_id or '-'}"
        )
    if len(filtered_rows) > args.limit:
        print(f"... {len(filtered_rows) - args.limit} more")
