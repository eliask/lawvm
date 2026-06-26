"""Unit gate for the M1 Finnish morphology rule engine.

Asserts generated reference_v1 forms, vowel-harmony selection, the gradation
rules, the partitive-quality rule, and the fail-loud behaviour of ``classify``
on the genuine walls (the -Us split, bare -i simplexes).
"""

from __future__ import annotations

from lawvm.finland.morphology import (
    MorphCase,
    MorphEntry,
    MorphNumber,
    classify,
    generate_forms,
    head_entry,
)


def _forms(entry: MorphEntry) -> dict[str, str]:
    return {f.case.name: f.surface for f in generate_forms(entry)}


def _certainty(entry: MorphEntry) -> dict[str, str]:
    return {f.case.name: f.certainty for f in generate_forms(entry)}


def _pl_forms(entry: MorphEntry) -> dict[str, str]:
    fs = generate_forms(entry, numbers=(MorphNumber.PL,))
    return {f.case.name: f.surface for f in fs}


def _pl_certainty(entry: MorphEntry) -> dict[str, str]:
    fs = generate_forms(entry, numbers=(MorphNumber.PL,))
    return {f.case.name: f.certainty for f in fs}


# --------------------------------------------------------------------------- #
# Statute / agency heads
# --------------------------------------------------------------------------- #


def test_laki_single_k_paradigm() -> None:
    f = _forms(head_entry("laki"))
    assert f["GEN"] == "lain"  # single-k k->zero
    assert f["INE"] == "laissa"
    assert f["ELA"] == "laista"
    assert f["PART"] == "lakia"  # partitive -a (single short vowel)
    assert f["ILL"] == "lakiin"
    assert f["TRA"] == "laiksi"


def test_us_kse_deverbal() -> None:
    assert _forms(head_entry("asetus"))["GEN"] == "asetuksen"  # -Us->-Ukse-
    assert _forms(head_entry("asetus"))["INE"] == "asetuksessa"
    assert _forms(head_entry("asetus"))["PART"] == "asetusta"  # partitive -ta
    assert _forms(head_entry("päätös"))["GEN"] == "päätöksen"
    assert _forms(head_entry("sopimus"))["GEN"] == "sopimuksen"
    assert _forms(head_entry("säädös"))["GEN"] == "säädöksen"
    assert _forms(head_entry("keskus"))["GEN"] == "keskuksen"


def test_oikeus_ude_trap() -> None:
    """THE -us TRAP: oikeus must be -Ude-, never *oikeuksen."""
    f = _forms(head_entry("oikeus"))
    assert f["GEN"] == "oikeuden"
    assert f["GEN"] != "oikeuksen"
    assert f["PART"] == "oikeutta"  # partitive -tta
    assert f["INE"] == "oikeudessa"


def test_direktiivi_stable_loan() -> None:
    f = _forms(head_entry("direktiivi"))
    assert f["GEN"] == "direktiivin"
    assert f["INE"] == "direktiivissä"  # front harmony, no gradation


def test_ministerio_front_harmony() -> None:
    f = _forms(head_entry("ministeriö"))
    assert f["GEN"] == "ministeriön"
    assert f["INE"] == "ministeriössä"


def test_virasto_no_gradation() -> None:
    assert _forms(head_entry("virasto"))["GEN"] == "viraston"


def test_luku_chapter_single_k_v() -> None:
    """luku (chapter head): single-k realizes as v -> luvun, NOT *lukun."""
    f = _forms(head_entry("luku"))
    assert f["GEN"] == "luvun"
    assert f["INE"] == "luvussa"
    assert f["ELA"] == "luvusta"
    assert f["ILL"] == "lukuun"  # vowel stem keeps the strong grade
    assert f["GEN"] != "lukun"
    pl = _pl_forms(head_entry("luku"))
    assert pl["NOM"] == "luvut"
    assert pl["INE"] == "luvuissa"


