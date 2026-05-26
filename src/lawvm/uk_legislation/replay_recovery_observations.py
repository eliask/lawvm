"""Owned replay recovery observation metadata for UK text replay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UKReplayRecoveryObservation:
    message: str
    family: str
    strict_disposition: str
    source_shape: str | None = None


UK_REPLAY_RECOVERY_OBSERVATIONS: dict[str, UKReplayRecoveryObservation] = {
    "uk_replay_definition_predicate_shall_construed_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-entry text op after recognizing "
            "the definition predicate variant 'shall be construed'."
        ),
        family="definition_entry_predicate_recovery",
        strict_disposition="record",
    ),
    "uk_replay_after_definition_child_structured_insert_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay inserted source-carried definition children after "
            "a preserved structured definition child."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="structured_after_definition_child_insert_selector",
    ),
    "uk_replay_after_definition_child_flat_ordinal_insert_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay inserted source-carried definition text after "
            "a flat definition entry using a bounded ordinal child segment."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="flat_after_definition_child_ordinal_insert_selector",
    ),
    "uk_replay_after_definition_text_insert_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay inserted definition text after proving a unique "
            "definition surface."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="after_definition_text_insert_selector",
    ),
    "uk_replay_definition_entry_qualifier_phrase_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-entry text op after recognizing "
            "a qualifier phrase between the defined term and predicate."
        ),
        family="definition_entry_predicate_recovery",
        strict_disposition="record",
    ),
    "uk_replay_definition_entry_orphan_separator_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-entry text op after normalizing "
            "an orphan comma after a definition-entry separator."
        ),
        family="definition_entry_separator_recovery",
        strict_disposition="record",
    ),
    "uk_replay_definition_entry_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition-entry text rewrite after "
            "proving a unique definition entry surface."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_entry_selector",
    ),
    "uk_replay_contextual_word_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a contextual adjacent-word text rewrite "
            "after resolving the source-carried anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="contextual_adjacent_word_selector",
    ),
    "uk_replay_contextual_word_anchor_kind_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a contextual adjacent-word text rewrite after "
            "normalizing the source-carried anchor kind."
        ),
        family="text_match_recovery",
        strict_disposition="record",
        source_shape="contextual_adjacent_word_selector",
    ),
    "uk_replay_ordinal_sentence_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an ordinal sentence text delete after proving "
            "a unique target text node under the source-named provision."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="ordinal_sentence_selector",
    ),
    "uk_replay_after_anchor_to_end_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an after-anchor tail text rewrite "
            "from a source-carried selector."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="after_anchor_to_end_selector",
    ),
    "uk_replay_node_local_range_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a node-local bounded range text rewrite "
            "without flattening the target's children."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="node_local_range_selector",
    ),
    "uk_replay_node_local_range_to_end_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a node-local range-to-end text rewrite "
            "without flattening the target's children."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="node_local_range_to_end_selector",
    ),
    "uk_replay_words_in_brackets_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a words-in-brackets text rewrite after proving "
            "the resolved target has exactly one parenthesized text span."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="words_in_brackets_selector",
    ),
    "uk_replay_each_other_place_after_anchor_insert_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a relative each-other-place insertion after "
            "skipping the first source-claimed occurrence of the quoted anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="each_other_place_after_anchor_selector",
    ),
    "uk_replay_each_other_place_substitution_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a relative each-other-place substitution, using "
            "the preceding first-occurrence sibling replacement when visible and "
            "otherwise skipping the first current occurrence."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="each_other_place_substitution_selector",
    ),
    "uk_replay_subtree_range_text_rewrite_flattened": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a bounded range text rewrite over the "
            "linearized target subtree and flattened covered children."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="subtree_range_selector",
    ),
    "uk_replay_subtree_range_to_end_text_rewrite_flattened": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a range-to-end text rewrite over the "
            "linearized target subtree and flattened covered children."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="subtree_range_to_end_selector",
    ),
    "uk_replay_definition_anchor_lexical_variant_recovered": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-anchor text op after resolving "
            "a narrow education/educational lexical variant in the source anchor."
        ),
        family="target_resolution_recovery",
        strict_disposition="block",
    ),
    "uk_replay_definition_anchor_parenthetical_translation_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-anchor text op after recognizing "
            "a parenthetical translation between the defined term and predicate."
        ),
        family="target_resolution_recovery",
        strict_disposition="record",
    ),
    "uk_replay_definition_anchor_qualifier_phrase_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-anchor text op after recognizing "
            "a qualifier phrase between the anchor term and predicate."
        ),
        family="target_resolution_recovery",
        strict_disposition="record",
    ),
    "uk_replay_definition_anchor_conjoined_term_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied definition-anchor text op after recognizing "
            "the anchor as the final term in a conjoined definition entry."
        ),
        family="target_resolution_recovery",
        strict_disposition="record",
    ),
    "uk_replay_definition_child_structured_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition-child text rewrite against "
            "a preserved structured definition child."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="structured_definition_child_selector",
    ),
    "uk_replay_definition_child_flat_ordinal_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition-child text rewrite against "
            "a flat definition entry using a bounded ordinal child segment."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="flat_definition_child_ordinal_selector",
    ),
    "uk_replay_in_definition_child_structured_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a scoped in-definition-child text rewrite "
            "against a preserved structured definition child."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="structured_in_definition_child_selector",
    ),
    "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a scoped in-definition-child text rewrite "
            "against a flat definition entry using a bounded ordinal child segment."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="flat_in_definition_child_ordinal_selector",
    ),
    "uk_replay_definition_child_tail_after_anchor_to_end_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition-child tail rewrite after proving "
            "the source-scoped definition entry, child segment, and tail anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_child_tail_after_anchor_to_end_selector",
    ),
    "uk_replay_definition_child_tail_flat_child_boundary_unavailable_anchor_unique": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition-child tail rewrite where source "
            "parse preserved the definition entry and a unique tail anchor, "
            "but not the cited child paragraph boundary."
        ),
        family="source_shape_recovery",
        strict_disposition="block",
        source_shape="flat_definition_child_boundary_unavailable_anchor_unique",
    ),
    "uk_replay_text_range_anchor_word_boundary_normalized": UKReplayRecoveryObservation(
        message=(
            "UK replay applied range text op after matching a quoted "
            "single-word range anchor as a word token."
        ),
        family="text_match_recovery",
        strict_disposition="record",
    ),
    "uk_replay_labeled_child_end_range_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a text range from a parent text anchor "
            "through the end of an explicitly labelled child provision."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
    ),
    "uk_replay_source_carried_child_tail_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a source-carried child-tail text rewrite "
            "against the collapsed parent text after proving the named "
            "child is the final child."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="source_carried_child_tail_selector",
    ),
    "uk_replay_source_carried_before_child_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a source-carried before-child text rewrite "
            "against the parent text after proving the named child is unique."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="source_carried_before_child_selector",
    ),
    "uk_replay_source_carried_after_child_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a source-carried after-child text rewrite "
            "against the named child text after proving the child anchor is unique."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="source_carried_after_child_selector",
    ),
    "uk_replay_source_carried_multi_child_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a source-carried multi-child text rewrite "
            "after proving every named child target is present and unique."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="source_carried_multi_child_selector",
    ),
    "uk_replay_amendment_insert_tail_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an amendment-instruction inserted-text rewrite "
            "after proving the target text contains an insert anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="amendment_instruction_insert_tail_selector",
    ),
    "uk_replay_amendment_program_inserted_parent_child_insert_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an amendment-program child insertion after proving "
            "the inserted parent and child anchor are unique in the target "
            "amendment-instruction text."
        ),
        family="amendment_program_recovery",
        strict_disposition="record",
        source_shape="amendment_program_inserted_parent_child_selector",
    ),
    "uk_replay_before_definition_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a before-definition text rewrite after "
            "proving the target has a flat definition text surface."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="flat_definition_text_selector",
    ),
    "uk_replay_in_definition_at_end_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an at-end definition text rewrite after "
            "proving a unique definition surface."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_at_end_selector",
    ),
    "uk_replay_in_definition_range_to_end_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition range-to-end text rewrite "
            "after proving a unique definition surface and start anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_range_to_end_selector",
    ),
    "uk_replay_in_definition_range_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition range text rewrite after "
            "proving a unique definition surface, start anchor, and end anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_range_selector",
    ),
    "uk_replay_in_definition_after_each_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied an all-occurrences definition text rewrite "
            "after proving a unique definition surface and at least one anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_after_each_anchor_selector",
    ),
    "uk_replay_in_definition_after_anchor_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay applied a definition after-anchor text rewrite after "
            "proving a unique definition surface and unique anchor."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="definition_after_anchor_selector",
    ),
    "uk_replay_proviso_child_structured_text_rewrite_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay replaced the text of a proviso child paragraph "
            "after matching the child label."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="proviso_child_selector",
    ),
    "uk_replay_children_range_replaced_with_text_applied": UKReplayRecoveryObservation(
        message=(
            "UK replay deleted a range of child provisions and "
            "inserted replacement text in the parent."
        ),
        family="text_rewrite_recovery",
        strict_disposition="record",
        source_shape="children_range_selector",
    ),
}


def uk_replay_recovery_observation(rule_id: str) -> UKReplayRecoveryObservation:
    return UK_REPLAY_RECOVERY_OBSERVATIONS[rule_id]
