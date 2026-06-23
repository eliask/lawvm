"""Tests for the corpus-graph EXPORT tool (tools/corpus_surface_graph.py).

Proves the export projects the merged cross-statute corpus surface graph into a
typed, deterministic artifact: node rows + edge rows (each carrying edge_kind,
endpoints, provenance, resolution status, the surface_only firewall flag, and the
cross_statute flag) plus a census (node-kind / edge-kind counts, the interlink
fabric, the resolution-status breakdown, and the count of genuinely inter-statute
reference edges). The DECLARED-slice discipline (no silent full-corpus default) is
asserted. The artifact's canonical dict is stable.
"""
from __future__ import annotations

import pytest

from lawvm.finland.legal_surface.corpus_graph import build_corpus_surface_graph
from lawvm.tools.corpus_surface_graph import (
    EXPORT_SCHEMA,
    CorpusSurfaceGraphExport,
    _resolve_slice,
    build_export,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# B (2022/711) is the cited act; A (2003/314) and C (2010/100) both cite B's §5
# via a concrete AKN <ref> → the shared provision-level address target.
_B_ID = "2022/711"
_A_ID = "2003/314"
_C_ID = "2010/100"


def _statute_xml(citing_section: str, ref_href: str, surface: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
        f"<section><num>{citing_section} §</num><paragraph><content>"
        f'<p>Noudatetaan, mitä <ref href="{ref_href}">{surface}</ref> '
        "säädetään.</p>"
        "</content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


_XML = {
    _A_ID: _statute_xml(
        "5", "/akn/fi/act/statute-consolidated/2022/711#sec_5", "lannoitelaissa"
    ),
    _C_ID: _statute_xml(
        "3", "/akn/fi/act/statute-consolidated/2022/711#sec_5", "lannoitelaissa"
    ),
    _B_ID: _statute_xml(
        "1", "/akn/fi/act/statute-consolidated/2003/314#sec_2", "edellisessä laissa"
    ),
}


class _DictStore:
    def __init__(self, xml: dict[str, bytes]) -> None:
        self._xml = xml

    def read_oracle(self, sid: str) -> bytes | None:
        return self._xml.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return None

    def read_amendment(self, sid: str) -> bytes | None:
        return None

    def list_statute_ids(self) -> list[str]:
        return [_A_ID, _C_ID, _B_ID]


class _StubCandidate:
    def __init__(self, statute_id: str) -> None:
        self.statute_id = statute_id


class _StubLookupResult:
    def __init__(self, candidates: list[str]) -> None:
        self.status = {0: "none", 1: "single"}.get(len(candidates), "multiple")
        self.candidates = tuple(_StubCandidate(c) for c in candidates)


class _StubStatuteRegistry:
    def __init__(self, table: dict[str, list[str]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: object = None) -> _StubLookupResult:
        return _StubLookupResult(self._table.get(name, []))


def _build_export() -> CorpusSurfaceGraphExport:
    store = _DictStore(_XML)
    registry = _StubStatuteRegistry({"lannoitelaki": ["2022/711"]})
    graph = build_corpus_surface_graph(
        [_A_ID, _C_ID, _B_ID], store, statute_registry=registry
    )
    return build_export(graph)


def test_export_is_typed_artifact_backed_by_claim() -> None:
    export = _build_export()
    assert export.claim_id == "lawvm.fi.legal_surface_graph.v1"
    assert export.census.nodes_total == len(export.nodes)
    assert export.census.edges_total == len(export.edges)
    assert set(export.slice_statute_ids) == {_A_ID, _C_ID, _B_ID}


def test_export_node_and_edge_rows_present() -> None:
    export = _build_export()
    assert export.nodes, "expected node rows"
    assert export.edges, "expected edge rows"
    # B §5 collapses to one shared legal_address_entity row.
    addr = [
        n
        for n in export.nodes
        if n.node_kind == "legal_address_entity"
        and n.work_id == _B_ID
    ]
    assert len(addr) == 1


def test_cross_statute_reference_edges_counted() -> None:
    export = _build_export()
    # Every refers_to / has_candidate whose citing work differs from the target
    # work is a genuine inter-statute link. This includes BOTH the statute-level
    # edges into legal_work_entity nodes (the per-statute lens "act A cites act B")
    # AND the provision-level edges into the shared legal_address_entity nodes (the
    # corpus pass "provision cites provision").
    cross = [e for e in export.edges if e.cross_statute]
    assert len(cross) == export.census.cross_statute_reference_edges
    assert cross, "expected cross-statute reference edges"
    assert all(e.citing_work_id != e.target_work_id for e in cross)
    # The navigable payoff: A and C each cite B §5 → exactly two provision-level
    # cross-statute edges into the ONE shared B §5 address node.
    b_sec5 = [
        e
        for e in cross
        if e.target_work_id == _B_ID and graph_dst_is_address(export, e)
    ]
    assert len(b_sec5) == 2
    assert {e.citing_work_id for e in b_sec5} == {_A_ID, _C_ID}
    assert {e.edge_kind for e in b_sec5} == {"refers_to"}


def graph_dst_is_address(export: CorpusSurfaceGraphExport, edge) -> bool:
    """True iff the edge's dst is a legal_address_entity (provision-level) node."""
    by_id = {n.node_id: n for n in export.nodes}
    dst = by_id.get(edge.dst)
    return dst is not None and dst.node_kind == "legal_address_entity"


def test_census_interlink_and_resolution_breakdown() -> None:
    export = _build_export()
    c = export.census
    assert c.firewall_holds is True
    assert c.interlink_edges_total == sum(c.interlink_edges.values())
    assert c.interlink_edges.get("refers_to", 0) >= 2
    # resolution-status breakdown is populated (the refs resolved to a target).
    assert sum(c.resolution_status.values()) >= 1


def test_firewall_flag_on_every_row() -> None:
    export = _build_export()
    assert all(n.surface_only for n in export.nodes)
    assert all(e.surface_only for e in export.edges)


def test_export_is_deterministic_and_sorted() -> None:
    a = _build_export()
    b = _build_export()
    assert a.to_canonical_dict() == b.to_canonical_dict()
    node_ids = [n.node_id for n in a.nodes]
    edge_ids = [e.edge_id for e in a.edges]
    assert node_ids == sorted(node_ids)
    assert edge_ids == sorted(edge_ids)


def test_canonical_dict_schema() -> None:
    export = _build_export()
    d = export.to_canonical_dict()
    assert d["schema"] == EXPORT_SCHEMA
    assert d["claim_id"] == "lawvm.fi.legal_surface_graph.v1"
    assert "census" in d and "nodes" in d and "edges" in d
    assert isinstance(d["nodes"], list) and isinstance(d["edges"], list)


def test_slice_must_be_declared() -> None:
    store = _DictStore(_XML)
    # No --ids and limit 0 → fail loud (no silent full-corpus default).
    with pytest.raises(SystemExit):
        _resolve_slice(store, None, 0)
    # explicit ids verbatim
    assert _resolve_slice(store, f"{_A_ID}, {_B_ID}", 0) == [_A_ID, _B_ID]
    # --limit prefix of the corpus
    assert _resolve_slice(store, None, 2) == [_A_ID, _C_ID]
