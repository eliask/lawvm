"""UK believed_spec catalog supplement — the effect-diagnostic / lowering gap.

This module fills a catalog gap in ``spec_ledger_uk_catalog.py``: a family of
``uk_effect_*`` / ``uk_affecting_act_*`` witness rule_ids emitted by the UK
lowering and effect-diagnostic code that currently carry NO ``believed_spec``.
Each entry is a one-line, present-tense, falsifiable hypothesis in the same voice
as ``_FI_RULE_SPECS`` (see ``spec_ledger.py``) and ``_UK_RULE_SPECS``.

The orchestrator wires this in by merging ``_UK_RULE_SPECS_SUPPLEMENT`` over the
main ``_UK_RULE_SPECS`` catalog; this module deliberately does NOT import or edit
either, keeping the supplement standalone and import-light.

Grounding discipline: every hypothesis is written from the rule's actual emission
site (grepped across ``src/lawvm/uk_legislation/`` and ``src/lawvm/tools/``), not
its name. The *kind* of each id determines the assertion:

* a ``_lowered`` / ``_text_patch`` / ``_inferred`` / ``_normalized`` /
  ``_structuralized`` / ``_extracted`` / ``_selected`` / ``_retargeted`` /
  ``_expanded`` / ``_refined`` / ``_split`` id asserts a *transformation* — what
  shape the source row is lowered to and why that shape is safe;
* a ``_rejected`` / ``_unresolved`` / ``_ignored`` / ``_recorded`` /
  ``_observed`` / ``_classified`` / ``_unlowered`` id asserts a *refusal or
  diagnosis* — what lowering declines to do and the source pathology that
  motivates the refusal (replay must not guess from live text/oracle order).

The single dynamically-suffixed id —
``uk_effect_repeal_table_definition_entry_text_repeal_unresolved`` — is the
``f"{base}_unresolved"`` variant minted in ``effect_table_lowering.py`` from the
cataloged base ``uk_effect_repeal_table_definition_entry_text_repeal``; it does
not appear as a literal string and is grounded on its emission branch.
"""
from __future__ import annotations

from typing import Dict

