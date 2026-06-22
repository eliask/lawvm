"""Synthetic regression for the §1.3 section→leafish payload-kind realignment.

When a UK amendment effect extracts a ``<P1>`` element (canonical kind
``section``) but the compiled op targets a leaf-level descendant such as
``section:1/subsection:1``, the payload-kind ``section`` must be realigned to the
canonical target leaf kind (``subsection``) before replay.  Without this guard
the INSERT op lands a ``section`` node as a child of an existing ``section``,
producing an ``unexpected section inside section`` tree-shape violation.

This was the root cause of the monotone ``all_tree`` failure on
``ukpga/1985/66`` s.1 (35/47 amendments bad, introduced by ``ukpga/2000/17``
Sch. 7 para. 1).
"""
from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNodeKind, LegalAddress
from lawvm.uk_legislation.effect_payload_normalization import (
    _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID,
    prepare_uk_operation_payload_node,
)


def _content_ir_section(label: str = "1", text: str = "") -> dict[str, Any]:
    return {
        "kind": "section",
        "label": label,
        "text": text,
        "attrs": {},
        "children": [],
    }


def _target_section_subsection(section: str, subsection: str) -> LegalAddress:
    return LegalAddress(
        path=(("section", section), ("subsection", subsection)),
        special=None,
    )


from lawvm.uk_legislation.effects import UKEffectRecord


def _effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="test-fixture",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="ukpga/1985/66",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1985",
        affected_number="66",
        affected_provisions="s. 1(1)",
        affecting_uri="ukpga/2000/17",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2000",
        affecting_number="17",
        affecting_provisions="Sch. 7 para. 1",
        affecting_title="Finance Act 2000",
    )


def _call_prepare(
    content_ir: dict[str, Any],
    target: LegalAddress,
) -> Any:
    observations: list[dict[str, Any]] = []
    result = prepare_uk_operation_payload_node(
        effect=_effect(),
        curr_action="insert",
        content_ir=content_ir,
        target_ref="s. 1(1)",
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
    return result, observations


def test_section_payload_targeting_subsection_is_realigned() -> None:
    """A ``section`` payload targeting ``section:N/subsection:M`` must be
    realigned to kind ``subsection`` to avoid §1.3 granularity escalation."""
    content_ir = _content_ir_section(label="1", text="climate change levy")
    target = _target_section_subsection("1", "1")
    result, observations = _call_prepare(content_ir, target)

    assert result.payload_node is not None, "payload must not be skipped"
    assert result.payload_node.kind == IRNodeKind.SUBSECTION, (
        f"§1.3 escalation: expected kind=SUBSECTION, got "
        f"kind={result.payload_node.kind!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID in rule_ids, (
        f"expected {_UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID} "
        f"in observations: {rule_ids}"
    )


def test_section_payload_targeting_section_is_not_realigned() -> None:
    """A ``section`` payload targeting ``section:N`` (same level) must NOT be
    realigned — it's a legitimate structural section insert."""
    content_ir = _content_ir_section(label="75B", text="Reimbursement")
    target = LegalAddress(path=(("section", "75B"),), special=None)
    result, observations = _call_prepare(content_ir, target)

    assert result.payload_node is not None
    assert result.payload_node.kind == IRNodeKind.SECTION, (
        f"legitimate section payload must keep kind=SECTION, got "
        f"kind={result.payload_node.kind!r}"
    )
