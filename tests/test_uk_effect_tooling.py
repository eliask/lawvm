from __future__ import annotations

import hashlib
import json
from lxml import etree as ET
from argparse import Namespace
from types import SimpleNamespace

import pytest

from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import (
    FacetKind,
    IRNodeKind,
    StructuralAction,
    TextPatchKindEnum,
)
from lawvm.tools import uk_effects
from lawvm.tools import uk_effect
from lawvm.tools import uk_replay
from lawvm.tools import uk_semantic_claims
from lawvm.tools.uk_effect import (
    _collect_target_shape,
    _fmt_target,
    _resolve_parent_presence,
    _resolve_target_presence,
    _source_state as _uk_effect_source_state,
    blocking_lowering_rejection_rule_counts,
    has_blocking_lowering_rejection,
    lowering_observation_rule_counts,
    lowering_rejection_rule_counts,
    uk_effect_report_jsonable,
)
from lawvm.tools.uk_claim_templates import (
    _definition_entry_terms,
    manual_compile_suggested_claim_template,
)
from lawvm.tools.uk_effects import (
    UK_CLAIM_TEMPLATE_RULE_IDS,
    _EffectFilters,
    _EffectReportRow,
    _EffectSummary,
    _EffectSummaryContext,
    _clear_context_resolver_lookup_caches,
    _effect_context_source_jsonable,
    _effect_report_row_jsonable,
    _effect_row_matches_filters,
    _effect_rows_to_summarize,
    _effect_summary_matches_filters,
    _manual_compile_evidence_row_jsonable,
    _print_uk_effects_summary,
    _source_state as _uk_effects_source_state,
    _write_manual_compile_evidence_jsonl,
    summarize_uk_effect,
    uk_effects_report_jsonable,
    uk_effects_summary_counts,
)
from lawvm.uk_legislation.lowering_records import (
    append_manual_compile_frontier_diagnostic,
)
from lawvm.uk_legislation.compiled_effect_facts import uk_compiled_effect_facts
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
    uk_manual_claim_template_status,
)
from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord
from lawvm.uk_legislation import witness_builders


def test_uk_replay_timeline_uses_witness_temporal_event_builder() -> None:
    """Regression: timeline mode must not call a stale uk_amendment_replay attr."""
    assert uk_replay._uk_temporal_events_from_ops is witness_builders._uk_temporal_events_from_ops


def test_uk_claim_template_rule_id_set_tracks_supported_templates() -> None:
    expected_rule_ids = {
        "uk_manual_frontier_appropriate_place_candidate",
        "uk_manual_frontier_appropriate_place_definition_entry_candidate",
        "uk_manual_frontier_appropriate_place_index_entry_candidate",
        "uk_manual_frontier_amendment_program_target_candidate",
        "uk_manual_frontier_cross_container_renumber_candidate",
        "uk_manual_frontier_crossheading_candidate",
        "uk_manual_frontier_deictic_amendment_program_target_candidate",
        "uk_manual_frontier_deictic_structural_sibling_insert_candidate",
        "uk_manual_frontier_definition_child_and_tail_substitution_candidate",
        "uk_manual_frontier_definition_child_structural_insert_candidate",
        "uk_manual_frontier_definition_child_structural_substitution_candidate",
        "uk_manual_frontier_definition_list_end_insert_candidate",
        "uk_manual_frontier_heading_facet_candidate",
        "uk_manual_frontier_mixed_body_heading_text_substitution_split",
        "uk_manual_frontier_parser_or_extraction_candidate",
        "uk_manual_frontier_range_to_container_candidate",
        "uk_manual_frontier_referent_qualified_text_substitution_candidate",
        "uk_manual_frontier_repeal_table_candidate",
        "uk_manual_frontier_schedule_list_entry_candidate",
        "uk_manual_frontier_schedule_note_candidate",
        "uk_manual_frontier_savings_qualified_text_omission_candidate",
        "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate",
        "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate",
        "uk_manual_frontier_source_carried_structured_text_patch_candidate",
        "uk_manual_frontier_source_carried_structured_tail_substitution_candidate",
        "uk_manual_frontier_structural_child_range_substitution_candidate",
        "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate",
        "uk_manual_frontier_structural_sibling_insert_candidate",
        "uk_manual_frontier_table_appropriate_place_candidate",
        "uk_manual_frontier_table_column_insert_candidate",
        "uk_manual_frontier_table_crossheading_candidate",
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_entry_placement_insert",
        "uk_manual_frontier_whole_act_word_level_text_patch_candidate",
    }
    assert UK_CLAIM_TEMPLATE_RULE_IDS == expected_rule_ids
    assert UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS == expected_rule_ids


def test_uk_claim_template_definition_entry_terms_extracts_multiple_entries() -> None:
    terms = _definition_entry_terms(
        "“EEA Agreement” means the Agreement on the European Economic Area; "
        "“EEA State” means a State which is a contracting party to that Agreement; "
        "“EEA Agreement” means duplicate publisher text."
    )

    assert terms == ("EEA Agreement", "EEA State")


def test_heading_claim_template_carries_source_parent_instruction_context() -> None:
    effect = UKEffectRecord(
        effect_id="key-heading-parent-context",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2015-10-16",
        affected_uri="/id/uksi/2010/1504/regulation/13/heading",
        affected_class="UnitedKingdomStatutoryInstrument",
        affected_year="2010",
        affected_number="1504",
        affected_provisions="reg. 13 heading",
        affecting_uri="/id/uksi/2015/1682/schedule/paragraph/10/bb",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2015",
        affecting_number="1682",
        affecting_provisions="Sch. para. 10(bb)",
        affecting_title="Office of Rail Regulation change",
    )
    row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="heading_facet_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": (
                        "uk_effect_source_payload_without_instruction_context_rejected"
                    ),
                    "reason_code": "source_payload_without_instruction_context",
                    "parser": "parse_fragment_substitution",
                    "blocking": True,
                    "source_parent_id": "schedule-paragraph-10",
                    "source_parent_context_preview": (
                        "In the following enactments and in the headings referred to, "
                        "for a reference to the Office of Rail Regulation substitute "
                        "a reference to the Office of Rail and Road—"
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "bb regulation 13 (enforcement body: the Office of Rail Regulation);"
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires an explicit manual claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_source_payload_without_instruction_context_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_source_payload_without_instruction_context_rejected",
            ),
        ),
    )

    template = manual_compile_suggested_claim_template(
        statute_id="uksi/2010/1504",
        row=row,
    )

    assert template["action_family"] == "facet_text_rewrite"
    assert template["text_match"] == "the Office of Rail Regulation"
    assert template["replacement"] == "the Office of Rail and Road"
    assert template["payload_fragment_preview"].startswith("bb regulation 13")
    assert template["source_parent_id"] == "schedule-paragraph-10"
    assert template["source_context_rule_id"] == (
        "uk_effect_source_payload_without_instruction_context_rejected"
    )
    assert template["source_context_reason_code"] == (
        "source_payload_without_instruction_context"
    )
    assert template["source_context_parser"] == "parse_fragment_substitution"
    assert template["source_context_used_for_text_pair"] is True
    assert "complete_source_parent_instruction_context" in template[
        "required_ownership"
    ]
    assert "claim_uses_complete_parent_instruction_not_payload_fragment" in template[
        "required_validator_checks"
    ]
    assert template["executable"] is False


def test_uk_compiled_effect_facts_preserve_source_pathology_wire_shape() -> None:
    op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("part", "2"), ("chapter", "1"))),
        payload=IRNode(kind=IRNodeKind.CHAPTER, label="1", text="Chapter text"),
    )

    facts = uk_compiled_effect_facts(
        ops=(op,),
        lowering_rejections=(
            {"rule_id": "before"},
            {
                "rule_id": "uk_effect_range_to_container_substitution_rejected",
                "target_ref": "Part 2 Chapter 1",
            },
        ),
        lowering_rejection_start_index=1,
    )

    assert facts.op_actions == ("replace",)
    assert facts.payload_kinds == ("chapter",)
    assert facts.payload_texts == ("Chapter text",)
    assert facts.target_paths == ("part:2/chapter:1", "Part 2 Chapter 1")
    assert facts.lowering_rule_ids == (
        "uk_effect_range_to_container_substitution_rejected",
    )

    formatted_facts = uk_compiled_effect_facts(
        ops=(
            LegalOperation(
                op_id="op-2",
                sequence=2,
                action=StructuralAction.TEXT_REPLACE,
                target=LegalAddress(
                    path=(("section", "1"),),
                    special=FacetKind.HEADING,
                ),
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="  A   B  "),
            ),
        ),
        target_formatter=_fmt_target,
        payload_text_formatter=lambda text: " ".join(text.split()),
    )

    assert formatted_facts.target_paths == ("section:1/heading",)
    assert formatted_facts.payload_texts == ("A B",)


def test_uk_manual_claim_template_status_only_labels_actionable_rows() -> None:
    assert (
        uk_manual_claim_template_status(
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
        )
        == "available"
    )
    assert (
        uk_manual_claim_template_status(
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_unclassified",
        )
        == "not_available"
    )
    assert (
        uk_manual_claim_template_status(
            manual_compile_status="deterministic_frontend_supported",
            manual_compile_rule_id="uk_manual_frontier_deterministic_supported",
        )
        == ""
    )


@pytest.mark.parametrize("rule_id", sorted(UK_CLAIM_TEMPLATE_RULE_IDS))
def test_uk_claim_template_rule_ids_all_render_nonempty_templates(rule_id: str) -> None:
    effect = UKEffectRecord(
        effect_id=f"eff-{rule_id}",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                'At the appropriate place insert— "new term" means X. '
                'For "old" substitute "new".'
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=rule_id,
            manual_compile_reason="test",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    assert payload["suggested_claim_template"]["schema"] == (
        "lawvm.uk_semantic_compile_claim_template.v1"
    )
    assert payload["suggested_claim_template"]["executable"] is False
    if payload["suggested_claim_template"]["action_family"] != "parser_or_extraction_gap":
        assert payload["suggested_claim_template"][
            "required_operation_family_proof_semantics"
        ]
    required_semantics = set(
        payload["suggested_claim_template"].get(
            "required_operation_family_proof_semantics",
            (),
        )
    )
    assert required_semantics <= uk_semantic_claims.UK_OPERATION_FAMILY_PROOF_SEMANTICS


def test_uk_manual_compile_evidence_jsonl_templates_referent_qualified_substitution() -> None:
    effect = UKEffectRecord(
        effect_id="eff-referent-qualified",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2003-07-01",
        affected_uri="/id/ukpga/1996/61/section/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1996",
        affected_number="61",
        affected_provisions="s. 21",
        affecting_uri="/id/ukpga/2003/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2003",
        affecting_number="20",
        affecting_provisions="Sch. 2 para. 22(a)",
        affecting_title="Railways and Transport Safety Act 2003",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="referent_qualified_text_substitution_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=("section-21",),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P4",
            source_extracted_text_preview=(
                'for "he" and "him", where they refer to the Rail Regulator, '
                'substitute "it"'
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_referent_qualified_text_substitution_candidate"
            ),
            manual_compile_reason="Referent-sensitive substitution needs a coreference claim.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1996/61",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1996/61",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "referent_qualified_text_substitution"
    assert template["placement_family"] == "referent_sensitive_occurrence_claim_required"
    assert template["text_preimages"] == ["he", "him"]
    assert template["referent_entity"] == "the Rail Regulator"
    assert template["replacement"] == "it"
    assert "claim_proves_each_mutated_occurrence_refers_to_the_named_entity" in (
        template["required_validator_checks"]
    )


def test_uk_manual_compile_evidence_jsonl_templates_whole_act_word_patch() -> None:
    effect = UKEffectRecord(
        effect_id="eff-whole-act-word-patch",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2009-10-01",
        affected_uri="/id/ukpga/1949/88",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1949",
        affected_number="88",
        affected_provisions="Act",
        affecting_uri="/id/ukpga/2005/4",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2005",
        affecting_number="4",
        affecting_provisions="Sch. 11 para. 4",
        affecting_title="Constitutional Reform Act 2005",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="whole_act_word_level_text_patch_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=("/whole_act",),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_whole_act_word_level_text_patch_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                'In each of the enactments listed in sub-paragraph (3) for '
                '"Supreme Court" or "Supreme Court of Judicature" in each place '
                'substitute "Senior Courts". This paragraph does not apply to '
                'those words in the short title or title of any enactment.'
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_whole_act_word_level_text_patch_candidate",
            manual_compile_reason="Whole-Act text patch needs a listed-enactment compiler.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_whole_act_word_level_text_patch_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_whole_act_word_level_text_patch_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1949/88",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1949/88",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "whole_act_listed_enactments_text_patch"
    assert template["placement_family"] == "listed_enactment_whole_act_scope_with_exclusions"
    assert template["text_preimages"] == [
        "Supreme Court of Judicature",
        "Supreme Court",
    ]
    assert template["replacement"] == "Senior Courts"
    assert "claim_excludes_title_and_short_title_surfaces" in (
        template["required_validator_checks"]
    )


def test_uk_manual_compile_evidence_jsonl_templates_parser_or_extraction_gap() -> None:
    effect = UKEffectRecord(
        effect_id="eff-parser-gap",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2017-04-06",
        affected_uri="/id/ukpga/2008/17/section/60",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2008",
        affected_number="17",
        affected_provisions="s. 60(4)",
        affecting_uri="/id/ukpga/2016/22",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="22",
        affecting_provisions="Sch. 4 para. 8(a)",
        affecting_title="Housing and Planning Act 2016",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=("section-60",),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_overlap_substitution_unlowered",
                    "reason_code": "overlap_substitution_parse_failed",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "for paragraph (b) (Regulator's consent) substitute— "
                "b Notification of regulator ;"
            ),
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id="uk_manual_frontier_parser_or_extraction_candidate",
            manual_compile_reason="Deterministic parser or extraction work remains.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2008/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2008/17",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "parser_or_extraction_gap"
    assert template["placement_family"] == "overlap_substitution_parse_failed"
    assert "source_instruction_grammar_production" in template["required_ownership"]
    assert "target_scope_is_the_effect_target_or_source_named_descendant_only" in (
        template["required_validator_checks"]
    )


def test_manual_frontier_diagnostic_records_claim_template_status() -> None:
    diagnostics: list[dict[str, object]] = []
    append_manual_compile_frontier_diagnostic(
        diagnostics,
        effect=UKEffectRecord(
            effect_id="effect-1",
            effect_type="words substituted",
            applied=True,
            requires_applied=True,
            modified="2024-01-01",
            affected_uri="/id/ukpga/2000/1/section/10",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions="s. 10",
            affecting_uri="/id/ukpga/2024/1",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2024",
            affecting_number="1",
            affecting_provisions="s. 2",
            affecting_title="Test Act 2024",
        ),
        source_pathology="unhandled_instruction_text",
        extracted_tag="P1",
        extracted_text='In the title to section 10, for "old" substitute "new".',
        lowering_rejections_out=[
            {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True}
        ],
        lowering_rejection_start_index=0,
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert diagnostics[0]["manual_compile_status"] == "manual_compile_candidate"
    assert (
        diagnostics[0]["manual_compile_rule_id"]
        == "uk_manual_frontier_heading_facet_candidate"
    )
    assert diagnostics[0]["owner_phase"] == "affecting_source_extraction"
    assert diagnostics[0]["suggested_claim_template_status"] == "available"
    assert diagnostics[0]["executable"] is False
    assert diagnostics[0]["replay_authorized"] is False
    assert diagnostics[0]["authorization_status"] == "manual_claim_required"
    assert (
        diagnostics[0]["authorization_rule_id"]
        == "uk_execution_authorization_manual_claim_required"
    )
    assert "mutation_boundary_proof" in diagnostics[0]["required_proofs"]
    assert diagnostics[0]["safe_default"] == (
        "block_until_validated_claim_authorizes_replay"
    )


def test_uk_effect_fmt_target_preserves_heading_facet() -> None:
    target = LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING)

    assert _fmt_target(target) == "section:1/heading"


class _FakeResolver:
    def __init__(self, mapping: dict[tuple[tuple[str, str], ...], str]) -> None:
        self.mapping = mapping

    def _derive_target_eid(self, target: LegalAddress) -> str:
        return self.mapping.get(target.path, "")


def test_uk_effect_record_to_dict_exposes_replay_applicability() -> None:
    effect = UKEffectRecord(
        effect_id="eff-metadata-only",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
        metadata_only=True,
    )

    payload = effect.to_dict()

    assert payload["applied"] is False
    assert payload["requires_applied"] is True
    assert payload["metadata_only"] is True
    assert payload["replay_applicable"] is True
    assert payload["structural"] is True
    assert payload["structural_for_replay"] is True
    assert payload["in_force_date"] == "2025-01-01"
    assert payload["in_force_dates"] == [{"date": "2025-01-01", "prospective": "false"}]


def test_uk_effect_record_to_dict_uses_effective_date_not_first_raw_date() -> None:
    effect = UKEffectRecord(
        effect_id="eff-prospective-first",
        effect_type="repealed",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[
            {"date": "", "prospective": "true"},
            {"date": "2025-03-01", "prospective": "false"},
        ],
    )

    payload = effect.to_dict()

    assert payload["in_force_date"] == "2025-03-01"
    assert payload["in_force_dates"] == [
        {"date": "", "prospective": "true"},
        {"date": "2025-03-01", "prospective": "false"},
    ]


def test_resolve_target_presence_reports_base_and_oracle_hits() -> None:
    target = LegalAddress((("section", "68"), ("subsection", "7"), ("paragraph", "g")))
    resolver = _FakeResolver({target.path: "section-68-7-g"})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids={"section-68-7-g"},
        oracle_eids={"section-68-7-g", "section-68-7-ha"},
    )

    assert resolver_eid == "section-68-7-g"
    assert base_hit is True
    assert oracle_hit is True


def test_resolve_target_presence_handles_missing_resolver_match() -> None:
    target = LegalAddress((("section", "72"), ("subsection", "4"), ("paragraph", "ba")))
    resolver = _FakeResolver({})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids={"section-72-4"},
        oracle_eids={"section-72-4-a", "section-72-4-b"},
    )

    assert resolver_eid == ""
    assert base_hit is False
    assert oracle_hit is False


def test_resolve_parent_presence_reports_parent_hits() -> None:
    parent_eid, base_hit, oracle_hit = _resolve_parent_presence(
        "section-72-4-ba",
        base_eids={"section-72-4"},
        oracle_eids={"section-72-4", "section-72-4-c"},
    )

    assert parent_eid == "section-72-4"
    assert base_hit is True
    assert oracle_hit is True


def test_resolve_target_presence_matches_mixed_alphanumeric_case_insensitively() -> None:
    target = LegalAddress((("schedule", "7"), ("paragraph", "10"), ("subsection", "1a"), ("item", "a")))
    resolver = _FakeResolver({target.path: "schedule-7-paragraph-10-1a-a"})

    resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
        target,
        resolver=resolver,
        base_eids=set(),
        oracle_eids={"schedule-7-paragraph-10-1A-a"},
    )

    assert resolver_eid == "schedule-7-paragraph-10-1a-a"
    assert base_hit is False
    assert oracle_hit is True


def test_resolve_parent_presence_matches_mixed_alphanumeric_case_insensitively() -> None:
    parent_eid, base_hit, oracle_hit = _resolve_parent_presence(
        "schedule-7-paragraph-10-1a-a",
        base_eids=set(),
        oracle_eids={"schedule-7-paragraph-10-1A"},
    )

    assert parent_eid == "schedule-7-paragraph-10-1a"
    assert base_hit is False
    assert oracle_hit is True


def test_collect_target_shape_falls_back_to_text_map_and_descendant_hits() -> None:
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-28-1",
        text_map={"section-28-1": "1 Commissioners may inquire into the claim."},
        descendant_hit=True,
    )

    assert has_text is True
    assert has_children is True
    assert texts == ["1 Commissioners may inquire into the claim."]


def test_collect_target_shape_uses_subtree_text_when_container_has_no_text_map() -> None:
    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="6",
        text="",
        attrs={"eId": "section-7a-6"},
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="a",
                text="NHS England may publish information.",
                attrs={"eId": "section-7a-6-a"},
                children=(),
            ),
        ),
    )
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7A",
                    text="",
                    attrs={"eId": "section-7a"},
                    children=(subsection,),
                ),
            ),
        ),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-7a-6",
        text_map={},
        descendant_hit=True,
    )

    assert has_text is True
    assert has_children is True
    assert texts == ["NHS England may publish information."]


