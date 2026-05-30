"""Tests for letter-suffix new-leaf insert promotion in UK lowering.

AGENTS.md §6 phase-ownership: when a Replace operation targets a
letter-suffix label (3A, 1B, 6ZA) and the source payload matches the target
leaf, lowering should directly emit Insert with a numeric-stem anchor rather
than emitting Replace and relying on replay-time recovery
(uk_replay_replace_materialized_as_insert_for_missing_leaf).

Rule ID : uk_effect_after_anchor_insert_promoted
Family  : targeted_after_anchor_insert
Blocking: False  (nonblocking observation)
strict_disposition : apply
quirks_disposition : apply

AGENTS.md obligations covered:
  §1.2  no action-family mutation without ownership
  §6    phase ownership — lowering detects the pattern, relay-time recovers only as fallback
  §15.1 synthetic unit test (positive)
  §15.2 negative test (genuine replace, plain numeric target)
  §15.3 finding/observation test (witness fields)
  §15.4 disposition test (apply/apply — not block)
"""
from __future__ import annotations

from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effect_substitution_normalization import (
    UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID,
    UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID,
    UKSubstitutedPayloadInsertNormalization,
    _letter_suffix_anchor_address,
    _letter_suffix_anchor_label,
    lower_substituted_payload_insert_normalization,
)
from lawvm.uk_legislation.effects import UKEffectRecord

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _source_backed_actual_el() -> ET._Element:
    """Minimal source XML element simulating a real extracted provision."""
    return ET.fromstring(
        f'<P2 xmlns="{_LEG_NS}" id="section-19-3a"><Pnumber>3A</Pnumber>'
        "<P2para><Text>New subsection (3A) text.</Text></P2para></P2>"
    )


def _extracted_el_after_anchor_insert() -> ET._Element:
    """Extracted source element whose instruction text contains 'after subsection (3) insert'.

    This is the canonical signal of a genuinely new provision being inserted between
    existing numbered siblings. The BlockAmendment child holds the new content.
    """
    return ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-1-3">
          <Pnumber>3</Pnumber>
          <P2para>
            <Text>after subsection (3) insert—</Text>
            <BlockAmendment>
              <P2>
                <Pnumber>3A</Pnumber>
                <P2para><Text>New subsection (3A) text.</Text></P2para>
              </P2>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _minimal_effect(
    effect_type: str = "substituted",
    affected_uri: str = "/id/ukpga/1962/46/section/19/subsection/3a",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-aai-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri=affected_uri,
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1962",
        affected_number="46",
        affected_provisions="s. 19(3A)",
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 1",
        affecting_title="Test Amending Act 2024",
    )


def _addr_section_19_subsection_3a() -> LegalAddress:
    return LegalAddress(path=(("section", "19"), ("subsection", "3a")))


def _addr_section_19_subsection_3() -> LegalAddress:
    return LegalAddress(path=(("section", "19"), ("subsection", "3")))


def _addr_section_1a() -> LegalAddress:
    """Top-level section with letter-suffix label."""
    return LegalAddress(path=(("section", "1a"),))


def _addr_section_1() -> LegalAddress:
    return LegalAddress(path=(("section", "1"),))


def _addr_section_19_subsection_3_plain() -> LegalAddress:
    """Plain numeric subsection — NOT a letter-suffix label."""
    return LegalAddress(path=(("section", "19"), ("subsection", "3")))


def _payload_subsection_3a() -> dict[str, Any]:
    """Payload whose kind=subsection and label=3A — matches target leaf 3a."""
    return {
        "kind": "subsection",
        "label": "3A",
        "text": "New subsection (3A) text.",
        "children": [],
    }


def _payload_subsection_3() -> dict[str, Any]:
    """Payload whose kind=subsection and label=3 — matches plain numeric target."""
    return {
        "kind": "subsection",
        "label": "3",
        "text": "Plain subsection (3) replacement text.",
        "children": [],
    }


_UNSET: Any = object()  # sentinel for "caller did not specify"


