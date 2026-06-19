"""Internal (same-statute) bare section-reference recognition.

This lane owns the single largest reference-recall gap: BARE / INTERNAL section
references in body prose that point at a provision of the SAME statute, with no
statute identity of their own. These are the references the recall bench reports
under ``[SECTION]`` — tens of thousands of misses no other lane emits:

  - ``tämän lain 5 §:ssä``                 ("in § 5 of this act")
  - ``Edellä 1 ja 2 momentissa``           ("in subsections 1 and 2 above")
  - ``104 §:n 2 momentissa säädetään``     ("as provided in § 104(2)")
  - ``3 §:n 1 momentin 4 kohdassa``        ("in § 3(1)(4)")
  - ``108—110 §``                          (en-dash section range)
  - ``6 ja 8 §:ssä``                       (section coordination)

The structural tail (§ / momentti / kohta path, ranges, coordination) is parsed
by the SHARED body sub-ref grammar via
:func:`lawvm.finland.references.sections.parse_body_provision_tail` (§-anchored
shapes) and :func:`...grammar.subref.recognize_sub_refs` in ``body`` mode
(the bare-momentti/kohta shapes that carry no §). This lane adds ONLY the
recognizer that finds the citation site in prose, decides it is INTERNAL (not a
cross-statute case some other lane owns), and lifts each resolved provision to a
``ReferenceMention`` targeting the same statute.

LANE BOUNDARY (no double-emission):
  A bare § reference is INTERNAL unless it is owned by another lane:
    - preceded by a statute id ``(NNN/YYYY)``   → ``extract_plain_text_statute_mentions``
    - preceded by an inflected statute-NAME head → the cross-statute by-name lane
        (``…lain`` / ``…laissa`` / ``…asetuksen`` …)
  Those cases are EXCLUDED here. The one subtlety: a name-suffix word preceded by
  a self-referential demonstrative (``tämän lain`` / ``tässä laissa``) means
  "this act" → INTERNAL → ours. The vague catch-all (``muussa laissa``) is owned
  by the vague-OPEN lane and never produces a § path, so it never reaches here.

FAIL-LOUD (AGENTS.md §1.1): a bare reference is NEVER silently widened to the
whole statute. A trigger that fires but parses no concrete provision path is
dropped (we prefer not-emitting over guessing); only the rare case where a § is
present but no tail parses yields a STATUTE_ONLY section-less self-reference.
A cross-statute case is never emitted from this lane.

§1.11 hot-path regex discipline: patterns compiled at module scope, bounded
quantifiers, substring guards before the scan.
"""
from __future__ import annotations

import dataclasses
import re
from typing import List, Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.johtolause.grammar import sections as _sections  # noqa: F401  (load order: break subref circular import)
from lawvm.finland.johtolause.grammar.subref import recognize_sub_refs
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.lemma_gate import chapter_head_alternation
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    parse_body_provision_tail,
)

# ---------------------------------------------------------------------------
# Substring guards (fast path — eliminate non-matching paragraphs cheaply)
# ---------------------------------------------------------------------------
_GUARD_SECTION = "\xa7"  # §
_GUARD_MOMENTTI = "moment"  # momentissa / momentin / momenteissa cue
# Chapter-cue guard: the inflected ``luku`` stem appears as ``luvu…`` (genitive
# luvun, inessive luvussa, plural luvut/luvuissa) or as ``luku…`` (nominative
# luku, illative lukuun). Either stem is a cheap presence check for the chapter
# passes; both are substrings of ``lu``, so the guard is just that prefix.
_GUARD_CHAPTER = "lu"

# ---------------------------------------------------------------------------
# Section-citation surface recognizer (§-anchored shapes)
# ---------------------------------------------------------------------------
#
# Captures the FULL citation surface from the leading section number run through
# the § and its momentti/kohta tail, so the captured text can be fed verbatim to
# the shared body tail parser (no trailing prose). Bounded quantifiers only.
#
#   section label:  \d{1,6}(?:\s*[a-z])?   (e.g. 7, 7a, 7 a, 104, 115 a)
#   number run:     label (sep label)* with ,/ja/sekä/tai/en-dash joiners
#   §:             § optionally with an inflection suffix (§:ssä, §:n, §:ää)
#   tail step:      <number run> momentti|momentin|kohta|kohdassa   (repeatable)
#
# The letter suffix is written in body prose WITH a space (``115 a §``,
# ``47 a §:ssä``, ``106 a–106 e §:ää``) far more often than glued (``115a``),
# so the optional letter must tolerate intervening whitespace. The shared body
# tail parser already normalizes both spaced and glued forms to the glued AKN
# eId label (``115 a §`` → ``sec_115a``), so the captured surface resolves
# regardless of which spacing the source used.
_SEC_LABEL = r"\d{1,6}(?:\s*[a-zA-Z])?"
_SEP = r"(?:,|ja|sekä|tai|[–—-])"
_NUM_RUN = rf"{_SEC_LABEL}(?:\s*{_SEP}\s*{_SEC_LABEL})*"
_TAIL_NOUN = r"(?:moment\w+|kohda\w+|kohta)"

