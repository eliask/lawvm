from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lawvm.uk_legislation import replay_recovery_observations as recovery_obs
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


def _uk_replay_module_paths() -> tuple[Path, ...]:
    uk_dir = Path(__file__).parents[1] / "src/lawvm/uk_legislation"
    return (*sorted(uk_dir.glob("replay_*.py")), uk_dir / "uk_amendment_replay.py")


def _function_return_string_constants(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                child.value.value
                for child in ast.walk(node)
                if isinstance(child, ast.Return)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
            }
    raise AssertionError(f"function not found: {function_name}")


def _uk_replay_recovery_rule_ids(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"append", "extend"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("uk_replay_")
    }


def test_uk_replay_emitted_adjudication_kinds_are_explicitly_owned() -> None:
    emitted = set().union(
        *(_uk_replay_string_constants(path) for path in _uk_replay_module_paths())
    )
    owned = (
        sa.UK_REPLAY_BUG_ADJUDICATION_KINDS
        | sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS
    )

    assert sorted(emitted - owned) == []


def test_uk_replay_recovery_observations_are_explicitly_owned() -> None:
    keys = set(recovery_obs.UK_REPLAY_RECOVERY_OBSERVATIONS)
    owned = (
        sa.UK_REPLAY_BUG_ADJUDICATION_KINDS
        | sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS
        | sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS
    )
    nonblocking_or_source_shape = (
        sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS
        | sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS
    )

    assert sorted(keys - owned) == []
    assert sorted(keys - nonblocking_or_source_shape) == []
    for observation in recovery_obs.UK_REPLAY_RECOVERY_OBSERVATIONS.values():
        assert observation.message
        assert observation.family in {
            "definition_entry_predicate_recovery",
            "definition_entry_separator_recovery",
            "target_resolution_recovery",
            "source_shape_recovery",
            "text_match_recovery",
            "text_rewrite_recovery",
            "amendment_program_recovery",
        }
        assert observation.strict_disposition in {"block", "record"}


def test_uk_replay_text_recovery_rules_have_explicit_observation_metadata() -> None:
    uk_dir = Path(__file__).parents[1] / "src/lawvm/uk_legislation"
    emitted_recovery_rules = _uk_replay_recovery_rule_ids(uk_dir / "replay_text_apply.py")

    assert sorted(
        emitted_recovery_rules - set(recovery_obs.UK_REPLAY_RECOVERY_OBSERVATIONS)
    ) == []


def test_uk_replay_recovery_observation_rejects_unknown_rule_id() -> None:
    with pytest.raises(KeyError):
        recovery_obs.uk_replay_recovery_observation("uk_replay_missing_recovery_metadata")


def test_uk_effect_source_pathology_classes_are_explicitly_owned() -> None:
    source_path = Path(__file__).parents[1] / "src/lawvm/uk_legislation/source_adjudication.py"
    emitted = {
        value
        for value in _function_return_string_constants(
            source_path,
            "classify_uk_effect_source_pathology",
        )
        if value
    }

    assert sorted(emitted - sa.UK_EFFECT_SOURCE_PATHOLOGY_CLASSES) == []


def test_uk_manual_frontier_source_pathology_tables_are_owned() -> None:
    tables = (
        sa._UK_MANUAL_FRONTIER_RANGE_SOURCE_PATHOLOGY_RESULTS,
        sa._UK_MANUAL_FRONTIER_SOURCE_INSUFFICIENT_PATHOLOGY_RESULTS,
        sa._UK_MANUAL_FRONTIER_MAIN_SOURCE_PATHOLOGY_RESULTS,
    )
    table_keys = {source_pathology for table in tables for source_pathology in table}
    rule_ids = {rule_id for table in tables for _, rule_id, _ in table.values()}

    assert sorted(table_keys - sa.UK_EFFECT_SOURCE_PATHOLOGY_CLASSES) == []
    assert "table_entry_target_unsupported" not in table_keys
    assert all(rule_ids)


def test_uk_replay_source_shape_residual_table_is_owned() -> None:
    mapped = {
        adjudication_kind
        for adjudication_kind, _ in sa._UK_REPLAY_SOURCE_SHAPE_RESIDUAL_KIND_PRIORITY
    }
    defaulted = sa._UK_REPLAY_SOURCE_SHAPE_RESIDUAL_DEFAULT_ADJUDICATION_KINDS

    assert len(mapped) == len(sa._UK_REPLAY_SOURCE_SHAPE_RESIDUAL_KIND_PRIORITY)
    assert sorted(mapped - sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS) == []
    assert sorted(defaulted - sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS) == []
    assert sorted(mapped & defaulted) == []
    assert sorted(sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS - mapped - defaulted) == []


def test_uk_replay_text_surface_residual_table_is_owned() -> None:
    mapped = {
        adjudication_kind
        for adjudication_kind, _ in sa._UK_REPLAY_TEXT_SURFACE_RESIDUAL_KIND_PRIORITY
    }

    assert len(mapped) == len(sa._UK_REPLAY_TEXT_SURFACE_RESIDUAL_KIND_PRIORITY)
    assert sorted(mapped - sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS) == []
    assert sorted(sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS - mapped) == []


