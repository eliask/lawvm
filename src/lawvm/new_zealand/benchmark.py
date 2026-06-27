"""Archive-first benchmark coverage reports for the New Zealand frontend.

This module measures source readiness for NZ replay work without claiming replay
support. It consumes only archived API/XML artifacts and reports which works can
be source-parsed, dependency-extracted, and compared across consolidated
versions. Replay remains explicitly blocked until amendment semantics are
lowered to canonical effects.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from lxml import etree

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dependencies import ArchiveReader, extract_dependency_report, latest_xml_locator_for_work
from lawvm.new_zealand.effect_candidates import (
    build_effect_candidate_preflight,
    build_effect_candidate_surface_with_archived_source_witnesses,
)
from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand.instruction_workqueue import build_instruction_workqueue
from lawvm.new_zealand.operation_surface import build_operation_surface, classify_operation_family
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.version_diff import diff_source_documents, previous_archived_xml_version_for_work
from lawvm.core.quirks_disposition import QuirksDisposition


NZ_REPLAY_BLOCKED_RULE_ID = "nz_replay_canonical_effects_not_implemented"
NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID = "nz_oracle_agreement_candidate_replay_missing"

NZ_BENCHMARK_SAMPLE_STRATEGIES = ("head", "stride")

# Declaration ladder levels (cumulative; honor the NZ roadmap Phase 8 names).
NZ_DECLARATION_SOURCE_COMPLETE = "source-complete"
NZ_DECLARATION_CANDIDATE_COMPLETE = "candidate-complete"
NZ_DECLARATION_DRY_RUN_COMPLETE = "dry-run-complete"
NZ_DECLARATION_REPLAY_COMPLETE = "replay-complete"
NZ_DECLARATION_JURISDICTION_COMPLETE = "jurisdiction-complete"
# The ordered ladder. The achieved level is the deepest contiguous rung met.
NZ_DECLARATION_LADDER = (
    NZ_DECLARATION_SOURCE_COMPLETE,
    NZ_DECLARATION_CANDIDATE_COMPLETE,
    NZ_DECLARATION_DRY_RUN_COMPLETE,
    NZ_DECLARATION_REPLAY_COMPLETE,
    NZ_DECLARATION_JURISDICTION_COMPLETE,
)
# The level reported when not even the first rung holds.
NZ_DECLARATION_INCOMPLETE = "incomplete"

# Residual families that are NOT typed frontiers: their presence means a residual
# is an unclassified crash / genuine replay bug rather than an accepted, typed
# frontier. These disqualify the jurisdiction-complete rung.
NZ_UNTYPED_RESIDUAL_FAMILIES = frozenset({"replay_bug", "error", "unknown"})


class NZBenchmarkSelectionError(ValueError):
    """Raised when a benchmark work-id selection filter yields no works.

    This is a loud failure on purpose: a filter that matches nothing must not
    silently fall back to the lexicographic head (ancient imperial acts), which
    is exactly the anti-representative sampling this selection layer exists to
    avoid.
    """


@dataclass(frozen=True)
class NZBenchmarkWorkReport:
    work_id: str
    latest_version_id: str = ""
    latest_xml_locator: str = ""
    source_status: str = "missing_xml"
    node_count: int = 0
    history_witness_count: int = 0
    history_operation_counts: Mapping[str, int] | None = None
    operation_witness_rows: int = 0
    target_hint_status_counts: Mapping[str, int] | None = None
    target_hint_kind_counts: Mapping[str, int] | None = None
    target_address_status_counts: Mapping[str, int] | None = None
    amending_provision_href_status_counts: Mapping[str, int] | None = None
    lowering_readiness_status_counts: Mapping[str, int] | None = None
    operation_surface_findings: int = 0
    payload_status_counts: Mapping[str, int] | None = None
    payload_role_counts: Mapping[str, int] | None = None
    payload_semantics_status_counts: Mapping[str, int] | None = None
    payload_instruction_shape_counts: Mapping[str, int] | None = None
    payload_instruction_safety_counts: Mapping[str, int] | None = None
    payload_found: int = 0
    effect_readiness_status_counts: Mapping[str, int] | None = None
    canonical_family_candidate_counts: Mapping[str, int] | None = None
    instruction_semantic_candidate_status_counts: Mapping[str, int] | None = None
    instruction_semantic_candidate_family_counts: Mapping[str, int] | None = None
    instruction_semantic_rule_id_counts: Mapping[str, int] | None = None
    instruction_structural_subfamily_status_counts: Mapping[str, int] | None = None
    instruction_structural_subfamily_counts: Mapping[str, int] | None = None
    ready_for_canonical_effect_lowering: int = 0
    effect_candidate_status_counts: Mapping[str, int] | None = None
    effect_candidate_action_counts: Mapping[str, int] | None = None
    effect_candidate_operation_family_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_rule_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_payload_shape_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_payload_safety_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_target_status_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_instruction_status_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_instruction_subfamily_status_counts: Mapping[str, int] | None = None
    effect_candidate_payload_structural_subfamily_status_counts: Mapping[str, int] | None = None
    effect_candidate_payload_structural_subfamily_counts: Mapping[str, int] | None = None
    effect_candidate_witness_rule_counts: Mapping[str, int] | None = None
    effect_candidate_action_witness_rule_counts: Mapping[str, int] | None = None
    effect_candidate_text_replace_witness_support_status_counts: Mapping[str, int] | None = None
    effect_candidate_action_text_replace_witness_support_status_counts: Mapping[str, int] | None = None
    effect_candidate_action_source_change_text_witness_status_counts: Mapping[str, int] | None = None
    effect_candidate_blocked_operation_family_source_change_text_witness_status_counts: Mapping[str, int] | None = None
    effect_candidate_source_version_date_window_status_counts: Mapping[str, int] | None = None
    effect_candidate_source_change_text_witness_status_counts: Mapping[str, int] | None = None
    effect_candidate_repeal_payload_corroboration_status_counts: Mapping[str, int] | None = None
    effect_candidate_emitted_rows: int = 0
    effect_candidate_operation_missing_rows: int = 0
    effect_candidate_operations: int = 0
    effect_preflight_status: str = ""
    effect_preflight_replayable_candidate_operations: int = 0
    effect_preflight_source_change_only_candidate_rows: int = 0
    effect_preflight_target_recovery_candidate_rows: int = 0
    effect_preflight_operations_to_replay: int = 0
    effect_preflight_blocking_rule_counts: Mapping[str, int] | None = None
    dependency_count: int = 0
    dependency_archived_count: int = 0
    dependency_diagnostics: int = 0
    previous_version_id: str = ""
    previous_xml_locator: str = ""
    snapshot_diff_status: str = "not_requested"
    snapshot_change_count: int = 0
    replay_status: str = "blocked"
    replay_blocking_rule_id: str = NZ_REPLAY_BLOCKED_RULE_ID
    oracle_agreement_status: str = "blocked_no_candidate_replay"
    oracle_agreement_blocking_rule_id: str = NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID
    oracle_agreement_exact_ratio: float | None = None
    oracle_agreement_residual_family_counts: Mapping[str, int] | None = None
    findings: tuple[dict[str, Any], ...] = ()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "latest_version_id": self.latest_version_id,
            "latest_xml_locator": self.latest_xml_locator,
            "source_status": self.source_status,
            "node_count": self.node_count,
            "history_witness_count": self.history_witness_count,
            "history_operation_counts": dict(self.history_operation_counts or {}),
            "operation_witness_rows": self.operation_witness_rows,
            "target_hint_status_counts": dict(self.target_hint_status_counts or {}),
            "target_hint_kind_counts": dict(self.target_hint_kind_counts or {}),
            "target_address_status_counts": dict(self.target_address_status_counts or {}),
            "amending_provision_href_status_counts": dict(self.amending_provision_href_status_counts or {}),
            "lowering_readiness_status_counts": dict(self.lowering_readiness_status_counts or {}),
            "operation_surface_findings": self.operation_surface_findings,
            "payload_status_counts": dict(self.payload_status_counts or {}),
            "payload_role_counts": dict(self.payload_role_counts or {}),
            "payload_semantics_status_counts": dict(self.payload_semantics_status_counts or {}),
            "payload_instruction_shape_counts": dict(self.payload_instruction_shape_counts or {}),
            "payload_instruction_safety_counts": dict(self.payload_instruction_safety_counts or {}),
            "payload_found": self.payload_found,
            "effect_readiness_status_counts": dict(self.effect_readiness_status_counts or {}),
            "canonical_family_candidate_counts": dict(self.canonical_family_candidate_counts or {}),
            "instruction_semantic_candidate_status_counts": dict(
                self.instruction_semantic_candidate_status_counts or {}
            ),
            "instruction_semantic_candidate_family_counts": dict(
                self.instruction_semantic_candidate_family_counts or {}
            ),
            "instruction_semantic_rule_id_counts": dict(self.instruction_semantic_rule_id_counts or {}),
            "instruction_structural_subfamily_status_counts": dict(
                self.instruction_structural_subfamily_status_counts or {}
            ),
            "instruction_structural_subfamily_counts": dict(self.instruction_structural_subfamily_counts or {}),
            "ready_for_canonical_effect_lowering": self.ready_for_canonical_effect_lowering,
            "effect_candidate_status_counts": dict(self.effect_candidate_status_counts or {}),
            "effect_candidate_action_counts": dict(self.effect_candidate_action_counts or {}),
            "effect_candidate_operation_family_counts": dict(self.effect_candidate_operation_family_counts or {}),
            "effect_candidate_blocked_operation_family_counts": dict(
                self.effect_candidate_blocked_operation_family_counts or {}
            ),
            "effect_candidate_blocked_operation_family_rule_counts": dict(
                self.effect_candidate_blocked_operation_family_rule_counts or {}
            ),
            "effect_candidate_blocked_operation_family_payload_shape_counts": dict(
                self.effect_candidate_blocked_operation_family_payload_shape_counts or {}
            ),
            "effect_candidate_blocked_operation_family_payload_safety_counts": dict(
                self.effect_candidate_blocked_operation_family_payload_safety_counts or {}
            ),
            "effect_candidate_blocked_operation_family_target_status_counts": dict(
                self.effect_candidate_blocked_operation_family_target_status_counts or {}
            ),
            "effect_candidate_blocked_operation_family_instruction_status_counts": dict(
                self.effect_candidate_blocked_operation_family_instruction_status_counts or {}
            ),
            "effect_candidate_blocked_operation_family_instruction_subfamily_status_counts": dict(
                self.effect_candidate_blocked_operation_family_instruction_subfamily_status_counts or {}
            ),
            "effect_candidate_payload_structural_subfamily_status_counts": dict(
                self.effect_candidate_payload_structural_subfamily_status_counts or {}
            ),
            "effect_candidate_payload_structural_subfamily_counts": dict(
                self.effect_candidate_payload_structural_subfamily_counts or {}
            ),
            "effect_candidate_witness_rule_counts": dict(self.effect_candidate_witness_rule_counts or {}),
            "effect_candidate_action_witness_rule_counts": dict(self.effect_candidate_action_witness_rule_counts or {}),
            "effect_candidate_text_replace_witness_support_status_counts": dict(
                self.effect_candidate_text_replace_witness_support_status_counts or {}
            ),
            "effect_candidate_action_text_replace_witness_support_status_counts": dict(
                self.effect_candidate_action_text_replace_witness_support_status_counts or {}
            ),
            "effect_candidate_action_source_change_text_witness_status_counts": dict(
                self.effect_candidate_action_source_change_text_witness_status_counts or {}
            ),
            "effect_candidate_blocked_operation_family_source_change_text_witness_status_counts": dict(
                self.effect_candidate_blocked_operation_family_source_change_text_witness_status_counts or {}
            ),
            "effect_candidate_source_version_date_window_status_counts": dict(
                self.effect_candidate_source_version_date_window_status_counts or {}
            ),
            "effect_candidate_source_change_text_witness_status_counts": dict(
                self.effect_candidate_source_change_text_witness_status_counts or {}
            ),
            "effect_candidate_repeal_payload_corroboration_status_counts": dict(
                self.effect_candidate_repeal_payload_corroboration_status_counts or {}
            ),
            "effect_candidate_emitted_rows": self.effect_candidate_emitted_rows,
            "effect_candidate_operation_missing_rows": self.effect_candidate_operation_missing_rows,
            "effect_candidate_operations": self.effect_candidate_operations,
            "effect_preflight_status": self.effect_preflight_status,
            "effect_preflight_replayable_candidate_operations": self.effect_preflight_replayable_candidate_operations,
            "effect_preflight_source_change_only_candidate_rows": self.effect_preflight_source_change_only_candidate_rows,
            "effect_preflight_target_recovery_candidate_rows": self.effect_preflight_target_recovery_candidate_rows,
            "effect_preflight_operations_to_replay": self.effect_preflight_operations_to_replay,
            "effect_preflight_blocking_rule_counts": dict(self.effect_preflight_blocking_rule_counts or {}),
            "dependency_count": self.dependency_count,
            "dependency_archived_count": self.dependency_archived_count,
            "dependency_diagnostics": self.dependency_diagnostics,
            "previous_version_id": self.previous_version_id,
            "previous_xml_locator": self.previous_xml_locator,
            "snapshot_diff_status": self.snapshot_diff_status,
            "snapshot_change_count": self.snapshot_change_count,
            "replay_status": self.replay_status,
            "replay_blocking_rule_id": self.replay_blocking_rule_id,
            "oracle_agreement_status": self.oracle_agreement_status,
            "oracle_agreement_blocking_rule_id": self.oracle_agreement_blocking_rule_id,
            "oracle_agreement_exact_ratio": self.oracle_agreement_exact_ratio,
            "oracle_agreement_residual_family_counts": dict(
                self.oracle_agreement_residual_family_counts or {}
            ),
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class NZBenchmarkReport:
    db_path: str
    work_reports: tuple[NZBenchmarkWorkReport, ...]
    include_diffs: bool
    include_payloads: bool = False
    include_actual_replay: bool = False
    requested_work_ids: tuple[str, ...] = ()
    selected_work_ids: tuple[str, ...] = ()
    available_work_count: int = 0
    max_works: int | None = None

    def summary(self) -> dict[str, Any]:
        source_ready = sum(1 for row in self.work_reports if row.source_status == "parsed")
        dependency_ready = sum(1 for row in self.work_reports if row.dependency_count > 0)
        diff_ready = sum(1 for row in self.work_reports if row.snapshot_diff_status == "diffed")
        blocked_replay = sum(1 for row in self.work_reports if row.replay_status == "blocked")
        blocked_agreement = sum(
            1 for row in self.work_reports if row.oracle_agreement_status == "blocked_no_candidate_replay"
        )
        replay_status_counts = _aggregate_mapping_counts(
            tuple({row.replay_status: 1} if row.replay_status else {} for row in self.work_reports)
        )
        oracle_agreement_status_counts = _aggregate_mapping_counts(
            tuple(
                {row.oracle_agreement_status: 1} if row.oracle_agreement_status else {}
                for row in self.work_reports
            )
        )
        oracle_agreement_residual_family_counts = _aggregate_mapping_counts(
            tuple(row.oracle_agreement_residual_family_counts or {} for row in self.work_reports)
        )
        return {
            "db_path": self.db_path,
            "selection_context": self.selection_context(),
            "works": len(self.work_reports),
            "source_parsed": source_ready,
            "source_missing_or_error": len(self.work_reports) - source_ready,
            "dependency_reports_with_edges": dependency_ready,
            "dependency_edges": sum(row.dependency_count for row in self.work_reports),
            "dependency_edges_archived": sum(row.dependency_archived_count for row in self.work_reports),
            "dependency_diagnostics": sum(row.dependency_diagnostics for row in self.work_reports),
            "history_operation_counts": _aggregate_operation_counts(self.work_reports),
            "operation_witness_rows": sum(row.operation_witness_rows for row in self.work_reports),
            "target_hint_status_counts": _aggregate_mapping_counts(
                tuple(row.target_hint_status_counts or {} for row in self.work_reports)
            ),
            "target_hint_kind_counts": _aggregate_mapping_counts(
                tuple(row.target_hint_kind_counts or {} for row in self.work_reports)
            ),
            "target_address_status_counts": _aggregate_mapping_counts(
                tuple(row.target_address_status_counts or {} for row in self.work_reports)
            ),
            "amending_provision_href_status_counts": _aggregate_mapping_counts(
                tuple(row.amending_provision_href_status_counts or {} for row in self.work_reports)
            ),
            "lowering_readiness_status_counts": _aggregate_mapping_counts(
                tuple(row.lowering_readiness_status_counts or {} for row in self.work_reports)
            ),
            "operation_surface_findings": sum(row.operation_surface_findings for row in self.work_reports),
            "payload_status_counts": _aggregate_mapping_counts(
                tuple(row.payload_status_counts or {} for row in self.work_reports)
            ),
            "payload_role_counts": _aggregate_mapping_counts(
                tuple(row.payload_role_counts or {} for row in self.work_reports)
            ),
            "payload_semantics_status_counts": _aggregate_mapping_counts(
                tuple(row.payload_semantics_status_counts or {} for row in self.work_reports)
            ),
            "payload_instruction_shape_counts": _aggregate_mapping_counts(
                tuple(row.payload_instruction_shape_counts or {} for row in self.work_reports)
            ),
            "payload_instruction_safety_counts": _aggregate_mapping_counts(
                tuple(row.payload_instruction_safety_counts or {} for row in self.work_reports)
            ),
            "payload_found": sum(row.payload_found for row in self.work_reports),
            "effect_readiness_status_counts": _aggregate_mapping_counts(
                tuple(row.effect_readiness_status_counts or {} for row in self.work_reports)
            ),
            "canonical_family_candidate_counts": _aggregate_mapping_counts(
                tuple(row.canonical_family_candidate_counts or {} for row in self.work_reports)
            ),
            "instruction_semantic_candidate_status_counts": _aggregate_mapping_counts(
                tuple(row.instruction_semantic_candidate_status_counts or {} for row in self.work_reports)
            ),
            "instruction_semantic_candidate_family_counts": _aggregate_mapping_counts(
                tuple(row.instruction_semantic_candidate_family_counts or {} for row in self.work_reports)
            ),
            "instruction_semantic_rule_id_counts": _aggregate_mapping_counts(
                tuple(row.instruction_semantic_rule_id_counts or {} for row in self.work_reports)
            ),
            "instruction_structural_subfamily_status_counts": _aggregate_mapping_counts(
                tuple(row.instruction_structural_subfamily_status_counts or {} for row in self.work_reports)
            ),
            "instruction_structural_subfamily_counts": _aggregate_mapping_counts(
                tuple(row.instruction_structural_subfamily_counts or {} for row in self.work_reports)
            ),
            "ready_for_canonical_effect_lowering": sum(
                row.ready_for_canonical_effect_lowering for row in self.work_reports
            ),
            "effect_candidate_status_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_status_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_action_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_action_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_operation_family_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_operation_family_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_blocked_operation_family_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_blocked_operation_family_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_blocked_operation_family_rule_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_blocked_operation_family_rule_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_blocked_operation_family_payload_shape_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_payload_shape_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_blocked_operation_family_payload_safety_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_payload_safety_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_blocked_operation_family_target_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_target_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_blocked_operation_family_instruction_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_instruction_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_blocked_operation_family_instruction_subfamily_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_instruction_subfamily_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_payload_structural_subfamily_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_payload_structural_subfamily_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_payload_structural_subfamily_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_payload_structural_subfamily_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_witness_rule_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_witness_rule_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_action_witness_rule_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_action_witness_rule_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_text_replace_witness_support_status_counts": _aggregate_mapping_counts(
                tuple(row.effect_candidate_text_replace_witness_support_status_counts or {} for row in self.work_reports)
            ),
            "effect_candidate_action_text_replace_witness_support_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_action_text_replace_witness_support_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_action_source_change_text_witness_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_action_source_change_text_witness_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_blocked_operation_family_source_change_text_witness_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_blocked_operation_family_source_change_text_witness_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_source_version_date_window_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_source_version_date_window_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_source_change_text_witness_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_source_change_text_witness_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_repeal_payload_corroboration_status_counts": _aggregate_mapping_counts(
                tuple(
                    row.effect_candidate_repeal_payload_corroboration_status_counts or {}
                    for row in self.work_reports
                )
            ),
            "effect_candidate_emitted_rows": sum(row.effect_candidate_emitted_rows for row in self.work_reports),
            "effect_candidate_operation_missing_rows": sum(
                row.effect_candidate_operation_missing_rows for row in self.work_reports
            ),
            "effect_candidate_operations": sum(row.effect_candidate_operations for row in self.work_reports),
            "effect_preflight_status_counts": _aggregate_mapping_counts(
                tuple({row.effect_preflight_status: 1} if row.effect_preflight_status else {} for row in self.work_reports)
            ),
            "effect_preflight_operations_to_replay": sum(
                row.effect_preflight_operations_to_replay for row in self.work_reports
            ),
            "effect_preflight_replayable_candidate_operations": sum(
                row.effect_preflight_replayable_candidate_operations for row in self.work_reports
            ),
            "effect_preflight_source_change_only_candidate_rows": sum(
                row.effect_preflight_source_change_only_candidate_rows for row in self.work_reports
            ),
            "effect_preflight_target_recovery_candidate_rows": sum(
                row.effect_preflight_target_recovery_candidate_rows for row in self.work_reports
            ),
            "effect_preflight_blocking_rule_counts": _aggregate_mapping_counts(
                tuple(row.effect_preflight_blocking_rule_counts or {} for row in self.work_reports)
            ),
            "snapshot_diffs": diff_ready,
            "snapshot_changed_paths": sum(row.snapshot_change_count for row in self.work_reports),
            "replay_blocked": blocked_replay,
            "replay_blocking_rule_id": NZ_REPLAY_BLOCKED_RULE_ID,
            "oracle_agreement_blocked": blocked_agreement,
            "oracle_agreement_blocking_rule_id": NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID,
            # Actual-replay reality lanes (populated only when include_actual_replay
            # ran the strict replay surface; otherwise the works are not_evaluated).
            "include_actual_replay": self.include_actual_replay,
            "replay_status_counts": replay_status_counts,
            "oracle_agreement_status_counts": oracle_agreement_status_counts,
            # Oracle agreement reported BY typed residual family, not just a number.
            "oracle_agreement_residual_family_counts": oracle_agreement_residual_family_counts,
            "triage_exemplars": {
                "effect_candidate_blocked_operation_family_rule": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_blocked_operation_family_rule_counts or {},
                ),
                "effect_candidate_blocked_operation_family_payload_shape": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_blocked_operation_family_payload_shape_counts or {},
                ),
                "effect_candidate_blocked_operation_family_payload_safety": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_blocked_operation_family_payload_safety_counts or {},
                ),
                "effect_candidate_blocked_operation_family_target_status": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_blocked_operation_family_target_status_counts or {},
                ),
                "effect_candidate_source_change_text_witness_status": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_source_change_text_witness_status_counts or {},
                ),
                "effect_candidate_blocked_operation_family_instruction_subfamily_status": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_candidate_blocked_operation_family_instruction_subfamily_status_counts
                    or {},
                ),
                "effect_preflight_blocking_rule": _triage_exemplars(
                    self.work_reports,
                    lambda row: row.effect_preflight_blocking_rule_counts or {},
                ),
                "effect_preflight_status": _triage_exemplars(
                    self.work_reports,
                    lambda row: {row.effect_preflight_status: 1} if row.effect_preflight_status else {},
                ),
                "ready_candidate_work_ids": [
                    row.work_id
                    for row in self.work_reports
                    if row.effect_preflight_status == "ready_for_dry_run_replay"
                ][:_TRIAGE_EXEMPLAR_LIMIT],
            },
        }

    def declaration(self) -> dict[str, Any]:
        """Compute the Phase 8 declaration level for the declared corpus slice.

        The declaration is a cumulative ladder computed over the lanes already
        present on the per-work reports. It NEVER flattens the lanes: it reports
        each lane's pass/fail rung witness alongside the achieved level, and it
        requires both the payload lanes (``include_payloads``) and the actual
        replay lanes (``include_actual_replay``) to claim anything past
        ``source-complete``. Without them the deeper rungs are reported as
        ``not_evaluated`` (loud), never as silently passed.
        """

        per_work = tuple(_work_declaration_inputs(row) for row in self.work_reports)
        return compute_nz_declaration(
            per_work,
            include_payloads=self.include_payloads,
            include_actual_replay=self.include_actual_replay,
        )

    def selection_context(self) -> dict[str, Any]:
        selected_work_ids = self.selected_work_ids or tuple(row.work_id for row in self.work_reports)
        requested_work_ids = self.requested_work_ids
        selected_sample = selected_work_ids[:_SELECTION_WORK_ID_SAMPLE_LIMIT]
        requested_sample = requested_work_ids[:_SELECTION_WORK_ID_SAMPLE_LIMIT]
        base_count = len(requested_work_ids) if requested_work_ids else self.available_work_count
        return {
            "available_work_count": self.available_work_count,
            "requested_work_count": len(requested_work_ids),
            "requested_work_ids_sample": list(requested_sample),
            "requested_work_ids_omitted": max(len(requested_work_ids) - len(requested_sample), 0),
            "selected_work_count": len(selected_work_ids),
            "selected_work_ids_sample": list(selected_sample),
            "selected_work_ids_omitted": max(len(selected_work_ids) - len(selected_sample), 0),
            "max_works": self.max_works,
            "truncated_by_max_works": self.max_works is not None and len(selected_work_ids) < base_count,
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "nz",
            "report_kind": "benchmark_source_coverage",
            "truth_claim": "source_witness_inventory",
            # The benchmark itself never claims replay; the actual-replay reality
            # lanes (when included) report the strict replay surface's own claim.
            "replay_claims": self.include_actual_replay,
            "selection_context": self.selection_context(),
            "summary": self.summary(),
            "declaration": self.declaration(),
            "works": [row.to_jsonable() for row in self.work_reports],
        }


def build_nz_benchmark_report(
    archive: ArchiveReader,
    *,
    db_path: Path,
    work_ids: tuple[str, ...] = (),
    max_works: int | None = None,
    work_id_prefix: str = "",
    min_version_year: int | None = None,
    sample_strategy: str = "head",
    include_diffs: bool = False,
    include_payloads: bool = False,
    include_actual_replay: bool = False,
) -> NZBenchmarkReport:
    archived_work_ids = tuple(_archived_work_ids(archive))
    requested_work_ids = tuple(dict.fromkeys(work_ids))
    if requested_work_ids:
        # Explicit caller-supplied work ids bypass the representative sampler:
        # the prefix/year/strategy filters only shape the archive-wide default
        # population, never an explicit list. --max-works still truncates.
        selected_work_ids = list(requested_work_ids)
        if max_works is not None:
            selected_work_ids = selected_work_ids[: max(max_works, 0)]
    else:
        selected_work_ids = list(
            select_benchmark_work_ids(
                archive,
                archived_work_ids=archived_work_ids,
                work_id_prefix=work_id_prefix,
                min_version_year=min_version_year,
                sample_strategy=sample_strategy,
                max_works=max_works,
            )
        )
    reports = tuple(
        _benchmark_work(
            archive,
            work_id=work_id,
            include_diffs=include_diffs,
            include_payloads=include_payloads,
            actual_replay_summary=(
                _actual_replay_summary_for_work(archive, db_path=db_path, work_id=work_id)
                if include_actual_replay
                else None
            ),
        )
        for work_id in selected_work_ids
    )
    return NZBenchmarkReport(
        db_path=str(db_path),
        work_reports=reports,
        include_diffs=include_diffs,
        include_payloads=include_payloads,
        include_actual_replay=include_actual_replay,
        requested_work_ids=requested_work_ids,
        selected_work_ids=tuple(selected_work_ids),
        available_work_count=len(archived_work_ids),
        max_works=max_works,
    )


def _actual_replay_summary_for_work(
    archive: ArchiveReader,
    *,
    db_path: Path,
    work_id: str,
) -> Mapping[str, Any]:
    """Run the strict actual-replay surface for one work and return its summary.

    A per-work replay failure is surfaced loudly as a typed ``error`` residual
    rather than silently swallowed: the declaration must be able to tell an
    unclassified crash apart from an accepted, typed frontier.
    """

    from lawvm.new_zealand.actual_replay import build_actual_replay
    from lawvm.new_zealand.effect_candidates import build_archived_work_effect_candidate_preflight
    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    try:
        preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
        surface = build_archived_work_operation_surface(db_path, work_id)
        report = build_actual_replay(
            archive,
            work_id=work_id,
            preflight=preflight,
            surface=surface,
        )
    except Exception as exc:  # surfaced as a typed crash residual, never silent
        return {
            "work_id": work_id,
            "transitions_replayed": 0,
            "transitions_refused": 0,
            "target_slice_nodes": 0,
            "target_slice_agreements": 0,
            "residual_family_counts": {"error": 1},
            "residual_status_counts": {"error": 1},
            "actual_replay_build_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    return report.summary()


def _benchmark_work(
    archive: ArchiveReader,
    *,
    work_id: str,
    include_diffs: bool,
    include_payloads: bool,
    actual_replay_summary: Mapping[str, Any] | None = None,
) -> NZBenchmarkWorkReport:
    latest_version_id, latest_locator = latest_xml_locator_for_work(archive, work_id)
    if not latest_version_id or not latest_locator:
        return NZBenchmarkWorkReport(
            work_id=work_id,
            source_status="missing_xml",
            findings=(
                _finding(
                    work_id=work_id,
                    rule_id="nz_benchmark_latest_xml_missing",
                    phase="acquisition",
                    family="source_coverage",
                    reason="no archived latest XML locator for work",
                    blocking=True,
                ),
            ),
        )
    xml_bytes = archive.get(latest_locator)
    if xml_bytes is None:
        return NZBenchmarkWorkReport(
            work_id=work_id,
            latest_version_id=latest_version_id,
            latest_xml_locator=latest_locator,
            source_status="missing_xml",
            findings=(
                _finding(
                    work_id=work_id,
                    rule_id="nz_benchmark_latest_xml_unreadable",
                    phase="acquisition",
                    family="source_coverage",
                    reason="latest XML locator exists but bytes are not archived",
                    locator=latest_locator,
                    blocking=True,
                ),
            ),
        )

    try:
        document = parse_nz_source_document(
            xml_bytes,
            xml_locator=latest_locator,
            version_id=latest_version_id,
        )
        dependency_report = extract_dependency_report(
            xml_bytes=xml_bytes,
            xml_locator=latest_locator,
            work_id=work_id,
            version_id=latest_version_id,
        )
    except etree.XMLSyntaxError as exc:
        return NZBenchmarkWorkReport(
            work_id=work_id,
            latest_version_id=latest_version_id,
            latest_xml_locator=latest_locator,
            source_status="parse_error",
            findings=(
                _finding(
                    work_id=work_id,
                    rule_id="nz_benchmark_source_parse_error",
                    phase="source_tree",
                    family="source_pathology",
                    reason=str(exc),
                    locator=latest_locator,
                    blocking=True,
                ),
            ),
        )

    diff_status = "not_requested"
    previous_version_id = ""
    previous_locator = ""
    change_count = 0
    if include_diffs:
        previous_version_id, previous_locator, change_count, diff_status = _snapshot_diff_summary(
            archive,
            work_id=work_id,
            latest_version_id=latest_version_id,
            latest_document=document,
        )

    history_count = int(document.summary()["history_witnesses"])
    archived_dependency_work_ids = _archived_dependency_work_ids(archive, dependency_report.amending_works)
    operation_surface = build_operation_surface(
        document,
        work_id=work_id,
        archived_dependency_work_ids=archived_dependency_work_ids,
    )
    operation_summary = operation_surface.summary()
    payload_status_counts: Mapping[str, int] = {}
    payload_role_counts: Mapping[str, int] = {}
    payload_semantics_status_counts: Mapping[str, int] = {}
    payload_instruction_shape_counts: Mapping[str, int] = {}
    payload_instruction_safety_counts: Mapping[str, int] = {}
    payload_found = 0
    effect_readiness_status_counts: Mapping[str, int] = {}
    canonical_family_candidate_counts: Mapping[str, int] = {}
    instruction_semantic_candidate_status_counts: Mapping[str, int] = {}
    instruction_semantic_candidate_family_counts: Mapping[str, int] = {}
    instruction_semantic_rule_id_counts: Mapping[str, int] = {}
    instruction_structural_subfamily_status_counts: Mapping[str, int] = {}
    instruction_structural_subfamily_counts: Mapping[str, int] = {}
    ready_for_canonical_effect_lowering = 0
    effect_candidate_status_counts: Mapping[str, int] = {}
    effect_candidate_action_counts: Mapping[str, int] = {}
    effect_candidate_operation_family_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_rule_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_payload_shape_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_payload_safety_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_target_status_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_instruction_status_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_instruction_subfamily_status_counts: Mapping[str, int] = {}
    effect_candidate_payload_structural_subfamily_status_counts: Mapping[str, int] = {}
    effect_candidate_payload_structural_subfamily_counts: Mapping[str, int] = {}
    effect_candidate_witness_rule_counts: Mapping[str, int] = {}
    effect_candidate_action_witness_rule_counts: Mapping[str, int] = {}
    effect_candidate_text_replace_witness_support_status_counts: Mapping[str, int] = {}
    effect_candidate_action_text_replace_witness_support_status_counts: Mapping[str, int] = {}
    effect_candidate_action_source_change_text_witness_status_counts: Mapping[str, int] = {}
    effect_candidate_blocked_operation_family_source_change_text_witness_status_counts: Mapping[str, int] = {}
    effect_candidate_source_version_date_window_status_counts: Mapping[str, int] = {}
    effect_candidate_source_change_text_witness_status_counts: Mapping[str, int] = {}
    effect_candidate_repeal_payload_corroboration_status_counts: Mapping[str, int] = {}
    effect_candidate_operations = 0
    effect_candidate_emitted_rows = 0
    effect_candidate_operation_missing_rows = 0
    effect_preflight_status = ""
    effect_preflight_replayable_candidate_operations = 0
    effect_preflight_source_change_only_candidate_rows = 0
    effect_preflight_target_recovery_candidate_rows = 0
    effect_preflight_operations_to_replay = 0
    effect_preflight_blocking_rule_counts: Mapping[str, int] = {}
    if include_payloads:
        dependency_documents = _archived_dependency_documents(archive, archived_dependency_work_ids)
        payload_surface = build_payload_surface(operation_surface, dependency_documents=dependency_documents)
        payload_summary = payload_surface.summary()
        payload_status_counts = _string_int_mapping(payload_summary["payload_status_counts"])
        payload_role_counts = _string_int_mapping(payload_summary["payload_role_counts"])
        payload_semantics_status_counts = _string_int_mapping(payload_summary["payload_semantics_status_counts"])
        payload_instruction_shape_counts = _string_int_mapping(payload_summary["payload_instruction_shape_counts"])
        payload_instruction_safety_counts = _string_int_mapping(payload_summary["payload_instruction_safety_counts"])
        payload_found = int(payload_summary["payload_found"])
        effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
        effect_summary = effect_readiness.summary()
        effect_readiness_status_counts = _string_int_mapping(effect_summary["effect_readiness_status_counts"])
        canonical_family_candidate_counts = _string_int_mapping(effect_summary["canonical_family_candidate_counts"])
        instruction_semantic_candidate_status_counts = _string_int_mapping(
            effect_summary["instruction_semantic_candidate_status_counts"]
        )
        instruction_semantic_candidate_family_counts = _string_int_mapping(
            effect_summary["instruction_semantic_candidate_family_counts"]
        )
        instruction_semantic_rule_id_counts = _string_int_mapping(effect_summary["instruction_semantic_rule_id_counts"])
        ready_for_canonical_effect_lowering = int(effect_summary["ready_for_canonical_effect_lowering"])
        instruction_workqueue = build_instruction_workqueue(
            operation_surface,
            payload_surface,
            effect_readiness,
            document,
        )
        instruction_summary = instruction_workqueue.summary()
        instruction_structural_subfamily_status_counts = _string_int_mapping(
            instruction_summary["payload_structural_subfamily_status_counts"]
        )
        instruction_structural_subfamily_counts = _string_int_mapping(
            instruction_summary["payload_structural_subfamily_counts"]
        )
        effect_candidates = build_effect_candidate_surface_with_archived_source_witnesses(
            archive,
            work_id=work_id,
            operation_surface=operation_surface,
            payload_surface=payload_surface,
            effect_readiness=effect_readiness,
            instruction_workqueue=instruction_workqueue,
        )
        candidate_summary = effect_candidates.summary()
        effect_candidate_status_counts = _string_int_mapping(candidate_summary["candidate_status_counts"])
        effect_candidate_action_counts = _string_int_mapping(candidate_summary["candidate_action_counts"])
        effect_candidate_operation_family_counts = _string_int_mapping(candidate_summary["operation_family_counts"])
        effect_candidate_blocked_operation_family_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_counts"]
        )
        effect_candidate_blocked_operation_family_rule_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_rule_counts"]
        )
        effect_candidate_blocked_operation_family_payload_shape_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_payload_shape_counts"]
        )
        effect_candidate_blocked_operation_family_payload_safety_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_payload_safety_counts"]
        )
        effect_candidate_blocked_operation_family_target_status_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_target_status_counts"]
        )
        effect_candidate_blocked_operation_family_instruction_status_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_instruction_status_counts"]
        )
        effect_candidate_blocked_operation_family_instruction_subfamily_status_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_instruction_subfamily_status_counts"]
        )
        effect_candidate_payload_structural_subfamily_status_counts = _string_int_mapping(
            candidate_summary["payload_structural_subfamily_status_counts"]
        )
        effect_candidate_payload_structural_subfamily_counts = _string_int_mapping(
            candidate_summary["payload_structural_subfamily_counts"]
        )
        effect_candidate_witness_rule_counts = _string_int_mapping(candidate_summary["candidate_witness_rule_counts"])
        effect_candidate_action_witness_rule_counts = _string_int_mapping(
            candidate_summary["candidate_action_witness_rule_counts"]
        )
        effect_candidate_text_replace_witness_support_status_counts = _string_int_mapping(
            candidate_summary["text_replace_witness_support_status_counts"]
        )
        effect_candidate_action_text_replace_witness_support_status_counts = _string_int_mapping(
            candidate_summary["candidate_action_text_replace_witness_support_status_counts"]
        )
        effect_candidate_action_source_change_text_witness_status_counts = _string_int_mapping(
            candidate_summary["candidate_action_source_change_text_witness_status_counts"]
        )
        effect_candidate_blocked_operation_family_source_change_text_witness_status_counts = _string_int_mapping(
            candidate_summary["blocked_operation_family_source_change_text_witness_status_counts"]
        )
        effect_candidate_source_version_date_window_status_counts = _string_int_mapping(
            candidate_summary["source_version_date_window_status_counts"]
        )
        effect_candidate_source_change_text_witness_status_counts = _string_int_mapping(
            candidate_summary["source_change_text_witness_status_counts"]
        )
        effect_candidate_repeal_payload_corroboration_status_counts = _string_int_mapping(
            candidate_summary["repeal_payload_corroboration_status_counts"]
        )
        effect_candidate_emitted_rows = int(candidate_summary["candidate_emitted_rows"])
        effect_candidate_operation_missing_rows = int(candidate_summary["candidate_operation_missing_rows"])
        effect_candidate_operations = int(candidate_summary["candidate_operations"])
        effect_preflight = build_effect_candidate_preflight(effect_candidates)
        preflight_summary = effect_preflight.summary()
        effect_preflight_status = str(preflight_summary["preflight_status"])
        effect_preflight_replayable_candidate_operations = int(preflight_summary["replayable_candidate_operations"])
        effect_preflight_source_change_only_candidate_rows = int(preflight_summary["source_change_only_candidate_rows"])
        effect_preflight_target_recovery_candidate_rows = int(preflight_summary["target_recovery_candidate_rows"])
        effect_preflight_operations_to_replay = int(preflight_summary["operations_to_replay"])
        effect_preflight_blocking_rule_counts = _string_int_mapping(preflight_summary["blocking_rule_counts"])
    return NZBenchmarkWorkReport(
        work_id=work_id,
        latest_version_id=latest_version_id,
        latest_xml_locator=latest_locator,
        source_status="parsed",
        node_count=len(document.nodes),
        history_witness_count=history_count,
        history_operation_counts=_history_operation_counts(document),
        operation_witness_rows=int(operation_summary["rows"]),
        target_hint_status_counts=_string_int_mapping(operation_summary["target_hint_status_counts"]),
        target_hint_kind_counts=_string_int_mapping(operation_summary["target_hint_kind_counts"]),
        target_address_status_counts=_string_int_mapping(operation_summary["target_address_status_counts"]),
        amending_provision_href_status_counts=_string_int_mapping(
            operation_summary["amending_provision_href_status_counts"]
        ),
        lowering_readiness_status_counts=_string_int_mapping(operation_summary["lowering_readiness_status_counts"]),
        operation_surface_findings=int(operation_summary["findings"]),
        payload_status_counts=payload_status_counts,
        payload_role_counts=payload_role_counts,
        payload_semantics_status_counts=payload_semantics_status_counts,
        payload_instruction_shape_counts=payload_instruction_shape_counts,
        payload_instruction_safety_counts=payload_instruction_safety_counts,
        payload_found=payload_found,
        effect_readiness_status_counts=effect_readiness_status_counts,
        canonical_family_candidate_counts=canonical_family_candidate_counts,
        instruction_semantic_candidate_status_counts=instruction_semantic_candidate_status_counts,
        instruction_semantic_candidate_family_counts=instruction_semantic_candidate_family_counts,
        instruction_semantic_rule_id_counts=instruction_semantic_rule_id_counts,
        instruction_structural_subfamily_status_counts=instruction_structural_subfamily_status_counts,
        instruction_structural_subfamily_counts=instruction_structural_subfamily_counts,
        ready_for_canonical_effect_lowering=ready_for_canonical_effect_lowering,
        effect_candidate_status_counts=effect_candidate_status_counts,
        effect_candidate_action_counts=effect_candidate_action_counts,
        effect_candidate_operation_family_counts=effect_candidate_operation_family_counts,
        effect_candidate_blocked_operation_family_counts=effect_candidate_blocked_operation_family_counts,
        effect_candidate_blocked_operation_family_rule_counts=effect_candidate_blocked_operation_family_rule_counts,
        effect_candidate_blocked_operation_family_payload_shape_counts=(
            effect_candidate_blocked_operation_family_payload_shape_counts
        ),
        effect_candidate_blocked_operation_family_payload_safety_counts=(
            effect_candidate_blocked_operation_family_payload_safety_counts
        ),
        effect_candidate_blocked_operation_family_target_status_counts=(
            effect_candidate_blocked_operation_family_target_status_counts
        ),
        effect_candidate_blocked_operation_family_instruction_status_counts=(
            effect_candidate_blocked_operation_family_instruction_status_counts
        ),
        effect_candidate_blocked_operation_family_instruction_subfamily_status_counts=(
            effect_candidate_blocked_operation_family_instruction_subfamily_status_counts
        ),
        effect_candidate_payload_structural_subfamily_status_counts=(
            effect_candidate_payload_structural_subfamily_status_counts
        ),
        effect_candidate_payload_structural_subfamily_counts=effect_candidate_payload_structural_subfamily_counts,
        effect_candidate_witness_rule_counts=effect_candidate_witness_rule_counts,
        effect_candidate_action_witness_rule_counts=effect_candidate_action_witness_rule_counts,
        effect_candidate_text_replace_witness_support_status_counts=effect_candidate_text_replace_witness_support_status_counts,
        effect_candidate_action_text_replace_witness_support_status_counts=(
            effect_candidate_action_text_replace_witness_support_status_counts
        ),
        effect_candidate_action_source_change_text_witness_status_counts=(
            effect_candidate_action_source_change_text_witness_status_counts
        ),
        effect_candidate_blocked_operation_family_source_change_text_witness_status_counts=(
            effect_candidate_blocked_operation_family_source_change_text_witness_status_counts
        ),
        effect_candidate_source_version_date_window_status_counts=(
            effect_candidate_source_version_date_window_status_counts
        ),
        effect_candidate_source_change_text_witness_status_counts=(
            effect_candidate_source_change_text_witness_status_counts
        ),
        effect_candidate_repeal_payload_corroboration_status_counts=(
            effect_candidate_repeal_payload_corroboration_status_counts
        ),
        effect_candidate_emitted_rows=effect_candidate_emitted_rows,
        effect_candidate_operation_missing_rows=effect_candidate_operation_missing_rows,
        effect_candidate_operations=effect_candidate_operations,
        effect_preflight_status=effect_preflight_status,
        effect_preflight_replayable_candidate_operations=effect_preflight_replayable_candidate_operations,
        effect_preflight_source_change_only_candidate_rows=effect_preflight_source_change_only_candidate_rows,
        effect_preflight_target_recovery_candidate_rows=effect_preflight_target_recovery_candidate_rows,
        effect_preflight_operations_to_replay=effect_preflight_operations_to_replay,
        effect_preflight_blocking_rule_counts=effect_preflight_blocking_rule_counts,
        dependency_count=len(dependency_report.amending_works),
        dependency_diagnostics=len(dependency_report.diagnostics),
        previous_version_id=previous_version_id,
        previous_xml_locator=previous_locator,
        snapshot_diff_status=diff_status,
        snapshot_change_count=change_count,
        dependency_archived_count=len(archived_dependency_work_ids),
        **_replay_status_fields(
            work_id=work_id,
            latest_locator=latest_locator,
            actual_replay_summary=actual_replay_summary,
        ),
    )


def _replay_status_fields(
    *,
    work_id: str,
    latest_locator: str,
    actual_replay_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Derive the replay + oracle-agreement lanes from the real replay surface.

    Without an actual-replay summary the benchmark makes no replay claim for the
    work (``not_evaluated``); it never asserts the stale hardcoded "blocked"
    status, which falsely implied replay was structurally impossible even after
    actual replay landed. With a summary, the lanes report the real surface:
    replay is ``replayed`` when at least one transition materialized, ``blocked``
    when transitions were declared but all fail-closed-refused, and
    ``no_declared_transitions`` when nothing was declared. The oracle-agreement
    lane reports the typed residual-family counts the replay actually produced.
    """

    if actual_replay_summary is None:
        return {
            "replay_status": "not_evaluated",
            "replay_blocking_rule_id": NZ_REPLAY_BLOCKED_RULE_ID,
            "oracle_agreement_status": "not_evaluated",
            "oracle_agreement_blocking_rule_id": NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID,
            "oracle_agreement_exact_ratio": None,
            "findings": (
                _finding(
                    work_id=work_id,
                    rule_id=NZ_REPLAY_BLOCKED_RULE_ID,
                    phase="P7",
                    family="replay_not_evaluated",
                    reason=(
                        "NZ source witnesses are available; actual replay was not "
                        "evaluated in this benchmark run (pass include_actual_replay)"
                    ),
                    locator=latest_locator,
                    blocking=False,
                ),
            ),
        }

    replayed = int(actual_replay_summary.get("transitions_replayed", 0) or 0)
    refused = int(actual_replay_summary.get("transitions_refused", 0) or 0)
    if replayed > 0:
        replay_status = "replayed"
    elif refused > 0:
        replay_status = "blocked"
    else:
        replay_status = "no_declared_transitions"

    residual_family_counts = _string_int_mapping(
        actual_replay_summary.get("residual_family_counts", {})
    )
    slice_nodes = int(actual_replay_summary.get("target_slice_nodes", 0) or 0)
    slice_agreements = int(actual_replay_summary.get("target_slice_agreements", 0) or 0)
    exact_ratio = (slice_agreements / slice_nodes) if slice_nodes else None
    if replayed > 0:
        oracle_agreement_status = "agreement_by_residual_family"
    elif refused > 0:
        oracle_agreement_status = "blocked_no_candidate_replay"
    else:
        oracle_agreement_status = "no_declared_transitions"

    findings: tuple[dict[str, Any], ...] = ()
    if replay_status == "blocked":
        findings = (
            _finding(
                work_id=work_id,
                rule_id=NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID,
                phase="P9",
                family="blocked_oracle_agreement",
                reason=(
                    "all declared NZ transitions for this work were fail-closed-refused; "
                    "no materialized slice was fed to oracle agreement"
                ),
                locator=latest_locator,
                blocking=True,
            ),
        )

    return {
        "replay_status": replay_status,
        "replay_blocking_rule_id": NZ_REPLAY_BLOCKED_RULE_ID,
        "oracle_agreement_status": oracle_agreement_status,
        "oracle_agreement_blocking_rule_id": NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID,
        "oracle_agreement_exact_ratio": exact_ratio,
        "oracle_agreement_residual_family_counts": residual_family_counts,
        "findings": findings,
    }


