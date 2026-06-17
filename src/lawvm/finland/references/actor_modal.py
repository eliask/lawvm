"""Surface-level actor/modal frame recognition (the H4 actor/modal lens).

This module implements the **H4 "actor/modal surface frame"** lens of the Legal
Surface Algebra. It scans Finnish statutory prose for the surface shape

    <ACTOR> <MODAL> [<OBJECT>]

— an institutional/role actor paired with a closed-list deontic-or-modal marker
("valtioneuvosto **antaa** asetuksen", "viranomainen **ei saa** ...", "asetuksella
**säädetään** ...") — and records it as a TYPED SURFACE FACT.

CRITICAL SAFETY BOUNDARY (non-negotiable, Pro r4):
====================================================
This layer records SURFACE FACTS ONLY. It NEVER emits a legal conclusion —
no "duty", no "discretion", no "power", no "obligation". The object of
"valtioneuvosto voi" is the surface fact

    SurfaceModality(token="voi", polarity="positive", voice="active")

and NOT "discretionary power". Legal interpretation begins in a LATER layer
that consumes these surface facts; this recognizer stops at

    typed surface fact + source span (+ a typed Residual for shapes it sees
    but cannot type safely).

It is consequently STANDALONE: it does not edit or depend on
``ref_mention_extractor.py``. The actor vocabulary is sourced READ-ONLY from
the existing :data:`lawvm.finland.canonical_actor_registry.REGISTRY` (institutional
actors: ministries, agencies, government levels) plus a small CLOSED list of
generic legal role-actors (``hakija``, ``viranomainen``, ``tuomioistuin`` …)
that the institutional registry does not carry.

Closed-list discipline (mirrors ``vague.py`` §1.11):
  - The modal marker set is a CLOSED, audited tuple. A token outside it never
    fires. New markers are added by editing the tuple, never by heuristic.
  - The generic role-actor set is likewise CLOSED.
  - Matching is longest-first so "ei saa" beats "saa" and "on velvollinen"
    beats "on".
  - A legally-significant shape the scanner sees but cannot type safely is
    emitted as a typed :class:`ActorModalResidual` — never silently dropped,
    never guessed into a frame.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import (
    expand_role_actor_phrases,
)

Polarity = Literal["positive", "negative"]
Voice = Literal["active", "passive"]

# ---------------------------------------------------------------------------
# Closed modal marker list (NORMATIVE)
# ---------------------------------------------------------------------------
#
# Each entry: surface token (exact lemma as it appears) -> (polarity, voice).
# These are SURFACE tokens, not legal categories. Glosses are descriptive of
# the SURFACE form only:
#
#   on             "is" (copula; surface obligation-shape in "on tehtävä")  active
#   tulee          "shall / must" (necessive)                               active
#   saa            "may"                                                     active
#   ei saa         "may not" (negated saa)                                  active  negative
#   voi            "can / may"                                              active
#   voidaan        "can / may" (passive)                                    passive
#   ei voida       "cannot" (negated voidaan)                              passive negative
#   on velvollinen "is obliged to" (surface; NOT typed as a duty here)      active
#   ei ole velvollinen  "is not obliged to"                                 active  negative
#   on oikeus      "has the right to" (surface; NOT typed as a power here)  active
#   ei ole oikeutta "has no right to"                                       active  negative
#   säädetään      "is provided (by statute)" (passive)                     passive
#   määrätään      "is ordered / prescribed" (passive)                      passive
#   annetaan       "is given / issued" (passive)                            passive
#   antaa          "gives / issues" (active counterpart of annetaan)        active
#   päättää        "decides"                                                active
#   on oikeutettu  "is entitled to" (surface)                               active
#
# polarity defaults to "positive"; only the explicitly ei-negated forms are
# "negative".
_MODAL_MARKERS: tuple[tuple[str, Polarity, Voice], ...] = (
    # negated forms FIRST conceptually; longest-first sort below handles ordering
    ("ei saa", "negative", "active"),
    ("ei voida", "negative", "passive"),
    ("ei ole velvollinen", "negative", "active"),
    ("ei ole oikeutta", "negative", "active"),
    ("on velvollinen", "positive", "active"),
    ("on oikeutettu", "positive", "active"),
    ("on oikeus", "positive", "active"),
    ("voidaan", "positive", "passive"),
    ("säädetään", "positive", "passive"),
    ("määrätään", "positive", "passive"),
    ("annetaan", "positive", "passive"),
    ("antaa", "positive", "active"),
    ("päättää", "positive", "active"),
    ("tulee", "positive", "active"),
    ("saa", "positive", "active"),
    ("voi", "positive", "active"),
    ("on", "positive", "active"),
)

#: Cheap substring pre-guards; if none of these tokens appears, no modal can match.
_MODAL_GUARDS: tuple[str, ...] = (
    "on",
    "tulee",
    "saa",
    "voi",
    "voidaan",
    "säädetään",
    "määrätään",
    "annetaan",
    "antaa",
    "päättää",
    "ei ",
)

# ---------------------------------------------------------------------------
# Closed generic role-actor list (NORMATIVE)
# ---------------------------------------------------------------------------
#
# The closed generic role/class actors NOT carried by the institutional registry
# are sourced from the shared :mod:`lawvm.finland.references.role_actors` module
# (imported above as ``_ROLE_ACTORS``). Surface forms include the nominative and
# the common genitive/partitive variants that head an actor noun phrase in legal
# prose. These role classes act as the grammatical subject of a modal.


def _build_actor_phrases() -> Tuple[str, ...]:
    """Union of registry phrase variants and closed role actors, longest-first.

    The institutional vocabulary is read READ-ONLY from the shared
    ``REGISTRY``; we do not mutate it. Role actors are the shared closed list,
    expanded with their sentence-initial capitalized variant (legal prose
    capitalizes a clause-leading actor: "Viranomainen ...").
    """
    phrases = set(REGISTRY.all_phrases_longest_first())
    phrases.update(expand_role_actor_phrases(_ROLE_ACTORS))
    return tuple(sorted(phrases, key=len, reverse=True))


_ACTOR_PHRASES_LONGEST_FIRST: Tuple[str, ...] = _build_actor_phrases()

# Compiled actor alternation (module scope). Word boundaries on both sides so
# that "kunta" does not match inside "kuntalainen". Finnish word chars include
# the ASCII set plus äöå/ÄÖÅ; \b handles the ASCII boundary and the trailing
# guard rejects an immediately-following Finnish letter.
_actor_alternation = "|".join(
    re.escape(phrase) for phrase in _ACTOR_PHRASES_LONGEST_FIRST
)
_ACTOR_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _actor_alternation + r")(?![\wäöåÄÖÅ])"
)

# Compiled modal alternation (module scope), longest-first.
_modal_lookup = {tok: (pol, voice) for tok, pol, voice in _MODAL_MARKERS}
_modal_phrases_longest_first = sorted(_modal_lookup.keys(), key=len, reverse=True)
_modal_alternation = "|".join(
    r"\s+".join(re.escape(word) for word in tok.split(" "))
    for tok in _modal_phrases_longest_first
)
_MODAL_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _modal_alternation + r")(?![\wäöåÄÖÅ])"
)

#: Maximum gap (in characters) between an actor head and the modal that may
#: still be read as the SAME surface frame. Beyond this the actor and modal are
#: treated as unrelated and no frame is emitted.
_MAX_ACTOR_MODAL_GAP = 60

#: Maximum object-span length (characters) captured after a modal. The object
#: is a SURFACE span only; it is not parsed.
_MAX_OBJECT_SPAN = 200


# ---------------------------------------------------------------------------
# Frozen output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SurfaceModality:
    """A surface deontic/modal marker. SURFACE FACT ONLY.

    This records the *form* of the marker, never its legal force. ``token="voi"``
    is the surface fact, not "discretionary power"; ``token="on velvollinen"`` is
    the surface fact, not "an obligation".

    Attributes:
        token:       The exact surface marker from the closed list (e.g. "ei saa").
        polarity:    "positive" or "negative" (negative only for ei-negated forms).
        voice:       "active" or "passive".
        source_span: Byte span of the marker in the source text.
    """

    token: str
    polarity: Polarity
    voice: Voice
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class ActorModalFrame:
    """A surface actor+modal frame. SURFACE FACT ONLY.

    Records that a known actor surface appears in close textual proximity before
    a closed-list modal marker, optionally with a trailing object span. This is
    NOT a duty/power/obligation assertion — interpretation happens downstream.

    Attributes:
        actor_surface: The matched actor surface phrase (verbatim).
        actor_span:    Byte span of the actor surface.
        modal:         The :class:`SurfaceModality` surface fact.
        object_span:   Byte span of the trailing object surface (or None).
        source_span:   Byte span covering the whole frame (actor start .. modal/object end).
        status:        Always "surface_fact_only".
        rule_id:       The recognizer rule that fired.
    """

    actor_surface: str
    actor_span: SourceSpan
    modal: SurfaceModality
    object_span: Optional[SourceSpan]
    source_span: SourceSpan
    status: Literal["surface_fact_only"]
    rule_id: str


# Residual classes: legally-significant shapes the recognizer SEES but cannot
# type into a frame safely. Never silent, never guessed.
ResidualKind = Literal[
    "modal_without_actor",
    "actor_without_modal",
    "ambiguous_actor",
]


@dataclass(frozen=True, slots=True)
class ActorModalResidual:
    """A typed residual: a seen-but-untypeable surface shape.

    Attributes:
        kind:         Which residual class fired.
        surface_text: The verbatim offending surface fragment.
        source_span:  Byte span of the offending fragment.
        detail:       Human-readable self-evidencing description (embeds the
                      offending text), e.g. for an ambiguous actor the candidate
                      canonical ids it could be.
    """

    kind: ResidualKind
    surface_text: str
    source_span: SourceSpan
    detail: str


@dataclass(frozen=True, slots=True)
class ActorModalScan:
    """The full result of scanning one text: typed frames + typed residuals."""

    frames: Tuple[ActorModalFrame, ...]
    residuals: Tuple[ActorModalResidual, ...]


# ---------------------------------------------------------------------------
# Recognizer
# ---------------------------------------------------------------------------

_RULE_ID = "fi.surface.actor_modal.v1"


def _span(source_file: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_file=source_file,
        byte_offset=start,
        byte_len=end - start,
    )


def _capture_object_span(text: str, after: int) -> Optional[Tuple[int, int]]:
    """Capture a trailing object surface span after a modal at offset ``after``.

    SURFACE ONLY: the object is the run of text up to the next clause terminator
    (``.``/``;``/``:``/newline) bounded by :data:`_MAX_OBJECT_SPAN`. Returns
    (start, end) or None if there is nothing but whitespace/terminator.
    """
    start = after
    # skip leading whitespace
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text):
        return None
    limit = min(len(text), start + _MAX_OBJECT_SPAN)
    end = start
    while end < limit and text[end] not in ".;:\n":
        end += 1
    # trim trailing whitespace
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


def recognize_actor_modal_frames(
    text: str, source_file: str = ""
) -> ActorModalScan:
    """Recognise surface actor/modal frames in ``text``.

    Returns an :class:`ActorModalScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a modal with no nearby actor, an actor with no nearby modal, and an
    ambiguous actor surface each become a typed :class:`ActorModalResidual`.

    The recognizer records SURFACE FACTS ONLY and never asserts legal force.
    """
    if not any(guard in text for guard in _MODAL_GUARDS):
        # No modal can fire. Still, a lone actor is not a frame, and with no
        # modal present we emit no residual for it (an actor alone outside any
        # modal context is not a "seen-but-untypeable frame" — it is just text).
        return ActorModalScan(frames=(), residuals=())

    actor_matches = list(_ACTOR_RE.finditer(text))
    modal_matches = list(_MODAL_RE.finditer(text))

    frames: List[ActorModalFrame] = []
    residuals: List[ActorModalResidual] = []

    consumed_actor_idx: set[int] = set()
    consumed_modal_idx: set[int] = set()

    # Pair each modal with the nearest preceding actor within the gap window.
    for m_idx, modal_m in enumerate(modal_matches):
        token = modal_m.group(0)
        # Normalise inter-word whitespace back to the canonical single-space token
        # so the lookup key matches (regex allowed \s+ between words).
        norm_token = re.sub(r"\s+", " ", token)
        pol, voice = _modal_lookup[norm_token]

        best_actor_idx: Optional[int] = None
        for a_idx, actor_m in enumerate(actor_matches):
            if a_idx in consumed_actor_idx:
                continue
            if actor_m.end() > modal_m.start():
                break  # actor not before this modal
            gap = modal_m.start() - actor_m.end()
            if gap <= _MAX_ACTOR_MODAL_GAP:
                best_actor_idx = a_idx  # keep advancing to the nearest

        if best_actor_idx is None:
            residuals.append(
                ActorModalResidual(
                    kind="modal_without_actor",
                    surface_text=norm_token,
                    source_span=_span(
                        source_file, modal_m.start(), modal_m.end()
                    ),
                    detail=(
                        f"modal marker {norm_token!r} with no known actor "
                        f"within {_MAX_ACTOR_MODAL_GAP} chars before it"
                    ),
                )
            )
            continue

        actor_m = actor_matches[best_actor_idx]
        actor_surface = actor_m.group(0)

        # Ambiguity check against the institutional registry. Role actors are not
        # in the registry; an unmatched registry lookup with a role-actor surface
        # is fine (not ambiguous). A registry phrase mapping to >1 canonical id
        # IS an ambiguous actor and must not be silently picked.
        _, candidates = REGISTRY.lookup(actor_surface)
        if len(candidates) > 1:
            residuals.append(
                ActorModalResidual(
                    kind="ambiguous_actor",
                    surface_text=actor_surface,
                    source_span=_span(
                        source_file, actor_m.start(), actor_m.end()
                    ),
                    detail=(
                        f"actor surface {actor_surface!r} is ambiguous across "
                        f"{len(candidates)} canonical actors: "
                        f"{', '.join(candidates)}"
                    ),
                )
            )
            consumed_modal_idx.add(m_idx)
            continue

        modal = SurfaceModality(
            token=norm_token,
            polarity=pol,
            voice=voice,
            source_span=_span(source_file, modal_m.start(), modal_m.end()),
        )

        obj = _capture_object_span(text, modal_m.end())
        object_span = (
            _span(source_file, obj[0], obj[1]) if obj is not None else None
        )

        frame_end = obj[1] if obj is not None else modal_m.end()
        frames.append(
            ActorModalFrame(
                actor_surface=actor_surface,
                actor_span=_span(source_file, actor_m.start(), actor_m.end()),
                modal=modal,
                object_span=object_span,
                source_span=_span(source_file, actor_m.start(), frame_end),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )
        consumed_actor_idx.add(best_actor_idx)
        consumed_modal_idx.add(m_idx)

    # Any actor that immediately precedes a modal within the gap window but was
    # never consumed would be unusual; we only emit actor_without_modal for an
    # actor that has NO modal anywhere after it within the gap window, to avoid
    # noise on ordinary descriptive prose. An actor consumed into a frame is
    # excluded.
    for a_idx, actor_m in enumerate(actor_matches):
        if a_idx in consumed_actor_idx:
            continue
        has_following_modal = any(
            0 <= (modal_m.start() - actor_m.end()) <= _MAX_ACTOR_MODAL_GAP
            for modal_m in modal_matches
        )
        if has_following_modal:
            # There was a modal close after this actor but it bound to a nearer
            # actor; this actor is genuinely seen-near-a-modal yet untyped.
            residuals.append(
                ActorModalResidual(
                    kind="actor_without_modal",
                    surface_text=actor_m.group(0),
                    source_span=_span(
                        source_file, actor_m.start(), actor_m.end()
                    ),
                    detail=(
                        f"actor surface {actor_m.group(0)!r} appears near a "
                        f"modal but did not bind to one (a nearer actor did)"
                    ),
                )
            )

    return ActorModalScan(
        frames=tuple(frames),
        residuals=tuple(residuals),
    )
