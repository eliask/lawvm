"""Finnish statute metadata extraction — date and identifier helpers.

Pure lxml-read-only functions that extract dates, version identifiers, and
the ``johtolause`` text from amendment XML trees.  No grafter state, no
XMLStatute dependency.
"""
from __future__ import annotations

import calendar
import copy
import re
import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Literal, Optional, Set, Tuple, cast

import lxml.etree as etree

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.core.unicode_folds import CF_FORMAT_CPS, ZS_NON_ASCII_SPACE_CPS
from lawvm.finland.fi_dates import (
    FI_MONTH_GENITIVE_TO_NUMBER,
    FiDateForm,
    fi_partitive_month_number,
    match_fi_date,
    parse_fi_day_month_year,
)
from lawvm.finland.helpers import _norm_num_token, _parse_iso_date
from lawvm.finland.references.sections import BodyProvisionTarget, parse_body_provision_tail, parse_body_provision_tail_spanned
from lawvm.finland.temporal_lowering import _extract_expiry_date_from_text


# ---------------------------------------------------------------------------
# Structural-parse text normalisation
# ---------------------------------------------------------------------------
#
# Scope: ONLY applied to text that feeds structural parsers (johtolause,
# voimaantulosäännös, section-reference patterns).  Never applied to body
# text stored in IR nodes or compared against oracle content — we must not
# silently rewrite what a law says.
#
# Rationale: Finlex XML is published across decades by many editors and tools;
# typographic variants carry no legal meaning for identifiers.  A single
# canonical lexical layer here is clearer and more robust than adding each
# new variant to every downstream regex as the need arises.
#
# Why NOT unicodedata.normalize('NFKC', ...):
#   NFKC is tempting because it is the Unicode-blessed "compatibility
#   normalization" form, but it is lossy in ways that matter for legal text:
#     - superscript/subscript digits fold to plain digits (² → 2) — could
#       silently rewrite footnote markers or exponents
#     - Roman numeral code points fold to ASCII (Ⅳ → IV)
#     - vulgar fractions decompose (½ → "1⁄2")
#     - ligatures fold (ﬁ → fi)
#   For a high-assurance legal compiler, any silent semantic fold is wrong by
#   default.  We prefer an explicit, auditable mapping limited to:
#     - horizontal space equivalents (Unicode category Zs), all to U+0020
#     - em-dash → en-dash (a domain-specific Finlex typography mapping)

# ``ZS_NON_ASCII_SPACE_CPS`` and ``CF_FORMAT_CPS`` are imported from
# ``lawvm.core.unicode_folds`` (shared across jurisdictions, drift-guarded by
# ``tests/test_unicode_folds.py``).

# Dash variants → en-dash (U+2013), the standard Finnish range dash.
# Hyphen-minus U+002D is intentionally excluded: it is used in statute IDs
# (e.g. "1996/931") and its meaning differs from a range dash.
# U+2010 HYPHEN, U+2011 NON-BREAKING HYPHEN and U+2015 HORIZONTAL BAR are
# intentionally NOT folded either — if they ever appear in Finlex section
# ranges we want the failure to surface rather than be silently rewritten.
_DASH_TO_EN_DASH: tuple[str, ...] = (
    '\u2014',   # EM DASH — used in older Finlex XML for section ranges
)

# Pre-built translation table combining all character-level folds.  Using
# ``str.translate`` with a single table is both faster than repeated
# ``str.replace`` calls and makes the set of folds declarative in one place.
_TYPO_TRANSLATION_TABLE: dict[int, str] = {
    **{cp: ' ' for cp in ZS_NON_ASCII_SPACE_CPS},
    **{ord(ch): '\u2013' for ch in _DASH_TO_EN_DASH},
    **{cp: '' for cp in CF_FORMAT_CPS},
}
_NORMALIZE_FI_PARSE_TEXT_CACHE_MAX_CHARS = 4096
_normalized_tree_text_cache: dict[tuple[int, int], str] = {}


@dataclass(frozen=True, slots=True)
class SeparateCommencementLawWitness:
    """A source witness that gives an amendment act's deferred commencement date."""

    target_statute_id: str
    commencement_statute_id: str
    source_provision_ref: str
    effective_date: dt.date
    rule_id: str
    source_text: str


_SEPARATE_COMMENCEMENT_LIST_RE = re.compile(
    r'\bSeuraavat\s+lait\s+tulevat\s+voimaan\s+'
    r'(?P<day>\d{1,2})\s+päivänä\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})\s*:',
    flags=re.IGNORECASE,
)
_SEPARATE_COMMENCEMENT_INLINE_LIST_RE = re.compile(
    r'\b(?P<subjects>(?:Laki|Asetus|Päätös)\s+.{0,2000}?)\s+'
    r'tulevat\s+voimaan\s+'
    r'(?P<day>\d{1,2})\s+päivänä\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})',
    flags=re.IGNORECASE,
)
_PAREN_STATUTE_ID_RE = re.compile(r'\((?P<sid>\d{1,4}/\d{4})\)')


def _normalize_fi_parse_text(text: str) -> str:
    """Normalize Finnish legislative text for structural parsing.

    Applies lossless typography normalizations before any regex that extracts
    section numbers, statute identifiers, or date clauses:

    - All Unicode horizontal-space characters (category ``Zs``) → ordinary
      space (U+0020).  This covers NBSP, thin space, narrow NBSP, en/em
      spaces, and every other Zs variant without needing a hand-maintained
      list.
    - Em-dash (U+2014) → en-dash (U+2013): Finnish section ranges appear as
      both "16 a–16 g" (en) and "43 a—43 c" (em) across different Finlex eras.

    This function must NOT be applied to body text that will be stored in IR
    nodes or compared against oracle content.  See the module header comment
    for the rationale of preferring targeted folds over ``unicodedata.NFKC``.
    """
    if len(text) <= _NORMALIZE_FI_PARSE_TEXT_CACHE_MAX_CHARS:
        return _normalize_fi_parse_text_cached(text)
    return text.translate(_TYPO_TRANSLATION_TABLE)


@lru_cache(maxsize=4096)
def _normalize_fi_parse_text_cached(text: str) -> str:
    return text.translate(_TYPO_TRANSLATION_TABLE)


def _normalized_tree_text(tree: "etree._Element", raw_text: str | None = None) -> str:
    """Return parser-normalized full tree text with a per-root content cache."""
    if raw_text is None:
        raw_text = etree.tostring(tree, method="text", encoding="unicode")
    cache_key = (id(tree), hash(raw_text))
    cached = _normalized_tree_text_cache.get(cache_key)
    if cached is not None:
        return cached
    normalized = _normalize_fi_parse_text(raw_text)
    _normalized_tree_text_cache[cache_key] = normalized
    return normalized


def _normalized_entry_into_force_text(
    tree: "etree._Element",
    fallback_full_text: str | None = None,
) -> str:
    eit_els = tree.findall('.//{*}hcontainer[@name="entryIntoForce"]')
    if not eit_els:
        if fallback_full_text is not None:
            return fallback_full_text
        return _normalized_tree_text(tree)
    return _normalize_fi_parse_text(
        " ".join(etree.tostring(el, method="text", encoding="unicode") for el in eit_els)
    )


# ---------------------------------------------------------------------------
# Johtolause verb normalisation constants
# ---------------------------------------------------------------------------

_VERB_NORM_TABLE: List[Tuple[str, str]] = [
    # (participle_pattern, present_tense)
    (r'muuttan(?:ut|eet)', 'muutetaan'),
    (r'kumonn(?:ut|eet)', 'kumotaan'),
    (r'lisänn?(?:yt|eet)', 'lisätään'),
    (r'siirtän(?:yt|eet)', 'siirretään'),
]
_VERB_NORM_PATTERNS: List[Tuple[re.Pattern[str], str]] = []
for _part, _pres in _VERB_NORM_TABLE:
    # 5 positions: "on/ovat X", start-of-line, ", X", "sekä X", "ja X"
    _VERB_NORM_PATTERNS.append((re.compile(rf'\b(?:on|ovat)\s+{_part}\b', re.I), _pres))
    _VERB_NORM_PATTERNS.append((re.compile(rf'^\s*{_part}\b', re.I), _pres))
    _VERB_NORM_PATTERNS.append((re.compile(rf',\s*{_part}\b', re.I), f', {_pres}'))
    _VERB_NORM_PATTERNS.append((re.compile(rf'\bsekä\s+{_part}\b', re.I), f'sekä {_pres}'))
    _VERB_NORM_PATTERNS.append((re.compile(rf'\bja\s+{_part}\b', re.I), f'ja {_pres}'))


_SPLIT_STRUCTURAL_VERB_REPAIRS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmuute\s+taan\b", re.I), "muutetaan"),
]


# Some historical amendment XML contains a malformed section marker immediately
# after the parent statute citation, e.g. ``(772/92) 6 ) seuraavasti:`` where
# the section sign was lost and only a stray closing parenthesis remains.
_LEADING_SECTION_MARKER_AFTER_CITATION_RE = re.compile(
    r"(\(\s*\d+/\d+\s*\)\s*)(\d+\s*[a-z]?)\s*\)(?=\s+seuraavasti\b)",
    re.IGNORECASE,
)


def _repair_leading_section_marker_after_citation(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", "", match.group(2))
        return f"{match.group(1)}{label} §"

    return _LEADING_SECTION_MARKER_AFTER_CITATION_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# Johtolause extraction
# ---------------------------------------------------------------------------

# Strips cross-law descriptive context from johtolause text.
#
# Pattern: "muutetaan [description with §:n / §:ssä refs to another law's
# sections] ([CITATION]) N ja M § seuraavasti:"
#
# The section references before the explicit citation are descriptive (they
# describe the subject-matter context of the cited statute, e.g. "valmiuslain
# 106 §:n 1 momentissa... säädettyjen toimivaltuuksien käyttöönotosta annetun
# valtioneuvoston asetuksen"), NOT the amendment targets. The amendment targets
# (bare nominative "N § seuraavasti:") appear AFTER the citation.
#
# Fix: keep the verb + citation + targets, drop the pre-citation description.
# The citation is preserved so citation_routing still works.
# Anti-backtracking: the lazy ``.{0,400}?`` before a literal ``(`` re-expands
# at every intermediate position on inputs that contain ``(`` runs but no
# trailing citation, re-trying the suffix from each.  The tempered-possessive
# fill ``(?:(?!CITE).){0,400}+`` consumes the same ≤400-char window but cannot
# backtrack: it greedily advances until the first position where the citation
# (the original literal suffix) matches, which is exactly the position the lazy
# form converged on.  Match semantics are identical (verified by 400k-input
# fuzz + a full Finlex corpus replay); the 400-char cap is load-bearing
# (real johtolause citations sit up to exactly 400 chars after the §-ref).
_CROSS_LAW_DESC_PAT = re.compile(
    r'(?:§:[nä]|§:ss[aä])(?:(?!\(\s*\d{3,4}/\d{4}\s*\)).){0,400}+\(\s*(\d{3,4}/\d{4})\s*\)',
    re.DOTALL,
)
_AS_AMENDED_QUALIFIER_PAT = re.compile(r"\bsellais(?:ena|ina)\s+kuin\b", re.IGNORECASE)
_NOMINATIVE_TARGET_PAT = re.compile(r'\d+\s*(?:ja\s+\d+\s*)?§(?!\s*:)')
_OPERATIVE_KEYWORD_PAT = re.compile(
    r"\b(?:kumotaan|muutetaan|lisätään|poistetaan|siirretään)\b",
    re.IGNORECASE,
)
# Anti-backtracking: the old unbounded lazy gap ``(.+?)`` before the distant
# ``tule…kuitenkin voimaan`` anchor expands to end-of-text from every subject
# word (``Lain``/``Sen``/…) on non-matching input — O(N²)+ on long text with
# many subject words and no anchor (measured ~23 s on a 112 KB adversarial
# input).  Two defences, mirroring the codebase convention (cf.
# divergence_heuristics._REPEAL_PRIOR_WORDING_BANNER_RE): (1) the gap is bounded
# to 2000 chars — the largest real-corpus gap is 633 (a long compound
# subsection clause), so 2000 gives >3x margin and keeps corpus output
# byte-identical (asserted in tests); (2) call sites apply a cheap literal
# ``kuitenkin``/``voimaan`` pre-guard so non-matching text never reaches the
# regex.  Match semantics on in-bound gaps are identical (500k-input fuzz +
# full Finlex corpus replay).
_SCOPED_COMMENCEMENT_RE = re.compile(
    r"(?:Tämän\s+lain|Lain|Asetuksen|Päätöksen|Sen)\s+(.{1,2000}?)\s+"
    r"tule(?:vat|e)\s+kuitenkin\s+voimaan(?:\s+(?:jo|vasta))?\s+"
    r"(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})",
    re.IGNORECASE,
)
_SCOPED_APPLICATION_COMMENCEMENT_RE = re.compile(
    r"(?:Tämän\s+lain|Lain|Asetuksen|Päätöksen|Sen)\s+(.{1,2000}?)\s+"
    r"sovelletaan\s+kuitenkin\s+"
    r"(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})",
    re.IGNORECASE,
)
_CHAPTER_COMMENCEMENT_RE = re.compile(
    r"(?:Tämän\s+lain|Lain|Asetuksen|Päätöksen|Sen)\s+"
    r"(?P<chapters>.{1,500}?\bluku)\s+"
    r"tule(?:vat|e)\s+(?:kuitenkin\s+)?voimaan(?:\s+(?:jo|vasta))?\s+"
    r"(?P<day>\d{1,2})\s+päivänä\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_COMMENCEMENT_SUBSECTION_REF_RE = re.compile(
    r"(?:(?P<chapter>\d+\s*[a-z]?)\s+luvun\s+)?"
    r"(?P<section>\d+\s*[a-z]?)\s*§\s*:\s*n\s+"
    r"(?P<subsections>\d+(?:\s*(?:\u2013|-)\s*\d+)?"
    r"(?:\s*(?:,|ja|sekä)\s*\d+(?:\s*(?:\u2013|-)\s*\d+)?)*)"
    r"\s+moment",
    re.IGNORECASE,
)
_COMMENCEMENT_SUBSECTION_LIST_SPLIT_RE = re.compile(r"\s*(?:,|ja|sekä)\s*")
_COMMENCEMENT_SUBSECTION_RANGE_RE = re.compile(r"(\d+)\s*(?:\u2013|-)\s*(\d+)")
_COMMENCEMENT_SUBSECTION_LABEL_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")
_COMMENCEMENT_HEADING_REF_RE = re.compile(
    r"(?P<section>\d+\s*[a-z]?)\s*§\s*:\s*n"
    r"(?:(?!\d+\s*[a-z]?\s*§).){0,160}?\botsikko\b",
    re.IGNORECASE,
)
_COMMENCEMENT_REPEAL_REF_RE = re.compile(
    r"(?P<section>\d+\s*[a-z]?)\s*§\s*:\s*n\s+kumoaminen\b",
    re.IGNORECASE,
)


def _scoped_commencement_guard(text: str) -> bool:
    """Cheap literal pre-guard for ``_SCOPED_COMMENCEMENT_RE``.

    ``kuitenkin`` and either ``voimaan`` or ``sovelletaan`` are mandatory
    literals of the anchor, so
    text lacking either can never match.  Running this O(n) substring check
    first keeps the regex off non-matching input entirely — the key defence
    against the bounded-but-still-superlinear matching path on long text.
    """
    lo = text.lower()
    return "kuitenkin" in lo and ("voimaan" in lo or "sovelletaan" in lo)


def _strip_cross_law_description(text: str) -> str:
    """Remove cross-law descriptive context that precedes a citation.

    When the johtolause describes *which* statute it amends via a long phrase
    containing §:n / §:ssä references (genitive/locative — not targets), and
    then gives the actual section targets AFTER a (YYYY/NNN) citation, the
    pre-citation description confuses the structural parser.

    Example (2021/194 → 2021/186):
        "muutetaan valmiuslain 106 §:n 1 momentissa ja 107 §:ssä säädettyjen
         toimivaltuuksien käyttöönotosta annetun valtioneuvoston asetuksen
         (186/2021) 2 ja 3 § seuraavasti:"
    →
        "muutetaan (186/2021) 2 ja 3 § seuraavasti:"
    """
    m = _CROSS_LAW_DESC_PAT.search(text)
    if not m:
        return text
    if _AS_AMENDED_QUALIFIER_PAT.search(m.group(0)):
        return text
    after = text[m.end():]
    if not _NOMINATIVE_TARGET_PAT.search(after):
        return text
    # lawvm-regex: owning_parser leading-verb capture for the johtolause cross-law strip; structural lexer over already-classified text, mints no op
    verb_m = re.match(r'^\s*(\w+)\s+', text)
    verb = (verb_m.group(1) + ' ') if verb_m else ''
    cite_id = m.group(1)
    return f'{verb}({cite_id}){after}'


def _element_text(node: "etree._Element") -> str:
    return etree.tostring(node, method="text", encoding="unicode").strip()


def _formula_block_text(formula_el: "etree._Element") -> str:
    blocks = cast(
        List[etree._Element],
        formula_el.xpath(
            ".//*[local-name()='block' and ("
            "@name='substitutions' or "
            "@name='repeals' or "
            "@name='insertions' or @name='insertions-originals')]"
        ),
    )
    return " ".join(_element_text(block) for block in blocks if _element_text(block))


def _formula_outside_blocks_text(formula_el: "etree._Element") -> str:
    formula_copy = copy.deepcopy(formula_el)
    for node in cast(
        List[etree._Element],
        formula_copy.xpath(".//*[local-name()='blockContainer' or local-name()='block']"),
    ):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return _element_text(formula_copy)


def _operative_body_repeal_candidate(tree: "etree._Element") -> str:
    body = tree.find(".//{*}body")
    if body is None:
        return ""

    if body.xpath(
        ".//*[local-name()='section' or local-name()='chapter' or "
        "local-name()='part' or local-name()='article' or local-name()='subsection' or "
        "local-name()='paragraph' or local-name()='point' or local-name()='item']"
    ):
        return ""

    body_copy = copy.deepcopy(body)
    for node in cast(
        List[etree._Element],
        body_copy.xpath(
            ".//*[local-name()='hcontainer' and ("
            "@name='conclusions' or @name='signatures' or @name='attachments' or "
            "@name='entryIntoForce' or @name='entryIntoForceStart' or "
            "@name='preliminaryWork')]"
        ),
    ):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)

    raw = _element_text(body_copy)
    # lawvm-regex: prefilter cheap literal presence guard before the body-repeal candidate path; no op minted here
    if not re.search(r"\bkumotaan\b", raw, re.IGNORECASE):
        return ""
    return _strip_cross_law_description(raw)


