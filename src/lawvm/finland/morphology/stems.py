"""Stem formation + partitive-quality selection.

Generation needs, per lemma:

* the **vowel stem** (NOM minus nothing for vowel-final lemmas; the stem before
  the case vowel), and
* the **oblique (weak-grade) stem** used before the closed-syllable suffixes
  (GEN -n, INE/ELA/ADE/ABL/ALL/TRA), to which gradation is applied.

The mapping from a lemma to these stems is driven by ``morph_class`` (the
emergent paradigm key), NOT by 49 hand-written code paths.  Each class is a small
rule that says how to peel the lemma's ending and what oblique stem it grows.

Partitive quality (-a / -ta / -tta) is a 100% rule keyed on the stem ending:

* single short vowel -> ``-A``            (lakia, virastoa, ministeriötae)
* diphthong / long vowel / consonant -> ``-tA``  (asetusta, direktiiviae? no)
* a contracted -e raised to -ee -> ``-ttA``       (oikeutta, Tamperetta? -> -tta)

The selection is computed from the *partitive stem* the class supplies, so each
class declares the stem the partitive attaches to.
"""

from __future__ import annotations

from dataclasses import dataclass

from .gradation import weaken_stem

_VOWELS = frozenset("aeiouyäö")


@dataclass(frozen=True, slots=True)
class Stems:
    """The derived stems a paradigm needs for one entry.

    * ``nominative`` --- the citation form (= lemma, surfaced verbatim).
    * ``vowel_stem`` --- stem ending in a vowel, before suffixes that take a
      bare vowel-initial ending (the illative).
    * ``oblique_stem`` --- weak-grade stem before the closed-syllable suffixes.
    * ``partitive_stem`` --- stem the singular partitive attaches to.

    Plural stems are intentionally absent: M1 generates singular only and fails
    loud on plural cases (the plural ``-i-`` marker's stem interactions are
    M-series follow-up).
    """

    nominative: str
    vowel_stem: str
    oblique_stem: str
    partitive_stem: str
    # Distinct strong-grade illative stem, set only where the illative does NOT
    # build off ``vowel_stem`` (the -Uus class: oikeus -> oikeute- -> oikeuteen,
    # a strong -te- the weak -Ude- stem cannot supply).  ``None`` -> use
    # ``vowel_stem`` (the regular case).
    illative_stem: str | None = None
    # Cases this class cannot generate by rule -> fail loud rather than emit a
    # wrong form.  (Empty now that the -Uus illative is a modelled exception.)
    unsupported_cases: frozenset[str] = frozenset()


def build_stems(
    lemma: str,
    *,
    morph_class: str,
    gradation: bool,
    single_k: str | None,
) -> Stems:
    """Derive the stems for ``lemma`` under ``morph_class``.

    Raises :class:`ValueError` for an unknown class --- generation must never
    silently guess a paradigm.
    """
    builder = _CLASS_BUILDERS.get(morph_class)
    if builder is None:
        msg = f"unknown morph_class {morph_class!r} for lemma {lemma!r}"
        raise ValueError(msg)
    return builder(lemma, gradation=gradation, single_k=single_k)


# --------------------------------------------------------------------------- #
# Class builders.  Each returns the derived stems.  Gradation is only
# ever applied to the *oblique* stem (the weak grade surfaces in closed
# syllables); the vowel stem keeps the strong grade.
# --------------------------------------------------------------------------- #


def _vowel_final(
    lemma: str,
    *,
    gradation: bool,
    single_k: str | None,
) -> Stems:
    """Plain vowel-final lemma: virasto, direktiivi, ministerio, Helsinki, Turku.

    Vowel stem = lemma.  The oblique stem keeps the lemma's final vowel and
    gradates the consonant cluster before it (weak grade in the closed syllable).
    """
    consonant_part = lemma[:-1]
    final_vowel = lemma[-1]
    weak = weaken_stem(consonant_part, gradation=gradation, single_k=single_k)
    oblique = weak + final_vowel
    return Stems(
        nominative=lemma,
        vowel_stem=lemma,
        oblique_stem=oblique,
        partitive_stem=lemma,
    )


