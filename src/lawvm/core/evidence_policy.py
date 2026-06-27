"""EvidencePolicyRegistry — versioned, hashed, declarative policy for provenance graph.

Piece 2 of Step 2 (UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §4 + §13).

Types
-----
EvidencePolicyRegistry  — versioned + hashed container of EvidenceGraphPredicates
EvidenceGraphPredicate  — per-claim-kind evidence policy (required + forbidden clauses)
PolicyExpr              — declarative DSL primitive (data, not Python closure)
IndependenceDimension   — structural independence axes

DSL constructor helpers (ergonomic authoring)
---------------------------------------------
exists / none / count_distinct_at_least / within_time / signed_by /
not_retracted / reachable / independent / materials_match_dependencies

Design constraints (§12 anti-patterns)
---------------------------------------
  - NO arbitrary Python closures in PolicyExpr.  Evaluator lives in evidence_kernel.py.
  - PolicyExpr.op must be in KNOWN_OPS; unknown ops hard-fail at evaluation time.
  - Registry hash is deterministic: same predicates → same hash across processes.
  - No Pydantic; frozen dataclasses + module-level Python dicts.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    # Forward-only import to resolve the ``Finding`` return-annotation type
    # without introducing a circular import at module-load time.
    from lawvm.core.phase_result import Finding


Json = Any

KNOWN_OPS: frozenset[str] = frozenset({
    "exists",
    "none",
    "count_distinct_at_least",
    "all",
    "within_time",
    "signed_by",
    "not_retracted",
    "reachable",
    "independent",
    "materials_match_dependencies",
})


# ---------------------------------------------------------------------------
# PolicyExpr — declarative DSL primitive (data only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyExpr:
    """One declarative DSL clause.

    ``op`` must be one of KNOWN_OPS.  ``args`` is a frozen mapping of
    operator-specific parameters.  The evaluator in evidence_kernel.py
    interprets these — no Python closures live here.

    Unknown ``op`` values are accepted at construction but will hard-fail
    when the evaluator encounters them (§12.3).
    """

    op: str
    args: Mapping[str, Json]

    def __post_init__(self) -> None:
        if not isinstance(self.op, str) or not self.op:
            raise ValueError("PolicyExpr.op must be a non-empty string")
        if not isinstance(self.args, Mapping):
            raise ValueError("PolicyExpr.args must be a Mapping")

    def canonical_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-encodable dict for hashing."""
        return {"op": self.op, "args": dict(sorted(self.args.items()))}


# ---------------------------------------------------------------------------
# IndependenceDimension enum
# ---------------------------------------------------------------------------


class IndependenceDimension(Enum):
    PRODUCER_ID = "producer_id"
    PRODUCER_KIND = "producer_kind"
    MODEL_FAMILY = "model_family"
    PROMPT_PATH = "prompt_path"
    PARENT_ATTESTATION = "parent_attestation"
    PROPOSAL_BATCH = "proposal_batch"
    REVIEW_CLUSTER = "review_cluster"


# ---------------------------------------------------------------------------
# EvidenceGraphPredicate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceGraphPredicate:
    """Evidence policy for one claim kind.

    ``required`` clauses must all be satisfied.
    ``forbidden`` clauses must all be absent.
    """

    predicate_id: str
    claim_kind: str
    required: tuple[PolicyExpr, ...]
    forbidden: tuple[PolicyExpr, ...] = ()

    def __post_init__(self) -> None:
        if not self.predicate_id:
            raise ValueError("EvidenceGraphPredicate.predicate_id must be non-empty")
        if not self.claim_kind:
            raise ValueError("EvidenceGraphPredicate.claim_kind must be non-empty")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "claim_kind": self.claim_kind,
            "required": [e.canonical_dict() for e in self.required],
            "forbidden": [e.canonical_dict() for e in self.forbidden],
        }


