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
