"""``generate_forms`` --- the public generation entry point.

Given a :class:`~lawvm.finland.morphology.api.MorphEntry`, generate the requested
``reference_v1`` case forms as :class:`~lawvm.finland.morphology.api.MorphForm`
objects.  Plural generation is supported only for the common classes that the
plural stem builder handles; an unsupported (case, number) pair is returned as a
``certainty="unsupported"`` form rather than a silent guess.
"""

from __future__ import annotations

from .api import (
    REFERENCE_V1_PL,
    REFERENCE_V1_SG,
    MorphCase,
    MorphEntry,
    MorphForm,
    MorphNumber,
)
from .paradigm import case_form, plural_case_form
from .plurals import PluralStem, build_plural
from .stems import build_stems

_PROFILES = {
    "reference_v1": (REFERENCE_V1_SG, REFERENCE_V1_PL),
}


def generate_forms(
    entry: MorphEntry,
    profile: str = "reference_v1",
    cases: tuple[MorphCase, ...] | None = None,
    numbers: tuple[MorphNumber, ...] = (MorphNumber.SG,),
) -> tuple[MorphForm, ...]:
    """Generate the case forms for ``entry``.

    ``cases=None`` uses the profile's full per-number case set.  Each form
    carries a ``rule_id`` and ``certainty``; ``form_overrides`` on the entry win
    over the rule and are tagged ``source="exception"``.
    """
    if profile not in _PROFILES:
        msg = f"unknown profile {profile!r}"
        raise ValueError(msg)
    sg_cases, pl_cases = _PROFILES[profile]

    stems = build_stems(
        entry.lemma,
        morph_class=entry.morph_class,
        gradation=entry.gradation,
        single_k=entry.single_k,
    )
    plural = build_plural(
        morph_class=entry.morph_class,
        lemma=entry.lemma,
        stems=stems,
    )

    out: list[MorphForm] = []
    for number in numbers:
        number_cases = cases if cases is not None else (
            sg_cases if number is MorphNumber.SG else pl_cases
        )
        for case in number_cases:
            override = entry.form_overrides.get((case, number))
            if override is not None:
                out.append(
                    MorphForm(
                        surface=override,
                        lemma_id=entry.lemma_id,
                        lemma=entry.lemma,
                        case=case,
                        number=number,
                        rule_id="override",
                        source="exception",
                    ),
                )
                continue
            if number is MorphNumber.PL:
                out.append(_plural_form(entry, case, plural))
                continue
            if case.name in stems.unsupported_cases:
                out.append(
                    MorphForm(
                        surface="",
                        lemma_id=entry.lemma_id,
                        lemma=entry.lemma,
                        case=case,
                        number=number,
                        rule_id=f"{case.name}.unsupported",
                        certainty="unsupported",
                    ),
                )
                continue
            rule_id, surface = case_form(case, stems)
            out.append(
                MorphForm(
                    surface=surface,
                    lemma_id=entry.lemma_id,
                    lemma=entry.lemma,
                    case=case,
                    number=number,
                    rule_id=rule_id,
                ),
            )
    return tuple(out)


def _plural_form(
    entry: MorphEntry,
    case: MorphCase,
    plural: PluralStem,
) -> MorphForm:
    """Generate one plural case form, or fail loud where the rule does not hold.

    The plural ``-i-`` marker's stem/grade interactions are modelled in
    :mod:`plurals`; the genuinely-irregular classes (above all the ``-Uus``
    plural, oikeus -> oikeuksia, which abandons the singular ``-Ude-`` stem) and
    the lexical 3+ syllable ``-a`` subclass split set ``plural.unsupported`` ->
    ``certainty="unsupported"`` rather than emitting a wrong form.
    """
    if plural.unsupported:
        return MorphForm(
            surface="",
            lemma_id=entry.lemma_id,
            lemma=entry.lemma,
            case=case,
            number=MorphNumber.PL,
            rule_id="PL.unsupported",
            certainty="unsupported",
        )
    rule_id, surface = plural_case_form(case, plural)
    return MorphForm(
        surface=surface,
        lemma_id=entry.lemma_id,
        lemma=entry.lemma,
        case=case,
        number=MorphNumber.PL,
        rule_id=rule_id,
    )


__all__ = ["generate_forms"]
