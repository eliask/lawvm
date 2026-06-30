"""§2.9 production-lane guard-liveness for the UK per-op mutation-boundary probe.

CONTEXT
``lawvm.core.mutation_boundary_proof.verify_per_op`` (LS-01 / §1.0 invariants
at ``core/invariant_spec.py``) is the post-apply per-op mutation boundary
verifier: it diffs the IR tree before/after a ``LegalOperation`` apply and
reports any changed path that is not covered by the op's declared mutation
boundary (target declared_migration declared_recovery declared_editorial
projection). Finland wires it into replay at ``finland/apply_resolved_op.py:
426/471`` via ``_gate_mutation_boundary_at_op``; ``src/lawvm/uk_legislation/``
had NO production call site — the §2.9 worst failure class: a check that
exists, is registered, passes review, and creates false confidence in
invisible containment.

This module wires the verifier into the **UK replay fold's per-op apply site**
as an OBSERVATION-ONLY, env-gated probe. It is the **first consumer** of the
core-owned per-op audit ``lawvm.core.mutation_boundary_proof
.audit_op_mutation_boundary`` (§2.3 core-owns-mutation-boundary/findings, §2.5
one-proof-per-family): the probe does NOT re-run ``verify_per_op`` or re-derive
the verdict→finding shape — it calls the core audit in observation mode
(``is_strict=False``), takes the typed ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``
``Finding`` the core emits, and PROJECTS it into the UK
:class:`~lawvm.replay_adjudication.CompileAdjudication` interop surface for
every shortfall so the gap is VISIBLE without risking a bench-wide metric
shift. The shared diagnostic detail (op id, changed paths, out-of-boundary
paths, boundary status) therefore comes from the one core producer and cannot
drift from Finland's apply-lane emission. STRICT ENFORCEMENT (block under
strict mode) is multi-session: the UK replay fold has no ``strict_profile``
signaling path today (Finland's ``strict_profile``/``is_strict`` system is
absent in ``replay_executor.py`` — historically the executor mutated IR
mutably via the now-deleted ``UKMutableStatute`` mirror, and now operates on
the frozen ``IRStatute`` directly), so the probe is the discipline-disclosing
first step, not the strict verdict.

WHY A SNAPSHOT PROBE (post-apply, not pre-apply filter)
``verify_per_op`` computes ``diff_ir_paths(before, after)`` — it requires
before/after ``IRNode`` snapshots. The Finland replay fold already carries
``prev_state.ir`` / ``new_state.ir`` snapshots as part of its
``ReplayState`` model; the UK fold historically used ``UKMutableStatute`` (the
XJUR-02 "hidden replay kernel" — ``mutable_ir.py``, now deleted) and required a
fresh ``UKMutableNode.to_irnode()`` deep-copy per per-op snapshot. Under the
post-Wave-N3d frozen-``IRStatute`` fold the statute body is already immutable,
so the snapshot is a direct reference (no deep-copy); the snapshot cost is
therefore negligible, and default-off remains in place to preserve byte-stable
bench output. The probe never raises — it appends non-blocking
``uk_replay_mutation_boundary_per_op_violation_observed`` adjudications to
the supplied sink.

WHAT IT DOES NOT PROMISE (honesty boundary, mirror of the totality probe):
* It does NOT block the op — the mutation has already landed by the time
  the probe runs. A future ``strict_profile`` UK lane can flip
  ``blocking=True`` after the totality policy decides the enforcement
  disposition.
* It does NOT prove full §1.0 totality — only the storage mutation boundary
  subset (``operation_storage_boundary_prefixes``). Other §1.0 dimensions
  (declare-vs-actual target identity, migration path ownership) are carried
  by separate audit lanes.
* It carries declared_RECOVERY prefixes (task #108-UK): the executor's
  recovery apply branches surface the authorized recovery-retarget write
  parents per op, threaded here so an authorized retarget reads in-boundary —
  the SAME prefixes the always-on seam observer reads from
  ``MaterializeResult.declared_recovery_prefixes``. It does NOT yet carry
  declared_migration / declared_editorial prefixes — UK does not surface those
  per-op from a typed carrier; an empty tuple there preserves the conservative
  scope: any observed change outside the op's storage target / declared
  recovery boundary is reported.
"""
from __future__ import annotations

from typing import Optional, Sequence

