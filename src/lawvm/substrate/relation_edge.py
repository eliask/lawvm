"""``lawvm.legal_relation_edge.v0`` — the universal proof-graded relation edge.

Spec: ``DISTRIBUTABLE_LAW_SUBSTRATE_DESIGN.md §25`` (round-12 ruling). The
substrate is not a compressed statute store; it is a **typed, proof-labeled
relation graph over legal text-state**. References (§14), amendment transitions,
lineage/renumber, incorporation, derivation, kinship, transposition, and
proposal branches all become **profiles** of ONE core relation algebra — not
special architectures.

**Hard invariant (the firewall, made enforceable): no relation edge may be
stronger than its evidence class.** ``kinship`` must never behave like
``verified_textual_derivation``; ``source_claimed_transposition`` must never
behave like ``conformance_assessment``. The enums in this module are inert
labels until :func:`edge_authority_violation` (a pure function) FORBIDS illegal
``(authority_plane, verification_level)`` combinations — and until the checker's
``_check_relation_edge_authority`` L-check turns a violation into a hard
``INVALID_EDGE_AUTHORITY`` integrity failure. That, not the vocabulary, is where
the firewall actually bites (§25.3): the edge-level analog of the
"silent divergence = type error" spine.

The edge id is content-addressed exactly like every other substrate object: it
is ``LeafHash("legal_relation_edge", body_without_edge_id)`` (§1.3 — the id is
never a member of the body it hashes), mirroring the ``content_leaf_hash`` /
``resolution_id`` pattern. The module is jurisdiction-NEUTRAL — ``corpus_version``
/ ``branch_id`` / ``policy_id`` are plain strings, never imported P1 objects.

The §14 cross-work reference resolution (:func:`lawvm.substrate.corpus
.make_cross_work_resolution`) is the FIRST profile of this edge (§25.4 —
extraction, not greenfield): its ``authority.surface_only=True,
replay_authorized=False`` posture maps to ``authority_plane=surface`` +
``verification_level=registry_resolved`` + ``replay_authorized=false``, a
combination the legality matrix ACCEPTS.
"""

from __future__ import annotations

import enum

from lawvm.substrate.canonical_json import JsonValue
from lawvm.substrate.roots import leaf_hash

SCHEMA_RELATION_EDGE = "lawvm.legal_relation_edge.v0"
_DOMAIN_RELATION_EDGE = "legal_relation_edge"


# --------------------------------------------------------------------------- #
# Closed enums (§25.1) — typed vocabularies, never stringly-typed dicts        #
# crossing the boundary. ``str``-valued ``Enum`` so a body field is a plain    #
# JSON string while the producer side is type-checked.                         #
# --------------------------------------------------------------------------- #


class RelationKind(enum.Enum):
    """The edge taxonomy (§25.2) — proof grades, not vibes."""

    SAME_CONTENT = "same_content"
    CITATION = "citation"
    INCORPORATES_BY_REFERENCE = "incorporates_by_reference"
    VERIFIED_TEXTUAL_DERIVATION = "verified_textual_derivation"
    SOURCE_CLAIMED_TRANSPOSITION = "source_claimed_transposition"
    CONFORMANCE_ASSESSMENT = "conformance_assessment"
    TIMELINESS_FACT = "timeliness_fact"
    KINSHIP = "kinship"
    BRANCH_DERIVATION = "branch_derivation"


class TargetSetSemantics(enum.Enum):
    """What the ``target_set`` MEANS (§25.1) — never an implicit convention."""

    SINGLE = "single"
    ALL_VALID = "all_valid"
    CANDIDATE_AMBIGUITY = "candidate_ambiguity"
    OPEN = "open"
    NO_ENUMERABLE_EXTENSION = "no_enumerable_extension"


class AuthorityPlane(enum.Enum):
    """Which plane the edge speaks for (§25.1). ``legal_state`` is the only
    plane that asserts the operative law; the rest are evidence / surface /
    overlay / projection planes that must NEVER masquerade as legal state."""

    LEGAL_STATE = "legal_state"
    SURFACE = "surface"
    EVIDENCE = "evidence"
    OVERLAY = "overlay"
    PROJECTION = "projection"


