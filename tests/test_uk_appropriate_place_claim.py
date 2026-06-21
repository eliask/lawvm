"""Tests for the UK appropriate-place insert manual claim.

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim (each position form)
  - validator rejects unsupported / mismatched / anchored / occupied claims
    across all three stages (schema, source-binding, position-consistency)
  - gate emits the insert at the claimed position when validated, and withholds
    (no operation) when unvalidated
  - registry registration (proof semantic + candidate rule ids)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.appropriate_place_claim import (
    APPROPRIATE_PLACE_CANDIDATE_RULE_ID,
    APPROPRIATE_PLACE_DEFINITION_ENTRY_CANDIDATE_RULE_ID,
    APPROPRIATE_PLACE_DEFINITION_ENTRY_CLAIM_KIND,
    APPROPRIATE_PLACE_INDEX_ENTRY_CANDIDATE_RULE_ID,
    APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
    APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID,
    APPROPRIATE_PLACE_INSERT_WITHHELD_RULE_ID,
    APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC,
    CLAIM_REJECTED_POSITION_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    POSITION_ALPHABETICAL_INDEX,
    POSITION_FOLLOWING_SIBLING,
    POSITION_PRECEDING_SIBLING,
    AppropriatePlaceInsertClaim,
    claim_from_dict,
    gate_appropriate_place_insert,
    validate_appropriate_place_claim,
)
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)

# A real-shaped appropriate-place definition-entry insert snippet.
_DEF_SNIPPET = 'At the appropriate place insert "widget" means a small device.'
# A real-shaped general appropriate-place insert snippet (no named anchor).
_GEN_SNIPPET = "At the appropriate place insert the following entry."

_LIST = ("apple", "banana", "zebra")


def _alpha_claim(**overrides: Any) -> AppropriatePlaceInsertClaim:
    base = AppropriatePlaceInsertClaim(
        claim_id="claim-1",
        claim_kind=APPROPRIATE_PLACE_DEFINITION_ENTRY_CLAIM_KIND,
        statute_id="ukpga/2008/17",
        effect_id="e-77",
        target_list_eid="s31-defs",
        entry_label="widget",
        entry_text='"widget" means a small device.',
        source_snippet=_DEF_SNIPPET,
        position_kind=POSITION_ALPHABETICAL_INDEX,
        alphabetical_index=1,
        claimant="reviewer",
        claim_status="proposed",
    )
    return replace(base, **overrides)


def _preceding_claim(**overrides: Any) -> AppropriatePlaceInsertClaim:
    base = AppropriatePlaceInsertClaim(
        claim_id="claim-2",
        claim_kind=APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
        statute_id="ukpga/2008/17",
        effect_id="e-78",
        target_list_eid="s276-list",
        entry_label="mango",
        entry_text="mango entry text",
        source_snippet=_GEN_SNIPPET,
        position_kind=POSITION_PRECEDING_SIBLING,
        preceding_sibling_eid="banana",
    )
    return replace(base, **overrides)


# ── schema / round-trip ──────────────────────────────────────────────────────
def test_claim_dict_round_trip() -> None:
    claim = _alpha_claim()
    assert claim_from_dict(claim.to_dict()) == claim


def test_claim_from_dict_defaults() -> None:
    claim = claim_from_dict(
        {
            "claim_id": "c",
            "claim_kind": APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
            "statute_id": "s",
            "effect_id": "e",
            "target_list_eid": "L",
            "entry_text": "t",
            "source_snippet": _GEN_SNIPPET,
            "position_kind": POSITION_ALPHABETICAL_INDEX,
            "alphabetical_index": 0,
        }
    )
    assert claim.alphabetical_index == 0
    assert claim.preceding_sibling_eid == ""
    assert claim.claim_status == "proposed"


# ── validator: accept ────────────────────────────────────────────────────────
def test_validate_accepts_alphabetical_index_claim() -> None:
    v = validate_appropriate_place_claim(_alpha_claim(), target_list=_LIST)
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC


def test_validate_accepts_preceding_sibling_claim() -> None:
    v = validate_appropriate_place_claim(_preceding_claim(), target_list=_LIST)
    assert v.validated


def test_validate_accepts_following_sibling_claim() -> None:
    claim = _preceding_claim(
        position_kind=POSITION_FOLLOWING_SIBLING,
        preceding_sibling_eid="",
        following_sibling_eid="zebra",
    )
    v = validate_appropriate_place_claim(claim, target_list=_LIST)
    assert v.validated


def test_validate_accepts_without_target_list() -> None:
    # No live view supplied: only internal consistency is checked.
    v = validate_appropriate_place_claim(_alpha_claim())
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_appropriate_place_claim(_alpha_claim(claim_kind="nonsense"))
    assert not v.validated
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_position_kind() -> None:
    v = validate_appropriate_place_claim(_alpha_claim(position_kind="middle"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_preceding_missing_eid() -> None:
    v = validate_appropriate_place_claim(
        _preceding_claim(preceding_sibling_eid="")
    )
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_mixed_position_forms() -> None:
    v = validate_appropriate_place_claim(
        _preceding_claim(following_sibling_eid="zebra")
    )
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_empty_entry_payload() -> None:
    v = validate_appropriate_place_claim(
        _alpha_claim(entry_label="", entry_text="")
    )
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_negative_alphabetical_index() -> None:
    v = validate_appropriate_place_claim(_alpha_claim(alphabetical_index=-1))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_free_form_source() -> None:
    # Not an appropriate-place insert shape => may not invent a placement.
    v = validate_appropriate_place_claim(
        _alpha_claim(source_snippet="Section 12 is repealed.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_source_named_anchor() -> None:
    # The source already names a concrete anchor => deterministic, no claim.
    v = validate_appropriate_place_claim(
        _preceding_claim(
            source_snippet="after section 5 insert at the appropriate place the following"
        )
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


@dataclass
class _FakeEffect:
    effect_id: str = ""
    comments: str = ""
    effect_type: str = ""
    source_text: str = ""
    raw_text: str = ""


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(effect_id="other", comments=_GEN_SNIPPET)
    v = validate_appropriate_place_claim(_alpha_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_appropriate_place_shape() -> None:
    effect = _FakeEffect(effect_id="e-77", comments="Section 12 is repealed.")
    v = validate_appropriate_place_claim(_alpha_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(effect_id="e-77", comments=_DEF_SNIPPET)
    v = validate_appropriate_place_claim(
        _alpha_claim(), effect=effect, target_list=_LIST
    )
    assert v.validated


# ── validator: reject position consistency ───────────────────────────────────
def test_validate_rejects_empty_target_list() -> None:
    v = validate_appropriate_place_claim(_alpha_claim(), target_list=())
    assert v.rule_id == CLAIM_REJECTED_POSITION_RULE_ID


def test_validate_rejects_missing_named_sibling() -> None:
    v = validate_appropriate_place_claim(
        _preceding_claim(preceding_sibling_eid="ghost"), target_list=_LIST
    )
    assert v.rule_id == CLAIM_REJECTED_POSITION_RULE_ID


def test_validate_rejects_index_past_end() -> None:
    v = validate_appropriate_place_claim(
        _alpha_claim(alphabetical_index=9), target_list=_LIST
    )
    assert v.rule_id == CLAIM_REJECTED_POSITION_RULE_ID


def test_validate_rejects_occupied_slot() -> None:
    # The entry's own label already occupies the list => incompatible re-insert.
    v = validate_appropriate_place_claim(
        _alpha_claim(entry_label="apple"), target_list=_LIST
    )
    assert v.rule_id == CLAIM_REJECTED_POSITION_RULE_ID


def test_validate_accepts_append_at_end() -> None:
    # index == len is the append slot, which is admissible.
    v = validate_appropriate_place_claim(
        _alpha_claim(alphabetical_index=len(_LIST)), target_list=_LIST
    )
    assert v.validated


# ── gate: apply / withhold ───────────────────────────────────────────────────
def test_gate_emits_insert_at_claimed_alphabetical_slot() -> None:
    claim = _alpha_claim(alphabetical_index=1)
    g = gate_appropriate_place_insert(
        claim, sequence=3, target_list=_LIST, validated=True
    )
    assert g.emitted
    assert g.rule_id == APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID
    assert g.operation is not None
    op = g.operation
    assert op.action is StructuralAction.INSERT
    assert op.sequence == 3
    # alphabetical_index 1 anchors after the member at index 0 ("apple").
    assert g.anchor_eid == "apple"
    assert op.anchor is not None and op.anchor.leaf_label() == "apple"
    assert op.target.leaf_label() == "widget"
    assert op.payload is not None and op.payload.kind is IRNodeKind.ITEM


def test_gate_emits_insert_at_named_preceding_sibling() -> None:
    g = gate_appropriate_place_insert(
        _preceding_claim(), sequence=0, target_list=_LIST, validated=True
    )
    assert g.emitted
    assert g.anchor_eid == "banana"
    assert g.operation is not None
    assert g.operation.anchor is not None
    assert g.operation.anchor.leaf_label() == "banana"


def test_gate_emits_head_insert_for_first_following_sibling() -> None:
    claim = _preceding_claim(
        position_kind=POSITION_FOLLOWING_SIBLING,
        preceding_sibling_eid="",
        following_sibling_eid="apple",
    )
    g = gate_appropriate_place_insert(
        claim, sequence=0, target_list=_LIST, validated=True
    )
    assert g.emitted
    # The follower is first => no preceding sibling => head insert (no anchor).
    assert g.anchor_eid == ""
    assert g.operation is not None and g.operation.anchor is None


def test_gate_emits_head_insert_for_index_zero() -> None:
    g = gate_appropriate_place_insert(
        _alpha_claim(alphabetical_index=0), sequence=0, target_list=_LIST, validated=True
    )
    assert g.emitted
    assert g.anchor_eid == ""
    assert g.operation is not None and g.operation.anchor is None


def test_gate_withholds_when_not_validated() -> None:
    g = gate_appropriate_place_insert(
        _alpha_claim(), sequence=0, target_list=_LIST, validated=False
    )
    assert not g.emitted
    assert g.rule_id == APPROPRIATE_PLACE_INSERT_WITHHELD_RULE_ID
    assert g.operation is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert (
        APPROPRIATE_PLACE_POSITION_PROOF_SEMANTIC in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


def test_candidate_rule_ids_advertise_claim_template() -> None:
    assert (
        APPROPRIATE_PLACE_DEFINITION_ENTRY_CANDIDATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )
    assert (
        APPROPRIATE_PLACE_INDEX_ENTRY_CANDIDATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )
    assert APPROPRIATE_PLACE_CANDIDATE_RULE_ID in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    specs = _UK_RULE_SPECS
    for rule_id in (
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_POSITION_RULE_ID,
        APPROPRIATE_PLACE_INSERT_EMITTED_RULE_ID,
        APPROPRIATE_PLACE_INSERT_WITHHELD_RULE_ID,
    ):
        assert rule_id in specs
