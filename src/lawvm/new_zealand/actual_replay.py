"""Strict actual (canonical) replay for New Zealand — Phase 4.

Every other NZ replay surface is dry-run by design: it materializes a candidate
after-tree and compares it to the archived oracle, but it never *claims* the
materialization as real replay (``replay_claims`` stays ``False`` everywhere,
``materialization_kind`` is ``proposed_future_branch``).

This surface is the narrow, fail-closed gate that converts a dry-run-PROVEN
transition into an actual materialized replay. It is deliberately small:

* It consumes ONLY operations the dry-run surface already verified — a
  per-operation mutation-boundary proof whose ``oracle_match == "agrees"`` AND
  whose ``neighbors_unchanged`` is true. Such a proof is ``dry_run_verified``:
  the boring apply kernel produced an after-node that agrees with the archived
  on-or-after oracle and disturbed nothing else.
* It materializes ONE transition at a time: ``(before version) + (authorized
  ops) -> (candidate after version)``. The before/oracle archived version ids
  and the amendment date are preserved as temporal witnesses on every replayed
  transition.
* It FAILS CLOSED. A transition is replayed only when EVERY op declared in that
  transition's change window is ``dry_run_verified``. If any declared op is
  refused, or its proof does not agree, or its mutation perturbed a neighbour,
  the whole transition is refused with a distinct named diagnostic and NOTHING
  is materialized for it. There is never a silent skip and never a guessed
  fallback.
* Four families are promotable: direct repeal, direct single-occurrence text
  substitution, structural whole-provision replace, and structural whole-provision
  or nested insert. Source-diff-only / recovered-carrier candidates never reach
  this surface: the dry-run kernel refuses them upstream (target-recovery,
  source-change-only) so their proofs are never ``agrees``.
* Its output is a SEPARATE artifact from the official NZ XML, with explicit
  candidate / replay / oracle labels. The archived on-or-after XML is the
  oracle the materialized replay is *checked against*; it is never treated as
  the payload authority for the replay itself.

The replayed-transition count is reported separately from the
refused-fail-closed count, so the number of transitions that were ACTUALLY
replayed is always separable from the candidate-only rows that were blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualStatus,
    agreement_surface_from_residuals,
)
from lawvm.core.semantic_types import StructuralAction
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZDryRunReport,
    NZMutationBoundaryProof,
    NZStructuralMaterializationError,
    _leaf_source_kind,
    _oracle_partition,
    _oracle_partition_insert,
    _oracle_partition_replace,
    _oracle_partition_text,
    _parse_archived_version,
    _reextract_structural_insertion_for_proof,
    _reextract_structural_replacement_for_proof,
    _resolve_target_nodes,
    _substitute_node_text,
    _tombstone_node,
    apply_structural_insert_to_nodes,
    apply_structural_replace_to_nodes,
    build_dry_run_insert,
    build_dry_run_replace,
    build_dry_run_repeal,
)
from lawvm.core.comparison_normalization import normalized_inline_occurrence_count
from lawvm.new_zealand.effect_candidates import (
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import NZSourceDocument, NZSourceNode
from lawvm.new_zealand.version_diff import archived_xml_version_change_window


# --- Family scope. ------------------------------------------------------------
# The promotable families. Only these may be promoted to actual replay; any other
# family is refused before it can materialize. Repeal and single-occurrence text
# substitution are the original two; structural whole-provision REPLACE and whole/
# nested INSERT are promoted here because they extract strongly via dry-run (a
# per-op mutation-boundary proof whose oracle match is "agrees" with neighbours
# unchanged), so the dry-run-VERIFIED ones can be materialized into actual replay.
NZ_ACTUAL_REPLAY_FAMILY_REPEAL = "repeal"
NZ_ACTUAL_REPLAY_FAMILY_TEXT_REPLACE = "text_replace"
NZ_ACTUAL_REPLAY_FAMILY_REPLACE = "replace"
NZ_ACTUAL_REPLAY_FAMILY_INSERT = "insert"
NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES = (
    NZ_ACTUAL_REPLAY_FAMILY_REPEAL,
    NZ_ACTUAL_REPLAY_FAMILY_TEXT_REPLACE,
    NZ_ACTUAL_REPLAY_FAMILY_REPLACE,
    NZ_ACTUAL_REPLAY_FAMILY_INSERT,
)
_FAMILY_TO_DRY_RUN_SCOPE = {
    NZ_ACTUAL_REPLAY_FAMILY_REPEAL: NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_ACTUAL_REPLAY_FAMILY_TEXT_REPLACE: NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    NZ_ACTUAL_REPLAY_FAMILY_REPLACE: NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_ACTUAL_REPLAY_FAMILY_INSERT: NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
}
# The structural families are operation-surface driven (not preflight driven);
# they need a built operation surface, and their dry-run is routed to its own
# surface-consuming builder rather than build_dry_run_repeal.
_SURFACE_DRIVEN_FAMILIES = frozenset(
    {NZ_ACTUAL_REPLAY_FAMILY_REPLACE, NZ_ACTUAL_REPLAY_FAMILY_INSERT}
)

# --- Distinct named refusal diagnostics (fail-closed vocabulary). -------------
# A transition is refused (nothing materialized) when any of these holds. Each
# is a distinct named rule id so a refusal is never an opaque or silent skip.
NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID = (
    "nz_actual_replay_refused_declared_op_not_dry_run_verified"
)
NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID = (
    "nz_actual_replay_refused_declared_op_dry_run_oracle_residual_not_agreement"
)
NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID = (
    "nz_actual_replay_refused_declared_op_mutation_perturbed_neighbours"
)
NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID = (
    "nz_actual_replay_refused_op_family_not_in_promotable_set"
)
NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID = (
    "nz_actual_replay_refused_before_version_xml_unreadable"
)
NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID = (
    "nz_actual_replay_refused_on_or_after_version_xml_unreadable"
)
NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID = (
    "nz_actual_replay_refused_missing_before_after_version_window"
)
NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID = (
    "nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle"
)
NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID = (
    "nz_actual_replay_refused_structural_payload_not_re_materializable"
)
NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID = (
    "nz_actual_replay_refused_operation_surface_missing_for_structural_family"
)

# Agreement / replay rule ids.
NZ_ACTUAL_REPLAY_TRANSITION_MATERIALIZED_RULE_ID = (
    "nz_actual_replay_transition_materialized_from_archived_before_and_verified_ops"
)
NZ_ACTUAL_REPLAY_SLICE_AGREES_RULE_ID = (
    "nz_actual_replay_materialized_target_slice_agrees_with_archived_on_or_after_oracle"
)

_FORBIDDEN_SHORTCUTS = (
    "unverified_op_as_replay_authorization",
    "oracle_consolidation_view_as_replay_payload_authority",
    "blocked_candidate_row_as_replayed_transition",
)

# Dry-run refusals that are family-level (no candidate set / preflight not ready)
# rather than a per-op block of a declared transition. These never create a
# phantom blocked transition; they simply mean the family declared nothing.
_FAMILY_LEVEL_DRY_RUN_REFUSALS = frozenset(
    {
        NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_NO_REPLACE_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
    }
)


@dataclass(frozen=True)
class NZActualReplayMutation:
    """One verified op that was actually applied in a materialized transition.

    Carries the proof identity it was authorized by plus the before/after node
    digests, so the materialized mutation is auditable against the dry-run proof
    it was promoted from.
    """

    op_id: str
    action: str
    family: str
    target_address: str
    target_source_path: tuple[str, ...]
    target_digest_before: str
    target_digest_after: str
    dry_run_oracle_match_rule_id: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "action": self.action,
            "family": self.family,
            "target_address": self.target_address,
            "target_source_path": list(self.target_source_path),
            "target_digest_before": self.target_digest_before,
            "target_digest_after": self.target_digest_after,
            "dry_run_oracle_match_rule_id": self.dry_run_oracle_match_rule_id,
        }


@dataclass(frozen=True)
class NZActualReplayRefusal:
    """A declared transition (or op) refused — nothing was materialized for it.

    This is the fail-closed product. ``op_ids`` lists every op in the declared
    transition; the whole transition is blocked even when only one op failed
    verification, because actual replay never partially materializes a declared
    transition.
    """

    rule_id: str
    message: str
    amendment_date_iso: str = ""
    op_ids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "amendment_date_iso": self.amendment_date_iso,
            "op_ids": list(self.op_ids),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class NZActualReplayedTransition:
    """One transition that was ACTUALLY replayed (not dry-run).

    The before/oracle archived version ids and the amendment date are the
    temporal witnesses: the materialized after-document came from the archived
    ``before_version_id`` snapshot, the authorized ops were applied, and the
    result is checked against the archived ``oracle_version_id`` snapshot dated
    on or after ``amendment_date_iso``.
    """

    amendment_date_iso: str
    before_version_id: str
    before_xml_locator: str
    oracle_version_id: str
    oracle_xml_locator: str
    mutations: tuple[NZActualReplayMutation, ...]
    materialized_node_count: int
    oracle_node_count: int
    target_slice_node_count: int
    target_slice_agreements: int
    # The actual replay OUTPUT: the materialized after-document (a separate
    # artifact from the official NZ XML). Oracle agreement consumes this, not a
    # hand-picked candidate XML.
    materialized_after: NZSourceDocument = field(repr=False)

    @property
    def target_slice_agrees(self) -> bool:
        return (
            self.target_slice_node_count > 0
            and self.target_slice_agreements == self.target_slice_node_count
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "amendment_date_iso": self.amendment_date_iso,
            "before_version_id": self.before_version_id,
            "before_xml_locator": self.before_xml_locator,
            "oracle_version_id": self.oracle_version_id,
            "oracle_xml_locator": self.oracle_xml_locator,
            "mutations": [mutation.to_jsonable() for mutation in self.mutations],
            "materialized_node_count": self.materialized_node_count,
            "oracle_node_count": self.oracle_node_count,
            "target_slice_node_count": self.target_slice_node_count,
            "target_slice_agreements": self.target_slice_agreements,
            "target_slice_agrees": self.target_slice_agrees,
            "slice_agreement_rule_id": NZ_ACTUAL_REPLAY_SLICE_AGREES_RULE_ID,
            "transition_rule_id": NZ_ACTUAL_REPLAY_TRANSITION_MATERIALIZED_RULE_ID,
        }


@dataclass(frozen=True)
class NZActualReplayReport:
    """Strict actual-replay report for one archived NZ work.

    Unlike every dry-run surface, ``replay_claims`` is ``True`` for the
    transitions in :attr:`transitions`: those were materialized from archived
    inputs and the verified target slice agrees with the archived oracle. The
    blocked candidate rows live in :attr:`refusals` and are counted separately,
    so the count of actually-replayed transitions is always separable from the
    candidate-only rows that were fail-closed-blocked.
    """

    work_id: str
    families: tuple[str, ...]
    transitions: tuple[NZActualReplayedTransition, ...]
    refusals: tuple[NZActualReplayRefusal, ...]
    dry_run_reports: tuple[NZDryRunReport, ...] = ()
    # Families that were requested but could not even be ATTEMPTED (e.g. a
    # structural family requested without an operation surface). These are NOT
    # per-transition refusals — they declared nothing — so they are reported
    # separately and never inflate the fail-closed-blocked transition count.
    families_not_attempted: tuple[NZActualReplayRefusal, ...] = ()
    forbidden_shortcuts: tuple[str, ...] = _FORBIDDEN_SHORTCUTS

    def replayed_mutation_count(self) -> int:
        return sum(len(transition.mutations) for transition in self.transitions)

    def all_slices_agree(self) -> bool:
        return all(transition.target_slice_agrees for transition in self.transitions)

    def summary(self) -> dict[str, Any]:
        replayed = self.replayed_mutation_count()
        slice_nodes = sum(transition.target_slice_node_count for transition in self.transitions)
        slice_agreements = sum(transition.target_slice_agreements for transition in self.transitions)
        return {
            "work_id": self.work_id,
            "families": list(self.families),
            # Actually-replayed transitions vs fail-closed-blocked declared
            # transitions — always separately countable.
            "transitions_replayed": len(self.transitions),
            "transitions_refused": len(self.refusals),
            "ops_replayed": replayed,
            "target_slice_nodes": slice_nodes,
            "target_slice_agreements": slice_agreements,
            "all_slices_agree": self.all_slices_agree(),
            "refusal_rule_counts": _counts(refusal.rule_id for refusal in self.refusals),
            # Typed disagreement-family counts over every row (agrees +
            # refusals), so a benchmark can count agreement by family and keep
            # source-honest disagreement distinct from a replay bug.
            "residual_family_counts": _counts(
                residual.family for residual in self.agreement_residuals()
            ),
            "residual_status_counts": _counts(
                residual.agreement_residual_status for residual in self.agreement_residuals()
            ),
            # Families requested but not attempted (e.g. structural family with no
            # operation surface). Separate from the fail-closed transition count.
            "families_not_attempted": _counts(
                refusal.detail.get("family", "") for refusal in self.families_not_attempted
            ),
            # This surface DOES claim actual replay — for the transitions it
            # materialized and verified against the archived oracle.
            "replay_claims": bool(self.transitions),
            "actual_replay_agreements": slice_agreements,
            "dry_run_claims": False,
        }

    def agreement_residuals(self) -> tuple[AgreementResidual, ...]:
        """Every actual-replay row typed into a core agreement-residual family.

        Two lanes feed this. The replayed-transition lane contributes one
        ``agrees`` residual per materialized mutation. The fail-closed refusal
        lane (and the families-not-attempted lane) contributes one typed residual
        per refusal, classified with the shared
        :func:`lawvm.new_zealand.dry_run_oracle.classify_refusal_family` map, so a
        source-honest refusal (``accepted_non_executable_frontier`` /
        ``temporal_mismatch`` / ``source_footing_gap``) is always distinct from a
        genuine ``replay_bug``. The result is the queryable typed-family surface a
        benchmark can count by family.
        """

        from lawvm.new_zealand.dry_run_oracle import classify_refusal_family

        residuals: list[AgreementResidual] = []
        for transition in self.transitions:
            for mutation in transition.mutations:
                residuals.append(
                    AgreementResidual(
                        residual_id=f"{self.work_id}:{mutation.op_id}:actual_replay_agrees",
                        jurisdiction="nz",
                        agreement_surface="nz_actual_replay",
                        family="agreement",
                        agreement_residual_status="agrees",
                        owner_phase="actual_replay",
                        rule_id=NZ_ACTUAL_REPLAY_SLICE_AGREES_RULE_ID,
                        source_artifact_id=mutation.op_id,
                        replay_count=1,
                        oracle_count=1,
                        safe_default="materialize_only_dry_run_verified_ops_fail_closed_otherwise",
                        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
                        detail={
                            "target_address": mutation.target_address,
                            "amendment_date_iso": transition.amendment_date_iso,
                            "before_version_id": transition.before_version_id,
                            "oracle_version_id": transition.oracle_version_id,
                        },
                    )
                )
        for index, refusal in enumerate(self.refusals + self.families_not_attempted):
            residuals.append(_refusal_residual(self.work_id, index, refusal, classify_refusal_family))
        return tuple(residuals)

    def agreement_surface(self) -> dict[str, Any]:
        """Project every actual-replay row into the shared agreement surface.

        Each actually-replayed target node that agrees with the archived oracle
        is an ``agrees`` residual owned by the ``actual_replay`` phase, and each
        fail-closed refusal is a typed residual (source-honest disagreement vs a
        genuine ``replay_bug``, kept distinct). The materialization is labeled
        ``legal_text_state`` (an actually-reconstructed legal state, NOT a
        ``proposed_future_branch`` dry-run candidate); the comparison target is
        the official consolidation view. The oracle is the thing the replay is
        checked against, never the replay's payload authority — encoded in the
        forbidden shortcuts.
        """

        residuals = self.agreement_residuals()
        slice_nodes = sum(transition.target_slice_node_count for transition in self.transitions)
        slice_agreements = sum(transition.target_slice_agreements for transition in self.transitions)
        surface = agreement_surface_from_residuals(
            tuple(residuals),
            jurisdiction="nz",
            agreement_surface="nz_actual_replay",
            materialization_id=f"nz_actual_replay:{self.work_id}",
            comparison_target_id=f"nz_on_or_after_oracle:{self.work_id}",
            comparison_kind="actual_replay_materialized_after_tree_vs_archived_on_or_after_xml",
            materialization_kind="legal_text_state",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=(slice_agreements / slice_nodes) if slice_nodes else None,
        )
        return surface.to_dict()

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "actual_replay",
            "truth_claim": (
                "actual_replay_of_dry_run_verified_ops_materialized_after_tree_"
                "vs_archived_on_or_after_xml_oracle"
            ),
            "replay_claims": bool(self.transitions),
            "dry_run_claims": False,
            "fail_closed": True,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["transitions"] = [transition.to_jsonable() for transition in self.transitions]
        payload["refusals"] = [refusal.to_jsonable() for refusal in self.refusals]
        payload["families_not_attempted"] = [
            refusal.to_jsonable() for refusal in self.families_not_attempted
        ]
        payload["agreement_surface"] = self.agreement_surface()
        return payload


# --- Build ------------------------------------------------------------------


def build_archived_work_actual_replay(
    db_path: Path,
    work_id: str,
    *,
    families: tuple[str, ...] = NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
) -> NZActualReplayReport:
    """Build the strict actual-replay report for one archived NZ work."""

    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
    requested = _validated_families(families)
    # The structural families (replace/insert) are operation-surface driven; build
    # the surface once (the same surface their dry-run consumes) only when one of
    # them is requested, so a repeal/text_replace-only run needs no surface.
    surface = (
        build_archived_work_operation_surface(db_path, work_id)
        if any(family in _SURFACE_DRIVEN_FAMILIES for family in requested)
        else None
    )
    archive = open_farchive(db_path)
    try:
        return build_actual_replay(
            archive,
            work_id=work_id,
            preflight=preflight,
            families=families,
            surface=surface,
        )
    finally:
        archive.close()


def build_actual_replay(
    archive: Any,
    *,
    work_id: str,
    preflight: NZEffectCandidatePreflightReport,
    families: tuple[str, ...] = NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
    surface: Any | None = None,
) -> NZActualReplayReport:
    """Promote dry-run-verified ops into actual materialized transitions.

    The dry-run surface is the sole source of authorized ops. We never re-derive
    candidates here: we run the dry-run for each requested family, treat each
    agreeing, neighbour-preserving proof as ``dry_run_verified``, and refuse
    everything else with a distinct named diagnostic.

    Repeal and text_replace are preflight-driven (``preflight``). The structural
    REPLACE/INSERT families are operation-surface driven (``surface``): their
    dry-run consumes the work's operation-surface witnesses + the cited amending
    act XML. When a structural family is requested without a surface, that family
    is refused with a distinct named diagnostic (never silently skipped).
    """

    requested = _validated_families(families)
    dry_run_reports: list[NZDryRunReport] = []
    # Collect verified proofs and the refusal reasons for non-verified declared
    # ops, both keyed by amendment date (= change window = one transition).
    verified_by_date: dict[str, list[_VerifiedOp]] = {}
    blocked_by_date: dict[str, list[NZActualReplayRefusal]] = {}
    surface_refusals: list[NZActualReplayRefusal] = []

    # The amendment date that defines a proof's change window is carried by the
    # candidate row, not the proof. Index op_id -> amendment date from BOTH the
    # preflight (repeal/text_replace) and the operation surface (replace/insert)
    # the dry-run consumed, so an agreeing proof is grouped into the exact
    # transition the candidate declared (no proof-schema change needed).
    amendment_date_by_op_id = _index_amendment_dates(preflight)
    if surface is not None:
        amendment_date_by_op_id.update(_index_structural_amendment_dates(surface, work_id))

    for family in requested:
        if family in _SURFACE_DRIVEN_FAMILIES:
            if surface is None:
                surface_refusals.append(
                    NZActualReplayRefusal(
                        rule_id=NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
                        message=(
                            "actual replay refused the structural family because no "
                            f"operation surface was provided (family={family})"
                        ),
                        detail={"family": family},
                    )
                )
                continue
            if family == NZ_ACTUAL_REPLAY_FAMILY_REPLACE:
                report = build_dry_run_replace(archive, work_id=work_id, surface=surface)
            else:
                report = build_dry_run_insert(archive, work_id=work_id, surface=surface)
        else:
            scope = _FAMILY_TO_DRY_RUN_SCOPE[family]
            report = build_dry_run_repeal(
                archive, work_id=work_id, preflight=preflight, scope=scope
            )
        dry_run_reports.append(report)
        _partition_dry_run_outcomes(
            report, family, amendment_date_by_op_id, verified_by_date, blocked_by_date
        )

    transitions: list[NZActualReplayedTransition] = []
    refusals: list[NZActualReplayRefusal] = []
    parsed_cache: dict[str, NZSourceDocument | None] = {}
    amending_root_cache: dict[str, Any] = {}

    for amendment_date_iso in sorted(set(verified_by_date) | set(blocked_by_date)):
        window_refusals = blocked_by_date.get(amendment_date_iso, [])
        verified_ops = verified_by_date.get(amendment_date_iso, [])
        if window_refusals:
            # FAIL CLOSED: any blocked op in this window blocks the whole declared
            # transition. Nothing is materialized for it; the refusals are carried
            # out verbatim (each with its distinct named rule id), and the
            # verified ops in the same window are surfaced as part of the blocked
            # transition rather than partially replayed.
            refusals.extend(window_refusals)
            if verified_ops:
                refusals.append(
                    NZActualReplayRefusal(
                        rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
                        message=(
                            "actual replay refused for the whole transition because a "
                            "sibling op in the same change window was not dry-run-verified; "
                            "a declared transition is never partially materialized"
                        ),
                        amendment_date_iso=amendment_date_iso,
                        op_ids=tuple(op.proof.op_id for op in verified_ops),
                        detail={
                            "verified_ops": len(verified_ops),
                            "blocked_ops": len(window_refusals),
                        },
                    )
                )
            continue
        if not verified_ops:
            continue
        outcome = _replay_one_transition(
            archive,
            work_id=work_id,
            amendment_date_iso=amendment_date_iso,
            verified_ops=tuple(verified_ops),
            parsed_cache=parsed_cache,
            amending_root_cache=amending_root_cache,
        )
        if isinstance(outcome, NZActualReplayRefusal):
            refusals.append(outcome)
        else:
            transitions.append(outcome)

    return NZActualReplayReport(
        work_id=work_id,
        families=requested,
        transitions=tuple(transitions),
        refusals=tuple(refusals),
        dry_run_reports=tuple(dry_run_reports),
        families_not_attempted=tuple(surface_refusals),
    )


@dataclass(frozen=True)
class _VerifiedOp:
    family: str
    proof: NZMutationBoundaryProof


def _partition_dry_run_outcomes(
    report: NZDryRunReport,
    family: str,
    amendment_date_by_op_id: dict[str, str],
    verified_by_date: dict[str, list[_VerifiedOp]],
    blocked_by_date: dict[str, list[NZActualReplayRefusal]],
) -> None:
    """Split a dry-run report into verified ops and fail-closed refusals.

    A proof is ``dry_run_verified`` only when it agreed with the oracle AND its
    mutation preserved its neighbours. Any other proof, and every dry-run
    refusal, becomes a distinct named actual-replay refusal so nothing is
    silently dropped.
    """

    # Every per-op dry-run refusal is carried forward as a blocked declared op.
    # Family-level refusals (no candidates / preflight not ready) are NOT per-op
    # transition blocks — they mean the family had nothing to declare — so they
    # never create a phantom blocked transition. The family simply contributes no
    # ops; if no other family contributes either, the work has zero transitions.
    for refusal in report.refusals:
        if refusal.rule_id in _FAMILY_LEVEL_DRY_RUN_REFUSALS:
            continue
        date = refusal.amendment_date_iso or amendment_date_by_op_id.get(refusal.op_id, "")
        blocked_by_date.setdefault(date, []).append(
            NZActualReplayRefusal(
                rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
                message=(
                    "actual replay refused because the op was refused by the dry-run "
                    f"surface (dry_run_rule_id={refusal.rule_id})"
                ),
                amendment_date_iso=refusal.amendment_date_iso,
                op_ids=(refusal.op_id,),
                detail={
                    "family": family,
                    "dry_run_refusal_rule_id": refusal.rule_id,
                    "target_address": refusal.target_address,
                },
            )
        )

    for proof in report.proofs:
        date = amendment_date_by_op_id.get(proof.op_id, "")
        if proof.oracle_match != "agrees":
            actual_replay_refusal_detail: dict[str, Any] = {
                "family": family,
                "oracle_match": proof.oracle_match,
                "oracle_match_rule_id": proof.oracle_match_rule_id,
                "target_address": proof.target_address,
            }
            # Propagate the dry-run's target-level divergence classification
            # (AGENTS §0 — every residual resolves to deterministic-gap /
            # manual-compilation-frontier / oracle-suspect). The dry-run proof
            # already classified the divergence; carrying it forward to the
            # actual-replay refusal receipt keeps the source-truth-bucket signal
            # visible at the promotion plane rather than forcing a downstream
            # human to re-derive it from the dry-run plane. Strict-superset
            # additive: no rule_id change, no fail-closed behaviour change.
            if proof.divergence_class is not None:
                actual_replay_refusal_detail["divergence_class"] = proof.divergence_class
            if proof.divergence_sub_families:
                actual_replay_refusal_detail["divergence_sub_families"] = list(
                    proof.divergence_sub_families
                )
            blocked_by_date.setdefault(date, []).append(
                NZActualReplayRefusal(
                    rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
                    message=(
                        "actual replay refused because the dry-run proof did not agree "
                        f"with the on-or-after oracle (oracle_match={proof.oracle_match})"
                    ),
                    amendment_date_iso=date,
                    op_ids=(proof.op_id,),
                    detail=actual_replay_refusal_detail,
                )
            )
            continue
        if not proof.neighbors_unchanged:
            blocked_by_date.setdefault(date, []).append(
                NZActualReplayRefusal(
                    rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
                    message=(
                        "actual replay refused because the dry-run mutation perturbed a "
                        "neighbour or the parent node (mutation boundary not clean)"
                    ),
                    amendment_date_iso=date,
                    op_ids=(proof.op_id,),
                    detail={
                        "family": family,
                        "target_address": proof.target_address,
                    },
                )
            )
            continue
        verified_by_date.setdefault(date, []).append(_VerifiedOp(family=family, proof=proof))


def _replay_one_transition(
    archive: Any,
    *,
    work_id: str,
    amendment_date_iso: str,
    verified_ops: tuple[_VerifiedOp, ...],
    parsed_cache: dict[str, NZSourceDocument | None],
    amending_root_cache: dict[str, Any],
) -> NZActualReplayedTransition | NZActualReplayRefusal:
    op_ids = tuple(op.proof.op_id for op in verified_ops)
    change_window = archived_xml_version_change_window(
        archive, work_id=work_id, version_date=amendment_date_iso
    )
    if change_window.before is None or change_window.on_or_after is None:
        return NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
            message=(
                "actual replay refused because the before/after archived XML version "
                "window is missing for the transition"
            ),
            amendment_date_iso=amendment_date_iso,
            op_ids=op_ids,
        )
    before_doc = _parse_archived_version(archive, change_window.before, parsed_cache)
    if before_doc is None:
        return NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
            message="actual replay refused because the before-version XML is unreadable",
            amendment_date_iso=amendment_date_iso,
            op_ids=op_ids,
            detail={"before_version_id": change_window.before.version_id},
        )
    oracle_doc = _parse_archived_version(archive, change_window.on_or_after, parsed_cache)
    if oracle_doc is None:
        return NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
            message="actual replay refused because the on-or-after-version XML is unreadable",
            amendment_date_iso=amendment_date_iso,
            op_ids=op_ids,
            detail={"on_or_after_version_id": change_window.on_or_after.version_id},
        )

    # Materialize: apply each verified op's recorded mutation to the archived
    # before document. Each mutation is the boring kernel the dry-run proof was
    # produced by (tombstone for repeal, single-occurrence substitution for
    # text_replace, subtree swap for replace, sibling add for insert), re-applied
    # from the proof's own recorded intent so the materialized op is exactly the
    # verified one. A structural payload that is no longer re-extractable fails
    # closed for the whole transition with a distinct named diagnostic.
    try:
        materialized_after, mutations = _materialize_verified_after_document(
            before_doc, verified_ops, archive, amending_root_cache
        )
    except NZStructuralMaterializationError as exc:
        return NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_STRUCTURAL_MATERIALIZATION_FAILED_RULE_ID,
            message=(
                "actual replay refused because a dry-run-verified structural op could "
                f"not be re-materialized into an after-tree ({exc})"
            ),
            amendment_date_iso=amendment_date_iso,
            op_ids=op_ids,
        )

    # Re-confirm the materialized target slice against the archived oracle using
    # the SAME family-specific agreement notion the dry-run verified each op
    # under (repeal: tombstone direction, not byte text — NZ consolidations erase
    # the repealed body text, which the boring kernel deliberately does not;
    # text_replace: the substitution reflected in the oracle node; replace: the
    # normalized oracle subtree matches the replacement subtree; insert: the new
    # node is present in the oracle with matching content at the derived position).
    # This re-runs the check after compositing all ops in the transition; the slice
    # is the mutated nodes only and every one must still agree.
    proof_by_path = {op.proof.selected_source_path: op for op in verified_ops}
    slice_agreements = 0
    diverging: list[tuple[str, ...]] = []
    diverging_match: dict[tuple[str, ...], str] = {}
    for mutation in mutations:
        op = proof_by_path[mutation.target_source_path]
        match = _reconfirm_slice_agreement(
            materialized_after, oracle_doc, op.proof, archive, amending_root_cache
        )
        if match == "agrees":
            slice_agreements += 1
        else:
            diverging.append(mutation.target_source_path)
            diverging_match[mutation.target_source_path] = match
    if diverging:
        # Should never happen for verified ops; if it does, fail closed loudly
        # rather than emit an unsound actual replay.
        return NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
            message=(
                "actual replay refused because a materialized target node diverged from "
                "the archived oracle after compositing the transition, despite the op "
                "being dry-run-verified in isolation"
            ),
            amendment_date_iso=amendment_date_iso,
            op_ids=op_ids,
            detail={
                "diverging_paths": ["/".join(path) for path in sorted(diverging)],
                "oracle_match": {
                    "/".join(path): diverging_match.get(path, "unknown")
                    for path in sorted(diverging)
                },
            },
        )

    return NZActualReplayedTransition(
        amendment_date_iso=amendment_date_iso,
        before_version_id=change_window.before.version_id,
        before_xml_locator=before_doc.xml_locator,
        oracle_version_id=change_window.on_or_after.version_id,
        oracle_xml_locator=oracle_doc.xml_locator,
        mutations=mutations,
        materialized_node_count=len(materialized_after.nodes),
        oracle_node_count=len(oracle_doc.nodes),
        target_slice_node_count=len(mutations),
        target_slice_agreements=slice_agreements,
        materialized_after=materialized_after,
    )


def _materialize_verified_after_document(
    before_doc: NZSourceDocument,
    verified_ops: tuple[_VerifiedOp, ...],
    archive: Any,
    amending_root_cache: dict[str, Any],
) -> tuple[NZSourceDocument, tuple[NZActualReplayMutation, ...]]:
    """Apply every verified op's mutation to the before document.

    The result is ``before_doc`` with each verified op's recorded mutation
    applied. For the leaf-local families (repeal / text_replace) the target node
    is swapped in place; for the structural families (replace / insert) the
    kernel's own splice helpers swap the whole target subtree (replace) or add the
    new node + its descendants next to the verified anchor (insert). None of the
    apply semantics are re-implemented here — they are delegated to
    ``_tombstone_node`` / ``_substitute_node_text`` and the additive
    ``apply_structural_replace_to_nodes`` / ``apply_structural_insert_to_nodes``
    helpers from the dry-run kernel module, so the materialized mutation is
    exactly the verified one.

    The ops in one transition are applied SEQUENTIALLY to an evolving document so
    that any path shift an earlier structural op introduces is reflected before a
    later op resolves its target/anchor. Each op's mutation record is keyed by the
    proof's ``selected_source_path`` (the resolved live-body path for repeal /
    text_replace / replace, the resolved new-node path for insert), which is the
    key the slice re-confirmation looks the proof up under.
    """

    current = before_doc
    mutations: list[NZActualReplayMutation] = []
    for op in verified_ops:
        proof = op.proof
        action = proof.action
        if action in (str(StructuralAction.REPEAL), str(StructuralAction.TEXT_REPLACE)):
            current, mutation = _apply_leaf_local_mutation(current, op)
        elif action == str(StructuralAction.REPLACE):
            after_nodes, after_root = apply_structural_replace_to_nodes(
                current, proof, archive, amending_root_cache
            )
            before_target = _first_node_at_path(current, proof.selected_source_path)
            current = _with_nodes(current, after_nodes)
            mutation = NZActualReplayMutation(
                op_id=proof.op_id,
                action=action,
                family=op.family,
                target_address=proof.target_address,
                target_source_path=proof.selected_source_path,
                target_digest_before=_node_digest(before_target) if before_target else "",
                target_digest_after=_node_digest(after_root),
                dry_run_oracle_match_rule_id=proof.oracle_match_rule_id,
            )
        elif action == str(StructuralAction.INSERT):
            after_nodes, after_new_node = apply_structural_insert_to_nodes(
                current, proof, archive, amending_root_cache
            )
            current = _with_nodes(current, after_nodes)
            mutation = NZActualReplayMutation(
                op_id=proof.op_id,
                action=action,
                family=op.family,
                target_address=proof.target_address,
                target_source_path=proof.selected_source_path,
                target_digest_before="",  # the new node did not exist before
                target_digest_after=_node_digest(after_new_node),
                dry_run_oracle_match_rule_id=proof.oracle_match_rule_id,
            )
        else:  # defence in depth: only promotable families reach here
            raise NZStructuralMaterializationError(
                f"actual replay has no materialization kernel for action {action!r}"
            )
        mutations.append(mutation)
    return current, tuple(mutations)


def _apply_leaf_local_mutation(
    document: NZSourceDocument,
    op: _VerifiedOp,
) -> tuple[NZSourceDocument, NZActualReplayMutation]:
    """Swap the verified op's target node in place (repeal / text_replace)."""

    proof = op.proof
    new_nodes: list[NZSourceNode] = []
    before_node: NZSourceNode | None = None
    after_node: NZSourceNode | None = None
    for node in document.nodes:
        if node.path == proof.selected_source_path:
            before_node = node
            after_node = _apply_verified_mutation(node, proof)
            new_nodes.append(after_node)
            continue
        new_nodes.append(node)
    if before_node is None or after_node is None:
        raise NZStructuralMaterializationError(
            "verified leaf-local target is no longer present in the before tree"
        )
    mutation = NZActualReplayMutation(
        op_id=proof.op_id,
        action=proof.action,
        family=op.family,
        target_address=proof.target_address,
        target_source_path=proof.selected_source_path,
        target_digest_before=_node_digest(before_node),
        target_digest_after=_node_digest(after_node),
        dry_run_oracle_match_rule_id=proof.oracle_match_rule_id,
    )
    return _with_nodes(document, tuple(new_nodes)), mutation


