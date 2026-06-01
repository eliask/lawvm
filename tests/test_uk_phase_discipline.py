from types import SimpleNamespace

from lawvm.uk_legislation.phase_discipline import (
    UK_PHASE_AFFECTING_SOURCE_EXTRACTION,
    UK_PHASE_CANONICAL_OP_COMPILATION,
    UK_PHASE_COMPARE_ORACLE_CLASSIFICATION,
    UK_PHASE_EFFECT_METADATA_FRONTEND,
    UK_PHASE_REPLAY_INVARIANTS,
    UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER,
    UK_PHASE_TYPED_ELABORATION,
    uk_phase_owner_counts_for_replay_adjudications,
    uk_phase_owner_for_diagnostic,
    uk_phase_owner_for_replay_adjudication,
    uk_phase_owner_counts_for_diagnostics,
    uk_phase_owner_for_manual_frontier,
)


def test_manual_frontier_phase_owner_classifies_phase_boundaries() -> None:
    assert (
        uk_phase_owner_for_manual_frontier(
            manual_compile_status="deterministic_frontend_supported",
            manual_compile_rule_id="uk_manual_frontier_deterministic_supported",
        )
        == UK_PHASE_CANONICAL_OP_COMPILATION
    )
    assert (
        uk_phase_owner_for_manual_frontier(
            manual_compile_status="source_insufficient",
            manual_compile_rule_id="uk_manual_frontier_missing_payload_source_insufficient",
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_manual_frontier(
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_deictic_amendment_program_target_candidate"
            ),
        )
        == UK_PHASE_TYPED_ELABORATION
    )
    assert (
        uk_phase_owner_for_manual_frontier(
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_commencement_condition_candidate",
        )
        == UK_PHASE_EFFECT_METADATA_FRONTEND
    )
    assert (
        uk_phase_owner_for_manual_frontier(
            manual_compile_status="unclassified",
            manual_compile_rule_id="",
        )
        == UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    )


def test_diagnostic_phase_owner_prefers_explicit_and_infers_common_families() -> None:
    assert (
        uk_phase_owner_for_diagnostic({"owner_phase": UK_PHASE_TYPED_ELABORATION})
        == UK_PHASE_TYPED_ELABORATION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "manual_compile_status": "source_insufficient",
                "manual_compile_rule_id": (
                    "uk_manual_frontier_missing_payload_source_insufficient"
                ),
            }
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_diagnostic({"rule_id": "uk_effect_feed_empty_recorded"})
        == UK_PHASE_EFFECT_METADATA_FRONTEND
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {"rule_id": "uk_prefetch_http_error", "phase": "acquisition"}
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_diagnostic({"rule_id": "uk_replay_oracle_branch_retained"})
        == UK_PHASE_REPLAY_INVARIANTS
    )
    assert (
        uk_phase_owner_for_diagnostic({"rule_id": "uk_oracle_projection_artifact"})
        == UK_PHASE_COMPARE_ORACLE_CLASSIFICATION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_nonstructural_unsupported_no_ops_observed",
                "family": "nonstructural_replay_observation",
                "phase": "lowering",
            }
        )
        == UK_PHASE_CANONICAL_OP_COMPILATION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "family": "source_pathology",
                "source_pathology": "missing_extracted_source",
            }
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_instruction_text_payload_rejected",
                "family": "source_pathology_filter",
                "source_pathology": "instruction_text_reused_as_payload",
            }
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_non_substantive_payload_rejected",
                "family": "source_pathology_filter",
                "source_pathology": "non_substantive_shell_payload",
            }
        )
        == UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "family": "source_pathology",
                "source_pathology": "table_entry_target_unsupported",
            }
        )
        == UK_PHASE_TYPED_ELABORATION
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "family": "source_pathology",
                "source_pathology": "commencement_effect_out_of_scope",
            }
        )
        == UK_PHASE_EFFECT_METADATA_FRONTEND
    )
    assert (
        uk_phase_owner_for_diagnostic(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "family": "source_pathology",
            }
        )
        == UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    )


def test_phase_owner_counts_for_diagnostics_returns_stable_sorted_counts() -> None:
    assert uk_phase_owner_counts_for_diagnostics(
        (
            {"owner_phase": UK_PHASE_TYPED_ELABORATION},
            {"rule_id": "uk_effect_feed_empty_recorded"},
            {"rule_id": "uk_replay_existing_target_conflict_gap"},
        )
    ) == {
        UK_PHASE_EFFECT_METADATA_FRONTEND: 1,
        UK_PHASE_REPLAY_INVARIANTS: 1,
        UK_PHASE_TYPED_ELABORATION: 1,
    }


def test_replay_adjudication_phase_owner_uses_replay_phase() -> None:
    adjudication = SimpleNamespace(
        kind="uk_replay_target_not_found",
        detail={"target": "section:99"},
    )
    assert (
        uk_phase_owner_for_replay_adjudication(adjudication)
        == UK_PHASE_REPLAY_INVARIANTS
    )
    assert uk_phase_owner_counts_for_replay_adjudications((adjudication,)) == {
        UK_PHASE_REPLAY_INVARIANTS: 1
    }
