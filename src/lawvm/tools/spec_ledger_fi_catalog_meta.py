"""Finland spec-ledger rule metadata: the S/P sort (§3.5) and per-rule falsifier
(§3.2(4)) sidecars.

Kept in a separate import-light module (stdlib typing only), mirroring
``spec_ledger_fi_catalog_supplement.py``: the FI adapter folds these into role/falsifier
sidecars passed to ``build_ledger``, leaving the believed_spec catalogs untouched. This
is READ-ONLY / ADDITIVE — no replay/apply behaviour changes.

Two annotations per catalogued FI rule id (see ``notes_internal/FABLE_SPEC_RECONSTRUCTION.md``):

* ``_FI_RULE_ROLES``: ``"S"`` (a hypothesis ABOUT FINNISH AMENDMENT LAW — the drafting
  language's semantics; belongs in the published spec) vs ``"P"`` (a policy of THIS
  COMPILER for surviving its own coverage gaps — recovery / fallback / tolerance /
  materialization-shadow heuristics; NOT a statement about the law). P-rule firing
  density is the undiscovered-spec heatmap.

* ``_FI_RULE_FALSIFIERS``: the oracle observation that would REFUTE the rule's
  believed_spec, making each rule Popper-falsifiable.

An id absent from ``_FI_RULE_ROLES`` defaults to ``"S"`` in the core; the anti-drift
guard (tests/test_fi_spec_ledger_meta.py) requires every catalogued id to carry an
explicit role and falsifier, so the default never silently absorbs a new rule.
"""
from __future__ import annotations

from typing import Dict, Literal

RuleRole = Literal["S", "P"]

