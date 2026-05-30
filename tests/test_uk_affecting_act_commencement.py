"""Commencement lookup primitive for an affecting act's provisions (§6.8 resolver core)."""
from __future__ import annotations

from lawvm.uk_legislation.affecting_act_commencement import (
    affecting_provision_in_force,
    affecting_provision_start_dates,
    parse_affecting_provision_refs,
)

_NS = "http://www.legislation.gov.uk/namespaces/legislation"

# Minimal affecting-act XML: section 17 in force 2009-10-31, schedule 5 in force
# 2099-01-01 (genuinely future), section 16 with no start date.
_AFFECTING_XML = f"""<?xml version="1.0"?>
<Legislation xmlns="{_NS}">
  <Body>
    <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/1996/46/section/16">
      <P1/>
    </P1group>
    <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/1996/46/section/17"
             RestrictStartDate="2009-10-31">
      <P1/>
    </P1group>
  </Body>
  <Schedules>
    <Schedule IdURI="http://www.legislation.gov.uk/id/ukpga/1996/46/schedule/5"
              RestrictStartDate="2099-01-01"/>
  </Schedules>
</Legislation>
""".encode()


class TestParseRefs:
    def test_section_and_schedule(self) -> None:
        refs = parse_affecting_provision_refs("s. 17(2)(b) Sch. 7 Pt. 3")
        assert refs["section"] == {"17"}
        assert refs["schedule"] == {"7"}

    def test_schedule_only(self) -> None:
        refs = parse_affecting_provision_refs("Sch. 5")
        assert refs["schedule"] == {"5"}
        assert refs["section"] == set()

    def test_letter_suffix_section(self) -> None:
        assert parse_affecting_provision_refs("s. 23A")["section"] == {"23a"}


class TestStartDates:
    def test_resolves_section_start_date(self) -> None:
        assert affecting_provision_start_dates("s. 17", _AFFECTING_XML) == ["2009-10-31"]

    def test_unresolved_provision_yields_nothing(self) -> None:
        # section 16 has no RestrictStartDate, section 99 does not exist
        assert affecting_provision_start_dates("s. 16", _AFFECTING_XML) == []
        assert affecting_provision_start_dates("s. 99", _AFFECTING_XML) == []

    def test_no_xml_yields_nothing(self) -> None:
        assert affecting_provision_start_dates("s. 17", None) == []


class TestInForce:
    def test_in_force_when_start_date_before_as_of(self) -> None:
        assert affecting_provision_in_force("s. 17", _AFFECTING_XML, as_of="2026-05-30") is True

    def test_not_in_force_when_genuinely_future(self) -> None:
        # schedule 5 starts 2099 — genuinely uncommenced as of 2026
        assert affecting_provision_in_force("Sch. 5", _AFFECTING_XML, as_of="2026-05-30") is False

    def test_unknown_when_unresolved(self) -> None:
        # never guess: an unresolvable affecting provision is tri-state None
        assert affecting_provision_in_force("s. 16", _AFFECTING_XML, as_of="2026-05-30") is None
        assert affecting_provision_in_force("Sch. 2 para. 6", _AFFECTING_XML, as_of="2026-05-30") is None

    def test_all_cited_must_be_in_force(self) -> None:
        # cites section 17 (in force) AND schedule 5 (future) -> not fully in force
        assert affecting_provision_in_force("s. 17 Sch. 5", _AFFECTING_XML, as_of="2026-05-30") is False
