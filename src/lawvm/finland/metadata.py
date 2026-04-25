"""Finnish statute metadata extraction — date and identifier helpers.

Pure lxml-read-only functions that extract dates, version identifiers, and
the ``johtolause`` text from amendment XML trees.  No grafter state, no
XMLStatute dependency.
"""
from __future__ import annotations

import copy
import re
import sys
import datetime as dt
import unicodedata
from typing import List, Optional, Set, Tuple, cast

import lxml.etree as etree

from lawvm.finland.helpers import _norm_num_token, _parse_iso_date


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

# Horizontal-space variants → ordinary space (U+0020).
#
# Computed from the Unicode ``Zs`` (Space Separator) general category rather
# than maintained as a hand-written list.  Using the category makes this
# exhaustive against all Unicode spaces — including EN SPACE (U+2002),
# EM SPACE (U+2003), the N-PER-EM quads (U+2004..U+2006), MEDIUM
# MATHEMATICAL SPACE (U+205F), IDEOGRAPHIC SPACE (U+3000), etc. — that a
# hand-written list would likely miss.  Excludes U+0020 itself (the target).
_ZS_NON_ASCII_SPACES: frozenset[str] = frozenset(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if cp != 0x20 and unicodedata.category(chr(cp)) == 'Zs'
)

# Unicode ``Cf`` (Format) characters that should be deleted from structural
# parse text.  These are invisible control characters that do not carry
# lexical meaning in Finnish legislative XML but will silently corrupt regex
# and PEG matches if left in place.  Confirmed real-world occurrence:
# U+200D ZERO WIDTH JOINER appears in 2020/818 johtolause ("3\u200D\u200D §:n")
# and causes the PEG parser to silently fail the section-number match.
# We delete all Cf characters (zero-width joiners, non-joiners, soft hyphens,
# etc.) except for U+FEFF BOM which should never appear mid-stream but is
# harmless to strip as well.
_CF_FORMAT_CHARS: frozenset[str] = frozenset(
    chr(cp)
    for cp in range(sys.maxunicode + 1)
    if unicodedata.category(chr(cp)) == 'Cf'
)

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
    **{ord(ch): ' ' for ch in _ZS_NON_ASCII_SPACES},
    **{ord(ch): '\u2013' for ch in _DASH_TO_EN_DASH},
    **{ord(ch): '' for ch in _CF_FORMAT_CHARS},
}


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
    return text.translate(_TYPO_TRANSLATION_TABLE)


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
_VERB_NORM_PATTERNS: List[Tuple[re.Pattern, str]] = []
for _part, _pres in _VERB_NORM_TABLE:
    # 5 positions: "on/ovat X", start-of-line, ", X", "sekä X", "ja X"
    _VERB_NORM_PATTERNS.append((re.compile(rf'\b(?:on|ovat)\s+{_part}\b', re.I), _pres))
    _VERB_NORM_PATTERNS.append((re.compile(rf'^\s*{_part}\b', re.I), _pres))
    _VERB_NORM_PATTERNS.append((re.compile(rf',\s*{_part}\b', re.I), f', {_pres}'))
    _VERB_NORM_PATTERNS.append((re.compile(rf'\bsekä\s+{_part}\b', re.I), f'sekä {_pres}'))
    _VERB_NORM_PATTERNS.append((re.compile(rf'\bja\s+{_part}\b', re.I), f'ja {_pres}'))


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
_CROSS_LAW_DESC_PAT = re.compile(
    r'(?:§:[nä]|§:ss[aä]).{0,400}?\(\s*(\d{3,4}/\d{4})\s*\)',
    re.DOTALL,
)
_NOMINATIVE_TARGET_PAT = re.compile(r'\d+\s*(?:ja\s+\d+\s*)?§(?!\s*:)')
_OPERATIVE_KEYWORD_PAT = re.compile(
    r"\b(?:kumotaan|muutetaan|lisätään|poistetaan|siirretään)\b",
    re.IGNORECASE,
)


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
    after = text[m.end():]
    if not _NOMINATIVE_TARGET_PAT.search(after):
        return text
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
    if not re.search(r"\bkumotaan\b", raw, re.IGNORECASE):
        return ""
    return _strip_cross_law_description(raw)


