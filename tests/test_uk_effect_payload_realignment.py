"""Tests for UK payload label/kind realignment ownership (AGENTS.md §1.3/§1.5).

Covers two previously unowned silent payload mutations in
effect_payload_normalization.py:prepare_uk_operation_payload_node:

  Site 1 (lines ~313-319):
    When a payload has a blank label but its kind matches the canonical target
    leaf kind, the label is silently overwritten with the target leaf label.
    The fix emits uk_effect_payload_label_realigned_to_target_leaf so the
    mutation is owned and strict mode can gate on it.

  Site 2 (lines ~320-326):
    When a payload has a leafish kind that differs from the canonical target
    leaf kind but whose label-number matches the target leaf label, the kind is
    silently retyped.  The fix emits
    uk_effect_payload_kind_realigned_to_target_leaf so the retyping is owned
    and strict mode can gate on it.

Both observations: family=payload_realignment, blocking=False,
strict_disposition=block, quirks_disposition=apply.

AGENTS.md obligations covered:
  §1.3  no granularity escalation without ownership
  §1.5  no payload smuggling / late payload mutation
  §15.1 synthetic unit test
  §15.2 negative test (no observation on valid shapes)
  §15.3 finding/observation test (witness fields)
  §15.4 strict-mode test (strict_disposition=block)
"""
from __future__ import annotations

from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effect_payload_normalization import (
    prepare_uk_operation_payload_node,
    _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID,
    _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID,
)
from lawvm.uk_legislation.effects import UKEffectRecord


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _minimal_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-pra-0001",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/5/subsection/2",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 5(2)",
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 1",
        affecting_title="Test Amending Act 2024",
    )


def _target_subsection_2() -> LegalAddress:
    return LegalAddress(path=(("section", "5"), ("subsection", "2")))


def _call_prepare(
    *,
    content_ir: dict[str, Any],
    target: Optional[LegalAddress] = None,
    curr_action: str = "insert",
    target_ref: str = "s. 5(2)",
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> Any:
    if target is None:
        target = _target_subsection_2()
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
        lowering_rejections_out=lowering_rejections_out,
    )


# ===========================================================================
# Site 1 — blank-label realignment
# ===========================================================================

def _payload_blank_label_matching_kind() -> dict[str, Any]:
    """Payload whose kind=subsection and label is blank (unset)."""
    return {
        "kind": "subsection",
        "label": "",
        "text": "Inserted subsection text.",
        "children": [],
    }


def _payload_nonempty_label_matching_kind() -> dict[str, Any]:
    """Payload whose kind=subsection and label is already set."""
    return {
        "kind": "subsection",
        "label": "2",
        "text": "Already-labelled subsection text.",
        "children": [],
    }


# ---------------------------------------------------------------------------
# Test 1.1 — positive: blank label → observation emitted
# ---------------------------------------------------------------------------

def test_payload_label_realignment_emits_observation() -> None:
    """Blank-label insert payload that matches target kind emits label-realignment observation."""
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID in rule_ids, (
        f"Expected {_UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID!r} in {rule_ids!r}"
    )


# ---------------------------------------------------------------------------
# Test 1.2 — positive: label is realigned (no regression)
# ---------------------------------------------------------------------------

def test_payload_label_realignment_result_has_label() -> None:
    """After realignment, the payload node carries the target leaf label."""
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        lowering_rejections_out=observations,
    )
    assert result.payload_node is not None, "Payload node must be returned"
    assert result.payload_node.label == "2", (
        f"Expected label='2' after realignment, got {result.payload_node.label!r}"
    )


# ---------------------------------------------------------------------------
# Test 1.3 — negative: non-blank label → no observation
# ---------------------------------------------------------------------------

def test_payload_label_realignment_no_observation_when_label_present() -> None:
    """When payload label is already non-blank, no label-realignment observation fires."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_nonempty_label_matching_kind(),
        lowering_rejections_out=observations,
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID not in rule_ids, (
        "Label-realignment observation must NOT fire when label is already non-blank"
    )


# ---------------------------------------------------------------------------
# Test 1.4 — strict-mode: strict_disposition=block
# ---------------------------------------------------------------------------

def test_payload_label_realignment_strict_disposition_is_block() -> None:
    """Label-realignment observation carries strict_disposition=block."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"
    assert obs.get("strict_disposition") == "block", (
        f"Expected strict_disposition=block, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("blocking") is False, (
        "Observation must be non-blocking (quirks mode allows realignment)"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )


# ---------------------------------------------------------------------------
# Test 1.5 — witness fields: original/new label and kind
# ---------------------------------------------------------------------------

def test_payload_label_realignment_witness_fields() -> None:
    """Label-realignment observation records original/new label, payload kind, and target fields."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"

    assert "original_payload_label" in obs, "Must record original_payload_label"
    assert "new_payload_label" in obs, "Must record new_payload_label"
    assert "payload_kind" in obs, "Must record payload_kind"
    assert "target_leaf_kind" in obs, "Must record target_leaf_kind"
    assert "target_leaf_label" in obs, "Must record target_leaf_label"

    assert obs["original_payload_label"] == "", (
        f"original_payload_label must be empty string, got {obs['original_payload_label']!r}"
    )
    assert obs["new_payload_label"] == "2", (
        f"new_payload_label must be the target leaf label '2', got {obs['new_payload_label']!r}"
    )
    assert obs["payload_kind"] == "subsection", (
        f"payload_kind must be 'subsection', got {obs['payload_kind']!r}"
    )
    assert obs["target_leaf_kind"] == "subsection", (
        f"target_leaf_kind must be 'subsection', got {obs['target_leaf_kind']!r}"
    )
    assert obs["target_leaf_label"] == "2", (
        f"target_leaf_label must be '2', got {obs['target_leaf_label']!r}"
    )


# ---------------------------------------------------------------------------
# Test 1.6 — family
# ---------------------------------------------------------------------------

def test_payload_label_realignment_family() -> None:
    """Label-realignment observation carries family=payload_realignment."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("family") == "payload_realignment", (
        f"Expected family=payload_realignment, got {obs.get('family')!r}"
    )


