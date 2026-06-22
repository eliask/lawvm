"""Tests for the SURF-01 / SURF-02 / SURF-07 surface-totality sweeps.

Two layers (mirroring ``test_fi_token_partition_coverage.py``):

* Structure tests (corpus-free, always run): the token-realization gap sweep
  (SURF-01) and its waist-edge twin (SURF-02) fire on an under-summed partition
  and stay silent on a balanced one; the orphan-entity sweep (SURF-07) isolates
  an uncovered entity handle and stays silent when every entity is covered.

* Corpus smoke (archive-gated): build a real statute's forests + LegalSurfaceGraph
  and REPORT the residual populations each sweep surfaces — a non-empty residual
  is the expected, correct tag-don't-guess outcome.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SurfaceEdge,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.finland.legal_surface.source_syntax_graph import (
    SourceSyntaxGraph,
    SyntaxCoverage,
)
from lawvm.finland.legal_surface.token_partition_coverage import (
    build_token_partition_coverage,
)
from lawvm.finland.legal_surface.surface_token_totality import (
    SURFACE_ORPHAN_ENTITY_NODE,
    SURFACE_TOKEN_REALIZATION_GAP,
    WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN,
    OrphanEntityNodeFinding,
    TokenRealizationFinding,
    assert_handoff_parity,
    sweep_orphan_entity_nodes,
    sweep_token_realization,
)


def _subject() -> SurfaceGraphSubject:
    return SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="test/1/2024",
        scope={},
        surface_time=None,
        source_bundle_hash="deadbeef",
        language="fi",
    )


def _cert(*, total: int, owned: int, benign: int = 0, residual: int = 0, silent: int = 0):
    cov = SyntaxCoverage(
        total_tokens=total,
        owned_tokens=owned,
        benign_tokens=benign,
        residual_tokens=residual,
        silent_tokens=silent,
    )
    forest = SourceSyntaxGraph(
        graph_id="forest-1",
        subject=_subject(),
        source_units=(),
        text_hash="h",
        text_len=200,
        syntax_nodes={},
        syntax_edges=(),
        parse_status="parsed",
        residuals=(),
        coverage=cov,
    )
    return build_token_partition_coverage(forest, statute_id="1/2024")


# ---------------------------------------------------------------------------
# SURF-01 — token-realization totality
# ---------------------------------------------------------------------------


def test_token_realization_clean_when_partition_total() -> None:
    cert = _cert(total=10, owned=6, benign=2, residual=1, silent=1)
    assert cert.is_partition()
    assert sweep_token_realization(cert) == ()


def test_token_realization_fires_on_undersummed_partition() -> None:
    # buckets sum to 6, total is 10 -> 4-token gap (dropped)
    cert = _cert(total=10, owned=6)
    findings = sweep_token_realization(cert)
    assert [f.code for f in findings] == [SURFACE_TOKEN_REALIZATION_GAP]
    f = findings[0]
    assert isinstance(f, TokenRealizationFinding)
    assert f.total_tokens == 10
    assert f.accounted == 6
    assert f.gap == 4
    # self-evidencing: the gap magnitude + per-bucket counts are in the detail
    assert "gap of 4" in f.detail
    assert "owned=6" in f.detail
    assert "dropped" in f.detail


def test_token_realization_detail_marks_double_count_on_negative_gap() -> None:
    # buckets sum to 12 but total is 10 -> double-counted (gap < 0)
    cert = _cert(total=10, owned=12)
    findings = sweep_token_realization(cert)
    assert findings[0].gap == -2
    assert "double-counted" in findings[0].detail


# ---------------------------------------------------------------------------
# SURF-02 — handoff parity source->token (subsumes into SURF-01)
# ---------------------------------------------------------------------------


def test_handoff_parity_clean_when_balanced() -> None:
    assert assert_handoff_parity(_cert(total=10, owned=10)) == ()


def test_handoff_parity_fires_under_waist_edge_code() -> None:
    findings = assert_handoff_parity(_cert(total=10, owned=6))
    assert [f.code for f in findings] == [WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN]
    assert findings[0].gap == 4


def test_handoff_parity_is_the_same_totality_as_token_realization() -> None:
    # SURF-02 subsumes into SURF-01: same gap, different finding NAME.
    cert = _cert(total=20, owned=11, benign=2)
    surf01 = sweep_token_realization(cert)
    surf02 = assert_handoff_parity(cert)
    assert len(surf01) == len(surf02) == 1
    assert surf01[0].gap == surf02[0].gap
    assert surf01[0].code == SURFACE_TOKEN_REALIZATION_GAP
    assert surf02[0].code == WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN


# ---------------------------------------------------------------------------
# SURF-07 — entity-handle totality
# ---------------------------------------------------------------------------


def _entity(node_id: str, *, kind: str = "term_symbol_entity", term: str = "x") -> SurfaceNode:
    return SurfaceNode(
        node_id=node_id,
        node_kind=kind,
        authority_role="entity_handle",
        jurisdiction="fi",
        source_ref=None,
        lens_id="lens.def",
        rule_id="r",
        status="asserted",
        payload_hash="p",
        payload={"term": term},
    )


def _surface_fact(node_id: str) -> SurfaceNode:
    return SurfaceNode(
        node_id=node_id,
        node_kind="definition_binding",
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=None,
        lens_id="lens.def",
        rule_id="r",
        status="resolved",
        payload_hash="p",
        payload={},
    )


def _graph(nodes: list[SurfaceNode], edges: tuple[SurfaceEdge, ...]) -> LegalSurfaceGraph:
    return LegalSurfaceGraph(
        schema="lawvm.legal_surface_graph.v0",
        graph_id="g1",
        subject=_subject(),
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in nodes},
        edges=edges,
        build_diagnostics=(),
    )


def _defines_edge(src: str, dst: str) -> SurfaceEdge:
    return SurfaceEdge(
        edge_id=f"{src}->{dst}",
        edge_kind="defines_term",
        src=src,
        dst=dst,
        rule_id="r",
        status="asserted",
        payload_hash="p",
        payload={},
    )


def test_orphan_entity_clean_when_every_entity_covered() -> None:
    binding = _surface_fact("b")
    ent = _entity("e", term="sivutuote")
    graph = _graph([binding, ent], (_defines_edge("b", "e"),))
    assert sweep_orphan_entity_nodes(graph) == ()


def test_orphan_entity_fires_on_uncovered_entity() -> None:
    binding = _surface_fact("b")
    covered = _entity("e", term="sivutuote")
    orphan = _entity("orphan", term="jäte")
    graph = _graph([binding, covered, orphan], (_defines_edge("b", "e"),))
    findings = sweep_orphan_entity_nodes(graph)
    assert [f.node_id for f in findings] == ["orphan"]
    f = findings[0]
    assert isinstance(f, OrphanEntityNodeFinding)
    assert f.code == SURFACE_ORPHAN_ENTITY_NODE
    assert f.node_kind == "term_symbol_entity"
    # self-evidencing handle from payload
    assert "jäte" in f.detail


def test_orphan_entity_ignores_non_entity_nodes() -> None:
    # a surface_fact node with no edge is NOT an entity orphan (out of scope)
    lonely = _surface_fact("lonely")
    assert sweep_orphan_entity_nodes(_graph([lonely], ())) == ()


def test_orphan_entity_counts_src_endpoint_as_covering() -> None:
    # an entity that is the SRC of some edge is covered too (not only dst)
    ent = _entity("e")
    other = _surface_fact("o")
    edge = SurfaceEdge(
        edge_id="e->o",
        edge_kind="defines_term",
        src="e",
        dst="o",
        rule_id="r",
        status="asserted",
        payload_hash="p",
        payload={},
    )
    assert sweep_orphan_entity_nodes(_graph([ent, other], (edge,))) == ()


# ---------------------------------------------------------------------------
# Corpus smoke (archive-gated) — report the real residual populations
# ---------------------------------------------------------------------------


def _archive_linked() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    try:
        from lawvm.tools.parse_bench import _archive_path

        return Path(_archive_path()).exists()
    except Exception:
        return False


@pytest.mark.skipif(
    not _archive_linked(), reason="canonical corpus archive not linked"
)
@pytest.mark.parametrize("statute_id", ["731/1999", "1501/1993", "523/1999"])
def test_corpus_surface_token_totality_smoke(statute_id: str) -> None:
    from farchive import Farchive
    from lawvm.finland.legal_surface.bundle import build_surface_bundle
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
    from lawvm.finland.legal_surface.source_syntax_graph import (
        assemble_source_syntax_graph_for_unit,
    )
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.export_fi_interlinks import _get_statute_xml
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        pytest.skip(f"no archived source XML for {statute_id}")

    # SURF-01 / SURF-02: per-unit token-realization gap (expected 0 — the forest
    # census is total by construction; a non-zero gap would be a real leak).
    bundle = build_surface_bundle(xml_bytes, statute_id)
    surf01_gaps = 0
    surf02_gaps = 0
    for u in bundle.units:
        forest = assemble_source_syntax_graph_for_unit(subject=bundle.subject, unit=u)
        cert = build_token_partition_coverage(forest, statute_id=u.source_unit_id)
        surf01_gaps += len(sweep_token_realization(cert))
        surf02_gaps += len(assert_handoff_parity(cert))
    print(
        f"\n[{statute_id}] SURF-01 token-realization gaps = {surf01_gaps}  "
        f"SURF-02 handoff-parity breaks = {surf02_gaps}"
    )

    # SURF-07: orphan entity-handle nodes over the real LegalSurfaceGraph.
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    entity_count = sum(
        1 for n in graph.nodes.values() if n.authority_role == "entity_handle"
    )
    orphans = sweep_orphan_entity_nodes(graph)
    kinds: dict[str, int] = {}
    for f in orphans:
        kinds[f.node_kind] = kinds.get(f.node_kind, 0) + 1
    print(
        f"[{statute_id}] SURF-07 entity_handles={entity_count} "
        f"orphans={len(orphans)} {kinds}"
    )
    # the sweeps surface, never crash; the gap sweeps must be clean (total census)
    assert surf01_gaps == 0
    assert surf02_gaps == 0
    assert all(isinstance(f, OrphanEntityNodeFinding) for f in orphans)