# ---------------------------------------------------------------------------
# EvidencePolicyRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidencePolicyRegistry:
    """Versioned, hashed container of EvidenceGraphPredicates.

    ``registry_hash`` must equal _compute_registry_hash(self) — enforced on
    load; callers should use ``build()`` to construct valid registries.
    """

    registry_id: str
    registry_version: str
    registry_hash: str
    predicates: tuple[EvidenceGraphPredicate, ...]

    def __post_init__(self) -> None:
        if not self.registry_id:
            raise ValueError("EvidencePolicyRegistry.registry_id must be non-empty")
        if not self.registry_version:
            raise ValueError("EvidencePolicyRegistry.registry_version must be non-empty")
        if not self.registry_hash:
            raise ValueError("EvidencePolicyRegistry.registry_hash must be non-empty")

    def get_predicate(self, predicate_id: str) -> EvidenceGraphPredicate | None:
        for p in self.predicates:
            if p.predicate_id == predicate_id:
                return p
        return None

    def get_predicate_for_claim_kind(self, claim_kind: str) -> EvidenceGraphPredicate | None:
        for p in self.predicates:
            if p.claim_kind == claim_kind:
                return p
        return None

    @classmethod
    def build(
        cls,
        registry_id: str,
        registry_version: str,
        predicates: tuple[EvidenceGraphPredicate, ...],
    ) -> "EvidencePolicyRegistry":
        """Construct a registry with computed hash."""
        h = _compute_registry_hash(registry_id, registry_version, predicates)
        return cls(
            registry_id=registry_id,
            registry_version=registry_version,
            registry_hash=h,
            predicates=predicates,
        )

    def verify_hash(self) -> None:
        """Raise ValueError if stored hash does not match recomputed hash."""
        expected = _compute_registry_hash(
            self.registry_id, self.registry_version, self.predicates
        )
        if self.registry_hash != expected:
            raise ValueError(
                f"EvidencePolicyRegistry hash mismatch: stored {self.registry_hash!r} "
                f"but recomputed {expected!r}. The registry may have been tampered with."
            )


# ---------------------------------------------------------------------------
# Registry hash computation
# ---------------------------------------------------------------------------


