"""Surface-level sanction/consequence frame recognition (the H5 sanction lens).

This module implements the **H5 "sanction surface frame"** lens of the Legal
Surface Algebra. It is a sibling of the H4 actor/modal lens
(:mod:`lawvm.finland.references.actor_modal`) and of the delegation and
procedure lenses. It scans Finnish statutory prose for the surface shape of a
**consequence/penalty object**

    [<TARGET ACTOR>] <SANCTION NOUN/VERB> [<TRIGGER CONDITION>]

— a closed-vocabulary sanction marker ("rangaistaan", "tuomitaan sakkoon",
"uhkasakko", "peruuttaa luvan", "vahingonkorvaus" …) — and records it as a
TYPED SURFACE FACT.

CRITICAL SAFETY BOUNDARY (non-negotiable):
==========================================
This layer records SURFACE FACTS ONLY. It NEVER emits a legal conclusion —
no "guilt", no "culpability", no "liability", no "enforceable", no "punishable
as charged". The object of "rangaistaan" is the surface fact

    SanctionFrame(sanction_kind=SanctionKind.RANGAISTUS, ...)

and NOT "the defendant is culpable / liable / guilty". Legal interpretation
(who is actually liable, whether the sanction is enforceable) begins in a LATER
layer that consumes these surface facts; this recognizer stops at

    typed surface fact + source span (+ a typed SanctionResidual for shapes it
    sees but cannot type safely).

It is consequently STANDALONE: it does not edit or depend on
``ref_mention_extractor.py`` and is wired into no graph. The actor vocabulary,
where used to locate a sanction TARGET, is sourced READ-ONLY from the existing
:data:`lawvm.finland.canonical_actor_registry.REGISTRY` plus a small CLOSED list
of generic legal role-actors.

Closed-list discipline (mirrors ``actor_modal.py`` / ``vague.py``):
  - The sanction marker set is a CLOSED, audited tuple of (substring stem ->
    SanctionKind). A token outside it never fires as a frame.
  - Matching is longest-first so "uhkasakko" beats "sakko" and
    "seuraamusmaksu" is preferred over a bare fine.
  - A sanction-SHAPED token the scanner sees but cannot type into the closed
    kind set is emitted as a typed :class:`SanctionResidual` (self-evidencing,
    embedding the offending text) — never silently dropped, never guessed.

TOKEN-GRAMMAR SUBSTRATE (Phase 7 migration, decision B — re-baselined spans):
============================================================================
The marker is no longer a maximal ``[\\wäöåÄÖÅ]+`` regex run classified by
substring-anywhere. It is a ``word``-category TOKEN of the source-preserving
:class:`~lawvm.core.legal_surface_tokens.TokenTape`; its kind comes from matching
the closed sanction stems against a single token's ``Token.normalized`` (no
cross-boundary ``\\w`` run). This SPLITS digit-glued artifacts the old regex
collapsed: ``jos2sakko`` was ONE ``\\w`` run (matched the ``sakko`` substring
anywhere); on the tape it is ``jos`` | ``2`` | ``sakko``, three tokens, so the
SAKKO frame now classifies on the ``sakko`` TOKEN — an intended improvement.

Spans are token-aligned: ``marker_surface`` is the token's verbatim ``.text``
and the marker span is the token's ``.char_start``/``.char_end``. The
nearest-preceding-actor and trigger-span helpers operate on raw-text character
offsets unchanged (they bound prose, not vocabulary). ``MorphOverlay`` is NOT
used: it covers only structural heads (``laki``/``asetus``/``pykälä``), not
sanction stems (``rangaist``/``sakko``), so token-stem matching on ``.normalized``
stays primary.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Optional, Tuple

from lawvm.core.legal_surface_tokens import Token, TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.legal_surface.clause_segment import sentence_terminator_between
from lawvm.finland.legal_surface.tokenize import build_token_tape


class SanctionKind(Enum):
    """Closed set of recognised Finnish sanction/consequence kinds.

    These are SURFACE categories of the marker found, NOT legal conclusions.
    ``RANGAISTUS`` records that the *form* "rangaistaan / tuomitaan sakkoon"
    appears — never that anyone is punishable or culpable.
    """

    RANGAISTUS = "rangaistus"
    """Criminal punishment: 'rangaistaan', 'tuomitaan ... sakkoon/vankeuteen'."""

    SAKKO = "sakko"
    """Fine ('sakko') as a free-standing penalty noun/verb."""

    SEURAAMUSMAKSU = "seuraamusmaksu"
    """Administrative penalty payment ('seuraamusmaksu')."""

    UHKASAKKO = "uhkasakko"
    """Conditional fine ('uhkasakko')."""

    LUVAN_PERUUTTAMINEN = "luvan_peruuttaminen"
    """Revocation of a permit/licence ('peruuttaa luvan', 'luvan peruuttaminen')."""

    VAHINGONKORVAUS = "vahingonkorvaus"
    """Damages / compensation for harm ('vahingonkorvaus')."""


# ---------------------------------------------------------------------------
# Closed sanction marker list (NORMATIVE)
# ---------------------------------------------------------------------------
#
# Each entry: a SURFACE substring stem (lowercased, inflection-tolerant) ->
# SanctionKind. The stems are matched as substrings WITHIN A SINGLE word TOKEN
# (not across a maximal \w run), so inflected forms ("rangaistaan",
# "rangaistus", "rangaistakseen") all hit the "rangais" stem on the one token
# that carries them. Glosses are descriptive of the SURFACE form ONLY:
#
#   uhkasako / uhkasakko  conditional fine                  -> UHKASAKKO
#   seuraamusmaksu        administrative penalty payment    -> SEURAAMUSMAKSU
#   vahingonkorvau        damages                           -> VAHINGONKORVAUS
#   rangais               criminal punishment verb/noun     -> RANGAISTUS
#   tuomita / tuomitaan   "is sentenced to ..."             -> RANGAISTUS
#   peruutta + lupa-stem  permit revocation (compound rule) -> LUVAN_PERUUTTAMINEN
#   luvan peruutta        "revocation of the permit"        -> LUVAN_PERUUTTAMINEN
#   sako / sakko          fine                              -> SAKKO
#
# IMPORTANT longest-first ordering concerns:
#   - "uhkasakko" contains "sakko"; "uhkasako" stem must win -> UHKASAKKO, so it
#     sorts before the bare "sako"/"sakko" stem.
#   - "seuraamusmaksu" is independent of "sakko".
#   - "tuomitaan ... sakkoon" is a RANGAISTUS frame; the bare-"sako" arm only
#     fires when no RANGAISTUS/UHKASAKKO marker already claimed the token.
_SanctionStem = Tuple[str, SanctionKind]

_SANCTION_STEMS: tuple[_SanctionStem, ...] = (
    ("uhkasakko", SanctionKind.UHKASAKKO),
    ("uhkasako", SanctionKind.UHKASAKKO),
    ("seuraamusmaksu", SanctionKind.SEURAAMUSMAKSU),
    ("vahingonkorvau", SanctionKind.VAHINGONKORVAUS),
    ("rangais", SanctionKind.RANGAISTUS),
    ("tuomita", SanctionKind.RANGAISTUS),
    ("tuomitaan", SanctionKind.RANGAISTUS),
    ("sakko", SanctionKind.SAKKO),
    ("sako", SanctionKind.SAKKO),
)

#: Cheap substring pre-guards; if none of these tokens appears (lowercased), no
#: sanction marker can match. ``peruutta`` is paired with a permit stem at
#: recognition time (see :func:`_scan_permit_revocation`).
_SANCTION_GUARDS: tuple[str, ...] = (
    "rangais",
    "tuomit",
    "sako",
    "sakko",
    "seuraamusmaksu",
    "uhkasako",
    "uhkasakko",
    "peruutta",
    "vahingonkorvau",
)

# ---------------------------------------------------------------------------
# Permit-revocation compound rule
# ---------------------------------------------------------------------------
#
# "peruutta" (revoke) is only a SANCTION when paired with a permit/licence
# noun. The closed permit-noun stem list keeps "peruuttaa päätöksen" (revoke a
# decision) out of the sanction lens while admitting "peruuttaa luvan" /
# "luvan peruuttaminen" / "toimiluvan peruuttaminen".
_PERMIT_STEMS: tuple[str, ...] = (
    "lupa",
    "luvan",
    "luvas",  # luvasta / luvassa
    "luvol",  # luvolla
    "toimilup",
    "toimiluv",
)

# A "peruutta"-stem token that we SEE but for which no permit noun sits within
# this many characters either side becomes a typed residual rather than a guess.
_PERMIT_PROXIMITY = 60

#: Revocation-verb stems handled exclusively by the permit-revocation compound
#: rule (never by the bare sanction-stem arm).
_REVOKE_STEMS: tuple[str, ...] = ("peruutta", "peruute")

# ---------------------------------------------------------------------------
# Closed generic role-actor list (NORMATIVE)
# ---------------------------------------------------------------------------
#
# Generic legal role/class actors that can be the TARGET of a sanction and that
# the institutional registry does not carry. Surface forms include the common
# inflections that head a target noun phrase in penal/administrative prose.
_ROLE_ACTORS: tuple[str, ...] = (
    "elinkeinonharjoittaja",
    "elinkeinonharjoittajaa",
    "elinkeinonharjoittajan",
    "työnantaja",
    "työnantajaa",
    "työnantajan",
    "työntekijä",
    "työntekijää",
    "työntekijän",
    "rekisterinpitäjä",
    "rekisterinpitäjää",
    "rekisterinpitäjän",
    "yhtiö",
    "yhtiötä",
    "yhtiön",
    "luvanhaltija",
    "luvanhaltijaa",
    "luvanhaltijan",
    "toiminnanharjoittaja",
    "toiminnanharjoittajaa",
    "toiminnanharjoittajan",
    "asianosainen",
    "asianosaista",
    "asianosaisen",
    "hakija",
    "hakijaa",
    "hakijan",
)

# ---------------------------------------------------------------------------
# Trigger lead-ins (closed)
# ---------------------------------------------------------------------------
#
# A trigger condition is a SURFACE span introduced by a closed set of
# conditional/causal lead-in tokens ("joka", "jos", "mikäli", "milloin"). The
# captured trigger is the run of text after the lead-in up to a clause
# terminator. SURFACE ONLY: the trigger is not parsed into a legal condition.
_TRIGGER_LEADINS: tuple[str, ...] = (
    "joka",
    "jos",
    "mikäli",
    "milloin",
    "se, joka",
)


def _capitalize_first(word: str) -> str:
    if not word:
        return word
    return word[0].upper() + word[1:]


def _build_actor_phrases() -> Tuple[str, ...]:
    """Union of registry phrase variants and closed role actors, longest-first.

    The institutional vocabulary is read READ-ONLY from the shared ``REGISTRY``;
    we do not mutate it. Role actors are the closed local list, expanded with a
    sentence-initial capitalized variant.
    """
    phrases = set(REGISTRY.all_phrases_longest_first())
    for role in _ROLE_ACTORS:
        phrases.add(role)
        phrases.add(_capitalize_first(role))
    return tuple(sorted(phrases, key=len, reverse=True))


_ACTOR_PHRASES_LONGEST_FIRST: Tuple[str, ...] = _build_actor_phrases()

_actor_alternation = "|".join(
    re.escape(phrase) for phrase in _ACTOR_PHRASES_LONGEST_FIRST
)
_ACTOR_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _actor_alternation + r")(?![\wäöåÄÖÅ])"
)

_TRIGGER_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:"
    + "|".join(r"\s+".join(re.escape(w) for w in t.split(" ")) for t in _TRIGGER_LEADINS)
    + r")(?![\wäöåÄÖÅ])",
    re.IGNORECASE,
)

#: Maximum gap (chars) between a target actor head and the sanction marker that
#: may still be read as the SAME surface frame.
_MAX_TARGET_GAP = 80

#: Maximum trigger-span length (chars) captured.
_MAX_TRIGGER_SPAN = 240


# ---------------------------------------------------------------------------
# Frozen output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SanctionFrame:
    """A surface sanction/consequence frame. SURFACE FACT ONLY.

    Records that a closed-vocabulary sanction marker appears, optionally with a
    nearby TARGET actor (who is sanctioned) and/or a TRIGGER condition span.
    This is NOT a culpability/liability/enforceability assertion —
    interpretation happens downstream.

    Attributes:
        sanction_kind:    The closed :class:`SanctionKind` the marker typed to.
        marker_surface:   The matched sanction surface token (verbatim ``.text``).
        target_actor_span: Byte span of the sanctioned actor surface, or None.
        trigger_span:     Byte span of the trigger-condition surface, or None.
        source_span:      Byte span covering the whole frame.
        status:           Always "surface_fact_only".
        rule_id:          The recognizer rule that fired.
    """

    sanction_kind: SanctionKind
    marker_surface: str
    target_actor_span: Optional[SourceSpan]
    trigger_span: Optional[SourceSpan]
    source_span: SourceSpan
    sanction_status: Literal["surface_fact_only"]
    rule_id: str


# Residual classes: sanction-shaped shapes the recognizer SEES but cannot type.
ResidualKind = Literal[
    "untypeable_sanction_token",
    "revoke_without_permit",
]


@dataclass(frozen=True, slots=True)
class SanctionResidual:
    """A typed residual: a seen-but-untypeable sanction-shaped surface shape.

    Attributes:
        kind:         Which residual class fired.
        surface_text: The verbatim offending surface fragment.
        source_span:  Byte span of the offending fragment.
        detail:       Self-evidencing description embedding the offending text.
    """

    kind: ResidualKind
    surface_text: str
    source_span: SourceSpan
    detail: str


@dataclass(frozen=True, slots=True)
class SanctionScan:
    """The full result of scanning one text: typed frames + typed residuals."""

    frames: Tuple[SanctionFrame, ...]
    residuals: Tuple[SanctionResidual, ...]


# ---------------------------------------------------------------------------
# Recognizer
# ---------------------------------------------------------------------------

_RULE_ID = "fi.surface.sanction.v1"


def _span(source_file: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_file=source_file,
        byte_offset=start,
        byte_len=end - start,
    )


def sanction_kind(normalized_token: str) -> Optional[SanctionKind]:
    """Return the SanctionKind for ONE token's ``normalized`` surface.

    The stem table is iterated in declared order, which is longest/most-specific
    first for the overlapping cases (uhkasakko before sako). A stem matches if it
    is a substring of THIS SINGLE TOKEN's normalized text — NOT a substring
    anywhere in a maximal ``\\w`` run, so a digit-glued ``jos2sakko`` (three
    tape tokens) classifies on its ``sakko`` token alone. Returns None if no
    stem is a substring of the token. The revocation stems are excluded — those
    are handled only by the permit-revocation compound rule.
    """
    if any(stem in normalized_token for stem in _REVOKE_STEMS):
        return None
    for stem, kind in _SANCTION_STEMS:
        if stem in normalized_token:
            return kind
    return None


def _is_sanction_shaped(normalized_token: str) -> bool:
    """A token is 'sanction-shaped' if its normalized surface trips a guard.

    Used to decide whether an unclassifiable word TOKEN is a typed residual (it
    looked like a sanction but did not type) versus ordinary prose (ignored).
    """
    return any(guard in normalized_token for guard in _SANCTION_GUARDS)


def _coerce_tape(text: str, tape: Optional[TokenTape]) -> TokenTape:
    """Return the supplied tape, or build one over ``text`` on demand."""
    if isinstance(tape, TokenTape):
        return tape
    return build_token_tape("sanction#text", text)


def marker_surface(token: Token) -> str:
    """The verbatim marker surface for a sanction word token (its ``.text``)."""
    return token.text


def _nearest_preceding_actor(
    actor_matches: Tuple[re.Match[str], ...],
    actor_ends: Tuple[int, ...],
    marker_start: int,
    tokens: Tuple[Token, ...],
) -> Optional[re.Match[str]]:
    """The closest known actor surface ending within the gap window before a marker.

    SAME-SENTENCE GUARD: the target actor must live in the SAME sentence as the
    sanction marker. A bare char-gap fuses a target at the tail of sentence N with
    a sanction marker in sentence N+1 ("... koko. Erityisestä syystä voidaan
    hyvityssakko ...") into one fabricated frame. When a sentence terminator falls
    between the actor end and the marker start, the actor is not this marker's
    target (the marker simply has no in-sentence target).
    """
    index = bisect_right(actor_ends, marker_start) - 1
    if index < 0:
        return None
    actor_m = actor_matches[index]
    gap = marker_start - actor_m.end()
    if not (0 <= gap <= _MAX_TARGET_GAP):
        return None
    if sentence_terminator_between(tokens, actor_m.end(), marker_start):
        return None
    return actor_m


#: Sentence terminators that bound the trigger search to the marker's own
#: sentence. A trigger lead-in beyond one of these (in either direction) belongs
#: to a DIFFERENT sentence and must not be pulled into this marker's frame.
_SENTENCE_TERMINATORS = ".;:\n"


def _sentence_bounds(text: str, frame_lo: int, frame_hi: int) -> Tuple[int, int]:
    """Return (lo, hi) of the sentence containing the marker frame.

    ``lo`` is just after the nearest sentence terminator before ``frame_lo`` (or
    0); ``hi`` is at the nearest sentence terminator at/after ``frame_hi`` (or
    end of text). The marker's trigger must live inside this window — never
    across a sentence boundary.
    """
    lo = frame_lo
    while lo > 0 and text[lo - 1] not in _SENTENCE_TERMINATORS:
        lo -= 1
    hi = frame_hi
    n = len(text)
    while hi < n and text[hi] not in _SENTENCE_TERMINATORS:
        hi += 1
    return (lo, hi)


def _capture_trigger_span(
    text: str, frame_lo: int, frame_hi: int
) -> Optional[Tuple[int, int]]:
    """Capture a trigger-condition surface span in the marker's OWN sentence.

    A trigger is the run after a closed lead-in token ("joka"/"jos"/…) that sits
    in the SAME sentence as the sanction marker. The search window is clamped to
    the marker's sentence bounds (nearest sentence terminator on each side), so a
    lead-in in a different following/preceding sentence is never captured. SURFACE
    ONLY: the run is bounded by a clause terminator and :data:`_MAX_TRIGGER_SPAN`.
    Returns (start, end) of the run AFTER the lead-in, or None.
    """
    # search window: the marker's own sentence (never crossing a sentence
    # boundary), additionally capped by _MAX_TRIGGER_SPAN on each side.
    sent_lo, sent_hi = _sentence_bounds(text, frame_lo, frame_hi)
    window_lo = max(sent_lo, frame_lo - _MAX_TRIGGER_SPAN)
    window_hi = min(sent_hi, frame_hi + _MAX_TRIGGER_SPAN)
    if window_hi <= window_lo:
        return None
    segment = text[window_lo:window_hi]
    lead = _TRIGGER_RE.search(segment)
    if lead is None:
        return None
    start = window_lo + lead.end()
    while start < sent_hi and text[start].isspace():
        start += 1
    if start >= sent_hi:
        return None
    # the trigger run stays inside the sentence; cap by _MAX_TRIGGER_SPAN too.
    limit = min(sent_hi, start + _MAX_TRIGGER_SPAN)
    end = start
    while end < limit and text[end] not in _SENTENCE_TERMINATORS:
        end += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


def _build_frame(
    text: str,
    source_file: str,
    actor_matches: Tuple[re.Match[str], ...],
    actor_ends: Tuple[int, ...],
    tokens: Tuple[Token, ...],
    kind: SanctionKind,
    surface: str,
    marker_lo: int,
    marker_hi: int,
) -> SanctionFrame:
    """Assemble a SanctionFrame around a typed marker token span.

    Locates the nearest preceding target actor (constrained to the marker's own
    sentence) and a nearby trigger condition (both raw-text-offset surface
    helpers), and spans the whole frame from the earliest of {actor, marker} to
    the latest of {marker, trigger}.
    """
    actor_m = _nearest_preceding_actor(
        actor_matches, actor_ends, marker_lo, tokens
    )
    target_span = (
        _span(source_file, actor_m.start(), actor_m.end())
        if actor_m is not None
        else None
    )
    frame_lo = actor_m.start() if actor_m is not None else marker_lo
    trig = _capture_trigger_span(text, frame_lo, marker_hi)
    trigger_span = (
        _span(source_file, trig[0], trig[1]) if trig is not None else None
    )
    frame_hi = max(marker_hi, trig[1] if trig is not None else marker_hi)
    return SanctionFrame(
        sanction_kind=kind,
        marker_surface=surface,
        target_actor_span=target_span,
        trigger_span=trigger_span,
        source_span=_span(source_file, min(frame_lo, marker_lo), frame_hi),
        sanction_status="surface_fact_only",
        rule_id=_RULE_ID,
    )


def _scan_permit_revocation(
    text: str,
    source_file: str,
    tape: TokenTape,
    actor_matches: Tuple[re.Match[str], ...],
    actor_ends: Tuple[int, ...],
) -> Tuple[List[SanctionFrame], List[SanctionResidual], set[int]]:
    """Recognise permit-revocation frames from the 'peruutta' compound rule.

    Returns (frames, residuals, consumed_token_indices). A ``word`` token whose
    normalized surface carries a revocation stem with a permit noun within
    :data:`_PERMIT_PROXIMITY` chars is a LUVAN_PERUUTTAMINEN frame; one without
    is a typed ``revoke_without_permit`` residual (never guessed). The consumed
    token indices let the main word loop skip these tokens.
    """
    frames: List[SanctionFrame] = []
    residuals: List[SanctionResidual] = []
    consumed: set[int] = set()

    for idx, tok in enumerate(tape.tokens):
        if tok.category != "word":
            continue
        norm = tok.normalized
        if not any(stem in norm for stem in _REVOKE_STEMS):
            continue
        consumed.add(idx)
        lo = max(0, tok.char_start - _PERMIT_PROXIMITY)
        hi = min(len(text), tok.char_end + _PERMIT_PROXIMITY)
        nearby_lower = text[lo:hi].lower()
        has_permit = any(stem in nearby_lower for stem in _PERMIT_STEMS)
        if not has_permit:
            residuals.append(
                SanctionResidual(
                    kind="revoke_without_permit",
                    surface_text=tok.text,
                    source_span=_span(source_file, tok.char_start, tok.char_end),
                    detail=(
                        f"revocation-shaped token {tok.text!r} has no permit "
                        f"noun within {_PERMIT_PROXIMITY} chars; not typed as "
                        f"LUVAN_PERUUTTAMINEN"
                    ),
                )
            )
            continue
        frames.append(
            _build_frame(
                text,
                source_file,
                actor_matches,
                actor_ends,
                tape.tokens,
                SanctionKind.LUVAN_PERUUTTAMINEN,
                tok.text,
                tok.char_start,
                tok.char_end,
            )
        )
    return frames, residuals, consumed


def recognize_sanction_frames(
    text: str, source_file: str = "", *, tape: Optional[TokenTape] = None
) -> SanctionScan:
    """Recognise surface sanction/consequence frames in ``text``.

    Returns a :class:`SanctionScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a sanction-shaped ``word`` token that cannot be typed to the closed
    :class:`SanctionKind` set, and a revocation token with no nearby permit
    noun, each become a typed :class:`SanctionResidual`.

    Token-grammar substrate: the marker is a ``word``-category token of the
    source-preserving :class:`TokenTape`. A caller may pass a prebuilt ``tape``
    (the lens feeds ``unit.token_tape``); otherwise one is built over ``text``.
    Kind comes from matching closed sanction stems against a SINGLE token's
    ``normalized`` (no cross-boundary ``\\w`` run); spans are token-aligned.

    The recognizer records SURFACE FACTS ONLY and never asserts culpability,
    liability, guilt, or enforceability.
    """
    lower_text = text.lower()
    if not any(guard in lower_text for guard in _SANCTION_GUARDS):
        return SanctionScan(frames=(), residuals=())

    tape = _coerce_tape(text, tape)
    actor_matches = tuple(_ACTOR_RE.finditer(text))
    actor_ends = tuple(match.end() for match in actor_matches)

    frames: List[SanctionFrame] = []
    residuals: List[SanctionResidual] = []

    # Permit-revocation compound rule first (it consumes 'peruutta'/'peruute'
    # word tokens by index).
    perm_frames, perm_residuals, consumed = _scan_permit_revocation(
        text,
        source_file,
        tape,
        actor_matches,
        actor_ends,
    )
    frames.extend(perm_frames)
    residuals.extend(perm_residuals)

    for idx, tok in enumerate(tape.tokens):
        if tok.category != "word":
            continue
        if idx in consumed:
            continue
        norm = tok.normalized
        if not _is_sanction_shaped(norm):
            continue
        # revocation stems are handled exclusively by the compound rule above
        # (and excluded by sanction_kind too); skip them here to avoid an
        # untypeable residual for a token the compound rule already owns.
        if any(stem in norm for stem in _REVOKE_STEMS):
            continue
        kind = sanction_kind(norm)
        if kind is None:
            # Sanction-shaped but untypeable -> typed residual, never a guess.
            residuals.append(
                SanctionResidual(
                    kind="untypeable_sanction_token",
                    surface_text=tok.text,
                    source_span=_span(source_file, tok.char_start, tok.char_end),
                    detail=(
                        f"sanction-shaped token {tok.text!r} matched a guard but "
                        f"no closed sanction stem; not typed to any SanctionKind"
                    ),
                )
            )
            continue

        frames.append(
            _build_frame(
                text,
                source_file,
                actor_matches,
                actor_ends,
                tape.tokens,
                kind,
                tok.text,
                tok.char_start,
                tok.char_end,
            )
        )

    return SanctionScan(frames=tuple(frames), residuals=tuple(residuals))
