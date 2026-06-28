"""Estonia (EE) believed_spec catalog — the discovered-spec hypotheses, one per rule.

This is a standalone, import-light sibling of ``spec_ledger.py``'s ``_FI_RULE_SPECS``.
The main ledger's EE adapter will guard-import ``_EE_RULE_SPECS`` once its dispatch is
generalized; nothing here imports the estonia frontend, so it carries no heavy deps and
stays conflict-free with parallel ``spec_ledger.py`` edits.

Voice and contract (see ``spec_ledger.py`` and notes_internal/SPEC_DISCOVERY_DESIGN.md):
each entry is a one-line, falsifiable hypothesis about the *legal-amendment semantics*
the witness rule encodes — the believed spec the compiler is testing against the
authoritative Riigi Teataja consolidation. EE replays as consistency verification
against authoritative consolidated law, so these describe genuine amendment semantics,
not editorial conventions. Grounded in the rule-emitting code under
``src/lawvm/estonia/`` and, where it maps, the matching rule in
``notes/ESTONIA_FRONTEND_LIVING_SPEC.md`` (cited as "living spec §N").

Coverage is anti-drift-guarded by ``tests/test_spec_ledger_ee_catalog.py``: every
statically discoverable EE ``witness_rule_id`` constant must have a non-empty entry,
and every key here must map to a real constant (no dead entries).

Honest scope note — what is and is not statically enumerable.

* Many EE call sites pass ``witness_rule_id=rule_id`` / ``rule_id=...`` where ``rule_id``
  is selected from a table or branch rather than a single module-level constant. Each
  such id still resolves to a fixed ``"ee_…"`` string literal in the estonia source, so
  the literal *is* statically enumerable and is catalogued. The test discovers the
  authoritative set as every ``"ee_…"`` string literal under ``src/lawvm/estonia/``.

* Two ``"ee_…"`` literals are deliberately NOT rule ids and are excluded from the
  coverage denominator:
    - ``ee_riigiteataja.farchive`` — the default archive *filename* (the ``.`` is the
      tell), in ``estonia/fetch.py``;
    - ``ee_snap_`` — a *dynamically constructed* replay ``op_id`` prefix in
      ``estonia/grafter.py`` (``f"ee_snap_{op.sequence}[_{label}]"``). This is the one
      genuine prefix+suffix-concatenated id family in EE; it identifies a snapshot op
      instance, not a believed-spec hypothesis, so we cover the stable ``ee_snap_``
      prefix as a documented exclusion rather than fabricating per-instance entries.

Every other ``"ee_…"`` literal maps to exactly one believed-spec hypothesis here.
"""
from __future__ import annotations

from typing import Dict

