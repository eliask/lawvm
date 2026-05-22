from __future__ import annotations

import xml.etree.ElementTree as ET

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import ir_statute_from_dict
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.uk_grafter import (
    UKStatuteIR,
    _clean_num,
    _definition_ordered_list_term,
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


def test_clean_num_handles_fused_container_prefixes() -> None:
    assert _clean_num("Chapter 1A") == "1a"
    assert _clean_num("CHAPTER1A") == "1a"
    assert _clean_num("PartII") == "2"
    assert _clean_num("Paragraph12B") == "12b"
    assert _clean_num("Particular") == "particular"


def test_definition_ordered_list_term_prefers_source_language_quoted_term() -> None:
    ns = "http://www.legislation.gov.uk/namespaces/legislation"
    parent = ET.fromstring(
        f"""
        <Para xmlns="{ns}">
          <Text>“private sector employer” (“cyflogwr sector preifat”) means an employer that is not—</Text>
          <OrderedList Type="alpha">
            <ListItem><Para><Text>a public authority;</Text></Para></ListItem>
          </OrderedList>
        </Para>
        """
    )
    list_el = parent.find(f"{{{ns}}}OrderedList")

    assert list_el is not None
    assert _definition_ordered_list_term(parent, list_el) == "private sector employer"


def test_definition_ordered_list_term_ignores_qualifier_between_term_and_predicate() -> None:
    ns = "http://www.legislation.gov.uk/namespaces/legislation"
    parent = ET.fromstring(
        f"""
        <Para xmlns="{ns}">
          <Text>“relevant general policies”, in relation to a local transport authority, means the authority’s local transport strategy and—</Text>
          <OrderedList Type="alpha">
            <ListItem><Para><Text>where the authority is a local authority, the policies;</Text></Para></ListItem>
          </OrderedList>
        </Para>
        """
    )
    list_el = parent.find(f"{{{ns}}}OrderedList")

    assert list_el is not None
    assert _definition_ordered_list_term(parent, list_el) == "relevant general policies"


def test_visible_inline_citation_text_is_preserved_in_host_provision_text() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <P2 id="section-1-1">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>In this Act, "2013 Act" means the <Citation URI="http://www.legislation.gov.uk/id/anaw/2013/4">Local Government (Democracy) (Wales) Act 2013 (anaw 4)</Citation>; "relevant provision" means-</Text>
            <OrderedList Type="alpha">
              <ListItem><Para><Text>section 1;</Text></Para></ListItem>
            </OrderedList>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asc/test",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/asc/test/enacted/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert subsection.text == (
        'In this Act, "2013 Act" means the '
        'Local Government (Democracy) (Wales) Act 2013 (anaw 4) ; "relevant provision" means-'
    )
    assert subsection.children[0].text == "section 1;"
    visible_inline_rows = [
        row
        for row in ir.metadata["source_parse_observations"]
        if row["rule_id"] == "uk_visible_inline_text_preserved"
    ]
    assert visible_inline_rows == [
        {
            "rule_id": "uk_visible_inline_text_preserved",
            "family": "source_shape_preservation",
            "phase": "source_parse",
            "statute_id": "asc/test",
            "side": "enacted",
            "source_url": "https://www.legislation.gov.uk/asc/test/enacted/data.xml",
            "count": 1,
            "samples": (
                {
                    "tag": "Citation",
                    "text": "Local Government (Democracy) (Wales) Act 2013 (anaw 4)",
                },
            ),
            "reason": (
                "UK visible inline source tags such as Citation, CitationSubRef, and Term "
                "were preserved as host provision text while remaining non-addressable as "
                "standalone legal units."
            ),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    ]


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


def test_parse_uk_statute_ir_bytes_infers_generic_container_numbers_from_source_uri() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Generic Container Act</dc:title>
  </Metadata>
  <Body>
    <Part id="part-n2">
      <Number>Part</Number>
      <Title>Bus services</Title>
      <P1 eId="section-1">
        <Pnumber>1</Pnumber>
        <Text>Main text.</Text>
      </P1>
    </Part>
  </Body>
  <Schedules>
    <Schedule eId="schedule-n2">
      <Number>SCHEDULE</Number>
      <Title>Minor provision</Title>
      <ScheduleBody />
    </Schedule>
  </Schedules>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asp/2001/2",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/asp/2001/2/enacted/data.xml",
    )

    part = ir.body.children[0]
    schedule = ir.supplements[0]
    assert part.label == "2"
    assert schedule.label == "2"

    observations = [
        row
        for row in ir.metadata["source_parse_observations"]
        if row["rule_id"] == "uk_container_number_inferred_from_source_uri"
    ]
    assert len(observations) == 1
    assert observations[0]["count"] == 2
    assert observations[0]["samples"] == (
        {
            "kind": "part",
            "source_identifier": "part-n2",
            "original_label": "Part",
            "inferred_label": "2",
        },
        {
            "kind": "schedule",
            "source_identifier": "schedule-n2",
            "original_label": "SCHEDULE",
            "inferred_label": "2",
        },
    )


