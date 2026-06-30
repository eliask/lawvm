"""§2.9 UK per-op mutation-boundary adjudication: seam-observation projector.

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
byte-identical). Until the LS-01 cleanup increment UK ALSO kept its OWN in-fold
env-probe inside ``UKReplayExecutor.apply_op`` (migrated onto ``probe_base`` in
task #65, threading ``declared_recovery_prefixes`` per task #108-UK), whose only
remaining function was projecting an env-gated ``CompileAdjudication`` into
``adjudications_out``. The probe and the seam observer called the IDENTICAL core
producer (no drift, proven in ``tests/test_apply_seam_boundary_unification.py``),
so the in-fold probe was duplicate machinery.

This module is now the PROJECTOR half of the honest retirement: the in-fold
probe is deleted; ``replay_uk_ops`` instead DRAINS the seam's
``AppliedOp.observations`` mutation-boundary witness into the SAME
``uk_replay_mutation_boundary_per_op_violation_observed`` ``CompileAdjudication``
the in-fold probe used to emit (under the SAME env flag, into the SAME
``adjudications_out``). The seam observer already reads the IDENTICAL
``declared_recovery_prefixes`` from ``MaterializeResult`` (surfaced by
``_uk_materialize_one`` from the executor's recovery apply branches), so the
drained adjudication carries the IDENTICAL recovery-aware verdict the probe did —
the env-gated adjudication surface is PRESERVED, the duplicate per-op audit call
is gone (§2.5 one-proof-per-family; design §5 observe-first).

The drained adjudication is built via the SAME ``probe_base`` harness spec the
in-fold probe used (``_PROBE_SPEC`` — the D1 per-op row), so the uniform envelope
(rule_id/family/probe_mode/dispositions/witness fields) and the two D1-specific
overrides (``phase="replay"`` + ``blocking`` pass-through) are byte-identical to
the retired probe's record.

WHAT IT DOES NOT PROMISE (honesty boundary):
* It does NOT block the op — the mutation has already landed by the time the
  seam observer (and therefore this drain) runs.
* It does NOT prove full §1.0 totality — only the storage mutation boundary
  subset (``operation_storage_boundary_prefixes``).
* It carries declared_RECOVERY prefixes (task #108-UK) via the seam observer's
  ``MaterializeResult.declared_recovery_prefixes``; declared_migration /
  declared_editorial prefixes are still empty at v0.
"""
from __future__ import annotations

from typing import Iterable, Optional

from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    mutation_boundary_audit_enabled,
)
from lawvm.core.phase_result import Finding
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
# (rule_id/family/probe_mode/dispositions/witness fields) comes from this
# spec rather than a hand-written detail dict, so the drained record cannot drift
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
    """True when the per-op mutation-boundary adjudication should be projected.

    Thin alias over the core-owned :func:`mutation_boundary_audit_enabled` gate
    keyed on the UK flag — one fact, read from core, default-off. The seam runs
    the boundary observer unconditionally; this gate only controls whether
    ``replay_uk_ops`` PROJECTS the drained observation into ``adjudications_out``.
    """
    return mutation_boundary_audit_enabled(_PROBE_ENV_FLAG)


def project_boundary_observation(
    finding: Finding,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> CompileAdjudication:
    """Project ONE seam ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation
    into the UK ``uk_replay_mutation_boundary_per_op_violation_observed``
    ``CompileAdjudication``.

    The seam observer (``core/apply_seam.apply_op``) routes the core
    ``audit_op_mutation_boundary`` finding — the SAME producer the retired
    in-fold probe consumed, with the SAME ``declared_recovery_prefixes`` — to
    ``AppliedOp.observations``. This projection is the byte-identical successor of
    the deleted probe's adjudication build: it uses the SAME ``probe_base``
    harness spec + the two D1 overrides (``phase="replay"`` + the ``blocking``
    pass-through) and sources every diagnostic field from the one core finding's
    detail, so the UK record cannot drift from core / Finland.
    """
    core_detail = dict(finding.detail)
    return make_probe_observed_adjudication(
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
        blocking=finding.blocking,
        extra_detail={
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the
            # UK record cannot diverge from core / Finland.
            "changed_paths": list(core_detail.get("changed_paths", ())),
            "out_of_boundary_paths": list(core_detail.get("out_of_boundary_paths", ())),
            "boundary_status": core_detail.get("boundary_status", "out_of_boundary"),
            "core_finding_kind": finding.kind,
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
    and projects each into the UK per-op-violation adjudication via
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
    "UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "boundary_probe_enabled",
    "drain_seam_boundary_observations",
    "project_boundary_observation",
]
