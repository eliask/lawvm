"""§2.9 disposition-3 per-site test for the
``definition_child_structural_insert`` strict-profile consume site
(Tier C PR2, site 9).

The site lives in ``compile_effect_to_ir_ops`` (``effect_compiler.py``): an
``insert`` effect whose source parses to a blocking definition-child
structural sibling insert (``source_definition_child_structural_sibling_
insert`` returns a dict with ``blocking=True``) is rejected by default
(``uk_effect_definition_child_structural_insert_rejected``). When the active
strict-profile carries ``allows_uk_definition_child_structural_insert=True``
the code emits an audited
``uk_strict_profile_lifted_definition_child_structural_insert`` observation.

NON-MATERIALIZABLE LIFT (resolved owned-repair)
===============================================
A blocking definition-child structural insert is rejected *precisely because*
the source omitted the lowerable payload structure (the child + tail-connector
claim): ``source_definition_child_structural_sibling_insert``
(``source_definition_structural_insert.py``) only returns a ``blocking`` dict
on the ``if not payloads`` path, so the blocking shape NEVER carries the
``payloads`` / ``anchor_target`` keys that
``lower_uk_definition_child_structural_sibling_insert``
(``effect_special_lowering.py``) requires. Falling through to lowering
therefore always raised ``KeyError: 'payloads'`` (the original wiring gap).

Fabricating the missing structure is exactly the "lowering must not append the
payload to the broad section text" hazard the rejection guards, so the lift
*cannot* materialize an op. The consume site (``effect_compiler.py``) now
degrades the strict-profile "proceed" for this site to a **clean no-op block**:
it records the lift authorization as an audit observation
(``strict_disposition="proceed_non_materializable"``, ``materialized=False``)
and emits zero ops, preserving the provision unchanged. No ``KeyError``.

Trigger: SYNTHETIC. ``source_definition_child_structural_sibling_insert``'s
``blocking`` branch is reproduced with a minimal ``P2para`` element whose
text matches ``_IN_DEFINITION_AFTER_PARAGRAPH_INSERT_RE`` (and NOT the
``BEFORE_CONNECTOR`` variant), with a ``s. 5(2)`` target. Grounding-neutral
by construction (test-only).
"""
from __future__ import annotations

from typing import Any

from lxml import etree as ET

import lawvm.uk_legislation.effect_compiler as effect_compiler
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_definition_child_structural_insert"
_BLOCK_RULE_ID = "uk_effect_definition_child_structural_insert_rejected"
_SOURCE_TEXT = (
    "In section 5(2), in the definition of “qualifying person”, "
    "after paragraph (a), insert— (b) a person of a prescribed "
    "description;"
)


def _blocking_definition_child_insert_source() -> tuple[ET._Element, ET._Element]:
    el = ET.Element("P2para")
    ET.SubElement(el, "Text").text = _SOURCE_TEXT
    source_root = ET.Element("Schedule")
    source_root.append(el)
    return el, source_root


def _definition_child_insert_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-definition-child-structural-insert",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 5(2)",
        affecting_uri="http://www.legislation.gov.uk/id/uksi/2012/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2012",
        affecting_number="1",
        affecting_provisions="reg. 3",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2012-01-01", "prospective": "false"}],
    )


def test_default_profile_preserves_definition_child_insert_block(monkeypatch) -> None:
    """§2.9 disposition 2 (negative): no strict-profile loaded — the default
    block is preserved (zero ops, block-rejection receipt emitted, no lift)."""
    monkeypatch.delenv("LAWVM_UK_STRICT_PROFILE", raising=False)
    el, source_root = _blocking_definition_child_insert_source()
    observations: list[dict[str, Any]] = []
    ops = effect_compiler.compile_effect_to_ir_ops(
        _definition_child_insert_effect(),
        el,
        sequence=0,
        lowering_rejections_out=observations,
        source_root=source_root,
    )
    assert ops == [], "default must preserve the block (zero lowering ops)"
    rule_ids = {o.get("rule_id") for o in observations}
    assert _BLOCK_RULE_ID in rule_ids, "block-rejection receipt MUST be emitted"
    assert _LIFT_RULE_ID not in rule_ids, "lift must NOT fire under default profile"


def test_strict_profile_loaded_but_not_allowed_still_blocks(monkeypatch) -> None:
    """§2.9 disposition 2: strict-profile loaded (default preset) but
    ``allows_uk_definition_child_structural_insert=False`` — block preserved."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    el, source_root = _blocking_definition_child_insert_source()
    observations: list[dict[str, Any]] = []
    ops = effect_compiler.compile_effect_to_ir_ops(
        _definition_child_insert_effect(),
        el,
        sequence=0,
        lowering_rejections_out=observations,
        source_root=source_root,
    )
    assert ops == []
    rule_ids = {o.get("rule_id") for o in observations}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lift_degrades_to_clean_noop_block(monkeypatch) -> None:
    """§2.9 disposition 3 — non-materializable lift degrades to a clean no-op.

    Strict-profile loaded AND
    ``allows_uk_definition_child_structural_insert=True``. The consume site is
    wired: the ``uk_strict_profile_lifted_definition_child_structural_insert``
    observation is appended. Because the blocking shape carries no lowerable
    ``payloads`` structure (the source omitted the child-tail claim, which the
    rejection exists to guard), the lift cannot materialize an op — so the
    consume site emits ZERO ops (preserving the provision) and records the
    lift authorization as an audit observation marked non-materializable. No
    ``KeyError`` is raised.
    """
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_definition_child_structural_insert=True,
    )
    monkeypatch.setattr(
        effect_compiler, "active_uk_strict_profile", lambda: allowed_profile
    )
    el, source_root = _blocking_definition_child_insert_source()
    observations: list[dict[str, Any]] = []
    ops = effect_compiler.compile_effect_to_ir_ops(
        _definition_child_insert_effect(),
        el,
        sequence=0,
        lowering_rejections_out=observations,
        source_root=source_root,
    )
    assert ops == [], (
        "a non-materializable lift must preserve the provision (zero ops), "
        f"not fabricate or crash; got {ops!r}"
    )
    # The consume site is wired — the lift observation is appended, marked
    # non-materializable (and the original Key('payloads') crash is gone).
    lifts = [o for o in observations if o.get("rule_id") == _LIFT_RULE_ID]
    assert lifts, (
        "the strict-profile lift observation MUST be emitted at the consume "
        "site (the gate IS wired)"
    )
    lift = lifts[0]
    assert lift["family"] == "definition_entry_elaboration"
    assert lift["reason_code"] == (
        "strict_profile_authorized_definition_child_structural_insert"
    )
    assert lift["strict_disposition"] == "proceed_non_materializable"
    assert lift["materialized"] is False
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
