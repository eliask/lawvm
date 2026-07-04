"""Finland's θ totalization table — mixed routed / observation-based model (#186).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §2.3. This module
extends the neutral θ TotalizationTable (``core/totalization.py``) to Finland.

ROUTING STATUS (#206 tail). Three of FI's cells are now LOAD-BEARING — θ is their
SINGLE SOURCE, imported by the production apply path:

* ``(RENUMBER, SELF_RELABEL) → NoopIdempotent("self_relabel_noop")`` —
  ``restructure_plan.py`` (``_execute_relabel`` ~2118, ``_execute_same_parent_
  relabel_group`` ~1701) reads ``FI_TOTALIZATION_TABLE.lookup(...).code``.
* ``(RENUMBER, DEST_OCCUPIED) → Reject("destination_occupied")`` —
  ``restructure_plan.py::_execute_relabel`` ~2259 reads the table.
* ``(REPEAL, TARGET_ABSENT) → NoopIdempotent("idempotent_repeal_parent_section_
  absent")`` — ``apply_typed_dispatch.py`` ~1000 reads the table.

Each routed site is BYTE-IDENTICAL (the table returns exactly the disposition the
inline code used to hard-code; SHA-verified on the FI corpus). The conformance
test (``tests/test_totalization_conformance_fi.py``) binds these cells to the
``.lookup(...)`` call, so dropping the routing FAILS the test.

WHY THE OCCUPANCY CELLS STAY DECLARED (routing N-A). The reject/adjudicate
frontends (NO/SE/EE/UK) partition every off-domain op into a REJECTED lane
carrying a ``*_replay_*`` code, and θ.lookup routes that partition. FI's OCCUPANCY
lane has NO such partition — it cannot be byte-safely routed through a single
table cell:

* **Off-domain occupancy is a NON-BLOCKING OBSERVATION, not a reject.**
  ``finland/apply_policy.py`` (~838): when a slot's occupancy is outside
  ``policy.allowed_from``, FI appends a ``Finding(kind=
  "APPLY.OCCUPANCY_POLICY_VIOLATION", role="observation", blocking=False,
  detail={"strict_disposition": "record"})`` and then PROCEEDS to apply. §2.3's FI
  column — "INSERT target_occupied: apply + occupancy observation"; "REPLACE
  target_absent: observation, non-blocking" — is literally this lane. There is no
  reject carrier to make θ "the single source of": the op applies regardless.

* **FI accounts via a THREE-OUTCOME mutation-event ledger**, not a two-lane
  reject/accept partition: ``core/mutation_events.py::MutationEvent.outcome ∈
  {"applied", "skipped", "failed"}``. "skipped" = an idempotent/no-op or
  admissibility skip, witnessed at ~13 fixed sites each carrying a
  ``reason_code``; "failed" = a genuine ``FailedOp`` (``finland/ops.py``,
  ~70 distinct ``reason_code``s).

* **FI's apply entry is rop/state-heavy.** ``finland/apply.py::apply_op`` takes
  ``ReplayState, AmendmentOp, StatuteContext, muutos_ir, rop=ResolvedOp,
  migration_ledger, replay_history_ops, ...`` — there is no
  ``apply_fi_ops_conserved(statute, [op])`` analogue to SE/EE. Routing arbitrary
  cells through it needs a bespoke fixture per cell, at real byte-identity risk on
  the LARGEST corpus, for zero behavioral change (the occupancy cells have nothing
  to route — the op is applied either way).

So the faithful deliverable is a **DECLARED, conformance-tested** FI θ table that
documents FI's ACTUAL off-domain dispositions as data (parallel-first), with
routing DEFERRED. This is BYTE-IDENTICAL on the FI corpus (no production code path
changes); the conformance test (``tests/test_totalization_conformance_fi.py``)
binds each declared cell to the literal ``reason_code`` / observation ``kind`` FI
emits at its named site, so the table is a faithful spec that FAILS if FI's
off-domain behaviour drifts.

FI'S DISPOSITION SEMANTICS in θ terms. Reusing the neutral vocabulary
(Reject/NoopIdempotent/Recover), FI's off-domain cells map as:

* The **occupancy-observation** cells (INSERT into an occupied slot; REPLACE on an
  absent slot that installs a base-frame section) → ``NoopIdempotent``: the op is
  well-formed and APPLIES; the off-domain divergence is recorded as a non-blocking
  observation, NOT a refusal. (θ's ``NoopIdempotent`` names "the precondition path
  is honoured and the op lands no *conflicting* write" — the closest neutral
  disposition to FI's apply-and-observe; the code is the observation ``kind``.)
* The **idempotent-skip** cells (REPEAL of a subsection whose parent section is
  already absent; a RENUMBER whose source == destination) → ``NoopIdempotent``
  carrying the FI skip ``reason_code``.
* The one genuine off-domain **FAILURE** (a grouped RENUMBER whose destination
  label is already held by a DIFFERENT occupant) → ``Reject("destination_occupied")``:
  FI does NOT recover here (no scaffold-relabel-over-occupant); the executor
  returns ``ExecutedOp(success=False, reason_code="destination_occupied")``.

Grounded line-by-line in the FI source (verified at base 88c742ca7):

* ``APPLY.OCCUPANCY_POLICY_VIOLATION`` — off ``allowed_from`` observation
  (apply_policy.py ~851); non-blocking, op proceeds.
* ``idempotent_repeal_parent_section_absent`` — subsection REPEAL, parent gone
  (apply_typed_dispatch.py ~1000); ``outcome="skipped"``.
* ``self_relabel_noop`` — RENUMBER source == destination
  (restructure_plan.py ~1701 / ~2118); ``ExecutedOp(success=False)``.
* ``destination_occupied`` — grouped RENUMBER destination label already occupied
  by a different node (restructure_plan.py ~2259); ``ExecutedOp(success=False)``.
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Reject,
    TotalizationTable,
)

__all__ = ["FI_TOTALIZATION_TABLE", "build_fi_totalization_table"]

#: The non-blocking occupancy-observation ``Finding.kind`` FI records when a
#: slot's occupancy is outside the op's ``allowed_from`` (apply_policy.py). Both
#: the off-``allowed_from`` violation and the allowed-but-non-primary note share
#: this kind — the code the θ NoopIdempotent cells cite for the observation lane.
FI_OCCUPANCY_OBSERVATION_KIND = "APPLY.OCCUPANCY_POLICY_VIOLATION"


def build_fi_totalization_table() -> TotalizationTable:
    """Construct FI's θ table (mixed routed / observation-based).

    The occupancy cells are ``NoopIdempotent`` (apply-and-observe, non-blocking);
    the idempotent-skip cells are ``NoopIdempotent`` (skip reason_code); the one
    genuine destination-collision cell is a ``Reject``. The three RENUMBER/REPEAL
    precondition-failure cells are ROUTED — θ is their single source, imported by
    ``restructure_plan.py`` / ``apply_typed_dispatch.py`` (byte-identical). The
    occupancy cells stay DECLARED (routing N-A; see the module docstring).
    """
    return TotalizationTable(
        jurisdiction="fi",
        rows={
            # ── Occupancy-observation cells: the op APPLIES; the off-domain
            # divergence is recorded as a non-blocking observation (§2.3's FI
            # column). Modeled as NoopIdempotent carrying the observation kind. ──
            # INSERT into a live/occupied slot: apply + occupancy observation
            # (apply_policy.py ~851; SUBSTANTIVE not in fresh_insert.allowed_from).
            (StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED): NoopIdempotent(
                FI_OCCUPANCY_OBSERVATION_KIND
            ),
            # REPLACE on an absent slot: non-blocking. The base-frame-empty install
            # lane (apply_policy.py ~733) proceeds as a create; an off-allowed_from
            # occupancy is otherwise recorded as the observation (~851).
            (StructuralAction.REPLACE, FailureClass.TARGET_ABSENT): NoopIdempotent(
                FI_OCCUPANCY_OBSERVATION_KIND
            ),
            # ── Idempotent-skip cell (ROUTED: θ is the single source). REPEAL of a
            # subsection whose parent section is already absent — an idempotent
            # repeal. apply_typed_dispatch.py ~1000 reads this cell's code. ───────
            (StructuralAction.REPEAL, FailureClass.TARGET_ABSENT): NoopIdempotent(
                "idempotent_repeal_parent_section_absent"
            ),
            # (ROUTED: θ is the single source.) A RENUMBER/relabel whose parsed
            # source address equals its destination — nothing to move. Keys on the
            # additive FI SELF_RELABEL class. restructure_plan.py ~1701/~2118 read
            # this cell's code.
            (StructuralAction.RENUMBER, FailureClass.SELF_RELABEL): NoopIdempotent(
                "self_relabel_noop"
            ),
            # ── The one genuine off-domain FAILURE (ROUTED: θ is the single
            # source). A RENUMBER whose destination label is already held by a
            # DIFFERENT occupant: FI does NOT recover (no scaffold-relabel-over-
            # occupant); the executor fails the op. A Reject, not a Recover.
            # restructure_plan.py ~2259 reads this cell's code. ──────────────────
            (StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED): Reject(
                "destination_occupied"
            ),
        },
        # §2.3 strict-default floor for any unlisted cell. FI's real skip lane
        # names its own reason_code at each of ~13 witnessed sites and its
        # occupancy default is the non-blocking observation, so this default is
        # NOT the production disposition for any real FI cell — it is retained
        # only as the type's total floor (a table must be total over the grid).
        default=Reject("fi_replay_skipped_unspecified"),
    )


#: FI's DECLARED θ totalization table (module-level singleton; the frontend datum).
FI_TOTALIZATION_TABLE: TotalizationTable = build_fi_totalization_table()
