"""Bridge a folded FI ``ReferenceSet`` to a ``lawvm.legal_relation_edge.v0`` body.

This is the §25.4 "extraction, not greenfield" integration: the Finnish body
cross-reference graph — already extracted, flattened, and re-folded into one
:class:`~lawvm.core.reference_mention.ReferenceExpression` +
:class:`~lawvm.core.reference_mention.ReferenceResolution` per surface by
:func:`lawvm.finland.references.reference_sets.fold_reference_set` — is mapped
into REAL proof-graded relation edges so the substrate's L0.8 authority-legality
matrix (``checker._check_relation_edge_authority``) exercises live FI data.

A citation is **source-anchored evidence**: the source text POINTS at a target.
It is NEVER an assertion of legal state. The mapping therefore pins every edge
to the ``surface`` authority plane with ``replay_authorized=false`` — a posture
the §25.3 matrix accepts for both ``registry_resolved`` (a deterministically
resolved target) and ``source_asserted`` (a target whose identity is not pinned:
statute-only / ambiguous / open). The mapping is matrix-legal **by construction**
and asserts so before returning (a guard, not a hope).

ONE folded reference set → ONE edge. A written range ("33—35 artiklassa") that
flattened to N member rows is one ``ALL_VALID`` edge carrying multiple targets,
NOT N single-target edges — the set semantics survive the round-trip exactly as
:func:`fold_reference_set` restored them (§14).

The module is substrate-side and import-light: it imports the core reference
types (pure dataclasses, no Finland frontend) and the substrate edge builder,
nothing heavier.
"""

from __future__ import annotations

from lawvm.core.reference_mention import (
    ProvisionRef,
    ReferenceExpression,
    ReferenceResolution,
    ReferenceResolutionStatus,
    ReferenceTargetSetSemantics,
)
from lawvm.substrate.canonical_json import JsonValue
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
)

# --------------------------------------------------------------------------- #
# Enum mappings — closed, exhaustive, fail-loud (a missing key RAISES).        #
# --------------------------------------------------------------------------- #

# The §14 reference semantics and the substrate §25.1 set semantics share their
# wire strings (by design — see ``reference_mention.ReferenceTargetSetSemantics``
# vs ``relation_edge.TargetSetSemantics``); this table makes the typed bridge
# explicit rather than relying on the string coincidence.
_SEMANTICS_MAP: dict[ReferenceTargetSetSemantics, TargetSetSemantics] = {
    ReferenceTargetSetSemantics.SINGLE: TargetSetSemantics.SINGLE,
    ReferenceTargetSetSemantics.ALL_VALID: TargetSetSemantics.ALL_VALID,
    ReferenceTargetSetSemantics.CANDIDATE_AMBIGUITY: TargetSetSemantics.CANDIDATE_AMBIGUITY,
    ReferenceTargetSetSemantics.OPEN: TargetSetSemantics.OPEN,
    ReferenceTargetSetSemantics.NO_ENUMERABLE_EXTENSION: TargetSetSemantics.NO_ENUMERABLE_EXTENSION,
}


def _edge_status_and_verification(
    resolution: ReferenceResolution,
) -> tuple[EdgeStatus, VerificationLevel]:
    """Map a resolution's status (+ semantics) to ``EdgeStatus`` + evidence class.

    The evidence class is the load-bearing half: a deterministically RESOLVED
    resolution has its target identity PINNED, so it earns ``registry_resolved``
    (a strong-but-not-legal_state evidence class the matrix lets ride the surface
    plane). Anything not fully pinned — PARTIAL (some members unresolved),
    ambiguous (pick-one-unknown), open (vague catch-all), or non-enumerable —
    carries only ``source_asserted``: the source text asserts a citation surface,
    but the target identity is NOT registry-pinned.

    Both ``registry_resolved`` and ``source_asserted`` are legal on the surface
    plane with ``replay_authorized=false`` (§25.3), so every edge this bridge
    builds is matrix-legal by construction.
    """
    status = resolution.status
    semantics = resolution.target_set_semantics

    if status is ReferenceResolutionStatus.RESOLVED:
        return EdgeStatus.RESOLVED, VerificationLevel.REGISTRY_RESOLVED
    if status is ReferenceResolutionStatus.PARTIAL:
        # Some members resolved, some did not — the set's identity is not fully
        # pinned, so the edge is qualified and only source-asserted.
        return EdgeStatus.QUALIFIED, VerificationLevel.SOURCE_ASSERTED

    # UNRESOLVED — discriminate by the set semantics so the status is the
    # honest one (ambiguity vs open vs nothing-to-enumerate), never collapsed.
    if semantics is ReferenceTargetSetSemantics.CANDIDATE_AMBIGUITY:
        return EdgeStatus.AMBIGUOUS, VerificationLevel.SOURCE_ASSERTED
    if semantics is ReferenceTargetSetSemantics.OPEN:
        return EdgeStatus.OPEN, VerificationLevel.SOURCE_ASSERTED
    # NO_ENUMERABLE_EXTENSION (or any other unresolved shape): the expression has
    # no enumerable target — the citation is unsupported by an identifiable
    # target, but it is committed (not dropped).
    return EdgeStatus.UNSUPPORTED, VerificationLevel.SOURCE_ASSERTED