# ---------------------------------------------------------------------------
# S/P sort per catalogued FI rule id.
# ---------------------------------------------------------------------------
# P = compiler-survival policy (recovery / fallback / tolerance / timeline-shadow /
#     materialization projection / scope-inference-under-uncertainty). These lower a
#     source the primary spec did not recognize, or reconcile the replay product with the
#     oracle's editorial rendering — they are about THIS compiler, not Finnish law.
# S = hypothesis about the drafting language (surface recognizers + the genuine
#     state-conditional law rules: jolloin renumber, chapter seeding, scope inheritance).
_FI_RULE_ROLES: Dict[str, RuleRole] = {
    # --- seed catalog (_FI_RULE_SPECS) ---
    "fi.section_ref": "S",
    "fi.chapter_ref": "S",
    "fi.insertion_section": "S",
    "fi.insertion_chapter": "S",
    "fi.insertion_sub_target": "S",
    "fi.jolloin_renumber": "S",
    "fi_body_chapter_scope_from_source_body": "S",
    "fi_chapter_seed_inserted_from_amendment_body": "S",
    "fi.recovery.uncovered_body": "P",
    "fi.recovery.uncovered_body.part_insert_subtree_johto_bypass": "P",
    "fi.recovery.uncovered_chapter_scaffold": "P",
    "fi.recovery.uncovered_kumotaan": "P",
    "fi.restructure.renumber_timeline": "S",
    "fi.restructure.relabel_section_snapshot": "P",
    "fi.restructure.chapter_part_move_timeline": "S",
    "fi.restructure.chapter_part_move_timeline.label_reuse_guard": "S",
    "fi.restructure.relabel_migration_ledger_lookup": "P",
    "fi.restructure.relabel_structural_label_alias_lookup": "P",
    "fi.process.post_apply_label_dedup": "P",
    "fi.replay.fold_timeline_backfill": "P",
    "fi.elaboration.named_row_province_table_merge": "S",
    # --- supplement: fallback-extraction lane (all compiler-survival) ---
    "fi.fallback_extraction_recovery": "P",
    "fi.body_root_replace_fallback": "P",
    "fi.enacting_formula_body_replace_fallback": "P",
    "fi.enacting_formula_body_insert_fallback": "P",
    "fi.title_fallback": "P",
    "fi_recodification_vacated_insert_scope": "P",
    "fi_duplicate_section_scope_from_source_heading": "P",
    # --- supplement: pure-kumotaan repeal reconstruction (recovery) ---
    "fi.recovery.pure_kumotaan_repeal": "P",
    "fi.recovery.pure_kumotaan_subsection_repeal": "P",
    "fi.recovery.pure_kumotaan_item_repeal": "P",
    # --- supplement: structural target references (surface grammar = S) ---
    "fi.section_ref_pykala_prefix": "S",
    "fi.part_ref": "S",
    "fi.appendix_ref": "S",
    "fi.nimike_ref": "S",
    "fi.chapter_ref_reversed": "S",
    "fi.coordinated_part_chapter_heading_ref": "S",
    "fi.including_preceding_heading_target": "S",
    "fi.target_version_binding": "S",
    # --- supplement: back-references (surface grammar) ---
    "fi.backref_singular": "S",
    "fi.backref_plural": "S",
    # --- supplement: scope blocks (surface grammar) ---
    "fi.scope_block_chapter": "S",
    "fi.scope_block_part": "S",
    # --- supplement: sub-reference qualifiers (surface grammar) ---
    "fi.sub_ref_momentti": "S",
    "fi.sub_ref_kohta": "S",
    "fi.sub_ref_otsikko": "S",
    "fi.sub_ref_johdantokappale": "S",
    # --- supplement: insertion sub-targets (surface grammar) ---
    "fi.sub_target_pykala": "S",
    "fi.sub_target_luku": "S",
    "fi.sub_target_momentti": "S",
    "fi.sub_target_kohta": "S",
    # --- supplement: insertion shapes (surface grammar) ---
    "fi.insertion_heading": "S",
    "fi.heading_edelle_otsikko_target_list": "S",
    "fi.insertion_other": "S",
    "fi.insertion_section_ill": "S",
    "fi.insertion_section_postfix_chapter": "S",
    "fi.insertion_momentti_ill": "S",
    "fi.insertion_chapter_ill": "S",
    "fi.insertion_chapter_scoped": "S",
    "fi.insertion_chapter_anaphoric": "S",
    "fi.insertion_law_level": "S",
    "fi.insertion_law_level_bare_section": "S",
    "fi.insertion_alakohta_into_item": "S",
    # --- supplement: anaphoric / cross-verb insertion (surface grammar) ---
    "fi.anaphoric_bare_uusi": "S",
    "fi.anaphoric_pykala_ill": "S",
    "fi.anaphoric_momentti_ill": "S",
    "fi.anaphoric_determiner_insert": "S",
    "fi.cross_verb_bare_uusi": "S",
    "fi.cross_verb_momentti": "S",
    "fi.cross_verb_move_retarget": "S",
    # --- supplement: heading placements (surface grammar) ---
    "fi.heading_placement": "S",
    "fi.heading_edelle_luvun_otsikko": "S",
    "fi.heading_edelle_otsikko_after_uusi": "S",
    "fi.valiotsikko_heading_ref": "S",
    # --- supplement: renumber / relabel clauses (surface grammar) ---
    "fi.section_renumber": "S",
    "fi.chapter_renumber": "S",
    "fi.part_renumber": "S",
    "fi.direct_section_relabel": "S",
    "fi.renumber_backref": "S",
    "fi.jolloin_section_renumber": "S",
    "fi.current_section_renumber_tail": "S",
    "fi.jolloin_chapter_renumber": "S",
    # --- supplement: exception qualifier (surface grammar) ---
    "fi.lukuun_ottamatta_exception": "S",
    # --- supplement: meta / commencement / transition (surface grammar) ---
    "fi.meta_commencement": "S",
    "fi.meta_expiry": "S",
    "fi.meta_delegation": "S",
    "fi.meta_transition": "S",
    # --- supplement: text amendments (surface grammar) ---
    "fi.text_amend_sana": "S",
    "fi.text_amend_sanat": "S",
    "fi.text_amend_target": "S",
    # --- supplement: voimaantulosäännös repeal extraction (surface grammar) ---
    "fi.repeal_vts_voimaantulo": "S",
    # --- supplement: flat-body scope recovery (compiler-survival inference) ---
    "fi_reinstated_section_scope_from_prior_repeal_address": "P",
    "fi_live_stem_scope_overridden_by_corroborated_source_body": "P",
    "fi.act_wide_body_section_replace": "P",
    "fi.item_and_moment_target_supplement.v1": "P",
    "fi.mixed_explicit_target_supplement.v1": "P",
    "fi.numbered_table_target.v1": "S",
    "fi.sparse_osalta_row_omission_repeal.v1": "S",
    # --- supplement: timeline materialization shadows (compiler-survival) ---
    "fi.timeline.absent_content_shadow_collapse": "P",
    "fi.timeline.same_source_semantic_version_dedupe": "P",
    "fi.timeline.restructure_relabel_snapshot_shadow_collapse": "P",
    "fi.timeline.restructure_relabel_shell_shadow_collapse": "P",
    # --- supplement: flat-body / letter-suffix scope inference (compiler-survival) ---
    "fi_flat_body_replace_scope_from_bracketing_live_siblings": "P",
    "fi_letter_suffix_insert_scope_from_stem_host": "P",
    "fi_same_amendment_stem_scope_for_letter_suffix_insert": "P",
    "fi_materialized_attachments_wrapper_split_v1": "P",
    "fi_materialized_provisions_wrapper_projection_v1": "P",
    "fi_flat_body_insert_scope_from_bracketing_live_siblings": "P",
    "fi_flat_body_insert_scope_from_base_family_continuation": "P",
    "fi_flat_body_replace_scope_from_live_section_gap": "P",
}