def test_kaari_i_to_e_code() -> None:
    """kaari (historical code head): Kotus-26 -i noun, -e- oblique stem."""
    f = _forms(head_entry("kaari"))
    assert f["GEN"] == "kaaren"  # NOT *kaarin
    assert f["INE"] == "kaaressa"
    assert f["ELA"] == "kaaresta"
    assert f["ILL"] == "kaareen"
    assert f["ADE"] == "kaarella"
    assert f["TRA"] == "kaareksi"
    assert f["PART"] == "kaarta"  # type-26 -ta partitive on the consonant stem
    pl = _pl_forms(head_entry("kaari"))
    assert pl["NOM"] == "kaaret"
    assert pl["GEN"] == "kaarien"
    assert pl["PART"] == "kaaria"
    assert pl["INE"] == "kaarissa"


def test_jarjestys_muoto_name_heads() -> None:
    """järjestys / muoto: by-name constitutional-instrument heads, gradation-correct."""
    j = _forms(head_entry("järjestys"))
    assert j["GEN"] == "järjestyksen"  # -Us->-Ukse-
    assert j["INE"] == "järjestyksessä"
    m = _forms(head_entry("muoto"))
    assert m["GEN"] == "muodon"  # t->d gradation, NOT *muoton
    assert m["INE"] == "muodossa"


# --------------------------------------------------------------------------- #
# Gradation RULES (no stored form)
# --------------------------------------------------------------------------- #


def test_nk_to_ng_rule() -> None:
    """Helsinki -> Helsingin via the nk->ng cluster rule, not a stored form."""
    hki = MorphEntry(
        "place:helsinki", "Helsinki", "place", "vowel_final", gradation=True,
    )
    assert _forms(hki)["GEN"] == "Helsingin"


def test_nt_to_nn_rule() -> None:
    vh = MorphEntry(
        "agency:vh", "Verohallinto", "agency", "vowel_final", gradation=True,
    )
    assert _forms(vh)["GEN"] == "Verohallinnon"
    lk = MorphEntry(
        "agency:lk", "lautakunta", "agency", "vowel_final", gradation=True,
    )
    assert _forms(lk)["GEN"] == "lautakunnan"


def test_turku_single_k_flag() -> None:
    turku = MorphEntry(
        "place:turku", "Turku", "place", "vowel_final",
        gradation=True, single_k="zero",
    )
    assert _forms(turku)["GEN"] == "Turun"  # single-k FLAG
    assert _forms(turku)["INE"] == "Turussa"  # internal locative default


# --------------------------------------------------------------------------- #
# External-locative places
# --------------------------------------------------------------------------- #


def test_external_locative_place() -> None:
    tre = MorphEntry(
        "place:tampere", "Tampere", "place", "e_contract",
        gradation=False, locative_series="external",
    )
    f = _forms(tre)
    assert f["GEN"] == "Tampereen"
    assert f["ADE"] == "Tampereella"  # external locative series


# --------------------------------------------------------------------------- #
# Vowel harmony + partitive selection rules
# --------------------------------------------------------------------------- #


def test_vowel_harmony_back_vs_front() -> None:
    back = MorphEntry("c:back", "virasto", "common", "vowel_final")
    front = MorphEntry("c:front", "työ", "common", "vowel_final")
    assert _forms(back)["INE"].endswith("ssa")
    assert _forms(back)["ADE"].endswith("lla")
    assert _forms(front)["INE"].endswith("ssä")
    assert _forms(front)["ADE"].endswith("llä")
    assert _forms(head_entry("ministeriö"))["ADE"] == "ministeriöllä"


def test_partitive_quality_rule() -> None:
    # single short vowel -> -a ; consonant-final -> -ta ; long vowel -> -tta
    assert _forms(head_entry("laki"))["PART"] == "lakia"
    assert _forms(head_entry("asetus"))["PART"] == "asetusta"
    assert _forms(head_entry("oikeus"))["PART"] == "oikeutta"