def test_classify_uk_replay_adjudication_bucket() -> None:
    cases = {
        "uk_replay_target_not_found": "replay_bug",
        "uk_replay_text_patch_missing_structured_payload": "replay_bug",
        "uk_replay_definition_child_shape_gap": "source_shape",
        "uk_replay_definition_anchor_lexical_variant_recovered": "source_shape",
        "uk_replay_definition_entry_shape_gap": "source_shape",
        "uk_replay_heading_facet_target_gap": "source_shape",
        "uk_replay_heading_text_preimage_gap": "text_surface",
        "uk_replay_text_insert_anchor_preimage_gap": "text_surface",
        "uk_replay_text_match_article_phrase_surface_gap": "text_surface",
        "uk_replay_text_match_citation_connector_surface_gap": "text_surface",
        "uk_replay_repeated_form_label_payload_shape_gap": "source_shape",
        "uk_replay_absent_child_repeal_target_gap": "source_shape",
        "uk_replay_schedule_entry_repeal_granularity_blocked": "source_shape",
        "uk_replay_schedule_list_entry_anchor_unresolved": "source_shape",
        "uk_replay_schedule_list_entry_replace_unresolved": "source_shape",
        "uk_replay_schedule_list_entry_repeal_unresolved": "source_shape",
        "uk_replay_schedule_list_entry_table_rows_insert_unresolved": "source_shape",
        "uk_replay_schedule_table_end_rows_insert_unresolved": "source_shape",
        "uk_replay_table_entry_row_insert_unresolved": "source_shape",
        "uk_replay_table_entry_inline_text_insertion_unresolved": "source_shape",
        "uk_replay_table_entry_inline_text_preimage_gap": "source_shape",
        "uk_replay_definition_anchor_conjoined_term_normalized": "nonblocking_observation",
        "uk_replay_definition_anchor_parenthetical_translation_normalized": "nonblocking_observation",
        "uk_replay_definition_anchor_qualifier_phrase_normalized": "nonblocking_observation",
        "uk_replay_definition_entry_orphan_separator_normalized": "nonblocking_observation",
        "uk_replay_definition_entry_qualifier_phrase_normalized": "nonblocking_observation",
        "uk_replay_definition_predicate_shall_construed_normalized": "nonblocking_observation",
        "uk_replay_direct_section_paragraph_child_text_recovered": "nonblocking_observation",
        "uk_replay_empty_descendant_parent_text_recovered": "nonblocking_observation",
        "uk_replay_implicit_first_subparagraph_parent_text_recovered": "nonblocking_observation",
        "uk_replay_schedule_list_entry_alphabetical_position_resolved": "nonblocking_observation",
        "uk_replay_schedule_list_entry_anchor_article_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_anchor_prefix_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_group_anchor_resolved": "nonblocking_observation",
        "uk_replay_labeled_child_end_range_applied": "nonblocking_observation",
        "uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_replace_resolved": "nonblocking_observation",
        "uk_replay_schedule_list_entry_repeal_resolved": "nonblocking_observation",
        "uk_replay_repeal_target_already_absent_observed": "nonblocking_observation",
        "uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized": "nonblocking_observation",
        "uk_replay_schedule_list_entry_table_rows_insert_resolved": "nonblocking_observation",
        "uk_replay_schedule_table_end_rows_insert_resolved": "nonblocking_observation",
        "uk_replay_schedule_item_target_from_parent_substitution_resolved": "nonblocking_observation",
        "uk_replay_schedule_p1group_paragraph_wrapper_resolved": "nonblocking_observation",
        "uk_replay_body_root_fallback_insert_resolved": "nonblocking_observation",
        "uk_effect_table_entry_row_insert": "nonblocking_observation",
        "uk_replay_table_entry_multi_cell_text_patch_resolved": "nonblocking_observation",
        "uk_replay_source_label_changing_substitution_resolved": "nonblocking_observation",
        "uk_replay_source_carried_labeled_child_text_substitution_recovered": "nonblocking_observation",
        "uk_replay_source_carried_structured_tail_substitution_recovered": "nonblocking_observation",
        "uk_replay_text_match_missing": "text_surface",
        "uk_replay_text_monetary_amount_preimage_gap": "text_surface",
        "uk_replay_text_parenthetical_omission_preimage_gap": "text_surface",
        "uk_replay_same_source_text_patch_overlap_blocked": "source_shape",
        "uk_replay_same_source_text_patch_overlap_disjoint": "nonblocking_observation",
        "text_duplication_warning": "nonblocking_observation",
        "uk_replay_text_match_punctuation_space_normalized": "nonblocking_observation",
        "uk_replay_text_match_rotated_trailing_comma_omission": "nonblocking_observation",
        "uk_replay_numeric_list_trailing_comma_anchor_normalized": "nonblocking_observation",
        "uk_replay_text_match_replacement_normalized_present": "nonblocking_observation",
        "uk_replay_text_range_anchor_word_boundary_normalized": "nonblocking_observation",
        "uk_replay_future_kind": "unknown",
        "": "unknown",
    }

    for kind, expected_bucket in cases.items():
        assert sa.classify_uk_replay_adjudication_bucket(kind) == expected_bucket


