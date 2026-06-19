"""Statute-local EU-nickname → CELEX alias table (ad-hoc ``jäljempänä`` binds).

The static :mod:`lawvm.finland.references.registries.eu_nickname` registry holds
the *established* EU-instrument nicknames (``tietosuoja-asetus`` → GDPR). But a
modern EU-implementing Finnish act routinely coins its OWN ad-hoc nickname for an
EU instrument it cites, tying it to the instrument with a parenthetical
``(jäljempänä <nickname>)`` right after the full cite, e.g.

    … kriittisten raaka-aineiden tarjonnasta … annettu asetus (EU) 2024/1252
    (jäljempänä kriittisten raaka-aineiden asetus) …

and then refers to it later by the nickname + article:

    … kriittisten raaka-aineiden asetuksen 9 artiklan …

Those later ``<nickname> N artikla`` uses were DROPPED: the ad-hoc nickname is
not in the static seed, so :func:`eu_nickname.lookup` returned ``none`` and the
bare head had no adjacent formal cite, so the EU-directive recognizer declined it
(fail-loud). The instrument identity, however, IS deterministically recoverable
— it is the EU act cite bound to the nickname at the binding site.

This module builds a per-statute ``{nickname_lemma → CELEX}`` table ONCE from the
full statute text, by:

  1. reusing the existing ``jäljempänä`` / parenthetical-alias binder
     (:func:`lawvm.finland.references.defined_terms.recognize_defined_term_bindings`)
     to locate every alias binding site and the act cite it ties the alias to; and
  2. resolving the bound act cite → CELEX with the SAME cite→CELEX logic the
     EU-directive recognizer already uses for adjacent formal cites
     (:func:`lawvm.finland.references.eu_directive._celex_from_formal_cite`), where
     the nickname head (``…asetus`` → R / ``…direktiivi`` → L) supplies the CELEX
     type letter when the cite itself is form-less.

The resulting table is consulted by the EU-directive recognizer AFTER the static
seed, so later ``<nickname> N artikla`` uses resolve to the right CELEX article
with the SAME shape the well-known-nickname path produces.

Fail-loud (AGENTS.md §0.3, "tag-don't-guess"):
  * only an alias whose term is EU-nickname-shaped (head inflects ``asetus`` /
    ``direktiivi`` / ``päätös``) AND whose bound cite resolves to a CELEX is
    registered; an unbindable / non-EU alias is NOT registered (it stays open);
  * the lookup is morphology-driven (the same inflected-surface index the static
    seed builds), so an inflected use (``…asetuksen``) of a nickname bound in the
    nominative resolves without a fuzzy suffix guess.
"""
from __future__ import annotations

from dataclasses import dataclass

from lawvm.finland.references.defined_terms import (
    recognize_defined_term_bindings,
)
from lawvm.finland.references.eu_directive import (
    _celex_from_formal_cite,
    _celex_type_for_head,
)
from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    is_eu_instrument_head,
    recognize_celex,
    recognize_eu_acts,
)
from lawvm.finland.references.registries.eu_nickname import _inflected_surfaces


@dataclass(frozen=True, slots=True)
class StatuteLocalNicknames:
    """A statute-local nickname → CELEX alias table.

    Built once per statute (a pre-pass over the full text). ``lookup`` resolves a
    surface (possibly inflected on its head) to the bound CELEX, or ``None``.

    Attributes:
        celex_by_lemma:    nickname lemma (nominative, lowercase) → CELEX id.
        _surface_to_lemma: precomputed inflected-surface → lemma index (the same
                           morphology-backed expansion the static seed uses), so an
                           inflected later use resolves deterministically.
    """

    celex_by_lemma: dict[str, str]
    _surface_to_lemma: dict[str, str]

    def __bool__(self) -> bool:
        return bool(self.celex_by_lemma)

    def lookup(self, nickname_surface: str) -> str | None:
        """Resolve a (possibly inflected) nickname surface to its bound CELEX.

        Case-insensitive; surrounding whitespace trimmed. Returns the CELEX id, or
        ``None`` when the surface is not a statute-local alias.
        """
        key = nickname_surface.strip().lower()
        if not key:
            return None
        lemma = self._surface_to_lemma.get(key)
        if lemma is None:
            return None
        return self.celex_by_lemma.get(lemma)


_EMPTY = StatuteLocalNicknames(celex_by_lemma={}, _surface_to_lemma={})


def _is_eu_nickname_shaped(term: str) -> bool:
    """True iff ``term`` ends in an EU-instrument head (``…asetus``/``…direktiivi``).

    Delegates to the shared, M1-backed
    :func:`~lawvm.finland.references.eu_reference.is_eu_instrument_head`: the
    last whitespace-separated token's TAIL must be an M1-generated
    EU-instrument-head surface, so an alias coined in an inflected form
    (``…asetuksen``) is recognised while a ``…laki`` / other domestic head is
    False. Paradigm inversion over a closed head set, not an ``asetu`` substring
    guess.
    """
    return is_eu_instrument_head(term)


