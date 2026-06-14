"""UK believed_spec catalog — one-line falsifiable hypotheses per witness rule.

This is Stream B's standalone catalog, kept apart from ``spec_ledger.py`` so the
UK rule-spec prose can grow without colliding with the neutral core / FI adapter.
A guarded adapter in ``spec_ledger.py`` imports ``_UK_RULE_SPECS`` and feeds it as
the catalog when building a ``-j uk`` ledger.

Each value is a one-line ``believed_spec`` in the same voice as ``_FI_RULE_SPECS``
(see ``spec_ledger.py``): a concrete, falsifiable statement of what the
transformation *asserts about UK legislative semantics*. The prose is grounded in
the rule's actual emission behavior in ``src/lawvm/uk_legislation/``; where a rule
corresponds to a drafting convention named in
``notes/UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md`` (OPC Drafting Guidance Part 6
amendment grammar, temporal/commencement Parts 6.8/10.6/10.7, the manual-frontier
ambiguity lane) the hypothesis is phrased to match that convention.

Keys are the *string values* of the ``*_RULE_ID`` constants (the value that lands
in an op's ``witness_rule_id``), not the Python identifier names.

Coverage scope (kept honest; see ``tests/test_spec_ledger_uk_catalog.py``):

* Every module-level ``*_RULE_ID`` *string constant* declared in the
  ``uk_legislation`` package is cataloged here.
* The full ``uk_manual_frontier_*`` family of literal classification ids is
  cataloged here.
* **Not** cataloged (documented, not faked): rule ids that are *dynamically
  constructed* at emission time and so cannot be statically enumerated —
    - ``uk_execution_authorization_{lane}_*`` / ``..._replay_adjudication_{bucket}``
      (``execution_authorization.py``: lane/bucket-parameterized),
    - ``uk_repeal_semantics_*`` phrase/effect-family ids
      (``repeal_semantics_witnesses.py``: ``f"uk_repeal_semantics_{family}"`` etc.),
    - ``<base_RULE_ID>_unresolved`` suffix variants synthesized in
      ``effect_table_lowering.py`` from a cataloged base.
  The static prefixes of these families are recorded in
  ``_UK_DYNAMIC_RULE_ID_PREFIXES`` so the coverage guard can exclude them
  deliberately rather than silently.
"""
from __future__ import annotations

from typing import Dict, Tuple

# Dynamically constructed rule-id families that cannot be statically enumerated
# (prefix + runtime suffix). The guard excludes any discovered id matching a
# prefix here from the coverage requirement, and asserts the prefix is real.
_UK_DYNAMIC_RULE_ID_PREFIXES: Tuple[str, ...] = (
    "uk_execution_authorization_",
    "uk_repeal_semantics_",
)

# Suffix appended at emission time to a cataloged base rule id when a lowered
# text/structural op could not be resolved against the live tree.
_UK_DYNAMIC_RULE_ID_SUFFIXES: Tuple[str, ...] = (
    "_unresolved",
)


