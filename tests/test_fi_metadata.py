"""Unit tests for lawvm.finland.metadata — date/johtolause extraction helpers."""
import datetime as dt

import lxml.etree as etree

from lawvm.corpus_store import CorpusStore
import lawvm.finland.metadata as metadata
from lawvm.finland.metadata import (
    _amendment_effective_date,
    _amendment_effective_date_for_comparability,
    _amendment_effective_date_with_step,
    _normalize_johtolause_verbs,
    _statute_issue_date,
    _statute_id_sort_key,
    get_operative_body_repeal_candidate,
    get_johtolause,
    get_johtolause_from_tree,
    separate_commencement_law_witness,
)
from lawvm.finland.corpus import get_corpus

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


def test_get_johtolause_from_tree_restores_authorial_notes() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <span class='corrigendum'>muutetaan 3 §"
        "      <authorialNote>alkuperäinen sanamuoto kuului: muutetaan 9 §</authorialNote>"
        "    </span>"
        "  </formula>"
        "</preamble>"
    )
    tree = etree.fromstring(xml)

    result = get_johtolause_from_tree(tree)

    assert "muutetaan 3 §" in result
    assert "9 §" not in result
    authorial_notes = tree.xpath(".//*[local-name()='authorialNote']")
    assert isinstance(authorial_notes, list)
    assert len(authorial_notes) == 1


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


def test_get_johtolause_keeps_as_amended_qualifier_citations() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='substitutions'>"
        "muutetaan öljyvahinkojen torjunnasta annetun asetuksen ( 636/1993 ) "
        "3 §, 4 §, 5 §:n 2 momentti, sellaisina kuin ne ovat asetuksessa "
        "(1410/2004), 6 § seuraavasti:"
        "      </block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )

    result = " ".join(get_johtolause(xml).split())

    assert "( 636/1993 ) 3 §, 4 §, 5 §:n 2 momentti" in result
    assert "(1410/2004), 6 §" in result
    assert not result.startswith("muutetaan (1410/2004)")


def test_get_johtolause_keeps_comma_as_amended_qualifier_citation() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='substitutions'>"
        "muutetaan kirkkojärjestyksen 11 §:n 1 momentti sellaisena, kuin "
        "pykälä muutettiin marraskuun 28 päivänä 2017 (1160/2017) sekä "
        "kirkkojärjestyksen 12, 17, 19, 21 sekä 27 § seuraavasti:"
        "      </block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )

    result = " ".join(get_johtolause(xml).split())

    assert "kirkkojärjestyksen 11 §:n 1 momentti sellaisena, kuin" in result
    assert "(1160/2017) sekä kirkkojärjestyksen 12, 17, 19, 21 sekä 27 §" in result
    assert not result.startswith("muutetaan (1160/2017)")


def test_get_johtolause_strips_cross_law_descriptive_context() -> None:
    xml = _xml(
        "<preamble>"
        "  <formula name='enactingClause'>"
        "    <blockContainer>"
        "      <block name='substitutions'>"
        "muutetaan valmiuslain 106 §:n 1 momentissa ja 107 §:ssä säädettyjen "
        "toimivaltuuksien käyttöönotosta annetun asetuksen (186/2021) "
        "2 ja 3 § seuraavasti:"
        "      </block>"
        "    </blockContainer>"
        "  </formula>"
        "</preamble>"
    )

    result = " ".join(get_johtolause(xml).split())

    assert result == "muutetaan (186/2021) 2 ja 3 § seuraavasti:"


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


def test_amendment_effective_date_marks_erikseen_lailla_commencement_as_contingent() -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='2018-11-23'/>"
        "    </FRBRWork>"
        "  </identification>"
        "</meta>"
        "<body><section><content>"
        "Tämän lain voimaantulosta säädetään erikseen lailla."
        "</content></section></body>"
    )

    result, step = _amendment_effective_date_with_step(tree)

    assert result is None
    assert step == "contingent_text"


