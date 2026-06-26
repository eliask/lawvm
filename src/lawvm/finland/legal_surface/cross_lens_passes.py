"""EXPERIMENTAL cross-lens span-colocation edge passes (frame ↔ reference/temporal).

THESE ARE EXPERIMENTAL, CANDIDATE-STATUS AFFORDANCES — NOT settled semantics.

The Legal Surface Graph carries the H5/H6 frame families (delegation / procedure
/ sanction / exception / actor-modal) and the H1 ``reference_expr`` /
H3 ``temporal_expr`` surface facts as SEPARATE node sets with almost no edges
between them. Today you cannot ask "which provisions does this sanction frame
cite?" or "what deadline sits inside this delegation?" — the facts co-exist in
one graph but unlinked.

These two passes weave them together by *span containment* alone. Per Pro r5 §D5
the only place a cross-lens edge may be minted is an edge pass over the assembled
graph. Each edge is a CANDIDATE serendipity affordance (status ``"candidate"``,
never ``"asserted"``): it says ONLY "this reference/date span lies inside (or
within a small window of) this frame's span in the same source unit". It makes
NO legal claim that the frame governs the reference or that the date qualifies
the frame — that semantic conclusion would have to LEAVE this graph through a
named authorization/proof object (the authority firewall, §D7).

  * :class:`FrameReferenceColocationPass` joins a frame node to a
    ``reference_expr`` node → ``frame_contains_reference``.
  * :class:`FrameTemporalColocationPass` joins a frame node to a
    ``temporal_expr`` node → ``frame_qualified_by_temporal``.

DISCIPLINE
  * SPAN CONTAINMENT ONLY. An edge is minted only when both nodes carry a
    ``source_ref`` in the SAME ``source_unit_id`` and the child span lies inside
    the frame span (gap 0) or within ``window`` characters of it. A node without
    a ``source_ref`` (an entity handle) is skipped — no edge.
  * CANDIDATE status only, matching :class:`ActorTemporalColocationPass`.
  * DETERMINISTIC. Frame and child nodes are sorted by node_id before pairing;
    the gap is computed from raw span offsets; the same graph yields the same
    edge set (the assembler recomputes ``graph_id`` over the edge set).
  * SELF-EVIDENCING payload. Each edge carries the two node kinds and the overlap
    span offsets so a reader sees WHY the edge exists.
"""
from __future__ import annotations

from typing import Mapping

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed

# The H5/H6 frame-family node kinds these passes treat as "frames". Confirmed
# against the Finnish lens emitters (lenses/{delegation,procedure,sanction,
# exception_condition,actor_modal}.py): the exception lens emits the CUE kind
# ``exception_condition_cue`` (not a ``*_frame`` alias).
# A bare process/sanction noun is demoted to ``procedure_cue`` / ``sanction_cue``
# but anchors the SAME span the ``*_frame`` did; these colocation passes attach by
# SPAN PROXIMITY only, so including the cue kinds keeps the frame↔reference and
# frame↔temporal edge sets identical to before the demote.
FRAME_NODE_KINDS: tuple[str, ...] = (
    "actor_modal_frame",
    "delegation_frame",
    "exception_condition_cue",
    "procedure_frame",
    "procedure_cue",
    "sanction_frame",
    "sanction_cue",
)

# EXPERIMENTAL tunable: max character gap between the frame span and the child
# (reference/temporal) span, in the SAME source unit, for the two to count as
# "co-located". 0 = the child span lies inside / touches the frame span. This is
# a candidate-surfacing knob, NOT a semantic threshold.
#
# Why a non-zero default: the H5/H6 frame lenses anchor a frame to its narrow
# CUE span (the sanction verb, the delegation marker, the actor token) — NOT to
# the whole provision sentence. A reference or date that "sits inside the frame"
# in reading-terms therefore lands a few-dozen characters away from the cue
# span. The default mirrors :class:`ActorTemporalColocationPass` (120 chars):
# wide enough to capture a citation/date within the same provision sentence,
# tight enough not to bleed across provisions. The exact gap travels in the
# payload (``char_distance``), so a strict containment-only consumer can filter
# to ``char_distance == 0`` downstream without re-running the pass.
DEFAULT_CONTAINMENT_WINDOW = 120

PASS_ID_REFERENCE = "fi.frame_reference_colocation.v0"
RULE_FRAME_REFERENCE = "fi.frame_reference_colocation.frame_contains_reference"

PASS_ID_TEMPORAL = "fi.frame_temporal_colocation.v0"
RULE_FRAME_TEMPORAL = "fi.frame_temporal_colocation.frame_qualified_by_temporal"


def _span_gap(frame: SourceSpanRef, child: SourceSpanRef) -> int | None:
    """Character gap between a frame span and a child span in the SAME unit.

    Returns 0 when the child span lies inside / overlaps / touches the frame
    span; otherwise the number of characters separating the nearer edges.
    Different source units → ``None`` (no colocation).
    """
    if frame.source_unit_id != child.source_unit_id:
        return None
    if frame.char_end <= child.char_start:
        return child.char_start - frame.char_end
    if child.char_end <= frame.char_start:
        return frame.char_start - child.char_end
    return 0  # overlapping or nested