def test_collect_target_shape_uses_descendant_text_map_when_ir_eid_missing() -> None:
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-7a-6",
        text_map={
            "section-7a-6-a": "NHS England may publish information.",
            "section-7a-7": "Sibling text must not be included.",
        },
        descendant_hit=True,
    )

    assert has_text is True
    assert has_children is True
    assert texts == ["NHS England may publish information."]


def test_collect_target_shape_uses_case_insensitive_text_map_eid() -> None:
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="6",
                    text="",
                    attrs={"eId": "section-7a-6"},
                    children=(),
                ),
            ),
        ),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-7a-6",
        text_map={"section-7A-6": "6 the provision mentions NHS England."},
        descendant_hit=False,
    )

    assert has_text is True
    assert has_children is False
    assert texts == ["6 the provision mentions NHS England."]


def test_collect_target_shape_includes_direct_and_descendant_container_text() -> None:
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="6",
                    text="The provision mentioned in subsection (2)(c) may include provision about—",
                    attrs={"eId": "section-7a-6"},
                    children=(),
                ),
            ),
        ),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-7a-6",
        text_map={
            "section-7A-6-a": "the analysis by NHS England of information",
            "section-7A-6-b": "the publication by NHS England of information",
        },
        descendant_hit=True,
    )

    assert has_text is True
    assert has_children is True
    assert texts == [
        "The provision mentioned in subsection (2)(c) may include provision about—",
        "the analysis by NHS England of information the publication by NHS England of information",
    ]


def test_collect_target_shape_uses_heading_facet_carrier_text() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        text="",
        attrs={"eId": "section-1"},
        children=(),
    )
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    label=None,
                    text="Reduced rates of SDLT on residential property for a temporary period",
                    children=(section,),
                ),
            ),
        ),
        supplements=(),
    )

    has_text, has_children, texts = _collect_target_shape(
        statute,
        eid="section-1",
        text_map={"section-1": "body text should not satisfy heading selector"},
        descendant_hit=True,
        target=LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING),
    )

    assert has_text is True
    assert has_children is True
    assert texts == ["Reduced rates of SDLT on residential property for a temporary period"]


def test_manual_frontier_classifies_table_reference_instructions_as_manual() -> None:
    from lawvm.uk_legislation.source_adjudication import classify_uk_manual_compile_frontier

    result = classify_uk_manual_compile_frontier(
        effect_type="words inserted",
        source_pathology="unhandled_instruction_text",
        extracted_tag="P3",
        extracted_text='after the reference to "advocate", insert a reference to "CMA";',
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
                "affected_provisions": "sch. 9 table",
                "original_affected_provisions": "sch. 9 table",
            },
        ),
        compiled_op_count=0,
        replay_applicable=True,
        structural_for_replay=True,
    )

    assert result["status"] == "manual_compile_candidate"
    assert result["rule_id"] == "uk_manual_frontier_table_entry_candidate"