def get_operative_body_repeal_candidate(xml_bytes: bytes) -> str:
    """Extract a body-prose repeal clause when no structured operative body exists."""
    tree = etree.fromstring(xml_bytes)
    return get_operative_body_repeal_candidate_from_tree(tree)


def get_operative_body_repeal_candidate_from_tree(tree: "etree._Element") -> str:
    """Extract a body-prose repeal clause from an already parsed amendment tree."""
    return _operative_body_repeal_candidate(tree)


def get_johtolause(xml_bytes: bytes) -> str:
    """Extract the enacting clause (johtolause) from amendment XML bytes."""
    tree = etree.fromstring(xml_bytes)
    return get_johtolause_from_tree(tree)


def get_johtolause_from_tree(tree: "etree._Element") -> str:
    """Extract the enacting clause (johtolause) from an already parsed tree."""
    # Strip editorial corrigendum footnotes before extracting clause text. A
    # <span class="corrigendum"> wraps the CORRECTED (operative) wording and
    # carries an <authorialNote> holding only the SUPERSEDED original text
    # ("Merkitty kohta oikaistu (v. N), alkuperäinen sanamuoto kuului: ...").
    # Flattening that note into the johtolause leaks superseded section labels
    # (false drops) and splices noise between live tokens (e.g. "tilalle <note>
    # uusi 6 momentti"), breaking the parse. The corrigendum history itself is
    # captured separately by corrigendum.extract_inline_corrections (Population
    # A); all corpus authorialNotes live inside corrigendum spans, so dropping
    # them all is safe and equivalent. When the caller owns the parsed tree,
    # restore notes afterward so source-model consumers keep the full witness.
    removed_notes: list[tuple[etree._Element, etree._Element, int]] = []
    for _note in cast(List[etree._Element], tree.xpath(".//*[local-name()='authorialNote']")):
        _parent = _note.getparent()
        if _parent is not None:
            removed_notes.append((_note, _parent, _parent.index(_note)))
            _parent.remove(_note)
    try:
        formula = cast(List[etree._Element], tree.xpath("//*[local-name()='formula' and @name='enactingClause']"))
        if formula:
            formula_text = _element_text(formula[0])
            block_text = _formula_block_text(formula[0])
            raw = formula_text
            if block_text:
                outside_blocks = _formula_outside_blocks_text(formula[0])
                if not _OPERATIVE_KEYWORD_PAT.search(outside_blocks):
                    raw = block_text
            return _strip_cross_law_description(raw)
        blocks = cast(List[etree._Element], tree.xpath(
            "//*[local-name()='block' and ("
            "@name='substitutions' or "
            "@name='repeals' or "
            "@name='insertions' or @name='insertions-originals')]"
        ))
        raw = " ".join(_element_text(block) for block in blocks if _element_text(block))
        return _strip_cross_law_description(raw)
    finally:
        for note, parent, index in reversed(removed_notes):
            parent.insert(index, note)


def _normalize_johtolause_verbs(text: str) -> str:
    """Normalise structural johtolause text for downstream parsers.

    This keeps scope intentionally limited to parser-facing text normalization:
    verb-form normalization plus narrow source-pathology repairs that recover
    malformed identifiers without rewriting legal body text.
    """
    out = _normalize_fi_parse_text(text)
    out = _repair_leading_section_marker_after_citation(out)
    for pat, repl in _SPLIT_STRUCTURAL_VERB_REPAIRS:
        out = pat.sub(repl, out)
    for pat, repl in _VERB_NORM_PATTERNS:
        out = pat.sub(repl, out)
    return out


# ---------------------------------------------------------------------------
# Amendment and statute date extraction
# ---------------------------------------------------------------------------

# Section/chapter-scoped expiry ("Lain X § ovat/on voimassa N päivään MONTH
# YYYY"). Used only to DETECT a scoped form for diagnostics; v1 does not lift
# scoped expiry into a statute-level bound.
SECTION_SCOPED_EXPIRY_RE = re.compile(
    r'(?:Lain|Asetuksen|Päätöksen)\s+[\d\w\s,–:§]+?'
    r'\s*§\s+(?:ovat|on)\s+voimassa\s+'
    r'(?P<datetail>\d{1,2}\s+päivään\s+[a-zäöå]+\s+\d{4})',
    flags=re.IGNORECASE,
)

# Chapter-scoped expiry ("Lain N luku on voimassa ..."). Detection only.
CHAPTER_SCOPED_EXPIRY_RE = re.compile(
    r'(?:Lain|Asetuksen|Päätöksen)\s+[\d\w\s,–]+?\bluku\s+(?:ovat|on)\s+voimassa',
    flags=re.IGNORECASE,
)


# Sentence remainder after "Tämä laki/asetus/päätös ... on voimassa". The
# terminal date expression is parsed from this remainder so intervening words
# survive ("voimassa julkaisemispäivästä vuoden 1918 loppuun", "voimassa
# tammikuun 1 päivästä joulukuun 31 päivään 1917", "voimassa toistaiseksi,
# ei kuitenkaan kauvemmin kuin 1 päivään toukokuuta 1918"). This is matched
# against ONE sentence at a time (see parse_whole_law_validity), never across
# a sentence boundary: "Tämä laki tulee voimaan X. Lain 3 §:n 1 momentti on
# voimassa ..." must not bind the whole-law subject to the section-scoped
# validity clause of the NEXT sentence.
WHOLE_LAW_VALIDITY_REMAINDER_RE = re.compile(
    r'Tämä\s+(?:\w+\s+){0,2}(?:laki|asetus|päätös)'
    r'.{0,120}?\bon\s+voimassa\s+(.{0,160})',
    flags=re.IGNORECASE,
)

# NOTE deliberately NOT matched: the two-sentence bare-subject form "Tämä
# asetus tulee voimaan X. Asetus/Laki on voimassa Y." In an amendment's
# voimaantulosäännös the bare act-word subject of a follow-on sentence
# refers to the TARGET statute, not to the amendment itself (1992/272
# regression: "Laki on voimassa vuoden 1993 loppuun" stated the amended
# 1990/1105's validity; treating the PERMANENT amendment as temporary
# reverted its ops after 1993). 1992/884 → 1990/912 is the same class.
# Whole-law subjecthood requires the explicit "Tämä ..." in the same
# sentence as "on voimassa".

# Sentence boundary. A bare [^.] window would truncate dotted numeric dates
# ("1.1.1993"), so cut at period + space + capital/digit instead.
_VALIDITY_REMAINDER_SENTENCE_BOUNDARY_RE = re.compile(
    r'(?<=[.!?])\s+(?=[A-ZÄÖÅ0-9§])'
)

# Partitive month token, typo-tolerant: "joulukuuta" plus the recurring
# Finlex source typos "joulukuutta" (doubled t) and the hyphenation artifact
# "joulukuu-ta". Resolved through fi_partitive_month_number, which folds the typo
# forms before the FI_MONTH_MAP lookup — an unknown month still yields no
# candidate. "loppuun" likewise tolerates the observed "lopuun" typo via
# lop?puun in the *_END patterns.
_MONTH_PARTITIVE_PAT = r'[a-zäöå]+kuu(?:t?ta|-ta)'

# Terminal date forms inside the remainder. (\s* before "päivään" tolerates
# the observed missing-space typo "31päivään".)
_VALIDITY_DAY_FIRST_RE = re.compile(
    r'(\d{1,2})\.?\s*päivään\s+(' + _MONTH_PARTITIVE_PAT + r')\s+(\d{4})',
    flags=re.IGNORECASE,
)
# Essive day form as a validity end ("on voimassa 31 päivänä joulukuuta 2006
# (saakka)"). In the remainder after "on voimassa" the essive date is the
# terminal bound; commencement essives live before "on voimassa". The
# (?!\s+annet) lookahead excludes act-citation dates ("3 päivänä toukokuuta
# 1927 annettu laki"), which are references, not bounds.
_VALIDITY_DAY_ESSIVE_RE = re.compile(
    r'(\d{1,2})\.?\s*päivänä\s+(' + _MONTH_PARTITIVE_PAT + r')\s+(\d{4})'
    r'(?!\s+annet)',
    flags=re.IGNORECASE,
)
# Dotted numeric, possibly a range; the last date is the terminal bound
# ("voimassa 1.1.1994-31.12.1994", "voimassa 31.12.1996 saakka").
_VALIDITY_DOTTED_NUMERIC_RE = re.compile(
    r'\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})', flags=re.IGNORECASE
)
# Day + partitive month + year + saakka ("15 toukokuuta 1992 saakka").
_VALIDITY_SAAKKA_RE = re.compile(
    r'(\d{1,2})\.?\s+(' + _MONTH_PARTITIVE_PAT + r')\s+(\d{4})\s+(?:saakka|asti)',
    flags=re.IGNORECASE,
)
# Bare day + partitive month + year, no case word ("31 maaliskuuta 2022",
# "31. joulukuuta 1998"). Checked LAST so the more specific families win
# position ties (max() keeps the first maximal candidate). The (?!\s+annet)
# lookahead excludes act-citation dates ("15 toukokuuta 1992 annetun lain").
_VALIDITY_BARE_DAY_MONTH_RE = re.compile(
    r'(\d{1,2})\.?\s+(' + _MONTH_PARTITIVE_PAT + r')\s+(\d{4})(?!\s+annet)',
    flags=re.IGNORECASE,
)
# Month-genitive + year + loppuun ("joulukuun 1995 loppuun").
_VALIDITY_MONTH_YEAR_END_RE = re.compile(
    r'([a-zäöå]+kuun)\s+(\d{4})\s+lop?puun', flags=re.IGNORECASE
)
# Old-style month-first genitive: "joulukuun 31 päivään 1917".
_VALIDITY_MONTH_FIRST_RE = re.compile(
    r'([a-zäöå]+kuun)\s+(\d{1,2})\s+päivään\s+(\d{4})', flags=re.IGNORECASE
)
# Genitive month + day + päivän + year + loppuun ("maaliskuun 28 päivän 1992
# loppuun (saakka)") — end of the named DAY.
_VALIDITY_MONTH_DAY_GEN_END_RE = re.compile(
    r'([a-zäöå]+kuun)\s+(\d{1,2})\s+päivän\s+(\d{4})\s+lop?puun',
    flags=re.IGNORECASE,
)
# Day + päivän loppuun + partitive month + year ("31 päivän loppuun
# joulukuuta 2003 asti") — end of the named DAY, month after.
_VALIDITY_DAY_END_MONTH_RE = re.compile(
    r'(\d{1,2})\.?\s*päivän\s+lop?puun\s+(' + _MONTH_PARTITIVE_PAT + r')\s+(\d{4})',
    flags=re.IGNORECASE,
)
_VALIDITY_YEAR_END_RE = re.compile(
    r'vuoden\s+(\d{4})\s+lop?puun', flags=re.IGNORECASE
)
# Month-end forms: "vuoden 1989 maaliskuun loppuun" / "joulukuun loppuun 1988".
_VALIDITY_YEAR_MONTH_END_RE = re.compile(
    r'vuoden\s+(\d{4})\s+([a-zäöå]+kuun)\s+lop?puun', flags=re.IGNORECASE
)
_VALIDITY_MONTH_END_YEAR_RE = re.compile(
    r'([a-zäöå]+kuun)\s+lop?puun\s+(\d{4})', flags=re.IGNORECASE
)
# Anaphoric year-end: "sanotun/mainitun vuoden loppuun" — end of the year
# already named earlier in the sentence ("tulee voimaan ... 1987 ja on
# voimassa sanotun vuoden loppuun").
_VALIDITY_SAID_YEAR_END_RE = re.compile(
    r'(?:sanotun|mainitun)\s+vuoden\s+loppuun', flags=re.IGNORECASE
)

