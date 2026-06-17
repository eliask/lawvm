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
from functools import lru_cache

from lawvm.finland.morphology import build_lemma_index, generate_forms, head_entry
from lawvm.finland.morphology.api import MorphCase, MorphNumber
from lawvm.finland.morphology.harmony import harmonize
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


@lru_cache(maxsize=None)
def head_surface_forms(lemmas: tuple[str, ...]) -> tuple[str, ...]:
    """Every M1-generated inflected surface of the given statute-head ``lemmas``.

    This is the GENERATIVE inverse of a hand-written suffix table: for a closed
    set of statute heads (``laki``, ``asetus``, ``direktiivi`` ...) it returns
    the full ``reference_v1`` paradigm (singular + plural) that M1 actually
    inflects them to (``laki -> lain, laissa, lakia, ...``; ``asetus ->
    asetuksen, asetukseen, ...``).  A recognizer that historically detected a
    head by a substring suffix table (``direktiiv|asetu``) instead matches a
    token whose **tail** is one of these generated forms --- the head rides at
    the end of a compound (``teollisuuspäästö`` + ``direktiivin``), exactly as
    the negative-paradigm gate strips a free modifier off ``alainen`` /
    ``oppilas``.

    Soundness: each surface is a real M1 output for a closed, known head, so
    suffix-matching on this set is paradigm inversion, not a guess.  It kills the
    consonant-gradation substring bug class (``'asetus' not in 'asetuksen'``)
    because the gradated stem form (``asetukseN``) is generated, never inferred
    from an ``asetu`` substring.

    Returned LONGEST-FIRST so a caller building a regex alternation gets the
    most-specific (longest) head form preferred over a shorter prefix of it
    (``asetuksesta`` before ``asetus``), which is required for correct suffix
    matching.

    ``lemmas`` MUST be known heads; :func:`head_entry` raises ``KeyError`` on an
    unknown lemma rather than guessing a morph_class.  ``lemmas`` is a tuple so
    the result can be memoized.
    """
    surfaces: set[str] = set()
    for lemma in lemmas:
        entry = head_entry(lemma)
        forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
        for form in forms:
            if form.certainty != "deterministic" or not form.surface:
                continue
            surfaces.add(form.surface.lower())
    return tuple(sorted(surfaces, key=lambda s: (-len(s), s)))


# Plural EXTERNAL-local-case archiphoneme endings (built on the plural stem):
# adessive ``-llA``, ablative ``-ltA``, allative ``-lle``, translative ``-ksi``.
# These are categorical (the same productive endings every noun takes on its
# plural stem) and harmonize against the stem.
_PLURAL_LOCAL_ENDINGS: tuple[str, ...] = ("llA", "ltA", "lle", "ksi")


@lru_cache(maxsize=None)
def head_plural_external_local_forms(lemmas: tuple[str, ...]) -> tuple[str, ...]:
    """Plural external-local-case surfaces of ``lemmas`` that M1 cannot generate.

    M1's ``reference_v1`` paradigm models the plural inessive/elative/genitive/
    partitive/nominative but DECLINES to emit the plural external local cases
    (adessive ``direktiiveillä``, ablative ``direktiiveiltä``, allative
    ``direktiiveille``, translative ``direktiiveiksi``) --- ``plural_case_form``
    raises on them.  A recognizer whose head can legitimately appear in those
    cases (the EU-instrument nickname: ``... näillä direktiiveillä säädetään``)
    would otherwise lose that coverage when it stops using a substring matcher.

    This derives those forms SOUNDLY rather than hand-typing a suffix table: it
    takes the PLURAL STEM directly from M1's own generated plural inessive
    (``direktiiveissä`` -> stem ``direktiivei``; ``asetuksissa`` -> ``asetuksi``)
    and appends the categorical plural external-local-case endings, harmonized
    against the stem.  The stem is an M1 output and the endings are categorical,
    so each surface is a sound derivation, not a guess.  This is the explicit,
    documented M1-boundary supplement (the reference_v1 profile's plural-local
    gap), mirroring the inline essive supplement.

    Returned LONGEST-FIRST for regex-alternation suffix use.
    """
    surfaces: set[str] = set()
    for lemma in lemmas:
        entry = head_entry(lemma)
        pl_ine = next(
            (
                form.surface
                for form in generate_forms(entry, numbers=(MorphNumber.PL,))
                if form.case is MorphCase.INE
                and form.certainty == "deterministic"
                and form.surface
            ),
            None,
        )
        if pl_ine is None:
            continue
        # Plural stem = plural inessive minus its inessive ending (-ssA).
        if pl_ine.lower().endswith(("ssa", "ssä")):
            stem = pl_ine[:-3]
        else:  # pragma: no cover - all modelled plural inessives end in -ssA
            continue
        for ending in _PLURAL_LOCAL_ENDINGS:
            surfaces.add((stem + harmonize(stem, ending)).lower())
    return tuple(sorted(surfaces, key=lambda s: (-len(s), s)))


@lru_cache(maxsize=None)
def head_case_forms(lemma: str, case_numbers: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """The M1-generated surfaces of ``lemma`` for the given ``(case, number)`` set.

    Like :func:`head_surface_forms` but for a SINGLE head and a CALLER-CHOSEN
    subset of ``(case, number)`` pairs (names from :class:`MorphCase` /
    :class:`MorphNumber`, e.g. ``(("GEN", "SG"), ("GEN", "PL"))``).  This is for
    recognizers that historically encoded an incomplete, DELIBERATELY-CURATED
    case set per head (the inline statute-citation family and the treaty
    word-cue) rather than the full paradigm --- the curation matters (e.g. a
    treaty governs ``N artiklassa`` in the genitive/inessive but not the
    adessive ``sopimuksella`` = "by this agreement"), so widening to the full
    paradigm would introduce false positives.  This helper retires the
    hand-written surface STRINGS for those exact case/number pairs (killing the
    gradation substring bug class) while preserving precisely which forms are
    recognized.

    A requested ``(case, number)`` that M1's ``reference_v1`` profile does not
    generate (it omits the essive, for one) yields no surface here --- the
    caller must supplement that form explicitly and is responsible for reporting
    the boundary.  Soundness is unchanged: each returned surface is a real M1
    output of a closed head.  Returned LONGEST-FIRST for regex-alternation
    suffix use.
    """
    wanted = {(MorphCase[c], MorphNumber[n]) for c, n in case_numbers}
    entry = head_entry(lemma)
    forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
    surfaces = {
        form.surface.lower()
        for form in forms
        if (form.case, form.number) in wanted
        and form.certainty == "deterministic"
        and form.surface
    }
    return tuple(sorted(surfaces, key=lambda s: (-len(s), s)))


__all__ = [
    "Decision",
    "GateVerdict",
    "head_case_forms",
    "head_plural_external_local_forms",
    "head_surface_forms",
    "lemma_gate",
]
