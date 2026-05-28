"""Canonical effect candidates for New Zealand operation witnesses.

This is the first NZ surface that builds core ``LegalOperation`` envelopes, but
it still does not apply them, materialize text, or claim replay agreement. Rows
classified as repeal-ready are emitted as candidates. Direct text substitution
rows may also be emitted when the instruction-workqueue surface has owned the
parse and a latest-oracle witness, or when an exact-target archived source
change witness supports a candidate-only text replacement. Preflight still
blocks candidate-only evidence lanes that are not dry-run replay proof.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow, CorpusOperationEvidenceRow, CorpusRowStatus
from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import FacetKind, StructuralAction, TextPatchKindEnum
from lawvm.core.source_version_window import source_version_date_window_diagnostic_detail
from lawvm.new_zealand.effect_readiness import (
    NZEffectReadinessReport,
    build_archived_work_effect_readiness_surface,
    build_effect_readiness_surface,
)
from lawvm.new_zealand.instruction_workqueue import (
    NZInstructionWorkQueueReport,
    NZInstructionWorkQueueRow,
    build_instruction_workqueue,
)
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, NZOperationWitnessRow
from lawvm.new_zealand.payload_surface import NZPayloadSurfaceReport, NZPayloadWitnessRow
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.text_comparison import normalized_nz_inline_occurrence_count
from lawvm.new_zealand.version_diff import NZArchivedVersion, NZArchivedVersionDateWindow, archived_xml_version_date_window
from lawvm.new_zealand.version_diff import archived_xml_version_change_window


NZ_EFFECT_CANDIDATE_REPLAY_BLOCKED_RULE_ID = "nz_effect_candidates_not_replayed"
NZ_EFFECT_CANDIDATE_OPERATION_MISSING_RULE_ID = "nz_effect_candidate_emitted_operation_missing"
NZ_EFFECT_PREFLIGHT_REFUSED_BLOCKED_CANDIDATE_ROWS_RULE_ID = "nz_effect_preflight_refused_blocked_candidate_rows"
NZ_EFFECT_PREFLIGHT_NO_CANDIDATE_ROWS_RULE_ID = "nz_effect_preflight_no_candidate_rows"
NZ_EFFECT_PREFLIGHT_CANDIDATE_OPERATION_MISSING_RULE_ID = "nz_effect_preflight_candidate_operation_missing"
NZ_EFFECT_PREFLIGHT_SOURCE_CHANGE_ONLY_CANDIDATES_RULE_ID = "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable"
NZ_EFFECT_PREFLIGHT_TARGET_RECOVERY_CANDIDATES_RULE_ID = "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable"
NZ_EFFECT_PREFLIGHT_NON_REPLAYABLE_CANDIDATES_RULE_ID = "nz_effect_preflight_non_replayable_candidates_not_dry_run_replayable"
NZ_TEXT_REPLACE_CANDIDATE_RULE_ID = "nz_text_replace_candidate_from_direct_instruction_workqueue"
NZ_TEXT_REPLACE_SOURCE_CHANGE_CANDIDATE_RULE_ID = "nz_text_replace_candidate_from_archived_source_change_witness"
NZ_TEXT_REPLACE_LATEST_ORACLE_WITNESS_BLOCKED_RULE_ID = "nz_text_replace_candidate_latest_oracle_witness_unavailable"
NZ_REPEAL_PAYLOAD_CORROBORATED_RULE_ID = "nz_repeal_payload_target_corroborated"
NZ_REPEAL_PAYLOAD_NOT_DIRECT_RULE_ID = "nz_repeal_payload_corroboration_not_required_non_direct_payload"
NZ_REPEAL_PAYLOAD_UNPARSED_BLOCKED_RULE_ID = "nz_repeal_payload_target_unparsed"
NZ_REPEAL_PAYLOAD_MISMATCH_BLOCKED_RULE_ID = "nz_repeal_payload_target_mismatch"

_TEXT_REPLACE_ALLOWED_ORACLE_TEXT_STATUSES = frozenset(
    {
        "oracle_new_text_only",
        "oracle_new_text_only_each_place",
        "oracle_new_text_contains_old_text",
    }
)
_TEXT_REPLACE_CANDIDATE_SUBFAMILIES = frozenset(
    {
        "direct_single_text_substitution",
        "direct_each_place_text_substitution",
    }
)
_TEXT_REPLACE_ALLOWED_ORACLE_TARGET_RESOLUTION_STATUSES = frozenset(
    {
        "exact_source_path",
        "via_unlabeled_source_carrier",
    }
)
_TEXT_REPLACE_SOURCE_CHANGE_CANDIDATE_TARGET_RESOLUTION_STATUSES = frozenset({"exact_source_path"})
_TEXT_REPLACE_OBSERVED_SOURCE_CHANGE_STATUSES = frozenset(
    {
        "observed_single_replacement",
        "observed_each_place_replacement",
    }
)


@dataclass(frozen=True)
class _RepealPayloadCorroboration:
    status: str
    rule_id: str
    cited_targets: tuple[str, ...] = ()
    blocking_rule_id: str = ""


@dataclass(frozen=True)
class _SourceChangeTextWitness:
    status: str
    rule_id: str
    truth_claim: str = "source_text_change_witness_not_replay_proof"
    change_window_truth_claim: str = ""
    requested_date: str = ""
    before_version_id: str = ""
    before_xml_locator: str = ""
    on_or_after_version_id: str = ""
    on_or_after_xml_locator: str = ""
    target_source_path: tuple[str, ...] = ()
    before_old_text_occurrences: int = 0
    before_new_text_occurrences: int = 0
    on_or_after_old_text_occurrences: int = 0
    on_or_after_new_text_occurrences: int = 0


@dataclass(frozen=True)
class NZCanonicalEffectCandidateRow:
    row_id: str
    operation_row_id: str
    effect_readiness_row_id: str
    status: str
    action: str = ""
    target_address: str = ""
    operation: LegalOperation | None = None
    blocking_rule_id: str = ""
    source_path: tuple[str, ...] = ()
    source_xml_id: str = ""
    source_xml_path: str = ""
    source_zone: str = ""
    source_kind: str = ""
    amended_provision: str = ""
    operation_text: str = ""
    amendment_date: str = ""
    amendment_date_iso: str = ""
    source_version_date_window_status: str = ""
    source_version_date_window_rule_id: str = ""
    source_version_date_window_truth_claim: str = ""
    source_version_date_window_requested_date: str = ""
    source_version_date_window: Mapping[str, Any] = field(default_factory=dict)
    source_version_on_or_before_version_id: str = ""
    source_version_on_or_before_xml_locator: str = ""
    source_version_on_or_before_date: str = ""
    source_version_on_or_after_version_id: str = ""
    source_version_on_or_after_xml_locator: str = ""
    source_version_on_or_after_date: str = ""
    amending_work_id: str = ""
    amending_legislation: str = ""
    amending_provisions: tuple[str, ...] = ()
    amending_provision_hrefs: tuple[str, ...] = ()
    witness_text: str = ""
    operation_family: str = ""
    operation_lowering_readiness_status: str = ""
    operation_target_surface_status: str = ""
    operation_target_hint_status: str = ""
    operation_target_address_status: str = ""
    operation_target_blocking_rule_id: str = ""
    operation_dependency_status: str = ""
    payload_role: str = ""
    payload_semantics_status: str = ""
    payload_instruction_shape: str = ""
    payload_instruction_safety: str = ""
    instruction_semantic_candidate_status: str = ""
    instruction_semantic_candidate_family: str = ""
    instruction_semantic_rule_id: str = ""
    instruction_workqueue_row_id: str = ""
    instruction_subfamily_status: str = ""
    instruction_subfamily: str = ""
    instruction_subfamily_rule_id: str = ""
    payload_structural_subfamily_status: str = ""
    payload_structural_subfamily: str = ""
    payload_structural_subfamily_rule_id: str = ""
    old_text: str = ""
    new_text: str = ""
    text_substitution_scope: str = ""
    latest_oracle_text_status: str = ""
    latest_oracle_text_rule_id: str = ""
    latest_oracle_target_resolution_status: str = ""
    latest_oracle_target_resolution_rule_id: str = ""
    latest_oracle_target_source_path: tuple[str, ...] = ()
    latest_oracle_old_text_occurrences: int = 0
    latest_oracle_new_text_occurrences: int = 0
    text_replace_witness_support_status: str = ""
    text_replace_witness_support_rule_id: str = ""
    text_replace_witness_support_truth_claim: str = ""
    source_change_text_witness_status: str = ""
    source_change_text_witness_rule_id: str = ""
    source_change_text_witness_truth_claim: str = ""
    source_change_text_change_window_truth_claim: str = ""
    source_change_text_witness_requested_date: str = ""
    source_change_text_before_version_id: str = ""
    source_change_text_before_xml_locator: str = ""
    source_change_text_on_or_after_version_id: str = ""
    source_change_text_on_or_after_xml_locator: str = ""
    source_change_text_target_source_path: tuple[str, ...] = ()
    source_change_text_before_old_occurrences: int = 0
    source_change_text_before_new_occurrences: int = 0
    source_change_text_on_or_after_old_occurrences: int = 0
    source_change_text_on_or_after_new_occurrences: int = 0
    repeal_payload_corroboration_status: str = ""
    repeal_payload_corroboration_rule_id: str = ""
    repeal_payload_cited_targets: tuple[str, ...] = ()
    payload_match_count: int = 0
    payload_match_kinds: tuple[str, ...] = ()
    payload_match_headings: tuple[str, ...] = ()
    payload_match_xml_ids: tuple[str, ...] = ()
    payload_match_paths: tuple[tuple[str, ...], ...] = ()
    payload_match_labels: tuple[str, ...] = ()
    payload_match_texts: tuple[str, ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "operation_row_id": self.operation_row_id,
            "effect_readiness_row_id": self.effect_readiness_row_id,
            "status": self.status,
            "action": self.action,
            "target_address": self.target_address,
            "operation": _operation_jsonable(self.operation),
            "blocking_rule_id": self.blocking_rule_id,
            "source_path": list(self.source_path),
            "source_xml_id": self.source_xml_id,
            "source_xml_path": self.source_xml_path,
            "source_zone": self.source_zone,
            "source_kind": self.source_kind,
            "amended_provision": self.amended_provision,
            "operation_text": self.operation_text,
            "amendment_date": self.amendment_date,
            "amendment_date_iso": self.amendment_date_iso,
            "source_version_date_window_status": self.source_version_date_window_status,
            "source_version_date_window_rule_id": self.source_version_date_window_rule_id,
            "source_version_date_window_truth_claim": self.source_version_date_window_truth_claim,
            "source_version_date_window_requested_date": self.source_version_date_window_requested_date,
            "source_version_date_window": dict(self.source_version_date_window),
            "source_version_on_or_before_version_id": self.source_version_on_or_before_version_id,
            "source_version_on_or_before_xml_locator": self.source_version_on_or_before_xml_locator,
            "source_version_on_or_before_date": self.source_version_on_or_before_date,
            "source_version_on_or_after_version_id": self.source_version_on_or_after_version_id,
            "source_version_on_or_after_xml_locator": self.source_version_on_or_after_xml_locator,
            "source_version_on_or_after_date": self.source_version_on_or_after_date,
            "amending_work_id": self.amending_work_id,
            "amending_legislation": self.amending_legislation,
            "amending_provisions": list(self.amending_provisions),
            "amending_provision_hrefs": list(self.amending_provision_hrefs),
            "witness_text": self.witness_text,
            "operation_family": self.operation_family,
            "operation_lowering_readiness_status": self.operation_lowering_readiness_status,
            "operation_target_surface_status": self.operation_target_surface_status,
            "operation_target_hint_status": self.operation_target_hint_status,
            "operation_target_address_status": self.operation_target_address_status,
            "operation_target_blocking_rule_id": self.operation_target_blocking_rule_id,
            "operation_dependency_status": self.operation_dependency_status,
            "payload_role": self.payload_role,
            "payload_semantics_status": self.payload_semantics_status,
            "payload_instruction_shape": self.payload_instruction_shape,
            "payload_instruction_safety": self.payload_instruction_safety,
            "instruction_semantic_candidate_status": self.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": self.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": self.instruction_semantic_rule_id,
            "instruction_workqueue_row_id": self.instruction_workqueue_row_id,
            "instruction_subfamily_status": self.instruction_subfamily_status,
            "instruction_subfamily": self.instruction_subfamily,
            "instruction_subfamily_rule_id": self.instruction_subfamily_rule_id,
            "payload_structural_subfamily_status": self.payload_structural_subfamily_status,
            "payload_structural_subfamily": self.payload_structural_subfamily,
            "payload_structural_subfamily_rule_id": self.payload_structural_subfamily_rule_id,
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
            "text_replace_witness_support_status": self.text_replace_witness_support_status,
            "text_replace_witness_support_rule_id": self.text_replace_witness_support_rule_id,
            "text_replace_witness_support_truth_claim": self.text_replace_witness_support_truth_claim,
            "source_change_text_witness_status": self.source_change_text_witness_status,
            "source_change_text_witness_rule_id": self.source_change_text_witness_rule_id,
            "source_change_text_witness_truth_claim": self.source_change_text_witness_truth_claim,
            "source_change_text_change_window_truth_claim": self.source_change_text_change_window_truth_claim,
            "source_change_text_witness_requested_date": self.source_change_text_witness_requested_date,
            "source_change_text_before_version_id": self.source_change_text_before_version_id,
            "source_change_text_before_xml_locator": self.source_change_text_before_xml_locator,
            "source_change_text_on_or_after_version_id": self.source_change_text_on_or_after_version_id,
            "source_change_text_on_or_after_xml_locator": self.source_change_text_on_or_after_xml_locator,
            "source_change_text_target_source_path": list(self.source_change_text_target_source_path),
            "source_change_text_before_old_occurrences": self.source_change_text_before_old_occurrences,
            "source_change_text_before_new_occurrences": self.source_change_text_before_new_occurrences,
            "source_change_text_on_or_after_old_occurrences": self.source_change_text_on_or_after_old_occurrences,
            "source_change_text_on_or_after_new_occurrences": self.source_change_text_on_or_after_new_occurrences,
            "repeal_payload_corroboration_status": self.repeal_payload_corroboration_status,
            "repeal_payload_corroboration_rule_id": self.repeal_payload_corroboration_rule_id,
            "repeal_payload_cited_targets": list(self.repeal_payload_cited_targets),
            "payload_match_count": self.payload_match_count,
            "payload_match_kinds": list(self.payload_match_kinds),
            "payload_match_headings": list(self.payload_match_headings),
            "payload_match_xml_ids": list(self.payload_match_xml_ids),
            "payload_match_paths": [list(path) for path in self.payload_match_paths],
            "payload_match_labels": list(self.payload_match_labels),
            "payload_match_texts": list(self.payload_match_texts),
        }


@dataclass(frozen=True)
class NZCanonicalEffectCandidateReport:
    work_id: str
    rows: tuple[NZCanonicalEffectCandidateRow, ...]

    def summary(self) -> dict[str, Any]:
        status_counts = Counter(row.status for row in self.rows)
        emitted_rows = tuple(row for row in self.rows if row.status == "candidate_emitted")
        candidate_rows = tuple(row for row in emitted_rows if row.operation is not None)
        missing_operation_rows = tuple(row for row in emitted_rows if row.operation is None)
        action_counts = Counter(row.action or "__none__" for row in self.rows)
        operation_family_counts = Counter(row.operation_family or "__none__" for row in self.rows)
        blocked_operation_family_counts = Counter(
            row.operation_family or "__none__" for row in self.rows if row.status != "candidate_emitted"
        )
        blocker_counts = Counter(row.blocking_rule_id or "__none__" for row in self.rows if row.status != "candidate_emitted")
        blocked_family_rule_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.blocking_rule_id or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        blocked_family_payload_shape_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.payload_instruction_shape or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        blocked_family_payload_safety_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.payload_instruction_safety or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        blocked_family_target_status_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.operation_target_address_status or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        instruction_status_counts = Counter(row.instruction_semantic_candidate_status or "__none__" for row in self.rows)
        blocked_family_instruction_status_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.instruction_semantic_candidate_status or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        blocked_family_instruction_subfamily_status_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.instruction_subfamily_status or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        candidate_witness_rule_counts = Counter(
            _candidate_witness_rule_id(row) for row in self.rows if row.status == "candidate_emitted"
        )
        candidate_action_witness_rule_counts = Counter(
            f"{row.action or '__none__'}|{_candidate_witness_rule_id(row)}"
            for row in self.rows
            if row.status == "candidate_emitted"
        )
        candidate_action_source_change_text_counts = Counter(
            f"{row.action or '__none__'}|{row.source_change_text_witness_status or '__none__'}"
            for row in self.rows
            if row.status == "candidate_emitted"
        )
        blocked_family_source_change_text_counts = Counter(
            f"{row.operation_family or '__none__'}|{row.source_change_text_witness_status or '__none__'}"
            for row in self.rows
            if row.status != "candidate_emitted"
        )
        instruction_family_counts = Counter(row.instruction_semantic_candidate_family or "__none__" for row in self.rows)
        instruction_subfamily_status_counts = Counter(row.instruction_subfamily_status or "__none__" for row in self.rows)
        structural_subfamily_status_counts = Counter(
            row.payload_structural_subfamily_status or "__none__" for row in self.rows
        )
        structural_subfamily_counts = Counter(row.payload_structural_subfamily or "__none__" for row in self.rows)
        latest_oracle_text_status_counts = Counter(row.latest_oracle_text_status for row in self.rows if row.latest_oracle_text_status)
        latest_oracle_target_resolution_counts = Counter(
            row.latest_oracle_target_resolution_status
            for row in self.rows
            if row.latest_oracle_target_resolution_status
        )
        text_replace_support_counts = Counter(
            row.text_replace_witness_support_status or "__none__" for row in self.rows
        )
        text_replace_action_support_counts = Counter(
            f"{row.action or '__none__'}|{row.text_replace_witness_support_status or '__none__'}"
            for row in self.rows
        )
        source_version_date_window_counts = Counter(
            row.source_version_date_window_status or "__none__" for row in self.rows
        )
        source_change_text_counts = Counter(row.source_change_text_witness_status or "__none__" for row in self.rows)
        repeal_payload_corroboration_counts = Counter(
            row.repeal_payload_corroboration_status
            for row in self.rows
            if row.repeal_payload_corroboration_status
        )
        return {
            "work_id": self.work_id,
            "rows": len(self.rows),
            "candidate_emitted_rows": len(emitted_rows),
            "candidate_operation_missing_rows": len(missing_operation_rows),
            "candidate_status_counts": dict(sorted(status_counts.items())),
            "candidate_action_counts": dict(sorted(action_counts.items())),
            "operation_family_counts": dict(sorted(operation_family_counts.items())),
            "blocked_operation_family_counts": dict(sorted(blocked_operation_family_counts.items())),
            "candidate_blocking_rule_counts": dict(sorted(blocker_counts.items())),
            "blocked_operation_family_rule_counts": dict(sorted(blocked_family_rule_counts.items())),
            "blocked_operation_family_payload_shape_counts": dict(
                sorted(blocked_family_payload_shape_counts.items())
            ),
            "blocked_operation_family_payload_safety_counts": dict(
                sorted(blocked_family_payload_safety_counts.items())
            ),
            "blocked_operation_family_target_status_counts": dict(
                sorted(blocked_family_target_status_counts.items())
            ),
            "instruction_semantic_candidate_status_counts": dict(sorted(instruction_status_counts.items())),
            "blocked_operation_family_instruction_status_counts": dict(
                sorted(blocked_family_instruction_status_counts.items())
            ),
            "blocked_operation_family_instruction_subfamily_status_counts": dict(
                sorted(blocked_family_instruction_subfamily_status_counts.items())
            ),
            "candidate_witness_rule_counts": dict(sorted(candidate_witness_rule_counts.items())),
            "candidate_action_witness_rule_counts": dict(sorted(candidate_action_witness_rule_counts.items())),
            "candidate_action_source_change_text_witness_status_counts": dict(
                sorted(candidate_action_source_change_text_counts.items())
            ),
            "blocked_operation_family_source_change_text_witness_status_counts": dict(
                sorted(blocked_family_source_change_text_counts.items())
            ),
            "instruction_semantic_candidate_family_counts": dict(sorted(instruction_family_counts.items())),
            "instruction_subfamily_status_counts": dict(sorted(instruction_subfamily_status_counts.items())),
            "payload_structural_subfamily_status_counts": dict(sorted(structural_subfamily_status_counts.items())),
            "payload_structural_subfamily_counts": dict(sorted(structural_subfamily_counts.items())),
            "latest_oracle_text_status_counts": dict(sorted(latest_oracle_text_status_counts.items())),
            "latest_oracle_target_resolution_status_counts": dict(sorted(latest_oracle_target_resolution_counts.items())),
            "text_replace_witness_support_status_counts": dict(sorted(text_replace_support_counts.items())),
            "candidate_action_text_replace_witness_support_status_counts": dict(
                sorted(text_replace_action_support_counts.items())
            ),
            "source_version_date_window_status_counts": dict(sorted(source_version_date_window_counts.items())),
            "source_change_text_witness_status_counts": dict(sorted(source_change_text_counts.items())),
            "repeal_payload_corroboration_status_counts": dict(sorted(repeal_payload_corroboration_counts.items())),
            "candidate_operations": len(candidate_rows),
            "replay_claims": False,
            "canonical_effect_candidate_claims": True,
            "replay_blocking_rule_id": NZ_EFFECT_CANDIDATE_REPLAY_BLOCKED_RULE_ID,
        }

    def to_jsonable(
        self,
        *,
        summary_only: bool = False,
        row_limit: int | None = None,
        candidate_status: str = "",
        action: str = "",
        operation_family: str = "",
        blocking_rule: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
        repeal_payload_corroboration_status: str = "",
        operation_lowering_readiness_status: str = "",
        operation_target_address_status: str = "",
        operation_dependency_status: str = "",
        payload_instruction_shape: str = "",
        payload_instruction_safety: str = "",
        instruction_semantic_candidate_status: str = "",
        latest_oracle_text_status: str = "",
        text_replace_witness_support_status: str = "",
        source_change_text_witness_status: str = "",
    ) -> dict[str, Any]:
        filtered_rows = self.filtered_rows(
            candidate_status=candidate_status,
            action=action,
            operation_family=operation_family,
            blocking_rule=blocking_rule,
            instruction_subfamily_status=instruction_subfamily_status,
            instruction_subfamily=instruction_subfamily,
            payload_structural_subfamily_status=payload_structural_subfamily_status,
            payload_structural_subfamily=payload_structural_subfamily,
            repeal_payload_corroboration_status=repeal_payload_corroboration_status,
            operation_lowering_readiness_status=operation_lowering_readiness_status,
            operation_target_address_status=operation_target_address_status,
            operation_dependency_status=operation_dependency_status,
            payload_instruction_shape=payload_instruction_shape,
            payload_instruction_safety=payload_instruction_safety,
            instruction_semantic_candidate_status=instruction_semantic_candidate_status,
            latest_oracle_text_status=latest_oracle_text_status,
            text_replace_witness_support_status=text_replace_witness_support_status,
            source_change_text_witness_status=source_change_text_witness_status,
        )
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "canonical_effect_candidates",
            "truth_claim": "candidate_canonical_effects_not_replayed",
            "replay_claims": False,
            "canonical_effect_candidate_claims": True,
            "summary": self.summary(),
            "filters": _effect_candidate_jsonable_filters(
                candidate_status=candidate_status,
                action=action,
                operation_family=operation_family,
                blocking_rule=blocking_rule,
                instruction_subfamily_status=instruction_subfamily_status,
                instruction_subfamily=instruction_subfamily,
                payload_structural_subfamily_status=payload_structural_subfamily_status,
                payload_structural_subfamily=payload_structural_subfamily,
                repeal_payload_corroboration_status=repeal_payload_corroboration_status,
                operation_lowering_readiness_status=operation_lowering_readiness_status,
                operation_target_address_status=operation_target_address_status,
                operation_dependency_status=operation_dependency_status,
                payload_instruction_shape=payload_instruction_shape,
                payload_instruction_safety=payload_instruction_safety,
                instruction_semantic_candidate_status=instruction_semantic_candidate_status,
                latest_oracle_text_status=latest_oracle_text_status,
                text_replace_witness_support_status=text_replace_witness_support_status,
                source_change_text_witness_status=source_change_text_witness_status,
            ),
            "filtered_summary": NZCanonicalEffectCandidateReport(self.work_id, filtered_rows).summary(),
        }
        if summary_only:
            return payload
        rows = filtered_rows if row_limit is None else filtered_rows[:row_limit]
        payload["rows"] = [row.to_jsonable() for row in rows]
        if row_limit is not None and len(filtered_rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(filtered_rows) - row_limit
        return payload

    def operation_evidence_rows(self) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_candidate_evidence_row(self, row) for row in self.rows)

    def operation_evidence_rows_for(
        self,
        rows: tuple[NZCanonicalEffectCandidateRow, ...],
    ) -> tuple[CorpusOperationEvidenceRow, ...]:
        return tuple(_candidate_evidence_row(self, row) for row in rows)

    def filtered_rows(
        self,
        *,
        candidate_status: str = "",
        action: str = "",
        operation_family: str = "",
        blocking_rule: str = "",
        instruction_subfamily_status: str = "",
        instruction_subfamily: str = "",
        payload_structural_subfamily_status: str = "",
        payload_structural_subfamily: str = "",
        repeal_payload_corroboration_status: str = "",
        operation_lowering_readiness_status: str = "",
        operation_target_address_status: str = "",
        operation_dependency_status: str = "",
        payload_instruction_shape: str = "",
        payload_instruction_safety: str = "",
        instruction_semantic_candidate_status: str = "",
        latest_oracle_text_status: str = "",
        text_replace_witness_support_status: str = "",
        source_change_text_witness_status: str = "",
    ) -> tuple[NZCanonicalEffectCandidateRow, ...]:
        filtered = self.rows
        if candidate_status:
            filtered = tuple(row for row in filtered if row.status == candidate_status)
        if action:
            filtered = tuple(row for row in filtered if row.action == action)
        if operation_family:
            filtered = tuple(row for row in filtered if row.operation_family == operation_family)
        if blocking_rule:
            filtered = tuple(row for row in filtered if row.blocking_rule_id == blocking_rule)
        if instruction_subfamily_status:
            filtered = tuple(
                row for row in filtered if row.instruction_subfamily_status == instruction_subfamily_status
            )
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
        if repeal_payload_corroboration_status:
            filtered = tuple(
                row
                for row in filtered
                if row.repeal_payload_corroboration_status == repeal_payload_corroboration_status
            )
        if operation_lowering_readiness_status:
            filtered = tuple(
                row
                for row in filtered
                if row.operation_lowering_readiness_status == operation_lowering_readiness_status
            )
        if operation_target_address_status:
            filtered = tuple(
                row for row in filtered if row.operation_target_address_status == operation_target_address_status
            )
        if operation_dependency_status:
            filtered = tuple(row for row in filtered if row.operation_dependency_status == operation_dependency_status)
        if payload_instruction_shape:
            filtered = tuple(row for row in filtered if row.payload_instruction_shape == payload_instruction_shape)
        if payload_instruction_safety:
            filtered = tuple(row for row in filtered if row.payload_instruction_safety == payload_instruction_safety)
        if instruction_semantic_candidate_status:
            filtered = tuple(
                row
                for row in filtered
                if row.instruction_semantic_candidate_status == instruction_semantic_candidate_status
            )
        if latest_oracle_text_status:
            filtered = tuple(row for row in filtered if row.latest_oracle_text_status == latest_oracle_text_status)
        if text_replace_witness_support_status:
            filtered = tuple(
                row
                for row in filtered
                if row.text_replace_witness_support_status == text_replace_witness_support_status
            )
        if source_change_text_witness_status:
            filtered = tuple(
                row
                for row in filtered
                if row.source_change_text_witness_status == source_change_text_witness_status
            )
        return filtered


def _effect_candidate_jsonable_filters(
    *,
    candidate_status: str,
    action: str,
    operation_family: str,
    blocking_rule: str,
    instruction_subfamily_status: str,
    instruction_subfamily: str,
    payload_structural_subfamily_status: str,
    payload_structural_subfamily: str,
    repeal_payload_corroboration_status: str,
    operation_lowering_readiness_status: str,
    operation_target_address_status: str,
    operation_dependency_status: str,
    payload_instruction_shape: str,
    payload_instruction_safety: str,
    instruction_semantic_candidate_status: str,
    latest_oracle_text_status: str,
    text_replace_witness_support_status: str,
    source_change_text_witness_status: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "candidate_status": candidate_status,
            "action": action,
            "operation_family": operation_family,
            "blocking_rule": blocking_rule,
            "instruction_subfamily_status": instruction_subfamily_status,
            "instruction_subfamily": instruction_subfamily,
            "payload_structural_subfamily_status": payload_structural_subfamily_status,
            "payload_structural_subfamily": payload_structural_subfamily,
            "repeal_payload_corroboration_status": repeal_payload_corroboration_status,
            "operation_lowering_readiness_status": operation_lowering_readiness_status,
            "operation_target_address_status": operation_target_address_status,
            "operation_dependency_status": operation_dependency_status,
            "payload_instruction_shape": payload_instruction_shape,
            "payload_instruction_safety": payload_instruction_safety,
            "instruction_semantic_candidate_status": instruction_semantic_candidate_status,
            "latest_oracle_text_status": latest_oracle_text_status,
            "text_replace_witness_support_status": text_replace_witness_support_status,
            "source_change_text_witness_status": source_change_text_witness_status,
        }.items()
        if value
    }


@dataclass(frozen=True)
class NZEffectCandidatePreflightReport:
    work_id: str
    candidate_report: NZCanonicalEffectCandidateReport

    def summary(self) -> dict[str, Any]:
        emitted_rows = tuple(row for row in self.candidate_report.rows if row.status == "candidate_emitted")
        candidate_rows = tuple(row for row in emitted_rows if row.operation is not None)
        source_change_only_rows = tuple(row for row in candidate_rows if _source_change_only_candidate(row))
        target_recovery_rows = tuple(row for row in candidate_rows if _target_recovery_candidate(row))
        replayable_candidate_rows = tuple(
            row
            for row in candidate_rows
            if not _source_change_only_candidate(row) and not _target_recovery_candidate(row)
        )
        missing_operation_rows = tuple(row for row in emitted_rows if row.operation is None)
        explicit_blocked_rows = tuple(row for row in self.candidate_report.rows if row.status != "candidate_emitted")
        blocked_rows = _preflight_blocked_rows(self.candidate_report.rows)
        blocker_counts = Counter(row.blocking_rule_id or "nz_effect_candidate_not_ready" for row in explicit_blocked_rows)
        if missing_operation_rows:
            blocker_counts[NZ_EFFECT_PREFLIGHT_CANDIDATE_OPERATION_MISSING_RULE_ID] += len(missing_operation_rows)
        if source_change_only_rows:
            blocker_counts[NZ_EFFECT_PREFLIGHT_SOURCE_CHANGE_ONLY_CANDIDATES_RULE_ID] += len(source_change_only_rows)
        if target_recovery_rows:
            blocker_counts[NZ_EFFECT_PREFLIGHT_TARGET_RECOVERY_CANDIDATES_RULE_ID] += len(target_recovery_rows)
        status = "ready_for_dry_run_replay" if not blocked_rows and replayable_candidate_rows else "blocked_incomplete_candidate_set"
        if not self.candidate_report.rows:
            status = "blocked_no_candidate_rows"
        elif missing_operation_rows:
            status = "blocked_candidate_operation_missing"
        elif not explicit_blocked_rows and source_change_only_rows and target_recovery_rows:
            status = "blocked_non_replayable_candidates"
        elif not explicit_blocked_rows and source_change_only_rows:
            status = "blocked_source_change_only_candidates"
        elif not explicit_blocked_rows and target_recovery_rows:
            status = "blocked_target_recovery_candidates"
        operations_to_replay = len(replayable_candidate_rows) if status == "ready_for_dry_run_replay" else 0
        blocking_rule_id = ""
        if status == "blocked_incomplete_candidate_set":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_REFUSED_BLOCKED_CANDIDATE_ROWS_RULE_ID
        if status == "blocked_no_candidate_rows":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_NO_CANDIDATE_ROWS_RULE_ID
        if status == "blocked_candidate_operation_missing":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_CANDIDATE_OPERATION_MISSING_RULE_ID
        if status == "blocked_source_change_only_candidates":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_SOURCE_CHANGE_ONLY_CANDIDATES_RULE_ID
        if status == "blocked_target_recovery_candidates":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_TARGET_RECOVERY_CANDIDATES_RULE_ID
        if status == "blocked_non_replayable_candidates":
            blocking_rule_id = NZ_EFFECT_PREFLIGHT_NON_REPLAYABLE_CANDIDATES_RULE_ID
        return {
            "work_id": self.work_id,
            "rows": len(self.candidate_report.rows),
            "candidate_operations": len(candidate_rows),
            "replayable_candidate_operations": len(replayable_candidate_rows),
            "source_change_only_candidate_rows": len(source_change_only_rows),
            "target_recovery_candidate_rows": len(target_recovery_rows),
            "blocked_rows": len(blocked_rows),
            "operations_to_replay": operations_to_replay,
            "preflight_status": status,
            "blocking_rule_counts": dict(sorted(blocker_counts.items())),
            "replay_claims": False,
            "dry_run_only": True,
            "blocking_rule_id": blocking_rule_id,
        }

    def to_jsonable(self, *, summary_only: bool = False, row_limit: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "effect_candidate_preflight",
            "truth_claim": "candidate_set_replay_precondition_check",
            "replay_claims": False,
            "dry_run_only": True,
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        blocked_rows = _preflight_blocked_rows(self.candidate_report.rows)
        rows = blocked_rows if row_limit is None else blocked_rows[:row_limit]
        payload["blocked_rows"] = [_preflight_blocked_row_jsonable(row) for row in rows]
        if row_limit is not None and len(blocked_rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(blocked_rows) - row_limit
        return payload

    def operation_evidence_rows(self) -> tuple[CorpusOperationEvidenceRow, ...]:
        blocked = self.summary()["preflight_status"] != "ready_for_dry_run_replay"
        return tuple(_preflight_operation_evidence_row(self, row, batch_blocked=blocked) for row in self.candidate_report.rows)

    def operations_for_dry_run_replay(self) -> tuple[LegalOperation, ...]:
        if self.summary()["preflight_status"] != "ready_for_dry_run_replay":
            return ()
        return tuple(
            row.operation
            for row in self.candidate_report.rows
            if row.operation is not None and not _source_change_only_candidate(row) and not _target_recovery_candidate(row)
        )

    def finding_evidence_rows(self) -> tuple[CorpusFindingEvidenceRow, ...]:
        summary = self.summary()
        if summary["preflight_status"] == "ready_for_dry_run_replay":
            return ()
        blocked_row_ids = tuple(row.row_id for row in _preflight_blocked_rows(self.candidate_report.rows))
        status = str(summary["preflight_status"])
        if status == "blocked_no_candidate_rows":
            rule_id = NZ_EFFECT_PREFLIGHT_NO_CANDIDATE_ROWS_RULE_ID
            message = "dry-run replay refused because no canonical effect candidate rows were emitted"
            related_row_ids: tuple[str, ...] = ()
        elif status == "blocked_candidate_operation_missing":
            rule_id = NZ_EFFECT_PREFLIGHT_CANDIDATE_OPERATION_MISSING_RULE_ID
            message = "dry-run replay refused because one or more emitted candidates lack LegalOperation payloads"
            related_row_ids = blocked_row_ids
        elif status == "blocked_source_change_only_candidates":
            rule_id = NZ_EFFECT_PREFLIGHT_SOURCE_CHANGE_ONLY_CANDIDATES_RULE_ID
            message = "dry-run replay refused because source-change-only candidates are not replay proof"
            related_row_ids = blocked_row_ids
        elif status == "blocked_target_recovery_candidates":
            rule_id = NZ_EFFECT_PREFLIGHT_TARGET_RECOVERY_CANDIDATES_RULE_ID
            message = "dry-run replay refused because target-recovery candidates are not exact source targets"
            related_row_ids = blocked_row_ids
        elif status == "blocked_non_replayable_candidates":
            rule_id = NZ_EFFECT_PREFLIGHT_NON_REPLAYABLE_CANDIDATES_RULE_ID
            message = "dry-run replay refused because one or more emitted candidates are candidate-only evidence"
            related_row_ids = blocked_row_ids
        else:
            rule_id = NZ_EFFECT_PREFLIGHT_REFUSED_BLOCKED_CANDIDATE_ROWS_RULE_ID
            message = "dry-run replay refused because one or more candidate rows are blocked"
            related_row_ids = blocked_row_ids
        return (
            CorpusFindingEvidenceRow(
                finding_id=f"{self.work_id or 'new_zealand'}:{rule_id}",
                frontend_id="new_zealand",
                family="new_zealand_effect_preflight",
                rule_id=rule_id,
                phase="preflight",
                message=message,
                source_artifact_id=self.work_id or "new_zealand_effect_preflight",
                related_row_ids=related_row_ids,
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record_blocked_preflight",
                evidence={
                    "blocked_rows": summary["blocked_rows"],
                    "candidate_operations": summary["candidate_operations"],
                    "replayable_candidate_operations": summary["replayable_candidate_operations"],
                    "source_change_only_candidate_rows": summary["source_change_only_candidate_rows"],
                    "target_recovery_candidate_rows": summary["target_recovery_candidate_rows"],
                    "operations_to_replay": summary["operations_to_replay"],
                    "blocking_rule_counts": summary["blocking_rule_counts"],
                },
            ),
        )


def _preflight_blocked_rows(
    rows: tuple[NZCanonicalEffectCandidateRow, ...],
) -> tuple[NZCanonicalEffectCandidateRow, ...]:
    return tuple(
        row
        for row in rows
        if row.status != "candidate_emitted"
        or row.operation is None
        or _source_change_only_candidate(row)
        or _target_recovery_candidate(row)
    )


def _preflight_blocked_row_jsonable(row: NZCanonicalEffectCandidateRow) -> dict[str, Any]:
    payload = row.to_jsonable()
    payload["preflight_blocking_rule_id"] = _preflight_row_blocking_rule_id(row)
    payload["preflight_blocking_rule_ids"] = _preflight_row_blocking_rule_ids(row)
    return payload


def _preflight_row_blocking_rule_id(row: NZCanonicalEffectCandidateRow) -> str:
    rule_ids = _preflight_row_blocking_rule_ids(row)
    return rule_ids[0] if rule_ids else "nz_effect_candidate_not_ready"


def _preflight_row_blocking_rule_ids(row: NZCanonicalEffectCandidateRow) -> tuple[str, ...]:
    rule_ids: list[str] = []
    if row.status == "candidate_emitted" and row.operation is None:
        rule_ids.append(NZ_EFFECT_PREFLIGHT_CANDIDATE_OPERATION_MISSING_RULE_ID)
    if _source_change_only_candidate(row):
        rule_ids.append(NZ_EFFECT_PREFLIGHT_SOURCE_CHANGE_ONLY_CANDIDATES_RULE_ID)
    if _target_recovery_candidate(row):
        rule_ids.append(NZ_EFFECT_PREFLIGHT_TARGET_RECOVERY_CANDIDATES_RULE_ID)
    if rule_ids:
        return tuple(rule_ids)
    return (row.blocking_rule_id or "nz_effect_candidate_not_ready",)


def _source_change_only_candidate(row: NZCanonicalEffectCandidateRow) -> bool:
    return (
        row.status == "candidate_emitted"
        and row.operation is not None
        and row.operation.witness_rule_id == NZ_TEXT_REPLACE_SOURCE_CHANGE_CANDIDATE_RULE_ID
    )


def _target_recovery_candidate(row: NZCanonicalEffectCandidateRow) -> bool:
    return (
        row.status == "candidate_emitted"
        and row.operation is not None
        and row.action == str(StructuralAction.TEXT_REPLACE)
        and bool(row.latest_oracle_target_resolution_status)
        and row.latest_oracle_target_resolution_status != "exact_source_path"
    )


def build_effect_candidate_surface(
    operation_surface: NZOperationSurfaceReport,
    payload_surface: NZPayloadSurfaceReport,
    effect_readiness: NZEffectReadinessReport | None = None,
    instruction_workqueue: NZInstructionWorkQueueReport | None = None,
    source_version_date_windows: Mapping[str, NZArchivedVersionDateWindow] | None = None,
    source_change_text_witnesses: Mapping[str, _SourceChangeTextWitness] | None = None,
) -> NZCanonicalEffectCandidateReport:
    readiness = effect_readiness or build_effect_readiness_surface(operation_surface, payload_surface)
    operation_by_row_id = {row.row_id: row for row in operation_surface.rows}
    payload_by_operation_row_id = {row.operation_row_id: row for row in payload_surface.rows}
    instruction_by_operation_row_id = (
        {row.operation_row_id: row for row in instruction_workqueue.rows} if instruction_workqueue is not None else {}
    )
    rows: list[NZCanonicalEffectCandidateRow] = []
    for index, readiness_row in enumerate(readiness.rows, start=1):
        operation_row = operation_by_row_id.get(readiness_row.operation_row_id)
        if (
            operation_row is not None
            and readiness_row.effect_readiness_status == "ready_for_canonical_effect_lowering"
            and readiness_row.canonical_family_candidate == "repeal"
        ):
            payload_row = payload_by_operation_row_id.get(operation_row.row_id)
            corroboration = _repeal_payload_corroboration(operation_row, payload_row)
            if corroboration.blocking_rule_id:
                rows.append(
                    NZCanonicalEffectCandidateRow(
                        row_id=f"nz-effect-candidate-{index}",
                        operation_row_id=readiness_row.operation_row_id,
                        effect_readiness_row_id=readiness_row.row_id,
                        status="blocked",
                        target_address=readiness_row.target_address,
                        blocking_rule_id=corroboration.blocking_rule_id,
                        **_source_witness_fields(operation_row, source_version_date_windows),
                        **_operation_context_fields(readiness_row),
                        **_payload_match_witness_fields(payload_row),
                        payload_role=readiness_row.payload_role,
                        payload_semantics_status=readiness_row.payload_semantics_status,
                        payload_instruction_shape=readiness_row.payload_instruction_shape,
                        payload_instruction_safety=readiness_row.payload_instruction_safety,
                        instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
                        instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
                        instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
                        repeal_payload_corroboration_status=corroboration.status,
                        repeal_payload_corroboration_rule_id=corroboration.rule_id,
                        repeal_payload_cited_targets=corroboration.cited_targets,
                        payload_match_count=readiness_row.payload_match_count,
                        payload_match_kinds=readiness_row.payload_match_kinds,
                        payload_match_headings=readiness_row.payload_match_headings,
                    )
                )
                continue
            operation = _repeal_operation(index, operation_surface.work_id, operation_row)
            rows.append(
                NZCanonicalEffectCandidateRow(
                    row_id=f"nz-effect-candidate-{index}",
                    operation_row_id=operation_row.row_id,
                    effect_readiness_row_id=readiness_row.row_id,
                    status="candidate_emitted",
                    action=str(StructuralAction.REPEAL),
                    target_address=str(operation.target),
                    operation=operation,
                    **_source_witness_fields(operation_row, source_version_date_windows),
                    **_operation_context_fields(readiness_row),
                    **_payload_match_witness_fields(payload_row),
                    payload_role=readiness_row.payload_role,
                    payload_semantics_status=readiness_row.payload_semantics_status,
                    payload_instruction_shape=readiness_row.payload_instruction_shape,
                    payload_instruction_safety=readiness_row.payload_instruction_safety,
                    instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
                    instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
                    instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
                    repeal_payload_corroboration_status=corroboration.status,
                    repeal_payload_corroboration_rule_id=corroboration.rule_id,
                    repeal_payload_cited_targets=corroboration.cited_targets,
                    payload_match_count=readiness_row.payload_match_count,
                    payload_match_kinds=readiness_row.payload_match_kinds,
                    payload_match_headings=readiness_row.payload_match_headings,
                )
            )
            continue
        instruction_row = instruction_by_operation_row_id.get(readiness_row.operation_row_id)
        if operation_row is not None and instruction_row is not None:
            text_candidate = _text_replace_candidate(
                index=index,
                work_id=operation_surface.work_id,
                operation_row=operation_row,
                payload_row=payload_by_operation_row_id.get(operation_row.row_id),
                readiness_row=readiness_row,
                instruction_row=instruction_row,
                source_version_date_windows=source_version_date_windows,
                source_change_text_witnesses=source_change_text_witnesses,
            )
            if text_candidate is not None:
                rows.append(text_candidate)
                continue
        rows.append(
            NZCanonicalEffectCandidateRow(
                row_id=f"nz-effect-candidate-{index}",
                operation_row_id=readiness_row.operation_row_id,
                effect_readiness_row_id=readiness_row.row_id,
                status="blocked",
                target_address=readiness_row.target_address,
                blocking_rule_id=readiness_row.blocking_rule_id or "nz_effect_candidate_not_ready",
                **_source_witness_fields(operation_row, source_version_date_windows),
                **_operation_context_fields(readiness_row),
                **_payload_match_witness_fields(payload_by_operation_row_id.get(readiness_row.operation_row_id)),
                payload_role=readiness_row.payload_role,
                payload_semantics_status=readiness_row.payload_semantics_status,
                payload_instruction_shape=readiness_row.payload_instruction_shape,
                payload_instruction_safety=readiness_row.payload_instruction_safety,
                instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
                instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
                instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
                **_instruction_candidate_fields(instruction_row),
                payload_match_count=readiness_row.payload_match_count,
                payload_match_kinds=readiness_row.payload_match_kinds,
                payload_match_headings=readiness_row.payload_match_headings,
            )
        )
    return NZCanonicalEffectCandidateReport(work_id=operation_surface.work_id, rows=tuple(rows))


def build_archived_work_effect_candidate_surface(db_path: Path, work_id: str) -> NZCanonicalEffectCandidateReport:
    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface
    from lawvm.new_zealand.payload_surface import build_archived_work_payload_surface
    from lawvm.new_zealand.source_tree import parse_archived_work_latest

    target_document = parse_archived_work_latest(db_path, work_id)
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    payload_surface = build_archived_work_payload_surface(db_path, work_id)
    effect_readiness = build_archived_work_effect_readiness_surface(db_path, work_id)
    instruction_workqueue = build_instruction_workqueue(
        operation_surface,
        payload_surface,
        effect_readiness,
        target_document,
    )
    from lawvm.new_zealand.acquisition import open_farchive

    archive = open_farchive(db_path)
    try:
        return build_effect_candidate_surface_with_archived_source_witnesses(
            archive,
            work_id=work_id,
            operation_surface=operation_surface,
            payload_surface=payload_surface,
            effect_readiness=effect_readiness,
            instruction_workqueue=instruction_workqueue,
        )
    finally:
        archive.close()


def build_effect_candidate_surface_with_archived_source_witnesses(
    archive: Any,
    *,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
    payload_surface: NZPayloadSurfaceReport,
    effect_readiness: NZEffectReadinessReport,
    instruction_workqueue: NZInstructionWorkQueueReport,
) -> NZCanonicalEffectCandidateReport:
    source_version_date_windows = _source_version_date_windows_for_archive(
        archive,
        work_id,
        operation_surface,
    )
    source_change_text_witnesses = _source_change_text_witnesses_for_archive(
        archive,
        work_id,
        operation_surface,
        instruction_workqueue,
    )
    return build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
        source_version_date_windows,
        source_change_text_witnesses,
    )


def build_effect_candidate_preflight(
    candidate_report: NZCanonicalEffectCandidateReport,
) -> NZEffectCandidatePreflightReport:
    return NZEffectCandidatePreflightReport(work_id=candidate_report.work_id, candidate_report=candidate_report)


def build_archived_work_effect_candidate_preflight(db_path: Path, work_id: str) -> NZEffectCandidatePreflightReport:
    return build_effect_candidate_preflight(build_archived_work_effect_candidate_surface(db_path, work_id))


def _source_version_date_windows_for_archived_work(
    db_path: Path,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
) -> dict[str, NZArchivedVersionDateWindow]:
    from lawvm.new_zealand.acquisition import open_farchive

    archive = open_farchive(db_path)
    try:
        return _source_version_date_windows_for_archive(archive, work_id, operation_surface)
    finally:
        archive.close()


def _source_version_date_windows_for_archive(
    archive: Any,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
) -> dict[str, NZArchivedVersionDateWindow]:
    dates = tuple(sorted({row.amendment_date_iso for row in operation_surface.rows if row.amendment_date_iso}))
    if not dates:
        return {}
    return {
        date: archived_xml_version_date_window(
            archive,
            work_id=work_id,
            version_date=date,
        )
        for date in dates
    }


def _source_change_text_witnesses_for_archived_work(
    db_path: Path,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
    instruction_workqueue: NZInstructionWorkQueueReport,
) -> dict[str, _SourceChangeTextWitness]:
    from lawvm.new_zealand.acquisition import open_farchive

    archive = open_farchive(db_path)
    try:
        return _source_change_text_witnesses_for_archive(
            archive,
            work_id,
            operation_surface,
            instruction_workqueue,
        )
    finally:
        archive.close()


def _source_change_text_witnesses_for_archive(
    archive: Any,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
    instruction_workqueue: NZInstructionWorkQueueReport,
) -> dict[str, _SourceChangeTextWitness]:
    operation_by_row_id = {row.row_id: row for row in operation_surface.rows}
    relevant_rows = tuple(
        row
        for row in instruction_workqueue.rows
        if row.instruction_subfamily in _TEXT_REPLACE_CANDIDATE_SUBFAMILIES
    )
    if not relevant_rows:
        return {}
    parsed_documents: dict[tuple[str, str], Any] = {}
    witnesses: dict[str, _SourceChangeTextWitness] = {}
    for instruction_row in relevant_rows:
        operation_row = operation_by_row_id.get(instruction_row.operation_row_id)
        if operation_row is None:
            continue
        witnesses[instruction_row.operation_row_id] = _source_change_text_witness(
            archive=archive,
            parsed_documents=parsed_documents,
            work_id=work_id,
            operation_row=operation_row,
            instruction_row=instruction_row,
        )
    return witnesses


def _source_change_text_witness(
    *,
    archive: Any,
    parsed_documents: dict[tuple[str, str], Any],
    work_id: str,
    operation_row: NZOperationWitnessRow,
    instruction_row: NZInstructionWorkQueueRow,
) -> _SourceChangeTextWitness:
    if not operation_row.amendment_date_iso:
        return _SourceChangeTextWitness(
            status="missing_amendment_date_iso",
            rule_id="nz_source_change_text_missing_amendment_date_iso",
        )
    if not instruction_row.latest_oracle_target_source_path:
        return _SourceChangeTextWitness(
            status="missing_target_source_path",
            rule_id="nz_source_change_text_target_source_path_missing",
            requested_date=operation_row.amendment_date_iso,
        )
    window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=operation_row.amendment_date_iso,
    )
    if window.before is None or window.on_or_after is None:
        return _SourceChangeTextWitness(
            status="missing_change_window_witness",
            rule_id="nz_source_change_text_change_window_incomplete",
            change_window_truth_claim=window.truth_claim,
            requested_date=window.requested_version_date,
            before_version_id=window.before.version_id if window.before else "",
            before_xml_locator=window.before.xml_locator if window.before else "",
            on_or_after_version_id=window.on_or_after.version_id if window.on_or_after else "",
            on_or_after_xml_locator=window.on_or_after.xml_locator if window.on_or_after else "",
            target_source_path=instruction_row.latest_oracle_target_source_path,
        )
    before_document = _parsed_archived_document(
        archive,
        parsed_documents,
        version_id=window.before.version_id,
        xml_locator=window.before.xml_locator,
    )
    after_document = _parsed_archived_document(
        archive,
        parsed_documents,
        version_id=window.on_or_after.version_id,
        xml_locator=window.on_or_after.xml_locator,
    )
    if before_document is None or after_document is None:
        return _SourceChangeTextWitness(
            status="missing_change_window_xml",
            rule_id="nz_source_change_text_change_window_xml_missing",
            change_window_truth_claim=window.truth_claim,
            requested_date=window.requested_version_date,
            before_version_id=window.before.version_id,
            before_xml_locator=window.before.xml_locator,
            on_or_after_version_id=window.on_or_after.version_id,
            on_or_after_xml_locator=window.on_or_after.xml_locator,
            target_source_path=instruction_row.latest_oracle_target_source_path,
        )
    before_node = _single_node_by_path(before_document, instruction_row.latest_oracle_target_source_path)
    after_node = _single_node_by_path(after_document, instruction_row.latest_oracle_target_source_path)
    if before_node is None or after_node is None:
        return _SourceChangeTextWitness(
            status="target_node_missing_in_change_window",
            rule_id="nz_source_change_text_target_node_missing_in_change_window",
            change_window_truth_claim=window.truth_claim,
            requested_date=window.requested_version_date,
            before_version_id=window.before.version_id,
            before_xml_locator=window.before.xml_locator,
            on_or_after_version_id=window.on_or_after.version_id,
            on_or_after_xml_locator=window.on_or_after.xml_locator,
            target_source_path=instruction_row.latest_oracle_target_source_path,
        )
    before_old = normalized_nz_inline_occurrence_count(before_node.text, instruction_row.old_text)
    before_new = normalized_nz_inline_occurrence_count(before_node.text, instruction_row.new_text)
    after_old = normalized_nz_inline_occurrence_count(after_node.text, instruction_row.old_text)
    after_new = normalized_nz_inline_occurrence_count(after_node.text, instruction_row.new_text)
    status = _source_change_text_status(
        before_old=before_old,
        before_new=before_new,
        after_old=after_old,
        after_new=after_new,
        scope=instruction_row.text_substitution_scope,
    )
    return _SourceChangeTextWitness(
        status=status,
        rule_id=f"nz_source_change_text_{status}",
        change_window_truth_claim=window.truth_claim,
        requested_date=window.requested_version_date,
        before_version_id=window.before.version_id,
        before_xml_locator=window.before.xml_locator,
        on_or_after_version_id=window.on_or_after.version_id,
        on_or_after_xml_locator=window.on_or_after.xml_locator,
        target_source_path=instruction_row.latest_oracle_target_source_path,
        before_old_text_occurrences=before_old,
        before_new_text_occurrences=before_new,
        on_or_after_old_text_occurrences=after_old,
        on_or_after_new_text_occurrences=after_new,
    )


def _parsed_archived_document(
    archive: Any,
    parsed_documents: dict[tuple[str, str], Any],
    *,
    version_id: str,
    xml_locator: str,
) -> Any | None:
    key = (version_id, xml_locator)
    if key in parsed_documents:
        return parsed_documents[key]
    data = archive.get(xml_locator)
    if data is None:
        return None
    document = parse_nz_source_document(data, xml_locator=xml_locator, version_id=version_id)
    parsed_documents[key] = document
    return document


def _single_node_by_path(document: Any, source_path: tuple[str, ...]) -> Any | None:
    matches = tuple(node for node in document.nodes if node.path == source_path)
    return matches[0] if len(matches) == 1 else None


def _source_change_text_status(
    *,
    before_old: int,
    before_new: int,
    after_old: int,
    after_new: int,
    scope: str,
) -> str:
    if before_old == 1 and before_new == 0 and after_old == 0 and after_new == 1:
        return "observed_single_replacement"
    if (
        scope == "inline_text_each_place"
        and before_old > 0
        and before_old == after_new
        and before_new == 0
        and after_old == 0
    ):
        return "observed_each_place_replacement"
    if before_old == 0 and before_new == 0 and after_old == 0 and after_new == 0:
        return "neither_old_nor_new_observed"
    if before_old > 0 and after_new > 0:
        return "partial_text_change_observed"
    return "text_change_not_observed"


def _repeal_payload_corroboration(
    operation_row: NZOperationWitnessRow,
    payload_row: NZPayloadWitnessRow | None,
) -> _RepealPayloadCorroboration:
    if payload_row is None or payload_row.payload_instruction_shape != "direct_repeal_replace_instruction":
        return _RepealPayloadCorroboration(
            status="not_required_non_direct_repeal_payload",
            rule_id=NZ_REPEAL_PAYLOAD_NOT_DIRECT_RULE_ID,
        )
    target_label = _section_label_from_target_address(operation_row.target_address_candidate.address)
    cited_targets = _repeal_payload_cited_section_labels(payload_row)
    if not target_label or not cited_targets:
        return _RepealPayloadCorroboration(
            status="blocked_direct_repeal_payload_target_unparsed",
            rule_id=NZ_REPEAL_PAYLOAD_UNPARSED_BLOCKED_RULE_ID,
            cited_targets=cited_targets,
            blocking_rule_id=NZ_REPEAL_PAYLOAD_UNPARSED_BLOCKED_RULE_ID,
        )
    if target_label not in cited_targets:
        return _RepealPayloadCorroboration(
            status="blocked_direct_repeal_payload_target_mismatch",
            rule_id=NZ_REPEAL_PAYLOAD_MISMATCH_BLOCKED_RULE_ID,
            cited_targets=cited_targets,
            blocking_rule_id=NZ_REPEAL_PAYLOAD_MISMATCH_BLOCKED_RULE_ID,
        )
    return _RepealPayloadCorroboration(
        status="corroborated_direct_repeal_payload_target",
        rule_id=NZ_REPEAL_PAYLOAD_CORROBORATED_RULE_ID,
        cited_targets=cited_targets,
    )


def _repeal_payload_cited_section_labels(payload_row: NZPayloadWitnessRow) -> tuple[str, ...]:
    labels: set[str] = set()
    for match in payload_row.matches:
        text = " ".join((match.heading, match.text))
        for start, end in _section_ranges(text):
            labels.update(_expand_section_range(start, end))
        labels.update(_section_list_labels(text))
    return tuple(sorted(labels, key=_section_label_sort_key))


def _section_ranges(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (match.group("start").upper(), match.group("end").upper())
        for match in re.finditer(
            r"\bsections?\s+(?P<start>\d+[A-Za-z]*)\s+to\s+(?P<end>\d+[A-Za-z]*)\b",
            text,
            re.IGNORECASE,
        )
    )


def _section_list_labels(text: str) -> tuple[str, ...]:
    labels: set[str] = set()
    for match in re.finditer(
        r"\bsections?\s+(?P<body>\d+[A-Za-z]*(?:\s*(?:,|and)\s*\d+[A-Za-z]*)*)",
        text,
        re.IGNORECASE,
    ):
        labels.update(label.upper() for label in re.findall(r"\d+[A-Za-z]*", match.group("body")))
    return tuple(labels)


def _expand_section_range(start: str, end: str) -> tuple[str, ...]:
    start_match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z]*)", start)
    end_match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z]*)", end)
    if start_match is None or end_match is None:
        return (start, end)
    if start_match.group("suffix") or end_match.group("suffix"):
        return (start, end)
    start_number = int(start_match.group("number"))
    end_number = int(end_match.group("number"))
    if end_number < start_number or end_number - start_number > 100:
        return (start, end)
    return tuple(str(number) for number in range(start_number, end_number + 1))


def _section_label_from_target_address(target_address: str) -> str:
    for part in target_address.split("/"):
        if part.startswith("section:"):
            return part.removeprefix("section:").upper()
    return ""


def _section_label_sort_key(label: str) -> tuple[int, str]:
    match = re.fullmatch(r"(?P<number>\d+)(?P<suffix>[A-Z]*)", label)
    if match is None:
        return (10**9, label)
    return (int(match.group("number")), match.group("suffix"))


def _repeal_operation(sequence: int, work_id: str, row: NZOperationWitnessRow) -> LegalOperation:
    target = _legal_address(row)
    return LegalOperation(
        op_id=f"nz:{work_id}:{row.row_id}:repeal",
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=target,
        payload=None,
        source=OperationSource(
            statute_id=row.amending_work_id,
            title=row.amending_legislation,
            effective=row.amendment_date,
            raw_text=row.witness_text,
        ),
        provenance_tags=(
            "new_zealand",
            "history_note",
            "candidate_only",
            "not_replayed",
        ),
        witness_rule_id="nz_repeal_candidate_from_history_note_payload_witness",
    )


def _text_replace_candidate(
    *,
    index: int,
    work_id: str,
    operation_row: NZOperationWitnessRow,
    payload_row: NZPayloadWitnessRow | None,
    readiness_row: Any,
    instruction_row: NZInstructionWorkQueueRow,
    source_version_date_windows: Mapping[str, NZArchivedVersionDateWindow] | None = None,
    source_change_text_witnesses: Mapping[str, _SourceChangeTextWitness] | None = None,
) -> NZCanonicalEffectCandidateRow | None:
    if instruction_row.instruction_subfamily not in _TEXT_REPLACE_CANDIDATE_SUBFAMILIES:
        return None
    instruction_fields = _instruction_candidate_fields(instruction_row)
    witness_is_usable = (
        instruction_row.latest_oracle_text_status in _TEXT_REPLACE_ALLOWED_ORACLE_TEXT_STATUSES
        and instruction_row.latest_oracle_target_resolution_status in _TEXT_REPLACE_ALLOWED_ORACLE_TARGET_RESOLUTION_STATUSES
    )
    source_change_witness = (
        source_change_text_witnesses.get(operation_row.row_id)
        if source_change_text_witnesses is not None
        else None
    )
    source_change_can_emit = (
        source_change_witness is not None
        and source_change_witness.status in _TEXT_REPLACE_OBSERVED_SOURCE_CHANGE_STATUSES
        and _source_change_witness_matches_target(instruction_row, source_change_witness)
        and instruction_row.latest_oracle_target_resolution_status
        in _TEXT_REPLACE_SOURCE_CHANGE_CANDIDATE_TARGET_RESOLUTION_STATUSES
    )
    support_fields = _text_replace_witness_support_fields(
        instruction_row,
        witness_is_usable=witness_is_usable,
        source_change_text_witnesses=source_change_text_witnesses,
    )
    if not witness_is_usable and not source_change_can_emit:
        return NZCanonicalEffectCandidateRow(
            row_id=f"nz-effect-candidate-{index}",
            operation_row_id=readiness_row.operation_row_id,
            effect_readiness_row_id=readiness_row.row_id,
            status="blocked",
            target_address=readiness_row.target_address,
            blocking_rule_id=NZ_TEXT_REPLACE_LATEST_ORACLE_WITNESS_BLOCKED_RULE_ID,
            **_source_witness_fields(operation_row, source_version_date_windows),
            **_operation_context_fields(readiness_row),
            **_payload_match_witness_fields(payload_row),
            payload_role=readiness_row.payload_role,
            payload_semantics_status=readiness_row.payload_semantics_status,
            payload_instruction_shape=readiness_row.payload_instruction_shape,
            payload_instruction_safety=readiness_row.payload_instruction_safety,
            instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
            instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
            instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
            **instruction_fields,
            **support_fields,
            **_source_change_text_witness_fields(operation_row, source_change_text_witnesses),
            payload_match_count=readiness_row.payload_match_count,
            payload_match_kinds=readiness_row.payload_match_kinds,
            payload_match_headings=readiness_row.payload_match_headings,
        )
    if witness_is_usable:
        witness_rule_id = NZ_TEXT_REPLACE_CANDIDATE_RULE_ID
        witness_provenance_tag = "latest_oracle_text_witness"
    else:
        witness_rule_id = NZ_TEXT_REPLACE_SOURCE_CHANGE_CANDIDATE_RULE_ID
        witness_provenance_tag = "source_change_text_witness"
    operation = _text_replace_operation(
        index,
        work_id,
        operation_row,
        instruction_row,
        witness_rule_id=witness_rule_id,
        witness_provenance_tag=witness_provenance_tag,
    )
    return NZCanonicalEffectCandidateRow(
        row_id=f"nz-effect-candidate-{index}",
        operation_row_id=operation_row.row_id,
        effect_readiness_row_id=readiness_row.row_id,
        status="candidate_emitted",
        action=str(StructuralAction.TEXT_REPLACE),
        target_address=str(operation.target),
        operation=operation,
        **_source_witness_fields(operation_row, source_version_date_windows),
        **_operation_context_fields(readiness_row),
        **_payload_match_witness_fields(payload_row),
        payload_role=readiness_row.payload_role,
        payload_semantics_status=readiness_row.payload_semantics_status,
        payload_instruction_shape=readiness_row.payload_instruction_shape,
        payload_instruction_safety=readiness_row.payload_instruction_safety,
        instruction_semantic_candidate_status=readiness_row.instruction_semantic_candidate_status,
        instruction_semantic_candidate_family=readiness_row.instruction_semantic_candidate_family,
        instruction_semantic_rule_id=readiness_row.instruction_semantic_rule_id,
        **instruction_fields,
        **support_fields,
        **_source_change_text_witness_fields(operation_row, source_change_text_witnesses),
        payload_match_count=readiness_row.payload_match_count,
        payload_match_kinds=readiness_row.payload_match_kinds,
        payload_match_headings=readiness_row.payload_match_headings,
    )


def _text_replace_operation(
    sequence: int,
    work_id: str,
    operation_row: NZOperationWitnessRow,
    instruction_row: NZInstructionWorkQueueRow,
    *,
    witness_rule_id: str,
    witness_provenance_tag: str,
) -> LegalOperation:
    target = _legal_address(operation_row)
    return LegalOperation(
        op_id=f"nz:{work_id}:{operation_row.row_id}:text_replace",
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        payload=None,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(
                match_text=instruction_row.old_text,
                occurrence=_text_selector_occurrence(instruction_row),
            ),
            replacement=instruction_row.new_text,
        ),
        source=OperationSource(
            statute_id=operation_row.amending_work_id,
            title=operation_row.amending_legislation,
            effective=operation_row.amendment_date,
            raw_text=operation_row.witness_text,
        ),
        provenance_tags=(
            "new_zealand",
            "history_note",
            "instruction_workqueue",
            witness_provenance_tag,
            "candidate_only",
            "not_replayed",
        ),
        witness_rule_id=witness_rule_id,
    )


def _text_selector_occurrence(row: NZInstructionWorkQueueRow) -> int:
    if row.text_substitution_scope == "inline_text_each_place":
        return 0
    return 1


def _instruction_candidate_fields(row: NZInstructionWorkQueueRow | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "instruction_workqueue_row_id": row.row_id,
        "instruction_subfamily_status": row.instruction_subfamily_status,
        "instruction_subfamily": row.instruction_subfamily,
        "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
        "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
        "payload_structural_subfamily": row.payload_structural_subfamily,
        "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
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
    }


def _text_replace_witness_support_fields(
    row: NZInstructionWorkQueueRow,
    *,
    witness_is_usable: bool,
    source_change_text_witnesses: Mapping[str, _SourceChangeTextWitness] | None,
) -> dict[str, Any]:
    witness = source_change_text_witnesses.get(row.operation_row_id) if source_change_text_witnesses is not None else None
    source_change_observed_status = witness is not None and witness.status in _TEXT_REPLACE_OBSERVED_SOURCE_CHANGE_STATUSES
    source_change_target_mismatch = (
        source_change_observed_status and not _source_change_witness_matches_target(row, witness)
    )
    source_change_observed = (
        source_change_observed_status
        and _source_change_witness_matches_target(row, witness)
    )
    if witness_is_usable and source_change_observed:
        status = "latest_oracle_and_source_change_observed"
    elif witness_is_usable and witness is None:
        status = "latest_oracle_support_source_change_not_computed"
    elif witness_is_usable:
        status = "latest_oracle_support_source_change_not_observed"
    elif source_change_observed:
        status = "source_change_observed_latest_oracle_unavailable"
    elif source_change_target_mismatch:
        status = "source_change_observed_target_mismatch"
    else:
        status = "no_text_replace_witness_support"
    return {
        "text_replace_witness_support_status": status,
        "text_replace_witness_support_rule_id": f"nz_text_replace_witness_support_{status}",
        "text_replace_witness_support_truth_claim": "text_replace_witness_support_not_replay_proof",
    }


def _source_change_witness_matches_target(
    row: NZInstructionWorkQueueRow,
    witness: _SourceChangeTextWitness,
) -> bool:
    return bool(row.latest_oracle_target_source_path) and witness.target_source_path == row.latest_oracle_target_source_path


def _operation_context_fields(row: Any) -> dict[str, Any]:
    return {
        "operation_family": row.operation_family,
        "operation_lowering_readiness_status": row.operation_lowering_readiness_status,
        "operation_target_surface_status": row.operation_target_surface_status,
        "operation_target_hint_status": row.operation_target_hint_status,
        "operation_target_address_status": row.operation_target_address_status,
        "operation_target_blocking_rule_id": row.operation_target_blocking_rule_id,
        "operation_dependency_status": row.operation_dependency_status,
    }


def _source_witness_fields(
    row: NZOperationWitnessRow | None,
    source_version_date_windows: Mapping[str, NZArchivedVersionDateWindow] | None = None,
) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "source_path": row.source_path,
        "source_xml_id": row.source_xml_id,
        "source_xml_path": row.source_xml_path,
        "source_zone": row.source_zone,
        "source_kind": row.source_kind,
        "amended_provision": row.amended_provision,
        "operation_text": row.operation_text,
        "amendment_date": row.amendment_date,
        "amendment_date_iso": row.amendment_date_iso,
        **_source_version_date_window_fields(row, source_version_date_windows),
        "amending_work_id": row.amending_work_id,
        "amending_legislation": row.amending_legislation,
        "amending_provisions": row.amending_provisions,
        "amending_provision_hrefs": row.amending_provision_hrefs,
        "witness_text": row.witness_text,
    }


def _source_version_date_window_fields(
    row: NZOperationWitnessRow,
    source_version_date_windows: Mapping[str, NZArchivedVersionDateWindow] | None,
) -> dict[str, Any]:
    if source_version_date_windows is None:
        return {}
    if not row.amendment_date_iso:
        return {
            "source_version_date_window_status": "missing_amendment_date_iso",
            "source_version_date_window_rule_id": "nz_source_version_date_window_missing_amendment_date_iso",
        }
    window = source_version_date_windows.get(row.amendment_date_iso)
    if window is None:
        return {
            "source_version_date_window_status": "missing_source_version_date_window",
            "source_version_date_window_rule_id": "nz_source_version_date_window_not_computed",
            "source_version_date_window_requested_date": row.amendment_date_iso,
        }
    status = "source_version_date_window_available"
    if window.on_or_before is None and window.on_or_after is None:
        status = "source_version_date_window_no_archived_xml_witnesses"
    return {
        "source_version_date_window_status": status,
        "source_version_date_window_rule_id": window.rule_id,
        "source_version_date_window_truth_claim": window.truth_claim,
        "source_version_date_window_requested_date": window.requested_version_date,
        "source_version_date_window": source_version_date_window_diagnostic_detail(
            window,
            witness_detail=_archived_version_detail,
        ),
        "source_version_on_or_before_version_id": (
            window.on_or_before.version_id if window.on_or_before else ""
        ),
        "source_version_on_or_before_xml_locator": (
            window.on_or_before.xml_locator if window.on_or_before else ""
        ),
        "source_version_on_or_before_date": (
            window.on_or_before.version_date if window.on_or_before else ""
        ),
        "source_version_on_or_after_version_id": window.on_or_after.version_id if window.on_or_after else "",
        "source_version_on_or_after_xml_locator": (
            window.on_or_after.xml_locator if window.on_or_after else ""
        ),
        "source_version_on_or_after_date": window.on_or_after.version_date if window.on_or_after else "",
    }


def _archived_version_detail(version: NZArchivedVersion) -> dict[str, str]:
    return {
        "version_id": version.version_id,
        "xml_locator": version.xml_locator,
        "version_date": version.version_date,
    }


def _source_change_text_witness_fields(
    row: NZOperationWitnessRow,
    source_change_text_witnesses: Mapping[str, _SourceChangeTextWitness] | None,
) -> dict[str, Any]:
    if source_change_text_witnesses is None:
        return {}
    witness = source_change_text_witnesses.get(row.row_id)
    if witness is None:
        return {
            "source_change_text_witness_status": "missing_source_change_text_witness",
            "source_change_text_witness_rule_id": "nz_source_change_text_witness_not_computed",
            "source_change_text_witness_requested_date": row.amendment_date_iso,
        }
    return {
        "source_change_text_witness_status": witness.status,
        "source_change_text_witness_rule_id": witness.rule_id,
        "source_change_text_witness_truth_claim": witness.truth_claim,
        "source_change_text_change_window_truth_claim": witness.change_window_truth_claim,
        "source_change_text_witness_requested_date": witness.requested_date,
        "source_change_text_before_version_id": witness.before_version_id,
        "source_change_text_before_xml_locator": witness.before_xml_locator,
        "source_change_text_on_or_after_version_id": witness.on_or_after_version_id,
        "source_change_text_on_or_after_xml_locator": witness.on_or_after_xml_locator,
        "source_change_text_target_source_path": witness.target_source_path,
        "source_change_text_before_old_occurrences": witness.before_old_text_occurrences,
        "source_change_text_before_new_occurrences": witness.before_new_text_occurrences,
        "source_change_text_on_or_after_old_occurrences": witness.on_or_after_old_text_occurrences,
        "source_change_text_on_or_after_new_occurrences": witness.on_or_after_new_text_occurrences,
    }


def _payload_match_witness_fields(row: NZPayloadWitnessRow | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "payload_match_xml_ids": tuple(match.xml_id for match in row.matches),
        "payload_match_paths": tuple(match.path for match in row.matches),
        "payload_match_labels": tuple(match.label for match in row.matches),
        "payload_match_texts": tuple(match.text for match in row.matches),
    }


def _operation_context_detail(row: NZCanonicalEffectCandidateRow) -> dict[str, Any]:
    return {
        "operation_family": row.operation_family,
        "operation_lowering_readiness_status": row.operation_lowering_readiness_status,
        "operation_target_surface_status": row.operation_target_surface_status,
        "operation_target_hint_status": row.operation_target_hint_status,
        "operation_target_address_status": row.operation_target_address_status,
        "operation_target_blocking_rule_id": row.operation_target_blocking_rule_id,
        "operation_dependency_status": row.operation_dependency_status,
    }


def _source_witness_detail(row: NZCanonicalEffectCandidateRow) -> dict[str, Any]:
    return {
        "source_path": row.source_path,
        "source_xml_id": row.source_xml_id,
        "source_xml_path": row.source_xml_path,
        "source_zone": row.source_zone,
        "source_kind": row.source_kind,
        "amended_provision": row.amended_provision,
        "operation_text": row.operation_text,
        "amendment_date": row.amendment_date,
        "amendment_date_iso": row.amendment_date_iso,
        "source_version_date_window_status": row.source_version_date_window_status,
        "source_version_date_window_rule_id": row.source_version_date_window_rule_id,
        "source_version_date_window_truth_claim": row.source_version_date_window_truth_claim,
        "source_version_date_window_requested_date": row.source_version_date_window_requested_date,
        "source_version_date_window": dict(row.source_version_date_window),
        "source_version_on_or_before_version_id": row.source_version_on_or_before_version_id,
        "source_version_on_or_before_xml_locator": row.source_version_on_or_before_xml_locator,
        "source_version_on_or_before_date": row.source_version_on_or_before_date,
        "source_version_on_or_after_version_id": row.source_version_on_or_after_version_id,
        "source_version_on_or_after_xml_locator": row.source_version_on_or_after_xml_locator,
        "source_version_on_or_after_date": row.source_version_on_or_after_date,
        "text_replace_witness_support_status": row.text_replace_witness_support_status,
        "text_replace_witness_support_rule_id": row.text_replace_witness_support_rule_id,
        "text_replace_witness_support_truth_claim": row.text_replace_witness_support_truth_claim,
        "source_change_text_witness_status": row.source_change_text_witness_status,
        "source_change_text_witness_rule_id": row.source_change_text_witness_rule_id,
        "source_change_text_witness_truth_claim": row.source_change_text_witness_truth_claim,
        "source_change_text_change_window_truth_claim": row.source_change_text_change_window_truth_claim,
        "source_change_text_witness_requested_date": row.source_change_text_witness_requested_date,
        "source_change_text_before_version_id": row.source_change_text_before_version_id,
        "source_change_text_before_xml_locator": row.source_change_text_before_xml_locator,
        "source_change_text_on_or_after_version_id": row.source_change_text_on_or_after_version_id,
        "source_change_text_on_or_after_xml_locator": row.source_change_text_on_or_after_xml_locator,
        "source_change_text_target_source_path": row.source_change_text_target_source_path,
        "source_change_text_before_old_occurrences": row.source_change_text_before_old_occurrences,
        "source_change_text_before_new_occurrences": row.source_change_text_before_new_occurrences,
        "source_change_text_on_or_after_old_occurrences": row.source_change_text_on_or_after_old_occurrences,
        "source_change_text_on_or_after_new_occurrences": row.source_change_text_on_or_after_new_occurrences,
        "amending_work_id": row.amending_work_id,
        "amending_legislation": row.amending_legislation,
        "amending_provisions": row.amending_provisions,
        "amending_provision_hrefs": row.amending_provision_hrefs,
        "witness_text": row.witness_text,
    }


def _source_locator(row: NZCanonicalEffectCandidateRow) -> str:
    return row.source_xml_path or "/".join(row.source_path)


def _legal_address(row: NZOperationWitnessRow) -> LegalAddress:
    path = tuple((str(kind), str(label)) for kind, label in row.target_address_candidate.path)
    special = FacetKind(row.target_address_candidate.special) if row.target_address_candidate.special else None
    return LegalAddress(path=path, special=special)


def _operation_jsonable(operation: LegalOperation | None) -> dict[str, Any] | None:
    if operation is None:
        return None
    text_patch = None
    if operation.text_patch is not None:
        text_patch = {
            "kind": str(operation.text_patch.kind.value),
            "selector": {
                "match_text": operation.text_patch.selector.match_text,
                "occurrence": operation.text_patch.selector.occurrence,
            },
            "replacement": operation.text_patch.replacement,
        }
    return {
        "op_id": operation.op_id,
        "sequence": operation.sequence,
        "action": str(operation.action),
        "target": str(operation.target),
        "payload": None,
        "text_patch": text_patch,
        "source": {
            "statute_id": operation.source.statute_id if operation.source is not None else "",
            "title": operation.source.title if operation.source is not None else "",
            "effective": operation.source.effective if operation.source is not None else "",
            "raw_text": operation.source.raw_text if operation.source is not None else "",
        },
        "provenance_tags": list(operation.provenance_tags),
        "witness_rule_id": operation.witness_rule_id or "",
    }


def _candidate_operation_detail(row: NZCanonicalEffectCandidateRow) -> dict[str, Any]:
    if row.operation is None:
        return {
            "candidate_operation_missing": row.status == "candidate_emitted",
            "candidate_witness_rule_id": "",
            "candidate_provenance_tags": (),
        }
    return {
        "candidate_operation_missing": False,
        "candidate_witness_rule_id": _candidate_witness_rule_id(row),
        "candidate_provenance_tags": row.operation.provenance_tags,
    }


def _candidate_witness_rule_id(row: NZCanonicalEffectCandidateRow) -> str:
    if row.operation is None:
        return "__missing_operation__"
    return row.operation.witness_rule_id or "__none__"


def _candidate_evidence_row(
    report: NZCanonicalEffectCandidateReport,
    row: NZCanonicalEffectCandidateRow,
) -> CorpusOperationEvidenceRow:
    source_artifact_id = report.work_id or "new_zealand_effect_candidates"
    if row.status == "candidate_emitted" and row.operation is not None:
        return CorpusOperationEvidenceRow(
            row_id=row.row_id,
            frontend_id="new_zealand",
            source_artifact_id=source_artifact_id,
            source_unit_id=row.operation_row_id,
            source_locator=_source_locator(row),
            effect_family=row.action,
            canonical_family=row.action,
            resolved_target=row.target_address,
            status=CorpusRowStatus.ACCEPTED,
            blocking=False,
            strict_disposition="candidate_only",
            quirks_disposition="candidate_only",
            detail={
                "status": row.status,
                "reason": "candidate canonical effect emitted but not replayed",
                "replay_blocking_rule_id": NZ_EFFECT_CANDIDATE_REPLAY_BLOCKED_RULE_ID,
                "effect_readiness_row_id": row.effect_readiness_row_id,
                **_candidate_operation_detail(row),
                **_source_witness_detail(row),
                **_operation_context_detail(row),
                "payload_role": row.payload_role,
                "payload_semantics_status": row.payload_semantics_status,
                "payload_instruction_shape": row.payload_instruction_shape,
                "payload_instruction_safety": row.payload_instruction_safety,
                "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
                "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
                "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
                "instruction_workqueue_row_id": row.instruction_workqueue_row_id,
                "instruction_subfamily_status": row.instruction_subfamily_status,
                "instruction_subfamily": row.instruction_subfamily,
                "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
                "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
                "payload_structural_subfamily": row.payload_structural_subfamily,
                "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
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
                "repeal_payload_corroboration_status": row.repeal_payload_corroboration_status,
                "repeal_payload_corroboration_rule_id": row.repeal_payload_corroboration_rule_id,
                "repeal_payload_cited_targets": row.repeal_payload_cited_targets,
                "payload_match_count": row.payload_match_count,
                "payload_match_kinds": row.payload_match_kinds,
                "payload_match_headings": row.payload_match_headings,
                "payload_match_xml_ids": row.payload_match_xml_ids,
                "payload_match_paths": row.payload_match_paths,
                "payload_match_labels": row.payload_match_labels,
                "payload_match_texts": row.payload_match_texts,
            },
        )
    if row.status == "candidate_emitted" and row.operation is None:
        return CorpusOperationEvidenceRow(
            row_id=row.row_id,
            frontend_id="new_zealand",
            source_artifact_id=source_artifact_id,
            source_unit_id=row.operation_row_id,
            source_locator=_source_locator(row),
            effect_family=row.action,
            canonical_family=row.action,
            resolved_target=row.target_address,
            status=CorpusRowStatus.UNSUPPORTED,
            blocking=True,
            strict_disposition="block",
            quirks_disposition="record_blocked_candidate",
            detail={
                "status": row.status,
                "reason": NZ_EFFECT_CANDIDATE_OPERATION_MISSING_RULE_ID,
                "effect_readiness_row_id": row.effect_readiness_row_id,
                **_candidate_operation_detail(row),
                **_source_witness_detail(row),
                **_operation_context_detail(row),
                "payload_role": row.payload_role,
                "payload_semantics_status": row.payload_semantics_status,
                "payload_instruction_shape": row.payload_instruction_shape,
                "payload_instruction_safety": row.payload_instruction_safety,
                "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
                "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
                "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
                "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
                "payload_structural_subfamily": row.payload_structural_subfamily,
                "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
                "candidate_status": row.status,
            },
        )
    return CorpusOperationEvidenceRow(
        row_id=row.row_id,
        frontend_id="new_zealand",
        source_artifact_id=source_artifact_id,
        source_unit_id=row.operation_row_id,
        source_locator=_source_locator(row),
        resolved_target=row.target_address,
        status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record_blocked_candidate",
        detail={
            "status": row.status,
            "reason": row.blocking_rule_id or "nz_effect_candidate_not_ready",
            "effect_readiness_row_id": row.effect_readiness_row_id,
            **_source_witness_detail(row),
            **_operation_context_detail(row),
            "payload_role": row.payload_role,
            "payload_semantics_status": row.payload_semantics_status,
            "payload_instruction_shape": row.payload_instruction_shape,
            "payload_instruction_safety": row.payload_instruction_safety,
            "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
            "instruction_workqueue_row_id": row.instruction_workqueue_row_id,
            "instruction_subfamily_status": row.instruction_subfamily_status,
            "instruction_subfamily": row.instruction_subfamily,
            "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
            "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
            "payload_structural_subfamily": row.payload_structural_subfamily,
            "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
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
            "repeal_payload_corroboration_status": row.repeal_payload_corroboration_status,
            "repeal_payload_corroboration_rule_id": row.repeal_payload_corroboration_rule_id,
            "repeal_payload_cited_targets": row.repeal_payload_cited_targets,
            "payload_match_count": row.payload_match_count,
            "payload_match_kinds": row.payload_match_kinds,
            "payload_match_headings": row.payload_match_headings,
            "payload_match_xml_ids": row.payload_match_xml_ids,
            "payload_match_paths": row.payload_match_paths,
            "payload_match_labels": row.payload_match_labels,
            "payload_match_texts": row.payload_match_texts,
        },
    )


def _preflight_operation_evidence_row(
    report: NZEffectCandidatePreflightReport,
    row: NZCanonicalEffectCandidateRow,
    *,
    batch_blocked: bool,
) -> CorpusOperationEvidenceRow:
    source_artifact_id = report.work_id or "new_zealand_effect_preflight"
    preflight_blocking_rule_id = str(report.summary()["blocking_rule_id"])
    if (
        row.status == "candidate_emitted"
        and row.operation is not None
        and not _source_change_only_candidate(row)
        and not _target_recovery_candidate(row)
    ):
        return CorpusOperationEvidenceRow(
            row_id=f"preflight:{row.row_id}",
            frontend_id="new_zealand",
            source_artifact_id=source_artifact_id,
            source_unit_id=row.operation_row_id,
            source_locator=_source_locator(row),
            effect_family=row.action,
            canonical_family=row.action,
            resolved_target=row.target_address,
            status=CorpusRowStatus.ACCEPTED,
            blocking=False,
            strict_disposition="candidate_only_preflight",
            quirks_disposition="candidate_only_preflight",
            detail={
                "status": "ready_candidate",
                "candidate_row_id": row.row_id,
                "effect_readiness_row_id": row.effect_readiness_row_id,
                **_candidate_operation_detail(row),
                **_source_witness_detail(row),
                **_operation_context_detail(row),
                "payload_role": row.payload_role,
                "payload_semantics_status": row.payload_semantics_status,
                "payload_instruction_shape": row.payload_instruction_shape,
                "payload_instruction_safety": row.payload_instruction_safety,
                "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
                "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
                "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
                "instruction_workqueue_row_id": row.instruction_workqueue_row_id,
                "instruction_subfamily_status": row.instruction_subfamily_status,
                "instruction_subfamily": row.instruction_subfamily,
                "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
                "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
                "payload_structural_subfamily": row.payload_structural_subfamily,
                "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
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
                "repeal_payload_corroboration_status": row.repeal_payload_corroboration_status,
                "repeal_payload_corroboration_rule_id": row.repeal_payload_corroboration_rule_id,
                "repeal_payload_cited_targets": row.repeal_payload_cited_targets,
                "payload_match_count": row.payload_match_count,
                "payload_match_kinds": row.payload_match_kinds,
                "payload_match_headings": row.payload_match_headings,
                "payload_match_xml_ids": row.payload_match_xml_ids,
                "payload_match_paths": row.payload_match_paths,
                "payload_match_labels": row.payload_match_labels,
                "payload_match_texts": row.payload_match_texts,
                "batch_blocked": batch_blocked,
                "replay_claims": False,
            },
        )
    row_blocking_rule_ids = _preflight_row_blocking_rule_ids(row)
    reason = row.blocking_rule_id or "nz_effect_candidate_not_ready"
    detail_status = "blocked_batch_refused" if batch_blocked else "blocked_candidate"
    if row.status == "candidate_emitted" and row.operation is None:
        reason = _preflight_row_blocking_rule_id(row)
        detail_status = "blocked_candidate_operation_missing"
    elif _source_change_only_candidate(row) and _target_recovery_candidate(row):
        reason = NZ_EFFECT_PREFLIGHT_NON_REPLAYABLE_CANDIDATES_RULE_ID
        detail_status = "blocked_non_replayable_candidate"
    elif _source_change_only_candidate(row):
        reason = _preflight_row_blocking_rule_id(row)
        detail_status = "blocked_source_change_only_candidate"
    elif _target_recovery_candidate(row):
        reason = _preflight_row_blocking_rule_id(row)
        detail_status = "blocked_target_recovery_candidate"
    return CorpusOperationEvidenceRow(
        row_id=f"preflight:{row.row_id}",
        frontend_id="new_zealand",
        source_artifact_id=source_artifact_id,
        source_unit_id=row.operation_row_id,
        source_locator=_source_locator(row),
        effect_family=row.action,
        canonical_family="",
        resolved_target=row.target_address,
        status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record_blocked_preflight",
        detail={
            "status": detail_status,
            "candidate_row_id": row.row_id,
            "effect_readiness_row_id": row.effect_readiness_row_id,
            **_source_witness_detail(row),
            **_operation_context_detail(row),
            "payload_role": row.payload_role,
            "payload_semantics_status": row.payload_semantics_status,
            "payload_instruction_shape": row.payload_instruction_shape,
            "payload_instruction_safety": row.payload_instruction_safety,
            "instruction_semantic_candidate_status": row.instruction_semantic_candidate_status,
            "instruction_semantic_candidate_family": row.instruction_semantic_candidate_family,
            "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
            "instruction_workqueue_row_id": row.instruction_workqueue_row_id,
            "instruction_subfamily_status": row.instruction_subfamily_status,
            "instruction_subfamily": row.instruction_subfamily,
            "instruction_subfamily_rule_id": row.instruction_subfamily_rule_id,
            "payload_structural_subfamily_status": row.payload_structural_subfamily_status,
            "payload_structural_subfamily": row.payload_structural_subfamily,
            "payload_structural_subfamily_rule_id": row.payload_structural_subfamily_rule_id,
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
            "repeal_payload_corroboration_status": row.repeal_payload_corroboration_status,
            "repeal_payload_corroboration_rule_id": row.repeal_payload_corroboration_rule_id,
            "repeal_payload_cited_targets": row.repeal_payload_cited_targets,
            "payload_match_count": row.payload_match_count,
            "payload_match_kinds": row.payload_match_kinds,
            "payload_match_headings": row.payload_match_headings,
            "payload_match_xml_ids": row.payload_match_xml_ids,
            "payload_match_paths": row.payload_match_paths,
            "payload_match_labels": row.payload_match_labels,
            "payload_match_texts": row.payload_match_texts,
            "reason": reason,
            "batch_blocking_rule_id": preflight_blocking_rule_id if batch_blocked else "",
            "row_blocking_rule_ids": row_blocking_rule_ids,
            "candidate_status": row.status,
            "candidate_blocking_rule_id": row.blocking_rule_id,
            **_candidate_operation_detail(row),
        },
    )


def write_evidence_jsonl(report: NZCanonicalEffectCandidateReport, path: Path) -> int:
    rows = [row.to_dict() for row in report.operation_evidence_rows()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def write_preflight_evidence_jsonl(report: NZEffectCandidatePreflightReport, path: Path) -> int:
    rows = [row.to_dict() for row in report.operation_evidence_rows()]
    rows.extend(row.to_dict() for row in report.finding_evidence_rows())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main(args: Any) -> None:
    report = build_archived_work_effect_candidate_surface(Path(args.db), args.work_id)
    filtered_rows = report.filtered_rows(
        candidate_status=args.candidate_status,
        action=args.action,
        operation_family=args.operation_family,
        blocking_rule=args.blocking_rule,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
        repeal_payload_corroboration_status=args.repeal_payload_corroboration_status,
        operation_lowering_readiness_status=args.operation_lowering_readiness_status,
        operation_target_address_status=args.operation_target_address_status,
        operation_dependency_status=args.operation_dependency_status,
        payload_instruction_shape=args.payload_instruction_shape,
        payload_instruction_safety=args.payload_instruction_safety,
        instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
        latest_oracle_text_status=args.latest_oracle_text_status,
        text_replace_witness_support_status=args.text_replace_witness_support_status,
        source_change_text_witness_status=args.source_change_text_witness_status,
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
            candidate_status=args.candidate_status,
            action=args.action,
            operation_family=args.operation_family,
            blocking_rule=args.blocking_rule,
            instruction_subfamily_status=args.instruction_subfamily_status,
            instruction_subfamily=args.instruction_subfamily,
            payload_structural_subfamily_status=args.payload_structural_subfamily_status,
            payload_structural_subfamily=args.payload_structural_subfamily,
            repeal_payload_corroboration_status=args.repeal_payload_corroboration_status,
            operation_lowering_readiness_status=args.operation_lowering_readiness_status,
            operation_target_address_status=args.operation_target_address_status,
            operation_dependency_status=args.operation_dependency_status,
            payload_instruction_shape=args.payload_instruction_shape,
            payload_instruction_safety=args.payload_instruction_safety,
            instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
            latest_oracle_text_status=args.latest_oracle_text_status,
            text_replace_witness_support_status=args.text_replace_witness_support_status,
            source_change_text_witness_status=args.source_change_text_witness_status,
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
    filters = _effect_candidate_jsonable_filters(
        candidate_status=args.candidate_status,
        action=args.action,
        operation_family=args.operation_family,
        blocking_rule=args.blocking_rule,
        instruction_subfamily_status=args.instruction_subfamily_status,
        instruction_subfamily=args.instruction_subfamily,
        payload_structural_subfamily_status=args.payload_structural_subfamily_status,
        payload_structural_subfamily=args.payload_structural_subfamily,
        repeal_payload_corroboration_status=args.repeal_payload_corroboration_status,
        operation_lowering_readiness_status=args.operation_lowering_readiness_status,
        operation_target_address_status=args.operation_target_address_status,
        operation_dependency_status=args.operation_dependency_status,
        payload_instruction_shape=args.payload_instruction_shape,
        payload_instruction_safety=args.payload_instruction_safety,
        instruction_semantic_candidate_status=args.instruction_semantic_candidate_status,
        latest_oracle_text_status=args.latest_oracle_text_status,
        text_replace_witness_support_status=args.text_replace_witness_support_status,
        source_change_text_witness_status=args.source_change_text_witness_status,
    )
    print(
        f"work_id={summary['work_id']} rows={summary['rows']} "
        f"filtered_rows={len(filtered_rows)} filters={filters} "
        f"candidate_status_counts={summary['candidate_status_counts']} "
        f"candidate_operations={summary['candidate_operations']}"
    )
    print(f"replay_blocking_rule_id={summary['replay_blocking_rule_id']}")
    if summary["blocked_operation_family_instruction_subfamily_status_counts"]:
        print(
            "blocked_operation_family_instruction_subfamily_status_counts="
            f"{summary['blocked_operation_family_instruction_subfamily_status_counts']}"
        )
    if args.summary_only:
        return
    for row in filtered_rows[: args.limit]:
        print(
            f"{row.row_id}\t{row.operation_row_id}\t{row.status}\t"
            f"{row.action or '-'}\t{row.target_address or '-'}"
        )
    if len(filtered_rows) > args.limit:
        print(f"... {len(filtered_rows) - args.limit} more")


def preflight_main(args: Any) -> None:
    report = build_archived_work_effect_candidate_preflight(Path(args.db), args.work_id)
    evidence_row_count: int | None = None
    if args.evidence_jsonl:
        evidence_row_count = write_preflight_evidence_jsonl(report, Path(args.evidence_jsonl))
    if args.json:
        payload = report.to_jsonable(summary_only=args.summary_only, row_limit=args.limit)
        if args.evidence_rows and not args.summary_only:
            payload["evidence"] = {
                "operation_rows": [row.to_dict() for row in report.operation_evidence_rows()[: args.limit]],
                "finding_rows": [row.to_dict() for row in report.finding_evidence_rows()],
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
    print(
        f"work_id={summary['work_id']} preflight_status={summary['preflight_status']} "
        f"candidate_operations={summary['candidate_operations']} blocked_rows={summary['blocked_rows']} "
        f"replayable_candidate_operations={summary['replayable_candidate_operations']} "
        f"source_change_only_candidate_rows={summary['source_change_only_candidate_rows']} "
        f"target_recovery_candidate_rows={summary['target_recovery_candidate_rows']} "
        f"operations_to_replay={summary['operations_to_replay']}"
    )
    if summary["blocking_rule_id"]:
        print(f"blocking_rule_id={summary['blocking_rule_id']}")
    if args.summary_only:
        return
    blocked_rows = _preflight_blocked_rows(report.candidate_report.rows)
    for row in blocked_rows[: args.limit]:
        print(f"{row.row_id}\t{row.operation_row_id}\t{_preflight_row_blocking_rule_id(row)}")
    if len(blocked_rows) > args.limit:
        print(f"... {len(blocked_rows) - args.limit} more")
