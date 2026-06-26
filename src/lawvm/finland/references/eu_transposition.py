"""Detect an explicit EU-directive TRANSPOSITION CLAIM in FI statute prose.

The substrate's EU directive mini-vertical (design §25.8) emits ONLY the
deterministic, verifiable evidence about a directive transposition — never the
substantive legal doctrine:

  * ``source_claimed_transposition`` — the citing act SAYS, in its own text,
    that it transposes ("panee täytäntöön") a named EU directive. The claim is
    *source-given*: it is a fact about what the text asserts, not an assessment
    of whether the transposition is correct. Evidence plane, source-asserted.
  * ``timeliness_fact`` — the directive's transposition DEADLINE (a curated,
    hand-seeded demo date) vs. the citing act's commencement date. A pure date
    comparison: ``date_computable``. When the deadline is unknown the fact is an
    honest ``open`` residual ("deadline unknown"), NEVER a fabricated date.
  * ``conformance_assessment`` — NEVER asserted positive. The substrate emits a
    residual "conformance not assessed", representing the ABSENCE of a semantic
    judgment, so a consumer cannot mistake the evidentiary edges for a doctrinal
    conclusion ("correctly transposes" / "direct effect" / "in breach").

This module owns ONLY the extraction half: scan prose for a transposition-claim
surface, bind the named directive to its CELEX via the existing deterministic
``eu_directive`` / ``eu_nickname`` resolution (READ-ONLY), and emit a typed
:class:`TranspositionClaim`. The substrate-side mapping to relation-edge bodies
lives in :mod:`lawvm.substrate.eu_transposition_bridge`; the deadline seed lives
in :data:`TRANSPOSITION_DEADLINE_SEED` below.

Fail-loud discipline (§0.3 — no silent drops)
---------------------------------------------
A transposition-claim window that NAMES a directive but cannot bind a CELEX
(registry miss, no adjacent formal cite, ambiguous nickname) is STILL emitted —
with an explicit ``status`` recording WHY it is unbound (``ambiguous`` /
``statute_only``), never dropped and never guessed. The CELEX is left ``None``;
a downstream ``timeliness_fact`` then has no deadline key and degrades to the
honest ``open`` residual. The claim surface is preserved verbatim so the
evidentiary trail is auditable.

Window-location residue (bounded, §1.11)
----------------------------------------
The transposition-declaration phrase itself ("Tällä lailla pannaan täytäntöön …
direktiivi …", "… direktiivin täytäntöönpanemiseksi") is located by a small set
of literal-anchored, bounded-quantifier patterns — deliberate typed residue, as
in :mod:`eu_directive`: the johtolause construction grammar models Finnish
statute-internal structure (``§`` / ``momentti`` / ``kohta``), not the prose
declarative shape "this act implements directive X", so locating the claim
window via the grammar would need a new construction family disproportionate to
the (low) yield. Every quantifier is explicitly bounded, so each pattern is
provably linear and passes the §1.11 regex perf gate (no allowlist entry).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.references.eu_directive import (
    _find_nickname,
    _is_named_eu_instrument,
    _status_for,
)
from lawvm.core.reference_mention import CiteConfidence

# --------------------------------------------------------------------------- #
# Curated transposition-deadline seed (hand-seeded DEMO table, NOT scraped).   #
#                                                                              #
# Maps an EU directive CELEX -> its transposition deadline (the date by which  #
# member states had to bring the implementing measures into force). These are  #
# the dates the directive's "transposition" article fixes; they are looked up  #
# by hand for the handful of directives the e2e acts actually cite, NOT mined  #
# from any source. A directive with NO entry here yields an HONEST ``open``    #
# ``timeliness_fact`` ("deadline unknown"), never a fabricated date.           #
# --------------------------------------------------------------------------- #

TRANSPOSITION_DEADLINE_SEED: dict[str, str] = {
    # Industrial Emissions Directive 2010/75/EU (IED). Art. 80(1): member states
    # had to bring the implementing laws into force by 7 January 2013.
    "32010L0075": "2013-01-07",
    # Services Directive 2006/123/EC. Art. 44(1): transposition by 28 Dec 2009.
    "32006L0123": "2009-12-28",
    # Environmental Liability Directive 2004/35/EC. Art. 19(1): by 30 Apr 2007.
    "32004L0035": "2007-04-30",
}


def transposition_deadline(celex: str) -> Optional[str]:
    """Return the seeded transposition deadline (ISO date) for ``celex`` or None.

    ``None`` means "deadline not in the curated demo seed" — the honest absence a
    ``timeliness_fact`` turns into an ``open`` residual, NOT a fabricated date.
    """
    return TRANSPOSITION_DEADLINE_SEED.get(celex)


# --------------------------------------------------------------------------- #
# Transposition-claim surface patterns (§1.11: bounded, literal-anchored).      #
# --------------------------------------------------------------------------- #

# A transposition CLAIM is the act's OWN VERBAL act of putting a directive into
# force: "Tällä lailla pannaan täytäntöön … direktiivi …", "Lailla pannaan
# täytäntöön …", "<direktiivin> täytäntöön panemiseksi", "… täytäntöönpanemiseksi".
# We deliberately match the VERBAL ``panna täytäntöön`` paradigm (and its glued
# ``täytäntöönpane…`` participle/infinitive forms) — NOT the standalone NOUN
# ``täytäntöönpano`` / ``täytäntöönpanosta`` / ``täytäntöönpanoasetus``, which in
# FI body prose names a (Commission or national) IMPLEMENTING ACT of an EU
# instrument — "asetuksen (EU) N:o … täytäntöönpanosta annettu asetus" — a
# DIFFERENT relation (an implementing-act reference), not the FI act's claim to
# transpose a directive. Including the noun produced false-positive
# transposition claims, so the noun arm is excluded; the verbal/participle arm
# is the genuine transposition-declaration signal.
#
# Bounded (§1.11): a fixed small alternation of inflected ``panna`` forms + the
# literal ``täytäntöön`` (either order), plus the glued participle/infinitive
# ``täytäntöönpane…`` forms. No unbounded repeat.
_PANNA_FORMS = r"(?:pan(?:naan|nan|tava|tu|emiseksi|ee|i)|saatetaan)"
_TRANSPOSE_CLAIM_RE = re.compile(
    rf"(?:{_PANNA_FORMS}\s+täytäntöön|täytäntöön\s+{_PANNA_FORMS}"
    rf"|täytäntöönpane(?:miseksi|mistä|maan|mista|e))",
    re.IGNORECASE,
)

# How far AFTER a claim surface to look for the governing directive reference.
# Finnish keeps the directive name close to the declaration: "pannaan täytäntöön
# <…> direktiivi <…>". A bounded forward window keeps the binding deterministic
# and linear.
_CLAIM_FORWARD_WINDOW = 240
# …and a small BACKWARD window: "<nickname>direktiivin täytäntöönpanemiseksi"
# names the directive BEFORE the claim word.
_CLAIM_BACKWARD_WINDOW = 160


class TranspositionStatus(Enum):
    """Resolution status of a transposition claim's directive binding."""

    RESOLVED = "resolved"
    """The named directive bound to exactly one CELEX."""

    AMBIGUOUS = "ambiguous"
    """The named directive nickname maps to >1 CELEX — never picked."""

    STATUTE_ONLY = "statute_only"
    """A directive is NAMED but no CELEX could be bound (registry miss / no
    minable cite). The claim is committed with ``celex=None`` — tag, don't
    guess — never dropped."""


