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

The anti-drift guard ``tests/test_spec_ledger_fi_catalog.py`` discovers the FI
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
    "fi_flat_reinstated_section_scope_from_base_prior_address": (
        "A flat-body 'kumotun N §:n tilalle uusi N §' rebirth inherits the repealed "
        "section's prior chapter/part address from the base tree when it is unique and "
        "its container still exists."
    ),
    "fi_flat_body_insert_scope_from_bracketing_live_siblings": (
        "A flat-body whole-section insert infers its chapter from the live sibling "
        "sections bracketing it by label."
    ),
    "fi_flat_body_insert_scope_from_base_family_continuation": (
        "A flat-body lettered-suffix section insert (e.g. N a §) inherits the chapter of "
        "the preceding section in its label family."
    ),
}
