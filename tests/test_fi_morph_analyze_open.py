"""Tests for the OPEN-vocabulary morphological analyzer (M1 inversion).

The analyzer hypothesizes ``(lemma, morph_class, case, number)`` candidates from
suffix stripping + stem inversion, then keeps ONLY those that round-trip through
M1 ``generate_forms``.  Two invariants pin its correctness:

* RECALL (total over the generation space): every form M1 generates for the
  closed known-head inventory is recovered with its (lemma, case, number).
* SOUNDNESS: every analysis the analyzer returns round-trips --- there exists a
  ``MorphEntry`` for that (lemma, morph_class) whose generation reproduces the
  surface for that (case, number).  The analyzer never fabricates an analysis M1
  would not produce.

Unlike the closed-vocab :class:`LemmaIndex`, the analyzer also handles lemmas
OUTSIDE the head set (open vocabulary), demonstrated on real legal nominals.
"""

from __future__ import annotations

import pytest

from lawvm.finland.morphology.analyze import MorphAnalysis, analyze_open
from lawvm.finland.morphology.api import MorphCase, MorphEntry, MorphNumber
from lawvm.finland.morphology.generate import generate_forms
from lawvm.finland.morphology.heads import _HEADS, head_entry

# The lexical-flag space the analyzer searches over internally; reused by the
# soundness check to confirm SOME flag combination reproduces the surface.
_GRAD = (False, True)
_SK: tuple[str | None, ...] = (None, "zero", "v", "j")


def _round_trips(surface: str, analysis: MorphAnalysis) -> bool:
    """True if some MorphEntry for ``analysis`` generates ``surface``.

    Mirrors the analyzer's own gate: an analysis is sound iff there EXISTS a
    lexical-flag combination under which M1 inflects (lemma, morph_class) to the
    surface at (case, number).
    """
    norm = surface.strip().casefold()
    for gradation in _GRAD:
        for single_k in _SK:
            entry = MorphEntry(
                lemma_id="t",
                lemma=analysis.lemma,
                referent_kind="common",
                morph_class=analysis.morph_class,
                gradation=gradation,
                single_k=single_k,
            )
            forms = generate_forms(
                entry,
                numbers=(analysis.number,),
                cases=(analysis.case,),
            )
            if any(f.surface.casefold() == norm for f in forms):
                return True
    return False


def test_recall_is_total_over_the_generation_space() -> None:
    """Every M1-generated form of every known head is recovered by the analyzer.

    This is the completeness invariant: the analyzer's hypothesis generation is
    rich enough that the round-trip gate never discards a genuine paradigm slot.
    """
    missing: list[tuple[str, str, str, str]] = []
    checked = 0
    for lemma in _HEADS:
        entry = head_entry(lemma)
        forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
        for form in forms:
            if form.certainty != "deterministic" or not form.surface:
                continue
            checked += 1
            analyses = analyze_open(form.surface)
            hit = any(
                a.lemma == lemma
                and a.case == form.case
                and a.number == form.number
                for a in analyses
            )
            if not hit:
                missing.append(
                    (lemma, form.surface, form.case.name, form.number.name),
                )
    assert checked > 100
    assert missing == [], f"recall gaps: {missing}"


def test_every_returned_analysis_round_trips() -> None:
    """SOUNDNESS: no analyzer output is an analysis M1 would not produce.

    Exercises the whole closed-head generation space (sound by construction) plus
    open-vocab and out-of-vocab surfaces, and asserts every returned analysis
    round-trips.
    """
    surfaces: set[str] = set()
    for lemma in _HEADS:
        for form in generate_forms(
            head_entry(lemma), numbers=(MorphNumber.SG, MorphNumber.PL),
        ):
            if form.surface:
                surfaces.add(form.surface)
    surfaces |= {
        "talossa", "viisumia", "hakemuksen", "päätöksestä", "viranomaiselle",
        "maksun", "henkilölle", "asiakirjan", "koira", "xyzzy",
    }
    tested = 0
    for surface in surfaces:
        for analysis in analyze_open(surface):
            tested += 1
            assert _round_trips(surface, analysis), (
                f"{surface!r} -> {analysis} did not round-trip"
            )
    assert tested > 100