def test_parse_uk_statute_ir_bytes_uses_supplements() -> None:
    ir = parse_uk_statute_ir_bytes(
        _XML,
        statute_id="ukpga/2000/10",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/10/enacted/data.xml",
    )

    assert len(ir.supplements) == 1


def test_uk_statute_ir_wire_payload_does_not_export_schedules_alias() -> None:
    ir = UKStatuteIR(
        statute_id="ukpga/2000/10",
        version_label="enacted",
        title="Test Act",
        source_path="https://www.legislation.gov.uk/ukpga/2000/10/enacted/data.xml",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=[IRNode(kind=IRNodeKind.SCHEDULE, label="SCHEDULE")],
        metadata={},
    )

    payload = ir.to_dict()

    assert "supplements" in payload
    assert "schedules" not in payload
    round_tripped = ir_statute_from_dict(payload)
    assert [supplement.kind for supplement in round_tripped.supplements] == [IRNodeKind.SCHEDULE]


def test_extract_eid_map_bytes_collects_schedule_eids() -> None:
    eid_data = extract_eid_map_bytes(_XML)

    values = set(eid_data["eid_map"].values())
    assert "section-1" in values
    assert "schedule" in values
    assert "schedule-paragraph-1" in values


def test_extract_eid_map_bytes_ignores_text_fragment_ids() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Formula Anchor Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-11">
      <Pnumber>11</Pnumber>
      <P1para>
        <Text>For paragraphs (a) and (b) substitute </Text>
        <BlockAmendment PartialRefs="p10001">
          <Text id="p10001">in accordance with the formula-</Text>
          <Formula />
        </BlockAmendment>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    eid_data = extract_eid_map_bytes(xml)
    values = set(eid_data["eid_map"].values())

    assert "section-11" in values
    assert "p10001" not in values
    assert "p10001" not in eid_data["text_map"]


def test_parse_uk_statute_ir_bytes_preserves_local_text_before_child_paragraphs() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Lead Text Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-142">
      <Pnumber>142</Pnumber>
      <P1para>
        <P2 eId="section-142-1">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>The Welsh Ministers may direct the Independent Remuneration Panel for Wales that it must perform functions in relation to-</Text>
            <P3 eId="section-142-1-a">
              <Pnumber>a</Pnumber>
              <P3para><Text>the shadow council, and</Text></P3para>
            </P3>
            <P3 eId="section-142-1-b">
              <Pnumber>b</Pnumber>
              <P3para><Text>the principal council.</Text></P3para>
            </P3>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asc/2021/1",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/asc/2021/1/enacted/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert subsection.text == (
        "The Welsh Ministers may direct the Independent Remuneration Panel for Wales "
        "that it must perform functions in relation to-"
    )
    assert [child.label for child in subsection.children] == ["a", "b"]