def test_classify_uk_replay_adjudication_bucket_is_exhaustive_for_owned_kinds() -> None:
    owned_buckets = (
        (sa.UK_REPLAY_BUG_ADJUDICATION_KINDS, "replay_bug"),
        (sa.UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS, "source_shape"),
        (sa.UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS, "text_surface"),
        (sa.UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS, "nonblocking_observation"),
    )
    seen: set[str] = set()
    for kinds, expected_bucket in owned_buckets:
        assert sorted(seen & kinds) == []
        seen.update(kinds)
        for kind in kinds:
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


def test_classify_uk_perfect_replay_score_overrides_oracle_expansion_shape() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=13,
        n_oracle_eids=30,
        n_effects=9,
        raw_score=1.0,
    )

    assert comparison == "commensurable"
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


def test_classify_uk_effect_source_pathology_marks_conditional_temporal_repeal_out_of_scope() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "5 Paragraph 4 is repealed at the end of 2021 if, or to the "
            "extent that, it has not been brought into force before the end "
            "of that year."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_overlap_substitution_unlowered"],
        effect_type="words repealed",
        is_structural=True,
    )

    assert pathology == "conditional_temporal_repeal_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_marks_definition_child_and_tail_substitution() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "239 In section 15, in subsection (7), for paragraph (d) of the "
            "definition of “NHS body in England” and the “or” at the end of "
            "that paragraph substitute— an integrated care board established "
            "under section 14Z25 of that Act; ."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_overlap_substitution_unlowered"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "definition_child_and_tail_substitution_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_marks_definition_child_structural_insert() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "37 In section 374, in the definition of “custodial sentence”, "
            "after paragraph (e) (but before the “or” at the end of that "
            "paragraph) insert— ea a sentence of detention under section 226B; ."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_overlap_substitution_unlowered"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "definition_child_structural_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_marks_structured_tail_substitution() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "4 In paragraph 2, in sub-paragraph (3), for the words from "
            "“, any of the following provisions of the ANO 2016” to the end, "
            "substitute “— a any of the following provisions of the ANO 2016— "
            "i article 265E(1)(a); ii article 265E(1)(b); b any of the "
            "following provisions of Regulation (EU) 2019/947— i Article 12; "
            "ii Article 13; iii Article 14”"
        ),
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:9/paragraph:2/subparagraph:3"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "source_carried_structured_tail_substitution_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_source_pathology_accepts_lowered_anchor_to_end_block_substitution() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "a in subsection (5), for the words from “may be lawfully” to the end "
            "substitute— a may be lawfully imported into the United Kingdom, or "
            "b has been imported into the United Kingdom."
        ),
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:24g/subsection:5"],
        lowering_rule_ids=["uk_effect_anchor_to_end_block_substitution_text_patch"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


def test_classify_uk_effect_source_pathology_keeps_flat_tail_substitution_core() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "4 In paragraph 2, in sub-paragraph (3), for the words from "
            "“the old expression” to the end substitute “the new expression”"
        ),
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:9/paragraph:2/subparagraph:3"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


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