def _first_node_at_path(
    document: NZSourceDocument, path: tuple[str, ...]
) -> NZSourceNode | None:
    matches = _resolve_target_nodes(document, path)
    return matches[0] if matches else None


def _with_nodes(
    document: NZSourceDocument, nodes: tuple[NZSourceNode, ...]
) -> NZSourceDocument:
    return NZSourceDocument(
        xml_locator=document.xml_locator,
        version_id=document.version_id,
        metadata=document.metadata,
        nodes=nodes,
        document_history=document.document_history,
    )


def _reconfirm_slice_agreement(
    materialized_after: NZSourceDocument,
    oracle_doc: NZSourceDocument,
    proof: NZMutationBoundaryProof,
    archive: Any,
    amending_root_cache: dict[str, Any],
) -> str:
    """Re-run the verified family's oracle partition on the composited result.

    Returns ``"agrees"`` when the materialized target node still agrees with the
    archived on-or-after oracle under the same notion the dry-run used, or the
    divergent ``oracle_match`` token otherwise. Delegates to the dry-run's own
    partition functions; the agreement notion is never re-implemented here.
    """

    source_path = proof.selected_source_path
    if proof.action == str(StructuralAction.REPEAL):
        match, _rule_id, _present, _occ = _oracle_partition(
            oracle_doc, source_path, target_kind=_leaf_source_kind(source_path)
        )
        return match
    if proof.action == str(StructuralAction.TEXT_REPLACE):
        after_matches = _resolve_target_nodes(materialized_after, source_path)
        after_old_occ = (
            normalized_inline_occurrence_count(after_matches[0].text, proof.text_old_text)
            if after_matches
            else 0
        )
        match, _rule_id, _present, _occ, _ora_old, _ora_new = _oracle_partition_text(
            oracle_doc,
            source_path,
            old_text=proof.text_old_text,
            new_text=proof.text_new_text,
            after_old_occurrences=after_old_occ,
        )
        return match
    if proof.action == str(StructuralAction.REPLACE):
        # Re-extract the SAME replacement subtree the proof was verified from and
        # re-run the dry-run's own replace oracle partition at the resolved path.
        after_target = _first_node_at_path(materialized_after, source_path)
        target_kind = after_target.kind if after_target else _leaf_source_kind(source_path)
        replacement = _reextract_structural_replacement_for_proof(
            archive, proof, _kind_carrier(target_kind), amending_root_cache
        )
        match, _rule_id, _present, _occ, _digest = _oracle_partition_replace(
            oracle_doc,
            source_path,
            candidate_root=replacement.root,
            candidate_descendants=replacement.descendants,
        )
        return match
    if proof.action == str(StructuralAction.INSERT):
        # Re-extract the SAME new-node subtree and re-run the insert oracle
        # partition at the new node's resolved path, with the derived anchor +
        # direction the proof recorded (the position arbiter).
        payload = _reextract_structural_insertion_for_proof(
            archive, proof, amending_root_cache
        )
        anchor_label = _label_of_source_segment(proof.insert_anchor_source_path)
        anchor_kind = _kind_of_source_segment(proof.insert_anchor_source_path)
        match, _rule_id, _present, _occ, _digest = _oracle_partition_insert(
            oracle_doc,
            source_path,
            candidate_root=payload.root,
            candidate_descendants=payload.descendants,
            derived_anchor_label=anchor_label,
            derived_anchor_kind=anchor_kind,
            derived_direction=proof.insert_direction,
            co_inserted_block_labels=proof.insert_co_inserted_block_labels,
        )
        return match
    raise ValueError(
        f"actual replay cannot re-confirm slice agreement for action {proof.action!r}"
    )


