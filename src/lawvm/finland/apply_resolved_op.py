"""Typed resolved-op apply boundary with replay evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import (
    MutationAccountingResult,
    observed_vs_declared_cross_check,
)
from lawvm.core.mutation_boundary import diff_ir_paths_identity_pruned
from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE,
    verify_per_op,
)
from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    validate_transition,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.observed_write_audit import ObservedWriteAudit, build_observed_write_audit
from lawvm.core.phase_result import Finding
from lawvm.core.stage_result import (
    AuthoritySurface,
    CoverageCertificate,
    EMPTY_EVIDENCE,
    EvidenceBundle,
    Residual,
    StageResult,
)
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.tree_ops import receipt_from_diff
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_op_closure_sweeps import (
    gate_unknown_attestation_policy,
    run_per_op_closure_sweeps,
)
from lawvm.finland.apply_replay_authorization import (
    WRITE_RECEIPT_VIOLATION_FINDING_CODE,
    mint_apply_replay_authority,
    op_replay_authorized,
)
from lawvm.finland.apply import apply_op
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.apply_policy import _OP_TYPE_TO_ACTION, _section_occupancy
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp, ResolvedOp
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)

# Stage-0 passive observed-vs-declared tree cross-check. Defaults ON: after each
# applied op the replay fold computes an identity-pruned structural diff of the
# before/after IR and verifies every observed changed path is explained by the
# op's declared mutation-event paths. It only records findings; it never alters
# replay behavior, so it is safe to leave enabled.
OBSERVED_MUTATION_CROSS_CHECK_ENABLED = True

ApplyDisposition = Literal["APPLIED", "APPLY_FAILED", "NO_APPLY_PASS"]
APPLY_RESOLVED_OP_AUDIT_KIND = "APPLY.RESOLVED_OP_AUDIT"
FI_APPLY_RESOLVED_OP_RULE_ID = "fi.apply.resolved_op"


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpAudit:
    """One visible audit record for a resolved op considered by apply."""

    source_statute: str
    source_effective: str
    source_expires: str
    op_id: str
    action_type: str
    description: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str
    target_part: str
    target_paragraph: str
    target_item: str
    target_special: str
    disposition: ApplyDisposition

    def to_observation(self) -> dict[str, object]:
        return {
            "kind": APPLY_RESOLVED_OP_AUDIT_KIND,
            "source_statute": self.source_statute,
            "detail": {
                "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
                "source_effective": self.source_effective,
                "source_expires": self.source_expires,
                "op_id": self.op_id,
                "action_type": self.action_type,
                "description": self.description,
                "target_unit_kind": self.target_unit_kind,
                "target_norm": self.target_norm,
                "target_chapter": self.target_chapter,
                "target_part": self.target_part,
                "target_paragraph": self.target_paragraph,
                "target_item": self.target_item,
                "target_special": self.target_special,
                "disposition": self.disposition,
            },
        }


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpRequest:
    """Semantic inputs for one ResolvedOp apply pass."""

    state: ReplayState
    ctx: StatuteContext
    rop: ResolvedOp
    amendment_id: str
    replay_mode: Literal["official_consolidation", "legal_pit"]
    path_hint: tuple[tuple[str, str], ...] | None = None
    standalone_section_targets: frozenset[StandaloneSectionTarget] = frozenset()
    migration_ledger: Optional[MigrationLedger] = None
    strict_profile: Optional[StrictProfile] = None
    error_prefix: str = ""
    force_apply_pass: bool = False


FI_APPLY_OP_WRITE_HELPER = "fi.apply.resolved_op_write"
# Strict-mode blocking code for a landed write whose conservation receipt does
# not cover the observed mutation footprint (ObservedWriteAudit.audit_status ==
# "violation") or whose bound→landed divergence is unexplained. This reuses the
# registered apply-boundary touch-outside-target violation code (coordinated
# with the mutation-boundary guard-liveness lane) rather than minting a new one.
# Imported from apply_replay_authorization (above) so the producer and the
# authority consumer share one literal and cannot silently diverge.


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpSinks:
    """Mutable evidence/artifact channels for one ResolvedOp apply pass.

    ``write_receipts_out`` and ``write_audits_out`` are NON-Optional: every
    apply pass collects a conservation receipt + observed-write audit for each
    landed write by construction. A caller cannot run the apply path without
    providing the receipt accumulator — this closes the opt-in-None path where
    a production caller could omit the sink and let writes land un-audited.
    """

    write_receipts_out: list[WriteReceipt] = field(default_factory=list)
    write_audits_out: list[ObservedWriteAudit] = field(default_factory=list)
    lo_ops_out: Optional[list[LegalOperation]] = None
    failed_ops_out: Optional[list[FailedOp]] = None
    source_pathologies_out: Optional[list[SourcePathology]] = None
    mutation_events_out: Optional[list[ApplyMutationEvent]] = None
    findings_out: Optional[list[Finding]] = None
    observed_touch_results_out: Optional[list[MutationAccountingResult]] = None


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpResult:
    """Result of one ResolvedOp apply pass."""

    state: ReplayState
    disposition: ApplyDisposition
    audit: ApplyResolvedOpAudit


def apply_resolved_op_with_audit(
    request: ApplyResolvedOpRequest,
    sinks: ApplyResolvedOpSinks,
) -> ApplyResolvedOpResult:
    """Apply one ResolvedOp and cross-check declared mutation evidence."""
    state = request.state
    rop = request.rop
    disposition: ApplyDisposition = "NO_APPLY_PASS"
    if request.force_apply_pass or rop.replay_requires_apply_pass:
        state, disposition = _apply_required_resolved_op(request, sinks)
    return ApplyResolvedOpResult(
        state=state,
        disposition=disposition,
        audit=_audit_for_rop(request, disposition),
    )


def _apply_required_resolved_op(
    request: ApplyResolvedOpRequest,
    sinks: ApplyResolvedOpSinks,
) -> tuple[ReplayState, ApplyDisposition]:
    state = request.state
    rop = request.rop
    disposition: ApplyDisposition = "APPLIED"
    try:
        prev_state = state
        event_cursor = (
            len(sinks.mutation_events_out)
            if sinks.mutation_events_out is not None
            else 0
        )
        failed_cursor = (
            len(sinks.failed_ops_out)
            if sinks.failed_ops_out is not None
            else 0
        )
        state = apply_op(
            state,
            None,
            request.ctx,
            None,
            replay_mode=request.replay_mode,
            failed_ops_out=sinks.failed_ops_out,
            source_pathologies_out=sinks.source_pathologies_out,
            mutation_events_out=sinks.mutation_events_out,
            findings_out=sinks.findings_out,
            path_hint=request.path_hint,
            rop=rop,
            replay_history_ops=sinks.lo_ops_out,
            standalone_section_targets=request.standalone_section_targets,
            migration_ledger=request.migration_ledger,
            strict_profile=request.strict_profile,
            write_audits_out=sinks.write_audits_out,
        )
        if (
            sinks.failed_ops_out is not None
            and len(sinks.failed_ops_out) > failed_cursor
        ):
            disposition = "APPLY_FAILED"
            # Disposition firewall: a soft-failed op (APPLY_FAILED) is NOT an
            # authorized apply, yet it may still have landed a tree mutation
            # before failing. The production ``aggregate_replay_authority`` never
            # inspects disposition, so without this a non-APPLIED op that mutated
            # the tree would silently authorize the replay. Surface the landed
            # write as a blocking boundary-violation finding so the aggregate's
            # ``no_boundary_violation`` conjunct trips: a write that landed under a
            # non-authorized disposition is, by the op gate predicate
            # (``op_replay_authorized`` requires ``disposition == "APPLIED"``), an
            # un-authorized write.
            if (
                sinks.findings_out is not None
                and prev_state.ir is not state.ir
            ):
                sinks.findings_out.append(
                    Finding(
                        kind=WRITE_RECEIPT_VIOLATION_FINDING_CODE,
                        role="violation",
                        stage="apply",
                        blocking=True,
                        source_statute=request.amendment_id,
                        detail={
                            "message": (
                                "A soft-failed (APPLY_FAILED) op landed a tree "
                                "mutation; a write under a non-authorized "
                                "disposition cannot authorize the replay."
                            ),
                            "barrier_code": WRITE_RECEIPT_VIOLATION_FINDING_CODE,
                            "op_id": rop.op_id or "",
                            "disposition": disposition,
                        },
                    )
                )
        undeclared_touch: Optional[MutationAccountingResult] = None
        if sinks.mutation_events_out is not None:
            undeclared_touch = cross_check_observed_vs_declared(
                prev_state,
                state,
                rop.op_id or "",
                sinks.mutation_events_out[event_cursor:],
                sinks.observed_touch_results_out,
            )
        # Conservation receipt for this op's landed write, computed by
        # construction from the actual before/after IR diff (landed reality),
        # never from the nominal target. An op that mutated the tree yields
        # exactly one receipt + observed-write audit; the waist totality
        # assertion (inside) checks |receipts| == |landed writes|. The strict
        # path promotes the passive observed-vs-declared undeclared-touch signal
        # (a genuine production violation) into a blocking finding.
        _collect_op_write_receipt(
            prev_state,
            state,
            rop=rop,
            strict_profile=request.strict_profile,
            source_statute=request.amendment_id,
            sinks=sinks,
            undeclared_touch=undeclared_touch,
        )
        # Per-op apply-authority gates (LS-01 / LS-03 / EV-05+FW-01): run after
        # the write landed so the changed-path subset, the occupancy transition,
        # and the execution-authorization closure are all checked PER OP at the
        # production apply site.
        _enforce_per_op_apply_authority(
            prev_state,
            state,
            rop=rop,
            strict_profile=request.strict_profile,
            source_statute=request.amendment_id,
            findings_out=sinks.findings_out,
            migration_ledger=request.migration_ledger,
        )
    except (NameError, TypeError, AttributeError):
        raise
    except WriteReceiptTotalityError:
        # Waist-level conservation totality is a blocking invariant breach, not a
        # per-op apply failure: one landed write must yield exactly one receipt +
        # audit. Re-raise so it propagates as a hard failure instead of being
        # muted into a generic, catchable APPLY_FAILED with console-only logging.
        raise
    except Exception as e:
        disposition = "APPLY_FAILED"
        prefix = request.error_prefix
        if prefix:
            logger.debug(
                "  [%s] %s %s -> ERROR",
                request.amendment_id,
                prefix,
                rop.description(),
                exc_info=True,
            )
            _replay_print(
                f"  [{request.amendment_id}] {prefix} {rop.description()} -> ERROR: {e}"
            )
        else:
            logger.debug(
                "  [%s] %s -> ERROR",
                request.amendment_id,
                rop.description(),
                exc_info=True,
            )
            _replay_print(f"  [{request.amendment_id}] {rop.description()} -> ERROR: {e}")
    return state, disposition


def cross_check_observed_vs_declared(
    prev_state: ReplayState,
    new_state: ReplayState,
    op_id: str,
    events: list[ApplyMutationEvent],
    observed_touch_results_out: Optional[list[MutationAccountingResult]],
) -> Optional[MutationAccountingResult]:
    """Passively verify the op's observed tree diff against its declared events.

    Returns the undeclared-touch result when the observed diff is not fully
    explained by the op's declared mutation events, else ``None``. The result is
    appended to ``observed_touch_results_out`` when present (the legacy passive
    side-channel) AND returned so strict mode can promote it to blocking.
    """
    if not OBSERVED_MUTATION_CROSS_CHECK_ENABLED:
        return None
    if prev_state.ir is new_state.ir:
        return None
    observed_paths = diff_ir_paths_identity_pruned(prev_state.ir, new_state.ir)
    if not observed_paths:
        return None
    helper = events[-1].helper if events else ""
    result = observed_vs_declared_cross_check(op_id, helper, observed_paths, events)
    if result is not None and observed_touch_results_out is not None:
        observed_touch_results_out.append(result)
    return result


# ---------------------------------------------------------------------------
# Per-op apply-authority gates (audit-registry lane L1: LS-01, LS-03, EV-05/FW-01)
# ---------------------------------------------------------------------------

# The FI replay IR is rooted under an unlabeled hcontainer wrapper; tree-diff
# paths carry this step but op-nominal LegalAddress targets do not. Mirrors
# ``mutation_accounting._WRAPPER_ROOT_STEP``.
_FI_REPLAY_WRAPPER_ROOT_STEP: tuple[tuple[str, str], ...] = (("hcontainer", ""),)

OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE = "APPLY.OCCUPANCY_TRANSITION_BLOCKED"
REPLAY_AUTHORIZATION_PROOF_REQUIRED_FINDING_CODE = "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED"
# Owner phase recorded on the per-op ExecutionAuthorization closure (EV-05/FW-01).
_APPLY_OP_AUTHORIZATION_OWNER_PHASE = "apply"
_APPLY_OP_AUTHORIZATION_REQUIRED_PROOFS: tuple[str, ...] = (
    "execution_authorization_rule_id_resolved",
)
# EV-06: the known/pinned evidence-policy id set the per-op apply-authority gate
# validates a cited policy id against. The apply-path ExecutionAuthorizations
# minted by ``_resolve_op_execution_authorization`` cite NO kernel evidence
# policy, so this set is empty in production and the EV-06 gate is a no-op on the
# corpus (0-delta). It is populated only when the apply path begins consuming
# kernel-projected authorizations that cite a policy id.
_APPLY_OP_KNOWN_ATTESTATION_POLICY_IDS: frozenset[str] = frozenset()


def _enforce_per_op_apply_authority(
    prev_state: ReplayState,
    new_state: ReplayState,
    *,
    rop: ResolvedOp,
    strict_profile: Optional[StrictProfile],
    source_statute: str,
    findings_out: Optional[list[Finding]],
    migration_ledger: Optional[MigrationLedger] = None,
) -> None:
    """Run the per-op apply-authority gates for one landed write.

    Three audit-registry gates fire here, PER OP, at the production apply site:

    * **LS-01** (mutation boundary): the op's observed changed paths must be a
      subset of its declared mutation boundary. Out-of-boundary → strict BLOCKS
      (``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP``), quirks records a non-blocking
      accounting finding (``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``).
    * **LS-03** (occupancy gate-liveness): a state-mutating op whose action maps
      to an occupancy transition must pass the (action, from)->to table. An invalid
      transition BLOCKS under strict (``APPLY.OCCUPANCY_TRANSITION_BLOCKED``) — the
      occupancy gate was previously telemetry-only.
    * **EV-05/FW-01** (execution-authorization closure): every state-mutating op
      must resolve an :class:`ExecutionAuthorization` (a non-empty rule_id +
      required proofs). An op that landed a write with no resolvable authorization
      rule BLOCKS under strict (``EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED``).

    No tree change → no landed write → no state mutation → nothing to gate.
    ``strict_profile is not None`` is the strict-mode signal; ``None`` is the
    permissive (quirks) profile, which records rather than blocks where the audit
    contract says so.
    """
    if prev_state.ir is new_state.ir:
        # No state mutation: these gates only police writes that actually landed.
        return
    if findings_out is None:
        return
    is_strict = strict_profile is not None

    _gate_mutation_boundary_at_op(
        prev_state, new_state, rop=rop, is_strict=is_strict,
        source_statute=source_statute, findings_out=findings_out,
    )
    _gate_occupancy_transition_at_op(
        prev_state, rop=rop, is_strict=is_strict,
        source_statute=source_statute, findings_out=findings_out,
    )
    _gate_execution_authorization_at_op(
        rop=rop, is_strict=is_strict,
        source_statute=source_statute, findings_out=findings_out,
    )
    # Wave-2 apply-authority closure: the per-op totality sweeps (LS-05 scope-
    # confidence, LS-06 verb-conversion, LS-07 granularity-escalation, LS-09
    # payload-smuggling, LS-10 unstated-migration) gate at the SAME landed-write
    # apply site as LS-01/EV-05 above.
    run_per_op_closure_sweeps(
        rop=rop,
        is_strict=is_strict,
        source_statute=source_statute,
        findings_out=findings_out,
        migration_ledger=migration_ledger,
    )


def _gate_mutation_boundary_at_op(
    prev_state: ReplayState,
    new_state: ReplayState,
    *,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-01: per-op mutation-boundary REJECT gate over the typed LegalOperation."""
    # ``rop.op`` is the AmendmentOp wrapper; its ``.lo`` is the typed core
    # LegalOperation (action + target) the verify gate needs.
    amendment_op = rop.op if rop is not None else None
    legal_op = amendment_op.lo if amendment_op is not None else None
    if legal_op is None:
        # No typed core operation available at this granularity — the op-level
        # boundary is carried by the observed-vs-declared cross-check + receipt
        # (already wired); there is no declared LegalOperation target to verify
        # the changed-path subset against here.
        return
    verdict = verify_per_op(
        prev_state.ir,
        new_state.ir,
        legal_op,
        op_id=rop.op_id or "",
        # The FI replay IR is rooted under an unlabeled ("hcontainer", "") wrapper
        # that tree-diff paths carry but op LegalAddress targets do not; strip it
        # so observed and declared surfaces align (same normalization as
        # mutation_accounting). Without it every wrapped diff path is a false escape.
        strip_root_prefix=_FI_REPLAY_WRAPPER_ROOT_STEP,
    )
    if verdict.within_boundary:
        return
    detail = {
        "message": (
            "Per-op mutation boundary escaped: the op's changed tree paths are not "
            "a subset of its declared target/migration/recovery/editorial boundary."
        ),
        "op_id": rop.op_id or "",
        "changed_paths": list(verdict.changed_paths),
        "out_of_boundary_paths": list(verdict.out_of_boundary_paths),
    }
    if is_strict:
        findings_out.append(
            Finding(
                kind=MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE,
                role="violation",
                stage="apply",
                blocking=True,
                source_statute=source_statute,
                detail=detail,
            )
        )
    else:
        findings_out.append(
            Finding(
                kind=MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
                role="observation",
                stage="apply",
                blocking=False,
                source_statute=source_statute,
                detail={**detail, "strict_disposition": "record"},
            )
        )


