from __future__ import annotations

from lawvm.estonia.fetch import (
    AmendmentRef,
    RTXmlMetadataDiagnostic,
    RedactionsFeedDiagnostic,
    extract_effective_date,
    extract_amendment_refs,
    extract_grupi_id,
    extract_rt_pub_ref,
    extract_tekstiliik,
    fetch_redactions_feed,
    find_algtekst_aktviide,
    get_oracle_aktviide_for_pit,
)


def test_extract_amendment_refs_preserves_partial_commencement_slices_from_note_text() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2024-12-11</aktikuupaev>
        <joustumine>2025-09-01</joustumine>
        <avaldamismarge>
          <aktViide>https://www.riigiteataja.ee/akt/109012025001</aktViide>
          <kuvatavTekst>RT I, 21.12.2024, 1, osaliselt 01.01.2026, osaliselt 01.01.2027</kuvatavTekst>
        </avaldamismarge>
      </muutmismarge>
    </tyviseadus>
    """

    assert extract_amendment_refs(xml) == [
        AmendmentRef(aktViide="109012025001", passed="2024-12-11", joustumine="2025-09-01"),
        AmendmentRef(aktViide="109012025001", passed="2024-12-11", joustumine="2026-01-01"),
        AmendmentRef(aktViide="109012025001", passed="2024-12-11", joustumine="2027-01-01"),
    ]


def test_fetch_redactions_feed_records_fetch_failure_diagnostic(monkeypatch) -> None:
    diagnostics: list[RedactionsFeedDiagnostic] = []

    def fail_fetch_rt_url(url, archive, max_age_hours):
        raise RuntimeError("RT unavailable")

    monkeypatch.setattr("lawvm.estonia.fetch.fetch_rt_url", fail_fetch_rt_url)

    redactions = fetch_redactions_feed("123", archive=None, diagnostics_out=diagnostics)

    assert redactions == []
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.rule_id == "ee_redactions_feed_fetch_failed"
    assert diagnostic.family == "source_pathology"
    assert diagnostic.phase == "acquisition"
    assert diagnostic.grupi_id == "123"
    assert diagnostic.exception_type == "RuntimeError"
    assert diagnostic.strict_disposition == "block"
    assert diagnostic.as_detail()["rule_id"] == "ee_redactions_feed_fetch_failed"


def test_get_oracle_aktviide_for_pit_threads_redactions_feed_diagnostics(monkeypatch) -> None:
    diagnostics: list[RedactionsFeedDiagnostic] = []

    def fail_fetch_rt_url(url, archive, max_age_hours):
        raise RuntimeError("RT unavailable")

    monkeypatch.setattr("lawvm.estonia.fetch.fetch_rt_url", fail_fetch_rt_url)

    oracle_id = get_oracle_aktviide_for_pit(
        "123",
        "2026-01-01",
        archive=None,
        diagnostics_out=diagnostics,
    )

    assert oracle_id is None
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["ee_redactions_feed_fetch_failed"]


def test_find_algtekst_aktviide_records_unrequested_probe() -> None:
    diagnostics: list[RTXmlMetadataDiagnostic] = []

    assert find_algtekst_aktviide("group-1", archive=object(), diagnostics_out=diagnostics) is None

    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["ee_algtekst_probe_not_requested"]
    assert diagnostics[0].phase == "acquisition"
    assert diagnostics[0].detail == {"grupi_id": "group-1"}
    assert diagnostics[0].strict_disposition == "block"


def test_find_algtekst_aktviide_records_invalid_probe_boundary() -> None:
    diagnostics: list[RTXmlMetadataDiagnostic] = []

    assert (
        find_algtekst_aktviide(
            "group-1",
            archive=object(),
            probe_below="not-an-id",
            diagnostics_out=diagnostics,
        )
        is None
    )

    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "ee_algtekst_probe_boundary_invalid"
    ]
    assert diagnostics[0].phase == "acquisition"
    assert diagnostics[0].element_name == "probe_below"
    assert diagnostics[0].exception_type == "ValueError"


def test_find_algtekst_aktviide_records_bounded_no_match(monkeypatch) -> None:
    diagnostics: list[RTXmlMetadataDiagnostic] = []
    xml_by_candidate = {
        "11": b"""
        <tyviseadus xmlns="tyviseadus_1_10.02.2010">
          <terviktekstiGrupiID>group-1</terviktekstiGrupiID>
          <tekstiliik>terviktekst</tekstiliik>
        </tyviseadus>
        """,
        "1": b"""
        <tyviseadus xmlns="tyviseadus_1_10.02.2010">
          <terviktekstiGrupiID>other-group</terviktekstiGrupiID>
          <tekstiliik>algtekst</tekstiliik>
        </tyviseadus>
        """,
    }

    def fake_fetch_rt_xml(candidate, archive):
        del archive
        return xml_by_candidate[candidate]

    monkeypatch.setattr("lawvm.estonia.fetch.fetch_rt_xml", fake_fetch_rt_xml)

    assert (
        find_algtekst_aktviide(
            "group-1",
            archive=object(),
            probe_below="12",
            diagnostics_out=diagnostics,
        )
        is None
    )

    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["ee_algtekst_probe_no_match"]
    assert diagnostics[0].phase == "acquisition"
    assert diagnostics[0].detail == {
        "grupi_id": "group-1",
        "probe_below": "12",
        "attempted_candidates": 2,
        "fetch_failures": 0,
        "same_group_non_algtekst": 1,
        "different_group": 1,
    }


def test_find_algtekst_aktviide_finds_algtekst_without_no_match_diagnostic(monkeypatch) -> None:
    diagnostics: list[RTXmlMetadataDiagnostic] = []

    def fake_fetch_rt_xml(candidate, archive):
        del candidate, archive
        return b"""
        <tyviseadus xmlns="tyviseadus_1_10.02.2010">
          <terviktekstiGrupiID>group-1</terviktekstiGrupiID>
          <tekstiliik>algtekst</tekstiliik>
        </tyviseadus>
        """

    monkeypatch.setattr("lawvm.estonia.fetch.fetch_rt_xml", fake_fetch_rt_xml)

    assert (
        find_algtekst_aktviide(
            "group-1",
            archive=object(),
            probe_below="12",
            diagnostics_out=diagnostics,
        )
        == "11"
    )
    assert diagnostics == []


def test_rt_xml_metadata_extractors_record_parse_failure_diagnostics() -> None:
    diagnostics: list[RTXmlMetadataDiagnostic] = []
    bad_xml = b"<tyviseadus><broken></tyviseadus>"

    assert extract_grupi_id(bad_xml, diagnostics_out=diagnostics) is None
    assert extract_effective_date(bad_xml, diagnostics_out=diagnostics) == ""
    assert extract_tekstiliik(bad_xml, diagnostics_out=diagnostics) == ""
    assert extract_rt_pub_ref(bad_xml, diagnostics_out=diagnostics) == ""
    assert extract_amendment_refs(bad_xml, diagnostics_out=diagnostics) == []

    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "ee_rt_xml_metadata_parse_failed",
        "ee_rt_xml_metadata_parse_failed",
        "ee_rt_xml_metadata_parse_failed",
        "ee_rt_xml_metadata_parse_failed",
        "ee_rt_xml_metadata_parse_failed",
    ]
    assert [diagnostic.extractor for diagnostic in diagnostics] == [
        "extract_grupi_id",
        "extract_effective_date",
        "extract_tekstiliik",
        "extract_rt_pub_ref",
        "extract_amendment_refs",
    ]
    assert {diagnostic.exception_type for diagnostic in diagnostics} == {"ParseError"}
    assert all(diagnostic.family == "source_pathology" for diagnostic in diagnostics)
    assert all(diagnostic.phase == "extraction" for diagnostic in diagnostics)
    assert all(diagnostic.blocking is True for diagnostic in diagnostics)
    assert all(diagnostic.strict_disposition == "block" for diagnostic in diagnostics)
    assert all(diagnostic.quirks_disposition == "record" for diagnostic in diagnostics)
    assert diagnostics[0].as_detail()["rule_id"] == "ee_rt_xml_metadata_parse_failed"


def test_extract_amendment_refs_records_skipped_malformed_muutmismarge_entries() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2024-12-11</aktikuupaev>
        <joustumine>2025-09-01</joustumine>
      </muutmismarge>
      <muutmismarge>
        <aktikuupaev>2024-12-12</aktikuupaev>
        <joustumine>2025-09-02</joustumine>
        <avaldamismarge />
      </muutmismarge>
      <muutmismarge>
        <aktikuupaev>2024-12-13</aktikuupaev>
        <joustumine>2025-09-03</joustumine>
        <avaldamismarge>
          <aktViide>https://www.riigiteataja.ee/akt/109012025001</aktViide>
        </avaldamismarge>
      </muutmismarge>
    </tyviseadus>
    """
    diagnostics: list[RTXmlMetadataDiagnostic] = []

    refs = extract_amendment_refs(xml, diagnostics_out=diagnostics)

    assert refs == [
        AmendmentRef(aktViide="109012025001", passed="2024-12-13", joustumine="2025-09-03")
    ]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == [
        "ee_rt_xml_muutmismarge_missing_avaldamismarge",
        "ee_rt_xml_muutmismarge_missing_aktviide",
    ]
    assert [diagnostic.element_name for diagnostic in diagnostics] == [
        "avaldamismarge",
        "aktViide",
    ]
    assert all(diagnostic.extractor == "extract_amendment_refs" for diagnostic in diagnostics)


