"""EU-instrument nickname -> CELEX registry (deterministic, T2).

Finnish statute prose routinely refers to EU instruments by an established
*nickname* rather than by their CELEX id or full title, e.g.

    teollisuuspäästödirektiivin 33 ja 35 artiklassa
    yleisen tietosuoja-asetuksen 6 artiklan

This module is the deterministic ``eu_nickname -> CELEX`` lookup table required
by §6 of ``notes_internal/FI_REFERENCE_CATALOGUE.md`` for the
``eu.directive_article`` family (T2). It is a *pure* registry: it recognises a
nickname surface and returns the curated CELEX candidate(s); resolution status
(EXACT / AMBIGUOUS / STATUTE_ONLY) is left to the caller via the typed
:class:`RegistryResult`.

Fail-loud contract (§0.3):
  - A nickname that maps to exactly one CELEX -> ``status=single``.
  - A nickname deliberately seeded with >1 CELEX (genuinely ambiguous Finnish
    usage) -> ``status=multiple`` with *all* candidates; the registry NEVER
    silently picks one.
  - An unknown nickname -> ``status=none`` (the caller emits STATUTE_ONLY only
    if it has independent evidence that a directive was named; the registry
    itself just reports "not in table").

Inflection handling (the morphology reuse)
------------------------------------------
Nicknames appear inflected on their *head* morpheme — the modifier prefix is
invariant, the head (``direktiivi`` / ``asetus`` / …) carries the Finnish case
ending: ``teollisuuspäästödirektiivi`` -> ``teollisuuspäästödirektiivin`` /
``…direktiivissä`` / ``…direktiiviä``. We resolve this deterministically by
reusing the merged morphology engine (``lawvm.finland.morphology``):

  1. Each registry lemma is split into ``modifier + known head`` (the head is a
     closed-class statute head — ``direktiivi`` / ``asetus`` / … — verified via
     :func:`lawvm.finland.morphology.is_known_head`).
  2. The engine generates every ``reference_v1`` case form of the *head* via
     :func:`lawvm.finland.morphology.generate_forms`.
  3. The inflected nickname surfaces are the modifier prefix concatenated with
     each generated head form (``teollisuuspäästö`` + {``direktiivi``,
     ``direktiivin``, ``direktiivissä``, …}).

This makes the inflected match a *generated, deterministic* set rather than a
fuzzy suffix heuristic. The precomputed ``inflected surface -> lemma`` map is
built once at import time. A nickname whose head is not a known morphology head
falls back to a conservative head-suffix-tolerant match (lemma-prefix + any of
the small fixed Finnish nominal endings) so the table never silently fails to
match a legitimately inflected surface; such entries are flagged in the seed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.morphology import (
    generate_forms,
    head_entry,
    is_known_head,
)

# ---------------------------------------------------------------------------
# Curated seed: lemma (nominative) -> tuple of CELEX candidate ids.
#
# A single-element tuple is an unambiguous nickname; a multi-element tuple is a
# genuinely ambiguous Finnish usage (the registry reports ALL candidates and
# refuses to pick). CELEX form: 3<YEAR>{L|R}<NNNN> (L=directive, R=regulation).
# ---------------------------------------------------------------------------

_SEED: dict[str, tuple[str, ...]] = {
    # --- Directives (L) ---
    "teollisuuspäästödirektiivi": ("32010L0075",),  # IED 2010/75/EU
    "vesipuitedirektiivi": ("32000L0060",),  # WFD 2000/60/EY
    "lintudirektiivi": ("32009L0147",),  # Birds 2009/147/EY (codified)
    "luontodirektiivi": ("31992L0043",),  # Habitats 92/43/ETY
    "kaupunkijätevesidirektiivi": ("31991L0271",),  # UWWTD 91/271/ETY
    "ympäristövastuudirektiivi": ("32004L0035",),  # ELD 2004/35/EY
    "palveludirektiivi": ("32006L0123",),  # Services 2006/123/EY
    "vesidirektiivi": ("31998L0083",),  # Drinking Water 98/83/EY
    # --- Regulations (R) ---
    "yleinen tietosuoja-asetus": ("32016R0679",),  # GDPR 2016/679
    "tietosuoja-asetus": ("32016R0679",),  # common short form of GDPR
    "sivutuoteasetus": ("32009R1069",),  # Animal by-products 1069/2009
    "reach-asetus": ("32006R1907",),  # REACH 1907/2006
    "clp-asetus": ("32008R1272",),  # CLP 1272/2008
    "dual-use-asetus": ("32021R0821",),  # Dual-use 2021/821
    # --- Deliberately ambiguous seed (Finnish usage genuinely splits) ---
    # "jätedirektiivi" is used in prose for both the consolidated Waste
    # Framework Directive (2008/98/EY) and, historically, its predecessor
    # 2006/12/EY. The registry reports both and refuses to pick.
    "jätedirektiivi": ("32008L0098", "32006L0012"),
}

# Heads that the morphology engine knows and that legitimately terminate a
# nickname. (Subset of the closed statute-head class; checked at build time.)
_NICKNAME_HEADS: tuple[str, ...] = ("direktiivi", "asetus")

# Conservative fallback endings for a nickname whose head is NOT a known
# morphology head — the small fixed set of Finnish nominal singular case endings
# the head could carry. Used only by the suffix-tolerant fallback path.
_FALLBACK_ENDINGS: tuple[str, ...] = (
    "",
    "n",
    "a",
    "ä",
    "ksi",
    "ssa",
    "ssä",
    "sta",
    "stä",
    "lla",
    "llä",
    "lta",
    "ltä",
    "lle",
    "in",
    "een",
    "seen",
)


class RegistryStatus(Enum):
    """Outcome of a nickname lookup."""

    SINGLE = "single"
    """Exactly one CELEX candidate — caller resolves EXACT."""

    MULTIPLE = "multiple"
    """More than one candidate — caller resolves AMBIGUOUS, never picks."""

    NONE = "none"
    """No candidate in the table — caller resolves STATUTE_ONLY (if it has
    independent evidence a directive was named) or treats as unknown."""


@dataclass(frozen=True, slots=True)
class RegistryResult:
    """Result of an :func:`lookup` call.

    Attributes:
        candidates: The CELEX ids that match (length 0 / 1 / >1).
        status:     ``single`` / ``multiple`` / ``none`` per :class:`RegistryStatus`.
        lemma:      The matched nickname lemma (nominative), or "" on a miss.
        matched_surface: The surface that triggered the match (possibly inflected).
    """

    candidates: tuple[str, ...]
    status: RegistryStatus
    lemma: str = ""
    matched_surface: str = ""


# ---------------------------------------------------------------------------
# Precomputed inflected-surface -> lemma index (built once at import).
# ---------------------------------------------------------------------------


def _split_head(lemma: str) -> Optional[tuple[str, str]]:
    """Split ``lemma`` into ``(modifier, head)`` if it ends in a known head.

    Returns the longest matching known head suffix, e.g.
    ``teollisuuspäästödirektiivi`` -> ``("teollisuuspäästö", "direktiivi")``.
    ``None`` if no known head terminates the lemma.
    """
    best: Optional[tuple[str, str]] = None
    for head in _NICKNAME_HEADS:
        if lemma.endswith(head) and is_known_head(head):
            modifier = lemma[: -len(head)]
            if best is None or len(head) > len(best[1]):
                best = (modifier, head)
    return best


def _word_variants(word: str) -> set[str]:
    """Inflected variants of a single ``word`` (lowercase).

    The head-bearing noun (last word) is inflected via the morphology engine
    when its head is known; any other word — and any word whose head is not a
    known morphology head — uses the conservative fixed-ending fallback. This
    also covers a leading adjective that agrees in case with the head
    (``yleinen`` -> ``yleisen``).
    """
    variants: set[str] = {word}
    split = _split_head(word)
    if split is not None:
        modifier, head = split
        for form in generate_forms(head_entry(head)):
            if form.surface and form.certainty == "deterministic":
                variants.add(modifier + form.surface)
        return variants
    # Conservative fallback: word stem + fixed nominal endings. For words whose
    # nominative ends in a vowel we also try dropping it, covering the common
    # gradationless cases (and adjective stems like ``yleine`` -> ``yleisen``).
    stems = {word}
    if word and word[-1] in "aeiouyäö":
        stems.add(word[:-1])
    if word.endswith("nen"):  # adjective/noun -nen -> -se- stem (yleinen->yleise-)
        stems.add(word[:-3] + "se")
    for stem in stems:
        for ending in _FALLBACK_ENDINGS:
            variants.add(stem + ending)
    return variants


def _inflected_surfaces(lemma: str) -> set[str]:
    """All inflected surface forms of a (possibly multi-word) ``lemma``.

    Each whitespace-separated word is independently expanded to its inflected
    variants (Finnish modifiers agree in case with their head noun), then the
    per-word variant sets are combined positionally. Always includes the bare
    lemma itself. All output is lowercase.
    """
    words = lemma.split()
    if not words:
        return {lemma}
    per_word = [_word_variants(w) for w in words]
    surfaces: set[str] = set()
    # Cartesian product across words; the seed nicknames are <=3 words so this
    # stays tiny (built once at import).
    stack: list[list[str]] = [[]]
    for choices in per_word:
        stack = [prefix + [choice] for prefix in stack for choice in choices]
    for combo in stack:
        surfaces.add(" ".join(combo))
    surfaces.add(lemma)
    return surfaces


def _build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for lemma in _SEED:
        for surface in _inflected_surfaces(lemma):
            # First registration wins; on collision keep the longer lemma so a
            # specific nickname (yleinen tietosuoja-asetus) is not shadowed by a
            # shorter one. Collisions across distinct CELEX would be a seed bug.
            existing = index.get(surface)
            if existing is None or len(lemma) > len(existing):
                index[surface] = lemma
    return index


_INFLECTED_INDEX: dict[str, str] = _build_index()


# ---------------------------------------------------------------------------
# Public lookup
# ---------------------------------------------------------------------------


def lookup(nickname_surface: str, as_of: object = None) -> RegistryResult:
    """Resolve a (possibly inflected) nickname surface to CELEX candidate(s).

    Args:
        nickname_surface: The surface as it appears in text, possibly inflected
            on its head (``teollisuuspäästödirektiivin``). Case-insensitive;
            surrounding whitespace is trimmed.
        as_of: Temporal coordinate placeholder. EU instruments do not (yet) need
            a temporal lookup in this curated seed — CELEX ids are stable — so
            this parameter is accepted for interface parity with the
            statute-name registry's ``static-as-of-citing`` convention but is
            currently unused. Reserved for future re-codified-instrument
            disambiguation.

    Returns:
        A :class:`RegistryResult`. ``status`` is ``single`` / ``multiple`` /
        ``none``; on a multi-candidate hit, ALL candidates are returned and the
        caller must not collapse to one (fail-loud, §0.3).
    """
    del as_of  # reserved; see docstring
    key = nickname_surface.strip().lower()
    if not key:
        return RegistryResult(candidates=(), status=RegistryStatus.NONE)

    lemma = _INFLECTED_INDEX.get(key)
    if lemma is None:
        return RegistryResult(candidates=(), status=RegistryStatus.NONE)

    candidates = _SEED[lemma]
    if len(candidates) == 1:
        status = RegistryStatus.SINGLE
    else:
        status = RegistryStatus.MULTIPLE
    return RegistryResult(
        candidates=candidates,
        status=status,
        lemma=lemma,
        matched_surface=nickname_surface.strip(),
    )


def known_lemmas() -> tuple[str, ...]:
    """Return the curated nickname lemmas (nominative), sorted."""
    return tuple(sorted(_SEED))


__all__ = [
    "RegistryResult",
    "RegistryStatus",
    "known_lemmas",
    "lookup",
]
