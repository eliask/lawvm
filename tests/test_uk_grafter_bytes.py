from __future__ import annotations

from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.uk_grafter import (
    extract_eid_map_bytes,
    parse_uk_statute_ir_bytes,
)


_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-1">
      <Pnumber>1</Pnumber>
      <Text>Main text.</Text>
    </P1>
  </Body>
  <Schedules>
    <Schedule eId="schedule">
      <Number>SCHEDULE</Number>
      <Title>Further provision</Title>
      <ScheduleBody>
        <P1 eId="schedule-paragraph-1">
          <Pnumber>1</Pnumber>
          <Text>Schedule text.</Text>
        </P1>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""


def test_parse_uk_statute_ir_bytes_preserves_source_metadata() -> None:
    ir = parse_uk_statute_ir_bytes(
        _XML,
        statute_id="ukpga/2000/10",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/10/enacted/data.xml",
    )

    assert ir.statute_id == "ukpga/2000/10"
    assert ir.title == "Test Act"
    assert ir.metadata["source_path"].endswith("/ukpga/2000/10/enacted/data.xml")
    assert ir.metadata["version_label"] == "enacted"
    assert [child.kind for child in ir.body.children] == [IRNodeKind.SECTION]
    assert [schedule.kind for schedule in ir.supplements] == [IRNodeKind.SCHEDULE]


def test_parse_uk_statute_ir_bytes_uses_supplements() -> None:
    ir = parse_uk_statute_ir_bytes(
        _XML,
        statute_id="ukpga/2000/10",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/10/enacted/data.xml",
    )

    assert len(ir.supplements) == 1


def test_extract_eid_map_bytes_collects_schedule_eids() -> None:
    eid_data = extract_eid_map_bytes(_XML)

    values = set(eid_data["eid_map"].values())
    assert "section-1" in values
    assert "schedule" in values
    assert "schedule-paragraph-1" in values


def test_parse_uk_statute_ir_bytes_preserves_schedule_part_number_text() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Part Test Act</dc:title>
  </Metadata>
  <Body />
  <Schedules>
    <Schedule eId="schedule-2">
      <Number>SCHEDULE 2</Number>
      <Title>Orders</Title>
      <ScheduleBody>
        <Part eId="schedule-2-part-1">
          <Number><CommentaryRef Ref="c1"/>PART 1</Number>
          <Title>England and Wales</Title>
          <Pblock eId="schedule-2-part-1-crossheading-power">
            <Title>Power to make order</Title>
          </Pblock>
        </Part>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="ukpga/2003/31",
        version_label="oracle",
        source_path="https://www.legislation.gov.uk/ukpga/2003/31/data.xml",
    )

    schedule = ir.supplements[0]
    assert schedule.label == "SCHEDULE 2"
    assert [child.kind for child in schedule.children] == [IRNodeKind.PART]
    part = schedule.children[0]
    assert part.label == "PART 1"
    assert part.text == "England and Wales"
    assert [child.kind for child in part.children] == [IRNodeKind.CROSSHEADING]
