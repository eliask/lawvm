"""The United Kingdom's θ totalization table — the seam-sourced strict frontend (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. This extends
the θ ``TotalizationTable`` (``core/totalization.py``) to the UK, mirroring the
NO/SE/EE work already merged. It encodes the UK's off-domain
(precondition-failure) dispositions as data and routes the UK's off-domain
control flow through ``UK_TOTALIZATION_TABLE.lookup(action, failure_class)`` so θ
is the single source of the off-domain disposition (the NO/SE/EE load-bearing
routing).

WHY THE UK IS DIFFERENT (and why this table is mostly its DEFAULT). Unlike the
NO/SE/EE grafters — whose conservation enumerates a fixed set of ``*_replay_*``
skip codes, one per off-domain lane — the UK is the reference **I1 ✓seam**
frontend: its conservation partition is SEAM-SOURCED, not adjudication-keyed
(``replay_conserved.py`` module docstring). A PREPARED op is accepted iff its
seam apply landed a write (the ``AppliedOp.applied`` signal); a prepared op that
landed no write is apply-skipped and surfaces in the conserved rejected lane
under the UNIFORM reason code ``uk_apply_no_write`` — regardless of which of the
UK's ~70 descriptive ``uk_replay_*`` adjudication kinds narrated the miss. The UK
also RECOVERS aggressively inside the algebra (a missing-leaf REPLACE
materializes as an INSERT — ``uk_replay_replace_materialized_as_insert_for_missing_leaf``
— so ``TARGET_ABSENT`` on a REPLACE is *accepted*, not rejected), so the
recovering lanes never reach a reject cell at all.

So at the CONSERVATION level — the level θ totalizes — the UK exhibits exactly
two off-domain dispositions, and this table encodes both faithfully:

* The **seam-sourced strict default**: any prepared op that landed no write and
  was not recovered rejects under ``uk_apply_no_write`` (the §2.3 strict-default
  ``Reject``). This is the single cell that covers the repeal-of-absent-target,
  renumber-of-absent-source, text-patch-missing-payload, empty-schedule-shape-gap,
  … lanes — each narrated by its own descriptive ``uk_replay_*`` adjudication in
  ``adjudications_out`` but conserved under the one uniform seam reason code. It
  is the ``default`` here precisely because the UK's disposition is derived from
  the seam applied-signal, not enumerated per lane.

* One explicit **prepare-time reject cell**: an amendment whose target is the
  whole-act facet but whose action is neither a whole-act REPEAL nor the
  recognized whole-act text substitution is filtered at prepare time
  (``replay_prepare.py``) and conserved under ``uk_replay_unsupported_action``.
  This is an ACTION-ADMISSIBILITY failure (the whole-act instruction never routes
  to a supported kernel op), so it keys on the additive EE ``UNSUPPORTED_ACTION``
  ``FailureClass`` member (reused — the semantics match exactly), on the ``META``
  action (the whole-act facet is not one of the routable structural actions). The
  same ``uk_replay_unsupported_action`` code is also emitted by two defensive
  apply-time lanes in ``replay_executor.py`` (a whole-act target with an
  unhandled action, and the ``"unknown"`` action arm); those route through the
  identical cell so the code has a single source.

ROUTING (load-bearing, byte-identical). The routing is a PURE REPRESENTATION
CHANGE: the codes, receipts, rule_ids, and final tree-state are unchanged on the
UK corpus (the UK already used seam-sourced conservation, so encoding the
disposition and dispatching on it reproduces current behavior exactly). It is
guarded by ``tests/test_totalization_conformance.py``, which drives each declared
cell through the REAL ``replay_uk_ops_conserved`` path and asserts the observed
disposition equals the declaration.

DISCIPLINE (AGENTS.md §0-§2). Jurisdiction-neutral kernel: this module imports
the neutral ``core.totalization`` type; the kernel never imports the UK package
(the registry direction). The table is frozen, typed, deterministic.
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    Reject,
    TotalizationTable,
)

__all__ = ["UK_TOTALIZATION_TABLE", "build_uk_totalization_table"]


def build_uk_totalization_table() -> TotalizationTable:
    """Construct the UK's seam-sourced strict θ table (§2.3 default = ``Reject``).

    The UK never routes an off-domain lane to a distinct enumerated reject code
    the way NO/SE/EE do — its conservation reads the seam applied-signal — so the
    table is the strict-default shape plus the single explicit prepare-time
    action-admissibility reject cell.
    """
    return TotalizationTable(
        jurisdiction="uk",
        rows={
            # ── Action-admissibility prepare-time reject. ──────────────────────
            # An amendment addressing the whole-act facet whose action is neither
            # a whole-act REPEAL nor the recognized whole-act text substitution is
            # filtered at prepare time (``replay_prepare.py``) and conserved under
            # ``uk_replay_unsupported_action``. The whole-act facet is not one of
            # the routable structural actions, so the cell keys on the META action
            # and the (reused, additive-from-EE) UNSUPPORTED_ACTION failure class.
            # The two defensive apply-time lanes in ``replay_executor.py`` (an
            # unhandled whole-act action, and the ``"unknown"`` action arm) emit
            # the same code and route through this same cell.
            (StructuralAction.META, FailureClass.UNSUPPORTED_ACTION): Reject(
                "uk_replay_unsupported_action"
            ),
        },
        # §2.3 strict default — the SEAM-SOURCED cell. Any prepared op that landed
        # no write and was not recovered rejects under the UNIFORM conserved
        # reason code ``uk_apply_no_write`` (``replay_conserved.py``): the UK's
        # accepted/rejected partition is derived from the ``AppliedOp.applied``
        # seam signal, not from enumerating the ~70 descriptive ``uk_replay_*``
        # adjudication kinds that narrate the individual misses. This is the UK's
        # I1 ✓seam character encoded as the θ default.
        default=Reject("uk_apply_no_write"),
    )


#: The UK's seam-sourced strict θ totalization table (module-level singleton; the
#: frontend datum). Statically imported by the UK replay modules so the
#: off-domain disposition is dispatched from this single source (LIVE).
UK_TOTALIZATION_TABLE: TotalizationTable = build_uk_totalization_table()
