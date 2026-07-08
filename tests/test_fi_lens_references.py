"""Tests for the H1 REFERENCES surface lens (Pro r5 Phase 2, §D3/§D5).

These prove the seed -> assembler path end to end: the lens turns existing
``ReferenceMention`` output into Legal Surface Graph seeds, and the core
assembler mints them into a firewall-safe graph whose intrinsic edges resolve.
"""
from __future__ import annotations

import datetime as dt

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import SourceUnitRef
from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.references import (
    LENS_ID,
    ReferenceLens,
    _CONFIDENCE_TO_STATUS,
)
from lawvm.core.reference_mention import CiteConfidence
from lawvm.finland.references.resolve import (
    StatuteSuccessorEdge,
    SuccessorReferenceReasonCode,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A small synthetic statute: one cross-statute id-bearing <ref> (→ 2022/711) and
# one internal § cross-reference (5 §:ssä, same statute).
_XML = (
    f'<akomaNtoso xmlns="{_AKN}" '
    'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
    "<section><num>5 §</num><paragraph><content>"
    '<p>Noudatetaan, mitä '
    '<ref href="/akn/fi/act/statute-consolidated/2022/711#sec_7">lannoitelaissa</ref>'
    " säädetään.</p>"
    "<p>Lisäksi 5 §:ssä säädetään poikkeuksesta.</p>"
    "</content></paragraph></section></body></act></akomaNtoso>"
).encode("utf-8")

_STATUTE_ID = "2003/314"

_RADIATION_XML = (
    f'<akomaNtoso xmlns="{_AKN}"><act><body>'
    "<section><num>3 §</num><paragraph><content><p>"
    "Tätä lakia ei sovelleta säteilylaissa (592/1991) tarkoitettuun toimintaan."
    "</p></content></paragraph></section>"
    "</body></act></akomaNtoso>"
).encode("utf-8")


def _run_lens(*, options: dict[str, object] | None = None):
    bundle = build_surface_bundle(_XML, _STATUTE_ID, surface_time="2020-06-01")
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(surface_time="2020-06-01", options=options or {})
    return bundle, lens.analyze(bundle, context=ctx)


def test_lens_protocol_surface() -> None:
    lens = ReferenceLens()
    assert lens.lens_id == "fi.references.v0"
    assert lens.jurisdiction == "fi"
    assert lens.schema_version == "v0"
    assert "reference_expr" in lens.produces_node_kinds
    assert "reference_resolution" in lens.produces_node_kinds
    assert set(lens.produces_edge_kinds) >= {
        "resolution_of",
        "refers_to",
        "has_candidate",
        "unresolved_because",
    }
    assert lens.required_views == ("raw_text",)


def test_confidence_status_map_is_total() -> None:
    # Every CiteConfidence member maps to a graph status (fail-loud completeness).
    assert set(_CONFIDENCE_TO_STATUS) == set(CiteConfidence)
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.EXACT] == "resolved"
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.STATUTE_ONLY] == "statute_only"
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.AMBIGUOUS] == "ambiguous"
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.OPEN] == "open"
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.BROKEN] == "broken"
    assert _CONFIDENCE_TO_STATUS[CiteConfidence.UNRESOLVED] == "unsupported"


def test_lens_emits_expr_and_resolution_seeds() -> None:
    _bundle, result = _run_lens()
    assert result.lens_id == LENS_ID
    kinds = [s.node_kind for s in result.node_seeds]
    assert "reference_expr" in kinds
    assert "reference_resolution" in kinds
    # one expr + one resolution per located mention
    n_expr = kinds.count("reference_expr")
    n_res = kinds.count("reference_resolution")
    assert n_expr == n_res
    assert n_expr >= 1
    # every reference_expr has a source_ref (located in raw_text)
    for seed in result.node_seeds:
        if seed.node_kind == "reference_expr":
            assert seed.source_ref is not None
    # one resolution_of edge per (expr, resolution) pair
    res_of = [e for e in result.edge_seeds if e.edge_kind == "resolution_of"]
    assert len(res_of) == n_expr
    # without registries, no target resolution: no refers_to / has_candidate
    assert all(e.edge_kind == "resolution_of" for e in result.edge_seeds)
    assert result.coverage["resolution_enabled"] is False


