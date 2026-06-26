"""Tests for the cross-statute corpus Legal Surface Graph (corpus_graph.py).

Proves the keystone: merging per-statute graphs into one corpus graph collapses
the SAME target across statutes to ONE shared entity node, asserts cross-statute
``refers_to`` edges into it, and makes ``citations_of`` answer "what cites this
provision". Plus a real-corpus smoke that the graph builds over ~20 statutes and
has cross-statute edges. The authority firewall is asserted to hold over the
merged graph (surface_only / never replay_authorized).
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_assembler import mint_entity_node_id
from lawvm.finland.legal_surface.corpus_graph import (
    CorpusReferenceEdgePass,
    build_corpus_surface_graph,
    citations_of,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# B is the cited act 2022/711. A (2003/314) and C (2010/100) both cite B's §5
# via a concrete AKN <ref> (so the resolution is `unchanged` with a provision
# tail → a provision-level address target).
_B_ID = "2022/711"
_A_ID = "2003/314"
_C_ID = "2010/100"
_D_ID = "2015/500"  # cites B at the ACT level only (no provision)


def _statute_xml(citing_section: str, ref_href: str, surface: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
        f"<section><num>{citing_section} §</num><paragraph><content>"
        f'<p>Noudatetaan, mitä <ref href="{ref_href}">{surface}</ref> '
        "säädetään.</p>"
        "</content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


# A cites B §5, C cites B §5 (the SHARED target), D cites B at act level.
_XML = {
    _A_ID: _statute_xml(
        "5", "/akn/fi/act/statute-consolidated/2022/711#sec_5", "lannoitelaissa"
    ),
    _C_ID: _statute_xml(
        "3", "/akn/fi/act/statute-consolidated/2022/711#sec_5", "lannoitelaissa"
    ),
    _D_ID: _statute_xml(
        "9", "/akn/fi/act/statute-consolidated/2022/711", "lannoitelaissa"
    ),
    _B_ID: _statute_xml(
        "1", "/akn/fi/act/statute-consolidated/2003/314#sec_2", "edellisessä laissa"
    ),
}


class _StubCandidate:
    def __init__(self, statute_id: str) -> None:
        self.statute_id = statute_id


class _StubLookupResult:
    def __init__(self, candidates: list[str]) -> None:
        self.registry_status = {0: "none", 1: "single"}.get(len(candidates), "multiple")
        self.candidates = tuple(_StubCandidate(c) for c in candidates)


class _StubStatuteRegistry:
    def __init__(self, table: dict[str, list[str]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: object = None) -> _StubLookupResult:
        return _StubLookupResult(self._table.get(name, []))


class _DictStore:
    """A minimal store: serves the synthetic statute XML as oracle bodies."""

    def __init__(self, xml: dict[str, bytes]) -> None:
        self._xml = xml

    def read_oracle(self, sid: str) -> bytes | None:
        return self._xml.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return None

    def read_amendment(self, sid: str) -> bytes | None:
        return None


def _build():
    store = _DictStore(_XML)
    registry = _StubStatuteRegistry({"lannoitelaki": ["2022/711"]})
    return build_corpus_surface_graph(
        [_A_ID, _C_ID, _D_ID, _B_ID],
        store,
        statute_registry=registry,
    )


def test_shared_target_node_is_deduped() -> None:
    graph = _build()
    # B §5 collapses to ONE shared legal_address_entity node, regardless of being
    # cited from both A and C.
    b_sec5 = mint_entity_node_id(f"{_B_ID}#5")
    assert b_sec5 in graph.nodes
    assert graph.nodes[b_sec5].node_kind == "legal_address_entity"
    addr_nodes = [
        nid
        for nid, n in graph.nodes.items()
        if n.node_kind == "legal_address_entity"
        and n.payload.get("work_id") == _B_ID
        and n.payload.get("address") == "5"
    ]
    assert addr_nodes == [b_sec5]  # exactly one, deduped across A and C


def test_two_cross_statute_refers_to_into_shared_node() -> None:
    graph = _build()
    b_sec5 = mint_entity_node_id(f"{_B_ID}#5")
    refers = [
        e
        for e in graph.edges
        if e.edge_kind == "refers_to" and e.dst == b_sec5
    ]
    # one asserted refers_to from A, one from C — both into the shared node
    assert len(refers) == 2
    assert {e.surface_edge_status for e in refers} == {"asserted"}
    citing_works = set()
    for e in refers:
        src = graph.nodes[e.src]
        assert src.node_kind == "reference_resolution"
        assert src.source_ref is not None
        citing_works.add(src.source_ref.work_id)
    assert citing_works == {_A_ID, _C_ID}


def test_citations_of_returns_both_citers() -> None:
    graph = _build()
    b_sec5 = mint_entity_node_id(f"{_B_ID}#5")
    cites = citations_of(graph, b_sec5)
    assert len(cites) == 2
    assert {c.citing_work_id for c in cites} == {_A_ID, _C_ID}
    assert {c.edge_kind for c in cites} == {"refers_to"}
    assert {c.citation_status for c in cites} == {"asserted"}


def test_act_level_citation_is_not_a_provision_address() -> None:
    graph = _build()
    # D cites B at the act level (no provision) → NO legal_address_entity for B
    # at act level, but the statute-level legal_work_entity (entity:2022/711)
    # exists from the per-statute lens.
    assert mint_entity_node_id(_B_ID) in graph.nodes
    # there is no fabricated act-level address node
    assert mint_entity_node_id(f"{_B_ID}#") not in graph.nodes


def test_firewall_holds_over_merged_graph() -> None:
    graph = _build()
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_corpus_subject_is_corpus_slice() -> None:
    graph = _build()
    assert graph.subject.work_id is None
    assert graph.subject.scope["kind"] == "corpus_slice"
    assert set(graph.subject.scope["statute_ids"]) == {_A_ID, _C_ID, _D_ID, _B_ID}


def test_edge_pass_fails_loud_on_target_disagreement() -> None:
    # The provision-address helper refuses to fabricate an address when the
    # resolved target id disagrees with the citing provision ref.
    from lawvm.core.legal_surface_assembler import SurfaceAssemblyError
    from lawvm.finland.legal_surface.corpus_graph import _provision_address_id

    with pytest.raises(SurfaceAssemblyError):
        _provision_address_id("2022/711", "1999/123/5")
    # statute-level (no provision tail) → None, not an error
    assert _provision_address_id("2022/711", "2022/711") is None
    # provision tail → a distinct navigable address id
    assert _provision_address_id("2022/711", "2022/711/7") == "2022/711#7"


def test_edge_pass_protocol_surface() -> None:
    p = CorpusReferenceEdgePass()
    assert p.pass_id == "fi.corpus.reference_edges.v0"
    assert set(p.emits_edge_kinds) == {"refers_to", "has_candidate"}


# ── real-corpus smoke ────────────────────────────────────────────────────────


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


def test_real_corpus_smoke_builds_with_cross_statute_edges() -> None:
    store = _real_store_or_skip()
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
    assert ids, "expected at least some statute ids in the corpus"

    graph = build_corpus_surface_graph(
        ids,
        store,
        statute_registry=statute_registry,
        eu_registry=eu_nickname,
    )
    # builds without error, is a corpus slice, firewall holds
    assert graph.subject.scope["kind"] == "corpus_slice"
    for node in graph.nodes.values():
        assert node.surface_only is True
    for edge in graph.edges:
        assert edge.surface_only is True

    # cross-statute edges: at least one refers_to whose citing statute differs
    # from the target work id (a genuine inter-statute link).
    refers = [e for e in graph.edges if e.edge_kind == "refers_to"]
    assert refers, "expected resolved references across ~20 statutes"
    cross = 0
    for e in refers:
        src = graph.nodes[e.src]
        dst = graph.nodes[e.dst]
        citing = src.source_ref.work_id if src.source_ref else None
        target = dst.payload.get("work_id")
        if citing is not None and target is not None and citing != target:
            cross += 1
    assert cross > 0, "expected at least one cross-statute refers_to edge"
