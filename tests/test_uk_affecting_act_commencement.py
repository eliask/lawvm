"""Commencement lookup primitive for an affecting act's provisions (§6.8 resolver core)."""
from __future__ import annotations

from lawvm.uk_legislation.affecting_act_commencement import (
    affecting_provision_in_force,
    affecting_provision_start_dates,
    parse_affecting_provision_refs,
)

_NS = "http://www.legislation.gov.uk/namespaces/legislation"

# Minimal affecting-act XML: section 17 in force 2009-10-31, schedule 5 in force
# 2099-01-01 (genuinely future), section 16 with no start date. Schedules also
# pin that root, Part, and paragraph dates stay distinct.
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
    <Schedule IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/1"
              RestrictStartDate="2007-03-01">
      <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/1/paragraph/p13"
               RestrictStartDate="1999-09-08"/>
    </Schedule>
    <Schedule IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/3"
              RestrictStartDate="2022-07-01">
      <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/3/paragraph/1A"
               RestrictStartDate="2012-08-01"/>
    </Schedule>
    <Schedule IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/4">
      <Part IdURI="http://www.legislation.gov.uk/id/ukpga/1999/8/schedule/4/part/III"
            RestrictStartDate="2015-04-01"/>
    </Schedule>
    <Schedule IdURI="http://www.legislation.gov.uk/id/ukpga/1996/46/schedule/5"
              RestrictStartDate="2099-01-01"/>
  </Schedules>
</Legislation>
""".encode()


class TestParseRefs:
    def test_section_and_schedule(self) -> None:
        refs = parse_affecting_provision_refs("s. 17(2)(b) Sch. 7 Pt. 3")
        assert refs["section"] == {"17"}
        assert refs["schedule"] == set()
        assert refs["schedule_part"] == {"7/iii"}

    def test_schedule_only(self) -> None:
        refs = parse_affecting_provision_refs("Sch. 5")
        assert refs["schedule"] == {"5"}
        assert refs["section"] == set()

    def test_letter_suffix_section(self) -> None:
        assert parse_affecting_provision_refs("s. 23A")["section"] == {"23a"}

    def test_schedule_trail_stops_before_next_top_level_ref(self) -> None:
        refs = parse_affecting_provision_refs("Sch. 5 and s. 17 para. 2")
        assert refs["schedule"] == {"5"}
        assert refs["schedule_paragraph"] == set()

    def test_schedule_paragraph_ref_is_specific(self) -> None:
        refs = parse_affecting_provision_refs("Sch. 3 para. 1A")
        assert refs["schedule"] == set()
        assert refs["schedule_paragraph"] == {"3/1a"}


class TestStartDates:
    def test_resolves_section_start_date(self) -> None:
        assert affecting_provision_start_dates("s. 17", _AFFECTING_XML) == ["2009-10-31"]

    def test_unresolved_provision_yields_nothing(self) -> None:
        # section 16 has no RestrictStartDate, section 99 does not exist
        assert affecting_provision_start_dates("s. 16", _AFFECTING_XML) == []
        assert affecting_provision_start_dates("s. 99", _AFFECTING_XML) == []

    def test_no_xml_yields_nothing(self) -> None:
        assert affecting_provision_start_dates("s. 17", None) == []

    def test_schedule_root_date_does_not_inherit_paragraph_date(self) -> None:
        assert affecting_provision_start_dates("Sch. 1", _AFFECTING_XML) == ["2007-03-01"]

    def test_schedule_paragraph_date_is_exact(self) -> None:
        assert affecting_provision_start_dates("Sch. 1 para. 13", _AFFECTING_XML) == [
            "1999-09-08"
        ]
        assert affecting_provision_start_dates("Sch. 3 para. 1A", _AFFECTING_XML) == [
            "2012-08-01"
        ]

    def test_schedule_part_date_accepts_arabic_source_ref(self) -> None:
        assert affecting_provision_start_dates("Sch. 4 Pt. 3", _AFFECTING_XML) == [
            "2015-04-01"
        ]


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

    def test_schedule_root_and_paragraph_have_separate_in_force_results(self) -> None:
        assert affecting_provision_in_force("Sch. 3", _AFFECTING_XML, as_of="2020-01-01") is False
        assert (
            affecting_provision_in_force("Sch. 3 para. 1A", _AFFECTING_XML, as_of="2020-01-01")
            is True
        )

    def test_all_cited_must_be_in_force(self) -> None:
        # cites section 17 (in force) AND schedule 5 (future) -> not fully in force
        assert affecting_provision_in_force("s. 17 Sch. 5", _AFFECTING_XML, as_of="2026-05-30") is False
