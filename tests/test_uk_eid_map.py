from __future__ import annotations

from pathlib import Path

from lawvm.uk_legislation.uk_grafter import extract_eid_map


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