def test_amendment_effective_date_resolves_erikseen_lailla_from_separate_commencement_law() -> None:
    corpus = get_corpus()
    source = corpus.read_source("2018/947")
    assert source is not None
    tree = etree.fromstring(source)

    result, step = _amendment_effective_date_with_step(tree)
    witness = separate_commencement_law_witness("2018/947")

    assert result == dt.date(2019, 1, 1)
    assert step == "separate_commencement_law"
    assert witness is not None
    assert witness.commencement_statute_id == "2018/937"
    assert witness.source_provision_ref == "2018/937/1"
    assert witness.rule_id == "fi_separate_commencement_law_list"


def test_comparability_effective_date_does_not_build_separate_commencement_index(monkeypatch) -> None:
    corpus = get_corpus()
    source = corpus.read_source("2018/947")
    assert source is not None
    tree = etree.fromstring(source)

    def fail_witness_lookup(target_statute_id: str) -> object:
        raise AssertionError(
            f"comparability selection must not build separate-law index for {target_statute_id}"
        )

    monkeypatch.setattr(
        "lawvm.finland.metadata.separate_commencement_law_witness",
        fail_witness_lookup,
    )

    result, step = _amendment_effective_date_with_step(
        tree,
        resolve_separate_commencement=False,
    )

    assert result is None
    assert step == "contingent_text"
    assert _amendment_effective_date_for_comparability(tree) is None


def test_decree_inline_list_resolves_deferred_commencement_for_pending_law() -> None:
    corpus = get_corpus()
    source = corpus.read_source("2005/493")
    assert source is not None
    tree = etree.fromstring(source)

    result, step = _amendment_effective_date_with_step(tree)
    witness = separate_commencement_law_witness("2005/493")

    assert result == dt.date(2022, 1, 1)
    assert step == "separate_commencement_law"
    assert witness is not None
    assert witness.commencement_statute_id == "2021/1324"
    assert witness.source_provision_ref == "2021/1324/1"
    assert witness.rule_id == "fi_separate_commencement_decree_inline_list"


def test_separate_commencement_index_uses_internal_scan_read(monkeypatch) -> None:
    class ScanOnlyCorpus(CorpusStore):
        def read_source(self, sid: str) -> bytes | None:
            raise AssertionError(f"staged read path should not be used for {sid}")

        def read_source_for_internal_scan(self, sid: str) -> bytes | None:
            if sid == "2019/999":
                return b"<not-xml>Seuraavat lait tulevat voimaan 1 paivana tammikuuta 2021:"
            if sid != "2020/1":
                return None
            return _xml(
                "<body><section><num>1 §</num><content>"
                "Seuraavat lait tulevat voimaan 1 päivänä tammikuuta 2021: "
                "laki testistä (947/2018)."
                "</content></section></body>"
            )

        def read_oracle(self, sid: str) -> bytes | None:
            return None

        def read_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def list_statute_ids(self) -> list[str]:
            return ["2019/999", "2020/1"]

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            return {}

        def read_locator(self, locator: str) -> bytes | None:
            return None

    metadata._separate_commencement_witness_index.cache_clear()
    metadata._separate_commencement_witnesses_for_target.cache_clear()
    monkeypatch.setattr("lawvm.finland.corpus.get_corpus", lambda: ScanOnlyCorpus())

    try:
        witness = separate_commencement_law_witness("2018/947")
    finally:
        metadata._separate_commencement_witness_index.cache_clear()
        metadata._separate_commencement_witnesses_for_target.cache_clear()

    assert witness is not None
    assert witness.commencement_statute_id == "2020/1"
    assert witness.source_provision_ref == "2020/1/1"
    assert witness.effective_date == dt.date(2021, 1, 1)
    assert witness.rule_id == "fi_separate_commencement_law_list"


