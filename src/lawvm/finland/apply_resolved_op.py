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
from lawvm.core.observed_write_audit import ObservedWriteAudit, build_observed_write_audit
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import receipt_from_diff
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply import apply_op
from lawvm.finland.apply_events import ApplyMutationEvent
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
    op_id: str
    description: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str
    target_part: str
    disposition: ApplyDisposition

    def to_observation(self) -> dict[str, object]:
        return {
            "kind": APPLY_RESOLVED_OP_AUDIT_KIND,
            "source_statute": self.source_statute,
            "detail": {
                "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
                "op_id": self.op_id,
                "description": self.description,
                "target_unit_kind": self.target_unit_kind,
                "target_norm": self.target_norm,
                "target_chapter": self.target_chapter,
                "target_part": self.target_part,
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
# not cover the observed mutation footprint (ObservedWriteAudit.status ==
# "violation") or whose bound→landed divergence is unexplained. This reuses the
# registered apply-boundary touch-outside-target violation code (coordinated
# with the mutation-boundary guard-liveness lane) rather than minting a new one.
WRITE_RECEIPT_VIOLATION_FINDING_CODE = "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"


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

    Strict-mode blocking is driven by the genuine production signal: the
    op-level observed-vs-declared cross-check (``undeclared_touch``). When that
    cross-check sees a changed tree path the op's declared mutation events do not
    explain, a strict profile promotes it to a BLOCKING finding instead of the
    legacy logger.warning / serialize-only passive disposition. The op-level
    receipt itself is built from landed reality and therefore covers its own
    observed footprint by construction, so its audit is clean — the
    receipt/audit are the conservation account; ``undeclared_touch`` is the
    boundary violation.
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

    # Strict-mode gate: a profile that refuses to guess targets is the profile
    # that must also refuse an un-accounted landed write. (No single boolean
    # "strict" exists on StrictProfile; strictness is the absence of permissive
    # allowances — mirrors the existing `strict_profile is None or allows_X`
    # pattern used across the apply lanes.) The blocking signal is the genuine
    # production undeclared-tree-touch cross-check, not a self-clean receipt.
    strict = strict_profile is not None and not strict_profile.allows_target_guessing
    if strict and sinks.findings_out is not None and undeclared_touch is not None:
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
                    "observed_write_status": audit.status,
                    "undeclared_touch_code": undeclared_touch.code,
                    "undeclared_paths": [
                        list(path) for path in undeclared_touch.out_of_scope_paths
                    ],
                },
            )
        )


def _audit_for_rop(
    request: ApplyResolvedOpRequest,
    disposition: ApplyDisposition,
) -> ApplyResolvedOpAudit:
    group = request.rop.resolved_group_key_view
    return ApplyResolvedOpAudit(
        source_statute=request.amendment_id,
        op_id=request.rop.op_id or "",
        description=request.rop.description(),
        target_unit_kind=str(group.unit_kind or ""),
        target_norm=str(group.target_norm or ""),
        target_chapter=str(group.target_chapter or ""),
        target_part=str(group.target_part or ""),
        disposition=disposition,
    )