def _call_normalization(
    *,
    curr_action: str = "replace",
    target: Optional[LegalAddress] = None,
    content_ir: Optional[dict[str, Any]] = None,
    effect: Optional[UKEffectRecord] = None,
    target_ref: str = "s. 19(3A)",
    original_target_refs: Optional[list[str]] = None,
    target_index: int = 0,
    source_payload_actual_el: Any = _UNSET,
    extracted_el: Any = _UNSET,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> UKSubstitutedPayloadInsertNormalization:
    if target is None:
        target = _addr_section_19_subsection_3a()
    if content_ir is None:
        content_ir = _payload_subsection_3a()
    if effect is None:
        effect = _minimal_effect()
    if original_target_refs is None:
        original_target_refs = [target_ref]
    # Default: simulate a source-backed payload (actual XML element present).
    # Pass source_payload_actual_el=None explicitly to test the inferred-payload guard.
    resolved_actual_el: Optional[ET._Element] = (
        _source_backed_actual_el() if source_payload_actual_el is _UNSET
        else source_payload_actual_el
    )
    # Default: use an extracted_el whose instruction text says "after subsection (3) insert—"
    # so the after-anchor guard fires. Pass extracted_el=None explicitly to test
    # the case where no "after X insert" pattern exists in the source.
    resolved_extracted_el: Optional[ET._Element] = (
        _extracted_el_after_anchor_insert() if extracted_el is _UNSET
        else extracted_el
    )
    return lower_substituted_payload_insert_normalization(
        effect=effect,
        curr_action=curr_action,
        original_target_refs=original_target_refs,
        target_index=target_index,
        target_ref=target_ref,
        target=target,
        content_ir=content_ir,
        source_replaced_sibling_count=None,
        source_payload_actual_el=resolved_actual_el,
        extracted_el=resolved_extracted_el,
        extracted_text=None,
        lowering_rejections_out=lowering_rejections_out,
    )


# ===========================================================================
# Unit helpers for _letter_suffix_anchor_label
# ===========================================================================


def test_letter_suffix_anchor_label_3a() -> None:
    """'3A' → '3'."""
    assert _letter_suffix_anchor_label("3A") == "3"


def test_letter_suffix_anchor_label_1b() -> None:
    """'1B' → '1'."""
    assert _letter_suffix_anchor_label("1B") == "1"


def test_letter_suffix_anchor_label_6za() -> None:
    """'6ZA' → '6'."""
    assert _letter_suffix_anchor_label("6ZA") == "6"


def test_letter_suffix_anchor_label_11zf() -> None:
    """'11ZF' → '11'."""
    assert _letter_suffix_anchor_label("11ZF") == "11"


def test_letter_suffix_anchor_label_plain_numeric() -> None:
    """Plain numeric labels return None."""
    assert _letter_suffix_anchor_label("3") is None
    assert _letter_suffix_anchor_label("20") is None


def test_letter_suffix_anchor_label_pure_alpha() -> None:
    """Pure alphabetic labels return None."""
    assert _letter_suffix_anchor_label("A") is None
    assert _letter_suffix_anchor_label("ZA") is None


def test_letter_suffix_anchor_label_empty() -> None:
    """Empty string returns None."""
    assert _letter_suffix_anchor_label("") is None


# ===========================================================================
# Unit helpers for _letter_suffix_anchor_address
# ===========================================================================


def test_letter_suffix_anchor_address_subsection_3a() -> None:
    """section:19/subsection:3a → section:19/subsection:3."""
    target = _addr_section_19_subsection_3a()
    anchor = _letter_suffix_anchor_address(target)
    assert anchor is not None
    assert anchor == _addr_section_19_subsection_3()


def test_letter_suffix_anchor_address_section_1a() -> None:
    """Top-level section:1a → section:1."""
    target = _addr_section_1a()
    anchor = _letter_suffix_anchor_address(target)
    assert anchor is not None
    assert anchor == _addr_section_1()


def test_letter_suffix_anchor_address_plain_numeric_returns_none() -> None:
    """Plain numeric leaf → None (cannot derive letter-suffix anchor)."""
    target = _addr_section_19_subsection_3_plain()
    anchor = _letter_suffix_anchor_address(target)
    assert anchor is None


def test_letter_suffix_anchor_address_empty_path_returns_none() -> None:
    """Empty path → None."""
    anchor = _letter_suffix_anchor_address(LegalAddress(path=()))
    assert anchor is None


# ===========================================================================
# Test 1 — Positive: letter-suffix replace → promoted to insert
# ===========================================================================


def test_letter_suffix_replace_promoted_to_insert() -> None:
    """Replace targeting a letter-suffix leaf with matching payload → Insert."""
    result = _call_normalization(curr_action="replace")
    assert result.curr_action == "insert", (
        f"Expected curr_action='insert' after promotion, got {result.curr_action!r}"
    )


def test_letter_suffix_replace_promotion_emits_observation() -> None:
    """Promotion fires the uk_effect_after_anchor_insert_promoted observation."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID in rule_ids, (
        f"Expected {UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID!r} in {rule_ids!r}"
    )


def test_letter_suffix_replace_promotion_returns_anchor_eid() -> None:
    """Promoted result carries a non-empty anchor_preceding_eid."""
    result = _call_normalization(curr_action="replace")
    assert result.anchor_preceding_eid is not None, (
        "anchor_preceding_eid must be set after letter-suffix promotion"
    )
    assert result.anchor_preceding_eid != "", (
        "anchor_preceding_eid must be non-empty"
    )


def test_letter_suffix_replace_promotion_anchor_eid_source() -> None:
    """anchor_preceding_eid_source is set to the rule ID."""
    result = _call_normalization(curr_action="replace")
    assert result.anchor_preceding_eid_source == UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID, (
        f"Expected anchor_preceding_eid_source={UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID!r}, "
        f"got {result.anchor_preceding_eid_source!r}"
    )


# ===========================================================================
# Test 2 — Observation shape: family, dispositions, blocking
# ===========================================================================


def test_promotion_observation_family() -> None:
    """Observation carries family=targeted_after_anchor_insert."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations
         if o.get("rule_id") == UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None, "Promotion observation must be emitted"
    assert obs.get("family") == "targeted_after_anchor_insert", (
        f"Expected family=targeted_after_anchor_insert, got {obs.get('family')!r}"
    )


def test_promotion_observation_dispositions() -> None:
    """Promotion observation has strict_disposition=apply and quirks_disposition=apply."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations
         if o.get("rule_id") == UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None, "Promotion observation must be emitted"
    assert obs.get("strict_disposition") == "apply", (
        f"Expected strict_disposition=apply, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )


def test_promotion_observation_is_nonblocking() -> None:
    """Promotion observation has blocking=False."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations
         if o.get("rule_id") == UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None, "Promotion observation must be emitted"
    assert obs.get("blocking") is False, (
        f"Promotion observation must be non-blocking, got blocking={obs.get('blocking')!r}"
    )


def test_promotion_observation_detail_contains_anchor() -> None:
    """Observation carries anchor_address and anchor_eid as top-level fields
    (lowering_records merges detail into the observation payload directly)."""
    observations: list[dict[str, Any]] = []
    _call_normalization(curr_action="replace", lowering_rejections_out=observations)
    obs = next(
        (o for o in observations
         if o.get("rule_id") == UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
        None,
    )
    assert obs is not None
    assert "anchor_address" in obs, f"observation missing anchor_address: {obs!r}"
    assert "anchor_eid" in obs, f"observation missing anchor_eid: {obs!r}"
    assert obs["anchor_eid"], "anchor_eid must be non-empty"


# ===========================================================================
# Test 3 — Negative: genuine replace (plain numeric target → no promotion)
# ===========================================================================


def test_plain_numeric_replace_not_promoted() -> None:
    """Replace targeting a plain numeric subsection is NOT promoted to insert."""
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=_addr_section_19_subsection_3(),
        content_ir=_payload_subsection_3(),
        target_ref="s. 19(3)",
        original_target_refs=["s. 19(3)"],
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "replace", (
        f"Plain-numeric replace must stay 'replace', got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion observation must NOT fire for plain-numeric replace"
    )
    assert result.anchor_preceding_eid is None, (
        "anchor_preceding_eid must be None for plain-numeric replace"
    )


def test_insert_action_not_promoted() -> None:
    """An already-Insert action with letter-suffix target is not re-promoted."""
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="insert",
        lowering_rejections_out=observations,
    )
    # Should still be insert, and no promotion observation (no replace→insert transition)
    assert result.curr_action == "insert", (
        f"Insert action must remain insert, got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion must not fire when curr_action is already insert"
    )


def test_replace_with_mismatched_payload_not_promoted() -> None:
    """Replace for letter-suffix target with mismatched payload is NOT promoted."""
    # Payload kind=subsection label=99 does not match target subsection:3a
    mismatched_payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "99",
        "text": "Mismatched subsection text.",
        "children": [],
    }
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        content_ir=mismatched_payload,
        lowering_rejections_out=observations,
    )
    # Payload doesn't match target leaf, so promotion guard (_source_payload_matches_target_leaf)
    # should prevent promotion
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion must NOT fire when payload does not match the target leaf"
    )


