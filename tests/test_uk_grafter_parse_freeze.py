"""PR1 boundary freeze guard for ``uk_grafter`` (audit XJUR-02 / AGENTS.md §2.3).

Regression for the parse-boundary migration of ``uk_grafter`` off of in-place
``UKMutableNode`` mutation (Wave N3d Sub-PR F: the ``mutable_ir`` shadow module
was deleted; the boundary IS frozen by construction). Pins two invariants:

  * (1) The tree returned by ``parse_uk_statute_ir_bytes`` at the parse
    boundary IS the frozen ``IRNode`` (the core's ``@dataclass(frozen=True)``
    dataclass). Mutating ``.children`` / ``.attrs`` / ``.label`` / ``.text``
    raises ``dataclasses.FrozenInstanceError``.
  * (2) The parse product carries the expected structural shape at the
    boundary (``p1group`` heading attached to a sole section; schedule
    supplement preserved). This was the byte-identical pre-PR1 contract.

Sub-PR F removed the prior ``TestNoUKMutableNodeSurvivesIntoBoundaryTree``
class — once the ``mutable_ir`` shadow was deleted there is no class to detect,
so the leak-detection guard no longer adds coverage. The structural
``IRNode``-type and frozen-field assertions below remain load-bearing.
"""
from __future__ import annotations

import dataclasses

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes


_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Metadata>
    <dc:title>Test Act</dc:title>
  </Metadata>
  <Body>
    <P1group>
      <Title>Sole section group</Title>
      <P1 eId="section-1">
        <Pnumber>1</Pnumber>
        <P1para>
          <P2 eId="section-1-1">
            <Pnumber>1</Pnumber>
            <P2para><Text>Body text before nested paragraphs.</Text>
              <P3 eId="section-1-1-a">
                <Pnumber>a</Pnumber>
                <P3para><Text>nested paragraph a.</Text></P3para>
              </P3>
              <P3 eId="section-1-1-b">
                <Pnumber>b</Pnumber>
                <P3para><Text>nested paragraph b.</Text></P3para>
              </P3>
            </P2para>
          </P2>
        </P1para>
      </P1>
    </P1group>
  </Body>
  <Schedules>
    <Schedule eId="schedule-1">
      <Number>SCHEDULE 1</Number>
      <Title>A schedule</Title>
      <ScheduleBody>
        <P1 eId="schedule-1-paragraph-1">
          <Pnumber>1</Pnumber>
          <P1para><Text>Schedule body text.</Text></P1para>
        </P1>
      </ScheduleBody>
    </Schedule>
  </Schedules>
</Legislation>
"""


def _parse_boundary_ir() -> IRStatute:
    return parse_uk_statute_ir_bytes(
        _XML,
        statute_id="ukpga/2000/1",
        version_label="enacted",
        source_path="https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
    )


class TestParseBoundaryIsFrozenIRNode:
    def test_body_is_frozen_irnode(self) -> None:
        ir = _parse_boundary_ir()
        assert isinstance(ir.body, IRNode)

    def test_supplements_are_frozen_irnode(self) -> None:
        ir = _parse_boundary_ir()
        assert ir.supplements
        for supplement in ir.supplements:
            assert isinstance(supplement, IRNode)

    @pytest.mark.parametrize(
        "field",
        ["label", "text", "children", "attrs"],
    )
    def test_mutating_irnode_field_raises_frozeninstanceerror(self, field: str) -> None:
        ir = _parse_boundary_ir()
        # ``setattr`` on a frozen dataclass field raises ``FrozenInstanceError``
        # at runtime; using ``setattr`` (not direct ``=``) keeps the test
        # compatible with the typed frozen ``IRNode`` API checked by ty.
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ir.body, field, [])


class TestParseBoundarySignalEquivalence:
    """The p1group-with-sole-section + schedule round-trip exercises every
    parse-time construction branch that PR1 refactored (heading attachment,
    p3 nested paragraphs, schedule body). Asserting the structural shape
    here is a regression both for the parse output AND for the migration
    (the IRNode tree at the boundary must be byte-identical to pre-PR1).
    """

    def test_body_section_carries_p1group_title_as_heading(self) -> None:
        ir = _parse_boundary_ir()
        section = ir.body.children[0].children[0]
        assert section.kind is IRNodeKind.SECTION
        headings = [c for c in section.children if c.kind is IRNodeKind.HEADING]
        assert [h.text for h in headings] == ["Sole section group"]
        # Subsections 1 and nested paragraphs a/b all materialize as frozen
        # IRNode at the boundary.
        subsection = next(c for c in section.children if c.kind is IRNodeKind.SUBSECTION)
        assert [c.label for c in subsection.children] == ["a", "b"]

    def test_schedule_supplement_preserves_paragraph(self) -> None:
        ir = _parse_boundary_ir()
        assert len(ir.supplements) == 1
        schedule = ir.supplements[0]
        assert schedule.kind is IRNodeKind.SCHEDULE
        assert [c.label for c in schedule.children] == ["1"]