# Anaphora antecedents: a year is plausible ONLY when it terminates a
# commencement/validity date expression in the same sentence ("1 päivänä
# maaliskuuta 1987", "vuonna 1987", "1.3.1987"). Statute/HE/section citations
# ("(123/1986)", "1986/123") are never antecedents and are masked out before
# this scan.
_VALIDITY_ANTECEDENT_YEAR_RE = re.compile(
    r'\d{1,2}\.?\s+päivänä\s+[a-zäöå]+kuuta\s+(\d{4})'
    r'|vuonna\s+(\d{4})'
    r'|\b\d{1,2}\.\s?\d{1,2}\.\s?(\d{4})',
    flags=re.IGNORECASE,
)
_STATUTE_CITATION_RE = re.compile(r'\b\d{1,4}/\d{2,4}\b')

# Toistaiseksi hard-cap phrases ("toistaiseksi, ei kuitenkaan kau(v)emmin
# kuin ..." / "toistaiseksi, enintään ..."). The stated date is an OUTER CAP
# on an otherwise open-ended validity, not a stated expiry day.
_VALIDITY_TOISTAISEKSI_RE = re.compile(r'\btoistaiseksi\b', flags=re.IGNORECASE)
_VALIDITY_CAP_KAUEMMIN_RE = re.compile(
    r'ei\s+kuitenkaan\s+kau[uv]emmin\s+kuin', flags=re.IGNORECASE
)
_VALIDITY_CAP_ENINTAAN_RE = re.compile(r'\benintään\b', flags=re.IGNORECASE)

# Genitive month names ("tammikuun") alongside the partitive FI_MONTH_MAP
# keys ("tammikuuta").
FI_MONTH_GENITIVE_MAP = FI_MONTH_GENITIVE_TO_NUMBER

# Per-grammar-family rule ids carried onto the extracted bound (one id per
# date-expression family below, plus the anaphoric family).
RULE_FI_FIXED_TERM_DAY_FIRST = "fi_fixed_term_day_first_paivaan"
RULE_FI_FIXED_TERM_DAY_ESSIVE = "fi_fixed_term_day_essive"
RULE_FI_FIXED_TERM_BARE_DAY_MONTH = "fi_fixed_term_bare_day_month"
RULE_FI_FIXED_TERM_MONTH_FIRST = "fi_fixed_term_month_first_genitive"
RULE_FI_FIXED_TERM_MONTH_DAY_GEN_END = "fi_fixed_term_month_day_genitive_end"
RULE_FI_FIXED_TERM_DAY_END_MONTH = "fi_fixed_term_day_end_month"
RULE_FI_FIXED_TERM_YEAR_END = "fi_fixed_term_year_end"
RULE_FI_FIXED_TERM_YEAR_MONTH_END = "fi_fixed_term_year_month_end"
RULE_FI_FIXED_TERM_MONTH_END_YEAR = "fi_fixed_term_month_end_year"
RULE_FI_FIXED_TERM_MONTH_YEAR_END = "fi_fixed_term_month_year_end"
RULE_FI_FIXED_TERM_DOTTED_NUMERIC = "fi_fixed_term_dotted_numeric"
RULE_FI_FIXED_TERM_SAAKKA = "fi_fixed_term_saakka"
RULE_FI_FIXED_TERM_ANAPHORIC_YEAR_END = "fi_fixed_term_anaphoric_same_sentence_year_end"


@dataclass(frozen=True)
class WholeLawValidityParse:
    """Structured parse of one whole-law validity clause.

    Either ``valid_until`` is set (the clause states a parseable bound), or
    ``ambiguous_years`` is non-empty (the anaphoric form matched but more than
    one same-sentence antecedent year is plausible — the caller must block,
    never guess). ``bound_kind`` distinguishes a stated expiry day from a
    toistaiseksi outer cap; an upper_cap bound can be terminated earlier.
    """

    valid_until: Optional[dt.date]
    rule_id: str
    bound_kind: 'Literal["stated_expiry", "upper_cap"]'
    source_phrase_kind: Optional[str]
    earlier_termination_possible: bool
    antecedent_text: Optional[str] = None
    antecedent_span: Optional[Tuple[int, int]] = None
    ambiguous_years: Tuple[int, ...] = ()


def _anaphoric_antecedent_years(
    text: str, anaphor_abs_start: int
) -> list[tuple[int, str, Tuple[int, int]]]:
    """Return plausible antecedent (year, text, span) triples for an anaphoric
    "sanotun/mainitun vuoden loppuun" at absolute offset ``anaphor_abs_start``.

    Same-sentence only, and only years that terminate a commencement/validity
    date expression; statute citations are masked out (span offsets preserved)
    so "(123/1986)" can never supply the year.
    """
    sentence_start = 0
    # lawvm-regex: owning_parser V-validity sentence segmenter scoping the anaphoric antecedent; structural split, no op
    for boundary in _VALIDITY_REMAINDER_SENTENCE_BOUNDARY_RE.finditer(
        text, 0, anaphor_abs_start
    ):
        sentence_start = boundary.end()
    sentence = text[sentence_start:anaphor_abs_start]
    masked = _STATUTE_CITATION_RE.sub(lambda c: " " * len(c.group(0)), sentence)
    out: list[tuple[int, str, Tuple[int, int]]] = []
    # lawvm-regex: owning_parser V-validity anaphoric antecedent-year recognizer over masked sentence text
    for m in _VALIDITY_ANTECEDENT_YEAR_RE.finditer(masked):
        year = int(next(g for g in m.groups() if g))
        span = (sentence_start + m.start(), sentence_start + m.end())
        out.append((year, sentence[m.start() : m.end()], span))
    return out