# ---------------------------------------------------------------------------
# Per-rule falsifier: the oracle observation that would refute believed_spec.
# ---------------------------------------------------------------------------
# Authored by layer template (§3.2(4)):
#   - surface recognizer (S): "an oracle triple where form F fired and the consolidated
#     text does not show the op F's believed_spec predicts";
#   - recovery/fallback (P): "an oracle triple where the recovered/guessed op contradicts
#     the consolidated text (its contradiction RATE exceeds tolerance — P-rules are only
#     statistically falsifiable)".
_FI_RULE_FALSIFIERS: Dict[str, str] = {
    "fi.section_ref": "An oracle triple where a '<n> §' citation fired but the effect landed on a section other than live section <n>.",
    "fi.chapter_ref": "An oracle triple where a '<n> luku' citation fired but the effect landed on a chapter other than live chapter <n>.",
    "fi.insertion_section": "An oracle triple where 'lisätään ... uusi <n> §' fired but the consolidated text has no new section at <n>.",
    "fi.insertion_chapter": "An oracle triple where 'lisätään ... uusi <n> luku' fired but the consolidated text has no new chapter at <n>.",
    "fi.insertion_sub_target": "An oracle triple where an insertion sub-target landed outside its named parent section in the consolidated text.",
    "fi.jolloin_renumber": "An oracle triple where a 'jolloin ... siirtyy' clause fired but the displaced sections are NOT renumbered in the consolidated text.",
    "fi_body_chapter_scope_from_source_body": "An oracle triple where the body-scoped section's chapter in the consolidated text differs from the amendment body container's chapter.",
    "fi_chapter_seed_inserted_from_amendment_body": "An oracle triple where chapter seeding fired for a base chapter that was in fact already present, producing a duplicate/misplaced chapter.",
    "fi.recovery.uncovered_body": "A rate of oracle triples where uncovered-body recovery synthesized a section op that contradicts the consolidated text exceeding tolerance.",
    "fi.recovery.uncovered_body.part_insert_subtree_johto_bypass": "An oracle triple where a part-payload section materialized by this bypass is absent from (or misplaced in) the consolidated part.",
    "fi.recovery.uncovered_chapter_scaffold": "An oracle triple where the synthesized chapter scaffold hosts sections at a chapter the consolidated text does not show.",
    "fi.recovery.uncovered_kumotaan": "An oracle triple where the recovered kumotaan repeal removed a provision still present in the consolidated text.",
    "fi.restructure.renumber_timeline": "An oracle triple where a migration-event RENUMBER fired but the old address is still live (not tombstoned) in the consolidated text.",
    "fi.restructure.relabel_section_snapshot": "A rate of oracle triples where the post-relabel snapshot materialized a body at a path the consolidated text does not carry, exceeding tolerance.",
    "fi.restructure.chapter_part_move_timeline": "An oracle triple where a chapter moved under a new part is not preserved (old address kept or new address missing) in the consolidated text.",
    "fi.restructure.chapter_part_move_timeline.label_reuse_guard": "An oracle triple where the guard suppressed a real cross-part move (the chapter DID move) or failed to suppress a mere label reuse.",
    "fi.restructure.relabel_migration_ledger_lookup": "A rate of oracle triples where the migration-lineage resolution retargeted a relabel to the wrong provision, exceeding tolerance.",
    "fi.restructure.relabel_structural_label_alias_lookup": "A rate of oracle triples where the label-alias equivalence matched a node that is not the drafter's target, exceeding tolerance.",
    "fi.process.post_apply_label_dedup": "An oracle triple where the dedup backstop removed a sibling that is genuinely distinct legal content in the consolidated text.",
    "fi.replay.fold_timeline_backfill": "An oracle triple where fold-backfill grafted a section snapshot onto a timeline address the consolidated text does not carry.",
    "fi.elaboration.named_row_province_table_merge": "An oracle triple where the province-table merge replaced province blocks the amendment did not claim, or failed to merge a claimed block.",
    "fi.fallback_extraction_recovery": "A rate of oracle triples where a Heuristic-#29 fallback op contradicts the consolidated text (target-guessing wrong) exceeding tolerance.",
    "fi.body_root_replace_fallback": "A rate of oracle triples where the whole-section REPLACE minted from body text contradicts the consolidated section, exceeding tolerance.",
    "fi.enacting_formula_body_replace_fallback": "A rate of oracle triples where the enacting-formula REPLACE fallback contradicts the consolidated text, exceeding tolerance.",
    "fi.enacting_formula_body_insert_fallback": "A rate of oracle triples where the enacting-formula INSERT fallback inserts a section absent from the consolidated text, exceeding tolerance.",
    "fi.title_fallback": "A rate of oracle triples where the title-derived op contradicts the consolidated text, exceeding tolerance.",
    "fi_recodification_vacated_insert_scope": "An oracle triple where the inferred part/chapter for a recodification-vacated insert differs from the consolidated placement.",
    "fi_duplicate_section_scope_from_source_heading": "An oracle triple where the wrapper-level section this rule suppressed is in fact independent live content in the consolidated text.",
    "fi.recovery.pure_kumotaan_repeal": "An oracle triple where the reconstructed whole-section/container repeal removed a provision still present in the consolidated text.",
    "fi.recovery.pure_kumotaan_subsection_repeal": "An oracle triple where the reconstructed subsection repeal-placeholder contradicts the consolidated subsection.",
    "fi.recovery.pure_kumotaan_item_repeal": "An oracle triple where the reconstructed item repeal-placeholder contradicts the consolidated item.",
    "fi.section_ref_pykala_prefix": "An oracle triple where a 'pykälien N, M ...' prefix citation targeted a section not in the listed set.",
    "fi.part_ref": "An oracle triple where a '<n> osa' citation targeted a part other than live part <n>.",
    "fi.appendix_ref": "An oracle triple where a 'liite' citation targeted something other than the statute appendix.",
    "fi.nimike_ref": "An oracle triple where a bare nimike citation targeted something other than the statute's own title.",
    "fi.chapter_ref_reversed": "An oracle triple where a reversed-order chapter citation spanned chapters other than the intended range.",
    "fi.coordinated_part_chapter_heading_ref": "An oracle triple where the coordinated heading citation targeted a container other than the named part-and-chapter heading.",
    "fi.including_preceding_heading_target": "An oracle triple where 'N § otsikko' did not include section N together with its preceding heading.",
    "fi.target_version_binding": "An oracle triple where the 'sellaisena kuin' qualifier bound to a statute version other than the one cited.",
    "fi.backref_singular": "An oracle triple where 'mainitun pykälän' resolved to a section other than the most recently cited one.",
    "fi.backref_plural": "An oracle triple where 'mainittujen pykälien' resolved to sections other than the most recently cited ones.",
    "fi.scope_block_chapter": "An oracle triple where a 'N luvun ...' scope block applied a chapter scope the consolidated targets do not bear.",
    "fi.scope_block_part": "An oracle triple where a 'N osan ...' scope block applied a part scope the consolidated targets do not bear.",
    "fi.sub_ref_momentti": "An oracle triple where 'N momentti' addressed a subsection other than N under its target.",
    "fi.sub_ref_kohta": "An oracle triple where 'N kohta' addressed an item other than N under its target.",
    "fi.sub_ref_otsikko": "An oracle triple where an 'otsikko' qualifier addressed something other than its target's heading.",
    "fi.sub_ref_johdantokappale": "An oracle triple where 'johdantokappale' addressed something other than the target's introductory paragraph.",
    "fi.sub_target_pykala": "An oracle triple where 'uusi N §' landed something other than a new section at N.",
    "fi.sub_target_luku": "An oracle triple where 'uusi N luku' landed something other than a new chapter at N.",
    "fi.sub_target_momentti": "An oracle triple where 'uusi N momentti' landed outside its parent section.",
    "fi.sub_target_kohta": "An oracle triple where 'uusi N kohta' landed outside its parent subsection.",
    "fi.insertion_heading": "An oracle triple where a HEADING-facet insertion placed a heading the consolidated text does not carry.",
    "fi.heading_edelle_otsikko_target_list": "An oracle triple where one target in the coordinated 'edelle uusi väliotsikko' list received no heading (or an extra heading) versus the consolidated text.",
    "fi.insertion_other": "An oracle triple where an unclassified lisätään node's emitted op contradicts the consolidated text.",
    "fi.insertion_section_ill": "An oracle triple where 'N §:ään uusi <sub>' inserted the sub-target into a section other than N.",
    "fi.insertion_section_postfix_chapter": "An oracle triple where the postfix '... lukuun' chapter scope differs from the consolidated placement.",
    "fi.insertion_momentti_ill": "An oracle triple where 'N §:n M momenttiin uusi <sub>' inserted into a momentti/section other than M/N.",
    "fi.insertion_chapter_ill": "An oracle triple where 'N lukuun uusi <sub>' inserted into a chapter other than N.",
    "fi.insertion_chapter_scoped": "An oracle triple where the chapter-scoped section insert landed under a different chapter/section than named.",
    "fi.insertion_chapter_anaphoric": "An oracle triple where the anaphoric chapter differs from the chapter carried by prior context.",
    "fi.insertion_law_level": "An oracle triple where 'lakiin uusi N §/luku' landed at a container level other than law level.",
    "fi.insertion_law_level_bare_section": "An oracle triple where the trailing-§-omitted law-level insert landed something other than a new section N.",
    "fi.insertion_alakohta_into_item": "An oracle triple where the new alakohta landed outside item K of momentti M in section N.",
    "fi.anaphoric_bare_uusi": "An oracle triple where 'uusi N momentti/kohta' inserted into a section other than the one carried from context.",
    "fi.anaphoric_pykala_ill": "An oracle triple where 'pykälään uusi <sub>' inserted into a section other than the anaphoric one.",
    "fi.anaphoric_momentti_ill": "An oracle triple where 'N momenttiin uusi <sub>' inherited a section other than the context section.",
    "fi.anaphoric_determiner_insert": "An oracle triple where 'sanottuun/mainittuun/samaan' resolved to a section/momentti other than the last mentioned one.",
    "fi.cross_verb_bare_uusi": "An oracle triple where a cross-verb bare 'uusi <sub>' inherited a section other than the shared VerbGroupContext's.",
    "fi.cross_verb_momentti": "An oracle triple where a cross-verb 'momenttiin uusi <sub>' inherited the wrong section from the verb group.",
    "fi.cross_verb_move_retarget": "An oracle triple where the cross-verb move retargeted a section to a chapter other than the one named.",
    "fi.heading_placement": "An oracle triple where the 'N §:n edelle uusi väliotsikko' heading is absent or misplaced in the consolidated text.",
    "fi.heading_edelle_luvun_otsikko": "An oracle triple where the chapter heading placed before section N differs from the consolidated text.",
    "fi.heading_edelle_otsikko_after_uusi": "An oracle triple where the heading before a freshly inserted section N is absent or misplaced.",
    "fi.valiotsikko_heading_ref": "An oracle triple where 'sen edellä oleva väliotsikko' resolved to a heading other than the one preceding the referenced section(s).",
    "fi.section_renumber": "An oracle triple where 'N §:n numero M:ksi' left section N un-renumbered (or renumbered to other than M).",
    "fi.chapter_renumber": "An oracle triple where 'N luvun numero M:ksi' left chapter N un-renumbered (or renumbered to other than M).",
    "fi.part_renumber": "An oracle triple where 'N osan numero M:ksi' left part N un-renumbered (or renumbered to other than M).",
    "fi.direct_section_relabel": "An oracle triple where the context-resolved section relabel targeted a section other than the referenced one.",
    "fi.renumber_backref": "An oracle triple where the 'mainitun pykälän' continuation carried the renumber over the wrong back-referenced section.",
    "fi.jolloin_section_renumber": "An oracle triple where 'jolloin nykyinen N § siirtyy M §:ksi' did not renumber the displaced section to M.",
    "fi.current_section_renumber_tail": "An oracle triple where 'nykyinen N § uudeksi M §:ksi' did not renumber section N to M.",
    "fi.jolloin_chapter_renumber": "An oracle triple where 'jolloin nykyinen N luku siirtyy M luvuksi' did not renumber the displaced chapter to M.",
    "fi.lukuun_ottamatta_exception": "An oracle triple where 'lukuun ottamatta N §' failed to exclude the named section from the amendment scope.",
    "fi.meta_commencement": "An oracle triple where a 'Tämä laki tulee voimaan' clause was compiled as a structural op instead of voimaantulo metadata.",
    "fi.meta_expiry": "An oracle triple where a 'Tämä laki on voimassa' clause was compiled as a structural op instead of voimassaolo metadata.",
    "fi.meta_delegation": "An oracle triple where a delegation clause was compiled as a structural op instead of delegation metadata.",
    "fi.meta_transition": "An oracle triple where a transition/applicability clause was compiled as a structural op instead of transition metadata.",
    "fi.text_amend_sana": "An oracle triple where a single-word 'korvataan sanalla' substitution did not replace X with Y in the consolidated text.",
    "fi.text_amend_sanat": "An oracle triple where a multi-word 'korvataan sanoilla' substitution did not replace X with Y in the consolidated text.",
    "fi.text_amend_target": "An oracle triple where the text-amend section ref scoped the substitution to a section other than the named one.",
    "fi.repeal_vts_voimaantulo": "An oracle triple where a voimaantulosäännös 'N § kumottu' clause left the named section present in the consolidated text.",
    "fi_reinstated_section_scope_from_prior_repeal_address": "An oracle triple where the reinstated section's chapter/part in the consolidated text differs from the prior-repeal address this rule reused.",
    "fi_live_stem_scope_overridden_by_corroborated_source_body": "An oracle triple where the source-body-corroborated chapter differs from the consolidated placement (the live-stem chapter was right).",
    "fi.act_wide_body_section_replace": "A rate of oracle triples where the act-wide body section op contradicts the consolidated section, exceeding tolerance.",
    "fi.item_and_moment_target_supplement.v1": "A rate of oracle triples where a recovered item/subsection target contradicts the consolidated text, exceeding tolerance.",
    "fi.mixed_explicit_target_supplement.v1": "A rate of oracle triples where a recovered sibling target from a mixed clause contradicts the consolidated text, exceeding tolerance.",
    "fi.numbered_table_target.v1": "An oracle triple where a numbered table row parsed as an op target contradicts the consolidated table content.",
    "fi.sparse_osalta_row_omission_repeal.v1": "An oracle triple where the 'osalta' row-omission repeal removed a row still present in the consolidated table.",
    "fi.timeline.absent_content_shadow_collapse": "An oracle triple where the collapsed absent-content shadow was in fact live legal content in the consolidated text.",
    "fi.timeline.same_source_semantic_version_dedupe": "An oracle triple where the deduped same-source versions carried competing legal content the consolidated text distinguishes.",
    "fi.timeline.restructure_relabel_snapshot_shadow_collapse": "An oracle triple where the dropped relabel snapshot was the only carrier of a provision the consolidated text shows.",
    "fi.timeline.restructure_relabel_shell_shadow_collapse": "An oracle triple where the collapsed relabel shell owned legal content absent elsewhere in the consolidated text.",
    "fi_flat_body_replace_scope_from_bracketing_live_siblings": "An oracle triple where the chapter inferred from bracketing siblings differs from the consolidated placement of the replaced section.",
    "fi_letter_suffix_insert_scope_from_stem_host": "An oracle triple where the stem-host chapter inherited by the letter-suffix insert differs from the consolidated placement.",
    "fi_same_amendment_stem_scope_for_letter_suffix_insert": "An oracle triple where the same-amendment stem chapter differs from the consolidated placement of the letter-suffix insert.",
    "fi_materialized_attachments_wrapper_split_v1": "An oracle triple where the projected attachment provisions differ from the consolidated attachment structure.",
    "fi_materialized_provisions_wrapper_projection_v1": "An oracle triple where the projected provision nodes differ from the consolidated provision structure.",
    "fi_flat_body_insert_scope_from_bracketing_live_siblings": "An oracle triple where the chapter inferred from bracketing siblings differs from the consolidated placement of the inserted section.",
    "fi_flat_body_insert_scope_from_base_family_continuation": "An oracle triple where the label-family chapter inherited by the lettered-suffix insert differs from the consolidated placement.",
    "fi_flat_body_replace_scope_from_live_section_gap": "An oracle triple where the gap-inferred chapter differs from the consolidated placement of the replaced section.",
}