def test_parse_uk_statute_ir_bytes_marks_local_text_after_child_paragraphs() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Tail Text Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <P2 eId="section-1-6">
          <Pnumber>6</Pnumber>
          <P2para>
            <Text>In a case where-</Text>
            <P3 eId="section-1-6-a">
              <Pnumber>a</Pnumber>
              <P3para><Text>condition A is met, and</Text></P3para>
            </P3>
            <P3 eId="section-1-6-b">
              <Pnumber>b</Pnumber>
              <P3para><Text>condition B is met,</Text></P3para>
            </P3>
            <Text>the old tail applies.</Text>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="ukpga/2000/1",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert subsection.text == "In a case where- the old tail applies."
    assert subsection.attrs["uk_post_child_text_tail"] == "the old tail applies."
    assert [child.label for child in subsection.children] == ["a", "b"]
    observations = ir.metadata["source_parse_observations"]
    tail_observation = next(
        row for row in observations if row["rule_id"] == "uk_post_child_text_tail_preserved"
    )
    assert tail_observation["count"] == 1
    assert tail_observation["samples"][0]["tail_text"] == "the old tail applies."


def test_parse_uk_statute_ir_bytes_preserves_subordinate_pgroup_heading_carrier() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Grouped Subsections Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-185">
      <Pnumber>185</Pnumber>
      <P1para>
        <P2group>
          <Title>Electronic monitoring requirement</Title>
          <P2 eId="section-185-4">
            <Pnumber>4</Pnumber>
            <P2para><Text>An electronic monitoring requirement applies.</Text></P2para>
          </P2>
        </P2group>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="ukpga/2000/10",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/10/enacted/data.xml",
    )

    section = ir.body.children[0]
    group = section.children[0]
    assert group.kind == IRNodeKind.PGROUP
    assert group.text == "Electronic monitoring requirement"
    assert group.attrs["source_tag"] == "P2group"
    assert group.attrs["source_rule_id"] == "uk_parse_subordinate_pgroup_heading_carrier"
    assert [child.label for child in group.children] == ["4"]


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


def test_parse_uk_statute_ir_bytes_preserves_uk_table_structure() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Table Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-166">
      <Pnumber>166</Pnumber>
      <P1para>
        <P2 eId="section-166-5">
          <Pnumber>5</Pnumber>
          <P2para>
            <Text>The following table has effect.</Text>
            <Table eId="section-166-5-table">
              <Tgroup>
                <Thead>
                  <Row eId="section-166-5-table-header">
                    <Entry>Entry</Entry>
                    <Entry>Meaning</Entry>
                  </Row>
                </Thead>
                <Tbody>
                  <Row eId="section-166-5-table-1">
                    <Entry>1</Entry>
                    <Entry>Original row</Entry>
                  </Row>
                </Tbody>
              </Tgroup>
            </Table>
            <Text>After the table.</Text>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="ukpga/2020/17",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert subsection.text == "The following table has effect. After the table."
    assert "Original row" not in subsection.text

    assert len(subsection.children) == 1
    table = subsection.children[0]
    assert table.kind == IRNodeKind.TABLE
    assert table.attrs["eId"] == "section-166-5-table"
    assert [row.kind for row in table.children] == [IRNodeKind.ROW, IRNodeKind.ROW]

    header_row = table.children[0]
    assert [cell.kind for cell in header_row.children] == [
        IRNodeKind.HEADER_CELL,
        IRNodeKind.HEADER_CELL,
    ]
    assert [cell.text for cell in header_row.children] == ["Entry", "Meaning"]

    body_row = table.children[1]
    assert [cell.kind for cell in body_row.children] == [IRNodeKind.CELL, IRNodeKind.CELL]
    assert [cell.text for cell in body_row.children] == ["1", "Original row"]


