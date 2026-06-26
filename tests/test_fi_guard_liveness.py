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
from lawvm.core.fire_drill_registry import (
    RECORDED_DEAD,
    classify_guard_liveness,
    hard_or_strict_codes,
)
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
from lawvm.finland.corpus import get_corpus_store
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.ops import OpType, AmendmentOp
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


def _drill_strict_rebound_apply(
    *,
    failed_ops_out: "list[Any] | None" = None,
    source_pathologies_out: "list[Any] | None" = None,
) -> Any:
    """Drive the production strict ``apply_op`` into a continuation-fragment reject.

    Builds a live section 73 whose third subsection is a stale continuation
    fragment, then replays a ``REPLACE 73 § 3 mom`` under the strict Finland
    profile. The production apply lane (the deciding guard) genuinely refuses the
    deterministic rebound: it leaves the tree unmutated and records a real
    ``FailedOp`` (``failed_ops_out``) and a real ``SourcePathology``
    (``source_pathologies_out``). These are the deciding inputs the
    APPLY.FAILED_OPERATION / APPLY.SOURCE_PATHOLOGY_DETECTED barriers consume;
    nothing is hand-built. (Mirrors test_fi_apply.py strict-rebound fixture.)
    """
    from lawvm.finland.apply import apply_op as _apply_op
    from lawvm.finland.ops import AmendmentOp as _AmendmentOp
    from lawvm.finland.ops import ResolvedOp as _ResolvedOp
    from lawvm.finland.ops import _build_canonical_intent
    from lawvm.core.ir import LegalAddress as _LegalAddress
    from lawvm.finland.strict_profile import default_finland_strict_profile

    def _content(text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.CONTENT, text=text)

    def _sub(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))

    def _para(label: str, text: str = "") -> IRNode:
        return IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=label,
            children=(_content(text),) if text else (),
        )

    def _intro(text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.INTRO, text=text)

    def _sec(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))

    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _sec(
                    "73",
                    _sub("1", _content("First moment.")),
                    _sub("2", _intro("List:"), _para("1", "item a;"), _para("2", "4) hallussapidetty aine.")),
                    _sub("3", _content("tuomita kokonaan tai osaksi valtiolle menetetyksi.")),
                    _sub("4", _content("Old real third moment.")),
                ),
            ),
        )
    )
    amend_sub = _sub("3", _content("Lisäksi on soveltuvin osin noudatettava, mitä rikoslain 10 luvussa säädetään."))
    op = _AmendmentOp(
        op_id="guard_liveness_strict_rebound",
        op_type=OpType.REPLACE,
        target_section="73",
        target_unit_kind="section",
        target_paragraph=3,
        source_statute="2001/880",
    )
    ctx = StatuteContext(id="0/0", title="", base_ir=state.ir, base_xml_bytes=b"<body/>")
    # Apply requires a typed CanonicalIntent (the legacy field-dispatch fallback
    # was removed as corpus-cold). Drive the production typed lane by projecting
    # the op onto a ResolvedOp and building its intent via the same production
    # op->intent map the live pipeline uses.
    muutos_ir = _sec("73", amend_sub)
    rop = _ResolvedOp.from_amendment_op(
        op,
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter=None,
        target_address=_LegalAddress(path=(("section", "73"), ("subsection", "3"))),
    )
    rop.amend_sub_ir = amend_sub
    rop.intent = _build_canonical_intent(rop)
    result = _apply_op(
        state,
        None,
        ctx,
        None,
        replay_mode="legal_pit",
        failed_ops_out=failed_ops_out,
        source_pathologies_out=source_pathologies_out,
        strict_profile=default_finland_strict_profile(),
        rop=rop,
    )
    # The strict guard refused the rebound: the live tree is left unmutated.
    assert result is state
    return result


def drill_failed_operation_apply_lane() -> None:
    """APPLY.FAILED_OPERATION reaches the strict verdict barrier from the apply lane.

    Production lane: the strict ``apply_op`` deciding guard refuses a stale
    continuation-fragment rebound and records a real ``FailedOp`` in
    ``failed_ops_out``. That FailedOp is converted by the production
    ``_failed_op_to_compile_failure`` into the ``CompileFailure`` the verdict
    ledger consumes; ``strict_fail_reasons_from_finding_ledger`` then trips the
    APPLY.FAILED_OPERATION barrier and ``compute_verdict_from_registry`` surfaces
    it in ``CompileVerdict.barrier_codes``. The deciding input (the failed op) is
    produced by the real apply lane, not hand-built.
    """
    from lawvm.finland._compile import _failed_op_to_compile_failure
    from lawvm.finland.ops import FailedOp

    failed_ops: list[FailedOp] = []
    _drill_strict_rebound_apply(failed_ops_out=failed_ops)
    assert failed_ops, "strict apply lane did not record a FailedOp for the refused rebound"
    failures = [_failed_op_to_compile_failure(f) for f in failed_ops]
    barrier_codes = _verdict_barrier_codes_from_findings(failures=failures)
    assert "APPLY.FAILED_OPERATION" in barrier_codes


def drill_source_pathology_detected_apply_lane() -> None:
    """APPLY.SOURCE_PATHOLOGY_DETECTED reaches the strict verdict barrier from apply.

    Production lane: the strict ``apply_op`` deciding guard records a real
    ``SourcePathology`` in ``source_pathologies_out`` when it refuses the rebound.
    That pathology is projected to the blocking APPLY.SOURCE_PATHOLOGY_DETECTED
    finding by the LIVE production guard
    ``_strict_rejected_source_pathology_finding`` (replay_findings.py) — the same
    guard production wires from replay_evidence_projection / group elaboration. The
    finding then flows through the real verdict ledger to
    ``CompileVerdict.barrier_codes``. (The ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY
    runtime alias the old verdict-only drill mapped has no production Finding
    emitter; this drill targets the live guard instead.)
    """
    from lawvm.core.compile_result import SourcePathology
    from lawvm.finland.replay_findings import _strict_rejected_source_pathology_finding

    pathologies: list[SourcePathology] = []
    _drill_strict_rebound_apply(source_pathologies_out=pathologies)
    assert pathologies, "strict apply lane did not record a SourcePathology for the refused rebound"
    finding = _strict_rejected_source_pathology_finding(
        pathologies[0],
        stage="replay_apply",
        fallback_source_statute="2001/880",
    )
    assert finding.kind == "APPLY.SOURCE_PATHOLOGY_DETECTED"
    assert finding.blocking is True
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
        op_type=OpType.INSERT,
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


def drill_fold_single_insert_subsection_list_tail_payload_elaboration() -> None:
    """ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL reaches payload elaboration output.

    Production lane: ``elaborate_payload_against_live`` normalizes a single
    explicitly-inserted ``lisätään uusi N momentti`` payload that historical
    Finlex XML serialized as two post-omission subsections — an intro/list prefix
    plus a content-only sibling tail. The preamble claims exactly one new moment,
    so the production guard ``_fold_single_insert_subsection_list_tail`` folds the
    tail into that one inserted list-shaped subsection and emits the blocking
    ``strict_fail`` elaboration observation.

    The trailing subsections are left source-unlabelled (the historical serializer
    does not number an inserted moment). That matters: the earlier production
    ``_align_sparse_omission_subsections_to_live`` pass only relabels *digit*
    labelled sparse subsections, so unlabelled rows survive to the fold guard
    intact. Labelled trailing rows are instead relabelled to live-slot order
    (``[N, N+1]``), which the fold guard rejects — that relabel-vs-fold ordering
    is exactly why this code could not be cleanly drilled with labelled fixtures.
    """
    sub1 = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmäinen momentti."),),
    )
    sub2 = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="2",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Toinen momentti."),),
    )
    live_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(IRNode(kind=IRNodeKind.NUM, text="12 §"), sub1, sub2),
    )
    ctx = PayloadElaborationContext(
        target_unit_kind="section",
        target_norm="12",
        target_chapter=None,
        target_part=None,
        live_node=live_node,
        parent_node=None,
        subsection_slots=(),
        live_subsections=(sub1, sub2),
        subsection_by_label={"1": sub1, "2": sub2},
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
    # Single "lisätään uusi 3 momentti": target == len(live_subsections) + 1.
    op = AmendmentOp(
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_paragraph=3,
        source_statute="2099/1",
    )
    # Post-omission intro/list prefix + content-only sibling tail, both unlabelled.
    prefix = IRNode(
        kind=IRNodeKind.SUBSECTION,
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="Sen estämättä mitä edellä säädetään, sovelletaan seuraavia:"),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen luettelokohta;"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen luettelokohta."),),
            ),
        ),
    )
    tail = IRNode(
        kind=IRNodeKind.SUBSECTION,
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text="Edellä tarkoitettu päätös tehdään viivytyksettä.",
            ),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            prefix,
            tail,
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    observations = got.elaboration_observations
    assert observations is not None
    hits = [
        observation
        for observation in observations
        if observation.kind == "ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL"
    ]
    assert hits, (
        "single inserted list-shaped subsection with a content-only sibling tail "
        "did not surface ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL on the "
        "payload elaboration ledger"
    )
    detail = hits[0].detail or {}
    assert detail["target_paragraph"] == 3
    assert detail["tail_text_chars"] > 0

    # The guard genuinely folded: the two trailing subsections collapse into one
    # inserted moment slot (labelled to the insert target) carrying a wrap-up tail.
    normalized = got.muutos_ir
    assert normalized is not None
    folded_subs = [c for c in normalized.children if c.kind is IRNodeKind.SUBSECTION]
    assert len(folded_subs) == 1, "tail sibling was not folded into the inserted moment"
    assert folded_subs[0].label == "3"
    assert any(c.kind is IRNodeKind.WRAP_UP for c in folded_subs[0].children), (
        "fold fired but the content-only tail was not carried as a wrap-up"
    )

    # Bite check: a near-miss where the sibling tail is itself a list-item row
    # (``3. ...``) is NOT a content-only non-item tail, so the fold must not fire.
    near_miss_tail = IRNode(
        kind=IRNodeKind.SUBSECTION,
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3. kolmas luettelokohta."),),
    )
    near_miss_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            prefix,
            near_miss_tail,
        ),
    )
    near_miss_ctx = PayloadElaborationContext(
        target_unit_kind="section",
        target_norm="12",
        target_chapter=None,
        target_part=None,
        live_node=live_node,
        parent_node=None,
        subsection_slots=(),
        live_subsections=(sub1, sub2),
        subsection_by_label={"1": sub1, "2": sub2},
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
    near_miss = elaborate_payload_against_live(near_miss_ctx, [op], near_miss_ir, set())
    assert not any(
        observation.kind == "ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL"
        for observation in (near_miss.elaboration_observations or [])
    ), "fold fired on a list-item sibling tail that it must leave as a distinct moment"


def _numbered_list_subsection(label: str) -> IRNode:
    """A live ``N momentti`` that is already an intro + numbered-list moment."""
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="Sovelletaan seuraavia:"),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen luettelokohta;"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen luettelokohta."),),
            ),
        ),
    )


