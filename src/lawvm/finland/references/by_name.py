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
from lawvm.finland.references.lemma_gate import GateVerdict, lemma_gate
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
    left = text[:match_start]
    m = _COORD_LEFT_RE.search(left)
    if m is None:
        return modifier
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
_DESC_WORD_RE = re.compile(r"^[a-zäöå]{2,40}(?:-[a-zäöå]{2,40})?(?::[a-zäöå]{1,20})?$")
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
_DATE_PHRASE_RE = re.compile(
    r"(?:\d{1,2}\.?\s*)?(?:p(?:äivänä|\.|:nä)?\s+)?"
    r"(?:tammi|helmi|maalis|huhti|touko|kesä|heinä|elo|syys|loka|marras|joulu)kuu"
    r"(?:ta|n|ssa)?\s+\d{4}\s+$",
    re.IGNORECASE,
)


def _complement_word_ok(word: str) -> bool:
    """True when ``word`` can be an inner word of the ``annettu`` complement NP.

    A title word (lower-case name token, not a determiner stopword) ending in a
    case the participle's NP carries (elative / inessive / partitive), or a
    coordinating joiner BETWEEN complement words. A word failing this breaks the
    NP — the leftward walk stops before it.
    """
    if word in _COMPLEMENT_JOINERS:
        return True
    if word in _COMPLEMENT_STOPWORDS:
        return False
    if not _DESC_WORD_RE.match(word):
        return False
    return word.endswith(_COMPLEMENT_CASE_SUFFIXES)


def _descriptive_complement(left: str) -> tuple[str, int] | None:
    """Extract the ``annettu`` complement NP at the END of ``left``.

    ``left`` is the text immediately before ``annetun`` (date phrase already
    peeled). Returns ``(complement_surface, start_offset_in_left)`` or ``None``.

    The walk anchors on the LAST elative (``…sta/…stä``) word — the head case the
    participle governs — then extends LEFT over contiguous complement-NP words
    (case-agreeing modifiers / joiners), stopping at the NP boundary (a
    determiner, verb, or nominative/genitive word that is not complement-cased).
    A trailing joiner is trimmed. Anchoring on the elative (rather than a greedy
    left run) is what keeps ``… sitä luottolaitostoiminnasta annetun`` → just
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
        if _DESC_WORD_RE.match(w) and w.endswith(("sta", "stä")):
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
    # Extend LEFT over contiguous complement-NP words (case-agreeing pre-modifiers
    # like the partitive participle ``valvotusta``), stopping at the NP boundary.
    start = anchor
    while start - 1 >= 0 and _complement_word_ok(words[start - 1]):
        start -= 1
    # Trim a leading joiner (``ja luottolaitostoiminnasta`` → drop ``ja``).
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
        date_m = _DATE_PHRASE_RE.search(left)
        if date_m is not None:
            left = left[: date_m.start()] + " "
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


def recognize_by_name_refs(text: str) -> list[ReferenceMention]:
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
    return out


__all__ = ["recognize_by_name_refs"]