def _compute_registry_hash(
    registry_id: str,
    registry_version: str,
    predicates: tuple[EvidenceGraphPredicate, ...],
) -> str:
    """Deterministic SHA-256 over canonical predicate serialization.

    Sorted by predicate_id for determinism.  Hash domain includes
    registry_id + registry_version + all predicates.
    """
    sorted_predicates = sorted(predicates, key=lambda p: p.predicate_id)
    payload = {
        "registry_id": registry_id,
        "registry_version": registry_version,
        "predicates": [p.canonical_dict() for p in sorted_predicates],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DSL constructor helpers (ergonomic policy authoring)
# ---------------------------------------------------------------------------


def exists(attestation_kind: str, **filters: Any) -> PolicyExpr:
    """Require that at least one attestation of ``attestation_kind`` exists."""
    args: dict[str, Any] = {"attestation_kind": attestation_kind}
    args.update(filters)
    return PolicyExpr(op="exists", args=args)


def none(attestation_kind: str, **filters: Any) -> PolicyExpr:
    """Require that no attestation of ``attestation_kind`` (matching filters) exists."""
    args: dict[str, Any] = {"attestation_kind": attestation_kind}
    args.update(filters)
    return PolicyExpr(op="none", args=args)


def count_distinct_at_least(attestation_kind: str, path: str, n: int) -> PolicyExpr:
    """Require that ``count_distinct(attestations(kind=K), path=P) >= n``."""
    return PolicyExpr(
        op="count_distinct_at_least",
        args={"attestation_kind": attestation_kind, "path": path, "n": n},
    )


def within_time(interval_start: str, interval_end: str | None = None) -> PolicyExpr:
    """Require attestation produced_at to fall within the given interval."""
    return PolicyExpr(
        op="within_time",
        args={"interval_start": interval_start, "interval_end": interval_end},
    )


def signed_by(keyring: str) -> PolicyExpr:
    """Require attestation to carry a valid signature from the given keyring."""
    return PolicyExpr(op="signed_by", args={"keyring": keyring})


def not_retracted(subject: str = "self") -> PolicyExpr:
    """Require that the subject (or 'self') has no retracted attestation."""
    return PolicyExpr(op="not_retracted", args={"subject": subject})


def reachable(subject: str, edge_types: tuple[str, ...]) -> PolicyExpr:
    """Require that ``subject`` is reachable from the claim via edge_types."""
    return PolicyExpr(op="reachable", args={"subject": subject, "edge_types": list(edge_types)})


def independent(attestation_kind: str, by: tuple[str, ...]) -> PolicyExpr:
    """Require structural independence of attestations of given kind across the ``by`` dimensions."""
    return PolicyExpr(
        op="independent",
        args={"attestation_kind": attestation_kind, "by": list(by)},
    )


def materials_match_dependencies() -> PolicyExpr:
    """Require that attestation materials match the assertion's dependency_refs."""
    return PolicyExpr(op="materials_match_dependencies", args={})


# ---------------------------------------------------------------------------
# Structural independence checker (§4.2)
# ---------------------------------------------------------------------------


from lawvm.core.provenance_graph import ProvenanceAttestation


def check_independence(
    attestations: tuple[ProvenanceAttestation, ...],
    by: tuple[IndependenceDimension, ...],
) -> bool:
    """Return True if all ``attestations`` are structurally independent along each ``by`` dimension.

    Independence means: for each dimension, no two attestations share the same value.
    If there are fewer than 2 attestations the check trivially passes.
    """
    if len(attestations) < 2:
        return True
    for dim in by:
        values = [_extract_dimension_value(a, dim) for a in attestations]
        if len(set(values)) < len(values):
            return False
    return True


def _extract_dimension_value(a: ProvenanceAttestation, dim: IndependenceDimension) -> object:
    if dim == IndependenceDimension.PRODUCER_ID:
        return a.producer.producer_id
    if dim == IndependenceDimension.PRODUCER_KIND:
        return a.producer.producer_kind
    if dim == IndependenceDimension.MODEL_FAMILY:
        return a.producer.metadata.get("model_family", a.producer.producer_id)
    if dim == IndependenceDimension.PROMPT_PATH:
        return a.producer.metadata.get("prompt_path", "")
    if dim == IndependenceDimension.PARENT_ATTESTATION:
        return a.payload.get("parent_attestation_id", "")
    if dim == IndependenceDimension.PROPOSAL_BATCH:
        return a.payload.get("proposal_batch_id", "")
    if dim == IndependenceDimension.REVIEW_CLUSTER:
        return a.payload.get("review_cluster_id", "")
    return ""


# ---------------------------------------------------------------------------
# JSON round-trip (for data/fi/v1/evidence_policy/*.json)
# ---------------------------------------------------------------------------


def registry_to_dict(reg: EvidencePolicyRegistry) -> dict[str, Any]:
    return {
        "registry_id": reg.registry_id,
        "registry_version": reg.registry_version,
        "registry_hash": reg.registry_hash,
        "predicates": [_predicate_to_dict(p) for p in reg.predicates],
    }


def _predicate_to_dict(p: EvidenceGraphPredicate) -> dict[str, Any]:
    return {
        "predicate_id": p.predicate_id,
        "claim_kind": p.claim_kind,
        "required": [_expr_to_dict(e) for e in p.required],
        "forbidden": [_expr_to_dict(e) for e in p.forbidden],
    }


def _expr_to_dict(e: PolicyExpr) -> dict[str, Any]:
    return {"op": e.op, "args": dict(e.args)}


def registry_from_dict(d: dict[str, Any]) -> EvidencePolicyRegistry:
    predicates = tuple(_predicate_from_dict(p) for p in d.get("predicates", []))
    reg = EvidencePolicyRegistry(
        registry_id=d["registry_id"],
        registry_version=d["registry_version"],
        registry_hash=d["registry_hash"],
        predicates=predicates,
    )
    reg.verify_hash()
    return reg


def _predicate_from_dict(d: dict[str, Any]) -> EvidenceGraphPredicate:
    return EvidenceGraphPredicate(
        predicate_id=d["predicate_id"],
        claim_kind=d["claim_kind"],
        required=tuple(_expr_from_dict(e) for e in d.get("required", [])),
        forbidden=tuple(_expr_from_dict(e) for e in d.get("forbidden", [])),
    )


def _expr_from_dict(d: dict[str, Any]) -> PolicyExpr:
    return PolicyExpr(op=d["op"], args=d.get("args", {}))


# --------------------------------------------------------------------------- #
# D12 — EVID.ATTESTATION_POLICY_GAP_TOTALITY audit                             #
# --------------------------------------------------------------------------- #
# Per audit_impl_D12.md + AGENTS.md §0/§2.10: an attestation policy id cited
# by a proof-carrying output but absent from the loaded
# EvidencePolicyRegistry is a TRUST-SURFACE violation — a forged policy id, not
# a soft mismatch. The gap must be a typed first-class object, never a silent
# drop. This audit consumes a loaded registry plus the certificate-bundle
# projection rows and returns one AttestationPolicyGap per cited-but-unknown
# policy id.
#
# PLANE & DISCIPLINE. This audit lives in the evidence kernel plane: it reads
# the registry's known predicates plus the projection proof_rows, returns
# :class:`AttestationPolicyGap` carriers, and never mutates the registry or
# the projection. The bundle wire (audit_impl_D12 §3) decides whether each
# gap becomes a strict-mode bundle-abort or quirks non-blocking residue; the
# audit itself only reports the gap.
#
# WHAT THIS DOES NOT YET DO (honest scope, per the staged-wire discipline of
# D7/D8/D11):
#   * Wire into ``tools/certificate_bundle.py:~2404`` (the existing policy-hash
#     commit block) is staged as a follow-up commit. The audit runs from the
#     unit/helper lane only until the wire lands; declared honestly in
#     ``NO_FIRE_DRILL_YET``.
#   * The cited-policy collector inspects ``authorization_rule_id`` and
#     ``detail.evidence_kernel.policy_id`` paths. Frontends that emit
#     policy cites under different detail keys must teach the collector.
#     Until then those cites are invisible — but the §1.8 receipt contract
#     for THIS collector stays closed (no fuzzy-match fallback).

EVID_ATTESTATION_POLICY_GAP_TOTALITY_RULE_ID = (
    "evid_attestation_policy_gap_totality"
)
EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE = "EVID.UNKNOWN_ATTESTATION_POLICY"
_EVID_ATTESTATION_AUDIT_STAGE = "evidence_kernel"
_EVID_ATTESTATION_AUDIT_OWNER = "evidence_kernel"
_EVID_ATTESTATION_PROOF_RULE_ID_KEY = "authorization_rule_id"
_EVID_ATTESTATION_PROOF_KERNEL_PATH = ("detail", "evidence_kernel", "policy_id")
_EVID_ATTESTATION_PROOF_ROW_ID_KEY = "row_id"


@dataclass(frozen=True, slots=True)
class AttestationPolicyGap:
    """One cited-but-unknown attestation policy id (audit D12 — carrier).

    A typed carrier (§1.9) so a triager can answer §3.2's evidence path
    (which cited_policy_id was unknown + where was the cite) without
    re-running extraction.

    Fields:
    * ``cited_policy_id``: the unknown predicate_id cited by the proof row.
    * ``cite_source``: ``authorization_rule_id`` | ``proof_id`` | ``row_id``.
    * ``cite_location``: a bundle-path / row-id string locating the cite.
    * ``rule_id``: defaults to the
      :data:`EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE` lowering so the
      projection-to-Finding step has a stable owner.
    """

    cited_policy_id: str
    cite_source: str
    cite_location: str
    rule_id: str = EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE


def known_predicate_ids(registry: EvidencePolicyRegistry) -> frozenset[str]:
    """Return the set of ``predicate_id`` strings the loaded registry admits.

    Single authority surface: a ``predicate_id`` is registered IF AND ONLY IF
    it appears as a ``predicates[].predicate_id`` member of the loaded
    registry (per audit_impl_D12 §9 risk: dynamically-constructed ids must
    persist into the registry JSON; there is no static-prefix escape hatch
    for attestation-policy ids — they are deliberately closed).
    """
    return frozenset(p.predicate_id for p in registry.predicates)


def _extract_cite_location(row: Mapping[str, Any]) -> str:
    """Locate the cite in one row so the gap record carries provenance.

    Prefers an explicit ``row_id`` field; falls back to a synthesized
    ``"<authorization_rule_id>"`` (the row's authority rule) so a triager
    can route the finding back to its source.
    """
    row_id = str(row.get(_EVID_ATTESTATION_PROOF_ROW_ID_KEY) or "")
    if row_id:
        return row_id
    auth_rule = str(row.get(_EVID_ATTESTATION_PROOF_RULE_ID_KEY) or "")
    if auth_rule:
        return f"<{auth_rule}>"
    return "<unknown_cite>"


def collect_cited_attestation_policy_ids(
    proof_rows: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    """Extract every policy id cited across the proof/certification surface.

    Cite locations recognised (closed set per the impl-spec §3):
      * ``row["authorization_rule_id"]`` — the rule_id cited by an
        :class:`ExecutionAuthorization` (the apply-authority path); and
      * ``row["detail"]["evidence_kernel"]["policy_id"]`` — the nested
        attestation-policy id the existent ExecutionAuthorization's
        EvidenceKernel projection carries.

    Frontends emitting policy cites under other detail keys must teach the
    collector; until then those cites are invisible. The collector does NOT
    raise on unknown keys (§1.10 fail-loud for the registry-vs-cite gap lives
    in :func:`audit_attestation_policy_gap` itself, not the collector).
    """
    cited: set[str] = set()
    for row in proof_rows:
        auth_rule = str(row.get(_EVID_ATTESTATION_PROOF_RULE_ID_KEY) or "")
        if auth_rule:
            cited.add(auth_rule)
        # Walk the nested EvidenceKernel.policy_id path.
        cursor: Any = row
        for key in _EVID_ATTESTATION_PROOF_KERNEL_PATH:
            if not isinstance(cursor, Mapping):
                cursor = None
                break
            cursor = cursor.get(key)
        if isinstance(cursor, str) and cursor:
            cited.add(cursor)
    return frozenset(cited)


def audit_attestation_policy_gap(
    registry: EvidencePolicyRegistry,
    proof_rows: Sequence[Mapping[str, Any]],
) -> tuple[AttestationPolicyGap, ...]:
    """Return one :class:`AttestationPolicyGap` per cited policy id NOT in the registry.

    Per AGENTS.md §0/§2.10: a cited-by-unknown predicate_id is a FORGED policy
    cite, not a soft mismatch. Empty tuple is the success witness; the audit
    never returns ``None`` (§1.10 fail-loud discipline — the absence of a
    gap is a valid result, but a None return would be silent folklore).
    """
    known = known_predicate_ids(registry)
    gaps: list[AttestationPolicyGap] = []
    for row in proof_rows:
        # authorization_rule_id cite path
        auth_rule = str(row.get(_EVID_ATTESTATION_PROOF_RULE_ID_KEY) or "")
        if auth_rule and auth_rule not in known:
            gaps.append(
                AttestationPolicyGap(
                    cited_policy_id=auth_rule,
                    cite_source=_EVID_ATTESTATION_PROOF_RULE_ID_KEY,
                    cite_location=_extract_cite_location(row),
                )
            )
        # Nested detail.evidence_kernel.policy_id cite path
        cursor: Any = row
        for key in _EVID_ATTESTATION_PROOF_KERNEL_PATH:
            if not isinstance(cursor, Mapping):
                cursor = None
                break
            cursor = cursor.get(key)
        if isinstance(cursor, str) and cursor and cursor not in known:
            # Deduplicate against the auth_rule cite on the same row
            # (if both paths cite the same id, the auth_rule gap is the
            # canonical witness and we don't double-fire).
            if cursor != auth_rule:
                gaps.append(
                    AttestationPolicyGap(
                        cited_policy_id=cursor,
                        cite_source="evidence_kernel_policy_id",
                        cite_location=_extract_cite_location(row),
                    )
                )
    return tuple(gaps)


def attestation_policy_gap_findings(
    gaps: Sequence[AttestationPolicyGap],
) -> tuple["Finding", ...]:
    """Project each :class:`AttestationPolicyGap` to a violation Finding.

    Spec §5: hard-fail emission in strict mode; non-blocking residue in quirks.
    The Findings are ``role="violation"`` + ``blocking=True`` per the contract:
    a forged policy id is a contract break, not informational. The wire
    consumer (certificate bundle emission) decides whether the bundle write
    aborts.
    """
    from lawvm.core.observation_registry import get_finding_spec  # noqa: PLC0415
    from lawvm.core.phase_result import Finding, VIOLATION_ROLE  # noqa: PLC0415

    findings: list[Finding] = []
    for gap in gaps:
        spec = get_finding_spec(EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE)
        if spec is None:  # pragma: no cover - defensive: registry-row present
            continue
        detail: dict[str, Any] = {
            "cited_policy_id": gap.cited_policy_id,
            "cite_source": gap.cite_source,
            "cite_location": gap.cite_location,
            "rule_id": EVID_ATTESTATION_POLICY_GAP_TOTALITY_RULE_ID,
            "reason": (
                "ExecutionAuthorization cites an attestation policy id that is "
                "not in the loaded EvidencePolicyRegistry; the cited "
                "predicate_id is unknown (forged attestation-policy cite per "
                "AGENTS.md §0/§2.10 — never a silent drop)"
            ),
        }
        findings.append(
            Finding(
                kind=EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE,
                role=VIOLATION_ROLE,
                stage=_EVID_ATTESTATION_AUDIT_STAGE,
                detail=detail,
                source_statute=gap.cite_location,
                blocking=True,
            )
        )
    return tuple(findings)


__all__ = [
    "EVID_ATTESTATION_POLICY_GAP_TOTALITY_RULE_ID",
    "EVID_UNKNOWN_ATTESTATION_POLICY_FINDING_CODE",
    "AttestationPolicyGap",
    "attestation_policy_gap_findings",
    "audit_attestation_policy_gap",
    "collect_cited_attestation_policy_ids",
    "known_predicate_ids",
]
