"""UK replay text-miss classification helpers."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec
from lawvm.core.semantic_types import FacetKind, TextPatchKindEnum
from lawvm.uk_legislation.addressing import _addr_leaf_kind
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_target_gaps import uk_broad_schedule_table_shape_gap
from lawvm.uk_legislation.replay_text import (
    _article_phrase_content_word_present,
    _citation_connector_elided_text_match_present,
    _citation_stripped_text_match_present,
    _definition_entry_term_absent,
    _monetary_amount_text_selector,
    _multi_fragment_text_selector,
    _node_text_contains_text,
    _non_substantive_text_selector,
    _normalized_replay_subtree_text,
    _normalized_replacement_text_present,
    _normalized_text_match_present,
    _parenthetical_omission_text_selector,
    _subtree_contains_text,
    _synthetic_text_selector,
    _text_patch_replacement_preserves_anchor,
)
from lawvm.uk_legislation.text_rewrite_fragments import _text_rewrite_rule_ids_for_op


UK_REPLAY_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID = (
    "uk_effect_respectively_all_occurrences_substitution_text_patch"
)

UK_REPLAY_BLOCKING_TEXT_MISS_KINDS = frozenset(
    {
        "uk_replay_broad_schedule_table_shape_gap",
        "uk_replay_broad_schedule_part_table_shape_gap",
        "uk_replay_table_shape_gap",
        "uk_replay_definition_entry_shape_gap",
        "uk_replay_heading_text_preimage_gap",
        "uk_replay_text_target_empty_surface_gap",
        "uk_replay_text_match_missing",
        "uk_replay_text_insert_anchor_preimage_gap",
        "uk_replay_text_monetary_amount_preimage_gap",
        "uk_replay_text_parenthetical_omission_preimage_gap",
        "uk_replay_text_match_article_phrase_surface_gap",
        "uk_replay_text_patch_preimage_drift",
        "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
        "uk_replay_text_match_synthetic_selector_gap",
        "uk_replay_text_match_normalized_preimage_present_gap",
        "uk_replay_text_match_non_substantive_selector_gap",
        "uk_replay_text_match_multi_fragment_selector_gap",
        "uk_replay_text_match_citation_tail_surface_gap",
        "uk_replay_text_match_citation_connector_surface_gap",
    }
)

_UK_REPLAY_TEXT_MISS_SOURCE_SHAPE_BY_KIND = {
    "uk_replay_text_target_empty_surface_gap": "target_subtree_without_text_surface",
    "uk_replay_heading_text_preimage_gap": "heading_preimage_absent",
    "uk_replay_heading_respectively_all_occurrences_absent_observed": (
        "respectively_all_occurrences_heading_preimage_absent"
    ),
    "uk_replay_definition_entry_already_absent_observed": "definition_entry_already_absent",
    "uk_replay_text_insert_anchor_preimage_gap": "insert_anchor_preimage_absent",
    "uk_replay_text_monetary_amount_preimage_gap": "monetary_amount_preimage_absent",
    "uk_replay_text_parenthetical_omission_preimage_gap": "parenthetical_omission_preimage_absent",
    "uk_replay_text_match_article_phrase_surface_gap": "article_phrase_content_word_surface_gap",
    "uk_replay_text_match_normalized_preimage_present_gap": "normalized_preimage_present",
    "uk_replay_text_match_replacement_normalized_present": "replacement_normalized_present",
    "uk_replay_text_match_multi_fragment_selector_gap": "multi_fragment_text_selector",
    "uk_replay_text_match_citation_tail_surface_gap": "citation_tail_surface_gap",
    "uk_replay_text_match_citation_connector_surface_gap": "citation_connector_surface_gap",
}


@dataclass(frozen=True)
class UKReplayTextMissClassification:
    kind: str
    message: str
    source_shape: str
    blocking: bool


def uk_replay_text_miss_source_shape(kind: str) -> str:
    if kind in {
        "uk_replay_broad_schedule_table_shape_gap",
        "uk_replay_broad_schedule_part_table_shape_gap",
    }:
        return "broad_schedule_without_table_or_provision_structure"
    return _UK_REPLAY_TEXT_MISS_SOURCE_SHAPE_BY_KIND.get(kind, "")


def classify_uk_replay_text_miss(
    *,
    op: LegalOperation,
    target: LegalAddress,
    node: UKMutableNode,
    heading_carrier: UKMutableNode | None,
    text_patch: TextPatchSpec,
    replacement: str,
    prior_same_target_text_patch_count: int,
) -> UKReplayTextMissClassification:
    kind: str
    message: str
    if (
        text_patch.kind is TextPatchKindEnum.REPLACE
        and bool(replacement)
        and (
            _node_text_contains_text(node, replacement)
            if heading_carrier is not None
            else _subtree_contains_text(node, replacement)
        )
    ):
        kind = "uk_replay_text_match_already_rewritten"
        message = (
            "UK replay skipped text-based op: text_match missing but "
            "replacement text is already present in target subtree."
        )
    elif (
        text_patch.kind is TextPatchKindEnum.REPLACE
        and bool(replacement)
        and _normalized_replacement_text_present(
            replacement,
            heading_carrier if heading_carrier is not None else node,
        )
    ):
        kind = "uk_replay_text_match_replacement_normalized_present"
        message = (
            "UK replay skipped text-based op: text_match missing but "
            "the normalized replacement text is already present in target subtree."
        )
    elif (
        text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_")
        and text_patch.kind is TextPatchKindEnum.DELETE
        and _definition_entry_term_absent(text_patch.selector.match_text, node)
    ):
        kind = "uk_replay_definition_entry_already_absent_observed"
        message = (
            "UK replay observed a definition-entry repeal whose named "
            "definition term is already absent from the target subtree."
        )
    elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_"):
        kind = "uk_replay_definition_entry_shape_gap"
        message = (
            "UK replay skipped definition-entry text op: definition entry "
            "could not be uniquely bounded in the target subtree."
        )
    elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_CHILD_"):
        kind = "uk_replay_definition_child_shape_gap"
        message = (
            "UK replay skipped definition-child text op: definition child "
            "could not be uniquely bounded in the target subtree."
        )
    elif (
        target.special is FacetKind.HEADING
        and heading_carrier is not None
        and UK_REPLAY_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID in _text_rewrite_rule_ids_for_op(op)
    ):
        kind = "uk_replay_heading_respectively_all_occurrences_absent_observed"
        message = (
            "UK replay observed a respectively paired heading-facet rewrite "
            "whose quoted preimage is absent from this heading carrier; the "
            "source instruction applies wherever that expression occurs."
        )
    elif target.special is FacetKind.HEADING and heading_carrier is not None:
        kind = "uk_replay_heading_text_preimage_gap"
        message = (
            "UK replay skipped heading-facet text op: heading carrier exists "
            "but lacks the source text preimage."
        )
    elif prior_same_target_text_patch_count:
        if prior_same_target_text_patch_count > 1:
            kind = "uk_replay_text_patch_preimage_drift_multi_prior_same_target"
        else:
            kind = "uk_replay_text_patch_preimage_drift"
        message = (
            "UK replay skipped text-based op: text_match missing after "
            "an earlier same-target text patch changed the replay preimage."
        )
    elif uk_broad_schedule_table_shape_gap(target, node):
        if str(_addr_leaf_kind(target) or "").lower() == "part":
            kind = "uk_replay_broad_schedule_part_table_shape_gap"
        else:
            kind = "uk_replay_broad_schedule_table_shape_gap"
        message = (
            "UK replay skipped text-based op: broad schedule target has no "
            "table or provision structure carrying the text patch preimage."
        )
    elif not _normalized_replay_subtree_text(node):
        kind = "uk_replay_text_target_empty_surface_gap"
        message = (
            "UK replay skipped text-based op: target subtree has no "
            "replay-visible text carrying the text patch preimage."
        )
    elif _synthetic_text_selector(text_patch.selector.match_text):
        kind = "uk_replay_text_match_synthetic_selector_gap"
        message = (
            "UK replay skipped text-based op: synthetic text selector "
            "could not be resolved in the target subtree."
        )
    elif _non_substantive_text_selector(text_patch.selector.match_text):
        kind = "uk_replay_text_match_non_substantive_selector_gap"
        message = (
            "UK replay skipped text-based op: non-substantive selector "
            "could not be resolved in the target subtree."
        )
    elif _multi_fragment_text_selector(text_patch.selector.match_text):
        kind = "uk_replay_text_match_multi_fragment_selector_gap"
        message = (
            "UK replay skipped text-based op: text_match appears to "
            "combine multiple separated source fragments into one selector."
        )
    elif _normalized_text_match_present(text_patch.selector.match_text, node):
        kind = "uk_replay_text_match_normalized_preimage_present_gap"
        message = (
            "UK replay skipped text-based op: exact text_match was missing "
            "but an alphanumeric-normalized preimage is present in the target subtree."
        )
    elif _citation_stripped_text_match_present(text_patch.selector.match_text, node):
        kind = "uk_replay_text_match_citation_tail_surface_gap"
        message = (
            "UK replay skipped text-based op: exact text_match was missing "
            "but the target subtree appears to omit citation year/chapter tail text."
        )
    elif _citation_connector_elided_text_match_present(text_patch.selector.match_text, node):
        kind = "uk_replay_text_match_citation_connector_surface_gap"
        message = (
            "UK replay skipped citation-list text op: exact text_match was missing "
            "but the target subtree appears to elide connector words between citations."
        )
    elif _article_phrase_content_word_present(text_patch.selector.match_text, node):
        kind = "uk_replay_text_match_article_phrase_surface_gap"
        message = (
            "UK replay skipped article-prefixed text op: exact text_match was missing "
            "but the target subtree contains the selector's content word in a different phrase shape."
        )
    elif _monetary_amount_text_selector(text_patch.selector.match_text):
        kind = "uk_replay_text_monetary_amount_preimage_gap"
        message = (
            "UK replay skipped monetary-amount text op: quoted amount preimage "
            "is absent from the target subtree."
        )
    elif text_patch.kind is TextPatchKindEnum.DELETE and _parenthetical_omission_text_selector(
        text_patch.selector.match_text
    ):
        kind = "uk_replay_text_parenthetical_omission_preimage_gap"
        message = (
            "UK replay skipped parenthetical omission text op: quoted parenthetical "
            "preimage is absent from the target subtree."
        )
    elif text_patch.kind is TextPatchKindEnum.REPLACE and _text_patch_replacement_preserves_anchor(
        text_patch.selector.match_text,
        replacement,
    ):
        kind = "uk_replay_text_insert_anchor_preimage_gap"
        message = (
            "UK replay skipped insertion-style text op: the replacement preserves "
            "the source anchor, but that anchor is absent from the target subtree."
        )
    else:
        kind = "uk_replay_text_match_missing"
        message = "UK replay skipped text-based op: text_match not found in target subtree."
    return UKReplayTextMissClassification(
        kind=kind,
        message=message,
        source_shape=uk_replay_text_miss_source_shape(kind),
        blocking=kind in UK_REPLAY_BLOCKING_TEXT_MISS_KINDS,
    )
