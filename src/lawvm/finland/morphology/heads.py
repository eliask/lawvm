"""Closed legal / agency head classes (~25), resolved once.

Each head is a lemma whose morph_class + flags are fixed (the ``-Us`` split is
resolved here, by head identity, not inferred from the surface).  This is the
table that lets a compound's inflected tail be generated: the modifier prefix is
invariant, the head carries the paradigm.

The values are flags, not form tables.  ``make_entry`` builds a ready-to-inflect
:class:`~lawvm.finland.morphology.api.MorphEntry` for any head.
"""

from __future__ import annotations

from .api import MorphEntry
from .lexical_residue import SINGLE_K

# head lemma -> (morph_class, gradation, referent_kind)
# gradation is the *occurrence* flag; single-k realization comes from SINGLE_K.
_HEADS: dict[str, tuple[str, bool, str]] = {
    # Statute / instrument heads.
    "laki": ("vowel_final", True, "statute_head"),  # single-k -> lain
    "asetus": ("-Us->-Ukse-", False, "statute_head"),
    "päätös": ("-Os->-Okse-", False, "statute_head"),
    "sopimus": ("-Us->-Ukse-", False, "statute_head"),
    "säädös": ("-Os->-Okse-", False, "statute_head"),
    "määräys": ("-Us->-Ukse-", False, "statute_head"),
    "ohje": ("e_contract", False, "statute_head"),
    "ilmoitus": ("-Us->-Ukse-", False, "statute_head"),
    "direktiivi": ("vowel_final", False, "statute_head"),  # stable loan, no grad
    # Agency / organ heads.
    "virasto": ("vowel_final", False, "agency"),  # no gradation
    "hallinto": ("vowel_final", True, "agency"),  # nt->nn: hallinnon
    "ministeriö": ("vowel_final", False, "agency"),
    "lautakunta": ("vowel_final", True, "agency"),  # nt->nn: lautakunnan
    "keskus": ("-Us->-Ukse-", False, "agency"),
    "laitos": ("-Os->-Okse-", False, "agency"),
    "oikeus": ("-Uus->-Ude-", False, "agency"),  # THE TRAP: -Ude-, not -Ukse-
    # Structural-vocab heads (the cite anatomy the plural profile exercises).
    "pykälä": ("vowel_final", False, "structural"),  # pl pykälien/pykäliä
    "momentti": ("vowel_final", True, "structural"),  # tt->t: momenteissa
    "kohta": ("vowel_final", True, "structural"),  # nt? no: ht->hd: kohdissa
}


def head_entry(lemma: str, *, lemma_id: str | None = None) -> MorphEntry:
    """Build a :class:`MorphEntry` for a known head ``lemma``.

    Raises :class:`KeyError` for an unknown head --- the head class is closed and
    membership is never guessed.
    """
    morph_class, gradation, kind = _HEADS[lemma]
    return MorphEntry(
        lemma_id=lemma_id or f"head:{lemma}",
        lemma=lemma,
        referent_kind=kind,
        morph_class=morph_class,
        head=lemma,
        gradation=gradation,
        single_k=SINGLE_K.get(lemma),
    )


def is_known_head(lemma: str) -> bool:
    """Return True if ``lemma`` is a closed-class head."""
    return lemma in _HEADS


__all__ = ["head_entry", "is_known_head"]