def _gate_occupancy_transition_at_op(
    prev_state: ReplayState,
    *,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """LS-03: occupancy-transition gate that BLOCKS an invalid transition under strict."""
    action_value = _OP_TYPE_TO_ACTION.get(rop.resolved_action_type or "")
    if action_value is None:
        return
    # Occupancy transitions are defined at the whole-section slot level (the
    # VALID_TRANSITIONS table); descendant (paragraph/item/special) edits do not
    # change slot occupancy and are out of scope, matching _observe_occupancy_transition.
    if (
        not rop.targets_whole_unit("section")
        or rop.effective_target_paragraph is not None
        or rop.effective_target_item_label is not None
        or rop.effective_target_special is not None
    ):
        return
    target_address = rop.resolved_target_address
    if target_address is None or not target_address.path:
        return
    sec_path = tuple((str(kind), str(label)) for kind, label in target_address.path)
    current = _section_occupancy(prev_state, sec_path)
    try:
        validate_transition(OccupancyAction(action_value), current)
    except InvalidOccupancyTransition as exc:
        if not is_strict:
            # Quirks: the legacy observational APPLY.OCCUPANCY_POLICY_VIOLATION
            # lane already records the non-blocking signal; do not double-emit.
            return
        findings_out.append(
            Finding(
                kind=OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE,
                role="violation",
                stage="apply",
                blocking=True,
                source_statute=source_statute,
                detail={
                    "message": (
                        "Strict occupancy gate blocked an invalid (action, occupancy) "
                        "transition for a state-mutating op."
                    ),
                    "op_id": rop.op_id or "",
                    "action": action_value,
                    "current_occupancy": current.value,
                    "transition_error": str(exc),
                },
            )
        )


def _gate_execution_authorization_at_op(
    *,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """EV-05/FW-01: closure that every state-mutating op resolves an ExecutionAuthorization."""
    authorization = _resolve_op_execution_authorization(rop)
    if authorization is not None and authorization.authorization_rule_id:
        # EV-06: a resolved authorization that CITES an evidence policy id must
        # cite a known/pinned policy. The apply-path authorizations cite no kernel
        # policy (0-delta on the corpus); a forged cited unknown policy BLOCKS.
        gate_unknown_attestation_policy(
            authorization=authorization,
            known_policy_ids=_APPLY_OP_KNOWN_ATTESTATION_POLICY_IDS,
            is_strict=is_strict,
            source_statute=source_statute,
            op_id=rop.op_id or "",
            findings_out=findings_out,
        )
        # The op carries/resolves an execution-authorization rule; closure met.
        return
    if not is_strict:
        return
    findings_out.append(
        Finding(
            kind=REPLAY_AUTHORIZATION_PROOF_REQUIRED_FINDING_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                "message": (
                    "A state-mutating op landed without resolving an ExecutionAuthorization "
                    "(no rule_id + required proofs)."
                ),
                "op_id": rop.op_id or "",
                "required_proofs": list(_APPLY_OP_AUTHORIZATION_REQUIRED_PROOFS),
            },
        )
    )


def _resolve_op_execution_authorization(
    rop: ResolvedOp,
) -> Optional[ExecutionAuthorization]:
    """Resolve the per-op :class:`ExecutionAuthorization` for a state-mutating op.

    The authorization rule_id is the op's stable identity (its ``op_id``). An op
    with no op_id cannot be tied to an execution-authorization rule, so the
    closure returns ``None`` and the strict gate emits
    ``EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED``. This is the closure sweep, not a
    full evidence-policy re-architecture: it asserts the authority *carrier* exists
    per op, which is the gap the audit names (apply path had ZERO references).
    """
    rule_id = (rop.op_id or "").strip()
    if not rule_id:
        return None
    return ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id=rule_id,
        owner_phase=_APPLY_OP_AUTHORIZATION_OWNER_PHASE,
        strict_disposition="record",
        safe_default="block_until_apply_op_authorization_rule_is_resolved",
        forbidden_shortcuts=(
            "landed_write_existence_as_execution_authorization",
        ),
    )


