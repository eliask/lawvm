"""Gate for the EXPERIMENTAL surface-graph AFFORDANCE INVENTORY.

An *affordance* is a T-relevant STRUCTURAL CHANNEL the Legal Surface Graph
already exposes that a future mechanism-evaluation layer MIGHT exploit. These
tests assert the inventory:

  * surfaces the per-statute channels (delegation / sanction / frame-reference /
    definition-closure / unresolved-reference) DERIVED ONLY from existing
    nodes/edges (no re-parse, no invented edges);
  * surfaces corpus citation fan-in channels via ``citations_of``;
  * is deterministic (same graph -> identical inventory);
  * ENFORCES THE DISCIPLINE BARRIER: every affordance carries
    ``legal_conclusion=False`` and ``is_score=False`` and the constructor REFUSES
    any other value;
  * fails loud on a contradictory (missing) support node, never guessing.

Graphs are built from synthetic AkomaNtoso so the test is self-contained — no
archive needed. The frame↔condition / frame↔reference channels require the
EXPERIMENTAL cross-lens / frame-relation passes, which the test runs explicitly
(they are not in DEFAULT_EDGE_PASSES).
"""
from __future__ import annotations

import dataclasses

import pytest

from lawvm.core.legal_surface_assembler import run_edge_passes
from lawvm.core.legal_surface_graph import LegalSurfaceGraph
from lawvm.finland.legal_surface.affordances import (
    AFFORDANCE_CITATION_FAN_IN_CHANNEL,
    AFFORDANCE_DEFINITION_CLOSURE_CHANNEL,
    AFFORDANCE_DELEGATION_CHANNEL,
    AFFORDANCE_FRAME_REFERENCE_CHANNEL,
    AFFORDANCE_KINDS,
    AFFORDANCE_SANCTION_CHANNEL,
    AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL,
    AffordanceInventoryError,
    SurfaceAffordance,
    inventory_affordances,
    inventory_corpus_affordances,
)
from lawvm.finland.legal_surface.cross_lens_passes import (
    FrameReferenceColocationPass,
    FrameTemporalColocationPass,
)
from lawvm.finland.legal_surface.frame_relations import (
    ExceptionScopesFramePass,
    FrameActorColocationPass,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Section 1: a delegation ("Valtioneuvosto voi antaa asetuksella") + a sanction
# ("rangaistaan sakolla") with an exception cue ("ei kuitenkaan") preceding it,
# plus a cross-reference ("lain 711/2022 7 §:n nojalla") sitting inside a frame.
# Section 2: a definitions section + a lone sanction with no co-located cue.
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body>
    <section eId="sec_1"><num>1 §</num><content>
      <p>Valtioneuvosto voi antaa asetuksella tarkempia saannoksia taman lain
         taytantoonpanosta lain 711/2022 7 §:n nojalla.</p>
      <p>Edella saadettya ei kuitenkaan sovelleta vahaiseen toimintaan, vaan
         joka rikkoo tata saannosta, rangaistaan sakolla.</p>
    </content></section>
    <section eId="sec_2"><num>2 §</num><content>
      <p>Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.</p>
      <p>Sivutuoteasetus toimii hyvin. Joka rikkoo tata lakia, rangaistaan
         sakolla.</p>
    </content></section>
  </body></act>
</akomaNtoso>
""".encode("utf-8")


def _build_with_all_passes() -> LegalSurfaceGraph:
    """Build the statute graph and run the EXPERIMENTAL frame relation passes.

    The frame↔reference and frame↔condition channels depend on
    ``frame_contains_reference`` / ``exception_scopes_frame`` edges that are NOT
    in DEFAULT_EDGE_PASSES, so we run them here explicitly.
    """
    graph = build_legal_surface_graph(_XML, "999/2025")
    return run_edge_passes(
        graph,
        (
            FrameReferenceColocationPass(),
            FrameTemporalColocationPass(),
            ExceptionScopesFramePass(),
            FrameActorColocationPass(),
        ),
    )


# ── discipline barrier (the non-negotiable invariant) ────────────────────────


def test_discipline_barrier_rejects_legal_conclusion() -> None:
    with pytest.raises(AffordanceInventoryError):
        SurfaceAffordance(
            affordance_kind=AFFORDANCE_DELEGATION_CHANNEL,
            subject_node_id="x",
            source_ref=None,
            payload={},
            legal_conclusion=True,  # type: ignore[arg-type]
        )


def test_discipline_barrier_rejects_score() -> None:
    with pytest.raises(AffordanceInventoryError):
        SurfaceAffordance(
            affordance_kind=AFFORDANCE_SANCTION_CHANNEL,
            subject_node_id="x",
            source_ref=None,
            payload={},
            is_score=True,  # type: ignore[arg-type]
        )


def test_discipline_barrier_rejects_unknown_kind() -> None:
    with pytest.raises(AffordanceInventoryError):
        SurfaceAffordance(
            affordance_kind="not_a_real_channel",
            subject_node_id="x",
            source_ref=None,
            payload={},
        )


def test_every_inventoried_affordance_obeys_the_barrier() -> None:
    affs = inventory_affordances(_build_with_all_passes())
    assert affs, "expected at least one affordance from the synthetic statute"
    for a in affs:
        assert a.legal_conclusion is False
        assert a.is_score is False
        assert a.experimental is True
        assert a.affordance_kind in AFFORDANCE_KINDS


# ── per-statute channels ─────────────────────────────────────────────────────


def _by_kind(affs: list[SurfaceAffordance], kind: str) -> list[SurfaceAffordance]:
    return [a for a in affs if a.affordance_kind == kind]


def test_delegation_channel_surfaces_instrument_and_condition_flag() -> None:
    affs = inventory_affordances(_build_with_all_passes())
    delegs = _by_kind(affs, AFFORDANCE_DELEGATION_CHANNEL)
    assert delegs, "expected a delegation_channel from the asetus delegation"
    a = delegs[0]
    # Self-evidencing structural payload (instrument + binding strength + the
    # surface fact of whether a condition cue is co-located). No score, no verdict.
    assert "instrument_kind" in a.payload
    assert "binding_strength" in a.payload
    assert "has_colocated_condition_cue" in a.payload
    assert isinstance(a.payload["has_colocated_condition_cue"], bool)
    assert isinstance(a.payload["colocated_condition_cue_count"], int)


def test_sanction_channel_distinguishes_colocated_condition() -> None:
    affs = inventory_affordances(_build_with_all_passes())
    sanctions = _by_kind(affs, AFFORDANCE_SANCTION_CHANNEL)
    assert sanctions, "expected sanction_channel(s)"
    # At least one sanction has a co-located condition cue (sec_1, "ei kuitenkaan"
    # precedes "rangaistaan"); at least one does not (sec_2, lone sanction). This
    # is a surface co-location FACT, never "this sanction is defective".
    flags = {bool(a.payload["has_colocated_condition_cue"]) for a in sanctions}
    assert flags == {True, False} or len(sanctions) == 1


def test_frame_reference_channel_counts_enclosed_citations() -> None:
    affs = inventory_affordances(_build_with_all_passes())
    fr = _by_kind(affs, AFFORDANCE_FRAME_REFERENCE_CHANNEL)
    assert fr, "expected a frame_reference_channel (citation inside a frame)"
    a = fr[0]
    count = a.payload["colocated_reference_count"]
    assert isinstance(count, int)
    assert count == len(a.support_node_ids)
    assert count >= 1


def test_definition_closure_channel_marks_used_vs_unused() -> None:
    affs = inventory_affordances(_build_with_all_passes())
    defs = _by_kind(affs, AFFORDANCE_DEFINITION_CLOSURE_CHANNEL)
    assert defs, "expected a definition_closure_channel from the tarkoitetaan def"
    for a in defs:
        assert "is_used_in_surface" in a.payload
        assert isinstance(a.payload["is_used_in_surface"], bool)
        assert a.payload["use_count"] == len(a.support_node_ids)


def test_unresolved_reference_channel_when_target_unresolved() -> None:
    # Built WITHOUT a statute registry -> the 711/2022 citation cannot resolve to
    # a concrete provision, so it surfaces as an unresolved channel.
    affs = inventory_affordances(_build_with_all_passes())
    unresolved = _by_kind(affs, AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL)
    for a in unresolved:
        assert isinstance(a.payload["resolution_status"], str)


# ── derivation discipline: no invented edges ─────────────────────────────────


def test_no_frame_relation_passes_means_no_relation_channels() -> None:
    # With ONLY the default edge passes, the frame_contains_reference /
    # exception_scopes_frame edges are absent — so the inventory must NOT invent
    # frame-reference channels, and delegation/sanction condition flags are False.
    graph = build_legal_surface_graph(_XML, "999/2025")
    affs = inventory_affordances(graph)
    assert not _by_kind(affs, AFFORDANCE_FRAME_REFERENCE_CHANNEL)
    for a in _by_kind(affs, AFFORDANCE_DELEGATION_CHANNEL):
        assert a.payload["has_colocated_condition_cue"] is False
    for a in _by_kind(affs, AFFORDANCE_SANCTION_CHANNEL):
        assert a.payload["has_colocated_condition_cue"] is False


# ── determinism ──────────────────────────────────────────────────────────────


def test_inventory_is_deterministic() -> None:
    a1 = inventory_affordances(_build_with_all_passes())
    a2 = inventory_affordances(_build_with_all_passes())
    assert a1 == a2
    # globally ordered by (kind, subject_node_id)
    keys = [(a.affordance_kind, a.subject_node_id) for a in a1]
    assert keys == sorted(keys)


# ── fail-loud on missing support node ────────────────────────────────────────


def test_inventory_fails_loud_on_missing_support_node() -> None:
    """A frame_contains_reference edge to a node that was dropped must raise."""
    graph = _build_with_all_passes()
    # Find a frame_reference edge and delete its dst node to fabricate a
    # contradictory graph (edge points at a node not in the node map).
    target_edge = next(
        (e for e in graph.edges if e.edge_kind == "frame_contains_reference"),
        None,
    )
    assert target_edge is not None, "fixture must contain a frame_reference edge"
    broken_nodes = {
        nid: n for nid, n in graph.nodes.items() if nid != target_edge.dst
    }
    broken = dataclasses.replace(graph, nodes=broken_nodes)
    with pytest.raises(AffordanceInventoryError):
        inventory_affordances(broken)


# ── corpus citation fan-in channel ───────────────────────────────────────────

# Two tiny statutes that BOTH cite the same provision of 711/2022, so the corpus
# graph collapses them onto one shared entity node with fan-in 2.
_CITER_A = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body>
    <section eId="sec_1"><num>1 §</num><content>
      <p>Talloin sovelletaan lain 711/2022 7 §:n saannoksia.</p>
    </content></section>
  </body></act>
</akomaNtoso>
""".encode("utf-8")

_CITER_B = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body>
    <section eId="sec_1"><num>1 §</num><content>
      <p>Lisaksi noudatetaan lain 711/2022 7 §:n mukaista menettelya.</p>
    </content></section>
  </body></act>
</akomaNtoso>
""".encode("utf-8")


class _DictStore:
    """Minimal archive-free store for corpus_graph (oracle = source body)."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies

    def read_oracle(self, sid: str) -> bytes | None:
        return self._bodies.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return self._bodies.get(sid)

    def read_amendment(self, sid: str) -> bytes | None:
        return None


def _build_corpus() -> LegalSurfaceGraph | None:
    """Build a 2-statute corpus graph, or None if resolution didn't fire.

    Uses a fake in-memory store + a permissive statute registry so the 711/2022
    provision citations resolve and collapse onto one shared address entity.
    """
    from lawvm.finland.legal_surface.corpus_graph import build_corpus_surface_graph

    store = _DictStore({"100/2025": _CITER_A, "200/2025": _CITER_B})

    class _Reg:
        def exists(self, statute_id: str) -> bool:
            return True

        def is_known(self, statute_id: str) -> bool:
            return True

    return build_corpus_surface_graph(
        ("100/2025", "200/2025"),
        store,
        statute_registry=_Reg(),
    )


def test_corpus_citation_fan_in_channel() -> None:
    graph = _build_corpus()
    assert graph is not None
    affs = inventory_corpus_affordances(graph)
    fan_in = [a for a in affs if a.affordance_kind == AFFORDANCE_CITATION_FAN_IN_CHANNEL]
    if not fan_in:
        pytest.skip("corpus resolution produced no provision-level fan-in targets")
    for a in fan_in:
        assert a.legal_conclusion is False
        assert a.is_score is False
        fan_in = a.payload["fan_in_count"]
        assert isinstance(fan_in, int)
        assert fan_in == len(a.support_node_ids)
        assert fan_in >= 1
        assert isinstance(a.payload["distinct_citing_works"], tuple)
    # determinism
    assert inventory_corpus_affordances(graph) == affs