def test_parse_uk_statute_ir_bytes_preserves_definition_ordered_list_children() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Definition Test Act</dc:title>
  </Metadata>
  <Body>
    <P1 eId="section-42">
      <Pnumber>42</Pnumber>
      <P1para>
        <P2 eId="section-42-2">
          <Pnumber>2</Pnumber>
          <P2para>
            <Text>In this section-</Text>
            <UnorderedList Decoration="none" Class="Definition">
              <ListItem>
                <Para><Text>“coronavirus” means severe acute respiratory syndrome coronavirus 2;</Text></Para>
              </ListItem>
              <ListItem>
                <Para>
                  <Text>“relevant provision” means-</Text>
                  <OrderedList Decoration="parens" Type="alpha">
                    <ListItem><Para><Text>section 13(2),</Text></Para></ListItem>
                    <ListItem><Para><Text>section 19(2),</Text></Para></ListItem>
                    <ListItem><Para><Text>paragraph 1 of Schedule 8, or</Text></Para></ListItem>
                    <ListItem><Para><Text>paragraph 1(3) or 18(1) of Schedule 11.</Text></Para></ListItem>
                  </OrderedList>
                </Para>
              </ListItem>
            </UnorderedList>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
""".encode()

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="ukpga/2020/12",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2020/12/enacted/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert subsection.text == (
        "In this section- “coronavirus” means severe acute respiratory syndrome coronavirus 2; "
        "“relevant provision” means-"
    )
    assert [child.kind for child in subsection.children] == [
        IRNodeKind.ITEM,
        IRNodeKind.ITEM,
        IRNodeKind.ITEM,
        IRNodeKind.ITEM,
    ]
    assert [child.label for child in subsection.children] == [None, None, None, None]
    assert [child.attrs["definition_child_label"] for child in subsection.children] == ["a", "b", "c", "d"]
    assert subsection.children[3].text == "paragraph 1(3) or 18(1) of Schedule 11."
    assert subsection.children[3].attrs["source_rule_id"] == "uk_definition_ordered_list_child_preserved"
    assert subsection.children[3].attrs["definition_term"] == "relevant provision"
    assert ir.metadata["source_parse_observations"] == (
        {
            "rule_id": "uk_definition_ordered_list_child_preserved",
            "family": "source_shape_preservation",
            "phase": "source_parse",
            "statute_id": "ukpga/2020/12",
            "side": "enacted",
            "source_url": "https://www.legislation.gov.uk/ukpga/2020/12/enacted/data.xml",
            "count": 4,
            "samples": (
                {
                    "kind": "item",
                    "definition_term": "relevant provision",
                    "definition_child_label": "a",
                },
                {
                    "kind": "item",
                    "definition_term": "relevant provision",
                    "definition_child_label": "b",
                },
                {
                    "kind": "item",
                    "definition_term": "relevant provision",
                    "definition_child_label": "c",
                },
                {
                    "kind": "item",
                    "definition_term": "relevant provision",
                    "definition_child_label": "d",
                },
            ),
            "reason": "UK source XML structure was preserved as replay-addressable IR rather than flattened into host text.",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )


def test_parse_uk_statute_ir_bytes_preserves_definition_number_override_labels() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             DocumentMainType="ScottishAct" Year="2001" Number="2">
  <Body>
    <P1 id="section-82">
      <Pnumber>82</Pnumber>
      <P1para>
        <P2 id="section-82-1">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>In this Act— “local transport authority” means—</Text>
            <OrderedList Type="alpha" Decoration="parens">
              <ListItem NumberOverride="a"><Para><Text>a local authority;</Text></Para></ListItem>
              <ListItem NumberOverride="aa"><Para><Text>the Shetland Transport Partnership;</Text></Para></ListItem>
              <ListItem NumberOverride="ab">
                <Para><Text>the South-West of Scotland Transport Partnership;</Text></Para>
              </ListItem>
              <ListItem NumberOverride="b">
                <Para><Text>the Strathclyde Passenger Transport Authority ; or</Text></Para>
              </ListItem>
              <ListItem NumberOverride="c">
                <Para><Text>the West of Scotland Transport Partnership;</Text></Para>
              </ListItem>
            </OrderedList>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
""".encode()

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asp/2001/2",
        version_label="current",
        source_path="https://www.legislation.gov.uk/asp/2001/2/data.xml",
    )

    subsection = ir.body.children[0].children[0]
    assert [child.attrs["definition_child_label"] for child in subsection.children] == [
        "a",
        "aa",
        "ab",
        "b",
        "c",
    ]
    assert subsection.children[1].text == "the Shetland Transport Partnership;"
    assert subsection.children[3].text == "the Strathclyde Passenger Transport Authority ; or"
    assert ir.metadata["source_parse_observations"][0]["samples"][1][
        "definition_child_label"
    ] == "aa"


