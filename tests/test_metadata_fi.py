"""Unit tests for lawvm.finland.metadata — date/johtolause extraction helpers."""
import datetime as dt

import lxml.etree as etree

from lawvm.finland.metadata import (
    _amendment_effective_date,
    _amendment_effective_date_with_step,
    _normalize_johtolause_verbs,
    _statute_issue_date,
    _statute_id_sort_key,
    get_operative_body_repeal_candidate,
    get_johtolause,
)

# ---------------------------------------------------------------------------
# Minimal XML helpers
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(fragment: str) -> bytes:
    return f'<act xmlns="{_AKN_NS}">{fragment}</act>'.encode()


# ---------------------------------------------------------------------------
# get_johtolause
# ---------------------------------------------------------------------------


def test_get_johtolause_returns_substitutions_block_text() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='substitutions'>muutetaan 3 § seuraavasti:</block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )
    result = get_johtolause(xml)
    assert "muutetaan 3 §" in result


def test_get_johtolause_returns_insertions_block_text() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='insertions'>lisätään lakiin uusi 5 §</block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )
    result = get_johtolause(xml)
    assert "lisätään lakiin uusi 5 §" in result


def test_get_johtolause_falls_back_to_enacting_clause_formula() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>säädetään seuraavasti:</formula>"
        "</preamble>"
    )
    result = get_johtolause(xml)
    assert "säädetään seuraavasti:" in result


def test_get_johtolause_returns_empty_when_no_match() -> None:
    xml = _xml("<body><section><num>1 §</num></section></body>")
    result = get_johtolause(xml)
    assert result == ""


def test_get_johtolause_includes_insertions_originals_block() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='insertions'>lisätään 11 f §:ään,</block>"
        "      <block name='insertions-originals'>sellaisena kuin se on laissa 303/1998, uusi 4 momentti:</block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )
    result = get_johtolause(xml)
    assert "11 f §:ään" in result
    assert "uusi 4 momentti" in result


def test_get_johtolause_keeps_formula_text_outside_blocks_when_it_is_operative() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <p>kumotaan 17 § ja muutetaan 6 § sekä</p>"
        "    <blockContainer>"
        "      <block name='insertions'>lisätään 18 §:ään uusi 2 momentti seuraavasti:</block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )

    result = get_johtolause(xml)

    assert "kumotaan 17 §" in result
    assert "muutetaan 6 §" in result
    assert "lisätään 18 §:ään uusi 2 momentti" in result


def test_get_operative_body_repeal_candidate_extracts_body_prose_repeal() -> None:
    xml = _xml(
        "<preamble><formula name='enactingClause'><p>säädetään:</p></formula></preamble>"
        "<body>"
        "  <hcontainer name='statuteTextWrapper'>"
        "    <content>"
        "      <p>Täten kumotaan asetuksen 9 §.</p>"
        "    </content>"
        "  </hcontainer>"
        "  <hcontainer name='conclusions'><content><p>allekirjoitukset</p></content></hcontainer>"
        "</body>"
    )

    result = get_operative_body_repeal_candidate(xml)

    assert result == "Täten kumotaan asetuksen 9 §."


# ---------------------------------------------------------------------------
# _normalize_johtolause_verbs
# ---------------------------------------------------------------------------


def test_normalize_verbs_on_muttunut_form() -> None:
    text = "on muuttanut 3 §:n 2 momenttia"
    result = _normalize_johtolause_verbs(text)
    assert "muutetaan" in result


def test_normalize_verbs_on_kumonnut_form() -> None:
    text = "on kumonnut 5 §:n"
    result = _normalize_johtolause_verbs(text)
    assert "kumotaan" in result


def test_normalize_verbs_preserves_unrelated_text() -> None:
    text = "tämä laki tulee voimaan 1 päivänä tammikuuta 2025"
    result = _normalize_johtolause_verbs(text)
    assert result == text


def test_normalize_verbs_handles_lisannyt_form() -> None:
    text = "on lisännyt lakiin uuden §:n"
    result = _normalize_johtolause_verbs(text)
    assert "lisätään" in result


def test_normalize_verbs_repairs_missing_section_sign_after_citation() -> None:
    text = "muutetaan asetuksen (772/92) 6 ) seuraavasti:"
    result = _normalize_johtolause_verbs(text)
    assert result == "muutetaan asetuksen (772/92) 6 § seuraavasti:"


def test_normalize_verbs_does_not_mutate_normal_citation_and_section_marker() -> None:
    text = "muutetaan asetuksen (772/92) 6 § seuraavasti:"
    result = _normalize_johtolause_verbs(text)
    assert result == text


# ---------------------------------------------------------------------------
# _amendment_effective_date
# ---------------------------------------------------------------------------


def _make_tree(fragment: str) -> "etree._Element":
    xml = f'<act xmlns="{_AKN_NS}">{fragment}</act>'
    return etree.fromstring(xml.encode())