def test_classify_uk_effect_source_pathology_marks_application_table_out_of_scope() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="Part",
        extracted_text=(
            "Column 1 Column 2 Column 3 Enactment Nature of Provision "
            "Modifications and Limitations In Section 85A Sub section (1)"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        lowering_rule_ids=["uk_effect_application_modification_table_rejected"],
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


def test_classify_uk_manual_compile_frontier_marks_structured_tail_substitution() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="source_carried_structured_tail_substitution_unsupported",
        extracted_tag="P1",
        extracted_text=(
            "4 In paragraph 2, in sub-paragraph (3), for the words from "
            "“, any of the following provisions of the ANO 2016” to the end, "
            "substitute “— a any of the following provisions of the ANO 2016— "
            "i article 265E(1)(a); ii article 265E(1)(b)”"
        ),
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_source_carried_structured_tail_substitution_candidate"
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


def test_classify_uk_manual_compile_frontier_marks_unquoted_preimage_substitution_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            "For the period specified in section 50(2) there is substituted "
            "the period of four years."
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

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_unquoted_preimage_substitution_source_insufficient"


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


def test_classify_uk_manual_compile_frontier_marks_feed_action_object_fragment() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words repealed",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P4",
        extracted_text=(
            "i in subsection (1)(a), the words "
            "“section 18 of the Gaming Act 1845, section 1 of the Gaming Act 1892 or”, and"
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
        == "uk_manual_frontier_effect_metadata_carried_text_patch_candidate"
    )


def test_classify_uk_manual_compile_frontier_marks_feed_definition_fragment() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text=(
            "f the definition of “documents” in section 417(1) of the "
            "Financial Services and Markets Act 2000 (c. 8)."
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

    assert result["status"] == "source_insufficient"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_definition_target_fragment_source_insufficient"
    )


def test_classify_uk_manual_compile_frontier_marks_unanchored_definition_entry_payload_claim() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="BlockAmendment",
        extracted_text=(
            "“regulated information” has the meaning given in Article 2(1)(k) "
            "of the transparency obligations directive;"
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
        == "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    )


def test_classify_uk_manual_compile_frontier_marks_appropriate_place_definition_rejection() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="BlockAmendment",
        extracted_text="“regulated information” has the meaning given in Article 2(1)(k);",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_appropriate_place_definition_entry_insert_rejected",
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
        == "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    )


def test_classify_uk_manual_compile_frontier_prefers_appropriate_place_definition_rejection_over_payload_shape() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="fragment_context_missing",
        extracted_tag="BlockAmendment",
        extracted_text=(
            "“deployable output” means, in relation to a facility, water produced "
            "under drought conditions, having regard in particular to— hydrological "
            "yield; licensed abstraction; environmental state;"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_appropriate_place_definition_entry_insert_rejected",
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
        == "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    )


def test_classify_uk_manual_compile_frontier_keeps_action_fragment_with_parser_work() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text='In subsection (1), after "old" insert "new".',
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


def test_classify_uk_manual_compile_frontier_marks_empty_type_whole_act_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P2",
        extracted_text=(
            "Notwithstanding any amendment, repeal or revocation made by this "
            "Order, the unpaid sum is payable after commencement."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_empty_type_whole_act_action_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_empty_type_whole_act_action_out_of_scope"
    )


def test_classify_uk_manual_compile_frontier_marks_whole_act_text_patch_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="whole_act_word_level_text_patch_unsupported",
        extracted_tag="P3",
        extracted_text='for “EEA state” wherever it occurs substitute “ EEA State ” ; and',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_whole_act_word_level_text_patch_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_whole_act_word_level_text_patch_candidate"


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


def test_classify_uk_manual_compile_frontier_marks_appropriate_place_index_entry() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="appropriate_place_index_entry_insert_unsupported",
        extracted_tag="P3",
        extracted_text=(
            "at the appropriate place insert- "
            '"relevant register" paragraph 22B(6A).'
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_appropriate_place_insert_rejected",
                "blocking": True,
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_appropriate_place_index_entry_candidate"


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


def test_classify_uk_manual_compile_frontier_marks_entry_beginning_substitution_manual() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P2",
        extracted_text=(
            "In schedule 19, for the entry beginning "
            "\u201cHer Majesty's Chief Inspector of Constabulary\u201d, substitute- "
            "\u201cHer Majesty's Inspectors of Constabulary\u201d."
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


def test_classify_uk_manual_compile_frontier_marks_child_tail_sibling_insert() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text=(
            "at the end of paragraph (b) insert , or c which is conferred "
            "by or under the Childcare Payments Act 2014;"
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_structural_sibling_insert_rejected",
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


def test_classify_uk_manual_compile_frontier_marks_unresolved_repeal_table_lowering() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="repealed",
        source_pathology="",
        extracted_tag="Schedule",
        extracted_text=(
            "SCHEDULE 1 Repeals Enactment Extent of repeal Broadcasting Act 1990 "
            "Section 128(2) to (5)."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_repeal_table_structural_repeal_unresolved",
                "reason_code": "broad_container_repeal_requires_grouped_feed_compilation",
                "blocking": True,
                "row_text": "Broadcasting Act 1990 (c. 42) | Section 128(2) to (5).",
            },
        ),
        compiled_op_count=2,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_repeal_table_candidate"


def test_classify_uk_manual_compile_frontier_marks_mixed_body_heading_split() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            'for "Commissioner" (in each place, including in the heading) '
            'substitute "appointed person".'
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_mixed_body_heading_text_substitution_rejected",
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
        == "uk_manual_frontier_mixed_body_heading_text_substitution_split"
    )


def test_classify_uk_manual_compile_frontier_marks_repeal_table_feed_source_gap() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="",
        extracted_tag="Part",
        extracted_text=(
            "Part IV Railways Reference Short title or title Extent of repeal "
            "Transport Act 1962 In the Seventh Schedule, paragraphs 23 and 24."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_repeal_table_structural_repeal",
                "blocking": False,
                "extent_cell": "In the Seventh Schedule, paragraphs 23 and 24.",
                "row_text": (
                    "10 & 11 Eliz.2 c. 46. | Transport Act 1962. | "
                    "In the Seventh Schedule, paragraphs 23 and 24."
                ),
                "target_ref": "Sch. 7 para. 23",
            },
            {
                "rule_id": "uk_effect_repeal_table_quoted_words_text_repeal_unresolved",
                "blocking": True,
                "reason_code": "no_unique_matching_repeal_table_row",
                "extent_cell": "",
                "row_text": "",
                "target_ref": "Sch. 7 para. 34",
            },
        ),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_repeal_table_feed_source_target_gap"


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


def test_classify_uk_manual_compile_frontier_marks_conditional_temporal_repeal_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words repealed",
        source_pathology="conditional_temporal_repeal_unsupported",
        extracted_tag="P1",
        extracted_text=(
            "5 Paragraph 4 is repealed at the end of 2021 if, or to the "
            "extent that, it has not been brought into force before the end "
            "of that year."
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

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == (
        "uk_manual_frontier_conditional_temporal_repeal_out_of_scope"
    )


def test_classify_uk_manual_compile_frontier_marks_definition_child_and_tail_substitution_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="definition_child_and_tail_substitution_unsupported",
        extracted_tag="P1",
        extracted_text=(
            "239 In section 15, in subsection (7), for paragraph (d) of the "
            "definition of “NHS body in England” and the “or” at the end of "
            "that paragraph substitute— an integrated care board established "
            "under section 14Z25 of that Act; ."
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
    assert result["rule_id"] == (
        "uk_manual_frontier_definition_child_and_tail_substitution_candidate"
    )


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


def test_classify_uk_manual_compile_frontier_marks_table_crossheading_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="table_crossheading_target_unsupported",
        extracted_tag="P1",
        extracted_text=(
            "in paragraph 51(6), in the definition of primary activity, in the "
            "table (the cross-heading preceding entry 1 of which becomes "
            "\"Environmental Permitting\")"
        ),
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
    assert result["rule_id"] == "uk_manual_frontier_table_crossheading_candidate"


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


def test_classify_uk_manual_compile_frontier_accepts_source_structuralized_added() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="added",
        source_pathology="",
        extracted_tag="BlockAmendment",
        extracted_text="7 Inserted subsection.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_added_type_source_structuralized",
                "strict_disposition": "record",
            },
        ),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "deterministic_frontend_supported"
    assert result["rule_id"] == "uk_manual_frontier_deterministic_supported"


def test_classify_uk_manual_compile_frontier_marks_pseudo_definition_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="added",
        source_pathology="nonstructural_root_gap",
        extracted_tag="P1",
        extracted_text="73 Schedule 2 shall have effect.",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_structural_pseudo_definition_target_rejected",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "source_insufficient"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_structural_pseudo_definition_source_insufficient"
    )


def test_classify_uk_manual_compile_frontier_marks_pseudo_definition_payload_placement_claim() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="added",
        source_pathology="nonstructural_root_gap",
        extracted_tag="BlockAmendment",
        extracted_text="“ the 1996 Act ” means the Broadcasting Act 1996;",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_structural_pseudo_definition_target_rejected",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "manual_compile_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
    )


def test_classify_uk_manual_compile_frontier_marks_pseudo_definition_instruction_payload() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="added",
        source_pathology="nonstructural_root_gap",
        extracted_tag="P3",
        extracted_text=(
            "b after the definition of “S4C” there is inserted— "
            "“satellite television service” has the meaning given by section 43(1); ."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_structural_pseudo_definition_target_rejected",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "manual_compile_candidate"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
    )


def test_classify_uk_manual_compile_frontier_promotes_definition_list_end_insert() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="added",
        source_pathology="",
        extracted_tag="SourceRange",
        extracted_text=(
            "f at the end there is inserted- "
            "“television multiplex service” means a multiplex service."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_source_range_definition_entry_at_end_insert_rejected",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=2,
        replay_applicable=True,
        structural_for_replay=False,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_definition_list_end_insert_candidate"


def test_classify_uk_manual_compile_frontier_accepts_ceases_replay_repeal() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="ceases to have effect",
        source_pathology="",
        extracted_tag="P1",
        extracted_text="Paragraph 6(1)(b) ceases to have effect.",
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=False,
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


def test_classify_uk_manual_compile_frontier_application_reference_preempts_preimage_gap() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="applied by 2006 c. 52, s. 215 (as amended)",
        source_pathology="application_by_reference_effect_out_of_scope",
        extracted_tag="P1",
        extracted_text=(
            "35 In section 215 (section 214: definitions etc), in subsection (1), "
            "for “Section 101(13) of the Sentencing Act” substitute "
            "“Section 238(3) of the Sentencing Code”."
        ),
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="text_patch_preimage_absent_from_target_surfaces",
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_application_by_reference_out_of_scope"


def test_classify_uk_manual_compile_frontier_misselected_target_preempts_preimage_gap() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="misselected_target_context",
        extracted_tag="P3",
        extracted_text='in paragraph (e) for "old" substitute "new";',
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="text_patch_preimage_absent_from_target_surfaces",
    )

    assert result["status"] == "source_insufficient"
    assert (
        result["rule_id"]
        == "uk_manual_frontier_misselected_target_context_source_insufficient"
    )


def test_classify_uk_manual_compile_frontier_marks_text_patch_target_source_chain_gap() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="",
        extracted_tag="P1",
        extracted_text='after "397(1)" insert "or 397A(2)".',
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="text_patch_target_absent_from_enacted_source_chain",
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_text_patch_target_source_chain_gap"
    assert "target absent from the enacted source" in result["reason"]


def test_classify_uk_manual_compile_frontier_marks_instruction_header_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P2",
        extracted_text="1 In section 183A of the Broadcasting Act 1990—",
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_instruction_header_source_insufficient"


def test_classify_uk_manual_compile_frontier_marks_structural_child_range_substitution() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='b for paragraphs (a) and (b) there shall be substituted "on a relevant frequency".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_structural_child_range_substitution_candidate"


def test_classify_uk_manual_compile_frontier_marks_active_structural_child_range_substitution() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='5 In subsection (3)(c), for sub-paragraphs (i) and (ii) substitute "as the trustee".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_structural_child_range_substitution_candidate"


def test_classify_uk_manual_compile_frontier_marks_to_range_definition_child_substitution() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            "for paragraphs (a) to (c) of the definition of "
            '"Northern Ireland department", substitute- a ... b ... c ...'
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_structural_child_range_substitution_rejected",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_structural_child_range_substitution_candidate"


def test_classify_uk_manual_compile_frontier_marks_deictic_text_patch_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='b for those words in the second place where they occur substitute "Part 1".',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_deictic_text_patch_source_insufficient"


def test_classify_uk_manual_compile_frontier_marks_definition_child_structural_substitution() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            "1 In section 177, in subsection (6), in the definition of "
            '"foreign satellite service", for paragraph (a) (including the "or" at the end) '
            "substitute- a a service which is broadcast by satellite."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_definition_child_structural_substitution_candidate"


def test_classify_uk_manual_compile_frontier_marks_definition_child_structural_insert() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            "37 In section 374, in the definition of “custodial sentence”, "
            "after paragraph (e) (but before the “or” at the end of that "
            "paragraph) insert— ea a sentence of detention under section 226B; ."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "block",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_definition_child_structural_insert_candidate"


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


def test_classify_uk_effect_compare_shape_marks_table_entry_column_surface_gap() -> None:
    result = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=("text_replace",),
        resolver_eids=("section-98",),
        base_target_hits=(True,),
        oracle_target_hits=(True,),
        base_target_texts=("Section 98 penalty table carrier text",),
        oracle_target_texts=("Section 98 penalty table carrier text",),
        text_patch_matches=("45G(4) and (5),",),
        text_patch_replacements=("45G(4) and (5), 45R(5) and (6),"),
        lowering_rule_ids=("uk_effect_table_entry_relating_column_text_patch",),
    )

    assert result == "table_cell_text_patch_requires_table_surface"
    assert not is_core_uk_effect_compare_candidate(result)