def parse_whole_law_validity(text: str) -> Optional[WholeLawValidityParse]:
    """Parse the whole-law validity bound stated in ``text``, if any.

    ``text`` must already be normalised with ``_normalize_fi_parse_text``.
    Recognises the whole-act forms: day-month-year ("31 päivään joulukuuta
    2020"), old-style month-first genitive ("joulukuun 31 päivään 1917"),
    year-end shorthand ("vuoden YYYY loppuun"), month-end forms, dotted
    numeric dates/ranges, saakka/asti forms, the anaphoric "sanotun vuoden
    loppuun" (same-sentence commencement antecedent only), and the
    toistaiseksi hard-cap form ("toistaiseksi, ei kuitenkaan kau(v)emmin kuin
    1 päivään toukokuuta 1918" → bound_kind="upper_cap"). Bare "toistaiseksi"
    states no bound and yields None. ``valid_until`` is the inclusive bound;
    callers convert to the exclusive cutoff. Section/chapter-scoped forms are
    intentionally excluded.
    """
    # Per-sentence matching: the whole-law subject and its validity clause
    # must live in the SAME sentence (see WHOLE_LAW_VALIDITY_REMAINDER_RE
    # and the bare-subject NOTE above it).
    # lawvm-regex: owning_parser V-validity per-sentence split for the whole-law remainder anchor
    boundaries = list(_VALIDITY_REMAINDER_SENTENCE_BOUNDARY_RE.finditer(text))
    starts = [0] + [b.end() for b in boundaries]
    ends = [b.start() for b in boundaries] + [len(text)]
    m = None
    for start, end in zip(starts, ends, strict=True):
        # lawvm-regex: owning_parser V-validity whole-law validity remainder anchor (canonical owner)
        m = WHOLE_LAW_VALIDITY_REMAINDER_RE.search(text, start, end)
        if m is not None:
            break
    if m is None:
        return None
    remainder = m.group(1)

    # (position-in-remainder, parse) candidates; the latest-positioned date
    # expression is the terminal bound (a start-range like "tammikuun 1
    # päivästä" precedes it in the sentence).
    candidates: list[tuple[int, WholeLawValidityParse]] = []

    def _add(pos: int, rule_id: str, year: int, month: int, day: int) -> None:
        try:
            date = dt.date(year, month, day)
        except ValueError:
            return
        candidates.append(
            (
                pos,
                WholeLawValidityParse(
                    valid_until=date,
                    rule_id=rule_id,
                    bound_kind="stated_expiry",
                    source_phrase_kind=None,
                    earlier_termination_possible=False,
                ),
            )
        )

    # lawvm-regex: owning_parser V-validity canonical FI fixed-term date grammar (per-form RULE_FI_FIXED_TERM_* recognizer battery over already-classified remainder)
    md = _VALIDITY_DAY_FIRST_RE.search(remainder)
    if md:
        month = fi_partitive_month_number(md.group(2), tolerate_finlex_typos=True)
        if month is not None:
            _add(md.start(), RULE_FI_FIXED_TERM_DAY_FIRST,
                 int(md.group(3)), month, int(md.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (day-essive arm)
    mde = _VALIDITY_DAY_ESSIVE_RE.search(remainder)
    if mde:
        month = fi_partitive_month_number(mde.group(2), tolerate_finlex_typos=True)
        if month is not None:
            _add(mde.start(), RULE_FI_FIXED_TERM_DAY_ESSIVE,
                 int(mde.group(3)), month, int(mde.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (month-first genitive arm)
    mm = _VALIDITY_MONTH_FIRST_RE.search(remainder)
    if mm:
        month = FI_MONTH_GENITIVE_MAP.get(mm.group(1).lower())
        if month is not None:
            _add(mm.start(), RULE_FI_FIXED_TERM_MONTH_FIRST,
                 int(mm.group(3)), month, int(mm.group(2)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (month-day genitive-end arm)
    mdg = _VALIDITY_MONTH_DAY_GEN_END_RE.search(remainder)
    if mdg:
        month = FI_MONTH_GENITIVE_MAP.get(mdg.group(1).lower())
        if month is not None:
            _add(mdg.start(), RULE_FI_FIXED_TERM_MONTH_DAY_GEN_END,
                 int(mdg.group(3)), month, int(mdg.group(2)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (day-end month arm)
    mdem = _VALIDITY_DAY_END_MONTH_RE.search(remainder)
    if mdem:
        month = fi_partitive_month_number(mdem.group(2), tolerate_finlex_typos=True)
        if month is not None:
            _add(mdem.start(), RULE_FI_FIXED_TERM_DAY_END_MONTH,
                 int(mdem.group(3)), month, int(mdem.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (year-end shorthand arm)
    my = _VALIDITY_YEAR_END_RE.search(remainder)
    if my:
        _add(my.start(), RULE_FI_FIXED_TERM_YEAR_END, int(my.group(1)), 12, 31)
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (year-month-end arm)
    mym = _VALIDITY_YEAR_MONTH_END_RE.search(remainder)
    if mym:
        month = FI_MONTH_GENITIVE_MAP.get(mym.group(2).lower())
        if month is not None:
            year = int(mym.group(1))
            _add(mym.start(), RULE_FI_FIXED_TERM_YEAR_MONTH_END,
                 year, month, calendar.monthrange(year, month)[1])
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (month-end year arm)
    mmy = _VALIDITY_MONTH_END_YEAR_RE.search(remainder)
    if mmy:
        month = FI_MONTH_GENITIVE_MAP.get(mmy.group(1).lower())
        if month is not None:
            year = int(mmy.group(2))
            _add(mmy.start(), RULE_FI_FIXED_TERM_MONTH_END_YEAR,
                 year, month, calendar.monthrange(year, month)[1])
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (dotted-numeric arm)
    for mdot in _VALIDITY_DOTTED_NUMERIC_RE.finditer(remainder):
        _add(mdot.start(), RULE_FI_FIXED_TERM_DOTTED_NUMERIC,
             int(mdot.group(3)), int(mdot.group(2)), int(mdot.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (saakka/asti arm)
    ms = _VALIDITY_SAAKKA_RE.search(remainder)
    if ms:
        month = fi_partitive_month_number(ms.group(2), tolerate_finlex_typos=True)
        if month is not None:
            _add(ms.start(), RULE_FI_FIXED_TERM_SAAKKA,
                 int(ms.group(3)), month, int(ms.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (month-year-end arm)
    mmye = _VALIDITY_MONTH_YEAR_END_RE.search(remainder)
    if mmye:
        month = FI_MONTH_GENITIVE_MAP.get(mmye.group(1).lower())
        if month is not None:
            year = int(mmye.group(2))
            _add(mmye.start(), RULE_FI_FIXED_TERM_MONTH_YEAR_END,
                 year, month, calendar.monthrange(year, month)[1])
    # Bare day-month-year LAST: the more specific families above win position
    # ties because max() keeps the first maximal candidate.
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (bare day-month-year arm)
    mb = _VALIDITY_BARE_DAY_MONTH_RE.search(remainder)
    if mb:
        month = fi_partitive_month_number(mb.group(2), tolerate_finlex_typos=True)
        if month is not None:
            _add(mb.start(), RULE_FI_FIXED_TERM_BARE_DAY_MONTH,
                 int(mb.group(3)), month, int(mb.group(1)))
    # lawvm-regex: owning_parser V-validity fixed-term date grammar (anaphoric "sanotun vuoden loppuun" arm)
    msaid = _VALIDITY_SAID_YEAR_END_RE.search(remainder)
    if msaid:
        antecedents = _anaphoric_antecedent_years(text, m.start(1) + msaid.start())
        years = sorted({a[0] for a in antecedents})
        if len(years) == 1:
            year, antecedent_text, antecedent_span = antecedents[-1]
            try:
                date = dt.date(year, 12, 31)
            except ValueError:
                date = None
            if date is not None:
                candidates.append(
                    (
                        msaid.start(),
                        WholeLawValidityParse(
                            valid_until=date,
                            rule_id=RULE_FI_FIXED_TERM_ANAPHORIC_YEAR_END,
                            bound_kind="stated_expiry",
                            source_phrase_kind=None,
                            earlier_termination_possible=False,
                            antecedent_text=antecedent_text,
                            antecedent_span=antecedent_span,
                        ),
                    )
                )
        elif len(years) > 1:
            candidates.append(
                (
                    msaid.start(),
                    WholeLawValidityParse(
                        valid_until=None,
                        rule_id=RULE_FI_FIXED_TERM_ANAPHORIC_YEAR_END,
                        bound_kind="stated_expiry",
                        source_phrase_kind=None,
                        earlier_termination_possible=False,
                        ambiguous_years=tuple(years),
                    ),
                )
            )
        # 0 plausible antecedents: the anaphor contributes nothing; the clause
        # falls through to the blocking-unparseable path unless another date
        # expression resolves it.
    if not candidates:
        return None
    parse = max(candidates, key=lambda item: item[0])[1]

    # Toistaiseksi cap classification (V3): "toistaiseksi, ei kuitenkaan
    # kau(v)emmin kuin <date>" / "toistaiseksi, enintään <date>" makes the
    # parsed date an OUTER CAP on an open-ended validity, terminable earlier.
    # lawvm-regex: owning_parser V-validity upper-cap classifier (toistaiseksi) over already-classified remainder
    if parse.valid_until is not None and _VALIDITY_TOISTAISEKSI_RE.search(remainder):
        phrase_kind: Optional[str] = None
        # lawvm-regex: owning_parser V-validity cap-phrase discriminator (kau(v)emmin kuin)
        if _VALIDITY_CAP_KAUEMMIN_RE.search(remainder):
            phrase_kind = "toistaiseksi_ei_kauemmin_kuin"
        # lawvm-regex: owning_parser V-validity cap-phrase discriminator (enintään)
        elif _VALIDITY_CAP_ENINTAAN_RE.search(remainder):
            phrase_kind = "toistaiseksi_enintaan"
        if phrase_kind is not None:
            parse = WholeLawValidityParse(
                valid_until=parse.valid_until,
                rule_id=parse.rule_id,
                bound_kind="upper_cap",
                source_phrase_kind=phrase_kind,
                earlier_termination_possible=True,
                antecedent_text=parse.antecedent_text,
                antecedent_span=parse.antecedent_span,
            )
    return parse


def whole_law_expiry_date_from_text(text: str) -> Optional[dt.date]:
    """Date-only view of ``parse_whole_law_validity`` (kept for callers that
    need just the inclusive ``valid_until``)."""
    parse = parse_whole_law_validity(text)
    if parse is None:
        return None
    return parse.valid_until


def _amendment_effective_date(tree: "etree._Element") -> Optional[dt.date]:
    """Return effective date; delegates to _amendment_effective_date_with_step."""
    date, _step = _amendment_effective_date_with_step(tree)
    return date


def _amendment_expiry_date(
    tree: "etree._Element",
    *,
    raw_text: str | None = None,
) -> Optional[dt.date]:
    """Return explicit expiry date for a temporary amendment when present.

    Matches three forms:
    1. Whole-act day-month-year:   ``Tämä [qualifier] laki/asetus/päätös ... on voimassa N päivään MONTH YEAR``
       (qualifier allows "eduskunnan", "valtioneuvoston" etc. before the document type word)
    2. Section-scoped day-month-year: ``Lain X § ovat/on voimassa N päivään MONTH YEAR``
       (section range may use en-dash or em-dash; thin space before §)
    3. Whole-act year-end shorthand: ``Tämä [qualifier] laki/asetus/päätös ... on voimassa vuoden YEAR loppuun``
       (means December 31 of YEAR)

    Section-scoped year-end shorthand (``Lain X § on voimassa vuoden YEAR loppuun``)
    is intentionally NOT handled here.  When the expiry is section-scoped, only the
    matching sections should get an expiry stamp — not all ops from the amendment.
    Returning a date here would cause ``_enrich_ops_from_amendment_tree`` to stamp
    all ops with that expiry when ``_temporary_section_expiry_override`` also doesn't
    match (it currently handles only the day-month-year format).  Section-scoped
    "vuoden YYYY loppuun" is handled via ``_temporary_section_expiry_override`` once
    that function is extended to cover the year-end shorthand.

    Returns the expiry date if any whole-act form is found, otherwise None.

    Date convention: the returned date is the prose-INCLUSIVE last in-force day
    ("on voimassa 30 päivään kesäkuuta 2023" = in force THROUGH June 30). Stamp
    sites writing kernel ``expires`` fields must convert via
    ``expires_on_from_valid_until``.
    """
    if raw_text is None:
        raw_text = etree.tostring(tree, method="text", encoding="unicode")
    if "voimassa" not in raw_text.casefold():
        return None
    full_text = _normalized_tree_text(tree, raw_text)
    # IMPORTANT: ALL patterns must be restricted to the entryIntoForce element,
    # not the full document text.  Some amendments MODIFY another statute's
    # voimaantulo clause and embed text like "Tämä laki on voimassa 31 päivään
    # joulukuuta 2020." or "Tämä asetus on voimassa vuoden 2012 loppuun." as
    # body content.  Searching full_text would falsely match that replaced content
    # and tag the AMENDING act itself as temporary (e.g. 2016/87 amending 2009/738
    # section 12; 2009/1362 amending another statute's voimaantulo clause).
    #
    # We extract text from <hcontainer name="entryIntoForce"> (the amendment's
    # own commencement element).  If that element is absent we fall back to
    # full_text so old-format statutes without the AKN element still work.
    eit_text = _normalized_entry_into_force_text(tree, full_text)

    # Patterns 1+3 (whole-act day-month-year and year-end shorthand) are
    # delegated to whole_law_expiry_date_from_text so the statute-level
    # fixed-term extractor reuses the identical proven regexes.
    whole_law = whole_law_expiry_date_from_text(eit_text)
    if whole_law is not None:
        return whole_law

    # Pattern 2: section-scoped expiry
    # After _normalize_fi_parse_text: em-dash → en-dash, spacing variants → space.
    # The character class only needs en-dash (U+2013) and ordinary space now.
    # lawvm-regex: owning_parser V-expiry section-scoped expiry clause LOCATOR over own commencement element; the date itself is lexed by the shared fi_dates.match_fi_date recognizer below, this only anchors the clause
    m2 = SECTION_SCOPED_EXPIRY_RE.search(eit_text)
    if m2:
        # DATE: the allative ``NN päivään Kkkuuta YYYY`` tail isolated by the
        # anchor is lexed by the shared recognizer (the anchor owns the
        # section-scope discrimination; the recognizer owns the date token).
        scoped_date = match_fi_date(m2.group("datetail"), forms={FiDateForm.ALLATIVE})
        if scoped_date is not None:
            return scoped_date.value
        return None

    # Pattern 4 (section-scoped "vuoden YYYY loppuun") is intentionally NOT implemented
    # here.  See docstring for the rationale.  When added, it belongs in
    # _temporary_section_expiry_override so the per-section override machinery fires
    # instead of stamping all ops globally.

    # NOT IMPLEMENTED: phased entry-into-force with conditional section expiry
    # ("lain X § lakkaa olemasta voimassa, kun tämä laki tulee muilta osin voimaan").
    # This pattern names SPECIFIC sections that expire, not the whole act.
    # _amendment_expiry_date returns one date for the entire amendment, so it cannot
    # express section-selective expiry.  Returning the main entry date here would
    # incorrectly mark all ops (including permanent inserts) as temporary.
    # Section-selective temporary handling requires per-op expiry in the scan/compile
    # layer, not in this metadata function.

    return None


def _normalize_textual_statute_id(raw: str) -> Optional[str]:
    raw = raw.strip()
    m = re.fullmatch(r'(\d{1,4})/(\d{4})', raw)
    if not m:
        return None
    left, right = m.groups()
    left_int = int(left)
    right_int = int(right)
    if 1800 <= left_int <= 2100 and not (1800 <= right_int <= 2100):
        return f"{left_int}/{right_int}"
    return f"{right_int}/{left_int}"


def _statute_id_from_doc_number(tree: "etree._Element") -> str | None:
    doc_number_el = tree.find('.//{*}docNumber')
    if doc_number_el is None:
        return None
    return _normalize_textual_statute_id(
        etree.tostring(doc_number_el, method="text", encoding="unicode").strip()
    )


def _date_from_fi_day_month_year_match(match: re.Match[str]) -> dt.date | None:
    return parse_fi_day_month_year(
        match.group("day"),
        match.group("month"),
        match.group("year"),
    )


def _section_label_for_source_ref(section: "etree._Element") -> str:
    num_el = section.find("./{*}num")
    if num_el is None:
        return ""
    label = _normalize_fi_parse_text(etree.tostring(num_el, method="text", encoding="unicode"))
    label = label.strip().removesuffix("§").strip()
    return label


def _separate_commencement_witnesses_from_tree(
    *,
    commencement_statute_id: str,
    tree: "etree._Element",
) -> tuple[SeparateCommencementLawWitness, ...]:
    witnesses: list[SeparateCommencementLawWitness] = []
    for section in tree.findall('.//{*}section'):
        section_text = _normalize_fi_parse_text(
            etree.tostring(section, method="text", encoding="unicode")
        )
        if "tulevat voimaan" not in section_text.casefold():
            continue
        source_provision_ref = commencement_statute_id
        section_label = _section_label_for_source_ref(section)
        if section_label:
            source_provision_ref = f"{commencement_statute_id}/{section_label}"

        def _append_witnesses(
            *,
            cited_text: str,
            effective_date: dt.date,
            rule_id: str,
            source_provision_ref: str,
            source_text: str,
        ) -> None:
            # lawvm-regex: witness_only separate-commencement target-id extractor; emits SeparateCommencementLawWitness only, no replay-authoritative op
            for cited in _PAREN_STATUTE_ID_RE.finditer(cited_text):
                target_id = _normalize_textual_statute_id(cited.group("sid"))
                if target_id is None:
                    continue
                witnesses.append(
                    SeparateCommencementLawWitness(
                        target_statute_id=target_id,
                        commencement_statute_id=commencement_statute_id,
                        source_provision_ref=source_provision_ref,
                        effective_date=effective_date,
                        rule_id=rule_id,
                        source_text=source_text,
                    )
                )

        # lawvm-regex: witness_only C-commence separate-commencement-law list recognizer; produces witnesses only
        match = _SEPARATE_COMMENCEMENT_LIST_RE.search(section_text)
        if match:
            effective = _date_from_fi_day_month_year_match(match)
            if effective is not None:
                _append_witnesses(
                    cited_text=section_text[match.end():],
                    effective_date=effective,
                    rule_id="fi_separate_commencement_law_list",
                    source_provision_ref=source_provision_ref,
                    source_text=match.group(0).strip(),
                )

        # lawvm-regex: witness_only bounded commencement witness extractor over one source section
        for inline in _SEPARATE_COMMENCEMENT_INLINE_LIST_RE.finditer(section_text):
            effective = _date_from_fi_day_month_year_match(inline)
            if effective is None:
                continue
            _append_witnesses(
                cited_text=inline.group("subjects"),
                effective_date=effective,
                rule_id="fi_separate_commencement_decree_inline_list",
                source_provision_ref=source_provision_ref,
                source_text=inline.group(0).strip(),
            )
    return tuple(witnesses)


@lru_cache(maxsize=1)
def _separate_commencement_witness_index() -> dict[str, tuple[SeparateCommencementLawWitness, ...]]:
    from lawvm.finland.corpus import get_corpus

    corpus = get_corpus()
    statute_ids = tuple(sorted(corpus.list_statute_ids()))
    index: dict[str, list[SeparateCommencementLawWitness]] = {}
    for source_id in statute_ids:
        xml_bytes = corpus.read_source(source_id)
        if xml_bytes is None or b"tulevat voimaan" not in xml_bytes:
            continue
        tree = etree.fromstring(xml_bytes)
        for witness in _separate_commencement_witnesses_from_tree(
            commencement_statute_id=source_id,
            tree=tree,
        ):
            index.setdefault(witness.target_statute_id, []).append(witness)
    return {
        target_id: tuple(sorted(rows, key=lambda row: (row.effective_date, row.commencement_statute_id)))
        for target_id, rows in index.items()
    }


def separate_commencement_law_witness(
    target_statute_id: str,
) -> SeparateCommencementLawWitness | None:
    """Return the deterministic separate-law commencement witness for *target*.

    Finland has amendment acts whose own entry-into-force clause says only that
    commencement is enacted separately by law. A later voimaanpano act may list
    those amendment acts under a shared fixed date. This helper resolves only
    that explicit list shape; absent or ambiguous witnesses stay unresolved.
    """
    rows = _separate_commencement_witness_index().get(target_statute_id, ())
    if len(rows) != 1:
        return None
    return rows[0]


def _expand_section_range(start: str, end: str) -> Set[str]:
    """Expand an en-dash range like '16 a–16 g' into individual section labels.

    Both ``start`` and ``end`` have already been normalised (whitespace collapsed,
    NBSP replaced with space).  If the range cannot be parsed deterministically we
    return the two endpoints so callers at least cover the boundaries.
    """
    # Normalise: remove all spaces so '16 a' → '16a'
    s = re.sub(r'\s+', '', start).lower()
    e = re.sub(r'\s+', '', end).lower()

    # Fast path: identical endpoints
    if s == e:
        return {s}

    # Try to expand numeric-plus-optional-letter ranges, e.g. 16a–16g or 58i–58k
    m_s = re.fullmatch(r'(\d+)([a-z]?)', s)
    m_e = re.fullmatch(r'(\d+)([a-z]?)', e)
    if m_s and m_e and m_s.group(1) == m_e.group(1):
        # Same numeric base, letter suffix range: 16a … 16g
        base = m_s.group(1)
        l_s = m_s.group(2)
        l_e = m_e.group(2)
        if l_s and l_e and l_s <= l_e:
            return {f"{base}{chr(c)}" for c in range(ord(l_s), ord(l_e) + 1)}
    if m_s and m_e and m_s.group(2) and not m_e.group(2):
        # Alpha-start to later plain-number end: 52a … 55 → 52a, 53, 54, 55
        n_s = int(m_s.group(1))
        n_e = int(m_e.group(1))
        if n_s < n_e:
            return {f"{n_s}{m_s.group(2)}"} | {str(n) for n in range(n_s + 1, n_e + 1)}
    if m_s and m_e and not m_s.group(2) and not m_e.group(2):
        # Pure numeric range: 10–14
        n_s = int(m_s.group(1))
        n_e = int(m_e.group(1))
        if n_s <= n_e:
            return {str(n) for n in range(n_s, n_e + 1)}

    # Fallback: return both endpoints
    return {s, e}


def _parse_section_list_labels(raw: str) -> Set[str]:
    """Parse a Finnish section-list string into a set of normalised labels.

    Handles:
    - simple lists:  ``5, 8 b, 11 ja 12``
    - sekä separator: ``87 a ja 89 a sekä 90``
    - en-dash ranges: ``16 a–16 g`` (U+2013)
    - em-dash ranges: ``43 a—43 c`` (U+2014, normalised to en-dash by caller)
    - NBSP / thin space within section numbers (normalised to space by caller)
    - complex multi-§ clauses: ``16 a–16 g ja 58 i–58 k §, 79 §:n 3 momentti sekä 87 a ja 89 a §``

    Callers are expected to pass text that has already been through
    ``_normalize_fi_parse_text``.  As belt-and-suspenders this function also
    applies that normalization in case it is called directly with raw XML text.
    """
    text = _normalize_fi_parse_text(raw)
    _EN_DASH = "–"
    text = text.replace("-", _EN_DASH).replace("―", _EN_DASH)

    # The contract: a Finnish section-list is a sequence of section items
    # separated by comma, semicolon, or the words "ja" / "sekä" ("and" /
    # "and also"). Each item is a section number, optionally an alpha suffix,
    # optionally an en-dash range, followed by a "§" marker and possibly a
    # pykälä/momentti qualifier ("§:n 3 momentti"). We want the section labels
    # only; the "§" marker and everything after it (the qualifier) is dropped.
    #
    # Split on the separators FIRST, then per item strip the "§" tail. This
    # is the principled inverse of the original code, which tried to strip the
    # "§" tail first with a single re.sub whose stop set was the negated char
    # class [^,;ja sekä–]. That char class was a coincidence, not a
    # contract: it negated the *characters* {j, a, s, e, k, ä, space} rather
    # than the *words* "ja"/"sekä", and it left the post-"§" qualifier text
    # (e.g. "3 momentti") in place to leak in as a bogus label like
    # "793momentti". Splitting on word-boundaried separators and truncating
    # each item at its "§" expresses the actual intent and drops the qualifier.
    # lawvm-regex: separator tokenizer for a FI section list; \b(?:ja|sekä)\b
    # matches the connective WORDS (not letters), [,;] the punctuation
    # separators; ranges (en-dash) stay intra-item and are expanded below.
    items = re.split(
        r"\s*(?:[,;]|\bja\b|\bsekä\b)\s*", text.strip(), flags=re.IGNORECASE
    )
    labels: Set[str] = set()
    for item in items:
        # Drop the "§" marker and any trailing pykälä/momentti qualifier;
        # the section number (and any en-dash range / alpha suffix) precedes "§".
        item = item.split("§", 1)[0].strip()
        if not item:
            continue
        # Check for en-dash range (em-dash already normalised to en-dash above)
        if _EN_DASH in item:
            parts = item.split(_EN_DASH, 1)
            labels.update(_expand_section_range(parts[0].strip(), parts[1].strip()))
        else:
            norm = re.sub(r"\s+", "", item).lower()
            if norm:
                labels.add(norm)
    return labels


def _subsection_clause_section_labels(raw: str) -> Set[str]:
    """Section labels for a subsection-scoped sunset clause's ``group(1)``.

    The subsection anchors (``… §:n N momentti …``) capture in ``group(1)``
    everything up to the last ``§:n``. Two shapes:

    * a SINGLE owning section ("51" from "Lain 51 §:n 5 momentti …") — emit it as
      the section-scoped granularity hint, exactly as before;
    * a MIXED multi-provision list (``group(1)`` carries an embedded ``§``, e.g.
      "3 §:n otsikko sekä 3 ja 4 momentti, 4 §:n …" or "1 §:n 2 momentti, … 11 §,
      … 22 §:n 1 momentti") — emit NOTHING here. The momentti/otsikko scopes are
      owned by the provision path + whole-section promotion guard, and any genuine
      whole-section run in the list ("58 c–58 h ja 59 a–59 e §") is already taken
      by the dedicated whole-section anchor (``_TEMPORARY_SECTION_EXPIRY_RE``);
      re-deriving labels here over-expires sections that keep most of their content
      (the bug EH1's correct section-list labels surfaced — the labels used to be
      harmless junk) and duplicates the whole-section anchor's expiries.
    """
    if "§" in raw:
        return set()
    return _parse_section_list_labels(raw)


_temporary_section_expiry_cache: dict[tuple[int, str, int], tuple[tuple[str, Set[str], dt.date], ...]] = {}


@dataclass(frozen=True, slots=True)
class TemporaryProvisionExpiryOverride:
    """Exact temporary-expiry scope for one provision facet or subsection."""

    target_mid: str
    section: str
    subsection: int | None
    special: str | None
    expiry: dt.date
    rule_id: str


@dataclass(frozen=True, slots=True)
class TemporarySectionApplicabilityWindow:
    """A section-scoped applicability window that bounds a temporary effect."""

    target_mid: str
    sections: frozenset[str]
    start: dt.date
    expiry: dt.date
    rule_id: str


_temporary_provision_expiry_cache: dict[tuple[int, str, int], tuple[TemporaryProvisionExpiryOverride, ...]] = {}
_TEMPORARY_SECTION_EXPIRY_TEXT_ANCHORS = (
    "voimassa",
    "väliaikaisesta muuttamisesta",
    "välisenä aikana",
)
_TEMPORARY_SECTION_CHARS = r"[\d\w\s,\-\u2013\u2015:§]"
_TEMPORARY_SECTION_CHARS_SIMPLE = r"[\d\w\s,\-\u2013\u2015]"
_TEMPORARY_SINGLE_SECTION_CHARS = r"[\dA-Za-zÄÖÅäöå\s]"
_TEMPORARY_CESSATION_SECTION_CHARS = r"[\dA-Za-zÄÖÅäöå\s,\u2013]+"
_TEMPORARY_CITED_COMMENCEMENT_RE = re.compile(
    r"\(\s*(\d{1,4}/\d{4}|\d{4}/\d+)\s*\)\s+voimaantulosäänn",
    re.IGNORECASE,
)
_TEMPORARY_SECTION_EXPIRY_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_TEMPORARY_SECTION_CHARS}+?)\s*§"
    rf"(?:\s+ja\s+sen\s+edellä\s+oleva\s+väliotsikko)?"
    rf"(?:\s*sekä\s+({_TEMPORARY_SECTION_CHARS_SIMPLE}+?)\s*§[^.]*?(?=\s+(?:ovat|on)\s))?"
    rf"\s+(?:ovat|on)\s+voimassa\s+(?:\d{{1,2}}\s+päivästä\s+[a-zäöå]+\s+)?"
    rf"(?P<datetail>\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})",
    re.IGNORECASE,
)
_TEMPORARY_SECTION_APPLICABILITY_WINDOW_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+"
    rf"(?P<subject>{_TEMPORARY_SECTION_CHARS}+?\s*§(?::[a-zäöå]+)?)\s+"
    rf"sovelletaan\s+[^.]*?\bjoka\s+tapahtuu\s+"
    rf"(?P<startday>\d{{1,2}})\s+päivän\s+(?P<startmonth>[a-zäöå]+)\s+(?P<startyear>\d{{4}})\s+ja\s+"
    rf"(?P<endday>\d{{1,2}})\s+päivän\s+(?P<endmonth>[a-zäöå]+)\s+(?P<endyear>\d{{4}})\s+"
    rf"välisenä\s+aikana",
    re.IGNORECASE,
)
# Per-date split of a `_TEMPORARY_SECTION_EXPIRY_RE` match span (group 0). The
# anchor exposes only one ``datetail`` (its LAST date), which is correct when the
# whole coordinated list shares one date ("59 §:n 2 momentti ja 62 § ovat voimassa
# … 2025"). When the span instead carries MORE THAN ONE ``… (ovat|on) voimassa …
# <date>`` segment (sections coordinated by "ja"/"sekä" but each with its OWN date,
# e.g. "64 a §:n 3 momentti on voimassa … 2025 ja lain 127 f § on voimassa …
# 2027"), the single last-date cannot be attributed to every section. These two
# patterns let the override builder count the date segments and, only when >1,
# attribute each date to the sections in ITS segment. A single-date span is left
# to the unchanged whole-list path, so those cases stay byte-identical.
# lawvm-regex: per-date segmenter for a multi-date temporary-expiry span; section
# structure delegated to _parse_section_list_labels, date to the shared match_fi_date
_TEMPORARY_SECTION_EXPIRY_DATE_SEGMENT_RE = re.compile(
    r"(?:ovat|on)\s+voimassa\s+(?:\d{1,2}\s+päivästä\s+[a-zäöå]+\s+)?"
    r"\d{1,2}\s+päivään\s+[a-zäöå]+\s+\d{4}",
    re.IGNORECASE,
)
_TEMPORARY_SECTION_EXPIRY_PER_DATE_RE = re.compile(
    r"(?P<sections>.+?)\s*(?:ovat|on)\s+voimassa\s+"
    r"(?:\d{1,2}\s+päivästä\s+[a-zäöå]+\s+)?"
    r"(?P<datetail>\d{1,2}\s+päivään\s+[a-zäöå]+\s+\d{4})",
    re.IGNORECASE,
)
# Leading connective / cited-act head that a later per-date segment carries over
# from the preceding "ja lain …"/"sekä asetuksen …" join; stripped so the section
# tokenizer sees only the section list (e.g. "ja lain 127 f §" -> "127 f §").
_TEMPORARY_SECTION_EXPIRY_SEGMENT_HEAD_RE = re.compile(
    r"^\s*(?:ja|sekä|,|;)\s+", re.IGNORECASE
)
_TEMPORARY_SECTION_EXPIRY_SEGMENT_CITED_HEAD_RE = re.compile(
    r"^(?:Lain|Asetuksen|Päätöksen|Sen)\s+", re.IGNORECASE
)
_TEMPORARY_ADDED_SECTION_EXPIRY_RE = re.compile(
    rf"(?:Lakiin|Asetukseen|Päätökseen)\s+väliaikaisesti\s+(?:lisätty|lisätyt)\s+"
    rf"({_TEMPORARY_SECTION_CHARS}+?)\s*§\s+"
    rf"(?:ovat|on)\s+voimassa\s+"
    rf"(\d{{1,2}})\s+päivään\s+([a-zäöå]+)\s+(\d{{4}})",
    re.IGNORECASE,
)
_TEMPORARY_SUBSECTION_EXPIRY_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_TEMPORARY_SECTION_CHARS}+?)\s*§:n\s+"
    rf"\d+\s+momentti\s+(?:ovat|on)\s+voimassa\s+"
    rf"(?P<datetail>\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})",
    re.IGNORECASE,
)
_TEMPORARY_CHAINED_SECTION_EXPIRY_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+(?P<section>{_TEMPORARY_SINGLE_SECTION_CHARS}+?)\s*§\s+on\s+voimassa\s+"
    rf"(?P<datetail>\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})"
    rf"(?P<chain>(?:\s+(?:ja|sekä)\s+{_TEMPORARY_SINGLE_SECTION_CHARS}+?\s*§\s+\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})+)",
    re.IGNORECASE,
)
_TEMPORARY_CHAINED_SECTION_EXPIRY_TAIL_RE = re.compile(
    rf"(?:ja|sekä)\s+(?P<section>{_TEMPORARY_SINGLE_SECTION_CHARS}+?)\s*§"
    rf"(?:\s+ja\s+sen\s+edellä\s+oleva\s+väliotsikko)?\s+"
    rf"(?P<datetail>\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})",
    re.IGNORECASE,
)
_TEMPORARY_CHAINED_PROVISION_EXPIRY_TAIL_RE = re.compile(
    r"(?:ja|sekä)\s+"
    r"(?P<subject>[^.]+?§:n\s+[^.]+?)\s+"
    r"(?P<datetail>\d{1,2}\s+päivään\s+[a-zäöå]+\s+\d{4})",
    re.IGNORECASE,
)
_TEMPORARY_SECTION_YEAR_END_EXPIRY_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_TEMPORARY_SECTION_CHARS}+?)\s*§\s+(?:ovat|on)\s+voimassa\s+(?P<datetail>vuoden\s+\d{{4}}\s+loppuun)",
    re.IGNORECASE,
)
_TEMPORARY_SUBSECTION_YEAR_END_EXPIRY_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_TEMPORARY_SECTION_CHARS}+?)\s*§:n\s+"
    rf"\d+\s+momentti\s+(?:ovat|on)\s+voimassa\s+(?P<datetail>vuoden\s+\d{{4}}\s+loppuun)",
    re.IGNORECASE,
)
_TEMPORARY_SECTION_CESSATION_RE = re.compile(
    rf"(?:Lain|Asetuksen|Päätöksen|Tämän lain)\s+({_TEMPORARY_CESSATION_SECTION_CHARS})\s*§\s+lakkaa\s+olemasta\s+voimassa\s*,?\s+kun\s+tämä\s+laki\s+tulee\s+muilta\s+osin\s+voimaan",
    re.IGNORECASE,
)
_TEMPORARY_TITLE_SCOPED_SECTION_RE = re.compile(
    r"((?:\d+\s*[a-z]?\s*(?:[,]\s*)?(?:ja\s+|sekä\s+)?)*\d+\s*[a-z]?)\s*§:n\s+väliaikaisesta\s+muuttamisesta",
    re.IGNORECASE,
)
_TEMPORARY_TITLE_SECTION_LABEL_RE = re.compile(r"\d+\s*(?:[a-z](?![a-z]))?")
_LEADING_CHAPTER_CONTEXT_RE = re.compile(r"^\s*(?:[\d\w]+\s+)*luvun\s+", re.IGNORECASE)
# Sentence ANCHOR (§1.11 cheap prefilter, not a structural parser): locates a
# scoped temporary-expiry sentence and splits it into the SUBJECT span (the
# provision reference, handed to the shared sub-ref grammar) and the DATE-tail
# span (handed to the canonical expiry-date extractor). The anchor itself does
# NOT parse provision structure or build a calendar date — both are delegated:
#   * subject  -> references.sections.parse_body_provision_tail (sections +
#                 momentti coordination/ranges/letter-suffix) + an otsikko
#                 facet detector on each section's ``§:n …`` clause;
#   * datetail -> temporal_lowering._extract_expiry_date_from_text (the canonical
#                 ``NN päivä[äa]n Kkkuuta YYYY`` recognizer reused across the
#                 temporal family).
# The ``päivästä`` start-of-range arm before the date is tolerated and skipped so
# the bound is read from the terminal date, exactly as before.
_TEMPORARY_EXPIRY_SENTENCE_RE = re.compile(
    r"(?:(?:Lain|Asetuksen|Päätöksen|Sen)\s+|"
    r"(?:Lakiin|Asetukseen|Päätökseen)\s+väliaikaisesti\s+(?:lisätty|lisätyt)\s+)"
    r"(?P<subject>[^.]+?)\s+"
    r"(?:ovat|on)\s+voimassa\s+"
    r"(?:\d{1,2}\s+päivästä\s+[a-zäöå]+\s+)?"
    r"(?P<datetail>\d{1,2}\s+päivään\s+[a-zäöå]+\s+\d{4})",
    re.IGNORECASE,
)

