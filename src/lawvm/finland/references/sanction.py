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
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Optional, Tuple

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY


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
# SanctionKind. The stems are matched as substrings on word-ish boundaries so
# inflected forms ("rangaistaan", "rangaistus", "rangaistakseen") all hit the
# "rangais" stem. Glosses are descriptive of the SURFACE form ONLY:
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

# Sanction-shaped token regex: a maximal Finnish word run. Each matched word is
# classified against the closed stem table; a word that LOOKS sanction-shaped
# (passes a guard) but resolves to no stem becomes a residual.
_WORD_RE = re.compile(r"[\wäöåÄÖÅ]+")

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
        marker_surface:   The matched sanction surface token (verbatim).
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
    status: Literal["surface_fact_only"]
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


def _classify_word(lower_word: str) -> Optional[SanctionKind]:
    """Return the SanctionKind for a lowercased word, longest-stem-first.

    The stem table is iterated in declared order, which is longest/most-specific
    first for the overlapping cases (uhkasakko before sako). Returns None if no
    stem is a substring of the word.
    """
    for stem, kind in _SANCTION_STEMS:
        if stem in lower_word:
            return kind
    return None


def _is_sanction_shaped(lower_word: str) -> bool:
    """A word is 'sanction-shaped' if it trips any closed guard substring.

    Used to decide whether an unclassifiable word is a typed residual (it looked
    like a sanction but did not type) versus ordinary prose (ignored).
    """
    return any(guard in lower_word for guard in _SANCTION_GUARDS)


def _nearest_preceding_actor(
    text: str, marker_start: int
) -> Optional[re.Match[str]]:
    """The closest known actor surface ending within the gap window before a marker."""
    best: Optional[re.Match[str]] = None
    for actor_m in _ACTOR_RE.finditer(text):
        if actor_m.end() > marker_start:
            break
        gap = marker_start - actor_m.end()
        if 0 <= gap <= _MAX_TARGET_GAP:
            best = actor_m  # keep advancing to the nearest
    return best


def _capture_trigger_span(
    text: str, frame_lo: int, frame_hi: int
) -> Optional[Tuple[int, int]]:
    """Capture a trigger-condition surface span associated with a marker.

    A trigger is the run after a closed lead-in token ("joka"/"jos"/…) that sits
    near the sanction marker (within the same sentence-ish window). SURFACE
    ONLY: the run is bounded by a clause terminator and :data:`_MAX_TRIGGER_SPAN`.
    Returns (start, end) of the run AFTER the lead-in, or None.
    """
    # search window: from a little before the frame to the end-of-clause after it
    window_lo = max(0, frame_lo - _MAX_TRIGGER_SPAN)
    window_hi = min(len(text), frame_hi + _MAX_TRIGGER_SPAN)
    segment = text[window_lo:window_hi]
    lead = _TRIGGER_RE.search(segment)
    if lead is None:
        return None
    start = window_lo + lead.end()
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return None
    limit = min(len(text), start + _MAX_TRIGGER_SPAN)
    end = start
    while end < limit and text[end] not in ".;:\n":
        end += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