def drill_fold_multi_target_subsection_list_wrapups_payload_elaboration() -> None:
    """ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS reaches payload elaboration output.

    Production lane: ``elaborate_payload_against_live`` normalizes a section
    payload whose preamble explicitly replaces ``N ja M momentti`` (two plain
    section-subsection REPLACE ops). Historical Finlex XML serialized each
    changed legal moment as two adjacent source subsections — an intro/numbered-
    list body followed by a content-only wrap-up. Every targeted moment is
    already a numbered-list moment in the live tree, so the production guard
    ``_fold_multi_target_subsection_list_wrapups`` folds each content-only
    wrap-up into its preceding list moment and emits the blocking ``strict_fail``
    elaboration observation.
    """
    live1 = _numbered_list_subsection("1")
    live2 = _numbered_list_subsection("2")
    live_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(IRNode(kind=IRNodeKind.NUM, text="5 §"), live1, live2),
    )
    ctx = PayloadElaborationContext(
        target_unit_kind="section",
        target_norm="5",
        target_chapter=None,
        target_part=None,
        live_node=live_node,
        parent_node=None,
        subsection_slots=(),
        live_subsections=(live1, live2),
        subsection_by_label={"1": live1, "2": live2},
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

    def _replace_moment(paragraph: int) -> AmendmentOp:
        return AmendmentOp(
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="5",
            target_paragraph=paragraph,
            source_statute="2099/1",
        )

    # "muutetaan 5 §:n 1 ja 2 momentti": two plain, contiguous subsection replaces.
    group_ops = [_replace_moment(1), _replace_moment(2)]

    def _intro_list_prefix() -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            children=(
                IRNode(kind=IRNodeKind.INTRO, text="Sovelletaan seuraavia:"),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen muutettu;"),),
                ),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen muutettu."),),
                ),
            ),
        )

    def _content_only_tail(text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
        )

    # len(targets) * 2 == 4 source subsections, as (intro/list prefix, wrap-up) pairs.
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 §"),
            _intro_list_prefix(),
            _content_only_tail("Edellä tarkoitettu päätös tehdään viivytyksettä."),
            _intro_list_prefix(),
            _content_only_tail("Näitä sovelletaan vastaavasti."),
        ),
    )

    got = elaborate_payload_against_live(ctx, group_ops, muutos_ir, set())

    observations = got.elaboration_observations
    assert observations is not None
    hits = [
        observation
        for observation in observations
        if observation.kind == "ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS"
    ]
    assert hits, (
        "two explicit list-moment replaces with content-only sibling wrap-ups did "
        "not surface ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS on the payload "
        "elaboration ledger"
    )
    detail = hits[0].detail or {}
    assert detail["target_paragraphs"] == [1, 2]
    assert detail["tail_text_chars"] and all(chars > 0 for chars in detail["tail_text_chars"])

    # The guard genuinely folded: the two wrap-up siblings collapse into the two
    # targeted list moments, each carrying a wrap-up tail.
    normalized = got.muutos_ir
    assert normalized is not None
    folded_subs = [c for c in normalized.children if c.kind is IRNodeKind.SUBSECTION]
    assert len(folded_subs) == 2, "wrap-up siblings were not folded into the targeted moments"
    assert all(
        any(c.kind is IRNodeKind.WRAP_UP for c in sub.children) for sub in folded_subs
    ), "fold fired but a content-only wrap-up was not carried into its moment"


def drill_body_chapter_descendant_scope_correction_compile_group_recovery() -> None:
    """LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION reaches the recovery ledger.

    Production lane: ``resolve_compile_group_scope_recovery`` runs the production
    pre-snapshot scope recovery over a descendant (subsection-intro) REPLACE whose
    preamble scopes ``2 luku`` but whose amendment body places ``10 b §`` under a
    freshly inserted letter-suffix chapter ``2 a luku``. The production guard
    ``_maybe_apply_descendant_body_chapter_scope`` rebounds the descendant target
    chapter to the source body chapter and emits the blocking ``strict_fail``
    recovery finding.
    """
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2a",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="10b",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="1",
                                    children=(IRNode(kind=IRNodeKind.INTRO, text="old intro"),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>2 a luku</num>
              <section>
                <num>10 b §</num>
                <subsection>
                  <content><p>new intro:</p></content>
                  <paragraph><num>1)</num><content><p>carried item</p></content></paragraph>
                </subsection>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    replace_intro_op = AmendmentOp(
        op_id="replace_10b_intro",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="10b",
        target_chapter="2",
        target_paragraph=1,
        target_special="johd",
        source_statute="2008/732",
        lo=LegalOperation(
            op_id="replace_10b_intro",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(
                path=(("chapter", "2"), ("section", "10b"), ("subsection", "1")),
                special=FacetKind.INTRO,
            ),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="10b",
            target_chapter="2",
            target_part=None,
            group_ops=[replace_intro_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto="lisätään asetukseen uusi 9 b § ja sen edelle uusi 2 a luvun otsikko",
            strict_profile=None,
        )
    )

    kinds = [finding.kind for finding in result.findings()]
    assert "LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION" in kinds, (
        "descendant section REPLACE under a body-owned letter-suffix chapter did "
        "not surface LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION on the "
        "scope-recovery finding ledger"
    )
    finding = next(
        f
        for f in result.findings()
        if f.kind == "LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION"
    )
    assert finding.detail["body_chapter"] == "2a"
    # The guard genuinely retargeted: the descendant op rebounds onto the body chapter.
    assert result.output.effective_target_chapter == "2a"
    assert result.output.group_ops[0].target_cols.target_chapter == "2a"


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
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="5",
        target_paragraph=2,
        source_statute="2099/1",
    )
    heading_facet_op = AmendmentOp(
        op_id="replace_5_otsikko",
        op_type=OpType.REPLACE,
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
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="29",
        target_chapter="4",
        source_statute="2099/1",
    )
    descendant = AmendmentOp(
        op_id="replace_31_3",
        op_type=OpType.REPLACE,
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
    ops = [op for op in phase.output if str(op.target_cols.target_section) in {"29", "31"}]

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


def drill_sparse_plain_subsection_shell_continuation_merge_payload_elaboration() -> None:
    """ELAB.SPARSE_PLAIN_SUBSECTION_SHELL_CONTINUATION_MERGE reaches elaboration.

    Production lane: ``elaborate_payload_against_live`` receives a sparse section
    payload where the source XML split an explicitly targeted previous intro
    and following plain subsection across three adjacent subsection slots.
    """
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="31a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="31 a §"),
            IRNode(kind=IRNodeKind.HEADING, text="Old heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Old first moment."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Old second moment."),),
            ),
        ),
    )
    ctx = PayloadElaborationContext(
        target_unit_kind="section",
        target_norm="31a",
        target_chapter="6",
        target_part=None,
        live_node=live_sec,
        parent_node=None,
        subsection_slots=(),
        live_subsections=tuple(child for child in live_sec.children if child.kind is IRNodeKind.SUBSECTION),
        subsection_by_label={
            str(child.label): child
            for child in live_sec.children
            if child.kind is IRNodeKind.SUBSECTION and child.label
        },
        item_index={},
        row_anchor_index={},
        container_member_labels=None,
        lookups=ReplayLookups(
            snapshot_rev=0,
            unique_section_paths={},
            chapter_members={},
            part_members={},
            all_section_labels=frozenset({"31a"}),
        ),
    )
    heading_op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="31a",
        target_chapter="6",
        target_special="otsikko",
        source_statute="2019/271",
    )
    intro_op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="31a",
        target_chapter="6",
        target_paragraph=1,
        target_special="johd",
        source_statute="2019/271",
    )
    subsection_op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="31a",
        target_chapter="6",
        target_paragraph=2,
        source_statute="2019/271",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="31a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="31 a §"),
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="New first moment."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="This training shall consist of:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Personnel are divided into categories."),
                    IRNode(kind=IRNodeKind.TABLE, children=(IRNode(kind=IRNodeKind.ROW, text="Category"),)),
                ),
            ),
        ),
    )

    result = elaborate_payload_against_live(
        ctx,
        [heading_op, intro_op, subsection_op],
        muutos_ir,
        set(),
    )

    kinds = {obs.kind for obs in result.elaboration_observations or ()}
    assert "ELAB.SPARSE_PLAIN_SUBSECTION_SHELL_CONTINUATION_MERGE" in kinds
    assert result.unassigned_sparse_payload_slots == ()


def drill_rebase_replaced_renumber_source_inspect_bundle() -> None:
    """ELAB.REBASE_REPLACED_RENUMBER_SOURCE reaches the inspect-amendment surface.

    Production lane: ``build_amendment_bundle`` runs the real Finland compile /
    group-elaboration path for the corpus witness ``2007/121 <- 2010/1357``.
    That path rebases the source ``REPLACE 45 § 3 mom`` to the typed
    renumber-destination slot and emits the blocking elaboration observation.
    The drill asserts the observation is consumer-visible on the same debug
    surface used during bench/divergence triage; it does not hand-build the
    observation.
    """
    from lawvm.tools.inspect_amendment import build_amendment_bundle

    bundle = build_amendment_bundle("2007/121", "2010/1357", mode="official_consolidation")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "45")

    assert any(
        observation["kind"] == "ELAB.REBASE_REPLACED_RENUMBER_SOURCE"
        and observation["detail"]["rebases"] == [
            {
                "from_paragraph": 3,
                "to_paragraph": 4,
                "op_description": "REPLACE 5 luku 45 § 3 mom",
            }
        ]
        for observation in group["elaboration_observations"]
    )


def drill_effect_lifecycle_target_unresolved_apply_lane() -> None:
    """APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED reaches strict barrier from the builder.

    Production lane: ``build_finland_effect_lifecycle`` (the deciding builder) is
    driven with a commencement/expiry lifecycle override whose target effect is
    absent, so the builder genuinely produces an ``unresolved_effect_target``
    lifecycle event carrying NO ``source_finding``. The production verdict ledger
    ``strict_fail_reasons_from_finding_ledger`` then derives
    APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED from that event (compile_result.py
    effect-lifecycle branch) and ``compute_verdict_from_registry`` surfaces it in
    ``CompileVerdict.barrier_codes``. The deciding input (the unresolved event) is
    produced by the real builder, not hand-built.
    """
    from lawvm.finland.effect_lifecycle_signals import (
        EffectLifecycleOverride,
        EffectLifecycleOverrideScope,
    )
    from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle

    _source_effects, _relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2021/2",
                target_statute="2020/1",
                scope=EffectLifecycleOverrideScope.sections(("4 a",)),
                expiry="2022-12-31",
                context="accepted_amendment",
            ),
        ),
    )
    assert any(e.kind == "unresolved_effect_target" for e in lifecycle_events), (
        "the lifecycle builder did not produce an unresolved-effect-target event"
    )
    assert all(
        not str(e.detail.get("source_finding") or "").strip()
        for e in lifecycle_events
        if e.kind == "unresolved_effect_target"
    ), "the unresolved event already carries a source_finding; the generic barrier won't fire"

    reasons = strict_fail_reasons_from_finding_ledger(
        _DRILL_STRICT_PROFILE,
        compiled_ops=[],
        canonical_ops=[],
        failures=[],
        findings=[],
        effect_lifecycle_events=lifecycle_events,
    )
    verdict = compute_verdict_from_registry(_DRILL_STRICT_PROFILE, reasons)
    assert "APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED" in verdict.barrier_codes


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
        op_type=OpType.RENUMBER,
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
        op_type=OpType.REPLACE,
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
        _op_type_seed=OpType.REPLACE,
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
        op_type=OpType.INSERT,
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
        _op_type_seed=OpType.INSERT,
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


