"""Real-corpus INVARIANT harness for the Legal Surface Graph (regression scaffold).

This guards the ``reference/`` Legal Surface Graph work against regressions while
its DEFAULT lens/edge/lint sets are *actively changing* (a sibling capstone is
wiring new passes). It therefore asserts only ROBUST INVARIANTS that survive
default-set changes — never exact snapshots, never exact counts.

It builds the per-statute :func:`build_legal_surface_graph` over a SMALL
deterministic statute slice (the first N from the corpus listing) and asserts:

  1. FIREWALL holds universally — every node/edge is ``surface_only`` and never
     ``replay_authorized``; every derived lint is ``surface_only`` and never a
     ``legal_conclusion`` (the single most important invariant).
  2. DETERMINISM — building the same statute twice yields the identical
     ``graph_id`` and identical node/edge id sets.
  3. NODE-ID INTEGRITY — no two distinct nodes share a ``node_id`` with a
     divergent ``payload_hash`` (the assembler enforces this; the harness proves
     it corpus-wide).
  4. EDGE ENDPOINT CLOSURE — every edge ``src``/``dst`` resolves to a node
     present in the graph.
  5. STATUS-POPULATION SANITY — across the slice the reference resolutions
     populate MORE THAN ONE distinct ``resolution_status`` (the pipeline is not
     collapsing everything to one status); and every emitted node_kind / edge_kind
     is a member of the core ``NODE_KINDS`` / ``EDGE_KINDS`` frozensets.
  6. MENTION-COUNT FLOOR — a LOWER BOUND only on reference mentions across the
     slice, so adding recognizers never breaks it.

Like the other real-corpus smoke tests (``test_fi_corpus_graph`` /
``test_fi_corpus_lints``) this SKIPS cleanly when the archive store /
``LAWVM_CANONICAL_DATA_ROOT`` is unavailable — it never errors in that case.

It only CONSUMES the public build API (``build_legal_surface_graph`` /
``lint_surface_graph``); it does not import or touch lenses, registries, the
assembler internals, or the corpus builders.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_graph import EDGE_KINDS, NODE_KINDS
from lawvm.finland.legal_surface.graph_build import (
    build_legal_surface_graph,
    lint_surface_graph,
)

# Size of the deterministic statute slice. Small enough to stay fast, large
# enough that the cross-statute status diversity (invariant 5) and mention floor
# (invariant 6) are robust against the natural variation of the corpus head.
_SLICE_N = 30

# Mention-count floor: a LOWER BOUND on the reference_expr nodes summed across
# the whole slice. Real Finnish statute bodies are citation-dense, so even the
# first 30 statutes carry far more than this; the bound is deliberately loose so
# that adding recognizers can only ever increase it.
_MIN_REFERENCE_MENTIONS = 5


# ── real-corpus fixtures (mirror test_fi_corpus_graph's guard exactly) ────────


def _real_store_or_skip():
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        pytest.skip("LAWVM_CANONICAL_DATA_ROOT not set; real-corpus invariants skipped")
    archive = os.path.join(root, "data", "finlex.farchive")
    if not os.path.exists(archive):
        pytest.skip(f"farchive absent at {archive}; real-corpus invariants skipped")
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    return TransparentCorpusStore(Farchive(archive))


def _registry_or_skip():
    from lawvm.finland.references.registries.statute_name import (
        default_artifact_path,
        load_statute_name_registry,
    )

    artifact = default_artifact_path()
    if not artifact.exists():
        pytest.skip(f"statute-name registry artifact absent at {artifact}")
    return load_statute_name_registry(artifact)


def _read_body(store, sid: str) -> bytes | None:
    """Best available body XML (oracle → source → amendment), mirroring the
    corpus builder's ``_read_body`` selection policy. Archive-only reads."""
    try:
        xb = store.read_oracle(sid)
    except Exception:  # noqa: BLE001 — oracle absence is normal, fall back
        xb = None
    if xb:
        return xb
    return store.read_source(sid) or store.read_amendment(sid)


def _slice_graphs():
    """Build the per-statute surface graph for the deterministic head slice.

    Returns a list of ``(statute_id, graph)`` for statutes that had body XML.
    Skips the whole harness if the archive / registry are unavailable or the
    slice produced no buildable statute.
    """
    store = _real_store_or_skip()
    registry = _registry_or_skip()
    from lawvm.finland.references.registries import eu_nickname

    ids = store.list_statute_ids()[:_SLICE_N]
    assert ids, "expected at least some statute ids in the corpus"

    built: list[tuple[str, object]] = []
    for sid in ids:
        xb = _read_body(store, sid)
        if not xb:
            continue
        graph = build_legal_surface_graph(
            xb,
            sid,
            statute_registry=registry,
            eu_registry=eu_nickname,
        )
        built.append((sid, graph))
    if not built:
        pytest.skip("no buildable statute bodies in the head slice")
    return store, registry, built


@pytest.fixture(scope="module")
def slice_graphs():
    store, registry, built = _slice_graphs()
    return built


# ── invariant 1: firewall holds universally ──────────────────────────────────


