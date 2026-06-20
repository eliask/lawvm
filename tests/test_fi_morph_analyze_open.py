"""Tests for the OPEN-vocabulary morphological CANDIDATE GENERATOR (M1 inversion).

The generator hypothesizes ``(lemma, morph_class, case, number)`` candidates from
suffix stripping + stem inversion, then keeps ONLY those that round-trip through
M1 ``generate_forms``.  The KEY discipline boundary (Pro D6): round-trip
soundness proves a candidate is *generable*, NOT that its lemma is a real word.
So ``analyze_open`` is a CANDIDATE generator (lemmas are hypotheses, including
fabricated ones like ``säännöknen``); admissible-lemma FACTS come only from
:func:`analyze_admissible_lemmas`, which gates candidates against the closed
attested set (the known-head inventory / :class:`LemmaIndex`).

Invariants pinned here:

* GENERABILITY (round-trip property of CANDIDATES, not of admissible lemmas):
  every candidate ``analyze_open`` returns round-trips --- there exists a
  ``MorphEntry`` for its (lemma, morph_class) whose generation reproduces the
  surface for its (case, number).  The generator never returns a candidate M1
  would not produce, but generable != attested.
* RECALL (total over the generation space): every form M1 generates for the
  closed known-head inventory is recovered with its (lemma, case, number).
* ADMISSIBILITY: a known word resolves to a KNOWN lemma marked
  ``admissible_as_lemma=True``; a fabricated lemma is NEVER admissible (it
  carries ``unattested_generated_candidate`` / ``generated_from_known_entry``,
  ``admissible_as_lemma=False``); an unknown surface yields the honest EMPTY
  admissible result, never a fabricated lemma fact.

The closed attested set is the ~25 known heads (== the default ``LemmaIndex``
lemma set).  A real common noun that is NOT a head (``säännös``) is therefore
not yet attestable; that is the documented trigger for a later pinned in-house
lexicon (deliberately NOT built here), tested below.
"""

from __future__ import annotations

import pytest

from lawvm.finland.morphology.analyze import (
    LemmaAdmissibility,
    MorphAnalysis,
    analyze_admissible_lemmas,
    analyze_candidates,
    analyze_open,
)
from lawvm.finland.morphology.api import MorphCase, MorphEntry, MorphNumber
from lawvm.finland.morphology.generate import generate_forms
from lawvm.finland.morphology.heads import _HEADS, head_entry, is_known_head

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


def test_every_returned_candidate_round_trips() -> None:
    """GENERABILITY: every returned CANDIDATE round-trips (property of candidates).

    This is the round-trip property reframed as a property of CANDIDATES, not of
    admissible lemmas: a returned candidate is provably generable by M1, which is
    NOT the same as its lemma being attested.  Exercises the whole closed-head
    generation space plus open-vocab and out-of-vocab surfaces.
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
def test_open_vocab_candidate_is_present_and_round_trips(
    surface: str,
    lemma: str,
    case: MorphCase,
    number: MorphNumber,
) -> None:
    """The intended open-vocab analysis is among the generated CANDIDATES.

    Presence among candidates is a GENERATOR-recall property, NOT a claim that
    the candidate's lemma is an admissible fact (these open-vocab lemmas are not
    in the closed head set, so they are NOT admissible --- see the admissibility
    tests).  Ambiguity is surfaced; we assert the correct candidate is present
    and that every returned candidate round-trips.
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


# --------------------------------------------------------------------------- #
# Admissibility contract (Pro D6): candidate-generator output is NOT lemma fact.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("surface", "lemma"),
    [
        ("päätöksen", "päätös"),   # -Os->-Okse- head
        ("asetuksen", "asetus"),   # -Us->-Ukse- head
        ("oikeuden", "oikeus"),    # -Uus->-Ude- head (the trap)
        ("virastossa", "virasto"),  # vowel_final agency head
    ],
)
def test_known_word_resolves_to_admissible_known_lemma(
    surface: str, lemma: str,
) -> None:
    """A known (head) word yields its known lemma as an ADMISSIBLE fact.

    The attested lemma is present with ``ATTESTED_DETERMINISTIC`` /
    ``admissible_as_lemma=True``, and ``analyze_admissible_lemmas`` returns it.
    """
    assert is_known_head(lemma)
    candidates = analyze_candidates(surface)
    attested = [
        c
        for c in candidates
        if c.analysis.lemma == lemma and c.admissible_as_lemma
    ]
    assert attested, f"{surface!r}: expected admissible lemma {lemma}, got {candidates}"
    for c in attested:
        assert c.admissibility is LemmaAdmissibility.ATTESTED_DETERMINISTIC
    # The honest admissible-lemma surface returns the attested lemma and only it.
    adm_lemmas = {c.analysis.lemma for c in analyze_admissible_lemmas(surface)}
    assert adm_lemmas == {lemma}


