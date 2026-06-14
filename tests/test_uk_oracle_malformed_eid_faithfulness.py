"""Audit pin: "malformed" oracle eIds are FAITHFUL to the source XML, not our bug.

Background (stream-uk-oracle-eid-extraction-audit)
--------------------------------------------------
While triaging zero-score UK statutes, three families of odd-looking oracle
eIds surfaced in ``extract_eid_map_bytes`` output and the bench score-witness
CSVs:

  * concatenated section numbers  -- ``section-4243.`` (sections 42 + 43 merged)
  * decimal section/division ids  -- ``annex-II-division-2.2`` (EU-retained)
  * decimal schedule-paragraph ids -- ``schedule-5-paragraph-4.2``

A 2000-statute corpus audit found 83 concatenated (25 statutes), 25 decimal
(2 EU-retained statutes), and 20 schedule-decimal (2 statutes) occurrences.
For EVERY one of the 128 malformed eIds, the value is carried *verbatim* in the
source XML's own ``id``/``eId`` attribute -- legislation.gov.uk's own
consolidation scheme.  ``extract_eid_map_bytes`` copies the source attribute
(``eid = el.get("eId") or el.get("id")``); it does NOT synthesize these from
visible ``<Number>`` / ``<Pnumber>`` text.

Verdict for all three kinds: FAITHFUL to source, NOT an extraction bug.  These
tests pin that behavior so a future refactor cannot start mangling (or
"helpfully" splitting) the source eId, and so the faithful-copy contract is
documented.  The concatenated kind is the only asymmetric one (enacted keeps
section-42/-43 separate, oracle merges them); it is already handled at the
*comparison* layer as ``body_oracle_collapsed_range_granularity_residual`` in
scripts/uk_broad_baseline.py -- no extraction change is warranted.
"""
from __future__ import annotations

from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes


def _values(xml: bytes) -> set[str]:
    return set(extract_eid_map_bytes(xml)["eid_map"].values())


def test_concatenated_section_eid_is_copied_verbatim_not_synthesized() -> None:
    """Mirror of ukpga/1861/98 section-4243.: oracle merged sections 42 + 43.

    The source ``<P1>`` carries ``id="section-4243."`` with a visible Pnumber of
    "42" and ``PuncAfter=", 43."``.  We must surface the source eId verbatim --
    NOT split it into section-42/section-43, and NOT rebuild it from the visible
    "42" Pnumber text.
    """
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Body>
    <P1group>
      <P1 id="section-4243.">
        <Pnumber PuncAfter=", 43.">42</Pnumber>
        <P1para><Text/></P1para>
      </P1>
    </P1group>
    <P1group>
      <P1 id="section-44">
        <Pnumber>44</Pnumber>
        <P1para><Text>Live.</Text></P1para>
      </P1>
    </P1group>
  </Body>
</Legislation>
"""
    eids = _values(xml)
    # Faithful: the source eId is surfaced exactly as legislation.gov.uk minted it.
    assert "section-4243." in eids
    # Not our bug: we never split a merged source id into its component sections.
    assert "section-42" not in eids
    assert "section-43" not in eids
    # The neighbouring genuine section is still surfaced normally.
    assert "section-44" in eids


def test_eu_retained_decimal_division_eid_is_copied_verbatim() -> None:
    """Mirror of eur/2020/740 annex-II-division-2.2: genuine decimal numbering.

    Source ``id="annex-II-division-2.2"`` with ``<Number>2.2.</Number>``.  The
    decimal is the provision's real visible number; both enacted and oracle use
    it, so it is symmetric and faithful.
    """
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <EURetained>
    <Schedules>
      <Schedule id="annex-II">
        <Number>ANNEX II</Number>
        <Division id="annex-II-division-2.2">
          <Number>2.2.</Number>
          <Title>For the purposes of point 2.1:</Title>
          <P><Text>Operative.</Text></P>
        </Division>
      </Schedule>
    </Schedules>
  </EURetained>
</Legislation>
"""
    assert "annex-II-division-2.2" in _values(xml)


def test_schedule_paragraph_decimal_eid_is_copied_verbatim() -> None:
    """Mirror of uksi/2000/730 schedule-5-paragraph-4.2: real Pnumber '4.2'."""
    xml = b"""\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedules>
    <Schedule id="schedule-5">
      <Number>SCHEDULE 5</Number>
      <ScheduleBody>
        <Para id="schedule-5-paragraph-4.2">
          <Pnumber>4.2</Pnumber>
          <P1para><Text>Operative.</Text></P1para>
        </Para>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""
    assert "schedule-5-paragraph-4.2" in _values(xml)
