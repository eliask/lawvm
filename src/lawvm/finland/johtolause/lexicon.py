"""lexicon — Closed vocabulary and token types for Finnish amendment clauses.

This module owns:
  - The Token dataclass (the classified token type)
  - The complete closed vocabulary of Finnish amendment instruction language
  - Regex patterns used by the lexer for tokenization and compound splitting
  - Helper for determining grammatical case from §-suffixes

No parsing, no control flow, no side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lawvm.finland.source_verb import SourceVerb

# ═══════════════════════════════════════════════════════════════════════
# Token type
# ═══════════════════════════════════════════════════════════════════════


@dataclass(slots=True, frozen=True)
class Token:
    """A classified token in the amendment clause stream."""

    text: str  # original surface form
    lemma: str  # canonical form (lowercase)
    cat: str  # VERB NUM PYKALA LUKU OSA MOMENTTI KOHTA LIITE NIMIKE
    # OTSIKKO JOHD UUSI CONJ COMMA DASH PUNCT DOC WORD
    case: str  # NOM GEN ILL or empty
    verb_code: "SourceVerb | None"
    char_start: int = -1  # character offset in the normalized input string (inclusive)
    char_end: int = -1  # character offset in the normalized input string (exclusive)


# ═══════════════════════════════════════════════════════════════════════
# Vocabulary
# ═══════════════════════════════════════════════════════════════════════

# ---- Surface form → (lemma, cat, case, verb_code) ----
# The complete closed vocabulary of Finnish amendment instruction language.

_V = ""  # no verb code

_VOCAB: dict[str, tuple[str, str, str, SourceVerb]] = {}


def _v(
    surface: str,
    lemma: str,
    cat: str,
    case: str = "",
    verb_code: SourceVerb = SourceVerb.MUUTTAA,
):
    """Register a surface form in the vocabulary."""
    _VOCAB[surface.lower()] = (lemma, cat, case, verb_code)


# Verbs — passive present indicative (the standard form)
_v("muutetaan", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)
_v("kumotaan", "kumota", "VERB", verb_code=SourceVerb.KUMOTA)
_v("lisätään", "lisätä", "VERB", verb_code=SourceVerb.LISATA)
_v("siirretään", "siirtää", "VERB", verb_code=SourceVerb.SIIRTAA)
# Verbs — active past participle (old ministerial decisions, 1980s-90s)
_v("muuttanut", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)
_v("muuttaneet", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)
_v("kumonnut", "kumota", "VERB", verb_code=SourceVerb.KUMOTA)
_v("lisännyt", "lisätä", "VERB", verb_code=SourceVerb.LISATA)
# Verbs — active indicative (rare, pre-1980s)
_v("muuttaa", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)
_v("kumota", "kumota", "VERB", verb_code=SourceVerb.KUMOTA)  # also participle stem
# Verbs — alternative passive forms (rare)
_v("korvataan", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)  # synonym for muutetaan
# Verbs — active 3rd person singular (agency decisions)
_v("lisää", "lisätä", "VERB", verb_code=SourceVerb.LISATA)  # "Verohallinto lisää..."
_v("muuttaa", "muuttaa", "VERB", verb_code=SourceVerb.MUUTTAA)  # already registered but confirm
# Verbs — past participle (additional plural forms)
_v("kumonneet", "kumota", "VERB", verb_code=SourceVerb.KUMOTA)

# Section sign (§) — all inflected forms
_v("§", "§", "PYKALA", "NOM")
_v("§:n", "§", "PYKALA", "GEN")
_v("§:in", "§", "PYKALA", "GEN")
_v("§:en", "§", "PYKALA", "GEN")
_v("§:ään", "§", "PYKALA", "ILL")
_v("§:iin", "§", "PYKALA", "ILL")
_v("§:aan", "§", "PYKALA", "ILL")
_v("§:een", "§", "PYKALA", "ILL")
_v("§.", "§", "PYKALA", "NOM")  # sentence-final
_v("pykälä", "§", "PYKALA", "NOM")
_v("pykälän", "§", "PYKALA", "GEN")
_v("pykälään", "§", "PYKALA", "ILL")
_v("pykälässä", "§", "PYKALA", "NOM")  # inessive (not parsed but occurs)
_v("pykälää", "§", "PYKALA", "NOM")  # partitive
# §-suffixed forms that appear in old texts but are not structural targets
# (inessive, partitive, etc. — treated as WORD for filtering purposes)
_v("§:ää", "§", "PYKALA", "NOM")  # partitive
_v("§:ssä", "§", "PYKALA", "NOM")  # inessive
_v("§:stä", "§", "PYKALA", "NOM")  # elative
_v("§:ksi", "§", "PYKALA", "NOM")  # translative
_v("§:", "§", "PYKALA", "NOM")  # colon-terminated

# Chapter
_v("luku", "luku", "LUKU", "NOM")
_v("luvun", "luku", "LUKU", "GEN")
_v("lukuun", "luku", "LUKU", "ILL")

# Part
_v("osa", "osa", "OSA", "NOM")
_v("osan", "osa", "OSA", "GEN")
_v("osaan", "osa", "OSA", "ILL")

# Subsection
_v("momentti", "momentti", "MOMENTTI", "NOM")
_v("momentin", "momentti", "MOMENTTI", "GEN")
_v("momenttiin", "momentti", "MOMENTTI", "ILL")
_v("momenttia", "momentti", "MOMENTTI", "NOM")  # partitive, treat as nom
_v("momentiksi", "momentti", "MOMENTTI", "NOM")  # translative (in jolloin)
_v("momentista", "momentti", "MOMENTTI", "GEN")  # elative (kumotaan X §:n N momentista M kohta)
_v("momentteja", "momentti", "MOMENTTI", "NOM")  # partitive plural
_v("momentit", "momentti", "MOMENTTI", "NOM")  # plural nom
_v("momentti:", "momentti", "MOMENTTI", "NOM")  # colon-terminated

# Item
_v("kohta", "kohta", "KOHTA", "NOM")
_v("kohdan", "kohta", "KOHTA", "GEN")
_v("kohtaan", "kohta", "KOHTA", "ILL")
_v("kohtaa", "kohta", "KOHTA", "NOM")
_v("kohdat", "kohta", "KOHTA", "NOM")
_v("kohdiksi", "kohta", "KOHTA", "NOM")  # translative (in jolloin)
_v("kohdassa", "kohta", "KOHTA", "NOM")  # inessive
_v("kohdasta", "kohta", "KOHTA", "NOM")  # elative

# Sub-paragraph (consumed but not represented in ops)
_v("alakohta", "alakohta", "ALAKOHTA", "NOM")
_v("alakohdan", "alakohta", "ALAKOHTA", "GEN")

# Heading
_v("otsikko", "otsikko", "OTSIKKO", "NOM")
_v("otsikon", "otsikko", "OTSIKKO", "GEN")
_v("otsikkoa", "otsikko", "OTSIKKO", "NOM")
_v("otsikot", "otsikko", "OTSIKKO", "NOM")  # plural nominative

# Intro paragraph
for _form in (
    "johdantokappale",
    "johdantokappaleen",
    "johdantolause",
    "johdantolauseen",
    "johtolause",
    "johtolauseen",
):
    _v(_form, "johdantokappale", "JOHD", "NOM")

# Sub-heading
for _form in ("väliotsikko", "väliotsikon"):
    _v(_form, "väliotsikko", "OTSIKKO", "NOM")

# Appendix
_v("liite", "liite", "LIITE", "NOM")
_v("liitteen", "liite", "LIITE", "GEN")
_v("liitteeseen", "liite", "LIITE", "ILL")
_v("liitteet", "liite", "LIITE", "NOM")  # plural nominative
_v("liitteitä", "liite", "LIITE", "NOM")  # partitive plural (muutetaan liitteitä 1, 2)
_v("liitteinä", "liite", "LIITE", "NOM")  # essive (in statute names — filter)
_v("liitteenä", "liite", "LIITE", "NOM")  # essive singular

# Title
_v("nimike", "nimike", "NIMIKE", "NOM")
_v("nimikkeen", "nimike", "NIMIKE", "GEN")

# Insertion marker
_v("uusi", "uusi", "UUSI", "NOM")
_v("uudet", "uusi", "UUSI", "NOM")  # plural nominative
_v("uuden", "uusi", "UUSI", "GEN")
_v("uutta", "uusi", "UUSI", "NOM")

# Conjunctions
_v("ja", "ja", "CONJ")
_v("sekä", "sekä", "CONJ")

# End sentinels
_v("seuraavasti", "seuraavasti", "END")
_v("seuraavasti:", "seuraavasti", "END")  # with trailing colon
_v("seuraava", "seuraavasti", "END")
_v("kuuluvaksi", "seuraavasti", "END")  # archaic
_v("kuuluviksi", "seuraavasti", "END")  # archaic

# Document type (for insertion patterns)
_v("lakiin", "laki", "DOC", "ILL")
_v("asetukseen", "asetus", "DOC", "ILL")
_v("säädökseen", "säädös", "DOC", "ILL")
_v("lain", "laki", "DOC", "GEN")
_v("asetuksen", "asetus", "DOC", "GEN")

# Provenance triggers
for _form in ("sellaisena", "sellaisina", "siten", "siltä"):
    _v(_form, _form, "PROV")

# Reinstatement
_v("siitä", "siitä", "REINST")
_v("tilalle", "tilalle", "TILALLE")
_v("sijaan", "tilalle", "TILALLE")  # synonym: "N momentin sijaan uusi N momentti"

# Jolloin
_v("jolloin", "jolloin", "JOLLOIN")

# Temporal modifiers
for _form in ("väliaikaisesti", "tilapäisesti", "määräaikaisesti"):
    _v(_form, _form, "TEMPORAL")

# Language qualifiers (consumed/skipped)
for _form in ("suomenkielinen", "ruotsinkielinen"):
    _v(_form, _form, "LANGQUAL")
_v("kieliasu", "kieliasu", "LANGQUAL")
_v("kieliasun", "kieliasu", "LANGQUAL")
_v("sanamuoto", "sanamuoto", "LANGQUAL")
_v("sanamuodon", "sanamuoto", "LANGQUAL")

# Finnish ordinals (used in place of numbers: "uusi toinen momentti" = "uusi 2 momentti")
_v("toinen", "2", "NUM")  # 2nd
_v("kolmas", "3", "NUM")  # 3rd
_v("neljäs", "4", "NUM")  # 4th
_v("viides", "5", "NUM")  # 5th

# Archaic preamble words (skip)
_v("näin", "näin", "WORD")
_v("kuuluva", "kuuluva", "WORD")
_v("kuuluvan", "kuuluva", "WORD")

# DOC type genitive (for statute names)
_v("säädöksen", "säädös", "DOC", "GEN")

# Heading position words
_v("edellä", "edellä", "EDELLA")
_v("edelle", "edellä", "EDELLA")
_v("oleva", "olla", "WORD")
_v("olevan", "olla", "WORD")

# Demonstrative provenance
_v("siihen", "siihen", "PROV")
_v("niihin", "niihin", "PROV")
_v("myöhemmin", "myöhemmin", "PROV")

# Provenance continuation
_v("viimeksi", "viimeksi", "PROV")

# Renumbering keyword
_v("numero", "numero", "NUMERO")

# Back-reference to previously mentioned section(s) — "mainitun pykälän"
_v("mainitun", "mainittu", "BACKREF", "GEN")  # singular genitive
_v("mainittu", "mainittu", "BACKREF", "NOM")  # singular nominative
_v("mainittujen", "mainittu", "BACKREF", "GEN")  # plural genitive
_v("mainitut", "mainittu", "BACKREF", "NOM")  # plural nominative

# Plural forms of pykälä (for back-references)
_v("pykälien", "§", "PYKALA", "GEN")  # plural genitive
_v("pykälät", "§", "PYKALA", "NOM")  # plural nominative
_v("pykäliä", "§", "PYKALA", "NOM")  # plural partitive


# ═══════════════════════════════════════════════════════════════════════
# Regex patterns for tokenization
# ═══════════════════════════════════════════════════════════════════════

_CITE_RE = re.compile(r"\(\d+/+\d{2,4}\)")  # (YYYY/NNN) compact
_YEAR_NUM_RE = re.compile(r"\d+/+\d{2,4}")  # YYYY/NNN bare
_DASH_CLASS = r"[\-\u2010\u2011\u2012\u2013\u2014\u2015]"
_RANGE_RE = re.compile(rf"^(\d+)\s*{_DASH_CLASS}\s*(\d+)$")  # 21\u201323
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")
_LETTER_RE = re.compile(r"^[a-z]$")
_NUM_RE = re.compile(r"^\d+$")
_SPLIT_RE = re.compile(
    rf"(\s+|,|;|:(?=[^a-z\u00e4\u00f6\u00e5\u00a7])|[()]|\u00a7(?!:)|\u00a7:[a-z\u00e4\u00f6\u00e5]+|(?<=[a-z\u00e4\u00f6\u00e50-9]){_DASH_CLASS}(?=[\d\s])|(?<=\s){_DASH_CLASS}(?=\d)|\d+/+\d{{2,4}}\)?)"
)
# Genitive number pattern: "1:n" \u2192 NUM with genitive flag
_GEN_NUM_RE = re.compile(r"^(\d+):n$")
# Translative number pattern: "3:ksi" \u2192 NUM (renumbering target)
_TRANSLATIVE_NUM_RE = re.compile(r"^(\d+):ksi$")
# Compound token patterns (pre-1980s and Finlex artifacts)
_NPYKALA_RE = re.compile(r"^(\d+[a-z]?)(\u00a7)(:.+)?$")  # 20\u00a7:n
_LETTER_PYKALA_RE = re.compile(r"^([a-z])(\u00a7.*)$")  # a\u00a7:n
_NUM_DASH_STRUCT_RE = re.compile(
    rf"^(\d+[a-z]?){_DASH_CLASS}(kohta|kohdan|momentti|momentin|momenttiin)$", re.I
)
_LETTER_DASH_STRUCT_RE = re.compile(rf"^([a-z]){_DASH_CLASS}(kohta|kohdan)$", re.I)
_LETTER_DASH_NUM_RE = re.compile(rf"^([a-z]){_DASH_CLASS}(\d+)$")


def _case_from_pykala_suffix(suffix: str) -> str:
    """Determine grammatical case from a §-suffix string."""
    if not suffix:
        return "NOM"
    if suffix in (":n", ":in", ":en"):
        return "GEN"
    if suffix in (":ään", ":iin", ":aan", ":een"):
        return "ILL"
    return "NOM"