# ===========================================================================
# Site 2 — kind realignment
# ===========================================================================

def _payload_mismatched_kind_matching_label() -> dict[str, Any]:
    """Payload whose kind=paragraph (not subsection) but label='2' matches target leaf."""
    return {
        "kind": "paragraph",
        "label": "2",
        "text": "Inserted text.",
        "children": [],
    }


def _payload_kind_already_matches() -> dict[str, Any]:
    """Payload whose kind=subsection already matches the canonical target kind."""
    return {
        "kind": "subsection",
        "label": "2",
        "text": "Already correct kind.",
        "children": [],
    }


# ---------------------------------------------------------------------------
# Test 2.1 — positive: mismatched kind → observation emitted
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_emits_observation() -> None:
    """Insert payload with leafish kind differing from canonical target kind emits kind-realignment observation."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_mismatched_kind_matching_label(),
        lowering_rejections_out=observations,
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID in rule_ids, (
        f"Expected {_UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID!r} in {rule_ids!r}"
    )


# ---------------------------------------------------------------------------
# Test 2.2 — positive: kind is realigned (no regression)
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_result_has_correct_kind() -> None:
    """After kind realignment, the payload node carries the canonical target leaf kind."""
    observations: list[dict[str, Any]] = []
    result = _call_prepare(
        content_ir=_payload_mismatched_kind_matching_label(),
        lowering_rejections_out=observations,
    )
    assert result.payload_node is not None, "Payload node must be returned"
    assert result.payload_node.kind.value == "subsection", (
        f"Expected kind='subsection' after realignment, got {result.payload_node.kind.value!r}"
    )


# ---------------------------------------------------------------------------
# Test 2.3 — negative: kind already matches → no observation
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_no_observation_when_kind_matches() -> None:
    """When payload kind already equals the canonical target kind, no kind-realignment observation fires."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_kind_already_matches(),
        lowering_rejections_out=observations,
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID not in rule_ids, (
        "Kind-realignment observation must NOT fire when payload kind already matches canonical target"
    )


# ---------------------------------------------------------------------------
# Test 2.4 — strict-mode: strict_disposition=block
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_strict_disposition_is_block() -> None:
    """Kind-realignment observation carries strict_disposition=block."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_mismatched_kind_matching_label(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"
    assert obs.get("strict_disposition") == "block", (
        f"Expected strict_disposition=block, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("blocking") is False, (
        "Observation must be non-blocking (quirks mode allows realignment)"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2.5 — witness fields: original/new kind, payload label, target fields
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_witness_fields() -> None:
    """Kind-realignment observation records original/new kind, payload label, and target fields."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_mismatched_kind_matching_label(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"

    assert "original_payload_kind" in obs, "Must record original_payload_kind"
    assert "new_payload_kind" in obs, "Must record new_payload_kind"
    assert "payload_label" in obs, "Must record payload_label"
    assert "target_leaf_kind" in obs, "Must record target_leaf_kind"
    assert "target_leaf_label" in obs, "Must record target_leaf_label"

    assert obs["original_payload_kind"] == "paragraph", (
        f"original_payload_kind must be 'paragraph', got {obs['original_payload_kind']!r}"
    )
    assert obs["new_payload_kind"] == "subsection", (
        f"new_payload_kind must be 'subsection', got {obs['new_payload_kind']!r}"
    )
    assert obs["payload_label"] == "2", (
        f"payload_label must be '2', got {obs['payload_label']!r}"
    )
    assert obs["target_leaf_kind"] == "subsection", (
        f"target_leaf_kind must be 'subsection', got {obs['target_leaf_kind']!r}"
    )
    assert obs["target_leaf_label"] == "2", (
        f"target_leaf_label must be '2', got {obs['target_leaf_label']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2.6 — family
# ---------------------------------------------------------------------------

def test_payload_kind_realignment_family() -> None:
    """Kind-realignment observation carries family=payload_realignment."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_mismatched_kind_matching_label(),
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("family") == "payload_realignment", (
        f"Expected family=payload_realignment, got {obs.get('family')!r}"
    )


# ===========================================================================
# Cross-site — non-insert action: neither observation fires
# ===========================================================================

def test_neither_realignment_fires_for_replace_action() -> None:
    """Both realignment observations are guarded by curr_action=='insert'; replace skips both."""
    observations: list[dict[str, Any]] = []
    _call_prepare(
        content_ir=_payload_blank_label_matching_kind(),
        curr_action="replace",
        lowering_rejections_out=observations,
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID not in rule_ids, (
        "Label-realignment must NOT fire for replace actions"
    )
    assert _UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID not in rule_ids, (
        "Kind-realignment must NOT fire for replace actions"
    )
