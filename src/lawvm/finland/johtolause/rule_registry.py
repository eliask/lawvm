"""rule_registry — Canonical rule catalog for Finnish amendment clause parsing.

Single source of truth for all parse rule metadata.  Merged from the former
construction_rules.py (stable IDs + shapes) and the Phase 8 rule_registry
(rich examples, node_kind, categories).

Each parse rule is a first-class object with a stable ID, description,
node kind, category, shape, and example inputs with expected outputs.

VPRI philosophy: the system explains itself from its own objects.
You can inspect a rule family without reading parser control flow.

All rule IDs use the ``fi.*`` namespace (e.g. ``fi.section_ref``).
Every ``ParseWitness.rule_id`` and ``SurfaceWitness.rule_id`` must resolve
to an entry in this catalog (or be an allowed catch-all ID).

Rule categories:
    structural   — section/chapter/part/appendix/nimike references
    insertion    — insertion patterns (uusi §, momentti, etc.)
    sub_target   — sub-target within insertion context
    sub_ref      — sub-reference qualifiers (momentti, kohta, otsikko, johd)
    resolution   — context-dependent resolution (backrefs, anaphoric)
    renumber     — renumbering patterns (§:n numero N:ksi, jolloin)
    meta         — non-structural (commencement, expiry, transition, delegation)
    text_amend   — textual amendment patterns (sanan X tilalle Y)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# RuleExample
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleExample:
    """One concrete example for a parse rule.

    Attributes:
        input_text:          Raw johtolause text fragment the rule handles.
        expected_node_kind:  SurfaceNode type or op-code family this produces
                             (e.g. "SurfaceTargetRef", "SurfaceInsertion",
                             "SurfaceMetaClause", or "K P", "L P", ...).
        expected_fields:     Key/value pairs extracted from the node or op
                             to spot-check the result.  Stored as a
                             read-only MappingProxyType.
        description:         Optional human note on what this example tests.
    """

    input_text: str
    expected_node_kind: str
    _expected_fields: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def __init__(
        self,
        input_text: str,
        expected_node_kind: str,
        expected_fields: dict[str, str] | None = None,
        description: str = "",
    ) -> None:
        from types import MappingProxyType

        object.__setattr__(self, "input_text", input_text)
        object.__setattr__(self, "expected_node_kind", expected_node_kind)
        object.__setattr__(
            self,
            "_expected_fields",
            MappingProxyType(expected_fields) if expected_fields else MappingProxyType({}),
        )
        object.__setattr__(self, "description", description)

    @property
    def expected_fields(self) -> dict[str, str]:
        return self._expected_fields  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# ParseRule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseRule:
    """A first-class parse rule with stable identity and examples.

    Attributes:
        rule_id:     Stable dot-namespaced identifier, e.g. "fi.section_ref".
                     Matches the corresponding ConstructionRule.id where one
                     exists (prefixed with "fi." instead of the bare category).
        description: Human-readable one-liner on what the rule matches.
        node_kind:   Primary SurfaceNode or output type, e.g. "SurfaceTargetRef".
        examples:    Concrete example inputs with expected outputs.
        category:    Rule family: "structural", "insertion", "sub_ref",
                     "resolution", "renumber", "meta", "text_amend".
        shape:       Compact token-pattern notation (optional).
    """

    rule_id: str
    description: str
    node_kind: str
    examples: tuple[RuleExample, ...]
    category: str = ""
    shape: str = ""


# ---------------------------------------------------------------------------
# RuleRegistry
# ---------------------------------------------------------------------------


class RuleRegistry:
    """Registry of all Finland parse rules, indexed by rule_id."""

    def __init__(self) -> None:
        self._rules: dict[str, ParseRule] = {}

    def register(self, rule: ParseRule) -> None:
        """Register a rule. Raises if the rule_id is already registered."""
        if rule.rule_id in self._rules:
            raise ValueError(f"Duplicate rule_id: {rule.rule_id!r}")
        self._rules[rule.rule_id] = rule

    def get(self, rule_id: str) -> ParseRule | None:
        """Return the rule with this ID, or None."""
        return self._rules.get(rule_id)

    def all_rules(self) -> list[ParseRule]:
        """Return all registered rules in insertion order."""
        return list(self._rules.values())

    def rules_by_category(self, category: str) -> list[ParseRule]:
        """Return all rules in the given category."""
        return [r for r in self._rules.values() if r.category == category]

    def rules_by_node_kind(self, kind: str) -> list[ParseRule]:
        """Return all rules that produce the given node kind."""
        return [r for r in self._rules.values() if r.node_kind == kind]

    def example_corpus(self) -> list[tuple[ParseRule, RuleExample]]:
        """Return all (rule, example) pairs from all registered rules."""
        pairs: list[tuple[ParseRule, RuleExample]] = []
        for rule in self._rules.values():
            for ex in rule.examples:
                pairs.append((rule, ex))
        return pairs

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def __len__(self) -> int:
        return len(self._rules)

    def __repr__(self) -> str:
        return f"RuleRegistry({len(self._rules)} rules)"


# ---------------------------------------------------------------------------
# Registry population
# ---------------------------------------------------------------------------


def _build_registry() -> RuleRegistry:
    reg = RuleRegistry()

    # -----------------------------------------------------------------------
    # STRUCTURAL — section/chapter/part/appendix/nimike target references
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.section_ref",
            description="Section reference: number_list § with optional sub-refs",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NUM+ PYKALA [SUB_REF]",
            examples=(
                RuleExample(
                    input_text="muutetaan 12 §",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "SECTION", "label": "12"},
                    description="basic single section ref",
                ),
                RuleExample(
                    input_text="muutetaan 3, 5 ja 7 §",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "SECTION"},
                    description="comma+ja section list",
                ),
                RuleExample(
                    input_text="muutetaan 21–23 §",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "SECTION"},
                    description="section range expansion",
                ),
                RuleExample(
                    input_text="muutetaan 5 a §",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "SECTION", "label": "5a"},
                    description="letter-suffix section",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.chapter_ref",
            description="Chapter reference: number_list LUKU with optional sub-refs",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NUM+ LUKU [SUB_REF]",
            examples=(
                RuleExample(
                    input_text="kumotaan 3 luku",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "CHAPTER", "label": "3"},
                    description="basic chapter repeal",
                ),
                RuleExample(
                    input_text="muutetaan 5 luvun otsikko",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "CHAPTER", "label": "5"},
                    description="chapter heading",
                ),
                RuleExample(
                    input_text="muutetaan 3 luvun 12 §:n 2 momentti",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "SECTION", "label": "12", "chapter": "3"},
                    description="chapter context propagation to section",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.part_ref",
            description="Part reference: number_list OSA (often Roman numerals)",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NUM+ OSA",
            examples=(
                RuleExample(
                    input_text="muutetaan 1 osa",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "PART", "label": "1"},
                    description="Arabic numeral part",
                ),
                RuleExample(
                    input_text="muutetaan III ja V osa",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "PART"},
                    description="Roman numeral part list",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.nimike_ref",
            description="Title (nimike) reference — the statute's own title",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NIMIKE",
            examples=(
                RuleExample(
                    input_text="muutetaan nimike ja 1 §",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "NIMIKE"},
                    description="nimike before section list",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.appendix_ref",
            description="Appendix reference: LIITE with optional number",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="LIITE [NUM]",
            examples=(
                RuleExample(
                    input_text="muutetaan 1 § ja liite",
                    expected_node_kind="SurfaceTargetRef",
                    expected_fields={"kind": "APPENDIX"},
                    description="appendix after section list",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.lukuun_ottamatta_exception",
            description="Exception qualifier: lukuun ottamatta number_list § (exception from scope)",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="LUKU:ILL OTTAMATTA NUM+ PYKALA",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.scope_block_chapter",
            description="Scope block: chapter-scoped group of section targets",
            node_kind="SurfaceScopeBlock",
            category="structural",
            shape="NUM LUKU:GEN targets",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.scope_block_part",
            description="Scope block: part-scoped group of section/chapter targets",
            node_kind="SurfaceScopeBlock",
            category="structural",
            shape="NUM OSA:GEN targets",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # INSERTION patterns
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.insertion_section_ill",
            description="Insert into section (illative): number §:ILL uusi sub_target",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="NUM+ PYKALA:ILL [REINST] UUSI SUB_TARGET",
            examples=(
                RuleExample(
                    input_text="lisätään 8 §:ään uusi 3 momentti",
                    expected_node_kind="SurfaceInsertion",
                    expected_fields={"kind": "SECTION", "label": "8"},
                    description="insert momentti into section",
                ),
                RuleExample(
                    input_text="lisätään 8 §:ään uusi 3 ja 4 momentti",
                    expected_node_kind="SurfaceInsertion",
                    description="insert multiple momenti",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_momentti_ill",
            description="Insert into momentti (illative): §:GEN number MOMENTTI:ILL uusi sub_target",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="NUM PYKALA:GEN NUM MOMENTTI:ILL [REINST] UUSI SUB_TARGET",
            examples=(
                RuleExample(
                    input_text="lisätään 3 §:n 1 momenttiin uusi 5 kohta",
                    expected_node_kind="SurfaceInsertion",
                    expected_fields={"kind": "SECTION", "label": "3"},
                    description="insert kohta into momentti",
                ),
                RuleExample(
                    input_text="lisätään 3 §:n 1 momenttiin uusi 10 ja 11 kohta",
                    expected_node_kind="SurfaceInsertion",
                    description="insert multiple kohdats",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_law_level",
            description="Law-level insert: DOC:ILL uusi number_list §/LUKU",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="DOC:ILL UUSI NUM+ PYKALA|LUKU",
            examples=(
                RuleExample(
                    input_text="lisätään lakiin uusi 5 a §",
                    expected_node_kind="SurfaceInsertion",
                    expected_fields={"kind": "SECTION", "label": "5a"},
                    description="law-level section insert with letter suffix",
                ),
                RuleExample(
                    input_text="lisätään lakiin uusi 3 luku",
                    expected_node_kind="SurfaceInsertion",
                    expected_fields={"kind": "CHAPTER", "label": "3"},
                    description="law-level chapter insert",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_chapter_ill",
            description="Insert into chapter (illative): number LUKU:ILL uusi sub_target",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="NUM LUKU:ILL [REINST] UUSI NUM+ PYKALA|LUKU",
            examples=(
                RuleExample(
                    input_text="lisätään 10 lukuun siitä lailla 361/1999 kumotun 14 §:n tilalle uusi 14 §",
                    expected_node_kind="SurfaceInsertion",
                    expected_fields={"kind": "SECTION", "label": "14", "chapter": "10"},
                    description="chapter-illative reinstatement insert",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_chapter_anaphoric",
            description="Anaphoric chapter insert: LUKU:ILL uusi number_list §/LUKU",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="LUKU:ILL [REINST] UUSI NUM+ PYKALA|LUKU",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_chapter_scoped",
            description="Chapter-scoped section insert: number LUKU:GEN number §:ILL uusi sub_target",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="NUM LUKU:GEN NUM PYKALA:ILL UUSI SUB_TARGET",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # HEADING PLACEMENT
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.heading_placement",
            description="Heading insertion before a section: N §:n edelle uusi väliotsikko / luvun otsikko",
            node_kind="SurfaceHeadingPlacement",
            category="structural",
            shape="NUM PYKALA EDELLE UUSI VALIOTSIKKO|LUVUN_OTSIKKO",
            examples=(
                RuleExample(
                    input_text="lisätään lakiin uusi 53 a § ja 53 §:n edelle uusi luvun otsikko",
                    expected_node_kind="SurfaceHeadingPlacement",
                    expected_fields={"target_section": "53"},
                    description="heading placement before section 53",
                ),
                RuleExample(
                    input_text="sekä lisätään lakiin uusi 25 a ja 25 b §, 38 §:n edelle uusi väliotsikko",
                    expected_node_kind="SurfaceHeadingPlacement",
                    expected_fields={"target_section": "38"},
                    description="heading placement (väliotsikko) before section 38",
                ),
            ),
        )
    )

    # -----------------------------------------------------------------------
    # SUB-REFERENCE qualifiers
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.sub_ref_momentti",
            description="Subsection qualifier: number_list MOMENTTI[:GEN]",
            node_kind="SurfaceSubRef",
            category="sub_ref",
            shape="NUM+ MOMENTTI[:GEN]",
            examples=(
                RuleExample(
                    input_text="muutetaan 5 §:n 2 momentti",
                    expected_node_kind="SurfaceSubRef",
                    expected_fields={"momentti": "2"},
                    description="section sub-ref: momentti",
                ),
                RuleExample(
                    input_text="muutetaan 70 §:n 2 momentin 1 ja 3 kohta",
                    expected_node_kind="SurfaceSubRef",
                    expected_fields={"momentti": "2"},
                    description="momentti with multiple kohta",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_ref_kohta",
            description="Item qualifier: number_list KOHTA",
            node_kind="SurfaceSubRef",
            category="sub_ref",
            shape="NUM+ KOHTA",
            examples=(
                RuleExample(
                    input_text="muutetaan 5 §:n 1 momentin 3 kohta",
                    expected_node_kind="SurfaceSubRef",
                    expected_fields={"item": "3"},
                    description="basic kohta sub-ref",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_ref_otsikko",
            description="Heading qualifier: OTSIKKO (section or chapter heading)",
            node_kind="SurfaceSubRef",
            category="sub_ref",
            shape="OTSIKKO",
            examples=(
                RuleExample(
                    input_text="muutetaan 6 §:n otsikko",
                    expected_node_kind="SurfaceSubRef",
                    expected_fields={"special": "otsikko"},
                    description="section otsikko sub-ref",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_ref_johdantokappale",
            description="Introductory paragraph qualifier: JOHDANTOKAPPALE",
            node_kind="SurfaceSubRef",
            category="sub_ref",
            shape="JOHD",
            examples=(
                RuleExample(
                    input_text="muutetaan 15 §:n johdantokappale",
                    expected_node_kind="SurfaceSubRef",
                    expected_fields={"special": "johd"},
                    description="section johd sub-ref",
                ),
            ),
        )
    )

    # -----------------------------------------------------------------------
    # RESOLUTION — backrefs, anaphoric, valiotsikko heading
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.backref_singular",
            description="Singular back-reference: mainitun pykälän [sub_ref]",
            node_kind="SurfaceBackRef",
            category="resolution",
            shape="BACKREF:SG PYKALA [SUB_REF]",
            examples=(
                RuleExample(
                    input_text="muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti",
                    expected_node_kind="SurfaceBackRef",
                    expected_fields={"referent_type": "singular"},
                    description="mainitun pykälän genitive backref",
                ),
                RuleExample(
                    input_text="muutetaan 11 §:n numero 13:ksi ja mainittu pykälä",
                    expected_node_kind="SurfaceBackRef",
                    expected_fields={"referent_type": "singular"},
                    description="mainittu pykälä nominative backref",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.backref_plural",
            description="Plural back-reference: mainittujen pykälien [sub_ref]",
            node_kind="SurfaceBackRef",
            category="resolution",
            shape="BACKREF:PL PYKALA [SUB_REF]",
            examples=(
                RuleExample(
                    input_text="muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien 1 momentti",
                    expected_node_kind="SurfaceBackRef",
                    expected_fields={"referent_type": "plural"},
                    description="mainittujen pykälien plural backref",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.valiotsikko_heading_ref",
            description="Valiotsikko heading back-reference: sen edellä oleva väliotsikko",
            node_kind="SurfaceValiotsikkoRef",
            category="resolution",
            shape="VALIOTSIKKO → otsikko ops for preceding section(s)",
            examples=(
                RuleExample(
                    input_text="muutetaan 5 § ja sen edellä oleva väliotsikko",
                    expected_node_kind="SurfaceValiotsikkoRef",
                    description="section ja sen edellä oleva väliotsikko",
                ),
                RuleExample(
                    input_text="muutetaan 10 §:n 2 momentti sekä pykälän edellä olevan väliotsikon sanamuoto",
                    expected_node_kind="SurfaceValiotsikkoRef",
                    description="pykälän valiotsikko sanamuoto",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.anaphoric_pykala_ill",
            description="Anaphoric §:ILL insertion: pykälään uusi N momentti/kohta",
            node_kind="SurfaceInsertion",
            category="resolution",
            shape="PYKALA:ILL [REINST] UUSI SUB_TARGET",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.anaphoric_momentti_ill",
            description="Anaphoric MOMENTTI:ILL insertion: N momenttiin [prov] uusi sub_target (inherits section)",
            node_kind="SurfaceInsertion",
            category="resolution",
            shape="NUM MOMENTTI:ILL [PROV] UUSI SUB_TARGET",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.anaphoric_bare_uusi",
            description="Bare anaphoric insertion: uusi N momentti/kohta (inherits section)",
            node_kind="SurfaceInsertion",
            category="resolution",
            shape="UUSI NUM+ MOMENTTI|KOHTA",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.cross_verb_momentti",
            description="Cross-verb-group: MOMENTTI:ILL uusi sub_target (inherits section from VerbGroupContext)",
            node_kind="SurfaceInsertion",
            category="resolution",
            shape="MOMENTTI:ILL [REINST] UUSI SUB_TARGET",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.cross_verb_bare_uusi",
            description="Cross-verb-group: uusi sub_target (inherits section from VerbGroupContext)",
            node_kind="SurfaceInsertion",
            category="resolution",
            shape="UUSI SUB_TARGET",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.direct_section_relabel",
            description="Direct section relabel from context: §:n numero M:ksi resolved to renumber",
            node_kind="SurfaceTargetRef",
            category="resolution",
            shape="NUM+ PYKALA NUMERO NUM+",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.cross_verb_move_retarget",
            description="Cross-verb-group move retarget: section moved to a different chapter",
            node_kind="SurfaceCrossVerbMoveTail",
            category="resolution",
            shape="NUM PYKALA NUM LUKU:ILL",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # RENUMBER — §:n numero N:ksi, jolloin renumber clauses
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # SUB-TARGET — sub-target within insertion context
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.sub_target_momentti",
            description="Insert subsection: number_list MOMENTTI",
            node_kind="SurfaceSubRef",
            category="sub_target",
            shape="NUM+ MOMENTTI",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_target_kohta",
            description="Insert item: number_list KOHTA",
            node_kind="SurfaceSubRef",
            category="sub_target",
            shape="NUM+ KOHTA",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_target_pykala",
            description="Insert section (within insertion): number_list PYKALA",
            node_kind="SurfaceSubRef",
            category="sub_target",
            shape="NUM+ PYKALA",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.sub_target_luku",
            description="Insert chapter (within insertion): number_list LUKU",
            node_kind="SurfaceSubRef",
            category="sub_target",
            shape="NUM+ LUKU",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.section_renumber",
            description="Section renumber: number_list §:n numero N:ksi",
            node_kind="SurfaceTargetRef",
            category="renumber",
            shape="NUM+ PYKALA NUMERO NUM+",
            examples=(
                RuleExample(
                    input_text="muutetaan 1 §:n numero 3:ksi",
                    expected_node_kind="SurfaceRenumberTail",
                    expected_fields={"new_label": "3"},
                    description="basic section renumber",
                ),
                RuleExample(
                    input_text="muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien otsikot",
                    expected_node_kind="SurfaceRenumberTail",
                    description="plural section renumber with backref",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.chapter_renumber",
            description="Chapter renumber: number_list luvun numero N:ksi",
            node_kind="SurfaceTargetRef",
            category="renumber",
            shape="NUM+ LUKU NUMERO NUM+",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.part_renumber",
            description="Part renumber: number_list osan numero N:ksi",
            node_kind="SurfaceTargetRef",
            category="renumber",
            shape="NUM+ OSA NUMERO NUM+",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.jolloin_chapter_renumber",
            description="Jolloin chapter renumber: jolloin nykyinen N luku siirtyy M luvuksi",
            node_kind="SurfaceMoveTail",
            category="renumber",
            shape="JOLLOIN NUM+ LUKU VERB:siirtyy NUM+ LUKU",
            examples=(
                RuleExample(
                    input_text="lisätään 7 §:ään uusi 4 ja 5 momentti, jolloin nykyinen 4-8 momentti siirtyvät 6-10 momentiksi",
                    expected_node_kind="SurfaceMoveTail",
                    description="jolloin momentti renumber (move tail)",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.jolloin_section_renumber",
            description="Jolloin section renumber: jolloin nykyinen N § siirtyy M §:ksi",
            node_kind="SurfaceMoveTail",
            category="renumber",
            shape="JOLLOIN NUM+ PYKALA VERB:siirtyy NUM+ [LETTER] PYKALA",
            examples=(
                RuleExample(
                    input_text="lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi",
                    expected_node_kind="SurfaceMoveTail",
                    description="jolloin section renumber with letter suffix",
                ),
                RuleExample(
                    input_text="lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi",
                    expected_node_kind="SurfaceMoveTail",
                    description="jolloin section renumber numeric dest",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.renumber_backref",
            description="Renumber backref continuation: mainitun/mainittujen pykälän sub_ref",
            node_kind="SurfaceBackRef",
            category="renumber",
            shape="BACKREF PYKALA SUB_REF",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.jolloin_renumber",
            description="Jolloin-enriched renumber tail from annotation pass (api.py Phase 1b)",
            node_kind="SurfaceRenumberTail",
            category="renumber",
            shape="JOLLOIN RENUMBER_PAIR",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # CATCH-ALL insertion rules (used by surface_parse for generic insertions)
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.insertion_section",
            description="Catch-all: generic section insertion",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_chapter",
            description="Catch-all: generic chapter insertion",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_heading",
            description="Catch-all: generic heading insertion",
            node_kind="SurfaceHeadingPlacement",
            category="insertion",
            shape="",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_sub_target",
            description="Catch-all: generic sub-target insertion",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.insertion_other",
            description="Catch-all: unclassified insertion pattern",
            node_kind="SurfaceInsertion",
            category="insertion",
            shape="",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.heading_edelle_luvun_otsikko",
            description="Heading placement: edelle luvun otsikko pattern",
            node_kind="SurfaceHeadingPlacement",
            category="structural",
            shape="NUM PYKALA EDELLE LUVUN OTSIKKO",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # META clauses (commencement, expiry, transition, delegation)
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.meta_commencement",
            description="Commencement clause: Tämä laki tulee voimaan [date]",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="tulee|tuli voimaan",
            examples=(
                RuleExample(
                    input_text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "commencement"},
                    description="standard commencement clause",
                ),
                RuleExample(
                    input_text="Tämä asetus tulee voimaan 15 päivänä maaliskuuta 2023.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "commencement"},
                    description="asetus commencement clause",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.meta_expiry",
            description="Expiry clause: Tämä laki on voimassa [until date]",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="on voimassa | voimassaoloaika",
            examples=(
                RuleExample(
                    input_text="Tämä laki on voimassa 31 päivään joulukuuta 2025.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "expiry"},
                    description="standard expiry clause",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.meta_transition",
            description="Transition/applicability clause: siirtymäsäännös, tätä lakia sovelletaan",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="siirtymäsäännös | tätä lakia sovelletaan | ennen voimaantuloa",
            examples=(
                RuleExample(
                    input_text="Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tuleviin asioihin.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "transition"},
                    description="applicability transition sentence",
                ),
                RuleExample(
                    input_text="Ennen tämän lain voimaantuloa vireille tulleisiin asioihin sovelletaan aiempaa lakia.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "transition"},
                    description="pre-commencement transition provision",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.meta_delegation",
            description="Delegation clause: antaa tarkempia säännöksiä/määräyksiä",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="antaa [tarkempia] säännöksiä|määräyksiä",
            examples=(
                RuleExample(
                    input_text="Valtioneuvoston asetuksella voidaan antaa tarkempia säännöksiä lain täytäntöönpanosta.",
                    expected_node_kind="SurfaceMetaClause",
                    expected_fields={"kind": "delegation"},
                    description="ministerial delegation provision",
                ),
            ),
        )
    )

    # -----------------------------------------------------------------------
    # TEXT AMEND — word/phrase substitution patterns
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="fi.text_amend_sana",
            description='Text amendment: sana "X" korvataan sanalla "Y" (single word replacement)',
            node_kind="SurfaceTextAmend",
            category="text_amend",
            shape='[N §:n [M momentissa]] sana "X" korvataan sanalla "Y"',
            examples=(
                RuleExample(
                    input_text='5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"',
                    expected_node_kind="SurfaceTextAmend",
                    expected_fields={"old_text": "lääninhallitus", "new_text": "aluehallintovirasto"},
                    description="section+momentti scoped single word replacement",
                ),
                RuleExample(
                    input_text='3 §:ssä sana "terveyskeskus" korvataan sanalla "hyvinvointialue"',
                    expected_node_kind="SurfaceTextAmend",
                    expected_fields={"old_text": "terveyskeskus", "new_text": "hyvinvointialue"},
                    description="section-inessive scoped single word replacement",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.text_amend_sanat",
            description='Text amendment: sanat "X" korvataan sanoilla "Y" (multi-word replacement)',
            node_kind="SurfaceTextAmend",
            category="text_amend",
            shape='[N §:n [M momentissa]] sanat "X" korvataan sanoilla "Y"',
            examples=(
                RuleExample(
                    input_text='sanat "kauppa- ja teollisuusministeriö" korvataan sanoilla "työ- ja elinkeinoministeriö"',
                    expected_node_kind="SurfaceTextAmend",
                    expected_fields={
                        "old_text": "kauppa- ja teollisuusministeriö",
                        "new_text": "työ- ja elinkeinoministeriö",
                    },
                    description="unscoped multi-word replacement (agency rename)",
                ),
            ),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.text_amend_target",
            description="Text amendment target: section ref within text amend context (api.py regex)",
            node_kind="SurfaceTargetRef",
            category="text_amend",
            shape="NUM PYKALA [MOMENTTI]",
            examples=(),
        )
    )

    # -----------------------------------------------------------------------
    # META PARSE — dynamic meta_parse:<kind> IDs from meta_parse.py
    # -----------------------------------------------------------------------

    reg.register(
        ParseRule(
            rule_id="meta_parse:commencement",
            description="Meta-parse commencement: tulee/tuli voimaan (sentence-level heuristic)",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="tulee|tuli voimaan",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="meta_parse:expiry",
            description="Meta-parse expiry: on voimassa (sentence-level heuristic)",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="on voimassa",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="meta_parse:transition",
            description="Meta-parse transition: siirtymäsäännös / tätä lakia sovelletaan (sentence-level heuristic)",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="siirtymäsäännös | sovelletaan",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="meta_parse:delegation",
            description="Meta-parse delegation: antaa säännöksiä/määräyksiä (sentence-level heuristic)",
            node_kind="SurfaceMetaClause",
            category="meta",
            shape="antaa säännöksiä|määräyksiä",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.chapter_ref_reversed",
            description="Chapter reference with reversed numeric order (e.g. 5-2 luku)",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NUM+ LUKU (reversed range)",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.heading_edelle_otsikko_after_uusi",
            description="Heading placement before section after uusi verb: uusi N § edellä otsikko",
            node_kind="SurfaceHeadingPlacement",
            category="structural",
            shape="UUSI NUM PYKALA EDELLA OTSIKKO",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.including_preceding_heading_target",
            description="Section target including its preceding heading: N § otsikko",
            node_kind="SurfaceTargetRef",
            category="structural",
            shape="NUM PYKALA OTSIKKO",
            examples=(),
        )
    )

    reg.register(
        ParseRule(
            rule_id="fi.target_version_binding",
            description="Version binding: target labels bound to a cited statute version",
            node_kind="SurfaceTargetVersionBinding",
            category="structural",
            shape="NUM PYKALA (sellaisena kuin|siten kuin) STATUTE_REF",
            examples=(),
        )
    )

    return reg


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

FINLAND_RULE_REGISTRY: RuleRegistry = _build_registry()


# ---------------------------------------------------------------------------
# Backward-compatible API (replaces construction_rules.py)
# ---------------------------------------------------------------------------

# Mapping from old category-prefixed IDs to new fi.* IDs.
# surface_parse.py, clause_surface.py, and tests used the old IDs.
_OLD_TO_NEW: dict[str, str] = {
    "target.section_ref": "fi.section_ref",
    "target.chapter_ref": "fi.chapter_ref",
    "target.part_ref": "fi.part_ref",
    "target.nimike_ref": "fi.nimike_ref",
    "target.appendix_ref": "fi.appendix_ref",
    "target.lukuun_ottamatta_exception": "fi.lukuun_ottamatta_exception",
    "insertion.section_ill": "fi.insertion_section_ill",
    "insertion.momentti_ill": "fi.insertion_momentti_ill",
    "insertion.chapter_ill": "fi.insertion_chapter_ill",
    "insertion.chapter_anaphoric": "fi.insertion_chapter_anaphoric",
    "insertion.doc_ill": "fi.insertion_law_level",
    "insertion.chapter_scoped": "fi.insertion_chapter_scoped",
    "insertion.section": "fi.insertion_section",
    "insertion.chapter": "fi.insertion_chapter",
    "insertion.heading": "fi.insertion_heading",
    "insertion.sub_target": "fi.insertion_sub_target",
    "insertion.other": "fi.insertion_other",
    "sub_target.momentti": "fi.sub_target_momentti",
    "sub_target.kohta": "fi.sub_target_kohta",
    "sub_target.pykala": "fi.sub_target_pykala",
    "sub_target.luku": "fi.sub_target_luku",
    "sub_ref.momentti": "fi.sub_ref_momentti",
    "sub_ref.kohta": "fi.sub_ref_kohta",
    "sub_ref.otsikko": "fi.sub_ref_otsikko",
    "sub_ref.johdantokappale": "fi.sub_ref_johdantokappale",
    "resolution.backref_singular": "fi.backref_singular",
    "resolution.backref_plural": "fi.backref_plural",
    "resolution.valiotsikko_ref": "fi.valiotsikko_heading_ref",
    "resolution.anaphoric_pykala_ill": "fi.anaphoric_pykala_ill",
    "resolution.anaphoric_momentti_ill": "fi.anaphoric_momentti_ill",
    "resolution.anaphoric_bare_uusi": "fi.anaphoric_bare_uusi",
    "resolution.cross_verb_momentti": "fi.cross_verb_momentti",
    "resolution.cross_verb_bare_uusi": "fi.cross_verb_bare_uusi",
    "resolution.cross_verb_move_retarget": "fi.cross_verb_move_retarget",
    "resolution.direct_section_relabel_from_context": "fi.direct_section_relabel",
    "renumber.section_numero": "fi.section_renumber",
    "renumber.backref": "fi.renumber_backref",
    "renumber.jolloin_chapter": "fi.jolloin_chapter_renumber",
    "renumber.jolloin_section": "fi.jolloin_section_renumber",
    "heading.edelle_luvun_otsikko": "fi.heading_edelle_luvun_otsikko",
    "scope_block.chapter": "fi.scope_block_chapter",
    "scope_block.part": "fi.scope_block_part",
}

ALL_RULES: dict[str, ParseRule] = {r.rule_id: r for r in FINLAND_RULE_REGISTRY.all_rules()}


def get_rule(rule_id: str) -> ParseRule | None:
    """Look up a rule by ID.  Accepts both fi.* and legacy IDs."""
    result = ALL_RULES.get(rule_id)
    if result is not None:
        return result
    new_id = _OLD_TO_NEW.get(rule_id)
    if new_id is not None:
        return ALL_RULES.get(new_id)
    return None


def all_rule_ids() -> frozenset[str]:
    """Return all known rule IDs (fi.* namespace)."""
    return frozenset(ALL_RULES.keys())