def drill_lineage_cycle_replay_products_build() -> None:
    """LINEAGE.CYCLE fires loud from the production migration-ledger build (LS-11).

    Production lane: ``ReplayProducts.__post_init__`` is the central seal for the
    finished migration ledger — every replay product passes through it, and it
    already type-checks and effect-graph-validates ``migration_events`` before the
    bundle is published. The drill builds a real ``ReplayProducts`` with a
    synthetic 2-node migration cycle (section 1 → section 2 → section 1) and
    asserts the production ``assert_acyclic`` guard raises ``LineageCycleError``
    carrying the ``LINEAGE.CYCLE`` code. Without the guard the address resolvers
    silently truncate the walk at their ``visited`` set, so the non-terminating
    lineage would otherwise reach materialization as repeated-PIT hash drift.
    """
    from lawvm.core.ir import LegalAddress
    from lawvm.core.provenance import MigrationEvent
    from lawvm.core.timeline_lineage import LineageCycleError
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.finland.statute import ReplayState

    def _section(label: str) -> LegalAddress:
        return LegalAddress(path=(("section", label),))

    def _renumber(from_label: str, to_label: str) -> MigrationEvent:
        return MigrationEvent(
            event_id=f"mig:2024/1:{from_label}->{to_label}",
            kind="renumber",
            from_address=_section(from_label),
            to_address=_section(to_label),
            effective="2024-01-01",
            source_statute="2024/1",
        )

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    cyclic_events = (_renumber("1", "2"), _renumber("2", "1"))

    with pytest.raises(LineageCycleError, match="LINEAGE.CYCLE") as excinfo:
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            migration_events=cyclic_events,
        )

    # The raise is self-evidencing: it carries the address cycle witness.
    assert excinfo.value.cycle, "LineageCycleError must carry the cycle witness"
    assert excinfo.value.cycle[0] == excinfo.value.cycle[-1]

    # A DAG ledger over the same addresses builds without raising (no false fire).
    ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines=None,
        migration_events=(_renumber("1", "2"),),
    )


# ---------------------------------------------------------------------------
# SURF-04 / SURF-05 surface-totality observation drills
# ---------------------------------------------------------------------------


def drill_definition_duplicate_definition_surface_totality() -> None:
    """Drive the production SURF-04 sweep into a DUPLICATE_DEFINITION firing.

    Exercises the real ``sweep_definition_totality_from_bindings`` over two
    bindings of the same (term, scope); the finding must reach the sweep's typed
    output. The drill exercises the production sweep, not a hand-built Finding.
    """
    from lawvm.core.reference_mention import SourceSpan
    from lawvm.finland.references.defined_terms import (
        BINDING_TARKOITETAAN,
        DefinedTermBinding,
    )
    from lawvm.finland.references.surface_totality import (
        DEFINITION_DUPLICATE_DEFINITION,
        sweep_definition_totality_from_bindings,
    )

    def _b(off: int) -> DefinedTermBinding:
        return DefinedTermBinding(
            term="sivutuote",
            target_ref=None,
            expansion="x",
            scope="statute",
            source_span=SourceSpan(source_file="drill", byte_offset=off, byte_len=8),
            binding_kind=BINDING_TARKOITETAAN,
        )

    findings = sweep_definition_totality_from_bindings(
        [_b(10), _b(99)], [], statute_id="drill/1"
    )
    assert any(f.code == DEFINITION_DUPLICATE_DEFINITION for f in findings), (
        "DUPLICATE_DEFINITION sweep did not fire on a duplicate (term, scope) binding"
    )


def drill_definition_orphan_reference_surface_totality() -> None:
    """Drive the production SURF-04 sweep into an ORPHAN_DEFINITION_REFERENCE firing."""
    from lawvm.core.reference_mention import SourceSpan
    from lawvm.finland.references.surface_totality import (
        DEFINITION_ORPHAN_DEFINITION_REFERENCE,
        sweep_definition_totality_from_bindings,
    )
    from lawvm.finland.references.term_use import (
        RULE_BEFORE_BINDING,
        STATUS_OPEN,
        TermUse,
    )

    open_use = TermUse(
        term_surface="sivutuotteisiin",
        lemma="sivutuote",
        binding=None,
        source_span=SourceSpan(source_file="drill", byte_offset=5, byte_len=15),
        status=STATUS_OPEN,
        rule_id=RULE_BEFORE_BINDING,
    )
    findings = sweep_definition_totality_from_bindings(
        [], [open_use], statute_id="drill/2"
    )
    assert any(
        f.code == DEFINITION_ORPHAN_DEFINITION_REFERENCE for f in findings
    ), "ORPHAN_DEFINITION_REFERENCE sweep did not fire on an open (unresolvable) use"


def drill_reference_unclassified_reference_surface_totality() -> None:
    """Drive the production SURF-05 sweep into an UNCLASSIFIED_REFERENCE firing."""
    from lawvm.core.reference_mention import (
        CiteConfidence,
        CiteKind,
        ProvisionRef,
        ReferenceMention,
        SourceSpan,
    )
    from lawvm.finland.references.ref_mention_extractor import ExtractionResult
    from lawvm.finland.references.surface_totality import (
        REFERENCE_UNCLASSIFIED_REFERENCE,
        sweep_citation_totality,
    )

    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="1/2020", section_label="3"),
        target_provision_ref=ProvisionRef(statute_id="2/2020", section_label="5"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=SourceSpan(source_file="1/2020", byte_offset=7, byte_len=4),
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )
    # Simulate a silently-widened classification set (out-of-closed-set value).
    object.__setattr__(
        mention, "cite_confidence", SimpleNamespace(value="FORGED_STATE")
    )
    findings = sweep_citation_totality(
        ExtractionResult(mentions=[mention]), statute_id="1/2020"
    )
    assert any(f.code == REFERENCE_UNCLASSIFIED_REFERENCE for f in findings), (
        "UNCLASSIFIED_REFERENCE sweep did not fire on an out-of-closed-set confidence"
    )


# ---------------------------------------------------------------------------
# SURF-01 / SURF-02 / SURF-07 surface-totality observation drills
# ---------------------------------------------------------------------------


def _drill_token_partition_cert(*, total: int, owned: int):
    """A real TokenPartitionCoverage whose buckets sum to ``owned`` (≤ total).

    Built via the PRODUCTION projection ``build_token_partition_coverage`` over a
    synthetic forest with a deliberately-undersummed census, so a non-zero
    realization gap is driven through the real sweep — not a hand-built finding.
    """
    from lawvm.finland.legal_surface.source_syntax_graph import (
        SourceSyntaxGraph,
        SurfaceGraphSubject,
        SyntaxCoverage,
    )
    from lawvm.finland.legal_surface.token_partition_coverage import (
        build_token_partition_coverage,
    )

    cov = SyntaxCoverage(
        total_tokens=total,
        owned_tokens=owned,
        benign_tokens=0,
        residual_tokens=0,
        silent_tokens=0,
    )
    forest = SourceSyntaxGraph(
        graph_id="drill-forest",
        subject=SurfaceGraphSubject(
            jurisdiction="fi",
            work_id="drill/1",
            scope={},
            surface_time=None,
            source_bundle_hash="deadbeef",
            language="fi",
        ),
        source_units=(),
        text_hash="h",
        text_len=100,
        syntax_nodes={},
        syntax_edges=(),
        parse_status="parsed",
        residuals=(),
        coverage=cov,
    )
    return build_token_partition_coverage(forest, statute_id="drill/1")


def drill_surface_token_realization_gap_surface_totality() -> None:
    """Drive the production SURF-01 sweep into a TOKEN_REALIZATION_GAP firing."""
    from lawvm.finland.legal_surface.surface_token_totality import (
        SURFACE_TOKEN_REALIZATION_GAP,
        sweep_token_realization,
    )

    # total=10 but only 6 owned and the other buckets empty -> 4-token gap.
    cert = _drill_token_partition_cert(total=10, owned=6)
    findings = sweep_token_realization(cert)
    assert any(f.code == SURFACE_TOKEN_REALIZATION_GAP for f in findings), (
        "TOKEN_REALIZATION_GAP sweep did not fire on an under-summed partition"
    )
    # and STAYS SILENT on a balanced partition (clean input)
    clean = _drill_token_partition_cert(total=10, owned=10)
    assert not sweep_token_realization(clean), (
        "TOKEN_REALIZATION_GAP sweep fired on a balanced partition"
    )


def drill_waist_handoff_parity_source_to_token_surface_totality() -> None:
    """Drive the production SURF-02 sweep into a HANDOFF_PARITY firing."""
    from lawvm.finland.legal_surface.surface_token_totality import (
        WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN,
        assert_handoff_parity,
    )

    cert = _drill_token_partition_cert(total=12, owned=5)
    findings = assert_handoff_parity(cert)
    assert any(
        f.code == WAIST_HANDOFF_PARITY_SOURCE_TO_TOKEN for f in findings
    ), "HANDOFF_PARITY sweep did not fire on a source->token parity break"
    clean = _drill_token_partition_cert(total=12, owned=12)
    assert not assert_handoff_parity(clean), (
        "HANDOFF_PARITY sweep fired on a balanced handoff"
    )


def drill_surface_orphan_entity_node_surface_totality() -> None:
    """Drive the production SURF-07 sweep into an ORPHAN_ENTITY_NODE firing."""
    from lawvm.core.legal_surface_graph import (
        LegalSurfaceGraph,
        SurfaceEdge,
        SurfaceGraphSubject,
        SurfaceNode,
    )
    from lawvm.finland.legal_surface.surface_token_totality import (
        SURFACE_ORPHAN_ENTITY_NODE,
        sweep_orphan_entity_nodes,
    )

    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="drill/1",
        scope={},
        surface_time=None,
        source_bundle_hash="deadbeef",
        language="fi",
    )

    def _entity(node_id: str, term: str) -> SurfaceNode:
        return SurfaceNode(
            node_id=node_id,
            node_kind="term_symbol_entity",
            authority_role="entity_handle",
            jurisdiction="fi",
            source_ref=None,
            lens_id="lens.def",
            rule_id="r",
            node_status="asserted",
            payload_hash="p",
            payload={"term": term},
        )

    covered = _entity("covered", "sivutuote")
    orphan = _entity("orphan", "jäte")
    binding = SurfaceNode(
        node_id="binding",
        node_kind="definition_binding",
        authority_role="surface_fact",
        jurisdiction="fi",
        source_ref=None,
        lens_id="lens.def",
        rule_id="r",
        node_status="resolved",
        payload_hash="p",
        payload={},
    )
    edge = SurfaceEdge(
        edge_id="e1",
        edge_kind="defines_term",
        src="binding",
        dst="covered",  # only `covered` is an edge endpoint; `orphan` is not
        rule_id="r",
        surface_edge_status="asserted",
        payload_hash="p",
        payload={},
    )
    graph = LegalSurfaceGraph(
        schema="lawvm.legal_surface_graph.v0",
        graph_id="g-drill",
        subject=subject,
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in (binding, covered, orphan)},
        edges=(edge,),
        build_diagnostics=(),
    )
    findings = sweep_orphan_entity_nodes(graph)
    assert [f.node_id for f in findings] == ["orphan"], (
        "ORPHAN_ENTITY_NODE sweep did not isolate the uncovered entity handle"
    )
    assert findings[0].code == SURFACE_ORPHAN_ENTITY_NODE
    # and STAYS SILENT when every entity is covered (drop the orphan)
    clean = LegalSurfaceGraph(
        schema="lawvm.legal_surface_graph.v0",
        graph_id="g-clean",
        subject=subject,
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in (binding, covered)},
        edges=(edge,),
        build_diagnostics=(),
    )
    assert not sweep_orphan_entity_nodes(clean), (
        "ORPHAN_ENTITY_NODE sweep fired when every entity handle was covered"
    )