from lawvm.core.ir import IRNode, IRStatute, LegalOperation
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.mutation_boundary_proof import (
    PerOpMutationBoundaryVerdict,
    audit_op_mutation_boundary,
    mutation_boundary_audit_enabled,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import ProbeSpec, make_probe_observed_adjudication

# Opt-in env flag — default-off preserves byte-stable bench replay output.
# Turn it on locally to surface the on-deck dormant per-op-violation class.
_PROBE_ENV_FLAG = "LAWVM_UK_MUTATION_BOUNDARY_PER_OP"

# UK-scoped adjudication kind emitted for an APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP
# shortfall. Mirrored after the existing ``uk_replay_*`` adjudication kind
# vocabulary; names the purpose explicitly so consumers can distinguish a
# per-op mutation-boundary escape from a fold-end accounting finding or a
# total-accounting shortfall.
UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND = (
    "uk_replay_mutation_boundary_per_op_violation_observed"
)

# The shared ``probe_base`` harness spec (task #65). D1 is the ONE per-op
# apply-site consumer of the harness: it differs from the 8 fold-exit probes
# only in ``phase="replay"`` + a ``blocking`` pass-through (both via the
# ``make_probe_observed_adjudication`` overrides). The uniform envelope
# (rule_id/family/probe_mode/dispositions/witness fields) now comes from this
# spec rather than a hand-written detail dict, so the D1 record cannot drift
# from the sibling probes' shape. ``core_registry_finding_kind`` stays empty:
# the concrete emitted finding kind is dynamic and carried per-finding as
# ``core_finding_kind`` in the extra detail.
_PROBE_SPEC = ProbeSpec(
    env_flag=_PROBE_ENV_FLAG,
    kind=UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    skipped_kind=UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND.replace(
        "_observed", "_probe_skipped"
    ),
    family="mutation_boundary",
    audit_module_path="core.mutation_boundary_proof.audit_op_mutation_boundary",
    # The canonical FI per-op violation witness (LS-01) is the SOTA analogue;
    # documented in core/invariant_spec.py at the LS-01 row.
    witness_prior_art="fi_apply_resolved_op_mutation_boundary_at_op_gate",
)


def boundary_probe_enabled() -> bool:
    """True when the per-op mutation-boundary probe should run on each apply.

    Thin alias over the core-owned :func:`mutation_boundary_audit_enabled` gate
    keyed on the UK flag — one fact, read from core, default-off.
    """
    return mutation_boundary_audit_enabled(_PROBE_ENV_FLAG)


def probe_op_mutation_boundary(
    *,
    before: Optional[IRNode],
    after: Optional[IRNode],
    op: LegalOperation,
    op_id: str,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
    declared_recovery_prefixes: Sequence[TreePath] = (),
) -> Optional[PerOpMutationBoundaryVerdict]:
    """Run the per-op mutation-boundary probe, appending each
    ``out_of_boundary`` short fall as a non-blocking ``CompileAdjudication``.

    Delegates the verify+emit to the core-owned
    :func:`~lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`
    (observation mode — ``is_strict=False``), which computes the op's storage
    mutation boundary and, on an escape, emits the typed
    ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` core finding. The probe PROJECTS
    that core finding into the UK ``CompileAdjudication`` interop surface — it
    no longer re-runs the verifier or re-derives the diagnostic shape, so the
    UK record cannot drift from the core producer (or from Finland's apply
    lane, which consumes the same producer family).

    ``declared_recovery_prefixes`` carries the concrete recovery-retargeted
    write-parent paths the executor's recovery apply branches surfaced for this
    op (e.g. the missing-leaf REPLACE→INSERT body-root write). They extend the
    op's declared mutation boundary so an authorized recovery retarget reads as
    within-boundary rather than an undeclared escape — the SAME prefixes the
    always-on seam observer reads from ``MaterializeResult.declared_recovery_prefixes``,
    keeping the in-fold probe and the seam observer in exact agreement. Default
    empty preserves the conservative scope when no recovery retarget fired.

    Returns the typed verdict on an out-of-boundary escape (also projected to
    ``adjudications_out`` when supplied); returns ``None`` on a clean apply or a
    ``None`` snapshot — emitting nothing (no diagnostic noise on a clean
    apply).
    """
    if before is None or after is None:
        return None
    audit = audit_op_mutation_boundary(
        before,
        after,
        op,
        op_id=str(op_id or ""),
        source_statute=str(source_statute or ""),
        is_strict=False,  # UK lane has no strict_profile signal yet (§2.9).
        declared_recovery_prefixes=tuple(declared_recovery_prefixes),
        # UK IRStatute.body is the top-level tree (no hcontainer wrapper);
        # ``strip_root_prefix`` defaults to ``()`` — no normalization needed,
        # unlike the FI replay fold which wraps under ("hcontainer", "").
    )
    verdict = audit.verdict
    if audit.within_boundary or not audit.findings:
        # Within boundary (or nothing emitted): no diagnostic noise, and the
        # historical probe contract returns None on a clean apply.
        return None
    core_finding = audit.findings[0]
    core_detail = dict(core_finding.detail)
    # Build via the shared probe_base harness (task #65). The uniform envelope
    # (rule_id/family/probe_mode/dispositions/witness fields) comes from
    # ``_PROBE_SPEC``; the per-finding evidence is the extra detail. The two
    # overrides — ``phase="replay"`` (per-op apply site, not fold-exit) and
    # ``blocking=core_finding.blocking`` (the pass-through; ``False`` today
    # under ``is_strict=False``) — are exactly what made D1 structurally
    # distinct from the 8 fold-exit probes; the harness now models both.
    adjudication = make_probe_observed_adjudication(
        _PROBE_SPEC,
        statute_id=str(source_statute or ""),
        message=(
            "UK replay per-op mutation boundary escaped: the op's changed tree "
            "paths are not a subset of its declared storage target boundary. "
            "Emitted observably; strict enforcement stays multi-session pending a "
            "UK strict_profile lane (§2.9 liveness, observation-only at v0)."
        ),
        op_id=str(op_id or ""),
        phase="replay",
        blocking=core_finding.blocking,
        extra_detail={
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the
            # UK record cannot diverge from core / Finland.
            "changed_paths": list(core_detail.get("changed_paths", ())),
            "out_of_boundary_paths": list(core_detail.get("out_of_boundary_paths", ())),
            "boundary_status": core_detail.get("boundary_status", verdict.boundary_status),
            "core_finding_kind": core_finding.kind,
        },
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return verdict


def snapshot_body(statute: IRStatute) -> Optional[IRNode]:
    """Capture an ``IRNode`` snapshot of the statute body before/after an apply.

    Wrapped as a named helper so the eviction / cache shape stays auditable at
    the call site and so tests can pin the snapshot shape (it is a fresh
    ``to_irnode`` deep-copy of the live mutable body, not a cached reference).
    """
    if statute is None or statute.body is None:
        return None
    return statute.body


__all__ = [
    "UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "boundary_probe_enabled",
    "probe_op_mutation_boundary",
    "snapshot_body",
]