# --------------------------------------------------------------------------- #
# Fail-loud (the walls)
# --------------------------------------------------------------------------- #


def test_classify_ambiguous_bare_i() -> None:
    """A bare ambiguous -i simplex returns ambiguous, never a silent paradigm."""
    c = classify("xoli")
    assert c.classification_status == "ambiguous"
    assert c.morph_class is None
    assert len(c.candidates) >= 2


def test_classify_us_split_needs_flag() -> None:
    """classify must NOT resolve the -Us split without head-class info."""
    for surface in ("asetus", "oikeus"):
        c = classify(surface)
        assert c.classification_status == "needs_flag"
        assert c.morph_class is None
        assert "-Us->-Ukse-" in c.candidates
        assert "-Uus->-Ude-" in c.candidates


def test_classify_categorical_resolves() -> None:
    assert classify("kaupunkilainen").morph_class == "-nen"
    assert classify("virasto").morph_class == "vowel_final"
    assert classify("mahdollisuus").morph_class == "-Uus->-Ude-"


def test_uus_illative_strong_grade_te() -> None:
    """oikeus ILL = oikeuteen (strong -te-), NOT the weak *oikeudeen."""
    f = _forms(head_entry("oikeus"))
    assert f["ILL"] == "oikeuteen"
    assert f["ILL"] != "oikeudeen"
    # The weak -Ude- stem is still what GEN/INE use (unchanged by the exception).
    assert f["GEN"] == "oikeuden"
    assert f["INE"] == "oikeudessa"
    # Carries a rule_id, not the unsupported placeholder.
    ill = next(x for x in generate_forms(head_entry("oikeus")) if x.case is MorphCase.ILL)
    assert ill.certainty == "deterministic"
    assert ill.rule_id.startswith("ILL")


# --------------------------------------------------------------------------- #
# Plurals (the plural -i- marker's stem/grade interactions)
# --------------------------------------------------------------------------- #


def test_plural_laki_i_to_e_hybrid() -> None:
    """laki: weak laei- (INE/ELA), strong lake- partitive, consonant lak- gen."""
    f = _pl_forms(head_entry("laki"))
    assert f["NOM"] == "lait"
    assert f["GEN"] == "lakien"  # consonant stem + -ien (hybrid)
    assert f["PART"] == "lakeja"  # vowel stem + -jA
    assert f["INE"] == "laeissa"  # weak grade (k gone) + i
    assert f["ELA"] == "laeista"


def test_plural_momentti_gradation_split() -> None:
    """momentti: weak momenti- (INE), strong momentt- gen / momentte- part."""
    f = _pl_forms(head_entry("momentti"))
    assert f["NOM"] == "momentit"
    assert f["GEN"] == "momenttien"  # strong tt
    assert f["PART"] == "momentteja"
    assert f["INE"] == "momenteissa"  # weak single t


def test_plural_pykala_a_drop() -> None:
    """pykälä: 3-syll -ä drops -> consonant stem; pykälien/pykäliä/pykälissä."""
    f = _pl_forms(head_entry("pykälä"))
    assert f["NOM"] == "pykälät"  # NOM carries no -i-, vowel kept
    assert f["GEN"] == "pykälien"
    assert f["PART"] == "pykäliä"
    assert f["INE"] == "pykälissä"
    assert f["ELA"] == "pykälistä"


def test_plural_kohta_a_drop_with_gradation() -> None:
    """kohta: first-syllable o -> -a drops; t->d gradation in the weak block."""
    f = _pl_forms(head_entry("kohta"))
    assert f["NOM"] == "kohdat"
    assert f["GEN"] == "kohtien"
    assert f["PART"] == "kohtia"
    assert f["INE"] == "kohdissa"  # weak grade kohd-


