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


NZ_REPLAY_BLOCKED_RULE_ID = "nz_replay_canonical_effects_not_implemented"
NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID = "nz_oracle_agreement_candidate_replay_missing"


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
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class NZBenchmarkReport:
    db_path: str
    work_reports: tuple[NZBenchmarkWorkReport, ...]
    include_diffs: bool
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
            "replay_claims": False,
            "selection_context": self.selection_context(),
            "summary": self.summary(),
            "works": [row.to_jsonable() for row in self.work_reports],
        }


def build_nz_benchmark_report(
    archive: ArchiveReader,
    *,
    db_path: Path,
    work_ids: tuple[str, ...] = (),
    max_works: int | None = None,
    include_diffs: bool = False,
    include_payloads: bool = False,
) -> NZBenchmarkReport:
    archived_work_ids = tuple(_archived_work_ids(archive))
    requested_work_ids = tuple(dict.fromkeys(work_ids))
    selected_work_ids = list(requested_work_ids or archived_work_ids)
    if max_works is not None:
        selected_work_ids = selected_work_ids[: max(max_works, 0)]
    reports = tuple(
        _benchmark_work(archive, work_id=work_id, include_diffs=include_diffs, include_payloads=include_payloads)
        for work_id in selected_work_ids
    )
    return NZBenchmarkReport(
        db_path=str(db_path),
        work_reports=reports,
        include_diffs=include_diffs,
        requested_work_ids=requested_work_ids,
        selected_work_ids=tuple(selected_work_ids),
        available_work_count=len(archived_work_ids),
        max_works=max_works,
    )


def _benchmark_work(
    archive: ArchiveReader,
    *,
    work_id: str,
    include_diffs: bool,
    include_payloads: bool,
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
        replay_status="blocked",
        oracle_agreement_status="blocked_no_candidate_replay",
        dependency_archived_count=len(archived_dependency_work_ids),
        findings=(
            _finding(
                work_id=work_id,
                rule_id=NZ_REPLAY_BLOCKED_RULE_ID,
                phase="P7",
                family="blocked_replay",
                reason="NZ source witnesses are available, but amendment Acts are not yet lowered to canonical effects",
                locator=latest_locator,
                blocking=True,
            ),
            _finding(
                work_id=work_id,
                rule_id=NZ_ORACLE_AGREEMENT_BLOCKED_RULE_ID,
                phase="P9",
                family="blocked_oracle_agreement",
                reason="NZ oracle agreement requires a candidate replay materialization, which is not emitted yet",
                locator=latest_locator,
                blocking=True,
            ),
        ),
    )


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
    work_ids: set[str] = set()
    prefix = "https://api.legislation.govt.nz/v0/versions/"
    for locator in archive.locators(prefix + "%"):
        version_id = locator.rstrip("/").rsplit("/", 1)[-1]
        work_id = _work_id_from_version_id(version_id)
        if work_id:
            work_ids.add(work_id)
    return tuple(sorted(work_ids))


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
    return {
        "rule_id": rule_id,
        "phase": phase,
        "family": family,
        "work_id": work_id,
        "locator": locator,
        "reason": reason,
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "warn",
        "quirks_disposition": "skip_with_finding" if blocking else "warn",
    }


def main(args: Any) -> None:
    archive = open_farchive(Path(args.db))
    try:
        report = build_nz_benchmark_report(
            archive,
            db_path=Path(args.db),
            work_ids=tuple(args.work_id or ()),
            max_works=args.max_works,
            include_diffs=args.include_diffs,
            include_payloads=args.include_payloads,
        )
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
