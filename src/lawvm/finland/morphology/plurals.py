"""Plural stem formation --- modelling the plural ``-i-`` marker's interactions.

The plural marker is an ``-i-`` that sits between the stem and the case ending.
It does not attach to a fixed plural form table; it triggers a small set of
deterministic stem changes that this module *computes* from the singular stems
the class builder already supplied (rules, not tabulated forms):

1.  **Grade per case** (verified, standard gradation): the ``-issA``/``-istA``
    block (and the plural nominative ``-t``) takes the **weak** grade --- the
    same ``oblique_stem`` the singular closed-syllable cases use --- while the
    plural **genitive** and **partitive** take the **strong** (``vowel_stem``)
    grade.  So ``laki`` -> ``laeissa`` (weak) but ``lakien`` (strong);
    ``momentti`` -> ``momenteissa`` / ``momenttien``.

2.  **Stem-vowel-before-i transform** (the only place with lexical residue):
    * ``o/ö/u/y``  -> kept, forms a diphthong with ``-i-`` (virasto -> virastoi-)
    * ``e``        -> dropped (ohje stem ohje- -> ohjei-)
    * ``ä``        -> always dropped (pykälä -> pykäli-)
    * ``a``        -> 2-syllable: ``->o`` if the first syllable vowel is a/e/i,
                     else dropped (kala -> kaloi-, kohta -> kohti-).  3+ syllable
                     ``-a`` is a genuine lexical subclass split -> **unsupported**.
    * ``i``        -> ``->e`` (laki -> lake-, momentti -> momentte-, direktiivi
                     -> direktiive-); the old ``-te`` i-stems that instead drop
                     are a classify-level wall and never reach here as a head.

3.  **Partitive/genitive ending choice** (derived from the transformed stem):
    * stem ends in a kept/raised vowel (``o``/``ö``/``a->o``) -> ``-jA`` / ``-jen``
    * stem ends in ``e`` from an ``i->e`` word -> ``-jA`` partitive on the vowel
      stem, but ``-ien`` genitive on the **consonant** stem (the hybrid split,
      laki -> lakeja / lakien)
    * stem-final vowel dropped (consonant-final) -> ``-iA`` / ``-ien``
    * the deverbal ``-Ukse-``/``-Okse-`` class prefers the legal ``-ten``
      genitive (asetusten), with ``-iA`` partitive (asetuksia)
    * contracted ``-ee`` / 3-syllable ``-io/-iö`` take ``-itA`` / ``-iden``
      (ohjeita/ohjeiden, ministeriöitä/ministeriöiden)

Anything genuinely irregular --- above all the ``-Uus`` (oikeus) plural, which
abandons the singular ``-Ude-`` stem for a ``-Ukse-`` plural (oikeuksien,
oikeuksia) that is NOT derivable from ``-Ude-`` --- is returned as
``unsupported`` so generation fails loud rather than emitting a wrong form.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .stems import Stems

_VOWELS = frozenset("aeiouyäö")
_FIRST_SYLLABLE_A_TO_O = frozenset("aei")  # 2-syll -a -> -o when 1st vowel here


@dataclass(frozen=True, slots=True)
class PluralStem:
    """Derived plural stems + ending choices for one entry.

    All fields are ``""`` / placeholder when ``unsupported`` is set; the caller
    must check ``unsupported`` first.

    * ``weak_i_stem``  --- weak grade + transformed vowel + ``i`` (INE/ELA, NOM
      uses ``nom`` instead).
    * ``nominative``   --- the plural nominative (weak grade + ``t``).
    * ``gen_stem`` / ``gen_ending`` --- plural genitive pieces.
    * ``part_stem`` / ``part_ending`` --- plural partitive pieces (ending may
      carry the ``A`` archiphoneme slot).
    """

    weak_i_stem: str = ""
    nominative: str = ""
    gen_stem: str = ""
    gen_ending: str = ""
    part_stem: str = ""
    part_ending: str = ""
    unsupported: bool = False
    reason: str = ""


def _first_syllable_vowel(stem: str) -> str | None:
    """Return the first vowel in ``stem`` (its first-syllable nucleus)."""
    for ch in stem:
        if ch in _VOWELS:
            return ch
    return None


def _syllable_count_le_two(stem: str) -> bool:
    """Rough 2-syllable test: count vowel *groups* (a diphthong = one nucleus)."""
    count = 0
    prev_vowel = False
    for ch in stem:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    return count <= 2


def _unsupported(reason: str) -> PluralStem:
    return PluralStem(unsupported=True, reason=reason)


def build_plural(*, morph_class: str, lemma: str, stems: Stems) -> PluralStem:
    """Derive the plural stems for ``lemma`` under ``morph_class``.

    Returns a :class:`PluralStem`; ``unsupported`` is set (with a ``reason``)
    for the genuinely-irregular classes rather than guessing a wrong form.
    """
    builder = _PLURAL_BUILDERS.get(morph_class)
    if builder is None:
        return _unsupported(
            f"no plural rule for morph_class {morph_class!r} (lemma {lemma!r})",
        )
    result = builder(lemma, stems)
    if result.unsupported:
        return result
    # The plural nominative carries NO ``-i-`` marker, so it never undergoes the
    # vowel-before-i transform: it is uniformly the weak (oblique) stem + ``t``
    # (kohta -> kohdat, pykälä -> pykälät, laki -> lait, asetus -> asetukset).
    return replace(result, nominative=stems.oblique_stem + "t")


# --------------------------------------------------------------------------- #
# Per-class plural builders.
# --------------------------------------------------------------------------- #


def _plural_vowel_final(lemma: str, stems: Stems) -> PluralStem:
    """Plain vowel-final lemmas: split on the final vowel of the stem.

    The vowel stem (= lemma) and the weak oblique stem differ only in grade; the
    transform is applied to each at the same final vowel.
    """
    final = lemma[-1]
    strong = stems.vowel_stem  # strong grade, lemma itself
    weak = stems.oblique_stem  # weak grade, gradated consonant cluster

    # -io/-iö (ministeriö, studio): a vowel directly after -i- takes the -itA /
    # -iden plural, NOT the -jA diphthong plural.  This is a categorical rule on
    # the stem ending, recovered here because classify routes -io/-iö (and other
    # plain vowel finals) all into ``vowel_final``.
    if len(lemma) >= 2 and lemma[-2] == "i" and final in "oöuy":
        return _plural_io(lemma, stems)
    if final in "ouyö":
        # Kept vowel -> diphthong with i; -jA / -jen.
        return _kept_vowel_plural(strong, weak, final)
    if final == "i":
        # i -> e hybrid (laki -> lakeja / lakien; direktiivi -> .../...).
        return _i_to_e_plural(strong, weak)
    if final in "aä":
        return _a_final_plural(lemma, strong, weak, final)
    if final == "e":
        # Plain -e vowel-final is uncommon as a head here; treat like drop.
        return _drop_vowel_plural(strong, weak)
    return _unsupported(f"unhandled vowel-final plural for {lemma!r}")


def _kept_vowel_plural(strong: str, weak: str, final: str) -> PluralStem:  # noqa: ARG001
    """o/ö/u/y final: vowel kept; the ``-i-`` surfaces as ``j`` between vowels.

    So the genitive/partitive endings are ``-jen``/``-jA`` straight on the
    strong stem (virasto -> virastojen/virastoja, kalo -> kalojen/kaloja); only
    the INE/ELA block keeps an actual ``-i-`` (virastoi -> virastoissa).
    ``nominative`` is set centrally in :func:`build_plural` (weak stem + t).
    """
    return PluralStem(
        weak_i_stem=weak + "i",
        gen_stem=strong,
        gen_ending="jen",
        part_stem=strong,
        part_ending="jA",
    )


def _i_to_e_plural(strong: str, weak: str) -> PluralStem:
    """i -> e words (laki, momentti, direktiivi).

    INE/ELA: weak stem, i->e, + i (laki -> lae+i = laei -> laeissa).
    Partitive: strong stem, i->e, + ja (lakeja, momentteja, direktiivejä).
    Genitive: strong CONSONANT stem (drop the e) + ien (lakien, momenttien,
    direktiivien) --- the hybrid split.
    """
    weak_e = weak[:-1] + "e"
    strong_e = strong[:-1] + "e"
    return PluralStem(
        weak_i_stem=weak_e + "i",
        gen_stem=strong_e[:-1],  # drop the e -> consonant stem
        gen_ending="ien",
        part_stem=strong_e,
        part_ending="jA",
    )


def _a_final_plural(lemma: str, strong: str, weak: str, final: str) -> PluralStem:
    """a/ä final: ä always drops; a is the 2-syll ->o / drop split."""
    if final == "ä":
        # ä always drops -> consonant stem + iA / ien.
        return _drop_vowel_plural(strong, weak)
    # final == "a"
    if not _syllable_count_le_two(lemma):
        return _unsupported(
            f"3+ syllable -a plural is a lexical subclass split "
            f"(-> -o- vs drop) for {lemma!r}; not rule-derivable",
        )
    first = _first_syllable_vowel(lemma)
    if first in _FIRST_SYLLABLE_A_TO_O:
        # a -> o : kala -> kaloja/kalojen.
        strong_o = strong[:-1] + "o"
        weak_o = weak[:-1] + "o"
        return _kept_vowel_plural(strong_o, weak_o, "o")
    # a dropped : kohta -> kohtia/kohtien.
    return _drop_vowel_plural(strong, weak)


def _drop_vowel_plural(strong: str, weak: str) -> PluralStem:
    """Final vowel dropped -> consonant stem; ``-iA`` / ``-ien``.

    ``nominative`` is set centrally in :func:`build_plural` (weak stem + t).
    """
    return PluralStem(
        weak_i_stem=weak[:-1] + "i",
        gen_stem=strong[:-1],
        gen_ending="ien",
        part_stem=strong[:-1],
        part_ending="iA",
    )


def _plural_us_kse(lemma: str, stems: Stems) -> PluralStem:  # noqa: ARG001
    """Deverbal -Ukse-/-Okse- (asetus, paatos): e drops, legal -ten genitive."""
    stem = stems.vowel_stem  # asetukse
    cons = stem[:-1]  # asetuks
    return PluralStem(
        weak_i_stem=cons + "i",  # asetuksi -> asetuksissa
        gen_stem=lemma,  # asetus
        gen_ending="ten",  # asetusten (legal preference)
        part_stem=cons,  # asetuks
        part_ending="iA",  # asetuksia
    )


def _plural_uus_ude(lemma: str, stems: Stems) -> PluralStem:  # noqa: ARG001
    """Quality -Uus (oikeus): the plural is IRREGULAR (oikeuksien/oikeuksia).

    The plural abandons the singular ``-Ude-`` stem for an ``-Ukse-`` plural that
    is not derivable from ``-Ude-`` -> fail loud, never guess.
    """
    return _unsupported(
        f"-Uus plural is irregular ({lemma!r} -> oikeuksien/oikeuksia, an "
        "-Ukse- plural not derivable from the singular -Ude- stem)",
    )


def _plural_e_contract(lemma: str, stems: Stems) -> PluralStem:  # noqa: ARG001
    """-e contracted nouns (ohje -> ohjee-): -itA / -iden plural."""
    weak = stems.oblique_stem  # ohjee
    short = weak[:-1]  # ohje  (shorten the long -ee to -e before i)
    return PluralStem(
        weak_i_stem=short + "i",  # ohjei -> ohjeissa
        gen_stem=short + "i",
        gen_ending="den",  # ohjeiden
        part_stem=short + "i",
        part_ending="tA",  # ohjeita
    )


def _plural_io(lemma: str, stems: Stems) -> PluralStem:  # noqa: ARG001
    """3-syllable -io/-iö (ministeriö): -itA / -iden plural."""
    strong = stems.vowel_stem  # ministeriö
    weak = stems.oblique_stem
    return PluralStem(
        weak_i_stem=weak + "i",  # ministeriöi -> ministeriöissä
        gen_stem=strong + "i",
        gen_ending="den",  # ministeriöiden
        part_stem=strong + "i",
        part_ending="tA",  # ministeriöitä
    )


_PLURAL_BUILDERS = {
    "vowel_final": _plural_vowel_final,
    "-Us->-Ukse-": _plural_us_kse,
    "-Os->-Okse-": _plural_us_kse,
    "-Uus->-Ude-": _plural_uus_ude,
    "e_contract": _plural_e_contract,
}


__all__ = ["PluralStem", "build_plural"]
