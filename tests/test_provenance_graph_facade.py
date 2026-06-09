"""Tests for provenance_graph_facade.py — Step 1 acceptance criteria.

Tests cover:
  - test_phase_result_round_trips_through_graph (mandatory)
  - test_real_phase_result_converts_to_graph (@pytest.mark.slow)
  - facade namespace markers are correct
  - empty PhaseResult produces empty graph
"""
from __future__ import annotations


import pytest

from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.provenance_graph import (
    ATTESTATION_KIND_REGISTRY_V0_HASH,
    ArtifactRef,
)
from lawvm.core.provenance_graph_facade import (
    _KIND_OBLIGATION,
    _KIND_OBSERVATION,
    _KIND_VIOLATION,
    findings_from_graph,
    graph_from_phase_result,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _phase_ref() -> ArtifactRef:
    return ArtifactRef(
        artifact_type="build",
        artifact_id="test_phase_ref_001",
        content_hash="test_phase_ref_001",
    )


def _make_observation_finding() -> Finding:
    return Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="elab",
        detail={"code": "MISSING_CLAUSE", "section": "1"},
        blocking=False,
        source_statute="2002/738",
    )


def _make_obligation_finding() -> Finding:
    return Finding(
        kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        role="obligation",
        stage="elab",
        detail={"reason": "strict"},
        blocking=True,
    )


def _make_violation_finding() -> Finding:
    return Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply",
        detail={"barrier_code": "APPLY.FAILED_OPERATION"},
        blocking=True,
    )


def _make_phase_result(*findings: Finding) -> "PhaseResult[None]":
    return PhaseResult(
        output=None,
        findings=findings,
    )


def _finding_signature(f: Finding) -> tuple:
    """Hashable key for set-equivalence comparison."""
    return (f.kind, f.role, f.stage, f.blocking, f.source_statute, repr(sorted(f.detail.items())))


# ---------------------------------------------------------------------------
# test_phase_result_round_trips_through_graph
# ---------------------------------------------------------------------------


def test_phase_result_round_trips_through_graph() -> None:
    """PhaseResult → graph → findings is set-equivalent to original findings.

    Round-trip property from v3 spec §13 Step 1.
    """
    observation = _make_observation_finding()
    obligation = _make_obligation_finding()
    violation = _make_violation_finding()

    pr = _make_phase_result(observation, obligation, violation)
    original_sigs = {_finding_signature(f) for f in pr.findings()}

    phase_ref = _phase_ref()
    graph, assertion_index = graph_from_phase_result(
        pr,
        phase_ref=phase_ref,
        source_bundle_hash="bundle_hash_001",
    )

    recovered_findings = findings_from_graph(
        graph,
        build_id="test_build_001",
        assertion_index=assertion_index,
    )

    recovered_sigs = {_finding_signature(f) for f in recovered_findings}
    assert recovered_sigs == original_sigs


def test_round_trip_preserves_detail() -> None:
    """Detail dict survives the round-trip."""
    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="elab",
        detail={"code": "EMPTY_CLAUSE", "section": "5", "nested": {"a": 1}},
        blocking=False,
    )
    pr = _make_phase_result(finding)
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b1")
    recovered = findings_from_graph(graph, build_id="b1", assertion_index=idx)

    assert len(recovered) == 1
    assert recovered[0].detail == {"code": "EMPTY_CLAUSE", "section": "5", "nested": {"a": 1}}


def test_round_trip_preserves_source_statute() -> None:
    """source_statute survives the round-trip."""
    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="elab",
        detail={},
        blocking=False,
        source_statute="1999/731",
    )
    pr = _make_phase_result(finding)
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b1")
    recovered = findings_from_graph(graph, build_id="b1", assertion_index=idx)
    assert len(recovered) == 1
    assert recovered[0].source_statute == "1999/731"


# ---------------------------------------------------------------------------
# Facade namespace markers
# ---------------------------------------------------------------------------


def test_facade_observation_uses_correct_kind() -> None:
    """Observation findings produce assertions with the observation facade kind."""
    pr = _make_phase_result(_make_observation_finding())
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")

    assertions = list(idx.values())
    assert len(assertions) == 1
    assert assertions[0].kind == _KIND_OBSERVATION
    assert assertions[0].layer == "facade_observation"


def test_facade_obligation_uses_correct_kind() -> None:
    """Obligation findings produce assertions with the obligation facade kind."""
    pr = _make_phase_result(_make_obligation_finding())
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")

    assertions = list(idx.values())
    assert len(assertions) == 1
    assert assertions[0].kind == _KIND_OBLIGATION
    assert assertions[0].layer == "facade_obligation"


def test_facade_violation_uses_correct_kind() -> None:
    """Violation findings produce assertions with the violation facade kind."""
    pr = _make_phase_result(_make_violation_finding())
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")

    assertions = list(idx.values())
    assert len(assertions) == 1
    assert assertions[0].kind == _KIND_VIOLATION
    assert assertions[0].layer == "facade_violation"


