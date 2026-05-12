from __future__ import annotations

from lawvm.estonia.fetch import (
    AmendmentRef,
    RedactionsFeedDiagnostic,
    extract_amendment_refs,
    fetch_redactions_feed,
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
