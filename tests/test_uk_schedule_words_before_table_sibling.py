"""Sibling deferral in the schedule 'words before the table substitute' path.

When a 'for the words before the table substitute' BlockAmendment carries a base
paragraph plus lettered siblings (e.g. 161, 161A, 161B), the base-target effect
lowers the whole series (a replace on the base paragraph plus chained sibling
inserts). A separate per-target call for one of those siblings must not lower it
again, but it must NOT be dropped silently either: it records a typed deferral
observation so the consumed sibling effect is visible in the lowering census.
"""
from __future__ import annotations

from typing import Any, Iterable, cast

from lxml import etree as ET

from lawvm.core.ir import LegalAddress, StructuralAction
from lawvm.uk_legislation.effect_schedule_lowering import (
    _try_lower_schedule_words_before_table_substitution,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.uk_grafter import _LEG_NS
from lawvm.uk_legislation.witness_builders import (
    _uk_effect_witness,
    _uk_extraction_witness,
)

_DEFERRAL_RULE_ID = (
    "uk_effect_schedule_words_before_table_substitution_sibling_deferred_to_base"
)


def _block(*labels: str) -> ET._Element:
    paragraphs = "\n".join(
        f"""
        <P1>
          <Pnumber>{label}</Pnumber>
          <P1para><Text>For the words before the table substitute "x".</Text></P1para>
        </P1>
        """
        for label in labels
    )
    return ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-1-paragraph-base">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>For the words before the table substitute the following—</Text>
            <BlockAmendment>{paragraphs}</BlockAmendment>
          </P2para>
        </P2>
        """
    )


def _effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="uk_test_words_before_table_sibling",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2010-01-01",
        affected_uri="/id/ukpga/2000/1/schedule/1/paragraph/161B",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Sch. 1 para. 161B",
        affecting_uri="/id/ukpga/2010/2",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2010",
        affecting_number="2",
        affecting_provisions="Sch. 1",
        affecting_title="Test Act 2010",
        in_force_dates=[{"date": "2010-01-01", "prospective": "false"}],
    )


def _call(*, target_label: str, block: ET._Element, records: list[dict[str, Any]]):
    effect = _effect()
    extracted_text = "".join(cast(Iterable[str], block.itertext()))
    return _try_lower_schedule_words_before_table_substitution(
        effect=effect,
        action="substitute",
        effect_type="words substituted",
        t_str="schedule-1-paragraph-" + target_label,
        target=LegalAddress(path=(("schedule", "1"), ("paragraph", target_label))),
        extracted_el=block,
        extracted_text=extracted_text,
        sequence=0,
        effect_witness=_uk_effect_witness(effect, authority_layer="AFFECTING_ACT_TEXT"),
        extraction_witness=_uk_extraction_witness(
            effect,
            extracted_el=block,
            extracted_text=extracted_text,
            metadata_fallback_used=False,
        ),
        original_targets_str=["schedule-1-paragraph-" + target_label],
        lowering_rejections_out=records,
    )


def test_base_target_lowers_series_without_deferral_observation() -> None:
    records: list[dict[str, Any]] = []
    result = _call(target_label="161", block=_block("161", "161A", "161B"), records=records)
    assert result.handled is True
    # The base target lowers a replace plus the chained sibling inserts.
    assert result.ops
    assert result.ops[0].action is StructuralAction.REPLACE
    assert not any(r.get("rule_id") == _DEFERRAL_RULE_ID for r in records)


def test_sibling_target_emits_deferral_observation_not_silent_drop() -> None:
    records: list[dict[str, Any]] = []
    result = _call(target_label="161B", block=_block("161", "161A", "161B"), records=records)
    # Consumed (handled) but NOT re-lowered, and NOT dropped silently.
    assert result.handled is True
    assert not result.ops
    deferrals = [r for r in records if r.get("rule_id") == _DEFERRAL_RULE_ID]
    assert len(deferrals) == 1
    detail = deferrals[0]
    assert detail.get("deferred_to_base_target") is True
    assert detail.get("deferred_sibling_label") == "161b"
    assert detail.get("base_target_label") == "161"
    assert detail.get("blocking") is False


def test_block_without_labelled_paragraphs_is_surfaced_not_silent() -> None:
    block = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-1-paragraph-base">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>For the words before the table substitute the following—</Text>
            <BlockAmendment><Text>Unlabelled payload.</Text></BlockAmendment>
          </P2para>
        </P2>
        """
    )
    records: list[dict[str, Any]] = []
    result = _call(target_label="161B", block=block, records=records)
    assert result.handled is True
    assert not result.ops
    deferrals = [r for r in records if r.get("rule_id") == _DEFERRAL_RULE_ID]
    assert len(deferrals) == 1
    assert deferrals[0].get("deferred_to_base_target") is False
