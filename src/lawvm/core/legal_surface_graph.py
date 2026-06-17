"""Core types for the LawVM Legal Surface Graph (Phase 0 skeleton).

The Legal Surface Graph is the single canonical typed container for
*source-anchored surface facts*: reference expressions, definition bindings,
term uses, temporal expressions, actor/modal frames, the entity handles they
point at, and the residuals/lints derived from them. Parquet rows and viewer
projections are *projections* of this graph — never parallel sources of truth.

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt``
(ChatGPT Pro ruling), §D1 (graph shape), §D7 (authority firewall), §D8
(v0 type sketch).

THE AUTHORITY FIREWALL (§D7) is structural, not prose:

    Every SurfaceNode/SurfaceEdge defaults ``surface_only=True`` and
    ``replay_authorized=False``. No surface node or edge may ever carry
    ``replay_authorized=True``; the assembler refuses to build one. A surface
    fact that becomes executable must LEAVE this graph and pass through a named
    authorization/proof object. The graph is never accepted by replay APIs.

This is a surface-analysis graph, NOT the global provenance/certificate graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

# ── Schema tags (stable identity prefixes; §D1) ──────────────────────────────

SCHEMA_TAG = "lawvm.legal_surface_graph.v0"
NODE_ID_SCHEMA_TAG = "lawvm.surface.node.v0"
EDGE_ID_SCHEMA_TAG = "lawvm.surface.edge.v0"
GRAPH_ID_SCHEMA_TAG = "lawvm.surface.graph.v0"


# ── Closed vocabularies (§D8) ────────────────────────────────────────────────

ResolutionStatus = Literal[
    "resolved",
    "statute_only",
    "ambiguous",
    "open",
    "broken",
    "unsupported",
]

AuthorityRole = Literal[
    "surface_fact",
    "candidate",
    "entity_handle",
    "residual",
    "projection",
]

# v0 node kinds (§D1). H5/H6 add more later WITHOUT a schema redesign.
NODE_KINDS: frozenset[str] = frozenset(
    {
        # entity handles
        "source_unit",
        "legal_work_entity",
        "legal_address_entity",
        "actor_entity",
        "term_symbol_entity",
        # surface facts / residuals
        "reference_expr",
        "reference_resolution",
        "definition_binding",
        "term_use",
        "temporal_expr",
        "actor_modal_frame",
        # H5/H6 frame families (Pro r5 Phase 8 — nodes only; edge/lint passes
        # deferred). condition + exception share one cue kind from the recognizer.
        "delegation_frame",
        "procedure_frame",
        "sanction_frame",
        "exception_condition_cue",
        "surface_residual",
    }
)

# v0 edge kinds (§D1).
EDGE_KINDS: frozenset[str] = frozenset(
    {
        "contains_source_fact",
        "resolution_of",
        "refers_to",
        "has_candidate",
        "defines_term",
        "uses_term",
        "term_use_resolves_to",
        "temporal_qualifies",
        "actor_modal_has_actor",
        "actor_modal_has_object",
        "unresolved_because",
        "supports_lint",
        "derives_projection",
        # ── EXPERIMENTAL (H5/H6 frame affordances; candidate-status only) ──
        # These are CANDIDATE cross-frame affordances surfaced for serendipity,
        # NOT settled semantics and NEVER asserted facts (Pro §D5). They link
        # frame-family nodes that some future analysis MIGHT exploit; they make
        # no legal claim.
        # delegation_frame -> the instrument_kind it names (carried in payload;
        # reserved for when an instrument ENTITY node exists to point at).
        "delegation_grants_instrument",  # EXPERIMENTAL
        # actor_modal_frame -> temporal_expr co-located within a small span
        # window in the same source unit (a nearby deadline/commencement).
        "actor_modal_temporal_colocated",  # EXPERIMENTAL
        # frame node (delegation/procedure/sanction/exception/actor_modal) ->
        # reference_expr whose source span sits INSIDE (or within a small window
        # of) the frame's span in the same source unit. A CANDIDATE serendipity
        # affordance ("a citation sits inside this frame's text"), NOT a claim
        # that the frame legally governs that reference.
        "frame_contains_reference",  # EXPERIMENTAL
        # frame node -> temporal_expr whose source span sits INSIDE (or within a
        # small window of) the frame's span in the same source unit. A CANDIDATE
        # affordance ("a date/deadline sits inside this frame's text"), NOT a
        # claim that the date legally qualifies the frame.
        "frame_qualified_by_temporal",  # EXPERIMENTAL
    }
)

# Allowed node `status` values. ResolutionStatus members plus the edge/structural
# statuses that surface facts legitimately carry.
NODE_STATUSES: frozenset[str] = frozenset(
    {
        "resolved",
        "statute_only",
        "ambiguous",
        "open",
        "broken",
        "unsupported",
        # structural / entity statuses
        "asserted",
        "present",
        "not_applicable",
    }
)

# Allowed edge `status` values (§D1 edge envelope).
EDGE_STATUSES: frozenset[str] = frozenset(
    {
        "asserted",
        "candidate",
        "ambiguous",
        "open",
        "blocked",
    }
)

AUTHORITY_ROLES: frozenset[str] = frozenset(
    {
        "surface_fact",
        "candidate",
        "entity_handle",
        "residual",
        "projection",
    }
)


# ── Source anchoring (§D8) ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceSpanRef:
    """A character span in one source unit, content-addressed by text_hash."""

    source_unit_id: str
    source_hash: str
    work_id: str | None
    address: str | None
    char_start: int
    char_end: int
    text_hash: str


@dataclass(frozen=True, slots=True)
class SourceUnitRef:
    """Reference to a source unit participating in the graph subject slice."""

    source_unit_id: str
    work_id: str
    address: str | None
    source_hash: str


# ── Graph subject (§D1) ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceGraphSubject:
    """The declared *surface slice* a graph is built over.

    A graph is not necessarily "one whole statute": it may be one work at one
    date, a corpus slice, an HE draft, or a law+proposal bundle. The subject
    says which.
    """

    jurisdiction: str
    work_id: str | None
    scope: Mapping[str, object]
    surface_time: str | None
    source_bundle_hash: str
    language: str


# ── Provenance of lens execution (§D1) ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceLensRun:
    """Record of one lens execution that contributed seeds to this graph."""

    lens_id: str
    schema_version: str
    jurisdiction: str
    produced_node_kinds: tuple[str, ...]
    produced_edge_kinds: tuple[str, ...]
    coverage: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SurfaceDiagnostic:
    """A build/assembly diagnostic. Surface-only; never a legal conclusion."""

    code: str
    severity: str  # info | warning | error
    message: str
    lens_id: str | None = None
    source_ref: SourceSpanRef | None = None


# ── Core graph elements (§D1, §D8) ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceNode:
    """One surface-analysis node. Defaults to the firewall-safe configuration.

    INVARIANT (§D7): ``replay_authorized`` MUST remain False. The assembler
    refuses to build any node with ``replay_authorized=True``.
    """

    node_id: str
    node_kind: str
    authority_role: AuthorityRole
    jurisdiction: str
    source_ref: SourceSpanRef | None
    lens_id: str | None
    rule_id: str | None
    status: ResolutionStatus | str
    payload_hash: str
    payload: Mapping[str, object]
    surface_only: bool = True
    replay_authorized: bool = False


@dataclass(frozen=True, slots=True)
class SurfaceEdge:
    """One surface-analysis edge between two graph nodes.

    INVARIANT (§D7): ``replay_authorized`` MUST remain False.
    """

    edge_id: str
    edge_kind: str
    src: str
    dst: str
    rule_id: str
    status: str
    payload_hash: str
    payload: Mapping[str, object]
    surface_only: bool = True
    replay_authorized: bool = False


@dataclass(frozen=True, slots=True)
class LegalSurfaceGraph:
    """The single canonical typed graph container (§D1).

    Identity layering (§D1):
      * ``node_id``     — stable surface identity (survives payload improvement)
      * ``payload_hash``— exact current payload of a node/edge
      * ``graph_id``    — full graph snapshot identity (changes iff any payload
                          hash or the node/edge id set changes)
    """

    schema: str
    graph_id: str
    subject: SurfaceGraphSubject
    source_units: tuple[SourceUnitRef, ...]
    lens_runs: tuple[SurfaceLensRun, ...]
    nodes: Mapping[str, SurfaceNode]
    edges: tuple[SurfaceEdge, ...]
    build_diagnostics: tuple[SurfaceDiagnostic, ...]