@pytest.mark.parametrize(
    ("rule_id", "match_text", "replacement"),
    [
        ("uk_effect_table_entry_label_text_patch", "terrorist", "certain"),
        (
            "uk_effect_table_entry_deictic_label_column_text_patch",
            "two-thirds",
            "one-half",
        ),
    ],
)
def test_classify_uk_effect_compare_shape_marks_table_entry_label_surface_gap(
    rule_id: str,
    match_text: str,
    replacement: str,
) -> None:
    result = classify_uk_effect_compare_shape(
        effect_type="word substituted",
        op_actions=("text_replace",),
        resolver_eids=("section-166",),
        base_target_hits=(True,),
        oracle_target_hits=(True,),
        base_target_texts=("Section 166 table carrier text",),
        oracle_target_texts=("Section 166 table carrier text",),
        text_patch_matches=(match_text,),
        text_patch_replacements=(replacement,),
        lowering_rule_ids=(rule_id,),
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


def test_classify_uk_manual_compile_frontier_marks_application_by_reference_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="application_by_reference_effect_out_of_scope",
        extracted_tag="P1",
        extracted_text=(
            "The compensation shall be determined, in case of dispute, under "
            "Part I of the Land Compensation Act 1961."
        ),
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
    assert result["rule_id"] == "uk_manual_frontier_application_by_reference_out_of_scope"


def test_classify_uk_manual_compile_frontier_external_act_target_out_of_scope() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text=(
            "In Schedule 4 to the Town and Country Planning (Scotland) Act 1997, "
            "in paragraph 8(2), for the words from X to the end substitute Y."
        ),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_external_act_target_rejected",
                "blocking": True,
                "source_named_target": "Town and Country Planning (Scotland) Act 1997",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "non_textual_or_out_of_scope"
    assert result["rule_id"] == "uk_manual_frontier_external_act_target_out_of_scope"


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


def test_classify_uk_manual_compile_frontier_marks_relative_occurrence_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="relative_other_place_occurrence_unsupported",
        extracted_tag="P3",
        extracted_text=(
            "b after “the British Waterways Board”, in each other place occurring, "
            "insert “or, as the case may be, Canal & River Trust”."
        ),
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_relative_other_place_occurrence_candidate"
    assert "sibling-aware occurrence selection" in result["reason"]


def test_classify_uk_manual_compile_frontier_marks_referent_qualified_pathology() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="referent_qualified_text_substitution_unsupported",
        extracted_tag="P3",
        extracted_text=(
            "for “his”, where it refers to the Rail Regulator, substitute “its”."
        ),
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "deterministic_frontend_candidate"
    assert result["rule_id"] == "uk_manual_frontier_referent_qualified_text_substitution_candidate"
    assert "referent-sensitive text predicate" in result["reason"]


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


def test_classify_uk_effect_appropriate_place_definition_entry_source_pathology_with_insert_in() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "In section 235(1) (interpretation) insert in the appropriate place— "
            "“deployable output” means, in relation to a facility, water;"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "appropriate_place_definition_entry_insert_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_appropriate_place_index_entry_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "at the appropriate place insert- "
            '"relevant register" paragraph 22B(6A).'
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "appropriate_place_index_entry_insert_unsupported"
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


def test_classify_uk_effect_application_by_reference_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "The rules set out in section 5 of the 1961 Act shall, so far as "
            "applicable and subject to any necessary modifications, have effect "
            "for the purpose of assessing compensation."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_compensation_determined_under_act_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "Any compensation payable shall be determined, in case of dispute, "
            "under Part I of the Land Compensation Act 1961."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_compensation_determined_under_year_act_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "Any compensation payable shall be determined, in case of dispute, "
            "under Part I of the 1961 Act."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_act_shall_apply_as_if_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "In relation to the determination of any such question, sections 2 "
            "and 4 of the 1961 Act shall apply as if references to the acquiring "
            "authority were references to the appropriate person."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_act_has_effect_as_if_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "The Land Compensation Act 1961 shall have effect, subject to "
            "paragraphs (1) and (2), as if this Order were a local enactment."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
        is_structural=True,
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_applied_by_effect_type_is_application_reference() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "35 In section 215 (section 214: definitions etc), in subsection (1), "
            "for “Section 101(13) of the Sentencing Act” substitute "
            "“Section 238(3) of the Sentencing Code”."
        ),
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:238/subsection:3"],
        effect_type="applied by 2006 c. 52, s. 215 (as amended)",
    )

    assert pathology == "application_by_reference_effect_out_of_scope"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_application_by_reference_does_not_hide_text_mutation() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "In section 5, for the words from X to the end substitute text about "
            "compensation determined under Part I of the Land Compensation Act 1961."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology != "application_by_reference_effect_out_of_scope"


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