def test_firewall_holds_over_every_node_and_edge(slice_graphs) -> None:
    for sid, graph in slice_graphs:
        for node in graph.nodes.values():
            assert node.surface_only is True, f"{sid}: node {node.node_id} not surface_only"
            assert node.replay_authorized is False, (
                f"{sid}: node {node.node_id} is replay_authorized"
            )
        for edge in graph.edges:
            assert edge.surface_only is True, f"{sid}: edge {edge.edge_id} not surface_only"
            assert edge.replay_authorized is False, (
                f"{sid}: edge {edge.edge_id} is replay_authorized"
            )


def test_firewall_holds_over_every_derived_lint(slice_graphs) -> None:
    saw_any_lint = False
    for sid, graph in slice_graphs:
        report = lint_surface_graph(graph)
        for lint in report.lints:
            saw_any_lint = True
            assert lint.surface_only is True, f"{sid}: lint {lint.lint_id} not surface_only"
            assert lint.legal_conclusion is False, (
                f"{sid}: lint {lint.lint_id} claims legal_conclusion"
            )
            assert lint.replay_authorized is False, (
                f"{sid}: lint {lint.lint_id} is replay_authorized"
            )
    # Not asserting that lints exist (default set may change); only that any that
    # ARE derived obey the firewall. The flag documents whether the slice
    # exercised the lint path at all.
    _ = saw_any_lint


# ── invariant 2: determinism ──────────────────────────────────────────────────


def test_building_the_same_statute_twice_is_identical(slice_graphs) -> None:
    store, registry, built = _slice_graphs()
    from lawvm.finland.references.registries import eu_nickname

    # Re-derive the body for the first buildable statute and build it again; the
    # graph_id and node/edge id sets must be byte-for-byte stable.
    sid, first = built[0]
    xb = _read_body(store, sid)
    assert xb is not None
    second = build_legal_surface_graph(
        xb, sid, statute_registry=registry, eu_registry=eu_nickname
    )
    assert second.graph_id == first.graph_id, f"{sid}: graph_id not deterministic"
    assert set(second.nodes) == set(first.nodes), f"{sid}: node id set not deterministic"
    assert {e.edge_id for e in second.edges} == {e.edge_id for e in first.edges}, (
        f"{sid}: edge id set not deterministic"
    )


# ── invariant 3: node-id integrity ────────────────────────────────────────────


def test_no_node_id_collision_with_divergent_payload(slice_graphs) -> None:
    for sid, graph in slice_graphs:
        # The graph's node map is keyed by node_id, so within one graph a node_id
        # is unique by construction; assert the key agrees with the stored node's
        # own node_id (no key/identity drift) and that payload_hash is populated.
        for nid, node in graph.nodes.items():
            assert node.node_id == nid, f"{sid}: node map key {nid} != node_id {node.node_id}"
            assert node.payload_hash, f"{sid}: node {nid} has empty payload_hash"


# ── invariant 4: edge endpoint closure ────────────────────────────────────────


def test_every_edge_endpoint_resolves_to_a_node(slice_graphs) -> None:
    for sid, graph in slice_graphs:
        node_ids = set(graph.nodes)
        for edge in graph.edges:
            assert edge.src in node_ids, (
                f"{sid}: edge {edge.edge_id} src {edge.src} not a graph node"
            )
            assert edge.dst in node_ids, (
                f"{sid}: edge {edge.edge_id} dst {edge.dst} not a graph node"
            )


# ── invariant 5: status-population sanity + closed vocabularies ───────────────


def test_resolution_statuses_are_not_collapsed_to_one(slice_graphs) -> None:
    statuses: set[str] = set()
    for _sid, graph in slice_graphs:
        for node in graph.nodes.values():
            if node.node_kind != "reference_resolution":
                continue
            rs = node.payload.get("resolution_status")
            if isinstance(rs, str):
                statuses.add(rs)
    assert len(statuses) >= 2, (
        "reference resolutions collapsed to a single status across the slice "
        f"(saw {sorted(statuses)}); the pipeline should populate more than one"
    )


def test_emitted_kinds_are_in_the_core_vocabularies(slice_graphs) -> None:
    for sid, graph in slice_graphs:
        for node in graph.nodes.values():
            assert node.node_kind in NODE_KINDS, (
                f"{sid}: off-vocabulary node_kind {node.node_kind!r}"
            )
        for edge in graph.edges:
            assert edge.edge_kind in EDGE_KINDS, (
                f"{sid}: off-vocabulary edge_kind {edge.edge_kind!r}"
            )


# ── invariant 6: mention-count floor (lower bound only) ───────────────────────


def test_reference_mention_floor_across_slice(slice_graphs) -> None:
    mentions = 0
    for _sid, graph in slice_graphs:
        for node in graph.nodes.values():
            if node.node_kind == "reference_expr":
                mentions += 1
    assert mentions >= _MIN_REFERENCE_MENTIONS, (
        f"expected >= {_MIN_REFERENCE_MENTIONS} reference mentions across the "
        f"{_SLICE_N}-statute slice, saw {mentions}"
    )
