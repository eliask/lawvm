"""EXPERIMENTAL cross-FRAME relation edge passes + a frame-pairing lint.

THESE ARE EXPERIMENTAL, CANDIDATE-STATUS AFFORDANCES — NOT settled semantics.

The sibling :mod:`lawvm.finland.legal_surface.cross_lens_passes` weaves the
H5/H6 frame families to the H1 ``reference_expr`` / H3 ``temporal_expr`` surface
facts (frame↔reference, frame↔temporal COLOCATION). THIS module weaves the frame
families to EACH OTHER and to the actor/modal frame — the frame↔frame and
frame↔actor structural co-locations that the graph currently lacks. Today the
``exception_condition_cue`` / ``delegation_frame`` / ``procedure_frame`` /
``sanction_frame`` / ``actor_modal_frame`` nodes coexist in one graph with almost
no edges between them; you cannot ask "which frame does this exception cue
qualify?" or "who acts in/near this sanction frame?".

Per Pro r5 §D5 the only place a cross-lens edge may be minted is an edge pass
over the assembled graph. Each edge here is a CANDIDATE serendipity affordance
(status ``"candidate"``, never ``"asserted"``): it says ONLY "these two frame
spans co-locate (the cue precedes/overlaps the frame, or the actor sits in/near
the frame) in the same source unit". It makes NO legal claim that the exception
legally governs the frame, nor that the actor is the legal subject of the frame
— that semantic conclusion would have to LEAVE this graph through a named
authorization/proof object (the authority firewall, §D7).

  * :class:`ExceptionScopesFramePass` joins an ``exception_condition_cue`` node
    to a frame it PRECEDES/overlaps → ``exception_scopes_frame``.
  * :class:`FrameActorColocationPass` joins a frame node to a co-located
    ``actor_modal_frame`` → ``frame_has_colocated_actor``.
  * :class:`SanctionConditionLintPass` (graph query, §D6) flags a
    ``sanction_frame`` whose source unit carries NO condition/exception cue
    → ``sanction.without_colocated_condition`` (info severity, surface-only).

DISCIPLINE
  * SPAN CONTAINMENT ONLY. An edge is minted only when both nodes carry a
    ``source_ref`` in the SAME ``source_unit_id`` and their spans co-locate
    within ``window`` characters. A node without a ``source_ref`` (an entity
    handle) is skipped — no edge.
  * CANDIDATE status only, matching :class:`FrameReferenceColocationPass`.
  * DIRECTIONAL for the exception pass: only an exception cue whose span starts
    at or before the frame (precedes) OR overlaps the frame qualifies — a cue
    that lies AFTER the frame is not "scoping" it on the surface, so no edge.
  * DETERMINISTIC. Nodes are sorted by node_id before pairing; the gap is
    computed from raw span offsets; the same graph yields the same edge/lint set
    (the assembler recomputes ``graph_id`` over the edge set).
  * SELF-EVIDENCING payload/message. Each edge carries the two node kinds and the
    overlap span offsets; the lint message embeds the sanction surface so a
    reader sees WHY it exists.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed
from lawvm.core.legal_surface_lints import SurfaceLint

JURISDICTION = "fi"

# The actual H5/H6 frame-family node kinds the Finnish lenses emit. Confirmed
# against the lens emitters (lenses/{delegation,procedure,sanction,actor_modal}.py
# emit ``*_frame``; lenses/exception_condition.py emits the CUE kind
# ``exception_condition_cue`` — NOT a ``*_frame`` alias). FRAME_KINDS excludes the
# exception cue: it is the SOURCE of the exception edge, not a target frame.
# A bare process/sanction noun is demoted to a ``*_cue`` kind (no actor/deadline,
# no target/trigger), but it carries the SAME span the ``*_frame`` did, and these
# passes attach edges by SPAN PROXIMITY only — never reading the frame's own
# actor/deadline payload. Including the cue kinds here keeps the proximity-edge /
# lint behaviour identical to before the demote.
FRAME_KINDS: tuple[str, ...] = (
    "actor_modal_frame",
    "delegation_frame",
    "procedure_frame",
    "procedure_cue",
    "sanction_frame",
    "sanction_cue",
)
EXCEPTION_CUE_KIND = "exception_condition_cue"
ACTOR_FRAME_KIND = "actor_modal_frame"
# The sanction-family kinds the without-condition lint observes. A bare sanction
# noun is now ``sanction_cue`` but is the SAME surface fact the lint observed when
# it was a ``sanction_frame`` — keep both so the lint's subject set is unchanged.
SANCTION_KINDS: frozenset[str] = frozenset({"sanction_frame", "sanction_cue"})

# EXPERIMENTAL tunable: max character gap between two co-located frame spans in
# the SAME source unit. Mirrors the sibling cross_lens_passes window (the frame
# lenses anchor to a narrow CUE span, not the whole sentence, so a non-zero
# default captures two frames within the same provision sentence). The exact gap
# travels in the payload (``char_distance``); a strict containment-only consumer
# can filter ``char_distance == 0`` downstream without re-running the pass.
DEFAULT_CONTAINMENT_WINDOW = 120

PASS_ID_EXCEPTION = "fi.exception_scopes_frame.v0"
RULE_EXCEPTION = "fi.exception_scopes_frame.exception_scopes_frame"

PASS_ID_FRAME_ACTOR = "fi.frame_actor_colocation.v0"
RULE_FRAME_ACTOR = "fi.frame_actor_colocation.frame_has_colocated_actor"

LINT_SANCTION_WITHOUT_CONDITION = "sanction.without_colocated_condition"
RULE_SANCTION_WITHOUT_CONDITION = (
    "fi.lint.sanction.without_colocated_condition"
)

# The legal readings the sanction lint must NEVER be mistaken for (§D6). A
# sanction provision carrying no co-located condition cue ON THE SURFACE is a
# drafting-surface observation, not a verdict on the sanction's validity.
_SANCTION_FORBIDDEN_OVERCLAIMS: tuple[str, ...] = (
    "this sanction is unconditional / strict-liability as a matter of law",
    "the sanction lacks a legally required precondition",
    "the statute is legally defective",
    "any legal consequence follows",
)


def _span_gap(a: SourceSpanRef, b: SourceSpanRef) -> int | None:
    """Character gap between two spans in the SAME unit.

    Returns 0 when the spans overlap / touch; otherwise the number of characters
    separating their nearer edges. Different source units → ``None``.
    """
    if a.source_unit_id != b.source_unit_id:
        return None
    if a.char_end <= b.char_start:
        return b.char_start - a.char_end
    if b.char_end <= a.char_start:
        return a.char_start - b.char_end
    return 0  # overlapping or nested


def _precedes_or_overlaps(cue: SourceSpanRef, frame: SourceSpanRef) -> bool:
    """True iff the cue span starts at/before the frame (precedes) or overlaps it.

    A cue strictly AFTER the frame (cue.char_start > frame.char_end) does not
    "scope" the frame on the surface, so it earns no edge.
    """
    return cue.char_start <= frame.char_end


def _sorted_kind(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind == kind),
        key=lambda kv: kv[0],
    )


def _sorted_kinds(
    nodes: Mapping[str, SurfaceNode], kinds: frozenset[str]
) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind in kinds),
        key=lambda kv: kv[0],
    )


class ExceptionScopesFramePass:
    """EXPERIMENTAL SurfaceEdgePass: exception_condition_cue ↔ frame colocation.

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. For every
    ``exception_condition_cue`` and frame node (delegation/procedure/sanction/
    actor_modal) in the SAME source unit, where the cue span PRECEDES or overlaps
    the frame within ``window`` characters, mints ONE candidate
    ``exception_scopes_frame`` edge (status ``"candidate"``). Answers "which frame
    does this exception/condition cue qualify?" as a SURFACE co-location
    affordance — no claim the exception legally governs the frame.
    """

    pass_id: str = PASS_ID_EXCEPTION
    reads_node_kinds: tuple[str, ...] = (EXCEPTION_CUE_KIND,) + FRAME_KINDS
    emits_edge_kinds: tuple[str, ...] = ("exception_scopes_frame",)

    def __init__(self, *, window: int = DEFAULT_CONTAINMENT_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        cues = _sorted_kind(graph.nodes, EXCEPTION_CUE_KIND)
        frames = _sorted_kinds(graph.nodes, frozenset(FRAME_KINDS))

        seeds: list[SurfaceEdgeSeed] = []
        for cue_id, cue in cues:
            c_ref = cue.source_ref
            if c_ref is None:
                continue
            for frame_id, frame in frames:
                f_ref = frame.source_ref
                if f_ref is None:
                    continue
                gap = _span_gap(c_ref, f_ref)
                if gap is None or gap > self.window:
                    continue
                if not _precedes_or_overlaps(c_ref, f_ref):
                    continue
                seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="exception_scopes_frame",
                        src_local=cue_id,
                        dst_local=frame_id,
                        rule_id=RULE_EXCEPTION,
                        # CANDIDATE, never asserted: a surface co-location
                        # affordance, not a settled fact (§D5). The exception does
                        # not "govern" the frame — they merely share text and the
                        # cue precedes/overlaps the frame.
                        status="candidate",
                        payload={
                            "char_distance": gap,
                            "window": self.window,
                            "cue_kind": cue.node_kind,
                            "frame_kind": frame.node_kind,
                            "cue_span": [c_ref.char_start, c_ref.char_end],
                            "frame_span": [f_ref.char_start, f_ref.char_end],
                            "experimental": True,
                        },
                    )
                )
        return tuple(seeds)


class FrameActorColocationPass:
    """EXPERIMENTAL SurfaceEdgePass: frame ↔ actor_modal_frame colocation.

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. For every
    frame node (delegation/procedure/sanction) and ``actor_modal_frame`` in the
    SAME source unit whose spans lie within ``window`` characters, mints ONE
    candidate ``frame_has_colocated_actor`` edge (status ``"candidate"``).
    Answers "who acts in/near this frame?" as a SURFACE co-location affordance —
    no claim the actor is the legal subject of the frame.

    The Finnish actor/modal lens emits NO standalone actor node — the actor shape
    lives inside the ``actor_modal_frame`` node — so the co-located target is the
    ``actor_modal_frame`` itself. A frame is never paired with itself, and two
    ``actor_modal_frame`` nodes are not paired (the source frame must be a
    delegation/procedure/sanction frame).
    """

    pass_id: str = PASS_ID_FRAME_ACTOR
    reads_node_kinds: tuple[str, ...] = FRAME_KINDS
    emits_edge_kinds: tuple[str, ...] = ("frame_has_colocated_actor",)

    # Source frames are the non-actor frames; the target is the actor_modal_frame.
    _SOURCE_FRAME_KINDS = frozenset(
        k for k in FRAME_KINDS if k != ACTOR_FRAME_KIND
    )

    def __init__(self, *, window: int = DEFAULT_CONTAINMENT_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        frames = _sorted_kinds(graph.nodes, self._SOURCE_FRAME_KINDS)
        actors = _sorted_kind(graph.nodes, ACTOR_FRAME_KIND)

        seeds: list[SurfaceEdgeSeed] = []
        for frame_id, frame in frames:
            f_ref = frame.source_ref
            if f_ref is None:
                continue
            for actor_id, actor in actors:
                a_ref = actor.source_ref
                if a_ref is None:
                    continue
                gap = _span_gap(f_ref, a_ref)
                if gap is None or gap > self.window:
                    continue
                seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="frame_has_colocated_actor",
                        src_local=frame_id,
                        dst_local=actor_id,
                        rule_id=RULE_FRAME_ACTOR,
                        # CANDIDATE, never asserted: a surface co-location
                        # affordance, not a settled fact (§D5). The actor is not
                        # claimed to be the frame's legal subject — the spans
                        # merely share text.
                        status="candidate",
                        payload={
                            "char_distance": gap,
                            "window": self.window,
                            "frame_kind": frame.node_kind,
                            "actor_kind": actor.node_kind,
                            "frame_span": [f_ref.char_start, f_ref.char_end],
                            "actor_span": [a_ref.char_start, a_ref.char_end],
                            "experimental": True,
                        },
                    )
                )
        return tuple(seeds)


def _mint_lint_id(*parts: str) -> str:
    """Deterministic lint id over the lint kind + its subject node id."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _refs_of(*nodes: SurfaceNode) -> tuple[SourceSpanRef, ...]:
    return tuple(n.source_ref for n in nodes if n.source_ref is not None)


