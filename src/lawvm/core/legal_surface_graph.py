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
        # Construction-grammar deontic core (the DENSE Layer-2 substrate). Minted
        # by the deontic_core lens from parse_modal_sentence — one node per modal
        # core (cue alone), so it is dense where the actor_modal_frame oracle (a
        # registered actor within 60 chars) is sparse. Surface-only; no legal force.
        "deontic_core",
        # H5/H6 frame families (Pro r5 Phase 8 — nodes only; edge/lint passes
        # deferred). condition + exception share one cue kind from the recognizer.
        "delegation_frame",
        "procedure_frame",
        "sanction_frame",
        "exception_condition_cue",
        # The LOWER INSTRUMENT a delegation grants the power to issue (the asetus /
        # määräys / päätös the delegation_frame authorizes). Minted by the
        # delegated_instrument lens from the construction delegation parse's
        # instrument anchor (the precise instrument-noun span), so the Layer-2
        # delegation_grants_instrument edge has an INSTRUMENT-ENTITY node to point
        # at. Surface-only; records the instrument's surface form, never that the
        # delegation is legally valid.
        "delegated_instrument",
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
        # delegation_frame -> the delegated_instrument ENTITY node it grants the
        # power to issue (the asetus/määräys/päätös). A Layer-2 norm->authorized-
        # instrument link: the delegation parse's instrument anchor backs a
        # delegated_instrument node, and this edge joins the recognizer frame to the
        # instrument node whose span sits INSIDE the frame (a structural containment
        # attachment, not mere proximity). Candidate-not-asserted: one contained
        # instrument -> "asserted"; several -> one edge per candidate with the full
        # set in payload, "ambiguous"; none -> a typed diagnostic, never an invented
        # edge. Still a SURFACE candidate, never a legal conclusion (§D7) — the
        # reading that the power validly delegates rulemaking must leave the graph
        # through a named authorization object.
        "delegation_grants_instrument",
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
        # exception_condition_cue -> frame node (delegation/procedure/sanction/
        # actor_modal) whose span the cue PRECEDES or overlaps within a small
        # window in the same source unit. A CANDIDATE affordance ("this
        # exception/condition cue sits at or before that frame's text"), NOT a
        # claim that the exception legally governs/qualifies the frame.
        "exception_scopes_frame",  # EXPERIMENTAL
        # frame node -> actor_modal_frame co-located within a small span window in
        # the same source unit. A CANDIDATE affordance ("an actor/modal shape sits
        # in/near this frame's text" — who acts in/near this frame), NOT a claim
        # that the actor is the legal subject of the frame.
        "frame_has_colocated_actor",  # EXPERIMENTAL
        # ── Layer-2 construction-derived deontic NORM edges ───────────────────
        # The FIRST real Layer-2 composition: a condition/exception qualifier
        # (exception_condition_cue node) -> the deontic core (actor_modal_frame
        # node) the CONSTRUCTION parse attaches it to (not a proximity window).
        # condition_attaches_norm: a CONDITION qualifier scopes that core ("the
        # norm applies WHEN/IF X"); exception_excepts_norm: an EXCEPTION qualifier
        # scopes that core ("the norm does NOT apply in case X"). status carries
        # the construction's attachment confidence: "resolved" (one core) or
        # "ambiguous" (one edge per candidate core, full set in payload). Still a
        # SURFACE candidate, never a legal conclusion (§D7) — the legal reading
        # that the norm is conditioned/excepted must leave the graph through a
        # named authorization object.
        "condition_attaches_norm",
        "exception_excepts_norm",
        # A power-register deontic core (deontic_core node, kind="power" — the
        # delegating verb register: säädetään/annetaan/valtuus…) -> the
        # delegation_frame in the SAME sentence whose instrument it grants ("this
        # power delegates rulemaking via that asetus"). A prohibition/obligation
        # deontic core (deontic_core node, kind in {prohibition,obligation}) ->
        # the sanction_frame in the SAME sentence that backs it ("this duty/ban is
        # sanctioned by that consequence"). Sentence-local, candidate-not-asserted:
        # one target -> status "candidate"; several -> one edge per candidate with
        # the full candidate set in payload, status "ambiguous"; never a silent
        # pick. Still a SURFACE candidate, never a legal conclusion (§D7) — the
        # reading that the power validly delegates / the norm is enforceably
        # sanctioned must leave the graph through a named authorization object.
        "delegates_to",
        "sanctioned_by",
        # A deontic core (deontic_core node) -> the actor_modal_frame node that
        # carries its norm SUBJECT/addressee. The modal parse records the core's
        # addressee_span (the overt subject NP preceding the cue); this edge binds
        # the core to the actor_modal_frame whose span COVERS that addressee span
        # (the production actor recognizer typed that subject). status "candidate"
        # (one covering actor frame) or "ambiguous" (several; full set in payload).
        # When the addressee is underspecified (impersonal/passive register) no
        # subject is fixed by the text -> NO edge, a typed subject_underspecified
        # diagnostic. Still a SURFACE candidate, never a legal conclusion (§D7) —
        # the reading that this actor is the norm's legal subject must leave the
        # graph through a named authorization object.
        "norm_has_subject",
        # An obligation/power deontic core (deontic_core node) -> a co-SENTENCE
        # procedure_frame node (the process the obligation/power runs through).
        # Sentence-local, candidate-not-asserted (one target "candidate"; several
        # "ambiguous" with the full set in payload; none -> typed diagnostic),
        # the same shape as delegates_to/sanctioned_by. A SURFACE candidate, never
        # a legal conclusion (§D7).
        "governed_by_procedure",
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
