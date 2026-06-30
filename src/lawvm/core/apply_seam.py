"""Unified core apply seam (Wave 1 of the pipeline-unification plan).

Design reference: ``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.1 (the
``apply_op`` seam signature), §3.4 (metric-agnostic boundary), §3.5 (core /
frontend seam), §4 Wave 1 + §4.1 (why NO first).

WHAT THIS IS. The single per-op apply kernel every tree frontend will eventually
route through. It generalizes FI's
``finland/apply_resolved_op.py::apply_resolved_op_with_audit`` — the de-facto
reference contract (per-op ``WriteReceipt``, LS-01 mutation-boundary gate, the
soft-fail recovery disposition) — into a jurisdiction-neutral kernel. **FI is
DELEGATED**: this module reads FI as the reference and does NOT import or modify
``finland/``. It reuses the SAME core mechanisms FI calls
(``core/mutation_boundary_proof.audit_op_mutation_boundary``,
``core/write_receipt.WriteReceipt``, ``core/tree_ops`` CoW,
``core/mutation_boundary.diff_ir_paths_identity_pruned``) so the kernel and FI's
apply lane cannot drift.

THE SHAPE. ``apply_op(base_state, typed_op, *, provenance, profile)`` runs the
universal per-op stages (design §3.1):

  1. resolve the op's **declared region** via ``profile.region_metric``;
  2. **mutate** via ``profile.materializer`` (tree CoW over ``core/tree_ops``);
  3. compute the **observed region** (identity-pruned IR-path diff) and
     synthesize the :class:`~lawvm.core.write_receipt.WriteReceipt`;
  4. run the per-op **gate battery** (mutation-boundary via
     ``audit_op_mutation_boundary`` with the profile's ``boundary_mode``
     disposition — the block that FI's ``_enforce_per_op_apply_authority`` runs);
  5. on a materializer-signalled failure, call ``profile.recover`` →
     skip / rewrite / raise.

GENERIC + PARAMETRIC. ``apply_op`` / :class:`AppliedOp` are generic over
``State`` (``IRStatute``/``IRNode`` for tree frontends; section text for a future
US char-span lane) and parametric over a :class:`RegionMetric` (§3.4), so US can
later plug a char-span metric with zero kernel changes. This module ships the
IR-path metric + tree-CoW materializer pair (the only one Wave 1 needs).

THE FRONTEND SEAM (§3.5). The materializer (the per-op tree dispatch + the
frontend's jurisdiction-specific recovery transforms) and the profile's recovery
**policy** stay in the frontend. The kernel owns the boundary audit, the receipt
synthesis, the coverage delta, and the recovery **mechanism**. NO's three strict
flags map onto :class:`ApplyProfile` dispositions (the design's "strictness =
profile policy").

PLANE & DISCIPLINE (AGENTS.md §0-§2). The kernel mutates legal state ONLY through
the supplied materializer (which itself goes through ``core/tree_ops`` CoW); it
never mutates a passed tree in place. Typed, frozen, deterministic; fail-loud on
shape-invalid input (§1.10). The receipt + coverage outputs are ADDITIVE evidence
— a frontend that had none before gains them without its pre-existing
materialized state or findings changing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, Optional, Protocol, TypeVar

from lawvm.core.coverage import CoverageClaim
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRNode, LegalOperation, OperationSource
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    OccupancyClass,
    validate_transition,
)
from lawvm.core.phase_result import Finding
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    diff_ir_paths_identity_pruned,
)
from lawvm.core.mutation_boundary_proof import audit_op_mutation_boundary
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string

__all__ = [
    "State",
    "Region",
    "RegionMetric",
    "IR_PATH_METRIC",
    "MaterializeResult",
    "Materializer",
    "BoundaryMode",
    "ApplyFailure",
    "RecoveryAction",
    "RecoveryDecision",
    "ApplySeamRecoveryRaised",
    "default_recover",
    "CoverageDelta",
    "AuthorizationResolver",
    "REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE",
    "no_op_execution_authorization",
    "OccupancyResolver",
    "OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE",
    "no_op_occupancy",
    "STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION",
    "ApplyProfile",
    "AppliedOp",
    "apply_op",
]


# ── EV-05/FW-01/OV-01 universal ExecutionAuthorization OBSERVE gate ───────────
#
# The firewall TYPE (``core/execution_authorization.ExecutionAuthorization``)
# exists, but ``apply`` never checked it: the audit-registry's #2 highest-EV
# OPEN item names "apply_structure_ops/apply_runtime_support have ZERO references
# to ExecutionAuthorization" (EV-05 / FW-01 / OV-01). FI's
# ``finland/apply_resolved_op._gate_execution_authorization_at_op`` is the ONLY
# producer today and it fires per-frontend (FI only) + strict-only. This seam
# gate hoists the CHECK to the universal kernel and runs it for ALL 6 frontends,
# OBSERVE-first (design §5): a mutating op carrying no ``ExecutionAuthorization``
# proof emits a non-blocking ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED``
# observation to the SEPARATE :attr:`AppliedOp.observations` lane — never to
# :attr:`AppliedOp.findings` (which the byte-identity gates assert on). This
# respects EV-04 (observation is not authority) and keeps every gate green.

#: The non-blocking observation code the seam emits per mutating op lacking an
#: ExecutionAuthorization proof. Its strict-blocking twin (FI-only today) is
#: ``EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED``; promoting this observe gate to
#: that block per-profile is increment-2 work (see notes/B_ENFORCEMENT_STATUS.md).
REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE = (
    "EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED"
)

# A resolver answers: does THIS op carry/resolve an ExecutionAuthorization proof?
# ``(op) -> ExecutionAuthorization | None``. ``None`` is the honest firewall-hole
# witness — no op carries a proof today (``core/ir.LegalOperation`` has no
# authorization field), so the kernel-default resolver returns ``None`` for every
# op and the gap is ~100% by construction. A frontend that begins minting proofs
# supplies a resolver that returns the op's ExecutionAuthorization.
AuthorizationResolver = Callable[[LegalOperation], Optional[ExecutionAuthorization]]


def no_op_execution_authorization(
    _op: LegalOperation,
) -> Optional[ExecutionAuthorization]:
    """The honest default resolver: NO op carries an ExecutionAuthorization.

    ``core/ir.LegalOperation`` carries no authorization field today, so the apply
    path can resolve none — this is exactly the EV-05/FW-01 firewall hole the
    audit registry names ("apply has ZERO references to ExecutionAuthorization").
    Returning ``None`` for every op makes that hole VISIBLE and MEASURABLE
    (≈100% of mutating ops, the real gap size) without fabricating a proof. A
    frontend that mints proofs replaces this on its profile.
    """
    return None


# ── LS-03 universal occupancy-transition OBSERVE gate ─────────────────────────
#
# The occupancy TYPE + the raising ``validate_transition`` exist in
# ``core/occupancy`` (the (action, from)->to ``VALID_TRANSITIONS`` table), but
# the audit-registry flags LS-03 as a GUARD-LIVENESS hole: "the type+raise exist
# but no frontend currently BLOCKS; the gate is telemetry." FI's
# ``finland/apply_resolved_op._gate_occupancy_transition_at_op`` is the only
# producer and it is FI-only + strict-only. This seam gate hoists the CHECK to
# the universal kernel, OBSERVE-first (design §5): when a profile supplies an
# ``occupancy_resolver`` (FI is the reference for the (action, from)->to table
# semantics; the kernel does NOT import ``finland/``), a mutating op whose
# (action, from-occupancy) pair is not in ``VALID_TRANSITIONS`` emits a
# non-blocking ``APPLY.OCCUPANCY_TRANSITION_OBSERVED`` observation to the SEPARATE
# :attr:`AppliedOp.observations` lane — never to :attr:`AppliedOp.findings`. The
# kernel-default resolver models no occupancy (``None``), so all 6 production
# profiles are 0-delta by construction (no occupancy model → no-op, exactly like
# ``no_op_execution_authorization``). EV-04: observation is not authority.

#: The non-blocking observation code the seam emits for a mutating op whose
#: (action, from-occupancy) pair is an invalid occupancy transition. Its
#: strict-blocking twin (FI-only today) is ``APPLY.OCCUPANCY_TRANSITION_BLOCKED``;
#: promoting this observe gate to that block per-profile is staged work (see
#: notes/B_ENFORCEMENT_STATUS.md).
OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE = "APPLY.OCCUPANCY_TRANSITION_OBSERVED"

#: The jurisdiction-neutral map from a structural ``op.action.value`` to the
#: occupancy-modelled :class:`~lawvm.core.occupancy.OccupancyAction`. Only the
#: occupancy-relevant actions are mapped (REPLACE/INSERT/REPEAL — the same three
#: FI's ``finland/apply_policy._OP_TYPE_TO_ACTION`` maps; FI is the reference for
#: this table). A structural action with no occupancy meaning (RENUMBER, META,
#: TEXT_*, HEADING_REPLACE — these do not change whole-slot occupancy) is absent,
#: so the gate is a no-op for it, mirroring FI's ``action_value is None`` skip.
STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION: dict[str, OccupancyAction] = {
    "replace": OccupancyAction.REPLACE,
    "insert": OccupancyAction.INSERT,
    "repeal": OccupancyAction.REPEAL,
}

# An occupancy resolver answers: what is the CURRENT (before-state) occupancy of
# the slot THIS op targets? ``(op, before_state, after_state) -> OccupancyClass |
# None``. ``None`` means the profile does not model occupancy for this op (the
# slot is not whole-unit-addressable, the action carries no occupancy meaning, or
# the frontend has no occupancy model at all) — the gate is then a no-op,
# matching FI's per-op skip conditions (``action_value is None`` / not a
# whole-section target). The default kernel resolver models no occupancy at all.
OccupancyResolver = Callable[
    [LegalOperation, object, object], Optional[OccupancyClass]
]


def no_op_occupancy(
    _op: LegalOperation,
    _before: object,
    _after: object,
) -> Optional[OccupancyClass]:
    """The honest default resolver: the kernel models NO slot occupancy.

    No core IR type carries a typed occupancy class today, and Norway/Sweden do
    not model slot occupancy at all (per ``core/occupancy`` module docs), so the
    universal kernel resolves none — exactly the LS-03 guard-liveness hole the
    audit registry names ("the type+raise exist but no frontend BLOCKS; the gate
    is telemetry"). Returning ``None`` for every op makes that hole VISIBLE and
    keeps all 6 production profiles 0-delta (no occupancy model → the gate is a
    no-op). A frontend that models occupancy (FI is the reference) supplies a
    resolver returning the targeted slot's before-occupancy.
    """
    return None


# ``State`` is the per-op apply state the kernel threads. For the tree frontends
# (NO/SE/EE/EU/UK/FI) it is the frozen ``IRNode`` body; a future US lane would
# bind a section-text blob. The kernel never inspects ``State`` internals — it
# only hands it to the materializer and the metric.
State = TypeVar("State")

# A region named in some metric. ``frozenset[TreePath]`` for the IR-path metric;
# ``tuple[int, int]`` for a future char-span metric. The kernel treats it
# opaquely — only the metric interprets it.
Region = TypeVar("Region")


# ── §3.4 metric-agnostic boundary ────────────────────────────────────────────


class RegionMetric(Protocol[Region]):
    """Pluggable metric for naming/comparing the region an op touches (§3.4).

    The mutation-boundary invariant — *the op changed only its declared region,
    nothing outside it* — is abstract over the metric used to name a region. The
    IR-path metric (tree frontends) names a region as a set of ``TreePath``s; a
    future US char-span metric names it as a ``(start, end)`` span. ``apply_op``
    is parametric over this so US joins at char-span granularity with no kernel
    change (design §3.4 open detail).
    """

    def observed_region(self, before: object, after: object) -> Region:
        """The region the op actually edited (before → after)."""
        ...


@dataclass(frozen=True, slots=True)
class _IRPathMetric:
    """IR-path region metric: a region is the identity-pruned changed-path set.

    The tree-frontend instantiation of :class:`RegionMetric`. ``observed_region``
    is exactly the ``diff_ir_paths_identity_pruned`` the env-gated per-op probes
    (``_<j>_probe_op_mutation_boundary``) and FI's ``_collect_op_write_receipt``
    compute today — so a frontend migrating onto the seam reuses its existing
    diff, not a parallel one.
    """

    def observed_region(self, before: object, after: object) -> frozenset[TreePath]:
        assert isinstance(before, IRNode) and isinstance(after, IRNode), (
            "IR_PATH_METRIC.observed_region requires IRNode before/after state"
        )
        return frozenset(diff_ir_paths_identity_pruned(before, after))


#: The IR-path / tree-CoW region metric (design §3.4 ``IR_PATH_METRIC``). NO is
#: IR-path (design §3.4: "NO uses the IR-path metric").
IR_PATH_METRIC: _IRPathMetric = _IRPathMetric()


# ── Materializer: the per-op tree mutation (frontend-pluggable) ───────────────


@dataclass(frozen=True, slots=True)
class MaterializeResult(Generic[State]):
    """Result of one op's materialization (the frontend per-op tree dispatch).

    The materializer is the ONLY surface that mutates legal state, and it does so
    exclusively through ``core/tree_ops`` CoW (so the returned ``new_state`` is a
    new frozen tree sharing untouched subtrees by identity). It returns:

    * ``new_state`` — the post-op state (``is`` the input when the op was a
      no-op / skip);
    * ``findings`` — the per-op adjudications the frontend dispatch produced
      (skip reasons, recovery records, invariant violations), as the frontend's
      interop adjudication carrier (the kernel treats them opaquely and threads
      them onto :attr:`AppliedOp.findings`);
    * ``applied`` — whether the op landed a write (``True``) or was skipped
      (``False``); drives receipt emission (a skipped op emits no receipt — the
      conserved FilterResult's rejected lane carries the witness instead);
    * ``failure`` — set when the materializer wants the kernel's recovery
      mechanism to decide skip/rewrite/raise; ``None`` on the normal path
      (the frontend already handled its own recovery inline and reported it via
      ``findings``);
    * ``declared_recovery_prefixes`` — concrete parent paths a recovery lane
      INTENTIONALLY retargeted the write to (a missing-target REPLACE recovered
      by INSERT at a resolved parent / body root), so the boundary audit reads
      the landed write as an authorized within-boundary recovery rather than an
      unexplained escape. Mirrors NO's per-op ``_no_declared_recovery_paths``.
    """

    new_state: State
    findings: tuple[object, ...] = ()
    applied: bool = True
    failure: Optional["ApplyFailure"] = None
    declared_recovery_prefixes: tuple[TreePath, ...] = ()


# A Materializer is the frontend per-op tree dispatch: ``(before_state, op) ->
# MaterializeResult``. It encapsulates the jurisdiction-specific apply +
# inline-recovery logic (NO's REPLACE/INSERT/REPEAL/RENUMBER/text_replace
# dispatch with its sentence-materialization, container-chain, occupied-target
# recovery transforms). The kernel wraps it with the universal boundary /
# receipt / coverage machinery.
Materializer = Callable[[State, LegalOperation], MaterializeResult[State]]


# ── Recovery: the strict disposition mechanism (policy stays in the profile) ──


@dataclass(frozen=True, slots=True)
class ApplyFailure:
    """A materializer-signalled per-op failure handed to ``profile.recover``.

    Carries the op, a typed ``reason_code`` (the frontend's failure kind), a
    human ``message``, and the pre-failure ``state`` so a rewrite policy can
    re-derive. The kernel never raises on this by itself — ``profile.recover``
    decides the disposition (design §3.1 step 5).
    """

    op: LegalOperation
    reason_code: str
    message: str
    state: object


RecoveryAction = Literal["skip", "rewrite", "raise"]


@dataclass(frozen=True, slots=True)
class RecoveryDecision(Generic[State]):
    """The disposition ``profile.recover`` returns for an :class:`ApplyFailure`.

    * ``skip`` — drop the op; the state is unchanged (the frontend already
      recorded a skip finding). The conserved FilterResult's rejected lane is
      the witness.
    * ``rewrite`` — re-run with ``rewritten_op`` (a corrected op the policy
      supplies). The kernel re-materializes once with it.
    * ``raise`` — fail loud: the kernel raises :class:`ApplySeamRecoveryRaised`
      with the failure detail (the strict-mode block). Mirrors NO's ``strict_*``
      flags raising ``ValueError`` mid-fold.
    """

    action: RecoveryAction
    rewritten_op: Optional[LegalOperation] = None


def default_recover(failure: ApplyFailure) -> RecoveryDecision[State]:
    """Permissive default disposition: skip the failed op (record, don't block).

    Models FI's permissive (quirks) soft-fail ``APPLY_FAILED`` disposition and
    NO's default (all three strict flags off) "emit-and-continue" recovery. A
    frontend supplies a stricter ``recover`` to map specific failures onto
    ``raise`` (NO's ``strict_invariants`` / ``strict_action_family`` /
    ``strict_recovery``).
    """
    return RecoveryDecision(action="skip")


class ApplySeamRecoveryRaised(RuntimeError):
    """Raised when ``profile.recover`` returns ``action="raise"`` (strict block).

    Carries the originating :class:`ApplyFailure` so the strict caller (a NO
    ``strict_*`` lane) can diagnose. This is the kernel's fail-loud path — it is
    never swallowed inside ``apply_op``.
    """

    def __init__(self, failure: ApplyFailure) -> None:
        super().__init__(
            f"apply_op recovery raised for op {failure.op.op_id or '<no-id>'}: "
            f"{failure.reason_code}: {failure.message}"
        )
        self.failure = failure


BoundaryMode = Literal["off", "observe", "block"]


# ── Coverage delta (the §3.1 coverage_delta feeding §3.3) ─────────────────────


@dataclass(frozen=True, slots=True)
class CoverageDelta:
    """The coverage units one op claimed (design §3.1 ``coverage_delta``).

    A thin carrier of the :class:`~lawvm.core.coverage.CoverageClaim`s this op
    produced, accumulated by the apply loop into the ledger that feeds
    ``core/coverage_totality.assert_coverage_totality`` (§3.3). For a frontend
    with no op-level coverage today (NO), the delta is additive: each applied op
    contributes one claim on the unit it landed on. Empty for a skipped op.
    """

    claims: tuple[CoverageClaim, ...] = ()


# ── The apply profile + per-op result ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ApplyProfile(Generic[State]):
    """Per-jurisdiction apply policy fed to :func:`apply_op` (design §3.1).

    * ``jurisdiction`` — the frontend tag (stamped into receipts/findings).
    * ``materializer`` — the frontend per-op tree dispatch (§3.5; the only
      state-mutating surface). Required.
    * ``region_metric`` — the §3.4 metric; defaults to :data:`IR_PATH_METRIC`
      (NO is IR-path).
    * ``boundary_mode`` — the per-op mutation-boundary gate disposition:
      ``"off"`` (no audit — byte-identical to a frontend that never ran the
      probe), ``"observe"`` (run + emit non-blocking accounting findings), or
      ``"block"`` (run + emit blocking violation findings). Maps NO's
      ``LAWVM_NO_MUTATION_BOUNDARY_PER_OP`` env gate + its strict lanes onto a
      profile field (design §2.1 #3 "strictness = profile policy").
    * ``recover`` — the frontend recovery POLICY: maps an :class:`ApplyFailure`
      to a :class:`RecoveryDecision`. Defaults to :func:`default_recover`
      (skip). NO's three strict flags compose into this.
    * ``emit_receipts`` — whether to synthesize the per-op
      :class:`~lawvm.core.write_receipt.WriteReceipt`. Default ``True`` (the
      seam makes the receipt the OUTPUT of the universal apply step, design
      §2.1 #5); a frontend that wants the cheaper fold sets it ``False``.
    * ``emit_coverage`` — whether to synthesize the additive per-op
      :class:`CoverageDelta`. Default ``True``.
    * ``renumber_migration_rule_ids`` — the named migration rule that explains a
      RENUMBER's bound→landed divergence (NO's ``no_section_renumber_relabel``);
      stamped onto the receipt so ``WriteReceipt.divergence_explained`` holds
      for the relabel.
    * ``receipt_helper_prefix`` — the helper-string prefix stamped onto the
      synthesized :class:`~lawvm.core.write_receipt.WriteReceipt` (the
      ``helper`` field is ``f"{prefix}::{action}::{leaf_kind}"``). Defaults to
      ``None``, which yields the kernel-canonical ``f"{jurisdiction}::apply_op"``
      prefix. A frontend whose pre-existing receipt emitter used a different
      helper prefix (SE's ``apply_se_ops``, NO's ``apply_no_ops``) sets this so
      the seam-synthesized receipt is byte-identical to that emitter — the
      strangler byte-identity contract (the receipt helper is the only
      jurisdiction-named string in the receipt; everything else is computed from
      the IR diff + the op).
    """

    jurisdiction: str
    materializer: Materializer[State]
    region_metric: RegionMetric = IR_PATH_METRIC
    boundary_mode: BoundaryMode = "observe"
    recover: Callable[[ApplyFailure], RecoveryDecision[State]] = default_recover
    emit_receipts: bool = True
    emit_coverage: bool = True
    renumber_migration_rule_ids: tuple[str, ...] = ()
    receipt_helper_prefix: Optional[str] = None
    #: EV-05/FW-01/OV-01 ExecutionAuthorization OBSERVE gate resolver. Answers
    #: ``(op) -> ExecutionAuthorization | None`` per op; a mutating op whose
    #: resolver yields ``None`` (or an authorization with an empty
    #: ``authorization_rule_id``) emits the non-blocking
    #: ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` observation to the seam's
    #: separate :attr:`AppliedOp.observations` lane. Defaults to
    #: :func:`no_op_execution_authorization` (no op carries a proof today — the
    #: honest ~100% firewall-hole default). Universal: all 6 profiles inherit it.
    authorization_resolver: AuthorizationResolver = no_op_execution_authorization
    #: LS-03 occupancy-transition OBSERVE gate resolver. Answers ``(op,
    #: before_state, after_state) -> OccupancyClass | None``: the CURRENT
    #: (before) occupancy of the slot the op targets, or ``None`` when the
    #: profile does not model occupancy for this op. When the resolver yields a
    #: non-``None`` occupancy AND ``op.action`` maps to an occupancy-modelled
    #: action (REPLACE/INSERT/REPEAL via
    #: :data:`STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION`), the seam validates the
    #: (action, from-occupancy) transition against the core
    #: :data:`~lawvm.core.occupancy.VALID_TRANSITIONS` table; an invalid
    #: transition emits the non-blocking ``APPLY.OCCUPANCY_TRANSITION_OBSERVED``
    #: observation to the seam's separate :attr:`AppliedOp.observations` lane.
    #: Defaults to :func:`no_op_occupancy` (the kernel models no occupancy — the
    #: LS-03 guard-liveness hole; all 6 current profiles inherit it and are
    #: 0-delta). FI is the reference for the table semantics; the kernel does
    #: NOT import ``finland/``.
    occupancy_resolver: OccupancyResolver = no_op_occupancy


@dataclass(frozen=True, slots=True)
class AppliedOp(Generic[State]):
    """The result of one :func:`apply_op` call (design §3.1).

    * ``new_state`` — the post-op state (``is`` the input when the op was
      skipped / a no-op).
    * ``write_receipt`` — the per-op landed-write receipt, or ``None`` when the
      op landed no write or ``profile.emit_receipts`` is ``False``.
    * ``findings`` — the materializer's per-op findings PLUS any boundary-gate
      findings, in that order.
    * ``coverage_delta`` — the units this op claimed (additive; empty for a
      skip).
    * ``applied`` — whether the op landed a write.
    * ``declared_recovery_prefixes`` — the concrete parent paths the
      materializer's recovery lane INTENTIONALLY retargeted the write to (passed
      through verbatim from :attr:`MaterializeResult.declared_recovery_prefixes`).
      A frontend that runs its OWN per-op mutation-boundary probe in the fold
      (``boundary_mode="off"``, the single-producer pattern NO/SE/EE use) reads
      this to declare the authorized recovery retarget to that probe, so the
      seam does not have to be the boundary-audit producer for the prefixes to
      survive the materialize→audit handoff. ``()`` when the materializer
      declared none (the common case).
    """

    new_state: State
    write_receipt: Optional[WriteReceipt]
    findings: tuple[object, ...]
    coverage_delta: CoverageDelta
    applied: bool
    declared_recovery_prefixes: tuple[TreePath, ...] = ()
    #: The SEPARATE observe lane (B-enforcement increments 1+2+3). Carries the
    #: universal apply-seam OBSERVE-mode findings: (1) the
    #: ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` firewall-hole witness emitted
    #: per mutating op lacking an ExecutionAuthorization proof (inc 1); (2)
    #: the always-on ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` per-op
    #: mutation-boundary witness (LS-01, inc 2) the seam now emits for EVERY
    #: landed write under a ``boundary_mode="off"`` tree profile — the single
    #: always-on boundary producer that replaces the per-frontend in-fold
    #: env-probes; and (3) the ``APPLY.OCCUPANCY_TRANSITION_OBSERVED`` LS-03
    #: guard-liveness witness (inc 3) emitted per mutating op whose (action,
    #: from-occupancy) pair is an invalid occupancy transition, when the profile
    #: supplies an ``occupancy_resolver`` (the kernel-default resolver models no
    #: occupancy → 0-delta for all 6 current profiles). These are ADDITIVE
    #: evidence (role=observation, non-blocking) and are routed here, NEVER into
    #: :attr:`findings`, so the production findings/adjudication multiset the
    #: byte-identity gates assert on is UNCHANGED (EV-04: an observation explains,
    #: it never becomes authority). Empty when the op landed no write (or, for
    #: the authorization witness, carried a resolvable authorization; for the
    #: boundary witness, stayed within its declared mutation boundary; for the
    #: occupancy witness, made a valid transition or the profile models no
    #: occupancy).
    observations: tuple[Finding, ...] = ()


def apply_op(
    base_state: State,
    typed_op: LegalOperation,
    *,
    provenance: Optional[OperationSource],
    profile: ApplyProfile[State],
    source_statute: str = "",
) -> AppliedOp[State]:
    """Apply one fully-resolved typed op through the universal per-op kernel.

    Runs the design §3.1 stages: materialize via ``profile.materializer`` (which
    resolves the declared region and mutates via ``core/tree_ops`` CoW), compute
    the observed region + synthesize the receipt, run the mutation-boundary gate
    under ``profile.boundary_mode``, and on a materializer-signalled failure call
    ``profile.recover`` for the skip/rewrite/raise disposition.

    ``provenance`` is the op's :class:`~lawvm.core.ir.OperationSource` (carried
    for the receipt/finding provenance; the affecting act). ``source_statute`` is
    the base statute id stamped into the boundary findings.

    Returns an :class:`AppliedOp`. The kernel never mutates ``base_state`` in
    place; the only mutation is the materializer's CoW. Deterministic: the
    output is a pure function of the inputs.
    """
    result = profile.materializer(base_state, typed_op)

    # Recovery disposition (design §3.1 step 5). The materializer signals a
    # failure only when it wants the kernel's mechanism to decide; the common
    # case (frontend handled its own inline recovery and reported it via
    # findings) leaves ``failure is None`` and the kernel proceeds.
    if result.failure is not None:
        decision = profile.recover(result.failure)
        if decision.action == "raise":
            raise ApplySeamRecoveryRaised(result.failure)
        if decision.action == "rewrite" and decision.rewritten_op is not None:
            # One bounded re-materialization with the corrected op. A rewrite
            # that itself fails is surfaced (no infinite retry): its findings
            # flow through, but its failure is not re-recovered.
            result = profile.materializer(base_state, decision.rewritten_op)
            typed_op = decision.rewritten_op
        # action == "skip" falls through: the op is dropped, state unchanged,
        # the materializer's skip finding is the witness.

    new_state = result.new_state
    findings: list[object] = list(result.findings)

    landed = result.applied and new_state is not base_state

    write_receipt: Optional[WriteReceipt] = None
    coverage_delta = CoverageDelta()
    observations: tuple[Finding, ...] = ()

    if landed:
        # ── EV-05/FW-01/OV-01 ExecutionAuthorization OBSERVE gate (universal). ──
        # A landed write is a state mutation; the firewall contract (§2.10) says
        # it must carry an ExecutionAuthorization proof (rule_id + required
        # proofs). The apply path never checked this before. We check it here for
        # ALL 6 frontends, OBSERVE-first: a mutating op with no resolvable proof
        # emits a non-blocking observation to the SEPARATE ``observations`` lane,
        # never to ``findings`` — so the production findings multiset the byte-
        # identity gates assert on is unchanged. Non-blocking, additive evidence.
        observations = _execution_authorization_observe(
            typed_op, profile=profile, source_statute=source_statute
        )

        # ── LS-03 occupancy-transition OBSERVE gate (universal). ────────────
        # A landed write is a slot occupancy transition; the occupancy table
        # (``core/occupancy.VALID_TRANSITIONS``) names which (action,
        # from-occupancy) pairs are valid. The apply path never enforced this
        # outside FI (the audit-registry LS-03 guard-liveness hole). When a
        # profile supplies an ``occupancy_resolver`` (FI is the reference), the
        # seam validates the transition and emits a non-blocking observation to
        # the SEPARATE ``observations`` lane on an invalid one — never to
        # ``findings``. Default-resolver profiles (all 6 today) model no
        # occupancy → no-op → 0-delta. Non-blocking, additive evidence.
        observations = (
            *observations,
            *_occupancy_transition_observe(
                typed_op,
                base_state,
                new_state,
                profile=profile,
                source_statute=source_statute,
            ),
        )

    if landed:
        if profile.emit_receipts:
            write_receipt = _synthesize_receipt(
                base_state,
                new_state,
                typed_op,
                profile=profile,
            )
        if profile.emit_coverage:
            coverage_delta = _coverage_delta_for_op(typed_op, profile=profile)

        # ── Per-op mutation-boundary gate (design §3.1 step 4; §3.4). ────────
        # The block FI's ``_enforce_per_op_apply_authority`` runs at the apply
        # site, hoisted to the kernel. ``observe`` emits a non-blocking
        # accounting finding into the PRODUCTION ``findings`` lane; ``block``
        # emits a blocking violation there. The ``isinstance`` guard narrows
        # ``State`` to ``IRNode`` (the tree metric's state) so the core audit's
        # ``IRNode`` contract is satisfied without an unchecked cast; under any
        # other metric the gate is skipped (a char-span lane carries its own
        # §3.4 boundary instantiation — US audits char-span in
        # ``us_federal/apply_profile``).
        on_tree_metric = (
            _is_tree_metric(profile.region_metric)
            and isinstance(base_state, IRNode)
            and isinstance(new_state, IRNode)
        )
        if profile.boundary_mode != "off" and on_tree_metric:
            audit = audit_op_mutation_boundary(
                base_state,
                new_state,
                typed_op,
                op_id=typed_op.op_id or "",
                source_statute=source_statute,
                is_strict=(profile.boundary_mode == "block"),
                declared_recovery_prefixes=result.declared_recovery_prefixes,
            )
            findings.extend(audit.findings)
        elif profile.boundary_mode == "off" and on_tree_metric:
            # ── B-enforcement increment 2 (LS-01): the UNIVERSAL always-on
            # mutation-boundary observer. ``boundary_mode="off"`` means the
            # profile does NOT want the boundary in its PRODUCTION ``findings``
            # (byte-identity), but the seam still runs the SAME core audit
            # (``is_strict=False``) on every landed write and routes the
            # resulting ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation to
            # the SEPARATE ``observations`` lane — NEVER to ``findings``. This
            # makes the seam the single always-on LS-01 producer for all tree
            # frontends — the universal observer the per-frontend in-fold
            # env-probes (NO/SE/EE/UK) duplicate as an env-gated adjudication
            # surface — while the production findings/adjudication multiset the
            # byte-identity gates assert on stays UNCHANGED (EV-04: observation
            # is not authority; design §5 observe-first). The probes and this
            # observer call the IDENTICAL core producer, so there is no drift
            # (proven in tests/test_apply_seam_boundary_unification.py).
            # ``declared_recovery_prefixes`` are threaded so an authorized
            # recovery retarget reads as within-boundary, exactly as the probes do.
            boundary_audit = audit_op_mutation_boundary(
                base_state,
                new_state,
                typed_op,
                op_id=typed_op.op_id or "",
                source_statute=source_statute,
                is_strict=False,
                declared_recovery_prefixes=result.declared_recovery_prefixes,
            )
            observations = (*observations, *boundary_audit.findings)

    return AppliedOp(
        new_state=new_state,
        write_receipt=write_receipt,
        findings=tuple(findings),
        coverage_delta=coverage_delta,
        applied=landed,
        # Pass the materializer's recovery-retarget prefixes through verbatim so
        # a frontend running its own fold-side boundary probe (``boundary_mode=
        # "off"``) can declare the authorized retarget without the seam being the
        # audit producer. Empty when the materializer declared none.
        declared_recovery_prefixes=result.declared_recovery_prefixes,
        # The SEPARATE observe lane: the universal ExecutionAuthorization
        # firewall-hole witness, ADDITIVE and never folded into ``findings``.
        observations=observations,
    )


def _is_tree_metric(metric: RegionMetric) -> bool:
    """True when the profile uses the IR-path/tree metric (boundary-audit-ready).

    The core ``audit_op_mutation_boundary`` operates on ``IRNode`` before/after
    snapshots, so the boundary gate runs only under the tree metric. A future
    char-span metric carries its own boundary instantiation (§3.4) and is not
    routed here.
    """
    return isinstance(metric, _IRPathMetric)


# ── EV-05/FW-01/OV-01 ExecutionAuthorization OBSERVE gate ─────────────────────


def _execution_authorization_observe(
    op: LegalOperation,
    *,
    profile: ApplyProfile[State],
    source_statute: str,
) -> tuple[Finding, ...]:
    """Observe whether a landed (mutating) op carries an ExecutionAuthorization.

    The universal, metric-agnostic ExecutionAuthorization closure (EV-05/FW-01/
    OV-01) hoisted to the kernel from FI's per-frontend
    ``_gate_execution_authorization_at_op``. ``profile.authorization_resolver``
    answers ``(op) -> ExecutionAuthorization | None``; an op that resolves an
    authorization with a non-empty ``authorization_rule_id`` met the closure and
    emits nothing. Otherwise — the ~100% common case today, because no op carries
    a proof — one non-blocking ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED``
    observation witnesses the firewall hole.

    The returned findings are role=observation, ``blocking=False``, and are
    routed to the SEPARATE :attr:`AppliedOp.observations` lane by the caller —
    NEVER to :attr:`AppliedOp.findings`. This is the byte-identity mechanism: the
    production findings/adjudication multiset the seam gates assert on is
    untouched, while the firewall hole becomes visible and gated (design §5
    observe-first; EV-04 observation-is-not-authority).
    """
    authorization = profile.authorization_resolver(op)
    if authorization is not None and authorization.authorization_rule_id:
        # The op resolves an execution-authorization rule; closure met, no
        # observation. (Increment 2 promotes the no-proof case to the strict
        # block ``EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED`` per profile.)
        return ()
    return (
        Finding(
            kind=REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
            role="observation",
            stage="apply",
            blocking=False,
            source_statute=source_statute,
            detail={
                "message": (
                    "A state-mutating op landed through the universal apply seam "
                    "without resolving an ExecutionAuthorization (no rule_id + "
                    "required proofs). Surfaced as a non-blocking firewall-hole "
                    "witness; not promoted to authority."
                ),
                "op_id": op.op_id or "",
                "jurisdiction": profile.jurisdiction,
                "action": op.action.value if op.action else "",
                "owner": "apply_seam_execution_authorization_observe",
            },
        ),
    )


# ── LS-03 occupancy-transition OBSERVE gate ───────────────────────────────────


def _occupancy_transition_observe(
    op: LegalOperation,
    before_state: State,
    after_state: State,
    *,
    profile: ApplyProfile[State],
    source_statute: str,
) -> tuple[Finding, ...]:
    """Observe whether a landed op made a VALID occupancy transition (LS-03).

    The universal occupancy-transition closure hoisted to the kernel from FI's
    per-frontend ``_gate_occupancy_transition_at_op``. The gate runs only when
    the profile supplies an ``occupancy_resolver`` that yields a non-``None``
    before-occupancy for the op's targeted slot AND ``op.action`` maps to an
    occupancy-modelled action (REPLACE/INSERT/REPEAL — the same three FI's
    ``_OP_TYPE_TO_ACTION`` maps). For every other op — no occupancy model (the
    default ``no_op_occupancy`` → ``None``, the 0-delta production case), or an
    action with no occupancy meaning (RENUMBER/META/TEXT_*/HEADING_REPLACE) — the
    gate is a no-op, mirroring FI's per-op skip conditions.

    When it runs, the seam validates the (action, from-occupancy) transition
    against the core ``VALID_TRANSITIONS`` table via the SAME
    ``validate_transition`` helper FI calls — so the kernel and FI's occupancy
    gate cannot drift, and the kernel does not import ``finland/``. A VALID
    transition emits nothing (observe-first: only the invalid case is the
    witness, matching FI's strict-block-on-invalid). An INVALID transition emits
    one non-blocking ``APPLY.OCCUPANCY_TRANSITION_OBSERVED`` observation.

    The returned findings are role=observation, ``blocking=False``, and are
    routed to the SEPARATE :attr:`AppliedOp.observations` lane by the caller —
    NEVER to :attr:`AppliedOp.findings`. Byte-identity mechanism: the production
    findings/adjudication multiset is untouched while the LS-03 guard-liveness
    hole becomes universally observable (design §5 observe-first; EV-04
    observation-is-not-authority).
    """
    occupancy_action = (
        STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION.get(op.action.value)
        if op.action is not None
        else None
    )
    if occupancy_action is None:
        # The op's action carries no whole-slot occupancy meaning (RENUMBER /
        # META / TEXT_* / HEADING_REPLACE); FI's gate skips it the same way.
        return ()
    current = profile.occupancy_resolver(op, before_state, after_state)
    if current is None:
        # The profile models no occupancy for this op (the default kernel
        # resolver, or a frontend whose slot is not whole-unit-addressable): the
        # gate is a no-op — exactly FI's per-op skip. 0-delta for all 6 profiles.
        return ()
    try:
        validate_transition(occupancy_action, current)
    except InvalidOccupancyTransition as exc:
        return (
            Finding(
                kind=OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE,
                role="observation",
                stage="apply",
                blocking=False,
                source_statute=source_statute,
                detail={
                    "message": (
                        "A state-mutating op landed through the universal apply "
                        "seam on an invalid (action, occupancy) transition (LS-03). "
                        "Surfaced as a non-blocking guard-liveness witness; not "
                        "promoted to authority."
                    ),
                    "op_id": op.op_id or "",
                    "jurisdiction": profile.jurisdiction,
                    "action": occupancy_action.value,
                    "current_occupancy": current.value,
                    "transition_error": str(exc),
                    "owner": "apply_seam_occupancy_transition_observe",
                },
            ),
        )
    # Valid transition: closure met, nothing observed.
    return ()


# ── Receipt synthesis (generalizes NO's ``_no_emit_one_op_receipt``, which
# mirrors FI's ``_collect_op_write_receipt``). ────────────────────────────────


def _legal_path_to_tree_path(addr: object) -> TreePath:
    """Coerce a ``LegalAddress`` path into the core ``TreePath`` shape.

    ``LegalAddress.path`` is a tuple of ``(kind, label | None)`` pairs; the core
    ``TreePath`` requires ``str`` labels (empty string for None). Mirrors
    ``norway/grafter._no_legal_path_to_tree_path`` /
    ``sweden/grafter._se_legal_path_to_tree_path`` so the seam's receipt path
    shape matches the frontends' verbatim.
    """
    path = getattr(addr, "path", ())
    return tuple((str(kind), str(label or "")) for kind, label in path)


def _resolve_or_find(body: IRNode, path: TreePath) -> Optional[IRNode]:
    """Resolve ``path`` against ``body``; fall back to a depth-search by label.

    Mirrors the NO/SE receipt hash resolution: a single-segment landed path
    sourced from the op's declared address may not resolve directly against a
    body where the section lives nested under a chapter; the recursive
    label-find recovers it (the production-lane case). Local re-expression of the
    frontend helper so the seam does not reach into ``norway/grafter``.
    """
    from lawvm.core import tree_ops

    node = tree_ops.resolve(body, list(path))
    if node is None and len(path) == 1:
        kind, label = path[0]
        if label:
            find_path = tree_ops.find(body, str(kind), str(label))
            if find_path is not None:
                node = tree_ops.resolve(body, list(find_path))
    return node


def _synthesize_receipt(
    before_state: State,
    after_state: State,
    op: LegalOperation,
    *,
    profile: ApplyProfile[State],
) -> Optional[WriteReceipt]:
    """Synthesize the per-op :class:`WriteReceipt` from the landed IR diff.

    Generalizes ``norway/grafter._no_emit_one_op_receipt`` (which mirrors FI's
    ``apply_resolved_op._collect_op_write_receipt`` and SE's
    ``_se_emit_one_op_receipt``). The footprint is categorized by
    ``op.action.value`` — REPLACE/text_replace → ``replaced_paths``; INSERT →
    ``created_paths``; REPEAL → ``removed_paths``; RENUMBER → ``renumbered_paths``
    (bound→landed (from, to) pair). pre/post structural subtree hashes are taken
    at the landed primary path's covering region.

    A RENUMBER's bound→landed divergence is the typed named migration for a
    relabel; ``profile.renumber_migration_rule_ids`` (NO's
    ``no_section_renumber_relabel``) owns it so ``divergence_explained`` holds.

    Only callable with the IR-path metric (the receipt path fields are the
    IR-metric view; a char-span lane would carry a span view, §3.4 open detail).
    Returns ``None`` only when the op landed no diff (a skip the caller already
    excluded; defensive).
    """
    assert isinstance(before_state, IRNode) and isinstance(after_state, IRNode), (
        "_synthesize_receipt requires the IR-path metric (IRNode before/after state)"
    )
    before_body: IRNode = before_state
    after_body: IRNode = after_state

    changed = diff_ir_paths_identity_pruned(before_body, after_body)
    if not changed:
        return None

    action_value = op.action.value if op.action else "unknown"
    leaf_kind = op.target.leaf_kind() or "unknown"
    helper_prefix = (
        profile.receipt_helper_prefix
        if profile.receipt_helper_prefix is not None
        else f"{profile.jurisdiction}::apply_op"
    )
    helper = f"{helper_prefix}::{action_value}::{leaf_kind}"
    bound_target_path = _legal_path_to_tree_path(op.target)

    landed_primary_path: TreePath | None
    if action_value in {"insert", "repeal", "replace", "text_replace"}:
        landed_primary_path = bound_target_path or None
    elif action_value == "renumber":
        landed_primary_path = (
            _legal_path_to_tree_path(op.destination)
            if op.destination is not None
            else None
        ) or None
    else:
        landed_primary_path = changed[0] if changed else None

    created_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    renumbered_paths: tuple[tuple[TreePath, TreePath], ...] = ()

    if action_value in {"replace", "text_replace"}:
        replaced_paths = changed
    elif action_value == "insert":
        created_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "repeal":
        removed_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "renumber" and op.destination is not None:
        destination_path = _legal_path_to_tree_path(op.destination)
        renumbered_paths = ((bound_target_path, destination_path),)

    migration_rule_ids: tuple[str, ...] = ()
    if action_value == "renumber" and op.destination is not None:
        migration_rule_ids = profile.renumber_migration_rule_ids

    pre_hashes: dict[str, str] = {}
    post_hashes: dict[str, str] = {}
    if landed_primary_path:
        key = receipt_address_string(landed_primary_path)
        before_node = _resolve_or_find(before_body, landed_primary_path)
        after_node = _resolve_or_find(after_body, landed_primary_path)
        pre_hashes[key] = (
            structural_subtree_hash(before_node) if before_node is not None else ""
        )
        post_hashes[key] = (
            structural_subtree_hash(after_node) if after_node is not None else ""
        )

    return WriteReceipt(
        op_id=op.op_id or "",
        helper=helper,
        action=action_value,
        bound_target_path=bound_target_path,
        landed_primary_path=landed_primary_path,
        created_paths=created_paths,
        replaced_paths=replaced_paths,
        removed_paths=removed_paths,
        renumbered_paths=renumbered_paths,
        migration_rule_ids=migration_rule_ids,
        pre_hashes=pre_hashes,
        post_hashes=post_hashes,
    )


# ── Coverage delta synthesis (additive; feeds §3.3) ───────────────────────────


def _coverage_delta_for_op(
    op: LegalOperation,
    *,
    profile: ApplyProfile[State],
) -> CoverageDelta:
    """One ``explicit`` coverage claim on the unit an applied op landed on.

    The additive op-level coverage NO lacks today (design §4.1 / §3.3): an
    applied op explicitly covers the unit named by its (destination for a
    RENUMBER, else target) leaf. The claim's ``covered_unit_ids`` uses the
    ``<kind>_<label>`` chapter-free id so it matches
    ``coverage_totality._unit_is_covered`` (which keys on ``<kind>_<label>``).
    Empty when the op carries no labelled leaf to claim (the kernel never
    fabricates a claim on an unidentifiable unit — §0).
    """
    addr = (
        op.destination
        if (op.action and op.action.value == "renumber" and op.destination is not None)
        else op.target
    )
    leaf_kind = addr.leaf_kind()
    leaf_label = addr.leaf_label()
    if not leaf_kind or not leaf_label:
        return CoverageDelta()
    unit_id = f"{leaf_kind}_{leaf_label}"
    claim = CoverageClaim(
        claim_kind="explicit",
        target=op.target,
        covered_unit_ids=frozenset({unit_id}),
        evidence=(
            f"op_id={op.op_id or ''}",
            f"action={op.action.value if op.action else ''}",
        ),
    )
    return CoverageDelta(claims=(claim,))