def _kind_carrier(kind: str) -> NZSourceNode:
    """A throwaway node carrying only ``kind`` for replace root-kind alignment.

    ``_reextract_structural_replacement_for_proof`` aligns the extracted root kind
    to the live-body target kind; it only reads ``.kind`` off the passed target.
    """

    return NZSourceNode(
        kind=kind,
        path=(),
        xml_id="",
        xml_path="",
        source_zone="",
        label="",
        heading="",
        deletion_status="",
        text="",
        history=(),
    )


def _label_of_source_segment(source_path: tuple[str, ...]) -> str:
    if not source_path:
        return ""
    leaf = source_path[-1]
    return leaf.split(":", 1)[1] if ":" in leaf else ""


def _kind_of_source_segment(source_path: tuple[str, ...]) -> str:
    if not source_path:
        return ""
    return source_path[-1].split(":", 1)[0]


def _apply_verified_mutation(node: NZSourceNode, proof: NZMutationBoundaryProof) -> NZSourceNode:
    if proof.action == str(StructuralAction.REPEAL):
        return _tombstone_node(node)
    if proof.action == str(StructuralAction.TEXT_REPLACE):
        # Each-place substitutions replace every occurrence (-1); single-occurrence
        # substitutions replace only the leading occurrence (1). The dry-run proof
        # recorded which mode it verified.
        return _substitute_node_text(
            node,
            proof.text_old_text,
            proof.text_new_text,
            count=-1 if proof.text_each_place else 1,
        )
    # Defence in depth: only the four promotable families reach here (others are
    # refused before they can be verified). Fail loud rather than guess a kernel.
    raise ValueError(
        f"actual replay has no materialization kernel for action {proof.action!r}; "
        "only repeal, text_replace, replace, and insert are promotable"
    )


