"""Delegation / authority construction parse — the asetuksenantovaltuus island.

The sixth net-new construction-grammar island after the citation pilot
(:mod:`lawvm.finland.legal_surface.sentence_parse`), the definition-entry pilot
(:mod:`lawvm.finland.legal_surface.definition_parse`), the temporal island
(:mod:`lawvm.finland.legal_surface.temporal_parse`), the modal / deontic-core
island (:mod:`lawvm.finland.legal_surface.modal_parse`), and the
conditions/exceptions island
(:mod:`lawvm.finland.legal_surface.condition_exception_parse`): the
**delegation / authority family** (asetuksenantovaltuudet). A delegation
construction is the formulaic Finnish statutory clause that grants the power to
issue a LOWER instrument (asetus / määräys / päätös) — fixing WHO may issue WHAT,
optionally UNDER WHICH provision basis (the ``… nojalla`` / ``… mukaan`` tail):

  * **forward grant** — ``Valtioneuvoston asetuksella säädetään tarkemmin …`` /
    ``Tarkempia säännöksiä … annetaan ministeriön asetuksella`` /
    ``Viranomainen voi antaa tarkempia määräyksiä …`` /
    ``Asetuksella säädetään …`` (bare, no overt issuer);
  * **authority basis** — ``[lain nimi] (NUM/YEAR) N §:n nojalla säädetään …``:
    the decree's preamble naming the provision basis under which it is issued.

A delegation core fixes four typed surface spans:

  * the delegation **CUE** — a closed-list power surface (the verb + instrument
    case, e.g. ``asetuksella säädetään`` / ``annetaan … asetuksella`` /
    ``voi antaa … määräyksiä``), matched by token-walk over a closed list, NOT a
    new structural regex per shape;
  * the **AUTHORITY HOLDER** — the actor span (``valtioneuvosto`` / a
    ``…ministeriö`` / a ``…virasto/…laitos/…keskus`` agency / a bare-asetus
    issuer is UNDERSPECIFIED, never absent);
  * the **INSTRUMENT KIND** — the lower instrument the power issues
    (``asetus`` / ``määräys`` / ``päätös``), a closed list;
  * the **PROVISION BASIS** — the ``… nojalla`` / ``… mukaan`` provision-tail
    span (the cited authorizing provision), recognized by REUSING the references
    sub-ref recognizer (:func:`parse_body_provision_tail`), or ``None`` when the
    grant carries no overt basis (the forward-grant common case).

Position in the stack
=====================
Same discipline as the five prior islands, one family over: a sentence-frame
construction with TOTAL TOKEN OWNERSHIP (every char is a typed construction span
— the cue, the holder, the instrument, the basis, or an EXPLICIT residual; the
invariant is "no silent drop", NOT "no residue"). It is purely ADDITIVE and
surface-only — it makes NO legal conclusion (no "this is a valid delegation", no
asserted graph edge), authorizes NO replay (``surface_only`` /
``replay_authorized=False``), and is NOT wired into the production delegation
extractor. The construction is a CANDIDATE projection, never asserted.

The CENSUS (:mod:`delegation_census`) compares this projection against the
PRODUCTION delegation extractor (``delegation.extract_delegations`` forward +
``delegation.extract_asetus_authority`` authority-basis), keyed on the
delegation IDENTITY the production extractor carries (``delegation_type`` for the
forward grant; ``parent_id:section`` for the authority basis). The parse REUSES
the production power markers (``modal_parse``'s ``KIND_POWER`` cues
``säädetään`` / ``annetaan`` / ``antaa`` / ``määrätään``), the existing low-
fidelity delegation fragment (``temporal_parse._CUE_DELEGATION``), and the
references provision-tail recognizer for the basis.

WEAK ORACLE CAVEAT
==================
The production extractor is a brittle 9-positive + 7-negative-regex module with
lazy-gap ``{0,150}?`` windows and a ``… ja … nojalla`` conjunct-distribution
quirk; it MISSES delegation grants whose issuer/verb ordering, qualifier width,
or instrument case its fixed windows do not cover. The construction parse, by
contrast, recognizes the grant from the CUE alone (holder underspecified when no
registered actor binds), so it will SUPERSET the oracle on genuine grants the
regex windows miss. Those supersets are reported NEUTRALLY as
construction-recall-candidates, NOT "production bugs" — and some may be
construction overreach (a non-delegating ``asetuksella`` reference). The real
recall gate is total-token-ownership (no silent drop) plus raw-XML adjudication
of the superset/miss frontier, not ``miss == 0`` against this weak oracle.

:func:`assert_total_ownership` is the checkable postcondition (the union of the
core cue spans, holder spans, instrument spans, basis spans, and residual spans
partitions the sentence char range exactly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Reuse the references provision-tail recognizer for the ``nojalla`` basis: do
# NOT re-implement section/momentti recognition. A blank import-time failure
# would be a silent fallback, so the import is unguarded (fail loud).
from lawvm.finland.references.sections import parse_body_provision_tail_spanned

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors the prior islands.
# ---------------------------------------------------------------------------
#: The delegation-construction grammar owned the frame (in-scope, no-silent-drop).
DELEGATION_LANE_CONSTRUCTION_OWNED = "delegation_construction_owned"
#: The frame declined: the span carried a delegation cue the family discriminator
#: keyed on, but NO recognizable delegation core parsed. Handed back as typed
#: residue, never a guessed parse.
DELEGATION_LANE_DECLINED = "delegation_construction_declined"

# ---------------------------------------------------------------------------
# Closed-list delegation KINDS — the SURFACE classification of the issuing
# authority, mirroring the production ``delegation_type`` vocabulary so the
# census key is directly comparable to the production oracle.
# ---------------------------------------------------------------------------
KIND_VN_ASETUS = "VN_ASETUS"      # valtioneuvoston asetus (Government decree)
KIND_MIN_ASETUS = "MIN_ASETUS"    # ministeriön asetus (Ministerial decree)
KIND_PRES_ASETUS = "PRES_ASETUS"  # tasavallan presidentin asetus
KIND_AGENCY = "AGENCY"            # viranomaisen määräys (agency regulation)
KIND_ASETUS = "ASETUS"            # generic asetus, unclassified issuer

#: Closed instrument-kind surfaces (the lower instrument the power issues).
INSTRUMENT_ASETUS = "asetus"
INSTRUMENT_MAARAYS = "määräys"
INSTRUMENT_PAATOS = "päätös"

# ---------------------------------------------------------------------------
# Power cues — REUSE the production power-verb closed list. ``modal_parse``'s
# ``KIND_POWER`` markers (``säädetään`` / ``annetaan`` / ``antaa`` /
# ``määrätään``) are the deontic power verbs; here they are the delegation cue
# heads. We spell the full delegation cue (verb + instrument case, in either
# order) as a closed alternation so the cue SPAN is owned for total ownership.
# ---------------------------------------------------------------------------

#: Power verbs that head a delegation grant (the ``KIND_POWER`` set, plus the
#: drafting variants the production extractor's verb alternation carries:
#: ``säätää`` / ``vahvistaa`` / ``vahvistetaan`` / ``määrätä`` / ``määritellään``).
_POWER_VERBS: tuple[str, ...] = (
    "säädetään",
    "säätää",
    "annetaan",
    "antaa",
    "määrätään",
    "määrätä",
    "vahvistetaan",
    "vahvistaa",
    "määritellään",
)

# ---------------------------------------------------------------------------
# Two-anchor clause-level cue model (grammar form, gap-tolerant).
# ---------------------------------------------------------------------------
# A delegation grant is recognized from the CO-OCCURRENCE of two CLOSED-LIST
# anchor tokens within ONE clause, NOT from an adjacency regex with a fixed gap
# window. This is the construction-grammar form: the cue is a discontinuous
# constituent (an instrument anchor + a power-verb anchor) whose two token spans
# the cue OWNS, with the intervening prose carried as benign residual. It handles
# a WIDE modifier gap between the issuer/instrument and the verb — the exact shape
# the production extractor's bounded ``{0,150}?`` / ``{0,2}``-word windows MISS —
# by construction, because the anchors are matched independently and only their
# clause co-occurrence is required.
#
# Two grant shapes:
#   * ASETUS shape — an INSTRUMENT anchor (``asetuksella`` instrumental, or an
#     ``asetus``-issuer nominative ``… antaa asetuksella``) co-occurring with a
#     POWER-VERB anchor in the clause. Either order, any gap.
#   * AGENCY shape — an ``antaa``/``voi antaa`` power head co-occurring with a
#     ``määräyksiä`` / ``ohjeita`` OBJECT anchor (the lower instrument is the
#     object, not the instrumental case).

#: Power-verb anchor (a single closed-list verb token). ``\b`` enforces whole-
#: token matching (AGENTS.md whole-token discipline at the char-regex level).
_VERB_ANCHOR_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in _POWER_VERBS) + r")\b",
    re.IGNORECASE,
)
#: Instrument anchor for the ASETUS shape: the instrumental ``asetuksella`` (by
#: decree). A single closed token.
_INSTRUMENT_ASETUKSELLA_RE = re.compile(r"\basetuksella\b", re.IGNORECASE)
#: SELF-/CROSS-REFERENCE GUARD. A demonstrative determiner immediately preceding
#: ``asetuksella`` (``Tällä asetuksella säädetään …`` / ``vahvistaa tällä
#: asetuksella …``) names the ENACTING decree's OWN instrument — the decree
#: exercising its own power — NOT a delegation that GRANTS the power to issue a
#: lower instrument. It is therefore out of the delegation family: the
#: ``asetuksella`` is a self-reference to the document itself, not a delegated
#: target. This mirrors the sibling H5 recognizer's ``_DEMONSTRATIVES`` guard
#: (:mod:`lawvm.finland.references.delegation`), which already excludes the
#: identical shape. The demonstrative set is the genitive/adessive surfaces that
#: bind ``asetuksella`` (``tällä``/``tämän``/``sillä``/``sen`` + the colloquial
#: ``tässä``/``tästä`` that appear in negated ``ei tällä asetuksella säädetä``
#: forms). Matched as a whole token directly before the anchor (only whitespace
#: between), so a coincidental earlier demonstrative does not suppress a genuine
#: ``[issuer] asetuksella`` grant later in the clause.
_DEMONSTRATIVE_BEFORE_ASETUS_RE = re.compile(
    r"\b(?:tällä|tämän|tässä|tästä|tällaisella|sillä|sen|tuolla|tuon)\s+\Z",
    re.IGNORECASE,
)
#: Object anchor for the AGENCY shape: ``määräyksiä`` / ``ohjeita`` (regulations /
#: guidance), the lower instrument as the verb's object.
_OBJECT_MAARAYS_RE = re.compile(r"\b(?:määräyksiä|ohjeita)\b", re.IGNORECASE)
#: The ``antaa``/``voi antaa`` head that governs the AGENCY object anchor.
_VERB_ANTAA_RE = re.compile(r"\bantaa\b", re.IGNORECASE)

#: Authority-holder surfaces (the actor span). Closed institutional heads:
#: valtioneuvosto, tasavallan presidentti, a ``…ministeriö(n)`` (compound names
#: like ``sosiaali- ja terveysministeriö``), and the agency family
#: (``…virasto/…keskus/…laitos/…hallinto/…valvonta/…lautakunta/…neuvosto``). The
#: compound-name prefix is a SINGLE bounded LAZY char-class run anchored by the
#: literal head word (``ministeriö`` / ``…virasto`` …), NOT a nested word-repeat
#: quantifier — so there is no overlapping-repeat backtracking risk
#: (AGENTS.md regex discipline / the regex perf gate).
_HOLDER_RE = re.compile(
    r"(?:valtioneuvosto(?:n)?"
    r"|tasavallan\s+presidentin?"
    r"|[\w][\w\s-]{0,40}?ministeriö(?:n|ssä)?"
    r"|[\w][\w-]{0,30}?(?:virasto|keskus|laitos|hallinto|valvonta|lautakunta"
    r"|neuvosto|komissio|hallitus)(?:n|sta)?)",
    re.IGNORECASE,
)

#: Clause boundaries WITHIN a sentence (``.`` / ``;`` / newline). NOTE: ``:`` is
#: deliberately NOT a boundary — the Finnish section surface ``8 §:n`` carries an
#: internal colon. Co-occurrence is required within ONE clause so two unrelated
#: clauses ("Asetus on annettu. Säädetään laissa.") do not spuriously pair an
#: instrument in one with a verb in the other.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.;\n]")

#: The ``nojalla`` / ``mukaan`` provision-basis tail: a ``(NUM/YEAR)`` id and/or a
#: ``N §:n`` provision path, terminated by ``nojalla`` (under) or ``mukaan``
#: (per). We capture the WINDOW from a left boundary up to the terminal so the
#: references recognizer can parse the section path inside it.
_BASIS_TERMINAL_RE = re.compile(r"\b(nojalla|mukaan)\b", re.IGNORECASE)
#: A basis window must carry at least one provision-id signal (a ``(NUM/YEAR)`` id
#: or a ``N §`` section) — a bare ``mukaan`` ("accordingly") with no provision is
#: not an authority basis.
# Two disjoint alternatives, each backtracking-safe: a ``(NUM/YEAR)`` id, or a
# section number (with an optional single letter suffix) immediately before ``§``.
# The section arm uses ``\b`` and a single optional letter (no adjacent variable
# repeats) to satisfy the regex perf gate.
_BASIS_ID_SIGNAL_RE = re.compile(
    r"\(\d{1,5}\s*/\s*\d{2,4}\)|\b\d{1,4}[a-z]?\s?§", re.IGNORECASE
)
#: ADJACENCY GUARD. A genuine authority basis is ``… (NUM/YEAR) N §:n [M momentin]
#: nojalla`` — the provision PATH (a ``§`` section / momentti tail, or a bare
#: ``(NUM/YEAR)`` id) directly precedes the terminal, with ONLY provision-tail
#: vocabulary in between (``§``, ``:n``, letter suffixes, section/momentti/kohta
#: numbers + their keywords, coordinators ``ja`` / ``sekä`` / ``tai``, whitespace,
#: punctuation) — NOT arbitrary prose. This rejects the anaphoric ``… tai sen
#: nojalla`` / ``tämän lain nojalla`` (no own id) AND the long-range FALSE basis
#: where an UNRELATED earlier ``(NUM/YEAR) §`` ref (``tämän lain mukaista …
#: varhaiskasvatuslain (540/2018) 1 §:n 2 momentin … kohdassa tarkoitetussa
#: päiväkodissa …``) sits far to the left of a bare ``sen nojalla``: the span
#: between that id's path and the terminal is prose (``tarkoitetussa
#: päiväkodissa``), which is not provision-tail vocabulary, so the guard fails.
#:
#: The tail is a REPEATED group of {a provision-tail token | whitespace/punct},
#: each token a bounded literal or digit run — the only ``\w`` runs allowed are the
#: closed keyword list, so a generic prose word breaks the match. The group repeat
#: is over disjoint, non-overlapping alternatives (no catastrophic backtracking).
_BASIS_TAIL_TOKEN = (
    r"(?:§|:n|momentin|momentti|momentissa|kohdan|kohta|kohdassa"
    r"|mukaisen|ja|sekä|tai|\d{1,4}|[a-zäö](?![\wäö])"
    r"|[\s.,:()/–-]++)"
)
# The tail is a single non-overlapping alternation repeated (each branch consumes
# at least one char; the whitespace/punct branch and the literal branches start
# with disjoint character sets), then anchored to the window end. The whitespace/
# punctuation branch is POSSESSIVE (``++``): a long whitespace run (e.g. the
# equal-length spaces left by :func:`_strip_amendment_interjections` blanking a
# ``sellaisena kuin`` interjection) is consumed in one bite with NO backtracking,
# so a FAILING match over a blanked window followed by non-tail prose (``… §:n
# <blanked spaces> sekä <act-name prose> (NNN/YYYY) …``) cannot trigger the
# catastrophic re-exploration of the outer repeat. Each whitespace char belongs to
# exactly one tail token regardless, so making the run possessive changes no valid
# match — it only removes the exponential failure path.
_BASIS_PATH_BEFORE_TERMINAL_RE = re.compile(
    r"(?:\(\d{1,5}\s*/\s*\d{2,4}\)|\b\d{1,4}\s*[a-zäö]?\s*§)"
    + _BASIS_TAIL_TOKEN
    + r"*\Z",
    re.IGNORECASE,
)
#: AMENDMENT-VERSION INTERJECTION. A Finnish authority basis routinely carries an
#: amendment-history aside about the basis provision BETWEEN its provision path and
#: the ``nojalla`` terminal: ``… N §:n, sellaisena kuin se on [muutettuna …]
#: [laissa/laeissa/asetuksessa …] (NNN/YYYY)[ muutoksineen], nojalla``. This aside is
#: parenthetical metadata — it records WHICH amending act gave the basis provision
#: its current form; the ``(NNN/YYYY)`` id(s) INSIDE it are the AMENDING act(s), NOT
#: the authority basis. The basis is the OUTER ``[act] (NUM/YEAR) N §:n … nojalla``.
#: Two consequences: (1) the prose between the path and ``nojalla`` defeats the
#: adjacency guard (the construction correctly refuses to read across arbitrary
#: prose); (2) if naively scanned, the inner amending id would be mis-bound as the
#: basis. We REMOVE every such interjection from a basis window BEFORE the adjacency
#: guard and conjunct-id extraction run — so the guard sees the clean
#: path-before-terminal and the amending ids are never extracted as bases.
#:
#: The interjection runs from a comma + ``sellaise… kuin (se|ne) (on|ovat|oli|olivat)``
#: through the amending-act reference it carries, and STOPS there: ``[^(,]*`` skips
#: the connecting prose (``muutettuna …`` / ``annetussa asetuksessa`` / ``laissa``)
#: up to EITHER a parenthesized id group ``(NNN/YYYY[ sekä … ja …])`` (one paren may
#: list several amending ids — they are all consumed as one group) OR a bare
#: ``NNN/YYYY`` id (the parenless ``laissa 348/1994`` form). It does NOT consume past
#: that amending id — crucially it stops BEFORE any following ``sekä``/``ja``
#: coordinated NEW basis (``… (365/92) sekä …lain (364/92) 1 §:n nojalla``: the
#: ``364/92 §`` is a separate basis, not part of the interjection, so it survives).
#: Bounded, perf-gate clean: ``[^(,]*`` is a single run over a class disjoint from
#: its neighbours; the two stop alternatives ``\([^)]*\)`` / ``\d…/\d…`` follow it
#: without sharing a variable prefix (no overlapping-start adjacent repeats, no
#: nested quantifiers). A coordinated basis carries one interjection PER conjunct;
#: ``re.sub`` removes them all. The basis is the OUTER ``[act] (NUM/YEAR) N §:n …
#: nojalla``; the amending ``(NNN/YYYY)`` inside the interjection is never bound.
_INTERJECTION_RE = re.compile(
    r",\s*sellaise\w*\s+kuin\s+(?:se|ne)\s+(?:on|ovat|oli|olivat)\b"
    r"[^(,]*(?:\([^)]*\)|\d{1,5}/\d{2,4})",
    re.IGNORECASE,
)


def _strip_amendment_interjections(window: str) -> str:
    """Blank every ``, sellaisena kuin … ,`` amendment-version interjection.

    Replaces each interjection with an equal-length run of spaces, so downstream
    char offsets within ``window`` are PRESERVED (the conjunct id offsets stay
    aligned with the original window) while the amending-act ids the interjection
    carries are removed from view — they are NOT the authority basis, only metadata
    about which act amended the basis provision. The OUTER basis path remains.

    A cheap ``"sellaise"`` substring prefilter skips the ``re.sub`` scan entirely
    for the overwhelming majority of basis windows (which carry no interjection),
    so the strip adds no measurable cost to the common no-interjection path.
    """
    if "sellaise" not in window:
        return window
    return _INTERJECTION_RE.sub(lambda m: " " * (m.end() - m.start()), window)


#: One authority-basis conjunct id ``(NUM/YEAR)``. A single bounded id token, no
#: nested optional repeats (AGENTS.md §1.11 / regex perf gate). The inflected
#: act-name word that precedes the id (the drafting-kind signal) is read SEPARATELY
#: from the chars before the id (see :func:`_name_word_before`), not as an optional
#: leading group, so this pattern stays a simple bounded literal.
_AUTHORITY_ID_RE = re.compile(
    r"\((?P<num>\d{1,5})\s*/\s*(?P<year>\d{2,4})\)\s*",
    re.IGNORECASE,
)
#: The inflected act-name word at the END of a bounded pre-id slice (a single
#: bounded word token), anchored to ``\Z`` — no nested repeat over an optional
#: prefix, so it does not trip the regex perf gate.
_NAME_WORD_AT_END_RE = re.compile(r"([A-Za-zÄÖÅäöå][\wäöå-]{0,59})\s*\Z", re.IGNORECASE)


def _name_word_before(text: str, id_start: int) -> str:
    """The inflected act-name word immediately preceding the id at ``id_start``.

    Reads the last word token of a short bounded look-back before the id (e.g.
    ``lukiolain`` in ``lukiolain (629/1998)``). ``""`` when none.
    """
    look_back = text[max(0, id_start - 70) : id_start]
    m = _NAME_WORD_AT_END_RE.search(look_back)
    return m.group(1) if m else ""


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the sentence (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True)
class DelegationCore:
    """One delegation/authority grant the sentence carries.

    The cue is a DISCONTINUOUS constituent: a power-verb anchor span
    (``cue_start``/``cue_end``) and an instrument-anchor span
    (``instrument_start``/``instrument_end``), which may be far apart in the clause
    (a wide modifier gap). Both token spans are owned; the intervening prose is
    benign residual.

    Attributes:
        kind:        Closed-list SURFACE classification of the issuing authority
                     (``VN_ASETUS`` / ``MIN_ASETUS`` / ``PRES_ASETUS`` /
                     ``AGENCY`` / ``ASETUS``) — surface form, not legal validity.
        cue:         The delegation-cue SURFACE (the verb anchor token), as matched.
        cue_start:   Char offset (sentence-local) where the power-verb anchor begins.
        cue_end:     One-past the power-verb anchor.
        instrument:  Closed-list lower instrument (``asetus`` / ``määräys`` /
                     ``päätös``) the power issues.
        instrument_start: Char offset where the instrument anchor (``asetuksella`` /
                     ``määräyksiä``) begins.
        instrument_end:   One-past the instrument anchor.
        holder_start: Char offset where the authority-holder NP begins, or ``None``.
        holder_end:   One-past the holder NP, or ``None``.
        holder_underspecified: True when no overt issuer NP binds (the bare-
                     ``asetuksella`` / impersonal register). NOT "absent" — the
                     issuer exists in the grant but is left unfixed by the text.
        basis_start: Char offset where the ``… nojalla`` / ``… mukaan`` provision
                     basis window begins, or ``None`` (no overt basis).
        basis_end:   One-past the basis window, or ``None``.
        basis_targets: The references-recognized provision targets inside the
                     basis window (labels like ``"44"`` / ``"8"``), empty when no
                     basis or none recognized.
    """

    kind: str
    cue: str
    cue_start: int
    cue_end: int
    instrument: str
    instrument_start: int
    instrument_end: int
    holder_start: int | None
    holder_end: int | None
    holder_underspecified: bool
    basis_start: int | None
    basis_end: int | None
    basis_targets: tuple[str, ...]


@dataclass(frozen=True)
class DelegationParse:
    """A delegation/authority sentence construction parse (the lite IR).

    Attributes:
        seg_start / seg_end: Sentence char range (sentence-local; the parse runs
                             on ``text`` so ``seg_start == 0``).
        text:                The exact sentence text.
        kind:                ``"delegation"`` when >=1 core parsed; ``"declined"``
                             when a delegation cue was present but no core parsed.
        cores:               The recognized delegation cores, in source order.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    cores: tuple[DelegationCore, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = DELEGATION_LANE_CONSTRUCTION_OWNED


def _clause_spans(text: str) -> list[tuple[int, int]]:
    """Partition ``text`` into clause spans on ``.`` / ``;`` / newline boundaries.

    The boundary char itself is left OUTSIDE every clause span (it falls to
    residual), so the clause spans plus the boundary chars tile the sentence. A
    delegation grant must have BOTH its anchors inside ONE clause span.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in _CLAUSE_BOUNDARY_RE.finditer(text):
        if m.start() > cursor:
            spans.append((cursor, m.start()))
        cursor = m.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fill_residuals(n: int, owned: list[tuple[int, int]], reason: str) -> list[Residual]:
    residuals: list[Residual] = []
    cursor = 0
    for s, e in _merge_intervals(owned):
        if s > cursor:
            residuals.append(Residual(cursor, s, reason))
        cursor = max(cursor, e)
    if cursor < n:
        residuals.append(Residual(cursor, n, reason))
    return residuals


def _classify_kind(classify_text: str) -> str:
    """Classify the issuing authority from the cue+holder surface.

    Mirrors the production ``_classify_delegation_type`` (which keys on the FULL
    matched clause text, issuer included): valtioneuvosto → VN_ASETUS, a
    ministeriö → MIN_ASETUS, presidentti → PRES_ASETUS, a määräys/ohje surface →
    AGENCY, else generic ASETUS. ``classify_text`` is the cue surface UNION the
    holder surface, because the instrument-first / agency cue spans
    (``asetuksella säädetään``) do NOT contain the issuer — the holder is owned by
    a separate span — so classifying off the cue alone would mis-key every
    holder-before-cue grant as the generic ``ASETUS``.

    Precedence matches the production ``_classify_delegation_type`` order:
    valtioneuvosto first, then ministeriö, then presidentti, then the
    agency/määräys surface, then the generic fallback.
    """
    t = classify_text.lower()
    # GENITIVE issuer forms ONLY. ``valtioneuvoston`` / ``ministeriön`` /
    # ``presidentin`` are the genitive surfaces that bind an ``asetuksella``
    # grant (``valtioneuvoston asetuksella``). The NOMINATIVE issuer heading an
    # ``antaa määräyksiä`` agency grant (``valtioneuvosto voi antaa määräyksiä``)
    # is NOT a decree class — it falls through to the agency/määräys check below.
    # This mirrors the production ``_classify_delegation_type`` EXACTLY, which keys
    # on the genitive ``"valtioneuvoston"`` / ``"ministeriön"`` / ``"presidentin"``.
    if "valtioneuvoston" in t:
        return KIND_VN_ASETUS
    if "ministeriön" in t:
        return KIND_MIN_ASETUS
    if "presidentin" in t:
        return KIND_PRES_ASETUS
    if "määräyksi" in t or "ohjeita" in t or "määräyks" in t:
        return KIND_AGENCY
    return KIND_ASETUS


def _holder_span_in_clause(
    text: str,
    clause_start: int,
    clause_end: int,
    anchor_offset: int,
    *,
    adjacent_only: bool = False,
    search_floor: int | None = None,
) -> tuple[int | None, int | None, bool]:
    """Find the authority-holder NP span bound to the instrument anchor.

    The issuer is the holder NP that BINDS the instrument: the holder match nearest
    to (at or before, else just after) the instrument anchor — NOT merely the last
    holder in the clause, which can be a name in the regulated OBJECT (``ministeriön
    asetuksella ... annettua VALTIONEUVOSTON asetusta``: the valtioneuvosto is the
    object, the ministeriö is the issuer bound to ``asetuksella``). ``anchor_offset``
    is the instrument-anchor start (sentence-local). A clause with no overt issuer
    NP (a bare ``asetuksella säädetään``) is holder-underspecified (the impersonal
    register), NOT absent.

    ``adjacent_only`` (the ASETUS instrument shape): the genitive issuer of an
    ``asetuksella`` decree is the construction ``[issuer-genitive] asetuksella`` —
    the issuer NP immediately precedes the anchor (only whitespace between). When
    set, ONLY a holder ending adjacent to the anchor binds; the ``after`` fallback
    is rejected. This stops a COORDINATED clause's later-instrument issuer being
    wrongly grabbed across the coordinator (``annetaan asetuksella, ympäristö-
    ministeriön päätöksellä …``: the ``asetuksella`` is a bare/generic decree, the
    ``ympäristöministeriön`` genitive binds ``päätöksellä``, NOT the asetus).

    ``search_floor`` (sentence-local) lower-bounds the holder search to the segment
    AFTER the PREVIOUS coordinated instrument anchor — so a coordinated grant's
    issuer cannot reach back across an earlier anchor (``valtioneuvoston asetuksella
    ja ministeriön asetuksella``: the second ``asetuksella``'s issuer is
    ``ministeriön``, confined to the post-first-anchor segment).
    """
    floor_local = 0 if search_floor is None else max(0, search_floor - clause_start)
    base = clause_start + floor_local
    clause = text[base:clause_end]
    anchor_local = anchor_offset - base
    before: re.Match[str] | None = None  # nearest holder ending at/before anchor
    after: re.Match[str] | None = None  # first holder starting after anchor
    for m in _HOLDER_RE.finditer(clause):
        if m.end() <= anchor_local:
            before = m  # keep the LATEST one before the anchor (nearest)
        elif after is None:
            after = m
    if adjacent_only:
        # Only an immediately-preceding genitive issuer binds an asetus anchor.
        if before is not None and clause[before.end() : anchor_local].strip() == "":
            return base + before.start(), base + before.end(), False
        return None, None, True
    chosen = before if before is not None else after
    if chosen is not None:
        return base + chosen.start(), base + chosen.end(), False
    return None, None, True


def _basis_span(
    text: str, search_from: int
) -> tuple[int | None, int | None, tuple[str, ...]]:
    """Find a ``… nojalla`` / ``… mukaan`` provision-basis window, REUSING refs.

    Scans from ``search_from`` for a ``nojalla`` / ``mukaan`` terminal whose
    preceding window carries a provision-id signal (a ``(NUM/YEAR)`` id or a
    ``N §`` path). Returns the window span and the references-recognized provision
    target labels inside it. ``(None, None, ())`` when no basis is present.

    The window left boundary is the previous clause boundary (``.``/``;``/newline
    / start); the references recognizer (:func:`parse_body_provision_tail_spanned`)
    parses each conjunct's section path — REUSING the existing sub-ref grammar, not
    a new section regex.

    CONJUNCT DISTRIBUTION: a single ``nojalla`` may coordinate several authority
    bases (``…lain (629/1998) 36 §:n 1 momentin ja valtion maksuperustelain
    (150/1992) 8 §:n nojalla``). We take the section tail AFTER EACH ``(NUM/YEAR)``
    id in the window and distribute the single ``nojalla`` over every conjunct, so
    BOTH ``36`` and ``8`` are recognized — not only the conjunct adjacent to
    ``nojalla``. This mirrors the production ``extract_asetus_authority`` conjunct
    loop, which an earlier single-match approach got wrong (it dropped all but the
    last conjunct).
    """
    term = _BASIS_TERMINAL_RE.search(text, search_from)
    if term is None:
        return None, None, ()
    # Left boundary: the previous clause terminator (``.`` / ``;`` / newline)
    # before the basis terminal. NOTE: we deliberately do NOT split on ``:`` —
    # the Finnish section surface ``8 §:n`` carries an internal colon, so a
    # ``:``-boundary would truncate the window to ``n `` and lose the section id.
    left = 0
    for m in re.finditer(r"[.;\n]", text[: term.start()]):
        left = m.end()
    window = text[left : term.start()]
    # Blank any ``, sellaisena kuin se on laissa NNN/YYYY,`` amendment-version
    # interjection between the path and the terminal: the inner ids are AMENDING
    # acts (metadata), not the basis, and the interjection prose would otherwise
    # defeat the adjacency guard. Offsets are preserved (equal-length blanking).
    window = _strip_amendment_interjections(window)
    if not _BASIS_ID_SIGNAL_RE.search(window):
        return None, None, ()
    # ADJACENCY GUARD: the provision path must DIRECTLY precede the terminal (only
    # provision-tail vocabulary in between). Rejects the long-range false basis
    # where an unrelated earlier ``(NUM/YEAR) §`` ref sits far to the left of an
    # anaphoric bare ``sen nojalla`` separated by prose.
    if not _BASIS_PATH_BEFORE_TERMINAL_RE.search(window):
        return None, None, ()
    # Recognize each conjunct's provision path. For each ``(NUM/YEAR)`` id, feed
    # the slice from just after that id up to the NEXT id (or window end) to the
    # references recognizer (so ``N §:n`` is what it sees, not the act-name prose
    # before the id). We do NOT swallow recognizer exceptions (fail loud — a parse
    # crash here is a real defect, not a benign "no basis"); an empty target list
    # is the legitimate "no recognizable section" outcome (a bare ``(NUM/YEAR) …
    # nojalla`` with no ``§``). A window with NO id at all (a bare ``N §:n
    # nojalla`` without a statute id) is parsed as a single conjunct.
    id_matches = list(re.finditer(r"\(\d{1,5}\s*/\s*\d{2,4}\)\s*", window))
    targets: list[str] = []
    if id_matches:
        for i, idm in enumerate(id_matches):
            tail_end = (
                id_matches[i + 1].start() if i + 1 < len(id_matches) else len(window)
            )
            tail = window[idm.end() : tail_end]
            parsed = parse_body_provision_tail_spanned(tail)
            targets.extend(t.section_label for t in parsed.targets if t.section_label)
    else:
        parsed = parse_body_provision_tail_spanned(window)
        targets.extend(t.section_label for t in parsed.targets if t.section_label)
    # Preserve source order, drop duplicates (coordinated conjuncts can repeat).
    seen: set[str] = set()
    ordered = [t for t in targets if not (t in seen or seen.add(t))]
    return left, term.end(), tuple(ordered)


def _build_core(
    text: str,
    clause_start: int,
    clause_end: int,
    verb_span: tuple[int, int],
    instr_span: tuple[int, int],
    instrument: str,
    *,
    own_basis: bool,
    search_floor: int | None = None,
) -> DelegationCore:
    """Assemble ONE delegation core from a (verb anchor, instrument anchor) pair.

    The holder NP is the issuer bound to THIS instrument anchor; the issuer KIND is
    classified off the narrow holder-vicinity window. ``own_basis`` is True only for
    the FIRST core of a coordinated clause, so the shared ``… nojalla`` provision
    basis (which precedes the coordinated grant once) is owned by exactly one core —
    the remaining coordinated cores leave the basis unset (it is already owned, so
    re-owning it would double-count the same span in totality).
    """
    h_start, h_end, underspec = _holder_span_in_clause(
        text,
        clause_start,
        clause_end,
        instr_span[0],
        adjacent_only=(instrument == INSTRUMENT_ASETUS),
        search_floor=search_floor,
    )
    holder_surface = (
        text[h_start:h_end] if h_start is not None and h_end is not None else ""
    )
    instrument_surface = text[instr_span[0] : instr_span[1]]
    if instrument == INSTRUMENT_MAARAYS:
        # The AGENCY shape (``voi antaa määräyksiä``) is ALWAYS an agency
        # regulation in the production taxonomy — its bounded match never captures
        # a genitive ``…n asetuksella`` issuer, so it classifies as AGENCY
        # regardless of any genitive name modifying the agency NP (``Valtioneuvoston
        # kanslia voi antaa määräyksiä`` is an AGENCY määräys, not a VN_ASETUS).
        kind = KIND_AGENCY
    else:
        # ASETUS shape: classify off a NARROW issuer-vicinity window — the holder
        # NP (the issuer bound to ``asetuksella``) UNION the instrument anchor —
        # NOT the whole clause. The whole clause can carry a spurious issuer name
        # in the regulated OBJECT (``ministeriön asetuksella ... annettua
        # VALTIONEUVOSTON asetusta``: the valtioneuvosto is the object, the
        # ministeriö is the issuer). The genitive issuer in the holder
        # discriminates VN_ASETUS / MIN_ASETUS / PRES_ASETUS; no genitive issuer →
        # generic ASETUS. Mirrors the production classifier's bounded match_text.
        kind = _classify_kind(holder_surface + " " + instrument_surface)

    b_start: int | None = None
    b_end: int | None = None
    basis_targets: tuple[str, ...] = ()
    if own_basis:
        # The ``nojalla`` provision basis (if any) lives inside this clause, before
        # the grant. Only the first coordinated core owns it (shared span).
        b_start, b_end, basis_targets = _basis_span(text, clause_start)
        if b_start is None or b_end is None or b_end > clause_end:
            b_start, b_end, basis_targets = None, None, ()

    return DelegationCore(
        kind=kind,
        cue=text[verb_span[0] : verb_span[1]],
        cue_start=verb_span[0],
        cue_end=verb_span[1],
        instrument=instrument,
        instrument_start=instr_span[0],
        instrument_end=instr_span[1],
        holder_start=h_start,
        holder_end=h_end,
        holder_underspecified=underspec,
        basis_start=b_start,
        basis_end=b_end,
        basis_targets=basis_targets,
    )


def _recognize_clause_cores(
    text: str, clause_start: int, clause_end: int
) -> list[DelegationCore]:
    """Recognize the delegation grant(s) inside a clause via two-anchor co-occurrence.

    A grant is recognized iff the clause carries either:

      * ASETUS shape — an ``asetuksella`` instrument anchor AND a power-verb anchor
        (any order, any gap within the clause); or
      * AGENCY shape — an ``antaa`` power head AND a ``määräyksiä`` / ``ohjeita``
        OBJECT anchor (the lower instrument as the verb's object).

    MULTI-CORE coordination: one clause may delegate to SEVERAL coordinated
    instruments sharing ONE power verb — the drafting form
    ``annetaan asetuksella, X:n päätöksellä ja Y:n järjestyksellä``, or a clause
    carrying SEVERAL distinct ``asetuksella`` instrumental anchors. Each coordinated
    instrument anchor that the family models (an ``asetuksella`` decree anchor, or
    the agency ``määräyksiä``/``ohjeita`` object) gets its OWN core (its own issuer
    KIND, bound to that anchor), sharing the clause's single power-verb anchor as
    the discontinuous cue head. Non-modelled coordinated instruments (a bare
    ``päätöksellä`` / ``järjestyksellä`` with no asetus/määräys surface) stay benign
    residual — the family's instrument vocabulary is asetus / määräys, so they are
    not silently asserted as grants. Returns ``[]`` when no grant shape is present
    (the clause is out of family).
    """
    clause = text[clause_start:clause_end]

    verb = _VERB_ANCHOR_RE.search(clause)
    antaa = _VERB_ANTAA_RE.search(clause)

    # ASETUS shape: EVERY ``asetuksella`` instrumental anchor in the clause is a
    # decree grant (coordinated decrees → one core each). Requires a power verb.
    # EXCEPT a self-/cross-reference anchor (``tällä asetuksella`` / ``tämän
    # asetuksella``): the decree exercising its OWN power is not a delegation that
    # grants the power to issue a lower instrument, so such an anchor is not a
    # grant. It falls to benign residual (totality preserved). Mirrors the H5
    # recognizer's demonstrative cross-reference guard.
    asetus_anchors = [
        am
        for am in _INSTRUMENT_ASETUKSELLA_RE.finditer(clause)
        if not _DEMONSTRATIVE_BEFORE_ASETUS_RE.search(clause[: am.start()])
    ]
    # AGENCY shape: the ``määräyksiä`` / ``ohjeita`` object anchor(s) under ``antaa``.
    object_anchors = list(_OBJECT_MAARAYS_RE.finditer(clause))

    cores: list[DelegationCore] = []
    # First-anchor-owns-basis: order the coordinated anchors by source position so
    # the leftmost (the one the shared ``nojalla`` basis precedes) owns the basis.
    anchors: list[tuple[int, tuple[int, int], str, tuple[int, int]]] = []
    if asetus_anchors and verb is not None:
        verb_span = (clause_start + verb.start(), clause_start + verb.end())
        for am in asetus_anchors:
            anchors.append(
                (
                    am.start(),
                    (clause_start + am.start(), clause_start + am.end()),
                    INSTRUMENT_ASETUS,
                    verb_span,
                )
            )
    if antaa is not None and object_anchors:
        antaa_span = (clause_start + antaa.start(), clause_start + antaa.end())
        for om in object_anchors:
            anchors.append(
                (
                    om.start(),
                    (clause_start + om.start(), clause_start + om.end()),
                    INSTRUMENT_MAARAYS,
                    antaa_span,
                )
            )

    if not anchors:
        return []

    anchors.sort(key=lambda a: a[0])
    prev_anchor_end: int | None = None
    for i, (_pos, instr_span, instrument, vspan) in enumerate(anchors):
        cores.append(
            _build_core(
                text,
                clause_start,
                clause_end,
                vspan,
                instr_span,
                instrument,
                own_basis=(i == 0),
                # Confine a coordinated grant's issuer search to the segment after
                # the previous anchor (it cannot reach back across an earlier one).
                search_floor=prev_anchor_end,
            )
        )
        prev_anchor_end = instr_span[1]
    return cores


def parse_delegation_sentence(text: str) -> DelegationParse:
    """Parse one sentence span into delegation/authority construction cores.

    ``text`` is the EXACT sentence span, in its own local coordinate system.
    Deterministic: split the sentence into clauses (on ``.`` / ``;`` / newline) and
    recognize the delegation grant(s) per clause via two-anchor co-occurrence (an
    instrument anchor + a power-verb anchor, any order, any intra-clause gap; or an
    ``antaa`` + ``määräyksiä`` agency shape). A clause may carry SEVERAL coordinated
    instrument anchors sharing one power verb (``annetaan asetuksella, X:n
    päätöksellä …`` / several ``asetuksella`` anchors) — each modelled instrument
    anchor yields its OWN core. For each grant emit ONE
    delegation core — classified into an issuer KIND, owning the discontinuous cue
    (verb anchor + instrument anchor), the authority-holder NP span (or marking the
    holder underspecified for the bare/impersonal register), the instrument kind,
    and the ``… nojalla`` / ``… mukaan`` provision-basis window (parsed via the
    references recognizer). Every other char is typed explicit residual.

    Declines (typed residue, never a guessed parse) when NO clause yields a grant
    (the caller's family discriminator guarantees a grant for in-scope spans, so a
    decline here is the out-of-family case).
    """
    n = len(text)
    cores: list[DelegationCore] = []
    owned: list[tuple[int, int]] = []
    for clause_start, clause_end in _clause_spans(text):
        for core in _recognize_clause_cores(text, clause_start, clause_end):
            cores.append(core)
            owned.append((core.cue_start, core.cue_end))
            owned.append((core.instrument_start, core.instrument_end))
            if core.holder_start is not None and core.holder_end is not None:
                owned.append((core.holder_start, core.holder_end))
            if core.basis_start is not None and core.basis_end is not None:
                owned.append((core.basis_start, core.basis_end))

    if not cores:
        return DelegationParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            cores=(),
            residuals=(Residual(0, n, "no_delegation_core"),),
            parser_lane=DELEGATION_LANE_DECLINED,
        )

    residuals = _fill_residuals(n, owned, "benign_uninterpreted_prose")
    return DelegationParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="delegation",
        cores=tuple(cores),
        residuals=tuple(residuals),
        parser_lane=DELEGATION_LANE_CONSTRUCTION_OWNED,
    )


def assert_total_ownership(dp: DelegationParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of core cue (verb) spans, instrument-anchor spans, holder spans,
    basis spans, and the explicit residual spans must cover every char of the
    sentence with NO gap and NO silent drop. Raises ``AssertionError`` on
    violation.
    """
    n = dp.seg_end - dp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = []
    for c in dp.cores:
        spans.append((c.cue_start, c.cue_end))
        spans.append((c.instrument_start, c.instrument_end))
        if c.holder_start is not None and c.holder_end is not None:
            spans.append((c.holder_start, c.holder_end))
        if c.basis_start is not None and c.basis_end is not None:
            spans.append((c.basis_start, c.basis_end))
    spans.extend((r.char_start, r.char_end) for r in dp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in sentence "
            f"(first gap at {missing[0]}); SILENT DROP. text={dp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: DelegationParse -> [production delegation key]
# ---------------------------------------------------------------------------


def delegation_key(kind: str, instrument: str) -> str:
    """Canonical census key for one FORWARD delegation grant.

    Keyed on the load-bearing IDENTITY the production forward extractor carries on
    its :class:`DelegationEdge`: the ``delegation_type`` (issuer class). The
    instrument is appended so an agency ``määräys`` grant and a decree ``asetus``
    grant are distinct keys. This is the SAME identity :mod:`delegation_census`
    derives from the oracle's ``extract_delegations`` edges, so the projected set
    is directly comparable to the production oracle for the same span.
    """
    return f"grant:{kind}:{instrument}"


def projection_grant_keys(dp: DelegationParse) -> set[str]:
    """The projected FORWARD-grant set as canonical census keys."""
    return {delegation_key(c.kind, c.instrument) for c in dp.cores}


# ---------------------------------------------------------------------------
# Standalone authority-basis recognizer (the ``… nojalla`` REVERSE direction)
# ---------------------------------------------------------------------------
#
# The construction-grammar form of an asetus authority basis: a decree's enacting
# clause names the provision under which it is issued — ``[act-name] (NUM/YEAR) N
# §:n [M momentin] nojalla``. This recognizer lifts that basis from arbitrary text
# (a decree PREAMBLE, or a body sentence) WITHOUT requiring a forward delegation
# core, REUSING the same conjunct distribution + adjacency guard + provision-tail
# recognizer the in-clause ``_basis_span`` uses. It is the construction-PRIMARY
# replacement for the production ``extract_asetus_authority`` regex (which the
# reference-mention lift demotes to a typed-residue fallback).


@dataclass(frozen=True)
class AuthorityBasisConjunct:
    """One coordinated authority basis recognized in a ``… nojalla`` clause.

    Attributes:
        num:           statute NUMBER as written (the ``NUM`` of ``(NUM/YEAR)``).
        year:          statute YEAR as written (the ``YEAR`` of ``(NUM/YEAR)``).
        name_word:     the inflected act-name word immediately preceding the id
                       (the drafting-KIND signal: ``…lain`` → act, ``…asetuksen``
                       → decree, ``…päätöksen`` → decision; ``""`` when none).
        section_labels: the references-recognized section path label(s) inside this
                       conjunct (e.g. ``("36",)`` / ``("60a",)``), possibly empty
                       when the basis carries an id but no overt ``§``.
        char_start:    char offset (text-local) where the conjunct's id begins.
        char_end:      one-past the ``nojalla`` / ``mukaan`` terminal.
    """

    num: str
    year: str
    name_word: str
    section_labels: tuple[str, ...]
    char_start: int
    char_end: int


def extract_authority_bases(text: str) -> list[AuthorityBasisConjunct]:
    """Recognize every ``[act] (NUM/YEAR) N §:n nojalla`` authority basis in ``text``.

    Scans for each ``nojalla`` / ``mukaan`` terminal whose preceding window is a
    genuine provision basis (carries a ``(NUM/YEAR)`` id directly preceding the
    terminal — the adjacency guard rejects the anaphoric ``sen nojalla`` and the
    long-range false basis), and distributes the single terminal over EVERY
    coordinated ``(NUM/YEAR)`` conjunct, each with its own section path. Returns one
    :class:`AuthorityBasisConjunct` per coordinated id, in source order.

    The left boundary of a terminal's window is the previous terminal's end (so two
    successive ``nojalla`` clauses do not bleed) or a ``.``/``;``/newline clause
    boundary — REUSING the same window logic as the in-clause basis recognizer.
    """
    out: list[AuthorityBasisConjunct] = []
    prev_terminal_end = 0
    for term in _BASIS_TERMINAL_RE.finditer(text):
        # Window left boundary: max of the previous terminal end and the previous
        # clause boundary before this terminal (so a coordinated multi-conjunct
        # window is kept whole, but an earlier independent clause is excluded).
        left = prev_terminal_end
        for m in re.finditer(r"[.;\n]", text[:term.start()]):
            if m.end() > left:
                left = m.end()
        prev_terminal_end = term.end()
        window = text[left : term.start()]
        # Blank any ``, sellaisena kuin se on laissa NNN/YYYY,`` amendment-version
        # interjection: the inner ids are the AMENDING act(s), not the authority
        # basis, and the interjection prose would otherwise defeat the adjacency
        # guard (the basis is the OUTER ``[act] (NUM/YEAR) N §:n … nojalla``).
        # Equal-length blanking preserves the window-local id/name-word offsets.
        window = _strip_amendment_interjections(window)
        if not _BASIS_ID_SIGNAL_RE.search(window):
            continue
        if not _BASIS_PATH_BEFORE_TERMINAL_RE.search(window):
            continue
        id_matches = list(_AUTHORITY_ID_RE.finditer(window))
        if not id_matches:
            continue
        for i, idm in enumerate(id_matches):
            tail_end = (
                id_matches[i + 1].start() if i + 1 < len(id_matches) else len(window)
            )
            tail = window[idm.end() : tail_end]
            parsed = parse_body_provision_tail_spanned(tail)
            labels = tuple(
                t.section_label for t in parsed.targets if t.section_label
            )
            out.append(
                AuthorityBasisConjunct(
                    num=idm.group("num"),
                    year=idm.group("year"),
                    name_word=_name_word_before(window, idm.start()),
                    section_labels=labels,
                    char_start=left + idm.start(),
                    char_end=term.end(),
                )
            )
    return out