# ===========================================================================
# Test 4 — Lowering rejections_out=None does not crash
# ===========================================================================


def test_promotion_with_none_rejections_out_does_not_crash() -> None:
    """Promotion with lowering_rejections_out=None must not raise."""
    result = _call_normalization(curr_action="replace", lowering_rejections_out=None)
    assert result.curr_action == "insert"


def test_substitute_instruction_text_promoted_pattern_c() -> None:
    """Replace for letter-suffix target where source says 'For X substitute' IS promoted (Pattern C).

    Guard 4 was widened: the original guard required instruction text to contain
    'after [anchor] insert', which excluded Pattern C — pure substitutions of a letter-suffix
    leaf ('For subsection (1A) substitute—') whose target didn't yet exist in the replayed
    state. The widened structural guard fires whenever the leaf label is a letter-suffix,
    regardless of instruction text.
    """
    for_substitute_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-13-3">
          <Pnumber>3</Pnumber>
          <P2para>
            <Text>For subsection (1A) substitute—</Text>
            <BlockAmendment>
              <P2>
                <Pnumber>1A</Pnumber>
                <P2para><Text>Replacement subsection (1A) text.</Text></P2para>
              </P2>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )
    payload_1a: dict[str, Any] = {
        "kind": "subsection",
        "label": "1A",
        "text": "Replacement subsection (1A) text.",
        "children": [],
    }
    target_1a = LegalAddress(path=(("section", "132"), ("subsection", "1a")))
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target_1a,
        content_ir=payload_1a,
        target_ref="s. 132(1A)",
        original_target_refs=["s. 132(1A)"],
        source_payload_actual_el=ET.fromstring(
            f'<P2 xmlns="{_LEG_NS}" id="section-132-1a"><Pnumber>1A</Pnumber>'
            "<P2para><Text>Replacement subsection (1A) text.</Text></P2para></P2>"
        ),
        extracted_el=for_substitute_el,
        lowering_rejections_out=observations,
    )
    # Pattern C: 'For X substitute' on a letter-suffix target with matching structural
    # payload is now promoted to insert (A16 widened guard).
    assert result.curr_action == "insert", (
        f"Pattern C 'For X substitute' on letter-suffix target must be promoted to insert, "
        f"got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID in rule_ids, (
        "Promotion observation must fire for Pattern C 'For X substitute' letter-suffix case"
    )