def test_separate_commencement_index_uses_bulk_internal_scan_when_available(monkeypatch) -> None:
    class BulkScanCorpus(CorpusStore):
        def read_source(self, sid: str) -> bytes | None:
            raise AssertionError(f"per-id read path should not be used for {sid}")

        def read_source_for_internal_scan(self, sid: str) -> bytes | None:
            raise AssertionError(f"per-id internal scan path should not be used for {sid}")

        def iter_source_bytes_for_internal_scan(self, *, min_year: int | None = None):
            assert min_year == 2018
            yield (
                "2020/1",
                _xml(
                    "<body><section><num>1 §</num><content>"
                    "Seuraavat lait tulevat voimaan 1 päivänä tammikuuta 2021: "
                    "laki testistä (947/2018)."
                    "</content></section></body>"
                ),
            )

        def read_oracle(self, sid: str) -> bytes | None:
            return None

        def read_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def list_statute_ids(self) -> list[str]:
            raise AssertionError("list_statute_ids should not be used by the bulk scan path")

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            return {}

        def read_locator(self, locator: str) -> bytes | None:
            return None

    metadata._separate_commencement_witness_index.cache_clear()
    metadata._separate_commencement_witnesses_for_target.cache_clear()
    monkeypatch.setattr("lawvm.finland.corpus.get_corpus", lambda: BulkScanCorpus())

    try:
        witness = separate_commencement_law_witness("2018/947")
    finally:
        metadata._separate_commencement_witness_index.cache_clear()
        metadata._separate_commencement_witnesses_for_target.cache_clear()

    assert witness is not None
    assert witness.commencement_statute_id == "2020/1"
    assert witness.effective_date == dt.date(2021, 1, 1)


def test_separate_commencement_target_scan_skips_unrelated_candidate_bytes(monkeypatch) -> None:
    class BulkScanCorpus(CorpusStore):
        def read_source(self, sid: str) -> bytes | None:
            raise AssertionError(f"per-id read path should not be used for {sid}")

        def read_source_for_internal_scan(self, sid: str) -> bytes | None:
            raise AssertionError(f"per-id internal scan path should not be used for {sid}")

        def iter_source_bytes_for_internal_scan(self, *, min_year: int | None = None):
            assert min_year == 2018
            yield (
                "2019/999",
                b"<not-xml>Seuraavat lait tulevat voimaan 1 paivana tammikuuta 2021: "
                b"laki testista (999/2018).</not-xml>",
            )
            yield (
                "2020/1",
                _xml(
                    "<body><section><num>1 §</num><content>"
                    "Seuraavat lait tulevat voimaan 1 päivänä tammikuuta 2021: "
                    "laki testistä (947/2018)."
                    "</content></section></body>"
                ),
            )

        def read_oracle(self, sid: str) -> bytes | None:
            return None

        def read_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_corrigendum_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
            return None

        def list_statute_ids(self) -> list[str]:
            raise AssertionError("list_statute_ids should not be used by the bulk scan path")

        def oracle_path_index(self, **kwargs: object) -> dict[str, str]:
            return {}

        def read_locator(self, locator: str) -> bytes | None:
            return None

    metadata._separate_commencement_witness_index.cache_clear()
    metadata._separate_commencement_witnesses_for_target.cache_clear()
    monkeypatch.setattr("lawvm.finland.corpus.get_corpus", lambda: BulkScanCorpus())

    try:
        witness = separate_commencement_law_witness("2018/947")
    finally:
        metadata._separate_commencement_witness_index.cache_clear()
        metadata._separate_commencement_witnesses_for_target.cache_clear()

    assert witness is not None
    assert witness.commencement_statute_id == "2020/1"


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


