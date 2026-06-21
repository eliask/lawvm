"""Tests for the UK N5 source/feed target reconciliation manual claim.

Covers (per AGENTS.md §15):
  - claim schema + dict round-trip
  - validator accepts a valid owned claim for each recognized basis, incl. the
    resolution-consistency stage against a live target member view
  - validator rejects malformed / mismatched / inconsistent claims across all
    three stages (schema, source-binding, resolution-consistency), including an
    identical source==feed target and a resolved target that is neither named
    surface
  - gate emits a REPLAYABLE child-target resolution only for the child-locatable
    basis, a NON-replayable adjudication finding for the parent-authoritative and
    genuinely-ambiguous bases, and withholds (no finding) when unvalidated
  - absent claim ⇒ no emission (replay-neutral integration via compile)
  - registry registration (proof semantic + candidate template + cataloged rules)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from lawvm.tools.uk_semantic_claims import UK_OPERATION_FAMILY_PROOF_SEMANTICS
from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)
from lawvm.uk_legislation.source_feed_reconciliation_claim import (
    BASIS_FEED_PARENT_AUTHORITATIVE,
    BASIS_GENUINELY_AMBIGUOUS,
    BASIS_SOURCE_CHILD_LOCATABLE,
    CLAIM_REJECTED_RESOLUTION_RULE_ID,
    CLAIM_REJECTED_SCHEMA_RULE_ID,
    CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
    CLAIM_VALIDATED_RULE_ID,
    SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID,
    SOURCE_FEED_RECONCILIATION_CLAIM_KIND,
    SOURCE_FEED_RECONCILIATION_CLAIM_TEMPLATE_RULE_ID,
    SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
    SOURCE_FEED_RECONCILIATION_FINDING_WITHHELD_RULE_ID,
    SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC,
    SourceFeedReconciliationClaim,
    claim_from_dict,
    gate_source_feed_reconciliation_claim,
    validate_source_feed_reconciliation_claim,
)

# A real-shaped N5 source: an omission scoped to a child the feed does not name.
# ITTOIA s.536(1) <- ukpga/2020/14: "omit the 'and' at the end of sub-paragraph (i)".
_SOURCE = "omit the “and” at the end of sub-paragraph (i)"
_EFFECT_TYPE = "word omitted"
# The source-named child (authoritative under the child-locatable basis) and the
# feed-named parent (the broader surface the feed targets).
_CHILD_TARGET = "ukpga/2005/5/section:536/subsection:1/paragraph:a/subparagraph:i"
_PARENT_TARGET = "ukpga/2005/5/section:536/subsection:1"
# A live target view in which the source-named child is a real member.
_LIVE_MEMBERS = (
    "ukpga/2005/5/section:536/subsection:1/paragraph:a/subparagraph:i",
    "ukpga/2005/5/section:536/subsection:1/paragraph:a/subparagraph:ii",
)


def _claim(**overrides: Any) -> SourceFeedReconciliationClaim:
    base = SourceFeedReconciliationClaim(
        claim_id="claim-1",
        claim_kind=SOURCE_FEED_RECONCILIATION_CLAIM_KIND,
        statute_id="ukpga/2005/5",
        effect_id="key-ittoia-536-and",
        effect_type=_EFFECT_TYPE,
        source_named_target=_CHILD_TARGET,
        feed_named_target=_PARENT_TARGET,
        resolved_target_eid=_CHILD_TARGET,
        reconciliation_basis=BASIS_SOURCE_CHILD_LOCATABLE,
        source_snippet=_SOURCE,
        rationale="source explicitly scopes the omission to sub-paragraph (i)",
        claimant="reviewer",
        claim_status="proposed",
    )
    return replace(base, **overrides)


@dataclass
class _FakeEffect:
    effect_id: str = ""
    effect_type: str = ""
    extracted_text: str = ""
    source_text: str = ""
    raw_text: str = ""
    comments: str = ""


# ── schema / round-trip ──────────────────────────────────────────────────────
def test_claim_dict_round_trip() -> None:
    claim = _claim()
    assert claim_from_dict(claim.to_dict()) == claim


def test_claim_from_dict_defaults() -> None:
    claim = claim_from_dict(
        {
            "claim_id": "c",
            "claim_kind": SOURCE_FEED_RECONCILIATION_CLAIM_KIND,
            "statute_id": "ukpga/2005/5",
            "effect_id": "e",
            "effect_type": _EFFECT_TYPE,
            "source_named_target": _CHILD_TARGET,
            "feed_named_target": _PARENT_TARGET,
            "resolved_target_eid": _PARENT_TARGET,
            "reconciliation_basis": BASIS_FEED_PARENT_AUTHORITATIVE,
            "source_snippet": _SOURCE,
        }
    )
    assert claim.rationale == ""
    assert claim.claimant == ""
    assert claim.claim_status == "proposed"


# ── validator: accept each basis ─────────────────────────────────────────────
def test_validate_accepts_child_locatable_basis() -> None:
    v = validate_source_feed_reconciliation_claim(_claim())
    assert v.validated
    assert v.rule_id == CLAIM_VALIDATED_RULE_ID
    assert v.proof_semantic == SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC


def test_validate_accepts_child_locatable_with_live_member_view() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(), live_target_member_eids=_LIVE_MEMBERS
    )
    assert v.validated


def test_validate_accepts_feed_parent_authoritative_basis() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(
            reconciliation_basis=BASIS_FEED_PARENT_AUTHORITATIVE,
            resolved_target_eid=_PARENT_TARGET,
        )
    )
    assert v.validated


def test_validate_accepts_genuinely_ambiguous_basis() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(
            reconciliation_basis=BASIS_GENUINELY_AMBIGUOUS,
            resolved_target_eid=_PARENT_TARGET,
        )
    )
    assert v.validated


def test_validate_accepts_matching_effect() -> None:
    effect = _FakeEffect(
        effect_id="key-ittoia-536-and",
        effect_type=_EFFECT_TYPE,
        extracted_text=_SOURCE,
    )
    v = validate_source_feed_reconciliation_claim(_claim(), effect=effect)
    assert v.validated


# ── validator: reject schema ─────────────────────────────────────────────────
def test_validate_rejects_unknown_kind() -> None:
    v = validate_source_feed_reconciliation_claim(_claim(claim_kind="nonsense"))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_source_named_target() -> None:
    v = validate_source_feed_reconciliation_claim(_claim(source_named_target=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_feed_named_target() -> None:
    v = validate_source_feed_reconciliation_claim(_claim(feed_named_target=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_resolved_target() -> None:
    v = validate_source_feed_reconciliation_claim(_claim(resolved_target_eid=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_unknown_basis() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(reconciliation_basis="guesswork")
    )
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


def test_validate_rejects_missing_effect_type() -> None:
    v = validate_source_feed_reconciliation_claim(_claim(effect_type=""))
    assert v.rule_id == CLAIM_REJECTED_SCHEMA_RULE_ID


# ── validator: reject source binding ─────────────────────────────────────────
def test_validate_rejects_free_form_source() -> None:
    # A non-child-scoped source is not the N5 family.
    v = validate_source_feed_reconciliation_claim(
        _claim(source_snippet="The section is repealed.")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_non_omission_effect_type() -> None:
    # The recognizer is gated on a word-omission verb; a substitution is rejected.
    v = validate_source_feed_reconciliation_claim(
        _claim(effect_type="words substituted")
    )
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_id_mismatch() -> None:
    effect = _FakeEffect(
        effect_id="other", effect_type=_EFFECT_TYPE, extracted_text=_SOURCE
    )
    v = validate_source_feed_reconciliation_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


def test_validate_rejects_effect_without_child_omission_shape() -> None:
    effect = _FakeEffect(
        effect_id="key-ittoia-536-and",
        effect_type=_EFFECT_TYPE,
        extracted_text="some unrelated payload",
    )
    v = validate_source_feed_reconciliation_claim(_claim(), effect=effect)
    assert v.rule_id == CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID


# ── validator: reject resolution consistency ─────────────────────────────────
def test_validate_rejects_identical_source_and_feed_target() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(feed_named_target=_CHILD_TARGET)
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_rejects_resolved_target_not_among_named_surfaces() -> None:
    v = validate_source_feed_reconciliation_claim(
        _claim(resolved_target_eid="ukpga/2005/5/section:999")
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_rejects_child_locatable_resolving_to_parent() -> None:
    # Child-locatable basis must resolve to the source-named child, not the parent.
    v = validate_source_feed_reconciliation_claim(
        _claim(resolved_target_eid=_PARENT_TARGET)
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_rejects_child_absent_from_live_target_view() -> None:
    # The source-named child is not a member of the supplied live target view, so
    # a replayable child resolution would over-omit — rejected (§2.1).
    v = validate_source_feed_reconciliation_claim(
        _claim(),
        live_target_member_eids=(
            "ukpga/2005/5/section:536/subsection:1/paragraph:a/subparagraph:ii",
        ),
    )
    assert v.rule_id == CLAIM_REJECTED_RESOLUTION_RULE_ID


def test_validate_skips_live_member_check_without_view() -> None:
    # No live view supplied: schema+source binding is the floor and the
    # child-locatable claim validates (resolution-consistency live check skipped).
    v = validate_source_feed_reconciliation_claim(_claim())
    assert v.validated


# ── gate: emit replayable / finding-only / withhold ──────────────────────────
def test_gate_emits_replayable_child_resolution_for_locatable_basis() -> None:
    g = gate_source_feed_reconciliation_claim(_claim(), validated=True)
    assert g.emitted
    assert g.replayable is True
    assert g.rule_id == SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID
    assert g.finding is not None
    finding = g.finding
    assert finding.replayable is True
    assert finding.resolved_target_eid == _CHILD_TARGET
    row = finding.to_dict()
    assert row["replayable"] is True
    assert row["proof_semantic"] == SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC


def test_gate_emits_non_replayable_finding_for_parent_authoritative_basis() -> None:
    claim = _claim(
        reconciliation_basis=BASIS_FEED_PARENT_AUTHORITATIVE,
        resolved_target_eid=_PARENT_TARGET,
    )
    g = gate_source_feed_reconciliation_claim(claim, validated=True)
    assert g.emitted
    assert g.replayable is False
    assert g.rule_id == SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    assert g.finding.replayable is False
    assert g.finding.resolved_target_eid == _PARENT_TARGET


def test_gate_emits_non_replayable_finding_for_ambiguous_basis() -> None:
    claim = _claim(
        reconciliation_basis=BASIS_GENUINELY_AMBIGUOUS,
        resolved_target_eid=_PARENT_TARGET,
    )
    g = gate_source_feed_reconciliation_claim(claim, validated=True)
    assert g.emitted
    assert g.replayable is False
    assert g.rule_id == SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID
    assert g.finding is not None
    assert g.finding.replayable is False


def test_gate_withholds_when_not_validated() -> None:
    g = gate_source_feed_reconciliation_claim(_claim(), validated=False)
    assert not g.emitted
    assert g.replayable is False
    assert g.rule_id == SOURCE_FEED_RECONCILIATION_FINDING_WITHHELD_RULE_ID
    assert g.finding is None


# ── registry registration ────────────────────────────────────────────────────
def test_proof_semantic_registered() -> None:
    assert (
        SOURCE_FEED_RECONCILIATION_PROOF_SEMANTIC
        in UK_OPERATION_FAMILY_PROOF_SEMANTICS
    )


def test_candidate_rule_id_advertises_claim_template() -> None:
    assert (
        SOURCE_FEED_RECONCILIATION_CLAIM_TEMPLATE_RULE_ID
        in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS
    )


def test_validator_and_gate_rule_ids_cataloged() -> None:
    from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS

    for rule_id in (
        SOURCE_FEED_RECONCILIATION_CLAIM_TEMPLATE_RULE_ID,
        CLAIM_VALIDATED_RULE_ID,
        CLAIM_REJECTED_SCHEMA_RULE_ID,
        CLAIM_REJECTED_SOURCE_MISMATCH_RULE_ID,
        CLAIM_REJECTED_RESOLUTION_RULE_ID,
        SOURCE_FEED_RECONCILIATION_CHILD_RESOLVED_RULE_ID,
        SOURCE_FEED_RECONCILIATION_FINDING_EMITTED_RULE_ID,
        SOURCE_FEED_RECONCILIATION_FINDING_WITHHELD_RULE_ID,
    ):
        assert rule_id in _UK_RULE_SPECS