@pytest.mark.parametrize(
    ("surface", "lemma", "case", "number"),
    [
        # Open-vocabulary nominals NOT in the closed head set.
        ("talossa", "talo", MorphCase.INE, MorphNumber.SG),
        ("viisumia", "viisumi", MorphCase.PART, MorphNumber.SG),
        ("hakemuksen", "hakemus", MorphCase.GEN, MorphNumber.SG),
        ("päätöksestä", "päätös", MorphCase.ELA, MorphNumber.SG),
        ("viranomaiselle", "viranomainen", MorphCase.ALL, MorphNumber.SG),
        ("maksun", "maksu", MorphCase.GEN, MorphNumber.SG),
        ("henkilölle", "henkilö", MorphCase.ALL, MorphNumber.SG),
        ("asiakirjan", "asiakirja", MorphCase.GEN, MorphNumber.SG),
        # The -Uus trap, open-vocab: oikeudesta -> oikeus (not in head set as a
        # common noun here, recovered by stem inversion + round-trip).
        ("oikeudesta", "oikeus", MorphCase.ELA, MorphNumber.SG),
    ],
)
def test_open_vocab_analyses_are_present_and_correct(
    surface: str,
    lemma: str,
    case: MorphCase,
    number: MorphNumber,
) -> None:
    """The intended open-vocab analysis is among the returned (round-tripped) set.

    Ambiguity is surfaced (other sound analyses may co-occur); we assert the
    correct one is present, and that each returned analysis round-trips.
    """
    analyses = analyze_open(surface)
    assert any(
        a.lemma == lemma and a.case == case and a.number == number
        for a in analyses
    ), f"{surface!r}: expected {lemma}/{case.name}/{number.name}, got {analyses}"
    for a in analyses:
        assert _round_trips(surface, a)


def test_out_of_vocabulary_returns_empty_not_a_guess() -> None:
    """Numerals / non-Finnish-nominal surfaces are honest unknowns (empty)."""
    for surface in ("69", "d", "g", "123", ""):
        assert analyze_open(surface) == ()


def test_ambiguity_is_surfaced_not_ranked() -> None:
    """A surface with several sound analyses returns ALL of them, never one pick.

    ``viisumia`` is both the singular partitive of ``viisumi`` and (trivially,
    under the generator) the bare nominative of a hypothetical ``viisumia`` ---
    both round-trip, so both are returned.
    """
    analyses = analyze_open("viisumia")
    keys = {(a.lemma, a.case, a.number) for a in analyses}
    assert ("viisumi", MorphCase.PART, MorphNumber.SG) in keys
    assert len(keys) > 1  # ambiguity present, surfaced not collapsed


def test_results_are_deterministic_and_sorted() -> None:
    """Repeated calls return identical, stably-sorted tuples (pure function)."""
    first = analyze_open("hakemuksen")
    second = analyze_open("hakemuksen")
    assert first == second
    assert list(first) == sorted(first, key=MorphAnalysis._sort_key)


def test_analysis_excludes_lexical_flags_from_identity() -> None:
    """MorphAnalysis identity is (lemma, class, case, number) --- no flag dupes.

    Flag-invariant paradigm slots (e.g. the bare nominative, which does not
    depend on gradation/single_k) must NOT split into many duplicate analyses.
    """
    analyses = analyze_open("virasto")  # a clean vowel_final nominative
    nom = [
        a for a in analyses
        if a.case == MorphCase.NOM and a.number == MorphNumber.SG
    ]
    # One NOM analysis per consistent morph_class, not one per flag combination.
    assert len(nom) == len({(a.lemma, a.morph_class) for a in nom})


def test_casefold_and_whitespace_normalization() -> None:
    """Leading/trailing space + case are normalized like the closed index."""
    spaced = analyze_open("  Hakemuksen  ")
    bare = analyze_open("hakemuksen")
    assert spaced == bare