def test_inferred_payload_no_actual_el_not_promoted() -> None:
    """When source_payload_actual_el is None (inferred payload), promotion must NOT fire.

    infer_source_payload_from_target synthesizes a payload that trivially matches
    the target by construction; this is not a source-backed structural provision
    and must not trigger the letter-suffix promotion.
    """
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        source_payload_actual_el=None,  # simulate inferred payload
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "replace", (
        f"Inferred payload must NOT be promoted to insert, got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion must NOT fire when source_payload_actual_el is None"
    )


# ===========================================================================
# Test 5 — Top-level letter-suffix section (single-element path)
# ===========================================================================


def test_toplevel_section_1a_promoted() -> None:
    """Top-level section:1A replace with matching payload is promoted.

    Source instruction: "after section (1) insert section (1A)..."
    """
    payload_section_1a: dict[str, Any] = {
        "kind": "section",
        "label": "1A",
        "text": "New section 1A text.",
        "children": [],
    }
    after_section_insert_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-1">
          <Pnumber>1</Pnumber>
          <P2para>
            <Text>after section 1 insert—</Text>
            <BlockAmendment>
              <P1>
                <Pnumber>1A</Pnumber>
                <P1para><Text>New section 1A text.</Text></P1para>
              </P1>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )
    actual_el_1a = ET.fromstring(
        f'<P1 xmlns="{_LEG_NS}" id="section-1a"><Pnumber>1A</Pnumber>'
        "<P1para><Text>New section 1A text.</Text></P1para></P1>"
    )
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=_addr_section_1a(),
        content_ir=payload_section_1a,
        target_ref="s. 1A",
        original_target_refs=["s. 1A"],
        source_payload_actual_el=actual_el_1a,
        extracted_el=after_section_insert_el,
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "insert", (
        f"Top-level letter-suffix replace must be promoted to insert, got {result.curr_action!r}"
    )
    assert result.anchor_preceding_eid is not None
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID in rule_ids