def _scan_permit_revocation(
    text: str, source_file: str
) -> Tuple[List[SanctionFrame], List[SanctionResidual], List[Tuple[int, int]]]:
    """Recognise permit-revocation frames from the 'peruutta' compound rule.

    Returns (frames, residuals, consumed_spans). A "peruutta"-stem word with a
    permit noun within :data:`_PERMIT_PROXIMITY` is a LUVAN_PERUUTTAMINEN frame;
    one without is a typed ``revoke_without_permit`` residual (never guessed).
    The consumed spans let the main word loop skip these words.
    """
    frames: List[SanctionFrame] = []
    residuals: List[SanctionResidual] = []
    consumed: List[Tuple[int, int]] = []

    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        lower = word.lower()
        if "peruutta" not in lower and "peruute" not in lower:
            continue
        consumed.append((m.start(), m.end()))
        lo = max(0, m.start() - _PERMIT_PROXIMITY)
        hi = min(len(text), m.end() + _PERMIT_PROXIMITY)
        nearby_lower = text[lo:hi].lower()
        has_permit = any(stem in nearby_lower for stem in _PERMIT_STEMS)
        if not has_permit:
            residuals.append(
                SanctionResidual(
                    kind="revoke_without_permit",
                    surface_text=word,
                    source_span=_span(source_file, m.start(), m.end()),
                    detail=(
                        f"revocation-shaped token {word!r} has no permit noun "
                        f"within {_PERMIT_PROXIMITY} chars; not typed as "
                        f"LUVAN_PERUUTTAMINEN"
                    ),
                )
            )
            continue
        actor_m = _nearest_preceding_actor(text, m.start())
        target_span = (
            _span(source_file, actor_m.start(), actor_m.end())
            if actor_m is not None
            else None
        )
        frame_lo = actor_m.start() if actor_m is not None else m.start()
        trig = _capture_trigger_span(text, frame_lo, m.end())
        trigger_span = (
            _span(source_file, trig[0], trig[1]) if trig is not None else None
        )
        frame_hi = max(m.end(), trig[1] if trig is not None else m.end())
        frames.append(
            SanctionFrame(
                sanction_kind=SanctionKind.LUVAN_PERUUTTAMINEN,
                marker_surface=word,
                target_actor_span=target_span,
                trigger_span=trigger_span,
                source_span=_span(source_file, min(frame_lo, m.start()), frame_hi),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )
    return frames, residuals, consumed


def recognize_sanction_frames(
    text: str, source_file: str = ""
) -> SanctionScan:
    """Recognise surface sanction/consequence frames in ``text``.

    Returns a :class:`SanctionScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a sanction-shaped token that cannot be typed to the closed
    :class:`SanctionKind` set, and a revocation token with no nearby permit
    noun, each become a typed :class:`SanctionResidual`.

    The recognizer records SURFACE FACTS ONLY and never asserts culpability,
    liability, guilt, or enforceability.
    """
    lower_text = text.lower()
    if not any(guard in lower_text for guard in _SANCTION_GUARDS):
        return SanctionScan(frames=(), residuals=())

    frames: List[SanctionFrame] = []
    residuals: List[SanctionResidual] = []

    # Permit-revocation compound rule first (it consumes 'peruutta' words).
    perm_frames, perm_residuals, consumed_spans = _scan_permit_revocation(
        text, source_file
    )
    frames.extend(perm_frames)
    residuals.extend(perm_residuals)
    consumed = set(consumed_spans)

    for m in _WORD_RE.finditer(text):
        key = (m.start(), m.end())
        if key in consumed:
            continue
        word = m.group(0)
        lower = word.lower()
        if not _is_sanction_shaped(lower):
            continue
        # "peruutta"/"peruute" are handled exclusively by the compound rule.
        if "peruutta" in lower or "peruute" in lower:
            continue
        kind = _classify_word(lower)
        if kind is None:
            # Sanction-shaped but untypeable -> typed residual, never a guess.
            residuals.append(
                SanctionResidual(
                    kind="untypeable_sanction_token",
                    surface_text=word,
                    source_span=_span(source_file, m.start(), m.end()),
                    detail=(
                        f"sanction-shaped token {word!r} matched a guard but no "
                        f"closed sanction stem; not typed to any SanctionKind"
                    ),
                )
            )
            continue

        actor_m = _nearest_preceding_actor(text, m.start())
        target_span = (
            _span(source_file, actor_m.start(), actor_m.end())
            if actor_m is not None
            else None
        )
        frame_lo = actor_m.start() if actor_m is not None else m.start()
        trig = _capture_trigger_span(text, frame_lo, m.end())
        trigger_span = (
            _span(source_file, trig[0], trig[1]) if trig is not None else None
        )
        frame_hi = max(m.end(), trig[1] if trig is not None else m.end())
        frames.append(
            SanctionFrame(
                sanction_kind=kind,
                marker_surface=word,
                target_actor_span=target_span,
                trigger_span=trigger_span,
                source_span=_span(
                    source_file, min(frame_lo, m.start()), frame_hi
                ),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )

    return SanctionScan(frames=tuple(frames), residuals=tuple(residuals))
