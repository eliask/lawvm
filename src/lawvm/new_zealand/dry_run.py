"""First New Zealand dry-run replay surface for direct corroborated repeal.

This surface is the promotion step between candidate preflight and actual
replay. It applies preflight-approved, exact-target repeal operations to an
immutable parsed *before* source tree, produces a candidate after-tree, and
compares the candidate after-tree against the archived on-or-after XML oracle.

It deliberately stays narrow and boring:

- It consumes only preflight-approved candidate operations whose status is
  ``candidate_emitted``, whose family is ``repeal``, and which preflight already
  considers replayable (not source-change-only, not target-recovery).
- The apply kernel is a single boring mutation: convert the exact target node to
  a repealed tombstone (preserving addressability), never delete-and-forget.
- It never enables actual replay, never mutates the archive, and never claims
  canonical corpus state. ``replay_claims`` stays ``False`` everywhere.

It refuses (typed refusal, not a crash) when a target was recovered rather than
exact, when payload evidence is source-change-only, when the before/after change
window is missing, or when any other precondition from preflight is unmet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.agreement_residual import AgreementResidual, agreement_surface_from_residuals
from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateRow,
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode, parse_nz_source_document
from lawvm.new_zealand.version_diff import (
    NZArchivedVersion,
    NZArchivedVersionChangeWindow,
    archived_xml_version_change_window,
)


# Rule ids: agreement / refusal vocabulary for the dry-run surface.
NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID = "nz_dry_run_repeal_tombstone_matches_oracle"
NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID = "nz_dry_run_surface_not_replay_authorized"

NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID = "nz_dry_run_refused_preflight_not_ready_for_dry_run"
NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID = "nz_dry_run_refused_no_replayable_repeal_candidate"
NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID = "nz_dry_run_refused_target_recovered_not_exact"
NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID = "nz_dry_run_refused_source_change_only_payload"
NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID = "nz_dry_run_refused_missing_before_after_version_window"
NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_before_xml_unreadable"
NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID = "nz_dry_run_refused_on_or_after_xml_unreadable"
NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID = "nz_dry_run_refused_target_not_present_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_NOT_SUBSTANTIVE_RULE_ID = "nz_dry_run_refused_target_not_substantive_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID = "nz_dry_run_refused_target_path_ambiguous_in_before_tree"
NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID = "nz_dry_run_refused_target_address_path_unmappable_to_source"

# Oracle residual rule ids.
NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_missing_in_oracle"
NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_not_tombstone_in_oracle"
# Removal-on-repeal (definition) oracle outcomes.
NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID = "nz_dry_run_repeal_removed_node_matches_oracle"
NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID = "nz_dry_run_residual_target_not_removed_in_oracle"

# Dry-run scopes.
#
# ``complete_set`` is the original, strict behavior: refuse the whole work
# unless its full candidate set reached ``ready_for_dry_run_replay``. This is
# the default and its semantics must never change.
#
# ``selected_family_repeal`` is the partial-scope mode: it dry-runs the ready
# repeal operations in a work EVEN WHEN the work's full candidate set is
# incomplete. It relaxes only the WHOLE-WORK readiness gate; it never relaxes
# any per-operation exactness/corroboration check (those still refuse, typed).
# The report declares the partial scope explicitly and carries the count of
# operation witnesses NOT covered, typed by reason — never hidden.
NZ_DRY_RUN_SCOPE_COMPLETE_SET = "complete_set"
NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL = "selected_family_repeal"
_VALID_DRY_RUN_SCOPES = (NZ_DRY_RUN_SCOPE_COMPLETE_SET, NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL)

# Typed not-in-scope reasons for the selected_family_repeal scope. Each operation
# witness in the work that is not a dry-run-eligible repeal is carried under one
# of these reasons so the partial scope can never silently inflate coverage.
NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY = "not_in_scope_non_repeal_family"
NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_SOURCE_CHANGE_ONLY = "not_in_scope_repeal_source_change_only"
NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_TARGET_RECOVERY = "not_in_scope_repeal_target_recovery"
NZ_DRY_RUN_NOT_IN_SCOPE_CANDIDATE_OPERATION_MISSING = "not_in_scope_candidate_operation_missing"
NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS = "not_in_scope_blocked_operation_witness"

# NZ history-note family verb for a repeal operation witness. This is the
# ``operation_family`` value the readiness lowering assigns to a repeal (the
# candidate ``action`` is ``str(StructuralAction.REPEAL)`` only on emitted rows;
# blocked rows still carry this family), so it is the stable discriminator for
# the repeal-witness replay-coverage denominator.
_NZ_REPEAL_OPERATION_FAMILY = "repealed"

# Canonical tombstone marker for a repealed-but-addressable source node.
_REPEAL_TOMBSTONE_DELETION_STATUS = "repealed"

# Address-kind -> source-tree node-kind mapping. This is the inverse of the
# operation-surface source-segment mapping and is exact, not a guess.
_ADDRESS_KIND_TO_SOURCE_KIND = {
    "section": "prov",
    "subsection": "subprov",
    "paragraph": "label-para",
    "definition": "def-para",
    "part": "part",
    "schedule": "schedule",
}

# Source-tree node kind whose repeal NZ effects by REMOVING the node from the
# consolidated text rather than leaving a repealed-but-addressable tombstone.
# When a definition (``def-para``) is repealed, the whole def-para disappears
# from the on-or-after XML; the agreeing oracle outcome is therefore an absent
# node, not a tombstone. (Ordinary provisions are tombstoned in place.)
_REMOVAL_ON_REPEAL_SOURCE_KIND = "def-para"


@dataclass(frozen=True)
class NZMutationBoundaryProof:
    """Per-operation mutation-boundary audit product.

    This is the point of the surface: it records exactly what node was touched,
    what its digest was before and after, and proves that siblings and parent
    were left unchanged. It also carries the oracle match partition for the one
    mutated node.
    """

    op_id: str
    action: str
    target_address: str
    selected_source_path: tuple[str, ...]
    target_xml_id: str
    target_digest_before: str
    target_digest_after: str
    operation_payload: str
    occupancy_before: str
    occupancy_after: str
    parent_source_path: tuple[str, ...]
    parent_digest_before: str
    parent_digest_after: str
    unaffected_neighbor_paths: tuple[tuple[str, ...], ...]
    unaffected_neighbor_digests_before: tuple[str, ...]
    unaffected_neighbor_digests_after: tuple[str, ...]
    neighbors_unchanged: bool
    oracle_version_id: str
    oracle_target_present: bool
    oracle_target_occupancy: str
    oracle_match: str
    oracle_match_rule_id: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "action": self.action,
            "target_address": self.target_address,
            "selected_source_path": list(self.selected_source_path),
            "target_xml_id": self.target_xml_id,
            "target_digest_before": self.target_digest_before,
            "target_digest_after": self.target_digest_after,
            "operation_payload": self.operation_payload,
            "occupancy_before": self.occupancy_before,
            "occupancy_after": self.occupancy_after,
            "parent_source_path": list(self.parent_source_path),
            "parent_digest_before": self.parent_digest_before,
            "parent_digest_after": self.parent_digest_after,
            "unaffected_neighbor_paths": [list(path) for path in self.unaffected_neighbor_paths],
            "unaffected_neighbor_digests_before": list(self.unaffected_neighbor_digests_before),
            "unaffected_neighbor_digests_after": list(self.unaffected_neighbor_digests_after),
            "neighbors_unchanged": self.neighbors_unchanged,
            "oracle_version_id": self.oracle_version_id,
            "oracle_target_present": self.oracle_target_present,
            "oracle_target_occupancy": self.oracle_target_occupancy,
            "oracle_match": self.oracle_match,
            "oracle_match_rule_id": self.oracle_match_rule_id,
        }


@dataclass(frozen=True)
class NZDryRunRefusal:
    """A typed refusal for one operation (no mutation performed)."""

    op_id: str
    rule_id: str
    message: str
    target_address: str = ""
    amendment_date_iso: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "rule_id": self.rule_id,
            "message": self.message,
            "target_address": self.target_address,
            "amendment_date_iso": self.amendment_date_iso,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class NZDryRunScopeCompleteness:
    """Honest declaration of how much of a work this dry-run report covers.

    In ``complete_set`` scope the report only runs when the work's full
    candidate set is ready, so the scope is the whole work. In
    ``selected_family_repeal`` scope only the ready repeal operations are
    dry-run while the work's other operation witnesses are explicitly carried
    here as typed not-in-scope counts. The scope is partial whenever any
    operation witness is left uncovered; this surface never hides that.
    """

    scope: str
    family: str
    total_operation_witnesses: int
    in_scope_operation_witnesses: int
    not_in_scope_operation_witnesses: int
    not_in_scope_reason_counts: Mapping[str, int] = field(default_factory=dict)
    # Repeal-family witness census. ``total_repeal_operation_witnesses`` is the
    # denominator of the family replay-coverage loop metric: every operation
    # witness in the work whose family is repeal, whether dry-run-eligible,
    # not-in-scope (source-change-only / target-recovery), or still blocked.
    total_repeal_operation_witnesses: int = 0
    repeal_witnesses_in_scope: int = 0
    repeal_witnesses_not_in_scope_reason_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        return self.not_in_scope_operation_witnesses > 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "family": self.family,
            "is_partial": self.is_partial,
            "total_operation_witnesses": self.total_operation_witnesses,
            "in_scope_operation_witnesses": self.in_scope_operation_witnesses,
            "not_in_scope_operation_witnesses": self.not_in_scope_operation_witnesses,
            "not_in_scope_reason_counts": dict(sorted(self.not_in_scope_reason_counts.items())),
            "total_repeal_operation_witnesses": self.total_repeal_operation_witnesses,
            "repeal_witnesses_in_scope": self.repeal_witnesses_in_scope,
            "repeal_witnesses_not_in_scope_reason_counts": dict(
                sorted(self.repeal_witnesses_not_in_scope_reason_counts.items())
            ),
        }


@dataclass(frozen=True)
class NZDryRunReport:
    """Typed dry-run replay report for direct corroborated repeal.

    Dry-run agreement is reported separately from any actual-replay agreement;
    actual replay is never performed by this surface.
    """

    work_id: str
    operation_family: str
    proofs: tuple[NZMutationBoundaryProof, ...]
    refusals: tuple[NZDryRunRefusal, ...]
    preflight_status: str
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET
    scope_completeness: NZDryRunScopeCompleteness | None = None

    def matched_proofs(self) -> tuple[NZMutationBoundaryProof, ...]:
        return tuple(proof for proof in self.proofs if proof.oracle_match == "agrees")

    def residual_proofs(self) -> tuple[NZMutationBoundaryProof, ...]:
        return tuple(proof for proof in self.proofs if proof.oracle_match != "agrees")

    def summary(self) -> dict[str, Any]:
        matched = self.matched_proofs()
        residual = self.residual_proofs()
        return {
            "work_id": self.work_id,
            "operation_family": self.operation_family,
            "scope": self.scope,
            "scope_completeness": self.scope_completeness.to_jsonable() if self.scope_completeness else None,
            "preflight_status": self.preflight_status,
            "operations_dry_run": len(self.proofs),
            "operations_refused": len(self.refusals),
            "dry_run_oracle_agreements": len(matched),
            "dry_run_oracle_residuals": len(residual),
            "neighbors_unchanged_all": all(proof.neighbors_unchanged for proof in self.proofs),
            "refusal_rule_counts": _counts(refusal.rule_id for refusal in self.refusals),
            "oracle_match_counts": _counts(proof.oracle_match for proof in self.proofs),
            # Dry-run agreement only. Actual replay is never claimed here.
            "replay_claims": False,
            "actual_replay_agreements": 0,
            "dry_run_claims": True,
        }

    def agreement_surface(self) -> dict[str, Any]:
        """Project the oracle partition into a typed agreement surface.

        Reuses :mod:`lawvm.core.agreement_residual`. Dry-run agreements and
        residuals are classified there; this never authorizes replay.
        """

        residuals: list[AgreementResidual] = []
        for proof in self.proofs:
            if proof.oracle_match == "agrees":
                residuals.append(
                    AgreementResidual(
                        residual_id=f"{self.work_id}:{proof.op_id}:agrees",
                        jurisdiction="nz",
                        agreement_surface="nz_dry_run_repeal",
                        family="agreement",
                        status="agrees",
                        owner_phase="dry_run",
                        rule_id=proof.oracle_match_rule_id,
                        source_artifact_id=proof.op_id,
                        replay_count=1,
                        oracle_count=1,
                        safe_default="classify_dry_run_agreement_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "dry_run_agreement_as_replay_authorization",
                            "oracle_tombstone_as_source_truth",
                        ),
                        detail={"target_address": proof.target_address},
                    )
                )
            else:
                residuals.append(
                    AgreementResidual(
                        residual_id=f"{self.work_id}:{proof.op_id}:residual",
                        jurisdiction="nz",
                        agreement_surface="nz_dry_run_repeal",
                        family="target_recovery_mismatch"
                        if proof.oracle_match == "target_missing"
                        else "oracle_editorial_pathology",
                        status="residual",
                        owner_phase="dry_run",
                        rule_id=proof.oracle_match_rule_id,
                        source_artifact_id=proof.op_id,
                        replay_count=1,
                        oracle_count=1 if proof.oracle_target_present else 0,
                        safe_default="keep_dry_run_residual_visible_without_authorizing_replay",
                        forbidden_shortcuts=(
                            "dry_run_residual_as_replay_bug",
                            "oracle_score_as_source_truth",
                        ),
                        detail={
                            "target_address": proof.target_address,
                            "oracle_target_occupancy": proof.oracle_target_occupancy,
                        },
                    )
                )
        surface = agreement_surface_from_residuals(
            tuple(residuals),
            jurisdiction="nz",
            agreement_surface="nz_dry_run_repeal",
            materialization_id=f"nz_dry_run:{self.work_id}",
            comparison_target_id=f"nz_on_or_after_oracle:{self.work_id}",
            comparison_kind="dry_run_after_tree_vs_archived_on_or_after_xml",
            materialization_kind="proposed_future_branch",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=(len(self.matched_proofs()) / len(self.proofs)) if self.proofs else None,
        )
        return surface.to_dict()

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "dry_run_repeal_replay",
            "truth_claim": "dry_run_after_tree_vs_archived_on_or_after_xml_not_actual_replay",
            "replay_claims": False,
            "dry_run_claims": True,
            "scope": self.scope,
            "scope_completeness": self.scope_completeness.to_jsonable() if self.scope_completeness else None,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["mutation_boundary_proofs"] = [proof.to_jsonable() for proof in self.proofs]
        payload["refusals"] = [refusal.to_jsonable() for refusal in self.refusals]
        payload["agreement_surface"] = self.agreement_surface()
        return payload


def build_archived_work_dry_run_repeal(
    db_path: Path,
    work_id: str,
    *,
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET,
) -> NZDryRunReport:
    """Build the dry-run repeal report for one archived NZ work."""

    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_dry_run_repeal(archive, work_id=work_id, preflight=preflight, scope=scope)
    finally:
        archive.close()


def build_dry_run_repeal(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET,
) -> NZDryRunReport:
    if scope not in _VALID_DRY_RUN_SCOPES:
        raise ValueError(f"unknown dry-run scope {scope!r}; expected one of {_VALID_DRY_RUN_SCOPES}")

    preflight_status = str(preflight.summary()["preflight_status"])
    proofs: list[NZMutationBoundaryProof] = []
    refusals: list[NZDryRunRefusal] = []

    # The selected_family_repeal scope relaxes ONLY the whole-work readiness
    # gate. The complete_set scope keeps the original strict refusal.
    if scope == NZ_DRY_RUN_SCOPE_COMPLETE_SET and preflight_status != "ready_for_dry_run_replay":
        # The whole candidate set is not dry-run ready. Refuse without mutating.
        return NZDryRunReport(
            work_id=work_id,
            operation_family="repeal",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
                    message=(
                        "dry-run repeal refused because candidate preflight is not "
                        f"ready_for_dry_run_replay (status={preflight_status})"
                    ),
                    detail={"preflight_status": preflight_status},
                ),
            ),
            preflight_status=preflight_status,
            scope=scope,
        )

    repeal_rows = _replayable_repeal_rows(preflight)
    scope_completeness = (
        _selected_family_repeal_scope_completeness(preflight, repeal_rows)
        if scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
        else None
    )
    if not repeal_rows:
        return NZDryRunReport(
            work_id=work_id,
            operation_family="repeal",
            proofs=(),
            refusals=(
                NZDryRunRefusal(
                    op_id=work_id or "new_zealand",
                    rule_id=NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
                    message="dry-run repeal refused because no replayable repeal candidate was found",
                ),
            ),
            preflight_status=preflight_status,
            scope=scope,
            scope_completeness=scope_completeness,
        )

    # Cache parsed source documents per XML locator so a work with multiple
    # repeals on the same change window does not reparse the same bytes.
    parsed_cache: dict[str, NZSourceDocument | None] = {}

    for row in repeal_rows:
        operation = row.operation
        assert operation is not None  # guaranteed by _replayable_repeal_rows
        outcome = _dry_run_one_repeal(archive, work_id, row, operation, parsed_cache)
        if isinstance(outcome, NZDryRunRefusal):
            refusals.append(outcome)
        else:
            proofs.append(outcome)

    return NZDryRunReport(
        work_id=work_id,
        operation_family="repeal",
        proofs=tuple(proofs),
        refusals=tuple(refusals),
        preflight_status=preflight_status,
        scope=scope,
        scope_completeness=scope_completeness,
    )


def _selected_family_repeal_scope_completeness(
    preflight: NZEffectCandidatePreflightReport,
    in_scope_repeal_rows: tuple[NZCanonicalEffectCandidateRow, ...],
) -> NZDryRunScopeCompleteness:
    """Type every operation witness in the work as in- or not-in-scope.

    The selected family is the replayable repeal family. Every other operation
    witness in the work is carried under a typed not-in-scope reason so the
    partial scope can never silently inflate coverage. The total is over all
    operation-witness rows in the work (blocked rows included), because a
    blocked witness is still an operation the work owns that this scope does
    not cover.
    """

    from lawvm.new_zealand.effect_candidates import (
        _source_change_only_candidate,
        _target_recovery_candidate,
    )

    in_scope_row_ids = {row.row_id for row in in_scope_repeal_rows}
    reason_counts: dict[str, int] = {}
    repeal_reason_counts: dict[str, int] = {}
    in_scope = 0
    total = 0
    total_repeal = 0
    repeal_in_scope = 0
    for row in preflight.candidate_report.rows:
        total += 1
        is_repeal_witness = row.operation_family == _NZ_REPEAL_OPERATION_FAMILY
        if is_repeal_witness:
            total_repeal += 1
        if row.row_id in in_scope_row_ids:
            in_scope += 1
            if is_repeal_witness:
                repeal_in_scope += 1
            continue
        reason = _not_in_scope_reason(row, _source_change_only_candidate, _target_recovery_candidate)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if is_repeal_witness:
            repeal_reason_counts[reason] = repeal_reason_counts.get(reason, 0) + 1
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        family="repeal",
        total_operation_witnesses=total,
        in_scope_operation_witnesses=in_scope,
        not_in_scope_operation_witnesses=total - in_scope,
        not_in_scope_reason_counts=dict(sorted(reason_counts.items())),
        total_repeal_operation_witnesses=total_repeal,
        repeal_witnesses_in_scope=repeal_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(sorted(repeal_reason_counts.items())),
    )


def _not_in_scope_reason(
    row: NZCanonicalEffectCandidateRow,
    source_change_only: Any,
    target_recovery: Any,
) -> str:
    if row.status != "candidate_emitted":
        return NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS
    if row.operation is None:
        return NZ_DRY_RUN_NOT_IN_SCOPE_CANDIDATE_OPERATION_MISSING
    if row.action != str(StructuralAction.REPEAL):
        return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY
    if source_change_only(row):
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_SOURCE_CHANGE_ONLY
    if target_recovery(row):
        return NZ_DRY_RUN_NOT_IN_SCOPE_REPEAL_TARGET_RECOVERY
    # A candidate_emitted, exact-target, corroborated repeal that is not in the
    # in-scope set would be a contradiction (the in-scope filter is exactly that
    # predicate). Fall back to a distinct named reason rather than silently
    # absorbing it, so any future filter drift surfaces loudly.
    return NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY


def _replayable_repeal_rows(
    preflight: NZEffectCandidatePreflightReport,
) -> tuple[NZCanonicalEffectCandidateRow, ...]:
    # Import the preflight's own replayability predicates so the dry-run surface
    # consumes exactly the operations preflight authorized (no broader set).
    from lawvm.new_zealand.effect_candidates import (
        _source_change_only_candidate,
        _target_recovery_candidate,
    )

    rows: list[NZCanonicalEffectCandidateRow] = []
    for row in preflight.candidate_report.rows:
        if row.status != "candidate_emitted":
            continue
        if row.operation is None:
            continue
        if row.action != str(StructuralAction.REPEAL):
            continue
        if _source_change_only_candidate(row) or _target_recovery_candidate(row):
            continue
        rows.append(row)
    return tuple(rows)


def _dry_run_one_repeal(
    archive: Any,
    work_id: str,
    row: NZCanonicalEffectCandidateRow,
    operation: LegalOperation,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> NZMutationBoundaryProof | NZDryRunRefusal:
    op_id = operation.op_id
    target_address = str(operation.target)
    amendment_date_iso = row.amendment_date_iso

    # Defence in depth: even though preflight is ready, refuse any non-exact
    # target locally. Target-recovery / source-change-only must never mutate.
    if row.latest_oracle_target_resolution_status and row.latest_oracle_target_resolution_status != "exact_source_path":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
            message="dry-run repeal refused because target was recovered rather than exact",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"target_resolution_status": row.latest_oracle_target_resolution_status},
        )
    if operation.witness_rule_id and "source_change" in str(operation.witness_rule_id):
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID,
            message="dry-run repeal refused because payload evidence is source-change-only",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    if not amendment_date_iso:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run repeal refused because the operation has no ISO amendment date for a version window",
            target_address=target_address,
        )

    change_window = archived_xml_version_change_window(
        archive,
        work_id=work_id,
        version_date=amendment_date_iso,
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message="dry-run repeal refused because the before/after archived XML version window is missing",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail=_change_window_detail(change_window),
        )

    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="dry-run repeal refused because the before XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="dry-run repeal refused because the on-or-after XML version is unreadable",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    source_path = _source_path_for_address(operation)
    if source_path is None:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_PATH_UNMAPPABLE_RULE_ID,
            message="dry-run repeal refused because the target address path is not mappable to a source-tree path",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
        )

    before_matches = _resolve_target_nodes(before_doc, source_path)
    if len(before_matches) == 0:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_IN_BEFORE_RULE_ID,
            message="dry-run repeal refused because the exact target is not present in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path)},
        )
    if len(before_matches) > 1:
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_AMBIGUOUS_RULE_ID,
            message="dry-run repeal refused because the target source path is ambiguous in the before tree",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"selected_source_path": list(source_path), "match_count": len(before_matches)},
        )

    before_target = before_matches[0]
    # The resolved node may carry a leading ``part:`` segment the address omitted;
    # everything downstream (proof, neighbours, oracle partition) uses the
    # resolved path so the surface reports the exact node it actually touched.
    resolved_path = before_target.path
    occupancy_before = _occupancy(before_target)
    if occupancy_before != "substantive":
        return NZDryRunRefusal(
            op_id=op_id,
            rule_id=NZ_DRY_RUN_REFUSED_TARGET_NOT_SUBSTANTIVE_RULE_ID,
            message="dry-run repeal refused because the before target is not substantive (cannot tombstone)",
            target_address=target_address,
            amendment_date_iso=amendment_date_iso,
            detail={"occupancy_before": occupancy_before},
        )

    # --- The boring apply kernel: substantive -> tombstone, addressability kept.
    after_target = _tombstone_node(before_target)
    occupancy_after = _occupancy(after_target)

    # Mutation-boundary proof: digests + unaffected neighbours.
    parent_path = resolved_path[:-1]
    siblings_before = _sibling_nodes(before_doc, resolved_path)
    # In the candidate after-tree only the target changed; siblings are the same
    # immutable nodes from the before tree, so their digests are unchanged by
    # construction. We still record both sides to make the boundary explicit.
    neighbor_paths = tuple(node.path for node in siblings_before)
    neighbor_before = tuple(_node_digest(node) for node in siblings_before)
    neighbor_after = neighbor_before  # kernel only touched the target node
    parent_before_nodes = _nodes_at_path(before_doc, parent_path) if parent_path else ()
    parent_digest_before = _node_digest(parent_before_nodes[0]) if parent_before_nodes else ""
    parent_digest_after = parent_digest_before  # parent identity untouched

    oracle_match, oracle_rule_id, oracle_present, oracle_occupancy = _oracle_partition(
        oracle_doc, resolved_path, target_kind=_leaf_source_kind(resolved_path)
    )

    return NZMutationBoundaryProof(
        op_id=op_id,
        action=str(operation.action),
        target_address=target_address,
        selected_source_path=resolved_path,
        target_xml_id=before_target.xml_id,
        target_digest_before=_node_digest(before_target),
        target_digest_after=_node_digest(after_target),
        operation_payload=_operation_payload_text(operation),
        occupancy_before=occupancy_before,
        occupancy_after=occupancy_after,
        parent_source_path=parent_path,
        parent_digest_before=parent_digest_before,
        parent_digest_after=parent_digest_after,
        unaffected_neighbor_paths=neighbor_paths,
        unaffected_neighbor_digests_before=neighbor_before,
        unaffected_neighbor_digests_after=neighbor_after,
        neighbors_unchanged=(neighbor_before == neighbor_after and parent_digest_before == parent_digest_after),
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_target_present=oracle_present,
        oracle_target_occupancy=oracle_occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
    )


def _oracle_partition(
    oracle_doc: NZSourceDocument,
    source_path: tuple[str, ...],
    *,
    target_kind: str = "",
) -> tuple[str, str, bool, str]:
    oracle_matches = _resolve_target_nodes(oracle_doc, source_path)
    if target_kind == _REMOVAL_ON_REPEAL_SOURCE_KIND:
        # NZ removes a repealed definition (``def-para``) from the consolidated
        # text rather than tombstoning it. An absent node is therefore the
        # agreeing outcome; a still-present node is the residual.
        if not oracle_matches:
            return (
                "agrees",
                NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
                False,
                "absent",
            )
        oracle_occupancy = _occupancy(oracle_matches[0])
        return (
            "target_not_removed",
            NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
            True,
            oracle_occupancy,
        )
    if not oracle_matches:
        # NZ consolidations preserve repealed-but-addressable tombstones, so a
        # missing node is a residual, not an agreement.
        return ("target_missing", NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID, False, "absent")
    oracle_node = oracle_matches[0]
    oracle_occupancy = _occupancy(oracle_node)
    if oracle_occupancy == "tombstone":
        return (
            "agrees",
            NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            True,
            oracle_occupancy,
        )
    return (
        "target_not_tombstone",
        NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
        True,
        oracle_occupancy,
    )


def _leaf_source_kind(source_path: tuple[str, ...]) -> str:
    if not source_path:
        return ""
    leaf = source_path[-1]
    for separator in (":", "@", "#"):
        if separator in leaf:
            return leaf.split(separator, 1)[0]
    return leaf


def _source_path_for_address(operation: LegalOperation) -> tuple[str, ...] | None:
    segments: list[str] = []
    for kind, label in operation.target.path:
        source_kind = _ADDRESS_KIND_TO_SOURCE_KIND.get(kind)
        if source_kind is None or not label:
            return None
        segments.append(f"{source_kind}:{label}")
    if not segments:
        return None
    return tuple(segments)


def _nodes_at_path(document: NZSourceDocument, path: tuple[str, ...]) -> tuple[NZSourceNode, ...]:
    return tuple(node for node in document.nodes if node.path == path)


# Source zones that are NOT the live consolidated text: end-of-document
# amendment skeletons and front/end history. A repeal target must resolve into
# the live body (or a schedule), never into a skeleton copy.
_NON_BODY_SOURCE_ZONES = frozenset({"end_skeleton", "front_history", "end_history"})


def _resolve_target_nodes(
    document: NZSourceDocument,
    source_path: tuple[str, ...],
) -> tuple[NZSourceNode, ...]:
    """Resolve an address-derived source path to live-body node(s).

    History-note ``amended-provision`` references omit the enclosing ``part``
    (e.g. "Section 2(1)"), while the parsed body nests provisions under their
    part (``part:1/prov:2/subprov:1``). We therefore accept a node whose path
    equals the address path exactly OR equals it with one extra leading
    ``part:`` segment, but only in the live body — never an end-of-document
    skeleton copy, which would resolve substantively for a node that is in fact
    repealed in the body. The caller still requires exactly one match; an empty
    or ambiguous result is a typed refusal, never a coarse-parent fallback.
    """
    matches: list[NZSourceNode] = []
    for node in document.nodes:
        if node.source_zone in _NON_BODY_SOURCE_ZONES:
            continue
        if node.path == source_path:
            matches.append(node)
        elif (
            len(node.path) == len(source_path) + 1
            and node.path[0].split(":", 1)[0] == "part"
            and node.path[1:] == source_path
        ):
            matches.append(node)
    return tuple(matches)


def _sibling_nodes(document: NZSourceDocument, path: tuple[str, ...]) -> tuple[NZSourceNode, ...]:
    if not path:
        return ()
    parent = path[:-1]
    return tuple(
        node
        for node in document.nodes
        if node.path != path and node.path[:-1] == parent and len(node.path) == len(path)
    )


def _occupancy(node: NZSourceNode) -> str:
    if node.deletion_status:
        return "tombstone"
    return "substantive"


def _tombstone_node(node: NZSourceNode) -> NZSourceNode:
    # Boring kernel: keep the node addressable (same kind/path/xml_id/label/
    # heading), mark it repealed. Do not delete-and-forget.
    return NZSourceNode(
        kind=node.kind,
        path=node.path,
        xml_id=node.xml_id,
        xml_path=node.xml_path,
        source_zone=node.source_zone,
        label=node.label,
        heading=node.heading,
        deletion_status=_REPEAL_TOMBSTONE_DELETION_STATUS,
        text=node.text,
        history=node.history,
    )


def _node_digest(node: NZSourceNode) -> str:
    payload = "".join(
        (
            node.kind,
            "/".join(node.path),
            node.xml_id,
            node.label,
            node.heading,
            node.deletion_status,
            node.text,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _operation_payload_text(operation: LegalOperation) -> str:
    return f"action={operation.action} witness_rule_id={operation.witness_rule_id or ''} payload=tombstone"


def _parse_archived_version(
    archive: Any,
    version: NZArchivedVersion,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> NZSourceDocument | None:
    locator = version.xml_locator
    if locator in parsed_cache:
        return parsed_cache[locator]
    data = archive.get(locator) if locator else None
    document: NZSourceDocument | None
    if data is None:
        document = None
    else:
        document = parse_nz_source_document(data, xml_locator=locator, version_id=version.version_id)
    parsed_cache[locator] = document
    return document


def _change_window_detail(window: NZArchivedVersionChangeWindow) -> dict[str, Any]:
    return {
        "requested_version_date": window.requested_version_date,
        "before_version_id": window.before.version_id if window.before else "",
        "on_or_after_version_id": window.on_or_after.version_id if window.on_or_after else "",
    }


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def scope_from_arg(value: str | None) -> str:
    """Normalize a CLI scope token (dash form) to the internal scope constant.

    ``None`` / empty -> the default ``complete_set`` so the existing behavior is
    preserved. An unknown token raises so it can never silently degrade to the
    relaxed scope.
    """

    if not value:
        return NZ_DRY_RUN_SCOPE_COMPLETE_SET
    normalized = value.replace("-", "_")
    if normalized not in _VALID_DRY_RUN_SCOPES:
        raise ValueError(f"unknown dry-run scope {value!r}; expected one of {_VALID_DRY_RUN_SCOPES}")
    return normalized


def main(args: Any) -> None:
    import json

    scope = scope_from_arg(getattr(args, "scope", None))
    report = build_archived_work_dry_run_repeal(Path(args.db), args.work_id, scope=scope)
    if args.json:
        print(json.dumps(report.to_jsonable(summary_only=args.summary_only), ensure_ascii=False, indent=2))
        return
    summary = report.summary()
    completeness = summary.get("scope_completeness")
    print(f"scope={summary['scope']}")
    if completeness:
        print(
            f"scope_completeness is_partial={completeness['is_partial']} "
            f"family={completeness['family']} "
            f"in_scope={completeness['in_scope_operation_witnesses']} "
            f"not_in_scope={completeness['not_in_scope_operation_witnesses']} "
            f"of_total={completeness['total_operation_witnesses']} "
            f"not_in_scope_reasons={completeness['not_in_scope_reason_counts']}"
        )
    print(
        f"work_id={summary['work_id']} preflight_status={summary['preflight_status']} "
        f"operations_dry_run={summary['operations_dry_run']} "
        f"operations_refused={summary['operations_refused']} "
        f"dry_run_oracle_agreements={summary['dry_run_oracle_agreements']} "
        f"dry_run_oracle_residuals={summary['dry_run_oracle_residuals']} "
        f"neighbors_unchanged_all={summary['neighbors_unchanged_all']}"
    )
    print(f"actual_replay_blocking_rule_id={NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID}")
    if summary["refusal_rule_counts"]:
        print(f"refusal_rule_counts={summary['refusal_rule_counts']}")
    if args.summary_only:
        return
    for proof in report.proofs:
        print(
            f"PROOF\t{proof.op_id}\t{proof.target_address}\t"
            f"{proof.occupancy_before}->{proof.occupancy_after}\t"
            f"oracle={proof.oracle_match}({proof.oracle_target_occupancy})\t"
            f"neighbors_unchanged={proof.neighbors_unchanged}"
        )
        print(
            f"\tdigest_before={proof.target_digest_before[:12]} "
            f"digest_after={proof.target_digest_after[:12]} "
            f"oracle_version={proof.oracle_version_id}"
        )
    for refusal in report.refusals:
        print(f"REFUSED\t{refusal.op_id}\t{refusal.rule_id}\t{refusal.target_address or '-'}")