class WriteReceiptTotalityError(AssertionError):
    """A landed write did not yield exactly one conservation receipt.

    This is the waist-level totality invariant for the apply receipt seam:
    every op that mutates the legal-state tree must produce exactly one
    WriteReceipt and one ObservedWriteAudit. A mismatch means a write landed
    without (or with a duplicate) conservation account — the single weakest
    conservation point this seam closes.
    """


def _collect_op_write_receipt(
    prev_state: ReplayState,
    new_state: ReplayState,
    *,
    rop: ResolvedOp,
    strict_profile: Optional[StrictProfile],
    source_statute: str,
    sinks: ApplyResolvedOpSinks,
    undeclared_touch: Optional[MutationAccountingResult] = None,
) -> None:
    """Produce + account one landed write's receipt and observed-write audit.

    No tree change → no landed write → no receipt (the op was a no-op apply
    pass). A tree change yields exactly one receipt computed from landed reality
    and exactly one independent audit. The totality assertion enforces that the
    receipt/audit counts move in lockstep with landed writes.

    Boundary blocking is driven by the genuine production signal: the op-level
    observed-vs-declared cross-check (``undeclared_touch``). When that cross-check
    sees a changed tree path the op's declared mutation events do not explain, it
    is promoted to a BLOCKING finding instead of the legacy logger.warning /
    serialize-only passive disposition — REGARDLESS of the caller's strict
    profile, so a permissive or absent profile cannot silently authorize a write
    that landed outside its declared target. The op-level receipt itself is built
    from landed reality and therefore covers its own observed footprint by
    construction, so its audit is clean — the receipt/audit are the conservation
    account; ``undeclared_touch`` is the boundary violation. ``strict_profile`` is
    retained on the signature for the receipt-binding context but no longer gates
    surfacing the boundary finding.
    """
    if prev_state.ir is new_state.ir:
        # No landed write: receipts and audits must not grow for this op.
        return

    receipts_before = len(sinks.write_receipts_out)
    audits_before = len(sinks.write_audits_out)

    # The op-level receipt is built purely from landed reality: no resolver
    # binding is available at this granularity (the binding lives inside
    # apply_op). With no classification hint, receipt_from_diff records every
    # observed changed path as a replaced path, so the declared footprint equals
    # the observed footprint by construction.
    _rop_source = rop.resolved_op_source
    receipt = receipt_from_diff(
        prev_state.ir,
        new_state.ir,
        op_id=rop.op_id or "",
        helper=FI_APPLY_OP_WRITE_HELPER,
        action=str(rop.resolved_action_type or "").lower(),
        bound_target_path=None,
        source_anchor=_rop_source.source_anchor if _rop_source is not None else None,
    )
    audit = build_observed_write_audit(prev_state.ir, new_state.ir, receipt)
    sinks.write_receipts_out.append(receipt)
    sinks.write_audits_out.append(audit)

    # Waist-level totality: one landed write ⇒ exactly one receipt + audit.
    if len(sinks.write_receipts_out) != receipts_before + 1:
        raise WriteReceiptTotalityError(
            f"op {rop.op_id!r} landed a write but produced "
            f"{len(sinks.write_receipts_out) - receipts_before} receipts (expected 1)"
        )
    if len(sinks.write_audits_out) != audits_before + 1:
        raise WriteReceiptTotalityError(
            f"op {rop.op_id!r} landed a write but produced "
            f"{len(sinks.write_audits_out) - audits_before} observed-write audits (expected 1)"
        )

    # The blocking signal is the genuine production undeclared-tree-touch
    # cross-check, not a self-clean receipt: when ``undeclared_touch`` is set the
    # op's landed write touched tree paths its declared mutation events do not
    # explain, which is a real boundary violation regardless of the caller's
    # strict profile. Emitting the finding is DECOUPLED from strict mode — a
    # permissive (or absent) ``strict_profile`` must not silently authorize a
    # write that landed outside its declared target. The downstream gate
    # (``aggregate_replay_authority``'s ``no_boundary_violation`` conjunct) then
    # trips on this blocking finding for ANY caller profile. (Strictness on
    # StrictProfile is the absence of permissive allowances; the firewall does not
    # depend on it here, so a None profile no longer bypasses the arm.)
    if sinks.findings_out is not None and undeclared_touch is not None:
        sinks.findings_out.append(
            Finding(
                kind=WRITE_RECEIPT_VIOLATION_FINDING_CODE,
                role="violation",
                stage="apply",
                blocking=True,
                source_statute=source_statute,
                detail={
                    "message": (
                        "Apply landed a write that touched tree paths its declared "
                        "mutation events do not explain."
                    ),
                    "barrier_code": WRITE_RECEIPT_VIOLATION_FINDING_CODE,
                    "op_id": rop.op_id or "",
                    "helper": undeclared_touch.helper or FI_APPLY_OP_WRITE_HELPER,
                    "observed_write_status": audit.audit_status,
                    "undeclared_touch_code": undeclared_touch.code,
                    "undeclared_paths": [
                        list(path) for path in undeclared_touch.out_of_scope_paths
                    ],
                },
            )
        )


