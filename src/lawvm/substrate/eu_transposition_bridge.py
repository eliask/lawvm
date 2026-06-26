"""Bridge an FI ``TranspositionClaim`` to the EU directive relation edges (§25.8).

The substrate's EU directive mini-vertical emits, for a Finnish act that the
source text SAYS transposes an EU directive, ONLY the deterministic, verifiable
relation edges — never a substantive conformance / direct-effect / breach
conclusion (that is legal interpretation, outside the oracle). For ONE
:class:`~lawvm.finland.references.eu_transposition.TranspositionClaim` this
builds, where applicable, up to three ``lawvm.legal_relation_edge.v0`` bodies:

1. ``source_claimed_transposition`` — the citing act CLAIMS (in its own text) to
   transpose the directive. The claim is source-given, so the edge is
   ``authority_plane=evidence`` + ``verification_level=source_asserted`` +
   ``replay_authorized=false`` + ``status=resolved`` (the CLAIM itself is
   resolved/source-given — NOT the conformance). ``source_ref`` = the citing
   act; ``target_set`` = {directive CELEX} when bound, else the named-but-unbound
   directive surface with an honest non-resolved status.

2. ``timeliness_fact`` — the directive's transposition DEADLINE (curated demo
   seed) vs. the citing act's commencement date. ``authority_plane=evidence`` +
   ``verification_level=date_computable`` + ``replay_authorized=false``. Status
   ``resolved`` when both dates are known (the on-time/late verdict is a pure
   date comparison carried in ``effective_scope``); ``open`` when the deadline is
   unknown (an honest residual, NEVER a fabricated date).

3. ``conformance_assessment`` — NEVER a positive edge. A single ``open``-status
   edge with ``authority_plane=overlay`` + ``verification_level=external_assessment``
   + ``replay_authorized=false`` representing the ABSENCE of any conformance
   assessment ("conformance not assessed"). It asserts no correct/incorrect
   transposition; it records that the doctrine was NOT adjudicated.

All three combinations are matrix-legal (§25.3): evidence+source_asserted,
evidence+date_computable, overlay+external_assessment, all with
``replay_authorized=false``. Each body is asserted matrix-legal via
:func:`edge_authority_violation` before it is returned (a guard, not a hope),
exactly as the §14 reference bridge does.
"""

from __future__ import annotations