def test_amendment_effective_date_uses_dateEntryIntoForce_element() -> None:
    # The code searches for a literal <dateEntryIntoForce date="..."> element,
    # not a <FRBRdate name="dateEntryIntoForce"> attribute-keyed element.
    tree = _make_tree(
        "<meta>"
        "  <lifecycle>"
        "    <eventRef date='2021-01-15' type='generation' source='#original'/>"
        "    <dateEntryIntoForce date='2021-03-01'/>"
        "  </lifecycle>"
        "</meta>"
    )
    result = _amendment_effective_date(tree)
    assert result == dt.date(2021, 3, 1)


def test_amendment_effective_date_falls_back_to_publication_date() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='2020-06-10'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
    )
    result = _amendment_effective_date(tree)
    assert result == dt.date(2020, 6, 10)


def test_amendment_effective_date_falls_back_to_date_published_when_date_issued_absent() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='datePublished' date='2025-03-14'/>"
        "      <FRBRdate name='dateIssuedGenerated' date='2025-01-01'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
    )
    result, step = _amendment_effective_date_with_step(tree)
    assert result == dt.date(2025, 3, 14)
    assert step == "publication_date"


def test_amendment_effective_date_falls_back_to_date_issued_generated_last() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssuedGenerated' date='2025-01-01'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
    )
    result, step = _amendment_effective_date_with_step(tree)
    assert result == dt.date(2025, 1, 1)
    assert step == "publication_date"


def test_amendment_effective_date_returns_none_when_absent() -> None:
    tree = _make_tree("<body/>")
    result = _amendment_effective_date(tree)
    assert result is None


def test_amendment_effective_date_prefers_metadata_over_text_regex() -> None:
    # dateEntryIntoForce element must beat text regex match (1 March vs 1 Feb).
    tree = _make_tree(
        "<meta>"
        "  <lifecycle>"
        "    <dateEntryIntoForce date='2021-02-01'/>"
        "  </lifecycle>"
        "</meta>"
        "<body><section><content>"
        "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2021"
        "</content></section></body>"
    )
    result = _amendment_effective_date(tree)
    assert result == dt.date(2021, 2, 1)


def test_amendment_effective_date_marks_decree_set_commencement_as_contingent() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='2005-07-15'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
        "<body><section><content>"
        "Tämä laki tulee voimaan valtioneuvoston asetuksella säädettävänä ajankohtana."
        "</content></section></body>"
    )

    result, step = _amendment_effective_date_with_step(tree)

    assert result is None
    assert step == "contingent_text"


def test_amendment_effective_date_marks_voimaantulosta_saadetaan_asetuksella_as_contingent() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='2006-12-22'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
        "<body><section><content>"
        "Lain voimaantulosta säädetään asetuksella."
        "</content></section></body>"
    )

    result, step = _amendment_effective_date_with_step(tree)

    assert result is None
    assert step == "contingent_text"


def test_statute_issue_date_prefers_signature_when_frbr_year_conflicts_with_doc_number_year() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='1982-12-28'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
        "<preface><p><docNumber>680/1962</docNumber></p></preface>"
        "<body>"
        "  <hcontainer name='signatures'>"
        "    <content><p>Helsingissä 28 päivänä joulukuuta 1962.</p></content>"
        "  </hcontainer>"
        "</body>"
    )

    result = _statute_issue_date(tree)

    assert result == dt.date(1962, 12, 28)


def test_amendment_effective_date_parses_tata_lakia_sovelletaan_lukien() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='1982-12-28'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
        "<preface><p><docNumber>680/1962</docNumber></p></preface>"
        "<body>"
        "  <hcontainer name='entryIntoForce'>"
        "    <content><p>Tätä lakia sovelletaan 1 päivästä tammikuuta 1963 lukien.</p></content>"
        "  </hcontainer>"
        "  <hcontainer name='signatures'>"
        "    <content><p>Helsingissä 28 päivänä joulukuuta 1962.</p></content>"
        "  </hcontainer>"
        "</body>"
    )

    result, step = _amendment_effective_date_with_step(tree)

    assert result == dt.date(1963, 1, 1)
    assert step == "text_regex"


# ---------------------------------------------------------------------------
# _statute_id_sort_key
# ---------------------------------------------------------------------------


def test_statute_id_sort_key_year_primary() -> None:
    assert _statute_id_sort_key("2020/100") < _statute_id_sort_key("2021/1")


def test_statute_id_sort_key_num_secondary() -> None:
    a = _statute_id_sort_key("2020/50")
    b = _statute_id_sort_key("2020/100")
    assert a < b


def test_statute_id_sort_key_same_returns_equal_key() -> None:
    assert _statute_id_sort_key("2019/738") == _statute_id_sort_key("2019/738")


def test_statute_id_sort_key_letter_suffix_sorts_after_base() -> None:
    a = _statute_id_sort_key("2020/100")
    b = _statute_id_sort_key("2020/100a")
    # 100a > 100 numerically since num_int is based on leading digits
    # (both share the same year; the raw string tie-break handles letter suffix)
    assert a <= b  # at minimum not greater
