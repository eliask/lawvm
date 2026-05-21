from __future__ import annotations

from pathlib import Path

from lawvm.uk_legislation.uk_grafter import extract_eid_map, extract_eid_map_bytes


def test_extract_eid_map_skips_zombie_child_ordinals(tmp_path: Path) -> None:
    xml_path = tmp_path / "uk_oracle.xml"
    xml_path.write_text(
        """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Body>
    <Part id="part-1">
      <Number>Part 1</Number>
      <Title>General</Title>
      <P1 id="section-1">
        <Pnumber>1</Pnumber>
        <Title>Live section</Title>
      </P1>
      <P1 id="section-2" Status="Repealed">
        <Pnumber>2</Pnumber>
        <P1para><Text>. . .</Text></P1para>
      </P1>
      <P1group id="group-a">
        <Title>Grouped material</Title>
        <P1 id="section-3" Status="Repealed">
          <Pnumber>3</Pnumber>
          <P1para><Text>. . .</Text></P1para>
        </P1>
      </P1group>
    </Part>
  </Body>
</Legislation>
""",
        encoding="utf-8",
    )

    eid_data = extract_eid_map(xml_path)
    eid_map = eid_data["eid_map"]
    eids = set(eid_map.values())

    assert "section-1" in eids
    assert "section-2" not in eids
    assert "section-3" not in eids
    assert "body:part-1:section[1]" in eid_map
    assert "body:part-1:section[2]" not in eid_map
    assert "body:part-1:group-a:section[1]" not in eid_map


def test_extract_eid_map_records_oracle_physical_parent_eid_drift() -> None:
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Body>
    <P1 id="section-5">
      <Pnumber>5</Pnumber>
      <P1para>
        <P2 id="section-5-4">
          <Pnumber>4</Pnumber>
          <P2para>
            <P3 id="section-5-1-aa">
              <Pnumber>aa</Pnumber>
              <P3para><Text>for ballot papers to contain photographs;</Text></P3para>
            </P3>
          </P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    eid_data = extract_eid_map_bytes(xml)

    assert eid_data["physical_eid_aliases"] == {"section-5-1-aa": "section-5-4-aa"}
    assert eid_data["eid_map"]["body:section-5:subsection-4:paragraph-aa"] == "section-5-1-aa"
    observations = eid_data["oracle_identity_observations"]
    assert observations == [
        {
            "rule_id": "uk_oracle_physical_parent_eid_drift_aligned",
            "phase": "oracle_alignment",
            "family": "oracle_identity_drift",
            "original_eid": "section-5-1-aa",
            "physical_eid": "section-5-4-aa",
            "xml_tag": "P3",
            "physical_path_key": "body:section-5:subsection-4:paragraph-aa",
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_extract_eid_map_does_not_alias_schedule_parent_shape_without_section_root() -> None:
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedules>
    <Schedule id="schedule-1">
      <Number>Schedule 1</Number>
      <ScheduleBody>
        <P1 id="schedule-1-paragraph-12n3">
          <Pnumber>12C</Pnumber>
          <P1para>
            <P2 id="schedule-1-paragraph-12C-1">
              <Pnumber>1</Pnumber>
              <P2para><Text>Charging power.</Text></P2para>
            </P2>
          </P1para>
        </P1>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""

    eid_data = extract_eid_map_bytes(xml)

    assert eid_data["physical_eid_aliases"] == {}
    assert eid_data["visible_number_eid_aliases"] == {
        "schedule-1-paragraph-12n3": "schedule-1-paragraph-12c"
    }
    assert eid_data["oracle_identity_observations"][0]["rule_id"] == (
        "uk_oracle_visible_number_eid_alias_aligned"
    )


def test_extract_eid_map_records_schedule_visible_number_alias() -> None:
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedules>
    <Schedule id="schedule-2">
      <Number>Schedule 2</Number>
      <ScheduleBody>
        <P1 id="schedule-2-paragraph-21n1">
          <Pnumber>21ZA</Pnumber>
          <P1para><Text>The commissioner.</Text></P1para>
        </P1>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""

    eid_data = extract_eid_map_bytes(xml)

    assert eid_data["visible_number_eid_aliases"] == {
        "schedule-2-paragraph-21n1": "schedule-2-paragraph-21za"
    }
    assert eid_data["oracle_identity_observations"] == [
        {
            "rule_id": "uk_oracle_visible_number_eid_alias_aligned",
            "phase": "oracle_alignment",
            "family": "oracle_identity_drift",
            "original_eid": "schedule-2-paragraph-21n1",
            "visible_number_eid": "schedule-2-paragraph-21za",
            "xml_tag": "P1",
            "visible_number": "21za",
            "physical_path_key": "schedule-2:paragraph-21za",
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_extract_eid_map_records_body_descendant_visible_number_alias() -> None:
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Body>
    <P1 id="section-16">
      <Pnumber>16</Pnumber>
      <P1para>
        <P2 id="section-16-9">
          <Pnumber>
            <Substitution ChangeId="key-renumber" CommentaryRef="key-renumber">8</Substitution>
          </Pnumber>
          <P2para><Text>Renumbered provision.</Text></P2para>
        </P2>
      </P1para>
    </P1>
  </Body>
</Legislation>
"""

    eid_data = extract_eid_map_bytes(xml)

    assert eid_data["visible_number_eid_aliases"] == {"section-16-9": "section-16-8"}
    assert eid_data["oracle_identity_observations"] == [
        {
            "rule_id": "uk_oracle_visible_number_eid_alias_aligned",
            "phase": "oracle_alignment",
            "family": "oracle_identity_drift",
            "original_eid": "section-16-9",
            "visible_number_eid": "section-16-8",
            "xml_tag": "P2",
            "visible_number": "8",
            "physical_path_key": "body:section-16:subsection-8",
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]