def _sorted_kind(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind == kind),
        key=lambda kv: kv[0],
    )


def _sorted_frames(
    nodes: Mapping[str, SurfaceNode],
) -> list[tuple[str, SurfaceNode]]:
    frame_kinds = frozenset(FRAME_NODE_KINDS)
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind in frame_kinds),
        key=lambda kv: kv[0],
    )


def _colocation_seeds(
    graph: LegalSurfaceGraph,
    *,
    child_kind: str,
    edge_kind: str,
    rule_id: str,
    window: int,
) -> tuple[SurfaceEdgeSeed, ...]:
    """Mint candidate frame↔child colocation edges by span containment.

    For every frame / child pair in the same source unit whose spans lie within
    ``window`` characters, emit ONE candidate edge carrying the gap and the two
    node kinds. Frames and children are iterated in node_id order for
    determinism. Nodes without a ``source_ref`` are skipped.
    """
    frame_nodes = _sorted_frames(graph.nodes)
    child_nodes = _sorted_kind(graph.nodes, child_kind)

    seeds: list[SurfaceEdgeSeed] = []
    for frame_id, frame in frame_nodes:
        f_ref = frame.source_ref
        if f_ref is None:
            continue
        for child_id, child in child_nodes:
            c_ref = child.source_ref
            if c_ref is None:
                continue
            gap = _span_gap(f_ref, c_ref)
            if gap is None or gap > window:
                continue
            seeds.append(
                SurfaceEdgeSeed(
                    edge_kind=edge_kind,
                    src_local=frame_id,
                    dst_local=child_id,
                    rule_id=rule_id,
                    # CANDIDATE, never asserted: a surface co-location affordance,
                    # not a settled fact (§D5). The frame does not "govern" the
                    # child — they merely share text.
                    surface_edge_status="candidate",
                    payload={
                        "char_distance": gap,
                        "window": window,
                        "frame_kind": frame.node_kind,
                        "child_kind": child.node_kind,
                        "frame_span": [f_ref.char_start, f_ref.char_end],
                        "child_span": [c_ref.char_start, c_ref.char_end],
                        "experimental": True,
                    },
                )
            )
    return tuple(seeds)


class FrameReferenceColocationPass:
    """EXPERIMENTAL SurfaceEdgePass: frame ↔ reference_expr span colocation.

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. For every
    frame node (delegation/procedure/sanction/exception/actor_modal) and
    ``reference_expr`` node in the SAME source unit whose spans lie within
    ``window`` characters, mints ONE candidate ``frame_contains_reference`` edge
    (status ``"candidate"``). Answers "which provisions does this frame cite?"
    as a SURFACE co-location affordance — no claim the frame governs the cite.
    """

    pass_id: str = PASS_ID_REFERENCE
    reads_node_kinds: tuple[str, ...] = FRAME_NODE_KINDS + ("reference_expr",)
    emits_edge_kinds: tuple[str, ...] = ("frame_contains_reference",)

    def __init__(self, *, window: int = DEFAULT_CONTAINMENT_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        return _colocation_seeds(
            graph,
            child_kind="reference_expr",
            edge_kind="frame_contains_reference",
            rule_id=RULE_FRAME_REFERENCE,
            window=self.window,
        )


class FrameTemporalColocationPass:
    """EXPERIMENTAL SurfaceEdgePass: frame ↔ temporal_expr span colocation.

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. For every
    frame node and ``temporal_expr`` node in the SAME source unit whose spans lie
    within ``window`` characters, mints ONE candidate
    ``frame_qualified_by_temporal`` edge (status ``"candidate"``). Answers "what
    date/deadline sits inside this frame?" as a SURFACE co-location affordance —
    no claim the date legally qualifies the frame.
    """

    pass_id: str = PASS_ID_TEMPORAL
    reads_node_kinds: tuple[str, ...] = FRAME_NODE_KINDS + ("temporal_expr",)
    emits_edge_kinds: tuple[str, ...] = ("frame_qualified_by_temporal",)

    def __init__(self, *, window: int = DEFAULT_CONTAINMENT_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        return _colocation_seeds(
            graph,
            child_kind="temporal_expr",
            edge_kind="frame_qualified_by_temporal",
            rule_id=RULE_FRAME_TEMPORAL,
            window=self.window,
        )


__all__ = [
    "DEFAULT_CONTAINMENT_WINDOW",
    "FRAME_NODE_KINDS",
    "FrameReferenceColocationPass",
    "FrameTemporalColocationPass",
    "PASS_ID_REFERENCE",
    "PASS_ID_TEMPORAL",
    "RULE_FRAME_REFERENCE",
    "RULE_FRAME_TEMPORAL",
]