def _validated_families(families: tuple[str, ...]) -> tuple[str, ...]:
    if not families:
        return NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES
    ordered: list[str] = []
    for family in families:
        if family not in _FAMILY_TO_DRY_RUN_SCOPE:
            raise ValueError(
                f"family {family!r} is not promotable to actual replay; "
                f"expected a subset of {tuple(_FAMILY_TO_DRY_RUN_SCOPE)}"
            )
        if family not in ordered:
            ordered.append(family)
    return tuple(ordered)


def _index_amendment_dates(preflight: NZEffectCandidatePreflightReport) -> dict[str, str]:
    """Map each candidate op_id to its declared ISO amendment date.

    Read straight from the preflight rows the dry-run consumed, so the date a
    proof is grouped under is exactly the date the candidate declared. Rows
    without an operation or without a date are skipped (they cannot be
    dry-run-verified, so they are never looked up as verified).
    """

    index: dict[str, str] = {}
    for row in preflight.candidate_report.rows:
        operation = row.operation
        if operation is None:
            continue
        if not row.amendment_date_iso:
            continue
        index[operation.op_id] = row.amendment_date_iso
    return index


def _index_structural_amendment_dates(surface: Any, work_id: str) -> dict[str, str]:
    """Map each structural (replace/insert) proof op_id to its ISO amendment date.

    The structural dry-run kernels mint op ids as ``nz:{work_id}:{row_id}:replace``
    and ``nz:{work_id}:{row_id}:insert`` from the operation-surface witness rows.
    We reconstruct those op ids from the same surface rows so an agreeing
    structural proof is grouped into the exact transition (= amendment date) the
    witness declared, mirroring the preflight index for the leaf-local families.
    Rows without an ISO date are skipped (they cannot be dry-run-verified).
    """

    index: dict[str, str] = {}
    for row in getattr(surface, "rows", ()):  # NZOperationSurfaceReport rows
        date = getattr(row, "amendment_date_iso", "")
        if not date:
            continue
        row_id = getattr(row, "row_id", "")
        if not row_id:
            continue
        index[f"nz:{work_id}:{row_id}:replace"] = date
        index[f"nz:{work_id}:{row_id}:insert"] = date
    return index


