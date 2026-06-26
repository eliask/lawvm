"""Parent instruction context can recover bare payload fragments for lowering.

When UK source extraction selects a single enumerated child of a multi-item
amendment container, the child text has no action verb.  If the parent
container supplies a clear instruction, lowering prepends it, emits an
observation, and parses the combined text into a typed text patch.
"""
from __future__ import annotations

from typing import Any

from lxml import etree as ET

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops
from lawvm.uk_legislation.effects import UKEffectRecord

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

_RULE_ID = "uk_effect_source_payload_instruction_context_augmented"


def _effect(effect_type: str = "substituted") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="e1",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/1996/5/section/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1996",
        affected_number="5",
        affected_provisions="s. 5",
        affecting_uri="/id/ukpga/2024/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Example Act 2024",
        in_force_dates=[{"date": "2024-02-01", "prospective": "false"}],
    )


def _source_root_with_extracted_item() -> tuple[ET._Element, ET._Element]:
    root = ET.fromstring(
        f"""
        <Body xmlns="{_LEG_NS}">
          <P1>
            <Pnumber>1</Pnumber>
            <Text>In section 5, for “old words” substitute—</Text>
            <BlockAmendment>
              <P2>
                <Pnumber>a</Pnumber>
                <Text>“new words”</Text>
              </P2>
            </BlockAmendment>
          </P1>
        </Body>
        """
    )
    extracted_el = root.find(".//{http://www.legislation.gov.uk/namespaces/legislation}P2")
    assert extracted_el is not None
    return root, extracted_el


def test_bare_payload_fragment_is_augmented_with_parent_instruction() -> None:
    records: list[dict[str, Any]] = []
    root, extracted_el = _source_root_with_extracted_item()

    ops = compile_effect_to_ir_ops(
        _effect("words substituted"),
        extracted_el,
        sequence=0,
        source_root=root,
        lowering_rejections_out=records,
    )

    assert _RULE_ID in {r["rule_id"] for r in records}
    assert any(
        r.get("rule_id") == _RULE_ID
        and "old words" in str(r.get("augmented_text_preview", ""))
        and "new words" in str(r.get("augmented_text_preview", ""))
        for r in records
    )
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, LegalOperation)
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "old words"
    assert op.text_patch.replacement == "new words"


def test_payload_with_own_action_verb_is_not_augmented() -> None:
    """When the extracted fragment already contains an action word, no augmentation."""
    records: list[dict[str, Any]] = []
    root = ET.fromstring(
        f"""
        <Body xmlns="{_LEG_NS}">
          <P1>
            <Pnumber>1</Pnumber>
            <Text>Miscellaneous amendments.</Text>
            <BlockAmendment>
              <P2>
                <Pnumber>a</Pnumber>
                <Text>In section 5, for “old words” substitute “new words”.</Text>
              </P2>
            </BlockAmendment>
          </P1>
        </Body>
        """
    )
    extracted_el = root.find(
        ".//{http://www.legislation.gov.uk/namespaces/legislation}P2"
    )
    assert extracted_el is not None

    ops = compile_effect_to_ir_ops(
        _effect("substituted"),
        extracted_el,
        sequence=0,
        source_root=root,
        lowering_rejections_out=records,
    )

    assert _RULE_ID not in {r["rule_id"] for r in records}
    assert len(ops) == 1