class VerificationLevel(enum.Enum):
    """The evidence class backing the edge (§25.1). The legality matrix binds
    this against :class:`AuthorityPlane` — a strong plane demands a strong
    evidence class, and a weak evidence class is barred from a strong plane."""

    HASH_IDENTITY = "hash_identity"
    SOURCE_ASSERTED = "source_asserted"
    REPLAY_VERIFIED = "replay_verified"
    DELTA_VERIFIED = "delta_verified"
    DATE_COMPUTABLE = "date_computable"
    REGISTRY_RESOLVED = "registry_resolved"
    INDUCED_SIMILARITY = "induced_similarity"
    EXTERNAL_ASSESSMENT = "external_assessment"
    UNVERIFIED = "unverified"


class EdgeStatus(enum.Enum):
    """The edge's resolution status (§25.1) — mirrors the §14 resolution
    statuses so the reference profile keeps its behavior unchanged (§25.4)."""

    RESOLVED = "resolved"
    QUALIFIED = "qualified"
    AMBIGUOUS = "ambiguous"
    OPEN = "open"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


# --------------------------------------------------------------------------- #
# The legality matrix (§25.3) — the teeth. A PURE function over the body.      #
# --------------------------------------------------------------------------- #

# legal_state ⇒ verification_level ∈ this set ∧ replay_authorized=true.
_LEGAL_STATE_ALLOWED_VERIFICATION: frozenset[str] = frozenset(
    {
        VerificationLevel.HASH_IDENTITY.value,
        VerificationLevel.REPLAY_VERIFIED.value,
        VerificationLevel.DELTA_VERIFIED.value,
        VerificationLevel.DATE_COMPUTABLE.value,
        VerificationLevel.REGISTRY_RESOLVED.value,
    }
)

# {induced_similarity, external_assessment, source_asserted, unverified} ⇒
# authority_plane ∈ {surface, evidence, overlay} ∧ replay_authorized=false.
_WEAK_VERIFICATION: frozenset[str] = frozenset(
    {
        VerificationLevel.INDUCED_SIMILARITY.value,
        VerificationLevel.EXTERNAL_ASSESSMENT.value,
        VerificationLevel.SOURCE_ASSERTED.value,
        VerificationLevel.UNVERIFIED.value,
    }
)
_WEAK_ALLOWED_PLANES: frozenset[str] = frozenset(
    {
        AuthorityPlane.SURFACE.value,
        AuthorityPlane.EVIDENCE.value,
        AuthorityPlane.OVERLAY.value,
    }
)


def edge_authority_violation(body: dict[str, JsonValue]) -> str | None:
    """Return a reason string iff the edge body is an ILLEGAL authority×evidence
    combination (§25.3), else ``None``. PURE — no I/O, no hashing.

    Two rules, exactly as the design freezes them:

    1. ``authority_plane == legal_state`` REQUIRES ``verification_level`` in
       ``{hash_identity, replay_verified, delta_verified, date_computable,
       registry_resolved}`` AND ``replay_authorized is True``. (A kinship /
       induced edge masquerading as legal_state must fail.)
    2. ``verification_level`` in ``{induced_similarity, external_assessment,
       source_asserted, unverified}`` REQUIRES ``authority_plane`` in
       ``{surface, evidence, overlay}`` AND ``replay_authorized is False``.

    The two rules together also forbid the cross-combination (e.g.
    ``source_asserted`` + ``legal_state``: rule 1 bars it because the level is
    not in the allowed set; rule 2 bars it because the plane is not weak-allowed
    — both fire, and we report the most specific first).
    """
    plane = body.get("authority_plane")
    level = body.get("verification_level")
    replay = body.get("replay_authorized")

    # Rule 1 — legal_state demands a strong evidence class + replay authority.
    if plane == AuthorityPlane.LEGAL_STATE.value:
        if level not in _LEGAL_STATE_ALLOWED_VERIFICATION:
            return (
                f"authority_plane=legal_state forbids verification_level={level!r} "
                f"(legal_state requires one of "
                f"{sorted(_LEGAL_STATE_ALLOWED_VERIFICATION)})"
            )
        if replay is not True:
            return (
                "authority_plane=legal_state requires replay_authorized=true "
                f"(got replay_authorized={replay!r})"
            )

    # Rule 2 — a weak evidence class is barred from any strong plane and from
    # claiming replay authority.
    if level in _WEAK_VERIFICATION:
        if plane not in _WEAK_ALLOWED_PLANES:
            return (
                f"verification_level={level!r} forbids authority_plane={plane!r} "
                f"(weak evidence requires one of {sorted(_WEAK_ALLOWED_PLANES)})"
            )
        if replay is not False:
            return (
                f"verification_level={level!r} requires replay_authorized=false "
                f"(got replay_authorized={replay!r})"
            )

    return None