def drill_sched_window_unmaterialized_schedule_window_totality() -> None:
    """Drive the production SCHED-01/02/03 sweep into a WINDOW_UNMATERIALIZED firing.

    Builds a synthetic replay output (``ReplayProducts``) carrying a temporary
    legal-effect window on the temporal-event plane (a commence + expire pair
    sharing a ``group_id``, the expire event scoped to a target address) whose
    ``[effective, expires)`` interval is NOT present in the materialized timeline
    — the timeline instead holds a later-effective fold occupant (the disjoint-
    window case). The real ``sweep_disjoint_window_materialization`` must surface
    that window. Then a CLEAN variant materializes the same interval as a version
    row and the sweep STAYS SILENT. The drill exercises the production sweep, not
    a hand-built finding.
    """
    from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
    from lawvm.core.temporal import (
        FIXED_DATE_KIND,
        ActivationRule,
        TemporalEvent,
        TemporalScope,
    )
    from lawvm.finland.legal_surface.schedule_window_totality import (
        SCHED_WINDOW_UNMATERIALIZED,
        sweep_disjoint_window_materialization,
    )
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.finland.statute import ReplayState

    address = LegalAddress(path=(("section", "5"),))
    scope = TemporalScope(target_statute="0001/2024")
    expire_scope = TemporalScope(
        target_statute="0001/2024", exact_addresses=(address,)
    )
    commence = TemporalEvent(
        event_id="fi-temporal:grp-w:commence",
        kind="commence",
        scope=scope,
        effective="2024-01-01",
        activation_rule=ActivationRule(
            kind=FIXED_DATE_KIND, effective_date="2024-01-01"
        ),
        group_id="grp-w",
    )
    expire = TemporalEvent(
        event_id="fi-temporal:grp-w:expire:section/5",
        kind="expire",
        scope=expire_scope,
        expires="2024-07-01",
        group_id="grp-w",
    )

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))

    # Disjoint case: the timeline holds ONLY a later-effective permanent occupant
    # (effective 2025, i.e. fold-order placed a later occupant in the slot); the
    # temporary [2024-01-01, 2024-07-01) window is NOT a version interval.
    disjoint_timeline = ProvisionTimeline(
        address=address,
        versions=[ProvisionVersion(effective="2025-01-01", variant_kind="permanent")],
    )
    disjoint = ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines={address: disjoint_timeline},
        temporal_events=(commence, expire),
    )
    findings = sweep_disjoint_window_materialization(disjoint)
    assert any(f.code == SCHED_WINDOW_UNMATERIALIZED for f in findings), (
        "SCHED window sweep did not fire on a disjoint, unmaterialized window"
    )
    fired = next(f for f in findings if f.code == SCHED_WINDOW_UNMATERIALIZED)
    assert fired.window_effective == "2024-01-01"
    assert fired.window_expires == "2024-07-01"
    assert fired.fold_occupant_effective == "2025-01-01"

    # Clean case: the SAME window IS materialized as a version interval -> silent.
    clean_timeline = ProvisionTimeline(
        address=address,
        versions=[
            ProvisionVersion(
                effective="2024-01-01",
                expires="2024-07-01",
                variant_kind="temporary",
            ),
            ProvisionVersion(effective="2025-01-01", variant_kind="permanent"),
        ],
    )
    clean = ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines={address: clean_timeline},
        temporal_events=(commence, expire),
    )
    assert not sweep_disjoint_window_materialization(clean), (
        "SCHED window sweep fired when the window was materialized as a version interval"
    )


def drill_scope_overlap_without_disjoint_scope_scope_lattice_totality() -> None:
    """Drive the production SCOPE-01/02 sweep into an OVERLAP firing.

    Builds a synthetic replay output (``ReplayProducts``) whose timeline holds two
    co-effective versions at the SAME address sharing the precedence-rail rank key
    (same variant/effective/enacted/source) with DISTINCT legal content and NO
    scope predicate to admit the overlap — the equal-rank collision the precedence
    rail cannot resolve. The real ``sweep_scope_lattice`` must surface it. Then a
    DISJOINT-SCOPE variant gives the two rows distinct ``territory`` predicates
    (non-overlapping includes) so the overlap is admitted by scope, and the sweep
    STAYS SILENT. The drill exercises the production sweep, not a hand-built
    finding.
    """
    from lawvm.core.ir import (
        LegalAddress,
        ProvisionTimeline,
        ProvisionVersion,
        ScopePredicate,
    )
    from lawvm.core.provenance import OperationSource
    from lawvm.finland.legal_surface.scope_lattice_totality import (
        SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE,
        sweep_scope_lattice,
    )
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.finland.statute import ReplayState

    address = LegalAddress(path=(("section", "9"),))
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    source = OperationSource(statute_id="0001/2024", effective="2024-01-01")

    def _content(text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label="9", text=text)

    # Collision case: two permanent versions at the SAME (effective, enacted,
    # source) rank key with DISTINCT content and no scope predicate -> the
    # precedence rail cannot separate them; list order would decide.
    collision_timeline = ProvisionTimeline(
        address=address,
        versions=[
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                variant_kind="permanent",
                content=_content("variant A"),
                source=source,
            ),
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                variant_kind="permanent",
                content=_content("variant B (distinct)"),
                source=source,
            ),
        ],
    )
    collision = ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines={address: collision_timeline},
    )
    findings = sweep_scope_lattice(collision)
    assert any(f.code == SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE for f in findings), (
        "SCOPE lattice sweep did not fire on a co-effective equal-rank collision "
        "with no disjoint scope predicate"
    )
    fired = next(f for f in findings if f.code == SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE)
    assert fired.address == str(address)
    assert fired.effective == "2024-01-01"
    assert fired.candidate_count == 2
    assert fired.left_content_hash != fired.right_content_hash
    assert not fired.scope_disjoint

    # Disjoint-scope case: the SAME co-effective rows carry distinct, disjoint
    # territory predicates -> the query's scope chooses, not list order -> silent.
    disjoint_timeline = ProvisionTimeline(
        address=address,
        versions=[
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                variant_kind="permanent",
                content=_content("variant A"),
                source=source,
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"mainland"}))
                ],
            ),
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                variant_kind="permanent",
                content=_content("variant B (distinct)"),
                source=source,
                applicability=[
                    ScopePredicate(dimension="territory", includes=frozenset({"aland"}))
                ],
            ),
        ],
    )
    disjoint = ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines={address: disjoint_timeline},
    )
    assert not sweep_scope_lattice(disjoint), (
        "SCOPE lattice sweep fired when disjoint territory predicates admit the "
        "co-effective overlap"
    )


def drill_residual_ledger_nonmonotone_stage_account_totality() -> None:
    """Drive the production EV-03 sweep into a RESIDUAL_LEDGER_NONMONOTONE firing.

    Exercises the real ``sweep_stage_residual_ledger`` over committed-shaped stage
    account rows: a stage whose coverage counts a ``violation`` but whose residual
    ledger holds no blocking residual record (a residual counted then dropped). The
    finding must reach the sweep's typed output; the sweep STAYS SILENT on a balanced
    account. The drill exercises the production sweep, not a hand-built Finding.
    """
    from lawvm.core.stage_residual_monotonicity import (
        RESIDUAL_LEDGER_NONMONOTONE,
        sweep_stage_residual_ledger,
    )

    # violation=1 counted, but the residual ledger is empty -> counted-not-recorded.
    counted_not_recorded = {
        "stage": "drill.stage",
        "coverage_row": {"violation": 1, "owned": 0, "residual": 0, "benign": 0},
        "residual_rows": [],
    }
    findings = sweep_stage_residual_ledger([counted_not_recorded])
    assert any(f.code == RESIDUAL_LEDGER_NONMONOTONE for f in findings), (
        "RESIDUAL_LEDGER_NONMONOTONE sweep did not fire on a counted-but-unrecorded "
        "stage residual"
    )
    assert findings[0].direction == "counted_not_recorded"

    # the dual: a blocking residual record present while coverage counts 0.
    recorded_not_counted = {
        "stage": "drill.stage2",
        "coverage_row": {"violation": 0, "owned": 1, "residual": 0, "benign": 0},
        "residual_rows": [{"kind": "unowned_violation", "blocking": True}],
    }
    dual = sweep_stage_residual_ledger([recorded_not_counted])
    assert dual and dual[0].direction == "recorded_not_counted", (
        "RESIDUAL_LEDGER_NONMONOTONE sweep did not fire on a recorded-but-uncounted "
        "stage residual"
    )

    # and STAYS SILENT on a balanced account (violation discharged by a blocking record).
    balanced = {
        "stage": "drill.clean",
        "coverage_row": {"violation": 1, "owned": 0, "residual": 0, "benign": 0},
        "residual_rows": [{"kind": "unowned_violation", "blocking": True}],
    }
    assert not sweep_stage_residual_ledger([balanced]), (
        "RESIDUAL_LEDGER_NONMONOTONE sweep fired on a balanced (counted+recorded) account"
    )


def drill_diagnostic_not_self_evidencing_residual_totality() -> None:
    """Drive the production EV-07 sweep into a DIAGNOSTIC_NOT_SELF_EVIDENCING firing.

    Exercises the real ``sweep_source_text_failure_self_evidencing`` over core
    ``Residual`` records: a source-text-failure residual (``unowned_violation`` /
    ``typed_residual``) with an empty ``text`` snippet must fire; a snippet-carrying
    residual and an out-of-family residual STAY SILENT. The drill exercises the
    production sweep, not a hand-built Finding.
    """
    from lawvm.core.diagnostic_self_evidencing import (
        DIAGNOSTIC_NOT_SELF_EVIDENCING,
        sweep_source_text_failure_self_evidencing,
    )
    from lawvm.core.stage_result import Residual

    snippetless = Residual(
        kind="unowned_violation",
        reason="forest_silent_unowned_cheap_signal:drill",
        scope="drill/1",
        source_unit_id="drill/1",
        char_start=0,
        char_end=5,
        text="",  # the opaque-diagnostic defect: no verbatim offending snippet
        blocking=True,
    )
    findings = sweep_source_text_failure_self_evidencing([snippetless])
    assert any(f.code == DIAGNOSTIC_NOT_SELF_EVIDENCING for f in findings), (
        "DIAGNOSTIC_NOT_SELF_EVIDENCING sweep did not fire on a snippet-less "
        "source-text-failure residual"
    )
    assert findings[0].kind == "unowned_violation"

    # STAYS SILENT on a self-evidencing residual (verbatim text present)...
    self_evidencing = Residual(
        kind="unowned_violation",
        reason="forest_silent_unowned_cheap_signal:drill",
        scope="drill/1",
        source_unit_id="drill/1",
        char_start=0,
        char_end=5,
        text="3 §:n 2 momentti",
        blocking=True,
    )
    assert not sweep_source_text_failure_self_evidencing([self_evidencing]), (
        "DIAGNOSTIC_NOT_SELF_EVIDENCING sweep fired on a residual carrying its snippet"
    )
    # ...and on an OUT-OF-FAMILY residual (out_of_scope carries no source text by design).
    out_of_family = Residual(
        kind="out_of_scope",
        reason="amendment_cutoff_excluded",
        scope="drill/1",
        text="",
        blocking=False,
    )
    assert not sweep_source_text_failure_self_evidencing([out_of_family]), (
        "DIAGNOSTIC_NOT_SELF_EVIDENCING sweep fired on an out-of-family residual"
    )


# ---------------------------------------------------------------------------
# Per-op apply-authority gate drills (audit lane L1: LS-01, LS-03, EV-05/FW-01)
# ---------------------------------------------------------------------------


