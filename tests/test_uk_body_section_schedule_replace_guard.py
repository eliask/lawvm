"""Owned guard against replacing body sections with an unrelated schedule payload.

A UK effect row can expose a whole SI schedule as the source payload for a body
section replace (e.g. s. 2 / s. 4 of ukpga/1961/33 being overwritten by the
entire Schedule 2 of uksi/2000/227).  The schedule's paragraphs are not body
sections: replaying a schedule-paragraph 4 as section 4 destroys the target
section and blocks later amendments.

AGENTS.md obligations covered:
  §0    repairs that change legal structure must be owned and observable
  §1.3  no granularity escalation
  §1.5  no payload smuggling
  §15   synthetic unit test + negative test + corpus regression
"""
from __future__ import annotations

from typing import Any

from lxml import etree as ET

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_body_section_replace_with_unmatched_schedule_payload,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.uk_grafter import _LEG_NS


def _minimal_effect(effect_type: str = "applied (with modifications)") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-schedule-section-replace",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/1961/33/section/4",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1961",
        affected_number="33",
        affected_provisions="s. 4",
        affecting_uri="/id/uksi/2000/227",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="227",
        affecting_provisions="Sch. 2",
        affecting_title="Test Regulations 2000",
    )


def _schedule_with_paragraphs() -> ET._Element:
    """Schedule that contains numbered paragraphs, not section-like units."""
    xml = f"""<Schedule xmlns="{_LEG_NS}" id="schedule-2">
      <Number>2</Number>
      <ScheduleBody>
        <P1 id="schedule-2-paragraph-1"><Pnumber>1</Pnumber><P1para><Text>First.</Text></P1para></P1>
        <P1 id="schedule-2-paragraph-2"><Pnumber>2</Pnumber><P1para><Text>Second.</Text></P1para></P1>
      </ScheduleBody>
    </Schedule>"""
    return ET.fromstring(xml)


def _schedule_with_section_4() -> ET._Element:
    """Schedule that genuinely carries a replacement section 4."""
    xml = f"""<Schedule xmlns="{_LEG_NS}" id="schedule-x">
      <Number>X</Number>
      <ScheduleBody>
        <Section id="section-4"><Pnumber>4</Pnumber><P1para><Text>New section 4.</Text></P1para></Section>
      </ScheduleBody>
    </Schedule>"""
    return ET.fromstring(xml)


def _schedule_with_schedule_paragraph_4() -> ET._Element:
    """Schedule with P1 paragraph 4 — not a body section."""
    xml = f"""<Schedule xmlns="{_LEG_NS}" id="schedule-y">
      <Number>Y</Number>
      <ScheduleBody>
        <P1 id="schedule-y-paragraph-4"><Pnumber>4</Pnumber><P1para><Text>Paragraph 4.</Text></P1para></P1>
      </ScheduleBody>
    </Schedule>"""
    return ET.fromstring(xml)


class _MutableStub:
    """Minimal stand-in for UKMutableNode in rejection tests."""

    def __init__(
        self,
        kind_value: str,
        *,
        label: str | None = None,
        children: list[Any] | None = None,
    ) -> None:
        self.kind = kind_value
        self.label = label
        self.children = children or []


def _call(
    target: LegalAddress,
    actual_el: ET._Element,
    *,
    curr_action: str = "replace",
    payload_node_mut: Any | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    if payload_node_mut is None:
        payload_node_mut = _MutableStub("schedule")
    rejections: list[dict[str, Any]] = []
    rejected = reject_body_section_replace_with_unmatched_schedule_payload(
        effect=_minimal_effect(),
        curr_action=curr_action,
        t_str="s. 4",
        target=target,
        payload_node_mut=payload_node_mut,
        actual_el=actual_el,
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=rejections,
    )
    return rejected, rejections


def test_rejects_schedule_payload_without_matching_section() -> None:
    target = LegalAddress(path=(("section", "4"),))
    rejected, rejections = _call(target, _schedule_with_paragraphs())
    assert rejected is True
    assert len(rejections) == 1
    detail = rejections[0]
    assert detail["rule_id"] == "uk_effect_body_section_replace_schedule_unmatched_rejected"
    assert detail["family"] == "payload_coverage_filter"
    assert detail["reason_code"] == "schedule_payload_lacks_target_section_like_unit"
    assert detail["target_ref"] == "s. 4"
    assert detail["target_leaf_label"] == "4"
    assert detail["strict_disposition"] == "block"


def test_rejects_schedule_payload_with_schedule_paragraph_having_same_number() -> None:
    target = LegalAddress(path=(("section", "4"),))
    rejected, rejections = _call(target, _schedule_with_schedule_paragraph_4())
    assert rejected is True
    assert len(rejections) == 1


def test_allows_schedule_payload_that_contains_target_section() -> None:
    target = LegalAddress(path=(("section", "4"),))
    payload = _MutableStub(
        "schedule",
        children=[_MutableStub("section", label="4")],
    )
    rejected, rejections = _call(target, _schedule_with_section_4(), payload_node_mut=payload)
    assert rejected is False
    assert not rejections


def test_allows_when_payload_children_contain_matching_section() -> None:
    """A wrapper (P1group) around a Section child must satisfy the guard."""
    target = LegalAddress(path=(("section", "4"),))
    schedule = _MutableStub(
        "schedule",
        children=[
            _MutableStub(
                "p1group",
                children=[_MutableStub("section", label="4", children=[])],
            )
        ],
    )
    rejected = reject_body_section_replace_with_unmatched_schedule_payload(
        effect=_minimal_effect(),
        curr_action="replace",
        t_str="s. 4",
        target=target,
        payload_node_mut=schedule,
        actual_el=ET.fromstring(f'<Schedule xmlns="{_LEG_NS}" id="s"/>'),
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=None,
    )
    assert rejected is False


def test_ignores_non_replace_actions() -> None:
    target = LegalAddress(path=(("section", "4"),))
    rejected, rejections = _call(
        target, _schedule_with_paragraphs(), curr_action="insert"
    )
    assert rejected is False
    assert not rejections


def test_ignores_when_payload_is_not_schedule() -> None:
    target = LegalAddress(path=(("section", "4"),))
    rejected, rejections = _call(
        target,
        _schedule_with_paragraphs(),
        payload_node_mut=_MutableStub("section"),
    )
    assert rejected is False
    assert not rejections


def test_ignores_schedule_container_target() -> None:
    target = LegalAddress(path=(("schedule", "2"),))
    schedule = _schedule_with_paragraphs()
    rejected, rejections = _call(target, schedule)
    assert rejected is False
    assert not rejections
