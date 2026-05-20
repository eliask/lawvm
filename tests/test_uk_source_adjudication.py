from __future__ import annotations

import ast
from pathlib import Path

from lawvm.uk_legislation import source_adjudication as sa
from lawvm.uk_legislation.source_adjudication import (
    classify_uk_commencement_current_projection,
    classify_uk_current_projection_eid_shape,
    classify_uk_effect_compare_shape,
    classify_uk_effect_source_pathology,
    classify_uk_manual_compile_frontier,
    classify_uk_replay_residual,
    classify_uk_bench_comparison,
    is_core_uk_effect_compare_candidate,
    is_core_uk_effect_source_candidate,
    is_core_uk_comparison,
    normalize_uk_replay_compare_eids,
)


def _uk_replay_string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("uk_replay_")
    }


def test_uk_replay_emitted_adjudication_kinds_are_explicitly_owned() -> None:
    replay_path = Path(__file__).parents[1] / "src/lawvm/uk_legislation/uk_amendment_replay.py"
    emitted = _uk_replay_string_constants(replay_path)
    owned = (
        sa.UK_REPLAY_BUG_ADJUDICATION_KINDS
        | sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS
    )

    assert sorted(emitted - owned) == []


def test_classify_uk_replay_adjudication_bucket() -> None:
    cases = {
        "uk_replay_target_not_found": "replay_bug",
        "uk_replay_definition_child_shape_gap": "source_shape",
        "uk_replay_definition_anchor_lexical_variant_recovered": "source_shape",
        "uk_replay_definition_entry_shape_gap": "source_shape",
        "uk_replay_heading_facet_target_gap": "source_shape",
        "uk_replay_heading_text_preimage_gap": "text_surface",
        "uk_replay_text_insert_anchor_preimage_gap": "text_surface",
        "uk_replay_text_match_article_phrase_surface_gap": "text_surface",
        "uk_replay_text_match_citation_connector_surface_gap": "text_surface",
        "uk_replay_repeated_form_label_payload_shape_gap": "source_shape",
        "uk_replay_schedule_entry_repeal_granularity_blocked": "source_shape",
        "uk_replay_schedule_list_entry_anchor_unresolved": "source_shape",
        "uk_replay_schedule_list_entry_replace_unresolved": "source_shape",
        "uk_replay_schedule_list_entry_repeal_unresolved": "source_shape",
        "uk_replay_table_entry_row_insert_unresolved": "source_shape",
        "uk_replay_table_entry_inline_text_insertion_unresolved": "source_shape",
        "uk_replay_table_entry_inline_text_preimage_gap": "source_shape",
        "uk_replay_definition_anchor_parenthetical_translation_normalized": "nonblocking_observation",
        "uk_replay_definition_predicate_shall_construed_normalized": "nonblocking_observation",
        "uk_replay_direct_section_paragraph_child_text_recovered": "nonblocking_observation",
        "uk_replay_empty_descendant_parent_text_recovered": "nonblocking_observation",
        "uk_replay_implicit_first_subparagraph_parent_text_recovered": "nonblocking_observation",
        "uk_replay_schedule_list_entry_alphabetical_position_resolved": "nonblocking_observation",
        "uk_replay_schedule_list_entry_anchor_article_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_anchor_prefix_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_group_anchor_resolved": "nonblocking_observation",
        "uk_replay_schedule_list_entry_replace_resolved": "nonblocking_observation",
        "uk_replay_schedule_list_entry_repeal_resolved": "nonblocking_observation",
        "uk_effect_table_entry_row_insert": "nonblocking_observation",
        "uk_replay_source_carried_structured_tail_substitution_recovered": "nonblocking_observation",
        "uk_replay_text_match_missing": "text_surface",
        "uk_replay_text_monetary_amount_preimage_gap": "text_surface",
        "uk_replay_text_parenthetical_omission_preimage_gap": "text_surface",
        "uk_replay_same_source_text_patch_overlap_blocked": "source_shape",
        "uk_replay_same_source_text_patch_overlap_disjoint": "nonblocking_observation",
        "text_duplication_warning": "nonblocking_observation",
        "uk_replay_text_match_punctuation_space_normalized": "nonblocking_observation",
        "uk_replay_text_match_replacement_normalized_present": "nonblocking_observation",
        "uk_replay_future_kind": "unknown",
        "": "unknown",
    }

    for kind, expected_bucket in cases.items():
        assert sa.classify_uk_replay_adjudication_bucket(kind) == expected_bucket


def test_classify_uk_no_oracle_as_non_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=72,
        n_oracle_eids=0,
        n_effects=11,
        raw_score=0.0,
    )

    assert comparison == "no_oracle_eids"
    assert is_core_uk_comparison(comparison) is False


def test_classify_uk_oracle_collapsed_structure_as_non_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=167,
        n_oracle_eids=1,
        n_effects=0,
        raw_score=0.006,
    )

    assert comparison == "oracle_collapsed_structure"
    assert is_core_uk_comparison(comparison) is False


def test_classify_uk_unapplied_expansion_as_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=46,
        n_oracle_eids=476,
        n_effects=12,
        raw_score=0.097,
    )

    assert comparison == "unapplied_oracle_expansion"
    assert is_core_uk_comparison(comparison) is True


def test_classify_uk_nonstructural_current_projection_as_non_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=22,
        n_oracle_eids=7,
        n_effects=7,
        raw_score=0.318,
        effect_source_pathology_counts={"nonstructural_root_gap": 7},
    )

    assert comparison == "nonstructural_current_projection"
    assert is_core_uk_comparison(comparison) is False


def test_classify_uk_mixed_nonstructural_and_structural_stays_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=2825,
        n_oracle_eids=1739,
        n_effects=839,
        raw_score=0.413,
        effect_source_pathology_counts={"nonstructural_root_gap": 836, "__none__": 3},
    )

    assert comparison == "commensurable"
    assert is_core_uk_comparison(comparison) is True


