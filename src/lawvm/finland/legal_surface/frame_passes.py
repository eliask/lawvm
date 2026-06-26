"""EXPERIMENTAL cross-frame edge passes (H5/H6 affordances).

THESE ARE EXPERIMENTAL, CANDIDATE-STATUS AFFORDANCES — NOT settled semantics.

Per Pro r5 §D5 the only place a cross-lens / cross-frame edge may be minted is an
edge pass over the assembled graph. This module surfaces a *candidate* affordance
that some future analysis MIGHT exploit, purely for serendipity. It is
surface-only and makes NO legal conclusion: a colocation edge says "this
actor/modal clause sits near a temporal cue in the same source unit", nothing
about deadlines, obligations, or validity.

The single edge pass here is :class:`ActorTemporalColocationPass`. It joins an
``actor_modal_frame`` node to a ``temporal_expr`` node whose source spans lie
within a tunable character window in the SAME source unit. The edge status is
``"candidate"`` (never ``"asserted"``): cross-frame links are candidates, not
asserted facts. The character distance travels in the edge payload so a consumer
can rank/threshold candidates downstream.

Determinism: nodes are sorted by id before pairing; the window is a fixed
parameter; the gap is computed from raw span offsets. Same graph → same edges.
"""
from __future__ import annotations

from typing import Mapping

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed

PASS_ID = "fi.actor_temporal_colocation.v0"
RULE_COLOCATION = "fi.actor_temporal_colocation.actor_modal_temporal_colocated"

# EXPERIMENTAL tunable: max character gap between the actor/modal frame span and
# the temporal_expr span (within the same source unit) for the two to count as
# "co-located". Conservative default; this is a candidate-surfacing knob, not a
# semantic threshold. Touching spans / containment → gap 0.
DEFAULT_COLOCATION_WINDOW = 120


def _span_gap(a: SourceSpanRef, b: SourceSpanRef) -> int | None:
    """Character gap between two spans in the SAME source unit, else None.

    0 when the spans overlap or touch; otherwise the number of characters
    separating the nearer edges. Different source units → None (no colocation).
    """
    if a.source_unit_id != b.source_unit_id:
        return None
    if a.char_end <= b.char_start:
        return b.char_start - a.char_end
    if b.char_end <= a.char_start:
        return a.char_start - b.char_end
    return 0  # overlapping or nested


class ActorTemporalColocationPass:
    """EXPERIMENTAL SurfaceEdgePass: actor_modal_frame ↔ temporal_expr colocation.

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. For every
    ``actor_modal_frame`` / ``temporal_expr`` pair in the same source unit whose
    spans lie within ``window`` characters, it mints ONE candidate
    ``actor_modal_temporal_colocated`` edge (status ``"candidate"``) carrying the
    character distance. Surface-only; no legal conclusion.
    """

    pass_id: str = PASS_ID
    reads_node_kinds: tuple[str, ...] = ("actor_modal_frame", "temporal_expr")
    emits_edge_kinds: tuple[str, ...] = ("actor_modal_temporal_colocated",)

    def __init__(self, *, window: int = DEFAULT_COLOCATION_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        nodes = graph.nodes
        actor_nodes = _sorted_kind(nodes, "actor_modal_frame")
        temporal_nodes = _sorted_kind(nodes, "temporal_expr")

        seeds: list[SurfaceEdgeSeed] = []
        for actor_id, actor in actor_nodes:
            a_ref = actor.source_ref
            if a_ref is None:
                continue
            for temporal_id, temporal in temporal_nodes:
                t_ref = temporal.source_ref
                if t_ref is None:
                    continue
                gap = _span_gap(a_ref, t_ref)
                if gap is None or gap > self.window:
                    continue
                seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="actor_modal_temporal_colocated",
                        src_local=actor_id,
                        dst_local=temporal_id,
                        rule_id=RULE_COLOCATION,
                        # CANDIDATE, never asserted: this is an experimental
                        # affordance, not a settled fact (§D5).
                        surface_edge_status="candidate",
                        payload={
                            "char_distance": gap,
                            "window": self.window,
                            "experimental": True,
                        },
                    )
                )
        return tuple(seeds)


def _sorted_kind(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind == kind),
        key=lambda kv: kv[0],
    )


__all__ = [
    "ActorTemporalColocationPass",
    "DEFAULT_COLOCATION_WINDOW",
    "PASS_ID",
    "RULE_COLOCATION",
]
