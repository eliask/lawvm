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
from lxml import etree

from lawvm.core.compile_result import (
    CompileFailure,
    StrictProfile,
    compute_verdict_from_registry,
    strict_fail_reasons_from_finding_ledger,
)
from lawvm.core.elaboration_context import PayloadElaborationContext, ReplayLookups
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
from lawvm.finland.corpus import get_corpus_store
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.payload_normalize import elaborate_payload_against_live
from lawvm.finland.replay_pipeline import (
    ReplayPlan,
    build_tree_invariant_finding,
    execute_replay_plan,
)
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.sparse_tail_claims import (
    SPARSE_OMISSION_TAIL_CLAIM_RULE,
    SPARSE_OMISSION_TAIL_PRUNE_RULE,
    build_sparse_omission_tail_claims,
)
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.finland.target_kind import TargetKind


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


def drill_leading_subsection_heading_payload_elaboration() -> None:
    """ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD reaches payload elaboration output.

    Production lane: ``elaborate_payload_against_live`` normalizes a whole-section
    insert payload whose first subsection is title-shaped, promotes that text to
    a section heading, and emits the blocking elaboration observation.
    """
    ctx = PayloadElaborationContext(
        target_unit_kind="section",
        target_norm="11a",
        target_chapter="1",
        target_part=None,
        live_node=None,
        parent_node=None,
        subsection_slots=(),
        live_subsections=(),
        subsection_by_label={},
        item_index={},
        row_anchor_index={},
        container_member_labels=None,
        lookups=ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset(),
        ),
    )
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="11a",
        target_chapter="1",
        source_statute="2021/278",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="11a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="11 a §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Veroilmoituksen antamisaikaa koskeva poikkeava määräys",
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Poiketen 11 §:stä ilmoitus annetaan myöhemmin."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    normalized = got.muutos_ir
    assert normalized is not None
    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.HEADING,
        IRNodeKind.SUBSECTION,
    ]
    observations = got.elaboration_observations
    assert observations is not None
    assert any(
        observation.kind == "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        and (observation.detail or {})["shifted_subsection_count"] == 1
        for observation in observations
    )


def drill_restore_heading_for_explicit_facet_group_elaboration() -> None:
    """ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET reaches the group elaboration ledger.

    Production lane: ``elaborate_group`` runs the production payload-prepare lane
    (``prepare_payload_surface``) over a sparse prepared section payload that has
    been projected down to its targeted subsection — the section heading is no
    longer in the payload — and then runs the production guard
    ``_restore_source_heading_for_explicit_heading_facet``. When the group also
    carries an explicit same-section heading-facet op (``target_special ==
    "otsikko"``) and the typed source-model payload still owns the heading, the
    guard copies that source heading back onto the payload and records the typed
    ``ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET`` observation, which
    ``elaborate_group`` projects onto its ``PhaseResult`` finding ledger.

    The heading-less prepared payload is the post-sparse-prepare shape an
    omission-projected section payload leaves behind; the source-model retains
    the full source payload (heading included). The guard, the heading copy, and
    the finding projection are all production code.
    """
    from lawvm.core.payload_surface import GroupSurface
    from lawvm.core.elaboration_context import (
        snapshot_replay_lookups,
        snapshot_target_context,
    )
    from lawvm.finland.ops import get_replay_profile
    from lawvm.finland.compile_group_elaboration import (
        ElaborateGroupRequest,
        elaborate_group,
    )

    source_xml = (
        '<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<section>"
        "<num>5 §</num>"
        "<heading>Otsikko viisi</heading>"
        "<subsection><num>2</num><content><p>Toinen momentti uusittuna.</p></content></subsection>"
        "</section>"
        "</body>"
    )
    source_model = AmendmentSourceModel.from_tree(etree.fromstring(source_xml.encode()))

    # Post-sparse-prepare payload shape: the section payload has been projected
    # down to its single targeted subsection and the heading is gone. The
    # source-model (above) still owns the section heading.
    prepared_body_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Toinen momentti uusittuna."),),
            ),
        ),
    )
    group_surface = GroupSurface(
        body_ir=prepared_body_ir,
        cross_heading_ir=None,
        source_statute="2099/1",
        target_unit_kind="section",
        target_norm="5",
        target_chapter=None,
    )

    body_op = AmendmentOp(
        op_id="replace_5_2",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="5",
        target_paragraph=2,
        source_statute="2099/1",
    )
    heading_facet_op = AmendmentOp(
        op_id="replace_5_otsikko",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="5",
        target_special="otsikko",
        source_statute="2099/1",
    )
    group_ops = [body_op, heading_facet_op]

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    lookups = snapshot_replay_lookups(state)
    result = elaborate_group(
        ElaborateGroupRequest(
            target_ctx=snapshot_target_context(state, "section", "5", None, lookups),
            lookups=lookups,
            group_surface=group_surface,
            group_ops=group_ops,
            standalone_section_targets=set(),
            foreign_scoped_standalone_section_targets=set(),
            foreign_scoped_replace_section_targets=set(),
            effective_target_part=None,
            source_model=source_model,
            johto="muutetaan 5 §:n 2 momentti ja pykälän otsikko",
            profile=get_replay_profile("legal_pit"),
            strict_profile=None,
        )
    )

    hits = [
        f for f in result.findings() if f.kind == "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET"
    ]
    assert hits, (
        "explicit heading-facet op over a heading-less sparse payload did not "
        "surface ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET on the group elaboration "
        "finding ledger"
    )
    # The guard genuinely copied the source heading back onto the payload.
    elaborated = result.output
    assert elaborated.muutos_ir is not None
    assert any(
        child.kind is IRNodeKind.HEADING for child in elaborated.muutos_ir.children
    ), "guard fired but did not restore the source heading onto the payload"


