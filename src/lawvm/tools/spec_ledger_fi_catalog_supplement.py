"""Finland spec-ledger catalog supplement.

``believed_spec`` prose for ``witness_rule_id`` literals that fire in
``src/lawvm/finland/`` but are not yet seeded in ``_FI_RULE_SPECS`` (in
``spec_ledger.py``).  Kept in a separate, import-light module (stdlib typing only)
so the orchestrator can fold it into ``_FI_RULE_SPECS`` at the FI adapter without
this base needing to edit ``spec_ledger.py`` (which carries uk/ee adapters not
present here).

Each entry is a one-line hypothesis in the ``_FI_RULE_SPECS`` voice describing the
drafting idiom the rule recognizes and what it targets.  Every entry is grounded in
the rule-id's emission site — the johtolause surface recognizer
(``johtolause/surface_parse.py``), its declaration in the parse-rule registry
(``johtolause/rule_registry.py``, whose ``ParseRule.description``/``shape`` fields
seed this prose), or the compile orchestrator (``frontend_compile.py``).

The anti-drift guard ``tests/test_fi_spec_ledger_catalog.py`` discovers the FI
parse-witness rule-id surface by AST and asserts ``_FI_RULE_SPECS`` ∪
``_FI_RULE_SPECS_SUPPLEMENT`` covers it with no dead supplement entries.
"""

from __future__ import annotations

from typing import Dict

