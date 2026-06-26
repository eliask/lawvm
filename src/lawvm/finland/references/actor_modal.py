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

TOKEN-NATIVE REWRITE (decision B)
=================================
This recognizer is a TOKEN/GRAMMAR recognizer over a :class:`TokenTape`, NOT a
regex over raw text. The actor vocabulary is matched by the shared
:class:`lawvm.finland.references.token_actor_match.TokenActorMatcher` (consecutive
verbatim ``Token.text`` runs, case-sensitive, longest-first); the modal markers
are matched by the same matcher over the closed modal phrase set; nearest-actor
pairing, object capture and gap/clause windows are TOKEN-INDEX / token-char-offset
operations. Emitted spans are whole-token aligned (re-baselined vs. the old
char-regex spans — expected and accepted). The frame PAYLOAD shape is UNCHANGED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.legal_surface_tokens import Token, TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.legal_surface.clause_segment import sentence_terminator_between
from lawvm.finland.references.role_actors import (
    ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import (
    expand_role_actor_phrases,
)
from lawvm.finland.references.token_actor_match import (
    TokenActorMatcher,
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
#   on             necessive shape ONLY: "on" + a -ttava/-tava/-tävä           active
#                  participle ("on tehtävä", "on toimitettava"). A bare copula
#                  ("X on Y", "joka on tehty ...") is NOT a modal and does not
#                  fire — see the necessive gate in _scan_tape.
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

# Token-native actor matcher (module scope). Token boundaries give the
# word-boundary guarantee the old regex spelled out with lookarounds: "kunta"
# cannot match inside the single "kuntalainen" word token. Matching is
# case-sensitive (verbatim Token.text), so the registry's case discipline holds.
_ACTOR_MATCHER = TokenActorMatcher(_ACTOR_PHRASES_LONGEST_FIRST)

# Modal-marker token matcher (module scope), longest-first over the closed modal
# phrase set. Multi-word markers ("ei saa", "on velvollinen") span several tokens;
# the matcher reconstructs them from consecutive verbatim Token.text runs, so the
# inter-word separator is the single space the tokenizer preserves.
_modal_lookup = {tok: (pol, voice) for tok, pol, voice in _MODAL_MARKERS}
_MODAL_MATCHER = TokenActorMatcher(tuple(_modal_lookup.keys()))

#: The bare ``on`` marker is a genuine deontic surface ONLY in the necessive
#: construction ``on`` + a passive necessive participle (``-ttava``/``-tava``/
#: ``-tävä``: "on tehtävä", "on toimitettava", "on annettava"). A plain copula
#: ("Viranomainen on toimivaltainen", "X on Y") or a relative-clause copula
#: ("päätös, joka on tehty esittelystä") is NOT a modal and must not fire. The
#: participle ends in a long vowel ``a``/``ä`` after a ``v``; require the ``ttav``
#: / ``tav`` / ``täv`` cluster so plain adjectives/past participles (``tehty``,
#: ``toimivaltainen``) do not qualify.
_NECESSIVE_PARTICIPLE_RE = re.compile(
    r"^\w*t[aä]v[aä]$", re.IGNORECASE
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

    This records the *form* of the marker, never its legal force. a ``token`` of
    ``"voi"`` is the surface fact, not "discretionary power"; ``"on velvollinen"``
    is the surface fact, not "an obligation".

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
    actor_status: Literal["surface_fact_only"]
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


#: Clause terminators (token-side): a ``punct`` token whose text is one of these,
#: or any ``whitespace`` token containing a newline, bounds the object window.
_CLAUSE_TERMINATOR_PUNCT = frozenset(".;:")


def _is_terminator(tok: Token) -> bool:
    if tok.category == "punct" and tok.text in _CLAUSE_TERMINATOR_PUNCT:
        return True
    if tok.category == "whitespace" and "\n" in tok.text:
        return True
    return False


def _capture_object_span(
    tokens: Tuple[Token, ...], after_index: int
) -> Optional[Tuple[int, int]]:
    """Capture a trailing object surface span after the modal end token.

    SURFACE ONLY: the run of tokens from ``after_index`` up to (not including) the
    next clause-terminator token, bounded by :data:`_MAX_OBJECT_SPAN` characters
    from the run start. Returns (char_start, char_end) or None if empty. Leading
    and trailing whitespace tokens are trimmed; the span is whole-token aligned.
    """
    n = len(tokens)
    i = after_index
    # skip leading whitespace
    while i < n and tokens[i].category == "whitespace":
        i += 1
    if i >= n or _is_terminator(tokens[i]):
        return None
    char_start = tokens[i].char_start
    limit = char_start + _MAX_OBJECT_SPAN
    last_nonspace_end: Optional[int] = None
    j = i
    while j < n and not _is_terminator(tokens[j]):
        tok = tokens[j]
        if tok.char_start >= limit:
            break
        if tok.category != "whitespace":
            last_nonspace_end = tok.char_end
        j += 1
    if last_nonspace_end is None:
        return None
    return (char_start, last_nonspace_end)


def _on_is_necessive(tokens: Tuple[Token, ...], after_index: int) -> bool:
    """True iff the word following a bare ``on`` is a passive necessive participle.

    Gates the bare ``on`` marker: only ``on`` + ``-ttava/-tava/-tävä`` ("on
    tehtävä", "on toimitettava") is a deontic surface. A plain copula
    ("X on Y") or a relative-clause copula ("joka on tehty ...") fails this gate
    and emits no frame. ``after_index`` is the token index just past ``on``.
    """
    n = len(tokens)
    j = after_index
    while j < n and tokens[j].category == "whitespace":
        j += 1
    if j >= n or tokens[j].category != "word":
        return False
    return _NECESSIVE_PARTICIPLE_RE.match(tokens[j].text) is not None


def _scan_tape(tape: TokenTape, source_file: str) -> ActorModalScan:
    tokens = tape.tokens

    actor_matches = _ACTOR_MATCHER.find_all(tokens)
    modal_matches = _MODAL_MATCHER.find_all(tokens)
    if not modal_matches:
        # No modal can fire. A lone actor is not a frame and emits no residual.
        return ActorModalScan(frames=(), residuals=())

    frames: List[ActorModalFrame] = []
    residuals: List[ActorModalResidual] = []

    consumed_actor_idx: set[int] = set()

    # Pair each modal with the nearest preceding actor within the gap window. The
    # gap is measured in source characters between the actor end and the modal
    # start (the same window the char-regex recognizer used).
    for modal_m in modal_matches:
        norm_token = modal_m.surface
        pol, voice = _modal_lookup[norm_token]

        # Bare ``on`` is a deontic surface ONLY in the necessive construction
        # (``on`` + ``-ttava/-tava/-tävä`` participle). A plain copula ("X on Y")
        # or a relative-clause copula ("joka on tehty ...") is not a modal — it
        # is demoted to residual (no frame, no bound actor). The longer ``on
        # velvollinen`` / ``on oikeus`` / ``on oikeutettu`` markers are distinct
        # tokens and are unaffected.
        if norm_token == "on" and not _on_is_necessive(tokens, modal_m.end_index):
            continue

        best_actor_idx: Optional[int] = None
        for a_idx, actor_m in enumerate(actor_matches):
            if a_idx in consumed_actor_idx:
                continue
            if actor_m.char_end > modal_m.char_start:
                break  # actor not before this modal
            gap = modal_m.char_start - actor_m.char_end
            if gap > _MAX_ACTOR_MODAL_GAP:
                continue
            # SAME-SENTENCE GUARD: an actor may bind a modal only when NO sentence
            # boundary separates them. The char-gap window alone fuses an actor at
            # the tail of sentence N with the modal of sentence N+1 ("... määräajan.
            # Päätös voidaan ...") into one fabricated frame. Refuse the pairing
            # when a sentence terminator falls between the actor end and the modal
            # start; the actor stays unbound (a typed residual below).
            if sentence_terminator_between(
                tokens, actor_m.char_end, modal_m.char_start
            ):
                continue
            best_actor_idx = a_idx  # keep advancing to the nearest

        if best_actor_idx is None:
            residuals.append(
                ActorModalResidual(
                    kind="modal_without_actor",
                    surface_text=norm_token,
                    source_span=_span(
                        source_file, modal_m.char_start, modal_m.char_end
                    ),
                    detail=(
                        f"modal marker {norm_token!r} with no known actor "
                        f"within {_MAX_ACTOR_MODAL_GAP} chars before it"
                    ),
                )
            )
            continue

        actor_m = actor_matches[best_actor_idx]
        actor_surface = actor_m.surface

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
                        source_file, actor_m.char_start, actor_m.char_end
                    ),
                    detail=(
                        f"actor surface {actor_surface!r} is ambiguous across "
                        f"{len(candidates)} canonical actors: "
                        f"{', '.join(candidates)}"
                    ),
                )
            )
            continue

        modal = SurfaceModality(
            token=norm_token,
            polarity=pol,
            voice=voice,
            source_span=_span(source_file, modal_m.char_start, modal_m.char_end),
        )

        obj = _capture_object_span(tokens, modal_m.end_index)
        object_span = (
            _span(source_file, obj[0], obj[1]) if obj is not None else None
        )

        frame_end = obj[1] if obj is not None else modal_m.char_end
        frames.append(
            ActorModalFrame(
                actor_surface=actor_surface,
                actor_span=_span(
                    source_file, actor_m.char_start, actor_m.char_end
                ),
                modal=modal,
                object_span=object_span,
                source_span=_span(source_file, actor_m.char_start, frame_end),
                actor_status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )
        consumed_actor_idx.add(best_actor_idx)

    # An actor that was seen near a modal but did not bind to one (a nearer actor
    # did) is a genuine seen-but-untyped shape, emitted as a typed residual.
    for a_idx, actor_m in enumerate(actor_matches):
        if a_idx in consumed_actor_idx:
            continue
        has_following_modal = any(
            0 <= (modal_m.char_start - actor_m.char_end) <= _MAX_ACTOR_MODAL_GAP
            and not sentence_terminator_between(
                tokens, actor_m.char_end, modal_m.char_start
            )
            for modal_m in modal_matches
        )
        if has_following_modal:
            residuals.append(
                ActorModalResidual(
                    kind="actor_without_modal",
                    surface_text=actor_m.surface,
                    source_span=_span(
                        source_file, actor_m.char_start, actor_m.char_end
                    ),
                    detail=(
                        f"actor surface {actor_m.surface!r} appears near a "
                        f"modal but did not bind to one (a nearer actor did)"
                    ),
                )
            )

    return ActorModalScan(
        frames=tuple(frames),
        residuals=tuple(residuals),
    )


def recognize_actor_modal_frames(
    tape_or_text: TokenTape | str, source_file: str = ""
) -> ActorModalScan:
    """Recognise surface actor/modal frames over a :class:`TokenTape`.

    Returns an :class:`ActorModalScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a modal with no nearby actor, an actor with no nearby modal, and an
    ambiguous actor surface each become a typed :class:`ActorModalResidual`.

    The recognizer records SURFACE FACTS ONLY and never asserts legal force.

    Accepts a :class:`TokenTape` (the token-native path the lens feeds) or, for
    convenience in tests/baselines, a raw ``str`` (tokenized internally via the
    Finnish tokenizer). Emitted spans are whole-token aligned.
    """
    if isinstance(tape_or_text, str):
        from lawvm.finland.legal_surface.tokenize import build_token_tape

        tape = build_token_tape(source_file or "actor_modal", tape_or_text)
    else:
        tape = tape_or_text
    return _scan_tape(tape, source_file)
