"""§2.9 SE per-op mutation-boundary adjudication: seam-observation projector.

CONTEXT
``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`` (LS-01 / §1.0
invariants at ``core/invariant_spec.py``) is the post-apply per-op
mutation-boundary verifier+emitter: it diffs the IR tree before/after a
``LegalOperation`` apply and, on any changed path that is not covered by the
op's declared mutation boundary (target ∪ declared_migration ∪
declared_recovery ∪ declared_editorial projection), emits the typed
``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` finding. Finland wires it into replay
at ``finland/apply_resolved_op.py`` via ``_gate_mutation_boundary_at_op``.

HISTORY (the retired in-fold probe)
B-enforcement increment 2 made ``core/apply_seam.apply_op`` the UNIVERSAL
always-on LS-01 observer: under every tree profile (``boundary_mode="off"``) the
seam runs the SAME core ``audit_op_mutation_boundary`` on each landed write and
routes the ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation to
``AppliedOp.observations`` (NEVER ``findings`` — production output is
byte-identical). Until the LS-01 cleanup increment SE ALSO kept its OWN in-fold
env-probe in the ``apply_se_ops`` materializer ``finally``, whose only remaining
function was projecting an env-gated ``CompileAdjudication`` into
``adjudications_out``. The probe and the seam observer called the IDENTICAL core
producer (no drift, proven in ``tests/test_apply_seam_boundary_unification.py``),
so the in-fold probe was duplicate machinery.

This module is now the PROJECTOR half of the honest retirement: the in-fold
probe is deleted; ``apply_se_ops`` instead DRAINS the seam's
``AppliedOp.observations`` mutation-boundary witness into the SAME
``se_replay_mutation_boundary_per_op_violation_observed`` ``CompileAdjudication``
the in-fold probe used to emit (under the SAME env flag, into the SAME
``adjudications_out``). The env-gated adjudication surface is PRESERVED; the
duplicate per-op audit call is gone (§2.5 one-proof-per-family; design §5
observe-first).

WHY OBSERVATION-ONLY (unchanged from the in-fold probe)
The SE apply fold has no ``strict_profile`` signaling path today; the seam runs
the boundary audit ``is_strict=False`` for ``boundary_mode="off"`` profiles, so
the drained adjudication is the discipline-disclosing first step, not a strict
verdict. STRICT ENFORCEMENT is the ``boundary_mode="observe"``→``"block"``
profile promotion, NOT this projector.

WHAT IT DOES NOT PROMISE (honesty boundary):
* It does NOT block the op — the mutation has already landed by the time the
  seam observer (and therefore this drain) runs.
* It does NOT prove full §1.0 totality — only the storage mutation boundary
  subset (``operation_storage_boundary_prefixes``).
* It does NOT carry declared_migration / declared_recovery / declared_editorial
  prefixes at v0 — SE does not surface those per-op from a typed carrier, so the
  seam's ``MaterializeResult.declared_recovery_prefixes`` is empty; any observed
  change outside the op's storage target boundary is reported.
* It observes the statute ``body`` tree only. ``apply_se_ops`` also mutates the
  separate ``supplements`` (appendix) list for appendix ops; an appendix-only op
  leaves ``body`` unchanged and so reads clean here (conservative v0 scope).
"""
from __future__ import annotations

from typing import Iterable, Optional

from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    mutation_boundary_audit_enabled,
)
from lawvm.core.phase_result import Finding
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

# Opt-in env flag — default-off preserves byte-stable bench replay output.
# Turn it on locally to surface the on-deck dormant per-op-violation class.
_PROBE_ENV_FLAG = "LAWVM_SE_MUTATION_BOUNDARY_PER_OP"

# SE-scoped adjudication kind emitted for an APPLY.MUTATION_BOUNDARY_FINDING_AT_OP
# escape. Mirrored after the existing ``se_replay_*`` / UK
# ``uk_replay_mutation_boundary_per_op_violation_observed`` vocabulary; names
# the purpose explicitly so consumers can distinguish a per-op mutation-boundary
# escape from a target-not-found skip or an unsupported-action skip.
SE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND = (
    "se_replay_mutation_boundary_per_op_violation_observed"
)


def boundary_probe_enabled() -> bool:
    """True when the per-op mutation-boundary adjudication should be projected.

    Thin alias over the core-owned :func:`mutation_boundary_audit_enabled` gate
    keyed on the SE flag — one fact, read from core, default-off. The seam runs
    the boundary observer unconditionally; this gate only controls whether
    ``apply_se_ops`` PROJECTS the drained observation into ``adjudications_out``.
    """
    return mutation_boundary_audit_enabled(_PROBE_ENV_FLAG)


def project_boundary_observation(
    finding: Finding,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> CompileAdjudication:
    """Project ONE seam ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation
    into the SE ``se_replay_mutation_boundary_per_op_violation_observed``
    ``CompileAdjudication``.

    The seam observer (``core/apply_seam.apply_op``) routes the core
    ``audit_op_mutation_boundary`` finding — the SAME producer the retired
    in-fold probe consumed — to ``AppliedOp.observations``. This projection is
    the byte-identical successor of the deleted probe's adjudication build: it
    sources every diagnostic field from the one core finding's detail, so the SE
    record cannot drift from core / Finland / UK.
    """
    core_detail = dict(finding.detail)
    return CompileAdjudication(
        kind=SE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
        message=(
            "Sweden replay per-op mutation boundary escaped: the op's changed "
            "tree paths are not a subset of its declared storage target "
            "boundary. Emitted observably; strict enforcement stays "
            "multi-session pending a SE strict_profile lane (§2.9 liveness, "
            "observation-only at v0)."
        ),
        source_statute=str(source_statute or ""),
        op_id=str(op_id or ""),
        blocking=finding.blocking,
        phase="replay",
        detail={
            "rule_id": SE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
            "family": "mutation_boundary",
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the
            # SE record cannot diverge from core / Finland / UK.
            "changed_paths": list(core_detail.get("changed_paths", ())),
            "out_of_boundary_paths": list(core_detail.get("out_of_boundary_paths", ())),
            "boundary_status": core_detail.get("boundary_status", "out_of_boundary"),
            "core_finding_kind": finding.kind,
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": "core.mutation_boundary_proof.audit_op_mutation_boundary",
            # The canonical FI per-op violation witness (LS-01) is the SOTA
            # analogue; documented in core/invariant_spec.py at the LS-01 row.
            "witness_prior_art": "fi_apply_resolved_op_mutation_boundary_at_op_gate",
        },
    )


def drain_seam_boundary_observations(
    observations: Iterable[Finding],
    *,
    adjudications_out: Optional[list[CompileAdjudication]],
    source_statute: str = "",
    op_id: str = "",
) -> None:
    """Drain a seam ``AppliedOp.observations`` tuple into ``adjudications_out``.

    Filters for the boundary witness kind (``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``)
    and projects each into the SE per-op-violation adjudication via
    :func:`project_boundary_observation`. A pure no-op when the env flag is off
    or ``adjudications_out`` is ``None`` (byte-identical production), exactly as
    the retired in-fold probe emitted nothing on the default path.
    """
    if adjudications_out is None or not boundary_probe_enabled():
        return
    for finding in observations:
        if finding.kind != MUTATION_BOUNDARY_FINDING_AT_OP_CODE:
            continue
        adjudications_out.append(
            project_boundary_observation(
                finding, source_statute=source_statute, op_id=op_id
            )
        )


__all__ = [
    "SE_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "boundary_probe_enabled",
    "drain_seam_boundary_observations",
    "project_boundary_observation",
]