def _snapshot_diff_summary(
    archive: ArchiveReader,
    *,
    work_id: str,
    latest_version_id: str,
    latest_document: Any,
) -> tuple[str, str, int, str]:
    previous = previous_archived_xml_version_for_work(
        archive,
        work_id=work_id,
        after_version_id=latest_version_id,
    )
    if previous is None:
        return "", "", 0, "missing_previous_xml"
    previous_version_id = previous.version_id
    previous_locator = previous.xml_locator
    previous_bytes = archive.get(previous_locator)
    if previous_bytes is None:
        return previous_version_id, previous_locator, 0, "missing_previous_xml"
    try:
        previous_document = parse_nz_source_document(
            previous_bytes,
            xml_locator=previous_locator,
            version_id=previous_version_id,
        )
    except etree.XMLSyntaxError:
        return previous_version_id, previous_locator, 0, "previous_parse_error"
    diff = diff_source_documents(previous_document, latest_document)
    return previous_version_id, previous_locator, len(diff.changes), "diffed"


def _archived_work_ids(archive: ArchiveReader) -> tuple[str, ...]:
    return tuple(_archived_work_max_version_year(archive))


def _archived_work_max_version_year(archive: ArchiveReader) -> dict[str, int | None]:
    """Map each archived work id to its latest archived version year (or None).

    The version year is the calendar year of the version-date suffix on the
    version id (``..._en_YYYY-MM-DD``). Works without a parseable date map to
    ``None`` so callers can decide how to treat them under a year filter.
    """

    max_year: dict[str, int | None] = {}
    prefix = "https://api.legislation.govt.nz/v0/versions/"
    for locator in archive.locators(prefix + "%"):
        version_id = locator.rstrip("/").rsplit("/", 1)[-1]
        work_id = _work_id_from_version_id(version_id)
        if not work_id:
            continue
        year = _version_year_from_version_id(version_id)
        previous = max_year.get(work_id, "__absent__")
        if previous == "__absent__":
            max_year[work_id] = year
        elif year is not None and (previous is None or year > previous):
            max_year[work_id] = year
    return dict(sorted(max_year.items()))