def apply_resolved_op_staged(
    request: ApplyResolvedOpRequest,
    sinks: ApplyResolvedOpSinks,
) -> StageResult[ReplayState]:
    """Apply one ResolvedOp and return the typed apply stage (WAIST #7).

    A thin wrapper over :func:`apply_resolved_op_with_audit` (the value-path,
    kept untouched so existing callers stay 0-delta). It SURFACES the apply
    boundary's account as the canonical :class:`StageResult` and — the CRUX —
    mints a real type-carried :class:`AuthoritySurface` (NOT the neutral default)
    from the SAME facts that already gate whether the write may stand:

      * ``value``    = the mutated :class:`ReplayState`.
      * ``coverage`` = the receipt declared-footprint partition; every declared
        path is ``owned``, ``violation==1`` iff the observed-vs-declared
        cross-check found an undeclared mutation touch.
      * ``residuals`` = the EXISTING #3 structural mutation-boundary residual
        (unexplained bound→landed divergence) + an undeclared-touch residual when
        the cross-check is dirty (REUSED signals, not recomputed semantics).
      * ``findings``  = the apply findings emitted for THIS op (the #3 blocking
        ``Finding`` etc.), projected as the typed tuple.
      * ``evidence``  = a :class:`SourceWitness` from the receipt ``source_anchor``
        quote-hash when present, else empty footing.
      * ``authority`` = the minted apply-replay :class:`AuthoritySurface`:
        ``replay_authorized`` ⟺ ``disposition == "APPLIED"`` AND no blocking
        structural residual AND a clean undeclared-touch cross-check (the exact
        conjunction that lets the write stand today). This is the firewall in the
        type — a permissive strict profile / a bare receipt is named a forbidden
        shortcut, never authority.

    The apply DECLINE verdict is UNCHANGED (it stays on the #3 residual→`Finding`
    channel — ESCALATE-3W); the new load-bearing branch the authority adds is at
    the certificate (the clean-claim gate), not a second apply decline.
    """
    receipts_before = len(sinks.write_receipts_out)
    findings_before = (
        len(sinks.findings_out) if sinks.findings_out is not None else 0
    )

    result = apply_resolved_op_with_audit(request, sinks)

    new_receipts = sinks.write_receipts_out[receipts_before:]
    new_findings = (
        sinks.findings_out[findings_before:]
        if sinks.findings_out is not None
        else []
    )

    # The op-level receipt (if a write landed) is the footprint + boundary
    # account; an undeclared touch surfaces as the apply-boundary violation
    # finding the value-path already emitted.
    receipt = new_receipts[-1] if new_receipts else None
    undeclared_touch_present = any(
        finding.kind == WRITE_RECEIPT_VIOLATION_FINDING_CODE and finding.blocking
        for finding in new_findings
    )

    coverage = _staged_apply_coverage(receipt, undeclared_touch_present)
    residuals = _staged_apply_residuals(receipt, undeclared_touch_present)
    evidence = _staged_apply_evidence(receipt)

    has_blocking_structural_residual = any(item.blocking for item in residuals)
    replay_authorized = op_replay_authorized(
        disposition=result.disposition,
        has_blocking_structural_residual=has_blocking_structural_residual,
        undeclared_touch_present=undeclared_touch_present,
    )
    authority: AuthoritySurface = mint_apply_replay_authority(
        replay_authorized=replay_authorized
    )

    return StageResult(
        value=result.state,
        evidence=evidence,
        residuals=residuals,
        findings=tuple(new_findings),
        coverage=coverage,
        authority=authority,
    )


