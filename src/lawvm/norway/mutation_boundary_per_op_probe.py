"""§2.9 NO per-op mutation-boundary adjudication: seam-observation projector.

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
byte-identical). Until the LS-01 cleanup increment NO ALSO kept its OWN in-fold
env-probe in the ``apply_no_ops`` materializer ``finally``, whose only remaining
function was projecting an env-gated ``CompileAdjudication`` into
``adjudications_out``. The probe and the seam observer called the IDENTICAL core
producer (no drift, proven in ``tests/test_apply_seam_boundary_unification.py``),
so the in-fold probe was duplicate machinery.

This module is now the PROJECTOR half of the honest retirement: the in-fold
probe is deleted; ``apply_no_ops`` instead DRAINS the seam's
``AppliedOp.observations`` mutation-boundary witness into the SAME
``no_replay_mutation_boundary_per_op_violation_observed`` ``CompileAdjudication``
the in-fold probe used to emit (under the SAME env flag, into the SAME
``adjudications_out``). The seam observer already threads
``declared_recovery_prefixes`` from the materializer's ``MaterializeResult``, so
the drained adjudication carries the IDENTICAL recovery-aware verdict the probe
did — the env-gated adjudication surface is PRESERVED, the duplicate per-op audit
call is gone (§2.5 one-proof-per-family; design §5 observe-first).

WHY OBSERVATION-ONLY (unchanged from the in-fold probe)
The NO apply fold has no ``strict_profile`` signaling path today; the seam runs
the boundary audit ``is_strict=False`` for ``boundary_mode="off"`` profiles, so
the drained adjudication is the discipline-disclosing first step, not a strict
verdict. STRICT ENFORCEMENT (block under a future NO strict_profile lane) is the
``boundary_mode="observe"``→``"block"`` profile promotion, NOT this projector.

WHAT IT DOES NOT PROMISE (honesty boundary):
* It does NOT block the op — the mutation has already landed by the time the
  seam observer (and therefore this drain) runs.
* It does NOT prove full §1.0 totality — only the storage mutation boundary
  subset (``operation_storage_boundary_prefixes``).
* The recovery-aware verdict is carried by the seam observer's
  ``declared_recovery_prefixes`` (surfaced per op from ``apply_no_ops`` on the
  ``MaterializeResult``); declared_migration / declared_editorial prefixes are
  still empty at v0 — NO does not yet surface those per-op.
"""
from __future__ import annotations

from typing import Iterable, Optional

from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    mutation_boundary_audit_enabled,
)
from lawvm.core.phase_result import Finding
from lawvm.core.probe_adjudication import ProbeSpec, make_probe_observed_adjudication
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

# The shared ``probe_adjudication`` harness spec (rule-of-three promotion).
# This NO per-op projector is one of the three apply-site consumers of the
# harness (UK/NO/SE): each differs from the 8 fold-exit probes only in
# ``phase="replay"`` + a ``blocking`` pass-through (both via the
# ``make_probe_observed_adjudication`` overrides). Sourcing the uniform envelope
# (rule_id/family/probe_mode/dispositions/witness fields) from this spec rather
# than a hand-written detail dict keeps the drained record byte-identical to the
# sibling UK/SE projectors. ``core_registry_finding_kind`` stays empty: the
# concrete emitted finding kind is dynamic and carried per-finding as
# ``core_finding_kind`` in the extra detail.
_PROBE_SPEC = ProbeSpec(
    env_flag=_PROBE_ENV_FLAG,
    kind=NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    skipped_kind=NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND.replace(
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
    keyed on the NO flag — one fact, read from core, default-off. The seam runs
    the boundary observer unconditionally; this gate only controls whether
    ``apply_no_ops`` PROJECTS the drained observation into ``adjudications_out``.
    """
    return mutation_boundary_audit_enabled(_PROBE_ENV_FLAG)


def project_boundary_observation(
    finding: Finding,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> CompileAdjudication:
    """Project ONE seam ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation
    into the NO ``no_replay_mutation_boundary_per_op_violation_observed``
    ``CompileAdjudication``.

    The seam observer (``core/apply_seam.apply_op``) routes the core
    ``audit_op_mutation_boundary`` finding — the SAME producer the retired
    in-fold probe consumed — to ``AppliedOp.observations``. This projection is
    the byte-identical successor of the deleted probe's adjudication build: it
    uses the SAME shared ``probe_adjudication`` harness spec the UK/SE sibling
    projectors use + the two per-op overrides (``phase="replay"`` + the
    ``blocking`` pass-through) and sources every diagnostic field from the one
    core finding's detail, so the NO record cannot drift from core / Finland / UK.
    """
    core_detail = dict(finding.detail)
    return make_probe_observed_adjudication(
        _PROBE_SPEC,
        statute_id=str(source_statute or ""),
        message=(
            "Norway replay per-op mutation boundary escaped: the op's changed "
            "tree paths are not a subset of its declared storage target "
            "boundary. Emitted observably; strict enforcement stays "
            "multi-session pending a NO strict_profile lane (§2.9 liveness, "
            "observation-only at v0)."
        ),
        op_id=str(op_id or ""),
        phase="replay",
        blocking=finding.blocking,
        extra_detail={
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            # Sourced from the core finding's detail (single producer) so the
            # NO record cannot diverge from core / Finland / UK.
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
    and projects each into the NO per-op-violation adjudication via
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
    "NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND",
    "boundary_probe_enabled",
    "drain_seam_boundary_observations",
    "project_boundary_observation",
]
