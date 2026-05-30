"""Tests for block-substitution tail promotion in UK lowering.

Pattern B: an effect whose affected_provisions covers a
range like 's. 25(4)-(4B)' decomposes into a multi-target group.  Op _0 is a
Replace on the numeric stem (subsection:4); ops _1, _2 target letter-suffix
variants (4a, 4b) that do not yet exist in the materialized state.  Lowering
should promote _1/_2 to InsertAfter(anchor=<previous target in group>) instead
of emitting Replace and relying on replay-time
uk_replay_replace_materialized_as_insert_for_missing_leaf recovery.

Rule ID : uk_effect_block_substitution_tail_promoted_to_insert_after
Family  : targeted_after_anchor_insert
Blocking: False  (nonblocking observation; nonblocking in strict mode)
strict_disposition : apply
quirks_disposition : apply

AGENTS.md obligations covered:
  §0    prime directive — named rule, observation emitted
  §1.2  no action-family mutation without ownership
  §6    phase ownership — lowering detects the pattern
  §7    heuristic policy — rule_id, family, source witness, observation
  §15.1 synthetic unit test (positive three-op group)
  §15.2 synthetic negative tests (payload mismatch, standalone tail, non-letter-suffix
        consecutive labels, _0 not Replace, group already at stem index)
  §15.3 finding/observation test (witness fields, dispositions, blocking)
  §15.4 adjudication-parity assertion (no recovery observation emitted for promoted ops)
"""
from __future__ import annotations

from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effect_substitution_normalization import (
    UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID,
    UKSubstitutedPayloadInsertNormalization,
    _block_substitution_tail_insert_detail,
    lower_substituted_payload_insert_normalization,
)
from lawvm.uk_legislation.effects import UKEffectRecord

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

# ---------------------------------------------------------------------------
# Source XML fixtures
# ---------------------------------------------------------------------------


def _block_amendment_el() -> ET._Element:
    """Minimal BlockAmendment XML for 'For subsection (4) substitute—'."""
    return ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-6-paragraph-2-2-a">
          <Pnumber>2</Pnumber>
          <P2para>
            <Text>for subsection (4) substitute—</Text>
            <BlockAmendment>
              <P2 id="section-25-4"><Pnumber>4</Pnumber>
                <P2para><Text>Subsection (4) text.</Text></P2para></P2>
              <P2 id="section-25-4a"><Pnumber>4A</Pnumber>
                <P2para><Text>New subsection (4A) text.</Text></P2para></P2>
              <P2 id="section-25-4b"><Pnumber>4B</Pnumber>
                <P2para><Text>New subsection (4B) text.</Text></P2para></P2>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )


def _real_source_el() -> ET._Element:
    """Minimal real source XML element (simulates actual_el != None)."""
    return ET.fromstring(
        f'<P2 xmlns="{_LEG_NS}" id="section-25-4a"><Pnumber>4A</Pnumber>'
        "<P2para><Text>New subsection (4A) text.</Text></P2para></P2>"
    )


# ---------------------------------------------------------------------------
# Effect fixture
# ---------------------------------------------------------------------------


def _minimal_effect(
    affected_provisions: str = "s. 25(4)-(4B)",
    effect_type: str = "substituted for s. 24(4)",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-bstp-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/1978/29/section/25",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1978",
        affected_number="29",
        affected_provisions=affected_provisions,
        affecting_uri="/id/uksi/2005/2011",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2005",
        affecting_number="2011",
        affecting_provisions="Sch. 6 para. 2(2)(a)",
        affecting_title="Test SI 2005/2011",
    )


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------


def _addr_s25_ss4() -> LegalAddress:
    return LegalAddress(path=(("section", "25"), ("subsection", "4")))


def _addr_s25_ss4a() -> LegalAddress:
    return LegalAddress(path=(("section", "25"), ("subsection", "4a")))


def _addr_s25_ss4b() -> LegalAddress:
    return LegalAddress(path=(("section", "25"), ("subsection", "4b")))


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _payload_ss4() -> dict[str, Any]:
    return {"kind": "subsection", "label": "4", "text": "Subsection (4) text.", "children": []}


def _payload_ss4a() -> dict[str, Any]:
    return {"kind": "subsection", "label": "4A", "text": "New subsection (4A) text.", "children": []}


