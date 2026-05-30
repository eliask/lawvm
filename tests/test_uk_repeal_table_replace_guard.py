"""A repeal-family effect must not be lowered to a replace that overwrites the
target with a repeal schedule table.

When the extracted source for a ``repealed in part`` (etc.) effect is a repeal
Schedule — a list of repeals by extent, not replacement content — lowering it as a
whole-node replace destroys the target. The guard withholds the operation and
preserves the target (over-retention is the safe wrong).
"""
from __future__ import annotations

from typing import Any

from lxml import etree as ET

from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops
from lawvm.uk_legislation.effects import UKEffectRecord

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

_REJECT_RULE = "uk_effect_repeal_table_replacement_payload_rejected"


def _effect(effect_type: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="e1",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2007-01-01",
        affected_uri="/id/ukpga/1996/5/schedule/1/paragraph/6",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1996",
        affected_number="5",
        affected_provisions="Sch. 1 para. 6",
        affecting_uri="/id/ukpga/1999/8",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="1999",
        affecting_number="8",
        affecting_provisions="Sch. 5",
        affecting_title="Finance Act 1999",
        in_force_dates=[{"date": "2000-01-01", "prospective": "false"}],
    )


def _repeal_table_el() -> ET._Element:
    return ET.fromstring(
        f"""
        <Schedule xmlns="{_LEG_NS}">
          <Number>5</Number>
          <Title>Repeals</Title>
          <Text>Chapter Short title Extent of repeal Section 65.</Text>
        </Schedule>
        """
    )


def test_repeal_in_part_with_repeal_table_source_is_withheld() -> None:
    records: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect("repealed in part"),
        _repeal_table_el(),
        sequence=0,
        lowering_rejections_out=records,
    )
    assert ops == []
    assert _REJECT_RULE in {r["rule_id"] for r in records}


def test_genuine_substitution_payload_is_not_withheld() -> None:
    # a real replacement payload (not a repeal table) must still lower normally
    el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <P2><Pnumber>6</Pnumber><Text>The substituted paragraph text.</Text></P2>
          </BlockAmendment>
        </P1>
        """
    )
    records: list[dict[str, Any]] = []
    compile_effect_to_ir_ops(
        _effect("substituted"),
        el,
        sequence=0,
        lowering_rejections_out=records,
    )
    assert _REJECT_RULE not in {r["rule_id"] for r in records}
