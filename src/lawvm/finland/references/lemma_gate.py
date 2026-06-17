"""Shared morphology gate for statute-head recognizers (SOUND, M1-derived).

A recognizer that triggers on a token ending in an oblique *statute-head*
surface (``lain`` / ``laissa`` / ``laista`` ...) faces a false-positive class:
ordinary Finnish words whose tail is byte-identical to a laki oblique
(``veronalaista`` = ``-lainen`` partitive, ``oppilaille`` = ``-las`` agent-noun
plural, ``jollain`` = ``jokin`` pronoun, ``tämänlain`` = determiner + laki).

Historically each recognizer carried its own hand-written suffix-substring
filters.  Suffix-substring matching has a consonant-gradation bug class
(``'asetus' not in 'asetuksen'``) and duplicates the same four tables across
lanes.  This module exposes ONE sound gate that every head-triggering recognizer
(by-name, internal-refs, inline citations, eu-directive head detection) reuses:

    >>> from lawvm.finland.references.lemma_gate import lemma_gate, GateVerdict
    >>> lemma_gate("oppilaille").verdict is GateVerdict.REJECT_KNOWN_OTHER
    True
    >>> lemma_gate("luonnonsuojelulaissa").verdict is GateVerdict.UNKNOWN
    True
    >>> lemma_gate("laissa").verdict is GateVerdict.ACCEPT_HEAD
    True

The gate is the deterministic inverse of M1 over the closed legal vocabulary
PLUS the closed negative collision paradigms:

* ``ACCEPT_HEAD``        -- the whole token is itself a statute-head inflection
  (a bare head, ``laissa``): M1's :class:`LemmaIndex` accepts it.
* ``REJECT_KNOWN_OTHER`` -- a closed NON-statute paradigm explains the token's
  tail at least as completely as the laki oblique that triggered (the four FP
  families).  This is a proof of non-reference, not a guess.
* ``UNKNOWN``            -- no closed paradigm (statute or negative) matches the
  whole token: a genuine compound (``luonnonsuojelulaissa``) or an out-of-
  vocabulary word.  Honest-unknown; the caller emits / handles as before.  This
  is the fail-loud default: the gate NEVER guesses.

Importable without circular deps: depends only on
:mod:`lawvm.finland.morphology` (no ``references`` siblings).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lawvm.finland.morphology import build_lemma_index
from lawvm.finland.morphology.heads import _HEADS
from lawvm.finland.morphology.negative import negative_paradigms


class GateVerdict(Enum):
    """The three sound outcomes of the morphology gate."""

    ACCEPT_HEAD = "accept_head"
    REJECT_KNOWN_OTHER = "reject_known_other"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Decision:
    """A gate decision plus the evidence that produced it.

    ``lemma`` carries the deciding lemma when known: the statute head for
    ``ACCEPT_HEAD``, the rejecting non-statute lemma for ``REJECT_KNOWN_OTHER``,
    ``None`` for ``UNKNOWN``.  ``reason`` is a short human label for diagnostics.
    """

    verdict: GateVerdict
    lemma: str | None = None
    reason: str = ""


_STATUTE_HEAD_LEMMAS: frozenset[str] = frozenset(
    lemma for lemma, (_cls, _grad, kind) in _HEADS.items() if kind == "statute_head"
)


def lemma_gate(token: str, *, peeled_modifier: str | None = None) -> Decision:
    """Classify a head-triggered ``token`` as a head / known-other / unknown.

    ``token`` is the WHOLE matched token (modifier + oblique head surface), e.g.
    ``luonnonsuojelulaissa`` or ``veronalaista``.  ``peeled_modifier`` is the
    modifier the caller's segmentation peeled off the head (``luonnonsuojelu`` /
    ``verona``); when supplied it lets the gate detect the determiner-collapse
    family (the modifier is itself a complete determiner inflection).  Callers
    that do not segment may omit it.

    The decision is sound (every branch is an M1 paradigm proof, never a suffix
    guess):

    1. Whole-token is a STATUTE-head inflection (``laissa``) -> ``ACCEPT_HEAD``.
    2. A closed NEGATIVE paradigm surface is a suffix of the token at least as
       long as the colliding laki oblique (``alaista`` over ``laista`` in
       ``veronalaista``; whole-token ``jollain``) -> ``REJECT_KNOWN_OTHER``.
    3. The peeled modifier is a complete closed-determiner inflection
       (``tämän`` in ``tämänlain``) -> ``REJECT_KNOWN_OTHER``.
    4. Otherwise -> ``UNKNOWN`` (genuine compound / OOV; emit as before).
    """
    low = token.lower()

    # (1) Whole-token statute head (bare head, e.g. "laissa").
    idx = build_lemma_index()
    for lemma in idx.analyze(low):
        if lemma in _STATUTE_HEAD_LEMMAS:
            return Decision(GateVerdict.ACCEPT_HEAD, lemma=lemma, reason="statute_head")

    neg = negative_paradigms()

    # (2) Negative collision paradigm explains the tail.
    hit = neg.longest_suffix_match(low)
    if hit is not None:
        return Decision(
            GateVerdict.REJECT_KNOWN_OTHER,
            lemma=hit.lemma,
            reason=f"non_statute_paradigm:{hit.lemma}",
        )

    # (3) Determiner-collapse: the peeled modifier is a full determiner form.
    if peeled_modifier is not None and neg.is_determiner_modifier(peeled_modifier):
        return Decision(
            GateVerdict.REJECT_KNOWN_OTHER,
            lemma="determiner",
            reason="determiner_collapse",
        )

    # (4) Honest unknown -- genuine compound or OOV.  Never a guess.
    return Decision(GateVerdict.UNKNOWN, reason="out_of_closed_vocabulary")


__all__ = ["Decision", "GateVerdict", "lemma_gate"]