def test_extract_amendment_refs_uses_decree_namespace() -> None:
    xml = b"""
    <oigusakt xmlns="maarus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2019-02-14</aktikuupaev>
        <joustumine>2019-04-27</joustumine>
        <avaldamismarge>
          <aktViide>121022019003</aktViide>
        </avaldamismarge>
      </muutmismarge>
    </oigusakt>
    """

    assert extract_amendment_refs(xml) == [
        AmendmentRef(aktViide="121022019003", passed="2019-02-14", joustumine="2019-04-27"),
    ]


def test_extract_amendment_refs_preserves_multiple_dates_in_one_partial_commencement_phrase() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2012-12-12</aktikuupaev>
        <joustumine>2013-01-01</joustumine>
        <avaldamismarge>
          <aktViide>129122012001</aktViide>
          <kuvatavTekst>RT I, 29.12.2012, 1, osaliselt 01.04.2013 ja 01.07.2013</kuvatavTekst>
        </avaldamismarge>
      </muutmismarge>
    </tyviseadus>
    """

    assert extract_amendment_refs(xml) == [
        AmendmentRef(aktViide="129122012001", passed="2012-12-12", joustumine="2013-01-01"),
        AmendmentRef(aktViide="129122012001", passed="2012-12-12", joustumine="2013-04-01"),
        AmendmentRef(aktViide="129122012001", passed="2012-12-12", joustumine="2013-07-01"),
    ]


def test_extract_amendment_refs_does_not_treat_other_metadata_dates_as_effective_slices() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2024-12-11</aktikuupaev>
        <joustumine>2025-09-01</joustumine>
        <avaldamismarge>
          <aktViide>109012025001</aktViide>
          <kuvatavTekst>RT I, 21.12.2024, 1, j\xc3\xb5ust. 01.01.2026</kuvatavTekst>
        </avaldamismarge>
      </muutmismarge>
    </tyviseadus>
    """

    assert extract_amendment_refs(xml) == [
        AmendmentRef(aktViide="109012025001", passed="2024-12-11", joustumine="2025-09-01"),
    ]


def test_extract_amendment_refs_preserves_riigikogu_term_start_partial_slice() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <muutmismarge>
        <aktikuupaev>2014-01-22</aktikuupaev>
        <joustumine>2014-04-01</joustumine>
        <avaldamismarge>
          <aktViide>105022014001</aktViide>
          <kuvatavTekst>RT I, 05.02.2014, 1, osaliselt Riigikogu XIII koosseisu volituste algusest.</kuvatavTekst>
        </avaldamismarge>
      </muutmismarge>
    </tyviseadus>
    """

    assert extract_amendment_refs(xml) == [
        AmendmentRef(aktViide="105022014001", passed="2014-01-22", joustumine="2014-04-01"),
        AmendmentRef(aktViide="105022014001", passed="2014-01-22", joustumine="2015-03-24"),
    ]