_SECTION_SURFACE_RE = re.compile(
    rf"""
    (?P<surf>
        {_NUM_RUN}
        \s*§(?::[a-zäöå]+)?
        (?:\s+{_NUM_RUN}\s+{_TAIL_NOUN})*
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Year / decree-year glue guard
# ---------------------------------------------------------------------------
#
# A number run is GREEDY across coordination joiners, so a 4-digit YEAR that
# abuts the run gets swallowed as if it were the first SECTION number:
#
#   ``vuoden 1971, 53 §:n 5 momentissa``  -> run ``1971, 53`` -> bogus § 1971
#   ``asetuksessa 1314/1996, 7 ja 17 §``  -> run ``1996, 7 ja 17`` -> bogus § 1996
#   ``vuoden 1984 ja 16 §:n 3 momentissa`` -> run ``1984 ja 16`` -> bogus § 1984
#
# The bogus token is ALWAYS the LEADING element of the captured surface, and the
# original text preceding it is either a YEAR word (``vuoden`` / ``vuodelta`` /
# ``vuonna`` / ``vuotta``) or a decree-id slash (``1314/1996`` -> the ``/`` just
# before the year). A genuine leading § number is NEVER preceded by either. When
# detected, the leading 4-digit year token + its trailing coordination separator
# are stripped, so the REAL provisions in the clause (``53 §:n 5 mom``, ``7 ja
# 17 §``) parse and the year never becomes a §. Stripping is conservative: only a
# bare 4-digit token with no letter suffix qualifies as a year.
_LEADING_YEAR_RE = re.compile(
    rf"^(?P<year>\d{{4}})\s*(?:{_SEP})\s*",
    re.IGNORECASE,
)
# A year-word or decree-id slash immediately before the surface marks the
# leading 4-digit token as a YEAR (``vuoden 1971`` / ``1314/1996``), not a §.
_YEAR_CONTEXT_RE = re.compile(
    r"(?:vuo(?:den|delta|nna|tta|sina|silta)|/)\s*$",
    re.IGNORECASE,
)


def _strip_leading_year(surface: str, before: str) -> str:
    """Drop a leading 4-digit YEAR token (+ separator) glued onto a § run.

    Returns ``surface`` with a leading ``YYYY,`` / ``YYYY ja`` stripped IFF the
    text immediately before the surface is a year word (``vuoden``) or a decree-id
    slash (``1314/1996``), so the year never parses as a § number. Otherwise the
    surface is returned unchanged (a genuine leading § is left intact).
    """
    ym = _LEADING_YEAR_RE.match(surface)
    if ym is None:
        return surface
    if _YEAR_CONTEXT_RE.search(before):
        return surface[ym.end() :]
    return surface

# Chapter prefix (``N luvun`` / ``N luku`` / ``N luvussa`` …) that qualifies a
# following section reference: ``3 luvun 5 §``, ``2 luvun 4 §:n 1 momentti``,
# coordinated ``3 ja 4 luvun 5 §``. The chapter number run reuses ``_NUM_RUN``
# (so ``ja`` / ``sekä`` / ranges coordinate). The ``luku`` head carries any
# Finnish case suffix (``luvun`` genitive, ``luvussa`` inessive, ``luku`` nom,
# ``luvut`` plural, ``lukuun`` …). Matched as a trailing prefix on the context
# BEFORE a section surface; the captured chapter run is applied to every target
# the following §-tail expands to.
#
# The head surfaces are M1-GENERATED from the closed ``luku`` head over the
# curated chapter case set (genitive / inessive / elative / illative + plural
# nominative/inessive + nominative), not a hand-typed paradigm table. This is the
# sound, single, shared chapter-head recognizer the body-tail lane
# (``references.sections``) reuses; it kills the single-k gradation substring bug
# class (``luku`` -> ``luvu-`` is generated, never inferred) and the rule-of-three
# table duplication. See ``lemma_gate.chapter_head_alternation``.
_CHAPTER_HEAD = rf"(?:{chapter_head_alternation()})"
_CHAPTER_PREFIX_RE = re.compile(
    rf"(?P<chnums>{_NUM_RUN})\s+{_CHAPTER_HEAD}\s*$",
    re.IGNORECASE,
)

# Chapter-only reference with NO following section: ``2 luvun säännöksiä``,
# ``3 luvussa tarkoitettu``. The chapter is concrete (a number run) but no §
# follows — emit a chapter-scoped internal mention (STATUTE_ONLY confidence:
# act is fixed = this statute, chapter is known, section deferred — never
# widened to the whole statute). A bare ``luku`` head with no number is NOT
# matched (fail-loud: no chapter to record → no emission).
_CHAPTER_ONLY_RE = re.compile(
    rf"\b(?P<chnums>{_NUM_RUN})\s+{_CHAPTER_HEAD}\b",
    re.IGNORECASE,
)

# Bare-momentti / bare-kohta surface (NO §), only after an internal lead-in cue
# (``Edellä`` / ``jäljempänä``). ``Edellä 1 ja 2 momentissa`` names subsections
# of the section currently being read — an internal self-reference with no § of
# its own. Without the cue, a bare ``N momentissa`` is too ambiguous to claim.
#
#   lead-in:   Edellä | Jäljempänä (case-insensitive, word-boundary)
#   body:      <number run> momentissa|momenteissa|kohdassa
_BARE_LEADIN = r"(?:edell\xe4|j\xe4ljemp\xe4n\xe4)"
_BARE_NOUN = r"(?:moment(?:issa|eissa|in)|kohda(?:ssa|n)|kohta)"
_BARE_SUBREF_RE = re.compile(
    rf"""
    \b(?P<leadin>{_BARE_LEADIN})\s+
    (?P<surf>{_NUM_RUN}\s+{_BARE_NOUN})
    """,
    re.VERBOSE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Exclusion recognizers on the PRECEDING context (lane boundary)
# ---------------------------------------------------------------------------
#
# A statute-id parenthetical immediately before the citation → plain-text by-id
# lane owns it.  e.g. ``(123/2020) 5 §``. The id appears both parenthesised
# ``(1/1998)`` and bracketed ``[1/1998]`` (the latter in ``(ampuma-aselain
# [1/1998] 20 §)`` editorial back-references); either form is an EXTERNAL-law
# anchor, so the following § is NOT an internal self-reference.
# The year is ``\d{2,4}``: pre-2000 statutes carry a 2-digit decree year
# (``(555/81)``, ``(495/89)``) as routinely as the modern 4-digit form. A
# 2-digit-year id is just as much an EXTERNAL-law anchor as a 4-digit one, so the
# following § is NOT an internal self-reference — gating on ``\d{4}`` let every
# old-style id leak its sections in as bogus internal targets.
_PRECEDING_STATUTE_ID_RE = re.compile(
    r"[(\[]\s*\d{1,6}/\d{2,4}\s*[)\]]\s*$",
)

# An inflected statute-NAME head immediately before the citation → cross-statute
# by-name lane owns it.  The trailing word ends in a Finnish law/decree case
# suffix (``…lain``, ``…laissa``, ``…asetuksen``, …). Captures the (optional)
# word before it so a self-referential demonstrative can be detected.
#
# Beyond the ``laki`` / ``asetus`` families, a small CLOSED set of named
# constitutional / procedural instruments carry their own nominal stem instead of
# ``laki``: ``valtiopäiväjärjestys`` (the old parliament act), ``hallitusmuoto``
# (the old constitution), an institution's ``työjärjestys`` (rules of procedure).
# These are by-name EXTERNAL statutes too — their § is NOT an internal
# self-reference — but their head ends in ``-järjestyksen`` / ``-muodon``, not a
# ``laki`` suffix, so the section leaked in as a bogus internal target
# (``valtiopäiväjärjestyksen 67 §`` in a 49-§ act). The stems are compound-only
# (a modifier precedes them), so the surrounding ``[chars]+`` capture keeps them
# from matching the bare common nouns ``järjestys`` / ``muoto``.
#
# The historical CODES (``-kaari``: oikeudenkäymiskaari, maakaari, kauppakaari,
# perintökaari, …) are by-name EXTERNAL statutes too. ``kaari`` declines as a
# Kotus type-26 ``-i`` noun with oblique stem ``kaare-`` (genitive ``kaaren``,
# inessive ``kaaressa``, illative ``kaareen`` …). A ``§`` tail preceded by such a
# head (``oikeudenkäymiskaaren 12 luvun 32 §:ää``) is owned by the cross-statute
# by-name lane, NOT an internal self-reference. The compound-only ``[chars]+``
# capture keeps the common nouns (``sateenkaari``, ``hammaskaari``) reachable but
# they never carry a ``§`` tail.
#
# The suffix surfaces are M1-GENERATED from the closed name-head lemma set —
# paradigm inversion, not a hand-typed table that duplicates (and drifts from) the
# by-name lane and risks a consonant-gradation substring bug. Each head is
# generated over the EXACT ``(case, number)`` set the old hand table encoded for
# it (not the full paradigm): widening to the full paradigm would add forms the
# old table deliberately omitted — the plural ``työjärjestykset`` and the illative
# ``asetukseen`` — which, for an EXCLUSION recognizer, would EXCLUDE genuine
# internal references (``Työjärjestykset 3 §``, ``lisätään asetukseen … 11 §``,
# the statute's own heading / johtolause), i.e. a recall regression, not a gain.
# So this is a strict-EQUAL replacement of the recognitions (gradation-correct by
# construction), not a recall-changing superset. The curated case sets:
#   laki / asetus : GEN PART INE ELA TRA ADE ABL ALL NOM (sg)
#   järjestys / muoto : GEN PART INE ELA NOM (sg)
#   kaari : GEN PART INE ELA ILL ADE ABL ALL TRA (sg) — no NOM (the bare ``kaari``
#     collides with the common noun ``sateenkaari`` and with the code's OWN title
#     heading a chapter, ``Maakaari 1 LUKU`` in 1734/1).
_FULL_OBLIQUE_SG: tuple[tuple[str, str], ...] = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"),
    ("TRA", "SG"), ("ADE", "SG"), ("ABL", "SG"), ("ALL", "SG"), ("NOM", "SG"),
)
_GRAMMATICAL_OBLIQUE_SG: tuple[tuple[str, str], ...] = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"), ("NOM", "SG"),
)
_KAARI_OBLIQUE_SG: tuple[tuple[str, str], ...] = (
    ("GEN", "SG"), ("PART", "SG"), ("INE", "SG"), ("ELA", "SG"), ("ILL", "SG"),
    ("TRA", "SG"), ("ADE", "SG"), ("ABL", "SG"), ("ALL", "SG"),
)
# reference_v1 profile gap supplement: the kaari essive + instructive the old hand
# table carried but M1's reference_v1 case profile omits (documented M1 boundary).
# (The old table's ``kaartta`` is dropped — it is a wrong partitive form; the real
# ``kaarta`` IS generated, so no genuine recognition is lost.)
_NAME_SUFFIX_SUPPLEMENT: tuple[str, ...] = ("kaarena", "kaarin")


def _build_name_suffix_forms() -> tuple[str, ...]:
    """Build the M1-backed name-head exclusion alternation (longest-first)."""
    from lawvm.finland.references.lemma_gate import head_case_forms

    forms: set[str] = set(_NAME_SUFFIX_SUPPLEMENT)
    forms.update(head_case_forms("laki", _FULL_OBLIQUE_SG))
    forms.update(head_case_forms("asetus", _FULL_OBLIQUE_SG))
    forms.update(head_case_forms("järjestys", _GRAMMATICAL_OBLIQUE_SG))
    forms.update(head_case_forms("muoto", _GRAMMATICAL_OBLIQUE_SG))
    forms.update(head_case_forms("kaari", _KAARI_OBLIQUE_SG))
    return tuple(sorted(forms, key=lambda s: (-len(s), s)))


_NAME_SUFFIX_FORMS = _build_name_suffix_forms()
_NAME_SUFFIX = "(?:{})".format("|".join(_NAME_SUFFIX_FORMS))
_PRECEDING_NAME_HEAD_RE = re.compile(
    rf"(?P<prev>[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+)?\s*"
    rf"(?P<head>[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5\-]*{_NAME_SUFFIX})\s*$",
    re.IGNORECASE,
)

_NAME_HEAD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "\xe4\xf6\xe5\xc4\xd6\xc5-"
)
_NAME_PREV_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "\xe4\xf6\xe5\xc4\xd6\xc5"
)


@dataclasses.dataclass(frozen=True, slots=True)
class _PrecedingNameHead:
    prev: str
    head: str


def _preceding_name_head(before: str) -> _PrecedingNameHead | None:
    """Return the final statute-name head in ``before`` if one abuts the end.

    This is the non-regex equivalent of ``_PRECEDING_NAME_HEAD_RE.search`` for
    the hot exclusion path. It only recognizes a final head token whose suffix is
    in the same M1-generated closed suffix set, plus an optional immediately
    preceding demonstrative/name token.
    """
    stripped = before.rstrip()
    if not stripped:
        return None
    end = len(stripped)
    head_start = end
    while head_start > 0 and stripped[head_start - 1] in _NAME_HEAD_CHARS:
        head_start -= 1
    if head_start == end:
        return None
    head = stripped[head_start:end]
    head_lower = head.lower()
    if not any(head_lower.endswith(suffix) for suffix in _NAME_SUFFIX_FORMS):
        return None
    prefix = stripped[:head_start].rstrip()
    prev_end = len(prefix)
    prev_start = prev_end
    while prev_start > 0 and prefix[prev_start - 1] in _NAME_PREV_CHARS:
        prev_start -= 1
    return _PrecedingNameHead(
        prev=prefix[prev_start:prev_end],
        head=head,
    )

# A self-referential demonstrative makes ``… lain / … laissa`` mean "THIS act" →
# INTERNAL → ours (overrides the name-head exclusion). ``tämän lain``,
# ``tässä laissa``, ``tähän lakiin`` …
#
# A demonstrative in a LOCAL (spatial) case binds the law-name head only when it
# AGREES with the head's case. A local-case demonstrative that DISAGREES does NOT
# qualify the name — it reaches a DOWNSTREAM noun, leaving the name a genuine
# EXTERNAL cross-statute anchor. The body shape that exposed this: ``tähän``(ill)
# ``arvopaperimarkkinalain``(gen) ``6 luvun 10 §:n 2 momentissa tarkoitetussa
# suhteessa`` — ``tähän`` agrees with ``suhteessa`` (``tähän … suhteessa olevan
# henkilön``), NOT with the genitive ``…lain`` head, so the citation is
# cross-statute (arvopaperimarkkinalaki), not internal. The grammatical-case
# (nom / gen / par) demonstratives carry no such directional reach, so they still
# bind even on a case mismatch — that mismatch is a drafting/source spelling of
# "this act" (``Tämä lain 8 a §`` for ``Tämän lain``), and excluding it would
# drop a genuine internal self-reference. See ``_demonstrative_binds_name``.
_DEMONSTRATIVE_CASE = {
    "t\xe4m\xe4": "nom",
    "t\xe4m\xe4n": "gen",
    "t\xe4t\xe4": "par",
    "t\xe4ss\xe4": "ine",
    "t\xe4st\xe4": "ela",
    "t\xe4h\xe4n": "ill",
    "t\xe4ll\xe4": "ade",
}
_SELF_DEMONSTRATIVES = frozenset(_DEMONSTRATIVE_CASE)

# Case of a statute-NAME head, by inflection suffix, so a preceding demonstrative
# can be checked for AGREEMENT. Mirrors the suffixes in ``_NAME_SUFFIX``; an
# unrecognized suffix yields ``None`` (treated as non-agreeing → no override).
_NAME_HEAD_CASE_SUFFIXES = (
    ("ssa", "ine"),  # laissa / asetuksessa / järjestyksessä / muodossa / kaaressa
    ("ss\xe4", "ine"),
    ("sta", "ela"),  # laista / asetuksesta / muodosta / kaaresta
    ("st\xe4", "ela"),
    ("lla", "ade"),  # lailla / asetuksella / kaarella
    ("ll\xe4", "ade"),
    ("een", "ill"),  # kaareen
    ("iin", "ill"),  # lakiin / asetukseen-style long-vowel illative
    ("n", "gen"),  # lain / asetuksen / järjestyksen / muodon / kaaren
    ("a", "par"),  # lakia / asetusta / muotoa / kaartta / kaarta
    ("\xe4", "par"),  # järjestystä
)


def _name_head_case(head: str) -> Optional[str]:
    """Grammatical case of a statute-name head from its inflection suffix.

    Returns ``"nom" / "gen" / "par" / "ine" / "ela" / "ill" / "ade"`` or ``None``
    when the suffix is not one of the recognized ``…lain``/``…laissa``/… forms.
    Longest distinctive suffix first so ``laissa`` (ine) is not mis-read as the
    ``…a`` partitive and ``lain`` (gen) is not mis-read as a bare ``…n``.
    """
    h = head.lower()
    for suffix, case in _NAME_HEAD_CASE_SUFFIXES:
        if h.endswith(suffix):
            return case
    # Bare nominative heads (``laki`` / ``asetus`` / ``muoto`` / ``järjestys``).
    return "nom"


# The LOCAL (spatial / directional) cases. A demonstrative in one of these is
# the tell of a DOWNSTREAM bind: ``tähän``(ill) reaches forward to a later noun
# (``tähän … suhteessa olevan henkilön``). A genuine "this act" qualifier in a
# local case ALWAYS agrees with the law form (``tässä laissa`` ine+ine, ``tähän
# lakiin`` ill+ill), so a local-case demonstrative that DISAGREES with the head
# never means "this act". The grammatical (nom / gen / par) demonstratives do not
# carry this directional reach, so a nom/gen/par mismatch is left binding — it is
# overwhelmingly a drafting/source spelling of "this act" (``Tämä lain 8 a §`` for
# ``Tämän lain``), not a downstream bind, and excluding it would drop a genuine
# internal self-reference.
_LOCAL_CASES = frozenset({"ine", "ela", "ill", "ade"})


def _demonstrative_binds_name(prev: str, head: str) -> bool:
    """True iff a leading demonstrative ``prev`` makes ``head`` mean "this act".

    Binds (→ INTERNAL self-reference) UNLESS the demonstrative is in a LOCAL case
    that DISAGREES with the law-name head's case — that combination is the
    downstream-bind tell (``tähän``(ill) ``…lain``(gen) ``… suhteessa``), where
    the demonstrative reaches a later noun and the name is a genuine external
    cross-statute anchor. A grammatical-case (nom/gen/par) demonstrative, or one
    that AGREES with the head (``tässä``+``laissa``, ``tähän``+``lakiin``), still
    binds.
    """
    dem_case = _DEMONSTRATIVE_CASE.get(prev)
    if dem_case is None:
        return False
    if dem_case in _LOCAL_CASES and dem_case != _name_head_case(head):
        return False
    return True

# How far back to look for a preceding name head / statute id.
_LOOKBACK = 80
# A wider window for coordination-governed citations: a long section list
# (``2 §:ssä, 69 §:n 1 momentissa, 71 §:ssä, 72 §``) can push the governing
# anchor several hundred chars before the last member. Used only after the
# trailing sibling fragments are peeled (so the name-head check still runs on a
# bounded ``_LOOKBACK`` slice at the head, not the whole window).
_COORD_LOOKBACK = 600

# A section-coordination FRAGMENT that precedes the citation when several
# sections of ONE governing act are listed: ``…(48/1999) 2 §:n 13 kohdassa,
# 69 §:n 1 momentissa, 71 §:ssä, 72 §…``. When the citing § is a later member of
# such a list, the governing external-law id / name head sits BEFORE the whole
# run — past the bounded name-head window. To find it we strip trailing
# ``…N §… ,`` fragments off the preceding context (right to left) so the
# name-head / id check sees the governing anchor at the head of the coordination,
# not the intervening sibling §. Each fragment is ``<num run> § <tail> <sep>``.
# Bounded quantifiers only (§1.11).
#
# The fragment tail tolerates the ABBREVIATED ``mom`` / ``mom.`` momentti spelling
# (``3 §:n 2 mom, 5, 6 …``) alongside the full ``momentissa`` form, because the
# governing-anchor reachability depends on peeling that fragment whole; a fragment
# whose ``2 mom`` tail did not match left the bare-number siblings (``5, 6 …``)
# governed-anchor-blind, leaking them in as bogus internal targets.
_COORD_TAIL_NOUN = r"(?:moment\w+|kohda\w+|kohta|mom\.?|k\.?)"
_COORD_FRAGMENT_RE = re.compile(
    rf"""
    \s*{_NUM_RUN}\s*§(?::[a-zäöå]+)?
    (?:\s+{_NUM_RUN}\s+{_COORD_TAIL_NOUN})*
    \s*(?:{_SEP})\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# A trailing CHAPTER-bearing coordination fragment governed by the same act, in
# either of the two chapter shapes that appear mid-list:
#   * chapter-only:        ``…, 20 luvussa, …``           (no § in the fragment)
#   * chapter + section:   ``…, 17 luvun 18, 18 a tai 19 §:ssä, …``
# A long mixed rikoslaki list (``rikoslain (39/1889) 17 luvun … §:ssä, 20
# luvussa, 21 luvun 1—3 tai 6 §:ssä``) interleaves these with the plain §
# fragments; without peeling them the governing id sits past reach and the later
# members (``20 luvussa``, ``21 luvun …``) leak in as bogus internal targets.
# The optional ``luvun M §`` section tail is consumed so the chapter+section
# shape is peeled whole. Bounded quantifiers only (§1.11).
_COORD_CHAPTER_FRAGMENT_RE = re.compile(
    rf"""
    \s*{_NUM_RUN}\s+{_CHAPTER_HEAD}
    (?:\s+{_NUM_RUN}\s*§(?::[a-zäöå]+)?
        (?:\s+{_NUM_RUN}\s+{_COORD_TAIL_NOUN})*
    )?
    \s*(?:{_SEP})\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _strip_trailing_coord_fragments(before: str) -> str:
    """Remove trailing sibling ``N §…,`` coordination fragments from ``before``.

    A citation that is a later member of a section coordination governed by one
    act (``…annetun lain 2 §:ssä, 69 §:n 1 momentissa, 72 §``) has its governing
    name head / id BEFORE the first member. Peeling the trailing ``…, 69 §…,``
    siblings off the preceding context lets :func:`_is_excluded` see that
    governing anchor instead of the adjacent sibling §. Iterates while a fragment
    abuts; bounded (each step shortens the string).
    """
    prev = None
    while before != prev:
        prev = before
        # Peel a trailing § fragment OR a chapter-bearing fragment, whichever
        # abuts. Both shapes appear interleaved in one governed coordination
        # (``17 luvun … §:ssä, 20 luvussa, 21 luvun … §:ssä``); peeling either
        # lets the governing id/name at the head of the run become reachable.
        m = _COORD_FRAGMENT_RE.search(before)
        cm = _COORD_CHAPTER_FRAGMENT_RE.search(before)
        # Take the fragment that strips MORE (the earlier start) so a chapter
        # fragment is not shadowed by a shorter § sub-match inside it.
        cut = None
        for cand in (m, cm):
            if cand is not None and cand.start() >= 0:
                if cut is None or cand.start() < cut:
                    cut = cand.start()
        if cut is None:
            break
        before = before[:cut]
    return before


def _strip_chapter_prefix(before: str) -> tuple[str, Optional[str]]:
    """Split a trailing ``N luvun`` chapter prefix off the preceding context.

    Returns ``(before_without_chapter, chapter_run)`` where ``chapter_run`` is
    the raw number run of the chapter (e.g. ``"3"`` or ``"3 ja 4"``), or
    ``(before, None)`` when no chapter prefix is present. Used both to recover
    the chapter context for an internal ref and so the exclusion check can see
    PAST the chapter prefix to a statute-name head (``jätelain 3 luvun 5 §`` is a
    cross-statute case — the name head is one ``luvun`` token further back).
    """
    cm = _CHAPTER_PREFIX_RE.search(before)
    if cm is None:
        return before, None
    return before[: cm.start()], cm.group("chnums")


def _preceding_chapter_match(text: str, start: int) -> Optional[re.Match[str]]:
    """Return the ``N luvun`` chapter-prefix match ending just before ``start``.

    Looks at the bounded window before the section surface at ``start``. The
    chapter prefix must be ADJACENT to the section (only whitespace between the
    ``luvun`` head and the section number) — a chapter named further away is not
    a qualifier for this section. ``None`` when no chapter prefix abuts.
    """
    before = text[max(0, start - _LOOKBACK) : start]
    cm = _CHAPTER_PREFIX_RE.search(before)
    return cm


def _is_excluded(text: str, start: int) -> bool:
    """True iff the citation at ``start`` is owned by another (cross-statute) lane.

    Excludes a citation preceded by a statute id ``(NNN/YYYY)`` (plain-text
    by-id lane) or by an inflected statute-NAME head (cross-statute by-name
    lane). A name head preceded by a self-referential demonstrative
    (``tämän lain`` / ``tässä laissa`` = "this act") is NOT excluded — it is an
    internal self-reference and therefore ours.

    An intervening ``N luvun`` chapter prefix is transparent to this check: a
    statute-name head one ``luvun`` token further back (``jätelain 3 luvun 5 §``)
    still owns the citation via the cross-statute by-name lane.
    """
    # A wider window than the bounded name-head lookback so the governing anchor
    # at the HEAD of a multi-section coordination is reachable once the trailing
    # sibling ``N §…,`` fragments are peeled off (``…annetun lain 2 §:ssä,
    # 69 §:n 1 momentissa, 72 §`` — the name head governs every member).
    wide = text[max(0, start - _COORD_LOOKBACK) : start]
    # Peel this member's own ``N luvun`` chapter prefix off the tail FIRST so the
    # preceding sibling fragments (``…, 20 luvussa, `` before ``21 luvun M §``)
    # become peelable — otherwise the trailing chapter prefix (no separator)
    # blocks the coordination peel and the governing id stays out of reach.
    wide_no_ch, _wch = _strip_chapter_prefix(wide)
    coord_head = _strip_trailing_coord_fragments(wide_no_ch)
    if coord_head != wide_no_ch or wide_no_ch != wide:
        # The citation is a later member of a section coordination; its governing
        # external-law id / name head (if any) sits just before the first member.
        head_ctx = coord_head[-_LOOKBACK:]
        head_ctx, _ch = _strip_chapter_prefix(head_ctx)
        if _PRECEDING_STATUTE_ID_RE.search(head_ctx):
            return True
        hm = _preceding_name_head(head_ctx)
        if hm is not None and not _demonstrative_binds_name(
            hm.prev.lower(), hm.head
        ):
            return True

    before = text[max(0, start - _LOOKBACK) : start]
    # Look PAST a chapter prefix so a name head before it still excludes
    # (``jätelain 3 luvun 5 §`` is cross-statute, not internal).
    before, _chapter = _strip_chapter_prefix(before)
    if _PRECEDING_STATUTE_ID_RE.search(before):
        return True
    m = _preceding_name_head(before)
    if m is not None:
        # A name head (``…lain`` / ``…laissa`` / ``…asetuksen`` …) immediately
        # before the citation is a cross-statute by-name case — EXCLUDED —
        # UNLESS a self-referential demonstrative AGREEING IN CASE with the head
        # makes it "THIS act" (``tämän lain`` / ``tässä laissa``), an internal
        # self-ref. A case-mismatched demonstrative (``tähän …lain``) binds a
        # downstream noun, not the name → stays cross-statute → EXCLUDED.
        if _demonstrative_binds_name(m.prev.lower(), m.head):
            return False
        return True
    return False


# ---------------------------------------------------------------------------
# Mention construction
# ---------------------------------------------------------------------------


# Split a chapter number run on COORDINATION joiners only (``ja`` / ``sekä`` /
# ``tai`` / comma) — NOT on dashes, which denote an inclusive range handled
# per-piece by ``_CHAPTER_RANGE_RE`` below.
_CHAPTER_COORD_SEP = r"(?:,|\bja\b|\bsek\xe4\b|\btai\b)"
_CHAPTER_SPLIT_RE = re.compile(rf"\s*{_CHAPTER_COORD_SEP}\s*", re.IGNORECASE)
# A single chapter range like ``3–5`` (en-dash / hyphen between two numbers).
_CHAPTER_RANGE_RE = re.compile(
    rf"^(?P<lo>{_SEC_LABEL})\s*[–—-]\s*(?P<hi>{_SEC_LABEL})$",
)


def _expand_chapter_run(chnums: str) -> List[str]:
    """Expand a chapter number run into individual chapter labels.

    ``"3"`` → ``["3"]``; ``"3 ja 4"`` → ``["3", "4"]``; ``"3–5"`` → ``["3","4","5"]``.
    Bare numeric chapters expand inclusively across a range; non-numeric labels
    (rare for chapters) are kept verbatim. Returns an empty list if nothing
    numeric parses (fail-loud — the caller then declines).
    """
    out: List[str] = []
    for piece in _CHAPTER_SPLIT_RE.split(chnums.strip()):
        piece = piece.strip()
        if not piece:
            continue
        rm = _CHAPTER_RANGE_RE.match(piece)
        if rm is not None and rm.group("lo").isdigit() and rm.group("hi").isdigit():
            lo, hi = int(rm.group("lo")), int(rm.group("hi"))
            if lo <= hi:
                out.extend(str(n) for n in range(lo, hi + 1))
                continue
        out.append(piece)
    return out


def _chapter_akn_path(
    chapter: str,
    *,
    section_label: str = "",
    subsection_num: Optional[int] = None,
    item_label: Optional[str] = None,
) -> str:
    """Build the AKN provision-path fragment carrying the chapter context.

    ProvisionRef has no first-class chapter field, so the chapter is encoded in
    ``provision_path`` using the SAME ``chp_N__sec_M__subsec_K__para_L`` AKN eId
    form the rest of the codebase uses (``core.locator``,
    ``finland.section_resolver``, ``references.interlinks`` all parse this with
    the ``__`` separator). The deeper components are appended so the interlink
    adapter — which prefers ``provision_path`` when it contains ``__`` — keeps
    the section/subsection/item rather than collapsing to chapter-only.
    """
    parts = [f"chp_{chapter}"]
    if section_label:
        parts.append(f"sec_{section_label}")
        if subsection_num is not None:
            parts.append(f"subsec_{subsection_num}")
            if item_label:
                parts.append(f"para_{item_label}")
    return "__".join(parts)


def _chapter_head_is_excluded(text: str, start: int) -> bool:
    """True iff a chapter-only ref at ``start`` is a cross-statute case.

    ``start`` is the offset of the chapter NUMBER run. A statute id ``(NNN/YYYY)``
    or an inflected statute-NAME head immediately before it (``jätelain 3
    luvussa`` / ``(123/2020) 3 luvussa``) means the chapter belongs to another
    act — owned by the cross-statute lanes — UNLESS a self-referential
    demonstrative makes the name head "this act". Reuses the same boundary
    logic as :func:`_is_excluded` (the section number plays the role the §
    surface plays there).
    """
    return _is_excluded(text, start)


def _target_to_ref(
    statute_id: str,
    tgt: BodyProvisionTarget,
    chapter: Optional[str] = None,
) -> ProvisionRef:
    provision_path = (
        _chapter_akn_path(
            chapter,
            section_label=tgt.section_label,
            subsection_num=tgt.subsection_num,
            item_label=tgt.item_label,
        )
        if chapter is not None
        else ""
    )
    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=tgt.section_label,
        subsection_num=tgt.subsection_num,
        item_label=tgt.item_label,
    )


def _make_mention(
    statute_id: str,
    surface: str,
    target_ref: ProvisionRef,
    confidence: CiteConfidence,
) -> ReferenceMention:
    """Build one INTERNAL ReferenceMention (span re-anchored downstream)."""
    src_ref = ProvisionRef(statute_id=statute_id, provision_path="", section_label="")
    return ReferenceMention(
        source_provision_ref=src_ref,
        target_provision_ref=target_ref,
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=confidence,
        phrase_lemma="internal_section_ref",
        source_span=None,  # the document-level integration re-anchors the span
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


# Numeric prefix of a section label (``16a`` -> 16, ``150f`` -> 150), for the
# above-max existence guard. ``None`` for a non-numeric label (never guarded).
_SEC_NUM_PREFIX_RE = re.compile(r"(\d{1,6})")


def _section_num_prefix(label: str) -> Optional[int]:
    m = _SEC_NUM_PREFIX_RE.match(label)
    return int(m.group(1)) if m is not None else None


def _internal_section_unsupported(
    section_label: str,
    known_sections: Optional[frozenset[str]],
) -> bool:
    """True iff an internal §-target's section cannot belong to this statute.

    A SECONDARY net for the external-law leak: when a governing external-law
    phrase did not anchor (it sits past the lookback, or in an earlier sentence),
    a foreign section number leaks in as a bogus INTERNAL target (``93 §`` in a
    42-section act). When the citing body is a TRUSTED, fully eId'd consolidated
    tree, a section number that is both ABSENT from that tree AND strictly ABOVE
    the tree's largest section cannot be a genuine same-statute reference — so the
    target is declined (the caller emits a fail-loud STATUTE_ONLY, never a
    concrete bogus internal target).

    Scoped to fail-loud, low collateral:
      * ``known_sections is None`` (non-consolidated / un-eId'd body, where
        absence is untrustworthy because the materialized tree is partial) ->
        NEVER guarded.
      * Only ABOVE the materialized max declines: a within-range "hole" is far
        more often a letter-suffix section the structure builder missed than a
        leak, so it is left intact (recall over a speculative decline).
    """
    if known_sections is None or not known_sections:
        return False
    if section_label in known_sections:
        return False
    tn = _section_num_prefix(section_label)
    if tn is None:
        return False
    maxnum = 0
    for k in known_sections:
        kn = _section_num_prefix(k)
        if kn is not None and kn > maxnum:
            maxnum = kn
    return maxnum > 0 and tn > maxnum


def recognize_internal_refs(
    text: str,
    statute_id: str,
    known_sections: Optional[frozenset[str]] = None,
) -> List[ReferenceMention]:
    """Recognize bare / internal same-statute section references in ``text``.

    Emits one :class:`ReferenceMention` per resolved provision, all targeting
    ``statute_id`` itself (``cite_kind=INTERNAL``).

    DOES emit: ``tämän lain N §…``, ``Edellä N momentissa``, bare ``N §:ssä`` /
    ``N §:n M momentissa`` / ``N §:n M momentin K kohdassa``, en-dash ranges and
    coordination — one mention per expanded provision. Chapter-qualified shapes
    (``3 luvun 5 §``, ``2 luvun 4 §:n 1 momentti``, coordinated
    ``3 ja 4 luvun 5 §``) carry the chapter in ``provision_path`` as the AKN
    ``chp_N__sec_M…`` eId form. A chapter with no section (``3 luvussa``,
    ``2 luvun säännöksiä``) emits a chapter-scoped STATUTE_ONLY mention
    (``provision_path="chp_N"``, section deferred — never widened).

    Does NOT emit: any reference preceded by a statute id ``(NNN/YYYY)`` or an
    inflected statute-NAME head (those are owned by the plain-text-by-id and
    by-name cross-statute lanes). Never widens a bare reference to "whole
    statute"; never emits a cross-statute case.

    ``cite_confidence`` is EXACT when a concrete provision path parses;
    STATUTE_ONLY only in the rare case where the § trigger fired but no path
    parsed. A trigger that yields no provision at all is dropped (prefer
    not-emitting over guessing).
    """
    if not text:
        return []
    lower = text.lower()
    has_section = _GUARD_SECTION in text
    has_momentti = _GUARD_MOMENTTI in lower
    has_chapter = _GUARD_CHAPTER in lower
    if not has_section and not has_momentti and not has_chapter:
        return []

    mentions: List[ReferenceMention] = []
    # Track byte/char spans already consumed by the §-anchored pass so the
    # bare-momentti pass does not re-emit a momentti that belongs to a § cite.
    consumed: List[tuple[int, int]] = []
    # Chapter-prefix spans already attached to a § citation; the chapter-only
    # pass must not re-emit these as standalone chapter references.
    consumed_chapters: List[tuple[int, int]] = []

    # ── §-anchored shapes (the common case) ────────────────────────────────
    if has_section:
        for m in _SECTION_SURFACE_RE.finditer(text):
            if _is_excluded(text, m.start()):
                continue
            surface = m.group("surf")
            # Drop a leading 4-digit YEAR token glued onto the § run when the
            # text just before it is a year word / decree-id slash (``vuoden 1971,
            # 53 §`` / ``1314/1996, 7 ja 17 §``), so the year never parses as a §
            # and the REAL provisions in the clause survive.
            before_surf = text[max(0, m.start() - 12) : m.start()]
            surface = _strip_leading_year(surface, before_surf)
            # A ``N luvun`` chapter prefix immediately before the section surface
            # qualifies it (``3 luvun 5 §`` → chapter 3, section 5). Recover the
            # chapter run and consume its span so the chapter-only pass does not
            # re-emit it as a bare chapter reference.
            win_start = max(0, m.start() - _LOOKBACK)
            cm = _preceding_chapter_match(text, m.start())
            chapters = _expand_chapter_run(cm.group("chnums")) if cm is not None else []
            if cm is not None:
                consumed_chapters.append(
                    (win_start + cm.start(), win_start + cm.end())
                )
            targets = parse_body_provision_tail(surface)
            if not targets:
                # § present but no parsable path: STATUTE_ONLY section-less
                # self-reference (act is fixed = this statute; provision
                # deferred, never widened). Rare; only when the recognizer
                # captured a § with no resolvable section label.
                mentions.append(
                    _make_mention(
                        statute_id,
                        surface,
                        ProvisionRef(statute_id=statute_id),
                        CiteConfidence.STATUTE_ONLY,
                    )
                )
                consumed.append((m.start(), m.end()))
                continue
            chapter_choices: List[Optional[str]] = list(chapters) or [None]
            for chapter in chapter_choices:
                for tgt in targets:
                    # Secondary external-leak net: a section that cannot belong
                    # to this statute (absent + above the trusted tree's max) is
                    # an external-law number that leaked in because its governing
                    # phrase did not anchor. Decline it to a fail-loud
                    # STATUTE_ONLY (act fixed = this statute, provision NOT
                    # claimed) — never a bogus concrete internal target.
                    if chapter is None and _internal_section_unsupported(
                        tgt.section_label, known_sections
                    ):
                        mentions.append(
                            _make_mention(
                                statute_id,
                                surface,
                                ProvisionRef(statute_id=statute_id),
                                CiteConfidence.STATUTE_ONLY,
                            )
                        )
                        continue
                    mentions.append(
                        _make_mention(
                            statute_id,
                            surface,
                            _target_to_ref(statute_id, tgt, chapter),
                            CiteConfidence.EXACT,
                        )
                    )
            consumed.append((m.start(), m.end()))

    # ── bare-momentti / bare-kohta shapes (no §; internal lead-in only) ─────
    if has_momentti:
        for m in _BARE_SUBREF_RE.finditer(text):
            if _is_excluded(text, m.start("leadin")):
                continue
            # Skip if this momentti run sits inside a § citation already taken.
            if any(s <= m.start("surf") < e for s, e in consumed):
                continue
            surface = m.group("surf")
            subs = _bare_subref_targets(surface)
            if not subs:
                continue
            for sub in subs:
                mentions.append(
                    _make_mention(
                        statute_id,
                        surface,
                        _internal_bare_target(statute_id, sub),
                        CiteConfidence.EXACT,
                    )
                )

    # ── chapter-only shapes (``2 luvun säännöksiä``, ``3 luvussa``) ─────────
    # A concrete chapter with NO following section. The act is fixed (= this
    # statute), the chapter is known, the in-chapter provision is deferred →
    # STATUTE_ONLY (never widened to the whole statute). Skip any chapter
    # prefix already attached to a § citation above, and any cross-statute
    # case (``jätelain 3 luvussa``).
    if has_chapter:
        for m in _CHAPTER_ONLY_RE.finditer(text):
            if any(s <= m.start() < e for s, e in consumed_chapters):
                continue
            if _chapter_head_is_excluded(text, m.start()):
                continue
            for chapter in _expand_chapter_run(m.group("chnums")):
                mentions.append(
                    _make_mention(
                        statute_id,
                        m.group(0),
                        ProvisionRef(
                            statute_id=statute_id,
                            provision_path=_chapter_akn_path(chapter),
                        ),
                        CiteConfidence.STATUTE_ONLY,
                    )
                )

    return mentions


def _bare_subref_targets(surface: str) -> List[ProvisionRef]:
    """Parse a bare ``N momentissa`` / ``N kohdassa`` run (no §) to ProvisionRefs.

    Routes the surface through the shared body sub-ref recognizer in ``body``
    mode (which promotes the inessive ``momentissa`` to MOMENTTI). The section is
    NOT named by the surface — a bare momentti reference names a subsection of
    the section currently being read — so ``section_label`` is left empty and the
    momentti/kohta carries the precision. Returns an empty list when nothing
    parses (fail-loud: no guessed widening).
    """
    toks = tokenize(surface)
    subs, _end = recognize_sub_refs(toks, 0, mode="body")
    refs: List[ProvisionRef] = []
    for sub in subs:
        sub_ref = sub.to_provision_ref(statute_id="", section_label="")
        # Only emit when the sub-ref actually carries a subsection or item; a
        # facet-only / empty sub-ref names no concrete provision path.
        if sub_ref.subsection_num is None and sub_ref.item_label is None:
            continue
        # statute_id is empty here; the caller re-keys onto the internal statute
        # via _internal_bare_target before building the mention.
        refs.append(sub_ref)
    return refs


def _internal_bare_target(statute_id: str, ref: ProvisionRef) -> ProvisionRef:
    """Re-key a bare sub-ref ProvisionRef onto the internal statute id."""
    return ProvisionRef(
        statute_id=statute_id,
        provision_path="",
        section_label=ref.section_label,
        subsection_num=ref.subsection_num,
        item_label=ref.item_label,
        subitem_label=ref.subitem_label,
    )