def test_parse_uk_statute_ir_bytes_preserves_schedule_unordered_list_entries() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Schedule Entry Test Act</dc:title>
  </Metadata>
  <Body />
  <Schedules>
    <Schedule eId="schedule-3">
      <Number>SCHEDULE 3</Number>
      <Title>Devolved public bodies</Title>
      <ScheduleBody>
        <P>
          <UnorderedList Decoration="none">
            <ListItem><Para><Text>Scottish Children's Reporter Administration</Text></Para></ListItem>
            <ListItem><Para><Text>Scottish Legal Aid Board</Text></Para></ListItem>
          </UnorderedList>
        </P>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
""".encode()

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asp/2000/7",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/asp/2000/7/enacted/data.xml",
    )

    schedule = ir.supplements[0]
    assert [child.kind for child in schedule.children] == [
        IRNodeKind.SCHEDULE_ENTRY,
        IRNodeKind.SCHEDULE_ENTRY,
    ]
    assert [child.label for child in schedule.children] == [None, None]
    assert [child.text for child in schedule.children] == [
        "Scottish Children's Reporter Administration",
        "Scottish Legal Aid Board",
    ]
    assert [child.attrs["source_ordinal"] for child in schedule.children] == ["1", "2"]
    assert schedule.children[0].attrs["source_rule_id"] == "uk_schedule_list_entry_preserved"
    assert schedule.children[0].attrs["source_tag"] == "ListItem"

    observations = ir.metadata["source_parse_observations"]
    schedule_observation = next(
        row for row in observations if row["rule_id"] == "uk_schedule_list_entry_preserved"
    )
    assert schedule_observation["count"] == 2
    assert schedule_observation["samples"][0] == {
        "kind": "schedule_entry",
        "source_tag": "ListItem",
        "source_ordinal": "1",
        "text": "Scottish Children's Reporter Administration",
    }
    assert schedule_observation["blocking"] is False


def test_parse_uk_statute_ir_bytes_preserves_schedule_p_text_entries() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Current Schedule Entry Test Act</dc:title>
  </Metadata>
  <Body />
  <Schedules>
    <Schedule eId="schedule-3">
      <Number>SCHEDULE 3</Number>
      <Title>Devolved public bodies</Title>
      <ScheduleBody>
        <P><Text>Scottish Children's Reporter Administration</Text></P>
        <P><Text>Scottish Legal Aid Board</Text></P>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
""".encode()

    ir = parse_uk_statute_ir_bytes(
        xml,
        statute_id="asp/2000/7",
        version_label="oracle",
        source_path="https://www.legislation.gov.uk/asp/2000/7/data.xml",
    )

    schedule = ir.supplements[0]
    assert [child.kind for child in schedule.children] == [
        IRNodeKind.SCHEDULE_ENTRY,
        IRNodeKind.SCHEDULE_ENTRY,
    ]
    assert [child.label for child in schedule.children] == [None, None]
    assert [child.attrs["source_tag"] for child in schedule.children] == ["P", "P"]
    assert [child.attrs["source_ordinal"] for child in schedule.children] == ["1", "2"]
    assert [child.text for child in schedule.children] == [
        "Scottish Children's Reporter Administration",
        "Scottish Legal Aid Board",
    ]