def get_operative_body_repeal_candidate(xml_bytes: bytes) -> str:
    """Extract a body-prose repeal clause when no structured operative body exists."""
    tree = etree.fromstring(xml_bytes)
    return _operative_body_repeal_candidate(tree)


def get_johtolause(xml_bytes: bytes) -> str:
    """Extract the enacting clause (johtolause) from amendment XML bytes."""
    tree = etree.fromstring(xml_bytes)
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


def _normalize_johtolause_verbs(text: str) -> str:
    """Normalise structural johtolause text for downstream parsers.

    This keeps scope intentionally limited to parser-facing text normalization:
    verb-form normalization plus narrow source-pathology repairs that recover
    malformed identifiers without rewriting legal body text.
    """
    out = _normalize_fi_parse_text(text)
    out = _repair_leading_section_marker_after_citation(out)
    for pat, repl in _VERB_NORM_PATTERNS:
        out = pat.sub(repl, out)
    return out


# ---------------------------------------------------------------------------
# Amendment and statute date extraction
# ---------------------------------------------------------------------------

def _amendment_effective_date(tree: "etree._Element") -> Optional[dt.date]:
    """Return effective date; delegates to _amendment_effective_date_with_step."""
    date, _step = _amendment_effective_date_with_step(tree)
    return date