def test_classify_uk_effect_repeal_table_header_without_effect_type_source_pathology() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="Part",
        extracted_text=(
            "Part IV Railways Reference Short title or title Extent of repeal or "
            "revocation Transport Act 1962 Sections 3 to 4A."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="",
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


def test_classify_uk_effect_payload_fragment_without_action_formula_numbered_block_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "1 A licence to provide additional services on a frequency which is a "
            "relevant frequency for the purposes of section 48 was assigned under section 65."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "payload_fragment_without_action_formula"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_numbered_block_instruction_remains_instruction_text() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text='1 In section 5, for "old" substitute "new".',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "unhandled_instruction_text"


def test_classify_uk_effect_source_parent_substitution_range_payload_is_owned() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "d in relation to a care service or limited registration service- "
            "i the person identified under section 7(2)(b); ii if the application is made under section 33(1)."
        ),
        op_actions=["replace", "repeal"],
        payload_kinds=["item"],
        payload_texts=["d in relation to a care service or limited registration service"],
        lowering_rule_ids=["uk_effect_source_parent_substitution_range_payload_lowered"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


def test_classify_uk_effect_source_parent_at_end_added_payload_is_owned() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "6 Expressions used in subsection (1) and in the Regulation of Care "
            "(Scotland) Act 2001 have the same meanings in that subsection as in that Act."
        ),
        op_actions=["insert"],
        payload_kinds=["subsection"],
        payload_texts=["Expressions used in subsection (1) and in the Regulation of Care (Scotland) Act 2001"],
        lowering_rule_ids=["uk_effect_source_parent_at_end_added_payload_lowered"],
        effect_type="",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