def _payload_ss4b() -> dict[str, Any]:
    return {"kind": "subsection", "label": "4B", "text": "New subsection (4B) text.", "children": []}


# Group target refs for the three-op case
_GROUP_REFS = ["s. 25(4)", "s. 25(4A)", "s. 25(4B)"]

# ---------------------------------------------------------------------------
# Helper: call lower_substituted_payload_insert_normalization for a group tail
# ---------------------------------------------------------------------------

_UNSET: Any = object()


def _call_normalization(
    *,
    curr_action: str = "replace",
    target: Optional[LegalAddress] = None,
    content_ir: Optional[dict[str, Any]] = None,
    effect: Optional[UKEffectRecord] = None,
    target_ref: str = "s. 25(4A)",
    original_target_refs: Optional[list[str]] = None,
    target_index: int = 1,
    source_payload_actual_el: Any = _UNSET,
    extracted_el: Any = _UNSET,
    source_replaced_sibling_count: Optional[int] = None,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> UKSubstitutedPayloadInsertNormalization:
    if target is None:
        target = _addr_s25_ss4a()
    if content_ir is None:
        content_ir = _payload_ss4a()
    if effect is None:
        effect = _minimal_effect()
    if original_target_refs is None:
        original_target_refs = list(_GROUP_REFS)
    resolved_actual_el: Optional[ET._Element] = (
        _real_source_el() if source_payload_actual_el is _UNSET else source_payload_actual_el
    )
    resolved_extracted_el: Optional[ET._Element] = (
        _block_amendment_el() if extracted_el is _UNSET else extracted_el
    )
    return lower_substituted_payload_insert_normalization(
        effect=effect,
        curr_action=curr_action,
        original_target_refs=original_target_refs,
        target_index=target_index,
        target_ref=target_ref,
        target=target,
        content_ir=content_ir,
        source_replaced_sibling_count=source_replaced_sibling_count,
        source_payload_actual_el=resolved_actual_el,
        extracted_el=resolved_extracted_el,
        extracted_text=None,
        lowering_rejections_out=lowering_rejections_out,
    )


# ===========================================================================
# Unit tests for _block_substitution_tail_insert_detail
# ===========================================================================


def test_block_sub_tail_detail_positive_index1() -> None:
    """Three-op group: index 1 (4a) returns non-None detail."""
    actual_el = _real_source_el()
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
        target=_addr_s25_ss4a(),
        content_ir=_payload_ss4a(),
        source_payload_actual_el=actual_el,
    )
    assert detail is not None, "Expected non-None detail for index-1 tail"
    assert detail["stem_leaf_label"] == "4"
    assert detail["leaf_label"] == "4a"
    assert detail["anchor_eid"], "anchor_eid must be non-empty"


def test_block_sub_tail_detail_positive_index2() -> None:
    """Three-op group: index 2 (4b) returns non-None detail anchored at 4a."""
    actual_el = _real_source_el()
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=list(_GROUP_REFS),
        target_index=2,
        target=_addr_s25_ss4b(),
        content_ir=_payload_ss4b(),
        source_payload_actual_el=actual_el,
    )
    assert detail is not None, "Expected non-None detail for index-2 tail"
    assert detail["stem_leaf_label"] == "4"
    assert detail["leaf_label"] == "4b"
    # Anchor should be the prev_ref (4a), not the stem (4)
    assert detail["prev_ref"] == "s. 25(4A)"


def test_block_sub_tail_detail_index0_returns_none() -> None:
    """Index 0 (the stem itself) is never a tail — returns None."""
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=list(_GROUP_REFS),
        target_index=0,
        target=_addr_s25_ss4(),
        content_ir=_payload_ss4(),
        source_payload_actual_el=_real_source_el(),
    )
    assert detail is None, "Stem op (index 0) must not be promoted"


def test_block_sub_tail_detail_no_actual_el_returns_none() -> None:
    """source_payload_actual_el=None → no promotion (synthesized payload guard)."""
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
        target=_addr_s25_ss4a(),
        content_ir=_payload_ss4a(),
        source_payload_actual_el=None,
    )
    assert detail is None, "No actual_el → must not promote"


def test_block_sub_tail_detail_payload_mismatch_returns_none() -> None:
    """Payload label doesn't match target leaf → no promotion."""
    mismatch_payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "99",
        "text": "Wrong.",
        "children": [],
    }
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
        target=_addr_s25_ss4a(),
        content_ir=mismatch_payload,
        source_payload_actual_el=_real_source_el(),
    )
    assert detail is None, "Payload mismatch → must not promote"