def _assemble(bundle, result):
    unit = bundle.units[0]
    source_units = (
        SourceUnitRef(
            source_unit_id=unit.source_unit_id,
            work_id=unit.work_id,
            address=unit.address,
            source_hash=unit.source_hash,
        ),
    )
    return assemble_surface_graph(
        subject=bundle.subject,
        source_units=source_units,
        lens_results=(result,),
    )


def test_e2e_assemble_graph() -> None:
    bundle, result = _run_lens()
    graph = _assemble(bundle, result)

    node_kinds = [n.node_kind for n in graph.nodes.values()]
    assert "reference_expr" in node_kinds
    assert "reference_resolution" in node_kinds

    # resolution_of edges resolve to real nodes in the graph
    res_of = [e for e in graph.edges if e.edge_kind == "resolution_of"]
    assert res_of
    for edge in res_of:
        assert edge.src in graph.nodes
        assert edge.dst in graph.nodes
        assert graph.nodes[edge.src].node_kind == "reference_resolution"
        assert graph.nodes[edge.dst].node_kind == "reference_expr"

    # firewall holds: every node and edge is surface_only and not replay_authorized
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_e2e_with_registry_resolves_target() -> None:
    # A registry that resolves the by-name placeholder to a single id should make
    # the lens emit a legal_work_entity + refers_to edge (§D5 resolved endpoint).
    registry = _StubStatuteRegistry({"lannoitelaki": ["2022/711"]})
    bundle, result = _run_lens(options={"statute_registry": registry})
    assert result.coverage["resolution_enabled"] is True

    entity_seeds = [s for s in result.node_seeds if s.node_kind == "legal_work_entity"]
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    # the resolved by-name placeholder yields one entity + one refers_to
    assert any(s.payload.get("work_id") == "2022/711" for s in entity_seeds)
    assert any(e.payload.get("work_id") == "2022/711" for e in refers_to)
    # resolved endpoints assert refers_to, never has_candidate, for that mention
    assert all(e.surface_edge_status == "asserted" for e in refers_to)

    graph = _assemble(bundle, result)
    # the minted entity node exists and the refers_to edge resolves to it
    entity_ids = {
        nid for nid, n in graph.nodes.items() if n.node_kind == "legal_work_entity"
    }
    assert entity_ids
    g_refers = [e for e in graph.edges if e.edge_kind == "refers_to"]
    assert g_refers
    for edge in g_refers:
        assert edge.dst in entity_ids
        assert graph.nodes[edge.src].node_kind == "reference_resolution"
    # has_candidate appears only if some mention was ambiguous; not here
    assert not has_candidate


def test_e2e_ambiguous_emits_candidates_not_refers_to() -> None:
    # Two candidates for the by-name placeholder → ambiguous: has_candidate edges
    # only, NO refers_to from THAT resolution node (§D5). A distinct candidate id
    # (not the direct <ref> target) keeps the assertion unambiguous.
    registry = _StubStatuteRegistry({"lannoitelaki": ["1999/123", "1888/77"]})
    bundle, result = _run_lens(options={"statute_registry": registry})

    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    assert has_candidate
    assert all(e.surface_edge_status == "candidate" for e in has_candidate)
    candidate_ids = {e.payload.get("candidate_id") for e in has_candidate}
    assert {"1999/123", "1888/77"} <= candidate_ids

    # The resolution node that owns has_candidate edges must NOT also own a
    # refers_to edge — ambiguity is never silently collapsed to one target (§D5).
    ambiguous_resolutions = {e.src_local for e in has_candidate}
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    assert not any(e.src_local in ambiguous_resolutions for e in refers_to)
    # No candidate id leaked into a refers_to assertion anywhere.
    assert not any(
        e.payload.get("work_id") in {"1999/123", "1888/77"} for e in refers_to
    )

    graph = _assemble(bundle, result)
    g_has = [e for e in graph.edges if e.edge_kind == "has_candidate"]
    assert g_has
    for edge in g_has:
        assert edge.dst in graph.nodes
        assert graph.nodes[edge.dst].node_kind == "legal_work_entity"