# ===========================================================================
# Pattern C tests (widened structural guard)
# ===========================================================================
#
# These tests verify that the widened A13 guard (drop instruction-text
# requirement, use structural letter-suffix check) correctly handles Pattern C:
# single letter-suffix substitution with a structural payload where the
# instruction text says "For X substitute—" not "after X insert".


def _pattern_c_actual_el(label: str = "4A") -> ET._Element:
    """Real source XML element for a letter-suffix provision substitution."""
    return ET.fromstring(
        f'<P2 xmlns="{_LEG_NS}" id="section-25-4a"><Pnumber>{label}</Pnumber>'
        f"<P2para><Text>New subsection ({label}) text.</Text></P2para></P2>"
    )


def _pattern_c_extracted_el_for_substitute(label: str = "4A") -> ET._Element:
    """Source element with 'For subsection (X) substitute—' instruction text."""
    return ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-25">
          <Pnumber>25</Pnumber>
          <P2para>
            <Text>For subsection ({label}) substitute—</Text>
            <BlockAmendment>
              <P2>
                <Pnumber>{label}</Pnumber>
                <P2para><Text>New subsection ({label}) text.</Text></P2para>
              </P2>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )


# ---------------------------------------------------------------------------
# Test PC-1: Pattern C positive — single letter-suffix substitution promoted
# ---------------------------------------------------------------------------


