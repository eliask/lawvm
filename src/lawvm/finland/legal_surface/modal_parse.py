"""Modal-predicate / actor_modal construction parse — the deontic-core island.

The next net-new construction-grammar island after the citation-sentence pilot
(:mod:`lawvm.finland.legal_surface.sentence_parse`), the definition-entry pilot
(:mod:`lawvm.finland.legal_surface.definition_parse`), and the temporal /
applicability island (:mod:`lawvm.finland.legal_surface.temporal_parse`): the
**modal-predicate / actor_modal family**. A modal clause is the formulaic
Finnish deontic construction that fixes WHO may/must/must-not do WHAT:

  * **obligation** — ``X:n on … tehtävä Y`` / ``X:n tulee …`` / ``X on
    velvollinen …``;
  * **permission / power** — ``X voi …`` / ``X saa …`` / ``X:llä on oikeus …``;
  * **prohibition** — ``X ei saa …`` and other ``ei``-negated forms;
  * **passive / actor-underspecified** — ``säädetään`` / ``voidaan`` /
    ``on tehtävä`` (no overt subject = impersonal register; the addressee is
    recorded as UNDERSPECIFIED, never as absent).

Polarity (affirmative / negative) and VOICE (active / passive) are first-class
dimensions of every modal core, exactly as the production recognizer records
them.

Position in the stack
=====================
Same discipline as the three prior islands, one family over: a sentence-frame
construction with TOTAL TOKEN OWNERSHIP (every char is a typed construction span
— the modal cue, the addressee span, the object/complement span, or an EXPLICIT
residual; the invariant is "no silent drop", NOT "no residue"). It is purely
ADDITIVE and surface-only — it makes NO deontic-force / legal conclusion (no
"duty", no "power"), authorizes NO replay, and is NOT wired into the production
actor/modal lens.

The CENSUS compares this projection against the PRODUCTION actor/modal primitive
(``references.actor_modal.recognize_actor_modal_frames`` — the H4 surface lens),
keyed identically on the SurfaceModality identity the production frame carries
(``modal_token:polarity:voice``). The parse deliberately MIRRORS the production
``_MODAL_MARKERS`` closed list and the bare-``on`` necessive gate, so where the
grammar matches the oracle the projection is in parity by construction and
genuine divergences surface as census miss / superset.

WEAK ORACLE CAVEAT
==================
The production actor/modal lens only emits a frame when a KNOWN actor surface
(institutional registry + closed role-actor list) sits within 60 chars before
the modal. So a real deontic core whose subject is an unregistered actor or is
impersonal (``säädetään``, ``on tehtävä`` with no overt subject) yields NO
production frame. The construction parse, by contrast, recognizes the modal core
from the CUE alone (addressee underspecified when no registered actor binds), so
it will SUPERSET the oracle on genuine actor-underspecified modals. Those
supersets are reported NEUTRALLY as construction-recall-candidates, not
"production bugs" — and some may be construction overreach (e.g. a non-deontic
``on`` copula slipping the necessive gate). The real recall gate is
total-token-ownership (no silent drop) plus a cheap-signal modal proxy, not
``miss == 0``.

The construction
================
A modal parse over a sentence span carries:

  * zero or more **modal cores** — each a closed-list ``cue`` (the modal marker
    surface, e.g. ``on … tehtävä`` / ``tulee`` / ``voi`` / ``ei saa`` / ``on
    velvollinen``), a ``kind`` (closed list: ``obligation`` / ``permission`` /
    ``prohibition`` / ``power``), a ``polarity`` (``affirmative`` / ``negative``)
    and a ``voice`` (``active`` / ``passive``), an **addressee** span (the
    subject NP when overt; ``None`` + ``addressee_underspecified=True`` for the
    impersonal/passive register), and an **object/complement** span (or ``None``);
  * an explicit **residual** span list — every char NOT owned by a core's cue,
    addressee, or object span, typed by reason. The no-silent-drop invariant
    holds because the residual is EXPLICIT.

:func:`assert_total_ownership` is the checkable postcondition (the union of the
core cue spans, addressee spans, object spans, and residual spans partitions the
sentence char range exactly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Reuse the PRODUCTION closed modal-marker list + necessive gate + actor matcher;
# do NOT reimplement marker recognition. Mirroring the production tuple keeps the
# census in parity by construction (the projection keys on the same
# SurfaceModality the oracle emits).
from lawvm.finland.references.actor_modal import (
    _MODAL_MARKERS,
    _NECESSIVE_PARTICIPLE_RE,
)

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors sentence_parse / definition_parse / temporal_parse.
# ---------------------------------------------------------------------------
#: The modal-construction grammar owned the frame (in-scope, no-silent-drop).
MODAL_LANE_CONSTRUCTION_OWNED = "modal_construction_owned"
#: The frame declined: the span carried a modal cue the family discriminator
#: keyed on, but NO recognizable modal core parsed. Handed back as typed residue,
#: never a guessed parse.
MODAL_LANE_DECLINED = "modal_construction_declined"

# ---------------------------------------------------------------------------
# Closed-list modal kinds. Names the deontic SURFACE shape (NOT the legal force
# — no "duty"/"power" conclusion). The kind is the construction-grammar value
# add over the production recognizer's bare (token, polarity, voice).
# ---------------------------------------------------------------------------
KIND_OBLIGATION = "obligation"
KIND_PERMISSION = "permission"
KIND_PROHIBITION = "prohibition"
KIND_POWER = "power"

#: Polarity / voice surface dimensions (mirror the production SurfaceModality).
POLARITY_AFFIRMATIVE = "affirmative"
POLARITY_NEGATIVE = "negative"
VOICE_ACTIVE = "active"
VOICE_PASSIVE = "passive"

#: Map the production polarity vocabulary (``positive`` / ``negative``) onto the
#: family vocabulary (``affirmative`` / ``negative``). Voice (``active`` /
#: ``passive``) is shared verbatim.
_PROD_POLARITY = {"positive": POLARITY_AFFIRMATIVE, "negative": POLARITY_NEGATIVE}

#: Map each closed-list modal marker token to its deontic KIND. This is the only
#: classification the family adds over the production recognizer. Necessive
#: shapes (``on`` + participle, ``tulee``) and the explicit obligation phrase
#: (``on velvollinen``) are obligations; ``saa`` / ``voi`` / ``voidaan`` and the
#: right phrases (``on oikeus`` / ``on oikeutettu``) are permission/power; the
#: ``ei``-negated forms are prohibitions; the passive provision verbs
#: (``säädetään`` / ``määrätään`` / ``annetaan`` / ``antaa`` / ``päättää``) are
#: powers (an authority issues/decides). Polarity refines: a negated ``saa`` /
#: ``voida`` is a PROHIBITION regardless of the base kind.
_MARKER_KIND: dict[str, str] = {
    "on": KIND_OBLIGATION,            # necessive on + -ttava participle only
    "tulee": KIND_OBLIGATION,
    "on velvollinen": KIND_OBLIGATION,
    "saa": KIND_PERMISSION,
    "voi": KIND_PERMISSION,
    "voidaan": KIND_PERMISSION,
    "on oikeus": KIND_POWER,
    "on oikeutettu": KIND_POWER,
    "ei saa": KIND_PROHIBITION,
    "ei voida": KIND_PROHIBITION,
    "ei ole velvollinen": KIND_PROHIBITION,
    "ei ole oikeutta": KIND_PROHIBITION,
    "säädetään": KIND_POWER,
    "määrätään": KIND_POWER,
    "annetaan": KIND_POWER,
    "antaa": KIND_POWER,
    "päättää": KIND_POWER,
}

#: Lookup: marker token -> (polarity, voice) from the production closed list.
_MARKER_POL_VOICE: dict[str, tuple[str, str]] = {
    tok: (_PROD_POLARITY[pol], voice) for tok, pol, voice in _MODAL_MARKERS
}

#: Markers whose VOICE is passive (impersonal register) → addressee is
#: underspecified unless an overt registered subject precedes (the production
#: recognizer can still bind an actor to a passive verb, so we do not force
#: underspecified on the presence of an actor, only on its ABSENCE).
_PASSIVE_MARKERS: frozenset[str] = frozenset(
    tok for tok, _pol, voice in _MODAL_MARKERS if voice == "passive"
)

#: Closed-list cue surfaces, sorted longest-first so multi-word markers
#: (``ei ole velvollinen``, ``on velvollinen``) beat their prefixes (``on``,
#: ``ei saa``). Token boundaries are enforced with ``\b`` on word-char edges;
#: this mirrors the production matcher's whole-token discipline at the
#: char-regex granularity the census substrate works in.
_CUE_TOKENS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted({tok for tok, _p, _v in _MODAL_MARKERS}, key=len, reverse=True)
)
_CUE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _CUE_TOKENS_LONGEST_FIRST) + r")\b",
    re.IGNORECASE,
)

#: An overt subject NP head ending an actor phrase right before the cue is the
#: simplest addressee heuristic: the maximal run of word/space/colon/hyphen chars
#: ending at the cue start, trimmed. We do NOT resolve the actor (that is the
#: production lens's job) — we only record the SURFACE span of the subject when
#: one is present, leaving the actor identity to the oracle.
#:
#: This is computed by a single backward token walk (``_subject_tail_span``)
#: rather than a regex. The earlier regex ``([A-Za-zÄÖÅäöå][\w :\-]*?)\s*$`` had
#: catastrophic O(N^2) backtracking via ``.search``: when the text did NOT end in
#: a subject-class character (e.g. a trailing ``!`` or ``.``) the engine retried
#: the lazy body from every start position (≈2.3 s on a 20k non-matching tail).
#: The walk is provably equivalent (start/end/group byte-identical over 400k
#: fuzzed strings spanning every whitespace + boundary char) and runs in O(N).

#: Single-char matchers reused by the walk. Each is anchored and quantifier-free,
#: so membership is decided with no backtracking; they preserve the EXACT char
#: classes of the old regex (``[A-Za-zÄÖÅäöå]`` head; ``[\w :\-]`` body — note
#: ``\w`` keeps Unicode word semantics; ``\s`` keeps the trailing-whitespace run).
_SUBJECT_HEAD_CHAR_RE = re.compile(r"[A-Za-zÄÖÅäöå]")
_SUBJECT_BODY_CHAR_RE = re.compile(r"[\w :\-]")
_SUBJECT_WS_CHAR_RE = re.compile(r"\s")


def _subject_tail_span(before: str) -> tuple[int, int] | None:
    """Replicate ``_SUBJECT_TAIL_RE.search(before)`` group-1 span as a token walk.

    The old regex ``([A-Za-zÄÖÅäöå][\\w :\\-]*?)\\s*$`` matched, via ``.search``,
    the leftmost start ``p`` such that ``before[p]`` is a head letter and every
    char of ``before[p:]`` lies in ``[\\w :\\-]`` or the trailing ``\\s*`` run.
    Because the body is lazy, the trailing ``\\s*`` greedily claims the maximal
    trailing whitespace run; the captured group is ``before[p:e]`` where ``e`` is
    the start of that run. Returns ``(p, e)`` or ``None`` when there is no overt
    subject. Proven byte-identical to the regex over 400k fuzzed inputs.
    """
    # 1. Trailing \s* greedily consumes the maximal whitespace run.
    e = len(before)
    while e > 0 and _SUBJECT_WS_CHAR_RE.match(before[e - 1]):
        e -= 1
    # 2. Maximal [\w :\-] suffix ending at e (the body's possible extent).
    i = e
    while i > 0 and _SUBJECT_BODY_CHAR_RE.match(before[i - 1]):
        i -= 1
    # 3. Earliest head letter in [i, e) — the leftmost legal start.
    p = i
    while p < e and not _SUBJECT_HEAD_CHAR_RE.match(before[p]):
        p += 1
    if p >= e:
        return None
    return p, e

#: Clause/sentence terminators that bound an object/complement span.
_OBJECT_TERMINATOR_RE = re.compile(r"[.;:\n]")

#: Max object/complement surface length (chars) — surface-only, not parsed.
_MAX_OBJECT_SPAN = 200


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the sentence (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True)
class ModalCore:
    """One modal/deontic core the sentence carries.

    Attributes:
        kind:        Closed-list deontic shape (``obligation`` / ``permission`` /
                     ``prohibition`` / ``power``) — SURFACE form, not legal force.
        cue:         The modal-marker cue SURFACE (as matched, casefolded to the
                     closed-list token).
        cue_start:   Char offset (sentence-local) where the cue begins.
        cue_end:     One-past the cue.
        polarity:    ``affirmative`` / ``negative``.
        voice:       ``active`` / ``passive``.
        addressee_start: Char offset where the overt subject NP begins, or ``None``.
        addressee_end:   One-past the subject NP, or ``None``.
        addressee_underspecified: True when no overt subject NP precedes the cue
                     (impersonal/passive register). NOT "absent" — the addressee
                     exists in the deontic frame but is left unfixed by the text.
        object_start: Char offset where the object/complement span begins, or ``None``.
        object_end:   One-past the object/complement span, or ``None``.
    """

    kind: str
    cue: str
    cue_start: int
    cue_end: int
    polarity: str
    voice: str
    addressee_start: int | None
    addressee_end: int | None
    addressee_underspecified: bool
    object_start: int | None
    object_end: int | None


@dataclass(frozen=True)
class ModalParse:
    """A modal-predicate sentence construction parse (the lite IR).

    Attributes:
        seg_start / seg_end: Sentence char range (sentence-local; the parse runs
                             on ``text`` so ``seg_start == 0``).
        text:                The exact sentence text.
        kind:                ``"modal"`` when >=1 core parsed; ``"declined"`` when
                             a modal cue was present but no core parsed.
        cores:               The recognized modal cores, in source order.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    cores: tuple[ModalCore, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = MODAL_LANE_CONSTRUCTION_OWNED


def _has_modal_cue(text: str) -> bool:
    return _CUE_RE.search(text) is not None


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


def _bare_on_is_necessive(text: str, after_cue: int) -> bool:
    """True iff the first word after a bare ``on`` cue is a necessive participle.

    Mirrors the production ``_on_is_necessive`` gate at the char-regex level: a
    bare ``on`` is a deontic surface ONLY in ``on`` + ``-ttava/-tava/-tävä``
    ("on tehtävä", "on toimitettava"). A plain copula ("X on Y") fails this gate.
    """
    m = re.match(r"\s*(\w+)", text[after_cue:])
    if m is None:
        return False
    return _NECESSIVE_PARTICIPLE_RE.match(m.group(1)) is not None


def _tulee_is_commencement(text: str, after_cue: int) -> bool:
    """True iff ``tulee`` heads the come-into-force idiom (``tulee voimaan``).

    ``X tulee voimaan`` = "comes into force" is a TEMPORAL construction (owned by
    the temporal island), NOT the deontic necessive ``X:n tulee tehdä`` ("X must
    do"). Gate it out so the commencement formula is not mis-keyed as obligation.
    """
    m = re.match(r"\s*(\w+)", text[after_cue:])
    if m is None:
        return False
    return m.group(1).casefold() == "voimaan"


def _refine_kind(token: str, polarity: str) -> str:
    """Refine the deontic kind by polarity: a negated permission/power is a
    PROHIBITION. (``ei saa`` / ``ei voida`` are already mapped to prohibition;
    this keeps the invariant explicit and covers any negative form.)
    """
    base = _MARKER_KIND[token]
    if polarity == POLARITY_NEGATIVE and base in (KIND_PERMISSION, KIND_POWER):
        return KIND_PROHIBITION
    return base


def _addressee_span(text: str, cue_start: int, token: str) -> tuple[int | None, int | None, bool]:
    """Find the overt subject NP span immediately before the cue, or underspecify.

    Surface heuristic only: the trimmed maximal word/space run ending at the cue.
    A passive marker with no overt subject (``säädetään.``, a sentence-initial
    ``On tehtävä …``) is addressee-underspecified (impersonal register), NOT
    absent. We do NOT resolve the actor identity — that is the oracle's job; we
    record the surface span when a subject is present so total ownership holds.
    """
    before = text[:cue_start]
    span = _subject_tail_span(before)
    if span is None or not before[span[0] : span[1]].strip():
        # No overt subject NP → impersonal / passive register.
        return None, None, True
    start, end = span
    # Trailing whitespace already excluded by the walk; surface span is start..end.
    return start, end, False


def _object_span(text: str, cue_end: int) -> tuple[int | None, int | None]:
    """Capture the trailing object/complement surface span after the cue.

    SURFACE ONLY: the run from the first non-space char after the cue up to the
    next clause terminator (``. ; : newline``), bounded by ``_MAX_OBJECT_SPAN``.
    Returns ``(None, None)`` when nothing follows before a terminator.
    """
    i = cue_end
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return None, None
    term = _OBJECT_TERMINATOR_RE.search(text, i)
    end = term.start() if term is not None else n
    end = min(end, i + _MAX_OBJECT_SPAN)
    # Trim trailing whitespace.
    while end > i and text[end - 1].isspace():
        end -= 1
    if end <= i:
        return None, None
    return i, end


def parse_modal_sentence(text: str) -> ModalParse:
    """Parse one sentence span into modal/deontic construction cores.

    ``text`` is the EXACT sentence span, in its own local coordinate system.
    Deterministic: scan for closed-list modal cues (longest-first), and for each
    cue emit ONE modal core — classified into a deontic kind by (token, polarity),
    owning the cue surface span, the overt subject NP span (or marking the
    addressee underspecified for the impersonal/passive register), and the
    trailing object/complement span. Every other char is typed explicit residual.

    The bare ``on`` cue fires ONLY in the necessive construction (``on`` +
    ``-ttava/-tava/-tävä`` participle); a plain copula is skipped (mirrors the
    production necessive gate). Declines (typed residue, never a guessed parse)
    when NO core parses (the caller's family discriminator guarantees a cue for
    in-scope spans, so a decline here is the out-of-family / copula-only case).
    """
    n = len(text)
    cores: list[ModalCore] = []
    owned: list[tuple[int, int]] = []
    consumed_until = 0
    for m in _CUE_RE.finditer(text):
        if m.start() < consumed_until:
            # Overlapping cue already consumed by a longer/earlier match.
            continue
        token = m.group(0).casefold()
        if token not in _MARKER_POL_VOICE:
            continue
        if token == "on" and not _bare_on_is_necessive(text, m.end()):
            continue
        if token == "tulee" and _tulee_is_commencement(text, m.end()):
            # ``tulee voimaan`` (comes into force) is temporal, owned by the
            # temporal island — not the necessive obligation ``X:n tulee tehdä``.
            continue
        polarity, voice = _MARKER_POL_VOICE[token]
        kind = _refine_kind(token, polarity)

        cue_start, cue_end = m.start(), m.end()
        a_start, a_end, underspec = _addressee_span(text, cue_start, token)
        # Passive markers default to underspecified when no overt subject binds.
        if voice == VOICE_PASSIVE and (a_start is None):
            underspec = True
        o_start, o_end = _object_span(text, cue_end)

        cores.append(
            ModalCore(
                kind=kind,
                cue=token,
                cue_start=cue_start,
                cue_end=cue_end,
                polarity=polarity,
                voice=voice,
                addressee_start=a_start,
                addressee_end=a_end,
                addressee_underspecified=underspec,
                object_start=o_start,
                object_end=o_end,
            )
        )
        owned.append((cue_start, cue_end))
        if a_start is not None and a_end is not None:
            owned.append((a_start, a_end))
        if o_start is not None and o_end is not None:
            owned.append((o_start, o_end))
        consumed_until = o_end if o_end is not None else cue_end

    if not cores:
        return ModalParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            cores=(),
            residuals=(Residual(0, n, "no_modal_core"),),
            parser_lane=MODAL_LANE_DECLINED,
        )

    residuals = _fill_residuals(n, owned, "benign_uninterpreted_prose")
    return ModalParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="modal",
        cores=tuple(cores),
        residuals=tuple(residuals),
        parser_lane=MODAL_LANE_CONSTRUCTION_OWNED,
    )