from lawvm.finland.references.eu_transposition import (
    TranspositionClaim,
    TranspositionStatus,
    transposition_deadline,
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

# Status mapping for the transposition-claim binding. The CLAIM is always
# source-given (resolved as a claim); the binding STATUS only governs the
# target-set semantics and whether a CELEX target exists — it never upgrades the
# evidence class (a claim is never more than source-asserted).
_BIND_TO_SEMANTICS: dict[TranspositionStatus, TargetSetSemantics] = {
    TranspositionStatus.RESOLVED: TargetSetSemantics.SINGLE,
    TranspositionStatus.AMBIGUOUS: TargetSetSemantics.CANDIDATE_AMBIGUITY,
    TranspositionStatus.STATUTE_ONLY: TargetSetSemantics.OPEN,
}


def _assert_legal(body: dict[str, JsonValue], label: str) -> dict[str, JsonValue]:
    """Guard: the body MUST be a matrix-legal edge (§25.3), else fail loudly here."""
    reason = edge_authority_violation(body)
    assert reason is None, (
        f"eu_transposition_bridge produced a matrix-ILLEGAL {label} edge "
        f"(authority_plane={body['authority_plane']!r}, "
        f"verification_level={body['verification_level']!r}, "
        f"replay_authorized={body['replay_authorized']!r}): {reason}"
    )
    return body


def _directive_target(claim: TranspositionClaim) -> tuple[str, TargetSetSemantics, EdgeStatus]:
    """The (target_ref, set_semantics, edge_status) for the directive a claim names.

    A BOUND directive → ``celex:<CELEX>`` target, SINGLE semantics, RESOLVED. A
    named-but-unbound directive (ambiguous nickname / registry miss) → the
    ``eu-nickname:<surface>`` target (tag, don't guess — never a fabricated
    CELEX), the binding's set semantics, and a non-resolved edge status that
    records the honest reason. The directive identity is NEVER dropped.
    """
    if claim.directive_celex is not None:
        return f"celex:{claim.directive_celex}", TargetSetSemantics.SINGLE, EdgeStatus.RESOLVED
    semantics = _BIND_TO_SEMANTICS[claim.transposition_status]
    edge_status = (
        EdgeStatus.AMBIGUOUS
        if claim.transposition_status is TranspositionStatus.AMBIGUOUS
        else EdgeStatus.QUALIFIED
    )
    return f"eu-nickname:{claim.directive_surface}", semantics, edge_status


def claimed_transposition_edge(
    claim: TranspositionClaim, *, corpus_version: str, branch_id: str = "actual"
) -> dict[str, JsonValue]:
    """Build the ``source_claimed_transposition`` edge for ONE claim (§25.8).

    The edge records that the citing act SAYS it transposes the directive — a
    source-given fact, evidence plane, source-asserted, replay NOT authorized. It
    is NEVER a conformance conclusion. ``status`` is ``resolved`` when the
    directive bound to a CELEX (the claim's TARGET is pinned), else the honest
    ambiguous/qualified status carrying the named-but-unbound surface.
    """
    target_ref, semantics, edge_status = _directive_target(claim)
    body = build_relation_edge(
        relation_kind=RelationKind.SOURCE_CLAIMED_TRANSPOSITION,
        source_ref=f"fi:act:{claim.citing_engine_id}",
        target_set=(target_ref,),
        target_set_semantics=semantics,
        authority_plane=AuthorityPlane.EVIDENCE,
        verification_level=VerificationLevel.SOURCE_ASSERTED,
        replay_authorized=False,
        edge_status=edge_status,
        effective_scope={
            "branch_id": branch_id,
            "claim_surface": claim.claim_surface,
            "directive_surface": claim.directive_surface,
            "binding_status": claim.transposition_status.value,
        },
        corpus_version=corpus_version,
        branch_id=branch_id,
    )
    return _assert_legal(body, "source_claimed_transposition")


def timeliness_edge(
    claim: TranspositionClaim,
    *,
    commencement_date: str,
    corpus_version: str,
    branch_id: str = "actual",
) -> dict[str, JsonValue]:
    """Build the ``timeliness_fact`` edge: deadline (seed) vs commencement (§25.8).

    The verdict is a PURE date comparison (``date_computable``):
      * deadline known + commencement <= deadline → ``on_time``;
      * deadline known + commencement > deadline   → ``late``;
      * deadline unknown                           → ``deadline_unknown`` and the
        edge ``status`` degrades to ``open`` (an honest residual — NEVER a
        fabricated date).
    The verdict + both dates live in ``effective_scope`` so a consumer sees the
    computation, not just the conclusion. Evidence plane, replay NOT authorized.
    """
    deadline = (
        transposition_deadline(claim.directive_celex)
        if claim.directive_celex is not None
        else None
    )
    scope: dict[str, JsonValue] = {
        "branch_id": branch_id,
        "commencement_date": commencement_date,
    }
    if deadline is None:
        verdict = "deadline_unknown"
        status = EdgeStatus.OPEN
        scope["transposition_deadline"] = None
    else:
        # ISO dates compare correctly as strings (zero-padded YYYY-MM-DD).
        verdict = "on_time" if commencement_date <= deadline else "late"
        status = EdgeStatus.RESOLVED
        scope["transposition_deadline"] = deadline
    scope["timeliness_verdict"] = verdict

    target_ref, _semantics, _ = _directive_target(claim)
    body = build_relation_edge(
        relation_kind=RelationKind.TIMELINESS_FACT,
        source_ref=f"fi:act:{claim.citing_engine_id}",
        target_set=(target_ref,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.EVIDENCE,
        verification_level=VerificationLevel.DATE_COMPUTABLE,
        replay_authorized=False,
        edge_status=status,
        effective_scope=scope,
        corpus_version=corpus_version,
        branch_id=branch_id,
    )
    return _assert_legal(body, "timeliness_fact")


def conformance_not_assessed_edge(
    claim: TranspositionClaim, *, corpus_version: str, branch_id: str = "actual"
) -> dict[str, JsonValue]:
    """Build the ``conformance_assessment`` edge — ALWAYS the "not assessed" form.

    The substrate NEVER emits a positive conformance edge ("correctly
    transposes" / "in breach" / "direct effect"). This edge is the honest
    ABSENCE of a semantic judgment: ``status=open`` (no assessment exists),
    ``authority_plane=overlay`` + ``verification_level=external_assessment`` (a
    conformance assessment, if it ever existed, would be an external semantic
    judgment — firewalled OFF the legal_state plane), ``replay_authorized=false``.
    A consumer therefore cannot mistake the evidentiary edges for a doctrinal
    conclusion: conformance was explicitly NOT adjudicated.
    """
    target_ref, _semantics, _ = _directive_target(claim)
    body = build_relation_edge(
        relation_kind=RelationKind.CONFORMANCE_ASSESSMENT,
        source_ref=f"fi:act:{claim.citing_engine_id}",
        target_set=(target_ref,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.OVERLAY,
        verification_level=VerificationLevel.EXTERNAL_ASSESSMENT,
        replay_authorized=False,
        edge_status=EdgeStatus.OPEN,
        effective_scope={
            "branch_id": branch_id,
            "conformance": "not_assessed",
            "note": (
                "conformance not assessed — the substrate exposes the "
                "evidentiary substrate (claimed transposition + timeliness) but "
                "does NOT adjudicate correct/incorrect transposition, direct "
                "effect, or breach (§25.8)"
            ),
        },
        corpus_version=corpus_version,
        branch_id=branch_id,
    )
    return _assert_legal(body, "conformance_assessment")


def transposition_claim_to_edges(
    claim: TranspositionClaim,
    *,
    commencement_date: str,
    corpus_version: str,
    branch_id: str = "actual",
) -> list[dict[str, JsonValue]]:
    """Map ONE transposition claim to its EU directive relation edges (§25.8).

    Returns the three edge bodies in a stable order:
    ``[source_claimed_transposition, timeliness_fact, conformance_assessment]``.
    The conformance edge is ALWAYS the "not assessed" residual — never a positive
    conclusion. Every body is matrix-legal by construction (asserted per edge).
    """
    return [
        claimed_transposition_edge(
            claim, corpus_version=corpus_version, branch_id=branch_id
        ),
        timeliness_edge(
            claim,
            commencement_date=commencement_date,
            corpus_version=corpus_version,
            branch_id=branch_id,
        ),
        conformance_not_assessed_edge(
            claim, corpus_version=corpus_version, branch_id=branch_id
        ),
    ]


__all__ = [
    "claimed_transposition_edge",
    "conformance_not_assessed_edge",
    "timeliness_edge",
    "transposition_claim_to_edges",
]
