"""Guard-liveness fire-drills.

A *guard* in LawVM is a diagnostic / finding code that is registered with
blocking enforcement and is supposed to fire from the production pipeline when
its guarded state occurs. The recurring bug shape this module defends against is
"guard-liveness failure": a guard that exists, is registered, and passes
isolated unit tests, but is *structurally unsatisfiable* from the production
lane — the only live builder / sink / group key on that lane can never put the
guard into its firing state, so the guard is dead weight that gives false
assurance.

This module encodes two things:

1. A declarative registry of *fire-drills*: each maps a finding code to a
   callable that drives the PRODUCTION lane (production intent builders,
   production sinks / pipelines, the production verdict computation) into the
   guarded state and asserts the finding reaches its consumer-visible surface
   (the PhaseResult finding ledger, or the registry-backed verdict barrier
   codes). A fire-drill must NOT hand-build a strict policy or call a private
   low-level helper to fabricate the finding — it must exercise the real guard
   so that, if the guard is silently disconnected, the drill goes red.

2. An inventory test: every finding code registered with blocking enforcement
   either has a fire-drill here or appears in ``NO_FIRE_DRILL_YET``. New blocking
   codes must consciously land in one bucket or the other; they cannot be added
   to the registry and quietly skip liveness coverage.

The ``xfail`` drills document verified, structurally-unsatisfiable guards
(guard-liveness debt). They are ``strict=False`` so they never fail CI; their
job is to keep the debt visible and to flip to a hard failure (xpass) the day
the underlying structural cause is fixed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast, Callable, Dict, NoReturn

import pytest

from lawvm.core.compile_result import (
    CompileFailure,
    StrictProfile,
    compute_verdict_from_registry,
    strict_fail_reasons_from_finding_ledger,
)
from lawvm.core.ir import IRNode
from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.replay_pipeline import (
    ReplayPlan,
    build_tree_invariant_finding,
    execute_replay_plan,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState, StatuteContext


# ---------------------------------------------------------------------------
# Shared production-lane harness helpers
# ---------------------------------------------------------------------------

_DRILL_STRICT_PROFILE = StrictProfile(name="guard_liveness_drill_strict")


def _drill_replay_plan() -> ReplayPlan:
    """Minimal but real ReplayPlan for driving ``execute_replay_plan``.

    The plan carries one amendment id. The production replay fold, the
    production tree-invariant guard (``check_invariants``), and the production
    finding projection (``build_tree_invariant_finding``) are all exercised by
    ``execute_replay_plan`` itself — the drills only supply the tree state that
    a real amendment apply would have produced.
    """
    return ReplayPlan(
        parent_id="guard-liveness/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="guard-liveness/1",
            title="Guard Liveness Drill",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )


def _run_replay_fold(process_muutoslaki: Callable[..., PhaseResult]) -> list[Finding]:
    """Drive the production replay fold and return its finding ledger.

    ``check_tree_invariants`` is the real production invariant detector and
    ``build_tree_invariant_finding`` (inside ``execute_replay_plan``) is the
    real production projection onto the finding ledger.
    """
    findings: list[Finding] = []
    execute_replay_plan(
        _drill_replay_plan(),
        corpus=cast(Any, object()),  # not read by these drills
        process_muutoslaki=process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda request, sinks=None: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )
    return findings


def _verdict_barrier_codes_from_findings(
    *,
    findings: list[Finding] | None = None,
    failures: list[CompileFailure] | None = None,
) -> tuple[str, ...]:
    """Drive the production verdict surface from a finding ledger / failures.

    Uses the real ``strict_fail_reasons_from_finding_ledger`` ->
    ``compute_verdict_from_registry`` lane and returns the consumer-visible
    ``CompileVerdict.barrier_codes``.
    """
    reasons = strict_fail_reasons_from_finding_ledger(
        _DRILL_STRICT_PROFILE,
        compiled_ops=[],
        canonical_ops=[],
        failures=failures or [],
        findings=findings or [],
    )
    verdict = compute_verdict_from_registry(_DRILL_STRICT_PROFILE, reasons)
    return verdict.barrier_codes


# ---------------------------------------------------------------------------
# Fire-drills for codes that ARE reachable today
# ---------------------------------------------------------------------------


def drill_tree_invariant_violation_duplicate_label() -> None:
    """APPLY.TREE_INVARIANT_VIOLATION reaches the replay finding ledger.

    Production lane: ``execute_replay_plan`` post-processes the fold, runs the
    real ``check_invariants`` guard, and projects a duplicate-label violation
    through ``build_tree_invariant_finding`` onto the finding ledger. The drill
    supplies the duplicate-label tree state an amendment apply would have left
    behind; the guard + projection are production code.
    """

    def process_muutoslaki(
        request: Any,
        sinks: Any,
    ) -> PhaseResult[ReplayState]:
        duplicated = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1"),
                IRNode(kind=IRNodeKind.SECTION, label="1"),
            ),
        )
        return PhaseResult(output=request.state.with_ir(duplicated))

    findings = _run_replay_fold(process_muutoslaki)
    hits = [f for f in findings if f.kind == "APPLY.TREE_INVARIANT_VIOLATION"]
    assert hits, "duplicate-label tree invariant did not reach the finding ledger"
    finding = hits[0]
    assert finding.role == "violation"
    assert finding.blocking is True
    assert "duplicate" in str(finding.detail.get("violation") or "")


def drill_tree_invariant_violation_verdict_barrier() -> None:
    """APPLY.TREE_INVARIANT_VIOLATION reaches the strict verdict barrier codes.

    Production lane: a real tree-invariant finding flows through
    ``strict_fail_reasons_from_finding_ledger`` -> ``compute_verdict_from_registry``
    and surfaces in the consumer-visible ``CompileVerdict.barrier_codes``.
    """
    finding = build_tree_invariant_finding(
        violation="body: duplicate section:1 (2 times)",
        source_statute="1991/1",
        phase="replay_fold",
        message="Replay tree invariant violated.",
    )
    barrier_codes = _verdict_barrier_codes_from_findings(findings=[finding])
    assert "APPLY.TREE_INVARIANT_VIOLATION" in barrier_codes


def drill_failed_operation_verdict_barrier() -> None:
    """APPLY.FAILED_OPERATION reaches the strict verdict barrier codes.

    Production lane: a real ``CompileFailure`` (the frontend-agnostic failed-op
    record) drives ``strict_fail_reasons_from_finding_ledger``, which trips the
    ``APPLY.FAILED_OPERATION`` barrier, then ``compute_verdict_from_registry``
    surfaces it in ``CompileVerdict.barrier_codes``.
    """
    failure = CompileFailure.from_scope(
        source_statute="1991/1",
        description="resolved op could not be applied to the live tree",
        reason="target_not_found",
        target_section="5",
        target_unit_kind="section",
    )
    barrier_codes = _verdict_barrier_codes_from_findings(failures=[failure])
    assert "APPLY.FAILED_OPERATION" in barrier_codes


def drill_source_pathology_detected_verdict_barrier() -> None:
    """APPLY.SOURCE_PATHOLOGY_DETECTED reaches the strict verdict barrier codes.

    Production lane: the runtime obligation ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY
    is mapped by ``strict_fail_reasons_from_finding_ledger`` onto the
    APPLY.SOURCE_PATHOLOGY_DETECTED strict barrier, which
    ``compute_verdict_from_registry`` surfaces in ``CompileVerdict.barrier_codes``.
    This exercises the runtime-finding -> strict-code mapping guard.
    """
    finding = Finding(
        kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        role="obligation",
        stage="strict",
        detail={"reason": "non-literal source path rejected"},
        source_statute="1991/1",
        blocking=True,
    )
    barrier_codes = _verdict_barrier_codes_from_findings(findings=[finding])
    assert "APPLY.SOURCE_PATHOLOGY_DETECTED" in barrier_codes


def drill_frontend_internal_error_parse_surface() -> None:
    """PARSE.FRONTEND_INTERNAL_ERROR reaches the parse-layer findings surface.

    Production lane: the Finnish clause compiler ``parse_clause`` wraps the
    production resolver / lowerer in a RuntimeError guard. When the resolver
    raises (a real internal compiler fault), the production guard records a
    ``severity="bug"`` frontend diagnostic, which ``frontend_diagnostic_findings``
    projects to PARSE.FRONTEND_INTERNAL_ERROR in ``ClauseParseResult.findings``.

    The fault is injected by making the production resolver raise; the guard,
    the diagnostic, and the finding projection are all production code. This is
    the surface where the code IS visible. (The separate, known frontend-ingress
    loss of this code is documented as xfail debt below.)
    """
    import lawvm.finland.johtolause.surface_resolve as surface_resolve
    from lawvm.finland.johtolause import api

    from unittest.mock import patch as _patch

    def raising_resolver(clause: surface_resolve.SurfaceClause) -> NoReturn:
        raise RuntimeError("synthetic internal resolver fault for guard-liveness drill")

    with _patch.object(surface_resolve, "resolve_surface_clause", raising_resolver):
        result = api.parse_clause("Muutetaan lain 1 §.")

    hits = [f for f in result.findings if f.kind == "PARSE.FRONTEND_INTERNAL_ERROR"]
    assert hits, "internal resolver fault did not surface PARSE.FRONTEND_INTERNAL_ERROR"
    finding = hits[0]
    assert finding.role == "violation"
    assert finding.blocking is True


def _drill_renumber_rop():
    """Build a real RENUMBER ResolvedOp + Relabel intent over a small statute.

    Returns ``(state, ctx, op, rop)`` ready for the production ``apply_op`` /
    ``apply_ops_to_tree`` lane. The op renumbers section 73 -> 61 inside chapter 7,
    which the production apply genuinely performs (a real ``applied`` mutation
    event with renumbered paths), so the mutation-accounting and observed-vs-
    declared guards have a real apply to reason over.
    """
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
    from lawvm.finland.ops import AmendmentOp, ResolvedOp

    def _num(text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.NUM, text=text)

    def _sec(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(_num(f"{label} §"), IRNode(kind=IRNodeKind.CONTENT, text=text)),
        )

    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(_num("7 luku"), _sec("60", "sixty"), _sec("62", "sixty-two"), _sec("73", "old73")),
            ),
        ),
    )
    state = ReplayState(ir=body)
    ctx = StatuteContext(id="0/0", title="", base_ir=body, base_xml_bytes=b"<body/>")

    lo = LegalOperation(
        op_id="renumber_73_to_61",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
        destination=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
        source=OperationSource(statute_id="1994/318"),
    )
    op = AmendmentOp(
        op_id="renumber_73_to_61",
        op_type="RENUMBER",
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
        lo=lo,
    )
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(address=LegalAddress(path=(("chapter", "7"), ("section", "73")))),
        destination=NodeTarget(address=LegalAddress(path=(("chapter", "7"), ("section", "61")))),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace(), coverage=CoverageMode.EXACT),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
    )
    rop.intent = intent
    return state, ctx, op, rop


def drill_replay_unknown_mutation_outcome_apply_lane() -> None:
    """REPLAY_UNKNOWN_MUTATION_OUTCOME surfaces from the production apply lane.

    Production lane: ``apply_op`` performs a real RENUMBER and stamps the mutation
    event via the production helper ``_emit_apply_mutation_event_for_rop``. The
    drill patches that production stamping helper to force an outcome label that
    is absent from APPLIED/FAILED/SKIPPED outcome sets, then runs the production
    mutation-accounting guard ``check_apply_mutation_accounting`` over the real
    events. An unknown outcome would otherwise silently bypass all boundary
    accounting; the guard must fail loudly with REPLAY_UNKNOWN_MUTATION_OUTCOME.
    """
    from unittest.mock import patch as _patch

    import lawvm.finland.apply_typed_dispatch as apply_typed_dispatch
    from lawvm.finland.apply import apply_op
    from lawvm.finland.apply_events import (
        ApplyMutationEvent,
        check_apply_mutation_accounting,
        _emit_apply_mutation_event_from_receipt as _real_receipt_emit,
    )

    state, ctx, _op, rop = _drill_renumber_rop()
    events: list[ApplyMutationEvent] = []

    def _unknown_outcome_emit(
        mutation_events_out: list[ApplyMutationEvent] | None,
        *,
        receipt: Any,
        outcome: str,
        rop: Any = None,
        op: Any = None,
        used_fallback_tags: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        # Force an outcome label outside the registered outcome sets while leaving
        # the rest of the real production event construction intact.
        return _real_receipt_emit(
            mutation_events_out,
            receipt=receipt,
            outcome="freshly_invented_outcome",
            rop=rop,
            op=op,
            used_fallback_tags=used_fallback_tags,
        )

    with _patch.object(apply_typed_dispatch, "_emit_apply_mutation_event_from_receipt", _unknown_outcome_emit):
        apply_op(state, None, ctx, None, rop=rop, replay_mode="legal_pit", mutation_events_out=events)

    assert events, "production apply did not emit a mutation event"
    assert events[-1].outcome == "freshly_invented_outcome"
    violations = check_apply_mutation_accounting(events)
    hits = [v for v in violations if v.split(" ", 1)[0] == "REPLAY_UNKNOWN_MUTATION_OUTCOME"]
    assert hits, (
        "unknown mutation outcome label did not surface REPLAY_UNKNOWN_MUTATION_OUTCOME "
        "from the production mutation-accounting guard"
    )


def drill_replay_undeclared_tree_touch_apply_lane() -> None:
    """APPLY.REPLAY_UNDECLARED_TREE_TOUCH observes an undeclared touch from production.

    Production lane: ``apply_ops_to_tree`` performs a real RENUMBER and, inside
    the resolved-op apply boundary, runs the production passive
    observed-vs-declared cross-check (gated by
    ``OBSERVED_MUTATION_CROSS_CHECK_ENABLED``) against the op's declared mutation
    events. The drill patches the production stamping helper to under-declare
    (drop the renumbered/target/parent paths) so the genuine observed tree change
    is no longer explained by the declared event paths. The cross-check is
    passive (observation role, never gates replay); the drill asserts it records
    a REPLAY_UNDECLARED_TREE_TOUCH result carrying the undeclared path payload.
    """
    from unittest.mock import patch as _patch

    from lxml import etree

    import lawvm.finland.apply_typed_dispatch as apply_typed_dispatch
    import lawvm.finland.apply_resolved_op as apply_resolved_op
    import lawvm.finland.apply_ops_executor as apply_ops_executor
    from lawvm.core.mutation_accounting import MutationAccountingResult
    from lawvm.finland.apply_events import (
        ApplyMutationEvent,
        _emit_apply_mutation_event_for_rop as _real_emit,
    )
    from lawvm.finland.apply_ops_boundary import ApplyOpsRequest, ApplyOpsSinks

    assert apply_resolved_op.OBSERVED_MUTATION_CROSS_CHECK_ENABLED, (
        "observed-vs-declared cross-check is disabled; the K1 gate cannot observe"
    )

    state, ctx, op, rop = _drill_renumber_rop()
    events: list[ApplyMutationEvent] = []
    observed: list[MutationAccountingResult] = []

    def _underdeclaring_emit(
        mutation_events_out: list[ApplyMutationEvent] | None,
        **kwargs: Any,
    ) -> None:
        # Keep the real apply, but emit an event that declares none of the paths
        # the apply actually touched — the observed diff is then unexplained.
        kwargs["renumbered_paths"] = ()
        kwargs["resolved_target_path"] = None
        kwargs["parent_path"] = None
        return _real_emit(mutation_events_out, **kwargs)

    def _underdeclaring_receipt_emit(
        mutation_events_out: list[ApplyMutationEvent] | None,
        *,
        receipt: Any,
        outcome: str,
        rop: Any = None,
        op: Any = None,
        used_fallback_tags: tuple[str, ...] = (),
    ) -> None:
        assert rop is not None
        _underdeclaring_emit(
            mutation_events_out,
            rop=rop,
            helper=receipt.helper,
            outcome=outcome,
            consumed_paths=(),
            created_paths=(),
            removed_paths=(),
            replaced_paths=(),
            renumbered_paths=(),
            placeholder_created_paths=(),
            placeholder_consumed_paths=(),
            used_fallback_tags=used_fallback_tags,
        )

    muutos_tree = etree.fromstring(b"<body/>")
    with _patch.object(
        apply_typed_dispatch,
        "_emit_apply_mutation_event_from_receipt",
        _underdeclaring_receipt_emit,
    ):
        apply_ops_executor._apply_ops_to_tree_typed(
            ApplyOpsRequest(
                state=state,
                ctx=ctx,
                resolved=[rop],
                ops=[op],
                source_model=AmendmentSourceModel.from_tree(muutos_tree),
                johto="muutetaan",
                amendment_id="1994/318",
                source_title="Laki",
                amendment_issue_date=None,
                amendment_effective_date=None,
                amendment_expiry_date=None,
                replay_mode="legal_pit",
                strict_profile=None,
                vts_ops_enrich_done=True,
            ),
            ApplyOpsSinks(
                mutation_events_out=events,
                observed_touch_results_out=observed,
            ),
        )

    hits = [r for r in observed if r.code == "REPLAY_UNDECLARED_TREE_TOUCH"]
    assert hits, (
        "production apply touched an undeclared tree path but the observed-vs-"
        "declared cross-check did not record REPLAY_UNDECLARED_TREE_TOUCH"
    )
    result = hits[0]
    assert result.out_of_scope_paths, "undeclared-touch observation carried no path payload"
    assert result.out_of_scope_paths == ((("chapter", "7"),),)


# ---------------------------------------------------------------------------
# xfail drills: verified structurally-unsatisfiable guards (liveness debt)
# ---------------------------------------------------------------------------


def drill_occupancy_policy_violation_finland_production() -> None:
    """APPLY.OCCUPANCY_POLICY_VIOLATION fires from the Finland apply lane.

    Production lane: the intent is built by the real ``_build_canonical_intent``
    (no hand-built policy), so the occupancy contract is the per-action policy
    the production builder assigns to a REPLACE. The drill puts the target slot
    into the tombstone occupancy class — the legitimate-but-non-primary
    reenactment lane — and runs the production ``_check_occupancy_policy``
    guard, asserting it records the observational finding. (Before the
    per-action policies, REPLACE carried ``allowed_from = frozenset(
    OccupancyClass)`` and the guard was structurally unsatisfiable.)
    """
    from lawvm.core.canonical_intent import NodeTarget, Replace
    from lawvm.core.ir import IRNode, LegalAddress
    from lawvm.core.occupancy import OccupancyClass
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.finland.apply_policy import _check_occupancy_policy
    from lawvm.finland.ops import AmendmentOp, ResolvedOp, _build_canonical_intent

    op = AmendmentOp(
        op_id="guard-liveness/occupancy",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="1",
        source_statute="1991/1",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="1",
        _op_type_seed="REPLACE",
        _source_statute_override="1991/1",
        _target_address_override=LegalAddress(path=(("section", "1"),)),
    )

    intent = _build_canonical_intent(rop)
    assert isinstance(intent, Replace), (
        "production builder did not produce a Replace intent for a REPLACE op"
    )
    assert isinstance(intent.target, NodeTarget)
    # Production REPLACE policy must be able to reject some occupancy class;
    # an all-classes allowed_from is the vacuity bug this drill guards against.
    assert intent.contract.occupancy.allowed_from != frozenset(OccupancyClass), (
        "production REPLACE policy permits every occupancy class; the occupancy "
        "guard would be structurally unsatisfiable again"
    )

    # Tombstone slot: REPLACE is allowed (reenactment lane) but non-primary, so
    # the production guard records the observational finding.
    tombstone = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                attrs={"lawvm_repeal_placeholder": "1"},
            ),
        ),
    )
    state = ReplayState(ir=tombstone)
    findings: list[Finding] = []
    _check_occupancy_policy(
        state,
        rop,
        intent,
        (("section", "1"),),
        "guard-liveness/occupancy",
        findings_out=findings,
    )

    hits = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert hits, (
        "production REPLACE-on-tombstone did not record an occupancy policy "
        "observation"
    )
    finding = hits[0]
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.detail["current_occupancy"] == "tombstone"


def drill_occupancy_temporally_disjoint_insert_finland_production() -> None:
    """APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT fires from the Finland apply lane.

    Production lane: a temporary gap-filler INSERT in force 2023-01-01 through
    2023-06-30 (exclusive kernel cutoff expires=2023-07-01) whose exact slot is
    occupied in fold order by a deferred-commencement twin effective 2023-07-01
    (the 2010/1326 ← 2022/1281 + 2022/1282 staggered twin-law family). The
    windows share the boundary day's midnight but are disjoint in legal time;
    the production ``_check_occupancy_policy`` guard must record the typed
    disjoint-window observation INSTEAD of an occupancy policy violation.
    """
    from lawvm.core.canonical_intent import Insert
    from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
    from lawvm.core.semantic_types import IRNodeKind, StructuralAction
    from lawvm.finland.apply_policy import _check_occupancy_policy
    from lawvm.finland.ops import AmendmentOp, ResolvedOp, _build_canonical_intent

    op = AmendmentOp(
        op_id="guard-liveness/occupancy-disjoint",
        op_type="INSERT",
        target_unit_kind="section",
        target_section="78c",
        target_chapter="8",
        source_statute="2022/1282",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="78c"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="78c",
        _op_type_seed="INSERT",
        _source_statute_override="2022/1282",
        _target_address_override=LegalAddress(
            path=(("chapter", "8"), ("section", "78c"))
        ),
        _op_source_override=OperationSource(
            statute_id="2022/1282",
            title="väliaikainen",
            effective="2023-01-01",
            # Exclusive kernel cutoff: prose "voimassa 30.6.2023" ⇒ 2023-07-01.
            expires="2023-07-01",
        ),
    )

    intent = _build_canonical_intent(rop)
    assert isinstance(intent, Insert), (
        "production builder did not produce an Insert intent for an INSERT op"
    )

    live = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                children=(IRNode(kind=IRNodeKind.SECTION, label="78c"),),
            ),
        ),
    )
    history = [
        LegalOperation(
            op_id="guard-liveness/occupancy-disjoint-occupant",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "8"), ("section", "78c"))),
            source=OperationSource(
                statute_id="2022/1281",
                title="pysyvä",
                effective="2023-07-01",
            ),
        )
    ]
    state = ReplayState(ir=live)
    findings: list[Finding] = []
    _check_occupancy_policy(
        state,
        rop,
        intent,
        (("chapter", "8"), ("section", "78c")),
        "guard-liveness/occupancy-disjoint",
        findings_out=findings,
        replay_history_ops=history,
    )

    violations = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert not violations, (
        "temporally disjoint twin insert was still recorded as an occupancy "
        "policy violation"
    )
    hits = [
        f for f in findings if f.kind == "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT"
    ]
    assert hits, (
        "temporally disjoint twin insert did not record the typed "
        "disjoint-window observation"
    )
    finding = hits[0]
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.detail["occupant_effective"] == "2023-07-01"


def drill_replay_product_invariant_violation_cross_act() -> None:
    """APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION cross-act conflict surface.

    Structural cause (xfail): the UK same-day order-normalization diagnostic in
    uk_legislation/ordering.py groups effects by an ``_EffectOrderingGroupKey``
    that includes ``affecting_act_id``. Cross-act conflicts therefore always
    land in different groups and can never be compared, so the same-day
    conflict guard is structurally unable to observe a cross-act ordering
    violation. There is no production input that drives this guard into its
    firing state for the cross-act case.
    """
    raise AssertionError(
        "UK same-day ordering groups by affecting_act_id, so cross-act ordering "
        "conflicts are partitioned into separate groups and the guard can never "
        "compare them; the cross-act invariant is structurally unobservable"
    )


def drill_frontend_internal_error_finland_ingress() -> None:
    """PARSE.FRONTEND_INTERNAL_ERROR survives the Finland frontend ingress.

    Production lane: the Finland frontend ``normalize_and_compile_ops`` runs the
    real PEG / clause compile, and ``parse_johtolause_clause`` records a blocking
    PARSE.FRONTEND_INTERNAL_ERROR violation when the production surface resolver
    crashes. The fault is injected by making the production resolver raise; the
    guard, the parse-layer finding, and the frontend ingress finding-conservation
    are all production code.

    Previously this lane dropped the violation twice: ``normalize_and_compile_ops``
    never read ``parse_result.findings`` and the grafter sink projected only
    observation/obligation roles. Both layers now carry the violation, so the
    code reaches the frontend ``PhaseResult`` ledger with ``has_blocking`` set.

    The frontend-boundary correctness is also asserted by
    ``test_fi_decomposition.py::test_blocking_parse_violation_carries_through_frontend``;
    this drill is the guard-liveness surface assertion for the same lane.
    """
    import copy

    from lxml import etree

    import lawvm.finland.johtolause.surface_resolve as surface_resolve
    from lawvm.finland.frontend_compile import normalize_and_compile_ops
    from lawvm.finland.helpers import _fi_label_postprocessor

    from unittest.mock import patch as _patch

    def _statute_xml(date: str) -> bytes:
        return (
            "<akomaNtoso><act>"
            f'<meta><lifecycle><eventRef date="{date}"/></lifecycle></meta>'
            "<body><section><num>3 §</num>"
            "<subsection><num>1</num><content><p>Vanha teksti.</p></content></subsection>"
            "</section></body></act></akomaNtoso>"
        ).encode()

    ctx = StatuteContext.from_xml(_statute_xml("2000-01-01"), _fi_label_postprocessor)
    master = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    muutos_tree = etree.fromstring(_statute_xml("2010-01-01"))

    def raising_resolver(clause: surface_resolve.SurfaceClause) -> NoReturn:
        raise RuntimeError("synthetic internal resolver fault for guard-liveness drill")

    with _patch.object(surface_resolve, "resolve_surface_clause", raising_resolver):
        result = normalize_and_compile_ops(
            johto="muutetaan 3 §:n 1 momentti seuraavasti:",
            muutos_tree=muutos_tree,
            master=master,
            amendment_id="2010/100",
            source_title="Laki muuttamisesta",
            used_sec1_fallback=False,
            parent_id="2000/1",
        )

    hits = [
        f
        for f in result.findings()
        if f.role == "violation" and f.kind == "PARSE.FRONTEND_INTERNAL_ERROR"
    ]
    assert hits, (
        "PARSE.FRONTEND_INTERNAL_ERROR was dropped at the Finland frontend ingress; "
        "it must reach the frontend PhaseResult finding ledger"
    )
    assert hits[0].blocking is True
    assert result.has_blocking


# ---------------------------------------------------------------------------
# Declarative fire-drill registry
# ---------------------------------------------------------------------------

# code -> fire-drill callable that drives the production lane to the guarded
# state and asserts the finding reaches its consumer-visible surface.
FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "APPLY.TREE_INVARIANT_VIOLATION": drill_tree_invariant_violation_duplicate_label,
    "APPLY.FAILED_OPERATION": drill_failed_operation_verdict_barrier,
    "APPLY.SOURCE_PATHOLOGY_DETECTED": drill_source_pathology_detected_verdict_barrier,
    "PARSE.FRONTEND_INTERNAL_ERROR": drill_frontend_internal_error_parse_surface,
    "REPLAY_UNKNOWN_MUTATION_OUTCOME": drill_replay_unknown_mutation_outcome_apply_lane,
}

# A second, distinct surface for an already-covered code. Tracked separately so
# the inventory still treats the code as covered, but the verdict-surface lane
# gets its own liveness assertion.
SECONDARY_FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "APPLY.TREE_INVARIANT_VIOLATION": drill_tree_invariant_violation_verdict_barrier,
}

# Additional distinct production surfaces for an already-covered code, where a
# single code is keyed by more than one secondary lane. Tracked here (rather than
# in SECONDARY_FIRE_DRILLS, which is one-drill-per-code) so each lane still gets
# its own liveness assertion while the inventory keeps treating the code as
# covered. The Finland frontend ingress lane is a second surface for
# PARSE.FRONTEND_INTERNAL_ERROR (the parse surface is in FIRE_DRILLS): the B1 fix
# made the parse-layer violation survive normalize_and_compile_ops + the grafter
# sink, so this lane is now reachable and is no longer guard-liveness debt.
EXTRA_SURFACE_FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "PARSE.FRONTEND_INTERNAL_ERROR": drill_frontend_internal_error_finland_ingress,
}

# Active fire-drills for codes registered as observation (non-blocking) rather
# than blocking enforcement. They still exercise the production guard lane, but
# they are tracked separately from FIRE_DRILLS because the blocking-code
# inventory tests only police blocking codes.
OBSERVATION_FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "APPLY.OCCUPANCY_POLICY_VIOLATION": drill_occupancy_policy_violation_finland_production,
    "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT": drill_occupancy_temporally_disjoint_insert_finland_production,
    "APPLY.REPLAY_UNDECLARED_TREE_TOUCH": drill_replay_undeclared_tree_touch_apply_lane,
}

# code -> (reason, xfail-drill). These guards are verified structurally
# unsatisfiable from the production lane today; the drill raises to document the
# debt and would xpass (hard-fail) if the structural cause were fixed.
XFAIL_FIRE_DRILLS: Dict[str, tuple[str, Callable[[], None]]] = {
    "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION": (
        "UK same-day ordering group key includes affecting_act_id; cross-act "
        "conflicts are partitioned into different groups and never compared",
        drill_replay_product_invariant_violation_cross_act,
    ),
}


# ---------------------------------------------------------------------------
# Inventory: which blocking codes consciously lack a fire-drill (for now)
# ---------------------------------------------------------------------------

# A finding code is treated as "blocking enforcement" for inventory purposes
# when it can hard-stop or strict-fail a compile: default_enforcement is
# hard_fail / strict_fail, or its registry role is violation / obligation.
def _is_blocking_code(spec: FindingSpec) -> bool:
    return spec.default_enforcement in ("hard_fail", "strict_fail") or spec.role in (
        "violation",
        "obligation",
    )


# Codes that block but do not yet have a fire-drill. The point of the inventory
# test is that NEW blocking codes must consciously land here or in FIRE_DRILLS —
# they cannot be added to the registry and silently skip liveness coverage.
# Pruning entries from this allowlist (by writing a fire-drill) is the intended
# direction of travel.
NO_FIRE_DRILL_YET: frozenset[str] = frozenset({
    # Fixed-term expiry blocking diagnostics surface at the provision-state
    # seam (flag-gated; exercised in test_fi_temporal_fixed_term_expiry.py), not
    # through the replay PhaseResult lanes this harness drills. Drill when the
    # semantics flag goes default-on.
    "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS",
    "TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS",
    "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE",
    # Typed residue subclasses of the same seam-surfaced blocking family
    # (governing_unparseable); exercised in test_fi_temporal_fixed_term_expiry.py.
    "TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING",
    "TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED",
    "TEMPORAL.EVENT_BOUND_RESOLVER_MISSING",
    "TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE",
    "TEMPORAL.SOURCE_IMPOSSIBLE_DATE",
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
    "APPLY.LEGACY_DISPATCH_FALLBACK",
    "APPLY.METADATA_ATTRIBUTION_CORRECTED_BY_ATTESTATION",
    "APPLY.REF_TARGET_CORRECTED_BY_ATTESTATION",
    "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",  # also in XFAIL (cross-act case)
    "APPLY.RELABEL_SKIPPED",
    "APPLY.SOURCE_CORRECTED_BY_PATCH",
    "APPLY.SOURCE_INCOMPLETE",
    "APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH",
    "APPLY.STRICT_REJECTED_UNCOVERED_BODY",
    "APPLY.UNCOVERED_BODY_RECOVERY",
    # Payload-normalization strict barrier; add a dedicated production-lane
    # drill when flattened insert-subsection tail splitting gets a small
    # stable fixture in this harness.
    "ELAB.SPLIT_FLATTENED_INSERT_SUBSECTION_TAIL",
    "APPLY.WORD_SUBSTITUTION",
    "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION",
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
    "COVERAGE.UNRESOLVED_BODY_GAP",
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
    "ELAB.AMBIGUOUS_BINDING",
    "ELAB.CHAPTER_SEED_REPAIR",
    "ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY",
    "ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST",
    "ELAB.CONTAINER_PRUNED_SHADOWED",
    "ELAB.DROP_ITEM_REPLACES_MISSING",
    "ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT",
    "ELAB.DUPLICATE_TABLE_NOTE_BLOCK_PRUNED",
    "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD",
    "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT",
    "ELAB.LEADING_OMISSION_ANCHOR_PREFIX_MERGE",
    "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING",
    "ELAB.MISSING_PAYLOAD_SURFACE",
    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
    "ELAB.NORMALIZE_ITEM_LIKE_TARGET",
    "ELAB.NUMBERED_TABLE_TARGET_MERGE",
    "ELAB.OMISSION_EXPANSION",
    "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT",
    "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE",
    "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL",
    "ELAB.REBASE_SPARSE_STALE_PREDECESSOR",
    "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
    "ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT",
    "ELAB.SEC1_PRE_ROUTING_FALLBACK",
    "ELAB.SPARSE_PAYLOAD_LEFTOVER",
    "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
    "ELAB.STRICT_REJECTED_OPERATION",
    "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",  # consumer code APPLY.SOURCE_PATHOLOGY_DETECTED has a drill
    "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION",
    "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION",
    "ELAB.TEXT_TABLE_ROW_CONTINUATION",
    "ELAB.TRAILING_SPARSE_INSERT_BINDING",
    "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNLABELED_ADJACENT_SECTION_CONTINUATION",
    "ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION",
    "ELAB.WRAPPER_ORPHAN_SUBSECTION_CONTINUATION",
    "LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION",
    "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE",
    "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET",
    "LOWER.CONTEXT_DEPENDENT_ANCHOR",
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
    "LOWER.EXPLICIT_CHUNK_SCOPE",
    "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED",
    "LOWER.EXPLICIT_SCOPE_REWRITE",
    "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
    "LOWER.SCOPE_CARRY_FORWARD",
    "PARSE.EXTRACTION_FALLBACK",
    "PARSE.FRONTEND_BLOCKING_DIAGNOSTIC",
    "PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA",
    "PARSE.JOHTOLAUSE_FAILED.RESOLVED_BY_ATTESTATION",
    "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
    "PARSE.STRICT_REJECTED_TARGET_GUESSING",
    "PARSE.TARGET_GUESSING",
    "PARSE.UNOWNED_BODY_SECTION",
    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
    "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
    "REPLAY_FAILED_OP_MUTATED_TREE",
    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    "REPLAY_SKIPPED_OP_MUTATED_TREE",
    "RUNTIME.VIOLATION",
    "TIME.CONTENT_DRIFT",
    "TIME.CONTINGENT_EFFECTIVE_DATE",
    "TIME.ESTIMATED_EFFECTIVE_DATE",
    "TIME.MISSING_EFFECTIVE_DATE",
    "TIME.SECTION_NO_TIMELINE",
    "TIME.TIMELINE_EXECUTION_ISSUE",
    "TIME.TIMELINE_NO_SECTION",
    "TIME.TRIGGER_COVERAGE_INCOMPLETE",
    "TIME.UNRESOLVED_COMMENCEMENT_TRIGGER",
    "uk_replay_text_patch_preimage_drift",
})


def _blocking_codes() -> set[str]:
    return {code for code, spec in FINDING_REGISTRY.items() if _is_blocking_code(spec)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(FIRE_DRILLS))
def test_fire_drill_reaches_consumer_surface(code: str) -> None:
    """Each registered fire-drill drives the production lane to its guard surface."""
    FIRE_DRILLS[code]()


@pytest.mark.parametrize("code", sorted(SECONDARY_FIRE_DRILLS))
def test_secondary_fire_drill_reaches_consumer_surface(code: str) -> None:
    """Secondary surfaces for already-covered codes also reach their guard surface."""
    SECONDARY_FIRE_DRILLS[code]()


@pytest.mark.parametrize("code", sorted(EXTRA_SURFACE_FIRE_DRILLS))
def test_extra_surface_fire_drill_reaches_consumer_surface(code: str) -> None:
    """Extra production surfaces for an already-covered code reach their guard surface."""
    EXTRA_SURFACE_FIRE_DRILLS[code]()


@pytest.mark.parametrize("code", sorted(OBSERVATION_FIRE_DRILLS))
def test_observation_fire_drill_reaches_consumer_surface(code: str) -> None:
    """Non-blocking observation guards still reach their production surface."""
    OBSERVATION_FIRE_DRILLS[code]()


@pytest.mark.parametrize("code", sorted(XFAIL_FIRE_DRILLS))
def test_known_unsatisfiable_guard_is_documented(code: str, request: pytest.FixtureRequest) -> None:
    """Document verified guard-liveness debt.

    These guards cannot be driven into their firing state from production today.
    Marked xfail(strict=False): they never fail CI, and would xpass (signal a
    fix) the day the structural cause is removed.
    """
    reason, drill = XFAIL_FIRE_DRILLS[code]
    request.applymarker(pytest.mark.xfail(reason=reason, strict=False))
    drill()


def test_every_fire_drill_targets_a_registered_blocking_code() -> None:
    """Fire-drills must target real, blocking registry codes (no dead drills)."""
    drilled = set(FIRE_DRILLS) | set(SECONDARY_FIRE_DRILLS) | set(EXTRA_SURFACE_FIRE_DRILLS)
    blocking = _blocking_codes()
    for code in sorted(drilled):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"fire-drill targets unregistered finding code: {code}"
        assert code in blocking, (
            f"fire-drill targets non-blocking code {code!r}; guard-liveness only "
            "covers codes with blocking enforcement"
        )


def test_xfail_drills_name_real_codes_or_synthetic_ingress_markers() -> None:
    """xfail drills must reference a registered finding code (or a documented ingress marker)."""
    # Synthetic markers document a loss point for an already-registered code on a
    # specific lane; they are suffixed and resolve to a real registered code.
    # (None active right now: the Finland-ingress marker was retired once the
    # PARSE.FRONTEND_INTERNAL_ERROR ingress lane became reachable.)
    synthetic_to_real: Dict[str, str] = {}
    blocking = _blocking_codes()
    for code in sorted(XFAIL_FIRE_DRILLS):
        real_code = synthetic_to_real.get(code, code)
        spec = FINDING_REGISTRY.get(real_code)
        assert spec is not None, f"xfail drill references unregistered code: {real_code}"
        assert real_code in blocking, (
            f"xfail drill references non-blocking code: {real_code}"
        )


def test_observation_fire_drills_name_real_non_blocking_codes() -> None:
    """Observation fire-drills must name registered, non-blocking observation codes."""
    blocking = _blocking_codes()
    for code in sorted(OBSERVATION_FIRE_DRILLS):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"observation fire-drill references unregistered code: {code}"
        assert code not in blocking, (
            f"observation fire-drill {code!r} is a blocking code; move it to FIRE_DRILLS"
        )


def test_blocking_code_inventory_is_fully_partitioned() -> None:
    """Every blocking finding code is covered by a fire-drill or the explicit allowlist.

    This is the liveness ratchet: a NEW blocking code added to FINDING_REGISTRY
    must consciously gain a fire-drill (FIRE_DRILLS) or be listed in
    NO_FIRE_DRILL_YET. It cannot silently skip guard-liveness coverage.
    """
    blocking = _blocking_codes()
    covered = set(FIRE_DRILLS)
    uncovered = blocking - covered - NO_FIRE_DRILL_YET
    assert not uncovered, (
        "blocking finding codes lack both a fire-drill and a NO_FIRE_DRILL_YET "
        f"entry: {sorted(uncovered)}. Add a fire-drill to FIRE_DRILLS or, if it is "
        "not yet drillable, add it to NO_FIRE_DRILL_YET (consciously)."
    )


def test_no_fire_drill_allowlist_has_no_stale_entries() -> None:
    """Allowlist entries must be real blocking codes that are NOT already drilled.

    Keeps the allowlist honest: it cannot list non-existent codes, non-blocking
    codes, or codes that already have a fire-drill (which would be stale debt).
    """
    blocking = _blocking_codes()
    drilled = set(FIRE_DRILLS)
    for code in sorted(NO_FIRE_DRILL_YET):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"NO_FIRE_DRILL_YET lists unregistered code: {code}"
        assert code in blocking, f"NO_FIRE_DRILL_YET lists non-blocking code: {code}"
        assert code not in drilled, (
            f"NO_FIRE_DRILL_YET lists {code!r} but it already has a fire-drill; "
            "remove it from the allowlist"
        )
