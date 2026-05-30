from __future__ import annotations

from typing import Any

from lawvm.uk_legislation.uk_grafter import (
    extract_eid_map_bytes,
    parse_uk_statute_ir_bytes,
)


_EU_ANNEX_XML = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <EURetained />
  <Schedules>
    <Schedule eId="annex-I">
      <Number>ANNEX I</Number>
      <TitleBlock><Title>Definitions</Title></TitleBlock>
      <ScheduleBody>
        <P eId="annex-I-paragraph-1">The following definition shall apply:</P>
        <P eId="annex-I-paragraph-2">
          <OrderedList>
            <ListItem NumberOverride="(a)">"beverage cooler" means a cooler.</ListItem>
          </OrderedList>
        </P>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""


def _collect_eids(node: Any) -> set[str]:
    eids: set[str] = set()
    eid = node.attrs.get("eId") or node.attrs.get("id")
    if eid:
        eids.add(eid)
    for child in node.children:
        eids.update(_collect_eids(child))
    return eids


def test_eu_annex_schedule_body_paragraph_eids_are_preserved_in_ir() -> None:
    ir = parse_uk_statute_ir_bytes(_EU_ANNEX_XML, statute_id="eur/2099/1")

    eids: set[str] = set()
    for supplement in ir.supplements:
        eids.update(_collect_eids(supplement))

    assert "annex-I-paragraph-1" in eids
    assert "annex-I-paragraph-2" in eids


def test_eu_annex_schedule_body_paragraph_eids_match_eid_map_values() -> None:
    ir = parse_uk_statute_ir_bytes(_EU_ANNEX_XML, statute_id="eur/2099/1")
    eid_map_values = set(extract_eid_map_bytes(_EU_ANNEX_XML)["eid_map"].values())

    ir_eids: set[str] = set()
    for supplement in ir.supplements:
        ir_eids.update(_collect_eids(supplement))

    assert {"annex-I-paragraph-1", "annex-I-paragraph-2"} <= ir_eids
    assert {"annex-I-paragraph-1", "annex-I-paragraph-2"} <= eid_map_values