_FI_RULE_SPECS_SUPPLEMENT: Dict[str, str] = {
    # --- fallback-extraction lane (frontend_compile.py) ---
    "fi.fallback_extraction_recovery": (
        "Heuristic #29 fallback extraction recovers structural ops from a johtolause "
        "the primary parser left unmodeled (op.fallback_provenance, gated by "
        "allows_target_guessing)."
    ),
    "fi.body_root_replace_fallback": (
        "Root body-text replacement fallback mints a whole-section REPLACE from "
        "amendment body text when the johtolause names no provision target "
        "(op.fallback_provenance, gated by allows_target_guessing)."
    ),
    "fi.enacting_formula_body_replace_fallback": (
        "Enacting-formula body-replace fallback mints REPLACE ops from amendment body "
        "sections introduced by a bare enacting formula with no explicit target "
        "(op.fallback_provenance, gated by allows_target_guessing)."
    ),
    "fi.enacting_formula_body_insert_fallback": (
        "Enacting-formula body-insert fallback mints INSERT ops from amendment body "
        "sections introduced by a bare enacting formula with no explicit target "
        "(op.fallback_provenance, gated by allows_target_guessing)."
    ),
    "fi.title_fallback": (
        "Title fallback mints ops from the amendment title when the johtolause yields "
        "no parsed structural target (op.fallback_provenance, gated by "
        "allows_target_guessing)."
    ),
    "fi_recodification_vacated_insert_scope": (
        "Recodification-vacated insert-scope inference resolves the part/chapter of a "
        "section INSERT whose original container was vacated by a recodification, by "
        "reading the recodification context rather than leaving the chapter unscoped "
        "(frontend_compile.py _infer_recodification_vacated_insert_scope)."
    ),
    "fi_duplicate_section_scope_from_source_heading": (
        "Duplicate section scope from a source heading records a wrapper-level section "
        "whose label is consumed by a later explicitly scoped section replacement "
        "rather than promoted as independent replay authority."
    ),
    # --- pure-kumotaan repeal reconstruction (kumotaan_replay.py) ---
    "fi.recovery.pure_kumotaan_repeal": (
        "Pure-kumotaan repeal injection reconstructs a whole-section/container REPEAL "
        "from a raw kumotaan johtolause when the typed pipeline emitted no op for the "
        "named target (witnessed reconstruction with a structured finding)."
    ),
    "fi.recovery.pure_kumotaan_subsection_repeal": (
        "Pure-kumotaan subsection repeal injection reconstructs a subsection "
        "repeal-placeholder REPLACE from a raw 'N §:n M momentti' kumotaan clause when "
        "no body op covered the subsection (witnessed reconstruction with a finding)."
    ),
    "fi.recovery.pure_kumotaan_item_repeal": (
        "Pure-kumotaan item repeal injection reconstructs an item repeal-placeholder "
        "REPLACE from a raw 'N §:n K kohta' kumotaan clause when no body op covered "
        "the item (witnessed reconstruction with a finding)."
    ),
    # --- structural target references (surface_parse / rule_registry) ---
    "fi.section_ref_pykala_prefix": (
        "A genitive-plural prefix citation 'pykälien N, M ...' targets the listed live "
        "sections."
    ),
    "fi.part_ref": "A '<n> osa' citation (often Roman numerals) targets the live part <n>.",
    "fi.appendix_ref": "A 'liite [N]' citation targets the statute appendix (liite).",
    "fi.nimike_ref": "A bare nimike citation targets the statute's own title.",
    "fi.chapter_ref_reversed": (
        "A chapter citation with reversed numeric order (e.g. '5-2 luku') targets the "
        "spanned live chapters."
    ),
    "fi.coordinated_part_chapter_heading_ref": (
        "A coordinated 'osan/luvun otsikko' citation targets the heading facet of the "
        "named part-and-chapter container."
    ),
    "fi.including_preceding_heading_target": (
        "An 'N § otsikko' citation targets section N together with its preceding heading."
    ),
    "fi.target_version_binding": (
        "A 'sellaisena/siten kuin <statute>' qualifier binds the target labels to the "
        "cited statute version."
    ),
    # --- back-references resolved against an earlier mention ---
    "fi.backref_singular": (
        "A singular back-reference 'mainitun pykälän [sub_ref]' resolves to the most "
        "recently cited section."
    ),
    "fi.backref_plural": (
        "A plural back-reference 'mainittujen pykälien [sub_ref]' resolves to the most "
        "recently cited sections."
    ),
    # --- scope blocks (a chapter/part scope over a group of targets) ---
    "fi.scope_block_chapter": (
        "A 'N luvun ...' scope block applies a chapter scope to the group of section "
        "targets that follow."
    ),
    "fi.scope_block_part": (
        "A 'N osan ...' scope block applies a part scope to the group of section/chapter "
        "targets that follow."
    ),
    # --- sub-reference qualifiers (the addressed facet under a target) ---
    "fi.sub_ref_momentti": "A 'N momentti' qualifier addresses subsection N under its target.",
    "fi.sub_ref_kohta": "A 'N kohta' qualifier addresses item N under its target.",
    "fi.sub_ref_otsikko": "An 'otsikko' qualifier addresses the heading facet of its target.",
    "fi.sub_ref_johdantokappale": (
        "A 'johdantokappale' qualifier addresses the introductory paragraph of its target."
    ),
    # --- insertion sub-targets (the unit minted by a lisätään node) ---
    "fi.sub_target_pykala": "An insertion's section sub-target ('uusi N §') lands a new section.",
    "fi.sub_target_luku": "An insertion's chapter sub-target ('uusi N luku') lands a new chapter.",
    "fi.sub_target_momentti": (
        "An insertion's subsection sub-target ('uusi N momentti') lands inside its parent "
        "section."
    ),
    "fi.sub_target_kohta": (
        "An insertion's item sub-target ('uusi N kohta') lands inside its parent subsection."
    ),
    # --- insertion shapes (heading / chapter / section / momentti illatives) ---
    "fi.insertion_heading": (
        "A catch-all heading insertion: a lisätään node whose sub-target is a HEADING "
        "facet stamps a generic otsikko placement."
    ),
    "fi.heading_edelle_otsikko_target_list": (
        "A target-first '<num_list> §:n edelle uusi väliotsikko' arm places a new "
        "subheading before each coordinated section target, where the target list may "
        "be an enumeration or em-dash range (e.g. '69 b-69 e ja 69 g-69 i §:n edelle "
        "uusi väliotsikko'); one heading placement is emitted per target."
    ),
    "fi.insertion_other": "A catch-all for a lisätään node whose insertion shape is unclassified.",
    "fi.insertion_section_ill": (
        "lisätään N §:ään uusi <sub> inserts the sub-target into section N (illative)."
    ),
    "fi.insertion_section_postfix_chapter": (
        "lisätään uusi N § ... lukuun inserts a new section whose chapter scope trails the "
        "section in the postfix '... lukuun' clause."
    ),
    "fi.insertion_momentti_ill": (
        "lisätään N §:n M momenttiin uusi <sub> inserts the sub-target into momentti M of "
        "section N (illative)."
    ),
    "fi.insertion_chapter_ill": (
        "lisätään N lukuun uusi <sub> inserts the sub-target into chapter N (illative)."
    ),
    "fi.insertion_chapter_scoped": (
        "lisätään N luvun M §:ään uusi <sub> inserts the sub-target into the "
        "chapter-scoped section."
    ),
    "fi.insertion_chapter_anaphoric": (
        "lisätään lukuun uusi <sub> inserts into the chapter carried from prior context "
        "(anaphoric, illative)."
    ),
    "fi.insertion_law_level": (
        "lisätään lakiin uusi N §/luku inserts a new section/chapter at law level "
        "(DOC illative)."
    ),
    "fi.insertion_law_level_bare_section": (
        "A historical law-level insert 'lisätään <statute>:ILL uusi N [a]' with the "
        "trailing § omitted inserts a new section N at law level (DOC illative)."
    ),
    "fi.insertion_alakohta_into_item": (
        "lisätään N §:n M momentin K kohtaan uusi <letter> alakohta inserts a new "
        "subparagraph (alakohta) into item K of momentti M in section N, encoded as a "
        "compound item label for replay."
    ),
    # --- anaphoric / cross-verb insertion recognizers inheriting section context ---
    "fi.anaphoric_bare_uusi": (
        "A bare anaphoric insertion 'uusi N momentti/kohta' inserts the sub-target into "
        "the section carried from prior context."
    ),
    "fi.anaphoric_pykala_ill": (
        "An anaphoric §:ILL insertion 'pykälään [uudelleen] uusi <sub>' inserts the "
        "sub-target into the anaphoric section."
    ),
    "fi.anaphoric_momentti_ill": (
        "An anaphoric momentti:ILL insertion 'N momenttiin uusi <sub>' inserts the "
        "sub-target into momentti N, inheriting the section from context."
    ),
    "fi.anaphoric_determiner_insert": (
        "A determiner-anchored insertion 'sanottuun/mainittuun/samaan pykälään|momenttiin "
        "uusi ...' resolves against the last mentioned section/momentti."
    ),
    "fi.cross_verb_bare_uusi": (
        "A cross-verb-group bare 'uusi <sub>' insertion inherits its section from the "
        "VerbGroupContext when the verb is shared across coordinated targets."
    ),
    "fi.cross_verb_momentti": (
        "A cross-verb-group 'momenttiin uusi <sub>' insertion inherits its section from "
        "the VerbGroupContext."
    ),
    "fi.cross_verb_move_retarget": (
        "A cross-verb-group 'N §:n ... N lukuun' move retargets a section to a different "
        "chapter under the shared verb."
    ),
    # --- heading placements ---
    "fi.heading_placement": (
        "An 'N §:n edelle uusi väliotsikko / luvun otsikko' clause inserts a heading "
        "before section N."
    ),
    "fi.heading_edelle_luvun_otsikko": (
        "An 'N §:n edelle ... luvun otsikko' placement targets a chapter heading "
        "positioned before section N."
    ),
    "fi.heading_edelle_otsikko_after_uusi": (
        "An 'uusi N § edellä otsikko' placement inserts a heading before a freshly "
        "inserted section N."
    ),
    "fi.valiotsikko_heading_ref": (
        "A 'sen edellä oleva väliotsikko' back-reference resolves to otsikko ops on the "
        "väliotsikko preceding the referenced section(s)."
    ),
    # --- renumber / relabel clauses ---
    "fi.section_renumber": (
        "A 'N §:n numero M:ksi' clause renumbers section N to M."
    ),
    "fi.chapter_renumber": (
        "A 'N luvun numero M:ksi' clause renumbers chapter N to M."
    ),
    "fi.part_renumber": (
        "A 'N osan numero M:ksi' clause renumbers part N to M."
    ),
    "fi.direct_section_relabel": (
        "A '§:n numero M:ksi' clause resolved from context relabels (renumbers) the "
        "referenced section to M."
    ),
    "fi.renumber_backref": (
        "A 'mainitun/mainittujen pykälän <sub_ref>' continuation carries a renumber over "
        "a back-referenced section."
    ),
    "fi.jolloin_section_renumber": (
        "A 'jolloin nykyinen N § siirtyy M §:ksi' clause renumbers the displaced section."
    ),
    "fi.current_section_renumber_tail": (
        "A SIIRTAA tail 'nykyinen N § uudeksi M §:ksi' renumbers the current section N "
        "to section M."
    ),
    "fi.jolloin_chapter_renumber": (
        "A 'jolloin nykyinen N luku siirtyy M luvuksi' clause renumbers the displaced "
        "chapter."
    ),
    # --- exception qualifier ---
    "fi.lukuun_ottamatta_exception": (
        "A 'lukuun ottamatta N §' qualifier excludes the named section(s) from the "
        "amendment scope."
    ),
    # --- meta / commencement / transition clauses ---
    "fi.meta_commencement": (
        "A commencement clause 'Tämä laki tulee voimaan [date]' is recognized as "
        "voimaantulo metadata, not a structural op."
    ),
    "fi.meta_expiry": (
        "An expiry clause 'Tämä laki on voimassa [until date]' is recognized as "
        "voimassaolo metadata."
    ),
    "fi.meta_delegation": (
        "A delegation clause 'antaa tarkempia säännöksiä/määräyksiä' is recognized as "
        "delegation metadata."
    ),
    "fi.meta_transition": (
        "A transition/applicability clause 'siirtymäsäännös / tätä lakia sovelletaan' is "
        "recognized as transition metadata."
    ),
    # --- text amendments (word/phrase substitution) ---
    "fi.text_amend_sana": (
        "A 'sana \"X\" korvataan sanalla \"Y\"' clause is a single-word text replacement."
    ),
    "fi.text_amend_sanat": (
        "A 'sanat \"X\" korvataan sanoilla \"Y\"' clause is a multi-word text replacement."
    ),
    "fi.text_amend_target": (
        "A section ref inside a text-amend context scopes the word/phrase substitution to "
        "that section."
    ),
    # --- voimaantulosäännös (transitional-provision) repeal extraction (vts.py) ---
    "fi.repeal_vts_voimaantulo": (
        "A repeal named in a voimaantulosäännös (transitional/entry-into-force "
        "provision) fragment — 'N § (on) kumottu ...' / 'N luku kumotaan' — is "
        "extracted as a whole-section or whole-chapter REPEAL op, even without a "
        "normal operative johtolause."
    ),
    # --- compile-time flat-body section-scope recovery (frontend_compile.py) ---
    "fi_reinstated_section_scope_from_prior_repeal_address": (
        "A reinstated flat-body section inherits the chapter/part address recorded by "
        "the prior repeal when that address is unique and still legally available."
    ),
    "fi_live_stem_scope_overridden_by_corroborated_source_body": (
        "A flat-body letter-suffix/live-stem insert whose chapter scope is not fixed "
        "by a prior-repeal address takes the chapter/part scope corroborated by the "
        "amendment's own source body, overriding the live stem host's chapter when "
        "the source body independently corroborates that placement."
    ),
    "fi.act_wide_body_section_replace": (
        "An act-wide body section replacement is recognized as a source-body section "
        "operation rather than a free-floating payload fragment."
    ),
    "fi.item_and_moment_target_supplement.v1": (
        "A mixed clause supplement recovers item and subsection targets skipped by the "
        "primary johtolause parse while preserving their source scope."
    ),
    "fi.mixed_explicit_target_supplement.v1": (
        "A mixed explicit-target clause supplement recovers omitted sibling targets "
        "from the same operative clause with a visible recovery rule."
    ),
    "fi.numbered_table_target.v1": (
        "A numbered table row target is parsed as a legal operation target, not as "
        "unowned table text."
    ),
    "fi.sparse_osalta_row_omission_repeal.v1": (
        "A sparse 'osalta' table-row omission is lowered as an owned repeal of the "
        "specified row target."
    ),
    "fi.timeline.absent_content_shadow_collapse": (
        "Timeline materialization collapses absent-content shadows from the same source "
        "only when the content absence is explicit and witnessed."
    ),
    "fi.timeline.same_source_semantic_version_dedupe": (
        "Timeline materialization deduplicates semantically equivalent same-source "
        "versions without hiding competing legal content."
    ),
    "fi.timeline.restructure_relabel_snapshot_shadow_collapse": (
        "Timeline materialization drops restructure-relabel snapshots from a "
        "same-source version group when at least one real-payload version of the "
        "same provision survives; the snapshot is an editing shadow, not competing "
        "legal content."
    ),
    "fi.timeline.restructure_relabel_shell_shadow_collapse": (
        "Timeline materialization collapses an empty restructure-relabel shell shadow "
        "only when same-source relabel evidence owns the legal content elsewhere."
    ),
    "fi_flat_body_replace_scope_from_bracketing_live_siblings": (
        "A flat-body whole-section replacement infers its chapter from live sibling "
        "sections bracketing the replaced label."
    ),
    "fi_letter_suffix_insert_scope_from_stem_host": (
        "A letter-suffix section insert can inherit the stem section's live chapter "
        "when the source gives no stronger container scope."
    ),
    "fi_same_amendment_stem_scope_for_letter_suffix_insert": (
        "A letter-suffix section insert can inherit chapter scope from a same-amendment "
        "stem section when that stem establishes stronger local scope than live lookup."
    ),
    "fi_materialized_attachments_wrapper_split_v1": (
        "Materialization splits attachment wrappers into typed projected provisions "
        "with a visible projection rule."
    ),
    "fi_materialized_provisions_wrapper_projection_v1": (
        "Materialization projects provision wrappers into replay-visible provision "
        "nodes with a visible projection rule."
    ),
    "fi_flat_body_insert_scope_from_bracketing_live_siblings": (
        "A flat-body whole-section insert infers its chapter from the live sibling "
        "sections bracketing it by label."
    ),
    "fi_flat_body_insert_scope_from_base_family_continuation": (
        "A flat-body lettered-suffix section insert (e.g. N a §) inherits the chapter of "
        "the preceding section in its label family."
    ),
    "fi_flat_body_replace_scope_from_live_section_gap": (
        "A flat-body whole-section replacement infers its chapter from the gap between "
        "live sections that the replaced section's label falls into."
    ),
}