def test_classify_uk_replay_residual_requires_replay_adjudication_for_proved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=[],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_text_match_missing_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_text_match_missing"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_missing_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_already_rewritten_text_match_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_text_match_already_rewritten"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_already_rewritten_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_text_patch_preimage_drift_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_text_patch_preimage_drift"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_patch_preimage_drift_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_heading_text_preimage_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-9"],
        only_in_oracle=["section-9"],
        adjudication_kinds=["uk_replay_heading_text_preimage_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_heading_text_preimage_gap_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_text_insert_anchor_preimage_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=[],
        only_in_oracle=["section-14-5-b"],
        adjudication_kinds=["uk_replay_text_insert_anchor_preimage_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_insert_anchor_preimage_gap_oracle_only_residual_eids"


def test_classify_uk_replay_residual_demotes_text_monetary_amount_preimage_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=[],
        only_in_oracle=["section-4-1"],
        adjudication_kinds=["uk_replay_text_monetary_amount_preimage_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_monetary_amount_preimage_gap_oracle_only_residual_eids"


def test_classify_uk_replay_residual_demotes_parenthetical_omission_preimage_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-9-1"],
        only_in_oracle=["section-9-2"],
        adjudication_kinds=["uk_replay_text_parenthetical_omission_preimage_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_parenthetical_omission_preimage_gap_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_citation_connector_surface_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-39-1-b"],
        only_in_oracle=[],
        adjudication_kinds=["uk_replay_text_match_citation_connector_surface_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_citation_connector_surface_gap_replay_only_residual_eids"


def test_classify_uk_replay_residual_demotes_article_phrase_surface_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=[],
        only_in_oracle=["section-57-3-a"],
        adjudication_kinds=["uk_replay_text_match_article_phrase_surface_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_article_phrase_surface_gap_oracle_only_residual_eids"


def test_classify_uk_replay_residual_demotes_multi_prior_text_patch_preimage_drift_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_patch_preimage_drift_multi_prior_same_target"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_patch_preimage_drift_multi_prior_same_target"


def test_classify_uk_replay_residual_demotes_synthetic_text_selector_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_match_synthetic_selector_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_synthetic_selector_gap"


def test_classify_uk_replay_residual_demotes_text_target_empty_surface_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1-1"],
        only_in_oracle=["section-1-1"],
        adjudication_kinds=["uk_replay_text_target_empty_surface_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_target_empty_surface_gap"


def test_classify_uk_replay_residual_demotes_normalized_preimage_text_match_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_match_normalized_preimage_present_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_normalized_preimage_present_gap"


def test_classify_uk_replay_residual_demotes_citation_tail_surface_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_match_citation_tail_surface_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_citation_tail_surface_gap"


def test_classify_uk_replay_residual_demotes_non_substantive_text_selector_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_match_non_substantive_selector_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_non_substantive_selector_gap"


def test_classify_uk_replay_residual_demotes_multi_fragment_text_selector_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1"],
        adjudication_kinds=["uk_replay_text_match_multi_fragment_selector_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_multi_fragment_selector_gap"


def test_classify_uk_replay_residual_demotes_empty_schedule_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_empty_schedule_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_empty_schedule_shape_gap"


def test_classify_uk_replay_residual_demotes_absent_sibling_range_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-10-1", "section-10-3"],
        only_in_oracle=["section-10-2"],
        adjudication_kinds=["uk_replay_absent_sibling_range_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_absent_sibling_range_gap"


def test_classify_uk_replay_residual_demotes_heading_facet_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-38"],
        only_in_oracle=["section-38"],
        adjudication_kinds=["uk_replay_heading_facet_target_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_heading_facet_target_gap"


def test_classify_uk_replay_residual_demotes_missing_sectionlike_range_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-6b", "section-7"],
        only_in_oracle=["section-6c"],
        adjudication_kinds=["uk_replay_missing_sectionlike_range_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_missing_sectionlike_range_gap"


def test_classify_uk_replay_residual_demotes_missing_schedule_branch_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1"],
        only_in_oracle=["schedule-2-paragraph-1"],
        adjudication_kinds=["uk_replay_missing_schedule_branch_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_missing_schedule_branch_gap"


def test_classify_uk_replay_residual_demotes_missing_schedule_range_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1", "schedule-3"],
        only_in_oracle=["schedule-2"],
        adjudication_kinds=["uk_replay_missing_schedule_range_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_missing_schedule_range_gap"


def test_classify_uk_replay_residual_demotes_missing_parent_grandparent_present_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1-9-a"],
        adjudication_kinds=["uk_replay_missing_parent_grandparent_present_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_missing_parent_grandparent_present_gap"


def test_classify_uk_replay_residual_demotes_missing_root_parent_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["body"],
        only_in_oracle=["section-9-1"],
        adjudication_kinds=["uk_replay_missing_root_parent_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_missing_root_parent_shape_gap"


def test_classify_uk_replay_residual_demotes_existing_target_conflict_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_existing_target_conflict_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_existing_target_conflict_gap"


def test_classify_uk_replay_residual_demotes_broad_schedule_table_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-2"],
        only_in_oracle=["schedule-2-paragraph-4"],
        adjudication_kinds=["uk_replay_broad_schedule_table_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_broad_schedule_table_shape_gap"


def test_classify_uk_replay_residual_demotes_broad_schedule_part_table_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-2-part-3"],
        only_in_oracle=["schedule-2-part-3-table-1"],
        adjudication_kinds=["uk_replay_broad_schedule_part_table_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_broad_schedule_part_table_shape_gap"


def test_classify_uk_replay_residual_demotes_schedule_partition_target_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-2"],
        only_in_oracle=["schedule-2-part-2-paragraph-80"],
        adjudication_kinds=["uk_replay_schedule_partition_target_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_partition_target_gap"


def test_classify_uk_replay_residual_demotes_schedule_partition_part_target_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-2-part-2"],
        only_in_oracle=["schedule-2-part-2-paragraph-80"],
        adjudication_kinds=["uk_replay_schedule_partition_part_target_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_partition_part_target_gap"


def test_classify_uk_replay_residual_demotes_schedule_paragraph_carrier_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1-paragraph-1"],
        only_in_oracle=["schedule-1-paragraph-1-3a"],
        adjudication_kinds=["uk_replay_schedule_paragraph_carrier_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_paragraph_carrier_gap"


def test_classify_uk_replay_residual_demotes_schedule_p1group_wrapper_carrier_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1-paragraph-1"],
        only_in_oracle=["schedule-1-paragraph-1-3a"],
        adjudication_kinds=["uk_replay_schedule_p1group_wrapper_carrier_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_p1group_wrapper_carrier_gap"


def test_classify_uk_replay_residual_demotes_annex_schedule_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["body"],
        only_in_oracle=["schedule-1"],
        adjudication_kinds=["uk_replay_annex_schedule_reference_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_annex_schedule_reference_gap"


def test_classify_uk_replay_residual_demotes_definition_entry_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1-definition-1"],
        adjudication_kinds=["uk_replay_definition_entry_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_definition_entry_shape_gap"


def test_classify_uk_replay_residual_demotes_repeated_form_label_payload_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-5a-paragraph-4-a"],
        only_in_oracle=["schedule-5a-paragraph-wrapper"],
        adjudication_kinds=["uk_replay_repeated_form_label_payload_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_repeated_form_label_payload_shape_gap"


def test_classify_uk_replay_residual_demotes_definition_child_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-1"],
        only_in_oracle=["section-1-definition-child-d"],
        adjudication_kinds=["uk_replay_definition_child_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_definition_child_shape_gap"


def test_classify_uk_replay_residual_demotes_schedule_container_text_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1"],
        only_in_oracle=["schedule-1-paragraph-2"],
        adjudication_kinds=["uk_replay_schedule_container_text_target_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_container_text_target_gap"


def test_classify_uk_replay_residual_demotes_schedule_unlabeled_paragraph_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["schedule-1-paragraph"],
        only_in_oracle=["schedule-1-paragraph-2"],
        adjudication_kinds=["uk_replay_schedule_unlabeled_paragraph_target_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_schedule_unlabeled_paragraph_target_gap"


def test_classify_uk_replay_residual_demotes_subsection_descendant_collapse_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-48"],
        only_in_oracle=["section-48-1-a"],
        adjudication_kinds=["uk_replay_subsection_descendant_target_collapse_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_subsection_descendant_target_collapse_gap"


def test_classify_uk_replay_residual_demotes_malformed_target_splits_to_unresolved() -> None:
    cases = {
        "uk_replay_malformed_target_placeholder_label_gap": "uk_malformed_target_placeholder_label_gap",
        "uk_replay_malformed_target_note_or_crossheading_gap": "uk_malformed_target_note_or_crossheading_gap",
        "uk_replay_malformed_target_sectionlike_label_gap": "uk_malformed_target_sectionlike_label_gap",
        "uk_replay_malformed_target_schedule_root_label_gap": "uk_malformed_target_schedule_root_label_gap",
        "uk_replay_malformed_target_granularity_collapse_gap": "uk_malformed_target_granularity_collapse_gap",
    }

    for adjudication_kind, expected_kind in cases.items():
        tier, kind = classify_uk_replay_residual(
            only_in_replayed=["section-1"],
            only_in_oracle=["section-1-a"],
            adjudication_kinds=[adjudication_kind],
        )

        assert tier == "UNRESOLVED"
        assert kind == expected_kind


def test_classify_uk_replay_residual_demotes_replace_payload_target_leaf_mismatch_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-26d-4-b"],
        only_in_oracle=["section-26d-4-b-bb"],
        adjudication_kinds=["uk_replay_replace_payload_target_leaf_mismatch_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_replace_payload_target_leaf_mismatch_gap"


def test_classify_uk_replay_residual_promotes_target_not_found_to_specific_proof() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_target_not_found"],
    )

    assert tier == "PROVED_REPLAY_BUG"
    assert kind == "uk_replay_target_not_found"


def test_classify_uk_replay_residual_prefers_payload_over_text_family() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=[
            "uk_replay_text_match_missing",
            "uk_replay_payload_mismatch",
        ],
    )

    assert tier == "PROVED_REPLAY_BUG"
    assert kind == "uk_replay_payload_mismatch"


def test_classify_uk_replay_residual_nonblocking_observations_do_not_prove_bug() -> None:
    for adjudication_kind in sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS:
        tier, kind = classify_uk_replay_residual(
            only_in_replayed=["section-3"],
            only_in_oracle=[],
            adjudication_kinds=[adjudication_kind],
        )

        assert tier == "UNRESOLVED"
        assert kind == "uk_replay_only_residual_eids"


def test_classify_uk_replay_residual_nonblocking_observations_without_residuals() -> None:
    for adjudication_kind in sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS:
        tier, kind = classify_uk_replay_residual(
            only_in_replayed=[],
            only_in_oracle=[],
            adjudication_kinds=[adjudication_kind],
        )

        assert tier == "UNRESOLVED"
        assert kind == "no_strong_claim"


def test_classify_uk_effect_missing_extracted_source() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag=None,
        extracted_text="",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
    )

    assert pathology == "missing_extracted_source"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_empty_nonstructural_source_as_nonstructural_gap() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag=None,
        extracted_text="",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="coming into force",
        is_structural=False,
    )

    assert pathology == "nonstructural_root_gap"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_marks_commencement_out_of_scope() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "2 Section 80 of the Transport (Scotland) Act 2001 shall come "
            "into force on 1st May 2001."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_commencement_source_rejected"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "commencement_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_marks_application_payload_out_of_scope() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "5 Where the proposed scheme specifies existing facilities the "
            "authority shall inform operators when those facilities were provided."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_application_modification_payload_rejected"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_modification_payload_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_manual_compile_frontier_marks_heading_facets_manual() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text='In the title to section 10, for "old" substitute "new".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_heading_facet_candidate"


def test_classify_uk_manual_compile_frontier_requires_source_witness_for_heading() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="missing_extracted_source",
        extracted_tag="",
        extracted_text="",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_missing_payload_source_insufficient"


def test_classify_uk_manual_compile_frontier_source_pathology_blocks_heading_claim() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="fragment_context_missing",
        extracted_tag="P1",
        extracted_text='"old" substitute "new"',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_broad_schedule_flat_payload_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="substituted",
        source_pathology="broad_schedule_flat_payload_unsupported",
        extracted_tag="BlockAmendment",
        extracted_text=(
            "6 Recovery of grants from voluntary organisations "
            "Expenditure on grants to voluntary organisations"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_broad_schedule_flat_payload_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_temporary_as_if_word_omission_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="temporary_as_if_word_omission_unsupported",
        extracted_tag="P1",
        extracted_text=(
            "Section 11(9) shall have effect in relation to the financial year "
            "as if the words use of resources and were omitted."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_empty_type_as_if_words_omitted_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_reference_only_fragment_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="reference_only_source_fragment",
        extracted_tag="P3",
        extracted_text="i paragraph 1(1);",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_payload_fragment_without_formula_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="payload_fragment_without_action_formula",
        extracted_tag="BlockAmendment",
        extracted_text="; or d in the Land Register of Scotland or in- i a land certificate; then",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_structured_payload_fragment_manual_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="fragment_context_missing",
        extracted_tag="BlockAmendment",
        extracted_text=(
            "i where that individual is a member of a police force, a police force; or "
            "ii where that individual is a police member of the Scottish Crime and Drug "
            "Enforcement Agency, that Agency,"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_source_carried_structured_text_patch_candidate"
    )


def test_classify_uk_manual_compile_frontier_does_not_promote_unstructured_payload_fragment() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="fragment_context_missing",
        extracted_tag="BlockAmendment",
        extracted_text="old expression",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_source_pathology_insufficient"


def test_classify_uk_manual_compile_frontier_marks_source_carried_multi_subunit_text_rewrite() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words repealed",
        source_pathology="source_carried_multi_subunit_text_rewrite_unsupported",
        extracted_tag="P2",
        extracted_text=(
            "the words “mental disorder”, where they occur in subsections (1) and (2), "
            "are repealed."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate"
    )


def test_classify_uk_manual_compile_frontier_marks_source_carried_child_tail_text_rewrite() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words repealed",
        source_pathology="source_carried_child_tail_text_rewrite_unsupported",
        extracted_tag="P3",
        extracted_text="the words following paragraph (b) are repealed",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate"
    )


def test_classify_uk_manual_compile_frontier_keeps_out_of_scope_heading_non_manual() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="",
        extracted_tag="P1",
        extracted_text='In the title to section 10, for "old" substitute "new".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=False,
        structural_for_replay=False,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_non_textual_or_out_of_scope"


def test_classify_uk_manual_compile_frontier_marks_missing_payload_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="inserted",
        source_pathology="missing_extracted_source",
        extracted_tag="",
        extracted_text="",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_missing_structural_payload_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_missing_payload_source_insufficient"


def test_classify_uk_manual_compile_frontier_prefers_deterministic_parser_work() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='For "old" substitute "new".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_parser_or_extraction_candidate"


def test_classify_uk_manual_compile_frontier_accepts_dash_punctuated_instruction() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text='After paragraph (a) insert— "new text".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_structural_sibling_insert_candidate"


def test_classify_uk_manual_compile_frontier_marks_appropriate_place_manual() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='at the appropriate place, insert— "Windsor Framework" means ...;',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_appropriate_place_candidate"


def test_classify_uk_manual_compile_frontier_marks_appropriate_place_source_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="appropriate_place_insert_unsupported",
        extracted_tag="P3",
        extracted_text='at the appropriate place, insert— "Windsor Framework" means ...;',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_appropriate_place_candidate"


def test_classify_uk_manual_compile_frontier_marks_appropriate_place_definition_entry() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="appropriate_place_definition_entry_insert_unsupported",
        extracted_tag="P4",
        extracted_text=(
            'iii at the appropriate place insert— "operational service standard" '
            "is to be construed in accordance with section 3C(1)(b),"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_appropriate_place_definition_entry_candidate"


def test_classify_uk_manual_compile_frontier_marks_schedule_list_entry_manual() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="schedule_list_entry_target_unsupported",
        extracted_tag="P3",
        extracted_text=(
            "after the entry relating to the Scottish Legal Aid Board "
            "insert- The Scottish Legal Complaints Commission"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_schedule_list_entry_candidate"


def test_classify_uk_manual_compile_frontier_marks_schedule_list_entry_before_generic_parser() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text=(
            "Schedule 3 is amended by the insertion, after the entry for "
            "Scottish Legal Aid Board, of The Scottish Legal Complaints Commission."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_schedule_list_entry_candidate"


def test_classify_uk_manual_compile_frontier_marks_structural_sibling_insert_before_generic_parser() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text=(
            "after paragraph (c) insert- d an appointment under paragraph 2(1); "
            "e an appointment under paragraph 3(1)."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_structural_sibling_insert_candidate"


def test_classify_uk_manual_compile_frontier_marks_amendment_program_target() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="amendment_text_target_unsupported",
        extracted_tag="P3",
        extracted_text="for the inserted text substitute- aa its chief executive;",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_amendment_program_target_candidate"


def test_classify_uk_manual_compile_frontier_marks_table_entry_target() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="table_entry_target_unsupported",
        extracted_tag="P4",
        extracted_text="after the third entry in the second column insert- Functions under Chapter 1A",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_candidate"


def test_classify_uk_manual_compile_frontier_marks_repeal_schedule_table_source() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words repealed",
        source_pathology="repeal_schedule_table_source_unsupported",
        extracted_tag="Part",
        extracted_text="PART 1 Repeals Enactment Extent of repeal Police Act The whole Act except section 1.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_repeal_table_candidate"


def test_classify_uk_manual_compile_frontier_marks_as_if_application_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="as_if_application_modification_unsupported",
        extracted_tag="P1",
        extracted_text="Section 19(3) shall have effect as if the period were 1 month.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_lowering_no_supported_action_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_as_if_application_modification_out_of_scope"


def test_classify_uk_manual_compile_frontier_marks_commencement_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="commencement_effect_out_of_scope",
        extracted_tag="P2",
        extracted_text=(
            "The provisions specified in the Schedule shall come into force "
            "on 1st April 2001."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_lowering_no_supported_action_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_commencement_effect_out_of_scope"


def test_classify_uk_manual_compile_frontier_marks_application_payload_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="application_modification_payload_out_of_scope",
        extracted_tag="BlockAmendment",
        extracted_text="5 Where the proposed scheme specifies existing facilities ...",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_application_modification_payload_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_application_modification_payload_out_of_scope"


def test_classify_uk_manual_compile_frontier_marks_schedule_note_target() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="substituted",
        source_pathology="schedule_note_target_unsupported",
        extracted_tag="BlockAmendment",
        extracted_text="1 In the case of a conservation body, insert the year and number.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_schedule_note_target_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_schedule_note_candidate"


def test_classify_uk_manual_compile_frontier_marks_heading_facet_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="heading_facet_target_unsupported",
        extracted_tag="P3",
        extracted_text='in the heading, for "old" substitute "new".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_heading_facet_candidate"


def test_classify_uk_manual_compile_frontier_marks_crossheading_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="crossheading_target_unsupported",
        extracted_tag="P2",
        extracted_text='In the italic heading before paragraph 14, for "old" substitute "new".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_crossheading_replace_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_crossheading_candidate"


def test_classify_uk_manual_compile_frontier_treats_record_disposition_as_nonblocking() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="inserted",
        source_pathology="",
        extracted_tag="P1",
        extracted_text="Inserted payload.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_lowering_observed",
                "strict_disposition": "record",
            },
        ),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_supported"
    assert result["rule_id"] == "uk_manual_frontier_deterministic_supported"


def test_classify_uk_manual_compile_frontier_marks_text_patch_preimage_chain_gap() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="word substituted",
        source_pathology="",
        extracted_tag="P3",
        extracted_text='in section 3(a), for "old amount" substitute "new amount";',
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="text_patch_preimage_absent_from_target_surfaces",
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_text_patch_preimage_chain_gap"


def test_classify_uk_effect_compare_shape_marks_table_cell_surface_gap() -> None:
    result = classify_uk_effect_compare_shape(
        effect_type="word substituted",
        op_actions=("text_replace",),
        resolver_eids=("schedule-1",),
        base_target_hits=(True,),
        oracle_target_hits=(True,),
        base_target_texts=("SCHEDULE 1 Budget amounts",),
        oracle_target_texts=("SCHEDULE 1 Budget amounts",),
        text_patch_matches=("£56,340,000",),
        text_patch_replacements=("£76,340,000",),
        lowering_rule_ids=("uk_effect_table_column_text_patch",),
    )

    assert result == "table_cell_text_patch_requires_table_surface"
    assert not is_core_uk_effect_compare_candidate(result)


def test_manual_frontier_keeps_owned_table_cell_surface_gap_deterministic() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="word substituted",
        source_pathology="",
        extracted_tag="P4",
        extracted_text='in column 4, for "£56,340,000" there is substituted "£76,340,000"',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_table_column_text_patch",
                "blocking": False,
            },
        ),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="table_cell_text_patch_requires_table_surface",
    )

    assert result["status"] == "deterministic_frontend_supported"
    assert result["rule_id"] == "uk_manual_frontier_deterministic_supported"


def test_classify_uk_manual_compile_frontier_marks_range_to_container_target_absent() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="substituted for ss. 3-12 and cross-heading",
        source_pathology="",
        extracted_tag="P2",
        extracted_text="For sections 3 to 12 substitute Chapter 1 ...",
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="range_to_container_target_absent",
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_range_to_container_candidate"


def test_classify_uk_manual_compile_frontier_marks_range_to_container_source_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="substituted for ss. 3-12 and cross-heading",
        source_pathology="range_to_container_target_unsupported",
        extracted_tag="P2",
        extracted_text="For sections 3 to 12 substitute Chapter 1 ...",
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_range_to_container_candidate"


def test_classify_uk_manual_compile_frontier_marks_naked_payload_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="",
        extracted_tag="BlockAmendment",
        extracted_text="1 A payload fragment with no operative verb.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_lowering_no_supported_action_rejected",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_payload_without_action_source_insufficient"


def test_classify_uk_manual_compile_frontier_keeps_unsupported_family_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="applied",
        source_pathology="",
        extracted_tag="P1",
        extracted_text="This provision is applied.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_lowering_no_supported_action_rejected",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_unsupported_effect_family"


def test_classify_uk_manual_compile_frontier_preserves_unclassified_rows() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="substituted",
        source_pathology="",
        extracted_tag="P1",
        extracted_text="For X substitute Y.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_new_unclassified_blocker",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "unclassified_frontier"
    assert result["rule_id"] == "uk_manual_frontier_unclassified"
    assert "inspect the source and lowering evidence" in result["reason"]


def test_classify_uk_effect_unhandled_instruction_text_without_ops() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text='In subsection (1A), at the end insert "(subject to section 33A)".',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
    )

    assert pathology == "unhandled_instruction_text"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_appropriate_place_insert_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P4",
        extracted_text=(
            "viii at the appropriate place insert- "
            '"Scottish Ministers" and "local authorities".'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "appropriate_place_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_appropriate_place_definition_entry_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P4",
        extracted_text=(
            "iii at the appropriate place insert- "
            '"operational service standard" is to be construed in accordance with section 3C(1)(b),'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "appropriate_place_definition_entry_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_appropriate_place_alphabetical_insert_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b at an appropriate place, in alphabetical order, insert- "
            '" Healthcare Improvement Scotland ".'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "appropriate_place_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_for_entry_relating_to_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "4 In schedule 3, for the entry relating to The Trustees of the "
            'National Library of Scotland substitute " The National Library of Scotland ".'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "schedule_list_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_entry_relation_to_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text=(
            "1 In schedule 3, after the entry relation to the Scottish Fiscal "
            'Commission insert- "The Scottish Food Commission".'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "schedule_list_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_omit_entry_for_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text='a omit the entry for "NHS Health Scotland", and',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words omitted",
        is_structural=True,
    )

    assert pathology == "schedule_list_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_as_if_application_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "3 Until 1st April 2000, section 13 of the Act shall have effect "
            "subject to the following provisions. References to Audit Scotland "
            "shall be read as if they were references to the Parliamentary corporation."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "as_if_application_modification_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_naked_block_amendment_payload_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "6 Expressions used in subsection (1) and in the Regulation of Care "
            "(Scotland) Act 2001 have the same meanings in that subsection as in that Act."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_lowering_no_supported_action_rejected"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "payload_fragment_without_action_formula"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_repeal_schedule_table_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="Part",
        extracted_text=(
            "PART 1 Repeals Enactment Extent of repeal "
            "Police (Scotland) Act 1967 The whole Act except for sections 32A and 42."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words repealed",
        is_structural=True,
    )

    assert pathology == "repeal_schedule_table_source_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_payload_fragment_without_action_formula() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "; or d in the Land Register of Scotland or in- "
            "i a land certificate; ii a charge certificate; then"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "payload_fragment_without_action_formula"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_payload_fragment_without_action_formula_block_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "the Parliamentary corporation- a after consulting such association of councils; "
            "and b with the agreement of the Parliament."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "payload_fragment_without_action_formula"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_carried_multi_subunit_text_rewrite() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text=(
            "6 In section 22, the words "
            "“(in a case where the incapacity of the granter is by reason of mental disorder)”, "
            "where they occur in subsections (1) and (2), are repealed."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words repealed",
        is_structural=True,
    )

    assert pathology == "source_carried_multi_subunit_text_rewrite_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_carried_child_tail_text_rewrite() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="a in subsection (5), the words following paragraph (b) are repealed; and",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words repealed",
        is_structural=True,
    )

    assert pathology == "source_carried_child_tail_text_rewrite_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_carried_child_tail_substitution() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "a in subsection (1), for the words after paragraph (b) substitute "
            "for a term exceeding the applicable limit in respect of any one offence;"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "source_carried_child_tail_text_rewrite_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_amendment_inserted_text_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "in paragraph 17, in sub-paragraph (a), for the inserted text "
            "substitute- aa its chief executive;"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:5/paragraph:17/item:a"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "amendment_text_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_table_entry_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P4",
        extracted_text=(
            "after the third entry in the second column relating to the Auditor "
            "General for Wales insert- Functions under Chapter 1A"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:159/subsection:5"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "table_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_table_entry_target_without_column() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "In section 166(5), after entry 4 in the table insert- "
            "4A a serious terrorism sentence of detention"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:166/subsection:5"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "table_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_table_target_deictic_entry_insert() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "after that entry insert- electronic whereabouts monitoring "
            "requirement Part 17 section 185(5)."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:174/subsection:1/table"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "table_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_manual_frontier_overlap_table_entry_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text="after that entry insert- electronic whereabouts monitoring requirement.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
                "original_affected_provisions": "s. 174(1) Table",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_candidate"


def test_classify_uk_effect_table_entry_added_or_amended_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text=(
            "In schedule 1, in column 1, in entry number 8, at the end there is added "
            "external relations initiatives; and the amounts specified in columns 2 "
            "and 4 are amended in accordance with Schedule 1."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:1"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "table_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_as_if_application_modification() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "Section 19(3) of the 2000 Act shall have effect as if the period "
            "specified in paragraph (b) of that subsection were 1 month instead of 12 months."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:19/subsection:3/paragraph:b"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "as_if_application_modification_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_broad_schedule_flat_payload_rejected() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "6 Recovery of grants from voluntary organisations "
            "Expenditure on grants to voluntary organisations"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:2"],
        lowering_rule_ids=["uk_effect_broad_schedule_flat_payload_rejected"],
        effect_type="substituted",
        is_structural=True,
    )

    assert pathology == "broad_schedule_flat_payload_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_empty_type_as_if_word_omission_rejected() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "Section 11(9) shall have effect in relation to the financial year "
            "as if the words use of resources and were omitted."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:11"],
        lowering_rule_ids=["uk_effect_empty_type_as_if_words_omitted_rejected"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "temporary_as_if_word_omission_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_schedule_list_entry_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "before the entry relating to Scottish Children's Reporter Administration "
            "insert- The Scottish Charity Regulator"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:3"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "schedule_list_entry_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_structural_sibling_insert_instruction() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "after paragraph (c) insert- d an appointment under paragraph 2(1); "
            "e an appointment under paragraph 3(1)."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:221/subsection:2"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "structural_sibling_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_deictic_structural_sibling_insert_instruction() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b after that paragraph, insert- aa the Shetland Transport Partnership; "
            "ab the South-West of Scotland Transport Partnership; ,"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:82/subsection:1"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "structural_sibling_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_at_end_child_dash_block_insert_as_structural_sibling() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "c at the end of paragraph (b), insert- ; or "
            "c the West of Scotland Transport Partnership; ."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:82/subsection:1"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "structural_sibling_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_before_child_block_insert_as_structural_sibling() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b in sub-paragraph (3)(a), in the inserted paragraph (d), "
            "before sub-paragraph (i) insert- ai the community order does not qualify."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:22/paragraph:21/subparagraph:3/item:a"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "structural_sibling_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_heading_facet_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text='in the heading, for "old" substitute "new".',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:11/heading"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "heading_facet_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_crossheading_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text='In the italic heading before paragraph 14, for "old" substitute "new".',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:1/paragraph:14/cross-heading"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "crossheading_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_only_source_fragment() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P4",
        extracted_text="i section 206 (strategies),",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:206"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "reference_only_source_fragment"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_only_short_title_fragment() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text="v Enterprise Act 2002",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:9/paragraph:5/subparagraph:3"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "reference_only_source_fragment"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_fragment_context_missing() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text="“ elderly person ” means a person who has attained the age of 60 years,",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:146"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "fragment_context_missing"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_instruction_text_reused_as_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="b after that subsection insert the subsections set out in subsection (2).",
        op_actions=["insert", "insert"],
        payload_kinds=["subsection", "subsection"],
        payload_texts=[
            "b after that subsection insert the subsections set out in subsection (2).",
            "b after that subsection insert the subsections set out in subsection (2).",
        ],
    )

    assert pathology == "instruction_text_reused_as_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_broad_source_reused_as_payload() -> None:
    broad_schedule = (
        "SCHEDULE 2 REPEALS AND REVOCATIONS Article 3(2) Reference Short title "
        "or title Extent of repeal or revocation 1863 c. 112 . The Telegraph Act "
        "1863. Section 45. 1868 c. 110 . The Telegraph Act 1868. Section 20."
    )
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="Schedule",
        extracted_text=broad_schedule,
        op_actions=["repeal"],
        payload_kinds=["schedule"],
        payload_texts=[""],
    )

    assert pathology == "broad_source_reused_as_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_misselected_target_context() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text='4 In subsection (3) for "and on the authority" substitute "..., the authority..."',
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:21f"],
        effect_type="inserted",
        is_structural=True,
    )

    assert pathology == "misselected_target_context"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_scoped_definition_child_omission_is_not_misselected() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="a in paragraph (a), omit “or”,",
        op_actions=["text_repeal"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:82/subsection:1"],
        lowering_rule_ids=["uk_effect_source_carried_definition_child_text_omission_text_patch"],
        effect_type="word omitted",
        is_structural=True,
    )

    assert pathology == ""


def test_classify_uk_effect_schedule_paragraph_context_is_not_misselected() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b in paragraph 3, for “at the time of” substitute "
            "“ throughout the period of 12 months ending with ” ,"
        ),
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:3/paragraph:3"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


def test_classify_uk_effect_range_to_container_target_unsupported() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text="For sections 3 to 12 substitute Chapter 1 Bus services improvement partnerships.",
        op_actions=["replace"],
        payload_kinds=["chapter"],
        payload_texts=["Chapter 1 Bus services improvement partnerships"],
        target_paths=["part:2/chapter:1"],
        effect_type="substituted for ss. 3-12 and cross-heading",
        is_structural=True,
    )

    assert pathology == "range_to_container_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_nonstructural_root_gap() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "1 An assistant to the executive of a local authority is entitled to attend, "
            "and speak at, any meeting of the executive or of a committee of the executive. 2"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=[],
        effect_type="words in Sch. 1 para. 5 renumbered as Sch. 1 para. 5(2)",
        is_structural=False,
    )

    assert pathology == "nonstructural_root_gap"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_non_substantive_shell_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text="2 . . . . . . . . . . . . . . . . . . . . .",
        op_actions=["insert"],
        payload_kinds=["schedule"],
        payload_texts=["2 . . . . . . . . . . . . . . . . . . . . ."],
        target_paths=["schedule:a1"],
        effect_type="inserted",
        is_structural=True,
    )

    assert pathology == "non_substantive_shell_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_non_substantive_shell_payload_with_leading_label() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="b . . . . . . . . . . . . . . . . . . . . .",
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:10/paragraph:3/subparagraph:1"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "non_substantive_shell_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_collapsed_subtree_oracle_shape() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-51-6"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=True,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_legacy_labeled_oracle_shape() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="substituted for s. 72(4)(c)-(e) and word",
        op_actions=["replace", "replace"],
        payload_texts=[],
        resolver_eids=["section-72-4-ba", "section-72-4-bb"],
        base_target_hits=[False, False],
        oracle_target_hits=[False, False],
        base_descendant_hits=[False, False],
        oracle_descendant_hits=[False, False],
        base_parent_hits=[True, True],
        oracle_parent_hits=[True, True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "legacy_labeled_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_range_to_container_target_absent() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="substituted for ss. 3-12 and cross-heading",
        op_actions=["replace"],
        payload_texts=["Chapter 1 Bus services improvement partnerships"],
        resolver_eids=[],
        base_target_hits=[],
        oracle_target_hits=[],
        base_descendant_hits=[],
        oracle_descendant_hits=[],
        base_parent_hits=[],
        oracle_parent_hits=[],
        base_target_texts=[],
        oracle_target_texts=[],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "range_to_container_target_absent"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_retained_repeal_oracle_branch() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="repealed",
        op_actions=["repeal", "repeal"],
        payload_texts=[],
        resolver_eids=["section-3", "section-4"],
        base_target_hits=[True, True],
        oracle_target_hits=[True, True],
        base_descendant_hits=[True, True],
        oracle_descendant_hits=[True, True],
        base_parent_hits=[False, False],
        oracle_parent_hits=[False, False],
        base_target_texts=[],
        oracle_target_texts=[],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "retained_repeal_oracle_branch"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_inserted_child_collapsed_into_oracle_parent() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["aa remuneration allowed to an employee by his employer"],
        resolver_eids=["schedule-7-paragraph-4-1-aa"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["1 none of the following shall be regarded as a donation"],
        oracle_parent_texts=[
            "1 none of the following shall be regarded as a donation aa remuneration allowed to an employee by his employer"
        ],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_compare_shape_collapse_does_not_become_source_pathology() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["aa remuneration allowed to an employee by his employer"],
        resolver_eids=["schedule-7-paragraph-4-1-aa"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["1 none of the following shall be regarded as a donation"],
        oracle_parent_texts=[
            "1 none of the following shall be regarded as a donation aa remuneration allowed to an employee by his employer"
        ],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )
    source_pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text="aa remuneration allowed to an employee by his employer",
        op_actions=["insert"],
        payload_kinds=["subparagraph"],
        payload_texts=["aa remuneration allowed to an employee by his employer"],
        target_paths=["schedule:7/paragraph:4/subparagraph:1/item:aa"],
        effect_type="inserted",
        is_structural=True,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False
    assert source_pathology == ""
    assert is_core_uk_effect_source_candidate(source_pathology) is True


def test_compare_shape_existing_parent_payload_is_not_oracle_collapse() -> None:
    payload_text = "aa remuneration allowed to an employee by his employer"
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=[payload_text],
        resolver_eids=["schedule-7-paragraph-4-1-aa"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=[
            "1 none of the following shall be regarded as a donation "
            "aa remuneration allowed to an employee by his employer"
        ],
        oracle_parent_texts=[
            "1 none of the following shall be regarded as a donation "
            "aa remuneration allowed to an employee by his employer"
        ],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )
    source_pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=payload_text,
        op_actions=["insert"],
        payload_kinds=["subparagraph"],
        payload_texts=[payload_text],
        target_paths=["schedule:7/paragraph:4/subparagraph:1/item:aa"],
        effect_type="inserted",
        is_structural=True,
    )

    assert compare_shape == ""
    assert is_core_uk_effect_compare_candidate(compare_shape) is True
    assert source_pathology == ""
    assert is_core_uk_effect_source_candidate(source_pathology) is True


def test_classify_uk_effect_text_replace_with_oracle_only_descendants() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-28-1"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[True],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=True,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_patch_preimage_absent_from_target_surfaces() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["schedule-3-paragraph-2-2"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=["The period is 3 months."],
        oracle_target_texts=["The period is 3 months."],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["6"],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "text_patch_preimage_absent_from_target_surfaces"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_patch_preimage_consumed_by_replay_chain_stays_core() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-7a-6"],
        base_target_hits=[False],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=[],
        oracle_target_texts=["NHS England may publish information."],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["the Information Centre"],
        text_patch_replacements=["NHS England"],
        lowering_rule_ids=["uk_effect_wherever_occurring_substitution_text_patch"],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "uk_compare_text_patch_preimage_consumed_by_replay_chain"
    assert is_core_uk_effect_compare_candidate(compare_shape) is True


def test_classify_uk_effect_text_patch_preimage_consumed_requires_absent_base_target() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-7a-6"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=["NHS England may publish information."],
        oracle_target_texts=["NHS England may publish information."],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["the Information Centre"],
        text_patch_replacements=["NHS England"],
        lowering_rule_ids=["uk_effect_wherever_occurring_substitution_text_patch"],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "text_patch_preimage_absent_from_target_surfaces"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_patch_preimage_present_stays_core() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["schedule-3-paragraph-2-2"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=["The period is 6 months."],
        oracle_target_texts=["The period is 6 12 months."],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["6"],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == ""
    assert is_core_uk_effect_compare_candidate(compare_shape) is True


def test_classify_uk_effect_synthetic_text_patch_selector_stays_core() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words omitted",
        op_actions=["text_repeal"],
        payload_texts=[],
        resolver_eids=["section-1"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[False],
        oracle_parent_hits=[False],
        base_target_texts=["Opening words before the list."],
        oracle_target_texts=["Opening words before the list."],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["TEXT_FROM__TO_END"],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == ""
    assert is_core_uk_effect_compare_candidate(compare_shape) is True


def test_classify_uk_effect_inserted_alphanumeric_child_collapsed_into_oracle_parent() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["This subsection applies to any donation received from a trustee"],
        resolver_eids=["section-162-3a"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["162 1 for the purposes of this act ... 3 this subsection applies ... 6 in this section ..."],
        oracle_parent_texts=["1621for the purposes of this act ... 3this subsection applies ... 3athis subsection applies to any donation received from a trustee ... 6in this section ..."],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_gibraltar_insert_missing_from_main_oracle() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        affecting_title=(
            "The European Parliamentary Elections (Combined Region and Campaign "
            "Expenditure) (United Kingdom and Gibraltar) Order 2004"
        ),
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["3 Paragraphs 3 and 5 to 11 do not apply in relation to a recognised Gibraltar third party."],
        resolver_eids=["schedule-10-paragraph-1-3"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["1 In this Schedule... 2 For the purposes of this Schedule..."],
        oracle_parent_texts=["1 In this Schedule... 2 For the purposes of this Schedule..."],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "territorial_extension_oracle_gap"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_gibraltar_text_change_missing_from_main_oracle() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        affecting_title=(
            "The European Parliamentary Elections (Combined Region and Campaign "
            "Expenditure) (United Kingdom and Gibraltar) Order 2004"
        ),
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-162-4"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=[
            "4 for the purposes of subsection 3 the relevant information means ..."
        ],
        oracle_target_texts=[
            "4 for the purposes of subsection 3 the relevant information means ..."
        ],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "territorial_extension_oracle_gap"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_change_against_missing_oracle_branch() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["schedule-5-paragraph-3-2-a"],
        base_target_hits=[True],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[False],
        base_target_texts=["(a) the Commission must prepare a report."],
        oracle_target_texts=[],
        base_parent_texts=["(2) In this paragraph..."],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "oracle_missing_live_branch"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_inserted_wrapper_with_oracle_only_descendants() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["Attribution of expenditure to different parliamentary constituencies"],
        resolver_eids=["schedule-10-paragraph-2a"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[True],
        base_parent_hits=[False],
        oracle_parent_hits=[False],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "descendant_only_oracle_wrapper"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_substantive_block_payload_is_not_misselected_target_context() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "5C Sections 2(2A) and 21(1A) of, and paragraph 5C(1) of Schedule 2 to, "
            "the Local Government Act 1972 are not to be taken to indicate any contrary intention."
        ),
        op_actions=["replace"],
        payload_kinds=["subsection"],
        payload_texts=["Sections 2(2A) and 21(1A) ..."],
        target_paths=["section:39/subsection:5c"],
        effect_type="substituted for s. 39(5)",
        is_structural=False,
    )

    assert pathology == ""


def test_normalize_uk_replay_compare_eids_handles_case_only_alphanumeric_drift() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-10-paragraph-2a-2"},
        {"schedule-10-paragraph-2A-2"},
    )

    assert replayed == {"schedule-10-paragraph-2a-2"}
    assert oracle == {"schedule-10-paragraph-2a-2"}


def test_normalize_uk_replay_compare_eids_handles_source_container_ordinal_drift() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "part",
            "part-n2",
            "part-n2-chapter-1",
            "schedule",
            "schedule-paragraph-1",
            "schedule-n2-part-1",
        },
        {
            "part-1",
            "part-2",
            "part-2-chapter-1",
            "schedule-1",
            "schedule-1-paragraph-1",
            "schedule-2-part-1",
        },
    )

    assert replayed == oracle
    assert "part-2-chapter-1" in replayed
    assert "schedule-1-paragraph-1" in replayed


def test_normalize_uk_replay_compare_eids_applies_oracle_physical_parent_alias() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-5-4-aa"},
        {"section-5-1-aa"},
        oracle_physical_eid_aliases={"section-5-1-aa": "section-5-4-aa"},
    )

    assert replayed == {"section-5-4-aa"}
    assert oracle == {"section-5-4-aa"}


def test_normalize_uk_replay_compare_eids_drops_nonlegal_text_fragment_ids() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-1", "p00090", "p01890a"},
        {"section-1"},
    )

    assert replayed == {"section-1"}
    assert oracle == {"section-1"}


def test_classify_uk_current_projection_eid_shape_marks_spent_amending_act_surface() -> None:
    classification = classify_uk_current_projection_eid_shape(
        enacted_eids={
            "section-1",
            "section-1-1",
            "section-1-1-a",
            "section-1-1-b",
            "section-2",
            "section-2-1",
            "section-2-2",
            "section-3",
            "section-3-1",
            "section-3-2",
        },
        oracle_eids={"section-3", "section-3-1", "section-3-2"},
    )

    assert classification == "spent_amending_act_current_projection"
    assert is_core_uk_comparison(classification) is False


def test_classify_uk_current_projection_eid_shape_keeps_multi_root_oracle_core() -> None:
    assert (
        classify_uk_current_projection_eid_shape(
            enacted_eids={
                "section-1",
                "section-2",
                "section-3",
                "section-3-1",
                "section-4",
                "section-4-1",
            },
            oracle_eids={"section-3", "section-4"},
        )
        == ""
    )


def test_classify_uk_commencement_current_projection_marks_commenced_subset_surface() -> None:
    classification = classify_uk_commencement_current_projection(
        replay_compare_eids={
            "section-1",
            "section-1-1",
            "section-2",
            "section-2-1",
            "section-3",
            "section-3-1",
            "section-4",
            "section-4-1",
            "section-5",
            "section-5-1",
            "section-6",
            "section-6-1",
            "schedule-1",
            "schedule-1-paragraph-1",
            "schedule-1-paragraph-2",
            "schedule-2",
            "schedule-2-paragraph-1",
        },
        oracle_compare_eids={
            "section-1",
            "section-1-1",
            "section-2",
            "section-2-1",
            "section-3",
        },
        commenced_replay_eids={
            "section-1",
            "section-1-1",
            "section-2",
            "section-2-1",
        },
        commenced_oracle_eids={
            "section-1",
            "section-1-1",
            "section-2",
            "section-2-1",
        },
    )

    assert classification == "commencement_current_projection"
    assert is_core_uk_comparison(classification) is False


def test_classify_uk_commencement_current_projection_rejects_oracle_only_gap() -> None:
    assert (
        classify_uk_commencement_current_projection(
            replay_compare_eids={"section-1", "section-2"},
            oracle_compare_eids={"section-1", "section-3"},
            commenced_replay_eids={"section-1"},
            commenced_oracle_eids={"section-1"},
        )
        == ""
    )


def test_classify_uk_commencement_current_projection_rejects_commencement_disagreement() -> None:
    assert (
        classify_uk_commencement_current_projection(
            replay_compare_eids={
                "section-1",
                "section-2",
                "section-3",
                "section-4",
                "section-5",
                "section-6",
                "section-7",
                "section-8",
                "section-9",
                "section-10",
                "section-11",
                "section-12",
            },
            oracle_compare_eids={"section-1"},
            commenced_replay_eids={"section-1", "section-2"},
            commenced_oracle_eids={"section-1"},
        )
        == ""
    )


def test_normalize_uk_replay_compare_eids_drops_wrapper_with_oracle_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-10-paragraph-2a", "schedule-10-paragraph-2a-2"},
        {
            "schedule-10-paragraph-2A-1",
            "schedule-10-paragraph-2A-2",
            "schedule-10-paragraph-2A-3",
            "schedule-10-paragraph-2A-4",
        },
    )

    assert "schedule-10-paragraph-2a" not in replayed
    assert "schedule-10-paragraph-2a-2" in replayed
    assert "schedule-10-paragraph-2a-2" in oracle


def test_normalize_uk_replay_compare_eids_applies_visible_number_alias() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-2-paragraph-21za"},
        {"schedule-2-paragraph-21n1"},
        oracle_visible_number_eid_aliases={
            "schedule-2-paragraph-21n1": "schedule-2-paragraph-21za"
        },
    )

    assert replayed == {"schedule-2-paragraph-21za"}
    assert oracle == {"schedule-2-paragraph-21za"}


def test_normalize_uk_replay_compare_eids_drops_collapsed_section_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-142", "section-142-1", "section-142-2", "section-142-3"},
        {"section-142"},
    )

    assert replayed == {"section-142"}
    assert oracle == {"section-142"}


def test_normalize_uk_replay_compare_eids_keeps_single_collapsed_section_descendant() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-142", "section-142-1"},
        {"section-142"},
    )

    assert replayed == {"section-142", "section-142-1"}
    assert oracle == {"section-142"}


def test_normalize_uk_replay_compare_eids_keeps_descendants_when_oracle_has_children() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-142", "section-142-1", "section-142-2"},
        {"section-142", "section-142-1"},
    )

    assert replayed == {"section-142", "section-142-1", "section-142-2"}
    assert oracle == {"section-142", "section-142-1"}


def test_normalize_uk_replay_compare_eids_drops_collapsed_schedule_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-2",
            "schedule-2-paragraph-1",
            "schedule-2-paragraph-1-1",
            "schedule-2-paragraph-2",
        },
        {"schedule-2"},
    )

    assert replayed == {"schedule-2"}
    assert oracle == {"schedule-2"}


def test_normalize_uk_replay_compare_eids_keeps_schedule_descendants_when_oracle_has_children() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-2",
            "schedule-2-paragraph-1",
            "schedule-2-paragraph-1-1",
            "schedule-2-paragraph-2",
        },
        {"schedule-2", "schedule-2-paragraph-1"},
    )

    assert replayed == {
        "schedule-2",
        "schedule-2-paragraph-1",
        "schedule-2-paragraph-1-1",
        "schedule-2-paragraph-2",
    }
    assert oracle == {"schedule-2", "schedule-2-paragraph-1"}


def test_normalize_uk_replay_compare_eids_drops_collapsed_crossheading_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "crossheading-transport",
            "crossheading-transport-10",
            "crossheading-transport-10-1",
            "crossheading-transport-11",
        },
        {"crossheading-transport"},
    )

    assert replayed == {"crossheading-transport"}
    assert oracle == {"crossheading-transport"}


def test_normalize_uk_replay_compare_eids_keeps_crossheading_descendants_when_oracle_has_children() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "crossheading-transport",
            "crossheading-transport-10",
            "crossheading-transport-11",
        },
        {
            "crossheading-transport",
            "crossheading-transport-10",
        },
    )

    assert replayed == {
        "crossheading-transport",
        "crossheading-transport-10",
        "crossheading-transport-11",
    }
    assert oracle == {
        "crossheading-transport",
        "crossheading-transport-10",
    }


def test_normalize_uk_replay_compare_eids_drops_part_and_crossheading_wrappers() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-13-part-I-paragraph-1",
            "schedule-13-part-I-crossheading-exclusions_paragraph-2",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10-paragraph-5",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10-paragraph-5-1",
        },
        {
            "schedule-13-part-I",
            "schedule-13-part-I-crossheading-exclusions",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10",
        },
    )

    assert replayed == set()
    assert oracle == {
        "schedule-13-part-i",
        "schedule-13-part-i-crossheading-exclusions",
        "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10",
    }


def test_normalize_uk_replay_compare_eids_keeps_schedule_local_paragraph_wrapper() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-13-paragraph-1"},
        {"schedule-13"},
    )

    assert replayed == {"schedule-13-paragraph-1"}
    assert oracle == {"schedule-13"}


def test_normalize_uk_replay_compare_eids_keeps_part_wrapper_when_parent_absent() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-13-part-i-paragraph-1"},
        {"schedule-13-part-ii"},
    )

    assert replayed == {"schedule-13-part-i-paragraph-1"}
    assert oracle == {"schedule-13-part-ii"}


def test_normalize_uk_replay_compare_eids_drops_replay_only_table_fallback_nodes() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-1-group-1",
            "schedule-1-group-1-part-1-table",
            "schedule-1-group-1-part-1-table-row",
            "schedule-1-group-1-part-1-table-row-cell",
            "schedule-1-group-1-part-1-table-row-header_cell",
        },
        {"schedule-1-group-1"},
    )

    assert replayed == {"schedule-1-group-1"}
    assert oracle == {"schedule-1-group-1"}


def test_normalize_uk_replay_compare_eids_keeps_table_nodes_when_oracle_has_table_surface() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-1-group-1",
            "schedule-1-group-1-part-1-table",
            "schedule-1-group-1-part-1-table-row-cell",
        },
        {
            "schedule-1-group-1",
            "schedule-1-group-1-part-1-table",
        },
    )

    assert replayed == {
        "schedule-1-group-1",
        "schedule-1-group-1-part-1-table",
        "schedule-1-group-1-part-1-table-row-cell",
    }
    assert oracle == {
        "schedule-1-group-1",
        "schedule-1-group-1-part-1-table",
    }