def _us_kse(
    lemma: str,
    *,
    gradation: bool,  # noqa: ARG001 - class never gradates
    single_k: str | None,  # noqa: ARG001
) -> Stems:
    """Deverbal -Us/-Os nouns: asetus->asetukse-, paatos->paatokse-, keskus.

    The final ``s`` of the lemma is replaced by ``kse`` to form the oblique /
    vowel stem.  Partitive attaches ``-ta`` to the *nominative* (asetus + ta).
    """
    base = lemma[:-1]  # drop the final s
    stem = base + "kse"
    return Stems(
        nominative=lemma,
        vowel_stem=stem,
        oblique_stem=stem,
        partitive_stem=lemma,  # consonant-final -> -ta
    )


def _uus_ude(
    lemma: str,
    *,
    gradation: bool,  # noqa: ARG001 - class never gradates
    single_k: str | None,  # noqa: ARG001
) -> Stems:
    """Quality -Uus/-Os adjective-abstract nouns: oikeus->oikeude- (THE TRAP).

    The final ``s`` is replaced by ``de`` (the weak grade of an underlying -te-).
    Partitive is ``-tta`` on the bare stem vowel (oikeu + tta = oikeutta).
    """
    base = lemma[:-1]  # drop the final s -> "oikeu"
    stem = base + "de"
    return Stems(
        nominative=lemma,
        vowel_stem=stem,
        oblique_stem=stem,
        partitive_stem=base + "+tta",  # sentinel: explicit -tta partitive
        # ILL takes the strong grade -te- (oikeus -> oikeute- -> oikeuteen), NOT
        # the weak -Ude- stem (which would give the wrong *oikeudeen).  This is a
        # per-class exception, modelled here rather than declined.
        illative_stem=base + "te",
    )


def _e_contract(
    lemma: str,
    *,
    gradation: bool,
    single_k: str | None,
) -> Stems:
    """-e nouns / -e place names: Tampere->Tamperee- (e lengthening).

    The final ``-e`` lengthens to ``-ee`` in the inflected stem.  Partitive is
    ``-tta`` (Tamperetta) per the long-vowel rule, but the gate only exercises
    GEN/ADE here.
    """
    consonant_part = lemma[:-1]
    weak = weaken_stem(consonant_part, gradation=gradation, single_k=single_k)
    stem = weak + "ee"
    return Stems(
        nominative=lemma,
        vowel_stem=stem,
        oblique_stem=stem,
        partitive_stem=lemma + "+tta",  # long vowel -> -tta
    )


def _nen_se(
    lemma: str,
    *,
    gradation: bool,  # noqa: ARG001
    single_k: str | None,  # noqa: ARG001
) -> Stems:
    """-nen nouns (Kotus 38): hallinen-style -> -se- stem, -sta partitive."""
    base = lemma[:-3]  # drop -nen
    stem = base + "se"
    return Stems(
        nominative=lemma,
        vowel_stem=stem,
        oblique_stem=stem,
        partitive_stem=lemma[:-3] + "s+ta",  # -sta partitive (sentinel)
    )


_CLASS_BUILDERS = {
    "vowel_final": _vowel_final,
    "-Us->-Ukse-": _us_kse,
    "-Os->-Okse-": _us_kse,
    "-Uus->-Ude-": _uus_ude,
    "e_contract": _e_contract,
    "-nen": _nen_se,
}



# --------------------------------------------------------------------------- #
# Partitive quality selection (100% rule, exposed for paradigm.py).
# --------------------------------------------------------------------------- #


def partitive_ending(partitive_stem: str) -> tuple[str, str]:
    """Return ``(stem, archiphoneme_ending)`` for the singular partitive.

    Sentinels embedded by the class builders take priority:

    * ``"<stem>+tta"`` -> explicit ``-ttA`` (oikeutta, Tamperetta).
    * ``"<base>s+ta"``  -> explicit ``-tA`` after restoring the ``s`` (-nen).

    Otherwise the quality is chosen from the stem ending:

    * consonant-final  -> ``-tA``  (asetus + ta -> asetusta)
    * single short vowel -> ``-A`` (laki + a -> lakia; virasto + a -> virastoa)
    """
    if partitive_stem.endswith("+tta"):
        return partitive_stem[:-4], "ttA"
    if partitive_stem.endswith("+ta"):
        return partitive_stem[:-3], "tA"
    last = partitive_stem[-1]
    if last in _VOWELS:
        # Long vowel / diphthong -> -tA; single short vowel -> -A.
        if len(partitive_stem) >= 2 and partitive_stem[-2] in _VOWELS:
            return partitive_stem, "tA"
        return partitive_stem, "A"
    # Consonant-final stem.
    return partitive_stem, "tA"


__all__ = ["Stems", "build_stems", "partitive_ending"]
