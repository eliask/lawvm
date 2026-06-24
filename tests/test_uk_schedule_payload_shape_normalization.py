"""Tests for UK schedule payload shape normalization (AGENTS.md §1.3, §1.5).

Inserted/replaced schedule payloads can carry structural shapes that are faithful
to the affecting XML but invalid under canonical UK nesting:

- A schedule ``Part`` may contain ``P1`` paragraphs directly, without the
  intermediate ``P1group`` wrapper expected by the consolidated tree.
- A schedule paragraph's ``P2para`` may contain a ``<UnorderedList Class="Definition">``
  whose items are lowered as ``schedule_entry`` children of a ``subparagraph``,
  but ``subparagraph`` does not admit ``schedule_entry`` children.

Both fixes are owned normalizations in
``effect_payload_normalization.prepare_uk_operation_payload_node``.
"""
from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNodeKind, LegalAddress
from lawvm.uk_legislation.effect_payload_normalization import (
    prepare_uk_operation_payload_node,
    _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID,
    _UK_EFFECT_SCHEDULE_SUBPARAGRAPH_DEFINITION_ENTRIES_RULE_ID,
)
from lawvm.uk_legislation.effects import UKEffectRecord


def _minimal_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-pra-0001",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2020/17/schedule/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 10 Pt. 3A",
        affecting_uri="/id/ukpga/2026/2",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="2",
        affecting_provisions="s. 38",
        affecting_title="Test Amending Act 2026",
    )


def _target_schedule_part(schedule: str, part: str) -> LegalAddress:
    return LegalAddress(path=(("schedule", schedule), ("part", part)))


def _target_schedule_part_paragraph(
    schedule: str, part: str, paragraph: str
) -> LegalAddress:
    return LegalAddress(
        path=(("schedule", schedule), ("part", part), ("paragraph", paragraph))
    )


def _target_schedule_paragraph(schedule: str, paragraph: str) -> LegalAddress:
    return LegalAddress(path=(("schedule", schedule), ("paragraph", paragraph)))


def _call_prepare(
    *,
    content_ir: dict[str, Any],
    target: LegalAddress,
    curr_action: str = "insert",
    target_ref: str = "Sch. 10 Pt. 3A",
    observations: list[dict[str, Any]] | None = None,
) -> Any:
    if observations is None:
        observations = []
    return prepare_uk_operation_payload_node(
        effect=_minimal_effect(),
        curr_action=curr_action,
        content_ir=content_ir,
        target_ref=target_ref,
        target=target,
        payload_match_target=target,
        target_replacement_leaf_override=None,
        target_replacement_leaf_kind=None,
        actual_el=None,
        extracted_el=None,
        extracted_text=None,
        allow_payload_identity_synthesis=False,
        lowering_rejections_out=observations,
    )


def _node(kind: str, label: str | None, text: str = "", children: list[Any] | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "text": text,
        "attrs": {},
        "children": children or [],
    }


# ===========================================================================
# Schedule part p1group wrapper
# ===========================================================================

def test_schedule_part_insert_direct_paragraph_wrapped_in_p1group() -> None:
    """A schedule part insert payload with a direct paragraph child is wrapped."""
    content_ir = _node(
        "part",
        "3A",
        text="Termination of order",
        children=[_node("paragraph", "15A", text="Provision text.")],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_part("10", "3A"),
        observations=observations,
    )
    assert result.payload_node is not None
    assert result.payload_node.kind == IRNodeKind.PART
    assert result.payload_node.label == "3A"
    assert len(result.payload_node.children) == 1
    wrapper = result.payload_node.children[0]
    assert wrapper.kind == IRNodeKind.P1GROUP, f"Expected p1group wrapper, got {wrapper.kind}"
    assert wrapper.label is None
    assert len(wrapper.children) == 1
    assert wrapper.children[0].kind == IRNodeKind.PARAGRAPH
    assert wrapper.children[0].label == "15A"

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID in rule_ids


def test_schedule_part_insert_p1group_payload_unchanged() -> None:
    """A schedule part payload that already has a p1group wrapper is untouched."""
    content_ir = _node(
        "part",
        "10A",
        text="Drug testing requirement",
        children=[
            _node(
                "p1group",
                None,
                text="Requirement",
                children=[_node("paragraph", "22A", text="Paragraph text.")],
            ),
        ],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_part("9", "10A"),
        observations=observations,
    )
    assert result.payload_node is not None
    assert len(result.payload_node.children) == 1
    wrapper = result.payload_node.children[0]
    assert wrapper.kind == IRNodeKind.P1GROUP
    assert wrapper.children[0].kind == IRNodeKind.PARAGRAPH
    assert wrapper.children[0].label == "22A"
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID not in rule_ids


def test_schedule_part_insert_table_wrapped_in_paragraph_p1group() -> None:
    """A schedule part insert payload with a direct table child is wrapped."""
    content_ir = _node(
        "part",
        "4A",
        text="Table part",
        children=[
            {
                "kind": "table",
                "label": None,
                "text": "",
                "attrs": {"source_tag": "Table"},
                "children": [],
            }
        ],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_part("11", "4A"),
        observations=observations,
    )
    assert result.payload_node is not None
    assert result.payload_node.kind == IRNodeKind.PART
    assert result.payload_node.label == "4A"
    assert len(result.payload_node.children) == 1
    wrapper = result.payload_node.children[0]
    assert wrapper.kind == IRNodeKind.P1GROUP
    assert wrapper.label is None
    assert wrapper.children[0].kind == IRNodeKind.PARAGRAPH
    assert wrapper.children[0].children[0].kind == IRNodeKind.TABLE
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID in rule_ids