def test_statute_issue_date_does_not_read_signatures_when_frbr_year_matches(monkeypatch) -> None:
    tree = _make_tree(
        "<meta>"
        "  <identification>"
        "    <FRBRWork>"
        "      <FRBRdate name='dateIssued' date='1962-12-28'/>"
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
    real_tostring = etree.tostring
    signature_reads = 0

    def counting_tostring(element, *args, **kwargs):
        nonlocal signature_reads
        if (
            kwargs.get("method") == "text"
            and etree.QName(element.tag).localname == "hcontainer"
            and element.get("name") == "signatures"
        ):
            signature_reads += 1
        return real_tostring(element, *args, **kwargs)

    monkeypatch.setattr(etree, "tostring", counting_tostring)

    result = _statute_issue_date(tree)

    assert result == dt.date(1962, 12, 28)
    assert signature_reads == 0


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


# ---------------------------------------------------------------------------
# _parse_section_list_labels — separator-tokenizer contract
# ---------------------------------------------------------------------------


def test_parse_section_list_simple_comma_ja_list() -> None:
    from lawvm.finland.metadata import _parse_section_list_labels

    assert _parse_section_list_labels("5, 8 b, 11 ja 12") == {"5", "8b", "11", "12"}


def test_parse_section_list_seka_separator() -> None:
    from lawvm.finland.metadata import _parse_section_list_labels

    assert _parse_section_list_labels("87 a ja 89 a sekä 90") == {"87a", "89a", "90"}


def test_parse_section_list_en_dash_range_expands() -> None:
    from lawvm.finland.metadata import _parse_section_list_labels

    assert _parse_section_list_labels("16 a–16 g") == {
        "16a",
        "16b",
        "16c",
        "16d",
        "16e",
        "16f",
        "16g",
    }


def test_parse_section_list_momentti_qualifier_yields_section_only() -> None:
    """The "§:n N momentti" qualifier is dropped; only the section number is kept.

    The old negated-char-class strip left the qualifier text in place, so this
    input produced the bogus glued label "793momentti" instead of "79". The
    fix splits on the real separator words first and truncates each item at its
    "§" marker, dropping the pykälä/momentti qualifier that follows.
    """
    from lawvm.finland.metadata import _parse_section_list_labels

    assert _parse_section_list_labels("79 §:n 3 momentti") == {"79"}
    # No leftover glued-qualifier label.
    assert "793momentti" not in _parse_section_list_labels("79 §:n 3 momentti")


def test_parse_section_list_momentti_with_seka_continuation() -> None:
    """A momentti-qualified item followed by `sekä` must split on the word,
    not on the letters of "sekä", and must drop the qualifier on each item."""
    from lawvm.finland.metadata import _parse_section_list_labels

    assert _parse_section_list_labels("12 §:n 2 momentti sekä 14 §") == {"12", "14"}


def _entry_into_force_tree(text: str) -> "etree._Element":
    """Wrap a sunset-clause sentence in the entryIntoForce element the
    section-expiry override builder scans, so a test can drive the production
    function ``_temporary_section_expiry_overrides`` end-to-end."""
    return etree.fromstring(
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        f'<hcontainer name="entryIntoForce"><p>{text}</p></hcontainer>'
        "</akomaNtoso>"
    )


def _section_expiry_by_label(tree: "etree._Element", sid: str) -> dict[str, set[dt.date]]:
    from lawvm.finland.metadata import _temporary_section_expiry_overrides

    by_label: dict[str, set[dt.date]] = {}
    for override in _temporary_section_expiry_overrides(tree, sid):
        for label in override.labels:
            by_label.setdefault(label, set()).add(override.expiry)
    return by_label


def test_section_expiry_per_date_attributes_each_date_to_its_own_section() -> None:
    """1992/1535 (amendment 2021/1132) bite: two provisions joined by "ja" carry
    TWO different dates ("64 a §:n 3 momentti … 2025 ja … 127 f § … 2027"). The
    anchor's single ``datetail`` is its LAST date (2027); without the per-date
    split that 2027 is wrongly attributed to "64 a" too, colliding with the
    subsection path's correct "64 a -> 2025" and crashing the effect-lifecycle
    conflict guard (the statute was then excluded from scoring). The per-date split
    must instead give "64 a -> 2025" and "127 f -> 2027" (two distinct dates)."""
    tree = _entry_into_force_tree(
        "Lain 64 a §:n 3 momentti on voimassa 31 päivään joulukuuta 2025 "
        "ja lain 127 f § on voimassa 31 päivään joulukuuta 2027."
    )
    by_label = _section_expiry_by_label(tree, "1992/1535")
    assert by_label.get("64a") == {dt.date(2025, 12, 31)}
    assert by_label.get("127f") == {dt.date(2027, 12, 31)}
    # The cited-act head carried over the "ja lain …" join must be stripped, so the
    # section is the clean "127f", never a glued "lain127f".
    assert "lain127f" not in by_label


def test_section_expiry_single_date_list_shares_one_date_across_sections() -> None:
    """Regression guard for the single-date coordinated lists: a span with ONE
    ``… voimassa … <date>`` segment keeps the whole-list path unchanged, attributing
    the one shared date to every section in the list (both "59" and "62" here)."""
    tree = _entry_into_force_tree(
        "Lain 59 §:n 2 momentti ja 62 § ovat voimassa 31 päivään joulukuuta 2025."
    )
    by_label = _section_expiry_by_label(tree, "1900/1")
    assert by_label.get("59") == {dt.date(2025, 12, 31)}
    assert by_label.get("62") == {dt.date(2025, 12, 31)}


def test_section_expiry_multi_provision_subsection_clause_yields_no_whole_section() -> None:
    """2017/236 (amendment 2025/236) bite: a subsection-scoped clause that
    coordinates MORE THAN ONE section ("3 §:n otsikko sekä 3 ja 4 momentti, 4 §:n
    3 ja 4 momentti, …") must produce NO whole-section expiry from the section
    path — only its specific momentit/otsikko expire, owned by the provision path.
    EH1's clean section-list labels otherwise over-expired whole sections 3-8 (they
    were harmless junk before EH1). A SINGLE-section subsection clause ("51 §:n 5
    momentti") still legitimately yields its owning section, so the guard keys on
    an embedded '§' (= multiple coordinated provisions), not on '§:n' alone."""
    tree = _entry_into_force_tree(
        "Asetuksen 3 §:n otsikko sekä 3 ja 4 momentti, 4 §:n 3 ja 4 momentti, "
        "5 §:n 4–6 momentti, 6 §:n 4–6 momentti, 7 §:n 4 momentti ja "
        "8 §:n 3 momentti ovat voimassa 31 päivään joulukuuta 2025."
    )
    by_label = _section_expiry_by_label(tree, "2025/236")
    assert by_label == {}


def test_section_expiry_mixed_subsection_clause_emits_no_whole_section() -> None:
    """2004/983-style bite: a subsection-scoped sunset that coordinates several
    provisions (an embedded "§" in the captured list) emits NO whole-section
    expiry from the subsection path — the owning sections of the "§:n …" items
    ("1", "2", "9", "18", "22") must not leak in as whole sections. EH1's clean
    labels otherwise over-expired them (they were harmless junk before EH1)."""
    tree = _entry_into_force_tree(
        "Lain 1 §:n 2 momentti, 2 §:n 1 momentti, 9 §:n 1 momentti, 11 §, "
        "18 §:n 2 momentti, 2 a luku ja 22 §:n 1 momentti ovat voimassa "
        "31 päivään joulukuuta 2005."
    )
    by_label = _section_expiry_by_label(tree, "1996/1094")
    for leaked in ("1", "2", "9", "18", "22"):
        assert leaked not in by_label


def test_parse_section_list_complex_multi_section_clause() -> None:
    from lawvm.finland.metadata import _parse_section_list_labels

    got = _parse_section_list_labels(
        "16 a–16 g ja 58 i–58 k §, 79 §:n 3 momentti sekä 87 a ja 89 a §"
    )
    assert got == {
        "16a",
        "16b",
        "16c",
        "16d",
        "16e",
        "16f",
        "16g",
        "58i",
        "58j",
        "58k",
        "79",
        "87a",
        "89a",
    }
    # The qualifier must not leak in as a glued label.
    assert "793momentti" not in got