def test_summarize_uk_effect_uses_heading_facet_text_for_batch_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch uk-effects must compare heading-facet ops against heading text, not host body."""
    from lawvm.uk_legislation import effects as uk_effects_model
    from lawvm.uk_legislation import source_adjudication
    from lawvm.uk_legislation import uk_amendment_replay

    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="17",
        text="Body text without the quoted heading anchor.",
        attrs={"id": "section-17"},
        children=(),
    )
    statute = IRStatute(
        statute_id="ukpga/test",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    label=None,
                    text="Interest paid or credited by banks etc. without deduction of income tax",
                    children=(section,),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="op-heading",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "17"),), special=FacetKind.HEADING),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector("banks"),
            replacement="banks, building societies",
        ),
    )
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/1970/9/section/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1970",
        affected_number="9",
        affected_provisions="s. 17",
        affecting_uri="/id/ukpga/2025/1/schedule/1/paragraph/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="Sch. 1 para. 1",
        affecting_title="Test Amendment Act",
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/test",
        enacted_ir=statute,
        oracle_ir=statute,
        base_eids={"section-17"},
        oracle_eids={"section-17"},
        base_text_map={"section-17": "Body text without the quoted heading anchor."},
        oracle_eid_map={},
        oracle_text_map={"section-17": "Body text without the quoted heading anchor."},
        resolver=SimpleNamespace(_derive_target_eid=lambda target: "section-17"),
        affecting_xml_cache={},
    )
    source_context = SimpleNamespace(
        source_status="absent",
        source_size=0,
        xml_bytes=None,
        root=None,
        authority_layer="EFFECT_FEED_INDEX",
    )

    monkeypatch.setattr(
        uk_effects_model,
        "uk_effect_requires_affecting_source_for_replay",
        lambda _effect, *, applicability_mode: False,
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "_build_affecting_source_context",
        lambda **_kwargs: (source_context, None),
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "_select_enacted_source_for_current_shell",
        lambda **_kwargs: (source_context, None, ()),
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "compile_effect_to_ir_ops",
        lambda *_args, **_kwargs: [op],
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "mark_nonreplay_lowering_rejections_nonblocking",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "mark_source_pathology_nonreplay_lowering_rejections_nonblocking",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_amendment_replay,
        "append_source_pathology_filter_lowering_rejections",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        source_adjudication,
        "classify_uk_effect_source_pathology",
        lambda **_kwargs: "",
    )

    summary = summarize_uk_effect(effect, archive=object(), context=context)

    assert summary.compare_shape == ""
    assert summary.manual_compile_rule_id == "uk_manual_frontier_deterministic_supported"


def test_lowering_rejection_rule_counts_are_stable() -> None:
    rows = [
        {"rule_id": "uk_effect_lowering_no_ops_rejected"},
        {"rule_id": "uk_effect_lowering_no_ops_rejected"},
        {"rule_id": "uk_effect_payload_missing"},
        {},
    ]
    assert lowering_observation_rule_counts(rows) == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
        "unknown": 1,
    }
    # Compatibility alias: old callers named all lowering observations
    # "rejections" even when the record is nonblocking.
    assert lowering_rejection_rule_counts(
        rows
    ) == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
        "unknown": 1,
    }


def test_blocking_lowering_rejection_detection_uses_shared_compile_classifier() -> None:
    assert has_blocking_lowering_rejection(()) is False
    assert has_blocking_lowering_rejection(
        (
            {"blocking": False},
            {"rule_id": "note", "strict_disposition": "record"},
        )
    ) is False
    assert has_blocking_lowering_rejection(({"rule_id": "legacy_block"},)) is True
    assert has_blocking_lowering_rejection(({"rule_id": "block", "blocking": True},)) is True
    assert blocking_lowering_rejection_rule_counts(
        [
            {"rule_id": "block", "blocking": True},
            {"rule_id": "note", "strict_disposition": "record"},
            {"rule_id": "legacy_block"},
            {"rule_id": "block", "blocking": True},
        ]
    ) == {"block": 2, "legacy_block": 1}


def test_uk_effect_source_state_distinguishes_absent_too_small_and_available() -> None:
    assert _uk_effect_source_state(None) == ("absent", 0)
    assert _uk_effect_source_state(b"") == ("too_small", 0)
    assert _uk_effect_source_state(b"<short/>") == ("too_small", 8)
    assert _uk_effect_source_state(b"x" * 100) == ("available", 100)


def test_summarize_uk_effect_preserves_lowering_rejections(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-1",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    def fake_compile_effect_to_ir_ops(effect_arg, extracted, **kwargs):  # noqa: ANN001
        del effect_arg, extracted
        kwargs["lowering_rejections_out"].append(
            {
                "rule_id": "uk_effect_lowering_no_ops_rejected",
                "phase": "lowering",
                "effect_id": "eff-1",
            }
        )
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        fake_compile_effect_to_ir_ops,
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.n_ops == 0
    assert summary.candidate is False
    assert summary.lowering_rejections == (
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "phase": "lowering",
            "effect_id": "eff-1",
        },
    )
    assert summary.source_acquisition_rejections == (
        {
            "rule_id": "uk_affecting_act_xml_missing_rejected",
            "family": "source_pathology",
            "phase": "acquisition",
            "effect_id": "eff-1",
            "affecting_act_id": "ukpga/2025/1",
            "locator": "https://www.legislation.gov.uk/ukpga/2025/1/data.xml",
            "reason": "UK affecting act XML was missing from the archive, so the effect source fragment could not be extracted.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )


def test_summarize_uk_effect_reuses_affecting_source_context_cache(monkeypatch) -> None:
    from lawvm.uk_legislation.source_context import UKAffectingSourceContext

    effect = UKEffectRecord(
        effect_id="eff-cache-1",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1/section/2",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )
    build_calls: list[str] = []
    enacted_cache_ids: list[int] = []

    def fake_build_affecting_source_context(**kwargs):  # noqa: ANN001
        build_calls.append(kwargs["locator"])
        return (
            UKAffectingSourceContext(
                xml_bytes=kwargs["xml_bytes"],
                root=None,
                parent_map=None,
                exact_id_map={},
                sequence_map={},
                source_status="available",
                source_size=len(kwargs["xml_bytes"] or b""),
                locator=kwargs["locator"],
                authority_layer=kwargs["authority_layer"],
            ),
            None,
        )

    def fake_select_enacted_source_for_current_shell(**kwargs):  # noqa: ANN001
        enacted_cache = kwargs["enacted_context_cache"]
        enacted_cache_ids.append(id(enacted_cache))
        enacted_cache.setdefault("sentinel", kwargs["current_context"])
        return kwargs["current_context"], kwargs["current_el"], ()

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _affecting_act_id, _archive: b"<Legislation>" + (b"x" * 128) + b"</Legislation>",
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._build_affecting_source_context",
        fake_build_affecting_source_context,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._select_enacted_source_for_current_shell",
        fake_select_enacted_source_for_current_shell,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda *_args, **_kwargs: [],
    )

    summarize_uk_effect(effect, archive=object(), context=context)
    summarize_uk_effect(effect, archive=object(), context=context)

    assert build_calls == ["https://www.legislation.gov.uk/ukpga/2025/1/data.xml"]
    assert len(set(enacted_cache_ids)) == 1
    assert "sentinel" in context.affecting_enacted_context_cache


def test_summarize_uk_effect_bounds_affecting_source_caches(monkeypatch) -> None:
    from lawvm.uk_legislation.source_context import UKAffectingSourceContext

    def make_effect(number: int) -> UKEffectRecord:
        return UKEffectRecord(
            effect_id=f"eff-cache-bound-{number}",
            effect_type="inserted",
            applied=True,
            requires_applied=False,
            modified="2025-01-01",
            affected_uri=f"/id/ukpga/2000/1/section/{number}",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions=f"s. {number}",
            affecting_uri=f"/id/ukpga/2025/{number}/section/2",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number=str(number),
            affecting_provisions="s. 2",
            affecting_title="Test Act",
            in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
        )

    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
        affecting_source_cache_limit=2,
    )

    def fake_build_affecting_source_context(**kwargs):  # noqa: ANN001
        return (
            UKAffectingSourceContext(
                xml_bytes=kwargs["xml_bytes"],
                root=None,
                parent_map=None,
                exact_id_map={},
                sequence_map={},
                source_status="available",
                source_size=len(kwargs["xml_bytes"] or b""),
                locator=kwargs["locator"],
                authority_layer=kwargs["authority_layer"],
            ),
            None,
        )

    def fake_select_enacted_source_for_current_shell(**kwargs):  # noqa: ANN001
        effect = kwargs["effect"]
        kwargs["enacted_context_cache"][effect.affecting_act_id] = kwargs["current_context"]
        return kwargs["current_context"], kwargs["current_el"], ()

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, _archive: (
            f"<Legislation>{affecting_act_id}</Legislation>".encode()
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._build_affecting_source_context",
        fake_build_affecting_source_context,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._select_enacted_source_for_current_shell",
        fake_select_enacted_source_for_current_shell,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda *_args, **_kwargs: [],
    )

    for number in (1, 2, 3):
        summarize_uk_effect(make_effect(number), archive=object(), context=context)

    assert set(context.affecting_xml_cache) == {"ukpga/2025/2", "ukpga/2025/3"}
    assert set(context.affecting_source_context_cache) == {
        ("ukpga/2025/2", "AFFECTING_ACT_TEXT"),
        ("ukpga/2025/3", "AFFECTING_ACT_TEXT"),
    }
    assert set(context.affecting_enacted_context_cache) == {
        "ukpga/2025/2",
        "ukpga/2025/3",
    }


def test_clear_context_resolver_lookup_caches() -> None:
    class FakeResolver:
        def __init__(self) -> None:
            self._eid_search_cache = {"a": object()}
            self._target_lookup_cache = {"b": object()}
            self._recursive_match_cache = {"c": object()}

    resolver = FakeResolver()
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=resolver,
        affecting_xml_cache={},
    )

    _clear_context_resolver_lookup_caches(context)

    assert resolver._eid_search_cache == {}
    assert resolver._target_lookup_cache == {}
    assert resolver._recursive_match_cache == {}


def test_summarize_uk_effect_uses_observed_source_extraction(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="eff-observed-extract",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="Sch. 22 para. 88(1)(a)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    extracted = ET.fromstring("<P3 id='schedule-22-paragraph-88-a'>source payload</P3>")
    observation = {
        "rule_id": "uk_affecting_act_implicit_first_subparagraph_context_ignored",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }
    seen_extracted: list[ET._Element | None] = []

    def fake_extract_with_observations(_context, effect_arg):  # noqa: ANN001
        assert effect_arg is effect
        return extracted, (observation,)

    def fake_compile_effect_to_ir_ops(_effect, extracted_arg, **_kwargs):  # noqa: ANN001
        seen_extracted.append(extracted_arg)
        return [
            LegalOperation(
                op_id="eff-observed-extract",
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "1"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="payload"),
            )
        ]

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _affecting_act_id, _archive: (
            b"<Legislation><Body><P1 id='x'>"
            + (b"x" * 128)
            + b"</P1></Body></Legislation>"
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._extract_from_affecting_source_context_with_observations",
        fake_extract_with_observations,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        fake_compile_effect_to_ir_ops,
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert seen_extracted == [extracted]
    assert summary.source_extracted is True
    assert summary.source_extracted_tag == "P3"
    assert summary.source_acquisition_rejections == (observation,)


def test_summarize_uk_effect_source_pathology_uses_replay_applicability_mode(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="eff-unapplied",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda effect_arg, extracted, **kwargs: [],
    )

    default_summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )
    effective_date_only_summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
        applicability_mode="effective_date_only",
    )

    assert default_summary.structural_for_replay is False
    assert default_summary.source_pathology == "nonstructural_root_gap"
    assert effective_date_only_summary.structural_for_replay is True
    assert effective_date_only_summary.source_pathology == "missing_extracted_source"


def test_summarize_uk_effect_surfaces_range_to_container_blocking_rejection(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="eff-range-container",
        effect_type="substituted for ss. 3-12 and cross-heading",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="Pt. 2 Ch. 1",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="s. 35(2)",
        affecting_title="Transport (Scotland) Act 2019",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        text="Chapter 1",
        attrs={"eId": "part-2-chapter-1"},
        children=(
            IRNode(
                kind=IRNodeKind.CROSSHEADING,
                text="Bus services improvement partnership plans",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="3A", attrs={"eId": "section-3A"}),
                ),
            ),
        ),
    )
    compiled = [
        LegalOperation(
            op_id="eff-range-container",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "2"), ("chapter", "1"))),
            payload=payload,
        )
    ]

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _affecting_act_id, _archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: compiled,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.source_adjudication.classify_uk_effect_source_pathology",
        lambda **_kwargs: "range_to_container_target_unsupported",
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="asp/2001/2",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.source_pathology == "range_to_container_target_unsupported"
    assert summary.candidate is False
    assert summary.manual_compile_status == "manual_compile_candidate"
    assert summary.manual_compile_rule_id == "uk_manual_frontier_range_to_container_candidate"
    assert summary.lowering_rejections == (
        {
            "rule_id": "uk_effect_range_to_container_substitution_rejected",
            "family": "source_pathology_filter",
            "phase": "lowering",
            "effect_id": "eff-range-container",
            "affecting_act_id": "asp/2019/17",
            "affected_provisions": "Pt. 2 Ch. 1",
            "affecting_provisions": "s. 35(2)",
            "effect_type": "substituted for ss. 3-12 and cross-heading",
            "reason": (
                "UK source substitutes a section range into a container payload; "
                "lowering must own range replacement and lineage before replay"
            ),
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "owner_phase": "typed_elaboration",
            "source_pathology": "range_to_container_target_unsupported",
            "compiled_actions": ("replace",),
            "compiled_targets": ("part:2/chapter:1",),
            "payload_kinds": ("chapter",),
            "payload_roots": (
                {
                    "kind": "chapter",
                    "label": "1",
                    "eid": "part-2-chapter-1",
                    "direct_child_count": 1,
                    "direct_children": (
                        {"kind": "crossheading", "label": "", "eid": ""},
                    ),
                    "truncated_direct_children": False,
                    "descendant_section_count": 1,
                    "descendant_sections": (
                        {"label": "3A", "eid": "section-3A"},
                    ),
                    "truncated_descendant_sections": False,
                },
            ),
            "required_ownership": (
                "source_range",
                "container_payload",
                "lineage_or_migration_events",
                "mutation_boundary",
            ),
            "target_container_ref": "Pt. 2 Ch. 1",
            "source_range_kind": "section",
            "source_range_start": "3",
            "source_range_end": "12",
            "source_range_section_count": 10,
            "source_range_sections": tuple(
                {"label": str(label), "eid": ""} for label in range(3, 13)
            ),
            "truncated_source_range_sections": False,
        },
    )


def test_summarize_uk_effect_records_malformed_affecting_act_xml(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-malformed-source",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Malformed Affecting Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _affecting_act_id, _archive: b"<Legislation><P1>" + (b"x" * 128),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: [],
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.n_ops == 0
    assert summary.candidate is False
    assert len(summary.source_acquisition_rejections) == 1
    rejection = summary.source_acquisition_rejections[0]
    assert rejection["rule_id"] == "uk_affecting_act_xml_parse_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "parse"
    assert rejection["effect_id"] == "eff-malformed-source"
    assert rejection["affecting_act_id"] == "ukpga/2025/1"
    assert rejection["locator"] == "https://www.legislation.gov.uk/ukpga/2025/1/data.xml"
    assert rejection["exception_type"] in ("ParseError", "XMLSyntaxError")  # lxml raises XMLSyntaxError
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"


def test_summarize_uk_effect_records_too_small_affecting_act_xml(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-too-small-source",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Too Small Affecting Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _affecting_act_id, _archive: b"<short/>",
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: [],
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.affecting_source_status == "too_small"
    assert summary.affecting_source_size == len(b"<short/>")
    assert len(summary.source_acquisition_rejections) == 1
    rejection = summary.source_acquisition_rejections[0]
    assert rejection["rule_id"] == "uk_affecting_act_xml_too_small_rejected"
    assert rejection["phase"] == "acquisition"
    assert rejection["source_size"] == len(b"<short/>")
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"


def test_summarize_uk_effect_does_not_require_source_for_commencement_rows(
    monkeypatch,
) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-commencement",
        effect_type="coming into force",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2",
        affecting_title="Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    def fail_if_fetched(_affecting_act_id, _archive):  # noqa: ANN001
        raise AssertionError("commencement rows should not fetch affecting XML")

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        fail_if_fetched,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: [],
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.source_pathology == "nonstructural_root_gap"
    assert summary.source_acquisition_rejections == ()
    assert summary.lowering_rejections == ()
    assert summary.structural_for_replay is False
    assert summary.replay_applicable is True


def test_summarize_uk_effect_records_structural_no_op_lowering_rejection(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-structural-noop",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda effect_arg, extracted, **kwargs: [],
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.n_ops == 0
    assert summary.candidate is False
    assert summary.lowering_rejections == (
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "eff-structural-noop",
            "affecting_act_id": "ukpga/2025/1",
            "affected_provisions": "s. 1",
            "affecting_provisions": "s. 2",
            "effect_type": "inserted",
            "reason": "UK structural effect lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "owner_phase": "canonical_op_compilation",
        },
    )


def test_summarize_uk_effect_suppresses_aggregate_no_op_rejection_with_specific_rejection(monkeypatch) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-specific-noop",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    def fake_compile_effect_to_ir_ops(effect_arg, extracted, **kwargs):  # noqa: ANN001
        del effect_arg, extracted
        kwargs["lowering_rejections_out"].append(
            {
                "rule_id": "uk_effect_missing_structural_payload_rejected",
                "blocking": True,
            }
        )
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        fake_compile_effect_to_ir_ops,
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.candidate is False
    assert [item["rule_id"] for item in summary.lowering_rejections] == [
        "uk_effect_missing_structural_payload_rejected",
    ]
    assert all(item["blocking"] is True for item in summary.lowering_rejections)


@pytest.mark.parametrize(
    ("effect_type", "expected_rule_id", "expected_family"),
    [
        (
            "revoked",
            "uk_effect_nonstructural_lowering_no_ops_rejected",
            "revoked_repeal",
        ),
        (
            "modified",
            "uk_effect_nonstructural_unsupported_no_ops_observed",
            None,
        ),
    ],
)
def test_summarize_uk_effect_records_nonstructural_no_op_rejections(
    monkeypatch,
    effect_type: str,
    expected_rule_id: str,
    expected_family: str | None,
) -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id=f"eff-{effect_type}",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda affecting_act_id, archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda effect_arg, extracted, **kwargs: [],
    )

    summary = summarize_uk_effect(
        effect,
        archive=object(),
        context=_EffectSummaryContext(
            statute_id="ukpga/2000/1",
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )

    assert summary.n_ops == 0
    assert summary.candidate is False
    assert len(summary.lowering_rejections) == 1
    rejection = summary.lowering_rejections[0]
    assert rejection["rule_id"] == expected_rule_id
    expected_blocking = expected_family is not None
    assert rejection["blocking"] is expected_blocking
    assert rejection["strict_disposition"] == ("block" if expected_blocking else "record")
    if expected_family is None:
        assert "nonstructural_replay_candidate_family" not in rejection
    else:
        assert rejection["nonstructural_replay_candidate_family"] == expected_family


def test_uk_effects_summary_counts_are_stable() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    rows = (
        _EffectReportRow(
            effect=UKEffectRecord(
                effect_id="eff-1",
                effect_type="repealed",
                applied=True,
                requires_applied=False,
                modified="2025-01-01",
                affected_uri="/id/ukpga/2000/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 1",
                affecting_uri="/id/ukpga/2025/1",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2025",
                affecting_number="1",
                affecting_provisions="s. 2",
                affecting_title="Test Act",
                in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
            ),
            summary=_EffectSummary(
                source_pathology="",
                compare_shape="commensurable",
                n_ops=2,
                candidate=True,
                resolver_eids=("section-1",),
                lowering_rejections=(),
                replay_applicable=True,
                structural_for_replay=True,
                manual_compile_status="deterministic_frontend_supported",
                manual_compile_rule_id="uk_manual_frontier_deterministic_supported",
                manual_compile_reason="Already lowers without blocking rejections.",
                manual_compile_owner_phase="canonical_op_compilation",
            ),
        ),
        _EffectReportRow(
            effect=UKEffectRecord(
                effect_id="eff-2",
                effect_type="inserted",
                applied=False,
                requires_applied=True,
                modified="2025-01-02",
                affected_uri="/id/ukpga/2000/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 3",
                affecting_uri="/id/ukpga/2025/2",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2025",
                affecting_number="2",
                affecting_provisions="s. 4",
                affecting_title="Other Act",
                in_force_dates=[],
            ),
            summary=_EffectSummary(
                source_pathology="missing_extracted_source",
                compare_shape="oracle_missing_live_branch",
                n_ops=0,
                candidate=False,
                resolver_eids=(),
                lowering_rejections=(
                    {
                        "rule_id": "uk_effect_lowering_no_ops_rejected",
                        "blocking": True,
                        "reason_code": "no_lowerable_operations",
                    },
                    {
                        "rule_id": "uk_effect_payload_missing",
                        "blocking": False,
                        "strict_disposition": "record",
                        "reason_code": "payload_missing",
                    },
                ),
                source_acquisition_rejections=(
                    {
                        "rule_id": "uk_affecting_act_xml_cached_recorded",
                        "blocking": False,
                        "strict_disposition": "record",
                    },
                    {
                        "rule_id": "uk_affecting_act_xml_missing_rejected",
                        "blocking": True,
                    },
                ),
                replay_applicable=False,
                structural_for_replay=False,
                manual_compile_status="source_insufficient",
                manual_compile_rule_id="uk_manual_frontier_missing_payload_source_insufficient",
                manual_compile_reason="No extracted payload is available.",
                manual_compile_owner_phase="affecting_source_extraction",
            ),
        ),
    )

    assert uk_effects_summary_counts(rows) == {
        "matched_effects": 2,
        "matched_effect_count_before_limit": 2,
        "emitted_effect_count": 2,
        "truncated": False,
        "diagnostic_count_scope": "emitted_rows",
        "candidate_counts": {"candidate": 1, "not_candidate": 1},
        "replay_applicability_counts": {
            "replay_applicable": 1,
            "not_replay_applicable": 1,
        },
        "structural_for_replay_counts": {
            "structural_for_replay": 1,
            "not_structural_for_replay": 1,
        },
        "metadata_only_count": 0,
        "applied_count": 1,
        "requires_applied_count": 1,
        "source_pathology_counts": {"__none__": 1, "missing_extracted_source": 1},
        "compare_shape_counts": {"commensurable": 1, "oracle_missing_live_branch": 1},
        "manual_compile_status_counts": {
            "deterministic_frontend_supported": 1,
            "source_insufficient": 1,
        },
        "manual_compile_rule_counts": {
            "uk_manual_frontier_deterministic_supported": 1,
            "uk_manual_frontier_missing_payload_source_insufficient": 1,
        },
        "manual_compile_candidate_rule_counts": {},
        "manual_compile_owner_phase_counts": {
            "affecting_source_extraction": 1,
            "canonical_op_compilation": 1,
        },
        "manual_frontier_work_item_family_counts": {
            "uk_manual_frontier_missing_payload_source_insufficient": 1,
        },
        "manual_frontier_work_item_authorization_status_counts": {
            "source_insufficient": 1,
        },
        "suggested_claim_template_status_counts": {},
        "total_compiled_ops": 2,
        "rows_with_resolver_eids": 1,
        "rows_with_lowering_observations": 1,
        "lowering_observation_rule_counts": {
            "uk_effect_lowering_no_ops_rejected": 1,
            "uk_effect_payload_missing": 1,
        },
        "lowering_observation_reason_code_counts": {
            "no_lowerable_operations": 1,
            "payload_missing": 1,
        },
        "lowering_observation_owner_phase_counts": {
            "affecting_source_extraction": 1,
            "canonical_op_compilation": 1,
        },
        "rows_with_lowering_rejections": 1,
        "rows_with_blocking_lowering_rejections": 1,
        "rows_with_source_acquisition_observations": 1,
        "source_acquisition_observation_rule_counts": {
            "uk_affecting_act_xml_cached_recorded": 1,
            "uk_affecting_act_xml_missing_rejected": 1,
        },
        "source_acquisition_observation_owner_phase_counts": {
            "affecting_source_extraction": 2,
        },
        "rows_with_source_acquisition_rejections": 1,
        "source_acquisition_rejection_rule_counts": {
            "uk_affecting_act_xml_missing_rejected": 1,
        },
        "source_acquisition_rejection_owner_phase_counts": {
            "affecting_source_extraction": 1,
        },
        "lowering_rejection_rule_counts": {
            "uk_effect_lowering_no_ops_rejected": 1,
        },
        "lowering_rejection_reason_code_counts": {
            "no_lowerable_operations": 1,
        },
        "lowering_rejection_owner_phase_counts": {
            "canonical_op_compilation": 1,
        },
        "blocking_lowering_rejection_rule_counts": {
            "uk_effect_lowering_no_ops_rejected": 1,
        },
        "blocking_lowering_rejection_reason_code_counts": {
            "no_lowerable_operations": 1,
        },
        "blocking_lowering_rejection_owner_phase_counts": {
            "canonical_op_compilation": 1,
        },
    }


def test_uk_effect_row_json_exposes_manual_compile_frontier() -> None:
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )
    row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_heading_only_ref_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the title, for "old" substitute "new".',
            affecting_source_status="available",
            affecting_source_size=17,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires an explicit manual claim.",
            manual_compile_owner_phase="typed_elaboration",
            manual_compile_lowering_rule_ids=("uk_effect_heading_only_ref_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_heading_only_ref_rejected",
            ),
        ),
    )

    payload = _effect_report_row_jsonable(row, statute_id="ukpga/2000/1")

    assert payload["manual_compile_frontier"] == {
        "status": "manual_compile_candidate",
        "rule_id": "uk_manual_frontier_heading_facet_candidate",
        "reason": "Heading facet requires an explicit manual claim.",
        "owner_phase": "typed_elaboration",
        "lowering_rule_ids": ["uk_effect_heading_only_ref_rejected"],
        "blocking_lowering_rule_ids": ["uk_effect_heading_only_ref_rejected"],
    }
    assert payload["execution_authorization"]["authorization_status"] == (
        "manual_claim_required"
    )
    assert payload["execution_authorization"]["replay_authorized"] is False
    assert "mutation_boundary_proof" in payload["execution_authorization"][
        "required_proofs"
    ]
    assert payload["frontier_work_item"]["frontier_family"] == (
        "uk_manual_frontier_heading_facet_candidate"
    )
    assert payload["frontier_work_item"]["candidate_operation_family"] == (
        "facet_text_rewrite"
    )
    assert payload["frontier_work_item"]["authorization_status"] == (
        "manual_claim_required"
    )
    assert payload["frontier_work_item"]["executable"] is False
    assert payload["frontier_work_item"]["replay_authorized"] is False
    assert payload["frontier_work_item"]["source_witness"]["source_sha256"] == (
        "affecting-sha"
    )
    assert payload["frontier_work_item"]["source_witness"]["source_role"] == (
        "affecting_source"
    )
    assert payload["frontier_work_item"]["source_witness"]["digest"] == (
        "affecting-sha"
    )
    assert payload["frontier_work_item"]["detail"]["manual_compile_reason"] == (
        "Heading facet requires an explicit manual claim."
    )
    assert payload["frontier_work_item"]["detail"]["statute_id"] == "ukpga/2000/1"
    assert payload["frontier_work_item"]["detail"]["blocking_lowering_rule_ids"] == [
        "uk_effect_heading_only_ref_rejected",
    ]
    assert payload["suggested_claim_template_status"] == "available"
    assert payload["suggested_claim_template"]["action_family"] == "facet_text_rewrite"
    assert payload["source"] == {
        "extracted": True,
        "tag": "P1",
        "text_preview": 'In the title, for "old" substitute "new".',
    }


def test_heading_frontier_template_distinguishes_part_wrapper_insert() -> None:
    effect = UKEffectRecord(
        effect_id="eff-heading-wrapper",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-09-06",
        affected_uri="/id/ukpga/2020/14/schedule/15/part/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="14",
        affected_provisions="Sch. 15 Pt. 1 heading",
        affecting_uri="/id/ukpga/2024/3",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="3",
        affecting_provisions="s. 12(3)(b)",
        affecting_title="Finance Act 2024",
    )
    row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="heading_facet_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "b before paragraph 1 of Schedule 15 (and the italic heading before it) "
                "insert— Part 1 Income tax and other related relief ;"
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires an explicit manual claim.",
            manual_compile_owner_phase="typed_elaboration",
            manual_compile_lowering_rule_ids=("uk_effect_heading_only_ref_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_heading_only_ref_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2020/14",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2020/14",
        row=row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "schedule_part_wrapper_insertion"
    assert template["placement_family"] == "before_anchor_paragraph_and_carried_heading"
    assert template["schedule_label"] == "15"
    assert template["anchor_paragraph_label"] == "1"
    assert template["inserted_part_label"] == "Part 1"
    assert template["inserted_heading_text"] == "Income tax and other related relief"
    assert "partition_scope_or_non_scope_claim" in template["required_ownership"]
    assert "claim_states_whether_following_children_move_under_new_part" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_single_uk_effect_report_includes_manual_claim_template() -> None:
    effect = UKEffectRecord(
        effect_id="eff-heading-wrapper",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-09-06",
        affected_uri="/id/ukpga/2020/14/schedule/15/part/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="14",
        affected_provisions="Sch. 15 Pt. 1 heading",
        affecting_uri="/id/ukpga/2024/3",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="3",
        affecting_provisions="s. 12(3)(b)",
        affecting_title="Finance Act 2024",
    )
    extracted = ET.fromstring(
        """
        <P3 id="section-12-3-b">
          <Pnumber>b</Pnumber>
          <Text>before paragraph 1 of Schedule 15 (and the italic heading before it) insert— Part 1 Income tax and other related relief ;</Text>
        </P3>
        """
    )

    report = uk_effect_report_jsonable(
        statute_id="ukpga/2020/14",
        effect=effect,
        source_pathology="heading_facet_target_unsupported",
        extracted=extracted,
        lowering_rejections=[
            {
                "rule_id": "uk_effect_heading_only_ref_rejected",
                "blocking": True,
                "target_ref": "Sch. 15 Pt. 1 heading",
            }
        ],
        compare_shape="",
        candidate=False,
        op_rows=[],
    )

    assert report["manual_compile_frontier"]["rule_id"] == (
        "uk_manual_frontier_heading_facet_candidate"
    )
    assert report["execution_authorization"]["authorization_status"] == (
        "manual_claim_required"
    )
    assert report["execution_authorization"]["owner_phase"] == "typed_elaboration"
    assert report["execution_authorization"]["replay_authorized"] is False
    assert report["frontier_work_item"]["frontier_family"] == (
        "uk_manual_frontier_heading_facet_candidate"
    )
    assert "before paragraph 1" in report["frontier_work_item"]["source_witness"][
        "text_preview"
    ]
    assert report["suggested_claim_template_status"] == "available"
    assert report["suggested_claim_template"]["action_family"] == (
        "schedule_part_wrapper_insertion"
    )
    assert report["suggested_claim_template"]["inserted_heading_text"] == (
        "Income tax and other related relief"
    )


def test_single_uk_effect_report_includes_deterministic_frontier_claim_template() -> None:
    effect = UKEffectRecord(
        effect_id="eff-referent-qualified",
        effect_type="word substituted",
        applied=True,
        requires_applied=True,
        modified="2003-07-01",
        affected_uri="/id/ukpga/1996/61/section/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1996",
        affected_number="61",
        affected_provisions="s. 21",
        affecting_uri="/id/ukpga/2003/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2003",
        affecting_number="20",
        affecting_provisions="Sch. 2 para. 22(a)",
        affecting_title="Railways and Transport Safety Act 2003",
    )
    extracted = ET.fromstring(
        """
        <P3 id="schedule-2-paragraph-22-a">
          <Pnumber>a</Pnumber>
          <Text>for "he" and "him", where they refer to the Rail Regulator, substitute "it"</Text>
        </P3>
        """
    )

    report = uk_effect_report_jsonable(
        statute_id="ukpga/1996/61",
        effect=effect,
        source_pathology="referent_qualified_text_substitution_unsupported",
        extracted=extracted,
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "blocking": True,
            }
        ],
        compare_shape="",
        candidate=False,
        op_rows=[],
    )

    assert report["manual_compile_frontier"]["status"] == "deterministic_frontend_candidate"
    assert report["suggested_claim_template_status"] == "available"
    assert report["suggested_claim_template"]["action_family"] == (
        "referent_qualified_text_substitution"
    )
    assert report["suggested_claim_template"]["text_preimages"] == ["he", "him"]


def test_uk_manual_compile_evidence_jsonl_rows_are_source_witnessed(tmp_path) -> None:
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
            ),
            source_acquisition_rejections=(
                {
                    "rule_id": "uk_affecting_act_xml_missing_rejected",
                    "phase": "acquisition",
                    "blocking": True,
                },
                {
                    "rule_id": "uk_affecting_act_xml_cached_recorded",
                    "phase": "acquisition",
                    "blocking": False,
                    "strict_disposition": "record",
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the title, for "old" substitute "new".',
            affecting_source_status="available",
            affecting_source_size=17,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires an explicit manual claim.",
            manual_compile_owner_phase="typed_elaboration",
            manual_compile_lowering_rule_ids=("uk_effect_heading_only_ref_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_heading_only_ref_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
        archive_path="/tmp/uk.farchive",
        enacted_url="https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        oracle_url="https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        enacted_source_status="available",
        oracle_source_status="available",
        enacted_source_size=123,
        oracle_source_size=456,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
        replay_regime={
            "applicability_mode": "effective_date_only",
            "authority_mode": "effect_feed_inspection",
            "unused": None,
        },
    )
    out_path = tmp_path / "nested" / "uk-manual.jsonl"
    count = _write_manual_compile_evidence_jsonl(
        out_path,
        statute_id="ukpga/2000/1",
        rows=(report_row,),
        context=context,
        replay_regime={
            "applicability_mode": "effective_date_only",
            "authority_mode": "effect_feed_inspection",
            "unused": None,
        },
    )
    written = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]

    assert count == 1
    assert written == [payload]
    assert payload["schema"] == "lawvm.uk_manual_compile_frontier.v1"
    assert payload["rule_id"] == "uk_manual_compile_frontier_workqueue"
    assert payload["work_item_kind"] == "semantic_compile_candidate"
    assert payload["claim_kind"] == "semantic_compile"
    assert payload["claim_status"] == "unresolved_work_item"
    assert payload["validator_status"] == "not_validated"
    assert payload["work_item_id"].startswith("uk-manual-frontier-")
    assert payload["affected_uri"] == "/id/ukpga/2000/1"
    assert payload["affecting_uri"] == "/id/ukpga/2025/1"
    assert payload["manual_compile_status"] == "manual_compile_candidate"
    assert payload["manual_compile_rule_id"] == "uk_manual_frontier_heading_facet_candidate"
    assert payload["owner_phase"] == "typed_elaboration"
    assert payload["manual_compile_owner_phase"] == "typed_elaboration"
    assert payload["manual_compile_lowering_rule_ids"] == [
        "uk_effect_heading_only_ref_rejected"
    ]
    assert payload["manual_compile_blocking_lowering_rule_ids"] == [
        "uk_effect_heading_only_ref_rejected"
    ]
    assert payload["source"]["extracted"] is True
    assert payload["source"]["text_preview_sha256"] == hashlib.sha256(
        'In the title, for "old" substitute "new".'.encode("utf-8")
    ).hexdigest()
    assert payload["source_witness"]["archive_path"] == "/tmp/uk.farchive"
    assert payload["source_witness"]["enacted_source_status"] == "available"
    assert payload["source_witness"]["enacted_source_sha256"] == "enacted-sha"
    assert payload["source_witness"]["oracle_source_sha256"] == "oracle-sha"
    assert payload["affecting_source_witness"] == {
        "affecting_act_id": "ukpga/2025/1",
        "affecting_provisions": "s. 2",
        "source_status": "available",
        "source_size": 17,
        "source_sha256": "affecting-sha",
    }
    assert payload["target_context"] == {
        "surface": "effect_feed_affected_provisions",
        "affected_provisions": "s. 1",
        "resolver_eids": [],
        "compare_shape": "commensurable",
    }
    assert payload["replay_regime"] == {
        "applicability_mode": "effective_date_only",
        "authority_mode": "effect_feed_inspection",
    }
    assert payload["authorization_status"] == "manual_claim_required"
    assert payload["replay_authorized"] is False
    assert "mutation_boundary_proof" in payload["required_proofs"]
    assert payload["frontier_work_item"]["work_item_id"] == payload["work_item_id"]
    assert payload["frontier_work_item"]["jurisdiction"] == "uk"
    assert payload["frontier_work_item"]["source_unit_id"] == "eff-heading"
    assert payload["frontier_work_item"]["owner_phase"] == "typed_elaboration"
    assert payload["frontier_work_item"]["frontier_family"] == (
        "uk_manual_frontier_heading_facet_candidate"
    )
    assert payload["frontier_work_item"]["candidate_operation_family"] == (
        "facet_text_rewrite"
    )
    assert payload["frontier_work_item"]["candidate_targets"] == ["s. 1"]
    assert payload["frontier_work_item"]["executable"] is False
    assert payload["frontier_work_item"]["replay_authorized"] is False
    assert payload["frontier_work_item"]["detail"]["claim_status"] == (
        "unresolved_work_item"
    )
    assert payload["frontier_work_item"]["detail"]["validator_status"] == (
        "not_validated"
    )
    assert payload["frontier_work_item"]["detail"]["lowering_rule_ids"] == [
        "uk_effect_heading_only_ref_rejected"
    ]
    assert payload["frontier_work_item"]["detail"]["blocking_lowering_rule_ids"] == [
        "uk_effect_heading_only_ref_rejected"
    ]
    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["schema"] == "lawvm.uk_semantic_compile_claim_template.v1"
    assert template["action_family"] == "facet_text_rewrite"
    assert template["facet_family"] == "heading_or_title"
    assert template["placement_family"] == "explicit_facet_target_required"
    assert template["candidate_target_surface"] == "s. 1"
    assert template["text_match"] == "old"
    assert template["replacement"] == "new"
    assert "exact_facet_carrier" in template["required_ownership"]
    assert "host_body_text_and_children_preservation" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]
    assert "claim_identifies_exact_target_facet_not_host_body" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False
    assert payload["lowering_rejection_rule_counts"] == {
        "uk_effect_heading_only_ref_rejected": 1,
    }
    assert payload["lowering_observation_rule_counts"] == {
        "uk_effect_heading_only_ref_rejected": 1,
    }
    assert payload["lowering_observations"] == [
        {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
    ]
    assert payload["lowering_rejections"] == [
        {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
    ]
    assert payload["source_acquisition_rejections"] == [
        {
            "rule_id": "uk_affecting_act_xml_missing_rejected",
            "phase": "acquisition",
            "blocking": True,
        },
    ]
    assert payload["blocking"] is False
    assert payload["strict_disposition"] == "record"

    changed_rule_payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=_EffectReportRow(
            effect=effect,
            summary=_EffectSummary(
                source_pathology="unhandled_instruction_text",
                compare_shape="commensurable",
                n_ops=0,
                candidate=False,
                resolver_eids=(),
                lowering_rejections=(),
                replay_applicable=True,
                structural_for_replay=True,
                source_extracted_text_preview='In the title, for "old" substitute "new".',
                manual_compile_status="manual_compile_candidate",
                manual_compile_rule_id="uk_manual_frontier_crossheading_candidate",
            ),
        ),
        context=context,
    )
    assert changed_rule_payload["work_item_id"] != payload["work_item_id"]


def test_summarized_manual_compile_evidence_carries_text_patch_preimage(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="eff-text-patch",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )
    compiled = [
        LegalOperation(
            op_id="eff-text-patch-op",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old words", occurrence=2),
                replacement="new words",
            ),
        )
    ]
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={
            "ukpga/2025/1": b"<Legislation><Body>x</Body></Legislation>"
        },
    )

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: compiled,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._build_affecting_source_context",
        lambda **_kwargs: (
            type(
                "FakeSourceContext",
                (),
                {
                    "source_status": "available",
                    "source_size": 128,
                    "xml_bytes": b"<Legislation><Body>x</Body></Legislation>",
                    "root": None,
                    "authority_layer": "AFFECTING_ACT_TEXT",
                },
            )(),
            None,
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay._select_enacted_source_for_current_shell",
        lambda effect, archive, current_context, current_el, enacted_context_cache: (
            current_context,
            current_el,
            (),
        ),
    )

    summary = summarize_uk_effect(effect, archive=object(), context=context)
    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=_EffectReportRow(effect=effect, summary=summary),
        context=context,
    )

    assert payload["text_patch_evidence"] == {
        "schema": "lawvm.uk_text_patch_compile_evidence.v1",
        "sample_count": 1,
        "samples": [
            {
                "op_id": "eff-text-patch-op",
                "action": "text_replace",
                "target": "section:1",
                "patch_kind": "replace",
                "match_text": "old words",
                "replacement": "new words",
                "occurrence": 2,
                "end_occurrence": 0,
            }
        ],
    }


def test_uk_manual_compile_evidence_jsonl_marks_missing_claim_template() -> None:
    effect = UKEffectRecord(
        effect_id="eff-missing-template",
        effect_type="transfer of functions",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="as_if_application_modification_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_lowering_no_supported_action_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=False,
            structural_for_replay=False,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview="The Act applies as if modified.",
            affecting_source_status="available",
            affecting_source_size=17,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="non_textual_or_out_of_scope",
            manual_compile_rule_id=(
                "uk_manual_frontier_as_if_application_modification_out_of_scope"
            ),
            manual_compile_reason="Out of scope for direct text/tree replay.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_lowering_no_supported_action_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_lowering_no_supported_action_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "not_available"
    assert payload["suggested_claim_template"] == {}


def test_uk_manual_compile_evidence_jsonl_templates_table_entry_placement_insert() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-placement",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-04-01",
        affected_uri="/id/eur/2019/1021/annex/4/table",
        affected_class="EuropeanUnionRegulation",
        affected_year="2019",
        affected_number="1021",
        affected_provisions="Annex 4 table",
        affecting_uri="/id/uksi/2025/296",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="296",
        affecting_provisions="reg. 4(3)",
        affecting_title="Test Regulations 2025",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_table_entry_placement_insert_rejected",
                    "blocking": True,
                    "target_ref": "Annex 4 table",
                    "target": "annex:4/table",
                    "reason_code": "table_entry_insert_requires_row_or_cell_placement_model",
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P2",
            source_extracted_text_preview="At the end, insert- New table entry.",
            affecting_source_status="absent",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_entry_placement_insert",
            manual_compile_reason="Table entry insertion needs a row/cell placement claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_table_entry_placement_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_table_entry_placement_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="eur/2019/1021",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="eur/2019/1021",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert payload["suggested_claim_template_status"] == "available"
    assert template["action_family"] == "table_surface_mutation"
    assert template["placement_family"] == "table_entry_placement_requires_row_or_cell_claim"
    assert template["source_target_address"] == "annex:4/table"
    assert "claim_identifies_exact_insert_position_within_table_or_list" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_savings_qualified_omission() -> None:
    effect = UKEffectRecord(
        effect_id="eff-savings-omission",
        effect_type="words omitted",
        applied=True,
        requires_applied=True,
        modified="2005-04-01",
        affected_uri="/id/ukpga/1981/20/schedule/1/paragraph/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1981",
        affected_number="20",
        affected_provisions="Sch. 1 para. 1",
        affecting_uri="/id/ukpga/2005/9",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2005",
        affecting_number="9",
        affecting_provisions="Sch. 6 para. 27",
        affecting_title="Test Act 2005",
    )
    source_preview = (
        "omit the reference to the Magistrates' Courts Act 1980 except in the "
        "case of proceedings begun immediately before commencement"
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="savings_qualified_text_omission_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_overlap_substitution_unlowered",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=source_preview,
            affecting_source_status="available",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_savings_qualified_text_omission_candidate"
            ),
            manual_compile_reason="Savings-qualified omission needs an applicability-aware claim.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1981/20",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1981/20",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert payload["suggested_claim_template_status"] == "available"
    assert template["action_family"] == "savings_qualified_text_omission"
    assert template["placement_family"] == (
        "applicability_qualified_omission_requires_savings_claim"
    )
    assert template["omitted_reference"] == "the Magistrates' Courts Act 1980"
    assert "except in the case of proceedings" in template["savings_condition_preview"]
    assert "claim_represents_savings_condition_as_applicability_not_unconditional_deletion" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_appropriate_place_definition_entry() -> None:
    effect = UKEffectRecord(
        effect_id="eff-definition-entry",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/asp/2001/2/section/48",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 48(1)",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="sch. para. 3(6)(a)(iii)",
        affecting_title="Transport (Scotland) Act 2019",
    )
    source_preview = (
        'iii at the appropriate place insert— " operational service standard " '
        "is to be construed in accordance with section 3C(1)(b), ,"
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="appropriate_place_definition_entry_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_appropriate_place_definition_entry_insert_rejected",
                    "blocking": True,
                    "source_parent_id": "schedule-paragraph-3-6-a-iii",
                    "source_parent_context_preview": (
                        "iii at the appropriate place insert- "
                        '" operational service standard " is to be construed'
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P4",
            source_extracted_text_preview=source_preview,
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_appropriate_place_definition_entry_candidate"
            ),
            manual_compile_reason="Definition-entry placement needs a validated anchor.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_appropriate_place_definition_entry_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_appropriate_place_definition_entry_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="asp/2001/2",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="asp/2001/2",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["schema"] == "lawvm.uk_semantic_compile_claim_template.v1"
    assert template["claim_status"] == "template_only_not_validated"
    assert template["action_family"] == "definition_entry_insert"
    assert template["placement_family"] == "appropriate_place_requires_anchor_claim"
    assert template["statute_id"] == "asp/2001/2"
    assert template["inserted_definition_term"] == "operational service standard"
    assert template["inserted_definition_terms"] == ["operational service standard"]
    assert template["candidate_target_surface"] == "s. 48(1)"
    assert template["source_parent_id"] == "schedule-paragraph-3-6-a-iii"
    assert "at the appropriate place insert" in (
        template["source_parent_context_preview"]
    )
    assert template["executable"] is False
    assert "claim_supplies_exact_definition_entry_anchor_or_insertion_index" in (
        template["required_validator_checks"]
    )
    assert "inserted_definition_term_identity" in template["required_ownership"]
    assert "complete_definition_entry_payload" in template["required_ownership"]
    assert "definition_list_target_boundary" in template["required_ownership"]
    assert "insertion_position_or_list_end_boundary" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]


def test_uk_manual_compile_evidence_jsonl_templates_definition_child_and_tail_substitution() -> None:
    effect = UKEffectRecord(
        effect_id="eff-definition-child-tail",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2022-07-01",
        affected_uri="/id/ukpga/2021/17/section/15/7",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2021",
        affected_number="17",
        affected_provisions="s. 15(7)",
        affecting_uri="/id/ukpga/2022/31",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="31",
        affecting_provisions="Sch. 4 para. 239",
        affecting_title="Health and Care Act 2022",
    )
    source_preview = (
        "239 In section 15, in subsection (7), for paragraph (d) of the "
        "definition of “NHS body in England” and the “or” at the end of "
        "that paragraph substitute— an integrated care board established "
        "under section 14Z25 of that Act; ."
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="definition_child_and_tail_substitution_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=source_preview,
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_definition_child_and_tail_substitution_candidate"
            ),
            manual_compile_reason=(
                "Definition child plus tail substitution needs a validated boundary."
            ),
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2021/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2021/17",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "definition_child_and_tail_substitution"
    assert template["placement_family"] == (
        "definition_child_plus_post_child_tail_boundary_required"
    )
    assert template["definition_term"] == "NHS body in England"
    assert template["definition_child_label"] == "d"
    assert template["tail_connector"] == "or"
    assert "integrated care board" in template["replacement_preview"]
    assert "post_child_tail_connector_boundary" in template["required_ownership"]
    assert "claim_splits_or_lowers_into_bounded_child_and_tail_mutations" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_definition_child_structural_substitution() -> None:
    effect = UKEffectRecord(
        effect_id="eff-definition-child-structural",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2020-12-31",
        affected_uri="/id/ukpga/1990/42/section/177/subsection/6",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="42",
        affected_provisions="s. 177(6)",
        affecting_uri="/id/uksi/2019/224",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2019",
        affecting_number="224",
        affecting_provisions="Sch. 1 para. 1",
        affecting_title="Audiovisual Media Services Regulations 2019",
    )
    source_preview = (
        "1 In section 177 of the Broadcasting Act 1990 (orders proscribing "
        "unacceptable foreign satellite services), in subsection (6), in the "
        "definition of “foreign satellite service”, for paragraph (a) "
        "(including the “or” at the end) substitute— a a service which— i "
        "consists wholly or mainly in the transmission by satellite of "
        "television programmes."
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=source_preview,
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_definition_child_structural_substitution_candidate"
            ),
            manual_compile_reason=(
                "Definition child structural substitution needs a validated boundary."
            ),
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1990/42",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1990/42",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "definition_child_structural_substitution"
    assert template["placement_family"] == (
        "definition_child_structural_payload_boundary_required"
    )
    assert template["definition_term"] == "foreign satellite service"
    assert template["definition_child_label"] == "a"
    assert template["tail_connector"] == "or"
    assert "a service which" in template["replacement_preview"]
    assert "replacement_child_payload_shape" in template["required_ownership"]
    assert "claim_identifies_replacement_payload_child_units" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_definition_child_structural_insert() -> None:
    effect = UKEffectRecord(
        effect_id="key-c22952a1d51f5424f15e181282571ec0",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-11-11",
        affected_uri="/id/ukpga/2006/52/section/374",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="52",
        affected_provisions="s. 374",
        affecting_uri="/id/ukpga/2012/10",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="10",
        affecting_provisions="Sch. 22 para. 37",
        affecting_title="Legal Aid, Sentencing and Punishment of Offenders Act 2012",
    )
    source_preview = (
        "37 In section 374 (definitions applying for purposes of the whole Act), "
        "in the definition of “custodial sentence”, after paragraph (e) "
        "(but before the “or” at the end of that paragraph) insert— ea a "
        "sentence of detention under section 226B of that Act passed as a "
        "result of section 221A of this Act; ."
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="definition_child_structural_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=source_preview,
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_definition_child_structural_insert_candidate"
            ),
            manual_compile_reason=(
                "Definition child structural insert needs a validated connector boundary."
            ),
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2006/52",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2006/52",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "definition_child_structural_insert"
    assert template["placement_family"] == (
        "definition_child_insert_before_existing_tail_connector"
    )
    assert template["definition_term"] == "custodial sentence"
    assert template["anchor_child_label"] == "e"
    assert template["tail_connector"] == "or"
    assert "sentence of detention" in template["inserted_payload_preview"]
    assert "connector_migration_or_preservation_rule" in template["required_ownership"]
    assert "claim_identifies_existing_tail_connector_surface" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_cross_container_renumber() -> None:
    effect = UKEffectRecord(
        effect_id="eff-cross-container-renumber",
        effect_type="Sch. 22 para. 87 renumbered as Sch. 2 para. 87(1)",
        applied=True,
        requires_applied=True,
        modified="2020-12-31",
        affected_uri="/id/ukpga/2020/17/schedule/22/paragraph/87/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 22 para. 87(1)",
        affecting_uri="/id/uksi/2020/1520",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2020",
        affecting_number="1520",
        affecting_provisions="reg. 5(3)(a)",
        affecting_title="Test Regulations 2020",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_metadata_cross_container_renumber_rejected",
                    "blocking": True,
                    "source_target": "schedule:22/paragraph:87",
                    "destination": "schedule:2/paragraph:87/subparagraph:1",
                    "effect_type_normalized": (
                        "sch. 22 para. 87 renumbered as sch. 2 para. 87(1)"
                    ),
                    "reason_code": "explicit_effect_metadata_cross_container_renumber",
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview="a the existing provision becomes sub-paragraph (1);",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_cross_container_renumber_candidate",
            manual_compile_reason="Cross-container migration needs an explicit lineage claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_metadata_cross_container_renumber_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_metadata_cross_container_renumber_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2020/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2020/17",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "cross_container_renumber_migration"
    assert template["placement_family"] == (
        "explicit_effect_metadata_destination_required"
    )
    assert template["source_target_address"] == "schedule:22/paragraph:87"
    assert template["destination_address"] == "schedule:2/paragraph:87/subparagraph:1"
    assert "lineage_or_migration_events" in template["required_ownership"]
    assert "claim_emits_lineage_or_migration_events_for_moved_identity" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_definition_template_survives_unparsed_payload() -> None:
    effect = UKEffectRecord(
        effect_id="eff-definition-entry-unparsed",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/asp/2001/2/section/48",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 48(1)",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="sch. para. 3(6)(a)(iii)",
        affecting_title="Transport (Scotland) Act 2019",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="appropriate_place_definition_entry_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P4",
            source_extracted_text_preview="Definition entry payload with unusual publisher punctuation.",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_appropriate_place_definition_entry_candidate"
            ),
            manual_compile_reason="Definition-entry placement needs a validated anchor.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="asp/2001/2",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="asp/2001/2",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["schema"] == "lawvm.uk_semantic_compile_claim_template.v1"
    assert template["action_family"] == "definition_entry_insert"
    assert template["inserted_definition_term"] == ""
    assert template["inserted_definition_terms"] == []
    assert (
        template["inserted_definition_entry_preview"]
        == "Definition entry payload with unusual publisher punctuation."
    )
    assert "inserted_definition_term_identity" in template["required_ownership"]
    assert "complete_definition_entry_payload" in template["required_ownership"]
    assert "definition_list_target_boundary" in template["required_ownership"]
    assert "insertion_position_or_list_end_boundary" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_pseudo_definition_instruction_payload() -> None:
    effect = UKEffectRecord(
        effect_id="eff-pseudo-definition-instruction",
        effect_type="added",
        applied=True,
        requires_applied=True,
        modified="1997-07-01",
        affected_uri="/id/ukpga/1990/42/section/71/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="42",
        affected_provisions='s. 71(1) (defn. of "satellite television service")',
        affecting_uri="/id/uksi/1997/1682",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="1997",
        affecting_number="1682",
        affecting_provisions="reg. 2 Sch. para. 10(b)",
        affecting_title="Satellite Television Service Regulations 1997",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="nonstructural_root_gap",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_structural_pseudo_definition_target_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=False,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "b after the definition of “S4C” there is inserted— "
                "“satellite television service” has the meaning given by section 43(1); ."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
            ),
            manual_compile_reason="Pseudo definition target needs a validated anchor.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_structural_pseudo_definition_target_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_structural_pseudo_definition_target_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1990/42",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1990/42",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "definition_entry_insert"
    assert template["placement_family"] == "pseudo_definition_target_requires_anchor_claim"
    assert template["inserted_definition_term"] == "satellite television service"
    assert template["inserted_definition_terms"] == ["satellite television service"]
    assert template["inserted_definition_entry_preview"].startswith(
        "“satellite television service” has the meaning given by section 43(1)"
    )
    assert "inserted_definition_term_identity" in template["required_ownership"]
    assert "complete_definition_entry_payload" in template["required_ownership"]
    assert "definition_list_target_boundary" in template["required_ownership"]
    assert "insertion_position_or_list_end_boundary" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_crossheading_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-crossheading",
        effect_type="cross-heading substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/crossheading/public-standards",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="cross-heading before s. 10",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="crossheading_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_crossheading_replace_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                'For the cross-heading "Old public standards" substitute '
                '"New public standards".'
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_crossheading_candidate",
            manual_compile_reason="Crossheading requires a validated carrier claim.",
            manual_compile_lowering_rule_ids=("uk_effect_crossheading_replace_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_crossheading_replace_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "crossheading_text_rewrite"
    assert template["facet_family"] == "crossheading"
    assert template["placement_family"] == "explicit_crossheading_carrier_required"
    assert template["candidate_target_surface"] == "cross-heading before s. 10"
    assert template["text_match"] == "Old public standards"
    assert template["replacement"] == "New public standards"
    assert "exact_crossheading_carrier" in template["required_ownership"]
    assert "neighbouring_sections_and_body_text_preservation" in (
        template["required_ownership"]
    )
    assert "mutation_boundary" in template["required_ownership"]
    assert "claim_identifies_exact_crossheading_carrier" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_table_crossheading_becomes_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-crossheading",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2010-04-06",
        affected_uri="/id/ukpga/2000/17/schedule/6/paragraph/51/6/table/crossheading",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="17",
        affected_provisions="Sch. 6 para. 51(6) Table cross-heading",
        affecting_uri="/id/uksi/2010/675",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2010",
        affecting_number="675",
        affecting_provisions="Sch. 26 Pt. 1 para. 16",
        affecting_title="Test Regulations 2010",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="table_crossheading_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_crossheading_replace_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                "in the table (the cross-heading preceding entry 1 of which becomes "
                "“Installations regulated under the Environmental Permitting "
                "(England and Wales) Regulations 2010”)— "
                "in entry 5(1), for “the Environmental Permitting (England and Wales) "
                "Regulations 2007” substitute “the Environmental Permitting "
                "(England and Wales) Regulations 2010”"
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_crossheading_candidate",
            manual_compile_reason="Table crossheading requires a validated carrier claim.",
            manual_compile_lowering_rule_ids=("uk_effect_crossheading_replace_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_crossheading_replace_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/17",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_crossheading_text_rewrite"
    assert template["facet_family"] == "table_crossheading"
    assert template["source_formula"] == "becomes"
    assert template["table_crossheading_anchor"] == "entry 1"
    assert template["text_match"] == ""
    assert "exact_table_carrier" in template["required_ownership"]
    assert "heading_cell_or_text_prefix_boundary" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]
    assert (
        template["replacement"]
        == "Installations regulated under the Environmental Permitting "
        "(England and Wales) Regulations 2010"
    )
    assert "claim_identifies_heading_cell_or_text_prefix_boundary" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_schedule_note_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-schedule-note",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/1/note/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 1 note",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 3",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="schedule_note_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_schedule_note_target_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the note, for "old note" substitute "new note".',
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_schedule_note_candidate",
            manual_compile_reason="Schedule note requires a validated carrier claim.",
            manual_compile_lowering_rule_ids=("uk_effect_schedule_note_target_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_schedule_note_target_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "schedule_note_text_rewrite"
    assert template["facet_family"] == "schedule_note"
    assert template["placement_family"] == "explicit_schedule_note_carrier_required"
    assert template["candidate_target_surface"] == "Sch. 1 note"
    assert template["text_match"] == "old note"
    assert template["replacement"] == "new note"
    assert "exact_schedule_note_carrier" in template["required_ownership"]
    assert "schedule_paragraph_body_structure_preservation" in (
        template["required_ownership"]
    )
    assert "mutation_boundary" in template["required_ownership"]
    assert "claim_preserves_schedule_paragraph_body_structure" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_schedule_list_entry_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-schedule-entry",
        effect_type="entry inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/2/table",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions='Sch. 2 entry relating to "old entry"',
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 4",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="schedule_list_entry_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_schedule_list_entry_target_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='After the entry relating to "old entry" insert "new entry".',
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_schedule_list_entry_candidate",
            manual_compile_reason="Schedule entry placement requires a validated carrier claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_schedule_list_entry_target_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_schedule_list_entry_target_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "schedule_list_entry_mutation"
    assert template["placement_family"] == "entry_anchor_requires_carrier_claim"
    assert template["candidate_target_surface"] == 'Sch. 2 entry relating to "old entry"'
    assert "entry_carrier" in template["required_ownership"]
    assert "claim_identifies_predecessor_or_replaced_entry" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_table_entry_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-entry",
        effect_type="entry inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/3/table",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 3 table entry",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 5",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="table_entry_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_table_entry_target_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="Table",
            source_extracted_text_preview="After that entry insert the following entry.",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_entry_deictic_candidate",
            manual_compile_reason="Table entry placement requires a validated row claim.",
            manual_compile_lowering_rule_ids=("uk_effect_table_entry_target_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_table_entry_target_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_surface_mutation"
    assert template["placement_family"] == "deictic_table_entry_anchor_required"
    assert template["candidate_target_surface"] == "Sch. 3 table entry"
    assert "row_or_column_carrier" in template["required_ownership"]
    assert "claim_preserves_unclaimed_rows_columns_and_cells" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_table_appropriate_place_template_carries_entry_shape() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-appropriate-place",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-05-01",
        affected_uri="/id/ukpga/2020/17/section/379/1/table",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="s. 379(1) table",
        affecting_uri="/id/ukpga/2023/41",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2023",
        affecting_number="41",
        affecting_provisions="Sch. 13 para. 11",
        affecting_title="Test Act 2023",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="table_entry_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_table_entry_instruction_rejected",
                    "blocking": True,
                    "target_ref": "s. 379(1) table",
                    "target": "section:379/subsection:1/paragraph:table",
                    "entry_shape": "appropriate_place_table_entry",
                    "inserted_table_rows": (
                        (
                            "Northern Ireland Troubles (Legacy and Reconciliation) Act 2023",
                        ),
                        (
                            "section 26",
                            "revocation of immunity under that Act",
                            "making of false statements",
                        ),
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                "In section 379, in the table in subsection (1), at the "
                "appropriate place insert- Northern Ireland Troubles "
                "(Legacy and Reconciliation) Act 2023."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_appropriate_place_candidate",
            manual_compile_reason="Table appropriate-place insertion requires ordering claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_table_entry_instruction_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_table_entry_instruction_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2020/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2020/17",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_surface_mutation"
    assert template["placement_family"] == (
        "appropriate_place_table_entry_requires_ordering_claim"
    )
    assert template["source_target_surface"] == "s. 379(1) table"
    assert template["source_target_address"] == "section:379/subsection:1/paragraph:table"
    assert template["table_entry_shape"] == "appropriate_place_table_entry"
    assert template["inserted_table_rows"] == [
        ["Northern Ireland Troubles (Legacy and Reconciliation) Act 2023"],
        [
            "section 26",
            "revocation of immunity under that Act",
            "making of false statements",
        ],
    ]
    assert "table_ordering_rule_or_anchor_claim" in template["required_ownership"]
    assert "claim_identifies_table_ordering_rule_or_anchor" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_table_row_insert_template_carries_selector_evidence() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-row-insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2012-04-02",
        affected_uri="/id/ukpga/2006/52/section/164/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="52",
        affected_provisions="s. 164(3)",
        affecting_uri="/id/ukpga/2011/18",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="18",
        affecting_provisions="Sch. 4 para. 9(c)",
        affecting_title="Test Act 2011",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="table_entry_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_table_entry_row_insert",
                    "blocking": True,
                    "target_ref": "s. 164(3)",
                    "target": "section:164/subsection:3",
                    "selector_mode": "relating_entry",
                    "direction": "after",
                    "relating_text": "Part 9",
                    "inserted_text": (
                        "and Schedule 3A (offender elected Court Martial trial)"
                    ),
                    "table_label": "",
                    "column_index": 1,
                    "entry_index": 1,
                    "source_names_table": False,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "after the entry relating to Part 9 insert; and Schedule 3A."
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_entry_candidate",
            manual_compile_reason="Table/list entry placement requires a validated claim.",
            manual_compile_lowering_rule_ids=("uk_effect_table_entry_row_insert",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_table_entry_row_insert",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2006/52",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2006/52",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_surface_mutation"
    assert template["table_selector_mode"] == "relating_entry"
    assert template["table_insert_direction"] == "after"
    assert template["table_anchor_relating_text"] == "Part 9"
    assert (
        template["table_inserted_text"]
        == "and Schedule 3A (offender elected Court Martial trial)"
    )
    assert template["table_column_index"] == 1
    assert template["table_entry_index"] == 1
    assert template["source_names_table"] is False
    assert template["executable"] is False


def test_uk_manual_compile_table_child_template_carries_cell_child_evidence() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-child-insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2022-05-01",
        affected_uri="/id/ukpga/2006/52/section/132",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="52",
        affected_provisions="s. 132(1) Table",
        affecting_uri="/id/ukpga/2021/35",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2021",
        affecting_number="35",
        affecting_provisions="s. 13(2)(a)",
        affecting_title="Test Act 2021",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="table_entry_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_table_entry_instruction_rejected",
                    "blocking": True,
                    "target_ref": "s. 132(1) Table",
                    "target": "section:132/subsection:1/paragraph:table",
                    "entry_shape": "table_child_structural_insert",
                    "source_parent_instruction": (
                        "In subsection (1), in row 1 of the table, in the third column—"
                    ),
                    "source_parent_id": "section-13-2",
                    "source_table_row_number": 1,
                    "source_table_column_text": "third",
                    "source_table_column_index": 3,
                    "table_child_insert_direction": "after",
                    "table_child_anchor_kind": "paragraph",
                    "table_child_anchor_label": "a",
                    "inserted_ordered_list_units": (
                        {
                            "source_list_type": "alpha",
                            "source_list_decoration": "parens",
                            "label": "aa",
                            "text": "corporal in the Royal Marines;",
                        },
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "after paragraph (a) insert— corporal in the Royal Marines;"
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_table_entry_candidate",
            manual_compile_reason="Table child insertion requires a cell child claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_table_entry_instruction_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_table_entry_instruction_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2006/52",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2006/52",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_surface_mutation"
    assert (
        template["placement_family"]
        == "table_cell_child_anchor_requires_row_column_claim"
    )
    assert template["source_table_row_number"] == 1
    assert template["source_table_column_index"] == 3
    assert template["table_child_anchor_label"] == "a"
    assert template["inserted_ordered_list_units"] == [
        {
            "source_list_type": "alpha",
            "source_list_decoration": "parens",
            "label": "aa",
            "text": "corporal in the Royal Marines;",
        }
    ]
    assert "table_cell_child_list_carrier" in template["required_ownership"]
    assert "claim_identifies_ordered_list_inside_cell" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_appropriate_place_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-appropriate-place",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/8",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 8",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 6",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="appropriate_place_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_appropriate_place_insert_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview="At the appropriate place insert the following subsection.",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_appropriate_place_candidate",
            manual_compile_reason="Appropriate-place placement requires a validated anchor.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_appropriate_place_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_appropriate_place_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "appropriate_place_mutation"
    assert template["placement_family"] == "appropriate_place_requires_anchor_claim"
    assert "validated_predecessor_or_successor_anchor" in template["required_ownership"]
    assert "target_container_boundary" in template["required_ownership"]
    assert "claim_supplies_exact_anchor_or_ordering_rule" in (
        template["required_validator_checks"]
    )
    assert "claim_identifies_target_container_surface" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_appropriate_place_index_entry_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-appropriate-place-index",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/22/paragraph/147",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 22 para. 147",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 6",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="appropriate_place_index_entry_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_appropriate_place_insert_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                'At the appropriate place insert "relevant register" paragraph 22B(6A).'
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_appropriate_place_index_entry_candidate"
            ),
            manual_compile_reason="Appropriate-place index entry requires a validated anchor.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_appropriate_place_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_appropriate_place_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "index_entry_insert"
    assert template["placement_family"] == "appropriate_place_requires_anchor_claim"
    assert "source_named_index_entry_payload" in template["required_ownership"]
    assert "target_index_or_list_container_boundary" in template["required_ownership"]
    assert "payload_is_complete_index_entry" in template["required_validator_checks"]
    assert (
        "claim_supplies_exact_index_entry_anchor_or_ordering_rule"
        in template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_structural_sibling_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-sibling-insert",
        effect_type="paragraph inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/8/subsection/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 8(1)(a)",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 7",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="structural_sibling_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_structural_sibling_insert_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P2",
            source_extracted_text_preview="After paragraph (a) insert the following paragraph.",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id="uk_manual_frontier_structural_sibling_insert_candidate",
            manual_compile_reason="Sibling insertion requires an explicit compiler or claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_structural_sibling_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_structural_sibling_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "structural_sibling_insert"
    assert template["placement_family"] == "source_named_sibling_anchor_required"
    assert "source_named_sibling_anchor" in template["required_ownership"]
    assert "sibling_order_boundary" in template["required_ownership"]
    assert "claim_identifies_exact_parent_and_anchor_sibling" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_deictic_structural_sibling_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-deictic-sibling-insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/2/paragraph/ground/subparagraph/4",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 2 Ground 4",
        affecting_uri="/id/ukpga/2025/26",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="26",
        affecting_provisions="Sch. 1 para. 9(d)",
        affecting_title="Test Act 2025",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="structural_sibling_insert_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_overlap_substitution_unlowered",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "after that unnumbered paragraph insert and- if the tenancy arose "
                "by succession"
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_deictic_structural_sibling_insert_candidate"
            ),
            manual_compile_reason="Deictic sibling insertion requires an anchor claim.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert payload["suggested_claim_template_status"] == "available"
    assert template["action_family"] == "structural_sibling_insert"
    assert template["placement_family"] == "deictic_sibling_anchor_claim_required"
    assert "source_deictic_anchor_phrase" in template["required_ownership"]
    assert "claimed_anchor_resolution" in template["required_ownership"]
    assert "claim_proves_deictic_anchor_from_source_context" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_structural_child_range_substitution() -> None:
    effect = UKEffectRecord(
        effect_id="eff-child-range",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/48/subsection/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 48(1)",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="sch. 15 para. 20(2)(b)",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_overlap_substitution_unlowered",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "b for paragraphs (a) and (b) there shall be substituted "
                "“ on a relevant frequency ” ."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_structural_child_range_substitution_candidate"
            ),
            manual_compile_reason="Child range substitution requires explicit ownership.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "structural_child_range_substitution"
    assert template["placement_family"] == "source_named_child_range_required"
    assert "removed_child_identities" in template["required_ownership"]
    assert "claim_identifies_each_removed_child_unit" in template["required_validator_checks"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_mixed_body_heading_substitution() -> None:
    effect = UKEffectRecord(
        effect_id="eff-mixed-body-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/82",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 82",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="Sch. 13 para. 34",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_mixed_body_heading_text_substitution_rejected",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                "34 In section 82 for “Commissioner” (in each place, including "
                "in the heading) substitute “ appointed person ” ."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_mixed_body_heading_text_substitution_split"
            ),
            manual_compile_reason="Body and heading surfaces require a split claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_mixed_body_heading_text_substitution_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_mixed_body_heading_text_substitution_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == "mixed_body_heading_text_substitution_split"
    assert template["placement_family"] == "split_body_and_heading_facet_required"
    assert template["text_match"] == "Commissioner"
    assert template["replacement"] == "appointed person"
    assert "body_text_boundary" in template["required_ownership"]
    assert "heading_facet_boundary" in template["required_ownership"]
    assert "split_surface_mutation_boundary" in template["required_ownership"]
    assert "mutation_boundary" in template["required_ownership"]
    assert "claim_splits_body_text_operation_from_heading_facet_operation" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_repeal_table_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-repeal",
        effect_type="table repealed",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/4/table",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 4 table",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 8",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="repeal_schedule_table_source_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="Table",
            source_extracted_text_preview="In the table, omit the entry for old licence.",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_repeal_table_candidate",
            manual_compile_reason="Table repeal requires row/cell boundary ownership.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_repeal_or_omission"
    assert template["placement_family"] == "source_named_table_or_row_boundary_required"
    assert "repealed_row_column_or_cell_boundary" in template["required_ownership"]
    assert "claim_preserves_unclaimed_table_rows_columns_and_cells" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_repeal_table_mixed_split_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-repeal-mixed",
        effect_type="repealed",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/4/paragraph/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 4 para. 3",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="Sch. 2",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="repeal_schedule_table_source_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_repeal_table_structural_repeal_unresolved",
                    "reason_code": "mixed_structural_and_word_repeal_requires_split",
                    "target_ref": "Sch. 4 para. 3",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="Schedule",
            source_extracted_text_preview=(
                "Schedule 2 Repeals Title Extent of repeal Test Act 2000 "
                "Paragraph 3. In paragraph 4, the words “or paragraph 3”."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_repeal_table_candidate",
            manual_compile_reason="Mixed table repeal requires split ownership.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_repeal_table_structural_repeal_unresolved",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_repeal_table_structural_repeal_unresolved",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["lowering_rule_id"] == "uk_effect_repeal_table_structural_repeal_unresolved"
    assert template["lowering_reason_code"] == "mixed_structural_and_word_repeal_requires_split"
    assert "structural_and_text_repeal_split_boundary" in template["required_ownership"]
    assert (
        "claim_splits_structural_repeal_from_word_omission_clauses"
        in template["required_validator_checks"]
    )
    assert template["source_target_surface"] == "Sch. 4 para. 3"


def test_uk_manual_compile_evidence_jsonl_templates_repeal_table_preserves_fallback_blocker() -> None:
    effect = UKEffectRecord(
        effect_id="eff-table-repeal-fallback-blocker",
        effect_type="words repealed",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/12/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 12(5)",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="Sch. 40 Pt. 3",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="repeal_schedule_table_source_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_crossheading_source_target_mismatch_rejected",
                    "reason_code": "crossheading_source_requires_crossheading_target",
                    "target_ref": "s. 12(5)",
                    "blocking": True,
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="Schedule",
            source_extracted_text_preview=(
                "Schedule 40 Repeals Short title and chapter Extent of repeal "
                "Test Act 2000 In section 12(5), the words from \"old\" to the end."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_repeal_table_candidate",
            manual_compile_reason="Table repeal requires row/cell boundary ownership.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_crossheading_source_target_mismatch_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_crossheading_source_target_mismatch_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "table_repeal_or_omission"
    assert (
        template["lowering_rule_id"]
        == "uk_effect_crossheading_source_target_mismatch_rejected"
    )
    assert (
        template["lowering_reason_code"]
        == "crossheading_source_requires_crossheading_target"
    )
    assert template["source_target_surface"] == "s. 12(5)"
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_source_carried_structured_patch_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-source-carried-structured",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 9",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 9",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="fragment_context_missing",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="BlockAmendment",
            source_extracted_text_preview=(
                'for the words "old" substitute the words "new" in paragraph (a)'
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_source_carried_structured_text_patch_candidate"
            ),
            manual_compile_reason="Structured carried payload requires child-bound claims.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "source_carried_structured_text_patch"
    assert (
        template["placement_family"]
        == "parent_formula_anchor_with_structured_payload_required"
    )
    assert "source_carried_payload_units" in template["required_ownership"]
    assert "claim_rejects_flattening_structured_payload_into_host_text" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


@pytest.mark.parametrize(
    (
        "rule_id",
        "expected_action_family",
        "expected_placement_family",
        "expected_ownership",
        "expected_validator_check",
    ),
    [
        (
            "uk_manual_frontier_amendment_program_target_candidate",
            "amendment_program_target_mutation",
            "inserted_parent_instruction_context_required",
            "source_amendment_program_context",
            "claim_identifies_the_parent_instruction_that_created_the_target",
        ),
        (
            "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate",
            "source_carried_multi_subunit_text_rewrite",
            "source_named_child_units_required",
            "source_named_child_unit_set",
            "claim_splits_the_parent_formula_into_bounded_child_operations",
        ),
        (
            "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate",
            "source_carried_child_tail_text_rewrite",
            "source_named_child_tail_required",
            "source_named_child_anchor",
            "claim_targets_only_the_tail_text_following_that_child",
        ),
        (
            "uk_manual_frontier_source_carried_structured_tail_substitution_candidate",
            "source_carried_structured_tail_substitution",
            "tail_range_with_structured_payload_required",
            "source_carried_structured_payload_units",
            "claim_materializes_replacement_payload_as_child_units_not_flat_text",
        ),
    ],
)
def test_uk_manual_compile_evidence_jsonl_templates_source_carried_frontier_claims(
    rule_id: str,
    expected_action_family: str,
    expected_placement_family: str,
    expected_ownership: str,
    expected_validator_check: str,
) -> None:
    effect = UKEffectRecord(
        effect_id=f"eff-{rule_id}",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 9",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 9",
        affecting_title="Test Act 2024",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="BlockAmendment",
            source_extracted_text_preview='In paragraphs (a) and (b), for "old" substitute "new".',
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id=rule_id,
            manual_compile_reason="Source-carried target requires bounded claim.",
            manual_compile_lowering_rule_ids=("uk_effect_overlap_substitution_unlowered",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_overlap_substitution_unlowered",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2000/1",
        row=report_row,
        context=context,
    )

    assert payload["suggested_claim_template_status"] == "available"
    template = payload["suggested_claim_template"]
    assert template["action_family"] == expected_action_family
    assert template["placement_family"] == expected_placement_family
    assert expected_ownership in template["required_ownership"]
    assert expected_validator_check in template["required_validator_checks"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_amendment_program_template_carries_inserted_parent_detail() -> None:
    effect = UKEffectRecord(
        effect_id="eff-amendment-program-insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2022-06-28",
        affected_uri="/id/ukpga/2020/17/schedule/22/paragraph/21/3/a",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 22 para. 21(3)(a)",
        affecting_uri="/id/ukpga/2022/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="32",
        affecting_provisions="Sch. 14 para. 14(2)(b)",
        affecting_title="Test Act 2022",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="amendment_text_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
                    "blocking": True,
                    "target_ref": "Sch. 22 para. 21(3)(a)",
                    "target": "schedule:22/paragraph:21/subparagraph:3/item:a",
                    "source_subparagraph_label": "3",
                    "source_item_label": "a",
                    "inserted_parent_label": "d",
                    "direction": "before",
                    "anchor_label": "i",
                    "inserted_label": "ai",
                    "inserted_text_preview": (
                        "the community order does not qualify for special procedures"
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "b in sub-paragraph (3)(a), in the inserted paragraph (d), "
                "before sub-paragraph (i) insert- ai the community order does "
                "not qualify for special procedures."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id="uk_manual_frontier_amendment_program_target_candidate",
            manual_compile_reason="Inserted parent needs explicit amendment-program context.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2020/17",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2020/17",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "amendment_program_target_mutation"
    assert template["source_target_address"] == (
        "schedule:22/paragraph:21/subparagraph:3/item:a"
    )
    assert template["source_subparagraph_label"] == "3"
    assert template["source_item_label"] == "a"
    assert template["inserted_parent_label"] == "d"
    assert template["insert_direction"] == "before"
    assert template["anchor_label"] == "i"
    assert template["inserted_label"] == "ai"
    assert "community order" in template["inserted_text_preview"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_amendment_program_template_carries_inserted_anchor_detail() -> None:
    effect = UKEffectRecord(
        effect_id="eff-amendment-program-inserted-anchor",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2026-04-24",
        affected_uri="/id/ukpga/1988/50/schedule/2",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1988",
        affected_number="50",
        affected_provisions="Sch. 2 Ground 2ZB-2ZD",
        affecting_uri="/id/ukpga/2025/26",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="26",
        affecting_provisions="Sch. 1 para. 7",
        affecting_title="Test Act 2025",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="amendment_text_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
                    "blocking": True,
                    "target_ref": "Sch. 2 Ground 2ZB",
                    "target": "schedule:2/paragraph:ground/subparagraph:2zb",
                    "inserted_anchor_kind": "ground",
                    "inserted_anchor_label": "2za",
                    "source_inserted_by": "paragraph 6 of this Schedule",
                    "direction": "after",
                    "anchor_label": "2za",
                    "inserted_label": "2zb",
                    "inserted_text_preview": "The landlord holds a superior tenancy.",
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview=(
                "After Ground 2ZA (inserted by paragraph 6 of this Schedule) "
                "insert- Ground 2ZB The landlord holds a superior tenancy."
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="deterministic_frontend_candidate",
            manual_compile_rule_id="uk_manual_frontier_amendment_program_target_candidate",
            manual_compile_reason="Inserted anchor needs explicit amendment-program context.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/1988/50",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/1988/50",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "amendment_program_target_mutation"
    assert template["source_target_surface"] == "Sch. 2 Ground 2ZB"
    assert template["source_target_address"] == "schedule:2/paragraph:ground/subparagraph:2zb"
    assert template["insert_direction"] == "after"
    assert template["anchor_label"] == "2za"
    assert template["inserted_label"] == "2zb"
    assert template["inserted_anchor_kind"] == "ground"
    assert template["inserted_anchor_label"] == "2za"
    assert template["source_inserted_by"] == "paragraph 6 of this Schedule"
    assert "superior tenancy" in template["inserted_text_preview"]
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_amendment_program_template_marks_deictic_anchor() -> None:
    effect = UKEffectRecord(
        effect_id="eff-amendment-program-deictic-anchor",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2006-06-01",
        affected_uri="/id/ukpga/2006/28/section/41/subsection/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="28",
        affected_provisions="s. 41(3)",
        affecting_uri="/id/uksi/2006/1407",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2006",
        affecting_number="1407",
        affecting_provisions="Sch. 1 Pt. 2 para. 15(c)",
        affecting_title="Test Order 2006",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="amendment_text_target_unsupported",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
                    "blocking": True,
                    "target_ref": "s. 41(3)",
                    "target": "section:41/subsection:3",
                    "inserted_anchor_kind": "paragraph",
                    "inserted_anchor_label": "2b(8)",
                    "source_inserted_by": "as inserted",
                    "direction": "after",
                    "anchor_label": "2b(8)",
                    "inserted_label": "9",
                    "inserted_text_preview": '"Relevant body" means a Strategic Health Authority.',
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P3",
            source_extracted_text_preview=(
                "after paragraph 2B(8) as inserted, insert- 9 "
                '"Relevant body" means a Strategic Health Authority.'
            ),
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id=(
                "uk_manual_frontier_deictic_amendment_program_target_candidate"
            ),
            manual_compile_reason="Deictic amendment-program anchor requires a claim.",
            manual_compile_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
            ),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="ukpga/2006/28",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="ukpga/2006/28",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert payload["suggested_claim_template_status"] == "available"
    assert template["action_family"] == "amendment_program_target_mutation"
    assert (
        template["placement_family"]
        == "deictic_inserted_anchor_instruction_context_required"
    )
    assert "claimed_inserted_anchor_source_instruction" in template["required_ownership"]
    assert "claim_proves_as_inserted_anchor_from_source_context" in (
        template["required_validator_checks"]
    )
    assert template["source_inserted_by"] == "as inserted"
    assert template["executable"] is False


def test_uk_manual_compile_evidence_jsonl_templates_range_to_container_claim() -> None:
    effect = UKEffectRecord(
        effect_id="eff-range-container",
        effect_type="Pt. 2 Ch. 1 s. 35(2) substituted for ss. 3-12 and cross-heading",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/asp/2001/2/part/2/chapter/1",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="Pt. 2 Ch. 1",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="s. 35(2)",
        affecting_title="Transport (Scotland) Act 2019",
    )
    report_row = _EffectReportRow(
        effect=effect,
        summary=_EffectSummary(
            source_pathology="range_to_container_target_unsupported",
            compare_shape="range_to_container_target_absent",
            n_ops=1,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_range_to_container_substitution_rejected",
                    "blocking": True,
                    "source_range_kind": "section",
                    "source_range_start": "3",
                    "source_range_end": "12",
                    "source_range_section_count": 10,
                    "source_range_sections": tuple(
                        {"label": str(label), "eid": ""} for label in range(3, 13)
                    ),
                    "truncated_source_range_sections": False,
                    "target_container_ref": "Pt. 2 Ch. 1",
                    "compiled_targets": ("part:2/chapter:1",),
                    "payload_kinds": ("chapter",),
                    "payload_roots": (
                        {
                            "kind": "chapter",
                            "label": "1",
                            "eid": "part-2-chapter-1",
                            "direct_child_count": 1,
                            "direct_children": (
                                {"kind": "crossheading", "label": "", "eid": ""},
                            ),
                            "truncated_direct_children": False,
                            "descendant_section_count": 2,
                            "descendant_sections": (
                                {"label": "3A", "eid": "section-3A"},
                                {"label": "3B", "eid": "section-3B"},
                            ),
                            "truncated_descendant_sections": False,
                        },
                    ),
                    "required_ownership": (
                        "source_range",
                        "container_payload",
                        "lineage_or_migration_events",
                        "mutation_boundary",
                    ),
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="Chapter",
            source_extracted_text_preview="CHAPTER 1 Bus services improvement partnerships",
            affecting_source_status="available",
            affecting_source_size=123,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_range_to_container_candidate",
            manual_compile_reason="Range-to-container substitution needs lineage.",
            manual_compile_lowering_rule_ids=("uk_effect_range_to_container_substitution_rejected",),
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_range_to_container_substitution_rejected",
            ),
        ),
    )
    context = _EffectSummaryContext(
        statute_id="asp/2001/2",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
    )

    payload = _manual_compile_evidence_row_jsonable(
        statute_id="asp/2001/2",
        row=report_row,
        context=context,
    )

    template = payload["suggested_claim_template"]
    assert template["action_family"] == "range_to_container_substitution"
    assert template["placement_family"] == "requires_lineage_or_migration_claim"
    assert template["source_range_kind"] == "section"
    assert template["source_range_start"] == "3"
    assert template["source_range_end"] == "12"
    assert template["source_range_section_count"] == 10
    assert template["source_range_sections"] == [
        {"label": str(label), "eid": ""} for label in range(3, 13)
    ]
    assert template["truncated_source_range_sections"] is False
    assert template["target_container_surface"] == "Pt. 2 Ch. 1"
    assert template["compiled_targets"] == ["part:2/chapter:1"]
    assert template["payload_kinds"] == ["chapter"]
    assert template["replacement_section_count"] == 2
    assert template["replacement_sections"] == [
        {"label": "3A", "eid": "section-3A"},
        {"label": "3B", "eid": "section-3B"},
    ]
    assert template["payload_roots"][0]["descendant_section_count"] == 2
    assert template["required_ownership"] == [
        "source_range",
        "container_payload",
        "lineage_or_migration_events",
        "mutation_boundary",
    ]
    assert "claim_emits_lineage_or_migration_events_for_displaced_units" in (
        template["required_validator_checks"]
    )
    assert template["executable"] is False


def test_print_uk_effects_summary_splits_blocking_lowering_rules(capsys) -> None:
    _print_uk_effects_summary(
        {
            "matched_effects": 3,
            "emitted_effect_count": 1,
            "diagnostic_count_scope": "emitted_rows",
            "candidate_counts": {"candidate": 0, "not_candidate": 1},
            "replay_applicability_counts": {
                "replay_applicable": 1,
                "not_replay_applicable": 0,
            },
            "structural_for_replay_counts": {
                "structural_for_replay": 1,
                "not_structural_for_replay": 0,
            },
            "metadata_only_count": 0,
            "applied_count": 1,
            "requires_applied_count": 1,
            "source_pathology_counts": {"missing_extracted_source": 1},
            "compare_shape_counts": {"oracle_missing_live_branch": 1},
            "total_compiled_ops": 0,
            "rows_with_resolver_eids": 1,
            "rows_with_lowering_observations": 1,
            "rows_with_lowering_rejections": 1,
            "rows_with_blocking_lowering_rejections": 1,
            "lowering_observation_rule_counts": {"rule-a": 2, "rule-b": 1},
            "lowering_rejection_rule_counts": {"rule-a": 2, "rule-b": 1},
            "blocking_lowering_rejection_rule_counts": {"rule-a": 2},
        }
    )

    out = capsys.readouterr().out
    assert "Matched effects: 3" in out
    assert "Emitted effects: 1" in out
    assert "Truncated: true" in out
    assert "Diagnostic counts scope: emitted_rows" in out
    assert "Rows with resolver EIDs: 1" in out
    assert "Rows with lowering observations: 1" in out
    assert "Lowering observation rules:" in out
    assert "Rows with lowering rejections: 1" in out
    assert "Source pathology counts: missing_extracted_source=1" in out
    assert "Compare shape counts: oracle_missing_live_branch=1" in out
    assert "Lowering rejection rules:" in out
    assert "  rule-b: 1" in out
    assert "Rows with blocking lowering rejections: 1" in out
    assert "Blocking lowering rejection rules:" in out
    assert "  rule-a: 2" in out


def test_print_uk_effects_summary_prints_source_acquisition_rules(capsys) -> None:
    _print_uk_effects_summary(
        {
            "matched_effects": 1,
            "emitted_effect_count": 1,
            "candidate_counts": {"candidate": 0, "not_candidate": 1},
            "replay_applicability_counts": {
                "replay_applicable": 1,
                "not_replay_applicable": 0,
            },
            "structural_for_replay_counts": {
                "structural_for_replay": 1,
                "not_structural_for_replay": 0,
            },
            "metadata_only_count": 0,
            "applied_count": 1,
            "requires_applied_count": 0,
            "source_pathology_counts": {},
            "compare_shape_counts": {},
            "total_compiled_ops": 0,
            "rows_with_resolver_eids": 0,
            "rows_with_lowering_rejections": 0,
            "rows_with_source_acquisition_observations": 1,
            "source_acquisition_observation_rule_counts": {
                "uk_affecting_act_xml_cached_recorded": 1,
                "uk_affecting_act_xml_missing_rejected": 1,
            },
            "rows_with_source_acquisition_rejections": 1,
            "source_acquisition_rejection_rule_counts": {
                "uk_affecting_act_xml_missing_rejected": 1,
            },
            "lowering_rejection_rule_counts": {},
            "blocking_lowering_rejection_rule_counts": {},
        }
    )

    out = capsys.readouterr().out
    assert "Rows with source acquisition observations: 1" in out
    assert "Source acquisition observation rules:" in out
    assert "  uk_affecting_act_xml_cached_recorded: 1" in out
    assert "Rows with source acquisition rejections: 1" in out
    assert "Source acquisition rejection rules:" in out
    assert "  uk_affecting_act_xml_missing_rejected: 1" in out


def test_print_uk_effects_summary_prints_manual_compile_frontier(capsys) -> None:
    _print_uk_effects_summary(
        {
            "matched_effects": 1,
            "emitted_effect_count": 1,
            "candidate_counts": {"candidate": 0, "not_candidate": 1},
            "replay_applicability_counts": {
                "replay_applicable": 1,
                "not_replay_applicable": 0,
            },
            "structural_for_replay_counts": {
                "structural_for_replay": 1,
                "not_structural_for_replay": 0,
            },
            "metadata_only_count": 0,
            "applied_count": 1,
            "requires_applied_count": 0,
            "source_pathology_counts": {},
            "compare_shape_counts": {},
            "manual_compile_status_counts": {"manual_compile_candidate": 1},
            "manual_compile_rule_counts": {
                "uk_manual_frontier_heading_facet_candidate": 1,
            },
            "manual_compile_candidate_rule_counts": {
                "uk_manual_frontier_heading_facet_candidate": 1,
            },
            "suggested_claim_template_status_counts": {"available": 1},
            "total_compiled_ops": 0,
            "rows_with_resolver_eids": 0,
            "rows_with_lowering_rejections": 0,
            "source_acquisition_rejection_rule_counts": {},
            "lowering_rejection_rule_counts": {},
            "blocking_lowering_rejection_rule_counts": {},
        }
    )

    out = capsys.readouterr().out
    assert "Manual compile frontier statuses: manual_compile_candidate=1" in out
    assert "Suggested claim templates: available=1" in out
    assert "Manual compile frontier rules:" in out
    assert "  uk_manual_frontier_heading_facet_candidate: 1" in out
    assert "Manual compile candidate rules:" in out
    assert "  uk_manual_frontier_heading_facet_candidate: 1" in out


def test_uk_effects_summary_counts_templates_for_actionable_frontier_only() -> None:
    def _row(
        *,
        effect_id: str,
        manual_compile_status: str,
        manual_compile_rule_id: str,
        source_text: str,
    ) -> _EffectReportRow:
        return _EffectReportRow(
            effect=UKEffectRecord(
                effect_id=effect_id,
                effect_type="words substituted",
                applied=True,
                requires_applied=True,
                modified="2024-01-01",
                affected_uri="/id/ukpga/2000/1/section/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 1",
                affecting_uri="/id/ukpga/2024/1",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2024",
                affecting_number="1",
                affecting_provisions="s. 2",
                affecting_title="Test Act 2024",
            ),
            summary=_EffectSummary(
                source_pathology="unhandled_instruction_text",
                compare_shape="",
                n_ops=0,
                candidate=False,
                resolver_eids=(),
                lowering_rejections=(
                    {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
                ),
                replay_applicable=True,
                structural_for_replay=True,
                source_extracted=True,
                source_extracted_tag="P1",
                source_extracted_text_preview=source_text,
                manual_compile_status=manual_compile_status,
                manual_compile_rule_id=manual_compile_rule_id,
                manual_compile_reason="test",
            ),
        )

    rows = (
        _row(
            effect_id="available-template",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            source_text='In the title, for "old" substitute "new".',
        ),
        _row(
            effect_id="missing-template",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_unclassified",
            source_text="Do something currently unclassified.",
        ),
        _row(
            effect_id="out-of-scope",
            manual_compile_status="non_textual_or_out_of_scope",
            manual_compile_rule_id=(
                "uk_manual_frontier_as_if_application_modification_out_of_scope"
            ),
            source_text="The Act applies as if modified.",
        ),
    )

    summary = uk_effects_summary_counts(rows, statute_id="ukpga/2000/1")

    assert summary["suggested_claim_template_status_counts"] == {
        "available": 1,
        "not_available": 1,
    }
    assert summary["manual_compile_candidate_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 1,
        "uk_manual_frontier_unclassified": 1,
    }
    assert summary["manual_frontier_work_item_authorization_status_counts"] == {
        "manual_claim_required": 2,
        "out_of_scope": 1,
    }


def test_uk_effects_summary_counts_preserve_pre_limit_match_count() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    rows = (
        _EffectReportRow(
            effect=UKEffectRecord(
                effect_id="eff-1",
                effect_type="repealed",
                applied=True,
                requires_applied=False,
                modified="2025-01-01",
                affected_uri="/id/ukpga/2000/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 1",
                affecting_uri="/id/ukpga/2025/1",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2025",
                affecting_number="1",
                affecting_provisions="s. 2",
                affecting_title="Test Act",
                in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
            ),
            summary=_EffectSummary(
                source_pathology="",
                compare_shape="commensurable",
                n_ops=1,
                candidate=True,
                resolver_eids=("section-1",),
                lowering_rejections=(),
                replay_applicable=True,
                structural_for_replay=True,
            ),
        ),
    )

    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=rows,
        filters=_EffectFilters(limit=1),
        summary_only=True,
        matched_effect_count_before_limit=3,
    )

    assert report["summary"]["matched_effects"] == 3
    assert report["summary"]["matched_effect_count_before_limit"] == 3
    assert report["summary"]["emitted_effect_count"] == 1
    assert report["summary"]["truncated"] is True
    assert report["summary"]["diagnostic_count_scope"] == "emitted_rows"
    assert report["summary"]["candidate_counts"] == {"candidate": 1, "not_candidate": 0}
    assert report["summary"]["replay_applicability_counts"] == {
        "replay_applicable": 1,
        "not_replay_applicable": 0,
    }
    assert report["summary"]["structural_for_replay_counts"] == {
        "structural_for_replay": 1,
        "not_structural_for_replay": 0,
    }
    assert report["summary"]["suggested_claim_template_status_counts"] == {}


def test_uk_effects_limit_zero_summary_preserves_matched_count() -> None:
    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=(),
        filters=_EffectFilters(limit=0),
        summary_only=True,
        matched_effect_count_before_limit=2,
    )

    assert report["summary"]["matched_effects"] == 2
    assert report["summary"]["matched_effect_count_before_limit"] == 2
    assert report["summary"]["emitted_effect_count"] == 0
    assert report["summary"]["truncated"] is True
    assert report["summary"]["diagnostic_count_scope"] == "emitted_rows"
    assert report["summary"]["candidate_counts"] == {"candidate": 0, "not_candidate": 0}
    assert report["summary"]["replay_applicability_counts"] == {
        "replay_applicable": 0,
        "not_replay_applicable": 0,
    }
    assert "rows" not in report


def test_uk_effects_report_jsonable_can_expose_source_surface() -> None:
    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=(),
        filters=_EffectFilters(limit=0),
        summary_only=True,
        source={
            "archive_path": "data/uk_legislation.farchive",
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "oracle_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
            "enacted_missing": False,
            "oracle_missing": True,
            "enacted_source_status": "available",
            "oracle_source_status": "absent",
            "enacted_source_size": 123,
            "oracle_source_size": 0,
        },
    )

    assert report["source"] == {
        "archive_path": "data/uk_legislation.farchive",
        "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        "oracle_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        "enacted_missing": False,
        "oracle_missing": True,
        "enacted_source_status": "available",
        "oracle_source_status": "absent",
        "enacted_source_size": 123,
        "oracle_source_size": 0,
    }
    assert report["effect_feed_parse_rejections"] == {
        "count": 0,
        "rule_counts": {},
        "rows": [],
    }


def test_uk_effects_json_records_available_source_parse_failures(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    malformed_xml = b"<Legislation>" + (b"x" * 128)

    class FakeArchive:
        def __init__(self, path):
            self._db_path = str(path)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url):
            return malformed_xml

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            limit=0,
            json=True,
            summary_only=True,
            uk_applicability_mode="effective_date_plus_feed_applied",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["enacted_source_status"] == "available"
    assert payload["source"]["oracle_source_status"] == "available"
    assert payload["source"]["enacted_missing"] is True
    assert payload["source"]["oracle_missing"] is True
    assert payload["source"]["enacted_source_parse_failed"] is True
    assert payload["source"]["oracle_source_parse_failed"] is True
    assert payload["source_parse_rejections"]["rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert payload["source_parse_rejections"]["rows"][0]["blocking"] is True
    assert payload["source_parse_rejections"]["rows"][0]["strict_disposition"] == "block"
    assert payload["source_parse_observation_rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
        "uk_oracle_xml_parse_rejected": 1,
    }
def test_uk_effects_report_jsonable_exposes_feed_parse_rejections() -> None:
    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=(),
        filters=_EffectFilters(limit=0),
        summary_only=True,
        parse_rejections=(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed-1",
            },
            {
                "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
                "phase": "acquisition",
                "feed_locator": "https://example.test/feed-2",
            },
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed-3",
            },
        ),
    )

    assert report["effect_feed_parse_rejections"] == {
        "count": 3,
        "rule_counts": {
            "uk_effect_feed_locator_payload_missing_rejected": 1,
            "uk_effect_feed_xml_parse_rejected": 2,
        },
        "rows": [
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed-1",
            },
            {
                "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
                "phase": "acquisition",
                "feed_locator": "https://example.test/feed-2",
            },
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed-3",
            },
        ],
    }
    assert report["effect_feed_observation_count"] == 3
    assert report["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_locator_payload_missing_rejected": 1,
        "uk_effect_feed_xml_parse_rejected": 2,
    }
    assert report["effect_feed_observations"] == report["effect_feed_parse_rejections"]["rows"]


def test_uk_effects_report_jsonable_splits_nonblocking_feed_observations() -> None:
    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=(),
        filters=_EffectFilters(limit=0),
        summary_only=True,
        parse_rejections=(
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "phase": "parse",
                "blocking": False,
            },
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "blocking": True,
            },
        ),
    )

    assert report["effect_feed_parse_rejections"] == {
        "count": 1,
        "rule_counts": {"uk_effect_feed_xml_parse_rejected": 1},
        "rows": [
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "blocking": True,
            }
        ],
    }
    assert report["effect_feed_observation_count"] == 2
    assert report["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
def test_effect_context_source_jsonable_records_missing_source_surfaces() -> None:
    context = _EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
        archive_path="data/uk_legislation.farchive",
        enacted_url="https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        oracle_url="https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        enacted_missing=False,
        oracle_missing=True,
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=123,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
    )

    assert _effect_context_source_jsonable(context) == {
        "archive_path": "data/uk_legislation.farchive",
        "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        "oracle_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        "enacted_missing": False,
        "oracle_missing": True,
        "enacted_source_status": "available",
        "oracle_source_status": "too_small",
        "enacted_source_size": 123,
        "oracle_source_size": 7,
        "enacted_source_sha256": "enacted-sha",
        "oracle_source_sha256": "oracle-sha",
        "enacted_source_parse_failed": False,
        "oracle_source_parse_failed": False,
    }


def test_uk_effects_source_state_distinguishes_absent_too_small_and_available() -> None:
    assert _uk_effects_source_state(None) == ("absent", 0)
    assert _uk_effects_source_state(b"") == ("too_small", 0)
    assert _uk_effects_source_state(b"<short/>") == ("too_small", 8)
    assert _uk_effects_source_state(b"x" * 100) == ("available", 100)


def test_uk_effects_main_limit_zero_json_summary_preserves_matched_count(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effects = [
        UKEffectRecord(
            effect_id="eff-1",
            effect_type="repealed",
            applied=True,
            requires_applied=False,
            modified="2025-01-01",
            affected_uri="/id/ukpga/2000/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions="s. 1",
            affecting_uri="/id/ukpga/2025/1",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number="1",
            affecting_provisions="s. 2",
            affecting_title="Test Act",
            in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
        ),
        UKEffectRecord(
            effect_id="eff-2",
            effect_type="inserted",
            applied=True,
            requires_applied=False,
            modified="2025-01-02",
            affected_uri="/id/ukpga/2000/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions="s. 2",
            affecting_uri="/id/ukpga/2025/2",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number="2",
            affecting_provisions="s. 3",
            affecting_title="Other Act",
            in_force_dates=[{"date": "2025-01-02", "prospective": "false"}],
        ),
    ]

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda statute_id, archive, **kwargs: effects,
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_url="https://example.test/ukpga/2000/1/data.xml",
            enacted_missing=True,
            oracle_missing=True,
            enacted_source_status="too_small",
            oracle_source_status="absent",
            enacted_source_size=8,
            oracle_source_size=0,
        ),
    )
    monkeypatch.setattr(
        uk_effects,
        "summarize_uk_effect",
        lambda effect, archive, context, **kwargs: pytest.fail(
            "limit=0 should not summarize ordinary rows"
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            limit=0,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=True,
            summary_only=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["matched_effects"] == 2
    assert payload["summary"]["emitted_effect_count"] == 0
    assert payload["summary"]["truncated"] is True
    assert "rows" not in payload


def test_uk_effects_text_splits_feed_observations_from_rejections(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_load_effects(statute_id, archive, *, parse_rejections_out=None):
        del statute_id, archive
        assert parse_rejections_out is not None
        parse_rejections_out.extend(
            (
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "phase": "parse",
                    "blocking": False,
                },
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "blocking": True,
                },
            )
        )
        return []

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        fake_load_effects,
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_url="https://example.test/ukpga/2000/1/data.xml",
            enacted_missing=True,
            oracle_missing=True,
            enacted_source_status="absent",
            oracle_source_status="absent",
            enacted_source_size=0,
            oracle_source_size=0,
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            limit=0,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=False,
            summary_only=True,
        )
    )

    out = capsys.readouterr().out
    assert "Effect feed parse/acquisition observations:" in out
    assert "  uk_effect_feed_pages_absent_recorded: 1" in out
    assert "  uk_effect_feed_xml_parse_rejected: 1" in out
    assert "Blocking effect feed parse/acquisition rejections:" in out
    assert out.count("  uk_effect_feed_xml_parse_rejected: 1") == 2
    assert (
        "Blocking effect feed parse/acquisition rejections:\n"
        "  uk_effect_feed_pages_absent_recorded"
    ) not in out


def test_uk_effects_main_candidate_only_classifies_before_limit(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effects = [
        UKEffectRecord(
            effect_id=f"eff-{index}",
            effect_type="repealed",
            applied=True,
            requires_applied=False,
            modified=f"2025-01-0{index}",
            affected_uri="/id/ukpga/2000/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions=f"s. {index}",
            affecting_uri=f"/id/ukpga/2025/{index}",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number=str(index),
            affecting_provisions=f"s. {index + 10}",
            affecting_title="Test Act",
            in_force_dates=[{"date": f"2025-01-0{index}", "prospective": "false"}],
        )
        for index in (1, 2, 3)
    ]
    summarized: list[str] = []

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_summary(effect, archive, context, **kwargs):
        summarized.append(effect.effect_id)
        return _EffectSummary(
            source_pathology="",
            compare_shape="commensurable",
            n_ops=1,
            candidate=effect.effect_id != "eff-2",
            resolver_eids=(f"section-{effect.effect_id[-1]}",),
            lowering_rejections=(),
            replay_applicable=True,
            structural_for_replay=True,
            applicability_mode=kwargs.get(
                "applicability_mode",
                "effective_date_plus_feed_applied",
            ),
        )

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda statute_id, archive, **kwargs: effects,
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_url="https://example.test/ukpga/2000/1/data.xml",
            enacted_missing=True,
            oracle_missing=True,
            enacted_source_status="too_small",
            oracle_source_status="absent",
            enacted_source_size=8,
            oracle_source_size=0,
        ),
    )
    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summary)

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            limit=1,
            applied_only=False,
            structural_only=False,
            candidate_only=True,
            non_candidate_only=False,
            json=True,
            summary_only=False,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert summarized == ["eff-1", "eff-2", "eff-3"]
    assert payload["summary"]["matched_effects"] == 2
    assert payload["summary"]["emitted_effect_count"] == 1
    assert payload["summary"]["truncated"] is True
    assert [row["effect_id"] for row in payload["rows"]] == ["eff-1"]


def test_uk_effects_main_lowering_rule_filter_classifies_before_limit(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effects = [
        UKEffectRecord(
            effect_id=f"eff-{index}",
            effect_type="words inserted",
            applied=True,
            requires_applied=False,
            modified=f"2025-01-0{index}",
            affected_uri="/id/ukpga/2000/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions=f"s. {index}",
            affecting_uri=f"/id/ukpga/2025/{index}",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number=str(index),
            affecting_provisions=f"s. {index + 10}",
            affecting_title="Test Act",
            in_force_dates=[{"date": f"2025-01-0{index}", "prospective": "false"}],
        )
        for index in (1, 2, 3)
    ]
    summarized: list[str] = []

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_summary(effect, archive, context, **kwargs):
        summarized.append(effect.effect_id)
        lowering_rejections = (
            {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
        ) if effect.effect_id != "eff-2" else ()
        return _EffectSummary(
            source_pathology="instruction_text_reused_as_payload" if lowering_rejections else "",
            compare_shape="",
            n_ops=0 if lowering_rejections else 1,
            candidate=not lowering_rejections,
            resolver_eids=(),
            lowering_rejections=lowering_rejections,
            replay_applicable=True,
            structural_for_replay=True,
            applicability_mode=kwargs.get(
                "applicability_mode",
                "effective_date_plus_feed_applied",
            ),
        )

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda statute_id, archive, **kwargs: effects,
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )
    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summary)

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            source_pathology="",
            lowering_rule="uk_effect_overlap_substitution_unlowered",
            lowering_reason_code="",
            source_acquisition_rule="",
            limit=1,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=True,
            summary_only=False,
            uk_applicability_mode="effective_date_plus_feed_applied",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert summarized == ["eff-1", "eff-2", "eff-3"]
    assert payload["filters"]["lowering_rule"] == "uk_effect_overlap_substitution_unlowered"
    assert payload["summary"]["matched_effects"] == 2
    assert payload["summary"]["emitted_effect_count"] == 1
    assert payload["summary"]["truncated"] is True
    assert [row["effect_id"] for row in payload["rows"]] == ["eff-1"]


def test_uk_effects_main_fast_limit_stops_after_matching_diagnostic_rows(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effects = [
        UKEffectRecord(
            effect_id=f"eff-{index}",
            effect_type="words inserted",
            applied=True,
            requires_applied=False,
            modified=f"2025-01-0{index}",
            affected_uri="/id/ukpga/2000/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2000",
            affected_number="1",
            affected_provisions=f"s. {index}",
            affecting_uri=f"/id/ukpga/2025/{index}",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year="2025",
            affecting_number=str(index),
            affecting_provisions=f"s. {index + 10}",
            affecting_title="Test Act",
            in_force_dates=[{"date": f"2025-01-0{index}", "prospective": "false"}],
        )
        for index in (1, 2, 3)
    ]
    summarized: list[str] = []

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_summary(effect, archive, context, **kwargs):
        summarized.append(effect.effect_id)
        lowering_rejections = (
            {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
        ) if effect.effect_id != "eff-2" else ()
        return _EffectSummary(
            source_pathology="instruction_text_reused_as_payload" if lowering_rejections else "",
            compare_shape="",
            n_ops=0 if lowering_rejections else 1,
            candidate=not lowering_rejections,
            resolver_eids=(),
            lowering_rejections=lowering_rejections,
            replay_applicable=True,
            structural_for_replay=True,
            applicability_mode=kwargs.get(
                "applicability_mode",
                "effective_date_plus_feed_applied",
            ),
        )

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda statute_id, archive, **kwargs: effects,
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
        ),
    )
    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summary)

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            source_pathology="",
            lowering_rule="uk_effect_overlap_substitution_unlowered",
            lowering_reason_code="",
            source_acquisition_rule="",
            limit=1,
            fast_limit=True,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=True,
            summary_only=False,
            evidence_jsonl="",
            uk_applicability_mode="effective_date_plus_feed_applied",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert summarized == ["eff-1"]
    assert payload["filters"]["fast_limit"] is True
    assert payload["fast_limit"]["matched_effect_count_exact"] is False
    assert payload["summary"]["matched_effects"] == 1
    assert payload["summary"]["emitted_effect_count"] == 1
    assert [row["effect_id"] for row in payload["rows"]] == ["eff-1"]


def test_uk_effects_report_rows_expose_replay_applicability() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    rows = (
        _EffectReportRow(
            effect=UKEffectRecord(
                effect_id="eff-metadata",
                effect_type="inserted",
                applied=False,
                requires_applied=True,
                modified="2025-01-01",
                affected_uri="/id/ukpga/2000/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 1",
                affecting_uri="/id/ukpga/2025/1",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2025",
                affecting_number="1",
                affecting_provisions="s. 2",
                affecting_title="Test Act",
                in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
                metadata_only=True,
            ),
            summary=_EffectSummary(
                source_pathology="",
                compare_shape="",
                n_ops=1,
                candidate=True,
                resolver_eids=("section-1",),
                lowering_rejections=(),
                replay_applicable=True,
                structural_for_replay=True,
            ),
        ),
    )

    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=rows,
        filters=_EffectFilters(),
    )

    row = report["rows"][0]
    assert row["applied"] is False
    assert row["requires_applied"] is True
    assert row["metadata_only"] is True
    assert row["replay_applicable"] is True
    assert row["structural"] is True
    assert row["structural_for_replay"] is True
    assert row["applicability_mode"] == "effective_date_plus_feed_applied"


def test_uk_effects_report_rows_use_summary_applicability_lens() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-unapplied",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    rows = (
        _EffectReportRow(
            effect=effect,
            summary=_EffectSummary(
                source_pathology="",
                compare_shape="",
                n_ops=1,
                candidate=True,
                resolver_eids=("section-1",),
                lowering_rejections=(),
                replay_applicable=True,
                structural_for_replay=True,
                applicability_mode="effective_date_only",
            ),
        ),
    )

    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=rows,
        filters=_EffectFilters(applicability_mode="effective_date_only"),
    )

    assert effect.is_applicable_for_replay() is False
    assert report["filters"]["applicability_mode"] == "effective_date_only"
    assert report["summary"]["replay_applicability_counts"] == {
        "replay_applicable": 1,
        "not_replay_applicable": 0,
    }
    assert report["rows"][0]["replay_applicable"] is True
    assert report["rows"][0]["structural_for_replay"] is True
    assert report["rows"][0]["applicability_mode"] == "effective_date_only"


def test_uk_effects_text_rows_expose_replay_applicability(monkeypatch, tmp_path, capsys) -> None:
    import farchive
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effect = UKEffectRecord(
        effect_id="eff-metadata",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
        metadata_only=True,
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda statute_id, archive, **kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_url="https://example.test/ukpga/2000/1/data.xml",
            enacted_missing=True,
            oracle_missing=True,
            enacted_source_status="too_small",
            oracle_source_status="absent",
            enacted_source_size=8,
            oracle_source_size=0,
        ),
    )
    monkeypatch.setattr(
        uk_effects,
        "summarize_uk_effect",
        lambda effect_arg, archive, context, **kwargs: _EffectSummary(
            source_pathology="",
            compare_shape="",
            n_ops=1,
            candidate=False,
            resolver_eids=("section-1",),
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_lowering_note_only_observed",
                    "phase": "lowering",
                    "blocking": False,
                },
                {
                    "rule_id": "uk_effect_lowering_no_ops_rejected",
                    "phase": "lowering",
                    "blocking": True,
                },
            ),
            source_acquisition_rejections=(
                {
                    "rule_id": "uk_affecting_act_xml_missing_rejected",
                    "phase": "acquisition",
                    "blocking": True,
                },
                {
                    "rule_id": "uk_affecting_act_xml_cached_recorded",
                    "phase": "acquisition",
                    "blocking": False,
                    "strict_disposition": "record",
                },
            ),
            replay_applicable=True,
            structural_for_replay=True,
            applicability_mode=kwargs.get(
                "applicability_mode",
                "effective_date_plus_feed_applied",
            ),
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            limit=None,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=False,
            summary_only=False,
        )
    )

    out = capsys.readouterr().out
    assert f"Archive: {db_path}" in out
    assert "Enacted URL: https://example.test/ukpga/2000/1/enacted/data.xml" in out
    assert "Oracle URL: https://example.test/ukpga/2000/1/data.xml" in out
    assert "Enacted source: too_small (8 bytes)" in out
    assert "Oracle source:  absent (0 bytes)" in out
    assert "applied:    False  requires-applied: True  metadata-only: True" in out
    assert (
        "replay:     mode=effective_date_plus_feed_applied  applicable=True  "
        "structural=True  structural-for-replay=True"
    ) in out
    assert (
        "lowering rejections: 2  "
        "uk_effect_lowering_no_ops_rejected=1,"
        "uk_effect_lowering_note_only_observed=1"
    ) in out
    assert "blocking lowering: 1  uk_effect_lowering_no_ops_rejected=1" in out
    assert "source acquisition observations: 2" in out
    assert "uk_affecting_act_xml_cached_recorded=1" in out
    assert "uk_affecting_act_xml_missing_rejected=1" in out
    assert (
        "source acquisition rejections: 1  "
        "uk_affecting_act_xml_missing_rejected=1"
    ) in out


def test_uk_effect_rows_to_summarize_prelimits_only_without_candidate_filters() -> None:
    rows = [object(), object(), object()]

    assert _effect_rows_to_summarize(
        rows,
        limit=1,
        candidate_only=False,
        non_candidate_only=False,
    ) == rows[:1]
    assert _effect_rows_to_summarize(
        rows,
        limit=1,
        candidate_only=True,
        non_candidate_only=False,
    ) == rows
    assert _effect_rows_to_summarize(
        rows,
        limit=1,
        candidate_only=False,
        non_candidate_only=True,
    ) == rows
    assert _effect_rows_to_summarize(
        rows,
        limit=1,
        candidate_only=False,
        non_candidate_only=False,
        post_summary_filter=True,
    ) == rows


def test_uk_effect_summary_matches_post_summary_filters() -> None:
    summary = _EffectSummary(
        source_pathology="instruction_text_reused_as_payload",
        compare_shape="",
        n_ops=0,
        candidate=False,
        resolver_eids=(),
        lowering_rejections=(
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "reason_code": "overlap_substitution_parse_failed",
            },
        ),
        source_acquisition_rejections=(
            {"rule_id": "uk_affecting_act_xml_missing_rejected"},
        ),
        manual_compile_status="deterministic_frontend_candidate",
        manual_compile_rule_id="uk_manual_frontier_parser_or_extraction_candidate",
    )

    assert _effect_summary_matches_filters(
        summary,
        source_pathology="instruction_text_reused_as_payload",
    )
    assert _effect_summary_matches_filters(
        summary,
        lowering_rule="uk_effect_overlap_substitution_unlowered",
    )
    assert _effect_summary_matches_filters(
        summary,
        lowering_reason_code="overlap_substitution_parse_failed",
    )
    assert _effect_summary_matches_filters(summary, blocking_only=True)
    assert _effect_summary_matches_filters(
        summary,
        source_acquisition_rule="uk_affecting_act_xml_missing_rejected",
    )
    assert _effect_summary_matches_filters(
        summary,
        manual_compile_status="deterministic_frontend_candidate",
    )
    assert _effect_summary_matches_filters(
        summary,
        manual_compile_rule="uk_manual_frontier_parser_or_extraction_candidate",
    )
    assert not _effect_summary_matches_filters(summary, source_pathology="__none__")
    assert not _effect_summary_matches_filters(summary, lowering_rule="missing-rule")
    assert not _effect_summary_matches_filters(
        summary,
        lowering_reason_code="missing_reason",
    )
    assert not _effect_summary_matches_filters(
        _EffectSummary(
            source_pathology="",
            compare_shape="",
            n_ops=1,
            candidate=True,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_note_only_observed", "blocking": False},
            ),
        ),
        blocking_only=True,
    )
    assert not _effect_summary_matches_filters(
        summary,
        source_acquisition_rule="missing-rule",
    )
    assert not _effect_summary_matches_filters(
        summary,
        manual_compile_status="manual_compile_candidate",
    )
    assert not _effect_summary_matches_filters(
        summary,
        manual_compile_rule="uk_manual_frontier_heading_facet_candidate",
    )


def test_uk_effect_row_matches_claim_template_status_filter() -> None:
    def _row(
        *,
        effect_id: str,
        rule_id: str,
        source_text: str,
        status: str = "manual_compile_candidate",
    ) -> _EffectReportRow:
        return _EffectReportRow(
            effect=UKEffectRecord(
                effect_id=effect_id,
                effect_type="words substituted",
                applied=True,
                requires_applied=True,
                modified="2024-01-01",
                affected_uri="/id/ukpga/2000/1/section/1",
                affected_class="UnitedKingdomPublicGeneralAct",
                affected_year="2000",
                affected_number="1",
                affected_provisions="s. 1",
                affecting_uri="/id/ukpga/2024/1",
                affecting_class="UnitedKingdomPublicGeneralAct",
                affecting_year="2024",
                affecting_number="1",
                affecting_provisions="s. 2",
                affecting_title="Test Act 2024",
            ),
            summary=_EffectSummary(
                source_pathology="unhandled_instruction_text",
                compare_shape="",
                n_ops=0,
                candidate=False,
                resolver_eids=(),
                lowering_rejections=(
                    {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
                ),
                replay_applicable=True,
                structural_for_replay=True,
                source_extracted=True,
                source_extracted_tag="P1",
                source_extracted_text_preview=source_text,
                manual_compile_status=status,
                manual_compile_rule_id=rule_id,
                manual_compile_reason="test",
            ),
        )

    available_row = _row(
        effect_id="available-template",
        rule_id="uk_manual_frontier_heading_facet_candidate",
        source_text='In the title, for "old" substitute "new".',
    )
    unavailable_row = _row(
        effect_id="unavailable-template",
        rule_id="uk_manual_frontier_unclassified",
        source_text="Do something currently unclassified.",
    )
    out_of_scope_row = _row(
        effect_id="out-of-scope",
        rule_id="uk_manual_frontier_as_if_application_modification_out_of_scope",
        source_text="The Act applies as if modified.",
        status="non_textual_or_out_of_scope",
    )

    assert _effect_row_matches_filters(
        available_row,
        statute_id="ukpga/2000/1",
        claim_template_status="available",
    )
    assert not _effect_row_matches_filters(
        unavailable_row,
        statute_id="ukpga/2000/1",
        claim_template_status="available",
    )
    assert _effect_row_matches_filters(
        unavailable_row,
        statute_id="ukpga/2000/1",
        claim_template_status="not_available",
    )
    assert not _effect_row_matches_filters(
        out_of_scope_row,
        statute_id="ukpga/2000/1",
        claim_template_status="not_available",
    )


def test_uk_effects_report_jsonable_can_omit_rows_for_summary_only() -> None:
    report = uk_effects_report_jsonable(
        statute_id="ukpga/2000/1",
        rows=(),
        filters=_EffectFilters(
            affected_contains="s. 1",
            applied_only=True,
            structural_only=True,
            candidate_only=True,
            limit=10,
        ),
        summary_only=True,
    )

    assert report["report_kind"] == "uk_effects_frontier_report"
    assert report["statute_id"] == "ukpga/2000/1"
    assert report["filters"] == {
        "affected_contains": "s. 1",
        "affecting_contains": "",
        "effect_type_contains": "",
        "source_pathology": "",
        "lowering_rule": "",
        "lowering_reason_code": "",
        "blocking_only": False,
        "source_acquisition_rule": "",
        "manual_compile_status": "",
        "manual_compile_rule": "",
        "claim_template_status": "",
        "applied_only": True,
        "structural_only": True,
        "candidate_only": True,
        "non_candidate_only": False,
        "limit": 10,
        "fast_limit": False,
        "applicability_mode": "effective_date_plus_feed_applied",
    }
    assert report["summary"]["matched_effects"] == 0
    assert report["summary"]["matched_effect_count_before_limit"] == 0
    assert report["summary"]["emitted_effect_count"] == 0
    assert report["summary"]["truncated"] is False
    assert report["summary"]["replay_applicability_counts"] == {
        "replay_applicable": 0,
        "not_replay_applicable": 0,
    }
    assert report["summary"]["structural_for_replay_counts"] == {
        "structural_for_replay": 0,
        "not_structural_for_replay": 0,
    }
    assert "rows" not in report


def test_uk_effects_rejects_conflicting_candidate_filters(capsys) -> None:
    args = Namespace(
        statute_id="ukpga/2000/1",
        db="does-not-matter.farchive",
        affected_contains="",
        affecting_contains="",
        effect_type_contains="",
        limit=None,
        applied_only=False,
        structural_only=False,
        candidate_only=True,
        non_candidate_only=True,
        json=False,
        summary_only=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_effects.main(args)

    assert excinfo.value.code == 2
    assert "--candidate-only cannot be combined" in capsys.readouterr().err


def test_uk_effects_rejects_negative_limit(capsys) -> None:
    args = Namespace(
        statute_id="ukpga/2000/1",
        db="does-not-matter.farchive",
        affected_contains="",
        affecting_contains="",
        effect_type_contains="",
        limit=-1,
        applied_only=False,
        structural_only=False,
        candidate_only=False,
        non_candidate_only=False,
        json=False,
        summary_only=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_effects.main(args)

    assert excinfo.value.code == 2
    assert "--limit must be zero or a positive integer" in capsys.readouterr().err


def test_uk_effects_evidence_jsonl_requires_manual_frontier_filter(capsys) -> None:
    args = Namespace(
        statute_id="ukpga/2000/1",
        db="does-not-matter.farchive",
        affected_contains="",
        affecting_contains="",
        effect_type_contains="",
        source_pathology="",
        lowering_rule="",
        lowering_reason_code="",
        source_acquisition_rule="",
        manual_compile_status="",
        manual_compile_rule="",
        limit=None,
        applied_only=False,
        structural_only=False,
        candidate_only=False,
        non_candidate_only=False,
        json=False,
        summary_only=False,
        evidence_jsonl=".tmp/uk-manual.jsonl",
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_effects.main(args)

    assert excinfo.value.code == 2
    assert (
        "--evidence-jsonl requires --manual-compile-status, "
        "--manual-compile-rule, --claim-template-status, or "
        "--lowering-reason-code"
        in capsys.readouterr().err
    )


def test_uk_effects_evidence_jsonl_threads_applicability_regime(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    out_path = tmp_path / "manual" / "rows.jsonl"
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/enacted.xml",
            oracle_url="https://example.test/current.xml",
            enacted_source_status="available",
            oracle_source_status="available",
        ),
    )
    monkeypatch.setattr(
        uk_effects,
        "summarize_uk_effect",
        lambda effect_arg, archive, context, **kwargs: _EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the title, for "old" substitute "new".',
            applicability_mode=kwargs["applicability_mode"],
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires manual compile.",
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            source_pathology="",
            lowering_rule="",
            source_acquisition_rule="",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule="",
            limit=None,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=True,
            summary_only=True,
            evidence_jsonl=str(out_path),
            uk_applicability_mode="effective_date_only",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    expected_regime = {
        "allow_metadata_backfill": True,
        "allow_metadata_only_effects": True,
        "allow_oracle_alignment": True,
        "applicability_mode": "effective_date_only",
        "authority_mode": "current_mixed",
    }
    assert payload["manual_compile_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
        "replay_regime": expected_regime,
    }
    assert rows[0]["replay_regime"] == expected_regime


def test_uk_effects_evidence_jsonl_accepts_manual_compile_rule_filter(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    out_path = tmp_path / "manual" / "rows.jsonl"
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda statute_id, archive: _EffectSummaryContext(
            statute_id=statute_id,
            enacted_ir=None,
            oracle_ir=None,
            base_eids=set(),
            oracle_eids=set(),
            base_text_map={},
            oracle_eid_map={},
            oracle_text_map={},
            resolver=None,
            affecting_xml_cache={},
            archive_path=str(db_path),
            enacted_url="https://example.test/enacted.xml",
            oracle_url="https://example.test/current.xml",
            enacted_source_status="available",
            oracle_source_status="available",
        ),
    )
    monkeypatch.setattr(
        uk_effects,
        "summarize_uk_effect",
        lambda effect_arg, archive, context, **kwargs: _EffectSummary(
            source_pathology="",
            compare_shape="",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires manual compile.",
            applicability_mode=kwargs["applicability_mode"],
        ),
    )

    uk_effects.main(
        Namespace(
            statute_id="ukpga/2000/1",
            db=str(db_path),
            affected_contains="",
            affecting_contains="",
            effect_type_contains="",
            source_pathology="",
            lowering_rule="",
            source_acquisition_rule="",
            manual_compile_status="",
            manual_compile_rule="uk_manual_frontier_heading_facet_candidate",
            limit=None,
            applied_only=False,
            structural_only=False,
            candidate_only=False,
            non_candidate_only=False,
            json=True,
            summary_only=True,
            evidence_jsonl=str(out_path),
            uk_applicability_mode="effective_date_plus_feed_applied",
            uk_source_first_candidate=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    expected_regime = {
        "allow_metadata_backfill": False,
        "allow_metadata_only_effects": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
    }
    assert payload["manual_compile_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
        "replay_regime": expected_regime,
    }
    assert rows[0]["manual_compile_status"] == "manual_compile_candidate"
    assert rows[0]["manual_compile_rule_id"] == "uk_manual_frontier_heading_facet_candidate"
    assert rows[0]["replay_regime"] == expected_regime


def test_uk_effect_report_jsonable_records_single_effect_evidence() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-1",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1(2)",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 3",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    extracted = ET.fromstring("<BlockAmendment eId='ukpga-2025-1-section-3'>Inserted text</BlockAmendment>")

    report = uk_effect_report_jsonable(
        statute_id="ukpga/2000/1",
        effect=effect,
        source_pathology="",
        extracted=extracted,
        lowering_rejections=[
            {
                "rule_id": "uk_effect_overlap_substitution_unlowered",
                "strict_disposition": "record",
            }
        ],
        compare_shape="",
        candidate=True,
        op_rows=[
            {
                "op_id": "eff-1",
                "action": "insert",
                "target": "section:1/subsection:2",
                "payload_kind": "subsection",
                "resolver_eid": "section-1-2",
                "base_target_present": False,
                "oracle_target_present": True,
                "base_descendant_present": False,
                "oracle_descendant_present": False,
                "parent_eid": "section-1",
                "base_parent_present": True,
                "oracle_parent_present": True,
                "payload": None,
            }
        ],
    )

    assert report["report_kind"] == "uk_effect_frontier_report"
    assert report["source_surface"] == {}
    assert report["applicability_mode"] == "effective_date_plus_feed_applied"
    assert report["effect"]["applied"] is True
    assert report["effect"]["requires_applied"] is True
    assert report["effect"]["metadata_only"] is False
    assert report["effect"]["replay_applicable"] is True
    assert report["effect"]["structural"] is True
    assert report["effect"]["structural_for_replay"] is True
    assert report["source"] == {
        "pathology": "",
        "extracted": True,
        "tag": "BlockAmendment",
        "id": "ukpga-2025-1-section-3",
        "text": "Inserted text",
    }
    assert report["lowering"]["compiled_op_count"] == 1
    assert report["lowering"]["observation_count"] == 1
    assert report["lowering"]["observation_rule_counts"] == {
        "uk_effect_overlap_substitution_unlowered": 1,
    }
    assert report["lowering"]["observations"] == [
        {
            "rule_id": "uk_effect_overlap_substitution_unlowered",
            "strict_disposition": "record",
        }
    ]
    assert report["lowering"]["rejection_count"] == 0
    assert report["lowering"]["rejection_rule_counts"] == {}
    assert report["lowering"]["rejections"] == []
    assert report["lowering"]["blocking_rejection_count"] == 0
    assert report["lowering"]["has_blocking_rejection"] is False
    assert report["lowering"]["blocking_rejection_rule_counts"] == {}
    assert report["effect_feed_parse_rejections"] == {
        "count": 0,
        "rule_counts": {},
        "rows": [],
    }
    assert report["compare"]["resolver_eids"] == ["section-1-2"]
    assert report["compare"]["base_target_hits"] == [False]
    assert report["compare"]["oracle_target_hits"] == [True]


def test_uk_effect_report_jsonable_uses_explicit_applicability_mode() -> None:
    effect = UKEffectRecord(
        effect_id="eff-unapplied",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    default_report = uk_effect_report_jsonable(
        statute_id="ukpga/2000/1",
        effect=effect,
        source_pathology="",
        extracted=None,
        lowering_rejections=[],
        compare_shape="",
        candidate=False,
        op_rows=[],
    )
    effective_date_only_report = uk_effect_report_jsonable(
        statute_id="ukpga/2000/1",
        effect=effect,
        source_pathology="",
        extracted=None,
        lowering_rejections=[],
        compare_shape="",
        candidate=False,
        op_rows=[],
        applicability_mode="effective_date_only",
    )

    assert default_report["applicability_mode"] == "effective_date_plus_feed_applied"
    assert default_report["effect"]["replay_applicable"] is False
    assert default_report["effect"]["structural_for_replay"] is False
    assert effective_date_only_report["applicability_mode"] == "effective_date_only"
    assert effective_date_only_report["effect"]["replay_applicable"] is True
    assert effective_date_only_report["effect"]["structural_for_replay"] is True


def test_uk_effect_report_jsonable_exposes_single_effect_feed_parse_rejections() -> None:
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    effect = UKEffectRecord(
        effect_id="eff-1",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 3",
        affecting_title="Test Act",
        in_force_dates=[],
    )

    report = uk_effect_report_jsonable(
        statute_id="ukpga/2000/1",
        effect=effect,
        source_pathology="",
        extracted=None,
        lowering_rejections=[],
        compare_shape="",
        candidate=False,
        op_rows=[],
        source_surface={
            "archive_path": "data/uk_legislation.farchive",
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "oracle_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
            "enacted_missing": False,
            "oracle_missing": True,
        },
        parse_rejections=(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed",
            },
        ),
        source_acquisition_rejections=(
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "affecting_act_id": "ukpga/2025/1",
                "blocking": True,
            },
        ),
    )

    assert report["effect_feed_parse_rejections"] == {
        "count": 1,
        "rule_counts": {"uk_effect_feed_xml_parse_rejected": 1},
        "rows": [
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed",
            }
        ],
    }
    assert report["effect_feed_observation_count"] == 1
    assert report["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert report["effect_feed_observations"] == report["effect_feed_parse_rejections"]["rows"]
    assert report["source_surface"] == {
        "archive_path": "data/uk_legislation.farchive",
        "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        "oracle_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        "enacted_missing": False,
        "oracle_missing": True,
    }
    assert report["source_acquisition_rejections"] == {
        "count": 1,
        "rule_counts": {"uk_affecting_act_xml_missing_rejected": 1},
        "rows": [
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "affecting_act_id": "ukpga/2025/1",
                "blocking": True,
            }
        ],
    }
    assert report["candidate"] is False
    json.dumps(report, sort_keys=True)


def test_uk_effect_report_jsonable_splits_nonblocking_feed_observations() -> None:
    effect = UKEffectRecord(
        effect_id="eff-1",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 3",
        affecting_title="Test Act",
        in_force_dates=[],
    )

    report = uk_effect_report_jsonable(
        statute_id="ukpga/2000/1",
        effect=effect,
        source_pathology="",
        extracted=None,
        lowering_rejections=[],
        compare_shape="",
        candidate=False,
        op_rows=[],
        parse_rejections=(
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "phase": "parse",
                "strict_disposition": "record",
            },
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "blocking": True,
            },
        ),
        source_acquisition_rejections=(
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            },
            {
                "rule_id": "uk_affecting_act_xml_cached_recorded",
                "phase": "acquisition",
                "blocking": False,
                "strict_disposition": "record",
            },
        ),
    )

    assert report["effect_feed_parse_rejections"] == {
        "count": 1,
        "rule_counts": {"uk_effect_feed_xml_parse_rejected": 1},
        "rows": [
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "blocking": True,
            }
        ],
    }
    assert report["effect_feed_observation_count"] == 2
    assert report["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert report["source_acquisition_rejections"] == {
        "count": 1,
        "rule_counts": {"uk_affecting_act_xml_missing_rejected": 1},
        "rows": [
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            }
        ],
    }
    assert report["source_acquisition_observation_count"] == 2
    assert report["source_acquisition_observation_rule_counts"] == {
        "uk_affecting_act_xml_cached_recorded": 1,
        "uk_affecting_act_xml_missing_rejected": 1,
    }


def test_uk_effects_blocking_rows_treat_record_disposition_as_nonblocking() -> None:
    rows = (
        {"rule_id": "legacy_block"},
        {"rule_id": "explicit_block", "blocking": True},
        {"rule_id": "explicit_observation", "blocking": False},
        {"rule_id": "record_observation", "strict_disposition": "record"},
    )

    assert uk_effects._blocking_rows(rows) == (
        {"rule_id": "legacy_block"},
        {"rule_id": "explicit_block", "blocking": True},
    )


def test_uk_effect_main_json_threads_feed_and_source_rejections(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effect

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effect = UKEffectRecord(
        effect_id="eff-unapplied",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url):
            if str(_url).endswith("/enacted/data.xml"):
                return b"<short/>"
            return None

    def fake_load_effects(statute_id, archive, *, parse_rejections_out=None):
        del statute_id, archive
        assert parse_rejections_out is not None
        parse_rejections_out.append(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "blocking": True,
            }
        )
        return [effect]

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        fake_load_effects,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: b"<short/>",
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )

    uk_effect.main(
        Namespace(
            statute_id="ukpga/2000/1",
            effect_id="eff-unapplied",
            show_text=False,
            show_payload=False,
            json=True,
            db=str(db_path),
            uk_applicability_mode="effective_date_only",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["applicability_mode"] == "effective_date_only"
    assert payload["effect"]["replay_applicable"] is True
    assert payload["effect"]["structural_for_replay"] is True
    assert payload["source_surface"] == {
        "archive_path": str(db_path),
        "enacted_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
        "oracle_url": "https://example.test/ukpga/2000/1/data.xml",
        "enacted_missing": True,
        "oracle_missing": True,
        "enacted_source_status": "too_small",
        "oracle_source_status": "absent",
        "enacted_source_size": len(b"<short/>"),
        "oracle_source_size": 0,
        "enacted_source_sha256": hashlib.sha256(b"<short/>").hexdigest(),
        "oracle_source_sha256": "",
        "enacted_source_parse_failed": False,
        "oracle_source_parse_failed": False,
    }
    assert payload["source"]["pathology"] == "missing_extracted_source"
    assert payload["effect_feed_parse_rejections"]["rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["source_acquisition_rejections"]["rule_counts"] == {
        "uk_affecting_act_xml_too_small_rejected": 1,
    }
    assert payload["lowering"]["blocking_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1,
    }

    uk_effect.main(
        Namespace(
            statute_id="ukpga/2000/1",
            effect_id="eff-unapplied",
            show_text=False,
            show_payload=False,
            json=False,
            db=str(db_path),
            uk_applicability_mode="effective_date_only",
        )
    )

    text = capsys.readouterr().out
    assert "Enacted source:     too_small (8 bytes)" in text
    assert "Oracle source:      absent (0 bytes)" in text
    assert f"Enacted SHA-256:    {hashlib.sha256(b'<short/>').hexdigest()}" in text
    assert "Oracle SHA-256:     (none)" in text
    assert "Source pathology:   missing_extracted_source" in text
    assert "Feed observations:  1" in text
    assert "Feed rejections:    1" in text
    assert "Source acquisition observations: 1" in text
    assert "Source acquisition rejections:   1" in text
    assert "Blocking lowering rejections: 1" in text
    assert "  uk_effect_lowering_no_ops_rejected: 1" in text

    monkeypatch.setattr(
        uk_effect,
        "affecting_act_xml_too_small_rejection",
        lambda _effect, *, source_size: {
            "rule_id": "uk_affecting_act_xml_cached_recorded",
            "phase": "acquisition",
            "source_size": source_size,
            "blocking": False,
            "strict_disposition": "record",
        },
    )
    uk_effect.main(
        Namespace(
            statute_id="ukpga/2000/1",
            effect_id="eff-unapplied",
            show_text=False,
            show_payload=False,
            json=False,
            db=str(db_path),
            uk_applicability_mode="effective_date_only",
        )
    )

    text = capsys.readouterr().out
    assert "Source acquisition observations: 1" in text
    assert "  uk_affecting_act_xml_cached_recorded: 1" in text
    assert "Source acquisition rejections:" not in text


def test_uk_effect_json_missing_effect_id_emits_typed_error_bundle(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effect = UKEffectRecord(
        effect_id="eff-present",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_load_effects(statute_id, archive, *, parse_rejections_out=None):
        del statute_id, archive
        assert parse_rejections_out is not None
        parse_rejections_out.extend(
            (
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "blocking": True,
                },
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "phase": "parse",
                    "blocking": False,
                },
            )
        )
        return [effect]

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        fake_load_effects,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_effect.main(
            Namespace(
                statute_id="ukpga/2000/1",
                effect_id="eff-missing",
                show_text=False,
                show_payload=False,
                json=True,
                db=str(db_path),
                uk_applicability_mode="effective_date_only",
            )
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert excinfo.value.code == 1
    assert payload["error"] == "EFFECT_ID_NOT_FOUND"
    assert payload["statute_id"] == "ukpga/2000/1"
    assert payload["effect_id"] == "eff-missing"
    assert payload["loaded_effect_count"] == 1
    assert payload["applicability_mode"] == "effective_date_only"
    assert payload["effect_feed_parse_rejections"]["rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert "effect_id 'eff-missing' not found" in captured.err


def test_uk_effect_json_records_available_source_parse_failures(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    malformed_xml = b"<Legislation>" + (b"x" * 128)
    effect = UKEffectRecord(
        effect_id="eff-present",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url):
            return malformed_xml

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.compile_effect_to_ir_ops",
        lambda _effect, _extracted, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )

    uk_effect.main(
        Namespace(
            statute_id="ukpga/2000/1",
            effect_id="eff-present",
            show_text=False,
            show_payload=False,
            json=True,
            db=str(db_path),
            uk_applicability_mode="effective_date_plus_feed_applied",
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_surface"]["enacted_source_status"] == "available"
    assert payload["source_surface"]["oracle_source_status"] == "available"
    assert payload["source_surface"]["enacted_missing"] is True
    assert payload["source_surface"]["oracle_missing"] is True
    assert payload["source_surface"]["enacted_source_sha256"] == hashlib.sha256(
        malformed_xml
    ).hexdigest()
    assert payload["source_surface"]["oracle_source_sha256"] == hashlib.sha256(
        malformed_xml
    ).hexdigest()
    assert payload["source_surface"]["enacted_source_parse_failed"] is True
    assert payload["source_surface"]["oracle_source_parse_failed"] is True
    assert payload["source_parse_rejections"]["rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert payload["source_parse_observation_rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
        "uk_oracle_xml_parse_rejected": 1,
    }