# --------------------------------------------------------------------------- #
# The builder.                                                                  #
# --------------------------------------------------------------------------- #


def build_relation_edge(
    *,
    relation_kind: RelationKind,
    source_ref: str,
    target_set: tuple[str, ...],
    target_set_semantics: TargetSetSemantics,
    authority_plane: AuthorityPlane,
    verification_level: VerificationLevel,
    replay_authorized: bool,
    status: EdgeStatus,
    effective_scope: dict[str, JsonValue],
    corpus_version: str,
    branch_id: str = "actual",
    policy_id: str = "",
    evidence_refs: tuple[str, ...] = (),
    residual_refs: tuple[str, ...] = (),
    finding_refs: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    """Build ONE ``lawvm.legal_relation_edge.v0`` body dict (§25.1).

    The returned body is a canonical-JSON object carrying a content-addressed
    ``edge_id = LeafHash("legal_relation_edge", body_without_edge_id)`` — the id
    is computed over the body WITHOUT itself (§1.3), exactly as
    ``content_leaf_hash`` / ``resolution_id`` are. Enums are stored as their
    string ``.value`` so the body is plain JSON; ``target_set`` and the
    ``*_refs`` are sorted into deterministic order (the ``target_set`` is a SET
    by name — §25.1 — so the same logical edge yields a byte-identical
    ``edge_id`` regardless of caller order).

    The builder does NOT itself reject an illegal authority×evidence combination
    — a caller may legitimately construct one for a fire-drill / test fixture.
    Legality is enforced by :func:`edge_authority_violation` and, in production,
    by the checker's ``_check_relation_edge_authority`` L-check.
    """
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_RELATION_EDGE,
        "relation_kind": relation_kind.value,
        "source_ref": source_ref,
        "target_set": sorted(target_set),
        "target_set_semantics": target_set_semantics.value,
        "authority_plane": authority_plane.value,
        "verification_level": verification_level.value,
        "replay_authorized": bool(replay_authorized),
        "status": status.value,
        "effective_scope": dict(effective_scope),
        "corpus_version": corpus_version,
        "branch_id": branch_id,
        "policy_id": policy_id,
        "evidence_refs": sorted(evidence_refs),
        "residual_refs": sorted(residual_refs),
        "finding_refs": sorted(finding_refs),
    }
    body["edge_id"] = leaf_hash(_DOMAIN_RELATION_EDGE, body)
    return body


def recompute_edge_id(body: dict[str, JsonValue]) -> str:
    """Recompute ``edge_id`` from ``body`` WITHOUT its own ``edge_id`` (§1.3).

    The checker uses this to verify text/identity integrity of an edge row: the
    declared ``edge_id`` must equal ``LeafHash("legal_relation_edge", body \\
    {edge_id})``. Kept here so the preimage convention lives with the builder
    and the two cannot drift.
    """
    without_id = {key: value for key, value in body.items() if key != "edge_id"}
    return leaf_hash(_DOMAIN_RELATION_EDGE, without_id)
