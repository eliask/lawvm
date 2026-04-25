"""Clause-family Finland rulebook constants."""

from __future__ import annotations

from lawvm.finland.rulebook.atoms import ClauseAtom
from lawvm.finland.rulebook.common import (
    AuthorityTier,
    CitationRef,
    EmitRef,
    RuleExample,
    RuleHeader,
    RulePhase,
    RuleStrength,
)
from lawvm.finland.rulebook.families import ClauseRule
from lawvm.finland.rulebook.registries import EmitId


CLAUSE_SHARED_INTRO_OVER_CONJUNCTED_MOMENTTI = ClauseRule(
    header=RuleHeader(
        rule_id="fi.clause.shared_intro_over_conjuncted_momentti",
        phase=RulePhase.CLAUSE_PARSE,
        priority=220,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Bind johdantokappale to every coordinated momentti in the same genitive chain",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="§4"),),
        examples=(
            RuleExample(
                label="dual moment intro",
                input_text="muutetaan 20 §:n 2 ja 3 momentin johdantokappale",
                expects=(
                    "replace section:20/subsection:2 facet:intro",
                    "replace section:20/subsection:3 facet:intro",
                ),
            ),
        ),
    ),
    when=(
        ClauseAtom.SECTION_REF,
        ClauseAtom.MOMENTTI_LIST_GEN,
        ClauseAtom.JOHD,
    ),
    emits=(
        EmitRef(
            emit_id=EmitId.EMIT_INTRO_REPLACE_FOR_EACH_MOMENT,
            args=(("section", "sec"), ("moments", "moms")),
        ),
    ),
)

CLAUSE_JOLLOIN_RENUMBER_PAIR = ClauseRule(
    header=RuleHeader(
        rule_id="fi.clause.jolloin_renumber_pair",
        phase=RulePhase.CLAUSE_PARSE,
        priority=210,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Keep jolloin-driven renumber pairs scoped to the immediate pair being renumbered",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="clause rules"),),
        examples=(
            RuleExample(
                label="renumber pair with jolloin",
                input_text="jolloin 3 §:n 1 ja 2 momentit numeroidaan uudelleen",
                expects=("renumber pair:3/1", "renumber pair:3/2"),
            ),
        ),
    ),
    when=(ClauseAtom.SECTION_REF, ClauseAtom.JOLLOIN_RENUMBER_PAIR),
    emits=(
        EmitRef(
            emit_id=EmitId.EMIT_RENUMBER_PAIR_SCOPE,
            args=(("section", "sec"), ("pairs", "pairs")),
        ),
    ),
)

CLAUSE_LUKUUN_O_OTTAMATTA_EXCEPTION_SCOPE = ClauseRule(
    header=RuleHeader(
        rule_id="fi.clause.lukuun_ottamatta_exception_scope",
        phase=RulePhase.CLAUSE_PARSE,
        priority=205,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Keep lukuun ottamatta phrases scoped as exclusions, not as enacted target text",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="clause rules"),),
        examples=(
            RuleExample(
                label="exception scope",
                input_text="muutetaan 1 § lukuun ottamatta 2 momenttia",
                expects=("exclude section:1/subsection:2",),
            ),
        ),
    ),
    when=(ClauseAtom.SECTION_REF, ClauseAtom.LUKUUNOTTAMATTA),
    emits=(
        EmitRef(
            emit_id=EmitId.EMIT_RENUMBER_PAIR_SCOPE,
            args=(("section", "sec"), ("scope", "exception")),
        ),
    ),
)