def _version_year_from_version_id(version_id: str) -> int | None:
    """Extract the 4-digit year from a ``..._YYYY-MM-DD`` version-date suffix."""

    suffix = version_id.rsplit("_", 1)[-1]
    head = suffix.split("-", 1)[0]
    if len(head) == 4 and head.isdigit():
        return int(head)
    return None


def select_benchmark_work_ids(
    archive: ArchiveReader,
    *,
    archived_work_ids: tuple[str, ...] | None = None,
    work_id_prefix: str = "",
    min_version_year: int | None = None,
    sample_strategy: str = "head",
    max_works: int | None = None,
) -> tuple[str, ...]:
    """Select archive-wide benchmark works deterministically.

    Selection order:
      1. start from all archived work ids (lexicographic),
      2. keep only ids beginning with ``work_id_prefix`` (if given),
      3. keep only works whose latest archived version year is
         ``>= min_version_year`` (if given); works with no parseable version
         date are dropped under a year filter,
      4. order by ``sample_strategy`` — ``head`` keeps lexicographic order,
         ``stride`` takes an evenly-spaced deterministic subsample,
      5. truncate to ``max_works``.

    A filter combination that matches no works raises
    :class:`NZBenchmarkSelectionError` rather than silently sampling the head.
    """

    if sample_strategy not in NZ_BENCHMARK_SAMPLE_STRATEGIES:
        raise NZBenchmarkSelectionError(
            f"unknown sample_strategy {sample_strategy!r}; "
            f"expected one of {', '.join(NZ_BENCHMARK_SAMPLE_STRATEGIES)}"
        )

    max_year = _archived_work_max_version_year(archive)
    population = tuple(archived_work_ids) if archived_work_ids is not None else tuple(max_year)

    filtered = [
        work_id
        for work_id in population
        if (not work_id_prefix or work_id.startswith(work_id_prefix))
        and _passes_min_year(max_year.get(work_id), min_version_year)
    ]
    if not filtered:
        raise NZBenchmarkSelectionError(
            "benchmark work-id selection matched zero works "
            f"(work_id_prefix={work_id_prefix!r}, min_version_year={min_version_year}, "
            f"available_work_count={len(population)}); refusing to fall back to the "
            "lexicographic head"
        )

    if sample_strategy == "stride" and max_works is not None and 0 < max_works < len(filtered):
        step = len(filtered) / max_works
        ordered = [filtered[int(index * step)] for index in range(max_works)]
    else:
        ordered = filtered
        if max_works is not None:
            ordered = ordered[: max(max_works, 0)]
    return tuple(ordered)


