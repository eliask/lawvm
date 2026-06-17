"""The case-suffix table --- ~12 archiphoneme suffixes x harmony.

The "49 Kotus classes" are EMERGENT from (stem rule x gradation flag x harmony);
this module never enumerates them.  Each case is a small function over the
:class:`~lawvm.finland.morphology.stems.Stems` bundle that picks the right stem
and appends the harmonized suffix.

Stem selection per case:

* NOM            -> nominative (citation form, verbatim)
* GEN -n         -> oblique (weak-grade) stem
* PART -A/-tA/-tt-> partitive_stem via :func:`partitive_ending`
* INE -ssA       -> oblique
* ELA -stA       -> oblique
* ILL -Vn/-seen  -> vowel stem (strong grade), special long-vowel handling
* ADE -llA       -> oblique
* ABL -ltA       -> oblique
* ALL -lle       -> oblique (suffix vowel is invariant e, no harmony slot)
* TRA -ksi       -> oblique

The locative series (internal vs external) swaps the INE/ELA/ILL block for the
ADE/ABL/ALL block as the *primary* locative for external-locative places, but
both series are still generable; the flag only marks which is idiomatic.  M1
generates all requested cases regardless and lets the caller choose.
"""

from __future__ import annotations

from .api import MorphCase
from .harmony import harmonize
from .plurals import PluralStem
from .stems import Stems, partitive_ending

_VOWELS = frozenset("aeiouyäö")


def _illative(stems: Stems) -> tuple[str, str]:
    """Return ``(rule_id, surface)`` for the singular illative.

    Short-vowel stem -> lengthen the final vowel + ``n`` (laki -> lakiin,
    virasto -> virastoon, asetukse -> asetukseen, ministerio -> ministerioon).
    Genuine long vowel (identical final pair, e.g. contracted -ee) -> ``-seen``
    (Tamperee -> Tampereeseen).

    Most classes build the illative off ``vowel_stem``; the -Uus class supplies a
    distinct strong-grade ``illative_stem`` (oikeute-) because its weak -Ude-
    vowel stem would give the wrong ``*oikeudeen``.
    """
    stem = stems.illative_stem if stems.illative_stem is not None else (
        stems.vowel_stem
    )
    if len(stem) >= 2 and stem[-1] in _VOWELS and stem[-1] == stem[-2]:
        return "ILL.seen", stem + "seen"
    if stem and stem[-1] in _VOWELS:
        return "ILL.Vn", stem + stem[-1] + "n"
    # Consonant-final vowel stem should not occur for the M1 classes.
    return "ILL.Vn", stem + "een"


def case_form(case: MorphCase, stems: Stems) -> tuple[str, str]:
    """Return ``(rule_id, surface)`` for ``case`` (singular).

    Harmony is computed against the oblique stem (the inflected head's stem),
    which carries the vowels that determine front/back.
    """
    oblique = stems.oblique_stem
    if case is MorphCase.NOM:
        return "NOM.bare", stems.nominative
    if case is MorphCase.GEN:
        return "GEN.n", oblique + "n"
    if case is MorphCase.PART:
        pstem, ending = partitive_ending(stems.partitive_stem)
        return "PART", pstem + harmonize(pstem, ending)
    if case is MorphCase.INE:
        return "INE.ssA", oblique + harmonize(oblique, "ssA")
    if case is MorphCase.ELA:
        return "ELA.stA", oblique + harmonize(oblique, "stA")
    if case is MorphCase.ILL:
        return _illative(stems)
    if case is MorphCase.ADE:
        return "ADE.llA", oblique + harmonize(oblique, "llA")
    if case is MorphCase.ABL:
        return "ABL.ltA", oblique + harmonize(oblique, "ltA")
    if case is MorphCase.ALL:
        return "ALL.lle", oblique + "lle"
    if case is MorphCase.TRA:
        return "TRA.ksi", oblique + "ksi"
    msg = f"unhandled case {case!r}"
    raise ValueError(msg)


def plural_case_form(case: MorphCase, plural: PluralStem) -> tuple[str, str]:
    """Return ``(rule_id, surface)`` for a plural ``case``.

    Only the ``reference_v1`` plural profile {NOM, GEN, PART, INE, ELA} is
    handled; the caller (:func:`generate_forms`) restricts the case set, and the
    ``plural.unsupported`` gate is checked there first.  Harmony is computed
    against the stem the ending attaches to (it carries the deciding vowels).
    """
    if case is MorphCase.NOM:
        return "PL.NOM.t", plural.nominative
    if case is MorphCase.GEN:
        return (
            "PL.GEN",
            plural.gen_stem + harmonize(plural.gen_stem, plural.gen_ending),
        )
    if case is MorphCase.PART:
        return (
            "PL.PART",
            plural.part_stem + harmonize(plural.part_stem, plural.part_ending),
        )
    if case is MorphCase.INE:
        return (
            "PL.INE.issA",
            plural.weak_i_stem + harmonize(plural.weak_i_stem, "ssA"),
        )
    if case is MorphCase.ELA:
        return (
            "PL.ELA.istA",
            plural.weak_i_stem + harmonize(plural.weak_i_stem, "stA"),
        )
    msg = f"unhandled plural case {case!r}"
    raise ValueError(msg)


__all__ = ["case_form", "plural_case_form"]