_UK_RULE_SPECS_SUPPLEMENT: Dict[str, str] = {
    # -- Source-pathology classification & terminal lowering filters -------------
    "uk_effect_source_pathology_classified":
        "A lowered UK effect is tagged with its source-pathology class plus its structural-for-replay / replay-applicable flags so downstream attribution can bucket it.",
    "uk_effect_application_overlay_no_textual_action_observed":
        "A non-textual application/extent overlay (applied/modified/excluded/extended/power-conferred/transfer-of-functions) mutates no consolidated text, so producing no replay op is correct, recorded as a non-blocking observation.",
    "uk_effect_lowering_no_supported_action_rejected":
        "A UK effect lowers to no replay operations because no supported action could be inferred from its type or source text.",
    "uk_effect_missing_structural_payload_rejected":
        "A structural UK effect with no extracted source payload is refused, since emitting an empty replace/insert would risk destructive replay.",
    "uk_effect_pdf_only_affecting_source_missing_payload_rejected":
        "A structural UK effect whose affecting Act is PDF-only (its XML is a NumberOfProvisions=\"0\" metadata stub with no Body/Schedule) is refused with a typed missing-input pathology, since the amending text is not in the archive as structured XML and cannot silently lower to zero ops.",
    "uk_effect_replay_applicability_filter_rejected":
        "A UK effect that compiled to ops but is excluded by replay applicability (e.g. temporal ceases-to-have-effect) is recorded as filtered, not replayed.",
    "uk_effect_instruction_text_payload_rejected":
        "A UK effect whose payload reused the amendment's instruction text rather than the source legal payload is refused as a source pathology.",
    "uk_effect_non_substantive_payload_rejected":
        "A structural effect payload containing only numbering or dot-leaders is refused, since replaying it would create a bogus legal unit.",
    "uk_effect_broad_schedule_flat_payload_rejected":
        "A structural replace of a whole schedule/schedule-part whose extracted payload is only flat text (not the target's descendant structure) is refused as undercovered.",

    # -- Affecting-act source acquisition / extraction / lane selection ----------
    "uk_affecting_act_xml_missing_rejected":
        "The affecting act's XML was missing from the archive, so the effect's source fragment could not be extracted and the effect is blocked.",
    "uk_affecting_act_current_shell_enacted_source_selected":
        "When the current affecting-act XML yields only a non-substantive dot-leader shell, lowering selects the official enacted XML, which carries substantive text for that provision.",
    "uk_affecting_act_missing_current_enacted_source_selected":
        "When the current affecting-act XML exposes no extractable same-provision node, lowering selects the enacted XML that does carry that exact affecting provision.",
    "uk_affecting_act_article_schedule_payload_source_extracted":
        "When an article's text points to material set out in an attached unnumbered Schedule, that Schedule is extracted as the amendment payload.",
    "uk_affecting_act_nonaddressable_schedule_part_context_ignored":
        "A named schedule-Part context represented only as an ancestor container (not in descendant paragraph IDs) is ignored once the normalized paragraph ref still extracts the right element.",
    "uk_affecting_act_parenthesized_range_source_extracted":
        "A parenthesized source range whose children are individually addressable is extracted as just the bounded child range into a synthetic wrapper, not by widening to the parent.",
    "uk_affecting_act_compound_reference_split_fallback":
        "A compound affecting reference that failed to extract whole, or resolved only to a gateway provision, is split at an explicit structural component and the selected part extracted.",
    "uk_affecting_act_block_amendment_payload_descendant_ref_rejected":
        "An affecting reference that greedily resolved to an anonymous descendant inside a BlockAmendment/InlineAmendment payload is refused, since that payload child is not the operative source provision.",
    "uk_affecting_act_schedule_part_standalone_split_rejected":
        "A named schedule Part that cannot be resolved while preserving its schedule container is refused, because the attempted standalone Part split may select a main-body element instead.",

    # -- Target-shape / feed normalization transforms ---------------------------
    "uk_effect_added_type_source_structuralized":
        "An effect-feed row classified merely as 'added' is admitted as a structural insert only when the exact affecting source provision resolves and carries a source-owned insert payload.",
    "uk_effect_direct_section_paragraph_target_normalized":
        "An affected ref of section-number plus an alphabetic bracket is normalized to a direct section paragraph rather than an alphabetic subsection.",
    "uk_effect_source_payload_sibling_range_expanded":
        "A metadata-compressed sibling target range is expanded to one target per source-owned BlockAmendment payload child.",
    "uk_effect_source_heading_facet_target_refined":
        "When source text explicitly targets a heading/title/sidenote facet but the feed names only the host provision, the target is refined to the typed facet instead of mutating body text.",
    "uk_effect_source_provision_order_normalized":
        "Effects sharing effective date, affected target, and affecting act are ordered by source provision citation rather than by opaque effect id.",
    "uk_effect_source_schedule_parent_payload_retargeted":
        "When the source payload carries an explicit Schedule wrapper but the feed target is a descendant, lowering retargets the source-claimed schedule shell rather than replaying the descendant at an unsafe fallback.",
    "uk_effect_chained_insertion_anchor_lowered":
        "When one insertion instruction expands into multiple sibling inserts, later ops are anchored after the prior generated target rather than the original source anchor.",

    # -- Inserted-provision heading-carrier preservation ------------------------
    "uk_effect_inserted_section_p1group_heading_carrier_lowered":
        "An inserted section payload wrapped by a P1group Title preserves that Title as a target-owned heading carrier instead of relying on a shared live parent group.",
    "uk_effect_inserted_p1group_heading_carrier_lowered":
        "An inserted non-section provision wrapped by a P1group Title preserves that Title as a target-owned heading carrier instead of relying on a shared live parent group.",

    # -- Heading / cross-heading facet lowering ---------------------------------
    "uk_effect_heading_facet_append_lowered":
        "A heading/title/sidenote target lowers to a typed facet append; replay mutates only the heading carrier.",
    "uk_effect_heading_facet_after_anchor_insert_lowered":
        "A heading/title/sidenote target lowers to a facet text insertion after an explicit heading anchor; replay mutates only the heading carrier.",
    "uk_effect_heading_facet_full_replacement_lowered":
        "A heading/title/sidenote target lowers to a full facet replacement; replay mutates only the heading carrier.",
    "uk_effect_heading_facet_word_patch_lowered":
        "A heading/title/sidenote target with no append/insert/replacement shape lowers to a facet word text patch; replay mutates only the heading carrier.",
    "uk_effect_heading_facet_source_parent_full_replacement_lowered":
        "When the source payload carries only inserted body provisions and its parent instruction carries the heading replacement, lowering mutates only the heading carrier.",
    "uk_effect_heading_facet_source_parent_full_replacement_text_patch":
        "A heading replacement resolved from a parent instruction (payload holds only inserted body) is lowered as a heading-facet text patch rather than a host-body mutation.",
    "uk_effect_crossheading_before_anchor_text_patch_lowered":
        "A cross-heading replacement is lowered as a typed heading-facet text patch anchored by the named following provision.",
    "uk_effect_crossheading_replace_rejected":
        "A cross-heading replacement target lacking an explicit heading-before-anchor replacement shape is refused as an unsupported target facet.",

    # -- Source-parent / source-carried text-patch context resolution -----------
    "uk_effect_grouped_anchor_occurrence_substitution_text_patch":
        "When a source child gives only the ordinal occurrence and its grouped parent carries the quoted anchor, lowering combines those source-local facts instead of guessing the anchor from live text.",
    "uk_effect_source_parent_following_provisions_substitution_text_patch":
        "When a source child enumerates a target provision and its parent carries the quoted substitution for the following provisions, lowering combines those facts and leaves target selection to the feed.",
    "uk_effect_source_carried_after_quoted_anchor_insert_text_patch":
        "When the source payload holds only the inserted text and the parent instruction names the quoted after-anchor, lowering combines those facts instead of guessing the anchor from live text.",
    "uk_effect_source_carried_quoted_text_substitution_text_patch":
        "When the source payload holds only the replacement text and the parent instruction names the quoted preimage, lowering combines those facts instead of guessing the old text from live state.",
    "uk_effect_source_carried_definition_entry_insert_text_patch":
        "When the source payload holds only the inserted definition entry and the parent instruction names the definition anchor, lowering combines those facts instead of guessing definition placement.",
    "uk_effect_source_carried_definition_entry_substitution_text_patch":
        "When the source payload holds only the replacement definition entry and the parent instruction names the definition being substituted, lowering combines those facts instead of guessing the old term.",
    "uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch":
        "An insert-after-quoted-anchor inside a named definition is scoped to that definition, with the anchor resolved from the parent source rather than guessed from live text.",
    "uk_effect_source_parent_definition_child_substitution_text_patch":
        "A substitution inside a definition's child paragraph is scoped to that child, resolved from the parent source instruction rather than guessed from live text.",

    # -- Occurrence-scoped / range / child text patches -------------------------
    "uk_effect_all_occurrences_substitution_text_patch":
        "An 'each time it appears' substitution rewrites the quoted preimage at every occurrence in the target scope.",
    "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch":
        "An 'after \"X\" insert Y' instruction with all-occurrences scope inserts Y after every occurrence of the quoted anchor X.",
    "uk_effect_labeled_child_end_range_text_patch":
        "A text range bounded from a parent text anchor to the end of a labelled child preserves the parent target and encodes the explicit child endpoint in the selector.",
    "uk_effect_child_qualified_final_word_omission_text_patch":
        "A word-level omission quoting the deleted final word at the exact child the feed selected lowers to a final-occurrence text patch without widening scope.",
    "uk_effect_corresponding_table_entry_word_substitution":
        "A table-driven word substitution is resolved by matching the affected provision to a unique source-table row (column-1 match, column-2 replacement).",

    # -- Repeal-table elaboration & unresolved refusals -------------------------
    "uk_effect_repeal_table_mixed_structural_word_repeal_split":
        "A repeal-table row naming both a structural target and an adjacent word deletion is split into separate typed operations so each mutation boundary stays owned.",
    "uk_effect_repeal_table_structural_repeal_unresolved":
        "A repeal-table structural repeal is refused when the source cannot be resolved to one exact structural extent row for the affected target.",
    "uk_effect_repeal_table_quoted_words_text_repeal_unresolved":
        "A repeal-table quoted-words text repeal is refused when the source cannot be resolved to one bounded quoted-words extent row for the affected target.",
    "uk_effect_repeal_table_definition_entry_text_repeal_unresolved":
        "A repeal-table definition-entry text repeal is refused when the source pseudo definition target does not uniquely name a definition entry in the owning provision.",

    # -- Metadata renumber transforms & cross-container refusal ------------------
    "uk_effect_metadata_renumber_lowered":
        "When effect metadata says a provision is renumbered as its own immediate descendant, lowering preserves that typed renumber instead of treating the row as nonstructural.",
    "uk_effect_metadata_sibling_renumber_lowered":
        "When effect metadata says a provision is renumbered as a same-parent sibling, lowering preserves a typed renumber instead of replaying it as a repeal of the destination label.",
    "uk_effect_metadata_cross_container_renumber_rejected":
        "A renumber that migrates a provision into a different top-level container is refused as a lineage/migration op until cross-container migration semantics are owned.",

    # -- Empty-effect-type source-action inference & out-of-scope refusals -------
    "uk_effect_empty_type_quoted_anchor_word_insertion_inferred":
        "A typeless effect whose source row explicitly inserts a target-local text fragment is treated as a source-owned text rewrite rather than a structural insertion.",
    "uk_effect_commencement_source_rejected":
        "A typeless effect whose source is a commencement instrument is refused, since structural replay must not synthesize a mutation from in-force language.",
    "uk_effect_empty_type_as_if_words_omitted_rejected":
        "A typeless effect using temporary 'shall have effect as if words were omitted' language is refused, since lowering must not infer a structural repeal of the broad affected provision.",

    # -- Unsupported target facet/scope & structural-shape refusals --------------
    "uk_effect_heading_only_ref_rejected":
        "An effect target naming only a heading or sidenote facet is refused, since lowering cannot safely mutate the host provision body.",
    "uk_effect_structural_pseudo_definition_target_rejected":
        "A definition entry encoded as a pseudo structural target path is refused, since replaying it as ordinary item/subparagraph structure needs a definition-entry compiler.",
    "uk_effect_whole_act_word_level_text_patch_rejected":
        "A word-level text patch pointed at the whole Act is refused, since lowering must not send a document-wide rewrite to ordinary replay without a whole-act text-patch compiler.",
    "uk_effect_overlap_substitution_unlowered":
        "A word-level overlap substitution lowers to no replay operations because the source instruction could not be parsed into a safe text patch.",
    "uk_effect_source_payload_without_instruction_context_rejected":
        "An extracted source that is a payload fragment carrying no operative action word is refused, since replaying it as a broad text patch would be unsafe.",
    "uk_effect_structural_sibling_insert_rejected":
        "An insert of structural siblings after a named child is refused when lowering cannot prove the parent, anchor, and inserted-child payload shape.",
    "uk_effect_appropriate_place_insert_rejected":
        "An 'insert at an appropriate place' instruction naming no anchor or ordering rule is refused, since lowering must not infer the insertion point from live text or oracle order.",
    "uk_effect_appropriate_place_definition_entry_insert_rejected":
        "An 'insert a definition entry at an appropriate place' instruction naming no anchor is refused, since lowering must not infer the insertion point from live text or oracle order.",
    "uk_effect_external_act_target_rejected":
        "When effect metadata points at the current Act but the affecting source text names a different Act as the target, the effect is refused rather than mutating the in-scope Act.",

    # -- Effect-feed acquisition diagnostic --------------------------------------
    "uk_effect_feed_empty_recorded":
        "A UK effects-feed page that contained no Atom entries is recorded as a non-blocking acquisition observation.",

    # -- Residual corpus-bench gap (ids observed firing uncataloged in the
    # -- 40-statute -j uk --corpus-bench ledger run) ------------------------------
    "uk_effect_crossheading_insert_rejected":
        "A UK effect inserting a cross-heading facet whose source carries no standalone cross-heading payload (a Pblock with a Title) is refused, since coercing the heading instruction into a body provision insert would corrupt structure.",
    "uk_effect_broad_container_repeal_table_feed_descendant_repeal":
        "When a UK repeal-table source names a whole-container repeal and the feed row explicitly targets a descendant inside that container, lowering emits a source-backed structural repeal of the feed target without requiring a unique exact row match.",
    "uk_effect_source_payload_instruction_context_augmented":
        "A UK extracted source that is a bare payload fragment with no amendment verb is parsed after prepending its parent amendment-container instruction, so the fragment lowers to a typed text patch; recorded as a non-blocking observation.",
    "uk_effect_body_section_replace_schedule_unmatched_rejected":
        "A structural replace targeting a body section whose extracted Schedule payload contains no section-like unit (Section/P1/Article/Rule) with a matching label is refused, since the effect is not a genuine section replacement and applying it would destroy the target carrier.",
    "uk_effect_incorporation_of_enactments_source_rejected":
        "A UK effect with no explicit text/tree action whose source is an Order's 'the following provisions ... shall be incorporated in this Order' enactments-uptake article is refused, since the Order incorporates the host provisions into its own scheme rather than amending them.",
}
