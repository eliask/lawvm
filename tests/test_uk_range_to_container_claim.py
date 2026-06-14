"""Tests for the UK range-to-container resolution manual claim.

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim (each recognized basis), incl. the
    member-consistency stage against a live container member list
  - validator rejects malformed / mismatched / inconsistent claims across all
    three stages (schema, source-binding, member-consistency), including a
    non-contiguous resolved set and an endpoint not in the container
  - gate emits the NON-replayable resolved-members finding only when validated,
    and withholds (no finding, no text op) when unvalidated
  - registry registration (proof semantic + candidate template + cataloged rules)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)
from lawvm.uk_legislation.range_to_container_claim import (
    BASIS_CONTIGUOUS_CONTAINER_SPAN,
    BASIS_POST_PROGRAM_RENUMBERED_SPAN,
    CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    RANGE_TO_CONTAINER_CLAIM_KIND,
    RANGE_TO_CONTAINER_CLAIM_TEMPLATE_RULE_ID,
    RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID,
    RANGE_TO_CONTAINER_FINDING_WITHHELD_RULE_ID,
    RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC,
    RangeToContainerClaim,
    claim_from_dict,
    gate_range_to_container_claim,
    validate_range_to_container_claim,
)

# A real-shaped range-to-container effect surface: a sibling RANGE substituted
# into a higher-level container.
_RANGE_SOURCE = "For sections 12 to 14 substitute— new Part 3."
# A live container whose ordered members include the range span 12..14 plus
# neighbours, so member-consistency can be exercised.
_CONTAINER_MEMBERS = (
    "ukpga/2000/1/part:2/section:11",
    "ukpga/2000/1/part:2/section:12",
    "ukpga/2000/1/part:2/section:13",
    "ukpga/2000/1/part:2/section:14",
    "ukpga/2000/1/part:2/section:15",
)
_RESOLVED_SPAN = (
    "ukpga/2000/1/part:2/section:12",
    "ukpga/2000/1/part:2/section:13",
    "ukpga/2000/1/part:2/section:14",
)


def _claim(**overrides: Any) -> RangeToContainerClaim:
    base = RangeToContainerClaim(
        claim_id="claim-1",
        claim_kind=RANGE_TO_CONTAINER_CLAIM_KIND,
        statute_id="ukpga/2000/1",
        effect_id="key-range-12-14",
        container_eid="ukpga/2000/1/part:2",
        range_start_label="12",
        range_end_label="14",
        source_snippet=_RANGE_SOURCE,
        resolved_member_eids=_RESOLVED_SPAN,
        resolution_basis=BASIS_CONTIGUOUS_CONTAINER_SPAN,
        renumbering_program_id="",
        claimant="reviewer",
        status="proposed",
    )
    return replace(base, **overrides)


@dataclass
class _FakeEffect:
    effect_id: str = ""
    effect_type: str = ""
    comments: str = ""
    source_text: str = ""
    raw_text: str = ""


# ── schema / round-trip ──────────────────────────────────────────────────────
def test_claim_dict_round_trip() -> None:
    claim = _claim()
    assert claim_from_dict(claim.to_dict()) == claim


def test_claim_from_dict_defaults() -> None:
    claim = claim_from_dict(
        {
            "claim_id": "c",
            "claim_kind": RANGE_TO_CONTAINER_CLAIM_KIND,
            "statute_id": "ukpga/2000/1",
            "effect_id": "e",
            "container_eid": "ukpga/2000/1/part:2",
            "range_start_label": "12",
            "range_end_label": "14",
            "source_snippet": _RANGE_SOURCE,
            "resolved_member_eids": list(_RESOLVED_SPAN),
            "resolution_basis": BASIS_CONTIGUOUS_CONTAINER_SPAN,
        }
    )
    assert claim.renumbering_program_id == ""
    assert claim.status == "proposed"
    assert claim.resolved_member_eids == _RESOLVED_SPAN


# ── validator: accept ────────────────────────────────────────────────────────
def test_validate_accepts_contiguous_span_claim() -> None:
    v = validate_range_to_container_claim(_claim())
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC


def test_validate_accepts_post_program_renumbered_span_claim() -> None:
    claim = _claim(
        resolution_basis=BASIS_POST_PROGRAM_RENUMBERED_SPAN,
        renumbering_program_id="ukpga/1999/9",
    )
    v = validate_range_to_container_claim(claim)
    assert v.validated


def test_validate_accepts_with_live_container_member_consistency() -> None:
    v = validate_range_to_container_claim(
        _claim(), container_member_eids=_CONTAINER_MEMBERS
    )
    assert v.validated


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(effect_id="key-range-12-14", effect_type=_RANGE_SOURCE)
    v = validate_range_to_container_claim(_claim(), effect=effect)
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_range_to_container_claim(_claim(claim_kind="nonsense"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_container_eid() -> None:
    v = validate_range_to_container_claim(_claim(container_eid=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_range_endpoint() -> None:
    v = validate_range_to_container_claim(_claim(range_end_label=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_empty_resolved_members() -> None:
    v = validate_range_to_container_claim(_claim(resolved_member_eids=()))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_basis() -> None:
    v = validate_range_to_container_claim(_claim(resolution_basis="guesswork"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_post_program_basis_without_program_id() -> None:
    v = validate_range_to_container_claim(
        _claim(resolution_basis=BASIS_POST_PROGRAM_RENUMBERED_SPAN)
    )
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_single_unit_source() -> None:
    # A single-unit (non-range) substitution is not the range-to-container family.
    v = validate_range_to_container_claim(
        _claim(source_snippet="For section 12 substitute— new section.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_free_form_source() -> None:
    v = validate_range_to_container_claim(
        _claim(source_snippet="Section 12 is repealed.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(effect_id="other", effect_type=_RANGE_SOURCE)
    v = validate_range_to_container_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_range_shape() -> None:
    effect = _FakeEffect(effect_id="key-range-12-14", effect_type="words substituted")
    v = validate_range_to_container_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


# ── validator: reject member consistency ─────────────────────────────────────
def test_validate_rejects_endpoint_not_in_container() -> None:
    # range_end_label 99 is not a member of the live container.
    v = validate_range_to_container_claim(
        _claim(range_end_label="99"),
        container_member_eids=_CONTAINER_MEMBERS,
    )
    assert v.rule_id == CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID


def test_validate_rejects_non_contiguous_resolved_set() -> None:
    # The resolved set skips section:13 — a gap in the contiguous span.
    v = validate_range_to_container_claim(
        _claim(
            resolved_member_eids=(
                "ukpga/2000/1/part:2/section:12",
                "ukpga/2000/1/part:2/section:14",
            )
        ),
        container_member_eids=_CONTAINER_MEMBERS,
    )
    assert v.rule_id == CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID


def test_validate_rejects_stray_member_outside_span() -> None:
    # The resolved set includes section:15, which is outside the 12..14 span.
    v = validate_range_to_container_claim(
        _claim(
            resolved_member_eids=(
                "ukpga/2000/1/part:2/section:12",
                "ukpga/2000/1/part:2/section:13",
                "ukpga/2000/1/part:2/section:14",
                "ukpga/2000/1/part:2/section:15",
            )
        ),
        container_member_eids=_CONTAINER_MEMBERS,
    )
    assert v.rule_id == CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID


def test_validate_rejects_inverted_endpoints() -> None:
    v = validate_range_to_container_claim(
        _claim(
            range_start_label="14",
            range_end_label="12",
            source_snippet="For sections 14 to 12 substitute— new Part.",
        ),
        container_member_eids=_CONTAINER_MEMBERS,
    )
    assert v.rule_id == CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID


def test_validate_skips_member_consistency_without_live_container() -> None:
    # No live container supplied: member-consistency is skipped, schema+source
    # binding is the floor and the claim validates (an even non-contiguous set is
    # NOT rejected without a live container to compare against).
    v = validate_range_to_container_claim(
        _claim(
            resolved_member_eids=(
                "ukpga/2000/1/part:2/section:12",
                "ukpga/2000/1/part:2/section:14",
            )
        )
    )
    assert v.validated


# ── gate: emit / withhold ────────────────────────────────────────────────────
def test_gate_emits_finding_when_validated() -> None:
    g = gate_range_to_container_claim(_claim(), validated=True)
    assert g.emitted
    assert g.rule_id == RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    finding = g.finding
    # The finding is a NON-replayable record, not a text op.
    assert finding.replayable is False
    assert finding.resolved_member_eids == _RESOLVED_SPAN
    assert finding.container_eid == "ukpga/2000/1/part:2"
    row = finding.to_dict()
    assert row["replayable"] is False
    assert row["proof_semantic"] == RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC
    assert row["resolved_member_eids"] == list(_RESOLVED_SPAN)


def test_gate_withholds_when_not_validated() -> None:
    g = gate_range_to_container_claim(_claim(), validated=False)
    assert not g.emitted
    assert g.rule_id == RANGE_TO_CONTAINER_FINDING_WITHHELD_RULE_ID
    assert g.finding is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert (
        RANGE_TO_CONTAINER_RESOLUTION_PROOF_SEMANTIC
        in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


def test_candidate_rule_id_advertises_claim_template() -> None:
    assert (
        RANGE_TO_CONTAINER_CLAIM_TEMPLATE_RULE_ID in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    for rule_id in (
        RANGE_TO_CONTAINER_CLAIM_TEMPLATE_RULE_ID,
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_MEMBER_CONSISTENCY_RULE_ID,
        RANGE_TO_CONTAINER_FINDING_EMITTED_RULE_ID,
        RANGE_TO_CONTAINER_FINDING_WITHHELD_RULE_ID,
    ):
        assert rule_id in _UK_RULE_SPECS