def test_unlocatable_surface_still_mints_reference_expr() -> None:
    # Phase 3b cardinality-gap closure: a mention whose surface cannot be
    # char-anchored in raw_text is NO LONGER dropped to a residual — it still
    # mints a reference_expr node (against a degenerate-char fallback source_ref),
    # so the full mention set round-trips to fi_refs rows. The authoritative byte
    # span rides the payload, independent of the graph char coordinate.
    bundle = build_surface_bundle(_XML, _STATUTE_ID)
    # Replace the unit's raw_text with empty so locate_span cannot anchor.
    import dataclasses

    unit = bundle.units[0]
    blanked_unit = dataclasses.replace(unit, raw_text="")
    blanked_bundle = dataclasses.replace(bundle, units=(blanked_unit,))

    lens = ReferenceLens()
    result = lens.analyze(blanked_bundle, context=SurfaceAnalysisContext())
    # Every mention still becomes a reference_expr node (no silent drop).
    expr_seeds = [s for s in result.node_seeds if s.node_kind == "reference_expr"]
    assert expr_seeds
    # The fallback source_ref is a degenerate (zero-length) char span — the byte
    # origin is carried in the payload, not fabricated into the char coordinate.
    for seed in expr_seeds:
        assert seed.source_ref is not None
        assert seed.source_ref.char_start == seed.source_ref.char_end

    # The nodes assemble (fail-loud firewall still holds, no drop).
    graph = _assemble(blanked_bundle, result)
    assert any(n.node_kind == "reference_expr" for n in graph.nodes.values())


def test_unit_effective_interval_narrows_multitemporal_resolution() -> None:
    # The lens threads the unit's effective_interval onto each mention's
    # valid_at_interval and resolves by-name placeholders against that instant.
    # A two-version act name whose unit window predates the later version
    # resolves to the EARLIER version (not ambiguous, not the latest).
    import dataclasses
    import datetime as dt

    registry = _VersionedStubRegistry(
        {
            "lannoitelaki": [
                ("1990/111", dt.date(1990, 1, 1)),
                ("2010/222", dt.date(2010, 1, 1)),
            ]
        }
    )
    bundle = build_surface_bundle(_XML, _STATUTE_ID, surface_time="2000-06-01")
    unit = dataclasses.replace(
        bundle.units[0], effective_interval=("2000-06-01", None)
    )
    scoped_bundle = dataclasses.replace(bundle, units=(unit,))
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(
        surface_time="2000-06-01", options={"statute_registry": registry}
    )
    result = lens.analyze(scoped_bundle, context=ctx)

    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    # The 2000 window selects the 1990 version only -> resolved, no candidates.
    assert any(e.payload.get("work_id") == "1990/111" for e in refers_to)
    assert not any(
        e.payload.get("candidate_id") in {"1990/111", "2010/222"}
        for e in has_candidate
    )