def _node_digest(node: NZSourceNode) -> str:
    from lawvm.new_zealand.dry_run import _node_digest as _dr_node_digest

    return _dr_node_digest(node)


# Map a refusal's core family to an AgreementResidual status. A source-honest
# non-executable frontier is a ``frontier`` row; a genuine replay bug is a
# ``residual``; a missing temporal window or unreadable footing is ``blocked``.
_REFUSAL_FAMILY_STATUS: dict[str, AgreementResidualStatus] = {
    "accepted_non_executable_frontier": "frontier",
    "replay_bug": "residual",
    "temporal_mismatch": "blocked",
    "source_footing_gap": "blocked",
}


def _refusal_residual(
    work_id: str,
    index: int,
    refusal: NZActualReplayRefusal,
    classify: Any,
) -> AgreementResidual:
    """Type one fail-closed refusal into a core agreement-residual row."""

    dry_run_refusal_rule_id = str(refusal.detail.get("dry_run_refusal_rule_id", "") or "")
    family = classify(
        refusal_rule_id=refusal.rule_id,
        dry_run_refusal_rule_id=dry_run_refusal_rule_id,
    )
    residual_status: AgreementResidualStatus = _REFUSAL_FAMILY_STATUS.get(family, "blocked")
    op_tag = refusal.op_ids[0] if refusal.op_ids else f"transition_{index}"
    return AgreementResidual(
        residual_id=f"{work_id}:{op_tag}:{refusal.rule_id}:{index}",
        jurisdiction="nz",
        agreement_surface="nz_actual_replay",
        family=family,
        agreement_residual_status=residual_status,
        owner_phase="actual_replay",
        rule_id=refusal.rule_id,
        source_artifact_id=op_tag,
        replay_count=0,
        oracle_count=0,
        missing_proofs=(dry_run_refusal_rule_id,) if dry_run_refusal_rule_id else (),
        safe_default="materialize_only_dry_run_verified_ops_fail_closed_otherwise",
        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
        detail={
            "amendment_date_iso": refusal.amendment_date_iso,
            "op_ids": list(refusal.op_ids),
            "dry_run_refusal_rule_id": dry_run_refusal_rule_id,
            "message": refusal.message,
            **{key: value for key, value in refusal.detail.items() if key != "dry_run_refusal_rule_id"},
        },
    )


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main(args: Any) -> None:
    import json

    families = _families_from_arg(getattr(args, "families", "all"))
    report = build_archived_work_actual_replay(Path(args.db), args.work_id, families=families)
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
        f"work_id={summary['work_id']} families={','.join(summary['families'])} "
        f"transitions_replayed={summary['transitions_replayed']} "
        f"transitions_refused={summary['transitions_refused']} "
        f"ops_replayed={summary['ops_replayed']} "
        f"target_slice_agreements={summary['target_slice_agreements']}/"
        f"{summary['target_slice_nodes']} "
        f"all_slices_agree={summary['all_slices_agree']} "
        f"replay_claims={summary['replay_claims']}"
    )
    if summary["refusal_rule_counts"]:
        print(f"refusal_rule_counts={summary['refusal_rule_counts']}")
    if args.summary_only:
        return
    for transition in report.transitions:
        print(
            f"REPLAYED\t{transition.amendment_date_iso}\t"
            f"before={transition.before_version_id}\toracle={transition.oracle_version_id}\t"
            f"ops={len(transition.mutations)}\t"
            f"slice={transition.target_slice_agreements}/{transition.target_slice_node_count}\t"
            f"slice_agrees={transition.target_slice_agrees}"
        )
    for refusal in report.refusals:
        print(
            f"REFUSED\t{refusal.amendment_date_iso or '-'}\t{refusal.rule_id}\t"
            f"ops={','.join(refusal.op_ids) or '-'}"
        )


def _families_from_arg(value: str | None) -> tuple[str, ...]:
    if not value or value == "all":
        return NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES
    return tuple(part.strip() for part in value.split(",") if part.strip())