def _staged_apply_coverage(
    receipt: Optional[WriteReceipt],
    undeclared_touch_present: bool,
) -> CoverageCertificate:
    """The receipt declared-footprint partition (violation iff undeclared touch)."""
    declared = len(receipt.declared_footprint) if receipt is not None else 0
    return CoverageCertificate(
        unit="paths",
        total=declared,
        owned=declared,
        residual=0,
        violation=1 if undeclared_touch_present else 0,
    )


def _staged_apply_residuals(
    receipt: Optional[WriteReceipt],
    undeclared_touch_present: bool,
) -> tuple[Residual, ...]:
    """REUSE the existing #3 structural + undeclared-touch residual signals.

    The op-level receipt carries no resolver binding (``bound_target_path is
    None``); there is no bound→landed divergence to explain, so the structural
    mutation-boundary residual only fires for a receipt that bound a target and
    landed elsewhere with no named rule (the exact #3 condition).
    """
    residuals: list[Residual] = []
    if (
        receipt is not None
        and receipt.bound_target_path is not None
        and not receipt.divergence_explained
    ):
        residuals.append(
            Residual(
                kind="unowned_violation",
                reason="unexplained_mutation_boundary_divergence",
                scope=str(receipt.op_id or ""),
                text="",
                blocking=True,
            )
        )
    if undeclared_touch_present:
        residuals.append(
            Residual(
                kind="unowned_violation",
                reason="undeclared_mutation_touch",
                scope=str(receipt.op_id or "") if receipt is not None else "",
                text="",
                blocking=True,
            )
        )
    return tuple(residuals)


