"""Tests for EvidenceKernel: authorize() + AuthorizationResult.

Required by spec:
  test_authorization_deterministic_under_same_graph_profile_policy
  test_retraction_propagates_via_query_not_stored_taint
  test_negative_evidence_admissible_as_positive_attestation
  test_no_arbitrary_python_in_policy
  test_profile_tag_enum_deleted (via primitive.py deprecation)
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from typing import Any, cast

from lawvm.core.compile_result import StrictProfile
from lawvm.core.evidence_kernel import (
    Json,
    authorize,
    query_retraction_taint,
)
from lawvm.core.evidence_policy import (
    EvidenceGraphPredicate,
    PolicyExpr,
    exists,
)
from lawvm.core.provenance_graph import (
    ArtifactRef,
    GraphBuilder,
    GraphEdge,
    Interval,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    ProvenanceGraph,
    assertion_canonical_payload,
    attestation_canonical_payload,
    attestation_kind_registry_hash,
    _sha256,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reg_hash() -> str:
    return attestation_kind_registry_hash()


def _make_producer(producer_id: str = "test.prod", producer_kind: str = "human") -> Producer:
    return Producer(
        producer_id=producer_id,
        producer_kind=cast(Any, producer_kind),
        public_key=None,
        metadata={},
    )


def _make_assertion(
    kind: str = "fi.v1.TEST",
    jurisdiction: str = "fi",
) -> ProvenanceAssertion:
    now = datetime.now(tz=timezone.utc)
    temp = ProvenanceAssertion(
        assertion_id="__ph__",
        schema_version="v1",
        jurisdiction=jurisdiction,
        kind=kind,
        layer="extraction",
        scope={"statute_id": "1234/2024"},
        target={"ref": "chapter:1/section:1"},
        value={"resolution": "laki 123/2020"},
        source_refs=(),
        dependency_refs=(),
        valid_at=Interval(start=date(2024, 1, 1)),
    )
    canonical = assertion_canonical_payload(temp)
    assertion_id = _sha256(canonical)
    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v1",
        jurisdiction=jurisdiction,
        kind=kind,
        layer="extraction",
        scope={"statute_id": "1234/2024"},
        target={"ref": "chapter:1/section:1"},
        value={"resolution": "laki 123/2020"},
        source_refs=(),
        dependency_refs=(),
        valid_at=Interval(start=date(2024, 1, 1)),
    )


def _make_attestation_for(
    assertion: ProvenanceAssertion,
    kind: str,
    payload: dict | None = None,
    producer: Producer | None = None,
) -> ProvenanceAttestation:
    now = datetime.now(tz=timezone.utc)
    prod = producer or _make_producer()
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    temp = ProvenanceAttestation(
        attestation_id="__ph__",
        attestation_kind=kind,
        subject=subject,
        materials=(),
        producer=prod,
        produced_at=now,
        payload=payload or {},
    )
    canonical = attestation_canonical_payload(temp)
    attest_id = _sha256(canonical)
    return ProvenanceAttestation(
        attestation_id=attest_id,
        attestation_kind=kind,
        subject=subject,
        materials=(),
        producer=prod,
        produced_at=now,
        payload=payload or {},
    )


def _build_graph(
    assertions: list[ProvenanceAssertion],
    attestations: list[ProvenanceAttestation],
) -> ProvenanceGraph:
    builder = GraphBuilder(attestation_kind_registry_hash_val=_reg_hash())
    for a in assertions:
        builder.add_assertion(a)
    for a in attestations:
        builder.add_attestation(a)
    return builder.finalize()


def _make_indexes(
    assertions: list[ProvenanceAssertion],
    attestations: list[ProvenanceAttestation],
) -> tuple[dict, dict]:
    ai = {a.assertion_id: a for a in assertions}
    att_i = {a.attestation_id: a for a in attestations}
    return ai, att_i


# ---------------------------------------------------------------------------
# test_no_arbitrary_python_in_policy (required)
# ---------------------------------------------------------------------------


def test_no_arbitrary_python_in_policy():
    """PolicyExpr with unknown op raises ValueError at evaluation time."""
    assertion = _make_assertion()
    bad_policy = EvidenceGraphPredicate(
        predicate_id="test.bad",
        claim_kind="fi.v1.TEST",
        required=(PolicyExpr(op="__import__('os').system", args={}),),
    )
    graph = _build_graph([assertion], [])
    ai, att_i = _make_indexes([assertion], [])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    with pytest.raises(ValueError, match="unknown PolicyExpr op"):
        authorize(
            subject=subject,
            profile=profile,
            policy=bad_policy,
            graph=graph,
            assertion_index=ai,
            attestation_index=att_i,
            at=datetime.now(tz=timezone.utc),
        )


# ---------------------------------------------------------------------------
# test_authorization_deterministic_under_same_graph_profile_policy (required)
# ---------------------------------------------------------------------------


def test_authorization_deterministic_under_same_graph_profile_policy():
    """Same (graph, profile, policy, at) → identical AuthorizationResult."""
    assertion = _make_assertion()
    attest = _make_attestation_for(assertion, "span_verified")
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    graph = _build_graph([assertion], [attest])
    ai, att_i = _make_indexes([assertion], [attest])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    at = datetime.now(tz=timezone.utc)

    r1 = authorize(subject=subject, profile=profile, policy=policy, graph=graph,
                   assertion_index=ai, attestation_index=att_i, at=at)
    r2 = authorize(subject=subject, profile=profile, policy=policy, graph=graph,
                   assertion_index=ai, attestation_index=att_i, at=at)

    assert r1.authorized == r2.authorized
    assert r1.satisfied_clauses == r2.satisfied_clauses
    assert r1.evidence_bundle_hash == r2.evidence_bundle_hash


# ---------------------------------------------------------------------------
# Basic exists/none evaluation
# ---------------------------------------------------------------------------


def test_exists_satisfied_when_attestation_present():
    assertion = _make_assertion()
    attest = _make_attestation_for(assertion, "span_verified")
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    graph = _build_graph([assertion], [attest])
    ai, att_i = _make_indexes([assertion], [attest])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert result.authorized is True


def test_exists_unsatisfied_when_attestation_absent():
    assertion = _make_assertion()
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    graph = _build_graph([assertion], [])
    ai, att_i = _make_indexes([assertion], [])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert result.authorized is False
    assert len(result.unsatisfied_clauses) == 1


def test_forbidden_blocks_authorization():
    assertion = _make_assertion()
    attest_span = _make_attestation_for(assertion, "span_verified")
    attest_retracted = _make_attestation_for(assertion, "retracted")
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
        forbidden=(exists("retracted"),),
    )
    graph = _build_graph([assertion], [attest_span, attest_retracted])
    ai, att_i = _make_indexes([assertion], [attest_span, attest_retracted])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert result.authorized is False
    assert len(result.forbidden_present) == 1


# ---------------------------------------------------------------------------
# test_retraction_propagates_via_query_not_stored_taint (required)
# ---------------------------------------------------------------------------


def test_retraction_propagates_via_query_not_stored_taint():
    """Retracting an assertion makes authorization False; no stored taint in nodes."""
    assertion = _make_assertion()
    attest_span = _make_attestation_for(assertion, "span_verified")
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
        forbidden=(exists("retracted"),),
    )
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    profile = StrictProfile(name="test")

    # Before retraction: authorized
    graph_before = _build_graph([assertion], [attest_span])
    ai, att_i = _make_indexes([assertion], [attest_span])
    r1 = authorize(subject=subject, profile=profile, policy=policy,
                   graph=graph_before, assertion_index=ai, attestation_index=att_i,
                   at=datetime.now(tz=timezone.utc))
    assert r1.authorized is True

    # After retraction: unauthorized (retraction is an attestation, not a node mutation)
    attest_retracted = _make_attestation_for(assertion, "retracted", payload={"reason": "test"})
    graph_after = _build_graph([assertion], [attest_span, attest_retracted])

    # The assertion node payload_hash is UNCHANGED — no stored taint
    node_before = next(n for n in graph_before.nodes if n.node_id == assertion.assertion_id)
    node_after = next(n for n in graph_after.nodes if n.node_id == assertion.assertion_id)
    assert node_before.payload_hash == node_after.payload_hash  # no stored taint

    ai2, att_i2 = _make_indexes([assertion], [attest_span, attest_retracted])
    r2 = authorize(subject=subject, profile=profile, policy=policy,
                   graph=graph_after, assertion_index=ai2, attestation_index=att_i2,
                   at=datetime.now(tz=timezone.utc))
    assert r2.authorized is False
    assert "exists:retracted" in " ".join(r2.forbidden_present) or len(r2.forbidden_present) > 0


# ---------------------------------------------------------------------------
# test_negative_evidence_admissible_as_positive_attestation (required)
# ---------------------------------------------------------------------------


def test_negative_evidence_admissible_as_positive_attestation():
    """no_candidate_found attestation satisfies an exists(no_candidate_found) clause."""
    assertion = _make_assertion()
    attest_neg = _make_attestation_for(
        assertion,
        "no_candidate_found",
        payload={"search_scope": "fi.corpus.statutes", "bound": 1000},
    )
    policy = EvidenceGraphPredicate(
        predicate_id="test.negative",
        claim_kind="fi.v1.TEST",
        required=(exists("no_candidate_found"),),
    )
    graph = _build_graph([assertion], [attest_neg])
    ai, att_i = _make_indexes([assertion], [attest_neg])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert result.authorized is True


# ---------------------------------------------------------------------------
# query_retraction_taint
# ---------------------------------------------------------------------------


def test_retraction_taint_query_finds_retracted_assertions():
    assertion = _make_assertion()
    attest_retracted = _make_attestation_for(assertion, "retracted")

    builder = GraphBuilder(attestation_kind_registry_hash_val=_reg_hash())
    ref = builder.add_assertion(assertion)
    builder.add_attestation(attest_retracted)
    # Add consumed_by_build edge
    edge = GraphEdge(
        edge_id=_sha256(f"consumed:{assertion.assertion_id}:build1"),
        edge_type="consumed_by_build",
        src_node_id=assertion.assertion_id,
        dst_node_id="build1",
        payload={"build_id": "build1"},
    )
    builder.add_edge(edge)
    graph = builder.finalize()

    att_i = {attest_retracted.attestation_id: attest_retracted}
    findings = query_retraction_taint(graph, ("build1",), att_i)
    assert len(findings) == 1
    assert findings[0].build_id == "build1"
    assert findings[0].retracted_assertion_id == assertion.assertion_id


def test_retraction_taint_empty_when_no_retraction():
    assertion = _make_assertion()
    attest_span = _make_attestation_for(assertion, "span_verified")
    graph = _build_graph([assertion], [attest_span])
    att_i = {attest_span.attestation_id: attest_span}
    findings = query_retraction_taint(graph, ("build1",), att_i)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# AuthorizationResult fields
# ---------------------------------------------------------------------------


def test_authorization_result_has_evidence_bundle_hash():
    assertion = _make_assertion()
    attest = _make_attestation_for(assertion, "span_verified")
    policy = EvidenceGraphPredicate(
        predicate_id="test.p1",
        claim_kind="fi.v1.TEST",
        required=(exists("span_verified"),),
    )
    graph = _build_graph([assertion], [attest])
    ai, att_i = _make_indexes([assertion], [attest])
    profile = StrictProfile(name="test")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert isinstance(result.evidence_bundle_hash, str)
    assert len(result.evidence_bundle_hash) == 64  # sha256 hex


def test_authorization_result_policy_id_and_profile_name():
    assertion = _make_assertion()
    policy = EvidenceGraphPredicate(
        predicate_id="fi.v1.INLINE_STATUTE_RESOLUTION.strict",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required=(exists("span_verified"),),
    )
    graph = _build_graph([assertion], [])
    ai, att_i = _make_indexes([assertion], [])
    profile = StrictProfile(name="fi_strict")
    subject = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion.assertion_id,
        content_hash=assertion.assertion_id,
    )
    result = authorize(subject=subject, profile=profile, policy=policy,
                       graph=graph, assertion_index=ai, attestation_index=att_i,
                       at=datetime.now(tz=timezone.utc))
    assert result.policy_id == "fi.v1.INLINE_STATUTE_RESOLUTION.strict"
    assert result.profile_name == "fi_strict"


# ---------------------------------------------------------------------------
# test_profile_tag_enum_deleted (required)
# ---------------------------------------------------------------------------


def test_profile_tag_enum_deleted():
    """ProfileTag is no longer directly importable from core.manual_claims without warning."""
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from lawvm.core.manual_claims import primitive
        pt = primitive.ProfileTag
        assert len(w) >= 1
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
    # Verify it still works (returns the deprecated enum)
    assert pt.DETERMINISTIC_ONLY.value == "deterministic_only"


def test_profile_tag_not_in_new_init_direct_import():
    """ProfileTag should NOT be in the direct imports of manual_claims __init__."""
    import lawvm.core.manual_claims as mc
    # ProfileTag should not be directly exported
    assert "ProfileTag" not in mc.__all__


def test_internal_profile_tag_compat_imports_do_not_warn():
    """Legacy internals use the private transition enum, not the public warning alias."""
    import importlib
    import warnings

    module_names = (
        "lawvm.core.manual_claims.storage",
        "lawvm.tools.build_index_db",
        "lawvm.tools.cmd_propose_claims",
        "lawvm.tools.cmd_validate_claims",
        "lawvm.tools.export_fi_refs",
    )
    # ``importlib.reload`` re-executes each module in its EXISTING namespace,
    # replacing that module's class/enum objects in place. Any already-imported
    # test module that did ``from <mod> import X`` keeps the pre-reload ``X``
    # while the live module now holds a fresh, non-identical ``X`` — a
    # duplicate-class / enum-identity leak that fails unrelated tests co-located
    # on the same worker (e.g. tests/test_fi_export_parity.py's repealedBy
    # candidate identity checks, which reference export_fi_refs enums). This
    # check only needs to observe that reloading emits no DeprecationWarning, so
    # snapshot each module namespace and restore it afterwards, leaving no
    # duplicated objects behind for the rest of the process.
    snapshots: list[tuple[Any, dict[str, Any]]] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            for module_name in module_names:
                module = importlib.import_module(module_name)
                snapshots.append((module, dict(module.__dict__)))
                importlib.reload(module)
    finally:
        for module, snapshot in snapshots:
            module.__dict__.clear()
            module.__dict__.update(snapshot)


def test_json_alias_is_constrained_recursive_type_not_object() -> None:
    # The evaluator's JSON type alias must be a constrained structural alias,
    # not the old ``Json = object`` escape hatch that disabled checking.
    assert Json is not object
    # It resolves as a typing alias usable in annotations (recursive forward
    # ref over JSON scalars + containers).
    from typing import get_type_hints

    def _annotated(value: "Json") -> "Json":
        return value

    hints = get_type_hints(_annotated)
    rendered = str(hints["value"])
    for scalar in ("bool", "int", "float", "str", "list", "dict"):
        assert scalar in rendered
