"""Tests for transitive-closure reachability over the corpus surface graph.

Builds a tiny synthetic ``LegalSurfaceGraph`` directly (act-level work entities
+ ``reference_resolution`` nodes + ``refers_to`` / ``has_candidate`` edges) so
the A->B->C chain and the candidate-edge marking are fully controlled, then
asserts the closure semantics: forward reaches the cited closure with stable hop
distances, backward reaches the citers, ``max_hops`` cuts to a frontier,
candidate edges mark candidate-reachability, an unknown start fails loud, and two
runs are identical. A real-corpus smoke is guarded to skip when the archive is
absent (mirrors test_fi_corpus_graph).
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_assembler import (
    compute_payload_hash,
    mint_entity_node_id,
)
from lawvm.core.legal_surface_graph import (
    SCHEMA_TAG,
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceEdge,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.finland.legal_surface.closure import (
    ClosureError,
    backward_closure,
    forward_closure,
)

_A = "2003/314"
_B = "2022/711"
_C = "2010/100"
_X = "2099/999"  # outside-slice target (no node / no outbound refs)


def _work_entity(work_id: str) -> SurfaceNode:
    payload = {"work_id": work_id, "entity_kind": "legal_work_entity"}
    return SurfaceNode(
        node_id=mint_entity_node_id(work_id),
        node_kind="legal_work_entity",
        authority_role="entity_handle",
        jurisdiction="fi",
        source_ref=None,
        lens_id=None,
        rule_id="test.work_entity",
        status="present",
        payload_hash=compute_payload_hash(payload),
        payload=payload,
    )


def _resolution_node(node_id: str, living_in_work: str) -> SurfaceNode:
    """A reference_resolution node living in ``living_in_work`` (its citing work)."""
    payload = {"resolution_status": "resolved", "node": node_id}
    return SurfaceNode(
        node_id=node_id,
        node_kind="reference_resolution",
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=SourceSpanRef(
            source_unit_id=f"su:{living_in_work}",
            source_hash="deadbeef",
            work_id=living_in_work,
            address="1",
            char_start=0,
            char_end=1,
            text_hash="cafe",
        ),
        lens_id="test.lens",
        rule_id="test.resolution",
        status="resolved",
        payload_hash=compute_payload_hash(payload),
        payload=payload,
    )


def _edge(edge_id: str, kind: str, src: str, dst: str) -> SurfaceEdge:
    status = "asserted" if kind == "refers_to" else "candidate"
    payload = {"edge": edge_id}
    return SurfaceEdge(
        edge_id=edge_id,
        edge_kind=kind,
        src=src,
        dst=dst,
        rule_id=f"test.{kind}",
        status=status,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
    )


def _graph(nodes: list[SurfaceNode], edges: list[SurfaceEdge]) -> LegalSurfaceGraph:
    node_map = {n.node_id: n for n in nodes}
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id=None,
        scope={"kind": "corpus_slice", "statute_ids": (_A, _B, _C)},
        surface_time=None,
        source_bundle_hash="0" * 64,
        language="fi",
    )
    return LegalSurfaceGraph(
        schema=SCHEMA_TAG,
        graph_id="test-graph-id",
        subject=subject,
        source_units=(),
        lens_runs=(),
        nodes=node_map,
        edges=tuple(edges),
        build_diagnostics=(),
    )


def _chain_graph() -> LegalSurfaceGraph:
    """A -> B -> C: A cites B (asserted), B cites C (asserted). Act-level."""
    nodes = [
        _work_entity(_A),
        _work_entity(_B),
        _work_entity(_C),
        _resolution_node("res:A->B", _A),
        _resolution_node("res:B->C", _B),
    ]
    edges = [
        _edge("e1", "refers_to", "res:A->B", mint_entity_node_id(_B)),
        _edge("e2", "refers_to", "res:B->C", mint_entity_node_id(_C)),
    ]
    return _graph(nodes, edges)


# ── forward ───────────────────────────────────────────────────────────────────


def test_forward_closure_reaches_chain_with_hops() -> None:
    g = _chain_graph()
    res = forward_closure(g, mint_entity_node_id(_A))
    reached = dict(res.reached)
    assert reached == {
        mint_entity_node_id(_B): 1,
        mint_entity_node_id(_C): 2,
    }
    assert res.direction == "forward"
    # C is reached but has no outbound refs in the slice -> frontier.
    assert mint_entity_node_id(_C) in res.frontier
    assert mint_entity_node_id(_B) not in res.frontier


def test_forward_path_is_recorded_edge_by_edge() -> None:
    g = _chain_graph()
    res = forward_closure(g, mint_entity_node_id(_A))
    paths = dict(res.paths)
    path_to_c = paths[mint_entity_node_id(_C)]
    assert [s.edge_id for s in path_to_c] == ["e1", "e2"]
    assert [s.dst_entity for s in path_to_c] == [
        mint_entity_node_id(_B),
        mint_entity_node_id(_C),
    ]
    assert all(s.candidate is False for s in path_to_c)


def test_forward_max_hops_stops_at_frontier() -> None:
    g = _chain_graph()
    res = forward_closure(g, mint_entity_node_id(_A), max_hops=1)
    reached = dict(res.reached)
    # only B at hop 1; C is beyond the cap and never reached.
    assert reached == {mint_entity_node_id(_B): 1}
    assert mint_entity_node_id(_C) not in reached
    # B sits at the max_hops boundary -> frontier (reached, not expanded).
    assert mint_entity_node_id(_B) in res.frontier


# ── backward ──────────────────────────────────────────────────────────────────


def test_backward_closure_reaches_citers() -> None:
    g = _chain_graph()
    res = backward_closure(g, mint_entity_node_id(_C))
    reached = dict(res.reached)
    assert reached == {
        mint_entity_node_id(_B): 1,
        mint_entity_node_id(_A): 2,
    }
    assert res.direction == "backward"
    # A has no incoming citations in the slice -> frontier.
    assert mint_entity_node_id(_A) in res.frontier


# ── candidate reachability ────────────────────────────────────────────────────


def test_candidate_edge_marks_target_candidate_reachable() -> None:
    # A -> B asserted, B -> C via has_candidate (ambiguous).
    nodes = [
        _work_entity(_A),
        _work_entity(_B),
        _work_entity(_C),
        _resolution_node("res:A->B", _A),
        _resolution_node("res:B->C", _B),
    ]
    edges = [
        _edge("e1", "refers_to", "res:A->B", mint_entity_node_id(_B)),
        _edge("e2", "has_candidate", "res:B->C", mint_entity_node_id(_C)),
    ]
    g = _graph(nodes, edges)
    res = forward_closure(g, mint_entity_node_id(_A))
    # B reached via asserted only -> NOT candidate-reachable.
    assert mint_entity_node_id(_B) not in res.candidate_reachable
    # C reached only through the has_candidate hop -> candidate-reachable.
    assert mint_entity_node_id(_C) in res.candidate_reachable
    # resolution_quality makes the per-hop decay visible.
    rq = res.resolution_quality
    assert rq["asserted_edges"] == 1
    assert rq["candidate_edges"] == 1
    assert rq["by_hop"] == {
        1: {"asserted": 1, "candidate": 0},
        2: {"asserted": 0, "candidate": 1},
    }
    # the candidate hop is legible edge-by-edge on the path.
    path_to_c = dict(res.paths)[mint_entity_node_id(_C)]
    assert path_to_c[-1].candidate is True
    assert path_to_c[-1].edge_kind == "has_candidate"


# ── fail-loud + determinism ──────────────────────────────────────────────────


def test_start_not_in_graph_fails_loud() -> None:
    g = _chain_graph()
    with pytest.raises(ClosureError):
        forward_closure(g, mint_entity_node_id(_X))
    with pytest.raises(ClosureError):
        backward_closure(g, mint_entity_node_id(_X))


def test_negative_max_hops_fails_loud() -> None:
    g = _chain_graph()
    with pytest.raises(ClosureError):
        forward_closure(g, mint_entity_node_id(_A), max_hops=-1)


def test_deterministic_across_runs() -> None:
    g = _chain_graph()
    r1 = forward_closure(g, mint_entity_node_id(_A))
    r2 = forward_closure(g, mint_entity_node_id(_A))
    assert r1 == r2
    b1 = backward_closure(g, mint_entity_node_id(_C))
    b2 = backward_closure(g, mint_entity_node_id(_C))
    assert b1 == b2


# ── real-corpus smoke (skips when archive absent) ────────────────────────────


def _real_store_or_skip():
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        pytest.skip("LAWVM_CANONICAL_DATA_ROOT not set; real-corpus smoke skipped")
    archive = os.path.join(root, "data", "finlex.farchive")
    if not os.path.exists(archive):
        pytest.skip(f"farchive absent at {archive}; real-corpus smoke skipped")
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    return TransparentCorpusStore(Farchive(archive))


def test_real_corpus_forward_closure_smoke() -> None:
    store = _real_store_or_skip()
    from lawvm.finland.legal_surface.corpus_graph import build_corpus_surface_graph
    from lawvm.finland.references.registries import eu_nickname
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
        load_statute_name_registry,
    )

    artifact = default_artifact_path()
    if not artifact.exists():
        pytest.skip(f"statute-name registry artifact absent at {artifact}")
    statute_registry = load_statute_name_registry(artifact)

    ids = store.list_statute_ids()[:20]
    assert ids
    graph = build_corpus_surface_graph(
        ids, store, statute_registry=statute_registry, eu_registry=eu_nickname
    )
    # pick a citing entity that has at least one outbound refers_to edge.
    start = None
    for edge in graph.edges:
        if edge.edge_kind == "refers_to":
            src = graph.nodes.get(edge.src)
            if src is not None and src.source_ref is not None:
                citing_work = src.source_ref.work_id
                if citing_work:
                    start = mint_entity_node_id(citing_work)
                    break
    if start is None:
        pytest.skip("no resolved refers_to edge in the sampled corpus slice")
    res = forward_closure(graph, start)
    # honest reporting: frontier is a subset of reached entities.
    reached_ids = {ent for ent, _ in res.reached}
    assert set(res.frontier) <= reached_ids
    assert set(res.candidate_reachable) <= reached_ids
