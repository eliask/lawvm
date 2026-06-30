"""§2.9 production-lane guard-liveness for the NO per-op mutation-boundary probe.

CONTEXT
``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`` (LS-01 / §1.0
invariants at ``core/invariant_spec.py``) is the post-apply per-op
mutation-boundary verifier+emitter: it diffs the IR tree before/after a
``LegalOperation`` apply and, on any changed path that is not covered by the
op's declared mutation boundary (target ∪ declared_migration ∪
declared_recovery ∪ declared_editorial projection), emits the typed
``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` finding. Finland wires it into replay
at ``finland/apply_resolved_op.py`` via ``_gate_mutation_boundary_at_op``; the
UK replay fold consumes it as the observation-only probe at
``uk_legislation/mutation_boundary_per_op_probe.py``. ``norway/grafter.py``
had NO production call site for this audit — the §2.9 worst failure class: a
check that exists, is registered, passes review, and creates false confidence
in invisible containment.

This module wires the audit into the **NO apply fold's per-op apply loop**
(``apply_no_ops``) as an OBSERVATION-ONLY, env-gated probe. It is a consumer of
the core-owned per-op audit (§2.3 core-owns-mutation-boundary/findings, §2.5
one-proof-per-family): the probe does NOT re-run the verifier or re-derive the
verdict→finding shape — it calls the core audit in observation mode
(``is_strict=False``), takes the typed ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``
``Finding`` the core emits, and PROJECTS it into the NO
:class:`~lawvm.replay_adjudication.CompileAdjudication` interop surface for
every escape so the gap is VISIBLE without risking a bench-wide metric shift.
The shared diagnostic detail (op id, changed paths, out-of-boundary paths,
boundary status) therefore comes from the one core producer and cannot drift
from Finland's apply-lane emission or the UK probe.

WHY OBSERVATION-ONLY (mirror of the UK first-step shape)
The NO apply fold has no ``strict_profile`` signaling path today (Finland's
``strict_profile``/``is_strict`` system is absent in ``apply_no_ops``; NO's
strict toggles — ``strict_invariants`` / ``strict_action_family`` /
``strict_recovery`` — gate tree-invariant / action-family / recovery findings,
not the per-op mutation boundary). So the probe is the discipline-disclosing
first step, not the strict verdict. STRICT ENFORCEMENT (block under a future
NO strict_profile lane) is multi-session.

WHY A SNAPSHOT PROBE (post-apply, not pre-apply filter)
``audit_op_mutation_boundary`` requires before/after ``IRNode`` snapshots.
``apply_no_ops`` folds over a frozen ``IRNode`` ``body`` reassigned through the
persistent ``lawvm.core.tree_ops`` operations, so the per-op before/after pair
is a direct reference (no deep-copy) and the core's identity-pruned diff costs
proportionally to the touched region. default-off therefore preserves
byte-stable bench output while the snapshot cost is negligible when opted in.

WHAT IT DOES NOT PROMISE (honesty boundary, mirror of the UK probe):
* It does NOT block the op — the mutation has already landed by the time the
  probe runs. A future NO strict_profile lane can flip ``blocking=True``.
* It does NOT prove full §1.0 totality — only the storage mutation boundary
  subset (``operation_storage_boundary_prefixes``).
* It does NOT carry declared_migration / declared_recovery / declared_editorial
  prefixes at v0 — NO does not yet surface those per-op from a typed carrier.
  Pre-fixing an empty tuple preserves the conservative scope: any observed
  change outside the op's storage target boundary is reported.
* It observes the statute ``body`` tree only; NO ``apply_no_ops`` mutates no
  other surface in the per-op loop (no supplements lane), so the body diff is
  the complete per-op footprint.
"""
from __future__ import annotations

from typing import Optional