def _sanction_label(node: SurfaceNode) -> str:
    """Self-evidencing sanction surface for the message."""
    for key in ("marker_surface", "sanction_kind"):
        value = node.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return node.node_id


class SanctionConditionLintPass:
    """EXPERIMENTAL ``sanction.without_colocated_condition`` (info severity).

    Implements ``lawvm.core.legal_surface_lints.SurfaceLintPass``. Flags a
    ``sanction_frame`` that has NO ``exception_condition_cue`` CO-LOCATED within
    ``window`` characters in the same source unit. Surface-only, never a legal
    conclusion (see ``forbidden_overclaims``). Conservative: a sanction frame
    without a ``source_ref`` (an entity handle) is skipped, and the lint fires
    only when no cue lies within the window — never guessing a missing
    precondition.

    SPAN co-location (not unit co-location): the Finnish graph builds one source
    unit for the whole body, so "any cue anywhere in the unit" would be far too
    coarse to be a meaningful per-sanction observation. The lint therefore uses
    the same span-window proximity the relation passes use.
    """

    lint_pass_id: str = "fi.lint.sanction.without_colocated_condition"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def __init__(self, *, window: int = DEFAULT_CONTAINMENT_WINDOW) -> None:
        self.window = window

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        cue_refs = tuple(
            n.source_ref
            for n in graph.nodes.values()
            if n.node_kind == EXCEPTION_CUE_KIND and n.source_ref is not None
        )

        sanctions = sorted(
            (
                (nid, n)
                for nid, n in graph.nodes.items()
                if n.node_kind in SANCTION_KINDS
            ),
            key=lambda kv: kv[0],
        )

        lints: list[SurfaceLint] = []
        for frame_id, frame in sanctions:
            ref = frame.source_ref
            if ref is None:
                continue  # entity handle, no span to anchor the observation
            has_nearby_cue = any(
                (gap := _span_gap(ref, cue)) is not None and gap <= self.window
                for cue in cue_refs
            )
            if has_nearby_cue:
                continue  # a condition/exception cue is co-located by span
            label = _sanction_label(frame)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(
                        LINT_SANCTION_WITHOUT_CONDITION, frame_id
                    ),
                    lint_kind=LINT_SANCTION_WITHOUT_CONDITION,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_SANCTION_WITHOUT_CONDITION,
                    severity="info",
                    subject_node_id=frame_id,
                    support_node_ids=(),
                    source_refs=_refs_of(frame),
                    message=(
                        "EXPERIMENTAL surface affordance: sanction frame "
                        f"{label!r} has no condition/exception cue co-located "
                        f"within {self.window} chars in its source unit (no "
                        "nearby exception_condition_cue). Surface observation "
                        "only; NOT a legal conclusion."
                    ),
                    lint_status="active",
                    forbidden_overclaims=_SANCTION_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


__all__ = [
    "ACTOR_FRAME_KIND",
    "DEFAULT_CONTAINMENT_WINDOW",
    "EXCEPTION_CUE_KIND",
    "ExceptionScopesFramePass",
    "FRAME_KINDS",
    "FrameActorColocationPass",
    "LINT_SANCTION_WITHOUT_CONDITION",
    "PASS_ID_EXCEPTION",
    "PASS_ID_FRAME_ACTOR",
    "RULE_EXCEPTION",
    "RULE_FRAME_ACTOR",
    "RULE_SANCTION_WITHOUT_CONDITION",
    "SANCTION_KINDS",
    "SanctionConditionLintPass",
]
