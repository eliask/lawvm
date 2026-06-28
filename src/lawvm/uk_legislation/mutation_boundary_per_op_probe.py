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
as an OBSERVATION-ONLY, env-gated probe — emitting typed
:class:`~lawvm.replay_adjudication.CompileAdjudication` records for every
``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`` shortfall so the gap is VISIBLE
without risking a bench-wide metric shift. STRICT ENFORCEMENT (block under
strict mode) is multi-session: the UK replay fold has no ``strict_profile``
signaling path today (Finland's ``strict_profile``/``is_strict`` system is
absent in ``replay_executor.py`` — see ``replay_executor.py`` already fact
the executor mutates IR mutably via ``UKMutableStatute``), so the probe is
the discipline-disclosing first step, not the strict verdict.

WHY A SNAPSHOT PROBE (post-apply, not pre-apply filter)
``verify_per_op`` computes ``diff_ir_paths(before, after)`` — it requires
before/after ``IRNode`` snapshots. The Finland replay fold already carries
``prev_state.ir`` / ``new_state.ir`` snapshots as part of its
``ReplayState`` model; the UK fold uses ``UKMutableStatute`` (the XJUR-02
"hidden replay kernel" — ``mutable_ir.py``), so each per-op snapshot is a
fresh ``UKMutableNode.to_irnode()`` deep-copy. That snapshot cost is real but
acceptable because the probe is **default-off**: production UK bench replay
output stays byte-stable; opt-in only for diagnostic runs / corpus probes /
CI liveness checks. The probe never raises — it appends non-blocking
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
* It does NOT carry declared_migration / declared_recovery / declared_editorial
  prefixes at v0 — UK does not yet surface those per-op from a typed
  carrier. Pre-fixing an empty tuple preserves the conservative scope: any
  observed change outside the op's storage target boundary is reported.
"""
from __future__ import annotations

import os
from typing import Optional

from lawvm.core.ir import IRNode, IRStatute, LegalOperation
from lawvm.core.mutation_boundary_proof import (
    PerOpMutationBoundaryVerdict,
    verify_per_op,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.replay_adjudication import CompileAdjudication

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


def boundary_probe_enabled() -> bool:
    """True when the per-op mutation-boundary probe should run on each apply."""
    return os.environ.get(_PROBE_ENV_FLAG, "") == "1"


def probe_op_mutation_boundary(
    *,
    before: Optional[IRNode],
    after: Optional[IRNode],
    op: LegalOperation,
    op_id: str,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> Optional[PerOpMutationBoundaryVerdict]:
    """Run the per-op mutation-boundary probe, appending each
    ``out_of_boundary`` short fall as a non-blocking ``CompileAdjudication``.

    The probe computes the op's storage mutation boundary
    (``operation_storage_boundary_prefixes(op)`` — no declared migration /
    recovery / editorial-projection prefixes at v0) and diffs ``before``→
    ``after``. Any observed changed path outside that boundary is reported as
    an ``uk_replay_*_violation_observed`` adjudication on the sink list —
    never a strict-mode block at v0.

    Returns the typed verdict (also appended to ``adjudications_out`` when
    supplied and out-of-bound). Callers without an output sink get the
    verdict as a return value, mirroring the helper-return shape Finland uses.

    Emits nothing when within boundary (no diagnostic noise on a clean apply).
    """
    if before is None or after is None:
        return None
    verdict = verify_per_op(
        before,
        after,
        op,
        op_id=str(op_id or ""),
        # UK IRStatute.body is the top-level tree (no hcontainer wrapper);
        # ``strip_root_prefix`` defaults to ``()`` — no normalization needed,
        # unlike the FI replay fold which wraps under ("hcontainer", "").
    )
    if verdict.within_boundary:
        return None
    adjudication = CompileAdjudication(
        kind=UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
        message=(
            "UK replay per-op mutation boundary escaped: the op's changed tree "
            "paths are not a subset of its declared storage target boundary. "
            "Emitted observably; strict enforcement stays multi-session pending a "
            "UK strict_profile lane (§2.9 liveness, observation-only at v0)."
        ),
        source_statute=str(source_statute or ""),
        op_id=str(op_id or ""),
        blocking=False,
        phase="replay",
        detail={
            "rule_id": UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
            "family": "mutation_boundary",
            "reason_code": "per_op_mutation_boundary_escape_observed",
            "op_id": str(op_id or ""),
            "changed_paths": list(verdict.changed_paths),
            "out_of_boundary_paths": list(verdict.out_of_boundary_paths),
            "boundary_status": verdict.boundary_status,
            "probe_mode": "observation_only",
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
            "witness_class": "core.mutation_boundary_proof.verify_per_op",
            # The canonical FI per-op violation witness (LS-01) is the SOTA
            # analogue; documented in core/invariant_spec.py at the LS-01 row.
            "witness_prior_art": "fi_apply_resolved_op_mutation_boundary_at_op_gate",
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