def test_build_surface_bundle_threads_surface_time_to_effective_interval() -> None:
    # The fix: ``build_surface_bundle(surface_time=X)`` populates the unit's
    # effective_interval START (was always (None, None), leaving the as-of-citing
    # disambiguation lever inert). End-to-end, with NO manual interval injection,
    # a supplied surface_time now resolves a two-version by-name collision to the
    # version in force as-of the citing text — excluding a not-yet-enacted one.
    registry = _VersionedStubRegistry(
        {
            "lannoitelaki": [
                ("1990/111", dt.date(1990, 1, 1)),
                ("2010/222", dt.date(2010, 1, 1)),
            ]
        }
    )
    bundle = build_surface_bundle(_XML, _STATUTE_ID, surface_time="2000-06-01")
    # The fix point: the START is the supplied surface_time, the right edge open.
    assert bundle.units[0].effective_interval == ("2000-06-01", None)

    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(
        surface_time="2000-06-01", options={"statute_registry": registry}
    )
    result = lens.analyze(bundle, context=ctx)
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    # 2000 window selects the 1990 version only -> resolved, the 2010 version is
    # excluded (not-yet-enacted as-of the citing text), no ambiguity candidates.
    assert any(e.payload.get("work_id") == "1990/111" for e in refers_to)
    assert not any(
        e.payload.get("candidate_id") in {"1990/111", "2010/222"}
        for e in has_candidate
    )


def test_build_surface_bundle_no_surface_time_stays_ambiguous() -> None:
    # Negative / fail-loud case: with NO surface_time the effective_interval stays
    # open, the as-of lever cannot fire, and a two-version name stays AMBIGUOUS
    # (every candidate listed, no guessed "now", no pick).
    registry = _VersionedStubRegistry(
        {
            "lannoitelaki": [
                ("1990/111", dt.date(1990, 1, 1)),
                ("2010/222", dt.date(2010, 1, 1)),
            ]
        }
    )
    bundle = build_surface_bundle(_XML, _STATUTE_ID)
    assert bundle.units[0].effective_interval == (None, None)
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(options={"statute_registry": registry})
    result = lens.analyze(bundle, context=ctx)
    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    candidate_ids = {e.payload.get("candidate_id") for e in has_candidate}
    assert {"1990/111", "2010/222"} <= candidate_ids
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    assert not any(
        e.payload.get("work_id") in {"1990/111", "2010/222"} for e in refers_to
    )


def test_successor_resolution_payload_preserves_literal_target_and_operational_endpoint() -> None:
    """The lens carries successor resolution without rewriting the literal citation."""
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=dt.date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    bundle = build_surface_bundle(
        _RADIATION_XML, "527/2014", surface_time="2026-01-01"
    )
    result = ReferenceLens().analyze(
        bundle,
        context=SurfaceAnalysisContext(
            surface_time="2026-01-01",
            options={
                "statute_registry": _StubStatuteRegistry({}),
                "successor_edges": (edge,),
                "successor_as_of": dt.date(2026, 1, 1),
            },
        ),
    )

    expr = next(s for s in result.node_seeds if s.node_kind == "reference_expr")
    resolution = next(
        s for s in result.node_seeds if s.node_kind == "reference_resolution"
    )
    assert expr.payload["surface_text"] == "säteilylaissa (592/1991)"
    assert expr.payload["target_id"] == "1991/592"
    assert resolution.payload["work_id"] == "1991/592"
    assert resolution.payload["literal_work_id"] == "1991/592"
    assert resolution.payload["operative_work_id"] == "859/2018"
    assert resolution.payload["successor_resolution_status"] == "resolved"
    assert resolution.payload["successor_as_of"] == "2026-01-01"
    assert resolution.payload["successor_reason_code"] == (
        SuccessorReferenceReasonCode.UNIQUE_WITNESSED_SUCCESSOR_CHAIN.value
    )
    assert resolution.payload["successor_chain"] == [
        {
            "predecessor_work_id": "1991/592",
            "successor_work_id": "859/2018",
            "effective_from": "2018-12-15",
            "witness_id": "finlex:1991/592:repealed-by:859/2018",
            "witness_text": "Tämä laki on kumottu lailla 859/2018.",
            "rule_id": "fi.reference_successor.witnessed_edge",
        }
    ]

    # Ordinary reference edges still point at the literal target only. The
    # successor endpoint is payload evidence, not a new asserted refers_to edge.
    refers_to_ids = {
        e.payload.get("work_id") for e in result.edge_seeds if e.edge_kind == "refers_to"
    }
    assert refers_to_ids == {"1991/592"}


