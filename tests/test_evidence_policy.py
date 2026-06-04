"""Tests for EvidencePolicyRegistry, EvidenceGraphPredicate, PolicyExpr, IndependenceDimension.

Required by spec:
  test_evidence_policy_registry_hash_stable
  test_structural_independence_distinguishes_shared_parent
  test_negative_evidence_admissible_as_positive_attestation (kernel side — see test_evidence_kernel.py)
  test_no_arbitrary_python_in_policy
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from lawvm.core.evidence_policy import (
    EvidenceGraphPredicate,
    EvidencePolicyRegistry,
    IndependenceDimension,
    PolicyExpr,
    check_independence,
    count_distinct_at_least,
    exists,
    independent,
    materials_match_dependencies,
    none,
    not_retracted,
    registry_from_dict,
    registry_to_dict,
    reachable,
    signed_by,
    within_time,
)
from lawvm.core.provenance_graph import Interval, Producer, ProvenanceAttestation, ArtifactRef


# ---------------------------------------------------------------------------
# PolicyExpr basics
# ---------------------------------------------------------------------------


def test_policy_expr_requires_nonempty_op():
    with pytest.raises(ValueError, match="non-empty string"):
        PolicyExpr(op="", args={})


def test_policy_expr_requires_mapping_args():
    with pytest.raises(ValueError, match="Mapping"):
        PolicyExpr(op="exists", args="bad")  # type: ignore[arg-type]


def test_policy_expr_canonical_dict_sorted():
    expr = PolicyExpr(op="exists", args={"z": 1, "a": 2})
    d = expr.canonical_dict()
    keys = list(d["args"].keys())
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# DSL helpers produce correct ops
# ---------------------------------------------------------------------------


def test_exists_helper():
    e = exists("span_verified", producer_id="lawvm.span.v1")
    assert e.op == "exists"
    assert e.args["attestation_kind"] == "span_verified"
    assert e.args["producer_id"] == "lawvm.span.v1"


def test_none_helper():
    e = none("retracted")
    assert e.op == "none"
    assert e.args["attestation_kind"] == "retracted"


def test_count_distinct_at_least_helper():
    e = count_distinct_at_least("reviewed", path="producer.producer_id", n=2)
    assert e.op == "count_distinct_at_least"
    assert e.args["n"] == 2


def test_not_retracted_helper():
    e = not_retracted()
    assert e.op == "not_retracted"
    assert e.args["subject"] == "self"


def test_independent_helper():
    e = independent("reviewed", by=("producer_kind", "parent_attestation"))
    assert e.op == "independent"
    assert "producer_kind" in e.args["by"]


def test_materials_match_dependencies_helper():
    e = materials_match_dependencies()
    assert e.op == "materials_match_dependencies"


# ---------------------------------------------------------------------------
# EvidenceGraphPredicate validation
# ---------------------------------------------------------------------------


def test_predicate_requires_nonempty_predicate_id():
    with pytest.raises(ValueError, match="predicate_id"):
        EvidenceGraphPredicate(predicate_id="", claim_kind="fi.v1.FOO", required=())


def test_predicate_requires_nonempty_claim_kind():
    with pytest.raises(ValueError, match="claim_kind"):
        EvidenceGraphPredicate(predicate_id="p1", claim_kind="", required=())


# ---------------------------------------------------------------------------
# EvidencePolicyRegistry hash stability (required)
# ---------------------------------------------------------------------------


def test_evidence_policy_registry_hash_stable():
    """Same predicates → same registry_hash across two independent builds."""
    pred = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
        forbidden=(exists("retracted"),),
    )
    reg1 = EvidencePolicyRegistry.build(
        registry_id="lawvm.test",
        registry_version="v0",
        predicates=(pred,),
    )
    reg2 = EvidencePolicyRegistry.build(
        registry_id="lawvm.test",
        registry_version="v0",
        predicates=(pred,),
    )
    assert reg1.registry_hash == reg2.registry_hash
    assert reg1.registry_hash != ""


def test_registry_hash_changes_with_different_predicate():
    pred1 = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    pred2 = EvidenceGraphPredicate(
        predicate_id="test.p2",
        claim_kind="fi.v1.OTHER",
        required=(exists("entailment_verified"),),
    )
    reg1 = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred1,))
    reg2 = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred2,))
    assert reg1.registry_hash != reg2.registry_hash


def test_registry_hash_stable_across_predicate_order():
    """Order of predicates does not affect registry_hash (sorted by predicate_id)."""
    pred_a = EvidenceGraphPredicate(
        predicate_id="a.pred",
        claim_kind="fi.v1.A",
        required=(exists("span_verified"),),
    )
    pred_b = EvidenceGraphPredicate(
        predicate_id="b.pred",
        claim_kind="fi.v1.B",
        required=(exists("entailment_verified"),),
    )
    reg1 = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred_a, pred_b))
    reg2 = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred_b, pred_a))
    assert reg1.registry_hash == reg2.registry_hash


# ---------------------------------------------------------------------------
# Registry JSON round-trip
# ---------------------------------------------------------------------------


def test_registry_round_trips_through_json():
    pred = EvidenceGraphPredicate(
        predicate_id="fi.v1.INLINE_STATUTE_RESOLUTION.strict",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required=(exists("span_verified"), count_distinct_at_least("reviewed", "producer.producer_id", 2)),
        forbidden=(exists("retracted"),),
    )
    reg = EvidencePolicyRegistry.build("lawvm.fi.v1.evidence_policy", "v0", (pred,))
    d = registry_to_dict(reg)
    reg2 = registry_from_dict(d)
    assert reg2.registry_hash == reg.registry_hash
    assert len(reg2.predicates) == 1
    assert reg2.predicates[0].predicate_id == pred.predicate_id


def test_registry_from_dict_verifies_hash():
    pred = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    reg = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred,))
    d = registry_to_dict(reg)
    d["registry_hash"] = "badhash"
    with pytest.raises(ValueError, match="hash mismatch"):
        registry_from_dict(d)


def test_registry_verify_hash_raises_on_tamper():
    pred = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    reg = EvidencePolicyRegistry.build("lawvm.test", "v0", (pred,))
    tampered = EvidencePolicyRegistry(
        registry_id=reg.registry_id,
        registry_version=reg.registry_version,
        registry_hash="tampered_hash",
        predicates=reg.predicates,
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        tampered.verify_hash()


# ---------------------------------------------------------------------------
# IndependenceDimension + check_independence (required)
# ---------------------------------------------------------------------------


def _make_attestation(
    attestation_id: str,
    producer_id: str,
    producer_kind: str,
    parent_attestation_id: str = "",
) -> ProvenanceAttestation:
    from datetime import datetime, timezone
    return ProvenanceAttestation(
        attestation_id=attestation_id,
        attestation_kind="reviewed",
        subject=ArtifactRef(artifact_type="assertion", artifact_id="sub", content_hash="sub"),
        materials=(),
        producer=Producer(
            producer_id=producer_id,
            producer_kind=producer_kind,
            public_key=None,
            metadata={},
        ),
        produced_at=datetime.now(tz=timezone.utc),
        payload={"parent_attestation_id": parent_attestation_id},
    )


def test_structural_independence_distinguishes_shared_parent():
    """Two attestations sharing a parent attestation fail PARENT_ATTESTATION check."""
    a1 = _make_attestation("a1", "prod1", "human", parent_attestation_id="shared_parent")
    a2 = _make_attestation("a2", "prod2", "llm", parent_attestation_id="shared_parent")
    result = check_independence(
        (a1, a2),
        by=(IndependenceDimension.PARENT_ATTESTATION,),
    )
    assert result is False


def test_independence_passes_when_different_parents():
    a1 = _make_attestation("a1", "prod1", "human", parent_attestation_id="parent_a")
    a2 = _make_attestation("a2", "prod2", "llm", parent_attestation_id="parent_b")
    result = check_independence(
        (a1, a2),
        by=(IndependenceDimension.PARENT_ATTESTATION,),
    )
    assert result is True


def test_independence_fails_same_producer_id():
    a1 = _make_attestation("a1", "same_prod", "human")
    a2 = _make_attestation("a2", "same_prod", "llm")
    result = check_independence(
        (a1, a2),
        by=(IndependenceDimension.PRODUCER_ID,),
    )
    assert result is False


def test_independence_passes_different_producer_ids():
    a1 = _make_attestation("a1", "prod1", "human")
    a2 = _make_attestation("a2", "prod2", "llm")
    result = check_independence(
        (a1, a2),
        by=(IndependenceDimension.PRODUCER_ID,),
    )
    assert result is True


def test_independence_trivially_passes_single_attestation():
    a1 = _make_attestation("a1", "prod1", "human")
    result = check_independence((a1,), by=(IndependenceDimension.PRODUCER_KIND,))
    assert result is True


def test_independence_trivially_passes_empty():
    result = check_independence((), by=(IndependenceDimension.PRODUCER_KIND,))
    assert result is True


# ---------------------------------------------------------------------------
# Registry lookup helpers
# ---------------------------------------------------------------------------


def test_registry_get_predicate():
    pred = EvidenceGraphPredicate(
        predicate_id="fi.v1.INLINE_STATUTE_RESOLUTION.strict",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required=(),
    )
    reg = EvidencePolicyRegistry.build("lawvm.fi.v1.evidence_policy", "v0", (pred,))
    assert reg.get_predicate("fi.v1.INLINE_STATUTE_RESOLUTION.strict") is pred
    assert reg.get_predicate("nonexistent") is None


def test_registry_get_predicate_for_claim_kind():
    pred = EvidenceGraphPredicate(
        predicate_id="fi.v1.INLINE_STATUTE_RESOLUTION.strict",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required=(),
    )
    reg = EvidencePolicyRegistry.build("lawvm.fi.v1.evidence_policy", "v0", (pred,))
    assert reg.get_predicate_for_claim_kind("fi.v1.INLINE_STATUTE_RESOLUTION") is pred
    assert reg.get_predicate_for_claim_kind("fi.v1.OTHER") is None


# ---------------------------------------------------------------------------
# Shipped policy file round-trip
# ---------------------------------------------------------------------------


def test_shipped_policy_file_loads_and_verifies():
    import pathlib
    policy_path = pathlib.Path("data/fi/v1/evidence_policy/lawvm.fi.v1.evidence_policy.v0.json")
    if not policy_path.exists():
        pytest.skip("shipped policy file not present")
    d = json.loads(policy_path.read_text(encoding="utf-8"))
    reg = registry_from_dict(d)
    reg.verify_hash()
    assert len(reg.predicates) >= 1
    pred = reg.get_predicate("fi.v1.INLINE_STATUTE_RESOLUTION.strict")
    assert pred is not None
    assert pred.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION"