def test_pattern_c_single_letter_suffix_substitution_promoted() -> None:
    """Pattern C: single 'substituted' effect on letter-suffix leaf → Insert.

    Case: ukpga/1978/29 effect from uksi/2005/2011 targeting
    schedule:1/paragraph:6A with effect_type='substituted'. Instruction text
    is 'For paragraph 6A substitute—'. The widened A13 guard must promote this
    to Insert using the numeric-stem anchor (paragraph:6).
    """
    target = LegalAddress(path=(("schedule", "1"), ("paragraph", "6a")))
    payload: dict[str, Any] = {
        "kind": "paragraph",
        "label": "6A",
        "text": "New paragraph (6A) text.",
        "children": [],
    }
    actual_el = ET.fromstring(
        f'<P3 xmlns="{_LEG_NS}" id="schedule-1-para-6a"><Pnumber>6A</Pnumber>'
        "<P3para><Text>New paragraph (6A) text.</Text></P3para></P3>"
    )
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-1">
          <Pnumber>6</Pnumber>
          <P3para>
            <Text>For paragraph 6A substitute—</Text>
            <BlockAmendment>
              <P3>
                <Pnumber>6A</Pnumber>
                <P3para><Text>New paragraph (6A) text.</Text></P3para>
              </P3>
            </BlockAmendment>
          </P3para>
        </P3>
        """
    )
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=payload,
        target_ref="Sch. 1 para. 6A",
        original_target_refs=["Sch. 1 para. 6A"],
        source_payload_actual_el=actual_el,
        extracted_el=extracted_el,
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "insert", (
        f"Pattern C: single letter-suffix substitution must be promoted to insert, "
        f"got {result.curr_action!r}"
    )
    assert result.anchor_preceding_eid is not None, (
        "anchor_preceding_eid must be set for Pattern C promotion"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID in rule_ids, (
        f"Expected {UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID!r} in {rule_ids!r}"
    )


def test_pattern_c_section_32d_substitution_promoted() -> None:
    """Pattern C: section:32d substitution promoted.

    ukpga/1978/29, effect from asp/2005/13 targeting section:32d with
    effect_type='substituted'. Predecessors 32A-32C exist. At replay time
    section:32d is absent. Widened guard must promote to Insert(anchor=32).
    """
    target = LegalAddress(path=(("section", "32d"),))
    payload: dict[str, Any] = {
        "kind": "section",
        "label": "32D",
        "text": "New section 32D text.",
        "children": [],
    }
    actual_el = ET.fromstring(
        f'<P1 xmlns="{_LEG_NS}" id="section-32d"><Pnumber>32D</Pnumber>'
        "<P1para><Text>New section 32D text.</Text></P1para></P1>"
    )
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="section-32">
          <Pnumber>32</Pnumber>
          <P1para>
            <Text>For section 32D substitute—</Text>
            <BlockAmendment>
              <P1>
                <Pnumber>32D</Pnumber>
                <P1para><Text>New section 32D text.</Text></P1para>
              </P1>
            </BlockAmendment>
          </P1para>
        </P1>
        """
    )
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=payload,
        target_ref="s. 32D",
        original_target_refs=["s. 32D"],
        source_payload_actual_el=actual_el,
        extracted_el=extracted_el,
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "insert", (
        f"Pattern C section:32D must be promoted to insert, got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID in rule_ids


# ---------------------------------------------------------------------------
# Test PC-2: Pattern C negative — non-letter-suffix label (plain numeric)
# ---------------------------------------------------------------------------


def test_pattern_c_plain_numeric_target_not_promoted() -> None:
    """Pattern C negative: target=subsection:5 (plain numeric) — no promotion.

    The letter-suffix structural guard requires digits+letters pattern. A plain
    numeric target (subsection:5) has no letter suffix and must not be promoted.
    """
    target = LegalAddress(path=(("section", "25"), ("subsection", "5")))
    payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "5",
        "text": "Replacement subsection (5) text.",
        "children": [],
    }
    actual_el = ET.fromstring(
        f'<P2 xmlns="{_LEG_NS}" id="section-25-5"><Pnumber>5</Pnumber>'
        "<P2para><Text>Replacement subsection (5) text.</Text></P2para></P2>"
    )
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=payload,
        target_ref="s. 25(5)",
        original_target_refs=["s. 25(5)"],
        source_payload_actual_el=actual_el,
        extracted_el=_pattern_c_extracted_el_for_substitute("5"),
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "replace", (
        f"Plain numeric target must NOT be promoted, got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Pattern C promotion must NOT fire for plain numeric target"
    )


# ---------------------------------------------------------------------------
# Test PC-3: Pattern C negative — payload doesn't match target leaf
# ---------------------------------------------------------------------------