def test_classify_uk_effect_after_paragraph_insert_labelled_series_is_owned() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b after paragraph (b), insert ; c make, on behalf of the granter, a request; "
            "d give, on behalf of the granter, an authorisation; or e make, on behalf of the granter, a nomination."
        ),
        op_actions=["text_replace", "insert", "insert", "insert"],
        payload_kinds=["paragraph", "paragraph", "paragraph"],
        payload_texts=["make, on behalf of the granter, a request"],
        lowering_rule_ids=["uk_effect_after_paragraph_insert_labelled_series_lowered"],
        effect_type="inserted",
        is_structural=True,
    )

    assert pathology == ""
    assert is_core_uk_effect_source_candidate(pathology) is True


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


def test_classify_uk_effect_relative_other_place_occurrence() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "b after “the British Waterways Board”, in each other place occurring, "
            "insert “or, as the case may be, Canal & River Trust”."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "relative_other_place_occurrence_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_referent_qualified_text_substitution() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "for “he” and “him”, where they refer to the Rail Regulator, "
            "substitute “it”."
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "referent_qualified_text_substitution_unsupported"
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


def test_classify_uk_effect_amendment_inserted_parent_structural_insert_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "in sub-paragraph (3)(a), in the inserted paragraph (d), "
            "before sub-paragraph (i) insert- ai inserted text"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:22/paragraph:21/subparagraph:3/item:a"],
        lowering_rule_ids=(
            "uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
        ),
        effect_type="words inserted",
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


def test_classify_uk_effect_table_target_child_anchor_insert() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="after paragraph (a) insert- corporal in the Royal Marines;",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:132/subsection:1/paragraph:table"],
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


def test_classify_uk_manual_frontier_table_target_end_insert_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text="at the end insert- Section 75(5) of ITTOIA 2005.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
                "original_affected_provisions": "s. 98 Table",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_candidate"


def test_classify_uk_manual_frontier_deictic_table_entry_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="table_entry_target_unsupported",
        extracted_tag="P3",
        extracted_text="after that entry insert- electronic whereabouts monitoring requirement.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_table_entry_instruction_rejected",
                "blocking": True,
                "entry_shape": "deictic_table_entry",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_deictic_candidate"


