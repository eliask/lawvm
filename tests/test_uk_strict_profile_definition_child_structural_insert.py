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
``uk_strict_profile_lifted_definition_child_structural_insert`` observation
and is *intended* to fall through to
``lower_uk_definition_child_structural_sibling_insert``.

WIRING GAP (documented, not forced green)
=========================================
The lift observation DOES fire — but the fall-through immediately raises
``KeyError: 'payloads'``. The blocking-dict path in
``source_definition_child_structural_sibling_insert``
(``source_definition_structural_insert.py``, the
``uk_effect_definition_child_structural_insert_rejected`` branch) returns a
dict WITHOUT the ``payloads`` / ``anchor_target`` keys that
``lower_uk_definition_child_structural_sibling_insert``
(``effect_special_lowering.py``) requires. So the lift path is non-functional:
enabling the gate trades a clean block for an uncaught ``KeyError`` mid-
lowering.

Per the task discipline (do NOT change replay/lowering semantics; report a
real wiring gap rather than force a passing test), this test PINS the current
behavior honestly:
  - the lift OBSERVATION fires (the gate *is* wired at the consume site), AND
  - the downstream fall-through raises ``KeyError('payloads')`` (the gap).
The negative (default profile preserves the block) is also pinned. If the gap
is later closed (the blocking dict made carry ``payloads`` for the lift lane,
or the lift path made not fall through), this test's ``KeyError`` expectation
should be replaced with an ops/no-block assertion.

Trigger: SYNTHETIC. ``source_definition_child_structural_sibling_insert``'s
``blocking`` branch is reproduced with a minimal ``P2para`` element whose
text matches ``_IN_DEFINITION_AFTER_PARAGRAPH_INSERT_RE`` (and NOT the
``BEFORE_CONNECTOR`` variant), with a ``s. 5(2)`` target. Grounding-neutral
by construction (test-only).
"""
from __future__ import annotations

from typing import Any

from lxml import etree as ET
import pytest

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


def test_strict_profile_allowed_emits_lift_then_hits_wiring_gap(monkeypatch) -> None:
    """§2.9 disposition 3 (WIRING GAP — pinned, not forced green).

    Strict-profile loaded AND
    ``allows_uk_definition_child_structural_insert=True``. The consume site
    IS wired: the ``uk_strict_profile_lifted_definition_child_structural_
    insert`` observation is appended. BUT the lift's fall-through into
    ``lower_uk_definition_child_structural_sibling_insert`` immediately raises
    ``KeyError: 'payloads'`` because the blocking-dict path returns no
    ``payloads`` key. This pins the gap so a future fix (or regression) is
    forced through this test rather than passing silently.
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
    with pytest.raises(KeyError) as excinfo:
        effect_compiler.compile_effect_to_ir_ops(
            _definition_child_insert_effect(),
            el,
            sequence=0,
            lowering_rejections_out=observations,
            source_root=source_root,
        )
    # The wiring gap is specifically the missing ``payloads`` key on the
    # lifted blocking dict.
    assert "payloads" in str(excinfo.value), (
        "the documented wiring gap is KeyError('payloads') from the lift "
        f"fall-through; got {excinfo.value!r}"
    )
    # The consume site IS wired — the lift observation was appended before the
    # crash (proving the gate fires; only the downstream lowering lane is
    # broken).
    lifts = [o for o in observations if o.get("rule_id") == _LIFT_RULE_ID]
    assert lifts, (
        "the strict-profile lift observation MUST be emitted at the consume "
        "site (the gate IS wired) even though the fall-through then crashes"
    )
    lift = lifts[0]
    assert lift["family"] == "definition_entry_elaboration"
    assert lift["reason_code"] == (
        "strict_profile_authorized_definition_child_structural_insert"
    )
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
