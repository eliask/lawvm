"""Shared mutation-boundary proof projection.

This module projects passive mutation-boundary accounting into a typed proof
object. It does not authorize replay; callers decide what proof status is
required before promoting evidence to execution.
"""

from __future__ import annotations

import os
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Mapping, Sequence, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.phase_result import Finding
from lawvm.core.mutation_accounting import (
    MUTATION_ACCOUNTING_HARD_CODES,
    MutationInvariantReport,
)
from lawvm.core.ir import IRNode, LegalOperation
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    diff_ir_paths_identity_pruned,
    operation_storage_boundary_prefixes,
    partition_changed_paths,
    tree_path_to_diagnostic_string,
    validate_tree_path,
)

MutationBoundaryProofStatus = Literal[
    "proved",
    "proved_with_allowance",
    "unresolved",
    "violated",
]

_VALID_STATUSES = frozenset(MutationBoundaryProofStatus.__args__)
_UNRESOLVED_RESULT_CODES = frozenset(
    {
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    }
)
_MUTATION_BOUNDARY_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "mutation_boundary_proof_as_replay_authorization",
    "proved_boundary_as_target_resolution_proof",
    "unresolved_boundary_as_successful_apply",
)


@dataclass(frozen=True, slots=True)
class MutationBoundaryProof:
    """Typed proof surface for changed-path containment.

    A proved boundary means the observed changed paths are covered by the
    declared target/effect region plus declared allowance, recovery, or
    migration paths. It is a proof/reporting object, not replay authority.
    """

    proof_id: str
    jurisdiction: str
    materialization_surface: str
    operation_id: str
    owner_phase: str
    rule_id: str
    boundary_proof_status: MutationBoundaryProofStatus
    helper: str = ""
    outcome: str = ""
    selected_target_paths: TreePaths = ()
    allowed_mutation_regions: TreePaths = ()
    changed_paths: TreePaths = ()
    covered_changed_paths: TreePaths = ()
    unexplained_changed_paths: TreePaths = ()
    declared_allowance_paths: TreePaths = ()
    declared_recovery_paths: TreePaths = ()
    declared_recovery_rule_ids: tuple[str, ...] = ()
    declared_migration_paths: TreePaths = ()
    declared_migration_rule_ids: tuple[str, ...] = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()
    result_codes: tuple[str, ...] = ()
    path_set_invariant_holds: bool = True
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proof_id", _required_string("proof_id", self.proof_id))
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(
            self,
            "materialization_surface",
            _required_string("materialization_surface", self.materialization_surface),
        )
        object.__setattr__(self, "operation_id", _required_string("operation_id", self.operation_id))
        object.__setattr__(self, "owner_phase", _required_string("owner_phase", self.owner_phase))
        object.__setattr__(self, "rule_id", _required_string("rule_id", self.rule_id))
        status = _required_string("status", self.boundary_proof_status)
        if status not in _VALID_STATUSES:
            raise ValueError(
                "MutationBoundaryProof.status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        object.__setattr__(self, "boundary_proof_status", status)
        for field_name, paths in (
            ("selected_target_paths", self.selected_target_paths),
            ("allowed_mutation_regions", self.allowed_mutation_regions),
            ("changed_paths", self.changed_paths),
            ("covered_changed_paths", self.covered_changed_paths),
            ("unexplained_changed_paths", self.unexplained_changed_paths),
            ("declared_allowance_paths", self.declared_allowance_paths),
            ("declared_recovery_paths", self.declared_recovery_paths),
            ("declared_migration_paths", self.declared_migration_paths),
        ):
            object.__setattr__(
                self,
                field_name,
                _validated_tree_paths(
                    f"MutationBoundaryProof.{field_name}",
                    paths,
                ),
            )
        for field_name, values in (
            ("declared_recovery_rule_ids", self.declared_recovery_rule_ids),
            ("declared_migration_rule_ids", self.declared_migration_rule_ids),
            ("matched_allowance_rule_ids", self.matched_allowance_rule_ids),
            ("result_codes", self.result_codes),
            ("forbidden_shortcuts", self.forbidden_shortcuts),
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(f"MutationBoundaryProof.{field_name}", values),
            )
        if not isinstance(self.path_set_invariant_holds, bool):
            raise ValueError("MutationBoundaryProof.path_set_invariant_holds must be a bool")
        object.__setattr__(self, "safe_default", _required_string("safe_default", self.safe_default))
        if not self.forbidden_shortcuts:
            raise ValueError("MutationBoundaryProof.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("MutationBoundaryProof.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    @classmethod
    def from_mutation_invariant_report(
        cls,
        report: MutationInvariantReport,
        *,
        proof_id: str,
        jurisdiction: str,
        materialization_surface: str,
        owner_phase: str,
        safe_default: str,
        forbidden_shortcuts: tuple[str, ...],
    ) -> MutationBoundaryProof:
        result_codes = tuple(result.code for result in report.results)
        return cls(
            proof_id=proof_id,
            jurisdiction=jurisdiction,
            materialization_surface=materialization_surface,
            operation_id=report.op_id,
            owner_phase=owner_phase,
            rule_id=_rule_id_for_report(report),
            boundary_proof_status=_status_for_report(report),
            helper=report.helper,
            outcome=report.outcome,
            selected_target_paths=report.allowed_roots,
            allowed_mutation_regions=report.permitted_paths,
            changed_paths=report.changed_paths,
            covered_changed_paths=report.covered_changed_paths,
            unexplained_changed_paths=report.unexplained_changed_paths,
            declared_allowance_paths=report.declared_allowance_paths,
            declared_recovery_paths=report.declared_recovery_paths,
            declared_recovery_rule_ids=report.declared_recovery_rule_ids,
            declared_migration_paths=report.declared_migration_paths,
            declared_migration_rule_ids=report.declared_migration_rule_ids,
            matched_allowance_rule_ids=report.matched_allowance_rule_ids,
            result_codes=result_codes,
            path_set_invariant_holds=report.path_set_invariant_holds,
            safe_default=safe_default,
            forbidden_shortcuts=forbidden_shortcuts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "jurisdiction": self.jurisdiction,
            "materialization_surface": self.materialization_surface,
            "operation_id": self.operation_id,
            "owner_phase": self.owner_phase,
            "rule_id": self.rule_id,
            "boundary_proof_status": self.boundary_proof_status,
            "helper": self.helper,
            "outcome": self.outcome,
            "selected_target_paths": _path_strings(self.selected_target_paths),
            "allowed_mutation_regions": _path_strings(self.allowed_mutation_regions),
            "changed_paths": _path_strings(self.changed_paths),
            "covered_changed_paths": _path_strings(self.covered_changed_paths),
            "unexplained_changed_paths": _path_strings(self.unexplained_changed_paths),
            "declared_allowance_paths": _path_strings(self.declared_allowance_paths),
            "declared_recovery_paths": _path_strings(self.declared_recovery_paths),
            "declared_recovery_rule_ids": list(self.declared_recovery_rule_ids),
            "declared_migration_paths": _path_strings(self.declared_migration_paths),
            "declared_migration_rule_ids": list(self.declared_migration_rule_ids),
            "matched_allowance_rule_ids": list(self.matched_allowance_rule_ids),
            "result_codes": list(self.result_codes),
            "path_set_invariant_holds": self.path_set_invariant_holds,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


# The registered per-op blocking finding code for LS-01: a landed op whose
# observed changed paths are NOT a subset of (target ∪ declared migration ∪
# declared recovery ∪ declared editorial projection). Registered in
# ``core/observation_registry.py`` as a blocking violation; the producer
# (apply_resolved_op) and any consumer import this literal so they cannot drift.
MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE = "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP"
# The non-blocking quirks-mode accounting companion: the same per-op boundary
# escape, recorded (not blocked) so a permissive profile still leaves a visible
# receipt of the unowned mutation.
MUTATION_BOUNDARY_FINDING_AT_OP_CODE = "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP"

PerOpBoundaryStatus = Literal["within_boundary", "out_of_boundary"]


@dataclass(frozen=True, slots=True)
class PerOpMutationBoundaryVerdict:
    """Typed per-op mutation-boundary verdict (LS-01).

    ``boundary_status == "out_of_boundary"`` ⟺ the op landed at least one changed
    tree path outside its declared mutation boundary
    (target ∪ declared_migration ∪ declared_recovery ∪ declared_editorial_projection).
    That is a §1.0 per-op violation: under strict it BLOCKS the op, under quirks
    it is recorded as a non-blocking accounting finding. ``out_of_boundary_paths``
    carries the offending diagnostic-string paths (self-evidencing).

    The disposition field is namespaced ``boundary_status`` (not a bare ``status``)
    per the public-schema status-vocabulary discipline (audit-registry VOCAB-02 /
    naming-hygiene Gate 46a).
    """

    op_id: str
    boundary_status: PerOpBoundaryStatus
    changed_paths: tuple[str, ...]
    out_of_boundary_paths: tuple[str, ...]

    @property
    def within_boundary(self) -> bool:
        return self.boundary_status == "within_boundary"


def verify_per_op(
    before: IRNode,
    after: IRNode,
    op: LegalOperation,
    *,
    op_id: str,
    declared_migration_prefixes: Sequence[TreePath] = (),
    declared_recovery_prefixes: Sequence[TreePath] = (),
    declared_editorial_projection_prefixes: Sequence[TreePath] = (),
    strip_root_prefix: TreePath = (),
) -> PerOpMutationBoundaryVerdict:
    """Per-op mutation-boundary REJECT gate (LS-01 / §1.0).

    Computes the operation's storage mutation boundary
    (target ∪ the supplied declared migration / recovery / editorial-projection
    prefixes) and diffs ``before``→``after``. Any observed changed path outside
    that boundary is an ``out_of_boundary`` per-op violation. The aggregate
    fold-end boundary closure (LS-02) only proves the SUM balances; this asserts
    the containment PER OP so a sibling-path edit fails loud on the op that did it
    rather than being absorbed into an aggregate that still nets to zero.

    ``strip_root_prefix`` lets the caller drop a leading materialization wrapper
    step (the FI replay IR is rooted under an unlabeled ``("hcontainer", "")``
    wrapper that tree-diff paths carry but op-nominal LegalAddress targets do
    not) so the observed and declared surfaces align — exactly the normalization
    ``mutation_accounting`` already applies. Without it every wrapped diff path is
    spuriously "out of boundary".

    Pure projection: it computes the verdict and never mutates or emits. The
    production apply site decides the strict (block) / quirks (record) disposition.
    """
    declared_extra: tuple[TreePath, ...] = (
        *declared_migration_prefixes,
        *declared_recovery_prefixes,
        *declared_editorial_projection_prefixes,
    )
    allowed_prefixes = operation_storage_boundary_prefixes(op, declared_extra)
    # MUST use the identity-pruned diff: verify_per_op runs on EVERY op of every
    # statute, and replay IR is CoW-persistent so untouched subtrees are identity-
    # shared and legitimately short-circuit. The non-pruned diff_ir_paths walks the
    # whole tree per op (~+30% FI bench wall; regression re-homed here 2026-06-30 in
    # e6f217e1a, fixed 2026-07-01). Every other consumer already uses the pruned twin.
    changed_paths = tuple(
        _strip_leading_prefix(path, strip_root_prefix)
        for path in diff_ir_paths_identity_pruned(before, after)
    )
    partition = partition_changed_paths(changed_paths, allowed_prefixes)
    out_of_boundary = tuple(
        _cached_tree_path_to_diagnostic_string(path)
        for path in partition.unexplained_changed_paths
    )
    return PerOpMutationBoundaryVerdict(
        op_id=str(op_id or ""),
        boundary_status="out_of_boundary" if out_of_boundary else "within_boundary",
        changed_paths=tuple(
            _cached_tree_path_to_diagnostic_string(path) for path in changed_paths
        ),
        out_of_boundary_paths=out_of_boundary,
    )


def _strip_leading_prefix(path: TreePath, prefix: TreePath) -> TreePath:
    """Drop a leading wrapper prefix from a tree path when present."""
    if prefix and path[: len(prefix)] == prefix:
        return path[len(prefix):]
    return path


@dataclass(frozen=True, slots=True)
class PerOpMutationBoundaryAudit:
    """Core-owned per-op mutation-boundary audit result (LS-01 / §1.0).

    Wraps the pure :class:`PerOpMutationBoundaryVerdict` together with the
    typed :class:`~lawvm.core.phase_result.Finding` emission the apply site
    consumes. ``findings`` is empty when the op stayed within boundary; on an
    escape it carries exactly one finding whose role/blocking disposition was
    chosen by ``is_strict``:

    * strict → ``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`` (role=violation,
      blocking=True) — the op is rejected.
    * quirks → ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` (role=observation,
      blocking=False) — a non-blocking accounting receipt.

    Owning the verdict→finding projection HERE (not at each frontend apply
    site) is the §2.3 core-owns-mutation-boundary/findings boundary and the
    §2.5 one-proof-per-family rule: Finland's inline emission and the UK probe
    are two consumers of one producer, never two re-implementations that can
    drift in role, code, or detail shape.
    """

    verdict: PerOpMutationBoundaryVerdict
    findings: tuple[Finding, ...]

    @property
    def within_boundary(self) -> bool:
        return self.verdict.within_boundary


def mutation_boundary_finding_detail(
    verdict: PerOpMutationBoundaryVerdict,
    *,
    source_statute: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical ``Finding.detail`` payload for a per-op escape.

    Single owner of the diagnostic shape so every consumer (FI apply lane, the
    UK probe, future frontends) reports the SAME self-evidencing fields — the
    op id, the full changed-path list, and the concrete out-of-boundary paths
    (never an opaque "boundary violated" with no escaped path, the §1.10
    forbidden diagnostic shape).
    """
    detail: dict[str, Any] = {
        "message": (
            "Per-op mutation boundary escaped: the op's changed tree paths are not "
            "a subset of its declared target/migration/recovery/editorial boundary."
        ),
        "op_id": verdict.op_id,
        "source_statute": str(source_statute or ""),
        "changed_paths": list(verdict.changed_paths),
        "out_of_boundary_paths": list(verdict.out_of_boundary_paths),
        "boundary_status": verdict.boundary_status,
    }
    if extra:
        detail.update(extra)
    return detail


def audit_op_mutation_boundary(
    before: IRNode,
    after: IRNode,
    op: LegalOperation,
    *,
    op_id: str,
    source_statute: str = "",
    is_strict: bool = False,
    declared_migration_prefixes: Sequence[TreePath] = (),
    declared_recovery_prefixes: Sequence[TreePath] = (),
    declared_editorial_projection_prefixes: Sequence[TreePath] = (),
    strip_root_prefix: TreePath = (),
    detail_extra: Mapping[str, Any] | None = None,
) -> PerOpMutationBoundaryAudit:
    """Core-owned per-op mutation-boundary audit (LS-01 / §1.0): verify + emit.

    Runs :func:`verify_per_op` and, on an out-of-boundary escape, emits the
    typed registry finding the apply site consumes — a blocking
    ``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`` violation under strict, or a
    non-blocking ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation under
    quirks. A within-boundary op emits nothing (no diagnostic noise on a clean
    apply).

    This is the jurisdiction-neutral producer §2.3 assigns to core. Frontends
    pass their own materialization-wrapper ``strip_root_prefix`` (FI strips the
    ``("hcontainer", "")`` root; UK passes ``()``) and the apply-time
    ``is_strict`` signal, then route the returned findings into their own
    finding ledger — they do NOT re-build the Finding, choose its role, or
    re-derive its detail shape. Pure: never mutates the tree; the caller owns
    the ledger append.
    """
    verdict = verify_per_op(
        before,
        after,
        op,
        op_id=op_id,
        declared_migration_prefixes=declared_migration_prefixes,
        declared_recovery_prefixes=declared_recovery_prefixes,
        declared_editorial_projection_prefixes=declared_editorial_projection_prefixes,
        strip_root_prefix=strip_root_prefix,
    )
    if verdict.within_boundary:
        return PerOpMutationBoundaryAudit(verdict=verdict, findings=())
    detail = mutation_boundary_finding_detail(
        verdict, source_statute=source_statute, extra=detail_extra
    )
    if is_strict:
        finding = Finding(
            kind=MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=str(source_statute or ""),
            detail=detail,
        )
    else:
        finding = Finding(
            kind=MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=str(source_statute or ""),
            detail={**detail, "strict_disposition": "record"},
        )
    return PerOpMutationBoundaryAudit(verdict=verdict, findings=(finding,))


def mutation_boundary_audit_enabled(env_var: str) -> bool:
    """Opt-in env gate for the per-op mutation-boundary audit.

    Jurisdiction-neutral helper: each frontend names its own flag (UK uses
    ``LAWVM_UK_MUTATION_BOUNDARY_PER_OP``) so the audit is observation-by-
    default — OFF unless the flag is exactly ``"1"`` — keeping production
    replay byte-identical with the audit disabled. Centralizing the read makes
    the gate semantics one fact, not a per-frontend ``os.environ`` literal.
    """
    return os.environ.get(env_var, "") == "1"


def mutation_boundary_evidence_report(
    proofs: (
        MutationBoundaryProof
        | Mapping[str, Any]
        | tuple[MutationBoundaryProof | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str = "",
    report_kind: str = "mutation_boundary_proof",
) -> EvidenceSurfaceReport:
    """Project mutation-boundary proofs into a shared passive report."""

    rows = tuple(_mutation_boundary_proof(row) for row in _proof_sequence(proofs))
    report_rows = tuple(_mutation_boundary_report_row(row) for row in rows)
    status_counts = _counts(row.boundary_proof_status for row in rows)
    owner_phase_counts = _counts(row.owner_phase for row in rows)
    rule_counts = _counts(row.rule_id for row in rows)
    result_code_counts = _counts(code for row in rows for code in row.result_codes)
    summary = {
        "mutation_boundary_proof_count": len(rows),
        "status_counts": status_counts,
        "owner_phase_counts": owner_phase_counts,
        "rule_counts": rule_counts,
        "result_code_counts": result_code_counts,
        "proved_count": status_counts.get("proved", 0),
        "proved_with_allowance_count": status_counts.get("proved_with_allowance", 0),
        "unresolved_count": status_counts.get("unresolved", 0),
        "violated_count": status_counts.get("violated", 0),
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction or _report_jurisdiction(rows),
        report_kind=report_kind,
        schema="lawvm.mutation_boundary_report.v1",
        truth_claim="passive mutation-boundary path containment proofs",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=report_rows,
        detail={
            "safe_default": "treat_boundary_proofs_as_apply-accounting_evidence_not_replay_authority",
            "forbidden_shortcuts": _MUTATION_BOUNDARY_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("mutation_boundary_proof",),
        },
    )


def _proof_sequence(
    value: (
        MutationBoundaryProof
        | Mapping[str, Any]
        | tuple[MutationBoundaryProof | Mapping[str, Any], ...]
    ),
) -> tuple[MutationBoundaryProof | Mapping[str, Any], ...]:
    if isinstance(value, MutationBoundaryProof) or isinstance(value, Mapping):
        return (cast(MutationBoundaryProof | Mapping[str, Any], value),)
    return tuple(value)


def _mutation_boundary_proof(value: MutationBoundaryProof | Mapping[str, Any]) -> MutationBoundaryProof:
    if isinstance(value, MutationBoundaryProof):
        return value
    return MutationBoundaryProof(
        proof_id=str(value.get("proof_id") or ""),
        jurisdiction=str(value.get("jurisdiction") or ""),
        materialization_surface=str(value.get("materialization_surface") or ""),
        operation_id=str(value.get("operation_id") or ""),
        owner_phase=str(value.get("owner_phase") or ""),
        rule_id=str(value.get("rule_id") or ""),
        boundary_proof_status=cast(MutationBoundaryProofStatus, str(value.get("boundary_proof_status") or "")),
        helper=str(value.get("helper") or ""),
        outcome=str(value.get("outcome") or ""),
        selected_target_paths=_tree_paths_from_diagnostics(value.get("selected_target_paths")),
        allowed_mutation_regions=_tree_paths_from_diagnostics(value.get("allowed_mutation_regions")),
        changed_paths=_tree_paths_from_diagnostics(value.get("changed_paths")),
        covered_changed_paths=_tree_paths_from_diagnostics(value.get("covered_changed_paths")),
        unexplained_changed_paths=_tree_paths_from_diagnostics(value.get("unexplained_changed_paths")),
        declared_allowance_paths=_tree_paths_from_diagnostics(value.get("declared_allowance_paths")),
        declared_recovery_paths=_tree_paths_from_diagnostics(value.get("declared_recovery_paths")),
        declared_recovery_rule_ids=tuple(str(item) for item in _sequence(value.get("declared_recovery_rule_ids"))),
        declared_migration_paths=_tree_paths_from_diagnostics(value.get("declared_migration_paths")),
        declared_migration_rule_ids=tuple(str(item) for item in _sequence(value.get("declared_migration_rule_ids"))),
        matched_allowance_rule_ids=tuple(str(item) for item in _sequence(value.get("matched_allowance_rule_ids"))),
        result_codes=tuple(str(item) for item in _sequence(value.get("result_codes"))),
        path_set_invariant_holds=bool(value.get("path_set_invariant_holds", True)),
        safe_default=str(value.get("safe_default") or ""),
        forbidden_shortcuts=tuple(str(item) for item in _sequence(value.get("forbidden_shortcuts"))),
        detail=_mapping_or_empty(value.get("detail")),
    )


def _mutation_boundary_report_row(proof: MutationBoundaryProof) -> dict[str, Any]:
    row = proof.to_dict()
    return {
        **row,
        "surface": "mutation_boundary_proof",
        "row_id": proof.proof_id,
        "subject_id": proof.operation_id,
        "proof_ref": proof.proof_id,
        "boundary_proof_status": proof.boundary_proof_status,
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *proof.forbidden_shortcuts,
                    *_MUTATION_BOUNDARY_REPORT_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
    }


def _status_for_report(report: MutationInvariantReport) -> MutationBoundaryProofStatus:
    result_codes = {result.code for result in report.results}
    if result_codes & MUTATION_ACCOUNTING_HARD_CODES:
        if result_codes <= _UNRESOLVED_RESULT_CODES:
            return "unresolved"
        return "violated"
    if not report.path_set_invariant_holds:
        return "violated"
    if result_codes:
        return "proved_with_allowance"
    return "proved"


def _rule_id_for_report(report: MutationInvariantReport) -> str:
    status = _status_for_report(report)
    if status == "proved":
        return "mutation_boundary_path_set_proved"
    if status == "proved_with_allowance":
        return "mutation_boundary_path_set_proved_with_allowance"
    if status == "unresolved":
        return "mutation_boundary_path_set_unresolved"
    return "mutation_boundary_path_set_violated"


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"MutationBoundaryProof.{field_name} is required")
    return text


def _validated_tree_paths(field_name: str, paths: Any) -> TreePaths:
    if isinstance(paths, str) or not isinstance(paths, IterableABC):
        raise ValueError(f"{field_name} must be a sequence of tree paths")
    normalized: list[tuple[tuple[str, str], ...]] = []
    for index, path in enumerate(paths):
        if isinstance(path, str) or not isinstance(path, IterableABC):
            raise ValueError(f"{field_name}[{index}] must be a tree path")
        tree_steps: list[tuple[str, str]] = []
        for step_index, step in enumerate(path):
            if isinstance(step, str) or not isinstance(step, IterableABC):
                raise ValueError(
                    f"{field_name}[{index}] step {step_index} must be a path step"
                )
            step_tuple = tuple(cast(IterableABC[object], step))
            if len(step_tuple) != 2:
                raise ValueError(
                    f"{field_name}[{index}] step {step_index} must have kind and label"
                )
            tree_steps.append((str(step_tuple[0]), str(step_tuple[1])))
        tree_path = tuple(tree_steps)
        issues = validate_tree_path(tree_path, field_name=f"{field_name}[{index}]")
        if issues:
            raise ValueError("; ".join(issues))
        normalized.append(tree_path)
    return tuple(normalized)


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _path_strings(paths: TreePaths) -> list[str]:
    return [_cached_tree_path_to_diagnostic_string(path) for path in paths]


@lru_cache(maxsize=16384)
def _cached_tree_path_to_diagnostic_string(path: tuple[tuple[str, str], ...]) -> str:
    return tree_path_to_diagnostic_string(path)


def _tree_paths_from_diagnostics(value: Any) -> TreePaths:
    paths: list[tuple[tuple[str, str], ...]] = []
    for item in _sequence(value):
        text = str(item or "").strip()
        if not text:
            continue
        path: list[tuple[str, str]] = []
        for step in text.split("/"):
            if ":" in step:
                kind, label = step.split(":", 1)
            else:
                kind, label = step, ""
            path.append((kind, label))
        paths.append(tuple(path))
    return tuple(paths)


def _report_jurisdiction(rows: tuple[MutationBoundaryProof, ...]) -> str:
    jurisdictions = tuple(dict.fromkeys(row.jurisdiction for row in rows if row.jurisdiction))
    if len(jurisdictions) == 1:
        return jurisdictions[0]
    if len(jurisdictions) > 1:
        return "mixed"
    return ""


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value
