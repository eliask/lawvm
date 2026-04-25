"""Construction rule inventory for Finnish amendment clause parsing.

Each grammar construction in peg3.py has a stable string ID defined here.
Parse witnesses reference these IDs to record which rule produced each op.

The Pro PEG3 review: "the more honest description is: scanner + annotations +
hand-built construction matcher.  Turn more of those constructions into data."

This module is the data.  The rule IDs are stable across parser versions —
they name the CONSTRUCTION, not the implementation.  If the parser's internal
structure changes, the same constructions still get the same IDs.

Rule categories:
    target.*      — top-level target references (produce ParsedOps)
    insertion.*   — insertion patterns (container + sub-target)
    sub_target.*  — sub-target within insertion context
    sub_ref.*     — sub-reference qualifiers (momentti, item, heading)
    resolution.*  — context-dependent resolution (backrefs, anaphoric)
    renumber.*    — renumber-specific patterns
    meta.*        — non-structural patterns
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ConstructionRule:
    """A named grammar construction.

    Attributes:
        id:          Stable identifier (e.g. "target.section_ref").
        category:    Rule category ("target", "insertion", "resolution", ...).
        description: Human-readable one-liner.
        shape:       Compact notation of the construction's shape.
                     Uses token categories and grammar terms.
    """
    id: str
    category: str
    description: str
    shape: str = ""


# ---------------------------------------------------------------------------
# Rule inventory
# ---------------------------------------------------------------------------

# Target-level rules (produce ParsedOps from explicit token patterns)
SECTION_REF = ConstructionRule(
    id="target.section_ref",
    category="target",
    description="Section reference: [part_ctx] [chapter_ctx] number_list § sub_ref?",
    shape="[PART_CTX] [CH_CTX] NUM+ PYKALA [SUB_REF]",
)
CHAPTER_REF = ConstructionRule(
    id="target.chapter_ref",
    category="target",
    description="Chapter reference: number_list LUKU sub_ref?",
    shape="NUM+ LUKU [SUB_REF]",
)
PART_REF = ConstructionRule(
    id="target.part_ref",
    category="target",
    description="Part reference: number_list OSA",
    shape="NUM+ OSA",
)
NIMIKE_REF = ConstructionRule(
    id="target.nimike_ref",
    category="target",
    description="Title (nimike) reference",
    shape="NIMIKE",
)
APPENDIX_REF = ConstructionRule(
    id="target.appendix_ref",
    category="target",
    description="Appendix reference: LIITE number?",
    shape="LIITE [NUM]",
)

# Insertion patterns (container + uusi + sub_target)
INSERTION_SECTION_ILL = ConstructionRule(
    id="insertion.section_ill",
    category="insertion",
    description="Insert into section: number_list §:ILL uusi sub_target",
    shape="NUM+ PYKALA:ILL [REINST] UUSI SUB_TARGET",
)
INSERTION_MOMENTTI_ILL = ConstructionRule(
    id="insertion.momentti_ill",
    category="insertion",
    description="Insert into momentti: number §:GEN number MOMENTTI:ILL uusi sub_target",
    shape="NUM PYKALA:GEN NUM MOMENTTI:ILL [REINST] UUSI SUB_TARGET",
)
INSERTION_CHAPTER_ILL = ConstructionRule(
    id="insertion.chapter_ill",
    category="insertion",
    description="Insert into chapter: number LUKU:ILL uusi number_list §/LUKU",
    shape="NUM LUKU:ILL [REINST] UUSI NUM+ PYKALA|LUKU",
)
INSERTION_CHAPTER_ANAPHORIC = ConstructionRule(
    id="insertion.chapter_anaphoric",
    category="insertion",
    description="Anaphoric chapter insert: LUKU:ILL uusi number_list §/LUKU",
    shape="LUKU:ILL [REINST] UUSI NUM+ PYKALA|LUKU",
)
INSERTION_DOC_ILL = ConstructionRule(
    id="insertion.doc_ill",
    category="insertion",
    description="Doc-level insert: DOC:ILL uusi number_list §/LUKU",
    shape="DOC:ILL UUSI NUM+ PYKALA|LUKU",
)
INSERTION_CHAPTER_SCOPED = ConstructionRule(
    id="insertion.chapter_scoped",
    category="insertion",
    description="Chapter-scoped section insert: number LUKU:GEN number §:ILL uusi sub_target",
    shape="NUM LUKU:GEN NUM PYKALA:ILL UUSI SUB_TARGET",
)

# Sub-target rules (within insertion context)
SUB_TARGET_MOMENTTI = ConstructionRule(
    id="sub_target.momentti",
    category="sub_target",
    description="Insert subsection: number_list MOMENTTI",
    shape="NUM+ MOMENTTI",
)
SUB_TARGET_KOHTA = ConstructionRule(
    id="sub_target.kohta",
    category="sub_target",
    description="Insert item: number_list KOHTA",
    shape="NUM+ KOHTA",
)
SUB_TARGET_PYKALA = ConstructionRule(
    id="sub_target.pykala",
    category="sub_target",
    description="Insert section (within insertion): number_list PYKALA",
    shape="NUM+ PYKALA",
)
SUB_TARGET_LUKU = ConstructionRule(
    id="sub_target.luku",
    category="sub_target",
    description="Insert chapter (within insertion): number_list LUKU",
    shape="NUM+ LUKU",
)

# Sub-reference rules (qualifiers after § token)
SUB_REF_MOMENTTI = ConstructionRule(
    id="sub_ref.momentti",
    category="sub_ref",
    description="Subsection ref: number_list MOMENTTI:GEN? (number_list KOHTA)?",
    shape="NUM+ MOMENTTI[:GEN] [NUM+ KOHTA]",
)
SUB_REF_KOHTA = ConstructionRule(
    id="sub_ref.kohta",
    category="sub_ref",
    description="Item ref: number_list KOHTA",
    shape="NUM+ KOHTA",
)
SUB_REF_OTSIKKO = ConstructionRule(
    id="sub_ref.otsikko",
    category="sub_ref",
    description="Heading qualifier: OTSIKKO",
    shape="OTSIKKO",
)
SUB_REF_JOHDANTOKAPPALE = ConstructionRule(
    id="sub_ref.johdantokappale",
    category="sub_ref",
    description="Intro paragraph qualifier: JOHDANTOKAPPALE",
    shape="JOHD",
)

# Resolution rules (context-dependent patterns)
BACKREF_SINGULAR = ConstructionRule(
    id="resolution.backref_singular",
    category="resolution",
    description="Singular backref: mainitun pykälän sub_ref",
    shape="BACKREF:SG PYKALA SUB_REF",
)
BACKREF_PLURAL = ConstructionRule(
    id="resolution.backref_plural",
    category="resolution",
    description="Plural backref: mainittujen pykälien sub_ref",
    shape="BACKREF:PL PYKALA SUB_REF",
)
VALIO_REF = ConstructionRule(
    id="resolution.valio_ref",
    category="resolution",
    description="Valio heading reference: VALIOTSIKKO → otsikko ops for preceding section(s)",
    shape="VALIOTSIKKO",
)
ANAPHORIC_PYKALA_ILL = ConstructionRule(
    id="resolution.anaphoric_pykala_ill",
    category="resolution",
    description="Anaphoric §:ILL insertion: pykälään uusi N momentti/kohta",
    shape="PYKALA:ILL [REINST] UUSI SUB_TARGET",
)
ANAPHORIC_BARE_UUSI = ConstructionRule(
    id="resolution.anaphoric_bare_uusi",
    category="resolution",
    description="Bare anaphoric insertion: uusi N momentti/kohta (inherits section)",
    shape="UUSI NUM+ MOMENTTI|KOHTA",
)
CROSS_VERB_MOMENTTI = ConstructionRule(
    id="resolution.cross_verb_momentti",
    category="resolution",
    description="Cross-verb-group: MOMENTTI:ILL uusi sub_target (inherits section from VerbGroupContext)",
    shape="MOMENTTI:ILL [REINST] UUSI SUB_TARGET",
)
CROSS_VERB_BARE_UUSI = ConstructionRule(
    id="resolution.cross_verb_bare_uusi",
    category="resolution",
    description="Cross-verb-group: uusi sub_target (inherits section from VerbGroupContext)",
    shape="UUSI SUB_TARGET",
)

# Renumber-specific
SECTION_RENUMBER = ConstructionRule(
    id="renumber.section_numero",
    category="renumber",
    description="Section renumber: §:n numero N:ksi",
    shape="NUM+ PYKALA NUMERO NUM+",
)
RENUMBER_BACKREF = ConstructionRule(
    id="renumber.backref",
    category="renumber",
    description="Renumber backref continuation: mainitun/mainittujen pykälän sub_ref",
    shape="BACKREF PYKALA SUB_REF",
)
JOLLOIN_CHAPTER_RENUMBER = ConstructionRule(
    id="renumber.jolloin_chapter",
    category="renumber",
    description="Jolloin chapter renumber: jolloin nykyinen N luku siirtyy M luvuksi",
    shape="JOLLOIN NUM+ LUKU VERB:siirtyy NUM+ LUKU",
)
JOLLOIN_SECTION_RENUMBER = ConstructionRule(
    id="renumber.jolloin_section",
    category="renumber",
    description="Jolloin section renumber: jolloin nykyinen N § siirtyy M §:ksi",
    shape="JOLLOIN NUM+ PYKALA VERB:siirtyy NUM+ [LETTER] PYKALA",
)


# ---------------------------------------------------------------------------
# Registry: all rules indexed by ID
# ---------------------------------------------------------------------------

RULE_INVENTORY: tuple[ConstructionRule, ...] = (
    SECTION_REF,
    CHAPTER_REF,
    PART_REF,
    NIMIKE_REF,
    APPENDIX_REF,
    INSERTION_SECTION_ILL,
    INSERTION_MOMENTTI_ILL,
    INSERTION_CHAPTER_ILL,
    INSERTION_CHAPTER_ANAPHORIC,
    INSERTION_DOC_ILL,
    INSERTION_CHAPTER_SCOPED,
    SUB_TARGET_MOMENTTI,
    SUB_TARGET_KOHTA,
    SUB_TARGET_PYKALA,
    SUB_TARGET_LUKU,
    SUB_REF_MOMENTTI,
    SUB_REF_KOHTA,
    SUB_REF_OTSIKKO,
    SUB_REF_JOHDANTOKAPPALE,
    BACKREF_SINGULAR,
    BACKREF_PLURAL,
    VALIO_REF,
    ANAPHORIC_PYKALA_ILL,
    ANAPHORIC_BARE_UUSI,
    CROSS_VERB_MOMENTTI,
    CROSS_VERB_BARE_UUSI,
    SECTION_RENUMBER,
    RENUMBER_BACKREF,
    JOLLOIN_CHAPTER_RENUMBER,
    JOLLOIN_SECTION_RENUMBER,
)

ALL_RULES: dict[str, ConstructionRule] = {rule.id: rule for rule in RULE_INVENTORY}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get_rule(rule_id: str) -> Optional[ConstructionRule]:
    """Look up a construction rule by ID. Returns None if unknown."""
    return ALL_RULES.get(rule_id)


def all_rule_ids() -> frozenset[str]:
    """Return the set of all known construction rule IDs."""
    return frozenset(ALL_RULES.keys())