def test_plural_us_kse_legal_ten_genitive() -> None:
    """Deverbal -Ukse-/-Okse-: -ten genitive, -iA partitive (asetuksia)."""
    f = _pl_forms(head_entry("asetus"))
    assert f["NOM"] == "asetukset"
    assert f["GEN"] == "asetusten"
    assert f["PART"] == "asetuksia"
    assert f["INE"] == "asetuksissa"
    assert _pl_forms(head_entry("päätös"))["PART"] == "päätöksiä"
    assert _pl_forms(head_entry("keskus"))["GEN"] == "keskusten"


def test_plural_kept_vowel_j_marker() -> None:
    """o/u-final: the -i- surfaces as j between vowels -> virastojen/virastoja."""
    f = _pl_forms(head_entry("virasto"))
    assert f["GEN"] == "virastojen"
    assert f["PART"] == "virastoja"
    assert f["INE"] == "virastoissa"
    # gradation still applies in the weak INE/ELA block.
    assert _pl_forms(head_entry("hallinto"))["INE"] == "hallinnoissa"
    assert _pl_forms(head_entry("hallinto"))["GEN"] == "hallintojen"


def test_plural_a_to_o_two_syllable() -> None:
    """2-syllable -a with a/e/i first vowel raises to -o- before -i- (kaloja)."""
    kala = MorphEntry("c:kala", "kala", "common", "vowel_final")
    f = _pl_forms(kala)
    assert f["GEN"] == "kalojen"
    assert f["PART"] == "kaloja"
    assert f["INE"] == "kaloissa"


def test_plural_io_final_ita_iden() -> None:
    """-iö (ministeriö) takes -itA / -iden, not the -jA diphthong plural."""
    f = _pl_forms(head_entry("ministeriö"))
    assert f["GEN"] == "ministeriöiden"
    assert f["PART"] == "ministeriöitä"
    assert f["INE"] == "ministeriöissä"


def test_plural_e_contract_ita_iden() -> None:
    """-e contracted (ohje -> ohjee-): ohjeiden / ohjeita / ohjeissa."""
    f = _pl_forms(head_entry("ohje"))
    assert f["NOM"] == "ohjeet"
    assert f["GEN"] == "ohjeiden"
    assert f["PART"] == "ohjeita"  # back harmony from the o
    assert f["INE"] == "ohjeissa"


def test_plural_direktiivi_loan() -> None:
    f = _pl_forms(head_entry("direktiivi"))
    assert f["GEN"] == "direktiivien"
    assert f["PART"] == "direktiivejä"  # front harmony
    assert f["INE"] == "direktiiveissä"


def test_plural_every_form_has_rule_id() -> None:
    for f in generate_forms(head_entry("laki"), numbers=(MorphNumber.PL,)):
        assert f.rule_id
        assert f.rule_id.startswith("PL.")
        assert f.certainty == "deterministic"


# --------------------------------------------------------------------------- #
# Plurals that are GENUINELY irregular -> still fail loud (no silent guessing)
# --------------------------------------------------------------------------- #


def test_plural_oikeus_uus_stays_unsupported() -> None:
    """-Uus plural (oikeuksien/oikeuksia) abandons the -Ude- stem -> unsupported.

    The engine must NOT guess a wrong *oikeudien from the singular stem.
    """
    cert = _pl_certainty(head_entry("oikeus"))
    assert all(c == "unsupported" for c in cert.values())
    forms = _pl_forms(head_entry("oikeus"))
    assert all(s == "" for s in forms.values())  # never an invented surface


def test_plural_three_syllable_a_stays_unsupported() -> None:
    """3+ syllable -a (lautakunta) is a lexical -o-/drop split -> unsupported."""
    cert = _pl_certainty(head_entry("lautakunta"))
    assert all(c == "unsupported" for c in cert.values())


# --------------------------------------------------------------------------- #
# Compound vowel-harmony: harmony keys off the FINAL constituent (rightmost
# non-neutral vowel), not the first vowel encountered left-to-right.
# --------------------------------------------------------------------------- #