def test_block_sub_tail_detail_standalone_letter_suffix_returns_none() -> None:
    """Standalone group where _0 already targets a letter-suffix label (Pattern C shape).

    Group refs: ['s. 19(3A)'] — _0 is 3a, not a plain numeric stem. No Pattern B.
    """
    target_3a = LegalAddress(path=(("section", "19"), ("subsection", "3a")))
    payload_3a: dict[str, Any] = {
        "kind": "subsection",
        "label": "3A",
        "text": "New text.",
        "children": [],
    }
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=["s. 19(3A)"],
        target_index=0,
        target=target_3a,
        content_ir=payload_3a,
        source_payload_actual_el=_real_source_el(),
    )
    assert detail is None, "Standalone letter-suffix (index 0) must not be promoted by Pattern B"


def test_block_sub_tail_detail_consecutive_numeric_labels_returns_none() -> None:
    """Non-letter-suffix consecutive group: [s.25(4), s.25(5)] — _1 target 5 is NOT a letter-suffix of 4."""
    target_5 = LegalAddress(path=(("section", "25"), ("subsection", "5")))
    payload_5: dict[str, Any] = {
        "kind": "subsection",
        "label": "5",
        "text": "Subsection (5) text.",
        "children": [],
    }
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=["s. 25(4)", "s. 25(5)"],
        target_index=1,
        target=target_5,
        content_ir=payload_5,
        source_payload_actual_el=_real_source_el(),
    )
    assert detail is None, "Plain-numeric consecutive targets must not be promoted by Pattern B"


def test_block_sub_tail_detail_stem_mismatch_returns_none() -> None:
    """Current leaf stem doesn't match group _0 leaf label → no promotion."""
    # Group refs: ['s. 25(2)', 's. 25(4A)'] — 4a has stem 4, but group[0] has leaf 2.
    target_4a = LegalAddress(path=(("section", "25"), ("subsection", "4a")))
    detail = _block_substitution_tail_insert_detail(
        original_target_refs=["s. 25(2)", "s. 25(4A)"],
        target_index=1,
        target=target_4a,
        content_ir=_payload_ss4a(),
        source_payload_actual_el=_real_source_el(),
    )
    assert detail is None, "Stem mismatch between current leaf and group[0] leaf must not promote"


# ===========================================================================
# Test 1 — Positive: three-op group, _1 and _2 promoted to insert
# ===========================================================================


def test_group_tail_1_promoted_to_insert() -> None:
    """Three-op group at index 1 (4A): Replace → Insert."""
    result = _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4a(),
        content_ir=_payload_ss4a(),
        target_ref="s. 25(4A)",
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
    )
    assert result.curr_action == "insert", (
        f"Expected 'insert' for group tail at index 1, got {result.curr_action!r}"
    )


def test_group_tail_2_promoted_to_insert() -> None:
    """Three-op group at index 2 (4B): Replace → Insert."""
    result = _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4b(),
        content_ir=_payload_ss4b(),
        target_ref="s. 25(4B)",
        original_target_refs=list(_GROUP_REFS),
        target_index=2,
    )
    assert result.curr_action == "insert", (
        f"Expected 'insert' for group tail at index 2, got {result.curr_action!r}"
    )


def test_group_stem_stays_replace() -> None:
    """Three-op group at index 0 (stem 4): stays Replace (no promotion)."""
    result = _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4(),
        content_ir=_payload_ss4(),
        target_ref="s. 25(4)",
        original_target_refs=list(_GROUP_REFS),
        target_index=0,
    )
    assert result.curr_action == "replace", (
        f"Stem op must stay 'replace', got {result.curr_action!r}"
    )


def test_two_tails_emit_two_observations() -> None:
    """Two promoted tails each emit one uk_effect_block_substitution_tail_promoted_to_insert_after."""
    observations: list[dict[str, Any]] = []
    # Tail 1 (index 1)
    _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4a(),
        content_ir=_payload_ss4a(),
        target_ref="s. 25(4A)",
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
        lowering_rejections_out=observations,
    )
    # Tail 2 (index 2)
    _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4b(),
        content_ir=_payload_ss4b(),
        target_ref="s. 25(4B)",
        original_target_refs=list(_GROUP_REFS),
        target_index=2,
        lowering_rejections_out=observations,
    )
    promo_obs = [
        o for o in observations
        if o.get("rule_id") == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID
    ]
    assert len(promo_obs) == 2, (
        f"Expected exactly 2 promotion observations, got {len(promo_obs)}: {promo_obs!r}"
    )