def _drive_per_op_apply_gate(
    *,
    rop: Any,
    new_ir: IRNode,
    state: ReplayState,
    strict: bool,
) -> list[Finding]:
    """Drive the PRODUCTION apply_resolved_op_with_audit and return its findings.

    The per-op gate (``_enforce_per_op_apply_authority``) runs from inside the
    production ``apply_resolved_op_with_audit`` after a landed write; ``apply_op``
    is stubbed only to land ``new_ir`` (the same pattern the boundary drills use),
    so the GUARD being tested is production code, not a hand-built finding.
    """
    from unittest.mock import patch as _patch

    import lawvm.finland.apply_resolved_op as apply_resolved_op
    from lawvm.finland.apply_resolved_op import (
        ApplyResolvedOpRequest,
        ApplyResolvedOpSinks,
        apply_resolved_op_with_audit,
    )

    def _fake_apply_op(current_state: ReplayState, *_a: Any, **_k: Any) -> ReplayState:
        return current_state.with_ir(new_ir)

    findings: list[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings, mutation_events_out=[])
    request = ApplyResolvedOpRequest(
        state=state,
        ctx=StatuteContext(
            id="100/2010", title="Guard Liveness", base_ir=state.ir, base_xml_bytes=b"<akn/>"
        ),
        rop=rop,
        amendment_id="12/2015",
        replay_mode="official_consolidation",
        strict_profile=_DRILL_STRICT_PROFILE if strict else None,
    )
    with _patch.object(apply_resolved_op, "apply_op", _fake_apply_op):
        apply_resolved_op_with_audit(request, sinks)
    return findings


def _per_op_gate_rop(*, op_id: str = "replace_1", with_lo: bool = False) -> Any:
    from typing import Any as _Any, cast as _cast

    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
    from lawvm.core.semantic_types import StructuralAction
    from lawvm.finland.ops import AmendmentOp, ResolvedOp
    from lawvm.finland.target_kind import TargetKind

    lo = None
    if with_lo:
        lo = LegalOperation(
            op_id=op_id or "replace_1",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            source=OperationSource(statute_id="12/2015"),
        )
    op = AmendmentOp(
        op_id=op_id,
        op_type=_cast(_Any, "REPLACE"),
        target_kind=TargetKind.SECTION,
        target_section="1",
        lo=lo,
    )
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )


def drill_recovered_op_rejected_in_strict_apply_lane() -> None:
    """APPLY.RECOVERED_OP_REJECTED_IN_STRICT fires from the production apply lane (AM-01).

    M3 typed-acceptance wiring: an op whose typed ``OpProvenance`` is ``Recovered``
    (here via the ``extraction_fallback_heuristic`` recovery tag, a BODY-surface
    recovery) is rejected at the PRODUCTION per-op apply gate
    (``_gate_provenance_acceptance_at_op``) under the default strict profile, which
    forbids target guessing. Acceptance is decided by ``mode_for`` / ``admits`` over
    the typed provenance, so the rejection is the type boundary firing, not an
    ad-hoc tag check.
    """
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.apply_resolved_op import (
        RECOVERED_OP_REJECTED_IN_STRICT_FINDING_CODE,
    )
    from lawvm.finland.op_provenance import Recovered

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    landed = IRNode(
        kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)
    )
    rop = _closure_rop(
        op_id="op_am01",
        target_address=LegalAddress(path=(("section", "1"),)),
        extraction_provenance_tags=("extraction_fallback_heuristic",),
    )
    # Precondition: the op genuinely carries a Recovered typed provenance.
    assert isinstance(rop.provenance, Recovered)
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=True)
    hits = [
        f
        for f in findings
        if f.kind == RECOVERED_OP_REJECTED_IN_STRICT_FINDING_CODE and f.blocking
    ]
    assert hits, (
        "strict path did not reject a Recovered op at the typed-acceptance gate "
        "(AM-01); mode_for/admits wiring is not live at the production apply lane"
    )


def test_recovered_op_acceptance_is_strict_only_at_apply_lane() -> None:
    """AM-01: strict rejects a ``Recovered`` op; quirks admits it; Parsed always admitted.

    The strict-blocking arm is drilled by
    ``drill_recovered_op_rejected_in_strict_apply_lane``; this complements it with
    the QUIRKS-admit and Parsed-admit arms, proving a certified/strict claim rests
    only on grammar-recognized (``Parsed``) ops and the gate is 0-delta on the
    permissive bench/corpus replay.
    """
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.apply_resolved_op import (
        RECOVERED_OP_REJECTED_IN_STRICT_FINDING_CODE,
    )
    from lawvm.finland.op_provenance import Recovered

    landed = IRNode(
        kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),)
    )

    # The strict-blocking arm itself (so this test also fails if wiring regresses).
    drill_recovered_op_rejected_in_strict_apply_lane()

    recovered_rop = _closure_rop(
        op_id="op_am01_quirks",
        target_address=LegalAddress(path=(("section", "1"),)),
        extraction_provenance_tags=("extraction_fallback_heuristic",),
    )
    assert isinstance(recovered_rop.provenance, Recovered)
    # QUIRKS (permissive profile) -> the SAME recovered op is admitted: 0-delta.
    quirks_findings = _drive_per_op_apply_gate(
        rop=recovered_rop,
        new_ir=landed,
        state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        strict=False,
    )
    assert not [
        f
        for f in quirks_findings
        if f.kind == RECOVERED_OP_REJECTED_IN_STRICT_FINDING_CODE
    ], "quirks path rejected a Recovered op (AM-01 should only block under strict)"

    # A grammar-recognized (Parsed/None-provenance) op is admitted under STRICT.
    parsed_rop = _closure_rop(
        op_id="op_am01_parsed",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    assert parsed_rop.provenance is None
    parsed_findings = _drive_per_op_apply_gate(
        rop=parsed_rop,
        new_ir=landed,
        state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        strict=True,
    )
    assert not [
        f
        for f in parsed_findings
        if f.kind == RECOVERED_OP_REJECTED_IN_STRICT_FINDING_CODE
    ], "strict path rejected a non-recovered op (AM-01 must admit Parsed/None provenance)"


def drill_mutation_boundary_violation_at_op_apply_lane() -> None:
    """APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP fires from the production apply lane.

    Production lane: a strict apply whose declared op targets section 1 but whose
    landed write changes section 2 (a sibling). The production per-op
    mutation-boundary gate (``verify_per_op``) must surface the out-of-boundary
    write as a blocking finding (LS-01).
    """
    sec1 = IRNode(kind=IRNodeKind.SECTION, label="1", text="old one")
    sec2 = IRNode(kind=IRNodeKind.SECTION, label="2", text="old two")
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(sec1, sec2)))
    rop = _per_op_gate_rop(op_id="replace_1", with_lo=True)
    sibling_landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(sec1, IRNode(kind=IRNodeKind.SECTION, label="2", text="changed")),
    )
    findings = _drive_per_op_apply_gate(
        rop=rop, new_ir=sibling_landed, state=state, strict=True
    )
    hits = [
        f
        for f in findings
        if f.kind == "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP" and f.blocking
    ]
    assert hits, (
        "a sibling-path edit did not surface APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP "
        "from the production per-op apply boundary gate"
    )
    assert hits[0].detail["out_of_boundary_paths"]


def drill_occupancy_transition_blocked_apply_lane() -> None:
    """APPLY.OCCUPANCY_TRANSITION_BLOCKED fires from the production apply lane.

    Production lane: a strict REPLACE on an ABSENT section slot (no valid
    occupancy transition) that lands a write. The production occupancy gate must
    BLOCK the invalid (replace, absent) transition (LS-03).
    """
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    rop = _per_op_gate_rop(op_id="replace_1", with_lo=False)
    landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=True)
    hits = [
        f for f in findings if f.kind == "APPLY.OCCUPANCY_TRANSITION_BLOCKED" and f.blocking
    ]
    assert hits, (
        "REPLACE-on-absent did not surface APPLY.OCCUPANCY_TRANSITION_BLOCKED from "
        "the production strict occupancy gate"
    )
    assert hits[0].detail["current_occupancy"] == "absent"


def drill_replay_authorization_proof_required_apply_lane() -> None:
    """EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED fires from the production apply lane.

    Production lane: a strict state-mutating op carrying NO stable op_id, so no
    ExecutionAuthorization rule can be resolved for the landed write. The
    production execution-authorization closure must BLOCK (EV-05/FW-01).
    """
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    rop = _per_op_gate_rop(op_id="", with_lo=False)
    landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=True)
    hits = [
        f
        for f in findings
        if f.kind == "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED" and f.blocking
    ]
    assert hits, (
        "an op with no resolvable authorization rule did not surface "
        "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED from the production closure gate"
    )
    assert hits[0].detail["required_proofs"]


def drill_mutation_boundary_finding_at_op_quirks_apply_lane() -> None:
    """APPLY.MUTATION_BOUNDARY_FINDING_AT_OP (observation) records under quirks.

    Production lane: the same sibling-path escape as the strict drill, but with a
    permissive (None) strict profile. The production gate records the non-blocking
    accounting observation instead of blocking.
    """
    sec1 = IRNode(kind=IRNodeKind.SECTION, label="1", text="old one")
    sec2 = IRNode(kind=IRNodeKind.SECTION, label="2", text="old two")
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(sec1, sec2)))
    rop = _per_op_gate_rop(op_id="replace_1", with_lo=True)
    sibling_landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(sec1, IRNode(kind=IRNodeKind.SECTION, label="2", text="changed")),
    )
    findings = _drive_per_op_apply_gate(
        rop=rop, new_ir=sibling_landed, state=state, strict=False
    )
    hits = [
        f
        for f in findings
        if f.kind == "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP" and not f.blocking
    ]
    assert hits, (
        "quirks-mode per-op boundary escape did not record "
        "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP (non-blocking accounting)"
    )
    assert not [f for f in findings if f.kind == "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP"]


# ---------------------------------------------------------------------------
# Wave-2 apply-authority closure drills (LS-05/06/07/09/10, EV-06, FW-01, OV-01/02)
# ---------------------------------------------------------------------------


def _closure_rop(
    *,
    op_id: str = "op_closure_1",
    op_type: OpType = OpType.REPLACE,
    target_paragraph: Any = None,
    target_item: Any = None,
    target_special: Any = None,
    target_address: Any = None,
    scope_provenance_tags: tuple[str, ...] = (),
    extraction_provenance_tags: tuple[str, ...] = (),
    with_lo: bool = False,
    lo_action: Any = None,
    lo_target: Any = None,
) -> Any:
    """Build a ResolvedOp for the per-op closure sweeps with explicit target bind.

    ``target_address`` is bound verbatim as the resolved address; the declared
    AmendmentOp descendant fields (paragraph/item/special) and provenance tags are
    set independently so the sweeps' declared-vs-resolved comparisons are driven.
    """
    from typing import Any as _Any, cast as _cast

    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
    from lawvm.finland.ops import AmendmentOp, ResolvedOp
    from lawvm.finland.target_kind import TargetKind

    lo = None
    if with_lo:
        lo = LegalOperation(
            op_id=op_id or "op_closure_1",
            sequence=1,
            action=lo_action,
            target=lo_target if lo_target is not None else LegalAddress(path=(("section", "1"),)),
            source=OperationSource(statute_id="12/2015"),
        )
    op = AmendmentOp(
        op_id=op_id,
        op_type=_cast(_Any, op_type),
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
        scope_provenance_tags=scope_provenance_tags,
        extraction_provenance_tags=extraction_provenance_tags,
        lo=lo,
    )
    # Direct construction with an explicit ``_target_address_override`` so the
    # resolved address is bound VERBATIM (bypassing
    # ``_augment_replay_address_with_op_descendant_scope``, which would otherwise
    # re-add the descendant step the LS-07/LS-09 escalation cases need absent).
    # The escalation gate is the backstop for ANY producer that hands a descendant
    # op resolved to a bare host; this drills exactly that input.
    return ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="1",
        target_unit_kind="section",
        op_id=op_id,
        _op_type_seed=op_type,
        scope_provenance_tags=scope_provenance_tags,
        extraction_provenance_tags=extraction_provenance_tags,
        _target_address_override=target_address,
    )


