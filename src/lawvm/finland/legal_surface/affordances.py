"""EXPERIMENTAL read-only AFFORDANCE INVENTORY over the Legal Surface Graph.

THIS IS EXPERIMENTAL / SERENDIPITY SCAFFOLDING — NOT a settled analysis layer.

An *affordance* is a T-relevant STRUCTURAL CHANNEL the surface graph already
exposes that some future mechanism-evaluation layer (MeVM) MIGHT exploit. We do
not yet know what MeVM will be; the value here is making the graph's exploitable
structure LEGIBLE as typed surface facts, so future work can discover affordances
without re-deriving them. A delegation frame that names an instrument but carries
no co-located condition cue, a provision cited by N other statutes, a defined
term never used — these are *channels*, not verdicts.

WHAT THIS IS NOT (the discipline barrier, non-negotiable):

  * NOT a legal conclusion. Every :class:`SurfaceAffordance` carries
    ``legal_conclusion=False`` and the constructor REFUSES any other value.
  * NOT a score. ``is_score=False`` is likewise enforced in ``__post_init__``.
    We never grade magnitude ("too much delegation"), never assert a "right"
    threshold, never rank channels. An affordance says only "this structural
    channel is PRESENT with these attributes".
  * NOT a re-parse. Every affordance is derived ONLY from nodes/edges already in
    the assembled graph. This module invents no edges and reads no source text
    beyond the self-evidencing payload the lenses already attached.

Each affordance is SELF-EVIDENCING: its ``payload`` carries the structural facts
(node kinds, edge kinds, counts, span offsets, the marker surfaces the lenses
recorded) that justify why the channel was inventoried, so a reader can audit it
from the record alone.

FAIL-LOUD: if an affordance derivation references a node id that is not in the
graph, it raises :class:`AffordanceInventoryError` rather than guessing.

Two entry points:

  * :func:`inventory_affordances` — over ONE statute's surface graph
    (delegation / sanction / frame-reference / definition-closure / unresolved
    reference channels).
  * :func:`inventory_corpus_affordances` — over a CROSS-statute corpus graph
    (citation fan-in channels via ``citations_of``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.finland.legal_surface.corpus_graph import citations_of

JURISDICTION = "fi"


class AffordanceInventoryError(RuntimeError):
    """Raised when an affordance derivation hits a missing/contradictory node.

    Fail-loud rather than guessing: an affordance that names a support node id
    not present in the graph is a contradiction (the graph changed under us), so
    we refuse to mint it.
    """


# ── Closed affordance vocabulary ─────────────────────────────────────────────
#
# Each kind is a STRUCTURAL CHANNEL present in the graph, NOT a defect and NOT a
# score. The vocabulary is closed: a new channel kind is a deliberate addition,
# not a free-form string.

# A delegation_frame: power is delegated via a named instrument. Payload carries
# the instrument_kind + binding_strength the lens recorded, and whether any
# condition/exception cue is co-located (derived from exception_scopes_frame
# edges already in the graph — NOT re-parsed). "Delegation channel present with
# these attributes", never "too much delegation".
AFFORDANCE_DELEGATION_CHANNEL = "delegation_channel"

# A sanction_frame and whether a condition/exception cue is co-located with it
# (derived from exception_scopes_frame edges). "Sanction channel present; a
# condition cue is / is not co-located", never "this sanction is defective".
AFFORDANCE_SANCTION_CHANNEL = "sanction_channel"

# A frame node (delegation/procedure/sanction/exception/actor_modal) that has one
# or more reference_expr nodes co-located inside it (frame_contains_reference
# edges). "This frame's text encloses N citation(s)" — a candidate channel where
# a frame and a cross-reference share text, never a claim the frame governs them.
AFFORDANCE_FRAME_REFERENCE_CHANNEL = "frame_reference_channel"

# A definition_binding and whether the defined term is used anywhere in this
# surface (incoming uses_term / term_use_resolves_to edges). "Definition closure
# channel: defined term, used / unused in this surface" — never "dead code is
# bad". Distinct from the dead_definition LINT (a defect framing); here it is
# just the closure structure exposed for downstream exploitation.
AFFORDANCE_DEFINITION_CLOSURE_CHANNEL = "definition_closure_channel"

# A reference_resolution whose status is open / ambiguous / broken / statute_only
# — a cross-reference channel that does NOT land on a single concrete target.
# "This citation is unresolved/ambiguous on the surface" — never "the citation is
# wrong". A downstream layer might exploit unresolved channels (e.g. to ask the
# oracle, or to flag where the surface graph is blind).
AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL = "unresolved_reference_channel"

# CORPUS-LEVEL: a target entity (act or provision) cited by one or more citing
# statutes. Payload carries the fan-in count + the distinct citing works.
# "This provision is cited by N statutes" — a structural fan-in channel, never
# "this provision is important / load-bearing" (that would be a score).
AFFORDANCE_CITATION_FAN_IN_CHANNEL = "citation_fan_in_channel"

AFFORDANCE_KINDS: frozenset[str] = frozenset(
    {
        AFFORDANCE_DELEGATION_CHANNEL,
        AFFORDANCE_SANCTION_CHANNEL,
        AFFORDANCE_FRAME_REFERENCE_CHANNEL,
        AFFORDANCE_DEFINITION_CLOSURE_CHANNEL,
        AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL,
        AFFORDANCE_CITATION_FAN_IN_CHANNEL,
    }
)

# reference_resolution statuses that count as "unresolved channels" — anything
# that does NOT land on one concrete resolved target. ``statute_only`` is
# included: the citation names an act but no provision, so the provision-level
# channel is unresolved on the surface.
_UNRESOLVED_STATUSES: frozenset[str] = frozenset(
    {"open", "ambiguous", "broken", "statute_only", "unsupported"}
)


# ── The typed affordance record ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceAffordance:
    """One inventoried STRUCTURAL CHANNEL the surface graph exposes.

    INVARIANT (the discipline barrier): ``legal_conclusion`` MUST be False and
    ``is_score`` MUST be False. The constructor refuses any other value — an
    affordance can NEVER carry a legal verdict or a magnitude/threshold score.
    It is purely "this channel is present with these structural attributes".
    """

    affordance_kind: str
    subject_node_id: str
    # The subject node's source anchor (None for an entity-handle subject, e.g. a
    # corpus citation target which has no span of its own).
    source_ref: SourceSpanRef | None
    # The structural facts that justify the channel — self-evidencing.
    payload: Mapping[str, object]
    # Support nodes/edges that evidence the channel (deterministic, sorted ids).
    support_node_ids: tuple[str, ...] = ()
    jurisdiction: str = JURISDICTION
    experimental: bool = True
    # ── The discipline barrier (structurally enforced, see __post_init__) ──
    legal_conclusion: bool = False
    is_score: bool = False

    def __post_init__(self) -> None:
        if self.affordance_kind not in AFFORDANCE_KINDS:
            raise AffordanceInventoryError(
                f"unknown affordance_kind {self.affordance_kind!r}; "
                f"must be one of {sorted(AFFORDANCE_KINDS)}"
            )
        if self.legal_conclusion is not False:
            raise AffordanceInventoryError(
                "SurfaceAffordance.legal_conclusion must be False — an "
                "affordance is a structural channel, never a legal conclusion"
            )
        if self.is_score is not False:
            raise AffordanceInventoryError(
                "SurfaceAffordance.is_score must be False — an affordance "
                "inventories a channel's presence/attributes, never a score"
            )


# ── shared graph indexing (one pass; deterministic) ──────────────────────────


@dataclass(frozen=True, slots=True)
class _Index:
    nodes: Mapping[str, SurfaceNode]
    # frame node id -> exception_condition_cue node ids co-located (incoming
    # exception_scopes_frame edges; cue is the edge src, frame the dst).
    cue_for_frame: Mapping[str, tuple[str, ...]]
    # frame node id -> reference_expr node ids co-located (frame_contains_reference).
    refs_in_frame: Mapping[str, tuple[str, ...]]
    # binding node id -> term_use node ids that use it (incoming uses_term /
    # term_use_resolves_to).
    uses_of_binding: Mapping[str, tuple[str, ...]]


def _index(graph: LegalSurfaceGraph) -> _Index:
    cue_for_frame: dict[str, list[str]] = {}
    refs_in_frame: dict[str, list[str]] = {}
    uses_of_binding: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_kind == "exception_scopes_frame":
            cue_for_frame.setdefault(edge.dst, []).append(edge.src)
        elif edge.edge_kind == "frame_contains_reference":
            refs_in_frame.setdefault(edge.src, []).append(edge.dst)
        elif edge.edge_kind in ("uses_term", "term_use_resolves_to"):
            uses_of_binding.setdefault(edge.dst, []).append(edge.src)
    return _Index(
        nodes=graph.nodes,
        cue_for_frame={k: tuple(sorted(set(v))) for k, v in cue_for_frame.items()},
        refs_in_frame={k: tuple(sorted(set(v))) for k, v in refs_in_frame.items()},
        uses_of_binding={
            k: tuple(sorted(set(v))) for k, v in uses_of_binding.items()
        },
    )


def _require(index: _Index, node_id: str, *, why: str) -> SurfaceNode:
    """Fail-loud node lookup: a missing support node is a contradiction."""
    node = index.nodes.get(node_id)
    if node is None:
        raise AffordanceInventoryError(
            f"affordance support node {node_id!r} ({why}) is not in the graph; "
            f"refusing to inventory a channel over a missing node"
        )
    return node


def _sorted_nodes(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in nodes.items() if n.node_kind == kind),
        key=lambda kv: kv[0],
    )


def _str_payload(node: SurfaceNode, key: str) -> str | None:
    val = node.payload.get(key)
    return val if isinstance(val, str) and val.strip() else None


# ── per-statute channel derivations ──────────────────────────────────────────


def _delegation_channels(index: _Index) -> list[SurfaceAffordance]:
    out: list[SurfaceAffordance] = []
    for nid, node in _sorted_nodes(index.nodes, "delegation_frame"):
        cue_ids = index.cue_for_frame.get(nid, ())
        # Verify support nodes exist (fail-loud).
        for cid in cue_ids:
            _require(index, cid, why="co-located condition cue")
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_DELEGATION_CHANNEL,
                subject_node_id=nid,
                source_ref=node.source_ref,
                support_node_ids=cue_ids,
                payload={
                    "instrument_kind": node.payload.get("instrument_kind"),
                    "binding_strength": node.payload.get("binding_strength"),
                    "delegate_actor": node.payload.get("delegate_actor"),
                    # surface fact only: is a condition/exception cue co-located?
                    "has_colocated_condition_cue": bool(cue_ids),
                    "colocated_condition_cue_count": len(cue_ids),
                    "experimental": True,
                },
            )
        )
    return out


def _sanction_channels(index: _Index) -> list[SurfaceAffordance]:
    out: list[SurfaceAffordance] = []
    for nid, node in _sorted_nodes(index.nodes, "sanction_frame"):
        cue_ids = index.cue_for_frame.get(nid, ())
        for cid in cue_ids:
            _require(index, cid, why="co-located condition cue")
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_SANCTION_CHANNEL,
                subject_node_id=nid,
                source_ref=node.source_ref,
                support_node_ids=cue_ids,
                payload={
                    "sanction_kind": node.payload.get("sanction_kind"),
                    "marker_surface": node.payload.get("marker_surface"),
                    "has_colocated_condition_cue": bool(cue_ids),
                    "colocated_condition_cue_count": len(cue_ids),
                    "experimental": True,
                },
            )
        )
    return out


def _frame_reference_channels(index: _Index) -> list[SurfaceAffordance]:
    out: list[SurfaceAffordance] = []
    for nid in sorted(index.refs_in_frame):
        ref_ids = index.refs_in_frame[nid]
        frame = _require(index, nid, why="frame enclosing references")
        for rid in ref_ids:
            _require(index, rid, why="co-located reference_expr")
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_FRAME_REFERENCE_CHANNEL,
                subject_node_id=nid,
                source_ref=frame.source_ref,
                support_node_ids=ref_ids,
                payload={
                    "frame_kind": frame.node_kind,
                    "colocated_reference_count": len(ref_ids),
                    "experimental": True,
                },
            )
        )
    return out


def _definition_closure_channels(index: _Index) -> list[SurfaceAffordance]:
    out: list[SurfaceAffordance] = []
    for nid, node in _sorted_nodes(index.nodes, "definition_binding"):
        use_ids = index.uses_of_binding.get(nid, ())
        for uid in use_ids:
            _require(index, uid, why="term use of binding")
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_DEFINITION_CLOSURE_CHANNEL,
                subject_node_id=nid,
                source_ref=node.source_ref,
                support_node_ids=use_ids,
                payload={
                    "term": _str_payload(node, "term"),
                    "binding_kind": node.payload.get("binding_kind"),
                    # surface fact only: is the defined term used in this surface?
                    "is_used_in_surface": bool(use_ids),
                    "use_count": len(use_ids),
                    "experimental": True,
                },
            )
        )
    return out


def _unresolved_reference_channels(index: _Index) -> list[SurfaceAffordance]:
    out: list[SurfaceAffordance] = []
    for nid, node in _sorted_nodes(index.nodes, "reference_resolution"):
        status = node.payload.get("resolution_status")
        # Prefer the explicit payload status; fall back to the node status.
        effective = status if isinstance(status, str) else node.status
        if effective not in _UNRESOLVED_STATUSES:
            continue
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL,
                subject_node_id=nid,
                source_ref=node.source_ref,
                support_node_ids=(),
                payload={
                    "resolution_status": effective,
                    "experimental": True,
                },
            )
        )
    return out


def inventory_affordances(graph: LegalSurfaceGraph) -> list[SurfaceAffordance]:
    """Inventory the structural channels in ONE statute's surface graph.

    Surfaces, in a deterministic order grouped by affordance kind then subject
    node id:

      * ``delegation_channel`` — delegation frames + instrument/binding strength
        + whether a condition/exception cue is co-located;
      * ``sanction_channel`` — sanction frames + whether a condition/exception
        cue is co-located;
      * ``frame_reference_channel`` — frames that enclose cross-references;
      * ``definition_closure_channel`` — defined terms + whether used in-surface;
      * ``unresolved_reference_channel`` — citations that do not resolve to a
        single concrete target (open/ambiguous/broken/statute_only/unsupported).

    The frame↔condition and frame↔reference channels require the EXPERIMENTAL
    cross-lens / frame-relation edge passes to have run on the graph (otherwise
    those edges are absent and the corresponding channels simply do not appear —
    the inventory never invents an edge). Derives ONLY from nodes/edges already
    present; fail-loud on a missing support node.
    """
    index = _index(graph)
    out: list[SurfaceAffordance] = []
    out.extend(_delegation_channels(index))
    out.extend(_sanction_channels(index))
    out.extend(_frame_reference_channels(index))
    out.extend(_definition_closure_channels(index))
    out.extend(_unresolved_reference_channels(index))
    # Deterministic global order: by kind, then subject node id.
    out.sort(key=lambda a: (a.affordance_kind, a.subject_node_id))
    return out


# ── corpus-level channel derivation ──────────────────────────────────────────


def _citation_target_entity_ids(graph: LegalSurfaceGraph) -> tuple[str, ...]:
    """Distinct entity node ids that any refers_to / has_candidate edge points at.

    These are the candidate fan-in targets; ``citations_of`` then counts the
    incoming citations for each. Sorted for determinism.
    """
    targets: set[str] = set()
    for edge in graph.edges:
        if edge.edge_kind in ("refers_to", "has_candidate"):
            targets.add(edge.dst)
    return tuple(sorted(targets))


def inventory_corpus_affordances(
    corpus_graph: LegalSurfaceGraph,
) -> list[SurfaceAffordance]:
    """Inventory CITATION FAN-IN channels over a cross-statute corpus graph.

    For every target entity (act or provision) that any ``refers_to`` /
    ``has_candidate`` edge points at, surfaces a ``citation_fan_in_channel`` whose
    payload carries the fan-in count and the distinct citing works (via
    :func:`citations_of`). "This provision is cited by N statutes" — a structural
    channel, NEVER a claim about the provision's importance (that would be a
    score). Deterministic: targets sorted, citing works sorted.

    Fail-loud: a citation edge whose target entity node is absent from the graph
    raises (the corpus assembler should always inject the target node first).
    """
    out: list[SurfaceAffordance] = []
    for target_id in _citation_target_entity_ids(corpus_graph):
        target = corpus_graph.nodes.get(target_id)
        if target is None:
            raise AffordanceInventoryError(
                f"citation target entity {target_id!r} is not in the corpus "
                f"graph; refusing to inventory a fan-in channel over it"
            )
        citations = citations_of(corpus_graph, target_id)
        if not citations:
            continue
        citing_works = tuple(
            sorted({c.citing_work_id for c in citations if c.citing_work_id})
        )
        asserted = sum(1 for c in citations if c.edge_kind == "refers_to")
        out.append(
            SurfaceAffordance(
                affordance_kind=AFFORDANCE_CITATION_FAN_IN_CHANNEL,
                subject_node_id=target_id,
                source_ref=target.source_ref,
                support_node_ids=tuple(sorted({c.citing_node_id for c in citations})),
                payload={
                    "fan_in_count": len(citations),
                    "asserted_citation_count": asserted,
                    "candidate_citation_count": len(citations) - asserted,
                    "distinct_citing_works": citing_works,
                    "distinct_citing_work_count": len(citing_works),
                    "experimental": True,
                },
            )
        )
    out.sort(key=lambda a: a.subject_node_id)
    return out


__all__ = [
    "AFFORDANCE_CITATION_FAN_IN_CHANNEL",
    "AFFORDANCE_DEFINITION_CLOSURE_CHANNEL",
    "AFFORDANCE_DELEGATION_CHANNEL",
    "AFFORDANCE_FRAME_REFERENCE_CHANNEL",
    "AFFORDANCE_KINDS",
    "AFFORDANCE_SANCTION_CHANNEL",
    "AFFORDANCE_UNRESOLVED_REFERENCE_CHANNEL",
    "AffordanceInventoryError",
    "SurfaceAffordance",
    "inventory_affordances",
    "inventory_corpus_affordances",
]