# Believed-spec hypothesis per EE witness_rule_id.  Keys are the literal rule-id strings
# bound to ``_EE_*_RULE`` constants (or passed inline as ``witness_rule_id=``) across
# ``src/lawvm/estonia/``.  Each value is a falsifiable one-line claim about the law.
_EE_RULE_SPECS: Dict[str, str] = {
    # --- Section/subsection/item sequence renumber before structural insert -----------
    "ee_section_sequence_renumber_before_insert": "Inserting a section at an occupied label renumbers the displaced successor sections upward before the insert.",
    "ee_subsection_sequence_renumber_before_insert": "Inserting a subsection at an occupied label renumbers the displaced successor subsections upward before the insert.",
    "ee_senine_text_subsection_renumber_before_insert": "A 'senine tekst loetakse lõikeks N' clause renumbers the existing subsection body to N before the new subsection is inserted.",
    "ee_item_renumber_before_replace": "An item replace that displaces existing labels renumbers the affected sibling items before the replacement lands.",
    "ee_implicit_division_sequence_relabel_after_high_jagu_insert": "Inserting a high-numbered 'jagu' division implicitly relabels the existing division sequence to keep ordinal continuity.",

    # --- Section intro / first-subsection attachment ----------------------------------
    "ee_section_intro_replace_to_first_subsection": "A section-level intro replace targets the section's first subsection body, not the whole section.",
    "ee_section_level_intro_attached_to_first_subsection": "A list intro serialized under paragrahv/sisuTekst belongs to the first subsection, not as section-level text.",
    "ee_section_level_reavahetus_items_attached_to_first_subsection": "Direct section-level line-break (reavahetus) items attach to the first subsection only when labels are explicit and non-colliding (living spec §63).",
    "ee_unlabeled_loige_continuation_attached_to_previous_subsection": "An unlabeled trailing lõige continuation is the tail of the preceding subsection, not a new subsection.",

    # --- Flat / sectionless singleton regulation scope --------------------------------
    "ee_flat_sectionless_singleton_item_insert": "A 'täiendatakse punktiga N' clause with no named section inserts the item under the sole section's first subsection (living spec §55).",
    "ee_flat_sectionless_singleton_item_repeal": "A sectionless item repeal in a singleton regulation removes the item under the only section's first subsection.",
    "ee_flat_sectionless_singleton_subsection_scope": "A sectionless subsection/item clause in a one-section regulation recovers the omitted singleton section/subsection path (living spec §55).",
    "ee_singleton_empty_section_label_to_1": "A regulation's sole top-level paragrahv with an empty label is the same unit later consolidated as § 1 (living spec §54).",

    # --- Payload / quote-marker extraction --------------------------------------------
    "ee_payload_after_marker_ignores_premarker_title_quote": "A quoted title before the operative marker is target scope, not replacement payload; the payload starts after the marker.",
    "ee_ascii_quoted_marker_payload": "ASCII-quoted amendment payloads are read with the same marker semantics as Estonian „…“ quotes.",
    "ee_plural_item_payload_outer_quote_tail_stripped": "A plural-item payload's outer wrapping quote tail is presentation, stripped before the inner item content is materialized.",
    "ee_plural_item_marker_payload_recovers_inner_quote": "A plural-item marker payload recovers its inner quoted item text rather than treating the outer quote as the whole payload.",

    # --- Plural / multi-target inserts and replaces -----------------------------------
    "ee_plural_item_replace_missing_label_repeal": "A plural item replace whose payload omits a previously listed label repeals that absent item.",
    "ee_plural_item_replace_range_omits_inserted_labels": "A plural item replace stated as a range does not silently overwrite later inserted (superscript) labels inside that range.",
    "ee_plural_subsection_insert_payload_split": "A plural-subsection insert whose payload carries several numbered blocks splits into one inserted subsection per block (living spec §1).",
    "ee_plural_subsection_replace_extra_payload_label": "A plural-subsection replace whose payload adds a label beyond the targets materializes the extra block as a new sibling subsection.",
    "ee_plural_subsection_insert_after_each_surface": "A plural-subsection insert-after clause fans the inserted surface out to each inherited subsection under the same section (living spec §7).",
    "ee_plural_section_insert_payload_split": "A plural-section insert whose payload carries several § blocks splits into one inserted section per block.",
    "ee_inline_item_replace_singleton_subsection": "An inline item replace under a singleton subsection resolves to that subsection's only item.",
    "ee_insert_multi_explicit_targets": "A single insert clause naming several explicit targets fans out to an insert at each named target.",
    "ee_insert_multi_explicit_targets_payload_label_filter": "A multi-target insert filters the shared payload to the labels actually addressed by each explicit target.",
    "ee_multi_target_replace_shared_payload": "A replace clause naming several targets applies the one shared payload to each named target.",
    "ee_multi_target_text_delete_split": "A text-delete clause naming several targets splits into one delete operation per target.",
    "ee_mixed_multi_section_replace_payload_split": "A multi-section replace splits its combined payload back onto the respective section targets.",
    "ee_mixed_multi_target_insert_after_and_replace": "A clause mixing insert-after and replace across targets fans out each verb to its own target.",

    # --- Mixed same-target verb combinations ------------------------------------------
    "ee_mixed_delete_and_replace_same_target": "A clause stating both a delete and a replace on one target emits both ops against that same target.",
    "ee_mixed_replace_and_insert_after_same_target": "A clause stating both a replace and an insert-after on one target emits both ops against that same target.",
    "ee_mixed_sentence_replace_and_insert_same_target": "A clause combining a sentence replace and an insert on one target emits both, scoped to that target.",
    "ee_mixed_text_replace_and_sentence_replace_same_target": "A clause combining a word-level text replace and a sentence replace on one target keeps both rewrites distinct.",
    "ee_mixed_insert_after_and_delete_same_target": "A clause combining an insert-after and a delete on one target emits both rewrites against that target.",
    "ee_repeated_insert_after_same_target": "Repeated insert-after clauses on one target each append at their own anchor rather than collapsing to one insert.",
    "ee_mixed_repeal_trailing_singular_subsection": "A mixed repeal clause's trailing singular subsection target is repealed, not absorbed into the earlier item repeal (living spec §30).",

    # --- Structural / whole-container repeal lists ------------------------------------
    "ee_explicit_mixed_structural_repeal_list": "An explicit repeal list mixing item/subsection/section families fans out a repeal for every named structural target (living spec §12).",
    "ee_flat_part_repeal_span": "A flat part-level repeal span removes the whole addressed part subtree as a single structural repeal.",
    "ee_compound_section_item_subsection_repeal": "A compound clause naming a section item and a subsection repeals both the item and the subsection (living spec §30).",
    "ee_cross_act_transitional_section_repeal": "A cross-act transitional repeal helper handles only act-named whole-section lists, never mixed section/subsection clauses (living spec §69).",

    # --- Renumber / move semantics ----------------------------------------------------
    "ee_chapter_heading_insert_after_section": "A chapter-heading insert positioned after a section inserts the heading at that boundary without absorbing the section.",
    "ee_structural_textosa_heading_relabel": "A structural 'tekstiosa'/heading relabel renames the container heading without mutating its child provisions.",

    # --- Text replace: anchors, synonyms, scope ---------------------------------------
    "ee_text_replace_after_anchor_clause": "A 'muudetakse ja pärast sõna … asendatakse' clause is a text replace on the target, with the anchor only locating the phrase (living spec §73).",
    "ee_peale_sona_insert_after_synonym": "'peale sõna' is a synonym of 'pärast sõna': a bounded insert-after text rewrite on the explicit target, not a structural item insert (living spec §70).",
    "ee_insert_after_terminal_punctuation_boundary": "An insert-after at a terminal-punctuation boundary inserts before the terminator rather than after the item ends.",
    "ee_insert_item_terminal_normalized_by_position": "An inserted item's terminal punctuation is normalized by its list position (e.g. ';' mid-list, '.' last).",
    "ee_insert_after_source_phrase_surface_variants": "An insert-after anchor may match a bounded morphology surface variant of the quoted source phrase on the exact target (living spec §51).",
    "ee_ambiguous_single_occurrence_text_replace": "A single-occurrence insert/replace whose anchor word repeats in the target is blocked as source-ambiguous rather than resolved by match order (living spec §49).",
    "ee_target_scoped_many_old_single_new_text_replace": "A target-scoped 'sõnad OLD1 ja OLD2 sõnaga NEW' clause splits to OLD1→NEW and OLD2→NEW within that target (living spec §3).",
    "ee_unscoped_many_old_single_new_text_replace": "An unscoped 'sõnad OLD1 ja OLD2 sõnaga NEW' clause splits to OLD1→NEW and OLD2→NEW statute-wide (living spec §3).",
    "ee_plural_section_scope_text_replace": "A text replace scoped to several sections fans the rewrite out across each named section.",
    "ee_plural_section_text_replace_preserve_later_explicit_targets": "A plural-section text replace preserves later explicit targets in the clause instead of stopping at the first.",
    "ee_text_replace_numbered_subsection_for_item_target_by_old_text": "An item-target text replace recovers to the numbered subsection bearing the old text when no item carries it.",
    "ee_text_replace_unique_descendant_item_by_old_text": "A text replace recovers to the unique descendant item carrying the quoted old text under the named target.",
    "ee_section_item_replace_unique_descendant_item": "A 'section:item' replace recovers only to a section's unique descendant item with that label, else stays unresolved (living spec §76).",
    "ee_text_replace_quoted_legal_title_protection": "A quoted legal title inside replacement payload is protected from being treated as the replaced old text.",
    "ee_textual_invalidation_as_text_delete": "A textual-invalidation clause ('jäetakse välja') lowers to a text delete, not a structural repeal.",
    "ee_direct_title_global_text_replace": "A direct-title clause stating a global rewrite compiles to a statute-wide text replace on the named act.",
    "ee_global_text_replace_statute_and_annex_scope": "A statute-wide 'tekstis' text replace also rewrites the act's annexes in scope.",
    "ee_global_text_replace_statute_and_annex_heading_scope": "A statute-and-annex text replace extends to annex headings as well as annex body text.",
    "ee_global_title_text_rewrite_no_payload_composition": "A global title rewrite uses typed text-rewrite semantics and does not compose a structural payload.",
    "ee_statute_title_replace": "A statute-title clause replaces the act title surface without inventing a provision address.",
    "ee_statute_title_text_delete": "A 'pealkirjast jäetakse välja' clause deletes title-surface text recorded as a text rewrite, not a provision op (living spec §58).",

    # --- Source-surface typo / case variants ------------------------------------------
    "ee_source_typo_text_replace_near_match": "A text replace whose source old-text near-matches the live surface applies on the bounded near-match rather than no-oping.",
    "ee_source_case_only_text_replace": "A text replace differing from the live surface only by letter case applies despite the case-only mismatch.",
    "ee_source_case_suffix_text_replace": "A text replace differing only by a case-suffix surface variant applies on that bounded variant.",
    "ee_fraktsioneeritud_source_typo_delete_variant": "A bounded 'fraktsioneeritud' source-typo surface variant is recognized for the targeted text delete.",
    "ee_lokaal_kohtkute_source_surface_delete_variant": "A bounded 'lokaal/kohtküte' source-surface variant is recognized for the targeted text delete.",

    # --- Subsection table-only replace ------------------------------------------------
    "ee_subsection_table_only_replace_preserve_intro": "A subsection replace whose payload is table-only replaces the table while preserving the subsection intro.",
    "ee_replace_subsection_intro_only": "An intro-only subsection replace rewrites the subsection intro and leaves its child items live.",
    "ee_lahter_text_replace": "A 'lahter' (table-cell) text replace rewrites only the addressed cell text.",
    "ee_replace_lahter_text": "A table-cell ('lahter') replace materializes the cell payload without disturbing sibling cells.",

    # --- Item replacement payload selection / guards ----------------------------------
    "ee_explicit_item_replacement_terminal_preserved": "An item replace whose quoted payload carries its own terminal punctuation preserves that terminal over list normalization (living spec §52).",
    "ee_labelled_item_replacement_payload_selection": "A labelled item replace selects the payload block matching the item label rather than the whole quoted payload.",
    "ee_inline_item_parentheses_marker_guard": "Parenthesized inline item markers are guarded from being misread as new item boundaries.",
    "ee_overbroad_container_replace_blocked": "A replace that would overwrite a whole container from a child-scoped payload is blocked as overbroad.",

    # --- Normitehniline märkus / EU-marker anchors ------------------------------------
    "ee_normitehniline_markus_insert_after_anchor": "A 'normitehniline märkus' note is inserted after its anchor clause as a non-body note, not a body mutation.",
    "ee_normitehniline_markus_optional_eu_marker_anchor": "A normitehniline märkus insert tolerates an optional EU-directive marker in its anchor.",

    # --- Act-citation / quoted-act targets --------------------------------------------
    "ee_act_citation_section_insert_target": "A cited-act '§-ga N' insert keeps the explicit section target over internal §-references in the inserted body (living spec §61).",
    "ee_quoted_act_chapter_insert_target": "A cited-act 'peatükiga' insert keeps the explicit chapter target over payload-local § and (N) markers (living spec §64).",
    "ee_nested_direct_target_law_clause": "A nested direct-target law clause resolves the target to the cited inner act, not the wrapping instruction.",
    "ee_nested_direct_target_law_clause_header_carry": "A nested direct-target law clause carries the cited act header scope to its following operative clause.",

    # --- HTML / table materialization -------------------------------------------------
    "ee_html_table_text_materialized": "An HTML table in source is materialized as the provision's table text, not dropped as transport markup.",
    "ee_html_table_numbered_items_materialized": "Numbered items inside an HTML table are materialized as real item nodes.",
    "ee_html_paragraph_numbered_items_materialized": "Numbered items inside an HTML paragraph stream are materialized as real item nodes.",
    "ee_plain_paragraph_html_items_extracted": "Numbered items in a plain HTML paragraph are extracted as item children of the host provision.",
    "ee_parenthesized_target_html_block_sliced": "A parenthesized target inside an HTML block is sliced out as its own addressed unit.",

    # --- Drop / cleanup of source residue ---------------------------------------------
    "ee_drop_orphan_appendix_marker_html": "An orphan HTML appendix marker with no appendix body is dropped as transport residue.",
    "ee_drop_repealed_range_residue": "Residue left by an already-repealed range is dropped as cleanup, not preserved as live text.",
    "ee_drop_loike_tekst_placeholder": "A 'lõike tekst' placeholder with no real content is dropped rather than materialized as a subsection.",
    "ee_spaced_superscript_subsection_marker": "A spaced superscript subsection marker (e.g. '2 1') is normalized to the superscript subsection label '2_1'.",

    # --- Optional label spacing -------------------------------------------------------
    "ee_optional_target_label_space": "A target label tolerates optional internal spacing (e.g. '§ 54 12' = '§ 54^12') without changing the addressed unit.",

    # --- Section heading + pealkiri rewrites ------------------------------------------
    "ee_section_heading_and_text_replace_split": "A clause changing both a section heading and its text splits into a heading op and a text op.",
    "ee_section_heading_pealkiri_asendatakse_pealkirjaga": "A 'pealkiri asendatakse pealkirjaga' clause replaces only the section heading surface.",
    "ee_repeated_section_heading_body_split": "A repeated section-heading-plus-body shape splits the heading from the body so neither absorbs the other.",

    # --- Compound subsection intro + item ---------------------------------------------
    "ee_compound_subsection_intro_and_item_replace": "A compound clause replacing a subsection intro and an item emits both rewrites under that subsection.",

    # --- Source-local global text replace composition ---------------------------------
    "ee_source_local_global_text_replace_payload_composition": "A source-local global text replace composes its payload from the in-clause old/new surfaces.",
    "ee_source_local_global_text_replace_selector_composition": "A source-local global text replace composes its selector from the in-clause explicit target list.",
    "ee_source_local_global_text_replace_selector_composition_skipped_for_excluded_target": "A source-local global selector composition skips a structurally excluded target named in the source.",
    "ee_source_local_global_text_replace_selector_exclusion_inferred": "An exclusion in a source-local global text replace is inferred from the clause's 'välja arvatud' surface.",
    "ee_source_local_global_text_replace_payload_authors_rename_target_surface_skipped": "A source-local global text replacement that would only rename the target surface is recorded as skipped instead of emitting an unsupported global mutation.",
    "ee_source_local_payload_composition_quoted_title_skipped": "A quoted legal title inside a source-local payload composition is skipped, not folded into the rewrite surface.",
    "ee_generic_ministry_reorganization_explicit_exceptions": "An inferred global ministry-reorganization rewrite carries the source's explicit exception paths and never mutates them (living spec §48).",

    # --- Direct-title / old-format direct-title rewrites ------------------------------
    "ee_direct_target_title_prefix_stripped_for_structural_repeal": "A direct-target title prefix is stripped before a structural repeal so the repeal addresses the provision, not the title.",
    "ee_old_format_direct_target_title_prefix_stripped_before_carry": "An old-format direct-target title prefix is stripped before section scope is carried forward.",
    "ee_old_format_direct_title_case_inflected_text_replace": "An old-format direct-title wrapper body is a whole-regulation case-inflected text rewrite once the header is admitted (living spec §58.1).",
    "ee_old_format_direct_title_unnumbered_text_replace": "An old-format unnumbered direct-title body clause compiles to a whole-regulation text replace.",
    "ee_old_format_direct_header_target_section": "An old-format direct header names the target section directly without a wrapper instruction.",

    # --- Old-format wrapper / carried scope -------------------------------------------
    "ee_old_format_carried_section_scope": "An old-format clause inherits section scope from the surrounding amendment context when not restated.",
    "ee_old_format_wrapper_scope_inherited": "An admitted old-format wrapper section's inner clause inherits whole-regulation scope from the wrapper header (living spec §58).",
    "ee_old_format_container_heading_target_blocks_section_carry": "An old-format container-heading target blocks section-scope carry into the following clause.",

    # --- Old-format HTML section vs preambul recovery ---------------------------------
    "ee_old_format_html_section_preferred_over_preambul_plain_body": "An old-format flat HTML amendment section wins over preambul recovery when it yields strictly more substantive ops (living spec §50).",
    "ee_old_format_html_section_richer_payload_preferred": "Between competing old-format parses, the HTML section with the richer materialized payload is preferred.",
    "ee_old_format_numbered_items_preferred_over_preambul_recovery": "Old-format numbered-item extraction beats preambul recovery when both produce duplicate body mutations for one target (living spec §67).",

    # --- Out-of-body appendix / preamble / non-body lanes -----------------------------
    "ee_preamble_clause_non_body": "A preamble clause is classified as non-body evidence, not a body mutation.",
    "ee_old_format_preamble_clause_non_body": "An old-format preamble clause is non-body evidence and is not replayed as a provision change.",
    "ee_out_of_body_appendix_clause_not_section_scoped": "An out-of-body appendix clause is meta, not scoped to the previous body section (living spec §68).",
    "ee_old_format_out_of_body_appendix_clause_not_section_scoped": "An old-format out-of-body appendix clause is meta and not carried onto the last body section (living spec §68).",
    "ee_out_of_body_appendix_or_note_clause": "Out-of-body appendix/note clauses stay visible as META operations with source-family evidence, never dropped (living spec §78).",
    "ee_appendix_addition_not_body_replay": "An appendix addition is a non-body meta op and is not replayed as a body mutation.",

    # --- Title / target-mismatch and rejection lanes ----------------------------------
    "ee_title_clause_unresolved_non_body": "A title clause that resolves to no body target is preserved as unresolved non-body evidence.",
    "ee_new_format_op_text_target_title_mismatch": "A new-format op whose text target-title mismatches the routed act is flagged rather than silently retargeted.",
    "ee_new_format_target_act_header_not_wrapper_instruction": "A new-format target-act header is target identity, not a wrapper instruction to execute.",
    "ee_html_amendment_section_heading_wrapper_stripped": "An HTML amendment section-heading wrapper is stripped so the inner operative clause is parsed.",
    "ee_embedded_open_quote_payload_section_header": "An embedded section header inside an open quoted chapter payload stays in the same amendment item (living spec §65).",
    "ee_old_format_open_quote_payload_section_header": "An old-format payload section header inside an open quote stays in the item and does not split the section (living spec §66).",
    "ee_plaintext_old_format_target_section_filter": "A plaintext old-format body filters its clauses to those targeting the addressed section.",
    "ee_direct_html_single_instruction_body": "A direct HTML body carrying a single instruction is parsed as that one operative clause.",
    "ee_unstructured_single_clause_amendment_body": "An unstructured single-clause amendment body is lowered as one operative clause, not dropped for lacking item markup.",

    # --- Unparsed / unsupported coverage debt -----------------------------------------
    "ee_unparsed_operation_clause": "An unclassifiable source instruction is preserved as a META coverage-debt carrier, not silently dropped (living spec §79).",
    "ee_old_format_unparsed_meta_rejected": "An old-format clause that cannot be parsed into a body op is rejected as meta with a recorded reason.",
    "ee_plaintext_numbered_clause_split": "A flat plaintext body splits into its numbered clauses, each recognized as a separate operative instruction (living spec §71.1).",

    # --- Replay-time skip / noop / unsupported markers (replay.py) ---------------------
    "ee_replay_meta_non_body_skipped": "A clause classified as non-body meta is skipped at replay with a meta-skip record, not failed as unsupported (living spec §78).",
    "ee_replay_unparsed_operation_skipped": "An unparsed operation clause is skipped at replay and surfaced as a coverage gap, distinct from a meta skip (living spec §79).",
    "ee_replay_noop": "An operation whose live target already matches the payload is recorded as a no-op rather than a spurious mutation.",
    "ee_replay_statute_title_noop": "A statute-title op already satisfied by the live title is recorded as a no-op.",
    "ee_replay_target_not_found": "An operation whose target is absent from the live tree is recorded as target-not-found, never rerouted to a coincidental candidate.",
    "ee_replay_unsupported_action": "An operative body action the replay kernel cannot execute is recorded as unsupported rather than approximated.",
    "ee_replay_unsupported_heading_target": "A heading-target action the replay kernel cannot execute is recorded as unsupported.",
    "ee_replay_unsupported_statute_title_action": "A statute-title action the replay kernel cannot execute is recorded as unsupported.",
    "ee_replay_skipped_unspecified": "A replay op skipped without a matching typed skip adjudication is rejected with an explicit unspecified-skip receipt rather than disappearing.",

    # --- §1.7 same-moment cross-act conflict (pre-pass in ordering.py) ---------------
    "ee_same_moment_cross_act_incompatible_payload_ambiguous": "Two affecting acts that change the same target at the same effective date with incompatible whole-target payloads are surfaced as a §1.7 ambiguity (sequence-order pick unproven) until a precedence claim proves which act prevails; apply order is unchanged, the finding makes the silent pick strict-rejectable.",

    # --- Temporal / commencement provenance -------------------------------------------
    "ee_old_format_commencement_whole_act_default": "An unstamped old-format op inherits the whole-act commencement default only when that default is the active reference slice (living spec §77).",
    "ee_old_format_commencement_section_effective": "An old-format op stamped by a section-specific commencement clause carries that section's effective date (living spec §77).",
    "ee_old_format_commencement_item_effective": "An old-format op stamped by an item-specific commencement clause carries that item's effective date (living spec §77).",
    "ee_pending_amendment_text_precompose": "A pending amendment's text is precomposed into the base before its commencement so later replay sees the staged surface.",
    "ee_pending_source_act_commencement_precompose": "A pending source act's commencement is precomposed so dependent amendments replay against the staged effective state.",

    # --- Reference-slice / cancelled-pending filtering (visible rejections) -----------
    "ee_ref_slice_operation_filtered": "An operation outside the active reference slice is filtered out with a visible rejection record.",
    "ee_cancelled_pending_amendment_ref_filtered": "A reference to a cancelled pending amendment is filtered out with a visible rejection record.",

    # --- Source-acquisition / parse failure provenance (failed-op lanes) --------------
    "ee_amendment_source_fetch_failed": "A failure to fetch an amendment's source is recorded as a visible failed-op, not a silent skip.",
    "ee_amendment_parse_failed": "A failure to parse a fetched amendment is recorded as a visible failed-op.",
    "ee_pending_amendment_metapass_parse_failed": "A parse failure during the pending-amendment metapass that re-reads future-oracle amendments to live-update still-targeted text is recorded as a visible non-blocking adjudication rather than silently swallowed.",
    "ee_extract_act_title_parse_failed": "A parse failure in the act-title prefilter helper (used to look up titles for cancelled-pending-ref and metapass matching) is recorded as a visible non-blocking adjudication instead of returning an empty string silently.",
    "ee_extract_target_matching_paragraphs_parse_failed": "A parse failure in the target-matching-paragraph-numbers prefilter helper (used by the cancelled-pending-ref filter) is recorded as a visible non-blocking adjudication instead of returning an empty set silently.",
    "ee_extract_repealed_source_paragraphs_parse_failed": "A parse failure in the repealed-source-paragraph-numbers prefilter helper (used by the cancelled-pending-ref filter to detect source-law repeals before commencement) is recorded as a visible non-blocking adjudication instead of returning an empty set silently.",
    "ee_extract_rewritten_source_paragraphs_parse_failed": "A parse failure in the rewritten-source-paragraph-numbers prefilter helper (used by the cancelled-pending-ref filter to detect source-law rewrites before commencement) is recorded as a visible non-blocking adjudication instead of returning an empty set silently.",
    "ee_temporal_source_scan_failed": "A failure to scan an act for temporal/commencement data is recorded as a visible failed-op.",
    "ee_cancelled_pending_ref_source_fetch_failed": "A failure to fetch a cancelled-pending reference's source is recorded as a visible failed-op.",
    "ee_cancelled_pending_ref_metadata_parse_failed": "A failure to parse cancelled-pending reference metadata is recorded as a visible failed-op.",
    "ee_pending_source_act_commencement_source_fetch_failed": "A failure to fetch a pending source act's commencement source is recorded as a visible failed-op.",

    # --- RT XML muutmismärge (change-note) repair / parse provenance -------------------
    "ee_muutmismarge_aktviide_publication_number_repair": "A muutmismärge act-reference with a wrong publication number is repaired against the registry rather than dropped.",
    "ee_muutmismarge_aktviide_publication_year_repair": "A muutmismärge act-reference with a wrong publication year is repaired against the registry.",
    "ee_muutmismarge_aktviide_repair_candidate_unavailable": "A muutmismärge act-reference repair is abandoned, visibly, when no registry candidate is available.",
    "ee_muutmismarge_publication_number_repair_xml_parse_failed": "A muutmismärge publication-number repair records an XML parse failure rather than guessing.",
    "ee_rt_xml_metadata_parse_failed": "An RT XML metadata parse failure is recorded as a visible source-lane failure.",
    "ee_rt_xml_muutmismarge_missing_aktviide": "A muutmismärge missing its act-reference is recorded as a source pathology, not inferred.",
    "ee_rt_xml_muutmismarge_empty_normalized_aktviide": "A muutmismärge whose act-reference normalizes to empty is recorded as a source pathology.",
    "ee_rt_xml_muutmismarge_missing_avaldamismarge": "A muutmismärge missing its publication mark is recorded as a source pathology.",

    # --- Constitutional-review parse rejections ---------------------------------------
    "ee_parse_constitutional_review_rejected": "A constitutional-review instruction failing its parse preconditions is rejected with a recorded reason.",
    "ee_parse_constitutional_review_missing_invalidation_trigger": "A constitutional-review clause lacking an invalidation trigger is rejected, not treated as a repeal.",
    "ee_parse_constitutional_review_missing_target_title": "A constitutional-review clause lacking a target title is rejected.",
    "ee_parse_constitutional_review_registry_mismatch": "A constitutional-review clause whose target fails registry identity is rejected.",
    "ee_parse_constitutional_review_target_sentence_unmatched": "A constitutional-review clause whose target sentence does not match the live text is rejected.",
    "ee_parse_constitutional_review_target_title_mismatch": "A constitutional-review clause whose target title mismatches is rejected.",

    # --- Preambul single-target parse rejections --------------------------------------
    "ee_parse_preambul_single_target_rejected": "A preambul single-target recovery failing its preconditions is rejected with a recorded reason.",
    "ee_parse_preambul_single_target_empty_intro": "A preambul single-target recovery is rejected when its intro is empty.",
    "ee_parse_preambul_single_target_registry_mismatch": "A preambul single-target recovery is rejected on registry-identity mismatch.",
    "ee_parse_preambul_single_target_title_mismatch": "A preambul single-target recovery is rejected on target-title mismatch.",

    # --- Other parse rejections / meta --------------------------------------------------
    "ee_parse_new_format_op_text_rejected": "A new-format op whose text fails parse preconditions is rejected with a recorded reason.",
    "ee_parse_old_format_unparsed_meta_rejected": "An old-format clause that stays unparsed is rejected as meta with a recorded reason.",

    # --- Algtekst probe (source boundary diagnostics) ---------------------------------
    "ee_algtekst_probe_not_requested": "An algtekst (original-text) probe is skipped when not requested, recorded so the absence is explicit.",
    "ee_algtekst_probe_no_match": "An algtekst probe that finds no matching original boundary is recorded as a no-match, not assumed.",
    "ee_algtekst_probe_boundary_invalid": "An algtekst probe yielding an invalid boundary is recorded as invalid rather than used.",

    # --- Pair / oracle classification + feed-fetch lanes ------------------------------
    "ee_pair_classification": "An explicit base/oracle pair carries a classification of how replay and oracle differ on that pair.",
    "ee_oracle_fetch_failed": "A failure to fetch the oracle consolidation is recorded as a visible failure, not a silent pass.",
    "ee_spec_ledger_fetch_rt_xml": "A spec-ledger RT XML fetch failure while resolving an oracle as-of date is emitted as a named source-lane finding rather than swallowed to an empty date silently.",
    "ee_oracle_ref_extraction_failed": "An unexpected crash while extracting amendment references from the oracle fails loud (EEOracleRefExtractionCrash, embedding oracle id + source snippet) rather than degrading to an empty oracle.",
    "ee_oracle_parse_failed": "A failure to parse the RT oracle consolidation is recorded as a visible blocking adjudication (oracle id + exception + source snippet) so a replay left uncompared is never read as agreement.",
    "ee_consistency_check_failed": "A crash in the replay/oracle consistency check is recorded as a visible blocking adjudication so an uncompared result (no divergences computed) is never read as agreement.",
    "ee_oracle_group_mismatch": "An oracle group that does not align with the replayed group is recorded as a mismatch.",
    "ee_redactions_feed_fetch_failed": "A failure to fetch the redactions feed is recorded as a visible source-lane failure.",
    "ee_pit": "A point-in-time materialization marker scopes the EE replay/compare to the oracle redaction's own effective date (living spec §31).",

    # --- Case-inflected morphology families (text_morphology.py) ----------------------
    "ee_case_inflected_ametikoht_teenistuskoht_forms": "The 'ametikoht/teenistuskoht' noun family is rewritten in all case forms for a vastavas-käändes rename.",
    "ee_case_inflected_volitatud_vastutav_forms": "The 'volitatud/vastutav' participial phrase family is rewritten in all case forms for a vastavas-käändes rename.",
    "ee_case_inflected_taotlusvoor_coordination_forms": "The 'taotlusvoor' coordinated-phrase family is rewritten in all case forms across each coordinated segment.",
    "ee_case_inflected_mixed_acronym_suffix_case": "A mixed acronym-plus-case-suffix surface is rewritten with the case suffix agreeing with the new term.",
    "ee_case_inflected_neto_omavahend_prefix_forms": "The 'neto-/omavahend' prefixed noun family is rewritten in all case forms for a vastavas-käändes rename.",
    "ee_case_inflected_kysk_riigi_tugiteenuste_keskus_forms": "The 'Riigi Tugiteenuste Keskus' agency family is rewritten in all case forms for a vastavas-käändes rename.",
    "ee_case_inflected_aruanded_aruanne_forms": "The 'aruanded/aruanne' noun family is rewritten in all case forms for a vastavas-käändes rename.",
    "ee_case_inflected_aruanded_heading_agreement": "An 'aruanded' rewrite also enforces case/number agreement in the affected heading.",
    "ee_case_inflected_olemasolev_tahkel_kutusel_phrase_forms": "The 'olemasolev tahkel kütusel põhinev kütteseade' phrase family is rewritten in all case forms.",
    "ee_case_inflected_riiklik_register_infosusteem_forms": "The 'riiklik … register' → 'sotsiaalkaitse infosüsteem' family rewrites the witnessed illative forms, not invented '-registerit' surfaces (living spec §59.1).",

    # --- Replayability-frontier diagnostic (replayability_frontier.py) -----------------
    "ee_replayability_frontier_classified": "Each (base, oracle) replay pair is classified into a typed replayability-frontier state (replayable / base- or oracle-source unavailable / source parse error / amendment-source unavailable / no amendments in window / other) off the EEPitResult, never silently treated as replayable.",

    # --- Generic structural-op families (peg.py back-fill of grafter-minted ops) -------
    "ee_structural_replace_from_amending_act": "A target provision is replaced with the content the amending act supplies at that address.",
    "ee_structural_insert_from_amending_act": "A new provision is inserted at the address the amending act specifies, with the content it supplies.",
    "ee_structural_repeal_from_amending_act": "A target provision is repealed (declared kehtetuks) as directed by the amending act.",
    "ee_structural_text_replace_from_amending_act": "An in-place text substitution (asendatakse) of an old phrase with a new one at the address the amending act specifies.",
    "ee_structural_text_repeal_from_amending_act": "An in-place deletion of text from a provision as directed by the amending act.",
    "ee_structural_heading_replace_from_amending_act": "A provision heading/title (pealkiri) is replaced or text-edited with the content the amending act supplies.",
    "ee_generic_minister_title_substitution": "§107³ ministerial-title harmonisation: legacy minister titles are globally replaced with 'valdkonna eest vastutav minister' (with the plural collapse) across the statute.",
    "ee_generic_ministry_reorganization": "§105¹⁹ ministry-reorganisation name substitution: a renamed/merged ministry's old name is globally replaced with its new name, honouring explicit per-statute exceptions.",
}