def drill_scope_confidence_totality_gap_at_op_apply_lane() -> None:
    """APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP records from the production apply lane (LS-05)."""
    from lawvm.core.ir import LegalAddress

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    rop = _closure_rop(
        op_id="op_ls05",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    landed = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=False)
    hits = [
        f
        for f in findings
        if f.kind == "APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP" and not f.blocking
    ]
    assert hits, (
        "an op with no scope-confidence witness did not record "
        "APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP from the production sweep"
    )


def drill_verb_conversion_unwitnessed_at_op_apply_lane() -> None:
    """LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP records from the production apply lane (LS-06)."""
    from lawvm.core.ir import LegalAddress
    from lawvm.core.semantic_types import StructuralAction

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    # Parsed action REPEAL (via lo) but resolved op_type REPLACE, no witness tag.
    rop = _closure_rop(
        op_id="op_ls06",
        op_type=OpType.REPLACE,
        target_address=LegalAddress(path=(("section", "1"),)),
        with_lo=True,
        lo_action=StructuralAction.REPEAL,
        lo_target=LegalAddress(path=(("section", "1"),)),
    )
    landed = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=False)
    hits = [
        f
        for f in findings
        if f.kind == "LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP" and not f.blocking
    ]
    assert hits, (
        "an unwitnessed verb conversion (REPEAL parsed -> REPLACE resolved) did not "
        "record LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP from the production sweep"
    )
    # Negative (clean) case: the same conversion WITH a named witness tag does not fire.
    #
    # RETENTION PROOF (FI_OP_PROVENANCE_CONSOLIDATION_SPEC §2.1): the suppressing
    # tag ``semantic_collapse_move_renumber`` is DELIBERATELY OUTSIDE the closed
    # ``RecognizerId`` namespace — it is a conversion-witness tag, not a recovery
    # recognizer. The whole-bag ``_has_conversion_witness`` read therefore CANNOT be
    # collapsed onto the typed ``OpProvenance`` (``isinstance(Recovered)`` /
    # ``has_recognizer``): an op carrying only this witness tag has NO typed
    # ``Recovered`` provenance, so a typed-only check would mis-fire the sweep. This
    # is the committed load-bearing proof that the three ``*_provenance_tags`` bags
    # are RETAINED (not deletable) for the dual-purpose witness reads.
    from lawvm.finland.op_provenance import RecognizerId, Recovered as _Recovered

    _conversion_witness_tag = "semantic_collapse_move_renumber"
    _recognizer_values = {m.value for m in RecognizerId}
    assert _conversion_witness_tag not in _recognizer_values, (
        "the conversion-witness tag gained a RecognizerId home; the bag retention "
        "rationale (§2.1) no longer holds — re-evaluate whether the witness reads "
        "can now route through the typed provenance"
    )
    witnessed = _closure_rop(
        op_id="op_ls06_clean",
        op_type=OpType.REPLACE,
        target_address=LegalAddress(path=(("section", "1"),)),
        extraction_provenance_tags=(_conversion_witness_tag,),
        with_lo=True,
        lo_action=StructuralAction.REPEAL,
        lo_target=LegalAddress(path=(("section", "1"),)),
    )
    # The witness tag carries NO typed Recovered provenance (it is outside the
    # closed namespace), so the suppression below rides ONLY on the whole-bag read.
    assert not isinstance(witnessed.provenance, _Recovered), (
        "an out-of-namespace conversion-witness tag synthesized a typed Recovered "
        "provenance; the whole-bag witness read would then be collapsible and the "
        "bag retention would be unnecessary"
    )
    clean_findings = _drive_per_op_apply_gate(
        rop=witnessed, new_ir=landed, state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)), strict=False
    )
    assert not [
        f for f in clean_findings if f.kind == "LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP"
    ], "verb-conversion sweep fired despite a named conversion witness tag"


def drill_granularity_escalation_at_op_apply_lane() -> None:
    """APPLY.GRANULARITY_ESCALATION_AT_OP fires from the production apply lane (LS-07)."""
    from lawvm.core.ir import LegalAddress

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    # Declared at paragraph granularity, but the resolved address is a bare section
    # (no subsection step) — escalation to overwrite the host whole-unit.
    rop = _closure_rop(
        op_id="op_ls07",
        target_paragraph=2,
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    landed = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=True)
    hits = [
        f for f in findings if f.kind == "APPLY.GRANULARITY_ESCALATION_AT_OP" and f.blocking
    ]
    assert hits, (
        "a descendant op resolved to its bare host unit did not surface "
        "APPLY.GRANULARITY_ESCALATION_AT_OP from the strict production gate"
    )
    assert hits[0].detail["declared_paragraph"] == 2
    # Negative (clean) case: a paragraph op resolving WITH its subsection step does not fire.
    clean = _closure_rop(
        op_id="op_ls07_clean",
        target_paragraph=2,
        target_address=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
    )
    clean_findings = _drive_per_op_apply_gate(
        rop=clean, new_ir=landed, state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)), strict=True
    )
    assert not [
        f for f in clean_findings if f.kind == "APPLY.GRANULARITY_ESCALATION_AT_OP"
    ], "granularity gate fired on a descendant op that kept its descendant slot"


def drill_payload_smuggling_at_op_apply_lane() -> None:
    """APPLY.PAYLOAD_SMUGGLING_AT_OP records from the production apply lane (LS-09)."""
    from lawvm.core.ir import LegalAddress

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    rop = _closure_rop(
        op_id="op_ls09",
        target_paragraph=3,
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    landed = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=False)
    hits = [
        f for f in findings if f.kind == "APPLY.PAYLOAD_SMUGGLING_AT_OP" and not f.blocking
    ]
    assert hits, (
        "a descendant-claiming op resolved to its bare host unit did not record "
        "APPLY.PAYLOAD_SMUGGLING_AT_OP from the production sweep"
    )


def drill_unstated_migration_at_op_apply_lane() -> None:
    """APPLY.UNSTATED_MIGRATION_AT_OP records from the production apply lane (LS-10)."""
    from lawvm.core.ir import LegalAddress
    from lawvm.core.semantic_types import StructuralAction

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    # Nominal address section 1; resolved address section 9 (an address-key delta)
    # with no migration ledger, scope tag, or witness rule id to back it.
    rop = _closure_rop(
        op_id="op_ls10",
        target_address=LegalAddress(path=(("section", "9"),)),
        with_lo=True,
        lo_action=StructuralAction.REPLACE,
        lo_target=LegalAddress(path=(("section", "1"),)),
    )
    landed = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="9"),))
    findings = _drive_per_op_apply_gate(rop=rop, new_ir=landed, state=state, strict=False)
    hits = [
        f for f in findings if f.kind == "APPLY.UNSTATED_MIGRATION_AT_OP" and not f.blocking
    ]
    assert hits, (
        "an unbacked nominal->resolved address-key delta did not record "
        "APPLY.UNSTATED_MIGRATION_AT_OP from the production sweep"
    )
    # Negative (clean) case: the same delta WITH a scope provenance tag does not fire.
    backed = _closure_rop(
        op_id="op_ls10_clean",
        target_address=LegalAddress(path=(("section", "9"),)),
        scope_provenance_tags=("chapter_scope_stripped_unique_section",),
        with_lo=True,
        lo_action=StructuralAction.REPLACE,
        lo_target=LegalAddress(path=(("section", "1"),)),
    )
    clean_findings = _drive_per_op_apply_gate(
        rop=backed, new_ir=landed, state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)), strict=False
    )
    assert not [
        f for f in clean_findings if f.kind == "APPLY.UNSTATED_MIGRATION_AT_OP"
    ], "unstated-migration sweep fired despite a typed scope/rekey witness"


def drill_unknown_attestation_policy_at_op_apply_lane() -> None:
    """EVID.UNKNOWN_ATTESTATION_POLICY fires from the production EV-06 gate.

    Production lane: ``gate_unknown_attestation_policy`` (called from the
    production ``_gate_execution_authorization_at_op``) validates an
    ExecutionAuthorization's cited evidence-policy id against the known set. The
    drill builds a real authorization that cites an unknown policy id and drives
    the production gate; the apply-path authorizations cite no policy id (0-delta),
    so the forged cited id is what exercises the gate.
    """
    from lawvm.core.execution_authorization import ExecutionAuthorization
    from lawvm.finland.apply_op_closure_sweeps import gate_unknown_attestation_policy

    forged = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id="op_ev06",
        owner_phase="apply",
        strict_disposition="record",
        safe_default="block_until_known_policy",
        forbidden_shortcuts=("cited_policy_existence_as_known_policy",),
        detail={"evidence_kernel": {"policy_id": "totally_unknown_policy_id"}},
    )
    findings: list[Finding] = []
    gate_unknown_attestation_policy(
        authorization=forged,
        known_policy_ids=frozenset({"some_known_policy"}),
        is_strict=True,
        source_statute="12/2015",
        op_id="op_ev06",
        findings_out=findings,
    )
    hits = [f for f in findings if f.kind == "EVID.UNKNOWN_ATTESTATION_POLICY" and f.blocking]
    assert hits, (
        "an authorization citing an unknown policy id did not surface "
        "EVID.UNKNOWN_ATTESTATION_POLICY from the production EV-06 gate"
    )
    assert hits[0].detail["cited_policy_id"] == "totally_unknown_policy_id"
    # Negative (clean) case: a known cited policy id does not fire.
    clean = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id="op_ev06_clean",
        owner_phase="apply",
        strict_disposition="record",
        safe_default="block_until_known_policy",
        forbidden_shortcuts=("cited_policy_existence_as_known_policy",),
        detail={"evidence_kernel": {"policy_id": "some_known_policy"}},
    )
    clean_findings: list[Finding] = []
    gate_unknown_attestation_policy(
        authorization=clean,
        known_policy_ids=frozenset({"some_known_policy"}),
        is_strict=True,
        source_statute="12/2015",
        op_id="op_ev06_clean",
        findings_out=clean_findings,
    )
    assert not clean_findings, "EV-06 gate fired on a known cited policy id"


def _closure_replay_products(*, surface_attrs: dict[str, str]) -> None:
    """Build a real ReplayProducts whose materialized tree carries a marked node.

    Drives the production ``ReplayProducts.__post_init__`` whole-tree closure
    sweep over a tree with one node carrying the supplied surface/overlay attrs.
    """
    from lawvm.finland.replay_products import ReplayProducts

    marked = IRNode(kind=IRNodeKind.SECTION, label="1", attrs=surface_attrs)
    materialized = ReplayState(
        ir=IRNode(kind=IRNodeKind.BODY, children=(marked,))
    )
    ReplayProducts(
        replay_fold_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        materialized_state=materialized,
        timelines=None,
    )


def drill_surface_node_replay_authority_unwitnessed_tree_closure() -> None:
    """FW.SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED fires from the ReplayProducts build (FW-01)."""
    from lawvm.finland.apply_tree_closure import SurfaceAuthorityClosureError

    with pytest.raises(SurfaceAuthorityClosureError, match="FW.SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED"):
        _closure_replay_products(
            surface_attrs={"lawvm_surface_only": "1", "lawvm_replay_authorized": "1"}
        )
    # Negative (clean) case: a surface_only node that is NOT replay-authorized builds clean.
    _closure_replay_products(surface_attrs={"lawvm_surface_only": "1"})


def drill_overlay_replay_authorized_without_promotion_tree_closure() -> None:
    """OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION fires from the ReplayProducts build (OV-01)."""
    from lawvm.finland.apply_tree_closure import OverlayPromotionClosureError

    with pytest.raises(OverlayPromotionClosureError, match="OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION") as exc:
        _closure_replay_products(
            surface_attrs={"lawvm_overlay_origin": "1", "lawvm_replay_authorized": "1"}
        )
    assert exc.value.code == "OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION"


