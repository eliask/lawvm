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

CANONICAL CUTOVER (DELEGATION-UNIFY-VERDICT step 5)
==================================================
The FORWARD-grant recognition is now the single canonical token-native parser's
(:func:`lawvm.finland.legal_surface.delegation_canonical.parse_delegation_grants`,
substrate Q1). :func:`parse_delegation_sentence` is a thin compatibility ADAPTER
for the forward direction: it calls the canonical parser and projects each
canonical ``DelegationGrant`` back to a :class:`DelegationCore` (a field rename,
not a re-parse), then fills the no-silent-drop residue over the complement so
:func:`assert_total_ownership` holds exactly as before. The ``delegated_instrument``
node identity (cue span + instrument kind + anchor start) is preserved for every
core C already produced; canonical recognizes a strict superset of the
adjudicated-correct union, so the only deltas are the adjudicated ADDITIONS (bare
/ ``Opetusministeriön`` / ``vahvistetaan`` / sentence-initial / ``ohje`` /
``päätös`` grants the old two-anchor model missed) and the adjudicated REMOVALS
(the genitive-instrument cross-reference false positives the canonical guards
decline).

The REVERSE authority-basis recognizer :func:`extract_authority_bases` is
UNTOUCHED — it is the clean construction-owned reverse direction and is OUT OF
SCOPE for the forward unification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Reuse the references provision-tail recognizer for the ``nojalla`` basis: do
# NOT re-implement section/momentti recognition. A blank import-time failure
# would be a silent fallback, so the import is unguarded (fail loud).
from lawvm.finland.references.sections import parse_body_provision_tail_spanned

# The single canonical forward-grant construction parser (DELEGATION-UNIFY-VERDICT
# step 5: C-forward calls the winner). Unguarded import = fail loud.
from lawvm.finland.legal_surface.delegation_canonical import (
    DelegationGrant,
    parse_delegation_grants,
)

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

# Forward-grant recognition (power-verb / instrument / holder / clause-boundary
# anchors) moved to the canonical token-native parser delegation_canonical.py
# (DELEGATION-UNIFY-VERDICT step 5). Only the reverse authority-basis surface
# vocabulary remains here, native to extract_authority_bases.

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


def _core_from_grant(grant: DelegationGrant) -> DelegationCore:
    """Project one canonical :class:`DelegationGrant` to a C ``DelegationCore``.

    Identity-preserving: the canonical grant already carries the EXACT C core
    fields (kind / cue / instrument / holder / basis spans) — this is a field
    rename, not a re-parse. The instrument vocabulary is a strict superset of C's
    old two-anchor set (it adds ``ohje`` and the ``päätös`` / object cases the old
    two-anchor model lacked); those richer instrument kinds flow straight through
    as the adjudicated ``old_B_correct`` additions.
    """
    return DelegationCore(
        kind=grant.kind,
        cue=grant.cue,
        cue_start=grant.cue_start,
        cue_end=grant.cue_end,
        instrument=grant.instrument,
        instrument_start=grant.instrument_start,
        instrument_end=grant.instrument_end,
        holder_start=grant.holder_start,
        holder_end=grant.holder_end,
        holder_underspecified=grant.holder_underspecified,
        basis_start=grant.basis_start,
        basis_end=grant.basis_end,
        basis_targets=grant.basis_targets,
    )


def parse_delegation_sentence(text: str) -> DelegationParse:
    """Parse one sentence span into delegation/authority construction cores.

    ``text`` is the EXACT sentence span, in its own local coordinate system.

    CANONICAL CUTOVER (DELEGATION-UNIFY-VERDICT step 5): the FORWARD-grant
    recognition is the single canonical token-native parser's
    (:func:`lawvm.finland.legal_surface.delegation_canonical.parse_delegation_grants`,
    substrate Q1). This function is a thin compatibility ADAPTER for the forward
    direction: it calls the canonical parser and projects each canonical
    :class:`~lawvm.finland.legal_surface.delegation_canonical.DelegationGrant`
    back to a :class:`DelegationCore` (a field rename, not a re-parse), then fills
    the no-silent-drop residue over the complement so :func:`assert_total_ownership`
    holds exactly as before. The ``delegated_instrument`` node identity (cue span +
    instrument kind + anchor start) is preserved for every core C already produced;
    canonical recognizes a strict superset of the adjudicated-correct union, so the
    only deltas are the adjudicated additions (bare / ``Opetusministeriön`` /
    ``vahvistetaan`` / sentence-initial / ``ohje`` / ``päätös`` grants the old
    two-anchor model missed) and the adjudicated removals (the old-B-style genitive
    cross-reference false positives the canonical guards decline).

    The REVERSE authority-basis recognizer :func:`extract_authority_bases` is
    UNTOUCHED — it is the clean construction-owned reverse direction, out of scope
    for the forward unification.

    Declines (typed residue, never a guessed parse) when the canonical parser
    yields NO forward grant for the sentence — the out-of-family / declined case.
    """
    n = len(text)
    scan = parse_delegation_grants(text)
    cores: list[DelegationCore] = [_core_from_grant(g) for g in scan.grants]

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

    owned: list[tuple[int, int]] = []
    for core in cores:
        owned.append((core.cue_start, core.cue_end))
        owned.append((core.instrument_start, core.instrument_end))
        if core.holder_start is not None and core.holder_end is not None:
            owned.append((core.holder_start, core.holder_end))
        if core.basis_start is not None and core.basis_end is not None:
            owned.append((core.basis_start, core.basis_end))

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
