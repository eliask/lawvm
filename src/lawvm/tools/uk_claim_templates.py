"""Non-executable UK semantic-claim templates for manual frontier review."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)

UK_CLAIM_TEMPLATE_RULE_IDS = UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS


def _quoted_for_substitute_pair(source_preview: str) -> tuple[str, str]:
    """Return the quoted preimage/replacement pair from a simple formula."""
    replacement_match = re.search(
        r"\bfor\b.{0,240}?[\"“](?P<old>[^\"”]{1,240})[\"”]\s+substitute\s+[\"“](?P<new>[^\"”]{1,240})[\"”]",
        " ".join(source_preview.split()),
        flags=re.I,
    )
    if replacement_match is None:
        return "", ""
    return (
        " ".join(replacement_match.group("old").split()),
        " ".join(replacement_match.group("new").split()),
    )


def _definition_entry_terms(payload: str) -> tuple[str, ...]:
    """Return quoted terms that appear to introduce definition entries."""
    terms: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"[\"“]\s*(?P<term>[^\"”]{1,160}?)\s*[\"”]\s+"
        r"(?:means|includes|has\s+the\s+(?:same\s+)?meaning\b|is\s+to\s+be\s+construed\b)",
        payload,
        flags=re.I,
    ):
        term = " ".join(match.group("term").split()).strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return tuple(terms)


def _range_to_container_replacement_sections(
    payload_roots: Any,
) -> tuple[dict[str, str], ...]:
    """Return bounded replacement section labels from range-to-container payload evidence."""
    sections: list[dict[str, str]] = []
    for root in payload_roots or ():
        if not isinstance(root, dict):
            continue
        root_sections = root.get("descendant_sections") or ()
        for section in root_sections:
            if not isinstance(section, dict):
                continue
            sections.append(
                {
                    "label": str(section.get("label") or ""),
                    "eid": str(section.get("eid") or ""),
                }
            )
    return tuple(sections)


def _first_lowering_rejection_detail(
    *,
    row: Any,
    rule_id: str,
) -> dict[str, Any]:
    """Return the first lowering rejection for a claim family."""
    for rejection in row.summary.lowering_rejections:
        if not isinstance(rejection, dict):
            continue
        if str(rejection.get("rule_id") or "") == rule_id:
            return dict(rejection)
    return {}


def _first_table_lowering_rejection_detail(*, row: Any) -> dict[str, Any]:
    """Return the first table-surface rejection with target-shape evidence."""
    for rule_id in (
        "uk_effect_table_entry_instruction_rejected",
        "uk_effect_table_entry_target_rejected",
        "uk_effect_table_entry_row_insert",
    ):
        detail = _first_lowering_rejection_detail(row=row, rule_id=rule_id)
        if detail:
            return detail
    return {}


def _definition_child_and_tail_parts(source_preview: str) -> dict[str, str]:
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bfor\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
        r"of\s+the\s+definition\s+of\s+[\"“](?P<term>[^\"”]{1,240})[\"”]\s+"
        r"and\s+the\s+[\"“]?(?P<tail_connector>or|and)[\"”]?\s+"
        r"at\s+the\s+end\s+of\s+that\s+paragraph\s+substitute\s*[—–-]\s*"
        r"(?P<replacement>.+?)\s*\.?\s*$",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {
            "definition_term": "",
            "definition_child_label": "",
            "tail_connector": "",
            "replacement_preview": source_norm[:500],
        }
    return {
        "definition_term": " ".join(match.group("term").split()),
        "definition_child_label": " ".join(match.group("label").split()),
        "tail_connector": " ".join(match.group("tail_connector").split()).lower(),
        "replacement_preview": " ".join(match.group("replacement").split())[:500],
    }


def _definition_child_structural_substitution_parts(source_preview: str) -> dict[str, str]:
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bin\s+the\s+definition\s+of\s+[\"“](?P<term>[^\"”]{1,240})[\"”]\s*,?\s+"
        r"for\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
        r"\(\s*including\s+the\s+[\"“]?(?P<tail_connector>or|and)[\"”]?\s+"
        r"at\s+the\s+end\s*\)\s+substitute\s*[—–-]\s*"
        r"(?P<replacement>.+?)\s*\.?\s*$",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {
            "definition_term": "",
            "definition_child_label": "",
            "tail_connector": "",
            "replacement_preview": source_norm[:500],
        }
    return {
        "definition_term": " ".join(match.group("term").split()),
        "definition_child_label": " ".join(match.group("label").split()),
        "tail_connector": " ".join(match.group("tail_connector").split()).lower(),
        "replacement_preview": " ".join(match.group("replacement").split())[:500],
    }


def _heading_facet_wrapper_insert_parts(source_preview: str) -> dict[str, str]:
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bbefore\s+paragraph\s+(?P<anchor_paragraph>[0-9A-Za-z]+)\s+"
        r"of\s+Schedule\s+(?P<schedule_label>[0-9A-Za-z]+)\s*"
        r"\(\s*and\s+the\s+italic\s+heading\s+before\s+it\s*\)\s+"
        r"insert\s*[—–-]\s*"
        r"(?P<part_label>Part\s+[0-9A-Za-zIVXLCivxlc]+)\s+"
        r"(?P<heading>.+?)\s*;?\s*$",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {}
    return {
        "schedule_label": " ".join(match.group("schedule_label").split()),
        "anchor_paragraph_label": " ".join(match.group("anchor_paragraph").split()),
        "inserted_part_label": " ".join(match.group("part_label").split()),
        "inserted_heading_text": " ".join(match.group("heading").split()).strip(" ;"),
        "carried_existing_heading": "italic heading before anchor paragraph",
    }


def _table_crossheading_rewrite_parts(source_preview: str) -> dict[str, str]:
    """Return table-crossheading rewrite evidence without stealing entry patches."""
    source_norm = " ".join(source_preview.split())
    becomes_match = re.search(
        r"\bcross[- ]heading\s+preceding\s+(?P<anchor>entry\s+[0-9A-Za-z]+)"
        r"\s+of\s+which\s+becomes\s+[\"“](?P<replacement>[^\"”]{1,300})[\"”]",
        source_norm,
        flags=re.I,
    )
    if becomes_match is not None:
        return {
            "text_match": "",
            "replacement": " ".join(becomes_match.group("replacement").split()),
            "source_formula": "becomes",
            "table_crossheading_anchor": " ".join(becomes_match.group("anchor").split()),
        }

    text_match, replacement = _quoted_for_substitute_pair(source_norm)
    return {
        "text_match": text_match,
        "replacement": replacement,
        "source_formula": "substitute" if replacement else "",
        "table_crossheading_anchor": "",
    }


def _referent_qualified_substitution_parts(source_preview: str) -> dict[str, Any]:
    """Return source-local evidence for referent-qualified substitutions."""
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bfor\s+(?P<preimages>.+?)\s*,?\s+where\s+"
        r"(?P<pronoun>it|they|he|him|his|those\s+words?)\s+refers?\s+to\s+"
        r"(?P<referent>.+?)\s*,?\s+substitute\s+[\"“](?P<replacement>[^\"”]{1,240})[\"”]",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {
            "text_preimages": [],
            "referent_entity": "",
            "replacement": "",
            "referent_pronoun": "",
        }
    return {
        "text_preimages": [
            " ".join(item.split())
            for item in re.findall(r"[\"“]([^\"”]{1,120})[\"”]", match.group("preimages"))
        ],
        "referent_entity": " ".join(match.group("referent").split()),
        "replacement": " ".join(match.group("replacement").split()),
        "referent_pronoun": " ".join(match.group("pronoun").split()).lower(),
    }


def _whole_act_word_patch_parts(source_preview: str) -> dict[str, Any]:
    """Return source-local evidence for whole-Act word-level patch candidates."""
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bfor\s+(?P<preimages>.+?)\s+in\s+each\s+place\s+substitute\s+"
        r"[\"“](?P<replacement>[^\"”]{1,240})[\"”]",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {
            "text_preimages": [],
            "replacement": "",
            "required_exclusions": [
                "short_title_or_title_surfaces",
                "words_amended_by_same_schedule_exceptions",
                "words_inserted_by_same_act_unless_otherwise_provided",
            ],
        }
    preimages = [
        " ".join(item.split())
        for item in re.findall(r"[\"“]([^\"”]{1,160})[\"”]", match.group("preimages"))
    ]
    return {
        "text_preimages": sorted(preimages, key=len, reverse=True),
        "replacement": " ".join(match.group("replacement").split()),
        "required_exclusions": [
            "short_title_or_title_surfaces",
            "words_amended_by_same_schedule_exceptions",
            "words_inserted_by_same_act_unless_otherwise_provided",
        ],
    }


def _surface_text_rewrite_claim_template(
    *,
    statute_id: str,
    row: Any,
    action_family: str,
    facet_family: str,
    placement_family: str,
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    text_match, replacement = _quoted_for_substitute_pair(source_preview)
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "facet_family": facet_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "text_match": text_match,
        "replacement": replacement,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def _table_crossheading_claim_template(
    *,
    statute_id: str,
    row: Any,
) -> dict[str, Any]:
    template = _surface_text_rewrite_claim_template(
        statute_id=statute_id,
        row=row,
        action_family="table_crossheading_text_rewrite",
        facet_family="table_crossheading",
        placement_family="explicit_table_heading_cell_or_prefix_required",
        required_validator_checks=[
            "source_witness_targets_table_crossheading_surface",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_heading_cell_or_text_prefix_boundary",
            "claim_preserves_table_rows_columns_and_entry_text",
            "claim_text_preimage_or_becomes_payload_matches_table_heading_surface",
            "changed_paths_are_within_declared_table_heading_surface",
        ],
    )
    source_preview = " ".join((row.summary.source_extracted_text_preview or "").split())
    parts = _table_crossheading_rewrite_parts(source_preview)
    template.update(parts)
    return template


def _bounded_mutation_claim_template(
    *,
    statute_id: str,
    row: Any,
    action_family: str,
    placement_family: str,
    required_ownership: list[str],
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "required_ownership": required_ownership,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def manual_compile_suggested_claim_template(
    *,
    statute_id: str,
    row: Any,
) -> dict[str, Any]:
    """Return a non-executable semantic-claim template for known manual families."""
    summary = row.summary
    effect = row.effect
    if summary.manual_compile_rule_id == "uk_manual_frontier_heading_facet_candidate":
        source_preview = " ".join((summary.source_extracted_text_preview or "").split())
        wrapper_parts = _heading_facet_wrapper_insert_parts(source_preview)
        if wrapper_parts:
            template = _bounded_mutation_claim_template(
                statute_id=statute_id,
                row=row,
                action_family="schedule_part_wrapper_insertion",
                placement_family="before_anchor_paragraph_and_carried_heading",
                required_ownership=[
                    "source_named_schedule_part_heading",
                    "anchor_paragraph_identity",
                    "carried_existing_italic_heading_boundary",
                    "partition_scope_or_non_scope_claim",
                    "lineage_or_wrapper_migration_events_if_existing_children_move",
                    "mutation_boundary",
                ],
                required_validator_checks=[
                    "source_witness_names_inserted_part_heading",
                    "claim_identifies_exact_schedule_anchor_paragraph",
                    "claim_identifies_existing_heading_before_anchor",
                    "claim_states_whether_following_children_move_under_new_part",
                    "claim_preserves_unclaimed_schedule_children",
                    "changed_paths_are_within_declared_wrapper_heading_or_migration_paths",
                ],
            )
            template.update(wrapper_parts)
            return template
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="facet_text_rewrite",
            facet_family="heading_or_title",
            placement_family="explicit_facet_target_required",
            required_validator_checks=[
                "source_witness_targets_heading_title_or_sidenote_facet",
                "claim_identifies_exact_target_facet_not_host_body",
                "claim_preserves_host_body_text_and_children",
                "claim_text_preimage_matches_target_facet_surface",
                "changed_paths_are_within_declared_facet_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_crossheading_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="crossheading_text_rewrite",
            facet_family="crossheading",
            placement_family="explicit_crossheading_carrier_required",
            required_validator_checks=[
                "source_witness_targets_crossheading_surface",
                "claim_identifies_exact_crossheading_carrier",
                "claim_preserves_neighbouring_sections_and_body_text",
                "claim_text_preimage_matches_crossheading_surface",
                "changed_paths_are_within_declared_crossheading_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_table_crossheading_candidate":
        return _table_crossheading_claim_template(statute_id=statute_id, row=row)
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_definition_child_and_tail_substitution_candidate"
    ):
        source_preview = " ".join((summary.source_extracted_text_preview or "").split())
        parts = _definition_child_and_tail_parts(source_preview)
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "definition_child_and_tail_substitution",
            "placement_family": "definition_child_plus_post_child_tail_boundary_required",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "candidate_target_surface": effect.affected_provisions,
            "candidate_source_preview": source_preview[:500],
            "definition_term": parts["definition_term"],
            "definition_child_label": parts["definition_child_label"],
            "tail_connector": parts["tail_connector"],
            "replacement_preview": parts["replacement_preview"],
            "required_ownership": [
                "definition_child_text_boundary",
                "post_child_tail_connector_boundary",
                "replacement_payload",
                "mutation_boundary",
            ],
            "required_validator_checks": [
                "source_witness_names_definition_term_and_child_label",
                "claim_identifies_exact_definition_child_node",
                "claim_identifies_post_child_tail_connector_surface",
                "claim_preserves_unclaimed_definition_children",
                "claim_splits_or_lowers_into_bounded_child_and_tail_mutations",
                "changed_paths_are_within_declared_definition_child_and_tail_boundary",
            ],
            "executable": False,
        }
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_definition_child_structural_substitution_candidate"
    ):
        source_preview = " ".join((summary.source_extracted_text_preview or "").split())
        parts = _definition_child_structural_substitution_parts(source_preview)
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "definition_child_structural_substitution",
            "placement_family": "definition_child_structural_payload_boundary_required",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "candidate_target_surface": effect.affected_provisions,
            "candidate_source_preview": source_preview[:500],
            "definition_term": parts["definition_term"],
            "definition_child_label": parts["definition_child_label"],
            "tail_connector": parts["tail_connector"],
            "replacement_preview": parts["replacement_preview"],
            "required_ownership": [
                "definition_term_scope",
                "definition_child_identity",
                "replacement_child_payload_shape",
                "post_child_tail_connector_boundary",
                "mutation_boundary",
            ],
            "required_validator_checks": [
                "source_witness_names_definition_term_and_child_label",
                "claim_identifies_exact_definition_child_node",
                "claim_identifies_replacement_payload_child_units",
                "claim_identifies_post_child_tail_connector_surface_when_present",
                "claim_preserves_unclaimed_definition_children",
                "changed_paths_are_within_declared_definition_child_boundary",
            ],
            "executable": False,
        }
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_note_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_note_text_rewrite",
            facet_family="schedule_note",
            placement_family="explicit_schedule_note_carrier_required",
            required_validator_checks=[
                "source_witness_targets_schedule_note_surface",
                "claim_identifies_exact_schedule_note_carrier",
                "claim_preserves_schedule_paragraph_body_structure",
                "claim_text_preimage_matches_schedule_note_surface",
                "changed_paths_are_within_declared_schedule_note_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_list_entry_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_list_entry_mutation",
            placement_family="entry_anchor_requires_carrier_claim",
            required_ownership=[
                "source_named_entry_anchor",
                "entry_carrier",
                "sibling_insertion_or_replacement_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_schedule_or_list_entry_anchor",
                "claim_identifies_exact_entry_carrier",
                "claim_identifies_predecessor_or_replaced_entry",
                "claim_preserves_unclaimed_sibling_entries",
                "changed_paths_are_within_claimed_entry_boundary",
            ],
        )
    if summary.manual_compile_rule_id in {
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_column_insert_candidate",
        "uk_manual_frontier_table_appropriate_place_candidate",
    }:
        placement_family_by_rule = {
            "uk_manual_frontier_table_entry_candidate": "table_entry_anchor_required",
            "uk_manual_frontier_table_entry_deictic_candidate": "deictic_table_entry_anchor_required",
            "uk_manual_frontier_table_column_insert_candidate": "table_column_boundary_required",
            "uk_manual_frontier_table_appropriate_place_candidate": "appropriate_place_table_entry_requires_ordering_claim",
        }
        detail = _first_table_lowering_rejection_detail(row=row)
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_surface_mutation",
            placement_family=placement_family_by_rule[summary.manual_compile_rule_id],
            required_ownership=[
                "source_named_table_surface",
                "row_or_column_carrier",
                "cell_alignment_or_column_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_table_entry_or_column_surface",
                "claim_identifies_exact_table_carrier",
                "claim_identifies_row_or_column_boundary",
                "claim_preserves_unclaimed_rows_columns_and_cells",
                "changed_paths_are_within_claimed_table_surface",
            ],
        )
        template.update(
            {
                "source_target_surface": detail.get(
                    "target_ref",
                    effect.affected_provisions,
                ),
                "source_target_address": detail.get("target", ""),
                "table_entry_shape": detail.get("entry_shape", ""),
            }
        )
        if (
            summary.manual_compile_rule_id
            == "uk_manual_frontier_table_appropriate_place_candidate"
        ):
            template["required_ownership"].append(
                "table_ordering_rule_or_anchor_claim"
            )
            template["required_validator_checks"].append(
                "claim_identifies_table_ordering_rule_or_anchor"
            )
        return template
    if summary.manual_compile_rule_id == "uk_manual_frontier_appropriate_place_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="appropriate_place_mutation",
            placement_family="appropriate_place_requires_anchor_claim",
            required_ownership=[
                "source_named_insertion_payload",
                "validated_predecessor_or_successor_anchor",
                "target_container_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_uses_appropriate_place_formula",
                "claim_supplies_exact_anchor_or_ordering_rule",
                "claim_identifies_target_container_surface",
                "claim_identifies_payload_units_owned_by_source",
                "claim_preserves_unclaimed_sibling_units",
                "changed_paths_are_within_claimed_insertion_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_appropriate_place_index_entry_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="index_entry_insert",
            placement_family="appropriate_place_requires_anchor_claim",
            required_ownership=[
                "source_named_index_entry_payload",
                "validated_predecessor_or_successor_anchor",
                "target_index_or_list_container_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_uses_appropriate_place_formula",
                "payload_is_complete_index_entry",
                "claim_supplies_exact_index_entry_anchor_or_ordering_rule",
                "claim_identifies_target_index_or_list_surface",
                "claim_preserves_unclaimed_index_entries",
                "changed_paths_are_within_claimed_insertion_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_structural_sibling_insert_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="structural_sibling_insert",
            placement_family="source_named_sibling_anchor_required",
            required_ownership=[
                "source_named_sibling_anchor",
                "inserted_sibling_payload",
                "sibling_order_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_before_or_after_sibling_anchor",
                "claim_identifies_exact_parent_and_anchor_sibling",
                "claim_identifies_each_inserted_sibling_payload",
                "claim_preserves_anchor_and_unclaimed_siblings",
                "changed_paths_are_within_declared_sibling_insertion_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_structural_child_range_substitution_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="structural_child_range_substitution",
            placement_family="source_named_child_range_required",
            required_ownership=[
                "source_named_child_range",
                "replacement_payload_shape",
                "removed_child_identities",
                "parent_text_or_tail_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_exact_child_range",
                "claim_identifies_each_removed_child_unit",
                "claim_identifies_replacement_payload_as_text_or_child_units",
                "claim_preserves_unclaimed_siblings_and_parent_text",
                "changed_paths_are_within_claimed_child_range_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_amendment_program_target_candidate":
        detail = _first_lowering_rejection_detail(
            row=row,
            rule_id="uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
        )
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="amendment_program_target_mutation",
            placement_family="inserted_parent_instruction_context_required",
            required_ownership=[
                "source_amendment_program_context",
                "inserted_parent_instruction",
                "derived_child_target_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_text_inserted_by_same_amending_program",
                "claim_identifies_the_parent_instruction_that_created_the_target",
                "claim_identifies_exact_inserted_parent_or_child_boundary",
                "claim_preserves_unclaimed_inserted_payload_and_live_target_text",
                "changed_paths_are_within_declared_amendment_program_target",
            ],
        )
        template.update(
            {
                "source_target_surface": detail.get(
                    "target_ref",
                    effect.affected_provisions,
                ),
                "source_target_address": detail.get("target", ""),
                "source_subparagraph_label": detail.get("source_subparagraph_label", ""),
                "source_item_label": detail.get("source_item_label", ""),
                "inserted_parent_label": detail.get("inserted_parent_label", ""),
                "insert_direction": detail.get("direction", ""),
                "anchor_label": detail.get("anchor_label", ""),
                "inserted_label": detail.get("inserted_label", ""),
                "inserted_text_preview": detail.get("inserted_text_preview", ""),
            }
        )
        return template
    if summary.manual_compile_rule_id == "uk_manual_frontier_cross_container_renumber_candidate":
        detail = _first_lowering_rejection_detail(
            row=row,
            rule_id="uk_effect_metadata_cross_container_renumber_rejected",
        )
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="cross_container_renumber_migration",
            placement_family="explicit_effect_metadata_destination_required",
            required_ownership=[
                "source_provision_identity",
                "destination_provision_identity",
                "descendant_wrapping_or_relabel_semantics",
                "lineage_or_migration_events",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "effect_metadata_names_source_and_destination_containers",
                "claim_identifies_exact_source_provision_before_migration",
                "claim_identifies_exact_destination_parent_and_label",
                "claim_preserves_unclaimed_source_and_destination_siblings",
                "claim_emits_lineage_or_migration_events_for_moved_identity",
                "changed_paths_are_within_declared_source_destination_or_migration_paths",
            ],
        )
        template.update(
            {
                "source_target_address": detail.get("source_target", ""),
                "destination_address": detail.get("destination", ""),
                "effect_type_normalized": detail.get("effect_type_normalized", ""),
                "reason_code": detail.get("reason_code", ""),
            }
        )
        return template
    if summary.manual_compile_rule_id == "uk_manual_frontier_repeal_table_candidate":
        detail = {}
        for rule_id in (
            "uk_effect_repeal_table_structural_repeal_unresolved",
            "uk_effect_repeal_table_quoted_words_text_repeal_unresolved",
        ):
            detail = _first_lowering_rejection_detail(row=row, rule_id=rule_id)
            if detail:
                break
        required_ownership = [
            "source_named_table_or_row_surface",
            "repealed_row_column_or_cell_boundary",
            "unclaimed_table_surface_preservation",
            "mutation_boundary",
        ]
        required_validator_checks = [
            "source_witness_targets_table_repeal_or_omission",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_every_repealed_row_column_or_cell",
            "claim_preserves_unclaimed_table_rows_columns_and_cells",
            "changed_paths_are_within_declared_table_repeal_boundary",
        ]
        if detail.get("reason_code") == "mixed_structural_and_word_repeal_requires_split":
            required_ownership.append("structural_and_text_repeal_split_boundary")
            required_validator_checks.append(
                "claim_splits_structural_repeal_from_word_omission_clauses"
            )
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_repeal_or_omission",
            placement_family="source_named_table_or_row_boundary_required",
            required_ownership=required_ownership,
            required_validator_checks=required_validator_checks,
        )
        template.update(
            {
                "lowering_rule_id": detail.get("rule_id", ""),
                "lowering_reason_code": detail.get("reason_code", ""),
                "source_target_surface": detail.get(
                    "target_ref",
                    effect.affected_provisions,
                ),
            }
        )
        return template
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_referent_qualified_text_substitution_candidate"
    ):
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="referent_qualified_text_substitution",
            placement_family="referent_sensitive_occurrence_claim_required",
            required_ownership=[
                "source_qualified_referent_entity",
                "quoted_preimage_terms",
                "replacement_text",
                "per_occurrence_coreference_decision",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_qualifies_substitution_by_referent",
                "claim_identifies_each_mutated_occurrence_and_target_surface",
                "claim_proves_each_mutated_occurrence_refers_to_the_named_entity",
                "claim_preserves_same_word_occurrences_referring_to_other_entities",
                "changed_paths_are_within_declared_referent_occurrence_boundaries",
            ],
        )
        template.update(
            _referent_qualified_substitution_parts(
                row.summary.source_extracted_text_preview or ""
            )
        )
        return template
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_whole_act_word_level_text_patch_candidate"
    ):
        template = _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="whole_act_listed_enactments_text_patch",
            placement_family="listed_enactment_whole_act_scope_with_exclusions",
            required_ownership=[
                "source_list_membership_for_affected_act",
                "quoted_preimage_terms",
                "replacement_text",
                "whole_act_text_carrier_set",
                "same_schedule_and_same_act_exclusions",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_lists_the_affected_act_or_short_citation",
                "claim_uses_longest_preimage_first_for_overlapping_phrases",
                "claim_excludes_title_and_short_title_surfaces",
                "claim_excludes_words_amended_by_named_same_schedule_paragraphs",
                "claim_excludes_words_inserted_by_same_act_unless_otherwise_provided",
                "changed_paths_are_within_declared_whole_act_text_carriers",
            ],
        )
        template.update(
            _whole_act_word_patch_parts(row.summary.source_extracted_text_preview or "")
        )
        return template
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_multi_subunit_text_rewrite",
            placement_family="source_named_child_units_required",
            required_ownership=[
                "source_named_child_unit_set",
                "per_child_text_preimage",
                "per_child_replacement_or_repeal_payload",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_each_child_unit_to_mutate",
                "claim_splits_the_parent_formula_into_bounded_child_operations",
                "claim_text_preimage_matches_each_declared_child_surface",
                "claim_preserves_unclaimed_child_units_and_parent_text",
                "changed_paths_are_within_declared_child_unit_boundaries",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_child_tail_text_rewrite",
            placement_family="source_named_child_tail_required",
            required_ownership=[
                "source_named_child_anchor",
                "tail_text_preimage_or_repeal_scope",
                "replacement_or_repeal_payload",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_the_child_anchor_and_tail_scope",
                "claim_targets_only_the_tail_text_following_that_child",
                "claim_text_preimage_matches_the_declared_tail_surface",
                "claim_preserves_child_body_and_unclaimed_parent_text",
                "changed_paths_are_within_declared_child_tail_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_structured_text_patch_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_structured_text_patch",
            placement_family="parent_formula_anchor_with_structured_payload_required",
            required_ownership=[
                "source_parent_formula_anchor",
                "source_carried_payload_units",
                "child_target_boundaries",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_contains_parent_formula_and_structured_payload",
                "claim_binds_payload_units_to_named_child_targets",
                "claim_preserves_unclaimed_parent_and_sibling_text",
                "claim_rejects_flattening_structured_payload_into_host_text",
                "changed_paths_are_within_claimed_child_target_boundaries",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_structured_tail_substitution_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_structured_tail_substitution",
            placement_family="tail_range_with_structured_payload_required",
            required_ownership=[
                "source_tail_range_preimage",
                "source_carried_structured_payload_units",
                "child_target_boundaries",
                "flattened_patch_replacement_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_contains_tail_range_and_structured_replacement",
                "claim_identifies_exact_tail_preimage_boundary",
                "claim_materializes_replacement_payload_as_child_units_not_flat_text",
                "claim_preserves_unclaimed_existing_child_units_and_parent_text",
                "changed_paths_are_within_claimed_tail_and_child_payload_boundaries",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_range_to_container_candidate":
        blocking_rows = tuple(
            row
            for row in summary.lowering_rejections
            if str(row.get("rule_id") or "") == "uk_effect_range_to_container_substitution_rejected"
        )
        detail = dict(blocking_rows[0]) if blocking_rows else {}
        payload_roots = tuple(detail.get("payload_roots") or ())
        replacement_sections = _range_to_container_replacement_sections(payload_roots)
        source_range_sections = tuple(detail.get("source_range_sections") or ())
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "range_to_container_substitution",
            "placement_family": "requires_lineage_or_migration_claim",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "source_range_kind": detail.get("source_range_kind", ""),
            "source_range_start": detail.get("source_range_start", ""),
            "source_range_end": detail.get("source_range_end", ""),
            "source_range_section_count": detail.get("source_range_section_count", 0),
            "source_range_sections": list(source_range_sections),
            "truncated_source_range_sections": bool(
                detail.get("truncated_source_range_sections", False)
            ),
            "target_container_surface": detail.get(
                "target_container_ref",
                effect.affected_provisions,
            ),
            "compiled_targets": list(detail.get("compiled_targets") or ()),
            "payload_kinds": list(detail.get("payload_kinds") or ()),
            "payload_roots": list(payload_roots),
            "replacement_section_count": len(replacement_sections),
            "replacement_sections": list(replacement_sections),
            "required_ownership": list(detail.get("required_ownership") or ()),
            "required_validator_checks": [
                "source_witness_contains_range_to_container_substitution",
                "claim_identifies_every_replaced_source_unit_in_range",
                "claim_identifies_container_payload_root_and_all_owned_children",
                "claim_emits_lineage_or_migration_events_for_displaced_units",
                "claim_preserves_crossheading_or_heading_facet_scope",
                "changed_paths_are_within_source_range_or_declared_migration_paths",
            ],
            "executable": False,
        }
    if summary.manual_compile_rule_id == "uk_manual_frontier_definition_list_end_insert_candidate":
        source_preview = summary.source_extracted_text_preview or ""
        source_norm = " ".join(source_preview.split())
        match = re.search(
            r"\bat\s+the\s+end\s+insert\s*[—–-]\s*(?P<payload>.+)$",
            source_norm,
            flags=re.I | re.S,
        )
        payload = (
            " ".join(match.group("payload").split()).strip()
            if match is not None
            else source_norm
        )
        terms = _definition_entry_terms(payload)
        term = terms[0] if terms else ""
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "definition_entry_insert",
            "placement_family": "definition_list_end_requires_boundary_claim",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "source_preview_sha256": (
                hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
                if source_preview
                else ""
            ),
            "inserted_definition_term": term,
            "inserted_definition_terms": list(terms),
            "inserted_definition_entry_preview": payload[:500],
            "candidate_target_surface": effect.affected_provisions,
            "required_validator_checks": [
                "source_witness_contains_exact_definition_list_end_instruction",
                "payload_is_complete_definition_entry",
                "claim_identifies_exact_definition_list_target",
                "target_subtree_contains_definition_list_surface",
                "inserted_term_is_not_already_present_in_target_at_effective_preimage",
                "changed_paths_remain_inside_claimed_interpretation_target",
            ],
            "executable": False,
        }
    if (
        summary.manual_compile_rule_id
        != "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    ):
        if (
            summary.manual_compile_rule_id
            != "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
        ):
            return {}
    source_preview = summary.source_extracted_text_preview or ""
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bat\s+(?:an?|the)\s+appropriate\s+place,?\s+"
        r"(?:in\s+alphabetical\s+order,?\s+)?insert\s*[—–-]\s*(?P<payload>.+)$",
        source_norm,
        flags=re.I | re.S,
    )
    if (
        match is None
        and summary.manual_compile_rule_id
        == "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
    ):
        match = re.search(
            r"\b(?:after|before)\s+the\s+definition\s+of\s+[\"“][^\"”]{1,200}[\"”]\s+"
            r"(?:there\s+is\s+)?inserted\s*[—–-]\s*(?P<payload>.+)$",
            source_norm,
            flags=re.I | re.S,
        )
    payload = (
        " ".join(match.group("payload").split()).strip()
        if match is not None
        else source_norm
    )
    terms = _definition_entry_terms(payload)
    term = terms[0] if terms else ""
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": "definition_entry_insert",
        "placement_family": (
            "pseudo_definition_target_requires_anchor_claim"
            if summary.manual_compile_rule_id
            == "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
            else "appropriate_place_requires_anchor_claim"
        ),
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "source_preview_sha256": (
            hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
            if source_preview
            else ""
        ),
        "inserted_definition_term": term,
        "inserted_definition_terms": list(terms),
        "inserted_definition_entry_preview": payload[:500],
        "candidate_target_surface": effect.affected_provisions,
        "required_validator_checks": [
            (
                "effect_metadata_names_pseudo_definition_target"
                if summary.manual_compile_rule_id
                == "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate"
                else "source_witness_contains_exact_appropriate_place_instruction"
            ),
            "payload_is_complete_definition_entry",
            "claim_supplies_exact_definition_entry_anchor_or_insertion_index",
            "target_subtree_contains_definition_list_surface",
            "inserted_term_is_not_already_present_in_target_at_effective_preimage",
            "changed_paths_remain_inside_claimed_interpretation_target",
        ],
        "executable": False,
    }