def _staged_apply_evidence(receipt: Optional[WriteReceipt]) -> EvidenceBundle:
    """Project the receipt ``source_anchor`` quote-hash into a SourceWitness."""
    if receipt is None:
        return EMPTY_EVIDENCE
    anchor = receipt.source_anchor
    if anchor is None:
        return EMPTY_EVIDENCE
    algorithm, _, digest = anchor.quote_hash.partition(":")
    return EvidenceBundle(
        (
            SourceWitness(
                source_role="amendment_source_clause",
                artifact_id=anchor.source_artifact_id,
                digest=DigestWitness(digest_algorithm=algorithm, digest=digest),
            ),
        )
    )


def _audit_for_rop(
    request: ApplyResolvedOpRequest,
    disposition: ApplyDisposition,
) -> ApplyResolvedOpAudit:
    group = request.rop.resolved_group_key_view
    source = request.rop.resolved_op_source
    return ApplyResolvedOpAudit(
        source_statute=request.amendment_id,
        source_effective=source.effective if source is not None else "",
        source_expires=source.expires if source is not None else "",
        op_id=request.rop.op_id or "",
        action_type=request.rop.resolved_action_type,
        description=request.rop.description(),
        target_unit_kind=str(group.unit_kind or ""),
        target_norm=str(group.target_norm or ""),
        target_chapter=str(group.target_chapter or ""),
        target_part=str(group.target_part or ""),
        target_paragraph=str(request.rop.effective_target_paragraph)
        if request.rop.effective_target_paragraph is not None
        else "",
        target_item=str(request.rop.effective_target_item_label or "").strip(),
        target_special=str(request.rop.effective_target_special or "").strip(),
        disposition=disposition,
    )