def test_pattern_c_payload_mismatch_not_promoted() -> None:
    """Pattern C negative: payload label mismatch (label=99, target label=4a) — no promotion.

    Guard 3 (_source_payload_matches_target_leaf) rejects when the payload label
    does not match the target leaf label. Use a clearly wrong label (99 vs 4a).
    """
    target = LegalAddress(path=(("section", "25"), ("subsection", "4a")))
    # Wrong label: 99 does not match 4a
    mismatched_payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "99",
        "text": "Wrong subsection text.",
        "children": [],
    }
    actual_el = _pattern_c_actual_el("4A")
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=mismatched_payload,
        target_ref="s. 25(4A)",
        original_target_refs=["s. 25(4A)"],
        source_payload_actual_el=actual_el,
        extracted_el=_pattern_c_extracted_el_for_substitute("4A"),
        lowering_rejections_out=observations,
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion must NOT fire when payload label does not match target leaf label"
    )


# ---------------------------------------------------------------------------
# Test PC-4: Pattern C negative — no actual_el (inferred payload)
# ---------------------------------------------------------------------------


def test_pattern_c_no_actual_el_not_promoted() -> None:
    """Pattern C negative: source_payload_actual_el=None (inferred) — no promotion.

    Guard 2 requires a real source XML element. infer_source_payload_from_target
    constructs a payload that trivially matches by construction; promotion must
    not fire without real source evidence.
    """
    target = LegalAddress(path=(("section", "25"), ("subsection", "4a")))
    payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "4A",
        "text": "Inferred subsection (4A) text.",
        "children": [],
    }
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=payload,
        target_ref="s. 25(4A)",
        original_target_refs=["s. 25(4A)"],
        source_payload_actual_el=None,  # inferred payload — no actual element
        extracted_el=_pattern_c_extracted_el_for_substitute("4A"),
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "replace", (
        f"Inferred payload must NOT be promoted, got {result.curr_action!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "Promotion must NOT fire when source_payload_actual_el is None"
    )


# ---------------------------------------------------------------------------
# Test PC-5: A15 still wins for block substitution groups (Pattern B)
# ---------------------------------------------------------------------------


def test_a15_wins_for_block_substitution_group() -> None:
    """A15 (Pattern B) fires for block-substitution group tail — A13 not reached.

    When an effect decomposes into a group [s.25(4), s.25(4A), s.25(4B)] (ops _0, _1, _2),
    the tail ops (target_index > 0, group[0] = plain numeric stem) are handled by A15's
    _block_substitution_tail_insert_detail path. The result carries
    anchor_preceding_eid_source = UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID,
    not the A13 rule ID.
    """
    # Group: [s.25(4), s.25(4A), s.25(4B)] — tail op at index 1 targeting subsection:4a
    original_target_refs = ["s. 25(4)", "s. 25(4A)", "s. 25(4B)"]
    target = LegalAddress(path=(("section", "25"), ("subsection", "4a")))
    payload: dict[str, Any] = {
        "kind": "subsection",
        "label": "4A",
        "text": "New subsection (4A) text.",
        "children": [],
    }
    actual_el = _pattern_c_actual_el("4A")
    observations: list[dict[str, Any]] = []
    result = _call_normalization(
        curr_action="replace",
        target=target,
        content_ir=payload,
        target_ref="s. 25(4A)",
        original_target_refs=original_target_refs,
        target_index=1,
        source_payload_actual_el=actual_el,
        extracted_el=_pattern_c_extracted_el_for_substitute("4A"),
        lowering_rejections_out=observations,
    )
    # A15 should have fired: result is insert with A15's rule ID as source
    assert result.curr_action == "insert", (
        f"Block-substitution group tail must be promoted to insert (A15), got {result.curr_action!r}"
    )
    assert result.anchor_preceding_eid_source == UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID, (
        f"A15 must win for block-substitution group tail: expected "
        f"{UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID!r}, "
        f"got {result.anchor_preceding_eid_source!r}"
    )
    rule_ids = [obs.get("rule_id") for obs in observations]
    assert UK_EFFECT_BLOCK_SUBSTITUTION_TAIL_PROMOTED_RULE_ID in rule_ids, (
        "A15 observation must be emitted for block-substitution group tail"
    )
    assert UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID not in rule_ids, (
        "A13 must NOT fire when A15 wins for block-substitution group"
    )
