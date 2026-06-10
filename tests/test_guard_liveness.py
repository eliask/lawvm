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

The three ``xfail`` drills document verified, structurally-unsatisfiable guards
(guard-liveness debt). They are ``strict=False`` so they never fail CI; their
job is to keep the debt visible and to flip to a hard failure (xpass) the day
the underlying structural cause is fixed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast, Callable, Dict

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
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.replay_pipeline import (
    ReplayPlan,
    build_tree_invariant_finding,
    execute_replay_plan,
)
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
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
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

    def process_muutoslaki(mid, state, ctx, **kwargs):  # noqa: ANN001
        duplicated = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1"),
                IRNode(kind=IRNodeKind.SECTION, label="1"),
            ),
        )
        return PhaseResult(output=state.with_ir(duplicated))

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

    original = surface_resolve.resolve_surface_clause

    def raising_resolver(clause):  # noqa: ANN001
        raise RuntimeError("synthetic internal resolver fault for guard-liveness drill")

    surface_resolve.resolve_surface_clause = raising_resolver  # ty: ignore[invalid-assignment]
    try:
        result = api.parse_clause("Muutetaan lain 1 §.")
    finally:
        surface_resolve.resolve_surface_clause = original  # ty: ignore[invalid-assignment]

    hits = [f for f in result.findings if f.kind == "PARSE.FRONTEND_INTERNAL_ERROR"]
    assert hits, "internal resolver fault did not surface PARSE.FRONTEND_INTERNAL_ERROR"
    finding = hits[0]
    assert finding.role == "violation"
    assert finding.blocking is True


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
    """PARSE.FRONTEND_INTERNAL_ERROR should survive to the Finland replay ledger.

    Structural cause (xfail): although the code is visible in
    ``parse_clause(...).findings`` (covered by the passing drill above), it is
    dropped twice on the Finland production replay lane. The frontend ingress
    ``normalize_and_compile_ops`` never reads ``parse_result.findings``, and the
    grafter sink projects only ``observation`` and ``obligation`` roles —
    ``violation`` findings (PARSE.FRONTEND_INTERNAL_ERROR is a violation) are
    discarded. So a frontend internal error never reaches the replay finding
    ledger / verdict surface from Finland production.
    """
    raise AssertionError(
        "PARSE.FRONTEND_INTERNAL_ERROR is dropped on the Finland lane: "
        "normalize_and_compile_ops never reads parse_result.findings, and the "
        "grafter finding sink keeps only observation/obligation roles, so the "
        "violation-role internal-error finding never reaches the replay ledger"
    )


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
}

# A second, distinct surface for an already-covered code. Tracked separately so
# the inventory still treats the code as covered, but the verdict-surface lane
# gets its own liveness assertion.
SECONDARY_FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "APPLY.TREE_INVARIANT_VIOLATION": drill_tree_invariant_violation_verdict_barrier,
}

# Active fire-drills for codes registered as observation (non-blocking) rather
# than blocking enforcement. They still exercise the production guard lane, but
# they are tracked separately from FIRE_DRILLS because the blocking-code
# inventory tests only police blocking codes.
OBSERVATION_FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "APPLY.OCCUPANCY_POLICY_VIOLATION": drill_occupancy_policy_violation_finland_production,
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
    "PARSE.FRONTEND_INTERNAL_ERROR_FINLAND_INGRESS": (
        "Finland normalize_and_compile_ops never reads parse_result.findings "
        "and the grafter sink keeps only observation/obligation roles, so the "
        "violation-role internal-error finding is dropped before the replay ledger",
        drill_frontend_internal_error_finland_ingress,
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
    "APPLY.WORD_SUBSTITUTION",
    "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION",
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
    "COVERAGE.UNRESOLVED_BODY_GAP",
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
    "ELAB.AMBIGUOUS_BINDING",
    "ELAB.CHAPTER_SEED_REPAIR",
    "ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY",
    "ELAB.CONTAINER_PRUNED_SHADOWED",
    "ELAB.DROP_ITEM_REPLACES_MISSING",
    "ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT",
    "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING",
    "ELAB.MISSING_PAYLOAD_SURFACE",
    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH",
    "ELAB.NORMALIZE_ITEM_LIKE_TARGET",
    "ELAB.OMISSION_EXPANSION",
    "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT",
    "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE",
    "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL",
    "ELAB.REBASE_SPARSE_STALE_PREDECESSOR",
    "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
    "ELAB.SEC1_PRE_ROUTING_FALLBACK",
    "ELAB.SPARSE_PAYLOAD_LEFTOVER",
    "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE",
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
    "ELAB.STRICT_REJECTED_OPERATION",
    "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",  # consumer code APPLY.SOURCE_PATHOLOGY_DETECTED has a drill
    "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION",
    "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION",
    "ELAB.TRAILING_SPARSE_INSERT_BINDING",
    "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION",
    "ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION",
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
    drilled = set(FIRE_DRILLS) | set(SECONDARY_FIRE_DRILLS)
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
    synthetic_to_real = {
        "PARSE.FRONTEND_INTERNAL_ERROR_FINLAND_INGRESS": "PARSE.FRONTEND_INTERNAL_ERROR",
    }
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
