"""Coverage guard for the UK effect-diagnostic / lowering catalog supplement.

Asserts that ``_UK_RULE_SPECS_SUPPLEMENT`` carries a non-empty, prose
``believed_spec`` for each of the 68 ``uk_effect_*`` / ``uk_affecting_act_*``
rule_ids that were the catalog gap, and (best-effort) that each keyed id still
appears in the UK lowering / tools source — flagging any that do not as
possibly-renamed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from lawvm.tools.spec_ledger_uk_catalog_supplement import _UK_RULE_SPECS_SUPPLEMENT

# Hardcoded copy of the gap list (the 67 ids that had no believed_spec). Kept
# inline so the guard does not depend on a /tmp file.
_GAP_RULE_IDS = (
    "uk_effect_source_pathology_classified",
    "uk_effect_application_overlay_no_textual_action_observed",
    "uk_effect_lowering_no_supported_action_rejected",
    "uk_effect_missing_structural_payload_rejected",
    "uk_effect_pdf_only_affecting_source_missing_payload_rejected",
    "uk_effect_replay_applicability_filter_rejected",
    "uk_affecting_act_compound_reference_split_fallback",
    "uk_affecting_act_missing_current_enacted_source_selected",
    "uk_effect_inserted_section_p1group_heading_carrier_lowered",
    "uk_affecting_act_block_amendment_payload_descendant_ref_rejected",
    "uk_effect_chained_insertion_anchor_lowered",
    "uk_effect_source_provision_order_normalized",
    "uk_effect_added_type_source_structuralized",
    "uk_effect_all_occurrences_substitution_text_patch",
    "uk_affecting_act_current_shell_enacted_source_selected",
    "uk_affecting_act_article_schedule_payload_source_extracted",
    "uk_effect_source_payload_without_instruction_context_rejected",
    "uk_effect_repeal_table_structural_repeal_unresolved",
    "uk_effect_instruction_text_payload_rejected",
    "uk_affecting_act_xml_missing_rejected",
    "uk_effect_heading_facet_word_patch_lowered",
    "uk_affecting_act_schedule_part_standalone_split_rejected",
    "uk_effect_source_parent_following_provisions_substitution_text_patch",
    "uk_effect_corresponding_table_entry_word_substitution",
    "uk_effect_direct_section_paragraph_target_normalized",
    "uk_effect_overlap_substitution_unlowered",
    "uk_effect_source_carried_after_quoted_anchor_insert_text_patch",
    "uk_effect_non_substantive_payload_rejected",
    "uk_effect_empty_type_quoted_anchor_word_insertion_inferred",
    "uk_effect_repeal_table_quoted_words_text_repeal_unresolved",
    "uk_effect_commencement_source_rejected",
    "uk_effect_heading_only_ref_rejected",
    "uk_effect_metadata_renumber_lowered",
    "uk_effect_source_carried_definition_entry_insert_text_patch",
    "uk_effect_feed_empty_recorded",
    "uk_effect_source_carried_quoted_text_substitution_text_patch",
    "uk_effect_heading_facet_after_anchor_insert_lowered",
    "uk_effect_heading_facet_full_replacement_lowered",
    "uk_effect_source_schedule_parent_payload_retargeted",
    "uk_affecting_act_nonaddressable_schedule_part_context_ignored",
    "uk_effect_heading_facet_append_lowered",
    "uk_effect_crossheading_before_anchor_text_patch_lowered",
    "uk_effect_labeled_child_end_range_text_patch",
    "uk_effect_inserted_p1group_heading_carrier_lowered",
    "uk_effect_broad_schedule_flat_payload_rejected",
    "uk_effect_structural_pseudo_definition_target_rejected",
    "uk_effect_source_heading_facet_target_refined",
    "uk_effect_heading_facet_source_parent_full_replacement_lowered",
    "uk_effect_heading_facet_source_parent_full_replacement_text_patch",
    "uk_effect_child_qualified_final_word_omission_text_patch",
    "uk_effect_appropriate_place_definition_entry_insert_rejected",
    "uk_effect_grouped_anchor_occurrence_substitution_text_patch",
    "uk_affecting_act_parenthesized_range_source_extracted",
    "uk_effect_metadata_sibling_renumber_lowered",
    "uk_effect_source_carried_definition_entry_substitution_text_patch",
    "uk_effect_source_payload_sibling_range_expanded",
    "uk_effect_empty_type_as_if_words_omitted_rejected",
    "uk_effect_repeal_table_mixed_structural_word_repeal_split",
    "uk_effect_structural_sibling_insert_rejected",
    "uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch",
    "uk_effect_metadata_cross_container_renumber_rejected",
    "uk_effect_source_parent_definition_child_substitution_text_patch",
    "uk_effect_whole_act_word_level_text_patch_rejected",
    "uk_effect_repeal_table_definition_entry_text_repeal_unresolved",
    "uk_effect_appropriate_place_insert_rejected",
    "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
    "uk_effect_crossheading_replace_rejected",
    "uk_effect_external_act_target_rejected",
)

# Dynamically-suffixed ids that are minted as f"{base}_unresolved" at emission
# time and so do NOT appear as a literal string under the source tree. Their
# cataloged base does. Excluded from the literal-grep grounding check, not from
# the coverage check.
_DYNAMIC_SUFFIX_IDS = {
    "uk_effect_repeal_table_definition_entry_text_repeal_unresolved",
}

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEARCH_ROOTS = (
    _REPO_ROOT / "src" / "lawvm" / "uk_legislation",
    _REPO_ROOT / "src" / "lawvm" / "tools",
)


def test_gap_list_has_exactly_68_ids() -> None:
    assert len(_GAP_RULE_IDS) == 68
    assert len(set(_GAP_RULE_IDS)) == 68, "duplicate id in the gap list"


def test_every_gap_id_has_nonempty_prose_spec() -> None:
    missing = [rid for rid in _GAP_RULE_IDS if rid not in _UK_RULE_SPECS_SUPPLEMENT]
    assert not missing, f"uncataloged gap ids: {missing}"
    for rid in _GAP_RULE_IDS:
        spec = _UK_RULE_SPECS_SUPPLEMENT[rid]
        assert isinstance(spec, str)
        # Non-empty prose: more than a token, ends like a sentence.
        assert spec.strip(), f"empty spec for {rid}"
        assert len(spec.split()) >= 5, f"spec too terse for {rid}: {spec!r}"
        assert spec.rstrip().endswith("."), f"spec not a sentence for {rid}: {spec!r}"


def test_supplement_has_no_extra_entries() -> None:
    extra = set(_UK_RULE_SPECS_SUPPLEMENT) - set(_GAP_RULE_IDS)
    assert not extra, f"supplement carries ids outside the gap list: {sorted(extra)}"


def _grep_present(rule_id: str) -> bool:
    for root in _SEARCH_ROOTS:
        res = subprocess.run(
            ["grep", "-rqF", rule_id, str(root)],
            check=False,
        )
        if res.returncode == 0:
            return True
    return False


def test_keyed_ids_appear_in_source_best_effort() -> None:
    """Best-effort grounding: every literal id should be findable in the UK
    lowering / tools source. Dynamically-suffixed ids are exempt. Any other
    miss is flagged as possibly-renamed."""
    possibly_renamed = [
        rid
        for rid in _GAP_RULE_IDS
        if rid not in _DYNAMIC_SUFFIX_IDS and not _grep_present(rid)
    ]
    assert not possibly_renamed, (
        "possibly-renamed rule_ids (no literal emission site found): "
        f"{possibly_renamed}"
    )