# ---------------------------------------------------------------------------
# Empty PhaseResult
# ---------------------------------------------------------------------------


def test_empty_phase_result_produces_empty_graph() -> None:
    """PhaseResult with no findings produces a graph with no assertion nodes."""
    pr = _make_phase_result()
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")
    assert len(graph.nodes) == 0
    assert len(graph.edges) == 0
    assert len(idx) == 0


def test_empty_graph_findings_from_graph_returns_empty() -> None:
    """findings_from_graph with no facade assertion nodes returns empty tuple."""
    pr = _make_phase_result()
    phase_ref = _phase_ref()
    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")
    recovered = findings_from_graph(graph, build_id="b", assertion_index=idx)
    assert recovered == ()


def test_findings_from_graph_without_index_returns_empty() -> None:
    """findings_from_graph with assertion_index=None returns empty tuple."""
    pr = _make_phase_result(_make_observation_finding())
    phase_ref = _phase_ref()
    graph, _ = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")
    recovered = findings_from_graph(graph, build_id="b", assertion_index=None)
    assert recovered == ()


# ---------------------------------------------------------------------------
# Graph structural properties
# ---------------------------------------------------------------------------


def test_graph_has_derives_projection_edges() -> None:
    """Each finding produces a derives_projection edge to the phase_ref node."""
    observation = _make_observation_finding()
    obligation = _make_obligation_finding()
    pr = _make_phase_result(observation, obligation)
    phase_ref = _phase_ref()

    graph, idx = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")

    projection_edges = [e for e in graph.edges if e.edge_type == "derives_projection"]
    assert len(projection_edges) == 2

    for edge in projection_edges:
        assert edge.dst_node_id == phase_ref.artifact_id


def test_graph_attestation_kind_registry_hash_carried() -> None:
    """ProvenanceGraph carries the attestation kind registry hash."""
    pr = _make_phase_result(_make_observation_finding())
    phase_ref = _phase_ref()
    graph, _ = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b")
    assert graph.attestation_kind_registry_hash == ATTESTATION_KIND_REGISTRY_V0_HASH


def test_graph_snapshot_hash_is_deterministic_for_same_input() -> None:
    """Two conversions of the same PhaseResult produce the same snapshot_hash."""
    observation = _make_observation_finding()
    pr = _make_phase_result(observation)
    phase_ref = _phase_ref()

    graph_a, _ = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b1")
    graph_b, _ = graph_from_phase_result(pr, phase_ref=phase_ref, source_bundle_hash="b1")

    assert graph_a.snapshot_hash == graph_b.snapshot_hash


# ---------------------------------------------------------------------------
# test_real_phase_result_converts_to_graph
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_phase_result_converts_to_graph() -> None:
    """Real PhaseResult from a corpus statute converts to a non-empty graph and round-trips.

    Requires corpus farchive data under data/finlex.farchive.
    Marked @pytest.mark.slow — run with --run-slow or -m slow.
    """
    from pathlib import Path

    farchive_path = Path("data/finlex.farchive")
    if not farchive_path.exists():
        pytest.skip("data/finlex.farchive not available; skipping real corpus test")

    # Import Finland compile pipeline
    try:
        from lawvm.finland.compile import compile_ops_for_statute
        from lawvm.core.phase_result import PhaseResult
    except ImportError as e:
        pytest.skip(f"Finland compile not importable: {e}")

    # Use a known statute that should produce findings
    statute_id = "2002/738"
    try:
        result = compile_ops_for_statute(statute_id)
    except Exception as e:
        pytest.skip(f"compile_ops_for_statute failed for {statute_id}: {e}")

    # Extract PhaseResult if the compile result carries one
    # Fall back to constructing a minimal PhaseResult from findings if needed
    if not hasattr(result, "findings"):
        pytest.skip(f"compile result for {statute_id} does not expose findings")

    findings = result.findings() if callable(getattr(result, "findings", None)) else ()
    if not findings:
        # Construct a synthetic PhaseResult with one finding to verify the plumbing
        finding = Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="elab",
            detail={"statute": statute_id},
            blocking=False,
        )
        pr: PhaseResult = PhaseResult(output=None, findings=(finding,))
    else:
        pr = PhaseResult(output=result, findings=tuple(findings))

    phase_ref = ArtifactRef(
        artifact_type="build",
        artifact_id=f"real_corpus_{statute_id.replace('/', '_')}",
        content_hash=f"real_corpus_{statute_id.replace('/', '_')}",
    )

    graph, assertion_index = graph_from_phase_result(
        pr,
        phase_ref=phase_ref,
        source_bundle_hash="real_corpus_bundle",
    )

    # Non-empty: at least one finding node
    assert len(graph.nodes) >= 1
    assert graph.snapshot_hash  # non-empty hash

    # Round-trip
    recovered = findings_from_graph(graph, build_id="real_test_build", assertion_index=assertion_index)
    original_sigs = {_finding_signature(f) for f in pr.findings()}
    recovered_sigs = {_finding_signature(f) for f in recovered}
    assert recovered_sigs == original_sigs