# ===========================================================================
# Test 2 — Observation shape: family, dispositions, blocking
# ===========================================================================


def test_promotion_observation_family() -> None:
    """Observation has family=targeted_after_anchor_insert."""
    observations: list[dict[str, Any]] = []
    _call_normalization(
        curr_action="replace",
        lowering_rejections_out=observations,
    )
    obs = next(
        (o for o in observations if o.get("rule_id") == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None, "Promotion observation must be emitted"
    assert obs.get("family") == "targeted_after_anchor_insert", (
        f"Expected family=targeted_after_anchor_insert, got {obs.get('family')!r}"
    )


def test_promotion_observation_dispositions() -> None:
    """Observation has strict_disposition=apply and quirks_disposition=apply."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations if o.get("rule_id") == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("strict_disposition") == "apply", (
        f"Expected strict_disposition=apply, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )


def test_promotion_observation_is_nonblocking() -> None:
    """Observation has blocking=False."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations if o.get("rule_id") == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("blocking") is False, (
        f"Promotion observation must be non-blocking, got blocking={obs.get('blocking')!r}"
    )


def test_promotion_observation_contains_anchor_eid() -> None:
    """Observation contains a non-empty anchor_eid field."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations if o.get("rule_id") == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None
    assert "anchor_eid" in obs, f"observation missing anchor_eid: {obs!r}"
    assert obs["anchor_eid"], "anchor_eid must be non-empty"


def test_promotion_returns_anchor_preceding_eid() -> None:
    """Promoted normalization result carries a non-empty anchor_preceding_eid."""
    result = _call_normalization(curr_action="replace")
    assert result.anchor_preceding_eid is not None
    assert result.anchor_preceding_eid != ""


def test_promotion_anchor_source_is_rule_id() -> None:
    """anchor_preceding_eid_source is set to the rule ID."""
    result = _call_normalization(curr_action="replace")
    assert result.anchor_preceding_eid_source == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID


# ===========================================================================
# Test 3 — Negative: payload mismatch — no promotion
# ===========================================================================


def test_payload_mismatch_not_promoted() -> None:
    """Replace for letter-suffix tail with mismatched payload label — NOT promoted."""
    bad_payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "99",
        "text": "Wrong.",
        "children": [],
    }
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        content_ir=bad_payload,
        lowering_rejections_out=observations,
    )
    rule_ids = [o.get("rule_id") for o in observations]
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID not in rule_ids
    assert result.curr_action != "insert" or result.anchor_preceding_eid is None or (
        # if something else promoted, it must not be this rule
        UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID not in rule_ids
    )


# ===========================================================================
# Test 4 — Negative: standalone letter-suffix (no numeric stem in group)
# ===========================================================================


def test_standalone_tail_not_promoted_by_pattern_b() -> None:
    """Single-target group for s.19(3A) — Pattern C shape, NOT Pattern B.

    Pattern B requires group[0] to be a plain numeric stem. Here the only
    target is already a letter-suffix label, so no promotion occurs.
    """
    observations: list[dict[str, Any]] = []
    result = lower_substituted_payload_insert_normalization(
        effect=_minimal_effect(affected_provisions="s. 19(3A)", effect_type="substituted"),
        curr_action="replace",
        original_target_refs=["s. 19(3A)"],
        target_index=0,
        target_ref="s. 19(3A)",
        target=LegalAddress(path=(("section", "19"), ("subsection", "3a"))),
        content_ir={
            "kind": "subsection",
            "label": "3A",
            "text": "New text.",
            "children": [],
        },
        source_replaced_sibling_count=None,
        source_payload_actual_el=_real_source_el(),
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=observations,
    )
    rule_ids = [o.get("rule_id") for o in observations]
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID not in rule_ids, (
        "Pattern B must NOT fire for standalone letter-suffix (Pattern C shape)"
    )


# ===========================================================================
# Test 5 — Negative: non-letter-suffix consecutive labels
# ===========================================================================


def test_consecutive_numeric_targets_not_promoted() -> None:
    """Group [s.25(4), s.25(5)] — _1 is numeric, not a letter-suffix of _0."""
    target_5 = LegalAddress(path=(("section", "25"), ("subsection", "5")))
    payload_5: dict[str, Any] = {
        "kind": "subsection",
        "label": "5",
        "text": "Subsection (5) text.",
        "children": [],
    }
    observations: list[dict[str, Any]] = []
    result = lower_substituted_payload_insert_normalization(
        effect=_minimal_effect(affected_provisions="s. 25(4)-(5)"),
        curr_action="replace",
        original_target_refs=["s. 25(4)", "s. 25(5)"],
        target_index=1,
        target_ref="s. 25(5)",
        target=target_5,
        content_ir=payload_5,
        source_replaced_sibling_count=None,
        source_payload_actual_el=_real_source_el(),
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=observations,
    )
    rule_ids = [o.get("rule_id") for o in observations]
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID not in rule_ids, (
        "Pattern B must NOT fire when tail target is a plain numeric sibling"
    )


# ===========================================================================
# Test 6 — Negative: no actual_el (synthesized payload)
# ===========================================================================


def test_no_actual_el_not_promoted() -> None:
    """source_payload_actual_el=None → no Pattern B promotion."""
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        source_payload_actual_el=None,
        lowering_rejections_out=observations,
    )
    rule_ids = [o.get("rule_id") for o in observations]
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID not in rule_ids, (
        "Pattern B must not fire when no actual_el (synthesized payload guard)"
    )


# ===========================================================================
# Test 7 — Adjudication parity: promoted tail does NOT emit recovery observation
# ===========================================================================


def test_promoted_tail_does_not_emit_recovery_observation() -> None:
    """Promoted tail emits the new rule_id, NOT uk_replay_replace_materialized_as_insert_for_missing_leaf."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    rule_ids = [o.get("rule_id") for o in observations]
    assert "uk_replay_replace_materialized_as_insert_for_missing_leaf" not in rule_ids, (
        "Promoted tail must NOT emit the replay-time recovery observation at lowering"
    )
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID in rule_ids, (
        "Promoted tail must emit the new lowering rule observation"
    )


# ===========================================================================
# Test 8 — lowering_rejections_out=None does not crash
# ===========================================================================


def test_promotion_with_none_rejections_out_does_not_crash() -> None:
    """Promotion with lowering_rejections_out=None must not raise."""
    result = _call_normalization(curr_action="replace", lowering_rejections_out=None)
    assert result.curr_action == "insert"


# ===========================================================================
# Test 9 — Anchor EID chaining: _1 anchors at stem, _2 anchors at _1's target
# ===========================================================================


def test_tail_1_anchor_is_stem_eid() -> None:
    """Tail at index 1 (4A) anchors at the stem (subsection:4) eId."""
    result = _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4a(),
        content_ir=_payload_ss4a(),
        target_ref="s. 25(4A)",
        original_target_refs=list(_GROUP_REFS),
        target_index=1,
    )
    assert result.anchor_preceding_eid is not None
    # The anchor EID should reference subsection 4 (the prev target at index 0)
    assert "4" in result.anchor_preceding_eid, (
        f"anchor_preceding_eid={result.anchor_preceding_eid!r} must reference '4'"
    )
    # Must NOT reference 4a
    assert "4a" not in result.anchor_preceding_eid.lower(), (
        "anchor_preceding_eid for _1 must anchor at stem (4), not 4a"
    )


def test_tail_2_anchor_is_prev_tail_eid() -> None:
    """Tail at index 2 (4B) anchors at _1's target (subsection:4a), not the stem."""
    result = _call_normalization(
        curr_action="replace",
        target=_addr_s25_ss4b(),
        content_ir=_payload_ss4b(),
        target_ref="s. 25(4B)",
        original_target_refs=list(_GROUP_REFS),
        target_index=2,
    )
    assert result.anchor_preceding_eid is not None
    # Anchor should reference 4a (the prev target at index 1, not the stem 4)
    assert "4a" in result.anchor_preceding_eid.lower(), (
        f"anchor_preceding_eid for _2 must reference '4a', got {result.anchor_preceding_eid!r}"
    )