def _target_ref(target: ProvisionRef) -> str:
    """Content-addressable target ref string for one resolved provision target.

    Uses the ``ProvisionRef.serialized()`` self-describing slash form
    (``statute_id[/chN]/section[/momentti][/kLABEL]``) — the stable, round-trippable
    target identity the rest of the codebase already keys on (``fi_refs`` rows,
    interlink overlays). ``build_relation_edge`` sorts the target set into
    deterministic order, so member emission order does not perturb ``edge_id``.
    """
    return target.serialized()


def reference_set_to_relation_edge(
    *,
    expression: ReferenceExpression,
    resolution: ReferenceResolution,
    corpus_version: str,
    source_struct_node_id: str = "",
    branch_id: str = "actual",
) -> dict[str, JsonValue]:
    """Map ONE folded FI reference set to a ``lawvm.legal_relation_edge.v0`` body.

    Args:
        expression: the immutable surface fact (one written citation surface).
        resolution: its one resolution carrying the whole target SET + semantics.
        corpus_version: the resolution scope key (the pack's ``corpus_version``).
        source_struct_node_id: the work struct-node the citation lives in, when
            known; folded into ``source_ref`` so the edge is anchored to the
            citing provision, not just the surface expression. Empty when the
            caller has only the surface identity.
        branch_id: the branch the targets resolve under (``"actual"`` for v0).

    Returns:
        A canonical-JSON edge body with a content-addressed ``edge_id``.

    Raises:
        KeyError: if the resolution carries a semantics not in ``_SEMANTICS_MAP``
            (a new §14 semantics value with no bridge mapping — fail loud, never
            silently coerce).
        AssertionError: if the constructed edge is NOT matrix-legal (a bug in the
            mapping table — the guard turns it into a loud failure here rather
            than a deferred checker violation).
    """
    set_semantics = _SEMANTICS_MAP[resolution.target_set_semantics]
    status, verification = _edge_status_and_verification(resolution)

    # ``source_ref`` anchors the edge to the citing point: prefer the work
    # struct-node (the provision the citation lives IN), fall back to the surface
    # expression identity. Both are content-addressed strings.
    if source_struct_node_id:
        source_ref = f"struct:{source_struct_node_id}#{expression.surface_expr_id}"
    else:
        source_ref = f"surface:{expression.surface_expr_id}"

    target_set = tuple(_target_ref(t) for t in resolution.target_set)

    body = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref=source_ref,
        target_set=target_set,
        target_set_semantics=set_semantics,
        # A citation is source-anchored evidence — NEVER legal_state.
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=verification,
        replay_authorized=False,
        edge_status=status,
        effective_scope={"branch_id": branch_id},
        corpus_version=corpus_version,
        branch_id=branch_id,
        evidence_refs=(expression.surface_expr_id,),
    )

    # Guard: the mapping MUST yield a matrix-legal edge. If this ever fires it is
    # a bug in the mapping above, surfaced loudly at construction rather than as a
    # deferred INVALID_EDGE_AUTHORITY at check time.
    reason = edge_authority_violation(body)
    assert reason is None, (
        "relation_edge_bridge produced a matrix-ILLEGAL edge "
        f"(authority_plane={body['authority_plane']!r}, "
        f"verification_level={body['verification_level']!r}, "
        f"replay_authorized={body['replay_authorized']!r}): {reason}"
    )
    return body


__all__ = ["reference_set_to_relation_edge"]
