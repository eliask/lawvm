"""Tests for the internal (same-statute) bare section-reference lane.

Covers the recall-gap shapes the ``[SECTION]`` bench reports as misses, plus the
lane-boundary exclusions (cross-statute by-id and by-name cases owned by other
lanes must emit NOTHING here).
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.internal_refs import recognize_internal_refs

_SID = "999/2020"


def _targets(text: str) -> list[tuple[str, int | None, str | None]]:
    """(section_label, subsection_num, item_label) for each emitted mention."""
    out: list[tuple[str, int | None, str | None]] = []
    for m in recognize_internal_refs(text, _SID):
        assert m.cite_kind is CiteKind.INTERNAL
        tr = m.target_provision_ref
        assert tr is not None
        assert tr.statute_id == _SID
        out.append((tr.section_label, tr.subsection_num, tr.item_label))
    return out


def _paths(text: str) -> list[str]:
    """provision_path (AKN ``chp_N__sec_M`` form) for each emitted mention."""
    out: list[str] = []
    for m in recognize_internal_refs(text, _SID):
        tr = m.target_provision_ref
        assert tr is not None
        out.append(tr.provision_path)
    return out


# ---------------------------------------------------------------------------
# DOES emit (internal shapes)
# ---------------------------------------------------------------------------


def test_edella_bare_coordinated_momentit() -> None:
    # "Edellä 1 ja 2 momentissa" → 2 internal momentti mentions (no §).
    got = _targets("Edellä 1 ja 2 momentissa tarkoitetun")
    assert got == [("", 1, None), ("", 2, None)]


def test_section_with_momentti() -> None:
    # "104 §:n 2 momentissa" → exactly one.
    got = _targets("104 §:n 2 momentissa säädetään")
    assert got == [("104", 2, None)]


def test_section_range_endash() -> None:
    # "108—110 §" → three sections.
    got = _targets("108—110 §:ää sovelletaan")
    assert got == [("108", None, None), ("109", None, None), ("110", None, None)]


def test_section_range_hyphen() -> None:
    got = _targets("108-110 §")
    assert got == [("108", None, None), ("109", None, None), ("110", None, None)]


def test_spaced_letter_suffix_section() -> None:
    # Body prose writes letter-suffix sections WITH a space ("115 a §"). The
    # surface must capture it, and the target section label normalizes to the
    # glued AKN eId form ("115a") so it resolves to <sec_115a>.
    assert _targets("115 a § koskee asiaa") == [("115a", None, None)]


def test_spaced_letter_suffix_with_self_reference_and_momentti() -> None:
    # "Tämän lain 47 a §:ssä" → internal, label glued to "47a".
    assert _targets("Tämän lain 47 a §:ssä säädetään") == [("47a", None, None)]


def test_spaced_letter_suffix_range_expands() -> None:
    # "106 a–106 e §:ää" → every member 106a..106e, glued labels.
    assert _targets("106 a–106 e §:ää sovelletaan") == [
        ("106a", None, None),
        ("106b", None, None),
        ("106c", None, None),
        ("106d", None, None),
        ("106e", None, None),
    ]


def test_coordinated_list_with_suffixed_members_tai() -> None:
    # COMPOUNDING case: a disjunctive ("tai") list with letter-suffixed members
    # must enumerate every member, not collapse to a section-less fallback.
    assert _targets("52 a, 52 d tai 52 e §:n nojalla") == [
        ("52a", None, None),
        ("52d", None, None),
        ("52e", None, None),
    ]


def test_coordinated_list_disjunctive_tai_enumerates_all() -> None:
    # A long disjunctive section list must enumerate every member (no collapse
    # to the bare statute root) — "tai" coordinates like "ja".
    assert _targets("114, 115, 133, 134, 139 tai 155 §:n nojalla") == [
        ("114", None, None),
        ("115", None, None),
        ("133", None, None),
        ("134", None, None),
        ("139", None, None),
        ("155", None, None),
    ]


def test_section_range_endash_enumerates_middle() -> None:
    # "16–18 §:ssä" → 16, 17 AND 18 (no dropped middle member).
    assert _targets("16–18 §:ssä") == [
        ("16", None, None),
        ("17", None, None),
        ("18", None, None),
    ]


def test_section_with_momentti_no_bare_fragment_duplicate() -> None:
    # "216 §:n 2 momentin" yields exactly ONE section+momentti ref; the momentti
    # tail must NOT also spawn a standalone bare-statute "2 momentissa" fragment.
    got = _targets("Tämän lain 216 §:n 2 momentin mukaan")
    assert got == [("216", 2, None)]
    # No section-less, momentti-less whole-statute fragment is emitted.
    assert all(not (sec == "" and sub is None) for sec, sub, _ in got)


def test_taman_lain_internal_self_reference() -> None:
    # "tämän lain N §" is INTERNAL (this act), not the by-name cross lane.
    assert _targets("tämän lain 5 §:ssä") == [("5", None, None)]


def test_tassa_laissa_internal_self_reference() -> None:
    assert _targets("tässä laissa 7 §:ssä") == [("7", None, None)]


def test_taman_lain_with_momentti() -> None:
    assert _targets("tämän lain 5 §:n 2 momentissa") == [("5", 2, None)]


def test_section_momentti_kohta_path() -> None:
    assert _targets("3 §:n 1 momentin 4 kohdassa") == [("3", 1, "4")]


def test_bare_section_inessive() -> None:
    assert _targets("7 §:ssä tarkoitettu") == [("7", None, None)]


def test_section_coordination() -> None:
    assert _targets("6 ja 8 §:ssä") == [("6", None, None), ("8", None, None)]


def test_section_coordination_two_members_distinct_targets() -> None:
    # "47 ja 49 §:ssä" → distinct 47 and 49 (NOT 48; no spurious member).
    assert _targets("47 ja 49 §:ssä") == [("47", None, None), ("49", None, None)]


def test_momentti_coordination_two_members() -> None:
    # "1 ja 2 momentissa" → distinct moments 1 and 2.
    assert _targets("Edellä 1 ja 2 momentissa") == [("", 1, None), ("", 2, None)]


def test_section_coordination_three_members() -> None:
    # "1, 2 ja 3 §" → three distinct section targets, no duplication.
    assert _targets("1, 2 ja 3 §:ssä") == [
        ("1", None, None),
        ("2", None, None),
        ("3", None, None),
    ]


def test_coordinated_members_share_one_surface() -> None:
    # Each coordinated member is its own mention but all carry the SAME whole-
    # coordination surface (per-member byte separation is the integration's job;
    # the lane carries the whole surface and one mention per resolved member).
    ms = recognize_internal_refs("47 ja 49 §:ssä", _SID)
    assert len(ms) == 2
    assert {m.surface_text for m in ms} == {"47 ja 49 §:ssä"}


def test_jaljempana_bare_momentti() -> None:
    assert _targets("jäljempänä 3 momentissa") == [("", 3, None)]


def test_mixed_bare_and_section_anchored() -> None:
    # The bare "1 momentissa" and the §-anchored "104 §:n 2 momentissa" both
    # surface; the bare lane does not double-emit the momentti inside the § cite.
    got = _targets("Edellä 1 momentissa ja 104 §:n 2 momentissa")
    assert ("104", 2, None) in got
    assert ("", 1, None) in got
    assert len(got) == 2


def test_confidence_exact_for_concrete_path() -> None:
    [m] = recognize_internal_refs("5 §:ssä", _SID)
    assert m.cite_confidence is CiteConfidence.EXACT


def test_surface_text_set_and_span_none() -> None:
    [m] = recognize_internal_refs("104 §:n 2 momentissa", _SID)
    assert m.surface_text == "104 §:n 2 momentissa"
    assert m.source_span is None  # integration re-anchors the span


def test_phrase_lemma_is_internal() -> None:
    [m] = recognize_internal_refs("5 §:ssä", _SID)
    assert m.phrase_lemma == "internal_section_ref"


def test_source_provision_ref_placeholder_is_same_statute() -> None:
    [m] = recognize_internal_refs("5 §:ssä", _SID)
    assert m.source_provision_ref.statute_id == _SID


# ---------------------------------------------------------------------------
# Chapter-qualified internal refs (``N luvun M §``)
# ---------------------------------------------------------------------------


def test_chapter_qualified_section() -> None:
    # "3 luvun 5 §" → internal mention, chapter 3 + section 5.
    assert _targets("3 luvun 5 §") == [("5", None, None)]
    assert _paths("3 luvun 5 §") == ["chp_3__sec_5"]


def test_chapter_qualified_section_inessive() -> None:
    assert _targets("3 luvun 5 §:ssä säädetään") == [("5", None, None)]
    assert _paths("3 luvun 5 §:ssä säädetään") == ["chp_3__sec_5"]


def test_chapter_qualified_section_momentti() -> None:
    # "2 luvun 4 §:n 1 momentti" → chapter 2, section 4, momentti 1.
    assert _targets("2 luvun 4 §:n 1 momentti") == [("4", 1, None)]
    assert _paths("2 luvun 4 §:n 1 momentti") == ["chp_2__sec_4__subsec_1"]


def test_chapter_qualified_section_momentti_kohta() -> None:
    assert _targets("3 luvun 5 §:n 2 momentin 4 kohta") == [("5", 2, "4")]
    assert _paths("3 luvun 5 §:n 2 momentin 4 kohta") == [
        "chp_3__sec_5__subsec_2__para_4"
    ]


def test_chapter_coordination() -> None:
    # "3 ja 4 luvun 5 §" → one mention per chapter, same section.
    assert _targets("3 ja 4 luvun 5 §") == [("5", None, None), ("5", None, None)]
    assert _paths("3 ja 4 luvun 5 §") == ["chp_3__sec_5", "chp_4__sec_5"]


def test_chapter_only_inessive() -> None:
    # "3 luvussa" (chapter, no section) → chapter-scoped STATUTE_ONLY mention.
    ms = recognize_internal_refs("3 luvussa tarkoitettu", _SID)
    assert len(ms) == 1
    [m] = ms
    assert m.cite_kind is CiteKind.INTERNAL
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    tr = m.target_provision_ref
    assert tr is not None
    assert tr.provision_path == "chp_3"
    assert tr.section_label == ""


def test_chapter_only_saannoksia() -> None:
    # "2 luvun säännöksiä" (chapter-only) → chapter-scoped STATUTE_ONLY.
    [m] = recognize_internal_refs("sovelletaan 2 luvun säännöksiä", _SID)
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.provision_path == "chp_2"


def test_taman_lain_chapter_qualified_internal() -> None:
    # "tämän lain 3 luvun 5 §" is internal (this act) — chapter carried.
    assert _paths("tämän lain 3 luvun 5 §") == ["chp_3__sec_5"]


def test_chapter_only_no_double_emit_when_attached_to_section() -> None:
    # The chapter prefix attached to a § citation is NOT also emitted as a
    # standalone chapter-only mention: exactly one mention.
    ms = recognize_internal_refs("3 luvun 5 §:ssä", _SID)
    assert len(ms) == 1


def test_chapter_only_without_number_not_emitted() -> None:
    # A bare "luku"/"luvussa" with no chapter number is too vague to claim.
    assert recognize_internal_refs("tässä luvussa tarkoitettu", _SID) == []


# ---------------------------------------------------------------------------
# Does NOT emit (cross-statute cases owned by other lanes)
# ---------------------------------------------------------------------------


def test_cross_statute_chapter_qualified_by_name_excluded() -> None:
    # "jätelain 3 luvun 5 §" → by-name cross-statute lane owns it; the chapter
    # prefix between the name head and the § is transparent to the exclusion.
    assert recognize_internal_refs("jätelain 3 luvun 5 §", _SID) == []


def test_cross_statute_chapter_only_by_name_excluded() -> None:
    assert recognize_internal_refs("jätelain 3 luvussa", _SID) == []


def test_cross_statute_chapter_qualified_by_id_excluded() -> None:
    assert recognize_internal_refs("(123/2020) 3 luvun 5 §", _SID) == []


def test_cross_statute_by_name_excluded() -> None:
    # "ympäristönsuojelulain 5 §" → by-name lane owns it. Emit nothing.
    assert recognize_internal_refs("ympäristönsuojelulain 5 §", _SID) == []


def test_cross_statute_by_name_inessive_excluded() -> None:
    assert recognize_internal_refs("luonnonsuojelulaissa 5 §:ssä", _SID) == []


def test_cross_statute_by_name_partitive_excluded() -> None:
    assert recognize_internal_refs("verolakia 12 §", _SID) == []


def test_bare_decree_head_excluded() -> None:
    assert recognize_internal_refs("asetuksen 3 §:ssä", _SID) == []


def test_statute_id_paren_excluded() -> None:
    # "(123/2020) 5 §" → plain-text by-id lane owns it. Emit nothing.
    assert recognize_internal_refs("(123/2020) 5 §", _SID) == []


def test_statute_id_paren_with_momentti_excluded() -> None:
    assert recognize_internal_refs("(123/2020) 5 §:n 2 momentissa", _SID) == []


def test_name_head_with_id_excluded() -> None:
    assert recognize_internal_refs("arvonlisäverolain (1501/1993) 5 §", _SID) == []


# ---------------------------------------------------------------------------
# Case-mismatched demonstrative does NOT make a by-name head "this act"
# (phantom foreign-chapter internal leak regression — statute 2006/479)
# ---------------------------------------------------------------------------


def test_case_mismatched_demonstrative_before_named_chapter_section_excluded() -> None:
    # ``tähän``(illative) does NOT agree in case with the genitive
    # ``arvopaperimarkkinalain`` head — it binds the downstream ``suhteessa``
    # (``tähän … suhteessa olevan henkilön``), not the law name. The chapter+§
    # tail is therefore a CROSS-STATUTE citation to arvopaperimarkkinalaki, NOT
    # an internal self-reference. It must emit NOTHING here (previously it leaked
    # a phantom internal ref carrying the FOREIGN chapter 6).
    text = (
        "tähän arvopaperimarkkinalain 6 luvun 10 §:n 2 momentissa "
        "tarkoitetussa suhteessa olevan henkilön"
    )
    assert recognize_internal_refs(text, _SID) == []


def test_case_mismatched_demonstrative_before_named_section_excluded() -> None:
    # Same defect without a chapter between name and §: ``tähän``(ill) +
    # genitive name head → still cross-statute, still nothing internal.
    assert recognize_internal_refs("tähän verolain 5 §:ssä", _SID) == []


def test_case_agreeing_demonstrative_chapter_qualified_still_internal() -> None:
    # ``tähän``(ill) + ``lakiin``(ill) DOES agree → genuine "this act" → the
    # demonstrative-override fires and the chapter-qualified § is INTERNAL (no
    # over-exclusion from the case gate).
    assert _paths("tähän lakiin 3 luvun 5 §") == ["chp_3__sec_5"]


def test_genuine_internal_chapter_section_no_name_head_still_emits() -> None:
    # A genuine internal ``N luvun M §`` with NO preceding external name head is
    # still recognized as internal (chapter carried) — the case gate only narrows
    # the demonstrative-override path, never the no-name-head path.
    assert _paths("noudatetaan 6 luvun 10 §:n 2 momentissa säädettyä") == [
        "chp_6__sec_10__subsec_2"
    ]


# ---------------------------------------------------------------------------
# Fail-loud / no false positives
# ---------------------------------------------------------------------------


def test_empty_text() -> None:
    assert recognize_internal_refs("", _SID) == []


def test_no_section_no_momentti() -> None:
    assert recognize_internal_refs("Tämä laki tulee voimaan.", _SID) == []


def test_bare_momentti_without_internal_leadin_not_emitted() -> None:
    # A bare "2 momentissa" with no Edellä/jäljempänä cue is too ambiguous to
    # claim as internal; the lane declines (prefer not-emitting over guessing).
    assert recognize_internal_refs("Poikkeuksena 2 momentissa", _SID) == []


def test_never_widens_to_whole_statute() -> None:
    # Every emitted mention names a concrete provision path (section or
    # subsection); no mention is a bare whole-statute self-reference.
    for m in recognize_internal_refs("Edellä 1 ja 2 momentissa", _SID):
        tr = m.target_provision_ref
        assert tr is not None
        assert tr.section_label or tr.subsection_num is not None or tr.item_label


# ---------------------------------------------------------------------------
# A comma-glued YEAR / decree-year is not parsed as a § section number
# ---------------------------------------------------------------------------


def test_year_word_not_glued_as_section_keeps_real_provision() -> None:
    # "vuoden 1971, 53 §:n 5 momentissa": the year 1971 must NOT become § 1971;
    # the real provision 53 §:n 5 mom survives.
    got = _targets("vuoden 1971, 53 §:n 5 momentissa säädettyä")
    assert ("1971", None, None) not in got
    assert got == [("53", 5, None)]


def test_year_word_before_coordinated_real_section() -> None:
    # "vuoden 1984 ja 16 §:n 3 momentissa": 1984 is a year, 16 §:n 3 mom is real.
    got = _targets("vuoden 1984 ja 16 §:n 3 momentissa")
    assert ("1984", None, None) not in got
    assert got == [("16", 3, None)]


def test_decree_id_year_part_not_glued_as_section() -> None:
    # "asetuksessa 1314/1996, 7 ja 17 §": the decree-id year 1996 must NOT become
    # § 1996; the real sections 7 and 17 survive.
    got = _targets("asetuksessa 1314/1996, 7 ja 17 §")
    assert ("1996", None, None) not in got
    assert got == [("7", None, None), ("17", None, None)]


def test_decree_id_year_part_before_coordinated_sections() -> None:
    got = _targets("asetuksissa 917/1981 ja 1314/1996 sekä 18, 19 ja 22 §")
    assert ("1981", None, None) not in got
    assert ("1996", None, None) not in got
    assert got == [("18", None, None), ("19", None, None), ("22", None, None)]


def test_leading_section_not_stripped_without_year_context() -> None:
    # A genuine leading section that happens to be 4 digits but is NOT preceded by
    # a year word / decree-id slash is left intact (no over-eager strip).
    got = _targets("Mitä 1234 §:ssä säädetään")
    assert got == [("1234", None, None)]


# ---------------------------------------------------------------------------
# An external-law section must not leak into a bogus INTERNAL self-target
# ---------------------------------------------------------------------------


def test_bracket_statute_id_excluded() -> None:
    # "(ampuma-aselain [1/1998] 20 §:n 3 momentti)": the bracket id [1/1998] is an
    # EXTERNAL-law anchor; 20 § is owned by the cross-statute by-id lane, never an
    # internal self-reference. Emit nothing here.
    assert recognize_internal_refs("ampuma-aselain [1/1998] 20 §:n 3 momentti", _SID) == []


def test_bracket_statute_id_with_momentti_excluded() -> None:
    assert recognize_internal_refs("[1/1998] 5 §:n 2 momentissa", _SID) == []


def test_coordinated_external_law_sections_all_excluded() -> None:
    # A section coordination governed by one external name head: every member is
    # external, not just the adjacent first one. None may leak as internal.
    text = (
        "sotilaskurinpidosta ja rikostorjunnasta Puolustusvoimissa annetun lain "
        "88 tai 93 §:n 1 momentissa tarkoitetun"
    )
    assert recognize_internal_refs(text, _SID) == []


def test_coordinated_external_law_long_list_excluded() -> None:
    # The governing id sits before a long coordination; the LAST member (72 §)
    # must still be recognised as external (coordination-aware lookback).
    text = (
        "sijoitusrahastolain (48/1999) 2 §:n 13 kohdassa, "
        "69 §:n 1 momentissa, 71 §:ssä, 72 §:ssä tarkoitettu"
    )
    assert recognize_internal_refs(text, _SID) == []


def test_two_digit_year_id_excluded() -> None:
    # A pre-2000 statute id with a 2-digit decree year ``(555/81)`` is just as
    # much an external-law anchor as the 4-digit form; the following § must not
    # leak as an internal self-reference.
    assert recognize_internal_refs("maa-aineslain (555/81) 3 §:ssä", _SID) == []


def test_two_digit_year_id_coordinated_tail_all_excluded() -> None:
    # The maa-aineslaki repro (1989/557 §124a): an external-law id with a 2-digit
    # year governs the WHOLE coordinated tail. Every member — including the bare
    # numbers after the abbreviated ``2 mom`` — binds to the external act, so the
    # internal lane emits NOTHING.
    text = "maa-aineslain (555/81) 3§:n 2 mom, 5, 6, 10-13, 13 a, 16 ja 21§:ssä"
    assert recognize_internal_refs(text, _SID) == []


def test_by_name_external_jarjestys_head_excluded() -> None:
    # ``valtiopäiväjärjestyksen 67 §`` is a by-name EXTERNAL statute (its head is
    # a named instrument ending in ``-järjestyksen``, not ``laki``); the § must
    # not leak as a bogus internal self-reference.
    assert recognize_internal_refs("valtiopäiväjärjestyksen 67 §:ssä", _SID) == []


def test_by_name_external_tyojarjestys_head_excluded() -> None:
    assert recognize_internal_refs("eduskunnan työjärjestyksen 5 §:ssä", _SID) == []


def test_by_name_external_kaari_genitive_head_excluded() -> None:
    # ``oikeudenkäymiskaaren 12 luvun 32 §`` is a by-name EXTERNAL code (its head
    # ends in the ``-kaari`` oblique ``kaaren``, owned by the cross-statute lane);
    # the § must not leak as a bogus internal self-reference.
    assert (
        recognize_internal_refs("oikeudenkäymiskaaren 12 luvun 32 §:ää", _SID) == []
    )


def test_by_name_external_kaari_inessive_head_excluded() -> None:
    assert (
        recognize_internal_refs("oikeudenkäymiskaaressa 17 luvun 65 §:ssä", _SID) == []
    )


def test_bare_internal_section_still_recognized_no_kaari_head() -> None:
    # Guard against over-exclusion: a genuine bare internal section with NO
    # preceding name head still resolves to an internal self-reference.
    got = _targets("12 §:ssä säädetään")
    assert ("12", None, None) in got


def test_rikoslaki_mixed_chapter_coordination_all_excluded() -> None:
    # The rikoslaki repro (2011/953 §25a): an external id governs a long list that
    # interleaves §-bearing, chapter-only (``20 luvussa``) and chapter-qualified
    # (``21 luvun 1—3 tai 6 §``) members. None may leak as internal — neither the
    # chapter-only nor the later chapter-qualified members.
    text = (
        "rikoslain (39/1889) 17 luvun 18, 18 a tai 19 §:ssä, 20 luvussa, "
        "21 luvun 1—3 tai 6 §:ssä, 31 luvun 2 §:ssä tai "
        "50 luvun 1, 2, 3, 4 tai 4 a §:ssä tarkoitetusta rikoksesta"
    )
    assert recognize_internal_refs(text, _SID) == []


def test_internal_coordination_not_excluded_by_coord_lookback() -> None:
    # A genuine internal section coordination (no governing external anchor) must
    # still be recognised — the coordination-aware lookback only excludes when an
    # external id / name head governs the run.
    got = _targets("Tämän lain 2 §:ssä, 5 §:ssä ja 7 §:ssä säädetään")
    assert ("2", None, None) in got
    assert ("7", None, None) in got


def test_section_absent_above_max_declined_on_trusted_tree() -> None:
    # An anaphoric external-law section (governing phrase in an EARLIER sentence,
    # no local anchor) that is ABSENT from the statute's own tree and ABOVE its
    # max section is declined to a fail-loud STATUTE_ONLY — never a bogus concrete
    # internal target. known_sections is the trusted (fully eId'd) section set.
    known = frozenset({str(n) for n in range(1, 47)})  # statute has sections 1..46
    ms = recognize_internal_refs("Pääesikunnan 93 §:n 1 momentissa", _SID, known_sections=known)
    assert len(ms) == 1
    tr = ms[0].target_provision_ref
    assert tr is not None
    assert tr.section_label == ""  # declined, not a bogus sec=93
    assert ms[0].cite_confidence is CiteConfidence.STATUTE_ONLY


def test_section_present_not_declined_on_trusted_tree() -> None:
    # A section that DOES exist in the trusted tree resolves normally.
    known = frozenset({str(n) for n in range(1, 47)})
    got = _targets_with_sections("5 §:n 2 momentissa", known)
    assert got == [("5", 2, None)]


def test_section_within_range_hole_not_declined() -> None:
    # A section absent from the tree but WITHIN [1, max] (a likely letter-suffix
    # the structure builder missed, not a leak) is NOT declined — recall over a
    # speculative decline. Here the tree lacks "9" but it is below max=46.
    known = frozenset({str(n) for n in range(1, 47)} - {"9"})
    got = _targets_with_sections("9 §:ssä", known)
    assert got == [("9", None, None)]


def test_existence_guard_inert_without_trusted_sections() -> None:
    # No known_sections (non-consolidated / un-eId'd body) => guard never fires,
    # so recall is preserved even for a high section number.
    got = _targets("93 §:n 1 momentissa")
    assert got == [("93", 1, None)]


def _targets_with_sections(
    text: str, known: frozenset[str]
) -> list[tuple[str, int | None, str | None]]:
    out: list[tuple[str, int | None, str | None]] = []
    for m in recognize_internal_refs(text, _SID, known_sections=known):
        tr = m.target_provision_ref
        assert tr is not None
        out.append((tr.section_label, tr.subsection_num, tr.item_label))
    return out