def test_successor_resolution_payload_does_not_select_future_successor() -> None:
    """A successor edge dated after successor_as_of is rejected, not selected."""
    edge = StatuteSuccessorEdge(
        predecessor_work_id="1991/592",
        successor_work_id="859/2018",
        effective_from=dt.date(2018, 12, 15),
        witness_id="finlex:1991/592:repealed-by:859/2018",
        witness_text="Tämä laki on kumottu lailla 859/2018.",
    )
    bundle = build_surface_bundle(
        _RADIATION_XML, "527/2014", surface_time="2017-01-01"
    )
    result = ReferenceLens().analyze(
        bundle,
        context=SurfaceAnalysisContext(
            surface_time="2017-01-01",
            options={
                "statute_registry": _StubStatuteRegistry({}),
                "successor_edges": (edge,),
                "successor_as_of": "2017-01-01",
            },
        ),
    )

    resolution = next(
        s for s in result.node_seeds if s.node_kind == "reference_resolution"
    )
    assert resolution.payload["work_id"] == "1991/592"
    assert resolution.payload["operative_work_id"] is None
    assert resolution.payload["successor_resolution_status"] == (
        "no_applicable_successor"
    )
    assert resolution.payload["successor_rejected_candidates"] == ["859/2018"]


def test_unit_open_interval_keeps_multitemporal_ambiguous() -> None:
    # SAME name + registry, but the unit window is open (None, None): every
    # version survives the (absent) instant -> stays ambiguous (has_candidate,
    # no refers_to). Fail-loud: an open window never guesses a version.
    import datetime as dt

    registry = _VersionedStubRegistry(
        {
            "lannoitelaki": [
                ("1990/111", dt.date(1990, 1, 1)),
                ("2010/222", dt.date(2010, 1, 1)),
            ]
        }
    )
    # default build_surface_bundle leaves effective_interval == (None, None)
    bundle = build_surface_bundle(_XML, _STATUTE_ID)
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(options={"statute_registry": registry})
    result = lens.analyze(bundle, context=ctx)

    has_candidate = [e for e in result.edge_seeds if e.edge_kind == "has_candidate"]
    candidate_ids = {e.payload.get("candidate_id") for e in has_candidate}
    assert {"1990/111", "2010/222"} <= candidate_ids
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    assert not any(
        e.payload.get("work_id") in {"1990/111", "2010/222"} for e in refers_to
    )


# ── Test doubles ─────────────────────────────────────────────────────────────


class _VersionedStubRegistry:
    """As-of-honouring StatuteNameRegistry stand-in (open-ended versions).

    Each version carries a ``valid_from`` and an open ``valid_to`` (like the real
    corpus artifact); ``lookup(name, as_of)`` filters to versions with
    ``valid_from <= as_of`` so a past instant narrows a multi-version name.
    """

    def __init__(self, table: dict[str, list[tuple[str, dt.date]]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: dt.date | None = None) -> _StubLookupResult:
        versions = self._table.get(name, [])
        if as_of is None:
            ids = [sid for sid, _vf in versions]
        else:
            ids = [sid for sid, vf in versions if vf <= as_of]
        return _StubLookupResult(ids)


class _StubLookupResult:
    def __init__(self, candidates: list[str]) -> None:
        if len(candidates) == 0:
            self.registry_status = "none"
        elif len(candidates) == 1:
            self.registry_status = "single"
        else:
            self.registry_status = "multiple"
        self.candidates = tuple(_StubCandidate(c) for c in candidates)


class _StubCandidate:
    def __init__(self, statute_id: str) -> None:
        self.statute_id = statute_id


class _StubStatuteRegistry:
    """Minimal StatuteNameRegistry stand-in for resolve_mentions routing."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: object = None) -> _StubLookupResult:
        return _StubLookupResult(self._table.get(name, []))