def _celex_from_binding_window(window: str, head: str) -> str | None:
    """Resolve the CELEX the nickname binds to from its binding window.

    The binding window is the EU-act citation that the ``(jäljempänä <nickname>)``
    cue follows. A long-form EU title routinely names a SECOND act as repeal
    provenance ("asetuksen (EU) 2021/2116 … sekä asetuksen (EU) N:o 1306/2013
    KUMOAMISESTA (jäljempänä horisontaaliasetus)"); the nickname binds to the
    ENACTING act (2021/2116), NOT the repealed one (1306/2013). A position-only
    "closest cite to the cue" pick would wrongly take the repealed act.

    We therefore:
      * prefer a literal CELEX in the window (self-typing — used verbatim);
      * else take the PRIMARY (non-``repealed_embedded``) EU act cite closest to
        the cue (largest start), with the L/R/D TYPE letter from the nickname head
        (``…asetus`` → R / ``…direktiivi`` → L); the embedded-repeal tagging from
        :func:`recognize_eu_acts` excludes the repealed provenance act.

    Returns ``None`` (fail-loud) when no primary act cite resolves — the nickname
    is then NOT registered (it stays open) rather than bound to a wrong target.
    """
    # A literal CELEX is self-typing — prefer it.
    celex_hits = recognize_celex(window, dialect=DIALECT_CROSS_REF)
    if celex_hits:
        return max(celex_hits, key=lambda h: h.start).celex
    type_letter = _celex_type_for_head(head)
    if type_letter is None:
        return None
    primary = [
        a for a in recognize_eu_acts(window, dialect=DIALECT_CROSS_REF)
        if a.role != "repealed_embedded"
    ]
    if not primary:
        # No primary act survived repeal tagging — fall back to the shared
        # cite→CELEX path (handles the year-first slash form the cross-ref
        # recognizer's repeal tagger does not cover); still fail-loud on None.
        return _celex_from_formal_cite(window, head)
    # The enacting act closest to the cue governs the alias.
    act = max(primary, key=lambda a: a.start)
    try:
        year, num = int(act.year), int(act.number)
    except ValueError:
        return None
    if not (1957 <= year <= 2050):
        return None
    return f"3{year:04d}{type_letter}{num:04d}"


def build_statute_local_nicknames(text: str) -> StatuteLocalNicknames:
    """Build the per-statute EU-nickname → CELEX alias table from full text.

    Scans the statute text ONCE for ``jäljempänä`` / parenthetical alias bindings
    (reusing the production defined-term binder), keeps those whose term is
    EU-instrument-shaped, and resolves each bound act cite to a CELEX using the
    governing nickname head for the type letter. Returns an empty table when the
    text carries no resolvable EU alias binding (the common case short-circuits).

    Fail-loud: a binding whose cite cannot be resolved to a CELEX, or whose term
    is not EU-instrument-shaped, is skipped — never guessed.
    """
    if not text or "jäljempänä" not in text.lower() and "(" not in text:
        return _EMPTY
    celex_by_lemma: dict[str, str] = {}
    for binding in recognize_defined_term_bindings(text):
        term = binding.term.strip()
        if not _is_eu_nickname_shaped(term):
            continue
        span = binding.source_span
        bind_start = span.byte_offset
        bind_end = span.byte_offset + span.byte_len
        # The cite the alias is tied to sits just BEFORE the binding cue (the
        # binder already located the act id, but as a bare NUMBER/YEAR without the
        # CELEX type letter). Re-resolve the full CELEX from the window ending at
        # the binding site, using the nickname head for the L/R/D type — the SAME
        # cite→CELEX path the recognizer uses for adjacent formal cites. A wider
        # left window than the binder's 90-char lookback covers a long EU title
        # between the cite and the cue.
        window_start = max(0, bind_start - 400)
        window = text[window_start:bind_end]
        celex = _celex_from_binding_window(window, term)
        if celex is None:
            continue
        key = term.lower()
        # First binding wins; a later re-binding of the same nickname to a
        # different CELEX would be a drafting anomaly — keep the first (document
        # order) and do not silently overwrite.
        celex_by_lemma.setdefault(key, celex)
    if not celex_by_lemma:
        return _EMPTY

    surface_to_lemma: dict[str, str] = {}
    for lemma in celex_by_lemma:
        for surface in _inflected_surfaces(lemma):
            existing = surface_to_lemma.get(surface)
            if existing is None or len(lemma) > len(existing):
                surface_to_lemma[surface] = lemma
    return StatuteLocalNicknames(
        celex_by_lemma=celex_by_lemma,
        _surface_to_lemma=surface_to_lemma,
    )


__all__ = [
    "StatuteLocalNicknames",
    "build_statute_local_nicknames",
]