# Otsikko (heading) facet detector for one section's ``N §:n …`` clause body.
_OTSIKKO_FACET_RE = re.compile(r"\botsikko\b", flags=re.IGNORECASE)


def _temporary_provision_expiry_overrides(
    tree: "etree._Element",
    source_statute_id: str,
    *,
    raw_text: str | None = None,
) -> tuple[TemporaryProvisionExpiryOverride, ...]:
    """Return exact subsection/facet expiry overrides from scoped sunset text.

    This covers mixed clauses such as:
    ``Asetuksen 3 §:n otsikko sekä 3 ja 4 momentti, 4 §:n 3 ja 4 momentti
    ... ovat voimassa 31 päivään joulukuuta 2025``.
    """
    eit_els = tree.findall('.//{*}hcontainer[@name="entryIntoForce"]')
    raw_text = (
        " ".join(etree.tostring(el, method="text", encoding="unicode") for el in eit_els)
        if eit_els
        else raw_text if raw_text is not None else etree.tostring(tree, method="text", encoding="unicode")
    )
    cache_key = (id(tree), source_statute_id, hash(raw_text))
    cached = _temporary_provision_expiry_cache.get(cache_key)
    if cached is not None:
        return cached
    if "voimassa" not in raw_text.casefold():
        _temporary_provision_expiry_cache[cache_key] = ()
        return ()
    text = _normalize_fi_parse_text(raw_text)
    overrides: list[TemporaryProvisionExpiryOverride] = []
    seen: set[tuple[str, str, int | None, str | None, str]] = set()

    def _append_subject_overrides(subject: str, expiry: dt.date) -> None:
        # DATE: delegate to the canonical temporal-family expiry-date extractor.
        # The anchor constrained the tail to ``NN päivään Kkkuuta YYYY`` so the
        # extractor reads exactly that bound (no earlier essive date can shadow).
        #
        # SUBJECT: split into per-section ``N §:n …`` clauses (the facet scope),
        # then delegate the section + momentti structure of each clause to the
        # shared sub-ref grammar. Only ``§:n``-bodied facets are owned here
        # (otsikko + momentti); whole-section / bare-``§`` scopes are handled by
        # _temporary_section_expiry_overrides, so they are NOT emitted here.
        # lawvm-regex: owning_parser V-expiry per-section ``N §:n …`` clause splitter; section/momentti structure delegated to the shared sub-ref grammar, this only segments the facet scope
        for scoped in re.finditer(
            r'(?P<section>\d+\s*[a-z]?)\s*§:n\s+'
            r'(?P<body>.*?)(?=(?:,\s*|\s+ja\s+|\s+sekä\s+)\d+\s*[a-z]?\s*§:n|$)',
            subject,
            flags=re.IGNORECASE,
        ):
            section = _norm_num_token(scoped.group("section"))
            body = scoped.group("body")
            if not section:
                continue
            # lawvm-regex: owning_parser V-expiry otsikko-facet discriminator over the already-segmented clause body
            if _OTSIKKO_FACET_RE.search(body):
                key = (source_statute_id, section, None, "otsikko", expiry.isoformat())
                if key not in seen:
                    seen.add(key)
                    overrides.append(
                        TemporaryProvisionExpiryOverride(
                            target_mid=source_statute_id,
                            section=section,
                            subsection=None,
                            special="otsikko",
                            expiry=expiry,
                            rule_id="fi_temporary_exact_provision_expiry",
                        )
                    )
            # Momentti subsections: parse the section's clause through the shared
            # body sub-ref grammar and keep this section's subsection targets.
            for target in parse_body_provision_tail(f"{section} §:n {body}"):
                if target.section_label != section or target.subsection_num is None:
                    continue
                subsection = target.subsection_num
                key = (source_statute_id, section, subsection, None, expiry.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                overrides.append(
                    TemporaryProvisionExpiryOverride(
                        target_mid=source_statute_id,
                        section=section,
                        subsection=subsection,
                        special=None,
                        expiry=expiry,
                        rule_id="fi_temporary_exact_provision_expiry",
                    )
                )

    # lawvm-regex: owning_parser V-expiry §1.11 sentence ANCHOR (subject+datetail split); subject → parse_body_provision_tail, datetail → _extract_expiry_date_from_text — both already delegated, this only locates the clause
    for sunset in _TEMPORARY_EXPIRY_SENTENCE_RE.finditer(text):
        iso_expiry = _extract_expiry_date_from_text(sunset.group("datetail"))
        if not iso_expiry:
            continue
        expiry = dt.date.fromisoformat(iso_expiry)
        _append_subject_overrides(sunset.group("subject"), expiry)
        tail_start = sunset.end()
        tail_end = text.find(".", tail_start)
        if tail_end == -1:
            tail_end = len(text)
        sentence_tail = text[tail_start:tail_end]
        # lawvm-regex: owning_parser bounded same-sentence temporary-provision expiry tail
        for chained in _TEMPORARY_CHAINED_PROVISION_EXPIRY_TAIL_RE.finditer(sentence_tail):
            chained_iso_expiry = _extract_expiry_date_from_text(chained.group("datetail"))
            if not chained_iso_expiry:
                continue
            _append_subject_overrides(
                chained.group("subject"),
                dt.date.fromisoformat(chained_iso_expiry),
            )
    result = tuple(overrides)
    _temporary_provision_expiry_cache[cache_key] = result
    return result


def _whole_section_labels_from_body_ref_text(raw: str) -> Set[str]:
    """Return only bare whole-section refs from a Finnish body-reference span."""
    labels: Set[str] = set()
    for target in parse_body_provision_tail(raw):
        if (
            target.subsection_num is None
            and target.item_label is None
            and target.subitem_label is None
        ):
            labels.add(target.section_label)
    return labels


def _source_section_direct_subsection_labels(
    tree: "etree._Element",
    section_label: str,
) -> set[int]:
    """Return direct subsection labels present in a source payload section."""
    wanted = _norm_num_token(section_label)
    if not wanted:
        return set()
    for section in tree.findall(".//{*}body//{*}section"):
        if _norm_num_token(_section_label_for_source_ref(section)) != wanted:
            continue
        labels: set[int] = set()
        for index, subsection in enumerate(section.findall("./{*}subsection"), start=1):
            num_el = subsection.find("./{*}num")
            if num_el is None:
                labels.add(index)
                continue
            num_text = _normalize_fi_parse_text(
                etree.tostring(num_el, method="text", encoding="unicode")
            )
            # lawvm-regex: witness_only lexical subsection-number extraction from a single num element
            num_match = re.search(r"\d+", num_text)
            if num_match is not None:
                labels.add(int(num_match.group(0)))
        return labels
    return set()


def _temporary_section_applicability_windows(
    expiry_scan_text: str,
    target_mid: str,
) -> tuple[TemporarySectionApplicabilityWindow, ...]:
    """Return section-scoped applicability windows from entry-into-force text.

    The regex is only a bounded sentence anchor. Provision structure is
    delegated to ``parse_body_provision_tail`` through
    ``_whole_section_labels_from_body_ref_text``.
    """
    scan_casefold = expiry_scan_text.casefold()
    if "välisenä aikana" not in scan_casefold or "sovelletaan" not in scan_casefold:
        return ()
    windows: list[TemporarySectionApplicabilityWindow] = []
    seen: set[tuple[str, frozenset[str], str, str]] = set()
    # lawvm-regex: owning_parser V-expiry applicability-window sentence ANCHOR; subject -> parse_body_provision_tail, dates -> parse_fi_day_month_year
    for match in _TEMPORARY_SECTION_APPLICABILITY_WINDOW_RE.finditer(expiry_scan_text):
        start = parse_fi_day_month_year(
            match.group("startday"),
            match.group("startmonth"),
            match.group("startyear"),
        )
        expiry = parse_fi_day_month_year(
            match.group("endday"),
            match.group("endmonth"),
            match.group("endyear"),
        )
        if start is None or expiry is None:
            continue
        sections = frozenset(_whole_section_labels_from_body_ref_text(match.group("subject")))
        if not sections:
            continue
        key = (target_mid, sections, start.isoformat(), expiry.isoformat())
        if key in seen:
            continue
        seen.add(key)
        windows.append(
            TemporarySectionApplicabilityWindow(
                target_mid=target_mid,
                sections=sections,
                start=start,
                expiry=expiry,
                rule_id="fi_temporary_section_applicability_window",
            )
        )
    return tuple(windows)


def _temporary_section_expiry_overrides(
    tree: "etree._Element",
    source_statute_id: str,
    *,
    raw_text: str | None = None,
) -> tuple[tuple[str, Set[str], dt.date], ...]:
    """Return all section-scoped expiry override metadata when present.

    Real compile/replay paths should consume this plural form so multiple scoped
    sunset clauses in one amendment are not truncated.

    Date convention: every returned expiry is the prose-INCLUSIVE last in-force
    day ("ovat voimassa 30 päivään kesäkuuta 2023" = in force THROUGH June 30).
    Stamp sites must convert to the kernel's exclusive ``expires`` cutoff via
    ``expires_on_from_valid_until`` (the ``lakkaa olemasta voimassa, kun tämä
    laki tulee muilta osin voimaan`` branch subtracts one day below to honour
    this contract, because that clause names the first day NOT in force).
    """
    if raw_text is None:
        raw_text = etree.tostring(tree, method="text", encoding="unicode")
    cache_key = (id(tree), source_statute_id, hash(raw_text))
    cached = _temporary_section_expiry_cache.get(cache_key)
    if cached is not None:
        return cached
    raw_text_casefold = raw_text.casefold()
    if not any(anchor in raw_text_casefold for anchor in _TEMPORARY_SECTION_EXPIRY_TEXT_ANCHORS):
        _temporary_section_expiry_cache[cache_key] = ()
        return ()
    full_text = _normalized_tree_text(tree, raw_text)
    full_text_casefold = full_text.casefold()
    overrides: list[tuple[str, Set[str], dt.date]] = []
    seen: set[tuple[str, frozenset[str], str]] = set()

    def _append_override(target_mid: str, labels: Set[str], expiry: dt.date) -> None:
        if not labels:
            return
        key = (target_mid, frozenset(labels), expiry.isoformat())
        if key in seen:
            return
        seen.add(key)
        overrides.append((target_mid, labels, expiry))

    target_mid_from_cited = source_statute_id
    if "voimaantulosäänn" in full_text_casefold:
        # lawvm-regex: owning_parser V-expiry cited-commencement target-id discriminator gated on "voimaantulosäänn"; id parse delegated to _normalize_textual_statute_id, no date/lifecycle minted here
        cited = _TEMPORARY_CITED_COMMENCEMENT_RE.search(full_text)
        if cited:
            norm = _normalize_textual_statute_id(cited.group(1))
            if norm:
                target_mid_from_cited = norm
    expiry_scan_text = (
        full_text
        if target_mid_from_cited != source_statute_id
        else _normalized_entry_into_force_text(tree, full_text)
    )
    expiry_scan_casefold = expiry_scan_text.casefold()

    if "päivään" in expiry_scan_casefold:
        # lawvm-regex: owning_parser V-expiry section-scoped sunset clause LOCATOR; date lexed by shared match_fi_date below, labels by _parse_section_list_labels — anchor only
        for m in _TEMPORARY_SECTION_EXPIRY_RE.finditer(expiry_scan_text):
            span = m.group(0)
            # A coordinated list sharing one date keeps the whole-list path (the
            # anchor's single ``datetail`` is correct for every section). A span
            # carrying more than one ``… voimassa … <date>`` segment means each
            # coordinated section has its OWN date, so the last-date attribution
            # would be wrong; split per date and attribute each date to the
            # sections in its own segment.
            # A span carrying more than one "… voimassa … <date>" segment means each
            # coordinated section has its OWN date; split per date (anchor only — the
            # dates are lexed by match_fi_date, labels by _parse_section_list_labels).
            # lawvm-regex: owning_parser V-expiry multi-date detector — counts voimassa-date segments to decide per-date split; anchor/counter only
            if len(_TEMPORARY_SECTION_EXPIRY_DATE_SEGMENT_RE.findall(span)) > 1:
                # lawvm-regex: owning_parser V-expiry per-date segment locator — splits span into (sections, datetail) per date; anchor only
                for seg in _TEMPORARY_SECTION_EXPIRY_PER_DATE_RE.finditer(span):
                    seg_expiry = match_fi_date(
                        seg.group("datetail"), forms={FiDateForm.ALLATIVE}
                    )
                    if seg_expiry is None:
                        continue
                    sections = _TEMPORARY_SECTION_EXPIRY_SEGMENT_HEAD_RE.sub(
                        "", seg.group("sections")
                    )
                    sections = _TEMPORARY_SECTION_EXPIRY_SEGMENT_CITED_HEAD_RE.sub(
                        "", sections
                    )
                    _append_override(
                        target_mid_from_cited,
                        _parse_section_list_labels(sections),
                        seg_expiry.value,
                    )
                continue
            expiry_match = match_fi_date(
                m.group("datetail"), forms={FiDateForm.ALLATIVE}
            )
            if expiry_match is None:
                continue
            expiry = expiry_match.value
            labels = _parse_section_list_labels(m.group(1))
            if m.group(2):
                labels |= _parse_section_list_labels(m.group(2))
            _append_override(target_mid_from_cited, labels, expiry)
            tail_start = m.end()
            tail_end = expiry_scan_text.find(".", tail_start)
            if tail_end == -1:
                tail_end = len(expiry_scan_text)
            sentence_tail = expiry_scan_text[tail_start:tail_end]
            # lawvm-regex: owning_parser V-expiry chained-sunset tail after a
            # normal plural ``ovat voimassa`` head; date via match_fi_date, label
            # via _parse_section_list_labels.
            for m_tail in _TEMPORARY_CHAINED_SECTION_EXPIRY_TAIL_RE.finditer(sentence_tail):
                tail_match = match_fi_date(
                    m_tail.group("datetail"), forms={FiDateForm.ALLATIVE}
                )
                if tail_match is None:
                    continue
                _append_override(
                    target_mid_from_cited,
                    _parse_section_list_labels(m_tail.group("section")),
                    tail_match.value,
                )

        # lawvm-regex: owning_parser bounded temporary added-section expiry recognizer
        for m_added in _TEMPORARY_ADDED_SECTION_EXPIRY_RE.finditer(expiry_scan_text):
            expiry = parse_fi_day_month_year(
                m_added.group(2),
                m_added.group(3),
                m_added.group(4),
            )
            if expiry is None:
                continue
            _append_override(
                target_mid_from_cited,
                _whole_section_labels_from_body_ref_text(f"{m_added.group(1)} §"),
                expiry,
            )

        # Section/subsection-scoped sunset, e.g.
        # "Lain 51 §:n 5 momentti on voimassa 31 päivään joulukuuta 2023."
        # The expiry is still section-scoped for replay stamping: the amendment op
        # target carries the exact subsection/item granularity.
        # lawvm-regex: owning_parser V-expiry subsection-scoped sunset clause LOCATOR; date via shared match_fi_date below — anchor only
        for m in _TEMPORARY_SUBSECTION_EXPIRY_RE.finditer(expiry_scan_text):
            expiry_match = match_fi_date(
                m.group("datetail"), forms={FiDateForm.ALLATIVE}
            )
            if expiry_match is None:
                continue
            expiry = expiry_match.value
            # Single owning section -> {N}; mixed multi-provision clause -> nothing
            # (subsection scopes owned by the provision path; any genuine whole
            # sections are taken by the dedicated whole-section anchor above).
            _append_override(
                target_mid_from_cited,
                _subsection_clause_section_labels(m.group(1)),
                expiry,
            )

        # Chained same-sentence temporary sunset where only the first section repeats
        # "on voimassa", e.g.:
        #   "Lain 90 a § on voimassa 31 päivään heinäkuuta 2020 ja 99 a § 31 päivään
        #    toukokuuta 2021."
        # lawvm-regex: owning_parser V-expiry chained-sunset head clause LOCATOR; date via shared match_fi_date below — chain-split anchor only
        for m_chain in _TEMPORARY_CHAINED_SECTION_EXPIRY_RE.finditer(expiry_scan_text):
            first_match = match_fi_date(
                m_chain.group("datetail"), forms={FiDateForm.ALLATIVE}
            )
            if first_match is not None:
                _append_override(
                    target_mid_from_cited,
                    _parse_section_list_labels(m_chain.group("section")),
                    first_match.value,
                )
            tail = m_chain.group("chain")
            # lawvm-regex: owning_parser V-expiry chained-sunset tail clause LOCATOR; date via shared match_fi_date below — anchor only
            for m_tail in _TEMPORARY_CHAINED_SECTION_EXPIRY_TAIL_RE.finditer(tail):
                tail_match = match_fi_date(
                    m_tail.group("datetail"), forms={FiDateForm.ALLATIVE}
                )
                if tail_match is None:
                    continue
                _append_override(
                    target_mid_from_cited,
                    _parse_section_list_labels(m_tail.group("section")),
                    tail_match.value,
                )

    for window in _temporary_section_applicability_windows(
        expiry_scan_text,
        target_mid_from_cited,
    ):
        _append_override(window.target_mid, set(window.sections), window.expiry)

    if "vuoden" in expiry_scan_casefold and "loppuun" in expiry_scan_casefold:
        # lawvm-regex: owning_parser V-expiry section year-end (vuoden YYYY loppuun) sunset LOCATOR; year-end arm lexed by shared match_fi_date below — anchor only
        for m_yend in _TEMPORARY_SECTION_YEAR_END_EXPIRY_RE.finditer(expiry_scan_text):
            yend_match = match_fi_date(
                m_yend.group("datetail"), forms={FiDateForm.YEAR_END}
            )
            if yend_match is None:
                continue
            expiry = yend_match.value
            raw_secs = _LEADING_CHAPTER_CONTEXT_RE.sub("", m_yend.group(1)).strip()
            labels = _parse_section_list_labels(raw_secs)
            _append_override(target_mid_from_cited, labels, expiry)

        # lawvm-regex: owning_parser V-expiry subsection year-end sunset LOCATOR; year-end arm via shared match_fi_date below — anchor only
        for m_yend_moment in _TEMPORARY_SUBSECTION_YEAR_END_EXPIRY_RE.finditer(expiry_scan_text):
            yend_match = match_fi_date(
                m_yend_moment.group("datetail"), forms={FiDateForm.YEAR_END}
            )
            if yend_match is None:
                continue
            expiry = yend_match.value
            # Subsection-scoped year-end sunset: same single-owning-section /
            # mixed-clause discipline as the päivään subsection path above.
            _append_override(
                target_mid_from_cited,
                _subsection_clause_section_labels(m_yend_moment.group(1)),
                expiry,
            )

    if "lakkaa" in expiry_scan_casefold and "muilta osin" in expiry_scan_casefold:
        # lawvm-regex: owning_parser V-expiry cessation (lakkaa … muilta osin) SCOPE LOCATOR; date comes from the typed _amendment_effective_date(tree), labels from _parse_section_list_labels — no date lexed from raw text here
        for m_lakkaa in _TEMPORARY_SECTION_CESSATION_RE.finditer(expiry_scan_text):
            cessation_date = _amendment_effective_date(tree)
            if cessation_date is None:
                continue
            # "lakkaa olemasta voimassa, kun tämä laki tulee muilta osin voimaan":
            # the section ceases to be in force ON the act's effective date, so that
            # date is the first day NOT in force (an exclusive cutoff). The override
            # contract carries the INCLUSIVE last in-force day, so subtract one day;
            # the stamp-site conversion (+1) then restores the cessation date.
            expiry = cessation_date - dt.timedelta(days=1)
            labels = _parse_section_list_labels(m_lakkaa.group(1))
            _append_override(source_statute_id, labels, expiry)

    if target_mid_from_cited == source_statute_id:
        whole_section_labels = {
            label
            for _target_mid, labels, _expiry in overrides
            for label in labels
        }
        by_section: dict[str, dict[int, dt.date]] = {}
        for override in _temporary_provision_expiry_overrides(
            tree,
            source_statute_id,
            raw_text=raw_text,
        ):
            if override.subsection is None or override.special is not None:
                continue
            by_section.setdefault(override.section, {})[override.subsection] = override.expiry
        for section, subsection_expiries in by_section.items():
            if section in whole_section_labels:
                continue
            if re.fullmatch(r"\d+[a-zäöå]+", section) is None:
                continue
            source_subsections = _source_section_direct_subsection_labels(tree, section)
            if source_subsections and source_subsections <= set(subsection_expiries):
                _append_override(
                    source_statute_id,
                    {section},
                    max(subsection_expiries[sub] for sub in source_subsections),
                )

    title_el = tree.find(".//{*}docTitle")
    title_text = (
        _normalize_fi_parse_text(etree.tostring(title_el, method="text", encoding="unicode"))
        if title_el is not None
        else ""
    )
    if title_text and "väliaikaisesta muuttamisesta" in title_text.casefold():
        expiry = _amendment_expiry_date(tree)
        if expiry is not None:
            title_labels: set[str] = set()
            # Match the full "N [, M]* [ja|sekä] M §:n väliaikaisesta muuttamisesta"
            # pattern to capture all section labels, including leading ones in
            # "6 ja 12 §:n väliaikaisesta muuttamisesta" style titles.
            # lawvm-regex: owning_parser V-expiry title-scoped "väliaikaisesta muuttamisesta" clause LOCATOR over the docTitle
            for match in _TEMPORARY_TITLE_SCOPED_SECTION_RE.finditer(title_text):
                # Extract individual section labels: digit(s) + optional single
                # letter that is not the start of "ja"/"sekä" (handled by (?![a-z])).
                # lawvm-regex: owning_parser section-label tokenizer inside the matched title clause; lexer-shaped
                for sec_str in _TEMPORARY_TITLE_SECTION_LABEL_RE.findall(match.group(1)):
                    title_labels.add(_norm_num_token(sec_str.strip()))
            title_labels.discard("")
            _append_override(source_statute_id, title_labels, expiry)

    result = tuple(overrides)
    _temporary_section_expiry_cache[cache_key] = result
    return result


def _temporary_section_expiry_override(
    tree: "etree._Element",
    source_statute_id: str,
) -> Optional[Tuple[str, Set[str], dt.date]]:
    """Return section-scoped expiry override metadata when present.

    Covers both:
    - direct temporary section clauses in the amendment itself
    - later acts that amend a prior amendment act's voimaantulosäännös

    The section list may contain en-dash ranges (e.g. ``16 a–16 g``), NBSP
    characters inside section numbers, and ``sekä`` as a list separator.  The
    regex captures everything between the statute-type word and the final
    ``§ ovat/on voimassa`` anchor, and also handles section-selective
    ``lakkaa olemasta voimassa`` clauses used by temporary amendments.
    Also covers mixed title shapes where the amendment's own voimaantulo uses a
    whole-act sunset but the title explicitly scopes temporariness to only some
    targets, for example ``25 §:n muuttamisesta ja 51 §:n väliaikaisesta
    muuttamisesta``.
    """
    overrides = _temporary_section_expiry_overrides(tree, source_statute_id)
    return overrides[0] if overrides else None


def _section_commencement_effective_override(
    tree: "etree._Element",
    source_statute_id: str,
) -> Optional[Tuple[str, dict[Optional[str], Set[str]], dt.date]]:
    """Return whole-section commencement overrides from voimaantulo text.

    This is a narrow counterpart to ``_temporary_section_expiry_override`` for
    phased entry-into-force clauses. It only captures whole-section targets and
    intentionally ignores subsection/item-scoped references such as
    ``2 §:n 1 momentti``.
    """
    full_text = _normalized_tree_text(tree)
    eit_text = _normalized_entry_into_force_text(tree, full_text)

    if not _scoped_commencement_guard(eit_text):
        return None
    # lawvm-regex: owning_parser C-commence scoped-commencement clause LOCATOR (guarded by _scoped_commencement_guard); date lexed by parse_fi_day_month_year below — anchor only
    match = _SCOPED_COMMENCEMENT_RE.search(eit_text)
    if match is None:
        return None

    effective = parse_fi_day_month_year(match.group(2), match.group(3), match.group(4))
    if effective is None:
        return None

    refs_text = match.group(1)
    chapter_section_map: dict[Optional[str], Set[str]] = {}
    # A Finnish whole-section enumeration shares one terminal § sign:
    # "51 a ja 51 b §" defers BOTH 51 a and 51 b, not just the label
    # adjacent to the sign. Match the §-terminated label chain first, then
    # split out every label inside it. Subsection-scoped refs ("2 §:n
    # 1 momentti") stay excluded via the §-colon lookahead; range chains
    # ("27 a–27 c §") do not parse as enumerations and keep the previous
    # last-label behavior.
    # lawvm-regex: owning_parser C-commence §-terminated section-chain recognizer over the located refs text; structural label extraction, no op
    for ref in re.finditer(
        r'(?:(?P<chapter>\d+\s*[a-z]?)\s+luvun\s+)?'
        r'(?P<sections>\d+\s*[a-z]?(?:\s*(?:,|ja|sekä)\s+\d+\s*[a-z]?)*)\s*§'
        r'(?!\s*:)',
        refs_text,
        flags=re.IGNORECASE,
    ):
        chapter_raw = ref.group("chapter")
        sections_raw = ref.group("sections")
        if not sections_raw:
            continue
        chapter = re.sub(r'\s+', '', chapter_raw).lower() if chapter_raw else None
        # lawvm-regex: owning_parser section-label tokenizer inside the matched section-chain; lexer-shaped
        for label_match in re.finditer(
            r'\d+(?:\s*[a-z](?![a-zåäö]))?', sections_raw, flags=re.IGNORECASE
        ):
            section = re.sub(r'\s+', '', label_match.group(0)).lower()
            if not section:
                continue
            chapter_section_map.setdefault(chapter, set()).add(section)

    if not chapter_section_map:
        return None
    return source_statute_id, chapter_section_map, effective


def _chapter_commencement_effective_overrides(
    tree: "etree._Element",
    source_statute_id: str,
) -> tuple[tuple[str, frozenset[str], dt.date], ...]:
    """Return chapter-scoped commencement overrides from voimaantulo text.

    Finland sometimes phases entire chapters rather than individual sections,
    for example ``Tämän lain 1, 6 ja 6 a luku tulevat voimaan ...`` followed by
    ``Lain 7 ja 7 a luku tulevat kuitenkin voimaan vasta ...``.  A chapter
    scope is not a section label, so it is kept as its own typed lane and later
    matched against chapter-prefixed operation targets.
    """
    full_text = _normalized_tree_text(tree)
    eit_text = _normalized_entry_into_force_text(tree, full_text)
    if "luku" not in eit_text.casefold() or "voimaan" not in eit_text.casefold():
        return ()

    rows: list[tuple[str, frozenset[str], dt.date]] = []
    # Chapter labels are lexed only inside the located subject span below.
    # lawvm-regex: owning_parser C-commence chapter-scoped commencement clause LOCATOR; chapter labels lexed only inside the located subject span, date via parse_fi_day_month_year
    for match in _CHAPTER_COMMENCEMENT_RE.finditer(eit_text):
        effective = parse_fi_day_month_year(
            match.group("day"),
            match.group("month"),
            match.group("year"),
        )
        if effective is None:
            continue
        chapters = frozenset(_chapter_labels_from_commencement_subject(match.group("chapters")))
        if chapters:
            rows.append((source_statute_id, chapters, effective))
    return tuple(rows)


def _chapter_labels_from_commencement_subject(subject_text: str) -> tuple[str, ...]:
    """Extract chapter labels from a shared-terminal ``luku`` subject."""
    before_luku = subject_text.rsplit("luku", 1)[0]
    labels: list[str] = []
    # lawvm-regex: owning_parser chapter-label lexer inside a located chapter-commencement subject.
    for label_match in re.finditer(
        r"\d+(?:\s*[a-z](?![a-zåäö]))?",
        before_luku,
        flags=re.IGNORECASE,
    ):
        label = re.sub(r"\s+", "", label_match.group(0)).lower()
        if label:
            labels.append(label)
    return tuple(labels)


def _section_subsection_commencement_effective_override(
    tree: "etree._Element",
    source_statute_id: str,
) -> Optional[Tuple[str, tuple[LegalAddress, ...], dt.date]]:
    """Return exact child-address commencement overrides from voimaantulo text.

    Historically this surfaced only subsection-granular ``N §:n M momentti``
    clauses.  Finnish delayed-commencement clauses also name heading facets and
    sparse item targets in the same subject run, for example ``5 §:n otsikko
    sekä 1 ja 2 momentti`` and ``4 §:n 2, 3 ja 5 kohta``.  The extraction below
    uses the shared body provision-reference parser for the provision targets
    and carries the resulting typed address suffixes through to the temporal
    rewriter; it does not infer live-state targets here.
    """

    full_text = _normalized_tree_text(tree)
    eit_text = _normalized_entry_into_force_text(tree, full_text)

    if not _scoped_commencement_guard(eit_text):
        return None
    # lawvm-regex: owning_parser C-commence scoped-commencement clause LOCATOR (subsection-exact consumer); date/provision structure delegated downstream — anchor only
    match = _SCOPED_COMMENCEMENT_RE.search(eit_text)
    if match is None:
        return None

    effective = parse_fi_day_month_year(match.group(2), match.group(3), match.group(4))
    if effective is None:
        return None

    addresses = _commencement_subject_exact_address_suffixes(match.group(1))

    if not addresses:
        return None
    return source_statute_id, addresses, effective


def _section_subsection_application_commencement_effective_override(
    tree: "etree._Element",
    source_statute_id: str,
) -> Optional[Tuple[str, tuple[LegalAddress, ...], dt.date]]:
    """Return exact child-address application-start overrides.

    This is intentionally narrower than ordinary transitional applicability
    prose.  The family exists for contingent temporary provisions whose legal
    text is carried by a parent payload before a decree-set commencement, while
    the provision itself applies only from a fixed date.
    """

    full_text = _normalized_tree_text(tree)
    eit_text = _normalized_entry_into_force_text(tree, full_text)

    if not _scoped_commencement_guard(eit_text):
        return None
    if "valtioneuvoston asetuksella" not in eit_text.lower():
        return None
    # lawvm-regex: owning_parser C-commence scoped-application clause LOCATOR (subsection-exact consumer); date/provision structure delegated downstream — anchor only
    match = _SCOPED_APPLICATION_COMMENCEMENT_RE.search(eit_text)
    if match is None:
        return None

    effective = parse_fi_day_month_year(match.group(2), match.group(3), match.group(4))
    if effective is None:
        return None

    addresses = _commencement_subject_exact_address_suffixes(match.group(1))

    if not addresses:
        return None
    return source_statute_id, addresses, effective


def _commencement_subject_exact_address_suffixes(subject_text: str) -> tuple[LegalAddress, ...]:
    """Parse exact delayed-commencement targets from the scoped subject text."""

    normalized = _WHITESPACE_RE.sub(" ", subject_text).strip()
    addresses: list[LegalAddress] = []
    seen: set[tuple[tuple[tuple[str, str], ...], FacetKind | None]] = set()
    cursor = 0
    while cursor < len(normalized):
        digit_pos = next((idx for idx in range(cursor, len(normalized)) if normalized[idx].isdigit()), -1)
        if digit_pos < 0:
            break
        parsed = parse_body_provision_tail_spanned(normalized[digit_pos:])
        if not parsed.consumed_text:
            cursor = digit_pos + 1
            continue
        consumed = parsed.consumed_text
        local_context = normalized[digit_pos : digit_pos + len(consumed) + 120]
        heading_sections = _commencement_heading_sections(consumed)
        repeal_sections = _commencement_repeal_sections(local_context)
        for section in sorted(heading_sections):
            _append_commencement_address(
                addresses,
                seen,
                LegalAddress(path=(("section", section),), special=FacetKind.HEADING),
            )
        for target in parsed.targets:
            address = _commencement_address_from_body_target(target)
            if address is None:
                continue
            if (
                address.special is None
                and address.path
                and address.path[-1][0] == "section"
                and address.path[-1][1] not in repeal_sections
            ):
                continue
            _append_commencement_address(addresses, seen, address)
        cursor = digit_pos + max(len(consumed), 1)
    return tuple(addresses)


def _commencement_heading_sections(consumed_text: str) -> frozenset[str]:
    labels = {
        _WHITESPACE_RE.sub("", match.group("section")).lower()
        # lawvm-regex: owning_parser delayed-commencement heading facet reference recognizer
        for match in _COMMENCEMENT_HEADING_REF_RE.finditer(consumed_text)
    }
    labels.discard("")
    return frozenset(labels)


def _commencement_repeal_sections(consumed_text: str) -> frozenset[str]:
    labels = {
        _WHITESPACE_RE.sub("", match.group("section")).lower()
        # lawvm-regex: owning_parser C-commence repeal-facet section-ref detector over already-consumed clause text
        for match in _COMMENCEMENT_REPEAL_REF_RE.finditer(consumed_text)
    }
    labels.discard("")
    return frozenset(labels)


def _commencement_address_from_body_target(target: BodyProvisionTarget) -> LegalAddress | None:
    section = _norm_num_token(target.section_label)
    if not section:
        return None
    path: list[tuple[str, str]] = []
    if target.chapter:
        path.append(("chapter", _norm_num_token(target.chapter)))
    path.append(("section", section))
    if target.subsection_num is not None:
        path.append(("subsection", str(target.subsection_num)))
    if target.item_label:
        path.append(("item", str(target.item_label)))
    if target.subitem_label:
        path.append(("subitem", str(target.subitem_label)))
    return LegalAddress(path=tuple(path))


def _append_commencement_address(
    addresses: list[LegalAddress],
    seen: set[tuple[tuple[tuple[str, str], ...], FacetKind | None]],
    address: LegalAddress,
) -> None:
    key = (address.path, address.special)
    if key in seen:
        return
    seen.add(key)
    addresses.append(address)


def _expand_commencement_subsection_labels(text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for part in _COMMENCEMENT_SUBSECTION_LIST_SPLIT_RE.split(text):
        part = part.strip()
        if not part:
            continue
        range_match = _COMMENCEMENT_SUBSECTION_RANGE_RE.fullmatch(part)
        if range_match is not None:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                labels.extend(str(value) for value in range(start, end + 1))
            continue
        if _COMMENCEMENT_SUBSECTION_LABEL_RE.fullmatch(part) is not None:
            labels.append(part)
    return tuple(labels)


def _infer_expiry_date_from_temporary_payload_text(text: str) -> Optional[dt.date]:
    """Infer expiry for temporary payloads whose scope is limited to tax years.

    Some older temporary Finland amendments are marked only by title/formula
    (``väliaikaisesta muuttamisesta`` / ``väliaikaisesti``) and never state an
    explicit expiry in the commencement clause.  A common bounded family is a
    payload whose first sentence limits application to named tax years:

    - ``Vuosilta 1982 ja 1983 toimitettavissa verotuksissa ...``
    - ``Vuodelta 1984 toimitettavassa verotuksessa ...``
    - ``Vuoden 2000 verotuksessa ...``

    For these temporary tax-year windows, the latest named tax year is a safe
    sunset for PIT materialization: the provision is not current after the end
    of that year even though the source omitted a formal ``on voimassa``
    clause.

    Date convention: returns the INCLUSIVE last in-force day (Dec 31 of the
    latest named year); stamp sites convert via ``expires_on_from_valid_until``.
    """
    normalized = " ".join(_normalize_fi_parse_text(text).split())
    if not normalized:
        return None

    years: list[int] = []

    # lawvm-regex: owning_parser V-expiry tax-year-window expiry inference (plural "Vuosilta … verotuksissa")
    for plural in re.finditer(
        r"\bVuosilta\s+(\d{4})(?:\s*(?:ja|sekä|\u2013|-)\s*(\d{4}))?\s+toimitettavissa\s+verotuksissa\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        years.append(int(plural.group(1)))
        if plural.group(2):
            years.append(int(plural.group(2)))

    # lawvm-regex: owning_parser V-expiry tax-year-window expiry inference (singular "Vuodelta … verotuksessa")
    for singular in re.finditer(
        r"\bVuodelta\s+(\d{4})\s+toimitettavassa\s+verotuksessa\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        years.append(int(singular.group(1)))

    # lawvm-regex: owning_parser V-expiry tax-year-window expiry inference (current-year "Vuoden … verotuksessa")
    for current_year in re.finditer(
        r"\bVuoden\s+(\d{4})\s+verotuksessa\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        years.append(int(current_year.group(1)))

    if not years:
        return None
    return dt.date(max(years), 12, 31)


def _expiry_date_precedes_effective_date(
    expiry_date: dt.date,
    effective_iso: str,
) -> bool:
    """Return whether a proposed expiry would make the interval born expired."""
    effective_date = _parse_iso_date(effective_iso)
    return effective_date is not None and expiry_date < effective_date


def _infer_section_expiry_from_temporary_body_text(
    tree: "etree._Element",
    section_label: str,
) -> Optional[dt.date]:
    """Infer expiry from the text of one amendment-body section."""
    normalized_target = re.sub(r"[\s§]", "", _normalize_fi_parse_text(section_label)).lower()
    if not normalized_target:
        return None

    for section in tree.findall(".//{*}body//{*}section"):
        num_el = section.find("{*}num")
        if num_el is None:
            continue
        normalized_num = re.sub(
            r"[\s§]",
            "",
            _normalize_fi_parse_text(etree.tostring(num_el, method="text", encoding="unicode")),
        ).lower()
        if normalized_num != normalized_target:
            continue
        text = etree.tostring(section, method="text", encoding="unicode")
        inferred = _infer_expiry_date_from_temporary_payload_text(text)
        if inferred is not None:
            return inferred
    return None


def _commencement_expiry_override(
    tree: "etree._Element",
    source_statute_id: str,
    *,
    section_expiry_overrides: tuple[tuple[str, Set[str], dt.date], ...] | None = None,
) -> Optional[Tuple[str, Optional[Set[str]], dt.date]]:
    """Return expiry override metadata for amended voimaantulosäännös clauses.

    If the amended commencement clause scopes expiry to specific sections, the
    returned label set contains those sections. Otherwise ``labels`` is ``None``
    and callers should treat the override as applying to all provisions emitted
    from the target source statute.

    Date convention: the returned expiry is the prose-INCLUSIVE last in-force
    day; stamp sites convert via ``expires_on_from_valid_until``.
    """
    scoped = (
        (section_expiry_overrides[0] if section_expiry_overrides else None)
        if section_expiry_overrides is not None
        else _temporary_section_expiry_override(tree, source_statute_id)
    )
    if scoped is not None and scoped[0] != source_statute_id:
        target_mid, labels, expiry = scoped
        return target_mid, labels, expiry

    full_text = _normalized_tree_text(tree)
    # lawvm-regex: owning_parser C-commence cited-voimaantulosäännös target-id redirection; id parse delegated to _normalize_textual_statute_id, no date minted here
    cited = re.search(
        r'\(\s*(\d{1,4}/\d{4}|\d{4}/\d+)\s*\)\s+voimaantulosäänn',
        full_text,
        flags=re.IGNORECASE,
    )
    if not cited:
        return None
    target_mid = _normalize_textual_statute_id(cited.group(1))
    if not target_mid or target_mid == source_statute_id:
        return None
    expiry = _amendment_expiry_date(tree)
    if expiry is None:
        return None
    return target_mid, None, expiry


def _chapter_expiry_from_base(
    tree: "etree._Element",
) -> Optional[Tuple[str, dt.date]]:
    """Return (chapter_label, expiry_date) if the base statute declares a chapter-scoped expiry.

    Matches patterns like:
      "Lain 9 luku on voimassa 31 päivään joulukuuta 2013."
    These appear in the voimaantulo section of the *base* statute (not amendments).

    Date convention: the returned date is the prose-INCLUSIVE last in-force
    day; any stamp site writing a kernel ``expires`` field must convert via
    ``expires_on_from_valid_until``.
    """
    full_text = _normalized_tree_text(tree)
    # lawvm-regex: owning_parser V-expiry chapter-scoped base-statute expiry clause LOCATOR; date lexed by parse_fi_day_month_year below — anchor only
    m = re.search(
        r'(?:Lain|Asetuksen)\s+(\d+)\s+luku\s+(?:on|ovat)\s+voimassa\s+(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})',
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    expiry = parse_fi_day_month_year(m.group(2), m.group(3), m.group(4))
    if expiry is None:
        return None
    return m.group(1), expiry


def _amendment_effective_date_with_step(
    tree: "etree._Element",
) -> "tuple[Optional[dt.date], str]":
    """Return (effective_date, step_used) where step_used is one of:
    'metadata'  — authoritative dateEntryIntoForce element (step 1)
    'text_regex' — extracted by Finnish voimaantulo sentence regex (step 2, #33)
    'contingent_text' — decree-set / contingent commencement detected in text
    'publication_date' — fell back to publication metadata (step 3, #33)
    'absent'    — no date found at all
    """
    # 1. Explicit dateEntryIntoForce metadata (most reliable when present)
    entry = tree.find('.//{*}dateEntryIntoForce')
    if entry is not None:
        parsed = _parse_iso_date(entry.get('date'))
        if parsed is not None:
            return parsed, 'metadata'
    issued = _statute_issue_date(tree)
    # 2. Text regex "Tämä laki tulee voimaan..." gives actual effective date
    #    (differs from issuance date — Finnish laws often enter force later).
    #
    #    Search the amendment's own entry-into-force container first. Whole-body
    #    scans can encounter replacement payload text earlier in document order
    #    (for example a replaced 8 § "Tämä asetus tulee voimaan..." clause)
    #    and then fall through to publication date, silently losing the
    #    amendment's real deferred commencement.
    full_text = _normalized_entry_into_force_text(tree)
    #    Sanity check: if extracted date < issuance date, the match is from the
    #    AMENDED statute's voimaantulo text (context in the amendment XML), not
    #    from the amendment itself.  Fall through to issuance date.
    # lawvm-regex: owning_parser E-effective effective-date sentence LOCATOR; date lexed by parse_fi_day_month_year below — anchor only
    m = re.search(
        r'Tämä\s+(?:laki|asetus|päätös)\s+tulee\s+voimaan\s+(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})',
        full_text,
        flags=re.IGNORECASE
    )
    if m:
        text_date = parse_fi_day_month_year(m.group(1), m.group(2), m.group(3))
        # Sanity: effective date must be >= issuance date
        if text_date is not None and (issued is None or text_date >= issued):
            return text_date, 'text_regex'
    # lawvm-regex: owning_parser E-effective application-date sentence LOCATOR; date lexed by parse_fi_day_month_year below — anchor only
    m = re.search(
        r'Tätä\s+(?:lakia|asetusta|päätöstä)\s+sovelletaan\s+(\d{1,2})\s+päivästä\s+([a-zäöå]+)\s+(\d{4})\s+lukien',
        full_text,
        flags=re.IGNORECASE,
    )
    if m:
        text_date = parse_fi_day_month_year(m.group(1), m.group(2), m.group(3))
        if text_date is not None and (issued is None or text_date >= issued):
            return text_date, 'text_regex'
    # 2b. Decree-set or otherwise contingent commencement: we know the law was
    # not in force at issuance, but we do not know the actual force date yet.
    # lawvm-regex: owning_parser E-effective decree-set contingent-commencement discriminator (no date present)
    if re.search(
        r'Tämä\s+(?:laki|asetus|päätös)\s+tulee\s+voimaan\s+(?:valtioneuvoston\s+)?asetuksella\s+säädettävänä\s+ajankohtana',
        full_text,
        flags=re.IGNORECASE,
    ):
        return None, 'contingent_text'
    # lawvm-regex: owning_parser E-effective separate-commencement contingent discriminator (no date present)
    if re.search(
        r'(?:Tämän|Taman|Lain|Asetuksen|Päätöksen)\s+voimaantulosta\s+säädetään\s+'
        r'(?:(?:valtioneuvoston\s+)?asetuksella|erikseen\s+lailla)',
        full_text,
        flags=re.IGNORECASE,
    ):
        target_id = _statute_id_from_doc_number(tree)
        if target_id is not None:
            witness = separate_commencement_law_witness(target_id)
            if witness is not None:
                return witness.effective_date, 'separate_commencement_law'
        return None, 'contingent_text'
    # 3. Fall back to publication metadata (publication date, not effective date,
    #    but better than nothing when text regex fails or matched wrong text)
    if issued is not None:
        return issued, 'publication_date'
    return None, 'absent'


def _statute_issue_date(tree: "etree._Element") -> Optional[dt.date]:
    """Return the best available publication/issuance date from an AKN XML tree."""
    doc_number_year: Optional[int] = None
    doc_number_el = tree.find('.//{*}docNumber')
    if doc_number_el is not None:
        doc_number_text = etree.tostring(doc_number_el, method="text", encoding="unicode").strip()
        # lawvm-regex: owning_parser ID docNumber year lexer over the docNumber element text
        m = re.search(r'/(\d{4})\b', doc_number_text)
        if m:
            try:
                doc_number_year = int(m.group(1))
            except ValueError:
                doc_number_year = None
    def _signature_date() -> Optional[dt.date]:
        signatures_text = _normalize_fi_parse_text(
            " ".join(
                etree.tostring(el, method="text", encoding="unicode")
                for el in tree.findall('.//{*}hcontainer[@name="signatures"]')
            )
        )
        if not signatures_text:
            return None
        # lawvm-regex: owning_parser E-effective signature-date sentence LOCATOR; date lexed by parse_fi_day_month_year below — anchor only
        m = re.search(
            r'Helsingissä\s+(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})',
            signatures_text,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
        return parse_fi_day_month_year(m.group(1), m.group(2), m.group(3))

    fallback_issued_generated: Optional[dt.date] = None
    for el in tree.findall('.//{*}FRBRdate'):
        parsed = _parse_iso_date(el.get('date'))
        if parsed is None:
            continue
        name = el.get('name')
        if name == 'dateIssued':
            if (
                doc_number_year is not None
                and parsed.year != doc_number_year
            ):
                signature_date = _signature_date()
                if signature_date is not None and signature_date.year == doc_number_year:
                    return signature_date
            return parsed
        if name == 'datePublished':
            return parsed
        if name == 'dateIssuedGenerated' and fallback_issued_generated is None:
            fallback_issued_generated = parsed
    return fallback_issued_generated


# ---------------------------------------------------------------------------
# Statute identifier helpers
# ---------------------------------------------------------------------------

def _statute_id_sort_key(statute_id: str) -> Tuple[int, int, str]:
    """Sort key for statute IDs of the form YYYY/NNN."""
    year, num = statute_id.split('/', 1)
    # lawvm-regex: owning_parser ID statute-id sort-key lexer over an already-split id component
    m = re.match(r'^(\d+)', num)
    num_int = int(m.group(1)) if m else 0
    return (int(year), num_int, num)