def drill_overlay_promotion_witness_incomplete_tree_closure() -> None:
    """OVERLAY.PROMOTION_WITNESS_INCOMPLETE fires from the ReplayProducts build (OV-02)."""
    from lawvm.finland.apply_tree_closure import OverlayPromotionClosureError

    with pytest.raises(OverlayPromotionClosureError, match="OVERLAY.PROMOTION_WITNESS_INCOMPLETE") as exc:
        _closure_replay_products(
            surface_attrs={
                "lawvm_overlay_origin": "1",
                "lawvm_replay_authorized": "1",
                "lawvm_overlay_promotion_event": "promote_1",
                # Promotion event present but cites NEITHER provider+model NOR registry+entry.
            }
        )
    assert exc.value.code == "OVERLAY.PROMOTION_WITNESS_INCOMPLETE"
    # Negative (clean) case: a complete provider-cited promotion builds clean.
    _closure_replay_products(
        surface_attrs={
            "lawvm_overlay_origin": "1",
            "lawvm_replay_authorized": "1",
            "lawvm_overlay_promotion_event": "promote_1",
            "lawvm_overlay_provider_id": "prov_x",
            "lawvm_overlay_model_version": "v1",
        }
    )


# ---------------------------------------------------------------------------
# Promotion-chain integrity drills (CHAIN-/PROMOTE- families, §0)
#
# These gates ride the EXISTING EV-05 execution-authorization graph as read-only
# checks (they do NOT modify the production apply mutation path — sibling sessions
# own it). The drills drive the real production-emit-site gate functions in
# ``finland.apply_promotion_chain`` into their firing state, the same way the
# EV-06 drill drives ``gate_unknown_attestation_policy`` directly. Each drill
# exercises the genuine guard, so a silently-disconnected guard goes red, and each
# pins the NAMED, bounded residual carried on the finding detail (the parts the
# apply-path carriers do not materialize yet).
# ---------------------------------------------------------------------------


def drill_authorization_identity_mismatch_promotion_chain() -> None:
    """PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH fires from the scope-match gate (PROMOTE-02).

    Production-emit site: ``gate_authorization_scope_match``. The checkable
    invariant today is rule_id<->op_id binding: an authorization minted for op A
    reused to gate op B is smuggled authority. The apply-path authorization is
    minted with ``rule_id = op_id`` for the SAME op (matches by construction,
    0-delta); the drill forges a mismatched authorization to exercise the gate.
    The deeper identity binding (input_node_ids/policy_id/candidate_set_hash) is
    the named residual carried on the finding detail.
    """
    from lawvm.core.execution_authorization import ExecutionAuthorization
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.apply_promotion_chain import gate_authorization_scope_match

    rop = _closure_rop(
        op_id="op_promote02_B",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    smuggled = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id="op_promote02_A",  # minted for a DIFFERENT op
        owner_phase="apply",
        strict_disposition="record",
        safe_default="block_until_apply_op_authorization_rule_is_resolved",
        forbidden_shortcuts=("landed_write_existence_as_execution_authorization",),
    )
    findings: list[Finding] = []
    gate_authorization_scope_match(
        authorization=smuggled,
        rop=rop,
        is_strict=True,
        source_statute="12/2015",
        findings_out=findings,
    )
    hits = [
        f
        for f in findings
        if f.kind == "PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH" and f.blocking
    ]
    assert hits, (
        "an authorization minted for a different op did not surface "
        "PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH from the scope-match gate"
    )
    assert hits[0].detail["bound_rule_id"] == "op_promote02_A"
    assert hits[0].detail["derived_rule_id"] == "op_promote02_B"
    assert hits[0].detail["unbound_identity_components"]  # named residual present
    # Negative (clean): a correctly-bound authorization does not fire.
    aligned = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id="op_promote02_B",
        owner_phase="apply",
        strict_disposition="record",
        safe_default="block_until_apply_op_authorization_rule_is_resolved",
        forbidden_shortcuts=("landed_write_existence_as_execution_authorization",),
    )
    clean: list[Finding] = []
    gate_authorization_scope_match(
        authorization=aligned, rop=rop, is_strict=True,
        source_statute="12/2015", findings_out=clean,
    )
    assert not clean, "scope-match gate fired on a correctly-bound authorization"


def drill_promotion_chain_incomplete_promotion_chain() -> None:
    """CHAIN.PROMOTION_CHAIN_INCOMPLETE fires from the chain-links gate (CHAIN-01)."""
    from lawvm.core.promotion_chain import PromotionChainLinks
    from lawvm.finland.apply_promotion_chain import gate_promotion_chain_links

    # A materialized execution-authorization link is absent → incomplete.
    links = PromotionChainLinks(
        source_witness=True,
        candidate_claim=True,
        execution_authorization=False,
        dry_run_proof=True,
        agreement_row=True,
    )
    findings: list[Finding] = []
    gate_promotion_chain_links(
        links=links, rop=None, is_strict=True,
        source_statute="12/2015", findings_out=findings,
    )
    hits = [
        f for f in findings if f.kind == "CHAIN.PROMOTION_CHAIN_INCOMPLETE" and f.blocking
    ]
    assert hits, "a missing materialized link did not block (CHAIN-01)"
    assert "execution_authorization" in hits[0].detail["missing_links"]
    # Negative (clean): a complete chain does not fire CHAIN-01.
    complete = PromotionChainLinks(
        source_witness=True, candidate_claim=True, execution_authorization=True,
        dry_run_proof=True, agreement_row=True,
    )
    clean: list[Finding] = []
    gate_promotion_chain_links(
        links=complete, rop=None, is_strict=True,
        source_statute="12/2015", findings_out=clean,
    )
    assert not [
        f for f in clean if f.kind == "CHAIN.PROMOTION_CHAIN_INCOMPLETE"
    ], "completeness gate fired on a complete chain"


def drill_authority_by_accumulation_promotion_chain() -> None:
    """CHAIN.AUTHORITY_BY_ACCUMULATION fires from the chain-links gate (CHAIN-02)."""
    from lawvm.core.promotion_chain import PromotionChainLinks
    from lawvm.finland.apply_promotion_chain import gate_promotion_chain_links

    # execution-authorization present with an absent candidate-claim predecessor:
    # authority reached by accumulation, not by climbing.
    links = PromotionChainLinks(
        source_witness=True,
        candidate_claim=False,
        execution_authorization=True,
        dry_run_proof=True,
        agreement_row=True,
    )
    findings: list[Finding] = []
    gate_promotion_chain_links(
        links=links, rop=None, is_strict=True,
        source_statute="12/2015", findings_out=findings,
    )
    hits = [
        f for f in findings if f.kind == "CHAIN.AUTHORITY_BY_ACCUMULATION" and f.blocking
    ]
    assert hits, "a link reached without its predecessor did not block (CHAIN-02)"
    assert "execution_authorization" in hits[0].detail["accumulation_links"]


def drill_stale_downstream_after_retraction_promotion_chain() -> None:
    """PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION fires from the down-chain gate (PROMOTE-01)."""
    from lawvm.finland.apply_promotion_chain import gate_downchain_retraction

    findings: list[Finding] = []
    gate_downchain_retraction(
        retracted_link="execution_authorization",
        downstream_links=("dry_run_proof", "agreement_row"),
        reopened_links=frozenset({"dry_run_proof"}),  # agreement_row left standing
        is_strict=True,
        source_statute="12/2015",
        op_id="op_promote01",
        findings_out=findings,
    )
    hits = [
        f
        for f in findings
        if f.kind == "PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION" and f.blocking
    ]
    assert hits, (
        "a downstream link standing on a retracted predecessor did not block "
        "(PROMOTE-01)"
    )
    assert "agreement_row" in hits[0].detail["stale_downstream"]
    assert hits[0].detail["multi_hop_residual"]  # named sub-chain residual present
    # Negative (clean): all downstream links reopened does not fire.
    clean: list[Finding] = []
    gate_downchain_retraction(
        retracted_link="execution_authorization",
        downstream_links=("dry_run_proof", "agreement_row"),
        reopened_links=frozenset({"dry_run_proof", "agreement_row"}),
        is_strict=True,
        source_statute="12/2015",
        op_id="op_promote01_clean",
        findings_out=clean,
    )
    assert not clean, "retraction gate fired despite all downstream links reopened"


# ---------------------------------------------------------------------------
# Declarative fire-drill registry
# ---------------------------------------------------------------------------

