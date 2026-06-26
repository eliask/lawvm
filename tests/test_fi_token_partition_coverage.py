"""Tests for the TokenPartitionCoverage + coverage-certifier (Pro ruling D2).

Two layers:

* Structure tests (corpus-free, always run): the four-class taxonomy, the
  certificate counts on synthetic forest fixtures, the coverage-certifier
  cross-check passing on a clean graph and FLAGGING an injected out-of-partition
  node, and the render goldens (text + json).

* Corpus smoke (archive-gated): build a real statute's forest, show the
  certificate + the HONEST ``unowned_violation`` count, and run the
  coverage-certifier cross-check over the real LegalSurfaceGraph (does any lens
  node sit on tokens the total parse did not own?).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.finland.legal_surface.source_syntax_graph import (
    SourceSyntaxGraph,
    SyntaxCoverage,
    SyntaxNode,
)
from lawvm.finland.legal_surface.token_partition_coverage import (
    PARTITION_CLASSES,
    GraphCoverageCrossCheck,
    TokenPartitionCoverage,
    build_token_partition_coverage,
    certificate_to_dict,
    certify_graph_coverage,
    coverage_certificate_to_dict,
    render_certificate,
    render_coverage_certificate,
)


# ---------------------------------------------------------------------------
# Synthetic builders (no parsing — direct construction of frozen objects)
# ---------------------------------------------------------------------------


def _subject() -> SurfaceGraphSubject:
    return SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="test/1/2024",
        scope={},
        surface_time=None,
        source_bundle_hash="deadbeef",
        language="fi",
    )


def _forest(
    *,
    graph_id: str = "forest-1",
    owned_spans: tuple[tuple[int, int], ...] = (),
    violation_spans: tuple[tuple[int, int, str, str], ...] = (),
    coverage: SyntaxCoverage,
    body_len: int = 200,
) -> SourceSyntaxGraph:
    """A synthetic forest with explicit owned construction leaves + violations.

    ``owned_spans`` become ``modal_predicate`` construction leaves (an owned
    leaf kind); ``violation_spans`` (start, end, shape, text) become
    ``residual_span`` witness nodes.
    """
    nodes: dict[str, SyntaxNode] = {}
    for i, (lo, hi) in enumerate(owned_spans):
        nid = f"owned-{i}"
        nodes[nid] = SyntaxNode(
            node_id=nid,
            kind="modal_predicate",
            char_start=lo,
            char_end=hi,
            node_status="parsed",
            families=("modal",),
        )
    for i, (lo, hi, shape, text) in enumerate(violation_spans):
        nid = f"resid-{i}"
        nodes[nid] = SyntaxNode(
            node_id=nid,
            kind="residual_span",
            char_start=lo,
            char_end=hi,
            node_status="open",
            residual_reason=f"unowned_cheap_signal:{shape}",
            residual_text=text,
        )
    return SourceSyntaxGraph(
        graph_id=graph_id,
        subject=_subject(),
        source_units=(),
        text_hash="hash",
        text_len=body_len,
        syntax_nodes=nodes,
        syntax_edges=(),
        parse_status="parsed",
        residuals=(),
        coverage=coverage,
    )


def _graph_with_nodes(
    nodes: list[SurfaceNode],
) -> LegalSurfaceGraph:
    return LegalSurfaceGraph(
        schema="lawvm.legal_surface_graph.v0",
        graph_id="g1",
        subject=_subject(),
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in nodes},
        edges=(),
        build_diagnostics=(),
    )


def _surface_node(
    node_id: str,
    *,
    unit: str,
    start: int,
    end: int,
    kind: str = "reference_expr",
    lens_id: str = "lens.ref",
) -> SurfaceNode:
    return SurfaceNode(
        node_id=node_id,
        node_kind=kind,
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=SourceSpanRef(
            source_unit_id=unit,
            source_hash="h",
            work_id="test/1/2024",
            address=None,
            char_start=start,
            char_end=end,
            text_hash="th",
        ),
        lens_id=lens_id,
        rule_id="r",
        node_status="resolved",
        payload_hash="p",
        payload={},
    )


# ---------------------------------------------------------------------------
# Taxonomy (the four Pro D2 classes)
# ---------------------------------------------------------------------------


def test_partition_classes_are_the_closed_four() -> None:
    assert PARTITION_CLASSES == (
        "owned",
        "benign_uninterpreted",
        "typed_residual",
        "unowned_violation",
    )


def test_coverage_pro_named_aliases_track_l0_fields() -> None:
    cov = SyntaxCoverage(
        total_tokens=10,
        owned_tokens=6,
        benign_tokens=2,
        residual_tokens=1,
        silent_tokens=1,
    )
    # the Pro D2 view is exactly the L0 fields, re-named (silent -> violation)
    assert cov.benign_uninterpreted_tokens == cov.benign_tokens == 2
    assert cov.typed_residual_tokens == cov.residual_tokens == 1
    assert cov.unowned_violation_tokens == cov.silent_tokens == 1


# ---------------------------------------------------------------------------
# Certificate counts on synthetic forests
# ---------------------------------------------------------------------------


def test_certificate_counts_and_partition() -> None:
    cov = SyntaxCoverage(
        total_tokens=20,
        owned_tokens=15,
        benign_tokens=3,
        residual_tokens=1,
        silent_tokens=1,
        family_token_counts={"modal": 10, "citation": 5},
    )
    forest = _forest(
        coverage=cov,
        owned_spans=((0, 10),),
        violation_spans=((50, 58, "modal_cue", "velvoitettu"),),
    )
    cert = build_token_partition_coverage(forest, statute_id="1/2024")
    assert cert.class_counts() == {
        "owned": 15,
        "benign_uninterpreted": 3,
        "typed_residual": 1,
        "unowned_violation": 1,
    }
    assert cert.is_partition()
    assert not cert.is_clean  # one unowned_violation
    assert cert.partition_total == 20
    assert cert.statute_id == "1/2024"


def test_certificate_clean_when_no_violation() -> None:
    cov = SyntaxCoverage(
        total_tokens=10,
        owned_tokens=8,
        benign_tokens=2,
        residual_tokens=0,
        silent_tokens=0,
    )
    cert = build_token_partition_coverage(_forest(coverage=cov))
    assert cert.is_clean
    assert cert.unowned_violation == 0
    assert not cert.violations


def test_certificate_violations_are_self_evidencing() -> None:
    cov = SyntaxCoverage(
        total_tokens=12,
        owned_tokens=5,
        benign_tokens=4,
        residual_tokens=0,
        silent_tokens=3,
    )
    forest = _forest(
        coverage=cov,
        violation_spans=(
            (10, 21, "modal_cue", "velvoitettu"),
            (40, 48, "deadline", "viipymättä"),
        ),
    )
    cert = build_token_partition_coverage(forest)
    # each surfaced violation carries verbatim offending text + its span
    assert [(v.shape, v.text) for v in cert.violations] == [
        ("modal_cue", "velvoitettu"),
        ("deadline", "viipymättä"),
    ]
    assert all(v.char_end > v.char_start for v in cert.violations)


# ---------------------------------------------------------------------------
# Coverage-certifier cross-check
# ---------------------------------------------------------------------------


def test_coverage_certifier_passes_on_clean_graph() -> None:
    cov = SyntaxCoverage(
        total_tokens=10, owned_tokens=10, benign_tokens=0,
        residual_tokens=0, silent_tokens=0,
    )
    forest = _forest(coverage=cov, owned_spans=((0, 50), (60, 100)))
    graph = _graph_with_nodes(
        [
            _surface_node("n1", unit="u1", start=5, end=20),
            _surface_node("n2", unit="u1", start=60, end=90),
        ]
    )
    cert = certify_graph_coverage(graph, {"u1": forest})
    assert cert.passes
    assert cert.nodes_checked == 2
    assert not cert.violations


def test_coverage_certifier_flags_out_of_partition_node() -> None:
    cov = SyntaxCoverage(
        total_tokens=10, owned_tokens=10, benign_tokens=0,
        residual_tokens=0, silent_tokens=0,
    )
    forest = _forest(coverage=cov, owned_spans=((0, 50),))
    graph = _graph_with_nodes(
        [
            _surface_node("good", unit="u1", start=5, end=20),
            # span [120,140) is NOT inside any owned forest leaf -> violation
            _surface_node("bad", unit="u1", start=120, end=140),
        ]
    )
    cert = certify_graph_coverage(graph, {"u1": forest})
    assert not cert.passes
    assert [v.node_id for v in cert.violations] == ["bad"]
    assert cert.violations[0].reason == "span_not_owned"


def test_coverage_certifier_flags_missing_forest() -> None:
    graph = _graph_with_nodes([_surface_node("n", unit="missing", start=0, end=5)])
    cert = certify_graph_coverage(graph, {})
    assert not cert.passes
    assert cert.violations[0].reason == "no_forest_for_unit"


def test_coverage_certifier_skips_nodes_without_span() -> None:
    node = SurfaceNode(
        node_id="nospan",
        node_kind="entity_handle",
        authority_role="entity_handle",
        jurisdiction="fi",
        source_ref=None,
        lens_id=None,
        rule_id=None,
        node_status="resolved",
        payload_hash="p",
        payload={},
    )
    cert = certify_graph_coverage(_graph_with_nodes([node]), {})
    assert cert.passes
    assert cert.nodes_skipped == 1
    assert cert.nodes_checked == 0


def test_coverage_certifier_partial_overlap_is_covered_when_contiguous() -> None:
    cov = SyntaxCoverage(
        total_tokens=10, owned_tokens=10, benign_tokens=0,
        residual_tokens=0, silent_tokens=0,
    )
    # two adjacent owned leaves; a node spanning the seam IS covered
    forest = _forest(coverage=cov, owned_spans=((0, 30), (30, 60)))
    graph = _graph_with_nodes([_surface_node("seam", unit="u1", start=20, end=50)])
    assert certify_graph_coverage(graph, {"u1": forest}).passes


# ---------------------------------------------------------------------------
# Render goldens (text + json)
# ---------------------------------------------------------------------------


def test_render_certificate_text_golden() -> None:
    cov = SyntaxCoverage(
        total_tokens=10,
        owned_tokens=8,
        benign_tokens=1,
        residual_tokens=0,
        silent_tokens=1,
        family_token_counts={"modal": 8},
    )
    forest = _forest(
        graph_id="gid",
        coverage=cov,
        violation_spans=((5, 16, "modal_cue", "velvoitettu"),),
    )
    cert = build_token_partition_coverage(forest, statute_id="1/2024")
    text = render_certificate(cert)
    assert "TOKEN PARTITION CERTIFICATE — 1/2024" in text
    assert "owned                 : 8  (80.00%)" in text
    assert "unowned_violation     : 1  (10.000%)  [VIOLATION]" in text
    assert "partition ok          : True" in text
    assert "unowned_violation spans (1):" in text
    assert "'velvoitettu'" in text


def test_certificate_to_dict_golden() -> None:
    cov = SyntaxCoverage(
        total_tokens=10, owned_tokens=9, benign_tokens=1,
        residual_tokens=0, silent_tokens=0,
    )
    cert = build_token_partition_coverage(
        _forest(graph_id="gid", coverage=cov), statute_id="1/2024"
    )
    d = certificate_to_dict(cert)
    assert d == {
        "graph_id": "gid",
        "statute_id": "1/2024",
        "total_tokens": 10,
        "classes": {
            "owned": 9,
            "benign_uninterpreted": 1,
            "typed_residual": 0,
            "unowned_violation": 0,
        },
        "is_partition": True,
        "is_clean": True,
        "parse_status": "parsed",
        "violations": [],
        "family_token_counts": {},
    }


def test_render_coverage_certificate_text_golden() -> None:
    cov = SyntaxCoverage(
        total_tokens=5, owned_tokens=5, benign_tokens=0,
        residual_tokens=0, silent_tokens=0,
    )
    forest = _forest(coverage=cov, owned_spans=((0, 50),))
    graph = _graph_with_nodes([_surface_node("bad", unit="u1", start=120, end=140)])
    cert = certify_graph_coverage(graph, {"u1": forest})
    text = render_coverage_certificate(cert)
    assert "COVERAGE CERTIFIER" in text
    assert "result        : FAIL  (1 not forest-owned)" in text
    assert "reason=span_not_owned" in text
    d = coverage_certificate_to_dict(cert)
    assert d["passes"] is False
    assert d["violations"] == [
        {
            "node_id": "bad",
            "node_kind": "reference_expr",
            "lens_id": "lens.ref",
            "source_unit_id": "u1",
            "char_start": 120,
            "char_end": 140,
            "reason": "span_not_owned",
        }
    ]


# ---------------------------------------------------------------------------
# Corpus smoke (archive-gated)
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
@pytest.mark.parametrize("statute_id", ["731/1999", "39/1889"])
def test_corpus_certificate_and_coverage_smoke(statute_id: str) -> None:
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

    bundle = build_surface_bundle(xml_bytes, statute_id)
    forests = {
        u.source_unit_id: assemble_source_syntax_graph_for_unit(
            subject=bundle.subject, unit=u
        )
        for u in bundle.units
    }
    # per-unit token-partition certificates compose without re-parsing
    total_violation = 0
    for unit_id, forest in forests.items():
        cert = build_token_partition_coverage(forest, statute_id=unit_id)
        assert isinstance(cert, TokenPartitionCoverage)
        assert cert.is_partition()
        total_violation += cert.unowned_violation
    print(
        f"\n[{statute_id}] token-partition unowned_violation tokens "
        f"(forest sum) = {total_violation}"
    )

    # the coverage-certifier cross-check over the real LegalSurfaceGraph
    graph = build_legal_surface_graph(xml_bytes, statute_id)
    cov_cert = certify_graph_coverage(graph, forests)
    print(
        f"[{statute_id}] coverage-certifier: checked={cov_cert.nodes_checked} "
        f"skipped={cov_cert.nodes_skipped} "
        f"out_of_partition={len(cov_cert.violations)} "
        f"passes={cov_cert.passes}"
    )
    # a real GraphCoverageCrossCheck is returned (we surface, never crash)
    assert isinstance(cov_cert, GraphCoverageCrossCheck)