from lawvm.core.ir import IRNode, LegalOperation
from lawvm.core.mutation_boundary_proof import (
    PerOpMutationBoundaryVerdict,
    audit_op_mutation_boundary,
    mutation_boundary_audit_enabled,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# Opt-in env flag — default-off preserves byte-stable bench replay output.
# Turn it on locally to surface the on-deck dormant per-op-violation class.
_PROBE_ENV_FLAG = "LAWVM_NO_MUTATION_BOUNDARY_PER_OP"

# NO-scoped adjudication kind emitted for an APPLY.MUTATION_BOUNDARY_FINDING_AT_OP
# escape. Mirrored after the existing ``no_replay_*`` / UK
# ``uk_replay_mutation_boundary_per_op_violation_observed`` vocabulary; names
# the purpose explicitly so consumers can distinguish a per-op mutation-boundary
# escape from a tree-invariant violation or an action-family recovery.
NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND = (
    "no_replay_mutation_boundary_per_op_violation_observed"
)


def boundary_probe_enabled() -> bool:
    """True when the per-op mutation-boundary probe should run on each apply.

    Thin alias over the core-owned :func:`mutation_boundary_audit_enabled` gate
    keyed on the NO flag — one fact, read from core, default-off.
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
) -> Optional[PerOpMutationBoundaryVerdict]:
    """Run the per-op mutation-boundary probe, appending each ``out_of_boundary``
    escape as a non-blocking ``CompileAdjudication``.

    Delegates verify+emit to the core-owned
    :func:`~lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`
    (observation mode — ``is_strict=False``), which computes the op's storage
    mutation boundary (no declared migration / recovery / editorial-projection
    prefixes at v0) and, on an escape, emits the typed
    ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` core finding. The probe PROJECTS
    that core finding into the NO ``CompileAdjudication`` interop surface — it
    does not re-run the verifier or re-derive the diagnostic shape, so the NO
    record cannot drift from the core producer (or from Finland's apply lane /
    the UK probe, which consume the same producer family).

    Returns the typed verdict on an out-of-boundary escape (also projected to
    ``adjudications_out`` when supplied); returns ``None`` on a clean apply or a
    ``None`` snapshot — emitting nothing (no diagnostic noise on a clean apply).
    """
    if before is None or after is None:
        return None
    audit = audit_op_mutation_boundary(
        before,
        after,
        op,
        op_id=str(op_id or ""),
        source_statute=str(source_statute or ""),
        is_strict=False,  # NO fold has no strict_profile signal yet (§2.9).
        # NO IRStatute.body is the top-level tree (no hcontainer wrapper);
        # ``strip_root_prefix`` defaults to ``()`` — no normalization needed,
        # unlike the FI replay fold which wraps under ("hcontainer", "").
    )
    verdict = audit.verdict
    if audit.within_boundary or not audit.findings:
        # Within boundary (or nothing emitted): no diagnostic noise, and the
        # probe contract returns None on a clean apply.
        return None
    core_finding = audit.findings[0]
    core_detail = dict(core_finding.detail)
    adjudication = CompileAdjudication(
        kind=NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
        message=(
            "Norway replay per-op mutation boundary escaped: the op's changed "
            "tree paths are not a subset of its declared storage target "
            "boundary. Emitted observably; strict enforcement stays "
            "multi-session pending a NO strict_profile lane (§2.9 liveness, "
            "observation-only at v0)."
        ),
        source_statute=str(source_statute or ""),
        op_id=str(op_id or ""),
        blocking=core_finding.blocking,
        phase="replay",
        detail={
            "rule_id": NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
            "family": "mutation_boundary",
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the
            # NO record cannot diverge from core / Finland / UK.
            "changed_paths": list(core_detail.get("changed_paths", ())),
            "out_of_boundary_paths": list(core_detail.get("out_of_boundary_paths", ())),
            "boundary_status": core_detail.get("boundary_status", verdict.boundary_status),
            "core_finding_kind": core_finding.kind,
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": "core.mutation_boundary_proof.audit_op_mutation_boundary",
            # The canonical FI per-op violation witness (LS-01) is the SOTA
            # analogue; documented in core/invariant_spec.py at the LS-01 row.
            "witness_prior_art": "fi_apply_resolved_op_mutation_boundary_at_op_gate",
        },
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return verdict


__all__ = [
    "NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "boundary_probe_enabled",
    "probe_op_mutation_boundary",
]
