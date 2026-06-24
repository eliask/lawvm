"""Surface-plane totality sweeps (registry rows SURF-01, SURF-02, SURF-07).

Three *per-unit totality* sweeps over the FI surface plane, in the spirit of the
audit-invariant registry's §0 generative principle: every owned unit is accepted,
typed as a residual, or recorded as a finding — never silently dropped. Like the
SURF-04/SURF-05 pair (:mod:`lawvm.finland.references.surface_totality`), all three
are OBSERVATION-role: they assert the totality CONTRACT and surface a residual
population; over the real corpus the residual is the *expected, correct* outcome
(an orphan entity node / a leaked token is a real surface fact, surfaced — not a
pipeline crash), so blocking would contradict tag-don't-guess. The synthetic
unit-level bite is the guard-liveness fire-drill.

SURF-01 — token-realization totality
====================================
Over ONE provision's forest token-coverage census (the
:class:`…token_partition_coverage.TokenPartitionCoverage` projection of a forest's
:class:`…source_syntax_graph.SyntaxCoverage`): every signal-bearing token must
enter exactly one of the closed Pro-D2 destination buckets —
``owned`` / ``benign_uninterpreted`` / ``typed_residual`` / ``unowned_violation``.
The four buckets are the closed destination set; ``unowned_violation`` is the
registered typed residual bucket (the no-silent-drop frontier already surfaced as
self-evidencing witness spans). A token in NONE of the buckets — i.e. the four
buckets do NOT sum to ``total_tokens`` — is a silently-dropped token, typed
``SURFACE.TOKEN_REALIZATION_GAP``. (The forest's ``is_partition()`` already
COMPUTES this sum; SURF-01 lifts it to a per-unit, self-evidencing FINDING that
names the gap magnitude, so a leak is auditable, not a silent ``False``.)

This is the **per-token** accounting that sits beside the **per-span** coverage
of SURF-03 (``certify_graph_coverage``): SURF-03 asserts every lens-node SPAN is
forest-owned; SURF-01 asserts every source TOKEN reaches a typed destination.

SURF-02 — handoff parity source→token
=====================================
The waist-crossing EDGE form of SURF-01: the source span consumed by tokenization
(``total_tokens``) == owned ∪ typed-residual ∪ benign ∪ violation. This is the
SAME census SURF-01 walks; over the token-partition machinery SURF-02 **subsumes
into SURF-01** — there is exactly one partition account per provision, and the
"span consumed by tokenization" is the ``total_tokens`` denominator of that
account. We therefore implement SURF-02 NOT as a duplicate walk but as the
SPAN-LEVEL assertion over the same :class:`TokenPartitionCoverage`
(:func:`assert_handoff_parity`), returning the same finding family. The honest
statement (see module docstring of the registry doc): SURF-02 is the edge-lens
NAME for the SURF-01 node-lens sweep; both are the one totality
``value + typed-residual + benign + violation == consumed``.

SURF-07 — entity-handle totality
================================
Over ONE :class:`~lawvm.core.legal_surface_graph.LegalSurfaceGraph`: an
``authority_role == "entity_handle"`` node (``legal_work_entity`` /
``legal_address_entity`` / ``actor_entity`` / ``term_symbol_entity`` /
``source_unit``) is minted to be the TARGET of a covering edge (``defines_term`` →
``term_symbol_entity``; ``refers_to`` / ``incorporates`` → ``legal_work_entity``;
the corpus cross-statute pass points ``refers_to`` at ``legal_address_entity``).
An entity-handle node that appears in NEITHER ``edge.src`` NOR ``edge.dst`` is an
ORPHAN — a surface entity node with no covering edge. It is typed
``SURFACE.ORPHAN_ENTITY_NODE`` rather than left silently uncovered. The "entity
node" concept is REAL in the FI surface graph (``AuthorityRole`` literal
``entity_handle`` + the entity ``NODE_KINDS``); over a well-formed single-statute
graph the orphan population is expected to be EMPTY (each entity is minted
alongside its edge) — a non-empty population is the typed surface of a producer
that minted an entity without its covering edge.

All three sweeps are PURE (no side effects, no production-behavior change): they
read already-produced carriers and return typed finding records. They sit off the
replay/apply path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from lawvm.core.legal_surface_graph import LegalSurfaceGraph

if TYPE_CHECKING:
    from lawvm.finland.legal_surface.token_partition_coverage import (
        TokenPartitionCoverage,
    )

# ---------------------------------------------------------------------------
# Finding codes (closed set; registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

SURFACE_TOKEN_REALIZATION_GAP = "SURFACE.TOKEN_REALIZATION_GAP"
WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN = "WAIST.HANDOFF_PARITY_SOURCE_TO_TOKEN"
SURFACE_ORPHAN_ENTITY_NODE = "SURFACE.ORPHAN_ENTITY_NODE"

#: The closed set of entity-handle authority role(s) SURF-07 sweeps. Kept as a
#: frozenset so a future authority role that is NOT consciously added here is, by
#: construction, NOT swept (and would be caught by the registry/role taxonomy,
#: not silently treated as an entity).
_ENTITY_HANDLE_ROLE = "entity_handle"


# ---------------------------------------------------------------------------
# Typed sweep findings (self-evidencing per AGENTS.md §1.8 / EV-07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenRealizationFinding:
    """One SURF-01 / SURF-02 surface fact about a provision's token-realization.

    Attributes:
        code:           ``SURFACE.TOKEN_REALIZATION_GAP`` (SURF-01 node form) or
                        ``WAIST.HANDOFF_PARITY_SOURCE_TO_TOKEN`` (SURF-02 edge
                        form). Same totality; different lens NAME.
        graph_id:       The forest the census came from (drift anchor).
        statute_id:     The provision/statute id the sweep ran over.
        total_tokens:   The span consumed by tokenization (the denominator).
        accounted:      owned + benign + typed_residual + unowned_violation.
        gap:            ``total_tokens - accounted`` — the count of tokens in NO
                        destination bucket (a silently-dropped token, > 0). A
                        negative value (double-counted) is also a parity break.
        detail:         SELF-EVIDENCING message naming the per-bucket counts + the
                        gap magnitude, so the finding is auditable from the record
                        alone (never an opaque count).
    """

    code: str
    graph_id: str
    statute_id: str
    total_tokens: int
    accounted: int
    gap: int
    detail: str


@dataclass(frozen=True, slots=True)
class OrphanEntityNodeFinding:
    """One SURF-07 surface fact: an entity-handle node with no covering edge.

    Attributes:
        code:        ``SURFACE.ORPHAN_ENTITY_NODE``.
        graph_id:    The graph the sweep ran over.
        node_id:     The orphan entity node id.
        node_kind:   Its node kind (``legal_work_entity`` / ``term_symbol_entity``
                     / ``legal_address_entity`` / ``actor_entity`` / …).
        lens_id:     The lens that produced it (``None`` for assembler/corpus-minted).
        detail:      SELF-EVIDENCING message naming the entity + its payload handle,
                     so the finding is auditable from the record alone.
    """

    code: str
    graph_id: str
    node_id: str
    node_kind: str
    lens_id: str | None
    detail: str


# ---------------------------------------------------------------------------
# SURF-01 — token-realization totality
# ---------------------------------------------------------------------------


def sweep_token_realization(
    cert: TokenPartitionCoverage,
) -> tuple[TokenRealizationFinding, ...]:
    """Assert token-realization totality over one provision's partition census.

    Every signal-bearing token must land in exactly one of the closed Pro-D2
    destination buckets (owned / benign_uninterpreted / typed_residual /
    unowned_violation). A token in NONE of them — the four buckets not summing to
    ``total_tokens`` — is a silently-dropped token, typed
    ``SURFACE.TOKEN_REALIZATION_GAP``.

    Args:
        cert: The already-built :class:`TokenPartitionCoverage` (a pure projection
              of a forest's census; re-parses NOTHING).

    Returns:
        A tuple with at most one :class:`TokenRealizationFinding`. Empty when the
        partition is total (the structural norm — every token has a destination).

    Discipline (tag-don't-guess): the sweep NEVER re-buckets a token. It reads the
    forest's OWN per-bucket counts and asserts they sum to the OWN ``total_tokens``;
    a non-zero gap is the forest's own leak, surfaced self-evidencing.
    """
    accounted = cert.partition_total
    gap = cert.total_tokens - accounted
    if gap == 0:
        return ()
    return (
        TokenRealizationFinding(
            code=SURFACE_TOKEN_REALIZATION_GAP,
            graph_id=cert.graph_id,
            statute_id=cert.statute_id,
            total_tokens=cert.total_tokens,
            accounted=accounted,
            gap=gap,
            detail=(
                f"token-realization gap of {gap} token(s) in {cert.statute_id or cert.graph_id!r}: "
                f"total_tokens={cert.total_tokens} but destination buckets sum to "
                f"{accounted} (owned={cert.owned}, "
                f"benign_uninterpreted={cert.benign_uninterpreted}, "
                f"typed_residual={cert.typed_residual}, "
                f"unowned_violation={cert.unowned_violation}); "
                f"{abs(gap)} token(s) reached NO typed destination "
                f"({'dropped' if gap > 0 else 'double-counted'})"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# SURF-02 — handoff parity source→token (the edge form of SURF-01)
# ---------------------------------------------------------------------------


def assert_handoff_parity(
    cert: TokenPartitionCoverage,
) -> tuple[TokenRealizationFinding, ...]:
    """SURF-02: source→token handoff parity (the waist-edge form of SURF-01).

    SUBSUMES into SURF-01: there is exactly ONE token-partition account per
    provision, and the "source span consumed by tokenization" is its
    ``total_tokens`` denominator. This is the SAME assertion as
    :func:`sweep_token_realization` — ``consumed == owned + typed_residual +
    benign + violation`` — re-emitted under the waist-edge finding NAME
    (``WAIST.HANDOFF_PARITY_SOURCE_TO_TOKEN``) so the audit registry's SURF-02 row
    has a live, distinctly-named detector. It does NOT re-walk the census or make a
    second ownership decision.

    Returns:
        A tuple with at most one :class:`TokenRealizationFinding`, carrying the
        SURF-02 code. Empty iff the handoff balances.
    """
    accounted = cert.partition_total
    gap = cert.total_tokens - accounted
    if gap == 0:
        return ()
    return (
        TokenRealizationFinding(
            code=WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN,
            graph_id=cert.graph_id,
            statute_id=cert.statute_id,
            total_tokens=cert.total_tokens,
            accounted=accounted,
            gap=gap,
            detail=(
                f"source->token handoff parity break in "
                f"{cert.statute_id or cert.graph_id!r}: the span consumed by "
                f"tokenization (total_tokens={cert.total_tokens}) does not equal "
                f"owned+typed_residual+benign+violation ({accounted}); "
                f"gap={gap}"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# SURF-07 — entity-handle totality
# ---------------------------------------------------------------------------


def sweep_orphan_entity_nodes(
    graph: LegalSurfaceGraph,
) -> tuple[OrphanEntityNodeFinding, ...]:
    """Assert entity-handle totality over one :class:`LegalSurfaceGraph`.

    Every ``authority_role == "entity_handle"`` node is minted to be the TARGET of
    a covering edge (``defines_term`` → ``term_symbol_entity``; ``refers_to`` /
    ``incorporates`` → ``legal_work_entity`` / ``legal_address_entity``). An entity
    node that appears in NEITHER ``edge.src`` NOR ``edge.dst`` is an ORPHAN — a
    surface entity node with no covering edge — typed
    ``SURFACE.ORPHAN_ENTITY_NODE`` rather than left silently uncovered.

    Args:
        graph: The assembled FI :class:`LegalSurfaceGraph`.

    Returns:
        A tuple of :class:`OrphanEntityNodeFinding`, sorted by ``node_kind`` then
        ``node_id``. Empty when every entity handle is covered by >=1 edge (the
        structural norm for a well-formed graph).

    Discipline (tag-don't-guess): the sweep NEVER fabricates an edge. Coverage is
    the node's OWN presence in the graph's OWN edge endpoints; nothing is inferred.
    """
    covered: set[str] = set()
    for edge in graph.edges:
        covered.add(edge.src)
        covered.add(edge.dst)
    findings: list[OrphanEntityNodeFinding] = []
    for node in graph.nodes.values():
        if node.authority_role != _ENTITY_HANDLE_ROLE:
            continue
        if node.node_id in covered:
            continue
        handle = _entity_handle_summary(node.payload)
        findings.append(
            OrphanEntityNodeFinding(
                code=SURFACE_ORPHAN_ENTITY_NODE,
                graph_id=graph.graph_id,
                node_id=node.node_id,
                node_kind=node.node_kind,
                lens_id=node.lens_id,
                detail=(
                    f"entity-handle node {node.node_id!r} (kind={node.node_kind!r}, "
                    f"handle={handle!r}) is in no edge endpoint: a surface entity "
                    f"node with no covering edge"
                ),
            )
        )
    findings.sort(key=lambda f: (f.node_kind, f.node_id))
    return tuple(findings)


def _entity_handle_summary(payload: Mapping[str, object]) -> str:
    """The most identifying handle from an entity node's payload (best-effort).

    Pure reporting helper: reads the conventional entity payload keys
    (``work_id`` / ``address_id`` / ``term`` / ``address``) for a self-evidencing
    detail string. Never raises — an unexpected payload shape degrades to ``""``.
    """
    for key in ("work_id", "address_id", "term", "address"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


__all__ = [
    "SURFACE_ORPHAN_ENTITY_NODE",
    "SURFACE_TOKEN_REALIZATION_GAP",
    "WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN",
    "OrphanEntityNodeFinding",
    "TokenRealizationFinding",
    "assert_handoff_parity",
    "sweep_orphan_entity_nodes",
    "sweep_token_realization",
]
