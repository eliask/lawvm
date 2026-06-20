"""Recognizer for cross-statute references made by inflected statute NAME.

Closes the ``[STATUTE_NAME_HEAD]`` recall family: cross-statute references that
name the target by its inflected *title* with **no** ``(NNN/YYYY)`` parenthetical
id, e.g.::

    luonnonsuojelulaissa säädetään ...
    ympäristönsuojelulain 5 §:ssä
    maankäyttö- ja rakennuslain 132 §:ssä

No existing lane emits these: the plain-text lane
(``ref_mention_extractor.PlainTextStatuteCitationRecognizer``) *requires* a
``(NNN/YYYY)`` id anchor, and the ``<ref>`` lane needs explicit markup. This
module recognises the *inflected name head* alone.

Design discipline
-----------------
* **M1-derived head detection.** A statute name is a compound whose trailing
  *head* (``laki`` / ``asetus`` / ``päätös`` ...) carries the inflection while
  the modifier prefix rides invariant. We ask the merged M1 morphology engine
  (``generate_forms``, READ-ONLY) for the oblique case surfaces of each closed
  statute head (``laissa``, ``lain``, ``asetuksen`` ...) and match a token that
  ENDS in one of them (longest-first, so ``asetuksessa`` is split on the whole
  inflected head and never on a coincidental shorter suffix). The nominative
  surface (``laki``) is deliberately NOT a trigger — an uninflected bare head is
  not a by-name *citation*.

* **Tag, don't guess (fail-loud id).** The recognizer's job is to TYPE the
  reference as an unresolved-by-name cross-statute ref. The act id is NOT
  resolved here (only the name surface); resolution to a real ``NNN/YYYY`` id is
  a later PROJECTION step against the statute-name registry (M2). We therefore
  emit ``cite_confidence=STATUTE_ONLY`` and carry the name in the target ref as
  ``statute_id="fi-name:<normalized_name>"`` — never a fabricated id.

* **Name normalization.** The normalized key reattaches the *nominative* head to
  the invariant modifier (``luonnonsuojelu`` + ``laki`` ->
  ``luonnonsuojelulaki``), folded to lower case. When the modifier cannot be
  recovered confidently the raw inflected surface key is carried instead — never
  an invented base.

* **No double-emission / no lane theft.** A name head immediately followed by a
  ``(NNN/YYYY)`` id is the id-anchored case owned by the plain-text lane —
  excluded here. A bare ``§`` tail with no name head (``5 §:ssä``) is an internal
  / other-lane reference — never emitted here (we only fire on a name head).

* **Structural tail reuse.** The ``§`` / momentti / kohta path after the name is
  parsed by the SHARED ``parse_body_provision_tail`` (body mode), so section
  ranges / coordination / momentti precision expand with the same expressiveness
  as the amendment grammar. One mention per expanded provision; one statute-level
  mention when there is no tail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.morphology import (
    MorphCase,
    MorphNumber,
    generate_forms,
    head_entry,
)
from lawvm.finland.references.lemma_gate import GateVerdict, head_case_forms, lemma_gate
from lawvm.finland.references.registries.statute_name import _HEADS_BY_LEN
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    chapter_akn_path,
    parse_body_provision_tail_spanned,
)


@dataclass(frozen=True, slots=True)
class _HeadForm:
    """One inflected statute-head surface and the data to normalize it.

    Attributes:
        oblique:    The inflected head surface, lower case (``laissa``).
        head_lemma: The closed-class head lemma (``laki``).
        nominative: The nominative head surface to reattach (``laki``).
    """

    oblique: str
    head_lemma: str
    nominative: str


def _build_head_forms() -> tuple[_HeadForm, ...]:
    """Derive the closed-class inflected-head trigger set from the M1 engine.

    For every closed statute head, generate its SG case forms and register every
    *oblique* surface (all cases except the nominative) as a trigger. The
    nominative surface is excluded: an uninflected bare head is not a by-name
    citation and would mis-fire on ordinary running text. The result is sorted
    longest-first so the modifier/head split is unambiguous (a token ending in
    ``asetuksessa`` splits on the whole inflected head, never a shorter
    coincidental suffix).
    """
    forms: list[_HeadForm] = []
    for head in _HEADS_BY_LEN:
        entry = head_entry(head)
        nom = ""
        obliques: list[str] = []
        for form in generate_forms(entry, numbers=(MorphNumber.SG,)):
            if form.certainty != "deterministic" or not form.surface:
                continue
            if form.case is MorphCase.NOM:
                nom = form.surface.lower()
                continue
            obliques.append(form.surface.lower())
        if not nom:
            continue
        for obl in obliques:
            forms.append(_HeadForm(oblique=obl, head_lemma=head, nominative=nom))
    # Dedup (distinct heads cannot share an oblique surface, but be safe) and
    # sort longest-first for unambiguous longest-match.
    seen: set[str] = set()
    uniq: list[_HeadForm] = []
    for f in sorted(forms, key=lambda f: len(f.oblique), reverse=True):
        if f.oblique in seen:
            continue
        seen.add(f.oblique)
        uniq.append(f)
    return tuple(uniq)


# Closed trigger set, built once at import time from the M1 engine.
_HEAD_FORMS: tuple[_HeadForm, ...] = _build_head_forms()
_HEAD_FORM_BY_OBLIQUE: dict[str, _HeadForm] = {f.oblique: f for f in _HEAD_FORMS}

# A name-head token: a run of name characters ending in a known oblique head
# surface. The leading part is the (possibly compound / coordinated) modifier;
# the trailing alternation is the closed inflected-head set (longest-first so the
# regex prefers the longest head surface). Bounded quantifier on the modifier
# (§1.11). The character class admits the coordinated-modifier hyphen
# (``maankäyttö-``) but the FULL coordinated phrase (``maankäyttö- ja
# rakennuslain``) is recovered by a separate left-extension scan below.
_NAME_CHAR = r"[A-Za-zÅÄÖåäö0-9-]"
_OBLIQUE_ALT = "|".join(re.escape(f.oblique) for f in _HEAD_FORMS)
_NAME_HEAD_RE = re.compile(
    rf"(?<![A-Za-zÅÄÖåäö0-9-])"  # word start (no preceding name char)
    rf"(?P<modifier>{_NAME_CHAR}{{0,80}}?)"
    rf"(?P<oblique>{_OBLIQUE_ALT})"
    rf"(?![A-Za-zÅÄÖåäö0-9])",  # word end (allow trailing hyphen? no)
    re.IGNORECASE,
)

# An id-anchored parenthetical ``(NNN/YYYY)`` immediately after the name head:
# that is the plain-text lane's case — exclude it here (no double-emission).
_ID_PAREN_RE = re.compile(r"\s{0,5}\(\s{0,3}\d{1,6}/\d{4}\s{0,3}\)")

# A coordinated left modifier fragment that elides its own head:
# ``maankäyttö- ja `` before ``rakennuslain``. We extend the matched modifier
# leftward over ``<word>- ja `` (and ``<word>- sekä ``) groups so the full name
# surface is reported. Bounded.
_COORD_LEFT_RE = re.compile(
    rf"(?:{_NAME_CHAR}{{1,80}}-\s+(?:ja|sekä|tai)\s+)+$",
    re.IGNORECASE,
)
_COORD_LEFT_LOOKBACK = 320

# ---------------------------------------------------------------------------
# ``-kaari`` (code) heads: oikeudenkäymiskaari, maakaari, kauppakaari, …
# ---------------------------------------------------------------------------
#
# The historical Finnish CODES (``kaari``) — Oikeudenkäymiskaari (procedural
# code, 1734/4), Maakaari (land code), Kauppakaari (commercial code),
# Perintökaari (inheritance code) — ARE statutes, named by their inflected title
# exactly like ``-laki`` acts: ``oikeudenkäymiskaaren 12 luvun 32 §:ää``,
# ``maakaaressa säädetään``. But ``kaari`` is NOT a closed statute head in the
# M1 morphology engine (it is not a productive document-type head like ``laki`` /
# ``asetus``), so the bare-head by-name lane above never fires on a ``-kaari``
# title. The consequence is a CORRECTNESS bug: with no by-name match the trailing
# ``32 §`` is captured by the INTERNAL lane as a self-reference to the CITING
# statute (wrong statute, ``exact`` confidence) instead of a cross-statute ref to
# the code.
#
# ``kaari`` declines as a regular Kotus type-26 ``-i`` noun: the oblique stem is
# ``kaare-`` (genitive ``kaaren``, inessive ``kaaressa``, elative ``kaaresta``,
# illative ``kaareen``, adessive ``kaarella``, ablative ``kaarelta``, allative
# ``kaarelle``, translative ``kaareksi``, essive ``kaarena``) plus the partitive
# ``kaarta`` and the comitative/instructive that do not occur in citations. The
# NOMINATIVE ``kaari`` is deliberately NOT a trigger (a bare uninflected head is
# not a by-name citation — same discipline as the ``-laki`` lane).
#
# A ``-kaari`` head requires a non-empty GLUED modifier (a real compound code
# title, ``oikeudenkäymis``-, ``maa``-, …) — a bare inflected ``kaaren`` with no
# modifier is not a resolvable title and is not emitted. Resolution to a concrete
# ``NNN/YYYY`` id is deferred to the registry (``fi-name:<nominative>``); the code
# titles resolve via the SAME statute-name registry the ``-laki`` lane uses
# (single → resolved, multiple → ambiguous, unregistered → STATUTE_ONLY). Never
# an internal leak.
# The closed set of CODE titles whose head is ``kaari`` (nominative key, lower
# case). A bare ``-kaari`` token ending in a code-noun oblique can also be an
# ordinary common noun (``sateenkaari`` rainbow, ``hammaskaari`` dental arch,
# ``jalkakaari`` foot arch) — those are NOT statutes. When the citation carries a
# ``§`` provision tail it is unambiguously a code citation (a rainbow has no
# sections), so the tail is sufficient positive evidence and any modifier is
# accepted. But a BARE ``-kaari`` head with no tail is only emitted when its
# normalized name is one of the KNOWN codes — otherwise the common-noun reading
# wins and nothing is emitted (same positive-evidence discipline as the weak
# ``-laki`` heads). The Finnish codes are a closed historical set; new codes are
# not minted, so this list is stable.
_KNOWN_KAARI_CODES: frozenset[str] = frozenset(
    {
        "oikeudenkäymiskaari",  # 1734/4 procedural code
        "maakaari",  # 1995/540 (and 1734/1) land code
        "kauppakaari",  # 1734/3 commercial code
        "perintökaari",  # 1965/40 inheritance code
        "ulosottokaari",  # 2007/705 enforcement code
        "tietoyhteiskuntakaari",  # 2014/917 information society code
        "naimiskaari",  # archaic marriage code (1734)
        "rakennuskaari",  # archaic building code (1734)
        "rikoskaari",  # archaic penal code (1734)
    }
)
# The exact ``(case, number)`` set the hand-written ``-kaari`` oblique table
# encoded: the singular grammatical + internal-local + external-local cases the
# code titles appear in within citing prose (``maakaaren`` GEN, ``maakaaressa``
# INE, ``maakaaresta`` ELA, ``maakaareen`` ILL, ``maakaarella`` ADE,
# ``maakaarelta`` ABL, ``maakaarelle`` ALL, ``maakaareksi`` TRA, ``maakaarta``
# PART).  Curated (not the full paradigm) and singular-only, reproducing the
# precise oblique set the hand table matched so the M1-backed recognizer is a
# strict superset of it (no precision change).  The NOMINATIVE ``kaari`` is
# deliberately absent (a bare uninflected head is not a by-name citation — same
# discipline as the ``-laki`` lane), as are the plural cases (the codes are cited
# in the singular).
_KAARI_HEAD_CASE_NUMBERS: tuple[tuple[str, str], ...] = (
    ("GEN", "SG"),
    ("INE", "SG"),
    ("ELA", "SG"),
    ("ILL", "SG"),
    ("ADE", "SG"),
    ("ABL", "SG"),
    ("ALL", "SG"),
    ("TRA", "SG"),
    ("PART", "SG"),
)

# Three singular oblique forms the hand table matched that M1's ``reference_v1``
# profile does NOT generate: the essive ``kaarena``, the instructive ``kaarin``,
# and the abessive ``kaartta``.  They are supplied explicitly so the M1-backed
# alternation stays a strict superset of the old hand table (no recall loss) —
# the same documented M1-boundary supplement the inline essive / plural-local
# helpers carry in :mod:`lawvm.finland.references.lemma_gate`.
_KAARI_M1_UNCOVERED_OBLIQUES: tuple[str, ...] = ("kaarena", "kaarin", "kaartta")

# SOUND replacement for the hand-typed ``kaar(?:en|essa|...)`` oblique table:
# M1's generated ``kaari`` surfaces over the curated case set above (paradigm
# inversion, not a suffix guess) plus the three M1-uncovered supplements.
# Longest-first so the head regex prefers the most-specific form.
_KAARI_OBLIQUE_ALT = "|".join(
    sorted(
        {
            *head_case_forms("kaari", _KAARI_HEAD_CASE_NUMBERS),
            *_KAARI_M1_UNCOVERED_OBLIQUES,
        },
        key=lambda s: (-len(s), s),
    )
)
_KAARI_HEAD_RE = re.compile(
    rf"(?<![A-Za-zÅÄÖåäö0-9-])"  # word start (no preceding name char)
    rf"(?P<modifier>[A-Za-zÅÄÖåäö0-9-]{{1,80}}?)"  # non-empty glued modifier
    rf"(?P<oblique>{_KAARI_OBLIQUE_ALT})"
    rf"(?![A-Za-zÅÄÖåäö0-9])",  # word end
    re.IGNORECASE,
)


# The window after the name head in which to look for a ``§`` / momentti tail.
# A citation tail is short; a bounded slice keeps the shared tail parser from
# scanning the rest of the paragraph.
_TAIL_WINDOW = 120

# ---------------------------------------------------------------------------
# Precision gate for weak (common-noun) heads and the ``laki`` elative.
# ---------------------------------------------------------------------------
#
# The bare name-head trigger fires on ANY token ending in an oblique statute-head
# surface. For the STRONG heads (``laki`` / ``asetus`` / ``direktiivi`` on a real
# capitalized/known modifier) this is mostly genuine. But the WEAK heads are
# productive ordinary common nouns whose oblique forms saturate running prose:
# ``vuokrasopimuksen`` (lease agreement), ``lupapäätöksen`` (permit decision),
# ``veroilmoituksen`` (tax return) — not act titles. A corpus diagnostic
# (``tools.resolution_miss_analysis``) attributes ~42% of by-name misses to these
# weak-head false positives plus the ``-alainen``/``-nainen`` adjective family.
#
# So weak heads (and the one ``laki`` form that collides with an adjective) only
# emit a cross-statute mention when there is POSITIVE EVIDENCE it is a real act
# reference:
#   * a following provision tail (``§`` / momentti) — a citation shape; or
#   * a capitalized modifier mid-sentence (a proper-name-ish title).
# Without either signal a weak-head common noun is not emitted (it is not a
# resolvable named act — emitting it is pure garbage, not a fail-loud residue).
_WEAK_HEADS: frozenset[str] = frozenset(
    {"sopimus", "päätös", "ilmoitus", "määräys", "ohje", "säädös"}
)

# The single ``laki`` oblique surface (elative ``laista``) that is orthographically
# identical to the partitive of the highly productive ``-lainen``/``-nainen``
# adjective family (``sellaista``, ``veronalaista`` …). It is gated with the same
# positive-evidence requirement as the weak heads (the morphology gate hard-
# rejects the unambiguous adjective inflections; this only adds caution to the
# residual bare ``laista`` collisions that the gate returns UNKNOWN).
_LAKI_ADJ_COLLISION_OBLIQUE = "laista"

# Statute-NAME homonyms: a (normalized-name, oblique-surface) pair where the FULL
# name surface is orthographically identical to an ordinary common noun's
# inflection, so the bare trigger alone cannot tell the act from the common noun.
# Unlike the ``-lainen`` / ``-las`` collisions (handled by the morphology gate via
# a non-statute paradigm that is STRICTLY LONGER than the bare laki oblique), here
# the surface is exactly ``modifier`` + bare laki oblique — the negative-paradigm
# strictly-longer rule cannot fire, and a blanket negative entry would also drop
# the genuine act reference. So we resolve it the same way weak heads are resolved:
# require POSITIVE EVIDENCE (a ``§`` / momentti tail, or a proper-name-ish
# capitalized modifier mid-sentence) that it is a real act citation. Without that
# evidence the common-noun reading wins and nothing is emitted.
#
#   * ``kauppalaki`` (Sale of Goods Act, 355/1987) vs ``kauppala`` (market town,
#     a municipality type) archaic plural genitive ``kauppalain``. Statute
#     1964/639 coordinates ``maalaiskuntien, kauppalain tai kaupunkien`` — the
#     market-town reading. Every genuine corpus ``kauppalaki`` reference carries a
#     ``§`` tail (``kauppalain 41 §``) or an ``(355/1987)`` id (id-anchored case
#     excluded earlier as the plain-text lane's). Only the genitive ``lain`` form
#     collides (the plural inessive/elative of ``kauppala`` are ``kauppaloissa`` /
#     ``kauppaloista``, never ``kauppalaissa`` / ``kauppalaista``), so the homonym
#     is keyed on the exact ``(name, oblique)`` pair, not the whole head.
_NAME_HOMONYM_OBLIQUES: frozenset[tuple[str, str]] = frozenset(
    {("kauppalaki", "lain")}
)

# The EU-instrument heads (``asetus`` / ``direktiivi``) that, when a name-head
# compound is DIRECTLY GOVERNING an ``N artikla``, are NOT a Finnish statute name
# but an EU-instrument reference owned by the ``eu_directive`` lane. Finnish acts
# are cited by ``§``, NEVER by ``artikla``; so a ``<compound>asetuksen N artikla``
# is unambiguously an EU regulation/directive reference (resolved to CELEX where
# known, or typed STATUTE_ONLY ``eu-nickname:`` when not). The by-name lane must
# therefore decline it — otherwise it mis-types the EU instrument as a
# ``fi-name:`` Finnish statute and double-counts the eu_directive lane's mention.
#
# ``asetus`` is artikla-GATED because Finland ALSO has domestic ``-asetus``
# decrees (a real ``fi-name:`` family) — only an artikla tail proves the EU
# reading. ``direktiivi`` is in a SEPARATE set below: there is NO domestic
# ``-direktiivi`` statute family (a directive is an EU instrument by definition),
# so a ``<compound>direktiivi`` head is ALWAYS an EU-nickname surface owned by
# the ``eu_directive`` lane and the by-name lane must decline it UNCONDITIONALLY
# — with OR without an artikla tail. (Witnessed: ``tietosuojadirektiivin
# mukainen`` in 2018/1054 has no artikla, yet the by-name lane wrongly minted a
# duplicate ``fi-name:tietosuojadirektiivi`` mis-typed as CROSS_STATUTE.)
_EU_NAME_HEADS: frozenset[str] = frozenset({"asetus", "direktiivi"})

# EU-instrument heads with NO domestic statute family: a ``<compound>direktiivi``
# is always an EU directive nickname, never a Finnish ``fi-name:`` statute, so the
# by-name lane declines it unconditionally (the ``eu_directive`` lane owns it).
_EU_ONLY_NAME_HEADS: frozenset[str] = frozenset({"direktiivi"})

# A directly-following article phrase: optional whitespace, a number (with an
# optional letter suffix), then an inflected ``artikla``. Anchored at the slice
# start (the caller passes ``text[head_end:]``). Bounded (§1.11).
_ARTIKLA_AFTER_HEAD_RE = re.compile(
    r"^\s+\d{1,4}(?:\s?[a-z])?\s*artikla", re.IGNORECASE
)

# False-positive families (``-lainen``/``-nainen`` adjectives, the ``jokin``
# pronoun ``joll-`` obliques, the ``-las``/``-läs`` agent-noun plurals, and the
# determiner+``laki`` orthographic collapse) are no longer matched by hand-written
# suffix regexes here. They are rejected by the SHARED, M1-derived morphology gate
# (:func:`lawvm.finland.references.lemma_gate.lemma_gate`), which inverts the M1
# paradigm engine over the closed statute heads PLUS the closed non-statute
# collision paradigms. Paradigm inversion is sound where suffix-substring matching
# had a consonant-gradation bug class (``'asetus' not in 'asetuksen'``); the gate
# also backs the same engine used to generate the positive head triggers, so the
# accept and reject sides can never disagree about what a real head surface is.

# A capitalized-modifier signal: the modifier's first character is an uppercase
# letter. Combined with a mid-sentence check (the match does not begin the text
# nor follow sentence-terminating punctuation), this is the proper-name-ish
# positive evidence for an otherwise-weak head.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?:;]\s*$")
_UPPER_FIRST_RE = re.compile(r"^[A-ZÅÄÖ]")


def _modifier_is_capitalized_midsentence(
    text: str, match_start: int, modifier: str
) -> bool:
    """True when ``modifier`` begins with a capital mid-sentence (proper-name-ish).

    A capitalized modifier that is NOT at the start of the text and NOT directly
    after sentence-terminating punctuation is positive evidence the token is a
    proper act title (``Kuntalain``, ``Hallintolain``) rather than a sentence-
    initial common noun. Sentence-initial capitalization is orthographic, not
    a title signal, so it does not count.
    """
    if not _UPPER_FIRST_RE.match(modifier):
        return False
    preceding = text[:match_start]
    if not preceding.strip():
        return False  # start of text — capitalization is positional, not a title
    if _SENTENCE_BOUNDARY_RE.search(preceding):
        return False  # sentence-initial — same
    return True


def _normalize_name(modifier: str, head_form: _HeadForm) -> str:
    """Build the normalized name key by reattaching the nominative head.

    ``modifier`` is the invariant prefix as matched (original casing), possibly
    already left-extended over a coordinated elided-head conjunct chain
    (``perintö- ja lahjavero``); the nominative head surface is reattached and
    the whole folded to lower case (``luonnonsuojelu`` + ``laki`` ->
    ``luonnonsuojelulaki``; ``perintö- ja lahjavero`` + ``laki`` ->
    ``perintö- ja lahjaverolaki``, the key the registry generates for the
    coordinated compound). When the modifier is empty (a bare inflected head,
    e.g. ``lain``), the nominative head alone is returned (key ``laki``).
    """
    mod = " ".join(modifier.split())
    return (mod + head_form.nominative).lower()


def _extend_coordinated_modifier(text: str, match_start: int, modifier: str) -> str:
    """Reattach an elided-head coordinated left modifier to the name surface.

    Finnish coordinates statute names by eliding the shared head on the first
    conjunct: ``maankäyttö- ja rakennuslaki`` = ``maankäyttölaki`` +
    ``rakennuslaki``. The name-head regex only captures from the last conjunct
    (``rakennus`` + ``lain``); this scans the text immediately to the LEFT of the
    match for a ``<word>- ja `` chain and prepends it so the FULL coordinated
    name is reported AND keyed: the registry generates the coordinated-compound
    surface under the whole name (``perintö- ja lahjaverolaki`` -> 1940/378), so
    the ``fi-name:`` key reattaches the head to the extended modifier, not the
    last conjunct alone. We still synthesize a single key (one mention), not
    per-conjunct ids.
    """
    left_start = max(0, match_start - _COORD_LEFT_LOOKBACK)
    left = text[left_start:match_start]
    m = _COORD_LEFT_RE.search(left)
    if m is None:
        return modifier
    # Preserve the historical full-prefix behavior if the fast window could have
    # cut through a very long coordinated title chain.
    if left_start > 0 and m.start() == 0:
        full = _COORD_LEFT_RE.search(text[:match_start])
        if full is None:
            return modifier
        return full.group(0) + modifier
    return m.group(0) + modifier


# ---------------------------------------------------------------------------
# Descriptive-participle citation form: ``[X:stä] annetun lain N §`` (G2)
# ---------------------------------------------------------------------------
#
# A pervasive Finnish citation form names an act NOT by its compound nickname
# (``työsopimuslain``) but by its OFFICIAL DESCRIPTIVE TITLE rendered as a
# participle phrase: ``Laki valvotusta koevapaudesta`` (act 629/2013) is cited
# ``valvotusta koevapaudesta annetun lain 23 §`` (= "§23 of the law GIVEN
# concerning supervised liberty"). The anchor is the past participle ``annettu``
# ("given/issued") agreeing in case with ``laki``; the descriptive complement
# (in the elative / partitive — ``…sta/…stä``) is the act's subject matter, the
# same phrase that follows ``Laki`` in the official title. The bare-head by-name
# lane above never fires here (the head ``lain`` carries no GLUED modifier — the
# modifier is the separate participle word), so the citation produced ZERO nodes.
#
# This recognizer types the shape and emits a cross-statute mention whose name
# key is reconstructed head-first as ``laki <complement>`` — the SAME surface the
# statute-name registry indexes for the official title (``_add(title)`` over
# ``Laki valvotusta koevapaudesta``), so resolve.py resolves it via the registry
# (or via the in-statute name→id anaphora) with NO change to resolve.py. When the
# title is not in the registry the mention stays STATUTE_ONLY (tag-don't-guess) —
# never a fabricated id.
#
# Scope guard (no lane theft):
#   * An inline ``(NNN/YYYY)`` right after ``lain`` is the plain-text by-id lane's
#     case (its bare-``lain`` arm matches ``lain (629/2013)``) — excluded here.
#   * The ``… annetun lain [N §:n] nojalla`` AUTHORITY-BASIS preamble is owned by
#     the ISSUED_UNDER (delegation/authority) path; a ``nojalla`` (or another
#     genitive-governing postposition) following the citation excludes it here, so
#     the two lanes never double-emit.
#   * The complement must be a genuine ``…sta/…stä`` (elative/partitive) descriptive
#     phrase: a bare ``annetun lain`` with no descriptive complement, or a
#     non-citation ``annetun`` fragment, emits nothing.

# The participle ``annettu`` agreeing in case with ``laki``, and the ``laki`` head
# in the matching case. Both inflect together; we accept the common oblique cases.
_ANNETTU_LAKI_RE = re.compile(
    r"\bannet(?:un|ussa|ulla|usta|uksi|ulle|ulta|tua)\s+"
    r"(?P<head>la(?:in|issa|illa|ista|iksi|ille|ilta|kia))\b",
    re.IGNORECASE,
)

# The descriptive complement preceding ``annetun`` is the NP the past participle
# ``annettu`` (of ``antaa``, "give/issue") governs, in the ELATIVE
# (``…sta/…stä`` = "concerning X"): ``Laki valvotusta koevapaudesta`` → cited
# ``valvotusta koevapaudesta annetun lain``. The complement is extracted by an
# ANCHORED token walk (``_descriptive_complement``), NOT a greedy left-run, so it
# stops at the NP boundary instead of swallowing a preceding clause
# (``… sitä luottolaitostoiminnasta annetun`` → ``luottolaitostoiminnasta``, not
# ``sitä luottolaitostoiminnasta``).
#
# A title word is a lower-case name token (the official title's subject matter is
# common-noun lower case), possibly hyphen/colon-joined.
_DESC_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzäöå")


def _desc_word_ok(word: str) -> bool:
    base, colon, suffix = word.partition(":")
    if colon and not (1 <= len(suffix) <= 20 and all(ch in _DESC_WORD_CHARS for ch in suffix)):
        return False
    if "-" in base:
        left, sep, right = base.partition("-")
        if not sep or "-" in right:
            return False
        return (
            2 <= len(left) <= 40
            and 2 <= len(right) <= 40
            and all(ch in _DESC_WORD_CHARS for ch in left)
            and all(ch in _DESC_WORD_CHARS for ch in right)
        )
    return 2 <= len(base) <= 40 and all(ch in _DESC_WORD_CHARS for ch in base)


# The complement NP's words agree in the case the participle governs OR are its
# inner modifiers: elative (``…sta/…stä``), partitive (``…ta/…tä/…a/…ä``), or the
# coordinative ``ja``/``sekä``/``tai`` joiner BETWEEN two complement words. The
# elative is the head case the participle requires; the partitive covers the
# pre-modifier participle (``valvotusta``) and trailing modifiers. A word that is
# none of these (a determiner ``sitä``/``sen``, a verb, a nominative noun) breaks
# the NP — the walk stops there.
_COMPLEMENT_CASE_SUFFIXES: tuple[str, ...] = (
    "sta",
    "stä",
    "ssa",
    "ssä",
    "ta",
    "tä",
)
_COMPLEMENT_JOINERS: frozenset[str] = frozenset({"ja", "sekä", "tai"})
# Determiner / pronoun surfaces that frequently abut ``X:stä annetun`` but are NOT
# part of the title (``sitä luottolaitostoiminnasta annetun`` — ``sitä`` = "it").
# Listed so the walk never starts the complement on one even if its surface happens
# to end in a complement-case suffix (``sitä`` ends in ``tä``).
_COMPLEMENT_STOPWORDS: frozenset[str] = frozenset(
    {"sitä", "sen", "tätä", "tämän", "tuota", "tuon", "niitä", "niiden", "joita"}
)

# Genitive-governing postpositions that, when they FOLLOW the citation tail, mark
# the ``annetun lain … nojalla`` authority-basis / postposition reading owned by
# the ISSUED_UNDER (delegation) path — excluded here to avoid double-emission.
# Matched anywhere in the SHORT window between the ``annetun lain`` head and the
# §-tail's end (``lain nojalla``, ``lain 6 §:n nojalla``): the postposition sits
# right after the head OR right after the § tail, both inside the bounded window,
# so a single window scan catches both without fragile consumed-length arithmetic.
_TRAILING_POSTPOSITION_RE = re.compile(
    r"\b(?:nojalla|mukaisesti|mukaan|perusteella|estämättä)\b",
    re.IGNORECASE,
)
# The window (chars after the ``annetun lain`` head) within which a trailing
# postposition still binds the citation as an authority basis. Long enough to span
# a short § tail (``6 §:n nojalla``) but not the rest of the paragraph.
_POSTPOSITION_WINDOW = 40

# A date phrase (``14 päivänä heinäkuuta 1898``) can sit BETWEEN the descriptive
# complement and ``annetun`` (``… kielitaidosta 1 päivänä kesäkuuta 1922 annetun
# lain``). It is part of the enactment reference, not the title complement, so it
# is stripped off the left of ``annetun`` before the complement scan.
_MONTH_STEMS = (
    "tammi",
    "helmi",
    "maalis",
    "huhti",
    "touko",
    "kesä",
    "heinä",
    "elo",
    "syys",
    "loka",
    "marras",
    "joulu",
)


@dataclass(frozen=True, slots=True)
class _TailToken:
    text: str
    start: int
    end: int


def _tail_nonspace_tokens(text: str, limit: int) -> list[_TailToken]:
    """Return up to ``limit`` final non-space tokens, right-to-left."""
    tokens: list[_TailToken] = []
    end = len(text.rstrip())
    while end > 0 and len(tokens) < limit:
        start = end
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        tokens.append(_TailToken(text=text[start:end], start=start, end=end))
        end = start
        while end > 0 and text[end - 1].isspace():
            end -= 1
    return tokens


def _date_phrase_start(left: str) -> int | None:
    """Return the start offset of a date phrase at the end of ``left``."""
    toks = _tail_nonspace_tokens(left, 4)
    if len(toks) < 2:
        return None
    year = toks[0].text
    if len(year) != 4 or not year.isdigit():
        return None
    month = toks[1].text.lower()
    if not any(month.startswith(stem + "kuu") for stem in _MONTH_STEMS):
        return None
    month_suffix = month.split("kuu", 1)[1]
    if month_suffix not in {"", "ta", "n", "ssa"}:
        return None
    start_idx = 1
    previous_idx = 2
    if len(toks) > previous_idx:
        marker = toks[previous_idx].text.lower()
        if marker in {"p", "p.", "päivänä", "p:nä", "p:na"}:
            start_idx = previous_idx
            previous_idx += 1
    if len(toks) > previous_idx:
        day = toks[previous_idx].text
        day_norm = day[:-1] if day.endswith(".") else day
        if day_norm.isdigit() and 1 <= len(day_norm) <= 2:
            start_idx = previous_idx
    return toks[start_idx].start


def _complement_word_ok(word: str) -> bool:
    """True when ``word`` can be an inner word of the ``annettu`` complement NP.

    A title word (lower-case name token, not a determiner stopword) ending in a
    case the participle's NP carries (elative / inessive / partitive), or a
    coordinating joiner BETWEEN complement words. Used for the trailing modifiers
    AFTER the elative anchor (``työsuojeluasioissa``) and, in the leftward walk,
    only within an open participle-complement span — NOT as a general left-boundary
    test (that would re-admit prior-clause pollution).
    """
    if word in _COMPLEMENT_JOINERS:
        return True
    if word in _COMPLEMENT_STOPWORDS:
        return False
    if not _desc_word_ok(word):
        return False
    return word.endswith(_COMPLEMENT_CASE_SUFFIXES)


def _is_elative(word: str) -> bool:
    """True when ``word`` is a clean title word in the elative (``-sta/-stä``).

    The elative is the case the participle governs; an elative pre-modifier (incl.
    plural ``-ista``, adjective ``-isesta`` and the elative attributive participle
    forms ``koskevasta``/``säädetyistä``) always agrees with the head, so it is an
    unconditionally safe member of the title NP.
    """
    if word in _COMPLEMENT_STOPWORDS:
        return False
    if not _desc_word_ok(word):
        return False
    return word.endswith(("sta", "stä"))


# Elative attributive participle suffixes: present passive (``koskevasta``,
# ``vaadittavasta``) and past passive (``säädetyistä``, ``noudatettavista``). When
# the elative HEAD is pre-modified by such a participle, the participle's own
# complement to its LEFT is a genuine title member EVEN IF that complement is not
# itself elative (``hakukoneita koskevasta …``, ``henkilöstöltä vaadittavasta …``).
# This licenses the ``[complement] [elative participle] [elative head]`` shape
# without re-admitting the prior-clause pollution a bare case-suffix test let in.
_ELATIVE_ATTR_PARTICIPLE_SUFFIXES: tuple[str, ...] = (
    # present passive (``koskevasta``/``koskevista``, ``vaadittavasta``)
    "vasta",
    "västä",
    "vista",
    "vistä",
    # past passive, singular (``säädetystä``, ``noudatetusta``)
    "tusta",
    "tystä",
    "dusta",
    "dystä",
    # past passive, plural (``säädetyistä``, ``noudatetuista``)
    "tuista",
    "tyistä",
    "duista",
    "dyistä",
)


def _is_elative_attr_participle(word: str) -> bool:
    """True when ``word`` is an elative attributive participle (``koskevasta``)."""
    if not _desc_word_ok(word):
        return False
    return word.endswith(_ELATIVE_ATTR_PARTICIPLE_SUFFIXES)


# Genitive premodifier suffixes. A Finnish statute-title NP stacks genitive
# premodifiers on the (elative) head — ``[sähköisen]gen [viestinnän]gen
# [palveluista]ela`` (``Laki sähköisen viestinnän palveluista``), ``[viranomaisten]gen
# [toiminnan]gen [julkisuudesta]ela``, ``[terveydenhuollon]gen [asiakasmaksuista]ela``,
# ``[yksityishenkilön]gen [velkajärjestelystä]ela``. The elative-only left walk drops
# them, truncating the title to its head and degrading the key to an over-broad
# ``laki <head>``. We re-admit a genitive premodifier that is contiguously
# left-adjacent to an already-admitted title member. Singular genitive is plain
# ``-n``; the plural genitive surfaces are ``-jen/-ien/-den/-ten``. All end in
# ``-n``, so an ``-n`` test — gated by the chain cap, the stopword/shape guards,
# and the verb/clitic ``-n`` exclusion below — is the admission rule.
_GENITIVE_PREMODIFIER_MIN_LEN = 3

# ``-n``-final suffixes that are NOT a genitive: they mark a VERB or a clitic, and
# are the dominant prior-clause polluters seen when a bare ``-n`` test walks left
# past the title (``säädetään``/``käytetään`` = passive present; ``pitämään`` = 3rd
# infinitive illative; ``kuitenkin`` = ``-kin`` clitic). Finnish has no genitive
# that produces these endings, so excluding them removes the verb/adverb
# pollution while keeping every genuine genitive premodifier (singular ``-n`` on a
# vowel stem; plural ``-jen/-ien/-den/-ten``). Ordered longest-first only matters
# for readability; ``str.endswith`` takes the tuple as an OR.
_NON_GENITIVE_N_SUFFIXES: tuple[str, ...] = (
    # passive present (``säädetään``, ``käytetään``, ``noudatetaan``)
    "taan",
    "tään",
    "daan",
    "dään",
    # 3rd-infinitive illative (``pitämään``, ``tekemään``)
    "maan",
    "mään",
    # focus/question clitics (``kuitenkin``, ``eikään``, ``onkin``)
    "kin",
    "kään",
)

# Function words (adverbs / conjunctions) that end in ``-n`` but are NOT genitive
# nouns: they slip the suffix test (no verb/clitic ending) yet are prior-clause
# connective tissue, not title premodifiers (``… siten kuin X:stä annetun lain``,
# ``… sovelletaan vain X:stä annetun``). Listed explicitly because they are a
# closed set and short enough to pass the min-length gate.
_GENITIVE_FUNCTION_WORD_STOPS: frozenset[str] = frozenset(
    {"kuin", "siten", "vain", "näin", "miten", "kuten", "joten"}
)


def _is_genitive_premodifier(word: str) -> bool:
    """True when ``word`` is a clean genitive (``-n``) title premodifier.

    Genitive ``-n`` is ALSO the singular total-object/accusative marker, so a
    preceding clause's verb object (``antaa luvan …`` -> ``luvan``) likewise ends
    ``-n``; that residual ambiguity is bounded by the caller's chain cap. This
    predicate screens SHAPE: a lower-case common-noun token ending in genitive
    ``-n`` (not a determiner stopword, not a verb/clitic ``-n`` form). The
    verb/clitic exclusion is the load-bearing guard — empirically the dominant
    ``-n`` polluters are the passive present (``säädetään``) and 3rd-infinitive
    illative (``pitämään``), not nominal total objects.
    """
    if word in _COMPLEMENT_STOPWORDS or word in _GENITIVE_FUNCTION_WORD_STOPS:
        return False
    if not _desc_word_ok(word):
        return False
    # A 2-letter ``-n`` token (``en``, ``on``) is far more likely a clause word
    # than a title premodifier; require a real noun-length stem.
    if len(word) < _GENITIVE_PREMODIFIER_MIN_LEN:
        return False
    if not word.endswith("n"):
        return False
    # Reject verb / clitic ``-n`` forms — no genitive produces these endings.
    return not word.endswith(_NON_GENITIVE_N_SUFFIXES)


# Max consecutive genitive premodifiers admitted into a title NP
# (``[sähköisen] [viestinnän] palveluista`` = a 2-genitive chain). Capped to bound
# the prior-clause-object risk: genitive ``-n`` is also the singular total-object
# marker, so an unbounded ``-n`` run could walk left into a preceding clause's
# object NP (``antaa luvan …`` -> ``luvan``). A statute title's genitive
# premodifier stack is short in practice — the corpus's observed witness shapes
# top out at a 2-genitive chain — so a cap of 2 recovers every observed title
# while refusing to chain a third ``-n`` token, which is far more likely a
# stranded prior-clause object than a genuine title premodifier.
_GENITIVE_CHAIN_CAP = 2


def _descriptive_complement(left: str) -> tuple[str, int] | None:
    """Extract the ``annettu`` complement NP at the END of ``left``.

    ``left`` is the text immediately before ``annetun`` (date phrase already
    peeled). Returns ``(complement_surface, start_offset_in_left)`` or ``None``.

    The walk anchors on the LAST elative (``…sta/…stä``) word — the head case the
    participle governs — then extends LEFT over genuine title members only,
    stopping at the clause boundary. A left word is admitted iff it is (a) an
    elative title word, (b) the complement (or modifier chain) of an immediately
    following elative attributive participle (``hakukoneita koskevasta …``,
    ``henkilöstöltä vaadittavasta …``), or (c) a coordinator joining two elative
    title members. A determiner/pronoun (``mitä``), a verb (``sovelleta``), a
    prior-clause locative + clause coordinator (``momentissa tai …``) or a phrase
    like ``tässä laissa ja …`` is NOT a title member — the walk stops before it,
    so ``… sitä luottolaitostoiminnasta annetun`` yields just
    ``luottolaitostoiminnasta`` and never swallows the preceding clause.
    """
    # Tokenize the tail of ``left`` into (word, start_offset) pairs.
    toks = [(mm.group(0).lower(), mm.start()) for mm in re.finditer(r"\S+", left)]
    if not toks:
        return None
    words = [w for w, _ in toks]
    # Find the LAST token that is an elative complement head (``…sta/…stä``) and
    # is itself a clean title word. Search from the right within a bounded tail.
    anchor: int | None = None
    for i in range(len(words) - 1, max(-1, len(words) - 9), -1):
        w = words[i]
        if w in _COMPLEMENT_STOPWORDS:
            continue
        if _desc_word_ok(w) and w.endswith(("sta", "stä")):
            anchor = i
            break
    if anchor is None:
        return None
    # Every token from the elative anchor up to the end of ``left`` must be a
    # complement-NP word (the trailing modifiers ``työsuojeluasioissa``); if a
    # non-complement word intervenes between the anchor and ``annetun`` it is not a
    # contiguous title NP — bail (FP guard).
    for j in range(anchor + 1, len(words)):
        if not _complement_word_ok(words[j]):
            return None
    # Extend LEFT over the title NP's pre-modifiers, stopping at the clause boundary.
    # A bare case-suffix test over-captures the preceding clause (a negative verb
    # ``sovelleta``, a locative ``momentissa``, a determiner ``mitä``). Instead a
    # left word is admitted only when it is a genuine title member:
    #   * an elative (``-sta/-stä``) word — agrees with the head, always safe; or
    #   * the complement of an immediately-following elative attributive participle
    #     (``hakukoneita`` before ``koskevasta``; ``henkilöstöltä`` before
    #     ``vaadittavasta``) — the ``[complement] [elative participle] [head]``
    #     title shape, where the complement need not itself be elative; or
    #   * a coordinator (``ja``/``tai``/``sekä``) ONLY when it joins two elative
    #     title members (``julkisista hankinnoista ja käyttöoikeussopimuksista``) —
    #     a coordinator whose left neighbour is NOT an elative title member
    #     coordinates two CLAUSES (``momentissa tai luottolaitostoiminnasta``,
    #     ``tässä laissa ja finanssivalvonnasta``), so the walk stops there.
    start = anchor
    # ``open_complement`` is set once the walk crosses an elative attributive
    # participle: its complement to the LEFT is a (possibly multi-word) NP whose
    # members carry locative/partitive cases rather than the elative
    # (``neuvoa-antavissa kunnallisissa kansanäänestyksissä noudatettavasta …``),
    # so within that span the permissive complement-case test applies.
    open_complement = False
    # Consecutive genitive premodifiers admitted so far (``[sähköisen] [viestinnän]
    # palveluista`` = a 2-chain). Bounded by ``_GENITIVE_CHAIN_CAP`` so a stranded
    # prior-clause total-object ``-n`` is not chained into the title. The run resets
    # when a non-genitive title member (an elative head / participle complement) is
    # crossed, so a long elative-coordinated title is not penalised.
    genitive_run = 0
    while start - 1 >= 0:
        prev = words[start - 1]
        if prev in _COMPLEMENT_JOINERS:
            # Internal title coordination iff the word to the coordinator's LEFT is
            # itself an elative title member; otherwise it is a clause coordinator.
            if start - 2 >= 0 and _is_elative(words[start - 2]):
                start -= 1
                genitive_run = 0
                continue
            break
        if prev in _COMPLEMENT_STOPWORDS:
            break
        if _is_elative_attr_participle(words[start]):
            # Crossing a participle opens its complement span to the left.
            open_complement = True
        if _is_elative(prev):
            start -= 1
            genitive_run = 0
            continue
        # A non-elative word is a title member only inside an open participle
        # complement span (its modifiers / the participle's own complement).
        if open_complement and _complement_word_ok(prev):
            start -= 1
            genitive_run = 0
            continue
        # A genitive (``-n``) premodifier contiguously left-adjacent to an admitted
        # title member is part of the title NP (``[sähköisen viestinnän]
        # palveluista``). Bounded by the chain cap so a prior-clause total-object
        # ``-n`` is not chained into the title. The premodifier terminates the NP on
        # its left at the first non-genitive, non-elative, non-open-complement token.
        if genitive_run < _GENITIVE_CHAIN_CAP and _is_genitive_premodifier(prev):
            start -= 1
            genitive_run += 1
            continue
        break
    # Trim a leading joiner left dangling at the NP start (defensive; the joiner
    # guard above already stops before a clause coordinator).
    while start < anchor and words[start] in _COMPLEMENT_JOINERS:
        start += 1
    start_off = toks[start][1]
    surface = " ".join(words[start:])
    return surface, start_off


def _recognize_descriptive_participle_refs(text: str) -> list[ReferenceMention]:
    """Recognise ``[X:stä] annetun lain N §`` descriptive-participle citations (G2).

    See the module-section comment for the shape and the scope guards. Emits one
    :class:`ReferenceMention` per provision in the optional ``§`` tail, carrying
    the name key ``fi-name:laki <complement>`` (head-first, matching the registry's
    official-title surface). Resolution to a concrete id is deferred to resolve.py
    (registry / anaphora / STATUTE_ONLY) — no id is fabricated here.
    """
    out: list[ReferenceMention] = []
    source_ref = ProvisionRef(statute_id="")

    for m in _ANNETTU_LAKI_RE.finditer(text):
        # Exclude the id-anchored ``annetun lain (NNN/YYYY)`` form: the plain-text
        # by-id lane (its bare-``lain`` arm) owns it — no double-emission.
        if _ID_PAREN_RE.match(text, m.end()):
            continue

        # The descriptive complement sits to the LEFT of ``annetun``. Peel any
        # intervening enactment date phrase first, then require a genuine
        # elative/partitive ``…sta/…stä`` complement. An empty / non-``…sta``
        # left context (a bare ``annetun lain`` or a non-citation fragment) is not
        # a descriptive title — emit nothing (tag-don't-guess, FP guard).
        left = text[: m.start()]
        date_start = _date_phrase_start(left)
        if date_start is not None:
            left = left[:date_start] + " "
        comp = _descriptive_complement(left)
        if comp is None:
            continue
        complement, comp_start = comp

        # Parse the optional structural ``§`` tail (shared body parser).
        tail_text = text[m.end() : m.end() + _TAIL_WINDOW]
        tail_parse = parse_body_provision_tail_spanned(tail_text)
        targets = tail_parse.targets
        consumed_tail = tail_parse.consumed_text

        # Authority-basis exclusion: ``… annetun lain [N §:n] nojalla`` (and the
        # other genitive-governing postpositions) are the ISSUED_UNDER path's. The
        # postposition sits right after ``lain`` (no § tail) or right after the §
        # tail — both inside a short window after the head. Scan that window; if a
        # postposition appears, this is the authority-basis reading — skip (no
        # double-emission). The window is bounded so a postposition far downstream
        # in the prose (unrelated to this citation) is not mistaken for its basis.
        post_window = text[m.end() : m.end() + _POSTPOSITION_WINDOW]
        if _TRAILING_POSTPOSITION_RE.search(post_window):
            continue

        if not targets:
            targets = [BodyProvisionTarget(section_label="")]

        # Reconstruct the official-title surface head-first: ``laki <complement>``.
        normalized = f"laki {complement}".lower()

        # Anchor at the descriptive complement's start (the citation surface
        # begins at the title, not at ``annetun``). The complement offset indexes
        # into ``left`` which, when a date phrase was peeled, is a PREFIX of the
        # original ``text[:m.start()]`` truncated only on the RIGHT, so the start
        # offset is identical in ``text``.
        name_start = comp_start
        name_surface = text[name_start : m.end()].strip()
        name_span = SourceSpan("", name_start, m.end() - name_start)

        for tgt in targets:
            provision_path = (
                chapter_akn_path(tgt.chapter, tgt.section_label)
                if tgt.chapter is not None
                else ""
            )
            target_ref = ProvisionRef(
                statute_id=f"fi-name:{normalized}",
                provision_path=provision_path,
                section_label=tgt.section_label,
                subsection_num=tgt.subsection_num,
                item_label=tgt.item_label,
            )
            if tgt.section_label and consumed_tail:
                surface = (name_surface + " " + consumed_tail).strip()
            else:
                surface = name_surface
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=CiteConfidence.STATUTE_ONLY,
                    phrase_lemma="statute_name_descriptive_participle",
                    source_span=name_span,
                    valid_at_interval=(None, None),
                    edge_subtype=None,
                    surface_text=surface,
                )
            )
    return out


def _recognize_kaari_refs(text: str) -> list[ReferenceMention]:
    """Recognise ``-kaari`` (code) head cross-statute references (gap [2]).

    See the module-section comment for the shape and the rationale (codes ARE
    statutes; ``kaari`` is not an M1 head so the bare-head lane misses it; the
    trailing ``§`` would otherwise leak as a WRONG internal self-reference). Emits
    one :class:`ReferenceMention` per provision in the optional ``§`` tail, carrying
    the chapter (``N luvun M §`` → ``chp_N__sec_M``) via the SAME chapter-carry
    path the ``-laki`` lane uses, and the name key ``fi-name:<modifier>kaari``
    (nominative reattached). Resolution to a concrete id is deferred to the
    registry — never an internal leak, never a fabricated id.
    """
    out: list[ReferenceMention] = []
    source_ref = ProvisionRef(statute_id="")

    for m in _KAARI_HEAD_RE.finditer(text):
        # An id-anchored ``(NNN/YYYY)`` right after the head is the plain-text
        # by-id lane's case — exclude (no double-emission).
        if _ID_PAREN_RE.match(text, m.end()):
            continue

        # Parse the optional structural tail (chapter + section + sub-refs) via the
        # shared body parser, bounded to a short window.
        tail_text = text[m.end() : m.end() + _TAIL_WINDOW]
        tail_parse = parse_body_provision_tail_spanned(tail_text)
        targets = tail_parse.targets
        consumed_tail = tail_parse.consumed_text

        # The normalized name key reattaches the nominative head ``kaari`` to the
        # modifier (``oikeudenkäymis`` + ``kaari`` → ``oikeudenkäymiskaari``),
        # folded to lower case — the SAME surface the statute-name registry indexes
        # for the code title, so resolve.py resolves it with no change.
        normalized = (m.group("modifier") + "kaari").lower()

        # Positive-evidence gate (mirrors the weak-``laki``-head discipline): a
        # ``§`` provision tail makes the code citation unambiguous (a common-noun
        # ``-kaari`` — rainbow / arch — has no sections), so any modifier is
        # accepted. A BARE ``-kaari`` head with no tail is only emitted for a KNOWN
        # code title; otherwise the common-noun reading wins and nothing is emitted
        # (no garbage ``fi-name:sateenkaari``).
        if not targets and normalized not in _KNOWN_KAARI_CODES:
            continue

        name_start = m.start("modifier")
        name_surface = text[name_start : m.end("oblique")]
        name_span = SourceSpan("", name_start, m.end("oblique") - name_start)

        if not targets:
            targets = [BodyProvisionTarget(section_label="")]

        for tgt in targets:
            provision_path = (
                chapter_akn_path(tgt.chapter, tgt.section_label)
                if tgt.chapter is not None
                else ""
            )
            target_ref = ProvisionRef(
                statute_id=f"fi-name:{normalized}",
                provision_path=provision_path,
                section_label=tgt.section_label,
                subsection_num=tgt.subsection_num,
                item_label=tgt.item_label,
            )
            if tgt.section_label and consumed_tail:
                surface = (name_surface + " " + consumed_tail).strip()
            else:
                surface = name_surface
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=CiteConfidence.STATUTE_ONLY,
                    phrase_lemma="statute_name_kaari_head",
                    source_span=name_span,
                    valid_at_interval=(None, None),
                    edge_subtype=None,
                    surface_text=surface,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Name-head NP recognizer for the id-anchored construction (worklist #2).
# ---------------------------------------------------------------------------
#
# The inline-(id) citation construction
# (``legal_surface.sentence_parse.parse_citation_sentence``) keys on the
# ``(NUMBER/YEAR)`` paren and recovers the cited statute id off that anchor. But
# the statute NAME-HEAD to the LEFT of the paren was, until this lane, captured for
# the production mention's surface by a naive single-token left-scan
# (``ref_mention_extractor._NAME_HEAD_BEFORE_PAREN_RE``) — a contiguous letter run
# immediately before the paren. That run STOPS at the first space, so it captures
# only the head word and DROPS any intervening modifier between the name head and
# the paren:
#
#   * ``annettu opetusministeriön asetus (253/2001)`` — the participle frame's head
#     ``asetus`` is NOMINATIVE and is preceded by an intervening genitive
#     (``opetusministeriön``); the single-token scan returns only ``asetus``;
#   * ``valvotusta koevapaudesta annetun lain (629/2013)`` — the descriptive title
#     complement (``valvotusta koevapaudesta``) is dropped, leaving only ``lain``.
#
# This recognizer parses the name-head as a proper (bounded, inflected) NP whose
# RIGHT edge sits immediately before the ``(id)`` paren, REUSING the proven by-name
# recognizers (``_NAME_HEAD_RE`` compound head, ``_KAARI_HEAD_RE`` code head, the
# ``annettu``-participle frame + ``_descriptive_complement`` title NP). It does NOT
# resolve an id (the construction already has it off the anchor) — it returns ONLY
# the NP's START offset so the construction emits a name-head-inclusive surface.
# Tag-don't-guess: when no clean NP is found the lane returns ``None`` and the
# caller keeps its previous single-token surface (a fail-loud non-extension, never
# a fabricated boundary).

# The name→id gap the production by-id anchor tolerates (``\s{0,5}`` in
# ``_PLAIN_TEXT_FI_STATUTE_RE``). Used to confirm the NP's right edge actually abuts
# the paren rather than sitting an arbitrary distance to its left.
_NAME_ID_GAP_MAX = 5

# ``annettu`` participle (the issued-frame anchor) and the bare NOMINATIVE document
# head (``laki`` / ``asetus`` / ``päätös``) it governs. Mirrors the ``annettu`` arm
# of ``_PLAIN_TEXT_FI_STATUTE_RE`` but is split into TWO flat (non-nested) regexes —
# the participle anchor and the trailing head — so the OPTIONAL intervening
# genitive-modifier words between them (``annettu opetusministeriön asetus``) can be
# validated by a bounded Python token walk instead of a nested-quantifier regex
# (the production regex's adjacent ``annet(tu|tua|un)\s+(laki|asetus)`` cannot admit
# an intervening modifier at all). Each pattern is a single flat match — no adjacent
# variable repeats, no nesting (§1.11).
_ANNETTU_PARTICIPLE_RE = re.compile(r"\bannet(?:tu|tua|un)\b", re.IGNORECASE)
_NOMINATIVE_HEAD_TAIL_RE = re.compile(r"(?:laki|asetus|päätös)$", re.IGNORECASE)
# A single intervening genitive-modifier word between the participle and the head: a
# bounded name token ending in genitive ``-n`` (a ministry / agency genitive:
# ``opetusministeriön``, ``valtioneuvoston``). One flat match per word; the bounded
# token walk applies it word-by-word (no repeat nesting).
_GENITIVE_MODIFIER_WORD_RE = re.compile(
    r"^[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö-]{1,40}n$", re.IGNORECASE
)
# Max intervening genitive-modifier words admitted between ``annettu`` and the head.
# Statute issuing-frame modifiers are a short ministry/agency genitive stack in
# practice; bound it so the walk never reaches across an unrelated clause.
_ANNETTU_MODIFIER_CAP = 3


def _annettu_nominative_head_start(left: str) -> int | None:
    """Start offset of an ``annettu [genitive-modifier] (laki|asetus|päätös)`` frame.

    ``left`` is the (rstripped) text whose RIGHT edge is the NP's right edge. Returns
    the offset of the ``annettu`` participle when ``left`` ENDS in the issued-frame
    ``annettu [≤3 genitive-modifier words] <nominative head>`` (``annettu
    opetusministeriön asetus``), else ``None``. Implemented as a bounded token walk
    over two flat regexes so there is no nested-quantifier regex smell (§1.11).
    """
    if _NOMINATIVE_HEAD_TAIL_RE.search(left) is None:
        return None
    toks = [(mm.group(0), mm.start()) for mm in re.finditer(r"\S+", left)]
    if len(toks) < 2:
        return None
    # The last token must be the bare nominative head.
    head_word, _ = toks[-1]
    if _NOMINATIVE_HEAD_TAIL_RE.fullmatch(head_word) is None:
        return None
    # Walk left over up to _ANNETTU_MODIFIER_CAP genitive-modifier words to a
    # directly-preceding ``annettu`` participle.
    i = len(toks) - 2
    modifiers = 0
    while i >= 0:
        word, off = toks[i]
        if _ANNETTU_PARTICIPLE_RE.fullmatch(word) is not None:
            return off
        if modifiers >= _ANNETTU_MODIFIER_CAP:
            return None
        if _GENITIVE_MODIFIER_WORD_RE.fullmatch(word) is None:
            return None
        modifiers += 1
        i -= 1
    return None


def name_head_np_start_before_paren(text: str, paren_start: int) -> int | None:
    """Start offset of the statute-name-head NP ending just before ``paren_start``.

    ``text`` is the segment text; ``paren_start`` is the char offset of the ``(`` of
    a recognized ``(NUMBER/YEAR)`` statute-id anchor. Returns the char offset where
    the name-head NP begins, so the construction's surface can be extended LEFT from
    the paren over the FULL inflected name (head + any intervening modifier), or
    ``None`` when no clean NP abuts the paren (the caller then keeps its prior
    single-token surface — tag-don't-guess, never a fabricated boundary).

    The NP is parsed by REUSING the proven by-name recognizers, tried in order of
    specificity:

      1. the ``annettu [genitive-modifier] (laki|asetus|päätös)`` participle frame
         with a NOMINATIVE head (``annettu opetusministeriön asetus``);
      2. the ``[X:stä] annetun lain`` descriptive-participle title NP (the official
         descriptive title rendered as a participle phrase);
      3. the compound inflected name head (``arvonlisäverolain``) with a real glued
         modifier;
      4. the ``-kaari`` (code) head (``perintökaaren`` / ``Maakaaren``).

    Only an NP whose RIGHT edge abuts the paren (within the ``\\s{0,5}`` name→id gap
    the production by-id anchor tolerates) is accepted.
    """
    if paren_start <= 0 or paren_start > len(text):
        return None
    raw_left = text[:paren_start]
    left = raw_left.rstrip()
    if not left:
        return None
    # The NP's right edge must abut the paren within the tolerated name→id gap.
    if paren_start - len(left) > _NAME_ID_GAP_MAX:
        return None
    left_len = len(left)

    # 1. ``annettu [genitive-modifier] (laki|asetus|päätös)`` nominative frame.
    ann_start = _annettu_nominative_head_start(left)
    if ann_start is not None:
        return ann_start

    # 2. Descriptive-participle title NP: ``[X:stä] annetun lain``. The head must be
    #    an oblique ``lain`` form ending at the NP's right edge; the descriptive
    #    complement to the left of ``annetun`` is parsed by the shared NP walk.
    am = None
    for cand in _ANNETTU_LAKI_RE.finditer(left):
        if cand.end() == left_len:
            am = cand
            break
    if am is not None:
        comp = _descriptive_complement(left[: am.start()])
        if comp is not None:
            _, comp_start = comp
            return comp_start
        # ``annetun lain`` with no recoverable descriptive complement: anchor at the
        # participle so at least the participle frame is captured (not a bare head).
        return am.start()

    # 3. Compound inflected name head with a real glued modifier (``arvonlisäverolain``).
    #    A coordinated elided-head left modifier (``maankäyttö- ja rakennuslain``) is
    #    reattached so the full coordinated name is the NP.
    for cand in _NAME_HEAD_RE.finditer(left):
        if cand.end() != left_len:
            continue
        if not cand.group("modifier"):
            continue
        whole_token = cand.group("modifier") + cand.group("oblique")
        if (
            lemma_gate(whole_token, peeled_modifier=cand.group("modifier")).verdict
            is GateVerdict.REJECT_KNOWN_OTHER
        ):
            continue
        mod_start = cand.start("modifier")
        extended = _extend_coordinated_modifier(left, mod_start, cand.group("modifier"))
        if extended != cand.group("modifier"):
            return mod_start - (len(extended) - len(cand.group("modifier")))
        return mod_start

    # 4. ``-kaari`` (code) head (``perintökaaren`` / ``Maakaaren``).
    for cand in _KAARI_HEAD_RE.finditer(left):
        if cand.end() != left_len:
            continue
        if not cand.group("modifier"):
            continue
        return cand.start("modifier")

    return None


def recognize_by_name_refs(text: str) -> list[ReferenceMention]:
    """Recognise inflected-statute-name cross-references in ``text``."""
    return list(_recognize_by_name_refs_cached(text))


@lru_cache(maxsize=8192)
def _recognize_by_name_refs_cached(text: str) -> tuple[ReferenceMention, ...]:
    """Recognise inflected-statute-name cross-references in ``text``.

    For each inflected statute-name head NOT immediately followed by a
    ``(NNN/YYYY)`` id (that id-anchored case belongs to the plain-text lane),
    emit one :class:`ReferenceMention` per provision in the optional ``§`` tail
    (via :func:`parse_body_provision_tail`), or a single statute-level mention
    when there is no tail.

    Every mention is ``cite_kind=CROSS_STATUTE`` /
    ``cite_confidence=STATUTE_ONLY``: the act is named only by (inflected) title,
    so the concrete ``NNN/YYYY`` id is deferred to a later registry-resolution
    step. The name is carried, never an invented id, as
    ``target_provision_ref.statute_id = "fi-name:<normalized_name>"``.

    ``source_provision_ref`` is an empty placeholder; ``source_span`` is None
    (the integration step re-anchors the surface to a byte span, like the other
    surface-grammar lanes). ``surface_text`` carries the full matched name + tail.

    A bare ``§`` reference with no name head (``5 §:ssä``) is NOT emitted here —
    that is an internal / other-lane reference; this lane only fires on a name
    head.

    A BARE inflected head with no attached compound modifier (``tämän lain``,
    ``valtioneuvoston asetuksessa``) is NOT emitted: it is either an internal
    self-reference (``tämän lain``) or a generic governed instrument, not a
    resolvable named title. This lane requires a genuine compound title — a
    non-empty modifier glued to the head (``luonnonsuojelu`` + ``laissa``).
    """
    out: list[ReferenceMention] = []
    source_ref = ProvisionRef(statute_id="")

    for m in _NAME_HEAD_RE.finditer(text):
        oblique = m.group("oblique").lower()
        head_form = _HEAD_FORM_BY_OBLIQUE.get(oblique)
        if head_form is None:  # pragma: no cover - regex alt mirrors the dict
            continue

        # Require a genuine compound title: a non-empty modifier glued directly
        # to the inflected head. A bare head (``lain``, ``asetuksessa``) is the
        # internal self-reference (``tämän lain``) or a generic governed
        # instrument — not a resolvable named title; do not emit (tag-don't-guess
        # excludes the internal lane). Coordinated elided-head left conjuncts are
        # recovered separately and still attach to THIS head's modifier.
        if not m.group("modifier"):
            continue

        # Exclusion: an id-anchored ``(NNN/YYYY)`` right after the head is the
        # plain-text lane's case. Skip — no double-emission.
        if _ID_PAREN_RE.match(text, m.end()):
            continue

        # Exclusion: a ``…direktiivi`` head is an EU directive nickname with no
        # domestic statute family — the ``eu_directive`` lane owns it
        # UNCONDITIONALLY (with or without an artikla tail). Minting a
        # ``fi-name:`` Finnish statute for it would mis-type the EU instrument as
        # CROSS_STATUTE and double-emit a surface the EU lane already covers.
        if head_form.head_lemma in _EU_ONLY_NAME_HEADS:
            continue

        # Exclusion: a ``…asetus`` head DIRECTLY GOVERNING an ``N artikla`` is an
        # EU regulation reference owned by the ``eu_directive`` lane (Finnish acts
        # use § not artikla). ``asetus`` is artikla-gated (not unconditional) so
        # genuine domestic ``-asetus`` decrees still emit. Declining the EU shape
        # here avoids mis-typing the EU instrument as a ``fi-name:`` Finnish
        # statute AND avoids double-counting the eu_directive lane's EU mention.
        if head_form.head_lemma in _EU_NAME_HEADS and _ARTIKLA_AFTER_HEAD_RE.match(
            text[m.end() :]
        ):
            continue

        # Morphology gate (SHARED, M1-derived): reject the non-statute collision
        # families by paradigm inversion, not suffix-substring matching. The gate
        # rejects a token only when a closed NON-statute paradigm explains it at
        # least as completely as the ``laki`` oblique that fired the trigger:
        # the ``-lainen``/``-nainen`` adjective partitive (``veronalaista``), the
        # ``jokin`` pronoun ``joll-`` obliques (``jollain``), the ``-las`` agent-
        # noun plurals (``oppilaille``), and the determiner+``laki`` collapse
        # (``tämänlain``, detected from the peeled modifier). These are non-
        # references, not fail-loud residue — they must not be emitted. A genuine
        # compound (``luonnonsuojelulaissa``) or a real ``-lai-`` law
        # (``työeläkelailla``) returns UNKNOWN and proceeds as before.
        whole_token = m.group("modifier") + m.group("oblique")
        if (
            lemma_gate(whole_token, peeled_modifier=m.group("modifier")).verdict
            is GateVerdict.REJECT_KNOWN_OTHER
        ):
            continue

        # Parse the optional structural tail (everything after the head) through
        # the shared body-mode section/sub-ref recognizers, bounded to a short
        # window so it does not scan the rest of the paragraph.
        tail_text = text[m.end() : m.end() + _TAIL_WINDOW]
        tail_parse = parse_body_provision_tail_spanned(tail_text)
        targets = tail_parse.targets
        # Only the bytes the grammar actually consumed (``5 a §:ssä``), not the
        # whole fixed window — otherwise the reported surface runs on into the
        # following prose.
        consumed_tail = tail_parse.consumed_text
        has_provision_tail = bool(targets)

        modifier = _extend_coordinated_modifier(text, m.start("modifier"), m.group("modifier"))
        # The normalized key uses the FULL (possibly coordinated) modifier, not
        # just the last conjunct: the registry generates coordinated-compound
        # surfaces (``perintö- ja lahjaverolain`` -> 1940/378) under the full
        # name, so keying on the truncated last conjunct (``lahjaverolaki``)
        # would miss the registered act.
        normalized = _normalize_name(modifier, head_form)

        # Precision gate for WEAK (common-noun) heads, the ``laki`` elative that
        # collides with the adjective partitive, and statute-NAME homonyms whose
        # full surface equals an ordinary common noun's inflection
        # (``kauppalain`` = ``kauppalaki`` gen.sg vs ``kauppala`` archaic pl.gen).
        # These trigger on ordinary common nouns, so require POSITIVE EVIDENCE
        # that the token is a real act reference: either a following provision
        # tail (a citation shape) or a capitalized modifier mid-sentence (a
        # proper-name-ish title). Strong heads (``laki`` in its other forms,
        # ``asetus``, ``direktiivi``) keep the looser behavior — the false
        # positives concentrate in the weak heads and these named homonyms.
        needs_evidence = (
            head_form.head_lemma in _WEAK_HEADS
            or oblique == _LAKI_ADJ_COLLISION_OBLIQUE
            or (normalized, oblique) in _NAME_HOMONYM_OBLIQUES
        )
        if needs_evidence and not has_provision_tail:
            if not _modifier_is_capitalized_midsentence(
                text, m.start("modifier"), m.group("modifier")
            ):
                continue

        if not targets:
            # No parsable § tail — a statute-level by-name reference.
            targets = [BodyProvisionTarget(section_label="")]

        # The reported surface spans the name head and (when present) its tail.
        name_surface = modifier + m.group("oblique")

        # Anchor the mention to where the name reference occurs in ``text``. The
        # offset is the regex match start (a character offset into ``text``) — the
        # same convention the defined-term binder uses for its own
        # ``SourceSpan.byte_offset`` (see references/defined_terms.py), so a use
        # site and a binding site recognized over the SAME text are directly
        # comparable for the binder's "binding precedes use" ordering check. Was
        # previously dropped to None, which left local-alias resolution inert.
        name_start = m.start("modifier")
        name_span = SourceSpan("", name_start, m.end("oblique") - name_start)

        for tgt in targets:
            # A chapter-qualified by-name tail (``rikoslain 47 luvun 4 §``,
            # ``osakeyhtiölain 20 luvun 4 §``) carries the chapter onto the AKN
            # ``provision_path`` via the SAME ``chp_N__sec_M`` helper the
            # parenthetical / plain-text lane uses (sections.chapter_akn_path) and
            # the internal lane mirrors — never dropped. Without it, ``rikoslain
            # 47 luvun 4 §`` and ``rikoslain 4 §`` collapse onto the same target
            # (§4 exists in EVERY rikoslaki chapter), pointing the chapter-47 cite
            # at chapter 1. A chapter-only tail (``5 luvussa``) yields ``chp_5``.
            provision_path = (
                chapter_akn_path(tgt.chapter, tgt.section_label)
                if tgt.chapter is not None
                else ""
            )
            target_ref = ProvisionRef(
                statute_id=f"fi-name:{normalized}",
                provision_path=provision_path,
                section_label=tgt.section_label,
                subsection_num=tgt.subsection_num,
                item_label=tgt.item_label,
            )
            # Surface = name + the consumed tail slice (for overlay display).
            # For the statute-level fallback it is just the name.
            if tgt.section_label and consumed_tail:
                surface = (name_surface + " " + consumed_tail).strip()
            else:
                surface = name_surface
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=CiteConfidence.STATUTE_ONLY,
                    phrase_lemma="statute_name_head",
                    source_span=name_span,
                    valid_at_interval=(None, None),
                    edge_subtype=None,
                    surface_text=surface,
                )
            )

    # Descriptive-participle citations (``[X:stä] annetun lain N §``) name the act
    # by its official descriptive title, not a glued compound nickname, so the
    # name-head lane above never fires on them (the ``lain`` head carries no glued
    # modifier). Recognize them separately and merge their mentions in.
    out.extend(_recognize_descriptive_participle_refs(text))
    # ``-kaari`` (code) heads (``oikeudenkäymiskaaren 12 luvun 32 §``) are not M1
    # statute heads, so the name-head lane above never fires on them; recognize
    # them separately (codes ARE statutes) and merge their cross-statute mentions
    # in — never an internal leak.
    out.extend(_recognize_kaari_refs(text))
    return tuple(out)


__all__ = ["recognize_by_name_refs", "name_head_np_start_before_paren"]