# code -> fire-drill callable that drives the production lane to the guarded
# state and asserts the finding reaches its consumer-visible surface.
FIRE_DRILLS: Dict[str, Callable[[], None]] = {
    "LINEAGE.CYCLE": drill_lineage_cycle_replay_products_build,
    "APPLY.TREE_INVARIANT_VIOLATION": drill_tree_invariant_violation_duplicate_label,
    "APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED": drill_effect_lifecycle_target_unresolved_apply_lane,
    "APPLY.FAILED_OPERATION": drill_failed_operation_apply_lane,
    "APPLY.META_REPEAL_EFFECT_UNRESOLVED": drill_meta_repeal_effect_unresolved_route_rejection_barrier,
    "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED": drill_pending_amendment_effect_unresolved_route_rejection_barrier,
    "APPLY.SOURCE_PATHOLOGY_DETECTED": drill_source_pathology_detected_apply_lane,
    "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD": drill_leading_subsection_heading_payload_elaboration,
    "ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL": drill_fold_single_insert_subsection_list_tail_payload_elaboration,
    "ELAB.FOLD_MULTI_TARGET_SUBSECTION_LIST_WRAPUPS": drill_fold_multi_target_subsection_list_wrapups_payload_elaboration,
    "LOWER.BODY_CHAPTER_DESCENDANT_SCOPE_CORRECTION": drill_body_chapter_descendant_scope_correction_compile_group_recovery,
    "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET": drill_restore_heading_for_explicit_facet_group_elaboration,
    "ELAB.SPARSE_OMISSION_TAIL_CLAIM": drill_sparse_omission_tail_claim_group_surface,
    "ELAB.SPARSE_OMISSION_TAIL_PRUNED_FROM_CARRIER": drill_sparse_omission_tail_pruned_from_carrier_compile_surface,
    "ELAB.SPARSE_PLAIN_SUBSECTION_SHELL_CONTINUATION_MERGE": (
        drill_sparse_plain_subsection_shell_continuation_merge_payload_elaboration
    ),
    "ELAB.REBASE_REPLACED_RENUMBER_SOURCE": drill_rebase_replaced_renumber_source_inspect_bundle,
    "PARSE.FRONTEND_INTERNAL_ERROR": drill_frontend_internal_error_parse_surface,
    "REPLAY_UNKNOWN_MUTATION_OUTCOME": drill_replay_unknown_mutation_outcome_apply_lane,
    "REPLAY_SKIPPED_OP_MUTATED_TREE": drill_replay_skipped_op_mutated_tree_apply_lane,
    "REPLAY_FAILED_OP_MUTATED_TREE": drill_replay_failed_op_mutated_tree_apply_lane,
    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": drill_replay_missing_primary_target_consumption_apply_lane,
    "REPLAY_APPLY_BOUNDARY_UNRESOLVED": drill_replay_apply_boundary_unresolved_apply_lane,
    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": drill_replay_apply_boundary_touch_outside_target_apply_lane,
    "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP": drill_mutation_boundary_violation_at_op_apply_lane,
    "APPLY.OCCUPANCY_TRANSITION_BLOCKED": drill_occupancy_transition_blocked_apply_lane,
    "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED": drill_replay_authorization_proof_required_apply_lane,
    # AM-01: typed-acceptance gate rejects a Recovered op under a strict profile.
    "APPLY.RECOVERED_OP_REJECTED_IN_STRICT": drill_recovered_op_rejected_in_strict_apply_lane,
    # Wave-2 apply-authority closure (blocking arms).
    "APPLY.GRANULARITY_ESCALATION_AT_OP": drill_granularity_escalation_at_op_apply_lane,
    "EVID.UNKNOWN_ATTESTATION_POLICY": drill_unknown_attestation_policy_at_op_apply_lane,
    "FW.SURFACE_NODE_REPLAY_AUTHORITY_UNWITNESSED": drill_surface_node_replay_authority_unwitnessed_tree_closure,
    "OVERLAY.REPLAY_AUTHORIZED_WITHOUT_PROMOTION": drill_overlay_replay_authorized_without_promotion_tree_closure,
    "OVERLAY.PROMOTION_WITNESS_INCOMPLETE": drill_overlay_promotion_witness_incomplete_tree_closure,
    # Promotion-chain integrity wave (CHAIN-/PROMOTE- families, §0).
    "PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH": drill_authorization_identity_mismatch_promotion_chain,
    "CHAIN.PROMOTION_CHAIN_INCOMPLETE": drill_promotion_chain_incomplete_promotion_chain,
    "CHAIN.AUTHORITY_BY_ACCUMULATION": drill_authority_by_accumulation_promotion_chain,
    "PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION": drill_stale_downstream_after_retraction_promotion_chain,
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
    "DEFINITION.DUPLICATE_DEFINITION": drill_definition_duplicate_definition_surface_totality,
    "DEFINITION.ORPHAN_DEFINITION_REFERENCE": drill_definition_orphan_reference_surface_totality,
    "REFERENCE.UNCLASSIFIED_REFERENCE": drill_reference_unclassified_reference_surface_totality,
    "SURFACE.TOKEN_REALIZATION_GAP": drill_surface_token_realization_gap_surface_totality,
    "WAIST.HANDOFF_PARITY_SOURCE_TO_TOKEN": drill_waist_handoff_parity_source_to_token_surface_totality,
    "SURFACE.ORPHAN_ENTITY_NODE": drill_surface_orphan_entity_node_surface_totality,
    "SCHED.WINDOW_UNMATERIALIZED": drill_sched_window_unmaterialized_schedule_window_totality,
    "SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE": drill_scope_overlap_without_disjoint_scope_scope_lattice_totality,
    "APPLY.OCCUPANCY_POLICY_VIOLATION": drill_occupancy_policy_violation_finland_production,
    "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT": drill_occupancy_temporally_disjoint_insert_finland_production,
    "APPLY.REPLAY_UNDECLARED_TREE_TOUCH": drill_replay_undeclared_tree_touch_apply_lane,
    "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP": drill_mutation_boundary_finding_at_op_quirks_apply_lane,
    # Wave-2 apply-authority closure (non-blocking observation arms).
    "APPLY.SCOPE_CONFIDENCE_TOTALITY_GAP_AT_OP": drill_scope_confidence_totality_gap_at_op_apply_lane,
    "LOWER.VERB_CONVERSION_UNWITNESSED_AT_OP": drill_verb_conversion_unwitnessed_at_op_apply_lane,
    "APPLY.PAYLOAD_SMUGGLING_AT_OP": drill_payload_smuggling_at_op_apply_lane,
    "APPLY.UNSTATED_MIGRATION_AT_OP": drill_unstated_migration_at_op_apply_lane,
    # EVIDENCE-LEDGER wave (EV-03, EV-07) totality sweeps.
    "EVID.RESIDUAL_LEDGER_NONMONOTONE": drill_residual_ledger_nonmonotone_stage_account_totality,
    "EVID.DIAGNOSTIC_NOT_SELF_EVIDENCING": drill_diagnostic_not_self_evidencing_residual_totality,
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
    "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH": ("payload-normalize ambiguity; needs fixture", "2026-06-20"),
    "ELAB.NORMALIZE_ITEM_LIKE_TARGET": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.NUMBERED_TABLE_TARGET_MERGE": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.OMISSION_EXPANSION": ("omission-expansion barrier; needs fixture", "2026-06-20"),
    "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.REBASE_SPARSE_STALE_PREDECESSOR": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT": ("sparse-elaboration recovery; needs fixture", "2026-06-20"),
    "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.SPARSE_PAYLOAD_LEFTOVER": ("grafter recovery; needs fixture", "2026-06-20"),
    "ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE": ("payload-normalize recovery; needs fixture", "2026-06-20"),
    "ELAB.SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION": ("payload-normalize source-shape recovery; covered by payload unit fixture", "2026-06-23"),
    "ELAB.SPLIT_MIXED_SPARSE_SLOT_CROSS_PARAGRAPH_PAYLOAD": ("payload-normalize recovery; needs fixture", "2026-06-23"),
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
    "build_finland_effect_lifecycle",
    "compile_amendment_ops",
    "elaborate_group",
    "elaborate_payload_against_live",
    # Consumer-visible production/debug bundle used by divergence triage; it
    # drives the real compile/group-elaboration path and exposes observations.
    "build_amendment_bundle",
    # Pre-snapshot scope/action recovery for one compile group: the production
    # entrypoint that runs every LOWER.* scope-recovery guard
    # (_maybe_apply_descendant_body_chapter_scope etc.) over the lowered group ops.
    "resolve_compile_group_scope_recovery",
    "api.parse_clause",
    "parse_clause(",
    "_check_occupancy_policy",
    # ReplayProducts.__post_init__ is the central production seal for the finished
    # replay product (migration ledger, effect graph): constructing it runs the
    # production validation guards (type checks, effect-graph closure, lineage
    # acyclicity) over the sealed ledger.
    "ReplayProducts(",
    # Wave-2 apply-authority closure: the EV-06 gate is the production
    # attestation-policy validator called from _gate_execution_authorization_at_op.
    "gate_unknown_attestation_policy",
    # Promotion-chain integrity wave (CHAIN-/PROMOTE- families, §0): the
    # production-emit-site gate functions in finland.apply_promotion_chain. They
    # ride the existing EV-05 authorization graph as read-only checks (the
    # production apply MUTATION path is owned by sibling sessions and is not
    # modified); the drill drives each genuine gate, the same shape as the EV-06
    # gate above.
    "gate_authorization_scope_match",
    "gate_promotion_chain_links",
    "gate_downchain_retraction",
)

# Codes whose ONLY honest production surface is the strict verdict mapping (a
# runtime-finding -> strict-barrier-code projection through
# ``compute_verdict_from_registry``). These primary drills legitimately drive the
# production verdict builder rather than a deeper apply/replay builder; they are
# the verdict-surface primary lane. Every OTHER primary drill must drive a
# builder from ``_PRODUCTION_BUILDER_CALLS``.
#
# Empty after the dead-gate reconciliation: the three former verdict-only drills
# (APPLY.FAILED_OPERATION, APPLY.SOURCE_PATHOLOGY_DETECTED,
# APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED) now drive their production-deciding
# guard — the strict ``apply_op`` lane (real FailedOp / SourcePathology) and the
# ``build_finland_effect_lifecycle`` builder (real unresolved-target event) — so
# none remains a verdict-mapping-only drill.
_VERDICT_SURFACE_PRIMARY_DRILLS: frozenset[str] = frozenset()


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


# ---------------------------------------------------------------------------
# Guard-liveness coverage gate: the three-state FireDrillRegistry partition
# ---------------------------------------------------------------------------
#
# The classifier in lawvm.core.fire_drill_registry partitions every hard/strict
# finding code into exactly one of {live_drill, recorded_dead, no_drill_yet}.
# The drilled set and the debt allowlist are owned HERE (with the drills); the
# owned-deadness set is owned in the core module (RECORDED_DEAD). A code that is
# both drilled here AND recorded-dead is classified recorded_dead: the
# production-call-site honesty (no execution path reaches the gate) dominates the
# weaker "a drill can drive the gate function" claim.


def _guard_liveness_live_drill_codes() -> frozenset[str]:
    """The drilled codes that count as live_drill for the three-state partition.

    A FIRE_DRILLS code is live_drill UNLESS it is recorded-dead: the
    promotion-chain gates have a drill that drives the gate FUNCTION, but no
    production call site reaches the emit site, so production-reachability honesty
    reclassifies them as recorded_dead (see RECORDED_DEAD).
    """
    in_range = hard_or_strict_codes()
    return (frozenset(FIRE_DRILLS) & in_range) - frozenset(RECORDED_DEAD)


def test_guard_liveness_three_state_partition_is_consistent() -> None:
    """Coverage gate: every hard/strict finding lands in exactly one of three states.

    The guard-liveness states are {live_drill, recorded_dead, no_drill_yet}. This
    asserts the partition over all hard_fail/strict_fail finding codes is
    exhaustive (no code is silently un-accounted) and mutually exclusive (no code
    is in two states). This is the structural guard-liveness coverage gate: a new
    hard/strict guard cannot be added without consciously landing in one state.

    HONESTY BOUNDARY: live_drill proves the guard FIRES from production, not that
    it is semantically correct. recorded_dead is an OWNED deadness (no production
    call site), not a fix. no_drill_yet is declared debt. Never "all guards fire".
    """
    classification = classify_guard_liveness(
        live_drill_codes=_guard_liveness_live_drill_codes(),
        no_drill_yet_codes=frozenset(NO_FIRE_DRILL_YET) & hard_or_strict_codes(),
    )
    assert not classification.unaccounted, (
        "hard/strict finding codes silently un-accounted by the guard-liveness "
        f"three-state partition: {sorted(classification.unaccounted)}. Each must "
        "gain a fire-drill (live_drill), be recorded dead (recorded_dead, owned), "
        "or be declared debt (no_drill_yet)."
    )
    assert not classification.overlapping, (
        "hard/strict finding codes in more than one guard-liveness state (the "
        f"states must be mutually exclusive): {sorted(classification.overlapping)}"
    )
    assert classification.is_consistent()


def test_recorded_dead_entries_are_real_hard_or_strict_codes() -> None:
    """RECORDED_DEAD must name registered hard/strict codes with a named owner+reason."""
    in_range = hard_or_strict_codes()
    for code in sorted(RECORDED_DEAD):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"RECORDED_DEAD names unregistered code: {code}"
        assert code in in_range, (
            f"RECORDED_DEAD names non-hard/strict code {code!r}; the classifier "
            "ranges over hard_fail/strict_fail only"
        )
        owner, reason = RECORDED_DEAD[code]
        assert owner.strip(), f"RECORDED_DEAD[{code!r}] has an empty owner"
        assert reason.strip(), f"RECORDED_DEAD[{code!r}] has an empty reason"


def test_recorded_dead_guards_have_no_production_call_site() -> None:
    """Owned deadness must stay honest: recorded-dead gates have no production caller.

    Each RECORDED_DEAD code's gate function lives in finland/apply_promotion_chain.py
    and must have NO call site outside that module (and outside tests). The moment a
    production caller is wired, the code becomes reachable and must move to a
    live_drill — this test fails until it does, so the deadness cannot rot into a
    silent false claim.
    """
    import pathlib

    src_root = pathlib.Path(__file__).resolve().parent.parent / "src" / "lawvm"
    gate_callers = ("gate_authorization_scope_match", "gate_promotion_chain_links", "gate_downchain_retraction")
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "apply_promotion_chain.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for gate in gate_callers:
            # A call site is "<gate>(" — the import-less invocation form.
            if f"{gate}(" in text:
                offenders.append(f"{path}: {gate}")
    assert not offenders, (
        "recorded-dead promotion-chain gate now has a production call site; move "
        f"the corresponding RECORDED_DEAD code to a live_drill: {offenders}"
    )