_UK_RULE_SPECS: Dict[str, str] = {
    # -- Whole-Act / occurrence-scope substitution (OPC §6 occurrence scope) ----
    "uk_effect_simple_whole_act_all_occurrences_substitution_text_patch":
        "'In this Act, for X substitute Y' rewrites every occurrence of X across the whole Act.",
    "uk_effect_unquoted_all_occurrences_substitution_text_patch":
        "An unquoted all-occurrences substitution rewrites X to Y wherever X appears in the target scope.",
    "uk_effect_wherever_appearing_substitution_text_patch":
        "'wherever appearing, for X substitute Y' rewrites X to Y at every occurrence in scope.",
    "uk_effect_wherever_they_occur_substitution_text_patch":
        "'wherever they occur, for X substitute Y' rewrites X to Y at every occurrence in scope.",
    "uk_effect_multi_wherever_occurring_substitution_text_patch":
        "A multi-pair 'wherever occurring' instruction applies each X->Y rewrite at every occurrence.",
    "uk_effect_respectively_all_occurrences_substitution_text_patch":
        "A 'respectively' list pairs each source word with its replacement and rewrites every occurrence.",
    "uk_effect_both_subsequent_occurrences_substitution_text_patch":
        "'in both places' / both subsequent occurrences substitutes X->Y at the two later occurrences only.",
    "uk_effect_quoted_word_ordinal_places_substitution_text_patch":
        "A quoted word at the named ordinal place(s) is substituted there, not at other occurrences.",
    "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch":
        "A quoted word is substituted only at the ordinal occurrences the instruction names.",
    "uk_effect_range_where_ordinal_substitution_text_patch":
        "A word-range substitution is scoped to the ordinal occurrence named ('where ... occurs').",
    "uk_effect_sibling_first_then_each_other_place_substitution_text_patch":
        "First-occurrence and each-other-place are substituted with distinct payloads in one instruction.",
    "uk_effect_sibling_first_then_second_place_deictic_substitution_text_patch":
        "First-then-second-place deictic substitution rewrites the two occurrences with their named payloads.",
    "uk_effect_sibling_first_then_subsequent_occurrence_substitution_text_patch":
        "First-then-subsequent-occurrence substitution rewrites the first then the later occurrence(s).",
    "uk_effect_negative_left_context_excluded_children_substitution_text_patch":
        "An occurrence-scoped substitution excludes child contexts matching a negative left-context guard.",

    # -- Bare / anchored quoted substitution (OPC §6.2 substitution) ------------
    "uk_effect_bare_quoted_substitution_text_patch":
        "'for \"X\" substitute \"Y\"' replaces the quoted text X with Y at the target.",
    "uk_effect_passive_quoted_substitution_text_patch":
        "'\"X\" is substituted by \"Y\"' (passive voice) replaces quoted X with Y at the target.",
    "uk_effect_unquoted_anchor_quoted_substitution_text_patch":
        "A substitution anchored on an unquoted phrase replaces the quoted payload at that anchor.",
    "uk_effect_words_before_quoted_anchor_substitution_text_patch":
        "Words before a quoted anchor are substituted while the anchor is preserved as position.",
    "uk_effect_alternate_preimage_substitution_text_patch":
        "A substitution offering an alternate preimage matches whichever preimage variant is live.",
    "uk_effect_amendment_inserted_text_substitution_text_patch":
        "Text previously inserted by an amendment is the preimage substituted by this later effect.",
    "uk_effect_amount_specified_substitution_text_patch":
        "A 'for the amount specified substitute' instruction replaces the monetary/numeric amount in place.",
    "uk_effect_referent_qualified_substitution_text_patch":
        "A substitution qualified by a referent ('the X in Y') is scoped to that referent's text.",
    "uk_effect_reference_to_substitution_text_patch":
        "'for the reference to X substitute a reference to Y' rewrites the cross-reference text.",
    "uk_effect_imperative_replace_reference_substitution_text_patch":
        "An imperative 'replace the reference to X' rewrites that cross-reference to the new target.",
    "uk_effect_imperative_replace_with_substitution_text_patch":
        "An imperative 'replace X with Y' rewrites X to Y at the target.",
    "uk_effect_varied_by_substituting_text_patch":
        "'is varied by substituting X for Y' (SI variation voice) rewrites Y to X at the target.",
    "uk_effect_missing_space_there_is_substituted_text_patch":
        "A 'there is substituted' form missing a separating space still substitutes the quoted payload.",
    "uk_effect_dangling_active_substitution_quote_text_patch":
        "An active substitution with a dangling/unbalanced quote recovers the intended quoted payload.",
    "uk_effect_dangling_passive_substitution_quote_text_patch":
        "A passive substitution with a dangling/unbalanced quote recovers the intended quoted payload.",
    "uk_effect_quoted_substitute_dash_quoted_payload_text_patch":
        "A 'substitute \"X\"—\"Y\"' dash-joined form substitutes the dashed quoted payload.",
    "uk_effect_quoted_substitution_scope_note_text_patch":
        "A quoted substitution carrying a scope note applies the rewrite within that stated scope.",
    "uk_effect_except_child_substitution_text_patch":
        "An 'except in X' substitution rewrites the target everywhere except within the excluded child.",
    "uk_effect_except_phrase_substitution_text_patch":
        "An 'except where the phrase ...' substitution rewrites the target except at the excluded phrase.",

    # -- Range / 'from ... to' substitution -------------------------------------
    "uk_effect_range_substitution_text_patch":
        "A 'from X to Y' word-range substitution replaces the inclusive span X..Y with the payload.",
    "uk_effect_range_unquoted_substitution_text_patch":
        "An unquoted word-range substitution replaces the spanned text with the payload.",
    "uk_effect_range_to_end_there_is_substituted_text_patch":
        "A range-to-end 'there is substituted' replaces from the anchor to the provision end.",
    "uk_effect_range_to_end_bare_quoted_substitution_text_patch":
        "A bare-quoted range-to-end substitution replaces from the quoted anchor to the end.",
    "uk_effect_range_to_end_missing_the_substitution_text_patch":
        "A range-to-end substitution with an elided 'the' still replaces from anchor to end.",
    "uk_effect_range_to_end_ordinal_block_substitution_text_patch":
        "A range-to-end substitution at an ordinal block replaces from that ordinal to the end.",
    "uk_effect_range_to_end_quoted_dash_substitution_text_patch":
        "A quoted-dash range-to-end substitution replaces from the dashed quoted anchor to the end.",
    "uk_effect_anchor_to_end_block_substitution_text_patch":
        "An anchor-to-end block substitution replaces the block from the anchor to the provision end.",
    "uk_effect_quoted_words_anchor_to_end_substitution_text_patch":
        "Quoted words from an anchor to the end are substituted with the payload.",
    "uk_effect_after_anchor_to_end_unquoted_substitution_text_patch":
        "An unquoted after-anchor-to-end substitution replaces text from after the anchor to the end.",
    "uk_effect_after_anchor_before_final_word_substitution_text_patch":
        "A substitution after an anchor but before the final word rewrites the bounded inner span.",
    "uk_effect_after_anchor_substitute_tail_substitution_text_patch":
        "An after-anchor substitution rewrites the tail following the anchor.",
    "uk_effect_labeled_end_range_substitution_text_patch":
        "A labelled end-range substitution replaces the span ending at the named label.",
    "uk_effect_child_qualified_range_substitution_text_patch":
        "A child-qualified range substitution scopes the range rewrite to the named child.",
    "uk_effect_ordinal_paragraph_range_substitution_text_patch":
        "An ordinal-paragraph range substitution rewrites the span across the named ordinal paragraphs.",

    # -- Definition-targeted text rewrites (OPC §6 definitions) -----------------
    "uk_effect_definition_anchor_final_punctuation_substitution_text_patch":
        "A definition's final punctuation is substituted (e.g. full stop to semicolon) at the entry end.",
    "uk_effect_definition_anchor_tail_insert_text_patch":
        "Text is inserted at the tail of a definition entry anchored on its defined term.",
    "uk_effect_definition_child_and_tail_substitution_text_patch":
        "A definition's child and its trailing tail are substituted together as one rewrite.",
    "uk_effect_definition_child_range_substitution_text_patch":
        "A range within a definition's children is substituted with the payload.",
    "uk_effect_definition_child_tail_after_anchor_to_end_text_patch":
        "A definition child's tail after a quoted anchor is rewritten to the entry end.",
    "uk_effect_unquoted_definition_range_to_end_substitution_text_patch":
        "An unquoted definition range-to-end substitution rewrites from the anchor to the entry end.",
    "uk_effect_in_definition_after_anchor_add_text_patch":
        "'in the definition of X, after \"A\" add \"B\"' inserts B after anchor A inside that definition.",
    "uk_effect_in_definition_after_anchor_insert_text_patch":
        "'in the definition of X, after \"A\" insert \"B\"' inserts B after anchor A inside that definition.",
    "uk_effect_in_definition_after_paragraphs_insert_text_patch":
        "An in-definition insert after the named paragraphs adds the payload at that position.",
    "uk_effect_in_definition_at_end_target_context_insert_text_patch":
        "An in-definition 'at the end' insert appends the payload at the end of the definition entry.",
    "uk_effect_in_definition_child_before_anchor_insert_text_patch":
        "An in-definition insert before a child anchor places the payload ahead of that anchor.",
    "uk_effect_metadata_carried_definition_entry_repeal_text_patch":
        "A metadata-carried definition-entry repeal omits the whole definition entry as text.",
    "uk_effect_metadata_carried_definition_quoted_word_repeal_text_patch":
        "A metadata-carried repeal removes the quoted word(s) inside a definition entry.",
    "uk_effect_interpretation_entries_relating_repeal_text_patch":
        "A repeal of interpretation entries 'relating to' a subject omits those matching entries.",
    "uk_effect_source_carried_deictic_definition_entry_insert_text_patch":
        "A deictic ('that definition') source-carried insert adds a definition entry at the referenced point.",
    "uk_effect_source_range_definition_entry_insert_text_patch":
        "A source-carried range defines a new definition entry inserted as text at the range position.",
    "uk_effect_source_range_definition_entry_list_end_schedule_entry_insert":
        "A source-range definition entry is inserted at the end of a schedule's definition list.",
    "uk_effect_direct_definition_entry_list_end_schedule_entry_insert":
        "A directly-supplied definition entry is appended at the end of a schedule definition list.",
    "uk_effect_source_range_definition_entry_at_end_insert_rejected":
        "An at-end definition-entry insert lacking a resolvable list anchor is rejected, not guessed.",

    # -- 'after'/'before'/'at end' insertion (OPC §6.3 insertion) ---------------
    "uk_effect_after_child_text_insertion_patch":
        "'after \"X\" insert \"Y\"' inserts Y immediately after child text X.",
    "uk_effect_after_ordinal_paragraph_text_insertion_patch":
        "An insert after the named ordinal paragraph places the payload after that paragraph.",
    "uk_effect_after_quoted_anchor_closing_quote_insert_text_patch":
        "An after-quoted-anchor insert recovers a missing closing quote and inserts after the anchor.",
    "uk_effect_after_quoted_anchor_dangling_insert_quote_text_patch":
        "An after-quoted-anchor insert with a dangling quote recovers the payload and inserts after the anchor.",
    "uk_effect_after_quoted_anchor_each_other_place_insert_text_patch":
        "An after-quoted-anchor insert applies at each other place the anchor occurs.",
    "uk_effect_after_quoted_anchor_except_child_insert_text_patch":
        "An after-quoted-anchor insert applies except within the excluded child.",
    "uk_effect_after_quoted_anchor_include_text_patch":
        "An after-quoted-anchor 'include' insert adds the listed payload after the anchor.",
    "uk_effect_after_quoted_anchor_ordinal_block_insert_text_patch":
        "An after-quoted-anchor insert at the named ordinal block places the payload there.",
    "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch":
        "An after-quoted-anchor insert applies at the named ordinal places of the anchor.",
    "uk_effect_after_quoted_anchor_space_before_comma_insert_text_patch":
        "An after-quoted-anchor insert normalizes the space-before-comma when placing the payload.",
    "uk_effect_after_reference_section_insert_text_patch":
        "An insert after a referenced section places the payload after that cross-reference.",
    "uk_effect_after_words_in_brackets_insert_text_patch":
        "An insert after parenthesised words places the payload after that bracketed phrase.",
    "uk_effect_before_child_text_substitution_patch":
        "'before \"X\" substitute' rewrites the text positioned before child anchor X.",
    "uk_effect_before_child_block_text_substitution_patch":
        "A before-child block substitution rewrites the block positioned before the child anchor.",
    "uk_effect_before_dangling_nested_quoted_anchor_insert_text_patch":
        "A before-anchor insert recovers a dangling nested quote and inserts before that anchor.",
    "uk_effect_before_nested_quoted_anchor_insert_text_patch":
        "An insert before a nested quoted anchor places the payload ahead of that nested anchor.",
    "uk_effect_before_quoted_anchor_all_occurrences_insert_text_patch":
        "A before-quoted-anchor insert applies before every occurrence of the anchor.",
    "uk_effect_before_quoted_anchor_nested_payload_insert_text_patch":
        "A before-quoted-anchor insert places a nested payload before the anchor.",
    "uk_effect_passive_before_quoted_anchor_insert_text_patch":
        "A passive-voice insert before a quoted anchor places the payload before that anchor.",
    "uk_effect_at_end_carried_parent_context_text_insertion_patch":
        "An 'at the end' insert using carried parent context appends the payload at the provision end.",
    "uk_effect_at_end_dangling_insert_quote_text_patch":
        "An 'at the end' insert with a dangling quote recovers the payload and appends it at the end.",
    "uk_effect_at_end_not_as_part_text_insertion_patch":
        "An 'at the end (but not as part of ...)' insert appends the payload outside the named sub-part.",
    "uk_effect_at_end_quoted_dash_text_insertion_patch":
        "An 'at the end' insert of a dash-joined quoted payload appends it at the provision end.",
    "uk_effect_at_end_step_insert_text_patch":
        "An 'at the end' step insert appends a new numbered step/item at the provision end.",
    "uk_effect_at_end_stray_full_stop_insert_text_patch":
        "An 'at the end' insert normalizes a stray full stop when appending the payload.",
    "uk_effect_at_end_unquoted_text_insertion_patch":
        "An 'at the end' insert of unquoted text appends that text at the provision end.",
    "uk_effect_at_end_words_in_parentheses_insert_text_patch":
        "An 'at the end' insert appends the parenthesised words at the provision end.",
    "uk_effect_before_step_insert_text_patch":
        "A 'before' step insert places a new numbered step/item before the named step.",
    "uk_effect_beginning_carried_parent_context_text_insertion_patch":
        "A 'at the beginning' insert using carried parent context prepends the payload to the provision.",

    # -- Omission / repeal text patches (OPC §6.1 repeal/omit) ------------------
    "uk_effect_after_anchor_to_end_omission_text_patch":
        "An after-anchor-to-end omission deletes text from after the anchor to the provision end.",
    "uk_effect_from_beginning_omission_text_patch":
        "A from-beginning omission deletes text from the provision start to the named anchor.",
    "uk_effect_opening_words_omission_text_patch":
        "An omission of opening words deletes the leading words of the provision.",
    "uk_effect_contextual_adjacent_word_omit_text_patch":
        "A contextual omission deletes a word together with its adjacent connective, preserving grammar.",
    "uk_effect_all_occurrences_word_repeal_text_patch":
        "An all-occurrences word repeal omits the word at every occurrence in scope.",
    "uk_effect_multi_quoted_word_repeal_text_patches":
        "A multi-quoted-word repeal omits each listed quoted word as a separate text patch.",
    "uk_effect_ordinal_word_repeal_text_patch":
        "An ordinal-word repeal omits the word at the named ordinal occurrence only.",
    "uk_effect_ordinal_sentence_repeal_text_patch":
        "An ordinal-sentence repeal omits the sentence at the named ordinal position.",
    "uk_effect_ordinal_paragraph_repeal_text_patch":
        "An ordinal-paragraph repeal omits the paragraph at the named ordinal position.",
    "uk_effect_range_repeal_text_patch":
        "A 'from X to Y' word-range repeal omits the inclusive span X..Y.",
    "uk_effect_range_repeal_pre_predicate_comma_text_patch":
        "A range repeal normalizes a pre-predicate comma when deleting the spanned text.",
    "uk_effect_listed_word_and_range_to_end_repeal_text_patch":
        "A repeal of a listed word plus a range-to-end omits both the word and the trailing span.",
    "uk_effect_section_reference_repeal_text_patch":
        "A repeal of a section cross-reference omits that reference text in place.",
    "uk_effect_unquoted_type_label_repeal_text_patch":
        "An unquoted type-label repeal omits the named structural label phrase as text.",
    "uk_effect_metadata_carried_omitting_words_repeal_text_patch":
        "A metadata-carried 'omitting the words' repeal deletes the named words as text.",
    "uk_effect_metadata_carried_quoted_words_repeal_text_patch":
        "A metadata-carried repeal deletes the quoted words at the carried target.",
    "uk_effect_cease_effect_quoted_word_repeal_text_patch":
        "A 'shall cease to have effect' applied to quoted words omits those words.",
    "uk_effect_cease_effect_range_to_end_repeal_text_patch":
        "A 'shall cease to have effect' over a range-to-end omits from the anchor to the provision end.",
    "uk_effect_range_independent_end_occurrence_repeal_text_patch":
        "A range repeal with an independent end occurrence omits the span bounded by that occurrence.",
    "uk_effect_range_independent_end_occurrence_substitution_text_patch":
        "A range substitution with an independent end occurrence rewrites the span bounded by that occurrence.",
    "uk_effect_range_independent_end_occurrence_text_patch":
        "A range text patch keys its end on an independently-located occurrence anchor.",

    # -- Source-carried child/tail/parent rewrites ------------------------------
    "uk_effect_source_carried_between_paragraphs_substitution_text_patch":
        "Source-carried text between two named paragraphs is substituted with the payload.",
    "uk_effect_source_carried_child_list_tail_repeal_text_patch":
        "A source-carried child-list tail repeal omits the trailing list items of the child.",
    "uk_effect_source_carried_child_tail_repeal_text_patch":
        "A source-carried child-tail repeal omits the trailing text of the named child.",
    "uk_effect_source_carried_child_tail_substitution_text_patch":
        "A source-carried child-tail substitution rewrites the trailing text of the named child.",
    "uk_effect_source_carried_deictic_child_tail_repeal_text_patch":
        "A deictic source-carried child-tail repeal omits the trailing text of the referenced child.",
    "uk_effect_source_carried_following_words_repeal_text_patch":
        "A source-carried 'and the following words' repeal omits the trailing words after the anchor.",
    "uk_effect_source_carried_multi_subunit_repeal_text_patch":
        "A source-carried multi-subunit repeal omits text across several named subunits.",
    "uk_effect_source_carried_multi_subunit_substitution_text_patch":
        "A source-carried multi-subunit substitution rewrites text across several named subunits.",
    "uk_effect_source_carried_subparagraph_tail_repeal_text_patch":
        "A source-carried subparagraph-tail repeal omits the trailing text of the named subparagraph.",
    "uk_effect_source_carried_table_entry_paragraph_substitution_text_patch":
        "A source-carried substitution rewrites the paragraph text of a table entry.",
    "uk_effect_source_carried_inserted_subsection_child_range_substitution_lowered":
        "A source-carried range within a freshly-inserted subsection's children is lowered to a substitution.",
    "uk_effect_source_carried_parent_quoted_child_substitution_lowered":
        "A parent-carried quoted child substitution is lowered to rewrite that child's text.",
    "uk_effect_source_carried_structured_tail_substitution_lowered":
        "A source-carried structured tail substitution is lowered to rewrite the parent's structured tail.",
    "uk_effect_source_parent_after_anchor_to_end_substitution_text_patch":
        "A parent-scoped after-anchor-to-end substitution rewrites from the anchor to the parent's end.",
    "uk_effect_source_parent_at_end_added_payload_lowered":
        "A parent-scoped 'at the end add' payload is lowered to append at the end of the parent.",
    "uk_effect_source_parent_at_end_quoted_list_text_insertion_patch":
        "A parent-scoped at-end insert appends the quoted list payload at the parent's end.",
    "uk_effect_source_parent_at_end_text_insertion_patch":
        "A parent-scoped at-end insert appends the text payload at the parent's end.",
    "uk_effect_source_parent_carried_after_word_ordinal_insert_text_patch":
        "A parent-carried insert after the ordinal occurrence of a word places the payload there.",
    "uk_effect_source_parent_substitution_range_payload_lowered":
        "A parent-scoped substitution range payload is lowered to rewrite the spanned parent text.",
    "uk_effect_source_parent_word_range_substitution_text_patch":
        "A parent-scoped word-range substitution rewrites the spanned text within the parent.",
    "uk_effect_source_sibling_except_occurrence_substitution_text_patch":
        "A sibling-scoped substitution rewrites every occurrence except the named excluded one.",
    "uk_effect_target_scoped_each_child_after_word_insert_text_patch":
        "A target-scoped insert adds the payload after the named word in each child of the target.",

    # -- Structural insert / sibling / amendment-program lowering ---------------
    "uk_effect_after_anchor_insert_promoted":
        "An after-anchor insertion is promoted to a structural insert when its payload is a whole provision.",
    "uk_effect_block_substitution_tail_promoted_to_insert_after":
        "A block substitution's extra tail payload is promoted to an insert-after of a new sibling.",
    "uk_effect_substituted_series_new_sibling_insert_lowered":
        "A substituted series whose payload exceeds the preimage lowers the surplus to new-sibling inserts.",
    "uk_effect_substituted_series_pre_anchor_sibling_insert_lowered":
        "A substituted series lowers a pre-anchor surplus payload to a sibling insert before the anchor.",
    "uk_effect_substituted_range_extra_payload_sibling_insert_lowered":
        "A substituted range with extra payload lowers the surplus to a new-sibling insert.",
    "uk_effect_amendment_program_inserted_anchor_structural_insert_lowered":
        "An amendment program's inserted-anchor instruction is lowered to a structural insert at that anchor.",
    "uk_effect_amendment_program_inserted_parent_child_insert_text_patch":
        "An amendment program inserts a child into a freshly-inserted parent as a carried text patch.",
    "uk_effect_after_paragraph_insert_block_amendment_lowered":
        "An after-paragraph block amendment is lowered to insert the block after the named paragraph.",
    "uk_effect_after_paragraph_insert_connector_sibling_lowered":
        "An after-paragraph insert carrying a connector is lowered to a connector-joined sibling insert.",
    "uk_effect_after_paragraph_insert_labelled_series_lowered":
        "An after-paragraph insert of a labelled series is lowered to inserts of each labelled sibling.",
    "uk_effect_after_paragraph_insert_single_label_lowered":
        "An after-paragraph insert of a single labelled provision is lowered to one sibling insert.",
    "uk_effect_after_section_subsection_range_insert_block_amendment_lowered":
        "An after-section/subsection range block amendment is lowered to insert the block over the range.",
    "uk_effect_at_end_section_subsection_insert_block_amendment_lowered":
        "An at-end section/subsection block amendment is lowered to append the block at the provision end.",
    "uk_effect_definition_child_structural_insert_before_tail_connector_lowered":
        "A definition child structural insert is placed before the tail connector of the definition.",
    "uk_effect_definition_child_structural_sibling_insert_lowered":
        "A definition child structural insert is lowered to a sibling insert within the definition.",
    "uk_effect_definition_child_structural_substitution_lowered":
        "A definition child structural substitution is lowered to replace that child node.",
    "uk_effect_flat_p1para_schedule_paragraph_insert_payload_lowered":
        "A flat P1para schedule paragraph payload is lowered to a structural schedule-paragraph insert.",
    "uk_effect_nonaddressable_schedule_part_insert_target_normalized":
        "A non-addressable schedule Part insert target is normalized to an addressable insert anchor.",
    "uk_effect_source_parent_whole_schedule_insert_inferred":
        "A parent-scoped instruction whose payload is a whole schedule is inferred as a whole-schedule insert.",
    "uk_effect_non_schedule_list_entry_insert":
        "A non-schedule list entry instruction inserts the entry into the addressed body list.",

    # -- Schedule list-entry insert/repeal/replace ------------------------------
    "uk_effect_schedule_list_entry_insert":
        "A schedule list-entry insert adds the entry into the addressed schedule list at its anchor.",
    "uk_effect_schedule_list_entry_repeal":
        "A schedule list-entry repeal removes the addressed entry from the schedule list.",
    "uk_effect_schedule_list_entry_replace":
        "A schedule list-entry replace substitutes the addressed schedule list entry with the payload.",
    "uk_effect_schedule_list_entry_table_rows_lowered":
        "A schedule list-entry whose payload is table rows is lowered to a table-row insert.",
    "uk_effect_schedule_table_end_rows_lowered":
        "A schedule table-end rows instruction is lowered to append the rows at the table end.",

    # -- Repeal-table feeds (Schedule of repeals; OPC §6.1) ---------------------
    "uk_effect_repeal_table_structural_repeal":
        "A repeal-table row whose extent column names a whole provision repeals that provision structurally.",
    "uk_effect_repeal_table_quoted_words_text_repeal":
        "A repeal-table row naming quoted words omits those words as a text repeal at the target.",
    "uk_effect_repeal_table_sentence_text_repeal":
        "A repeal-table row naming a sentence omits that sentence as a text repeal.",
    "uk_effect_repeal_table_reference_text_repeal":
        "A repeal-table row naming a cross-reference omits that reference as a text repeal.",
    "uk_effect_repeal_table_column_entry_text_repeal":
        "A repeal-table column entry is lowered to a text repeal of the named words.",
    "uk_effect_repeal_table_definition_entry_text_repeal":
        "A repeal-table row naming a definition entry omits that whole definition entry.",
    "uk_effect_repeal_table_definition_child_text_repeal":
        "A repeal-table row naming a definition child omits that child within the definition entry.",
    "uk_effect_repeal_table_parent_child_text_repeal_split":
        "A repeal-table parent/child row is split into a text repeal scoped to the named child.",
    "uk_effect_flat_repeal_schedule_quoted_words_text_repeal":
        "A flat repeal-schedule entry naming quoted words omits those words as a text repeal.",
    "uk_effect_flat_repeal_schedule_structural_repeal":
        "A flat repeal-schedule entry naming a provision repeals that provision structurally.",

    # -- Table-cell / table-entry text patches and inserts ----------------------
    "uk_effect_table_cell_child_list_insert":
        "A table-cell child-list insert adds the list item into the addressed table cell.",
    "uk_effect_table_column_entry_omission_text_patch":
        "A table-column entry omission deletes the text of the addressed column entry.",
    "uk_effect_table_column_entry_text_patch":
        "A table-column entry text patch rewrites the text of the addressed column entry.",
    "uk_effect_table_column_heading_text_patch":
        "A table-column heading text patch rewrites the heading of the addressed column.",
    "uk_effect_table_column_insert":
        "A table-column insert adds a new column into the addressed table.",
    "uk_effect_table_column_parent_at_end_insert_blocks_generic_text_append":
        "A table-column at-end parent insert blocks the generic text-append fallback (it owns the position).",
    "uk_effect_table_column_text_patch":
        "A table-column text patch rewrites the text within the addressed column.",
    "uk_effect_table_entry_deictic_label_column_text_patch":
        "A deictic-label table-entry patch rewrites the column text of the referenced labelled entry.",
    "uk_effect_table_entry_for_column_text_patch":
        "A 'for column X' table-entry patch rewrites that named column's text in the entry.",
    "uk_effect_table_entry_inline_text_insertion":
        "A table-entry inline insertion adds text inline within the addressed table entry.",
    "uk_effect_table_entry_instruction_rejected":
        "A table-entry instruction that cannot be resolved against the table is rejected, not guessed.",
    "uk_effect_table_entry_label_column_text_patch":
        "A table-entry label-column patch rewrites the label column text of the addressed entry.",
    "uk_effect_table_entry_label_text_patch":
        "A table-entry label patch rewrites the label text of the addressed entry.",
    "uk_effect_table_entry_labels_column_text_patch":
        "A table-entry labels-column patch rewrites the (plural) labels column text of the entry.",
    "uk_effect_table_entry_relating_column_text_patch":
        "A table-entry 'relating to' column patch rewrites that column's text in the entry.",
    "uk_effect_table_entry_relating_text_patch":
        "A table-entry 'relating to' patch rewrites the relating text of the addressed entry.",
    "uk_effect_table_entry_row_insert":
        "A table-entry row insert adds a new row into the addressed table.",
    "uk_effect_table_entry_row_replace":
        "A table-entry row replace substitutes the addressed table row with the payload.",
    "uk_effect_table_entry_text_patch":
        "A table-entry text patch rewrites the text of the addressed table entry.",
    "uk_effect_table_row_column_text_patch":
        "A table-row column patch rewrites the text of the named column in the addressed row.",
    "uk_effect_source_parent_table_column_entry_omission_text_patch":
        "A parent-scoped table-column entry omission deletes that column entry's text under the parent.",
    "uk_effect_source_previous_table_column_entry_omission_text_patch":
        "A table-column entry omission scoped to the previous entry deletes that prior entry's text.",
    "uk_effect_embedded_table_payload_structural_insertion_preserved":
        "An embedded-table payload structural insertion is preserved as a whole-table insert.",
    "uk_effect_embedded_table_payload_structural_substitution_preserved":
        "An embedded-table payload structural substitution is preserved as a whole-table replace.",
    "uk_block_amendment_table_preserved":
        "A block-amendment payload that is itself a table is preserved as a structural table block.",

    # -- Metadata-carried text patches ------------------------------------------
    "uk_effect_metadata_carried_after_ordinal_insert_text_patch":
        "A metadata-carried insert places the payload after the named ordinal occurrence.",
    "uk_effect_metadata_carried_after_substitute_insert_text_patch":
        "A metadata-carried insert places the payload after a prior substitute anchor.",
    "uk_effect_metadata_carried_at_end_add_insert_text_patch":
        "A metadata-carried 'at the end add' insert appends the payload at the provision end.",
    "uk_effect_metadata_carried_at_end_substitute_insert_text_patch":
        "A metadata-carried at-end substitute insert appends the substituted payload at the end.",
    "uk_effect_metadata_carried_range_insert_substitution_text_patch":
        "A metadata-carried range insert/substitution rewrites the spanned text with the carried payload.",
    "uk_effect_metadata_carried_substituting_words_text_patch":
        "A metadata-carried 'substituting the words' patch rewrites the named words at the target.",
    "uk_effect_metadata_pseudo_definition_entry_insert_text_patch":
        "A metadata pseudo-definition entry insert adds a definition-shaped entry as a text patch.",
    "uk_effect_compound_lettered_text_patch_instruction":
        "A compound lettered instruction is lowered to per-letter text patches at the addressed children.",
    "uk_effect_flat_target_paragraph_substitution_text_payload":
        "A flat-target paragraph substitution rewrites the addressed paragraph with the text payload.",

    # -- Mixed structural/text split -------------------------------------------
    "uk_effect_mixed_body_heading_all_occurrences_substitution_text_patch":
        "A mixed body+heading all-occurrences substitution rewrites every occurrence in body and heading.",
    "uk_effect_mixed_body_heading_substitution_split_text_patch":
        "A mixed body+heading substitution is split into separate body and heading text patches.",
    "uk_effect_mixed_structural_text_rewrite_text_half_repeal":
        "A mixed structural+text instruction lowers its text half to a repeal alongside the structural op.",

    # -- Word-substitution reclassification to structural -----------------------
    "uk_effect_word_substitution_escalated_to_structural_replace":
        "A word substitution covering a whole node is escalated to a structural replace of that node.",
    "uk_effect_word_substitution_parent_child_replacement_reclassified":
        "A word substitution spanning parent and child is reclassified as a parent/child structural replace.",
    "uk_effect_word_substitution_structural_child_replacement_reclassified":
        "A word substitution covering a whole child is reclassified as a structural replacement of that child.",
    "uk_effect_connector_preceding_child_list_entry_substitution":
        "A substitution of a connector preceding a child list entry rewrites that connector text.",

    # -- Payload eID / label / kind normalization -------------------------------
    "uk_payload_descendant_eid_synthesis":
        "Inserted payload descendants are assigned synthesized eIDs derived from the target address.",
    "uk_whole_schedule_payload_descendant_eid_synthesis":
        "A whole-schedule inserted payload has its descendant eIDs synthesized from the schedule address.",
    "uk_effect_payload_kind_realigned_to_target_leaf":
        "An inserted payload's structural kind is realigned to match the resolved target leaf kind.",
    "uk_effect_payload_label_realigned_to_target_leaf":
        "An inserted payload's label is realigned to match the resolved target leaf label.",
    "uk_container_number_inferred_from_source_uri":
        "A missing container number is inferred from the source document URI rather than left unset.",

    # -- Target prelude / refinement / overrides --------------------------------
    "uk_effect_enacted_schedule_table_row_part_target_refined":
        "An enacted schedule table-row target is refined to the addressed Part within the schedule.",
    "uk_effect_numbered_schedule_entry_repeal_target_refined":
        "A numbered schedule-entry repeal target is refined to the specific numbered entry.",
    "uk_effect_source_text_schedule_paragraph_target_overrides_metadata":
        "Source-text schedule-paragraph addressing overrides conflicting effect metadata for the target.",
    "uk_effect_fee_target_refinement_failed":
        "A fee-target refinement that cannot pin the addressed fee provision fails rather than guessing.",
    "uk_effect_substituted_for_label_changing_target_rebound":
        "A substitution that changes the target's own label rebinds the target to the new label.",

    # -- Temporal / commencement (OPC §§6.8, 10.6, 10.7) ------------------------
    "uk_effect_temporal_ceases_to_have_effect_replay_excluded":
        "A 'ceases to have effect' from a future date is excluded from current-text replay (temporal lane).",
    "uk_effect_undated_applied_si_commencement_date":
        "An undated applied SI effect takes the SI's own commencement date as its in-force date.",
    "uk_effect_undated_applied_si_commencement_unresolved":
        "An undated applied SI effect whose commencement date cannot be resolved is left unresolved, not dated.",
    "uk_commencement_undated_effects_block_self_commencement":
        "Undated effects in a commencement provision do not self-commence the commencing instrument.",
    "uk_commencement_unnumbered_single_schedule_target_resolved":
        "An unnumbered commencement reference to 'the Schedule' resolves to the sole schedule present.",
    "uk_prospective_effect_applied_to_current":
        "A prospective (not-yet-in-force) effect is applied to the current text only when warranted.",
    "uk_repeal_target_not_source_warranted":
        "A repeal whose target is not warranted by the source text is flagged rather than applied.",

    # -- Replay-time target resolution ------------------------------------------
    "uk_replay_target_resolved_by_recursive_descent":
        "A replay target absent at the addressed level is resolved by recursive descent into descendants.",
    "uk_replay_target_ambiguous_recursive_descent":
        "A recursive-descent target resolution that finds multiple candidates is reported ambiguous, not chosen.",
    "uk_replay_descendant_renumber_provision":
        "A renumber of a provision cascades to renumber its descendant labels accordingly.",
    "uk_replay_replace_materialized_as_insert_for_missing_leaf":
        "A replace whose target leaf is missing is materialized as an insert of the replacement leaf.",
    "uk_replay_schedule_entry_repeal_granularity_blocked":
        "A schedule-entry repeal coarser than the resolved target granularity is blocked rather than over-deleting.",
    "uk_replay_schedule_item_target_from_parent_substitution_resolved":
        "A schedule-item target is resolved from a parent substitution's payload structure.",
    "uk_replay_schedule_p1group_paragraph_wrapper_resolved":
        "A schedule P1group paragraph-wrapper is resolved transparently to the wrapped paragraph target.",
    "uk_replay_schedule_partition_transparent_paragraph_resolved":
        "A schedule partition is treated as transparent so the addressed paragraph resolves through it.",
    "uk_replay_source_carried_labeled_child_text_substitution_recovered":
        "A source-carried labelled-child text substitution is recovered against the live child at replay.",
    "uk_replay_source_label_changing_substitution_resolved":
        "A label-changing substitution is resolved at replay by matching the pre-change label.",
    "uk_replay_connector_preceding_child_list_entry_insert_resolved":
        "A connector-preceding child-list-entry insert is resolved to its position at replay.",
    "uk_replay_crossheading_and_structural_repeal_resolved":
        "A combined crossheading-and-structural repeal resolves both the heading and the provision at replay.",
    "uk_replay_crossheading_and_structural_repeal_unresolved":
        "A combined crossheading-and-structural repeal that cannot resolve both is left unresolved, not partial.",

    # -- Replay-time schedule list-entry resolution -----------------------------
    "uk_replay_schedule_list_entry_alphabetical_position_resolved":
        "A schedule list-entry insert at an alphabetical position resolves to the sorted insertion point.",
    "uk_replay_schedule_list_entry_anchor_article_normalized":
        "A schedule list-entry anchor expressed as an article number is normalized to the live anchor.",
    "uk_replay_schedule_list_entry_anchor_ordinal_resolved":
        "A schedule list-entry anchor expressed as an ordinal resolves to that ordinal entry.",
    "uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized":
        "A schedule list-entry anchor in parenthetical-paragraph form is normalized to the live anchor.",
    "uk_replay_schedule_list_entry_anchor_prefix_normalized":
        "A schedule list-entry anchor with a label prefix is normalized to match the live entry prefix.",
    "uk_replay_schedule_list_entry_anchor_unresolved":
        "A schedule list-entry anchor that matches no live entry is left unresolved, not force-placed.",
    "uk_replay_schedule_list_entry_beginning_position_resolved":
        "A schedule list-entry insert 'at the beginning' resolves to the head of the list.",
    "uk_replay_schedule_list_entry_end_position_resolved":
        "A schedule list-entry insert 'at the end' resolves to the tail of the list.",
    "uk_replay_schedule_list_entry_group_anchor_resolved":
        "A schedule list-entry group anchor resolves to the addressed group within the list.",
    "uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized":
        "A schedule list-entry repeal anchored by number is normalized to the live numbered entry.",
    "uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized":
        "A schedule list-entry repeal in parenthetical-paragraph form is normalized to the live entry.",
    "uk_replay_schedule_list_entry_repeal_resolved":
        "A schedule list-entry repeal resolves to the live entry and removes it.",
    "uk_replay_schedule_list_entry_repeal_unresolved":
        "A schedule list-entry repeal that matches no live entry is left unresolved, not over-deleted.",
    "uk_replay_schedule_list_entry_replace_resolved":
        "A schedule list-entry replace resolves to the live entry and substitutes it.",
    "uk_replay_schedule_list_entry_replace_unresolved":
        "A schedule list-entry replace that matches no live entry is left unresolved, not guessed.",
    "uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized":
        "A schedule list-entry table anchor citing a short title is normalized to the live citation.",
    "uk_replay_schedule_list_entry_table_rows_insert_resolved":
        "A schedule list-entry table-rows insert resolves to its table position at replay.",
    "uk_replay_schedule_list_entry_table_rows_insert_unresolved":
        "A schedule list-entry table-rows insert that cannot resolve its position is left unresolved.",
    "uk_replay_schedule_table_end_rows_insert_resolved":
        "A schedule table-end rows insert resolves to append rows at the live table end.",
    "uk_replay_schedule_table_end_rows_insert_unresolved":
        "A schedule table-end rows insert that cannot resolve the table is left unresolved.",

    # -- Replay-time table resolution -------------------------------------------
    "uk_replay_table_cell_child_list_insert_resolved":
        "A table-cell child-list insert resolves to the addressed cell at replay.",
    "uk_replay_table_cell_child_list_insert_unresolved":
        "A table-cell child-list insert that cannot resolve its cell is left unresolved, not misplaced.",
    "uk_replay_table_column_insert_unresolved":
        "A table-column insert that cannot resolve its column position is left unresolved.",
    "uk_replay_table_entry_inline_text_insertion_unresolved":
        "A table-entry inline text insertion that cannot resolve its anchor is left unresolved.",
    "uk_replay_table_entry_inline_text_preimage_gap":
        "A table-entry inline text insertion whose preimage is missing records a preimage gap, not a guess.",
    "uk_replay_table_entry_row_insert_unresolved":
        "A table-entry row insert that cannot resolve its position is left unresolved.",
    "uk_replay_table_entry_row_replace_unresolved":
        "A table-entry row replace that cannot resolve the target row is left unresolved.",

    # -- Adapter / preservation / oracle alignment ------------------------------
    "uk_oracle_eid_alignment_adapter":
        "The oracle-comparison adapter aligns LawVM and oracle eIDs before scoring divergences.",
    "uk_non_schedule_list_entry_preserved":
        "A non-schedule list entry that LawVM cannot lower is preserved verbatim rather than dropped.",
    "uk_schedule_list_entry_preserved":
        "A schedule list entry that LawVM cannot lower is preserved verbatim rather than dropped.",

    # -- Manual-frontier classification family (OPC ambiguity lane) -------------
    # These ``uk_manual_frontier_*`` ids classify effects/rows that the automatic
    # compiler does not lower deterministically: each labels *why* an effect is at
    # the manual-review frontier (a typed "do not silently mutate" verdict per the
    # drafting ledger's ambiguity-stays-explicit decision order).
    "uk_manual_frontier_unclassified":
        "An effect at the manual frontier with no more specific classification (catch-all, loud).",
    "uk_manual_frontier_deterministic_supported":
        "An effect that the deterministic compiler does support — not actually a manual-frontier case.",
    "uk_manual_frontier_non_textual_or_out_of_scope":
        "A non-textual or out-of-scope effect that current replay deliberately does not lower.",
    "uk_manual_frontier_unsupported_effect_family":
        "An effect whose family has no supported lowering — a named gap, not a silent skip.",
    "uk_manual_frontier_source_pathology_insufficient":
        "An effect whose source text is too pathological to support deterministic lowering.",
    "uk_manual_frontier_missing_payload_source_insufficient":
        "An insert/substitution whose payload is absent from the source is left to manual review.",
    "uk_manual_frontier_non_substantive_payload_source_insufficient":
        "An effect whose source payload is non-substantive (whitespace/noise) is left to manual review.",
    "uk_manual_frontier_payload_without_action_source_insufficient":
        "A source payload with no resolvable action verb is left to manual review.",
    "uk_manual_frontier_source_payload_without_instruction_context":
        "A source payload lacking surrounding instruction context is left to manual review.",
    "uk_manual_frontier_instruction_header_source_insufficient":
        "An instruction header with insufficient source detail to lower is left to manual review.",
    "uk_manual_frontier_misselected_target_context_source_insufficient":
        "An effect whose selected target context looks wrong and source is insufficient to repick.",
    "uk_manual_frontier_parser_or_extraction_candidate":
        "An effect whose failure looks like a parser/extraction gap rather than a semantic one.",
    "uk_manual_frontier_range_to_container_candidate":
        "A range whose endpoints span into a container, needing manual judgement on scope.",
    "uk_manual_frontier_amendment_program_target_candidate":
        "An amendment-program effect whose target needs manual confirmation.",
    "uk_manual_frontier_deictic_amendment_program_target_candidate":
        "A deictic amendment-program effect whose referenced target needs manual confirmation.",
    "uk_manual_frontier_schedule_list_entry_candidate":
        "A schedule list-entry effect at the frontier of deterministic schedule-list lowering.",
    "uk_manual_frontier_appropriate_place_definition_entry_candidate":
        "An 'in the appropriate place' definition-entry insert needing manual placement judgement.",
    "uk_manual_frontier_appropriate_place_index_entry_candidate":
        "An 'in the appropriate place' index-entry insert needing manual placement judgement.",
    "uk_manual_frontier_appropriate_place_candidate":
        "An 'in the appropriate place' insert whose ordering convention needs manual judgement.",
    "uk_manual_frontier_table_appropriate_place_candidate":
        "An 'in the appropriate place' insert into a table needing manual placement judgement.",
    "uk_manual_frontier_repeal_table_candidate":
        "A repeal-table row at the frontier of deterministic repeal-table lowering.",
    "uk_manual_frontier_repeal_table_feed_source_target_gap":
        "A repeal-table feed row whose source/target linkage has a gap, left to manual review.",
    "uk_manual_frontier_application_by_reference_out_of_scope":
        "An 'applies ... by reference' modification treated as out of current replay scope.",
    "uk_manual_frontier_as_if_application_modification_out_of_scope":
        "An 'as if' application/modification effect treated as out of current replay scope.",
    "uk_manual_frontier_application_modification_payload_out_of_scope":
        "An application-modification payload treated as out of current replay scope.",
    "uk_manual_frontier_commencement_effect_out_of_scope":
        "A commencement effect treated as out of current text-replay scope (handled in the temporal lane).",
    "uk_manual_frontier_conditional_temporal_repeal_out_of_scope":
        "A conditional/temporal repeal treated as out of current text-replay scope.",
    "uk_manual_frontier_empty_type_whole_act_action_out_of_scope":
        "A whole-Act action with an empty effect type treated as out of scope.",
    "uk_manual_frontier_external_act_target_out_of_scope":
        "An effect targeting an external Act (not the statute under replay) treated as out of scope.",
    "uk_manual_frontier_definition_child_and_tail_substitution_candidate":
        "A definition child-and-tail substitution at the frontier of deterministic lowering.",
    "uk_manual_frontier_definition_child_structural_insert_candidate":
        "A definition child structural insert needing manual confirmation.",
    "uk_manual_frontier_definition_child_structural_substitution_candidate":
        "A definition child structural substitution needing manual confirmation.",
    "uk_manual_frontier_nested_definition_child_structural_substitution_candidate":
        "A nested definition child structural substitution needing manual confirmation.",
    "uk_manual_frontier_definition_anchor_tail_insert_candidate":
        "A definition-anchor tail insert needing manual confirmation.",
    "uk_manual_frontier_definition_entry_substitution_candidate":
        "A whole definition-entry substitution at the frontier of deterministic lowering.",
    "uk_manual_frontier_definition_list_end_insert_candidate":
        "A definition insert at a list end needing manual placement judgement.",
    "uk_manual_frontier_definition_range_to_end_source_context_insufficient":
        "A definition range-to-end effect whose source context is insufficient to lower.",
    "uk_manual_frontier_definition_target_fragment_source_insufficient":
        "A definition-target fragment effect whose source is insufficient to pin the target.",
    "uk_manual_frontier_sentence_scoped_repeated_insert_candidate":
        "A sentence-scoped repeated insert needing manual confirmation of scope.",
    "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate":
        "A source-carried multi-subunit text rewrite needing manual confirmation.",
    "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate":
        "A source-carried child-tail text rewrite needing manual confirmation.",
    "uk_manual_frontier_source_carried_structured_tail_substitution_candidate":
        "A source-carried structured-tail substitution needing manual confirmation.",
    "uk_manual_frontier_source_carried_structured_text_patch_candidate":
        "A source-carried structured text patch needing manual confirmation.",
    "uk_manual_frontier_relative_other_place_occurrence_candidate":
        "A relative 'other place' occurrence-scoped effect needing manual confirmation.",
    "uk_manual_frontier_referent_qualified_text_substitution_candidate":
        "A referent-qualified text substitution needing manual confirmation of the referent.",
    "uk_manual_frontier_scoped_occurrence_text_patch_with_exclusions_candidate":
        "A scoped-occurrence text patch carrying exclusions needing manual confirmation.",
    "uk_manual_frontier_scoped_occurrence_program_exclusion_candidate":
        "A scoped-occurrence program with an exclusion needing manual confirmation.",
    "uk_manual_frontier_scoped_occurrence_substitution_with_exclusions":
        "A scoped-occurrence substitution carrying exclusions at the manual frontier.",
    "uk_manual_frontier_structural_sibling_insert_candidate":
        "A structural sibling insert needing manual confirmation of position.",
    "uk_manual_frontier_deictic_structural_sibling_insert_candidate":
        "A deictic structural sibling insert needing manual confirmation of the referenced position.",
    "uk_manual_frontier_structural_child_range_substitution_candidate":
        "A structural child-range substitution needing manual confirmation.",
    "uk_manual_frontier_heading_facet_candidate":
        "A heading-facet effect at the frontier of deterministic heading lowering.",
    "uk_manual_frontier_crossheading_candidate":
        "A crossheading effect at the frontier of deterministic crossheading lowering.",
    "uk_manual_frontier_table_crossheading_candidate":
        "A table-crossheading effect at the frontier of deterministic lowering.",
    "uk_manual_frontier_crossheading_source_target_mismatch":
        "A crossheading effect whose source and target disagree, left to manual review.",
    "uk_manual_frontier_schedule_note_candidate":
        "A schedule-note effect at the frontier of deterministic lowering.",
    "uk_manual_frontier_whole_act_word_level_text_patch_candidate":
        "A whole-Act word-level text patch needing manual confirmation of occurrence scope.",
    "uk_manual_frontier_partial_whole_act_repeal_candidate":
        "A partial whole-Act repeal needing manual confirmation of the affected span.",
    "uk_manual_frontier_savings_qualified_text_omission_candidate":
        "A text omission qualified by a savings clause needing manual confirmation of scope.",
    "uk_manual_frontier_amendment_table_payload_without_row_context":
        "An amendment-table payload lacking row context, left to manual review.",
    "uk_manual_frontier_amount_specified_source_target_mismatch":
        "An 'amount specified' effect whose source amount and target disagree, left to manual review.",
    "uk_manual_frontier_child_qualified_word_omission_target_mismatch":
        "A child-qualified word omission whose target does not match, left to manual review.",
    "uk_manual_frontier_cross_container_renumber_candidate":
        "A renumber spanning containers needing manual confirmation.",
    "uk_manual_frontier_deictic_text_patch_source_insufficient":
        "A deictic text patch whose source context is insufficient to resolve the referent.",
    "uk_manual_frontier_effect_metadata_carried_text_patch_candidate":
        "An effect-metadata-carried text patch needing manual confirmation.",
    "uk_manual_frontier_effect_metadata_schedule_paragraph_range_to_part_renumber_candidate":
        "A schedule paragraph-range-to-part renumber inferred from effect metadata, needing confirmation.",
    "uk_manual_frontier_effect_metadata_unsupported_renumber_candidate":
        "An effect-metadata renumber with no supported lowering, left to manual review.",
    "uk_manual_frontier_labeled_child_end_range_candidate":
        "A labelled child end-range effect needing manual confirmation of the range.",
    "uk_manual_frontier_mixed_body_heading_text_substitution_split":
        "A mixed body+heading text substitution split into parts at the manual frontier.",
    "uk_manual_frontier_mixed_structural_definition_repeal_split":
        "A mixed structural+definition repeal split into parts at the manual frontier.",
    "uk_manual_frontier_mixed_structural_text_rewrite_split":
        "A mixed structural+text rewrite split into parts at the manual frontier.",
    "uk_manual_frontier_multi_enactment_specified_provisions_text_patch":
        "A text patch over specified provisions across multiple enactments, left to manual review.",
    "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate":
        "A pseudo-definition entry whose structural placement needs manual confirmation.",
    "uk_manual_frontier_structural_pseudo_definition_source_insufficient":
        "A pseudo-definition structural effect whose source is insufficient to place it.",
    "uk_manual_frontier_schedule_table_end_rows_payload_source_insufficient":
        "A schedule table-end rows insert whose payload source is insufficient to lower.",
    "uk_manual_frontier_table_column_insert_candidate":
        "A table-column insert needing manual confirmation of position.",
    "uk_manual_frontier_table_deictic_this_subsection_insert":
        "A deictic 'this subsection' table insert needing manual confirmation of the referent.",
    "uk_manual_frontier_table_entry_candidate":
        "A table-entry effect at the frontier of deterministic table lowering.",
    "uk_manual_frontier_table_entry_deictic_candidate":
        "A deictic table-entry effect needing manual confirmation of the referent.",
    "uk_manual_frontier_table_entry_placement_insert":
        "A table-entry placement insert needing manual confirmation of position.",
    "uk_manual_frontier_text_patch_postimage_chain_gap":
        "A text patch whose post-image cannot be chained from a prior patch, recorded as a chain gap.",
    "uk_manual_frontier_text_patch_preimage_chain_gap":
        "A text patch whose pre-image cannot be chained from a prior patch, recorded as a chain gap.",
    "uk_manual_frontier_text_patch_target_source_chain_gap":
        "A text patch whose target/source chain has a gap, recorded rather than guessed through.",
    "uk_manual_frontier_unquoted_preimage_substitution_source_insufficient":
        "An unquoted-preimage substitution whose source is insufficient to fix the preimage.",
}