def test_schedule_part_direct_paragraph_payload_wrapped_in_p1group() -> None:
    """A paragraph inserted directly under a schedule Part is wrapped in p1group."""
    content_ir = _node("paragraph", "8", text="Paragraph text.")
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_part_paragraph("1", "2", "8"),
        target_ref="Sch. 1 Pt. 2 para. 8",
        observations=observations,
    )
    assert result.payload_node is not None
    assert result.payload_node.kind == IRNodeKind.P1GROUP
    assert result.payload_node.label is None
    assert len(result.payload_node.children) == 1
    assert result.payload_node.children[0].kind == IRNodeKind.PARAGRAPH
    assert result.payload_node.children[0].label == "8"
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID in rule_ids


def test_schedule_part_direct_table_payload_wrapped_in_paragraph_p1group() -> None:
    """A table inserted directly under a schedule Part is wrapped in paragraph+p1group."""
    content_ir = {
        "kind": "table",
        "label": None,
        "text": "",
        "attrs": {"source_tag": "Table"},
        "children": [],
    }
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_part_paragraph("1", "2", "8"),
        target_ref="Sch. 1 Pt. 2 para. 8 table",
        observations=observations,
    )
    assert result.payload_node is not None
    assert result.payload_node.kind == IRNodeKind.P1GROUP
    assert len(result.payload_node.children) == 1
    para = result.payload_node.children[0]
    assert para.kind == IRNodeKind.PARAGRAPH
    assert para.children[0].kind == IRNodeKind.TABLE
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID in rule_ids


# ===========================================================================
# Schedule subparagraph definition schedule_entry promotion
# ===========================================================================

def test_schedule_paragraph_definition_entries_promoted_out_of_subparagraph() -> None:
    """Definition schedule_entry items inside a subparagraph are promoted to paragraph siblings."""
    content_ir = _node(
        "paragraph",
        "22A",
        children=[
            _node(
                "subparagraph",
                "5",
                text="In this paragraph and paragraph 22B—",
                children=[
                    {
                        "kind": "schedule_entry",
                        "label": None,
                        "text": "“drug” means a controlled drug.",
                        "attrs": {"source_tag": "ListItem"},
                        "children": [],
                    },
                    {
                        "kind": "schedule_entry",
                        "label": None,
                        "text": "“psychoactive substance” has the given meaning.",
                        "attrs": {"source_tag": "ListItem"},
                        "children": [],
                    },
                ],
            ),
        ],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_paragraph("9", "22A"),
        observations=observations,
    )
    assert result.payload_node is not None
    para = result.payload_node
    assert para.kind == IRNodeKind.PARAGRAPH
    assert len(para.children) == 3
    subpara = para.children[0]
    assert subpara.kind == IRNodeKind.SUBPARAGRAPH
    assert subpara.label == "5"
    assert len(subpara.children) == 0
    assert para.children[1].kind == IRNodeKind.PARAGRAPH
    assert para.children[1].text == "“drug” means a controlled drug."
    assert para.children[2].kind == IRNodeKind.PARAGRAPH
    assert para.children[2].text == "“psychoactive substance” has the given meaning."

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_SUBPARAGRAPH_DEFINITION_ENTRIES_RULE_ID in rule_ids


def test_schedule_paragraph_definition_entries_under_paragraph_unchanged() -> None:
    """Schedule_entry children directly under a paragraph remain valid and untouched."""
    content_ir = _node(
        "paragraph",
        "22A",
        children=[
            {
                "kind": "schedule_entry",
                "label": None,
                "text": "Term definition.",
                "attrs": {"source_tag": "ListItem"},
                "children": [],
            },
        ],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=_target_schedule_paragraph("9", "22A"),
        observations=observations,
    )
    assert result.payload_node is not None
    assert len(result.payload_node.children) == 1
    assert result.payload_node.children[0].kind == IRNodeKind.SCHEDULE_ENTRY
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_SUBPARAGRAPH_DEFINITION_ENTRIES_RULE_ID not in rule_ids


def test_non_schedule_payload_not_normalized() -> None:
    """Body-section payloads are not touched by the schedule shape normalizers."""
    content_ir = _node(
        "part",
        "3A",
        children=[_node("paragraph", "15A", text="Provision text.")],
    )
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=content_ir,
        target=LegalAddress(path=(("chapter", "1"), ("part", "3A"))),
        target_ref="Pt. 3A",
        observations=observations,
    )
    assert result.payload_node is not None
    assert result.payload_node.children[0].kind == IRNodeKind.PARAGRAPH
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_SCHEDULE_PART_P1GROUP_WRAPPER_RULE_ID not in rule_ids
    assert _UK_EFFECT_SCHEDULE_SUBPARAGRAPH_DEFINITION_ENTRIES_RULE_ID not in rule_ids