def assert_total_ownership(mp: ModalParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of core cue spans, addressee spans, object spans, and the explicit
    residual spans must cover every char of the sentence with NO gap and NO
    silent drop. Raises ``AssertionError`` on violation.
    """
    n = mp.seg_end - mp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = []
    for c in mp.cores:
        spans.append((c.cue_start, c.cue_end))
        if c.addressee_start is not None and c.addressee_end is not None:
            spans.append((c.addressee_start, c.addressee_end))
        if c.object_start is not None and c.object_end is not None:
            spans.append((c.object_start, c.object_end))
    spans.extend((r.char_start, r.char_end) for r in mp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in sentence "
            f"(first gap at {missing[0]}); SILENT DROP. text={mp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: ModalParse -> [production actor_modal key]
# ---------------------------------------------------------------------------


def modal_key(token: str, polarity: str, voice: str) -> str:
    """Canonical census key for one modal core.

    Keyed on the load-bearing IDENTITY the production actor/modal primitive emits
    on its :class:`SurfaceModality`: the surface modal token, its polarity, and
    its voice. This is the SAME identity :mod:`modal_census` derives from the
    oracle's :func:`recognize_actor_modal_frames` frames, so the projected set is
    directly comparable to the production oracle for the same span.

    The construction-grammar deontic ``kind`` is NOT in the key: the production
    recognizer does not classify kind (surface-fact-only), so keying on kind
    would make every unit a superset. Kind is the family's own enrichment, used
    in the miss/superset shapes, not the comparison identity.
    """
    return f"{token}:{polarity}:{voice}"


def projection_modal_keys(mp: ModalParse) -> set[str]:
    """The projected modal set as canonical census keys."""
    return {modal_key(c.cue, c.polarity, c.voice) for c in mp.cores}
