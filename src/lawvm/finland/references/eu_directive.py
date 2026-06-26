"""Recognizer for EU directive/regulation references by nickname + article.

Closes the ``eu.directive_article`` family (§2/§3 of the FI Reference
Catalogue), which was 0% captured. It recognises two co-occurring constructs in
Finnish statute prose:

  (a) an EU-instrument **nickname head** (``teollisuuspäästödirektiivin``,
      ``yleisen tietosuoja-asetuksen``) resolved against the deterministic
      ``eu_nickname -> CELEX`` registry; and
  (b) an **article coordination / range** (``33 ja 35 artiklassa``,
      ``12 artiklan``, ``33—35 artiklassa``) parsed with the *shared*
      number-list / range helpers used by the section-reference grammar.

One typed :class:`~lawvm.core.reference_mention.ReferenceMention` is emitted per
expanded article, with ``cite_kind = EU`` and a resolution status:

  * ``EXACT``       — nickname resolved to a single CELEX.
  * ``AMBIGUOUS``   — nickname maps to >1 CELEX (registry refuses to pick).
  * ``STATUTE_ONLY``— a NAMED EU instrument (a compound/multi-word EU-head
    nickname directly governing an ``N artikla``) is not in the registry and has
    no minable adjacent cite (the instrument identity is textual, the CELEX is
    pending — tag, don't guess). Routed to ``eu-nickname:<surface>`` so it is NOT
    mis-typed as a Finnish ``fi-name:`` statute. A BARE standalone head
    (anaphoric/domestic ``asetuksessa`` / ``mainitun direktiivin``) carries no
    instrument identity and is dropped instead.

Article coordination reuse
--------------------------
The article number list is parsed by tokenising the number fragment with the
johtolause lexer and running the shared recognizers from
``lawvm.finland.johtolause.grammar.sections`` (imported READ-ONLY):

  * :func:`_number_list` — comma/conj/dash list parsing, identical to the one
    the section-reference family uses; it already folds in
  * :func:`_expand_range_single` / the internal ``_expand_range`` — so a written
    range (``33—35``) expands to one entry per article, exactly as section
    ranges do.

This module owns NO number-list logic of its own; it only locates the
``<numbers> artikla<case>`` window and a preceding nickname head, then delegates
the numeric expansion to the shared helpers.

Grammar boundary (window location = bounded typed residue)
----------------------------------------------------------
The numeric ENUMERATION core — the part structurally analogous to the Finnish
``momentti``/``kohta`` coordination — is already grammar-routed: every article,
kohta and alakohta number list (and a ``33—35`` range) is expanded by the shared
``_number_list``/``_expand_range`` grammar, yielding exactly one mention per
expanded element. What is NOT grammar-modelled is the *window location* itself:
``_ARTIKLA_RE`` / ``_KOHTA_TAIL_RE`` / ``_NICKNAME_RE`` / ``_BARE_HEAD_RE`` find
the ``<numbers> artikla<case>`` span and its governing nickname head in free
prose. The johtolause construction grammar models Finnish statute-INTERNAL
structure (``§`` / ``momentti`` / ``kohta``); it carries no ``artikla``
construction, so locating an EU-instrument-internal article window via the
grammar would require a new construction family — disproportionate for this
recognizer's (low) yield. These window-locating patterns are therefore retained
as DELIBERATE bounded typed residue: every quantifier is explicitly bounded
(``\\d{1,4}``, ``{0,30}``/``{0,10}`` list caps, literal-anchored ``artikla`` /
``kohta`` heads), so each is provably linear and passes the §1.11 regex perf
gate cleanly (no allowlist entry needed). The grammar does not yet model
EU-internal article structure; that is the documented residue, not a leak.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.sections import (
    _Scan,
    _number_list,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    DIALECT_EU_DIRECTIVE,
    eu_celex_type_for_head,
    recognize_celex,
    recognize_eu_acts,
    recognize_eu_year_first_slash,
)
from lawvm.finland.references.lemma_gate import (
    head_case_forms,
    head_plural_external_local_forms,
    head_surface_forms,
)
from lawvm.finland.references.registries import eu_nickname

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING only: ``eu_nickname_binding`` imports
    # ``_celex_from_formal_cite`` from THIS module, so a runtime top-level import
    # would be circular. The recognizer only calls the table's ``lookup`` method
    # (duck-typed), so no runtime import is needed here.
    from lawvm.finland.references.eu_nickname_binding import StatuteLocalNicknames

# ---------------------------------------------------------------------------
# Surface patterns (§1.11: bounded quantifiers, compiled at module scope).
# ---------------------------------------------------------------------------

# A nickname head is a Finnish word ending in an inflected ``direktiivi`` or
# ``asetus`` head (optionally a multi-word phrase with a leading agreeing
# modifier, e.g. ``yleisen tietosuoja-asetuksen``). We capture a small window of
# up-to-two preceding words plus the head word; the registry's morphology-backed
# index does the actual lemma resolution, so this only needs to be permissive
# enough to hand the right surface span to ``eu_nickname.lookup``.
_WORD = r"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö0-9-]*"
# A head word is a single token ENDING in an inflected ``direktiivi`` or
# ``asetus`` form, e.g. ``teollisuuspäästödirektiivin``, ``tietosuoja-asetuksen``.
# The head form is detected by MORPHOLOGY (paradigm inversion) rather than a
# hand-written ``direktiiv|asetu`` suffix-substring guess: ``head_surface_forms``
# returns the full M1-generated paradigm of the EU-instrument heads
# (``direktiivi``, ``asetus``), longest-first, and the token's tail must be one of
# those generated forms.  This is sound (every alternative is a real M1 output of
# a closed head) and kills the consonant-gradation substring bug class
# (``'asetu'`` substring vs the generated gradated ``asetuksen``).  The leading
# compound modifier (``teollisuuspäästö``, ``tietosuoja-``) rides invariant in
# front, exactly as a statute modifier rides before ``laki``; the
# morphology-backed ``eu_nickname.lookup`` then resolves the lemma.
#
# The plural external-local cases (``direktiiveillä``, ``asetuksilla`` …) are
# added via the explicit, sound M1-boundary supplement: M1's reference_v1
# profile cannot emit them (``plural_case_form`` raises), but they are real
# EU-instrument head forms ("näillä direktiiveillä säädetään") the substring
# matcher used to catch, so dropping them would regress coverage.
_EU_HEAD_LEMMAS: tuple[str, ...] = ("direktiivi", "asetus")
_EU_HEAD_FORMS: tuple[str, ...] = head_surface_forms(_EU_HEAD_LEMMAS) + (
    head_plural_external_local_forms(_EU_HEAD_LEMMAS)
)
_EU_HEAD_ALT = "|".join(
    re.escape(f) for f in sorted(set(_EU_HEAD_FORMS), key=lambda s: (-len(s), s))
)
# Optional compound-modifier prefix (any word-stem chars) + a generated head
# form, with a trailing word boundary so the head form is the token tail.
_HEAD_WORD = rf"[A-Za-zÅÄÖåäö0-9-]*(?:{_EU_HEAD_ALT})\b"

# A *named-instrument* head carries a NON-EMPTY compound modifier glued to the EU
# head form (``ESAP-asetuksen``, ``vakavaraisuusasetuksen``,
# ``teollisuuspäästödirektiivin``) — that compound is the instrument's name, so
# even a registry-miss nickname is unambiguously a NAMED EU instrument, not a bare
# anaphoric/domestic head (``asetuksessa``, ``mainitun direktiivin``). The bare
# standalone head form, whose whole token IS an inflected ``asetus``/``direktiivi``
# with no glued modifier, carries no instrument identity. ``re.fullmatch`` against
# the longest-first head alternation anchors the head form to the WHOLE token, so a
# leftover prefix (or none) tells the two apart. (A space-separated multi-word
# nickname surface — ``rahoitusvälineiden markkinat -asetuksen`` — is named by its
# own ``in`` check below.)
_BARE_HEAD_RE = re.compile(rf"(?:{_EU_HEAD_ALT})", re.IGNORECASE)

# nickname window: optional one or two leading modifier words + the head word.
# Case-sensitive (as the original ``_HEAD_WORD`` was): the generated head forms
# are lowercase, so a lowercase head form is the tail of the token, matching the
# original substring matcher's case sensitivity exactly.
_NICKNAME_RE = re.compile(
    rf"(?:(?P<m2>{_WORD})\s+)?(?:(?P<m1>{_WORD})\s+)?"
    rf"(?P<head>{_HEAD_WORD})",
)

# The article window: a number list (digits with optional letter suffix, joined
# only by explicit list connectors — comma / "ja" / "tai" / dash) immediately
# followed by an inflected ``artikla``.
#
# The number list is built from list ITEMS joined by CONNECTORS, instead of a
# loose ``[\d\s,...]`` class. This stops ``nums`` from reaching back across a
# whitespace-separated standalone number that is NOT part of the list:
# ``2004 8 artiklassa`` no longer captures ``2004`` (the bare ``2004`` is a
# preceding year, not an article), and ``2012 13 ja 14 artiklan`` captures
# ``13 ja 14`` (the real articles) rather than collapsing to ``2012``. Plain
# whitespace between two digit runs is NOT a connector, so the list start anchors
# to the contiguous run.
#
# ReDoS safety (§1.11): every quantifier is bounded and no two adjacent
# unbounded/overlapping repeats exist. An item is ``\d{1,4}`` + optional single
# letter suffix; the list is the item followed by at most a bounded number of
# ``connector item`` pairs; connectors are explicit (no bare ``\s`` bridging two
# digit runs). The whole thing precedes ``artikla`` directly (optional single
# space), so a no-``artikla`` tail fails fast without catastrophic backtracking.
_ARTIKLA_ITEM = r"\d{1,4}(?:\s?[a-z])?"
_ARTIKLA_CONNECTOR = r"(?:\s*[,–—-]\s*|\s+(?:ja|tai|sekä)\s+)"
_ARTIKLA_RE = re.compile(
    rf"(?P<nums>{_ARTIKLA_ITEM}(?:{_ARTIKLA_CONNECTOR}{_ARTIKLA_ITEM}){{0,30}})"
    r"\s*artikla(?P<artcase>ssa|sta|an|n|a|ksi|lla|lta|lle|t)?\b",
    re.IGNORECASE,
)

# Intra-article element tail: the ``kohta`` (numbered paragraph/point) and the
# optional ``alakohta`` (lettered sub-point) that an EU article reference can
# carry. The Finnish article reference maps onto ``ProvisionRef`` as
# ``section_label`` = article, with the intra-article element below it:
#
#   "6 artiklan 1 kohdan c alakohdassa"  → section 6, kohta 1, alakohta c
#       → ProvisionRef(section_label="6", subsection_num=1, item_label="c")
#       → serialized "celex:.../6/1/kc"
#   "7 artiklan 1 ja 2 kohdassa"         → kohta coordination → one ref per kohta
#       → "celex:.../7/1" and "celex:.../7/2"
#
# The tail sits in the genitive on the article ("N artiklan") followed by the
# kohta number(s) in the genitive/inessive ("M kohdan"/"M kohdassa") and an
# optional lettered/numbered sub-point ("L alakohdassa"). The kohta number list
# reuses the SAME bounded list/connector grammar as the article number list so a
# written coordination ("1 ja 2 kohdassa") enumerates exactly as the article
# coordination does.
#
# The intra-article tail is only attempted when the article itself is in the
# GENITIVE (``N artiklan``) — that is the case Finnish uses to govern a following
# kohta ("N artiklan M kohdassa"). A bare locative ``N artiklassa`` (no genitive)
# carries no intra-article element and is left untouched, so a stray number after
# it can never be mis-read as a kohta.
_ARTIKLA_GENITIVE_CASE = "n"
#
# ReDoS safety (§1.11): each item is a bounded digit run; the list is the item
# followed by at most a bounded number of ``connector item`` pairs; the
# ``koh(ta|dassa|…)`` head is a literal anchor, so a no-kohta tail fails fast.
# Anchored with ``^`` against the post-article remainder (the caller slices from
# the article match end, which already consumed ``N artiklan``).
_KOHTA_ITEM = r"\d{1,3}"
# An alakohta label is a short letter run (``a``, ``aa``) or a small number; the
# list is one label + a bounded number of ``connector label`` pairs so a written
# sub-point coordination (``a ja b alakohdassa``) enumerates exactly as the kohta
# coordination does. Letters are matched FIRST so a single letter never reads as
# a degenerate number.
#
# UNLIKE the article/kohta number lists (which route through the shared
# ``_number_list`` grammar that EXPANDS a written range correctly), the alakohta
# labels are split by a plain string-splitter here, so the alakohta connector is
# restricted to EXPLICIT coordination words / commas and EXCLUDES the dash. A
# dash between letter labels ("a–c alakohdassa") is a RANGE this lane cannot
# soundly expand (letter-range expansion is not implemented), so it must NOT be
# enumerated as if it were "a" + "c" — that would silently drop the middle "b".
# A dash-range alakohta therefore fails the alakohta arm and the reference keeps
# only the (sound) kohta level rather than fabricating a wrong sub-point set.
_ALAKOHTA_CONNECTOR = r"(?:\s*,\s*|\s+(?:ja|tai|sekä)\s+)"
_ALAKOHTA_ITEM = r"(?:[a-zåäö]{1,2}|\d{1,3})"
_ALAKOHTA_LIST = (
    rf"{_ALAKOHTA_ITEM}(?:{_ALAKOHTA_CONNECTOR}{_ALAKOHTA_ITEM}){{0,10}}"
)
# The ``kohta`` (point/paragraph) head, M1-backed. The hand-written
# ``koh(?:ta|dassa|dasta|taan|dan|taa|daksi|dalla|dalta|dalle|dat|tien)?`` arm was
# a truncated stem (``koh``) spanning the ``ht``/``hd`` consonant gradation with a
# hand-typed case suffix list — the gradation-substring smell ``lemma_gate``
# retires elsewhere (and its trailing ``?`` even let a bare ``koh`` match). It is
# replaced by the M1-generated full surfaces of ``kohta`` over the exact curated
# case set the suffix list encoded, so each alternative is a real M1 output of a
# closed head, not a stem guess. The curated set is a strict equal of the old
# forms (no precision change): a kohta reference appears in precisely these cases
# in EU-article body prose; widening to the full paradigm (plural inessive
# ``kohdissa`` etc.) is unnecessary and unverified here.
_KOHTA_CASE_NUMBERS: tuple[tuple[str, str], ...] = (
    ("NOM", "SG"),   # kohta
    ("INE", "SG"),   # kohdassa
    ("ELA", "SG"),   # kohdasta
    ("ILL", "SG"),   # kohtaan
    ("GEN", "SG"),   # kohdan
    ("PART", "SG"),  # kohtaa
    ("TRA", "SG"),   # kohdaksi
    ("ADE", "SG"),   # kohdalla
    ("ABL", "SG"),   # kohdalta
    ("ALL", "SG"),   # kohdalle
    ("NOM", "PL"),   # kohdat
    ("GEN", "PL"),   # kohtien
)
_KOHTA_HEAD_ALT = "|".join(head_case_forms("kohta", _KOHTA_CASE_NUMBERS))
# ``alakohta`` (lettered sub-point) is not an M1 head, but it is the invariant
# prefix ``ala`` + ``kohta``, so its paradigm is ``kohta``'s with ``ala``
# prepended — derive it soundly from the same M1 surfaces rather than re-typing a
# second truncated ``alakoh`` stem.
_ALAKOHTA_HEAD_ALT = "|".join(
    "ala" + form for form in head_case_forms("kohta", _KOHTA_CASE_NUMBERS)
)

# A kohta tail anchored at the start of the post-article remainder: a
# (possibly coordinated) kohta number list + the ``kohta`` head, then an optional
# (possibly coordinated) ``alakohta`` lettered/numbered sub-point list. The
# article-number portion is NOT re-captured; the caller already has it from
# ``_ARTIKLA_RE``.
_KOHTA_TAIL_RE = re.compile(
    r"^\s+"
    rf"(?P<kohdat>{_KOHTA_ITEM}(?:{_ARTIKLA_CONNECTOR}{_KOHTA_ITEM}){{0,10}})"
    rf"\s*(?:{_KOHTA_HEAD_ALT})\b"
    rf"(?:\s+(?P<alakohta>{_ALAKOHTA_LIST})\s*"
    rf"(?:{_ALAKOHTA_HEAD_ALT})\b)?",
    re.IGNORECASE,
)
# Splits an alakohta label list ("a ja b", "1, 2 ja 3") on the explicit
# coordination connectors only (NO dash — see ``_ALAKOHTA_CONNECTOR`` above), so
# a coordinated sub-point list enumerates while a dash-range is never matched.
_ALAKOHTA_SPLIT_RE = re.compile(_ALAKOHTA_CONNECTOR, re.IGNORECASE)

# Reasonable lookbehind window (chars) from an article phrase to its governing
# nickname head. Finnish keeps the two adjacent: "<nickname> N ja M artiklassa".
_NICKNAME_LOOKBEHIND = 80

# The year-first slash cite "YEAR/NUMBER/FORM" (e.g. "2009/138/EY", "2001/23/EY",
# and the legacy 2-digit-year directives/decisions "96/53/EY", "82/891/ETY") is
# recognised via the shared ``recognize_eu_year_first_slash(DIALECT_EU_DIRECTIVE)``
# waist, which carries this lane's legacy 2-digit-year tolerance. The shared
# ``recognize_eu_acts`` NUMBER/YEAR/FORM recognizer requires a 4-digit MIDDLE
# group, so it only reads the number-first order; this fills the year-first gap.
# A 2-digit year is normalised to its full 19xx form by ``_normalize_eu_year``.


def _normalize_eu_year(year: int) -> int:
    """Normalise an EU-act year, expanding a legacy 2-digit year to 19xx.

    EU directive/decision cites written before 2000 abbreviate the year to its
    last two digits ("96/53/EY" → 1996, "82/891/ETY" → 1982). The 2-digit form
    is unambiguously pre-2000 in this corpus (the year-first 4-digit form took
    over from 2000 on), so a value below 100 pivots to the 1900s. A value that is
    already 4-digit (≥ 1000) is returned unchanged.
    """
    if year < 100:
        return 1900 + year
    return year


def _celex_type_for_head(head: str) -> Optional[str]:
    """CELEX type letter (L/R/D) implied by an instrument-head surface, or None.

    Delegates to the shared, M1-backed
    :func:`~lawvm.finland.references.eu_reference.eu_celex_type_for_head`: the
    head token's TAIL must be an M1-generated EU-instrument-head surface
    (``direktiivin`` → L, ``asetuksen`` → R, ``päätöksen`` → D). Paradigm
    inversion over a closed head set, not a ``asetu`` substring guess — so the
    gradated forms map correctly and ``None`` means "not an EU-instrument head".
    """
    return eu_celex_type_for_head(head, default=None)


def _celex_from_formal_cite(window: str, head: str) -> Optional[str]:
    """Resolve an adjacent formal EU cite in ``window`` to a CELEX, or None.

    An EU-by-nickname head with NO registry hit is only resolvable when the same
    window also carries a formal EU cite. Two shapes resolve here:

      * a literal CELEX ("32018R1805") → used verbatim (its own type letter wins);
      * a form-less / formed act cite ("(EU) 2018/1805", "2009/138/EY",
        "(EY) N:o 999/2001") → the (year, number) are taken from the cite and the
        TYPE letter from the governing head word ("direktiivin" → L, "asetuksen"
        → R, "päätöksen" → D), yielding ``3{year}{TYPE}{number:04d}``.

    Returns ``None`` when no formal cite is adjacent (then the bare head is NOT
    emitted — fail-loud, no polluting STATUTE_ONLY double-count).
    """
    # A literal CELEX is self-typing — prefer it (closest one to the article).
    celex_hits = recognize_celex(window, dialect=DIALECT_CROSS_REF)
    if celex_hits:
        return max(celex_hits, key=lambda h: h.start).celex

    # Otherwise an act cite supplies (year, number); the head supplies the type.
    # Collect (start, year, number) from BOTH the shared act recognizer
    # (NUMBER/YEAR/FORM, "(FORM) N:o NUMBER/YEAR", "(FORM) YEAR/NUMBER") and the
    # year-first slash form ("YEAR/NUMBER/FORM") the shared recognizer misses.
    type_letter = _celex_type_for_head(head)
    if type_letter is None:
        return None
    candidates: list[tuple[int, int, int]] = []  # (start, year, number)
    for h in recognize_eu_acts(window, dialect=DIALECT_CROSS_REF):
        try:
            candidates.append((h.start, int(h.year), int(h.number)))
        except ValueError:
            continue
    for ref in recognize_eu_year_first_slash(window, dialect=DIALECT_EU_DIRECTIVE):
        candidates.append((ref.start, int(ref.year), int(ref.number)))
    if not candidates:
        return None
    # The cite closest to the article (largest start offset) governs it. A legacy
    # 2-digit year ("96" from "96/53/EY") is expanded to its full 19xx form before
    # the CELEX is built and range-checked.
    _, year, num = max(candidates, key=lambda c: c[0])
    year = _normalize_eu_year(year)
    if not (1957 <= year <= 2050):
        return None
    return f"3{year:04d}{type_letter}{num:04d}"


# ---------------------------------------------------------------------------
# Output record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EuDirectiveRef:
    """A single recognised EU directive/regulation article reference.

    Wraps the typed :class:`ReferenceMention` together with the resolution
    bookkeeping a caller (integration step) needs: the matched nickname surface,
    the resolved CELEX (or all candidates when ambiguous), and the article path.
    """

    mention: ReferenceMention
    nickname_surface: str
    celex_candidates: tuple[str, ...]
    article: str

    @property
    def status(self) -> CiteConfidence:
        return self.mention.cite_confidence


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


def _expand_number_list(nums_fragment: str) -> list[tuple[str, str]]:
    """Expand a number fragment to ``(number, letter_suffix)`` pairs.

    Delegates entirely to the shared section-grammar number-list recognizer:
    tokenise the fragment, run ``_number_list`` (which already folds in range
    expansion via ``_expand_range``). Used for BOTH the article number list and
    the intra-article kohta number list, so a written coordination expands
    identically in either position.
    """
    fragment = nums_fragment.strip().rstrip(",").strip()
    if not fragment:
        return []
    tokens = tokenize(fragment)
    scan = _Scan(Cursor(tokens))
    parsed = _number_list(scan)
    if not parsed:
        return []
    return list(parsed)


def _expand_articles(nums_fragment: str) -> list[str]:
    """Expand an article number fragment to one article token per article."""
    out: list[str] = []
    for num, suffix in _expand_number_list(nums_fragment):
        out.append(f"{num}{suffix}" if suffix else num)
    return out


@dataclass(frozen=True, slots=True)
class _KohtaTail:
    """A parsed intra-article element tail (``M [ja K] kohdassa [L alakohdassa]``).

    ``kohdat`` is one or more kohta numbers (a written coordination is enumerated
    by the shared number-list grammar, so ``1 ja 2 kohdassa`` yields ``(1, 2)``).
    ``alakohdat`` is the lettered/numbered sub-point label(s) (a coordination
    ``a ja b alakohdassa`` yields ``("a", "b")``), or an empty tuple when no
    alakohta follows. ``end`` is the offset (into the post-article remainder) one
    past the tail, so the caller can extend ``surface_text`` to cover the whole
    sub-element span.
    """

    kohdat: tuple[int, ...]
    alakohdat: tuple[str, ...]
    end: int


def _parse_kohta_tail(remainder: str) -> Optional[_KohtaTail]:
    """Parse an intra-article ``kohta``/``alakohta`` tail at the start of ``remainder``.

    ``remainder`` is the text immediately AFTER a genitive ``N artiklan`` match.
    Returns ``None`` when no kohta tail is present (the article carries no
    intra-article element — leave it at article level). Fail-loud: an ``alakohta``
    is only carried when the word ``alakohta`` actually follows the label(s) in
    the text, so a sub-point is never fabricated.

    Both the kohta number(s) and (when present) the alakohta label(s) are split on
    the SAME list connectors the article numbers use, so a written coordination
    (``1 ja 2 kohdassa`` / ``a ja b alakohdassa``) enumerates to one entry each.
    """
    m = _KOHTA_TAIL_RE.match(remainder)
    if m is None:
        return None
    kohdat_fragment = m.group("kohdat")
    kohta_nums: list[int] = []
    for num, _suffix in _expand_number_list(kohdat_fragment):
        try:
            kohta_nums.append(int(num))
        except ValueError:
            continue
    if not kohta_nums:
        return None
    alakohta_fragment = m.group("alakohta")
    alakohdat: tuple[str, ...] = ()
    if alakohta_fragment:
        alakohdat = tuple(
            label.strip().lower()
            for label in _ALAKOHTA_SPLIT_RE.split(alakohta_fragment)
            if label.strip()
        )
    return _KohtaTail(
        kohdat=tuple(kohta_nums),
        alakohdat=alakohdat,
        end=m.end(),
    )


def _find_nickname(
    text: str,
    before_idx: int,
    local_aliases: Optional["StatuteLocalNicknames"] = None,
) -> Optional[tuple[str, eu_nickname.RegistryResult]]:
    """Find the nickname head governing an article phrase ending before ``before_idx``.

    Scans the lookbehind window for nickname-shaped heads, preferring the one
    closest to the article phrase. Returns ``(surface, RegistryResult)`` — the
    registry result may be ``status=none`` (unknown nickname → STATUTE_ONLY),
    which is still a recognised directive reference, just unresolved.

    ``local_aliases`` is the statute-local ``jäljempänä``-bound nickname table
    (built once per statute). It is consulted AFTER the static ``eu_nickname``
    seed: an ad-hoc nickname the statute coined for an EU instrument resolves to
    its bound CELEX (single → EXACT), so later ``<nickname> N artikla`` uses are
    recovered. The static seed always wins on a collision (a coined alias never
    shadows an established term-of-art).
    """
    window_start = max(0, before_idx - _NICKNAME_LOOKBEHIND)
    window = text[window_start:before_idx]
    best: Optional[tuple[int, str, eu_nickname.RegistryResult]] = None
    for m in _NICKNAME_RE.finditer(window):
        # Try progressively wider surfaces (head only, m1+head, m2+m1+head) so a
        # multi-word nickname (yleisen tietosuoja-asetuksen) resolves while a
        # bare head (teollisuuspäästödirektiivin) also resolves.
        head = m.group("head")
        candidates_surfaces: list[tuple[int, str]] = []
        parts: list[str] = []
        if m.group("m2"):
            parts.append(m.group("m2"))
        if m.group("m1"):
            parts.append(m.group("m1"))
        parts.append(head)
        # widest first, then narrower, then head-only
        for k in range(len(parts)):
            surface = " ".join(parts[k:])
            candidates_surfaces.append((m.start(), surface))
        resolved: Optional[tuple[int, str, eu_nickname.RegistryResult]] = None
        for start, surface in candidates_surfaces:
            res = eu_nickname.lookup(surface)
            if res.registry_status is not eu_nickname.RegistryStatus.NONE:
                resolved = (start, surface, res)
                break
        if resolved is None and local_aliases is not None:
            # Static seed missed at every width — consult the statute-local
            # ad-hoc nickname table (widest surface first, as above).
            for start, surface in candidates_surfaces:
                celex = local_aliases.lookup(surface)
                if celex is not None:
                    resolved = (
                        start,
                        surface,
                        eu_nickname.RegistryResult(
                            candidates=(celex,),
                            registry_status=eu_nickname.RegistryStatus.SINGLE,
                            lemma=surface.lower(),
                            matched_surface=surface,
                        ),
                    )
                    break
        if resolved is None:
            # No registry hit at any width — but the head is nickname-shaped, so
            # record it as an unknown (STATUTE_ONLY) candidate using the head.
            resolved = (
                m.start("head"),
                head,
                eu_nickname.RegistryResult(
                    candidates=(),
                    registry_status=eu_nickname.RegistryStatus.NONE,
                ),
            )
        # Prefer the match whose head sits closest to the article phrase (largest
        # start offset within the window).
        if best is None or resolved[0] >= best[0]:
            best = resolved
    if best is None:
        return None
    _, surface, res = best
    return surface, res


def _is_named_eu_instrument(surface: str) -> bool:
    """True iff ``surface`` is a NAMED EU instrument (strong by-nickname signal).

    A named instrument carries the instrument's own name as a compound modifier
    glued to the EU head (``ESAP-asetuksen``, ``vakavaraisuusasetuksen``,
    ``teollisuuspäästödirektiivin``), or is a multi-word nickname phrase
    (``rahoitusvälineiden markkinat -asetuksen``). A BARE standalone head whose
    whole single token IS an inflected ``asetus``/``direktiivi`` with no glued
    modifier (``asetuksessa``, ``mainitun direktiivin`` → head ``direktiivin``)
    carries no instrument identity and is left to the anaphoric/domestic drop.

    This is the discriminator that, together with the directly-governed
    ``N artikla`` shape (Finnish acts use § not artikla), types a registry-miss
    nickname as an unresolved EU-instrument reference rather than a Finnish
    statute name (``fi-name:``). Sound: the head form is anchored to the WHOLE
    last token by ``re.fullmatch``; only a real compound (non-empty prefix) or a
    multi-word surface passes.
    """
    stripped = surface.strip()
    if not stripped:
        return False
    if " " in stripped:
        # Multi-word nickname surface (modifier words + head) — named.
        return True
    return _BARE_HEAD_RE.fullmatch(stripped) is None


def _status_for(res: eu_nickname.RegistryResult) -> tuple[CiteConfidence, tuple[str, ...]]:
    """Map a registry result to a (confidence, celex_candidates) pair."""
    if res.registry_status is eu_nickname.RegistryStatus.SINGLE:
        return CiteConfidence.EXACT, res.candidates
    if res.registry_status is eu_nickname.RegistryStatus.MULTIPLE:
        return CiteConfidence.AMBIGUOUS, res.candidates
    return CiteConfidence.STATUTE_ONLY, ()


def recognize_eu_directive_refs(
    text: str,
    *,
    source_statute_id: str = "",
    source_provision_path: str = "",
    local_aliases: Optional["StatuteLocalNicknames"] = None,
) -> list[EuDirectiveRef]:
    """Recognise EU directive/regulation article references in ``text``.

    Returns one :class:`EuDirectiveRef` per expanded article. An article window
    with no governing nickname head in its lookbehind is skipped (it is a plain
    same-instrument/section ``artikla`` reference owned by other lanes, not an
    EU-by-nickname reference).

    Args:
        text: The provision body / clause text to scan.
        source_statute_id: Statute the citation lives in (for the source ref).
        source_provision_path: Provision path of the citing text.
        local_aliases: Optional statute-local ``jäljempänä``-bound nickname →
            CELEX table (built once per statute by
            :func:`lawvm.finland.references.eu_nickname_binding.build_statute_local_nicknames`).
            Consulted AFTER the static seed: an ad-hoc nickname the statute coined
            for an EU instrument resolves to its bound CELEX (EXACT), recovering
            later ``<nickname> N artikla`` uses that would otherwise be dropped.

    Resolution status per emitted mention:
        EXACT (single CELEX — a registry SINGLE hit, or a bare head resolved via
        an adjacent formal EU cite) / AMBIGUOUS (>1 CELEX, registry MULTIPLE) /
        STATUTE_ONLY (a NAMED EU instrument — a compound/multi-word EU-head
        nickname directly governing an ``N artikla`` — that the registry does not
        know and that carries no minable cite here; the EU TYPE is asserted, the
        CELEX is left open, routed to ``eu-nickname:<surface>``).

    Named-instrument vs bare-head discipline (FAIL-LOUD):
      * a registry-miss nickname-shaped head with an adjacent formal EU cite
        resolves to that cite's CELEX (EXACT);
      * a registry-miss NAMED EU instrument (compound/multi-word EU-head, e.g.
        ``ESAP-asetuksen``, ``rahoitusvälineiden markkinat -asetuksen``)
        directly governing an article is emitted STATUTE_ONLY — Finnish acts use
        § not artikla, so the article-governed EU-head is unambiguously an EU
        instrument; typing it EU (not ``fi-name:``) is correct even unresolved;
      * a BARE standalone head (a domestic ``asetus`` / anaphoric ``direktiivin``
        whose article number is governed elsewhere — no glued instrument name) is
        NOT emitted: a bare ``eu-nickname:<head>`` would be a false positive and
        would double-count against the formal-cite lane.
    """
    source_ref = ProvisionRef(
        statute_id=source_statute_id,
        provision_path=source_provision_path,
    )
    out: list[EuDirectiveRef] = []
    for am in _ARTIKLA_RE.finditer(text):
        nickname = _find_nickname(text, am.start(), local_aliases)
        if nickname is None:
            continue
        surface, res = nickname
        confidence, celex = _status_for(res)
        if confidence is CiteConfidence.STATUTE_ONLY:
            # No registry hit. Prefer to RESOLVE: an adjacent formal EU cite in
            # the window pins the bare head to a CELEX (EXACT). The window is the
            # nickname lookbehind plus the cite that may sit between the head and
            # the article number.
            window = text[max(0, am.start() - _NICKNAME_LOOKBEHIND) : am.start()]
            resolved_celex = _celex_from_formal_cite(window, surface)
            if resolved_celex is not None:
                confidence = CiteConfidence.EXACT
                celex = (resolved_celex,)
            elif _is_named_eu_instrument(surface):
                # No formal cite, but the nickname is a NAMED EU instrument
                # (a compound/multi-word EU-head) directly governing an
                # ``N artikla`` — Finnish statutes use § not artikla, so this is
                # unambiguously an EU-instrument reference, just unresolved
                # (registry miss + no minable cite here). TYPE it as an EU
                # statute_only mention routed to ``eu-nickname:<surface>`` so it
                # is NOT mis-typed as a ``fi-name:`` Finnish statute by the
                # by-name lane. Tag-don't-guess: never invent a CELEX.
                pass  # keep confidence == STATUTE_ONLY; emitted below.
            else:
                # A BARE anaphoric/domestic head (``tässä asetuksessa``,
                # ``mainitun direktiivin``) whose article number is governed
                # elsewhere — no instrument identity. Drop (fail-loud), as
                # before; a bare ``eu-nickname:<head>`` would be a false positive
                # and would double-count against the formal-cite lane.
                continue
        articles = _expand_articles(am.group("nums"))
        if not articles:
            continue

        # Intra-article element: when the article is in the genitive
        # (``N artiklan``), an immediately-following ``M [ja K] kohdassa
        # [L alakohdassa]`` tail carries the kohta (paragraph/point) and optional
        # alakohta (sub-point) below the article. The tail expands the same way an
        # article coordination does (one kohta per coordinated number). When no
        # tail is present (a bare ``N artiklassa`` / article-only cite), the
        # reference stays at article level exactly as before.
        kohta_tail: Optional[_KohtaTail] = None
        surface_end = am.end()
        if (am.group("artcase") or "").lower() == _ARTIKLA_GENITIVE_CASE:
            kohta_tail = _parse_kohta_tail(text[am.end() :])
            if kohta_tail is not None:
                surface_end = am.end() + kohta_tail.end
        surface_text = text[am.start() : surface_end].strip()
        # One entry per kohta when a tail is present, else a single None
        # placeholder so the article-only path is unchanged. Likewise the alakohta
        # axis is the parsed sub-point list, or a single None when no alakohta
        # followed (article+kohta only). The reference set is the cartesian product
        # article × kohta × alakohta — exactly the enumeration a written
        # coordination spells out.
        kohta_values: tuple[Optional[int], ...] = (
            kohta_tail.kohdat if kohta_tail is not None else (None,)
        )
        alakohta_values: tuple[Optional[str], ...] = (
            kohta_tail.alakohdat
            if kohta_tail is not None and kohta_tail.alakohdat
            else (None,)
        )

        # By this point ``confidence`` is one of:
        #   * EXACT       — registry SINGLE, or a bare head resolved via an
        #     adjacent formal cite → a concrete ``celex:`` target.
        #   * AMBIGUOUS   — registry MULTIPLE → unresolved ``eu-nickname:``.
        #   * STATUTE_ONLY— a NAMED EU instrument (compound/multi-word EU-head)
        #     directly governing an article but with no registry hit and no
        #     minable cite here → unresolved ``eu-nickname:`` (the EU TYPE is
        #     asserted; the CELEX is left open — tag, don't guess). A bare
        #     anaphoric/domestic head never reaches here (dropped above).
        for article in articles:
            for kohta in kohta_values:
                for alakohta in alakohta_values:
                    if confidence is CiteConfidence.EXACT:
                        target = ProvisionRef(
                            statute_id=f"celex:{celex[0]}",
                            section_label=article,
                            subsection_num=kohta,
                            item_label=alakohta,
                        )
                    else:  # AMBIGUOUS / STATUTE_ONLY — do not pick/invent a CELEX
                        target = ProvisionRef(
                            statute_id="eu-nickname:" + surface,
                            section_label=article,
                            subsection_num=kohta,
                            item_label=alakohta,
                        )
                    mention = ReferenceMention(
                        source_provision_ref=source_ref,
                        target_provision_ref=target,
                        cite_kind=CiteKind.EU,
                        cite_confidence=confidence,
                        phrase_lemma="eu_directive_nickname_article",
                        source_span=None,
                        valid_at_interval=(None, None),
                        edge_subtype=None,
                        surface_text=surface_text,
                    )
                    out.append(
                        EuDirectiveRef(
                            mention=mention,
                            nickname_surface=surface,
                            celex_candidates=celex,
                            article=article,
                        )
                    )
    return out


__all__ = [
    "EuDirectiveRef",
    "recognize_eu_directive_refs",
]