def test_compound_harmony_keys_off_final_constituent() -> None:
    """väliotsikko (front väli- + back otsikko) -> BACK suffixes.

    Regression guard for the left-to-right harmony bug that returned front at
    the first front vowel (ä) and emitted *väliotsikkossä / *väliotsikkollä.
    """
    vo = MorphEntry("c:vo", "väliotsikko", "common", "vowel_final")
    f = _forms(vo)
    assert f["INE"] == "väliotsikkossa"  # NOT *väliotsikkossä
    assert f["ELA"] == "väliotsikkosta"
    assert f["ADE"] == "väliotsikkolla"  # NOT *väliotsikkollä
    assert f["ABL"] == "väliotsikkolta"
    assert f["PART"] == "väliotsikkoa"  # NOT *väliotsikkoä


def test_compound_harmony_all_back_unchanged() -> None:
    """alaotsikko (all back) -> back suffixes (the simplex/agreeing-vowels case)."""
    ao = MorphEntry("c:ao", "alaotsikko", "common", "vowel_final")
    f = _forms(ao)
    assert f["INE"] == "alaotsikkossa"
    assert f["ADE"] == "alaotsikkolla"


def test_harmony_all_neutral_stem_defaults_front() -> None:
    """An all-neutral (only e/i) stem still defaults to FRONT (not regressed)."""
    from lawvm.finland.morphology.harmony import is_back_harmony

    assert is_back_harmony("liite") is False
    assert is_back_harmony("nimi") is False
    # Simplex front word with a single front vowel stays front.
    assert is_back_harmony("työ") is False
    # Simplex back word stays back.
    assert is_back_harmony("virasto") is True


def test_all_neutral_final_i_compound_is_a_classify_wall() -> None:
    """All-neutral-final -i compounds (kaivovesi) never reach generation.

    The rightmost-non-neutral heuristic would wrongly back-harmonize such a
    word, but a bare -i final is a classify-level wall -> it is returned as
    ``ambiguous`` and is never routed to a paradigm, so the heuristic's known
    limitation cannot surface a wrong form.
    """
    assert classify("kaivovesi").classification_status == "ambiguous"
    assert classify("vesi").classification_status == "ambiguous"


# --------------------------------------------------------------------------- #
# e_contract + gradation: type-48 inflected stem GEMINATES (it is the strong
# grade), it must NOT be weakened.
# --------------------------------------------------------------------------- #


def test_e_contract_gradation_geminates_not_weakens() -> None:
    """liite/nimike (type 48, gradating) -> geminate inflected stem.

    Regression guard for the bug that ran weaken_stem on the e_contract cluster
    and produced *liideen / *nimikeen.  Correct: liite->liitteen,
    nimike->nimikkeen (the nominative is the WEAK grade; inflection STRENGTHENS).
    """
    liite = MorphEntry("c:liite", "liite", "common", "e_contract", gradation=True)
    f = _forms(liite)
    assert f["GEN"] == "liitteen"  # NOT *liideen
    assert f["ILL"] == "liitteeseen"  # NOT *liideeseen
    assert f["INE"] == "liitteessä"
    assert f["PART"] == "liitettä"

    nimike = MorphEntry("c:ni", "nimike", "common", "e_contract", gradation=True)
    g = _forms(nimike)
    assert g["GEN"] == "nimikkeen"  # NOT *nimikeen
    assert g["ILL"] == "nimikkeeseen"
    assert g["PART"] == "nimikettä"


def test_e_contract_no_gradation_unchanged() -> None:
    """A non-gradating -e noun (ohje) is unaffected by the gemination fix."""
    f = _forms(head_entry("ohje"))
    assert f["GEN"] == "ohjeen"  # no gemination, no weakening
    assert f["ILL"] == "ohjeeseen"
    assert f["INE"] == "ohjeessa"


def test_generate_specific_cases() -> None:
    only_gen = generate_forms(head_entry("laki"), cases=(MorphCase.GEN,))
    assert len(only_gen) == 1
    assert only_gen[0].surface == "lain"
    assert only_gen[0].rule_id == "GEN.n"