def test_fabricated_lemma_is_never_admissible() -> None:
    """A round-trip-sound but FABRICATED lemma is never admissible as fact.

    ``säännöksen`` makes M1 generate ``säännöknen`` (a non-word) among others.
    It must be present as a CANDIDATE (the generator is liberal) but carry
    ``admissible_as_lemma=False`` --- treating it as a lemma fact would fabricate.
    """
    candidates = analyze_candidates("säännöksen")
    fabricated = [c for c in candidates if c.analysis.lemma == "säännöknen"]
    assert fabricated, "expected the fabricated säännöknen candidate to be present"
    for c in fabricated:
        assert c.admissible_as_lemma is False
        assert c.admissibility is LemmaAdmissibility.UNATTESTED_GENERATED_CANDIDATE


def test_unattested_real_word_is_honestly_unknown_not_fabricated() -> None:
    """A real common noun that is NOT a known head is an honest unknown lemma.

    ``säännös`` is a real word but not in the closed head set, so the closed
    attested set cannot admit it.  The honest result is the EMPTY admissible set
    (NOT a fabricated guess), and the real ``säännös`` candidate is correctly
    NOT admissible.  This is the documented trigger for the deferred pinned
    in-house lexicon (once it attests common nouns beyond the ~25 heads,
    ``säännös`` would flip to admissible).
    """
    assert not is_known_head("säännös")
    assert analyze_admissible_lemmas("säännöksen") == ()
    candidates = analyze_candidates("säännöksen")
    real = [c for c in candidates if c.analysis.lemma == "säännös"]
    assert real, "the real säännös candidate should still be proposed (round-trips)"
    for c in real:
        assert c.admissible_as_lemma is False


def test_generated_rival_reading_of_known_surface_is_not_admissible() -> None:
    """A fabricated rival reading of a KNOWN surface is generated-from-known.

    ``päätöksen`` IS explained by the attested lemma ``päätös``; the analyzer also
    proposes fabricated rivals (``päätöknen`` etc).  Those rivals are tagged
    ``GENERATED_FROM_KNOWN_ENTRY`` (the surface is attested via another lemma) and
    are NOT admissible.
    """
    candidates = analyze_candidates("päätöksen")
    rivals = [
        c
        for c in candidates
        if c.analysis.lemma != "päätös"
    ]
    assert rivals, "expected fabricated rival candidates for a known surface"
    for c in rivals:
        assert c.admissible_as_lemma is False
        assert c.admissibility is LemmaAdmissibility.GENERATED_FROM_KNOWN_ENTRY


def test_unknown_surface_yields_no_admissible_lemma() -> None:
    """An out-of-vocabulary surface yields the EMPTY admissible result, no guess."""
    for surface in ("69", "d", "g", "123", ""):
        assert analyze_candidates(surface) == ()
        assert analyze_admissible_lemmas(surface) == ()


def test_admissible_lemmas_are_a_subset_of_candidates() -> None:
    """``analyze_admissible_lemmas`` is exactly the admissible candidates.

    The deterministic admissible path is a strict, honest subset of the candidate
    set --- never invents an analysis the generator did not propose.
    """
    surface = "oikeuden"
    admissible = analyze_admissible_lemmas(surface)
    candidate_keys = {
        (c.analysis.lemma, c.analysis.morph_class, c.analysis.case)
        for c in analyze_candidates(surface)
    }
    for c in admissible:
        assert c.admissible_as_lemma is True
        assert (
            c.analysis.lemma,
            c.analysis.morph_class,
            c.analysis.case,
        ) in candidate_keys


def test_candidate_results_are_deterministic_and_sorted() -> None:
    """``analyze_candidates`` is a pure, stably-sorted function."""
    first = analyze_candidates("päätöksen")
    second = analyze_candidates("päätöksen")
    assert first == second
    assert list(first) == sorted(first, key=type(first[0])._sort_key)