def _passes_min_year(year: int | None, min_version_year: int | None) -> bool:
    if min_version_year is None:
        return True
    if year is None:
        return False
    return year >= min_version_year


def _dependency_archived_count(archive: ArchiveReader, refs: tuple[Any, ...]) -> int:
    return len(_archived_dependency_work_ids(archive, refs))


def _archived_dependency_work_ids(archive: ArchiveReader, refs: tuple[Any, ...]) -> frozenset[str]:
    work_ids: set[str] = set()
    for ref in refs:
        _version_id, locator = latest_xml_locator_for_work(archive, ref.work_id)
        if locator:
            work_ids.add(ref.work_id)
    return frozenset(work_ids)


def _archived_dependency_documents(archive: ArchiveReader, work_ids: frozenset[str]) -> Mapping[str, Any]:
    documents: dict[str, Any] = {}
    for work_id in sorted(work_ids):
        version_id, locator = latest_xml_locator_for_work(archive, work_id)
        if not locator:
            continue
        data = archive.get(locator)
        if data is None:
            continue
        try:
            documents[work_id] = parse_nz_source_document(data, xml_locator=locator, version_id=version_id)
        except etree.XMLSyntaxError:
            continue
    return documents


def _history_operation_counts(document: Any) -> Mapping[str, int]:
    counts: Counter[str] = Counter()
    for witness in document.document_history:
        counts[_operation_key(witness.operation)] += 1
    for node in document.nodes:
        for witness in node.history:
            counts[_operation_key(witness.operation)] += 1
    return dict(sorted(counts.items()))