def test_classify_uk_manual_frontier_deictic_table_row_insert_payload_gap() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="after that entry insert- electronic whereabouts monitoring requirement.",
        op_actions=[],
        target_paths=["section:190/subsection:3/paragraph:table"],
        lowering_rule_ids=["uk_effect_table_entry_row_insert"],
        effect_type="words inserted",
        is_structural=True,
    )
    assert pathology == "table_entry_target_unsupported"

    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology=pathology,
        extracted_tag="P3",
        extracted_text="after that entry insert- electronic whereabouts monitoring requirement.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_table_entry_row_insert",
                "reason_code": "deictic_table_entry_insert_without_single_row_payload",
                "blocking": True,
                "entry_shape": "deictic_table_entry",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_deictic_candidate"


def test_classify_uk_manual_frontier_table_column_insert_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="table_entry_target_unsupported",
        extracted_tag="P3",
        extracted_text="between the second and third columns, insert- new column text.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_table_entry_instruction_rejected",
                "blocking": True,
                "entry_shape": "between_columns",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_column_insert_candidate"


def test_classify_uk_manual_frontier_table_appropriate_place_candidate() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="table_entry_target_unsupported",
        extracted_tag="P1",
        extracted_text="in the table in subsection (1), at the appropriate place insert- new row.",
        lowering_rejections=[
            {
                "rule_id": "uk_effect_table_entry_instruction_rejected",
                "blocking": True,
                "entry_shape": "appropriate_place_table_entry",
            }
        ],
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_appropriate_place_candidate"


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


def test_classify_uk_effect_whole_act_word_level_text_patch_rejected() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text='for “EEA state” wherever it occurs substitute “ EEA State ” ; and',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["/whole_act"],
        lowering_rule_ids=["uk_effect_whole_act_word_level_text_patch_rejected"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "whole_act_word_level_text_patch_unsupported"
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


def test_classify_uk_effect_before_child_block_insert_in_inserted_parent_as_amendment_program() -> None:
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

    assert pathology == "amendment_text_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_inserted_parent_structural_insert_as_amendment_program() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "a in sub-paragraph (2)(a), in the inserted paragraph (d), "
            "after sub-paragraph (i) insert- ia the order does not qualify;"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:22/paragraph:21/subparagraph:2/item:a"],
        effect_type="words inserted",
        is_structural=True,
    )

    assert pathology == "amendment_text_target_unsupported"
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


def test_classify_uk_effect_table_crossheading_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text=(
            "In Schedule 6, in paragraph 51(6), in the definition of "
            "primary activity, in the table (the cross-heading preceding "
            "entry 1 of which becomes \"Installations regulated under the "
            "Environmental Permitting Regulations\")"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:6/paragraph:51/subparagraph:6/item:table/crossheading"],
        lowering_rule_ids=["uk_effect_crossheading_replace_rejected"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "table_crossheading_target_unsupported"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_table_crossheading_from_feed_table_target() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text=(
            "for the italic cross-heading \"Installations regulated under "
            "the Pollution Prevention and Control Regulations\" substitute "
            "\"Installations regulated under the Environmental Permitting Regulations\""
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:6/paragraph:51/item:table"],
        lowering_rule_ids=["uk_effect_crossheading_replace_rejected"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "table_crossheading_target_unsupported"
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


def test_classify_uk_effect_text_patch_replacement_present_without_preimage() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-27-7"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=["paragraphs (a) to (dd) above"],
        oracle_target_texts=["paragraphs (a) to (dd) above"],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["paragraphs (a) to (d) above"],
        text_patch_replacements=["paragraphs (a) to (dd) above"],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "text_patch_replacement_present_without_preimage"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_manual_frontier_keeps_postimage_without_preimage_source_insufficient() -> None:
    result = classify_uk_manual_compile_frontier(
        effect_type="words substituted",
        source_pathology="",
        extracted_tag="P2",
        extracted_text='for "old" substitute "new"',
        lowering_rejections=(),
        compiled_op_count=1,
        replay_applicable=True,
        structural_for_replay=True,
        compare_shape="text_patch_replacement_present_without_preimage",
    )

    assert result["status"] == "source_insufficient"
    assert result["rule_id"] == "uk_manual_frontier_text_patch_postimage_chain_gap"


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

    assert compare_shape == "text_patch_replacement_present_without_preimage"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_patch_target_absent_from_enacted_source_chain() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-12aa-1a-b"],
        base_target_hits=[False],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[False],
        oracle_parent_hits=[True],
        base_target_texts=[],
        oracle_target_texts=[
            "the amount payable by a partner by way of income tax is the "
            "difference between the amount in which he is chargeable to income "
            "tax and the aggregate amount of any income tax deducted at source"
        ],
        base_parent_texts=[],
        oracle_parent_texts=[],
        text_patch_matches=["397(1)"],
        text_patch_replacements=["397(1) or 397A(2)"],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "text_patch_target_absent_from_enacted_source_chain"
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
        text_patch_matches=["the Committee"],
        text_patch_replacements=["the Commission"],
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


def test_normalize_uk_replay_compare_eids_handles_fused_chapter_container_label() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"part-6-chapter1a"},
        {"part-6-chapter-1a"},
    )

    assert replayed == {"part-6-chapter-1a"}
    assert oracle == {"part-6-chapter-1a"}


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
