"""Phase-0 gate for the Legal Surface Graph core skeleton.

Covers the test list from ``notes_internal/pro_on_fi_theory_grammar5.txt``
Phase 0:
  (1) node_id stable under rerun
  (2) payload_hash changes when payload changes
  (3) edge endpoint validation (edge to a non-existent node -> error)
  (4) no replay_authorized surface nodes (assembler refuses)
  (5) deterministic graph_id under permuted lens output
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_assembler import (
    AuthorityFirewallError,
    SurfaceAssemblyError,
    _enforce_edge_firewall,
    _enforce_node_firewall,
    assemble_surface_graph,
    compute_payload_hash,
)
from lawvm.core.legal_surface_graph import (
    SCHEMA_TAG,
    SourceSpanRef,
    SourceUnitRef,
    SurfaceEdge,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import (
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
)

# ── Fixtures / builders ──────────────────────────────────────────────────────


def _subject() -> SurfaceGraphSubject:
    return SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="fi:act:301/2004",
        scope={"kind": "whole_work"},
        surface_time="2026-01-01",
        source_bundle_hash="bundlehash0",
        language="fi",
    )


def _span(start: int = 10, end: int = 20) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id="su1",
        source_hash="srchash0",
        work_id="fi:act:301/2004",
        address="section:5",
        char_start=start,
        char_end=end,
        text_hash="texthash0",
    )


def _ref_expr_seed(payload: dict[str, object] | None = None) -> SurfaceNodeSeed:
    return SurfaceNodeSeed(
        node_kind="reference_expr",
        source_ref=_span(),
        local_discriminator="ref:laki 301/2004:citation",
        rule_id="fi.references.v0",
        node_status="resolved",
        payload=payload if payload is not None else {"surface_text": "lain 301/2004 5 §"},
    )


def _work_entity_seed() -> SurfaceNodeSeed:
    return SurfaceNodeSeed(
        node_kind="legal_work_entity",
        source_ref=None,
        local_discriminator="work:fi:act:301/2004",
        rule_id="fi.entity.v0",
        node_status="present",
        payload={"work_id": "fi:act:301/2004"},
        authority_role="entity_handle",
    )


def _lens_result(
    node_seeds: tuple[SurfaceNodeSeed, ...],
    edge_seeds: tuple[SurfaceEdgeSeed, ...] = (),
    lens_id: str = "fi.references.v0",
) -> SurfaceLensResult:
    return SurfaceLensResult(
        lens_id=lens_id,
        node_seeds=node_seeds,
        edge_seeds=edge_seeds,
        residuals=(),
        diagnostics=(),
        coverage={},
    )


def _source_units() -> tuple[SourceUnitRef, ...]:
    return (
        SourceUnitRef(
            source_unit_id="su1",
            work_id="fi:act:301/2004",
            address="section:5",
            source_hash="srchash0",
        ),
    )


def _assemble(lens_results: tuple[SurfaceLensResult, ...]):
    return assemble_surface_graph(
        subject=_subject(),
        source_units=_source_units(),
        lens_results=lens_results,
    )


# ── (1) node_id stable under rerun ───────────────────────────────────────────


def test_node_id_stable_under_rerun() -> None:
    g1 = _assemble((_lens_result((_ref_expr_seed(),)),))
    g2 = _assemble((_lens_result((_ref_expr_seed(),)),))
    assert set(g1.nodes) == set(g2.nodes)
    assert len(g1.nodes) == 1
    [nid] = list(g1.nodes)
    assert g1.nodes[nid].node_id == g2.nodes[nid].node_id


def test_node_id_unchanged_when_only_payload_changes() -> None:
    # The stable node_id must NOT change when payload details improve.
    g1 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v1"}),)),))
    g2 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v2 improved"}),)),))
    assert set(g1.nodes) == set(g2.nodes)


def test_entity_node_id_is_canonical() -> None:
    g = _assemble((_lens_result((_work_entity_seed(),)),))
    assert "entity:work:fi:act:301/2004" in g.nodes


# ── (2) payload_hash changes when payload changes ────────────────────────────


def test_payload_hash_changes_when_payload_changes() -> None:
    g1 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v1"}),)),))
    g2 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v2"}),)),))
    [nid] = list(g1.nodes)
    assert nid in g2.nodes  # same stable id
    assert g1.nodes[nid].payload_hash != g2.nodes[nid].payload_hash


def test_payload_hash_stable_for_identical_payload() -> None:
    assert compute_payload_hash({"a": 1, "b": 2}) == compute_payload_hash({"b": 2, "a": 1})


# ── (3) edge endpoint validation ─────────────────────────────────────────────


def test_edge_to_nonexistent_node_errors() -> None:
    expr = _ref_expr_seed()
    bad_edge = SurfaceEdgeSeed(
        edge_kind="refers_to",
        src_local=expr.local_discriminator,
        dst_local="ghost:does-not-exist",
        rule_id="fi.references.v0",
        surface_edge_status="asserted",
        payload={},
    )
    with pytest.raises(SurfaceAssemblyError, match="does not resolve to any node"):
        _assemble((_lens_result((expr,), (bad_edge,)),))


def test_edge_between_existing_nodes_resolves() -> None:
    expr = _ref_expr_seed()
    work = _work_entity_seed()
    edge = SurfaceEdgeSeed(
        edge_kind="refers_to",
        src_local=expr.local_discriminator,
        dst_local=work.local_discriminator,
        rule_id="fi.references.v0",
        surface_edge_status="asserted",
        payload={},
    )
    g = _assemble((_lens_result((expr, work), (edge,)),))
    assert len(g.edges) == 1
    [e] = g.edges
    assert e.src in g.nodes
    assert e.dst in g.nodes


# ── (4) no replay_authorized surface nodes (assembler refuses) ───────────────


def test_assembler_refuses_replay_authorized_node() -> None:
    bad_node = SurfaceNode(
        node_id="n1",
        node_kind="reference_expr",
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=_span(),
        lens_id="fi.references.v0",
        rule_id="fi.references.v0",
        node_status="resolved",
        payload_hash="ph",
        payload={},
        replay_authorized=True,
    )
    with pytest.raises(AuthorityFirewallError, match="replay_authorized=True"):
        _enforce_node_firewall(bad_node)


def test_assembler_refuses_non_surface_only_node() -> None:
    bad_node = SurfaceNode(
        node_id="n1",
        node_kind="reference_expr",
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=_span(),
        lens_id="fi.references.v0",
        rule_id="fi.references.v0",
        node_status="resolved",
        payload_hash="ph",
        payload={},
        surface_only=False,
    )
    with pytest.raises(AuthorityFirewallError, match="surface_only=False"):
        _enforce_node_firewall(bad_node)


def test_assembler_refuses_replay_authorized_edge() -> None:
    bad_edge = SurfaceEdge(
        edge_id="e1",
        edge_kind="refers_to",
        src="n1",
        dst="n2",
        rule_id="r",
        surface_edge_status="asserted",
        payload_hash="ph",
        payload={},
        replay_authorized=True,
    )
    with pytest.raises(AuthorityFirewallError, match="replay_authorized=True"):
        _enforce_edge_firewall(bad_edge)


def test_default_nodes_are_firewall_safe() -> None:
    g = _assemble((_lens_result((_ref_expr_seed(),)),))
    for node in g.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False


# ── (5) deterministic graph_id under permuted lens output ────────────────────


def test_graph_id_invariant_under_permuted_lens_output() -> None:
    a = _lens_result((_ref_expr_seed(),), lens_id="fi.references.v0")
    b = _lens_result((_work_entity_seed(),), lens_id="fi.entities.v0")
    g_ab = _assemble((a, b))
    g_ba = _assemble((b, a))
    assert g_ab.graph_id == g_ba.graph_id
    assert g_ab.schema == SCHEMA_TAG


def test_graph_id_changes_when_payload_changes() -> None:
    g1 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v1"}),)),))
    g2 = _assemble((_lens_result((_ref_expr_seed({"surface_text": "v2"}),)),))
    assert g1.graph_id != g2.graph_id


def test_graph_id_changes_when_node_set_changes() -> None:
    g1 = _assemble((_lens_result((_ref_expr_seed(),)),))
    g2 = _assemble((_lens_result((_ref_expr_seed(), _work_entity_seed())),))
    assert g1.graph_id != g2.graph_id


# ── Fail-loud seed validation ────────────────────────────────────────────────


def test_unknown_node_kind_rejected() -> None:
    bad = SurfaceNodeSeed(
        node_kind="not_a_real_kind",
        source_ref=_span(),
        local_discriminator="x",
        rule_id="r",
        node_status="resolved",
        payload={},
    )
    with pytest.raises(SurfaceAssemblyError, match="unknown node_kind"):
        _assemble((_lens_result((bad,)),))


def test_unknown_status_rejected() -> None:
    bad = SurfaceNodeSeed(
        node_kind="reference_expr",
        source_ref=_span(),
        local_discriminator="x",
        rule_id="r",
        node_status="totally_invalid_status",
        payload={},
    )
    with pytest.raises(SurfaceAssemblyError, match="unknown node status"):
        _assemble((_lens_result((bad,)),))


def test_source_fact_seed_requires_source_ref() -> None:
    bad = SurfaceNodeSeed(
        node_kind="reference_expr",
        source_ref=None,
        local_discriminator="x",
        rule_id="r",
        node_status="resolved",
        payload={},
    )
    with pytest.raises(SurfaceAssemblyError, match="requires a source_ref"):
        _assemble((_lens_result((bad,)),))


def test_node_id_collision_with_divergent_payload_rejected() -> None:
    # Two seeds with identical identity tuple but different payloads -> error.
    s1 = _ref_expr_seed({"surface_text": "a"})
    s2 = _ref_expr_seed({"surface_text": "b"})
    with pytest.raises(SurfaceAssemblyError, match="divergent payload"):
        _assemble((_lens_result((s1, s2)),))


def test_identical_duplicate_seed_dedups() -> None:
    s1 = _ref_expr_seed({"surface_text": "same"})
    s2 = _ref_expr_seed({"surface_text": "same"})
    g = _assemble((_lens_result((s1, s2)),))
    assert len(g.nodes) == 1