def _aggregate_operation_counts(reports: tuple[NZBenchmarkWorkReport, ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for report in reports:
        counts.update(report.history_operation_counts or {})
    return dict(sorted(counts.items()))


def _aggregate_mapping_counts(mappings: tuple[Mapping[str, int], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for mapping in mappings:
        counts.update(mapping)
    return dict(sorted(counts.items()))


def _triage_exemplars(
    reports: tuple[NZBenchmarkWorkReport, ...],
    key_counts_for_work: Callable[[NZBenchmarkWorkReport], Mapping[str, int]],
) -> dict[str, list[str]]:
    exemplars: dict[str, list[str]] = {}
    for report in reports:
        counts = key_counts_for_work(report)
        for key in sorted(counts):
            if int(counts[key]) <= 0:
                continue
            work_ids = exemplars.setdefault(str(key), [])
            if len(work_ids) < _TRIAGE_EXEMPLAR_LIMIT:
                work_ids.append(report.work_id)
    return dict(sorted(exemplars.items()))


def _string_int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): int(count) for key, count in value.items()}


_TRIAGE_EXEMPLAR_LIMIT = 5
_SELECTION_WORK_ID_SAMPLE_LIMIT = 50


def _work_declaration_inputs(row: NZBenchmarkWorkReport) -> dict[str, Any]:
    """Project one per-work benchmark report into the declaration-ladder inputs.

    This is the only coupling between the (rich) per-work report and the (pure)
    :func:`compute_nz_declaration`, so the ladder logic can be unit-tested on
    synthetic dicts without a farchive.
    """

    candidate_status_counts = _string_int_mapping(row.effect_candidate_status_counts or {})
    candidate_rows = sum(candidate_status_counts.values())
    return {
        "work_id": row.work_id,
        "source_status": row.source_status,
        "dependency_diagnostics": row.dependency_diagnostics,
        "operation_witness_rows": row.operation_witness_rows,
        "candidate_status_counts": candidate_status_counts,
        "candidate_rows": candidate_rows,
        "effect_preflight_status": row.effect_preflight_status,
        "effect_preflight_replayable_candidate_operations": (
            row.effect_preflight_replayable_candidate_operations
        ),
        "replay_status": row.replay_status,
        "oracle_agreement_status": row.oracle_agreement_status,
        "oracle_agreement_residual_family_counts": _string_int_mapping(
            row.oracle_agreement_residual_family_counts or {}
        ),
    }


def compute_nz_declaration(
    per_work: tuple[Mapping[str, Any], ...],
    *,
    include_payloads: bool,
    include_actual_replay: bool,
) -> dict[str, Any]:
    """Compute the cumulative NZ Phase 8 declaration ladder over a slice.

    The ladder is cumulative: the achieved level is the deepest rung for which
    that rung AND every rung below it hold. Each rung is a separate, honest lane
    — the result reports per-rung ``met``/``reason``/witness counts so a reader
    sees WHY a level was not reached, not just the headline. A rung that cannot
    even be evaluated (payload or actual-replay lanes were not run) is reported
    as ``met=False`` with a ``not_evaluated`` reason, never silently passed.

    Rungs (NZ roadmap Phase 8):

    * ``source-complete``     : every work source-parsed (archive/source-parse/
      dependency/evidence surfaces ran over the slice with no source crash);
    * ``candidate-complete``  : every operation witness is a candidate effect OR
      a typed (blocked) frontier row — i.e. the candidate surface covers every
      operation witness and emits only typed statuses;
    * ``dry-run-complete``    : the slice declared replay-authorized candidates
      and none were fail-closed-blocked (every authorized candidate has a
      mutation-boundary dry-run proof; no work blocked solely by replay);
    * ``replay-complete``     : actual replay materialized every declared
      transition in the slice (replay feeds agreement; no fail-closed-refused
      transition remains);
    * ``jurisdiction-complete``: every remaining residual is a TYPED frontier —
      no untyped ``replay_bug`` / ``error`` / ``unknown`` residual family and no
      unclassified per-work replay crash.
    """

    n = len(per_work)

    # --- source-complete ---------------------------------------------------
    source_parsed = sum(1 for w in per_work if w.get("source_status") == "parsed")
    source_not_parsed = [w["work_id"] for w in per_work if w.get("source_status") != "parsed"]
    source_met = n > 0 and not source_not_parsed
    source_rung = {
        "level": NZ_DECLARATION_SOURCE_COMPLETE,
        "met": source_met,
        "works": n,
        "source_parsed": source_parsed,
        "source_not_parsed": len(source_not_parsed),
        "source_not_parsed_work_ids": source_not_parsed[:_TRIAGE_EXEMPLAR_LIMIT],
        "reason": (
            "all works source-parsed"
            if source_met
            else ("no works in slice" if n == 0 else "some works did not source-parse")
        ),
    }

    # --- candidate-complete ------------------------------------------------
    if not include_payloads:
        candidate_rung = {
            "level": NZ_DECLARATION_CANDIDATE_COMPLETE,
            "met": False,
            "reason": "not_evaluated: include_payloads was not set, candidate lanes absent",
        }
    else:
        uncovered_works: list[str] = []
        untyped_status_works: list[str] = []
        typed_candidate_statuses = {"candidate_emitted", "blocked"}
        for w in per_work:
            op_rows = int(w.get("operation_witness_rows", 0) or 0)
            cand_rows = int(w.get("candidate_rows", 0) or 0)
            statuses = set((w.get("candidate_status_counts") or {}).keys())
            if op_rows > 0 and cand_rows < op_rows:
                uncovered_works.append(w["work_id"])
            if statuses - typed_candidate_statuses:
                untyped_status_works.append(w["work_id"])
        candidate_met = source_met and not uncovered_works and not untyped_status_works
        candidate_rung = {
            "level": NZ_DECLARATION_CANDIDATE_COMPLETE,
            "met": candidate_met,
            "operation_witness_uncovered_works": len(uncovered_works),
            "operation_witness_uncovered_work_ids": uncovered_works[:_TRIAGE_EXEMPLAR_LIMIT],
            "untyped_candidate_status_works": len(untyped_status_works),
            "untyped_candidate_status_work_ids": untyped_status_works[:_TRIAGE_EXEMPLAR_LIMIT],
            "reason": (
                "every operation witness is a candidate or a typed frontier row"
                if candidate_met
                else (
                    "blocked by source-complete"
                    if not source_met
                    else "some operation witnesses are uncovered or untyped"
                )
            ),
        }

    # --- replay lanes (dry-run / replay / jurisdiction) --------------------
    if not include_actual_replay:
        not_evaluated = "not_evaluated: include_actual_replay was not set, replay lanes absent"
        dry_run_rung = {
            "level": NZ_DECLARATION_DRY_RUN_COMPLETE,
            "met": False,
            "reason": not_evaluated,
        }
        replay_rung = {
            "level": NZ_DECLARATION_REPLAY_COMPLETE,
            "met": False,
            "reason": not_evaluated,
        }
        jurisdiction_rung = {
            "level": NZ_DECLARATION_JURISDICTION_COMPLETE,
            "met": False,
            "reason": not_evaluated,
        }
    else:
        # dry-run-complete: every replay-authorized candidate in the slice has a
        # mutation-boundary dry-run proof. Two ways a work blocks this rung:
        #  - replay_status "blocked": a declared transition was fail-closed-refused
        #    (a proof was attempted but no mutation-boundary proof held), and
        #  - a preflight status that is neither "ready_for_dry_run_replay" nor the
        #    "blocked_no_candidate_rows" terminus, which means a replay-authorizable
        #    candidate exists but never reached a dry-run proof.
        # A work whose preflight has genuinely no candidate rows is a typed
        # terminus (covered by candidate-complete), not a missing dry-run proof.
        replay_blocked_works = [w["work_id"] for w in per_work if w.get("replay_status") == "blocked"]
        replayed_works = [w["work_id"] for w in per_work if w.get("replay_status") == "replayed"]
        preflight_unproven_works = [
            w["work_id"]
            for w in per_work
            if str(w.get("effect_preflight_status") or "")
            not in ("ready_for_dry_run_replay", "blocked_no_candidate_rows", "")
            and w["work_id"] not in replayed_works
        ]
        dry_run_met = (
            candidate_rung["met"]
            and bool(replayed_works)
            and not replay_blocked_works
            and not preflight_unproven_works
        )
        dry_run_rung = {
            "level": NZ_DECLARATION_DRY_RUN_COMPLETE,
            "met": dry_run_met,
            "works_with_replayed_transitions": len(replayed_works),
            "replay_blocked_works": len(replay_blocked_works),
            "replay_blocked_work_ids": replay_blocked_works[:_TRIAGE_EXEMPLAR_LIMIT],
            "preflight_unproven_candidate_works": len(preflight_unproven_works),
            "preflight_unproven_candidate_work_ids": preflight_unproven_works[:_TRIAGE_EXEMPLAR_LIMIT],
            "reason": (
                "all replay-authorized candidates have dry-run proof; no work blocked by replay"
                if dry_run_met
                else (
                    "blocked by candidate-complete"
                    if not candidate_rung["met"]
                    else (
                        "no work materialized any transition"
                        if not replayed_works
                        else (
                            "some works are fail-closed-blocked on replay"
                            if replay_blocked_works
                            else "some works have replay-authorizable candidates with no dry-run proof"
                        )
                    )
                )
            ),
        }

        # replay-complete: actual replay materialized every declared transition
        # (no fail-closed-refused transition remains in the slice).
        replay_met = dry_run_met and not replay_blocked_works and bool(replayed_works)
        replay_rung = {
            "level": NZ_DECLARATION_REPLAY_COMPLETE,
            "met": replay_met,
            "works_with_replayed_transitions": len(replayed_works),
            "reason": (
                "actual replay materialized the slice and fed agreement"
                if replay_met
                else "blocked by dry-run-complete or unresolved fail-closed transitions"
            ),
        }

        # jurisdiction-complete: every remaining residual is a typed frontier.
        untyped_residual_works: list[str] = []
        untyped_residual_family_counts: Counter[str] = Counter()
        for w in per_work:
            families = w.get("oracle_agreement_residual_family_counts") or {}
            untyped = {
                family: count
                for family, count in families.items()
                if family in NZ_UNTYPED_RESIDUAL_FAMILIES and int(count) > 0
            }
            if untyped:
                untyped_residual_works.append(w["work_id"])
                untyped_residual_family_counts.update(untyped)
        jurisdiction_met = replay_met and not untyped_residual_works
        jurisdiction_rung = {
            "level": NZ_DECLARATION_JURISDICTION_COMPLETE,
            "met": jurisdiction_met,
            "untyped_residual_works": len(untyped_residual_works),
            "untyped_residual_work_ids": untyped_residual_works[:_TRIAGE_EXEMPLAR_LIMIT],
            "untyped_residual_family_counts": dict(sorted(untyped_residual_family_counts.items())),
            "reason": (
                "every remaining residual is a typed frontier"
                if jurisdiction_met
                else (
                    "blocked by replay-complete"
                    if not replay_met
                    else "some residuals are untyped (replay_bug / error / unknown)"
                )
            ),
        }

    rungs = (source_rung, candidate_rung, dry_run_rung, replay_rung, jurisdiction_rung)
    # The achieved level is the deepest contiguous rung that holds.
    achieved = NZ_DECLARATION_INCOMPLETE
    for rung in rungs:
        if rung["met"]:
            achieved = str(rung["level"])
        else:
            break

    return {
        "declaration_level": achieved,
        "ladder": list(NZ_DECLARATION_LADDER),
        "include_payloads": include_payloads,
        "include_actual_replay": include_actual_replay,
        "works": n,
        "rungs": {str(rung["level"]): rung for rung in rungs},
    }


def _operation_key(operation: str) -> str:
    return classify_operation_family(operation)


def _work_id_from_version_id(version_id: str) -> str:
    parts = version_id.split("_")
    if len(parts) < 6:
        return ""
    return "_".join(parts[:4])


def _finding(
    *,
    work_id: str,
    rule_id: str,
    phase: str,
    family: str,
    reason: str,
    locator: str = "",
    blocking: bool,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        phase=phase,
        family=family,
        reason=reason,
        blocking=blocking,
        strict_disposition="block" if blocking else "warn",
        quirks_disposition=QuirksDisposition.SKIP_WITH_FINDING if blocking else QuirksDisposition.WARN,
        work_id=work_id,
        locator=locator,
    )


def main(args: Any) -> None:
    work_ids = tuple(args.work_id or ())
    corpus_path = getattr(args, "corpus", None)
    if corpus_path:
        from lawvm.new_zealand.bench_corpus import NZBenchCorpusError, read_corpus_work_ids

        try:
            corpus_work_ids = read_corpus_work_ids(Path(corpus_path))
        except NZBenchCorpusError as exc:
            raise SystemExit(f"nz-corpus benchmark: {exc}") from exc
        # Explicit --work-id wins; otherwise the curated corpus is the population.
        if not work_ids:
            work_ids = corpus_work_ids

    archive = open_farchive(Path(args.db))
    try:
        report = build_nz_benchmark_report(
            archive,
            db_path=Path(args.db),
            work_ids=work_ids,
            max_works=args.max_works,
            work_id_prefix=getattr(args, "work_id_prefix", "") or "",
            min_version_year=getattr(args, "min_version_year", None),
            sample_strategy=getattr(args, "sample_strategy", "head") or "head",
            include_diffs=args.include_diffs,
            include_payloads=args.include_payloads,
            include_actual_replay=getattr(args, "include_actual_replay", False),
        )
    except NZBenchmarkSelectionError as exc:
        raise SystemExit(f"nz-corpus benchmark: {exc}") from exc
    finally:
        archive.close()

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2))
        return

    summary = report.summary()
    selection = summary["selection_context"]
    print(
        f"works={summary['works']} source_parsed={summary['source_parsed']} "
        f"selected_work_count={selection['selected_work_count']} "
        f"available_work_count={selection['available_work_count']} "
        f"dependency_edges={summary['dependency_edges']} snapshot_diffs={summary['snapshot_diffs']} "
        f"replay_blocked={summary['replay_blocked']}"
    )
    declaration = report.declaration()
    print(
        f"declaration_level={declaration['declaration_level']} "
        f"(include_payloads={declaration['include_payloads']} "
        f"include_actual_replay={declaration['include_actual_replay']})"
    )
    for level in declaration["ladder"]:
        rung = declaration["rungs"][level]
        mark = "PASS" if rung["met"] else "----"
        print(f"  [{mark}] {level}: {rung['reason']}")
    if report.include_actual_replay:
        print(f"replay_status_counts={summary['replay_status_counts']}")
        print(f"oracle_agreement_status_counts={summary['oracle_agreement_status_counts']}")
        print(
            "oracle_agreement_residual_family_counts="
            f"{summary['oracle_agreement_residual_family_counts']}"
        )
    print(f"history_operation_counts={summary['history_operation_counts']}")
    print(f"target_hint_status_counts={summary['target_hint_status_counts']}")
    print(f"target_address_status_counts={summary['target_address_status_counts']}")
    print(f"amending_provision_href_status_counts={summary['amending_provision_href_status_counts']}")
    print(f"lowering_readiness_status_counts={summary['lowering_readiness_status_counts']}")
    if summary["payload_status_counts"]:
        print(f"payload_status_counts={summary['payload_status_counts']}")
    if summary["payload_semantics_status_counts"]:
        print(f"payload_semantics_status_counts={summary['payload_semantics_status_counts']}")
    if summary["payload_instruction_shape_counts"]:
        print(f"payload_instruction_shape_counts={summary['payload_instruction_shape_counts']}")
    if summary["payload_instruction_safety_counts"]:
        print(f"payload_instruction_safety_counts={summary['payload_instruction_safety_counts']}")
    if summary["effect_readiness_status_counts"]:
        print(f"effect_readiness_status_counts={summary['effect_readiness_status_counts']}")
    if summary["instruction_semantic_candidate_status_counts"]:
        print(
            "instruction_semantic_candidate_status_counts="
            f"{summary['instruction_semantic_candidate_status_counts']}"
        )
    if summary["instruction_semantic_candidate_family_counts"]:
        print(
            "instruction_semantic_candidate_family_counts="
            f"{summary['instruction_semantic_candidate_family_counts']}"
        )
    if summary["instruction_structural_subfamily_status_counts"]:
        print(
            "instruction_structural_subfamily_status_counts="
            f"{summary['instruction_structural_subfamily_status_counts']}"
        )
    if summary["effect_candidate_status_counts"]:
        print(f"effect_candidate_status_counts={summary['effect_candidate_status_counts']}")
    if summary["effect_candidate_source_change_text_witness_status_counts"]:
        print(
            "effect_candidate_source_change_text_witness_status_counts="
            f"{summary['effect_candidate_source_change_text_witness_status_counts']}"
        )
    if summary["effect_candidate_text_replace_witness_support_status_counts"]:
        print(
            "effect_candidate_text_replace_witness_support_status_counts="
            f"{summary['effect_candidate_text_replace_witness_support_status_counts']}"
        )
    if summary["effect_candidate_action_source_change_text_witness_status_counts"]:
        print(
            "effect_candidate_action_source_change_text_witness_status_counts="
            f"{summary['effect_candidate_action_source_change_text_witness_status_counts']}"
        )
    if summary["effect_candidate_blocked_operation_family_payload_shape_counts"]:
        print(
            "effect_candidate_blocked_operation_family_payload_shape_counts="
            f"{summary['effect_candidate_blocked_operation_family_payload_shape_counts']}"
        )
    if summary["effect_candidate_blocked_operation_family_payload_safety_counts"]:
        print(
            "effect_candidate_blocked_operation_family_payload_safety_counts="
            f"{summary['effect_candidate_blocked_operation_family_payload_safety_counts']}"
        )
    if summary["effect_candidate_blocked_operation_family_instruction_subfamily_status_counts"]:
        print(
            "effect_candidate_blocked_operation_family_instruction_subfamily_status_counts="
            f"{summary['effect_candidate_blocked_operation_family_instruction_subfamily_status_counts']}"
        )
    if summary["effect_preflight_status_counts"]:
        print(f"effect_preflight_status_counts={summary['effect_preflight_status_counts']}")
        print(
            "effect_preflight_replayable_candidate_operations="
            f"{summary['effect_preflight_replayable_candidate_operations']}"
        )
        print(
            "effect_preflight_source_change_only_candidate_rows="
            f"{summary['effect_preflight_source_change_only_candidate_rows']}"
        )
        print(
            "effect_preflight_target_recovery_candidate_rows="
            f"{summary['effect_preflight_target_recovery_candidate_rows']}"
        )
    print(f"replay_blocking_rule_id={summary['replay_blocking_rule_id']}")
    print(f"oracle_agreement_blocking_rule_id={summary['oracle_agreement_blocking_rule_id']}")
    for row in report.work_reports[: args.limit]:
        print(
            f"{row.work_id}\t{row.source_status}\tnodes={row.node_count}\t"
            f"deps={row.dependency_count}\tdiff={row.snapshot_diff_status}:{row.snapshot_change_count}\t"
            f"replay={row.replay_status}\tagreement={row.oracle_agreement_status}"
        )
    if len(report.work_reports) > args.limit:
        print(f"... {len(report.work_reports) - args.limit} more")