@dataclass(frozen=True, slots=True)
class TranspositionClaim:
    """One explicit transposition claim found in a citing act's prose.

    Attributes:
        citing_engine_id: The engine id of the act that makes the claim (the
            ``source_ref`` anchor — the act SAYS it transposes the directive).
        directive_celex:  The bound directive CELEX, or ``None`` when the
            directive is named but unbound (``status`` records why).
        directive_surface: The directive nickname/name surface as it appears.
        claim_surface:    The transposition-declaration phrase verbatim (the
            evidence the claim rests on).
        char_start:       Char offset of the claim phrase in the scanned prose.
        char_end:         Char offset one past the claim/directive span.
        transposition_status: Why the binding resolved as it did (§0.3 fail-loud).
    """

    citing_engine_id: str
    directive_celex: Optional[str]
    directive_surface: str
    claim_surface: str
    char_start: int
    char_end: int
    transposition_status: TranspositionStatus


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _prose(text: str) -> str:
    """Crudely strip XML tags + collapse whitespace so claim windows are clean.

    The extractor scans declarative prose, not the AKN tree; stripping tags here
    keeps the bounded forward/backward windows aligned to readable sentence
    fragments. Read-only — the source bytes are never mutated.
    """
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text))


def _bind_directive(
    window: str, head_idx: int
) -> Optional[tuple[Optional[str], str, TranspositionStatus]]:
    """Bind the directive named in ``window`` (ending at ``head_idx``) to a CELEX.

    Reuses :func:`lawvm.finland.references.eu_directive._find_nickname` READ-ONLY:
    it scans the lookbehind of ``head_idx`` for the nickname head closest to the
    claim and resolves it against the deterministic ``eu_nickname`` registry,
    returning a :class:`RegistryResult` (single / multiple / none). The
    article-window recognizer is NOT used here because a transposition CLAIM names
    the directive WITHOUT an adjacent ``N artikla`` ("teollisuuspäästödirektiivin
    III luvun … täytäntöönpanemiseksi"); the registry lookup is the right
    read-only reuse.

    Returns ``(celex_or_None, surface, status)``:
      * SINGLE registry hit         → ``(celex, surface, RESOLVED)``;
      * MULTIPLE (genuinely ambiguous) → ``(None, surface, AMBIGUOUS)`` — never
        picks;
      * NAMED instrument, registry miss → ``(None, surface, STATUTE_ONLY)`` —
        tag, don't guess.
    Returns ``None`` when the window names no directive at all, OR names only a
    BARE anaphoric/domestic head (``asetuksen`` / ``direktiivin`` with no glued
    instrument name): such a head co-located with a transposition word is an
    incidental EU-act mention, NOT a directive-transposition claim.
    """
    found = _find_nickname(window, head_idx)
    if found is None:
        return None
    surface, res = found
    # FAIL-LOUD discipline: only a NAMED EU instrument (a compound/multi-word
    # EU-head — ``teollisuuspäästödirektiivin``, ``päästökattodirektiivin``)
    # constitutes the act's claim to transpose THAT directive. A bare head
    # (``direktiivin`` / ``asetuksen``) carries no instrument identity → decline.
    if not _is_named_eu_instrument(surface):
        return None
    confidence, celex = _status_for(res)
    if confidence is CiteConfidence.EXACT and celex:
        return celex[0], surface, TranspositionStatus.RESOLVED
    if confidence is CiteConfidence.AMBIGUOUS:
        return None, surface, TranspositionStatus.AMBIGUOUS
    # STATUTE_ONLY — named EU instrument, no CELEX bound. Tag, don't guess.
    return None, surface, TranspositionStatus.STATUTE_ONLY


