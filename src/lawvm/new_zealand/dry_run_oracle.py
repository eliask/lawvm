"""Whole-document dry-run-vs-oracle comparison for New Zealand repeal.

The per-operation :mod:`lawvm.new_zealand.dry_run` surface proves only that each
mutated target node became a tombstone in the archived on-or-after XML. It never
asks the harder question: does the *entire* candidate after-tree agree with the
archived on-or-after oracle, and if not, what exactly remains?

This surface answers that. For every change window touched by a dry-run repeal,
it materializes the full candidate after-document (the immutable parsed before
document, identical except the window's repeal targets are converted to
tombstones with the existing ``dry_run`` apply kernel), compares it node-for-node
against the archived on-or-after oracle with
:func:`lawvm.new_zealand.agreement.compare_source_documents`, and classifies
every residual.

The honesty requirement is the point of the surface. A dry-run repeal applies
**only** the repeal operations for the window. The archived oracle reflects
*all* changes in that window — other repeals, text replacements, insertions,
editorial drift. So a node that differs because of a non-repeal amendment we did
not apply is an **expected** residual (``unapplied_non_repeal_change_in_window``,
source-honest, mapped to ``accepted_non_executable_frontier``) and must stay
distinct from a genuine divergence where an applied repeal disagrees with the
oracle (a real replay-direction signal, mapped to ``replay_bug`` /
``target_recovery_mismatch``).

It never authorizes actual replay, never mutates the archive, and never turns
the oracle into source truth. ``replay_claims`` stays ``False`` everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    agreement_surface_from_residuals,
)
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.agreement import compare_source_documents
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
    NZDryRunReport,
    _nodes_at_path,
    _occupancy,
    _parse_archived_version,
    _replayable_repeal_rows,
    _source_path_for_address,
    _tombstone_node,
    build_dry_run_repeal,
)
from lawvm.new_zealand.effect_candidates import (
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode
from lawvm.new_zealand.version_diff import archived_xml_version_change_window


# Rule ids for the whole-tree comparison residual taxonomy.
NZ_DRY_RUN_ORACLE_NODE_AGREES_RULE_ID = "nz_dry_run_oracle_candidate_node_agrees_with_oracle"
NZ_DRY_RUN_ORACLE_REPEAL_TARGET_AGREES_RULE_ID = "nz_dry_run_oracle_repeal_target_tombstone_agrees"
NZ_DRY_RUN_ORACLE_REPEAL_TARGET_TEXT_NOT_ERASED_RULE_ID = (
    "nz_dry_run_oracle_repeal_target_tombstone_agrees_oracle_erased_body_text"
)
NZ_DRY_RUN_ORACLE_UNAPPLIED_NON_REPEAL_RULE_ID = "nz_dry_run_oracle_unapplied_non_repeal_change_in_window"
NZ_DRY_RUN_ORACLE_REPEAL_TARGET_DIVERGES_RULE_ID = "nz_dry_run_oracle_applied_repeal_target_diverges_from_oracle"

# Disposition families produced by the whole-tree classifier.
_FAMILY_EXACT = "exact_agreement"
_FAMILY_REPEAL_TARGET_AGREES = "repeal_target_agrees"
# Repeal direction agrees (both candidate and oracle are tombstones) but the
# oracle additionally erased the provision body text, which the boring tombstone
# kernel deliberately does not do. Source-honest, NOT a repeal-direction bug.
_FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED = "repeal_target_agrees_oracle_erased_body_text"
_FAMILY_UNAPPLIED_NON_REPEAL = "unapplied_non_repeal_change_in_window"
# Genuine divergence: at a node we repealed, the oracle is NOT a tombstone.
_FAMILY_REPEAL_TARGET_DIVERGES = "applied_repeal_target_diverges_from_oracle"

# Map whole-tree families onto core lawvm agreement-residual families.
_CORE_FAMILY: dict[str, AgreementResidualFamily] = {
    _FAMILY_EXACT: "agreement",
    _FAMILY_REPEAL_TARGET_AGREES: "agreement",
    # Repeal direction agrees; only the oracle's body-text erasure (a text
    # semantics the kernel does not apply) remains. Source-honest frontier.
    _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED: "accepted_non_executable_frontier",
    # Source-honest: an amendment we did not apply (we only apply repeal) is a
    # legitimate non-executable frontier, NOT a replay bug.
    _FAMILY_UNAPPLIED_NON_REPEAL: "accepted_non_executable_frontier",
    # Genuine divergence: our applied repeal disagrees with the oracle direction.
    _FAMILY_REPEAL_TARGET_DIVERGES: "replay_bug",
}

_RULE_ID: dict[str, str] = {
    _FAMILY_EXACT: NZ_DRY_RUN_ORACLE_NODE_AGREES_RULE_ID,
    _FAMILY_REPEAL_TARGET_AGREES: NZ_DRY_RUN_ORACLE_REPEAL_TARGET_AGREES_RULE_ID,
    _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED: NZ_DRY_RUN_ORACLE_REPEAL_TARGET_TEXT_NOT_ERASED_RULE_ID,
    _FAMILY_UNAPPLIED_NON_REPEAL: NZ_DRY_RUN_ORACLE_UNAPPLIED_NON_REPEAL_RULE_ID,
    _FAMILY_REPEAL_TARGET_DIVERGES: NZ_DRY_RUN_ORACLE_REPEAL_TARGET_DIVERGES_RULE_ID,
}

_FORBIDDEN_SHORTCUTS = (
    "dry_run_whole_tree_agreement_as_replay_authorization",
    "unapplied_non_repeal_residual_as_replay_bug",
    "oracle_consolidation_view_as_source_truth",
)


# --- Shared agreement-residual classification --------------------------------
#
# These two maps are the single typed-family vocabulary the agreement surfaces
# reuse: the actual-replay refusal lane (every fail-closed refusal row) and the
# standalone candidate-vs-oracle comparator (every node-status row). They extend
# the whole-tree ``_CORE_FAMILY`` split above so a typed disagreement family is
# attached to EVERY mismatch row, and the source-honest disagreement (the source
# simply does not license the op, or the temporal window/footing is missing)
# stays distinct from a genuine replay bug (the kernel applied an op and its
# materialized result diverged from the oracle).


def classify_refusal_family(
    *,
    refusal_rule_id: str,
    dry_run_refusal_rule_id: str = "",
) -> AgreementResidualFamily:
    """Type one actual-replay refusal row into a core agreement-residual family.

    The split is source-honest-vs-bug, not pass/fail magnitude:

    * The kernel produced a candidate mutation whose oracle match was a residual
      (the dry-run proof did not agree) or whose composited slice diverged → a
      genuine ``replay_bug`` (the highest-value finding).
    * The op was refused before any mutation because the source does not license
      it (target not present/ambiguous/recovered, payload not extractable, anchor
      not derivable, new node already present, family not promotable, or a
      sibling op in the same fail-closed transition blocked it) → source-honest
      ``accepted_non_executable_frontier``.
    * The before/oracle XML (the footing the replay reads from) is unreadable, or
      the amending payload XML could not be read → ``source_footing_gap``.
    * The before/on-or-after version window is missing for the transition's date
      → ``temporal_mismatch``.

    Fail-CLOSED totality: the classifier is EXHAUSTIVE over every declared
    actual-replay refusal reason. An unmapped ``refusal_rule_id`` (a NEW refusal
    constant nobody typed here) returns the CTSF-failing ``unknown`` family, NOT
    a silent benign default that would launder a real defect into a pass — the
    same fail-loud discipline as EU's ``_KIND_TO_CLASS`` KeyError idiom.
    """

    # Imported lazily to avoid an import cycle: ``actual_replay`` imports this
    # classifier, so its rule-id constants are read at call time, not load time.
    from lawvm.new_zealand.actual_replay import (
        NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
    )

    rule = refusal_rule_id or ""
    dry_rule = dry_run_refusal_rule_id or ""

    # A per-op dry-run block carries no mutation of its own: its family is that of
    # the UNDERLYING dry-run refusal reason. A dry-run refusal is ALWAYS a
    # pre-mutation decline (nothing was materialized) so it can never be a
    # ``replay_bug``; temporal / footing are lifted out and everything else is a
    # source-honest frontier. This is the ONLY case ``dry_run_refusal_rule_id`` is
    # consulted, and the frontier default here is safe-by-construction (a
    # pre-mutation decline cannot be a landed replay defect), NOT a fail-open
    # catch-all over the actual-replay refusal vocabulary.
    if rule == NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID:
        if dry_rule == NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID:
            return "temporal_mismatch"
        if (
            dry_rule
            in {
                NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
                NZ_DRY_RUN_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            }
            or "amending_act_xml_unreadable" in dry_rule
        ):
            return "source_footing_gap"
        return "accepted_non_executable_frontier"

    # EXHAUSTIVE, fail-CLOSED map over every OTHER declared actual-replay refusal
    # reason. A rule id absent here is a NEW, untyped refusal reason: it MUST fall
    # to the CTSF-failing ``unknown`` family via ``.get(rule, "unknown")`` — NOT a
    # silent benign default that would launder a genuine replay defect into a
    # pass. The paired totality test
    # (``test_classify_refusal_family_is_total_over_declared_refusal_reasons``)
    # proves every declared ``NZ_ACTUAL_REPLAY_REFUSED_*`` constant is typed here
    # (or by the delegation branch above), so adding a new one without typing it
    # fails red.
    family_by_rule: dict[str, AgreementResidualFamily] = {
        # Genuine replay-direction divergence: a mutation was materialized and its
        # result disagreed with the oracle (in isolation, or after compositing).
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID: "replay_bug",
        NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID: "replay_bug",
        # Temporal: the change window for the transition's date is absent.
        NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID: "temporal_mismatch",
        # Footing: the replay's before / on-or-after oracle XML could not be read.
        NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID: "source_footing_gap",
        NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID: "source_footing_gap",
        # Source-honest frontier: the op was correctly declined (before or at the
        # mutation boundary) because the source does not license a clean, exact
        # replay — a perturbed mutation boundary, a non-promotable family, a
        # structural payload that could not be re-materialized, a missing
        # operation surface, or a carried family-level "nothing to replay" receipt.
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID: "accepted_non_executable_frontier",
        NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID: "accepted_non_executable_frontier",
        NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID: "accepted_non_executable_frontier",
        NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID: "accepted_non_executable_frontier",
        NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID: "accepted_non_executable_frontier",
    }
    return family_by_rule.get(rule, "unknown")


# Comparator node-status (agreement.py ``_node_agreement_status`` plus the
# present/absent partitions) → core agreement-residual family. The standalone
# candidate-vs-oracle comparator is source-tree-vs-source-tree, so a divergence
# is a non-commensurable / topology / editorial surface signal, never a kernel
# replay bug: this comparator never applies an op, so it can never PRODUCE a
# replay bug. ``replay_bug`` is reserved for surfaces that actually materialize.
_COMPARATOR_STATUS_FAMILY: dict[str, AgreementResidualFamily] = {
    "exact": "agreement",
    # Both sides present, legal text differs: the candidate and oracle source
    # surfaces are not commensurable at this node.
    "changed": "non_commensurable_surface",
    # Same legal text, different stable id / history witnesses: editorial drift
    # in the oracle's consolidation view, not a substantive disagreement.
    "text_exact_identity_drift": "oracle_editorial_pathology",
    "text_exact_history_drift": "oracle_editorial_pathology",
    # One side has a node the other does not: a topology/granularity mismatch
    # between the two trees.
    "oracle_only": "topology_granularity_mismatch",
    "candidate_only": "topology_granularity_mismatch",
}


def classify_comparator_status_family(comparator_status: str) -> AgreementResidualFamily:
    """Type one standalone-comparator node status into a core family."""

    return _COMPARATOR_STATUS_FAMILY.get(comparator_status or "", "unknown")


@dataclass(frozen=True)
class NZDryRunOracleResidual:
    """One classified whole-tree residual node."""

    path: tuple[str, ...]
    disposition: str
    family: str
    rule_id: str
    is_repeal_target: bool
    candidate_occupancy: str = ""
    oracle_occupancy: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "path": list(self.path),
            "disposition": self.disposition,
            "family": self.family,
            "rule_id": self.rule_id,
            "is_repeal_target": self.is_repeal_target,
            "candidate_occupancy": self.candidate_occupancy,
            "oracle_occupancy": self.oracle_occupancy,
        }


@dataclass(frozen=True)
class NZDryRunOracleWindowComparison:
    """Whole-tree candidate-after vs archived on-or-after comparison for one window."""

    requested_version_date: str
    before_version_id: str
    oracle_version_id: str
    repeal_target_paths: tuple[tuple[str, ...], ...]
    candidate_node_count: int
    oracle_node_count: int
    exact_agreement_count: int
    repeal_target_agreement_count: int
    residuals: tuple[NZDryRunOracleResidual, ...]

    def family_counts(self) -> dict[str, int]:
        return _counts(residual.family for residual in self.residuals)

    def diverging_residuals(self) -> tuple[NZDryRunOracleResidual, ...]:
        return tuple(
            residual for residual in self.residuals if residual.family == _FAMILY_REPEAL_TARGET_DIVERGES
        )

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requested_version_date": self.requested_version_date,
            "before_version_id": self.before_version_id,
            "oracle_version_id": self.oracle_version_id,
            "repeal_target_paths": [list(path) for path in self.repeal_target_paths],
            "candidate_node_count": self.candidate_node_count,
            "oracle_node_count": self.oracle_node_count,
            "exact_agreement_count": self.exact_agreement_count,
            "repeal_target_agreement_count": self.repeal_target_agreement_count,
            "repeal_targets_agree": all(
                residual.family != _FAMILY_REPEAL_TARGET_DIVERGES for residual in self.residuals
            ),
            "family_counts": self.family_counts(),
        }
        if not summary_only:
            payload["residuals"] = [residual.to_jsonable() for residual in self.residuals]
        return payload


@dataclass(frozen=True)
class NZDryRunOracleComparisonReport:
    """Whole-document dry-run-vs-oracle comparison report.

    The repeal slice (just the mutated target nodes) is reported separately from
    the whole-tree agreement so the source-honest "other operations not yet
    replayed" residuals never masquerade as repeal-direction divergence.
    """

    work_id: str
    operation_family: str
    preflight_status: str
    dry_run: NZDryRunReport
    windows: tuple[NZDryRunOracleWindowComparison, ...]
    forbidden_shortcuts: tuple[str, ...] = _FORBIDDEN_SHORTCUTS

    def all_residuals(self) -> tuple[NZDryRunOracleResidual, ...]:
        return tuple(residual for window in self.windows for residual in window.residuals)

    def repeal_target_residuals(self) -> tuple[NZDryRunOracleResidual, ...]:
        return tuple(residual for residual in self.all_residuals() if residual.is_repeal_target)

    def diverging_residuals(self) -> tuple[NZDryRunOracleResidual, ...]:
        return tuple(
            residual
            for residual in self.all_residuals()
            if residual.family == _FAMILY_REPEAL_TARGET_DIVERGES
        )

    def summary(self) -> dict[str, Any]:
        residuals = self.all_residuals()
        candidate_nodes = sum(window.candidate_node_count for window in self.windows)
        oracle_nodes = sum(window.oracle_node_count for window in self.windows)
        exact = sum(window.exact_agreement_count for window in self.windows)
        repeal_target_agree = sum(window.repeal_target_agreement_count for window in self.windows)
        repeal_target_residuals = self.repeal_target_residuals()
        repeal_target_diverging = self.diverging_residuals()
        dry_run_summary = self.dry_run.summary()
        return {
            "work_id": self.work_id,
            "operation_family": self.operation_family,
            "preflight_status": self.preflight_status,
            "windows_compared": len(self.windows),
            # Repeal slice: the per-node dry-run partition (mutated targets only).
            "repeal_ops_dry_run": dry_run_summary["operations_dry_run"],
            "repeal_ops_refused": dry_run_summary["operations_refused"],
            "repeal_target_nodes": len(repeal_target_residuals),
            "repeal_target_agreements": repeal_target_agree,
            "repeal_target_divergences": len(repeal_target_diverging),
            "repeal_slice_agrees": len(repeal_target_diverging) == 0,
            # Whole-tree: every candidate-after node vs every oracle node.
            "candidate_after_nodes": candidate_nodes,
            "oracle_nodes": oracle_nodes,
            "whole_tree_exact_agreements": exact,
            "whole_tree_residuals": len(residuals),
            "residual_family_counts": _counts(residual.family for residual in residuals),
            "residual_core_family_counts": _counts(
                _CORE_FAMILY.get(residual.family, "unknown") for residual in residuals
            ),
            "unapplied_non_repeal_residuals": sum(
                1 for residual in residuals if residual.family == _FAMILY_UNAPPLIED_NON_REPEAL
            ),
            # Never an actual-replay claim.
            "replay_claims": False,
            "actual_replay_agreements": 0,
            "dry_run_claims": True,
        }

    def agreement_surface(self) -> dict[str, Any]:
        """Project the whole-tree residuals into the shared agreement surface."""

        residuals: list[AgreementResidual] = []
        for residual in self.all_residuals():
            status = "agrees" if residual.family in (_FAMILY_EXACT, _FAMILY_REPEAL_TARGET_AGREES) else "residual"
            if residual.family in (_FAMILY_UNAPPLIED_NON_REPEAL, _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED):
                status = "frontier"
            residuals.append(
                AgreementResidual(
                    residual_id=f"{self.work_id}:{'/'.join(residual.path) or 'root'}:{residual.family}",
                    jurisdiction="nz",
                    agreement_surface="nz_dry_run_repeal_whole_tree",
                    family=_CORE_FAMILY.get(residual.family, "unknown"),
                    agreement_residual_status=status,
                    owner_phase="dry_run",
                    rule_id=residual.rule_id,
                    source_artifact_id="/".join(residual.path),
                    replay_count=1 if residual.candidate_occupancy else 0,
                    oracle_count=1 if residual.oracle_occupancy else 0,
                    safe_default=(
                        "keep_whole_tree_residual_visible_without_authorizing_replay_or_oracle_truth"
                    ),
                    forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
                    detail={
                        "disposition": residual.disposition,
                        "is_repeal_target": residual.is_repeal_target,
                        "candidate_occupancy": residual.candidate_occupancy,
                        "oracle_occupancy": residual.oracle_occupancy,
                    },
                )
            )
        candidate_nodes = sum(window.candidate_node_count for window in self.windows)
        exact = sum(window.exact_agreement_count for window in self.windows)
        surface = agreement_surface_from_residuals(
            tuple(residuals),
            jurisdiction="nz",
            agreement_surface="nz_dry_run_repeal_whole_tree",
            materialization_id=f"nz_dry_run_candidate_after:{self.work_id}",
            comparison_target_id=f"nz_on_or_after_oracle:{self.work_id}",
            comparison_kind="dry_run_candidate_after_tree_vs_archived_on_or_after_xml_whole_document",
            materialization_kind="proposed_future_branch",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=(exact / candidate_nodes) if candidate_nodes else None,
        )
        return surface.to_dict()

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "dry_run_repeal_whole_tree_oracle_comparison",
            "truth_claim": (
                "dry_run_candidate_after_tree_vs_archived_on_or_after_xml_not_actual_replay"
            ),
            "replay_claims": False,
            "dry_run_claims": True,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["windows"] = [window.to_jsonable() for window in self.windows]
        payload["dry_run_summary"] = self.dry_run.summary()
        payload["agreement_surface"] = self.agreement_surface()
        return payload


def build_archived_work_dry_run_oracle_comparison(
    db_path: Path, work_id: str
) -> NZDryRunOracleComparisonReport:
    """Build the whole-tree dry-run-vs-oracle comparison for one archived NZ work."""

    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    archive = open_farchive(db_path)
    try:
        return build_dry_run_oracle_comparison(archive, work_id=work_id, preflight=preflight)
    finally:
        archive.close()


def build_dry_run_oracle_comparison(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
) -> NZDryRunOracleComparisonReport:
    """Materialize candidate after-documents and compare whole-tree to the oracle.

    The repeal *slice* partition is delegated to the existing per-node dry-run
    surface (reused, not re-implemented). The whole-document comparison is built
    on top of that same apply kernel.
    """

    dry_run = build_dry_run_repeal(archive, work_id=work_id, preflight=preflight)
    preflight_status = dry_run.preflight_status

    # Group the replayable repeal operations by their change window. Each window
    # has its own before/oracle pair; the candidate after-document for that
    # window is the before document with that window's repeal targets tombstoned.
    parsed_cache: dict[str, NZSourceDocument | None] = {}
    windows: list[NZDryRunOracleWindowComparison] = []

    if preflight_status == "ready_for_dry_run_replay":
        plan = _window_plan(archive, work_id, preflight, parsed_cache)
        for window in plan:
            windows.append(_compare_one_window(window))

    return NZDryRunOracleComparisonReport(
        work_id=work_id,
        operation_family="repeal",
        preflight_status=preflight_status,
        dry_run=dry_run,
        windows=tuple(windows),
    )


@dataclass(frozen=True)
class _WindowPlan:
    requested_version_date: str
    before_doc: NZSourceDocument
    oracle_doc: NZSourceDocument
    repeal_target_paths: tuple[tuple[str, ...], ...]


def _window_plan(
    archive: Any,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    parsed_cache: dict[str, NZSourceDocument | None],
) -> tuple[_WindowPlan, ...]:
    # Bucket the dry-run-eligible repeal targets by amendment date (= version
    # window). Reuse the dry-run surface's own row/path/window resolution so this
    # surface consumes exactly the operations the dry-run surface applies.
    by_date: dict[str, list[tuple[str, ...]]] = {}
    for row in _replayable_repeal_rows(preflight):
        operation = row.operation
        if operation is None:
            continue
        amendment_date_iso = row.amendment_date_iso
        if not amendment_date_iso:
            continue
        # Defence in depth mirrors the dry-run kernel: only exact targets mutate.
        if (
            row.latest_oracle_target_resolution_status
            and row.latest_oracle_target_resolution_status != "exact_source_path"
        ):
            continue
        source_path = _source_path_for_address(operation)
        if source_path is None:
            continue
        by_date.setdefault(amendment_date_iso, []).append(source_path)

    plans: list[_WindowPlan] = []
    for amendment_date_iso in sorted(by_date):
        change_window = archived_xml_version_change_window(
            archive, work_id=work_id, version_date=amendment_date_iso
        )
        if change_window.before is None or change_window.on_or_after is None:
            continue
        before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
        oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
        if before_doc is None or oracle_doc is None:
            continue
        # Only keep targets that are an exact, substantive, unambiguous node in
        # the before tree — same precondition the dry-run kernel enforces.
        applicable: list[tuple[str, ...]] = []
        for source_path in by_date[amendment_date_iso]:
            before_matches = _nodes_at_path(before_doc, source_path)
            if len(before_matches) != 1:
                continue
            if _occupancy(before_matches[0]) != "substantive":
                continue
            applicable.append(source_path)
        if not applicable:
            continue
        plans.append(
            _WindowPlan(
                requested_version_date=amendment_date_iso,
                before_doc=before_doc,
                oracle_doc=oracle_doc,
                repeal_target_paths=tuple(applicable),
            )
        )
    return tuple(plans)


def _compare_one_window(plan: _WindowPlan) -> NZDryRunOracleWindowComparison:
    candidate_after = materialize_candidate_after_document(
        plan.before_doc, plan.repeal_target_paths
    )
    agreement = compare_source_documents(candidate_after, plan.oracle_doc)
    target_set = set(plan.repeal_target_paths)

    residuals: list[NZDryRunOracleResidual] = []
    exact_count = 0
    repeal_target_agree_count = 0
    candidate_index = {node.path: node for node in candidate_after.nodes}
    oracle_index = {node.path: node for node in plan.oracle_doc.nodes}

    for row in agreement.rows:
        is_target = row.path in target_set
        candidate_occ = _occupancy_or_blank(candidate_index.get(row.path))
        oracle_occ = _occupancy_or_blank(oracle_index.get(row.path))
        if row.agreement_status == "exact":
            exact_count += 1
            if is_target:
                # Our tombstone matched the oracle's tombstone exactly.
                repeal_target_agree_count += 1
                residuals.append(
                    _residual(row.path, "exact", _FAMILY_REPEAL_TARGET_AGREES, True, candidate_occ, oracle_occ)
                )
            # Non-target exact nodes are silent agreement; do not emit a residual.
            continue
        # The node differs from the oracle.
        if is_target:
            # We applied a repeal here. Decide whether the *repeal direction*
            # agrees: both candidate and oracle must be tombstones. NZ
            # consolidations additionally erase the repealed provision body text,
            # which the boring tombstone kernel deliberately does not do, so a
            # tombstone-vs-tombstone text difference is source-honest, NOT a
            # repeal-direction bug. Only a non-tombstone oracle node at a path we
            # repealed is genuine replay-direction divergence — the highest-value
            # finding.
            if oracle_occ == "tombstone" and candidate_occ == "tombstone":
                family = _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED
            else:
                family = _FAMILY_REPEAL_TARGET_DIVERGES
        else:
            # We did not touch this node; the dry-run only applies repeal. The
            # difference is an oracle-side change from some other operation in
            # the window (text replace, insert, other repeal, editorial drift)
            # that we did not — and must not — apply. Source-honest frontier.
            family = _FAMILY_UNAPPLIED_NON_REPEAL
        if family == _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED:
            # Repeal direction agreed (tombstone vs tombstone); count it as a
            # repeal-target agreement even though the oracle erased body text.
            repeal_target_agree_count += 1
        residuals.append(
            _residual(row.path, row.agreement_status, family, is_target, candidate_occ, oracle_occ)
        )

    return NZDryRunOracleWindowComparison(
        requested_version_date=plan.requested_version_date,
        before_version_id=plan.before_doc.version_id,
        oracle_version_id=plan.oracle_doc.version_id,
        repeal_target_paths=plan.repeal_target_paths,
        candidate_node_count=len(candidate_after.nodes),
        oracle_node_count=len(plan.oracle_doc.nodes),
        exact_agreement_count=exact_count,
        repeal_target_agreement_count=repeal_target_agree_count,
        residuals=tuple(residuals),
    )


def materialize_candidate_after_document(
    before_doc: NZSourceDocument,
    repeal_target_paths: tuple[tuple[str, ...], ...],
) -> NZSourceDocument:
    """Produce the full candidate after-document for a dry-run repeal window.

    The result is identical to ``before_doc`` except every node whose path is in
    ``repeal_target_paths`` is converted to a tombstone with the existing
    ``dry_run`` apply kernel. No other node is touched, reordered, added, or
    removed. The document is immutable (a fresh frozen ``NZSourceDocument``).

    Repeal semantics are NOT re-implemented here: the single boring mutation is
    delegated to :func:`lawvm.new_zealand.dry_run._tombstone_node`.
    """

    target_set = set(repeal_target_paths)
    new_nodes: list[NZSourceNode] = []
    for node in before_doc.nodes:
        if node.path in target_set and not node.deletion_status:
            new_nodes.append(_tombstone_node(node))
        else:
            new_nodes.append(node)
    return NZSourceDocument(
        xml_locator=before_doc.xml_locator,
        version_id=before_doc.version_id,
        metadata=before_doc.metadata,
        nodes=tuple(new_nodes),
        document_history=before_doc.document_history,
    )


def _residual(
    path: tuple[str, ...],
    disposition: str,
    family: str,
    is_repeal_target: bool,
    candidate_occupancy: str,
    oracle_occupancy: str,
) -> NZDryRunOracleResidual:
    return NZDryRunOracleResidual(
        path=path,
        disposition=disposition,
        family=family,
        rule_id=_RULE_ID[family],
        is_repeal_target=is_repeal_target,
        candidate_occupancy=candidate_occupancy,
        oracle_occupancy=oracle_occupancy,
    )


def _occupancy_or_blank(node: NZSourceNode | None) -> str:
    if node is None:
        return "absent"
    return _occupancy(node)


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main(args: Any) -> None:
    import json

    report = build_archived_work_dry_run_oracle_comparison(Path(args.db), args.work_id)
    if args.json:
        print(
            json.dumps(
                report.to_jsonable(summary_only=args.summary_only),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    summary = report.summary()
    print(
        f"work_id={summary['work_id']} preflight_status={summary['preflight_status']} "
        f"windows_compared={summary['windows_compared']} "
        f"repeal_target_agreements={summary['repeal_target_agreements']}/"
        f"{summary['repeal_target_nodes']} "
        f"repeal_target_divergences={summary['repeal_target_divergences']} "
        f"repeal_slice_agrees={summary['repeal_slice_agrees']}"
    )
    print(
        f"whole_tree: candidate_after_nodes={summary['candidate_after_nodes']} "
        f"oracle_nodes={summary['oracle_nodes']} "
        f"exact_agreements={summary['whole_tree_exact_agreements']} "
        f"residuals={summary['whole_tree_residuals']}"
    )
    print(f"residual_family_counts={summary['residual_family_counts']}")
    print(f"residual_core_family_counts={summary['residual_core_family_counts']}")
    print(f"actual_replay_blocking_rule_id={NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID}")
    if args.summary_only:
        return
    diverging = report.diverging_residuals()
    if diverging:
        print("GENUINE-DIVERGENCE (applied repeal disagrees with oracle):")
        for residual in diverging:
            print(
                f"\tDIVERGENCE\t{'/'.join(residual.path)}\t"
                f"{residual.disposition}\tcandidate={residual.candidate_occupancy} "
                f"oracle={residual.oracle_occupancy}"
            )
    for window in report.windows:
        print(
            f"WINDOW\t{window.requested_version_date}\t"
            f"before={window.before_version_id}\toracle={window.oracle_version_id}\t"
            f"exact={window.exact_agreement_count}/{window.candidate_node_count}\t"
            f"family_counts={window.family_counts()}"
        )