def drill_sparse_omission_tail_claim_group_surface() -> None:
    """ELAB.SPARSE_OMISSION_TAIL_CLAIM reaches group-surface findings.

    Production lane: ``build_sparse_omission_tail_claims`` recognizes an omitted
    carrier-section tail and ``build_group_surface`` synthesizes the missing
    descendant payload for the actual descendant target.
    """
    tree = etree.fromstring(
        b"""
        <akomaNtoso>
          <act>
            <body>
              <chapter>
                <num>2 luku</num>
                <section>
                  <num>29 \xc2\xa7</num>
                  <subsection><num>1 mom.</num><content>First.</content></subsection>
                  <subsection><num>2 mom.</num><content>Second.</content></subsection>
                  <hcontainer name="omission"/>
                  <subsection><num>3 mom.</num><content>Claimed tail.</content></subsection>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    model = AmendmentSourceModel.from_tree(tree, source_ref="2099/1")
    carrier = AmendmentOp(
        op_id="replace_29",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="29",
        target_chapter="4",
        source_statute="2099/1",
    )
    descendant = AmendmentOp(
        op_id="replace_31_3",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="31",
        target_chapter="4",
        target_paragraph=3,
        source_statute="2099/1",
    )

    claims = build_sparse_omission_tail_claims([carrier, descendant], model)
    surface_result = build_group_surface(
        BuildGroupSurfaceRequest(
            group_ops=[descendant],
            target_unit_kind="section",
            target_norm="31",
            target_chapter="4",
            target_part=None,
            source_model=model,
            sparse_omission_tail_claims=claims,
        )
    )

    assert surface_result.output.body_ir is not None
    assert surface_result.output.body_ir.label == "31"
    assert "Claimed tail." in irnode_to_text(surface_result.output.body_ir)
    assert any(finding.kind == SPARSE_OMISSION_TAIL_CLAIM_RULE for finding in surface_result.findings())


def drill_sparse_omission_tail_pruned_from_carrier_compile_surface() -> None:
    """ELAB.SPARSE_OMISSION_TAIL_PRUNED_FROM_CARRIER reaches compile findings.

    Production lane: the 1995/1084 amendment has a carrier section whose source
    payload contains an omitted tail that belongs to a separately parsed
    descendant target. ``compile_amendment_ops`` routes that tail to the
    descendant payload and records the carrier-prune finding.
    """
    before = replay_xml(
        request=ReplayXmlRequest(
            parent_id="1985/336",
            mode="official_consolidation",
            stop_before="1995/1084",
            quiet=True,
            build_full_products=False,
        )
    )
    xml = get_corpus_store().read_source("1995/1084")
    assert xml is not None
    tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    source_model = AmendmentSourceModel.from_tree(tree, source_ref="1995/1084")
    phase = normalize_and_compile_ops(
        johto,
        tree,
        before.state,
        "1995/1084",
        "Asetus harjoittelukouluasetuksen muuttamisesta",
        False,
        parent_id="1985/336",
        source_model=source_model,
    )
    ops = [op for op in phase.output if str(op.target_section) in {"29", "31"}]

    result = compile_amendment_ops(
        before.state,
        ops,
        source_model,
        johto,
        "official_consolidation",
        source_ref="1995/1084",
        target_statute="1985/336",
    )

    finding_kinds = {finding.kind for finding in result.findings()}
    assert SPARSE_OMISSION_TAIL_CLAIM_RULE in finding_kinds
    assert SPARSE_OMISSION_TAIL_PRUNE_RULE in finding_kinds


def drill_effect_lifecycle_target_unresolved_verdict_barrier() -> None:
    """APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED reaches strict barrier codes.

    Production lane: unresolved effect-lifecycle target findings flow through
    ``strict_fail_reasons_from_finding_ledger`` -> ``compute_verdict_from_registry``
    and surface in ``CompileVerdict.barrier_codes``. This exercises the strict
    verdict mapping for lifecycle-target composition failures.
    """
    finding = Finding(
        kind="APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED",
        role="obligation",
        stage="apply",
        detail={
            "target_statute": "2010/100",
            "target_title": "Target amendment",
        },
        source_statute="2011/200",
        blocking=True,
    )
    barrier_codes = _verdict_barrier_codes_from_findings(findings=[finding])
    assert "APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED" in barrier_codes


def _route_rejection_findings(
    *,
    route_reason: str,
    route_target_amendment_id: str = "",
    johto: str = "",
) -> list[Finding]:
    """Drive Finland's production route-rejection handler and collect findings."""
    from lxml import etree

    from lawvm.finland.process_route_rejection import ProcessRouteRejectionContext

    findings: list[Finding] = []

    def record_finding(**kwargs: Any) -> Finding:
        finding = Finding(
            kind=kwargs["kind"],
            role=kwargs["role"],
            stage="process_muutoslaki.route_rejection",
            detail=kwargs.get("detail") or {},
            source_statute=kwargs["source_statute"],
            blocking=bool(kwargs.get("blocking", kwargs["role"] == "obligation")),
        )
        findings.append(finding)
        return finding

    ctx = ProcessRouteRejectionContext(
        amendment_id="2020/100",
        parent_id="2021/100",
        parent_title="Guard Liveness Parent",
        source_title="Guard Liveness Amendment",
        johto=johto,
        source_model=AmendmentSourceModel.from_tree(etree.Element("Laki")),
        route_reason=route_reason,
        route_target_amendment_id=route_target_amendment_id,
        strict_profile=None,
        replay_mode="legal_pit",
        lo_ops_out=None,
        vts_skipped_targets=[],
        commencement_expiry_override_notes=[],
        effect_relation_signals=[],
        record_finding=record_finding,
        replay_print=lambda _message: None,
    )
    ctx.handle()
    return findings


def drill_pending_amendment_effect_unresolved_route_rejection_barrier() -> None:
    """APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED fires from route rejection.

    Production lane: ``ProcessRouteRejectionContext.handle`` classifies a
    pending amendment-of-amendment skip, emits the typed effect-relation signal
    and blocking finding, then the real strict verdict mapping exposes the code
    in ``CompileVerdict.barrier_codes``.
    """
    findings = _route_rejection_findings(
        route_reason="pending_amendment_of_parent_skip",
        route_target_amendment_id="2019/50",
    )
    assert any(f.kind == "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED" for f in findings)
    barrier_codes = _verdict_barrier_codes_from_findings(findings=findings)
    assert "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED" in barrier_codes


def drill_meta_repeal_effect_unresolved_route_rejection_barrier() -> None:
    """APPLY.META_REPEAL_EFFECT_UNRESOLVED fires from route rejection.

    Production lane: a meta-repeal-shaped route rejection with malformed target
    citation reaches the unresolved meta-repeal branch, records the blocking
    effect-lifecycle finding, and the real strict verdict mapping exposes it.
    """
    findings = _route_rejection_findings(
        route_reason="citation_mismatch_skip",
        johto="kumotaan eräiden lakien muuttamisesta annetun lain ( 123/ ) 3 §",
    )
    assert any(f.kind == "APPLY.META_REPEAL_EFFECT_UNRESOLVED" for f in findings)
    barrier_codes = _verdict_barrier_codes_from_findings(findings=findings)
    assert "APPLY.META_REPEAL_EFFECT_UNRESOLVED" in barrier_codes


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


def _run_mutation_boundary_drill(
    perturb: Callable[[Dict[str, Any]], None],
) -> list[Finding]:
    """Drive the production apply + replay-evidence projection lanes for a drill.

    Runs the production ``apply_op`` over a real RENUMBER ResolvedOp so a genuine
    ``ApplyMutationEvent`` with a real landed footprint is produced, then patches
    the production stamping helper ``_emit_apply_mutation_event_from_receipt`` to
    perturb only the outcome / declared paths (the rest of the event is built by
    the real production receipt-derivation). The resulting events are then fed to
    the production ``project_replay_evidence`` projector, which runs the real
    mutation-accounting guard (``check_mutation_accounting``) and projects any
    violation onto ``request.replay_findings``. The returned ledger is the
    consumer-visible replay finding surface.
    """
    from unittest.mock import patch as _patch

    import lawvm.finland.apply_typed_dispatch as apply_typed_dispatch
    from lawvm.finland.apply import apply_op
    from lawvm.finland.apply_events import (
        ApplyMutationEvent,
        _emit_apply_mutation_event_for_rop as _real_for_rop,
    )
    from lawvm.finland.replay_evidence_projection import (
        ReplayEvidenceProjectionRequest,
        project_replay_evidence,
    )

    state, ctx, _op, rop = _drill_renumber_rop()
    events: list[ApplyMutationEvent] = []

    def _perturbing_receipt_emit(
        mutation_events_out: list[ApplyMutationEvent] | None,
        *,
        receipt: Any,
        outcome: str,
        rop: Any = None,
        op: Any = None,
        used_fallback_tags: tuple[str, ...] = (),
    ) -> None:
        # Rebuild exactly what the real receipt-derivation would pass, then let
        # the drill perturb only the fields under test (outcome / paths).
        assert rop is not None
        landed = receipt.landed_primary_path
        fields: Dict[str, Any] = dict(
            rop=rop,
            helper=receipt.helper,
            outcome=outcome,
            resolved_target_path=landed,
            parent_path=(landed[:-1] if landed else None),
            consumed_paths=receipt.consumed_paths,
            created_paths=receipt.created_paths,
            removed_paths=receipt.removed_paths,
            replaced_paths=receipt.replaced_paths,
            renumbered_paths=receipt.renumbered_paths,
            placeholder_created_paths=receipt.placeholder_created_paths,
            placeholder_consumed_paths=receipt.placeholder_consumed_paths,
            used_fallback_tags=used_fallback_tags,
        )
        perturb(fields)
        _real_for_rop(mutation_events_out, **fields)

    with _patch.object(
        apply_typed_dispatch,
        "_emit_apply_mutation_event_from_receipt",
        _perturbing_receipt_emit,
    ):
        apply_op(
            state,
            None,
            ctx,
            None,
            rop=rop,
            replay_mode="legal_pit",
            mutation_events_out=events,
        )

    assert events, "production apply did not emit a mutation event"
    findings: list[Finding] = []
    project_replay_evidence(
        ReplayEvidenceProjectionRequest(
            parent_id="1994/318",
            replay_findings=findings,
            source_pathologies=[],
            elaboration_observations=[],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            regex_recognition_coverages=[],
            commencement_expiry_overrides=[],
            write_audits=[],
            mutation_events=events,
            restructure_plans=[],
            source_pathologies_out=None,
            replay_meta_out=None,
            strict_profile=None,
            replay_print=lambda _message: None,
        )
    )
    return findings


def _assert_blocking_replay_finding(findings: list[Finding], code: str) -> None:
    hits = [f for f in findings if f.kind == code]
    assert hits, (
        f"production apply mutation-boundary guard did not surface {code} on the "
        f"replay finding ledger; saw {sorted({f.kind for f in findings})}"
    )
    finding = hits[0]
    assert finding.role == "violation", f"{code} reached the ledger non-blocking (role={finding.role})"
    assert finding.blocking is True, f"{code} reached the ledger with blocking=False"


def drill_replay_skipped_op_mutated_tree_apply_lane() -> None:
    """REPLAY_SKIPPED_OP_MUTATED_TREE reaches the replay finding ledger as blocking.

    Production lane: ``apply_op`` performs a real RENUMBER (a genuine renumbered
    footprint), but the production stamping is perturbed to record the outcome as
    ``skipped`` while still carrying the touched paths. A skipped op that mutated
    the tree is a mutation-boundary violation; the production
    ``check_mutation_accounting`` guard inside ``project_replay_evidence`` must
    project it onto ``replay_findings`` as blocking.
    """

    def _perturb(fields: Dict[str, Any]) -> None:
        fields["outcome"] = "skipped"

    findings = _run_mutation_boundary_drill(_perturb)
    _assert_blocking_replay_finding(findings, "REPLAY_SKIPPED_OP_MUTATED_TREE")


def drill_replay_failed_op_mutated_tree_apply_lane() -> None:
    """REPLAY_FAILED_OP_MUTATED_TREE reaches the replay finding ledger as blocking.

    Production lane: a real RENUMBER apply whose production stamping is perturbed
    to record the outcome as ``failed`` while still carrying the touched paths. A
    failed op that nonetheless mutated the tree is a mutation-boundary violation
    the production guard must surface as blocking.
    """

    def _perturb(fields: Dict[str, Any]) -> None:
        fields["outcome"] = "failed"

    findings = _run_mutation_boundary_drill(_perturb)
    _assert_blocking_replay_finding(findings, "REPLAY_FAILED_OP_MUTATED_TREE")


def drill_replay_missing_primary_target_consumption_apply_lane() -> None:
    """REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION reaches the ledger as blocking.

    Production lane: a real RENUMBER apply whose production stamping is perturbed
    to record an ``applied`` outcome that declares no touched paths at all. An
    applied op that consumed none of its primary target is a mutation-boundary
    violation the production guard must surface as blocking.
    """

    def _perturb(fields: Dict[str, Any]) -> None:
        fields["outcome"] = "applied"
        fields["renumbered_paths"] = ()
        fields["consumed_paths"] = ()
        fields["created_paths"] = ()
        fields["removed_paths"] = ()
        fields["replaced_paths"] = ()
        fields["placeholder_created_paths"] = ()
        fields["placeholder_consumed_paths"] = ()

    findings = _run_mutation_boundary_drill(_perturb)
    _assert_blocking_replay_finding(findings, "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION")


def drill_replay_apply_boundary_unresolved_apply_lane() -> None:
    """REPLAY_APPLY_BOUNDARY_UNRESOLVED reaches the replay finding ledger as blocking.

    Production lane: a real RENUMBER apply that genuinely touched the tree, but
    whose production stamping is perturbed to declare neither a resolved target
    path nor a parent path. With no allowed effect region the apply boundary is
    unresolvable, so the production guard cannot account for the observed change
    and must surface the violation as blocking.
    """

    def _perturb(fields: Dict[str, Any]) -> None:
        fields["outcome"] = "applied"
        fields["resolved_target_path"] = None
        fields["parent_path"] = None

    findings = _run_mutation_boundary_drill(_perturb)
    _assert_blocking_replay_finding(findings, "REPLAY_APPLY_BOUNDARY_UNRESOLVED")


def drill_replay_apply_boundary_touch_outside_target_apply_lane() -> None:
    """REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET reaches the ledger as blocking.

    Production lane: a real RENUMBER apply that touched chapter 7, but whose
    production stamping is perturbed to declare an unrelated target/parent
    (chapter 99). The genuine touched paths then fall outside the declared
    allowed region, so the production boundary guard must surface the
    out-of-target touch as a blocking violation.
    """

    def _perturb(fields: Dict[str, Any]) -> None:
        fields["outcome"] = "applied"
        fields["resolved_target_path"] = (("chapter", "99"), ("section", "1"))
        fields["parent_path"] = (("chapter", "99"),)

    findings = _run_mutation_boundary_drill(_perturb)
    _assert_blocking_replay_finding(findings, "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET")


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
            used_preamble_body_fallback=False,
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
    "APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED": drill_effect_lifecycle_target_unresolved_verdict_barrier,
    "APPLY.FAILED_OPERATION": drill_failed_operation_verdict_barrier,
    "APPLY.META_REPEAL_EFFECT_UNRESOLVED": drill_meta_repeal_effect_unresolved_route_rejection_barrier,
    "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED": drill_pending_amendment_effect_unresolved_route_rejection_barrier,
    "APPLY.SOURCE_PATHOLOGY_DETECTED": drill_source_pathology_detected_verdict_barrier,
    "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD": drill_leading_subsection_heading_payload_elaboration,
    "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET": drill_restore_heading_for_explicit_facet_group_elaboration,
    "ELAB.SPARSE_OMISSION_TAIL_CLAIM": drill_sparse_omission_tail_claim_group_surface,
    "ELAB.SPARSE_OMISSION_TAIL_PRUNED_FROM_CARRIER": drill_sparse_omission_tail_pruned_from_carrier_compile_surface,
    "PARSE.FRONTEND_INTERNAL_ERROR": drill_frontend_internal_error_parse_surface,
    "REPLAY_UNKNOWN_MUTATION_OUTCOME": drill_replay_unknown_mutation_outcome_apply_lane,
    "REPLAY_SKIPPED_OP_MUTATED_TREE": drill_replay_skipped_op_mutated_tree_apply_lane,
    "REPLAY_FAILED_OP_MUTATED_TREE": drill_replay_failed_op_mutated_tree_apply_lane,
    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": drill_replay_missing_primary_target_consumption_apply_lane,
    "REPLAY_APPLY_BOUNDARY_UNRESOLVED": drill_replay_apply_boundary_unresolved_apply_lane,
    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": drill_replay_apply_boundary_touch_outside_target_apply_lane,
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
#
# This is a debt registry, not a bare set: each entry carries a
# (reason_or_ticket, last_reviewed_date) so the debt is consciously maintained
# and cannot be silently parked. ``NO_FIRE_DRILL_CEILING`` is a committed
# monotone-decreasing ceiling: the allowlist may shrink (pay down debt) but may
# never grow past the ceiling, and the ceiling itself only ratchets down. To add
# a new entry you must first lower the ceiling somewhere else (drill an existing
# entry); the allowlist can never silently grow.
NO_FIRE_DRILL_YET: Dict[str, tuple[str, str]] = {
    # Fixed-term expiry blocking diagnostics surface at the provision-state
    # seam (flag-gated; exercised in test_fi_temporal_fixed_term_expiry.py), not
    # through the replay PhaseResult lanes this harness drills. Drill when the
    # semantics flag goes default-on.
    "TEMPORAL.FIXED_TERM_EXPIRY_AMBIGUOUS": ("fixed-term seam; flag-gated, drill when default-on", "2026-06-20"),
    "TEMPORAL.FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS": ("fixed-term seam; flag-gated, drill when default-on", "2026-06-20"),
    "TEMPORAL.FIXED_TERM_EXPIRY_UNPARSEABLE": ("fixed-term seam; flag-gated, drill when default-on", "2026-06-20"),
    # Typed residue subclasses of the same seam-surfaced blocking family
    # (governing_unparseable); exercised in test_fi_temporal_fixed_term_expiry.py.
    "TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING": ("fixed-term residue; seam-surfaced", "2026-06-20"),
    "TEMPORAL.DURATION_COMMENCEMENT_UNRESOLVED": ("fixed-term residue; seam-surfaced", "2026-06-20"),
    "TEMPORAL.EVENT_BOUND_RESOLVER_MISSING": ("fixed-term residue; seam-surfaced", "2026-06-20"),
    "TEMPORAL.EVENT_BOUND_OUT_OF_DOCTRINE": ("fixed-term residue; seam-surfaced", "2026-06-20"),
    "TEMPORAL.SOURCE_IMPOSSIBLE_DATE": ("fixed-term residue; seam-surfaced", "2026-06-20"),
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": ("strict barrier; needs a stable fallback fixture", "2026-06-20"),
    "APPLY.LEGACY_DISPATCH_FALLBACK": ("fallback-tag finding; needs a legacy-dispatch fixture", "2026-06-20"),
    "APPLY.METADATA_ATTRIBUTION_CORRECTED_BY_ATTESTATION": ("attestation-resolved; needs attestation fixture", "2026-06-20"),
    "APPLY.REF_TARGET_CORRECTED_BY_ATTESTATION": ("attestation-resolved; needs attestation fixture", "2026-06-20"),
    "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION": ("also in XFAIL (cross-act case)", "2026-06-20"),
    "APPLY.RELABEL_SKIPPED": ("governed relabel-skip; needs fixture", "2026-06-20"),
    "APPLY.SOURCE_CORRECTED_BY_PATCH": ("corrigendum-patch barrier; needs fixture", "2026-06-20"),
    "APPLY.SOURCE_INCOMPLETE": ("source-incomplete barrier; needs fixture", "2026-06-20"),
    "APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH": ("strict-mode barrier; needs fixture", "2026-06-20"),
    "APPLY.STRICT_REJECTED_UNCOVERED_BODY": ("strict-mode barrier; needs fixture", "2026-06-20"),
    "APPLY.UNCOVERED_BODY_RECOVERY": ("recovery barrier; needs fixture", "2026-06-20"),
    # Payload-normalization strict barrier; add a dedicated production-lane
    # drill when flattened insert-subsection tail splitting gets a small
    # stable fixture in this harness.
    "ELAB.SPARSE_DESCENDANT_LABEL_OMISSION_MERGE": ("sparse descendant merge barrier; needs stable fixture", "2026-06-22"),
    "ELAB.SPLIT_FLATTENED_INSERT_SUBSECTION_TAIL": ("payload-normalize barrier; needs stable fixture", "2026-06-20"),
    "APPLY.WORD_SUBSTITUTION": ("word-substitution barrier; needs fixture", "2026-06-20"),
    "COMPARE.UNADJUDICATED_ORACLE_DIVERGENCE.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED": ("coverage barrier; needs fixture", "2026-06-20"),
    "COVERAGE.UNRESOLVED_BODY_GAP": ("coverage barrier; needs fixture", "2026-06-20"),
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.AMBIGUOUS_BINDING": ("sparse-elaboration ambiguity; needs fixture", "2026-06-20"),
    "ELAB.CHAPTER_SEED_REPAIR": ("chapter-seed recovery; needs fixture", "2026-06-20"),
    "ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY": ("chapter-seed pathology; needs fixture", "2026-06-20"),
    "ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.CONTAINER_PRUNED_SHADOWED": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.DROP_ITEM_REPLACES_MISSING": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.DUPLICATE_TABLE_NOTE_BLOCK_PRUNED": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.LEADING_OMISSION_ANCHOR_PREFIX_MERGE": ("merge recovery; needs fixture", "2026-06-20"),
    "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.MISSING_PAYLOAD_SURFACE": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH": ("payload-normalize ambiguity; needs fixture", "2026-06-20"),
    "ELAB.NORMALIZE_ITEM_LIKE_TARGET": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.NUMBERED_TABLE_TARGET_MERGE": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.OMISSION_EXPANSION": ("omission-expansion barrier; needs fixture", "2026-06-20"),
    "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_SPARSE_STALE_PREDECESSOR": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.SPARSE_PAYLOAD_LEFTOVER": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL": ("payload-normalize recovery; covered by sparse payload fixture", "2026-06-22"),
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.STRICT_REJECTED_OPERATION": ("strict-mode barrier; needs fixture", "2026-06-20"),
    "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY": ("consumer APPLY.SOURCE_PATHOLOGY_DETECTED has a drill", "2026-06-20"),
    "ELAB.TARGET_AMBIGUITY_UNCLASSIFIED.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.TARGET_SELECTION_REQUIRED.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.TEXT_TABLE_ROW_CONTINUATION": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.TRAILING_SPARSE_INSERT_BINDING": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.UNCLASSIFIED_MODAL_SURFACE.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.UNLABELED_ADJACENT_SECTION_CONTINUATION": ("payload-lookup recovery; needs fixture", "2026-06-20"),
    "ELAB.UNLOCATED_SOURCE_LABELED_PURPOSE.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.UNRESOLVED_COMMITTEE_REPORT_REFERENCE.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.UNRESOLVED_EU_ACT_REFERENCE.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.UNRESOLVED_INLINE_STATUTE_CITATION.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.UNRESOLVED_POOL_ADDRESS.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "ELAB.WRAPPER_ORPHAN_SUBSECTION_CONTINUATION": ("payload-lookup recovery; needs fixture", "2026-06-20"),
    "LINEAGE.UNCLASSIFIED_PROVISION_MIGRATION.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE": ("scope recovery; needs fixture", "2026-06-20"),
    "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET": ("scope recovery; needs fixture", "2026-06-20"),
    "LOWER.CONTEXT_DEPENDENT_ANCHOR": ("scope recovery; needs fixture", "2026-06-20"),
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": ("scope barrier; needs fixture", "2026-06-20"),
    "LOWER.EXPLICIT_CHUNK_SCOPE": ("scope recovery; needs fixture", "2026-06-20"),
    "LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED": ("scope barrier; needs fixture", "2026-06-20"),
    "LOWER.EXPLICIT_SCOPE_REWRITE": ("scope recovery; needs fixture", "2026-06-20"),
    "LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED": ("scope barrier; needs fixture", "2026-06-20"),
    "LOWER.SCOPE_CARRY_FORWARD": ("scope recovery; needs fixture", "2026-06-20"),
    "PARSE.EXTRACTION_FALLBACK": ("parse barrier; needs fixture", "2026-06-20"),
    "PARSE.FRONTEND_BLOCKING_DIAGNOSTIC": ("frontend phase barrier; needs fixture", "2026-06-20"),
    "PARSE.BODY_SECTION_REPLACE_FROM_ACT_WIDE_FORMULA": ("frontend recovery; needs fixture", "2026-06-20"),
    "PARSE.PREAMBLE_CLAUSE_FAILED.RESOLVED_BY_ATTESTATION": ("attestation-resolved; needs fixture", "2026-06-20"),
    "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER": ("frontend recovery; needs fixture", "2026-06-20"),
    "PARSE.STRICT_REJECTED_TARGET_GUESSING": ("strict-mode barrier; needs fixture", "2026-06-20"),
    "PARSE.TARGET_GUESSING": ("parse barrier; needs fixture", "2026-06-20"),
    "PARSE.UNOWNED_BODY_SECTION": ("frontend recovery; needs fixture", "2026-06-20"),
    "RUNTIME.VIOLATION": ("generic runtime violation; needs fixture", "2026-06-20"),
    "TIME.CONTINGENT_EFFECTIVE_DATE": ("timeline barrier; needs fixture", "2026-06-20"),
    "TIME.ESTIMATED_EFFECTIVE_DATE": ("timeline barrier; needs fixture", "2026-06-20"),
    "TIME.MISSING_EFFECTIVE_DATE": ("timeline barrier; needs fixture", "2026-06-20"),
    "TIME.TIMELINE_EXECUTION_ISSUE": ("timeline barrier; needs fixture", "2026-06-20"),
    "TIME.TRIGGER_COVERAGE_INCOMPLETE": ("timeline barrier; needs fixture", "2026-06-20"),
    "TIME.UNRESOLVED_COMMENCEMENT_TRIGGER": ("timeline barrier; needs fixture", "2026-06-20"),
    "uk_replay_text_patch_preimage_drift": ("UK replay text-patch drift; needs UK fixture", "2026-06-20"),
}

# Committed monotone-decreasing debt ceiling for NO_FIRE_DRILL_YET. The allowlist
# may never contain more than this many entries, and the ceiling itself may only
# be edited downward. Lowering it (by drilling an entry) is the intended
# direction of travel; raising it requires a deliberate, reviewed exception (and
# the gate below makes silently raising it a test failure as long as the constant
# is committed at its current value).
NO_FIRE_DRILL_CEILING: int = len(NO_FIRE_DRILL_YET)


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
    uncovered = blocking - covered - set(NO_FIRE_DRILL_YET)
    assert not uncovered, (
        "blocking finding codes lack both a fire-drill and a NO_FIRE_DRILL_YET "
        f"entry: {sorted(uncovered)}. Add a fire-drill to FIRE_DRILLS or, if it is "
        "not yet drillable, add it to NO_FIRE_DRILL_YET (consciously)."
    )


def test_blocking_set_equals_fire_drills_union_allowlist() -> None:
    """Ratchet (Gate 1b): BLOCKING == set(FIRE_DRILLS) | NO_FIRE_DRILL_YET.

    The blocking-code set must be exactly partitioned into the drilled codes and
    the consciously-maintained debt allowlist — neither side may carry a code the
    other does not account for. This is the equality form of the inventory
    ratchet: a new blocking code cannot land without consciously joining one
    bucket, and a drilled/allowlisted code cannot linger after it stops being a
    blocking registry code.
    """
    blocking = _blocking_codes()
    accounted = set(FIRE_DRILLS) | set(NO_FIRE_DRILL_YET)
    assert blocking == accounted, (
        "BLOCKING != set(FIRE_DRILLS) | NO_FIRE_DRILL_YET.\n"
        f"  blocking-but-unaccounted: {sorted(blocking - accounted)}\n"
        f"  accounted-but-not-blocking: {sorted(accounted - blocking)}"
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


def test_no_fire_drill_allowlist_entries_are_well_formed_debt() -> None:
    """Gate 1c (shape): each allowlist entry carries a (reason, ISO date) pair.

    The allowlist is a debt registry, not a bare set: every entry must record a
    non-empty reason/ticket and an ISO-8601 ``YYYY-MM-DD`` last-reviewed date, so
    the debt is consciously maintained rather than silently parked.
    """
    from datetime import date

    for code in sorted(NO_FIRE_DRILL_YET):
        entry = NO_FIRE_DRILL_YET[code]
        assert isinstance(entry, tuple) and len(entry) == 2, (
            f"NO_FIRE_DRILL_YET[{code!r}] must be a (reason, last_reviewed) tuple"
        )
        reason, last_reviewed = entry
        assert isinstance(reason, str) and reason.strip(), (
            f"NO_FIRE_DRILL_YET[{code!r}] has an empty reason/ticket"
        )
        assert isinstance(last_reviewed, str), (
            f"NO_FIRE_DRILL_YET[{code!r}] last_reviewed must be an ISO date string"
        )
        # Raises ValueError (test failure) on a malformed date.
        date.fromisoformat(last_reviewed)


def test_no_fire_drill_allowlist_within_monotone_ceiling() -> None:
    """Gate 1c (ratchet): the allowlist size never exceeds the committed ceiling.

    ``NO_FIRE_DRILL_CEILING`` is a committed constant that may only ratchet
    downward. The allowlist may shrink (drill an entry, then lower the ceiling)
    but may never grow past the ceiling, so the debt set cannot silently
    accumulate new entries. To add an entry you must first pay one down.
    """
    count = len(NO_FIRE_DRILL_YET)
    assert count <= NO_FIRE_DRILL_CEILING, (
        f"NO_FIRE_DRILL_YET grew to {count} entries, above the committed ceiling "
        f"{NO_FIRE_DRILL_CEILING}. Drill an existing entry instead of adding debt; "
        "the ceiling may only ratchet down."
    )


# ---------------------------------------------------------------------------
# Gate 1d: every primary fire-drill drives a PRODUCTION builder
# ---------------------------------------------------------------------------

# Production builder entrypoints (functions / classes in lawvm.finland.* /
# lawvm.core.*) a PRIMARY fire-drill must drive into the guarded state. A drill
# satisfies the gate when its body — or a shared harness helper it calls —
# invokes one of these. The verdict-mapping helper
# ``_verdict_barrier_codes_from_findings`` is deliberately NOT in this set: a
# drill whose only production touch is the verdict mapping is a SECONDARY /
# verdict-mapping drill (see ``_VERDICT_SURFACE_PRIMARY_DRILLS``).
_PRODUCTION_BUILDER_CALLS = (
    "execute_replay_plan",
    "apply_op",
    "apply_ops_executor._apply_ops_to_tree_typed",
    "_apply_ops_to_tree_typed",
    "project_replay_evidence",
    "check_apply_mutation_accounting",
    "ProcessRouteRejectionContext",
    "normalize_and_compile_ops",
    "build_group_surface",
    "compile_amendment_ops",
    "elaborate_group",
    "elaborate_payload_against_live",
    "api.parse_clause",
    "parse_clause(",
    "_check_occupancy_policy",
)

# Codes whose ONLY honest production surface is the strict verdict mapping (a
# runtime-finding -> strict-barrier-code projection through
# ``compute_verdict_from_registry``). These primary drills legitimately drive the
# production verdict builder rather than a deeper apply/replay builder; they are
# the verdict-surface primary lane. Every OTHER primary drill must drive a
# builder from ``_PRODUCTION_BUILDER_CALLS``.
_VERDICT_SURFACE_PRIMARY_DRILLS = frozenset({
    "APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED",
    "APPLY.FAILED_OPERATION",
    "APPLY.SOURCE_PATHOLOGY_DETECTED",
})


def _drill_effective_source(drill: Callable[[], None]) -> str:
    """Return the drill body plus the bodies of module-level harness helpers it calls.

    A primary drill often drives production through a shared harness helper
    (``_run_replay_fold``, ``_run_mutation_boundary_drill``,
    ``_route_rejection_findings``), so the production builder call lives in the
    helper, not the drill. Inline-expand one level of such helpers so the
    production-builder check sees the real driving call.
    """
    import inspect

    source = inspect.getsource(drill)
    module = inspect.getmodule(drill)
    for name, obj in vars(module).items():
        if not callable(obj) or not name.startswith("_"):
            continue
        if f"{name}(" in source and obj is not drill:
            try:
                source += "\n" + inspect.getsource(obj)
            except (OSError, TypeError):
                continue
    return source


def test_every_primary_fire_drill_drives_a_production_builder() -> None:
    """Gate 1d: each FIRE_DRILLS entry drives a production builder.

    A primary fire-drill must drive a real ``lawvm.finland.*`` / ``lawvm.core.*``
    production builder into the guarded state — not merely construct a Finding and
    push it through the verdict mapping. Drills whose only production surface is
    the verdict mapping are the explicit, small ``_VERDICT_SURFACE_PRIMARY_DRILLS``
    lane; every other primary drill must call a builder from
    ``_PRODUCTION_BUILDER_CALLS`` (directly or via a shared harness helper). This
    is what makes the primary set a genuine end-to-end guard-liveness proof
    rather than a registry-shaped tautology.
    """
    # Keep the verdict-helper symbols live (they are the SECONDARY surface).
    _ = (_verdict_barrier_codes_from_findings, compute_verdict_from_registry)

    offenders: list[str] = []
    for code in sorted(FIRE_DRILLS):
        if code in _VERDICT_SURFACE_PRIMARY_DRILLS:
            continue
        source = _drill_effective_source(FIRE_DRILLS[code])
        drives_builder = any(call in source for call in _PRODUCTION_BUILDER_CALLS)
        if not drives_builder:
            offenders.append(code)
    assert not offenders, (
        "primary fire-drills that do not drive a production builder (no call into "
        f"{_PRODUCTION_BUILDER_CALLS} directly or via a harness helper): "
        f"{offenders}. Give the code a drill that drives a production builder, or "
        "(if the verdict mapping is genuinely its only surface) add it to "
        "_VERDICT_SURFACE_PRIMARY_DRILLS."
    )


def test_verdict_surface_primary_drills_are_blocking_codes() -> None:
    """The verdict-surface primary lane must name real, blocking, drilled codes."""
    blocking = _blocking_codes()
    for code in sorted(_VERDICT_SURFACE_PRIMARY_DRILLS):
        assert code in FIRE_DRILLS, (
            f"{code} is marked verdict-surface but is not in FIRE_DRILLS"
        )
        assert code in blocking, f"{code} is not a blocking registry code"


# ---------------------------------------------------------------------------
# Gate 1e: every blocking code has a real production emit site
# ---------------------------------------------------------------------------

# Codes whose production emit lives outside src/lawvm/{core,finland} (other
# jurisdiction frontends). They still must have a production emit site, just in a
# different package; the grep widens to the whole src tree for these.
_NON_FI_CORE_EMIT_PREFIXES = ("uk_", "TIME.")

# Known pre-existing registry/producer mismatches: blocking-registered codes that
# currently have NO production emit site anywhere in src/lawvm (only the registry
# declares them). These are the same structural class as the TIME consistency
# codes (rank 18) but are out of scope for this gate's first reconciliation pass;
# they are listed here so the producer-consistency gate lands green while still
# failing on any NEW mismatch. Each should eventually be reconciled (downgrade or
# wire a producer) and removed from this allowlist.
_KNOWN_NO_PRODUCTION_EMIT: Dict[str, str] = {
    "PARSE.STRICT_REJECTED_TARGET_GUESSING": (
        "strict barrier with no emit site; reconcile separately"
    ),
    "TIME.UNRESOLVED_COMMENCEMENT_TRIGGER": (
        "timeline barrier with no emit site; reconcile separately"
    ),
}


def _production_emit_grep(code: str, roots: tuple[str, ...]) -> list[str]:
    """Return non-test production files under *roots* that mention *code*.

    The match is the literal code constant in a non-test ``.py`` file. This is
    the producer side of the registry/producer-consistency check: a
    blocking-registered code must have at least one place in production that emits
    it.
    """
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent
    hits: list[str] = []
    needle = f'"{code}"'
    alt_needle = f"'{code}'"
    for root in roots:
        base = here / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            spath = str(path)
            if "/test" in spath or spath.endswith("_test.py"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if needle in text or alt_needle in text:
                hits.append(spath)
    return hits


def test_every_blocking_code_has_a_production_emit_site() -> None:
    """Gate 1e: each blocking code is emitted from at least one production site.

    For every blocking-registered code there must be at least one non-test
    production file that mentions the code constant (its emit site). This catches
    the registry/producer-mismatch class (the downgraded TIME.* codes): a code
    registered blocking whose only producer emits it non-blocking / off-pipeline
    is structurally unsatisfiable and must be reconciled in the registry, not
    left as a dead blocking guard.

    A bare registry entry (``observation_registry.py`` only) does not count as an
    emit site — the registry declares the code, it does not emit it.
    """
    blocking = _blocking_codes()
    missing: list[str] = []
    for code in sorted(blocking):
        roots: tuple[str, ...] = ("src/lawvm/core", "src/lawvm/finland")
        if code.startswith(_NON_FI_CORE_EMIT_PREFIXES):
            roots = ("src/lawvm",)
        emit_sites = [
            site
            for site in _production_emit_grep(code, roots)
            if not site.endswith("observation_registry.py")
        ]
        if not emit_sites and code not in _KNOWN_NO_PRODUCTION_EMIT:
            missing.append(code)
    assert not missing, (
        "blocking-registered codes with no production emit site (registry/producer "
        f"mismatch — reconcile in the registry or wire a producer): {missing}. "
        "If this is a known pre-existing mismatch out of scope, add it to "
        "_KNOWN_NO_PRODUCTION_EMIT consciously."
    )


def test_known_no_production_emit_allowlist_is_honest() -> None:
    """The pre-existing-mismatch allowlist must be real, blocking, and still emit-less.

    Keeps ``_KNOWN_NO_PRODUCTION_EMIT`` from rotting: every listed code must be a
    registered blocking code that genuinely still has no production emit site. The
    moment a producer is wired (or the code is downgraded), the entry becomes
    stale and must be removed — this test fails until it is.
    """
    blocking = _blocking_codes()
    for code, reason in sorted(_KNOWN_NO_PRODUCTION_EMIT.items()):
        assert reason.strip(), f"_KNOWN_NO_PRODUCTION_EMIT[{code!r}] has an empty reason"
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"_KNOWN_NO_PRODUCTION_EMIT lists unregistered code: {code}"
        assert code in blocking, (
            f"_KNOWN_NO_PRODUCTION_EMIT lists non-blocking code {code!r}; remove it"
        )
        roots: tuple[str, ...] = ("src/lawvm/core", "src/lawvm/finland")
        if code.startswith(_NON_FI_CORE_EMIT_PREFIXES):
            roots = ("src/lawvm",)
        emit_sites = [
            site
            for site in _production_emit_grep(code, roots)
            if not site.endswith("observation_registry.py")
        ]
        assert not emit_sites, (
            f"_KNOWN_NO_PRODUCTION_EMIT lists {code!r} but it now has a production "
            f"emit site ({emit_sites}); remove the stale allowlist entry"
        )


def test_downgraded_consistency_codes_match_their_only_producer() -> None:
    """Gate 1e (rank-18 reconciliation): the TIME consistency codes are non-blocking.

    TIME.SECTION_NO_TIMELINE / TIME.TIMELINE_NO_SECTION / TIME.CONTENT_DRIFT have
    exactly one producer (tools/consistency.py:ConsistencyResult.to_phase_result)
    which emits them role=observation/blocking=False and is not wired into the
    compile/replay pipeline. They were registered hard_fail (blocking) and so
    were structurally unsatisfiable as guards. This pins the reconciliation:
    their registry enforcement is downgraded to a non-blocking observation that
    matches the only real producer, so the producer-consistency gate above holds.
    """
    blocking = _blocking_codes()
    for code in (
        "TIME.SECTION_NO_TIMELINE",
        "TIME.TIMELINE_NO_SECTION",
        "TIME.CONTENT_DRIFT",
    ):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"{code} missing from FINDING_REGISTRY"
        assert spec.role == "observation", f"{code} must reconcile to role=observation"
        assert spec.default_enforcement == "warn", (
            f"{code} must reconcile to non-blocking enforcement matching its producer"
        )
        assert code not in blocking, (
            f"{code} is still classified blocking; the rank-18 reconciliation did "
            "not take effect"
        )