def _amendment_expiry_date(tree: "etree._Element") -> Optional[dt.date]:
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
    """
    full_text = _normalize_fi_parse_text(
        etree.tostring(tree, method="text", encoding="unicode")
    )
    month_map = {
        'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
        'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
        'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
    }

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
    eit_els = tree.findall('.//{*}hcontainer[@name="entryIntoForce"]')
    if eit_els:
        eit_text = _normalize_fi_parse_text(
            " ".join(
                etree.tostring(el, method="text", encoding="unicode")
                for el in eit_els
            )
        )
    else:
        eit_text = full_text

    # Pattern 1: whole-act expiry
    # NOTE: re.DOTALL intentionally omitted.  Without it, '.' does not match
    # newlines, preventing the pattern from crossing sentence boundaries and
    # falsely matching "on voimassa" in a different sentence (e.g. 2009/315
    # where "Tämä asetus tulee voimaan…\nPuutiaisaivotulehdusrokotusta koskeva
    # 2 a § on voimassa 31 päivään joulukuuta 2010." was incorrectly matched).
    m = re.search(
        r'Tämä\s+(?:\w+\s+){0,2}(?:laki|asetus|päätös).{0,120}?\bon\s+voimassa\s+(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})',
        eit_text,
        flags=re.IGNORECASE,
    )
    if m:
        month = month_map.get(m.group(2).lower())
        if month is not None:
            try:
                return dt.date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                pass

    # Pattern 2: section-scoped expiry
    # After _normalize_fi_parse_text: em-dash → en-dash, spacing variants → space.
    # The character class only needs en-dash (U+2013) and ordinary space now.
    m2 = re.search(
        r'(?:Lain|Asetuksen|Päätöksen)\s+[\d\w\s,\u2013:§]+?'
        r'\s*§\s+(?:ovat|on)\s+voimassa\s+(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})',
        eit_text,
        flags=re.IGNORECASE,
    )
    if m2:
        month = month_map.get(m2.group(2).lower())
        if month is not None:
            try:
                return dt.date(int(m2.group(3)), month, int(m2.group(1)))
            except ValueError:
                pass

    # Pattern 3: whole-act year-end shorthand
    # "Tämä laki ... on voimassa vuoden 2019 loppuun" → 2019-12-31
    #
    # NOTE: [^.]* stops at the first sentence-ending period to prevent cross-sentence
    # matches where "Tämä laki tulee voimaan DATE. [other sentences.] Laki on voimassa
    # vuoden YYYY loppuun" incorrectly matches (the second sentence's "Laki" refers to
    # the TARGET statute, not the amending act itself).  Finnish legal text uses ordinal
    # words ("1 päivänä") not "1." so periods in commencement clauses are true sentence
    # terminators and [^.]* is safe to use here.
    m3 = re.search(
        r'Tämä\s+(?:\w+\s+){0,2}(?:laki|asetus|päätös)[^.]*?\bon\s+voimassa\s+vuoden\s+(\d{4})\s+loppuun',
        eit_text,
        flags=re.IGNORECASE,
    )
    if m3:
        try:
            return dt.date(int(m3.group(1)), 12, 31)
        except ValueError:
            pass

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
    # Strip trailing § markers and momentti/pykälä qualifiers that follow §.
    # XXX FIXME: the negated char class `[^,;ja sekä\u2013]` is semantically
    # confused — the author clearly intended to stop at the *words* "ja" and
    # "sekä" (Finnish "and" / "and also"), but a char class matches
    # individual characters, so this actually stops at any of the letters
    # {j, a, s, e, k, ä}.  It happens to produce the right result on the
    # section-list inputs we've seen because those inputs use comma /
    # whitespace separators before any `ja`/`sekä`, but this is a
    # coincidence, not a contract.  Rewrite as an alternation stop
    # (`re.split` or a lookahead on `\b(?:ja|sekä)\b`) and drive it from
    # real regression cases before trusting it.
    text = re.sub(r'§[^,;ja sekä\u2013]*', ' ', text, flags=re.IGNORECASE)
    # Split on comma, 'ja', 'sekä'
    tokens = re.split(r'\s*(?:,|ja|sekä)\s*', text.strip(), flags=re.IGNORECASE)
    labels: Set[str] = set()
    _EN_DASH = '\u2013'
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        # Check for en-dash range (em-dash already normalised to en-dash above)
        if _EN_DASH in token:
            parts = token.split(_EN_DASH, 1)
            labels.update(_expand_section_range(parts[0].strip(), parts[1].strip()))
        else:
            norm = re.sub(r'\s+', '', token).lower()
            if norm:
                labels.add(norm)
    return labels


_temporary_section_expiry_cache: dict[tuple[int, str, int], tuple[tuple[str, Set[str], dt.date], ...]] = {}


def _temporary_section_expiry_overrides(
    tree: "etree._Element",
    source_statute_id: str,
) -> tuple[tuple[str, Set[str], dt.date], ...]:
    """Return all section-scoped expiry override metadata when present.

    Real compile/replay paths should consume this plural form so multiple scoped
    sunset clauses in one amendment are not truncated.
    """
    tree_bytes = etree.tostring(tree, method="xml")
    cache_key = (id(tree), source_statute_id, hash(tree_bytes))
    cached = _temporary_section_expiry_cache.get(cache_key)
    if cached is not None:
        return cached
    full_text = _normalize_fi_parse_text(
        etree.tostring(tree, method="text", encoding="unicode")
    )
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
    cited = re.search(
        r'\(\s*(\d{1,4}/\d{4}|\d{4}/\d+)\s*\)\s+voimaantulosäänn',
        full_text,
        flags=re.IGNORECASE,
    )
    if cited:
        norm = _normalize_textual_statute_id(cited.group(1))
        if norm:
            target_mid_from_cited = norm

    month_map = {
        'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
        'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
        'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
    }
    _sec_chars = r'[\d\w\s,\u2013:§]'
    _simpler_sec_chars = r'[\d\w\s,\u2013]'
    for m in re.finditer(
        rf'(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_sec_chars}+?)\s*§'
        rf'(?:\s*sekä\s+({_simpler_sec_chars}+?)\s*§[^.]*?(?=\s+(?:ovat|on)\s))?'
        rf'\s+(?:ovat|on)\s+voimassa\s+(\d{{1,2}})\s+päivään\s+([a-zäöå]+)\s+(\d{{4}})',
        full_text,
        flags=re.IGNORECASE,
    ):
        month = month_map.get(m.group(4).lower())
        if month is None:
            continue
        try:
            expiry = dt.date(int(m.group(5)), month, int(m.group(3)))
        except ValueError:
            continue
        labels = _parse_section_list_labels(m.group(1))
        if m.group(2):
            labels |= _parse_section_list_labels(m.group(2))
        _append_override(target_mid_from_cited, labels, expiry)

    # Chained same-sentence temporary sunset where only the first section repeats
    # "on voimassa", e.g.:
    #   "Lain 90 a § on voimassa 31 päivään heinäkuuta 2020 ja 99 a § 31 päivään
    #    toukokuuta 2021."
    _single_sec_chars = r'[\dA-Za-zÄÖÅäöå\s]'
    for m_chain in re.finditer(
        rf'(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_single_sec_chars}+?)\s*§\s+on\s+voimassa\s+'
        rf'(\d{{1,2}})\s+päivään\s+([a-zäöå]+)\s+(\d{{4}})'
        rf'((?:\s+(?:ja|sekä)\s+{_single_sec_chars}+?\s*§\s+\d{{1,2}}\s+päivään\s+[a-zäöå]+\s+\d{{4}})+)',
        full_text,
        flags=re.IGNORECASE,
    ):
        first_month = month_map.get(m_chain.group(3).lower())
        if first_month is not None:
            try:
                first_expiry = dt.date(int(m_chain.group(4)), first_month, int(m_chain.group(2)))
            except ValueError:
                first_expiry = None
            if first_expiry is not None:
                _append_override(
                    target_mid_from_cited,
                    _parse_section_list_labels(m_chain.group(1)),
                    first_expiry,
                )
        tail = m_chain.group(5)
        for m_tail in re.finditer(
            rf'(?:ja|sekä)\s+({_single_sec_chars}+?)\s*§\s+(\d{{1,2}})\s+päivään\s+([a-zäöå]+)\s+(\d{{4}})',
            tail,
            flags=re.IGNORECASE,
        ):
            tail_month = month_map.get(m_tail.group(3).lower())
            if tail_month is None:
                continue
            try:
                tail_expiry = dt.date(int(m_tail.group(4)), tail_month, int(m_tail.group(2)))
            except ValueError:
                continue
            _append_override(
                target_mid_from_cited,
                _parse_section_list_labels(m_tail.group(1)),
                tail_expiry,
            )

    for m_yend in re.finditer(
        rf'(?:Lain|Asetuksen|Päätöksen|Sen)\s+({_sec_chars}+?)\s*§\s+(?:ovat|on)\s+voimassa\s+vuoden\s+(\d{{4}})\s+loppuun',
        full_text,
        flags=re.IGNORECASE,
    ):
        try:
            expiry = dt.date(int(m_yend.group(2)), 12, 31)
        except ValueError:
            continue
        raw_secs = re.sub(r'^\s*(?:[\d\w]+\s+)*luvun\s+', '', m_yend.group(1), flags=re.IGNORECASE).strip()
        labels = _parse_section_list_labels(raw_secs)
        _append_override(target_mid_from_cited, labels, expiry)

    _lakkaa_sec_chars = r'[\dA-Za-zÄÖÅäöå\s,\u2013]+'
    for m_lakkaa in re.finditer(
        rf'(?:Lain|Asetuksen|Päätöksen|Tämän lain)\s+({_lakkaa_sec_chars})\s*§\s+lakkaa\s+olemasta\s+voimassa\s*,?\s+kun\s+tämä\s+laki\s+tulee\s+muilta\s+osin\s+voimaan',
        full_text,
        flags=re.IGNORECASE,
    ):
        expiry = _amendment_effective_date(tree)
        if expiry is None:
            continue
        labels = _parse_section_list_labels(m_lakkaa.group(1))
        _append_override(source_statute_id, labels, expiry)

    title_el = tree.find(".//{*}docTitle")
    title_text = (
        _normalize_fi_parse_text(etree.tostring(title_el, method="text", encoding="unicode"))
        if title_el is not None
        else ""
    )
    if title_text:
        expiry = _amendment_expiry_date(tree)
        if expiry is not None:
            title_labels: set[str] = set()
            # Match the full "N [, M]* [ja|sekä] M §:n väliaikaisesta muuttamisesta"
            # pattern to capture all section labels, including leading ones in
            # "6 ja 12 §:n väliaikaisesta muuttamisesta" style titles.
            for match in re.finditer(
                r'((?:\d+\s*[a-z]?\s*(?:[,]\s*)?(?:ja\s+|sekä\s+)?)*\d+\s*[a-z]?)\s*§:n\s+väliaikaisesta\s+muuttamisesta',
                title_text,
                flags=re.IGNORECASE,
            ):
                # Extract individual section labels: digit(s) + optional single
                # letter that is not the start of "ja"/"sekä" (handled by (?![a-z])).
                for sec_str in re.findall(r'\d+\s*(?:[a-z](?![a-z]))?', match.group(1)):
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
    full_text = _normalize_fi_parse_text(
        etree.tostring(tree, method="text", encoding="unicode")
    )
    eit_els = tree.findall('.//{*}hcontainer[@name="entryIntoForce"]')
    if eit_els:
        eit_text = _normalize_fi_parse_text(
            " ".join(
                etree.tostring(el, method="text", encoding="unicode")
                for el in eit_els
            )
        )
    else:
        eit_text = full_text

    month_map = {
        'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
        'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
        'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
    }
    match = re.search(
        r'(?:Tämän\s+lain|Lain|Asetuksen|Päätöksen|Sen)\s+(.+?)\s+'
        r'tule(?:vat|e)\s+kuitenkin\s+voimaan(?:\s+(?:jo|vasta))?\s+'
        r'(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})',
        eit_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    month = month_map.get(match.group(3).lower())
    if month is None:
        return None
    try:
        effective = dt.date(int(match.group(4)), month, int(match.group(2)))
    except ValueError:
        return None

    refs_text = match.group(1)
    chapter_section_map: dict[Optional[str], Set[str]] = {}
    for ref in re.finditer(
        r'(?:(?P<chapter>\d+\s*[a-z]?)\s+luvun\s+)?'
        r'(?P<section>\d+\s*[a-z]?)\s*§'
        r'(?!\s*:)',
        refs_text,
        flags=re.IGNORECASE,
    ):
        chapter_raw = ref.group("chapter")
        section_raw = ref.group("section")
        if not section_raw:
            continue
        chapter = re.sub(r'\s+', '', chapter_raw).lower() if chapter_raw else None
        section = re.sub(r'\s+', '', section_raw).lower()
        if not section:
            continue
        chapter_section_map.setdefault(chapter, set()).add(section)

    if not chapter_section_map:
        return None
    return source_statute_id, chapter_section_map, effective


def _infer_expiry_date_from_temporary_payload_text(text: str) -> Optional[dt.date]:
    """Infer expiry for temporary payloads whose scope is limited to tax years.

    Some older temporary Finland amendments are marked only by title/formula
    (``väliaikaisesta muuttamisesta`` / ``väliaikaisesti``) and never state an
    explicit expiry in the commencement clause.  A common bounded family is a
    payload whose first sentence limits application to named tax years:

    - ``Vuosilta 1982 ja 1983 toimitettavissa verotuksissa ...``
    - ``Vuodelta 1984 toimitettavassa verotuksessa ...``

    For these temporary tax-year windows, the latest named tax year is a safe
    sunset for PIT materialization: the provision is not current after the end
    of that year even though the source omitted a formal ``on voimassa``
    clause.
    """
    normalized = " ".join(_normalize_fi_parse_text(text).split())
    if not normalized:
        return None

    years: list[int] = []

    for plural in re.finditer(
        r"\bVuosilta\s+(\d{4})(?:\s*(?:ja|sekä|\u2013|-)\s*(\d{4}))?\s+toimitettavissa\s+verotuksissa\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        years.append(int(plural.group(1)))
        if plural.group(2):
            years.append(int(plural.group(2)))

    for singular in re.finditer(
        r"\bVuodelta\s+(\d{4})\s+toimitettavassa\s+verotuksessa\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        years.append(int(singular.group(1)))

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
) -> Optional[Tuple[str, Optional[Set[str]], dt.date]]:
    """Return expiry override metadata for amended voimaantulosäännös clauses.

    If the amended commencement clause scopes expiry to specific sections, the
    returned label set contains those sections. Otherwise ``labels`` is ``None``
    and callers should treat the override as applying to all provisions emitted
    from the target source statute.
    """
    scoped = _temporary_section_expiry_override(tree, source_statute_id)
    if scoped is not None and scoped[0] != source_statute_id:
        target_mid, labels, expiry = scoped
        return target_mid, labels, expiry

    full_text = _normalize_fi_parse_text(
        etree.tostring(tree, method="text", encoding="unicode")
    )
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
    """
    full_text = _normalize_fi_parse_text(
        etree.tostring(tree, method="text", encoding="unicode")
    )
    m = re.search(
        r'(?:Lain|Asetuksen)\s+(\d+)\s+luku\s+(?:on|ovat)\s+voimassa\s+(\d{1,2})\s+päivään\s+([a-zäöå]+)\s+(\d{4})',
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    month_map = {
        'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
        'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
        'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
    }
    month = month_map.get(m.group(3).lower())
    if month is None:
        return None
    try:
        expiry = dt.date(int(m.group(4)), month, int(m.group(2)))
    except ValueError:
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
    eit_els = tree.findall('.//{*}hcontainer[@name="entryIntoForce"]')
    if eit_els:
        full_text = _normalize_fi_parse_text(
            " ".join(etree.tostring(el, method="text", encoding="unicode") for el in eit_els)
        )
    else:
        full_text = _normalize_fi_parse_text(
            etree.tostring(tree, method="text", encoding="unicode")
        )
    #    Sanity check: if extracted date < issuance date, the match is from the
    #    AMENDED statute's voimaantulo text (context in the amendment XML), not
    #    from the amendment itself.  Fall through to issuance date.
    m = re.search(
        r'Tämä\s+(?:laki|asetus|päätös)\s+tulee\s+voimaan\s+(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})',
        full_text,
        flags=re.IGNORECASE
    )
    if m:
        month_map = {
            'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
            'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
            'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
        }
        month = month_map.get(m.group(2).lower())
        if month is not None:
            try:
                text_date = dt.date(int(m.group(3)), month, int(m.group(1)))
                # Sanity: effective date must be >= issuance date
                if issued is None or text_date >= issued:
                    return text_date, 'text_regex'
            except ValueError:
                pass
    m = re.search(
        r'Tätä\s+(?:lakia|asetusta|päätöstä)\s+sovelletaan\s+(\d{1,2})\s+päivästä\s+([a-zäöå]+)\s+(\d{4})\s+lukien',
        full_text,
        flags=re.IGNORECASE,
    )
    if m:
        month_map = {
            'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
            'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
            'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
        }
        month = month_map.get(m.group(2).lower())
        if month is not None:
            try:
                text_date = dt.date(int(m.group(3)), month, int(m.group(1)))
                if issued is None or text_date >= issued:
                    return text_date, 'text_regex'
            except ValueError:
                pass
    # 2b. Decree-set or otherwise contingent commencement: we know the law was
    # not in force at issuance, but we do not know the actual force date yet.
    if re.search(
        r'Tämä\s+(?:laki|asetus|päätös)\s+tulee\s+voimaan\s+(?:valtioneuvoston\s+)?asetuksella\s+säädettävänä\s+ajankohtana',
        full_text,
        flags=re.IGNORECASE,
    ):
        return None, 'contingent_text'
    if re.search(
        r'(?:Tämän|Taman|Lain|Asetuksen|Päätöksen)\s+voimaantulosta\s+säädetään\s+asetuksella',
        full_text,
        flags=re.IGNORECASE,
    ):
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
        m = re.search(r'/(\d{4})\b', doc_number_text)
        if m:
            try:
                doc_number_year = int(m.group(1))
            except ValueError:
                doc_number_year = None
    signature_date: Optional[dt.date] = None
    signatures_text = _normalize_fi_parse_text(
        " ".join(
            etree.tostring(el, method="text", encoding="unicode")
            for el in tree.findall('.//{*}hcontainer[@name="signatures"]')
        )
    )
    if signatures_text:
        m = re.search(
            r'Helsingissä\s+(\d{1,2})\s+päivänä\s+([a-zäöå]+)\s+(\d{4})',
            signatures_text,
            flags=re.IGNORECASE,
        )
        if m:
            month_map = {
                'tammikuuta': 1, 'helmikuuta': 2, 'maaliskuuta': 3, 'huhtikuuta': 4,
                'toukokuuta': 5, 'kesäkuuta': 6, 'heinäkuuta': 7, 'elokuuta': 8,
                'syyskuuta': 9, 'lokakuuta': 10, 'marraskuuta': 11, 'joulukuuta': 12,
            }
            month = month_map.get(m.group(2).lower())
            if month is not None:
                try:
                    signature_date = dt.date(int(m.group(3)), month, int(m.group(1)))
                except ValueError:
                    signature_date = None
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
                and signature_date is not None
                and signature_date.year == doc_number_year
            ):
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
    m = re.match(r'^(\d+)', num)
    num_int = int(m.group(1)) if m else 0
    return (int(year), num_int, num)