def recognize_transposition_claims(
    text: str, *, citing_engine_id: str
) -> list[TranspositionClaim]:
    """Find explicit EU-directive transposition claims in ``text``.

    Scans for a transposition-declaration surface ("pannaan täytäntöön",
    "täytäntöönpanemiseksi", …) and, in a bounded window around it, binds the
    governing directive to a CELEX via the existing EU resolver. Emits one
    :class:`TranspositionClaim` per claim surface that names a directive.

    A claim surface with no directive nearby is NOT emitted (it is an
    implementing-regulation reference or a domestic "täytäntöönpano", not the
    act's own directive-transposition claim). A claim that NAMES a directive but
    cannot bind a CELEX is emitted with ``celex=None`` and an explicit
    ``AMBIGUOUS`` / ``STATUTE_ONLY`` status — never dropped, never guessed.

    De-duplicates by ``(directive_celex_or_surface, claim_surface)`` so the same
    declaration recognized at overlapping windows yields one claim.
    """
    prose = _prose(text)
    out: list[TranspositionClaim] = []
    seen: set[tuple[Optional[str], str, str]] = set()
    # lawvm-regex: owning_parser canonical EU directive-transposition claim recognizer (the owner of TranspositionClaim emission); CELEX binding delegated to _find_nickname, status set explicitly, never guessed
    for m in _TRANSPOSE_CLAIM_RE.finditer(prose):
        fwd_end = min(len(prose), m.end() + _CLAIM_FORWARD_WINDOW)
        back_start = max(0, m.start() - _CLAIM_BACKWARD_WINDOW)
        # The directive can be named AFTER the claim ("pannaan täytäntöön …
        # direktiivi") or BEFORE it ("<nickname>direktiivin … täytäntöönpanemiseksi").
        # ``_find_nickname(text, before_idx)`` scans the lookbehind ENDING at
        # ``before_idx``, so:
        #   * forward case: scan the window AFTER the claim, with ``before_idx`` at
        #     its end so the nickname (which precedes that end) is in lookbehind;
        #   * backward case: scan the window BEFORE the claim, with ``before_idx``
        #     at the claim start (the nickname precedes the claim word).
        # Try backward first (the dominant FI order: "<directive> … täytäntöön-
        # panemiseksi"), then forward.
        back_window = prose[back_start:m.start()]
        bound = _bind_directive(back_window, len(back_window))
        if bound is None:
            fwd_window = prose[m.end():fwd_end]
            bound = _bind_directive(fwd_window, len(fwd_window))
        if bound is None:
            continue
        celex, surface, status = bound
        claim_surface = prose[m.start():m.end()].strip()
        key = (celex, surface, claim_surface)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            TranspositionClaim(
                citing_engine_id=citing_engine_id,
                directive_celex=celex,
                directive_surface=surface,
                claim_surface=claim_surface,
                char_start=m.start(),
                char_end=fwd_end,
                transposition_status=status,
            )
        )
    return out


__all__ = [
    "TRANSPOSITION_DEADLINE_SEED",
    "TranspositionClaim",
    "TranspositionStatus",
    "recognize_transposition_claims",
    "transposition_deadline",
]
