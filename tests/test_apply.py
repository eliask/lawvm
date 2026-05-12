"""Tests for the decomposed apply-cluster sub-functions.

Covers the granularity-specific handlers extracted from
_apply_deterministic_subsection_op (Phase 11) and rewritten as pure
(ReplayState, ...) → Optional[ReplayState] functions (Commit 1 of the
StatuteContext refactor):
  - _apply_subsection_repeal   — REPEAL whole moment
  - _apply_subsection_replace  — REPLACE whole moment
  - _apply_subsection_insert   — INSERT new moment
  - _apply_item_repeal         — REPEAL kohta
  - _apply_item_replace        — REPLACE kohta (standard + compound + OOR)
  - _apply_item_insert         — INSERT kohta
  - _apply_special_targets     — heading, intro, fallbacks
  - _resolve_subsection_index  — literal subsection index resolution
  - _resolve_item_subsection_index — item-only intro-list carrier resolution

Return-value protocol (new):
  - None            = not applicable (try next handler)
  - state is result = failure (IR not modified, same state object returned)
  - new ReplayState = success (state.ir was replaced)

All fixtures are self-contained (no corpus access, no network, no LLM calls).

Run:
    uv run pytest tests/test_apply.py -v
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace as dc_replace
from types import SimpleNamespace
from typing import Any, List, Optional, cast

import pytest
from lxml import etree


from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction
from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.corpus_store import get_corpus_store
from lawvm.finland.apply import apply_op
from lawvm.finland.apply_events import (
    ApplyMutationAccountingResult,
    ApplyMutationEvent,
    ApplyMutationInvariantReport,
    DeclaredMutationAllowance,
    analyze_apply_mutation_accounting,
    build_apply_mutation_invariant_reports,
    check_apply_mutation_accounting,
)
from lawvm.finland.apply_legacy_dispatch import _apply_legacy_dispatch
from lawvm.finland.apply_structure_ops import (
    _apply_materialization,
    _apply_container_op,
    _normalize_subsection_target_hint_ir,
    _apply_whole_section_op,
    _insert_or_replace_same_labeled_child,
)
from lawvm.finland.apply_runtime_support import (
    _emit_section_snapshot,
    _build_subsection_slot_assignment,
    _expired_temporary_section_merge_base,
    _expired_temporary_section_merge_base_rebase_info,
    _legacy_dispatch_shell_for_rop,
    _valid_target_path_hint,
    _valid_target_group_path_hint,
)
from lawvm.finland.apply_item_ops import (
    _apply_item_insert,
    _apply_item_repeal,
    _apply_item_replace,
    _apply_special_targets,
)
from lawvm.finland.apply_payload_ops import _collapse_intro_list_amend_subsection_ir, _has_intro_list_moment_shape_ir
from lawvm.finland.apply_subsection_ops import (
    _apply_subsection_insert,
    _apply_subsection_repeal,
    _apply_subsection_replace,
    _resolve_subsection_index_with_rebound_kind,
    _resolve_item_subsection_index,
    _resolve_subsection_index,
)
from lawvm.finland.apply_policy import _resolve_section_path_with_fallbacks
from lawvm.finland.apply_subsection_dispatch import (
    _apply_deterministic_subsection_op,
    _follow_same_wave_subsection_migration,
    _normalize_subsection_dispatch_inputs,
)
from lawvm.finland.apply_typed_dispatch import (
    _apply_intent_section_level,
    _materialization_root_move_allowances,
    _whole_section_move_rebind_allowances,
)
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.apply_ir_ops import _relabel_paragraph_ir
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.strict_profile import default_finland_strict_profile
from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.merge import _merge_section_with_omission_ir, _partial_section_replace_diagnostics_ir
from lawvm.finland.ops import (
    AmendmentOp,
    FailedOp,
    OpType,
    ResolvedOp,
    ScopeConfidence,
    get_replay_profile,
    scope_authority_parity_for_op,
    runtime_scope_confidence_for_op,
)
from lawvm.finland.payload_normalize import (
    PayloadCompletenessWitness,
    SparsePayloadSlotBinding,
    SubsectionSlotAssignmentResult,
    SubsectionSlotMap,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.statute import ReplayState, StatuteContext


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DATE = dt.date(2020, 1, 1)

_FINLEX_ORACLE = get_replay_profile("finlex_oracle")
_LEGAL_PIT = get_replay_profile("legal_pit")


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _para(label: str, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=(_content(text),) if text else ())


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _compat_upsert_policy():
    from lawvm.core.canonical_intent import OccupancyClass, OccupancyPolicy

    return OccupancyPolicy(
        primary_expected_from=frozenset(
            {
                OccupancyClass.ABSENT,
                OccupancyClass.SUBSTANTIVE,
                OccupancyClass.TOMBSTONE,
                OccupancyClass.SCAFFOLD,
            }
        ),
        allowed_from=frozenset(OccupancyClass),
        result=OccupancyClass.SUBSTANTIVE,
    )


def _make_state(body_ir: IRNode) -> ReplayState:
    """Build a minimal ReplayState with the given IRNode as its .ir."""
    return ReplayState(ir=body_ir)


def _ctx(base_ir: IRNode | None = None) -> StatuteContext:
    """Type-checking shim for legacy SimpleNamespace test contexts."""
    return cast(StatuteContext, SimpleNamespace(base_ir=base_ir))


def _op(
    op_type: OpType = "REPLACE",
    target_section: str = "1",
    target_chapter: Optional[str] = None,
    target_part: Optional[str] = None,
    target_paragraph: Optional[int] = None,
    target_item: Optional[str] = None,
    target_special: Optional[str] = None,
    move_clause_target_unit_kind: TargetUnitKind | None = None,
    is_temporary: bool = False,
    named_row_targets: tuple[str, ...] = (),
    body_root_replace_fallback: bool = False,
    fallback_provenance: bool = False,
    voimaantulo_repeal: bool = False,
    extraction_provenance_tags: tuple[str, ...] = (),
    target_guessing_provenance_tags: tuple[str, ...] = (),
    scope_provenance_tags: tuple[str, ...] = (),
    witness_rule_id: str | None = None,
) -> AmendmentOp:
    return AmendmentOp(
        op_id="test_op",
        op_type=op_type,
        target_section=target_section,
        target_unit_kind="section",
        target_chapter=target_chapter,
        target_part=target_part,
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
        move_clause_target_unit_kind=move_clause_target_unit_kind,
        named_row_targets=named_row_targets,
        body_root_replace_fallback=body_root_replace_fallback,
        fallback_provenance=fallback_provenance,
        voimaantulo_repeal=voimaantulo_repeal,
        extraction_provenance_tags=extraction_provenance_tags,
        target_guessing_provenance_tags=target_guessing_provenance_tags,
        scope_provenance_tags=scope_provenance_tags,
        source_statute="2020/1",
        source_issue_date=_DATE,
        is_temporary=is_temporary,
        witness_rule_id=witness_rule_id,
    )


def _modified(state: ReplayState, result: Optional[ReplayState]) -> ReplayState:
    """Return the modified state and fail fast if the operation did not modify it."""
    assert result is not None and result is not state
    return result


def _unchanged(state: ReplayState, result: Optional[ReplayState]) -> bool:
    """True if result is the same state object (failure, IR not modified)."""
    return result is state


def test_apply_op_requires_typed_intent_for_replace_family() -> None:
    state = _make_state(_body(_sec("1", _content("old"))))
    op = _op(op_type="REPLACE", target_section="1")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("1", _content("new")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    rop.intent = None

    with pytest.raises(RuntimeError, match="FI_TYPED_INTENT_REQUIRED"):
        apply_op(
            state,
            op,
            _ctx(state.ir),
            rop.muutos_ir,
            rop=rop,
        )


def test_apply_op_requires_typed_intent_for_renumber_without_destination() -> None:
    state = _make_state(_body(_sec("1", _content("old"))))
    op = _op(op_type="RENUMBER", target_section="1")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    rop.intent = None

    with pytest.raises(RuntimeError, match="FI_TYPED_INTENT_REQUIRED"):
        apply_op(
            state,
            op,
            _ctx(state.ir),
            None,
            rop=rop,
        )


def test_apply_op_binds_typed_intent_for_renumber_when_destination_exists() -> None:
    from lawvm.core.canonical_intent import IntentKind
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

    state = _make_state(_body(_sec("1", _content("old"))))
    lo = LegalOperation(
        op_id="renumber_1_to_2",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", "1"),)),
        destination=LegalAddress(path=(("section", "2"),)),
        source=OperationSource(statute_id="2020/1"),
    )
    op = AmendmentOp(
        op_id="renumber_1_to_2",
        op_type="RENUMBER",
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
        lo=lo,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )

    assert rop.intent is not None
    assert rop.intent.kind == IntentKind.RELABEL

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        None,
        rop=rop,
    )

    assert result.find_section("2") is not None
    assert result.find_section("1") is None


def test_relabel_paragraph_ir_preserves_letter_suffix_item_spacing() -> None:
    paragraph = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 a)"),
            IRNode(kind=IRNodeKind.CONTENT, text="IMSBC-säännöstöllä ..."),
        ),
    )

    relabelled = _relabel_paragraph_ir(paragraph, "4a")

    assert relabelled.label == "4a"
    assert relabelled.children[0].kind == IRNodeKind.NUM
    assert relabelled.children[0].text == "4 a)"


def test_apply_op_typed_section_relabel_relabels_and_resorts_within_chapter() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                    _sec("60", _content("sixty")),
                    _sec("62", _content("sixty-two")),
                    _sec("73", _content("old seventy-three")),
                ),
            )
        )
    )
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
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
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
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        None,
        _ctx(state.ir),
        None,
        rop=rop,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    chapter = next(child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER and child.label == "7")
    labels = [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]
    assert labels == ["60", "61", "62"]
    sec61 = next(child for child in chapter.children if child.kind == IRNodeKind.SECTION and child.label == "61")
    sec61_text = " ".join(grand.text or "" for grand in sec61.children)
    assert "old seventy-three" in sec61_text
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "73" for child in chapter.children)
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_relabel"
    assert event.outcome == "applied"
    assert event.renumbered_paths == (
        ((("chapter", "7"), ("section", "73")), (("chapter", "7"), ("section", "61"))),
    )


def test_apply_op_typed_section_relabel_keeps_part_scoped_parent_when_multiple_parts_share_chapter_label() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                            _sec("10", _content("part1 section 10")),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                            _sec("8", _content("part2 section 8")),
                        ),
                    ),
                ),
            ),
        )
    )
    lo = LegalOperation(
        op_id="renumber_p2_c1_8_to_10",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "8"))),
        destination=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "10"))),
        source=OperationSource(statute_id="2019/371"),
    )
    op = AmendmentOp(
        op_id="renumber_p2_c1_8_to_10",
        op_type="RENUMBER",
        target_section="8",
        target_unit_kind="section",
        target_chapter="1",
        target_part="2",
        source_statute="2019/371",
        lo=lo,
    )
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(address=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "8")))),
        destination=NodeTarget(address=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "10")))),
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="8",
        target_chapter="1",
        target_address=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "8"))),
        destination_address=LegalAddress(path=(("part", "2"), ("chapter", "1"), ("section", "10"))),
    )
    rop.intent = intent

    result = apply_op(
        state,
        None,
        _ctx(state.ir),
        None,
        rop=rop,
        replay_mode="legal_pit",
    )

    assert result.find_section("10", "1", "2") is not None
    assert result.find_section("8", "1", "2") is None
    part1_sec10 = result.find_section("10", "1", "1")
    assert part1_sec10 is not None
    assert "part1 section 10" in irnode_to_text(part1_sec10)


def test_apply_op_typed_section_relabel_missing_source_emits_target_address() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

    state = _make_state(_body())
    lo = LegalOperation(
        op_id="renumber_73_to_61_missing",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
        destination=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
        source=OperationSource(statute_id="1994/318"),
    )
    op = AmendmentOp(
        op_id="renumber_73_to_61_missing",
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
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
        target_address=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
        destination_address=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
    )
    rop.intent = intent
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_relabel"
    assert event.outcome == "skipped"
    assert event.resolved_target_path == (("chapter", "7"), ("section", "73"))
    assert event.used_fallback_tags == ("APPLY.RELABEL_SKIPPED", "source_section_missing")
    assert event.reason_code == "source_section_missing"
    assert "source section 73 not found" in event.failure_reason


def test_apply_op_typed_subsection_relabel_relabels_and_resorts_within_section() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                    _sec(
                        "3",
                        _sub("1", _content("first")),
                        _sub("3", _content("third")),
                        _sub("4", _content("fourth")),
                    ),
                ),
            )
        )
    )
    lo = LegalOperation(
        op_id="renumber_3_3_to_2",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "3"), ("section", "3"), ("subsection", "3"))),
        destination=LegalAddress(path=(("chapter", "3"), ("section", "3"), ("subsection", "2"))),
        source=OperationSource(statute_id="1999/1249"),
    )
    op = AmendmentOp(
        op_id="renumber_3_3_to_2",
        op_type="RENUMBER",
        target_section="3",
        target_unit_kind="section",
        target_chapter="3",
        target_paragraph=3,
        source_statute="1999/1249",
        lo=lo,
    )
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(address=LegalAddress(path=(("chapter", "3"), ("section", "3"), ("subsection", "3")))),
        destination=NodeTarget(address=LegalAddress(path=(("chapter", "3"), ("section", "3"), ("subsection", "2")))),
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter="3",
    )
    rop.intent = intent
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    section = result.find_section("3", "3")
    assert section is not None
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "4"]
    assert "third" in " ".join(irnode_to_text(subsections[1]).split())
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_relabel"
    assert event.outcome == "applied"
    assert event.renumbered_paths == (
        ((("chapter", "3"), ("section", "3"), ("subsection", "3")), (("chapter", "3"), ("section", "3"), ("subsection", "2"))),
    )


def test_apply_op_typed_chapter_insert_emits_resolved_target_path_from_rop() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        Insert,
        InsertOrder,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body())
    payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
            IRNode(kind=IRNodeKind.HEADING, text="New chapter"),
            _sec("29a", IRNode(kind=IRNodeKind.NUM, text="29 a §"), _content("new")),
        ),
    )
    op = AmendmentOp(
        op_id="insert_chapter_3a",
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="3a",
        source_statute="2003/1310",
        source_issue_date=_DATE,
    )
    intent = Insert(
        kind=IntentKind.INSERT,
        target=NodeTarget(
            address=LegalAddress(path=(("chapter", "3a"),)),
        ),
        payload=cast(Any, payload),
        contract=ExecutionContract(
            occupancy=OccupancyPolicy.fresh_insert(),
            insert_order=InsertOrder.SORTED_FAMILY,
        ),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="3a",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "3a"),)),
    )
    rop.intent = intent
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(_body()),
        payload,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    result = _modified(state, result)
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_container_op"
    assert event.outcome == "applied"
    assert event.resolved_target_path == (("chapter", "3a"),)
    assert event.created_paths == ((("chapter", "3a"),),)


def test_apply_materialization_prefers_rop_scope_over_legacy_op_scope() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter seven"),
                ),
            )
        )
    )
    payload = _sec("73", IRNode(kind=IRNodeKind.NUM, text="73 §"), _content("materialized"))
    op = AmendmentOp(
        op_id="materialize_73",
        op_type="REPLACE",
        target_section="73",
        target_unit_kind="section",
        target_chapter=None,
        source_statute="1994/318",
        source_issue_date=_DATE,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
    )

    result = _apply_materialization(state, _legacy_dispatch_shell_for_rop(rop), payload, "test")

    assert result is not None
    result = _modified(state, result)
    chapter = next(child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER and child.label == "7")
    assert any(child.kind == IRNodeKind.SECTION and child.label == "73" for child in chapter.children)
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "73" for child in result.ir.children)


def test_apply_materialization_prefers_typed_action_over_mutated_legacy_shell_action() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter seven"),
                ),
            )
        )
    )
    payload = _sec("73", IRNode(kind=IRNodeKind.NUM, text="73 §"), _content("materialized"))
    op = AmendmentOp(
        op_id="materialize_73_typed_action",
        op_type="REPLACE",
        target_section="73",
        target_unit_kind="section",
        target_chapter=None,
        target_paragraph=None,
        target_item=None,
        target_special=None,
        move_clause_target_unit_kind=None,
        named_row_targets=(),
        body_root_replace_fallback=False,
        fallback_provenance=False,
        voimaantulo_repeal=False,
        extraction_provenance_tags=(),
        target_guessing_provenance_tags=(),
        scope_provenance_tags=(),
        source_statute="1994/318",
        source_issue_date=_DATE,
        is_temporary=False,
        witness_rule_id=None,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
    )
    rop.op.op_type = "RENUMBER"

    result = _apply_materialization(state, rop, payload, "test")

    assert result is not None
    result = _modified(state, result)
    chapter = next(child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER and child.label == "7")
    assert any(child.kind == IRNodeKind.SECTION and child.label == "73" for child in chapter.children)
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "73" for child in result.ir.children)


def test_apply_materialization_root_move_emits_pathology() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter six"),
                ),
            ),
            _sec("23", _content("root-level section")),
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )
    source_pathologies: list[SourcePathology] = []

    result = _apply_materialization(
        state,
        _legacy_dispatch_shell_for_rop(rop),
        rop.muutos_ir,
        "test",
        source_pathologies_out=source_pathologies,
    )

    result = _modified(state, result)
    moved = result.find_section("23", "6")
    assert moved is not None
    assert "new root-level section" in irnode_to_text(moved)
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "23" for child in result.ir.children)
    assert len(source_pathologies) == 1
    assert source_pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert source_pathologies[0].detail["recovery_kind"] == "section_materialization_root_move_destination_rebind"


def test_insert_or_replace_same_labeled_child_reports_collision() -> None:
    tree = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3a",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Old chapter"),
            ),
        )
    )
    replacement = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
            IRNode(kind=IRNodeKind.HEADING, text="New chapter"),
        ),
    )

    new_tree, replaced = _insert_or_replace_same_labeled_child(tree, (), replacement)

    assert replaced is True
    chapter = next(child for child in new_tree.children if child.kind == IRNodeKind.CHAPTER and child.label == "3a")
    heading = next(child for child in chapter.children if child.kind == IRNodeKind.HEADING)
    assert heading.text == "New chapter"


def test_apply_materialization_keeps_chapter_scoped_section_inside_chapter_when_chapter_label_is_numeric() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1luku.",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter one"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2luku.",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter two"),
                    _sec("1", _sub("1", _content("live"))),
                ),
            ),
        )
    )
    payload = _sec("2", _sub("6", _content("materialized")))
    op = AmendmentOp(
        op_id="materialize_2_6",
        op_type="REPLACE",
        target_section="2",
        target_unit_kind="section",
        target_chapter="2",
        target_paragraph=6,
        source_statute="1997/611",
        source_issue_date=_DATE,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="2",
        target_chapter="2",
    )
    source_pathologies: list[SourcePathology] = []

    result = _apply_materialization(
        state,
        _legacy_dispatch_shell_for_rop(rop),
        payload,
        "test",
        source_pathologies_out=source_pathologies,
    )

    assert result is not None
    result = _modified(state, result)
    root = result.ir
    chapter_two = next(child for child in root.children if child.kind == IRNodeKind.CHAPTER and child.label == "2luku.")
    assert any(child.kind == IRNodeKind.SECTION and child.label == "2" for child in chapter_two.children)
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "2" for child in root.children)
    assert len(source_pathologies) == 1
    assert source_pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert source_pathologies[0].detail["recovery_kind"] == "section_materialization_scoped_insert"


def test_apply_materialization_skips_subsection_insert_when_section_exists_in_different_part() -> None:
    """Regression: subsection INSERT with chapter carry-forward must not
    materialise a duplicate when the section already lives in a different part.

    Pattern: statute has part:4/section:51d; amendment op carries a chapter:1
    scope from carry-forward and targets section 51d § 2 mom.  The old
    chapter-scoped guard only checked chapter:1 (absent), so it created a
    phantom section:51d inside chapter:1.  The fix: for subsection-level ops
    (_target_paragraph / _target_item set) the guard is global — if the section
    exists anywhere, materialisation is blocked and the subsection dispatch path
    handles it via the existing section.
    """
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="4",
                children=(
                    _sec("51d", _sub("1", _content("existing 51d subsection 1"))),
                ),
            ),
        )
    )
    payload = _sec("51d", _sub("2", _content("new subsection 2")))
    op = AmendmentOp(
        op_id="subsec_insert_51d_2",
        op_type="INSERT",
        target_section="51d",
        target_unit_kind="section",
        target_chapter="1",
        target_paragraph=2,
        source_statute="2004/717",
        source_issue_date=_DATE,
    )

    result = _apply_materialization(state, op, payload, "test")

    # Must return None — the section exists globally; subsection dispatch handles it.
    assert result is None


def test_apply_materialization_skips_unscoped_whole_section_replace() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    _sec("1", _content("existing 1")),
                ),
            )
        )
    )
    payload = _sec("14", IRNode(kind=IRNodeKind.NUM, text="14 §"), _content("new top-level text"))
    op = AmendmentOp(
        op_id="materialize_14",
        op_type="REPLACE",
        target_section="14",
        target_unit_kind="section",
        source_statute="2006/395",
        source_issue_date=_DATE,
    )

    result = _apply_materialization(state, op, payload, "test")

    assert result is None


def test_apply_op_rejects_contradictory_typed_intent_action_family() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Repeal,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("1", _content("old"))))
    op = _op(op_type="REPLACE", target_section="1")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    rop.intent = Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(
            address=LegalAddress(path=(("section", "1"),)),
        ),
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
        ),
    )

    with pytest.raises(RuntimeError, match="FI_TYPED_INTENT_ACTION_MISMATCH"):
        apply_op(
            state,
            op,
            _ctx(state.ir),
            None,
            rop=rop,
        )


def test_legacy_dispatch_shell_for_rop_prefers_late_waist_fields() -> None:
    op = _op(
        op_type="REPEAL",
        target_section="9",
        target_paragraph=99,
        named_row_targets=("alpha", "beta"),
        body_root_replace_fallback=True,
        fallback_provenance=True,
        voimaantulo_repeal=True,
        extraction_provenance_tags=("extract_a",),
        target_guessing_provenance_tags=("guess_a",),
        scope_provenance_tags=("scope_a",),
        is_temporary=True,
        witness_rule_id="rule-1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="2",
    )
    rop._target_address_override = LegalAddress(
        path=(("chapter", "2"), ("section", "1"), ("subsection", "2"), ("item", "a"))
    )
    rop._source_statute_override = "2020/1"
    rop._source_title_override = "Typed Source"
    rop.named_row_targets = ("typed_row",)
    rop.body_root_replace_fallback = False
    rop.fallback_provenance = False
    rop.voimaantulo_repeal = False
    rop.extraction_provenance_tags = ("typed_extract",)
    rop.target_guessing_provenance_tags = ("typed_guess",)
    rop.scope_provenance_tags = ("typed_scope",)
    rop.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="2",
    )
    rop.is_temporary = False
    rop.witness_rule_id = "typed-rule"

    bridge = _legacy_dispatch_shell_for_rop(rop)

    assert bridge is not op
    assert bridge.target_section == "1"
    assert bridge.target_chapter == "2"
    assert bridge.target_paragraph == 2
    assert bridge.target_item == "a"
    assert bridge.source_statute == "2020/1"
    assert bridge.source_title == "Typed Source"
    assert bridge.named_row_targets == ("typed_row",)
    assert bridge.body_root_replace_fallback is False
    assert bridge.fallback_provenance is False
    assert bridge.voimaantulo_repeal is False
    assert bridge.extraction_provenance_tags == ("typed_extract",)
    assert bridge.target_guessing_provenance_tags == ("typed_guess",)
    assert bridge.scope_provenance_tags == ("typed_scope",)
    assert bridge.scope_confidence is not None
    assert bridge.scope_confidence.tag == "chapter_scope_from_explicit_chunk"
    assert bridge.scope_confidence.source == "explicit_chunk"
    assert bridge.scope_confidence.confidence == "explicit"
    assert bridge.scope_confidence.resolved_chapter == "2"
    assert bridge.resolved_scope_confidence is not None
    assert bridge.resolved_scope_confidence.tag == "chapter_scope_from_explicit_chunk"
    assert bridge.resolved_scope_confidence.source == "explicit_chunk"
    assert bridge.resolved_scope_confidence.confidence == "explicit"
    assert bridge.resolved_scope_confidence.resolved_chapter == "2"
    assert bridge.is_temporary is False
    assert bridge.witness_rule_id == "typed-rule"


def test_resolvedop_resolved_amend_sub_ir_uses_stable_slot_lookup() -> None:
    amend_sub = _sub("2", _content("assigned via slot"))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=99)
    slots = SubsectionSlotMap()
    slots.assign(
        AmendmentOp(
            op_id=op.op_id,
            op_type=op.op_type,
            target_section="1",
            target_unit_kind="section",
            target_paragraph=2,
            source_statute=op.source_statute,
            source_issue_date=op.source_issue_date,
        ),
        amend_sub,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=slots,
            sparse_slot_bindings=(),
            used_subs=(0,),
            unassigned_payload_slots=(),
        ),
    )

    assert rop.resolved_amend_sub_ir() is amend_sub


def test_resolvedop_resolved_amend_sub_ir_does_not_singleton_fallback_from_muutos_ir() -> None:
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
    amend_sub = _sub("1", _content("single subsection payload"))
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("1", amend_sub),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=SubsectionSlotMap(),
            sparse_slot_bindings=(),
            used_subs=(),
            unassigned_payload_slots=(),
        ),
    )

    assert rop.resolved_amend_sub_ir() is None


def test_resolvedop_binds_identity_slot_lookup_only_at_construction() -> None:
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    op.op_id = ""
    amend_sub = _sub("2", _content("assigned by legacy identity"))
    slots = SubsectionSlotMap()
    slots.assign(op, amend_sub)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=slots,
            sparse_slot_bindings=(),
            used_subs=(0,),
            unassigned_payload_slots=(),
        ),
    )

    assert rop.amend_sub_ir is amend_sub
    assert rop.has_assigned_subsection_payload() is True
    assert rop.resolved_amend_sub_ir() is amend_sub


def test_resolvedop_does_not_identity_rescue_nonblank_id_miss() -> None:
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    blank_assigned_op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    blank_assigned_op.op_id = ""
    amend_sub = _sub("2", _content("assigned by identity"))
    slots = SubsectionSlotMap()
    slots.assign(blank_assigned_op, amend_sub)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=slots,
            sparse_slot_bindings=(),
            used_subs=(0,),
            unassigned_payload_slots=(),
        ),
    )

    assert rop.amend_sub_ir is None
    assert rop.has_assigned_subsection_payload() is False


def test_resolvedop_slot_assignment_uses_stable_op_id_and_reports_presence() -> None:
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    op.op_id = "slot_presence"
    amend_sub = _sub("2", _content("assigned by stable id"))
    slots = SubsectionSlotMap()
    slots.assign(op, amend_sub)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=slots,
            sparse_slot_bindings=(),
            used_subs=(0,),
            unassigned_payload_slots=(),
        ),
    )

    assert rop.has_assigned_subsection_payload() is True
    assert rop.resolved_amend_sub_ir() is amend_sub


def test_normalize_subsection_target_hint_rebinds_resolved_target_address() -> None:
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
    )
    master_subsecs = [_sub("1", _para("a", "only item"))]

    normalized = _normalize_subsection_target_hint_ir(rop, master_subsecs, None, "1 §")

    assert isinstance(normalized, ResolvedOp)
    assert normalized.effective_target_paragraph == 1
    assert normalized.effective_target_item_label == "2"
    assert normalized.resolved_target_address is not None
    assert normalized.resolved_target_address.path == (("section", "1"), ("subsection", "1"), ("item", "2"))
    assert normalized.targets_subsection_only() is False
    assert normalized.targets_whole_unit("section") is False


def test_normalize_subsection_target_hint_keeps_real_inserted_moment_on_subsection_lane() -> None:
    op = _op(op_type="INSERT", target_section="6", target_paragraph=2)
    master_subsecs = [
        _sub(
            "1",
            _intro("Sen lisäksi, mitä laissa säädetään, tiivistelmässä on mainittava, että"),
            _para("1", "ensimmäinen kohta"),
            _para("2", "toinen kohta"),
        )
    ]
    amend_sub = _sub(
        "1",
        _intro("Tiivistelmässä annettavia keskeisiä tietoja ovat esimerkiksi:"),
        _para("1", "lyhyt kuvaus liikkeeseenlaskijasta"),
        _para("2", "lyhyt kuvaus arvopaperista"),
    )

    normalized = _normalize_subsection_target_hint_ir(op, master_subsecs, amend_sub, "6 § 2 mom")

    if isinstance(normalized, ResolvedOp):
        assert normalized.effective_target_paragraph == 2
        assert normalized.effective_target_item_label is None
    else:
        assert normalized.target_paragraph == 2
        assert normalized.target_item is None


def test_resolvedop_from_lo_canonicalizes_roman_part_scope_on_replay_address() -> None:
    lo = LegalOperation(
        op_id="roman_part_insert",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("part", "III"), ("chapter", "1"), ("section", "4"))),
    )
    op = AmendmentOp.from_lo(lo, 0)[0]

    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="4",
        target_chapter="1",
    )

    assert rop.resolved_target_address is not None
    assert rop.resolved_target_address.path == (("part", "3"), ("chapter", "1"), ("section", "4"))
    assert rop.resolved_target_scope_part_label == "3"


def test_build_subsection_slot_assignment_wrapper_exposes_typed_result() -> None:
    muutos_ir = _sec("14", _sub("1"), _sub("2"))
    op = _op(op_type="REPLACE", target_section="14", target_paragraph=2)

    got = _build_subsection_slot_assignment(muutos_ir, [op])

    assert got.for_op(op).label == "2"
    assert got.has_op(op) is True


def test_emit_section_snapshot_preserves_base_address_for_removed_section_repeal() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="iv",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="13",
                        children=(),
                    ),
                ),
            )
        )
    )
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.PART,
            label="iv",
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="13",
                    children=(_sec("4", IRNode(kind=IRNodeKind.HEADING, text="Avaintietoesite")),),
                ),
            ),
        )
    )
    lo_ops = []

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="4",
        target_chapter="13",
        target_part=None,
        group_rops=[
            ResolvedOp.from_amendment_op(
                AmendmentOp(
                    op_id="repeal_4",
                    op_type="REPEAL",
                    target_section="4",
                    target_unit_kind="section",
                    target_chapter="13",
                    source_statute="2022/954",
                    source_issue_date=_DATE,
                ),
                muutos_ir=None,
                cross_ir=None,
                target_unit_kind="section",
                target_norm="4",
                target_chapter="13",
            )
        ],
        lo_ops_out=lo_ops,
        amendment_id="2022/954",
        source_title="Repeal",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    assert len(lo_ops) == 1
    assert lo_ops[0].target.path == (
        ("part", "iv"),
        ("chapter", "13"),
        ("section", "4"),
    )
    assert lo_ops[0].payload is not None
    assert lo_ops[0].payload.attrs.get("lawvm_repeal_placeholder") == "1"


def test_emit_section_snapshot_uses_typed_sec1_fallback_for_absent_whole_section_repeal() -> None:
    state = _make_state(_body())
    lo_ops = []

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_part=None,
        group_rops=[
            ResolvedOp.from_amendment_op(
                AmendmentOp(
                    op_id="repeal_1",
                    op_type="REPEAL",
                    target_section="1",
                    target_unit_kind="section",
                    sec1_body_johto_fallback=True,
                    source_statute="2022/955",
                    source_issue_date=_DATE,
                ),
                muutos_ir=None,
                cross_ir=None,
                target_unit_kind="section",
                target_norm="1",
                target_chapter=None,
            )
        ],
        lo_ops_out=lo_ops,
        amendment_id="2022/955",
        source_title="Repeal",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=None,
    )

    assert len(lo_ops) == 1
    assert lo_ops[0].action == StructuralAction.REPEAL
    assert lo_ops[0].payload is None


def test_emit_section_snapshot_does_not_import_muutos_payload_for_absent_repeal() -> None:
    state = _make_state(_body())
    lo_ops = []

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="11",
        target_chapter="13",
        target_part=None,
        group_rops=[
            ResolvedOp.from_amendment_op(
                AmendmentOp(
                    op_id="repeal_13_11",
                    op_type="REPEAL",
                    target_section="11",
                    target_unit_kind="section",
                    target_chapter="13",
                    source_statute="2011/1503",
                    source_issue_date=_DATE,
                    voimaantulo_repeal=True,
                ),
                muutos_ir=_sec(
                    "11",
                    IRNode(kind=IRNodeKind.HEADING, text="Tekninen keksintö"),
                    _sub("1", _content("foreign body")),
                ),
                cross_ir=None,
                target_unit_kind="section",
                target_norm="11",
                target_chapter="13",
            )
        ],
        lo_ops_out=lo_ops,
        amendment_id="2011/1503",
        source_title="Laivavarustelaki",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=None,
    )

    assert len(lo_ops) == 1
    assert lo_ops[0].target.path == (("chapter", "13"), ("section", "11"))
    assert lo_ops[0].payload is not None
    assert lo_ops[0].payload.attrs.get("lawvm_repeal_placeholder") == "1"
    assert "Tekninen keksintö" not in " ".join(irnode_to_text(lo_ops[0].payload).split())


def test_emit_section_snapshot_records_pathology_for_payloadless_container_replace_without_base() -> None:
    state = _make_state(_body())
    lo_ops = []
    source_pathologies = []

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="7",
        target_chapter=None,
        target_part=None,
        group_rops=[
            ResolvedOp.from_amendment_op(
                AmendmentOp(
                    op_id="replace_ch_7",
                    op_type="REPLACE",
                    target_section="7",
                    target_unit_kind="chapter",
                    source_statute="2099/7",
                    source_issue_date=_DATE,
                ),
                muutos_ir=None,
                cross_ir=None,
                target_unit_kind="chapter",
                target_norm="7",
                target_chapter=None,
            )
        ],
        lo_ops_out=lo_ops,
        amendment_id="2099/7",
        source_title="Replace chapter",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=None,
        source_pathologies_out=source_pathologies,
    )

    assert lo_ops == []
    assert len(source_pathologies) == 1
    assert source_pathologies[0].code == "CONTAINER_REPLACE_TARGET_ABSENT"
    assert source_pathologies[0].detail["has_payload"] is False


def test_emit_section_snapshot_emits_subsection_snapshots_for_whole_section_payload() -> None:
    section = _sec(
        "3",
        _sub("1", _content("First")),
        _sub("2", _content("Second")),
        _sub("3", _content("Third")),
    )
    state = _make_state(_body(section))
    lo_ops = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_3",
            op_type="REPLACE",
            target_section="3",
            target_unit_kind="section",
            source_statute="2019/1223",
            source_issue_date=_DATE,
        ),
        muutos_ir=section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "3"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2019/1223",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=_body(section),
    )

    assert len(lo_ops) == 4
    assert lo_ops[0].target.path == (("section", "3"),)
    assert [op.target.path for op in lo_ops[1:]] == [
        (("section", "3"), ("subsection", "1")),
        (("section", "3"), ("subsection", "2")),
        (("section", "3"), ("subsection", "3")),
    ]
    assert [op.payload.label if op.payload is not None else None for op in lo_ops[1:]] == ["1", "2", "3"]
    assert [op.action for op in lo_ops[1:]] == [
        StructuralAction.REPLACE,
        StructuralAction.REPLACE,
        StructuralAction.REPLACE,
    ]


def test_emit_section_snapshot_inserts_new_subsection_addresses_not_in_base() -> None:
    base_section = _sec("3", _sub("1", _content("First")))
    payload_section = _sec(
        "3",
        _sub("1", _content("First")),
        _sub("2", _content("Second")),
        _sub("3", _content("Third")),
    )
    state = _make_state(_body(payload_section))
    lo_ops = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_3",
            op_type="REPLACE",
            target_section="3",
            target_unit_kind="section",
            source_statute="2019/1223",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "3"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2019/1223",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=_body(base_section),
    )

    assert [op.action for op in lo_ops[1:]] == [
        StructuralAction.REPLACE,
        StructuralAction.INSERT,
        StructuralAction.INSERT,
    ]


def test_emit_section_snapshot_prefers_typed_body_chapter_move_from_over_lo_provenance_tag() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="5",
            children=(_sec("23", _content("old chapter 5 text")),),
        )
    )
    payload_section = _sec("23", _content("new chapter 6 text"))
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(payload_section,),
            )
        )
    )
    lo_ops: list[LegalOperation] = []
    lo = LegalOperation(
        op_id="move_23",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
        provenance_tags=("body_chapter_move_from:9",),
    )
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="move_23",
            op_type="INSERT",
            target_section="23",
            target_unit_kind="section",
            target_chapter="6",
            body_chapter_move_from="5",
            source_statute="2099/23",
            source_issue_date=_DATE,
            lo=lo,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_address=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2099/23",
        source_title="Move",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "5"), ("section", "23"))
        for op in lo_ops
    )
    assert not any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "9"), ("section", "23"))
        for op in lo_ops
    )


def test_emit_section_snapshot_does_not_use_lo_provenance_tag_without_typed_body_chapter_move_from() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="5",
            children=(_sec("23", _content("old chapter 5 text")),),
        )
    )
    payload_section = _sec("23", _content("new chapter 6 text"))
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(payload_section,),
            )
        )
    )
    lo_ops: list[LegalOperation] = []
    lo = LegalOperation(
        op_id="move_23_untyped",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
        provenance_tags=("body_chapter_move_from:5",),
    )
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="move_23_untyped",
            op_type="INSERT",
            target_section="23",
            target_unit_kind="section",
            target_chapter="6",
            source_statute="2099/23",
            source_issue_date=_DATE,
            lo=lo,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_address=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2099/23",
        source_title="Move",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    assert not any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "5"), ("section", "23"))
        for op in lo_ops
    )


def test_emit_section_snapshot_exports_missing_repealed_subsection_child() -> None:
    base_section = _sec(
        "3",
        _sub("1", _content("First")),
        _sub("2", _content("Second")),
        _sub("3", _content("Third")),
    )
    payload_section = _sec(
        "3",
        _sub("2", _content("Second")),
        _sub("3", _content("Third updated")),
    )
    state = _make_state(_body(payload_section))
    lo_ops: list[LegalOperation] = []

    repeal_rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="repeal_3_1",
            op_type="REPEAL",
            target_section="3",
            target_unit_kind="section",
            target_paragraph=1,
            source_statute="2019/1223",
            source_issue_date=_DATE,
        ),
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "3"), ("subsection", "1"))),
    )
    replace_rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_3_3",
            op_type="REPLACE",
            target_section="3",
            target_unit_kind="section",
            target_paragraph=3,
            source_statute="2019/1223",
            source_issue_date=_DATE,
        ),
        muutos_ir=_sub("3", _content("Third updated")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "3"), ("subsection", "3"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_part=None,
        group_rops=[repeal_rop, replace_rop],
        lo_ops_out=lo_ops,
        amendment_id="2019/1223",
        source_title="Mixed subsection update",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=_body(base_section),
    )

    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("section", "3"), ("subsection", "1"))
        for op in lo_ops
    )
    assert any(
        op.action is StructuralAction.REPLACE
        and op.target.path == (("section", "3"), ("subsection", "3"))
        for op in lo_ops
    )


def test_emit_section_snapshot_skips_payload_child_that_same_group_explicitly_repeals() -> None:
    base_section = _sec(
        "1",
        _sub("1", _content("First")),
        _sub("2", _content("Second")),
    )
    state = _make_state(_body(base_section))
    lo_ops: list[LegalOperation] = []

    repeal_rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="repeal_1_2",
            op_type="REPEAL",
            target_section="1",
            target_unit_kind="section",
            target_paragraph=2,
            source_statute="2009/1688",
            source_issue_date=_DATE,
        ),
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
    )
    replace_rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_1",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
            source_statute="2009/1688",
            source_issue_date=_DATE,
        ),
        muutos_ir=base_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_part=None,
        group_rops=[repeal_rop, replace_rop],
        lo_ops_out=lo_ops,
        amendment_id="2009/1688",
        source_title="Mixed repeal and section replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=_body(base_section),
    )

    assert any(
        op.action is StructuralAction.REPLACE and op.target.path == (("section", "1"),)
        for op in lo_ops
    )
    assert any(
        op.action is StructuralAction.REPLACE
        and op.target.path == (("section", "1"), ("subsection", "1"))
        for op in lo_ops
    )
    assert not any(
        op.target.path == (("section", "1"), ("subsection", "2"))
        and op.action is StructuralAction.REPLACE
        for op in lo_ops
    )
    assert any(
        op.target.path == (("section", "1"), ("subsection", "2"))
        and op.action is StructuralAction.REPEAL
        for op in lo_ops
    )


def test_emit_section_snapshot_sparse_chapter_replace_skips_missing_child_repeals() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="1",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Yleiset säännökset"),
                _sec("1", _content("base 1")),
                _sec("2", _content("base 2")),
                _sec("3", _content("base 3")),
                _sec("4", _content("base 4")),
                _sec("5", _content("base 5")),
                _sec("6", _content("base 6")),
                _sec("7", _content("base 7")),
            ),
        )
    )
    payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 luku"),
            IRNode(kind=IRNodeKind.HEADING, text="Yleiset säännökset"),
            _sec("2", _content("new 2")),
            _sec("3", _content("new 3")),
            _sec("8", _content("new 8")),
        ),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=payload.children,
            )
        )
    )
    lo_ops: list[LegalOperation] = []
    pathologies: list[SourcePathology] = []
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_1_chapter",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="chapter",
            source_statute="2021/669",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "1"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="1",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2021/669",
        source_title="Sparse chapter replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
        source_pathologies_out=pathologies,
    )

    assert any(op.op_id == "snapshot_chapter_1" for op in lo_ops)
    assert not any(op.op_id == "snapshot_repeal_missing_section_5_from_chapter_1" for op in lo_ops)
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert [p.detail["recovery_kind"] for p in pathologies] == [
        "container_snapshot_sparse_missing_child_repeal_skip"
    ]


def test_subsection_repeal_does_not_copy_whole_section_heading_from_muutos_ir() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    _sec(
                        "8",
                        IRNode(kind=IRNodeKind.HEADING, text="Öljyn kuljettaminen sisävesialueella"),
                        _sub("1", _content("Chapter 2 section 8")),
                        _sub("3", _content("Chapter 2 section 8 subsection 3")),
                    ),
                ),
            ),
        )
    )
    muutos_ir = _sec(
        "8",
        IRNode(kind=IRNodeKind.HEADING, text="Muut haitallisten nestemäisten aineiden kuljetuksen todistuskirjat"),
        _sub("1", _content("Chapter 4 section 8")),
        _sub("3", _content("Chapter 4 section 8 subsection 3")),
    )

    result = _apply_deterministic_subsection_op(
        state,
        _op(op_type="REPEAL", target_section="8", target_paragraph=3),
        (("chapter", "2"), ("section", "8")),
        muutos_ir,
        None,
        None,
        get_replay_profile("legal_pit"),
        "2017/275",
        None,
        rop=ResolvedOp.from_amendment_op(
            AmendmentOp(
                op_id="vts_repeal_P_8_m3",
                op_type="REPEAL",
                target_section="8",
                target_unit_kind="section",
                target_paragraph=3,
                source_statute="2017/275",
                source_issue_date=_DATE,
            ),
            muutos_ir=muutos_ir,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="8",
            target_chapter=None,
            target_address=LegalAddress(path=(("section", "8"), ("subsection", "3"))),
        ),
    )

    assert result is not None
    sec = result.find_section("8", "2")
    assert sec is not None
    text = " ".join(irnode_to_text(sec).split())
    assert "Öljyn kuljettaminen sisävesialueella" in text
    assert "Muut haitallisten nestemäisten aineiden kuljetuksen todistuskirjat" not in text


def test_emit_section_snapshot_skips_container_child_snapshots_for_heading_only_group() -> None:
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Oikaisuvaatimus ja muutoksenhaku"),
            _sec("18", _content("section 18")),
            _sec("19", _content("section 19")),
            _sec("23", _content("section 23")),
        ),
    )
    state = _make_state(_body(chapter_payload))
    lo_ops = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_5_heading",
            op_type="REPLACE",
            target_section="5",
            target_unit_kind="chapter",
            target_special="otsikko",
            source_statute="1997/1251",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="5",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "5"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="5",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="1997/1251",
        source_title="Heading replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=_body(chapter_payload),
    )

    assert len(lo_ops) == 1
    assert lo_ops[0].target.path == (("chapter", "5"),)


def test_emit_section_snapshot_repeals_prior_chapter_address_without_move_clause_marker() -> None:
    payload_section = _sec("23", _content("new chapter 6 content"))
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(payload_section,),
            )
        )
    )
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="5",
            children=(_sec("23", _content("old chapter 5 content")),),
        )
    )
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_23_from_chapter_5",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "5"), ("section", "23"))),
            payload=_sec("23", _content("old chapter 5 content")),
            source=OperationSource(statute_id="1997/1251", title="Old", enacted="1997-12-19"),
            group_id="finland-johto:1997/1251",
        )
    ]

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_23",
            op_type="REPLACE",
            target_section="23",
            target_unit_kind="section",
            target_chapter="6",
            source_statute="1997/1251",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_address=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="1997/1251",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    repeal = next(op for op in lo_ops if op.op_id == "snapshot_move_repeal_23")
    assert repeal.action == StructuralAction.REPEAL
    assert repeal.target.path == (("chapter", "5"), ("section", "23"))


def test_emit_section_snapshot_keeps_chapter_scoped_address_when_only_root_homonym_exists() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Erinäisiä säännöksiä"),
                ),
            ),
            _sec("22", _sub("1", _content("Tämä laki tulee voimaan."))),
        )
    )
    lo_ops: list[LegalOperation] = []
    payload_section = _sec("22", IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"))
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="insert_22_heading",
            op_type="INSERT",
            target_section="22",
            target_unit_kind="section",
            target_chapter="4",
            target_special="otsikko",
            source_statute="2024/247",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="22",
        target_chapter="4",
        target_address=LegalAddress(path=(("chapter", "4"), ("section", "22"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="22",
        target_chapter="4",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2024/247",
        source_title="Heading insert",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
    )

    assert any(op.target.path == (("chapter", "4"), ("section", "22")) for op in lo_ops)
    assert all(op.target.path != (("section", "22"),) for op in lo_ops if op.action is not StructuralAction.REPEAL)


def test_emit_section_snapshot_reuses_replay_owned_subsection_lineage_without_base_path() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="5",
            children=(_sec("1", _sub("1", _content("toinen pykälä"))),),
        )
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="12",
                children=(_sec("1", _sub("1", _content("vanha teksti"))),),
            )
        )
    )
    old_section = _sec("1", _sub("1", _content("vanha teksti")))
    old_subsection = _sub("1", _content("vanha teksti"))
    lo_ops: list[LegalOperation] = [
        LegalOperation(
            op_id="snapshot_section_1",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "12"), ("section", "1"))),
            payload=old_section,
            source=OperationSource(statute_id="1999/416", enacted="1998-12-18", effective="1998-12-18"),
            group_id="finland-johto:1999/416",
        ),
        LegalOperation(
            op_id="snapshot_subsection_1_from_section_1",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "12"), ("section", "1"), ("subsection", "1"))),
            payload=old_subsection,
            source=OperationSource(statute_id="1999/416", enacted="1998-12-18", effective="1998-12-18"),
            group_id="finland-johto:1999/416",
        ),
    ]
    payload_section = _sec(
        "1",
        IRNode(kind=IRNodeKind.HEADING, text="Kulutushyödykkeen välittäjän vastuu"),
        _sub("1", _content("uusi 1 momentti")),
        _sub("2", _content("uusi 2 momentti")),
    )
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_12_1",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
            target_chapter="12",
            source_statute="2021/1242",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="12",
        target_address=LegalAddress(path=(("chapter", "12"), ("section", "1"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="12",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2021/1242",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    section_snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_section_1" and op.source is not None and op.source.statute_id == "2021/1242"
    )
    assert section_snapshot.action is StructuralAction.REPLACE
    assert section_snapshot.target.path == (("chapter", "12"), ("section", "1"))
    assert section_snapshot.source is not None
    assert section_snapshot.source.statute_id == "2021/1242"
    subsection_snapshot = next(op for op in lo_ops if op.op_id == "snapshot_subsection_1_from_section_1" and op.source is not None and op.source.statute_id == "2021/1242")
    assert subsection_snapshot.action is StructuralAction.REPLACE


def test_emit_section_snapshot_uses_insert_for_scoped_commencement_on_replay_owned_address() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="5",
            children=(_sec("1", _sub("1", _content("toinen pykälä"))),),
        )
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="12",
                children=(_sec("1", _sub("1", _content("uusi teksti"))),),
            )
        )
    )
    old_section = _sec("1", _sub("1", _content("vanha teksti")))
    old_subsection = _sub("1", _content("vanha teksti"))
    lo_ops: list[LegalOperation] = [
        LegalOperation(
            op_id="snapshot_section_1",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "12"), ("section", "1"))),
            payload=old_section,
            source=OperationSource(statute_id="1999/416", enacted="1998-12-18", effective="1998-12-18"),
            group_id="finland-johto:1999/416",
        ),
        LegalOperation(
            op_id="snapshot_subsection_1_from_section_1",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "12"), ("section", "1"), ("subsection", "1"))),
            payload=old_subsection,
            source=OperationSource(statute_id="1999/416", enacted="1998-12-18", effective="1998-12-18"),
            group_id="finland-johto:1999/416",
        ),
    ]
    payload_section = _sec(
        "1",
        IRNode(kind=IRNodeKind.HEADING, text="Kulutushyödykkeen välittäjän vastuu"),
        _sub("1", _content("uusi 1 momentti")),
    )
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_12_1",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
            target_chapter="12",
            source_statute="2021/1242",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="12",
        target_address=LegalAddress(path=(("chapter", "12"), ("section", "1"))),
        op_source=OperationSource(
            statute_id="2021/1242",
            enacted="2021-01-01",
            effective="2021-03-01",
        ),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="12",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2021/1242",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=dt.date(2021, 1, 1),
        base_ir=base_ir,
    )

    section_snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_section_1" and op.source is not None and op.source.statute_id == "2021/1242"
    )
    assert section_snapshot.action is StructuralAction.INSERT
    subsection_snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_subsection_1_from_section_1" and op.source is not None and op.source.statute_id == "2021/1242"
    )
    assert subsection_snapshot.action is StructuralAction.INSERT


def test_emit_section_snapshot_does_not_repeal_prior_chapter_address_for_pure_insert() -> None:
    payload_section = _sec("8a", _content("new chapter 5 content"))
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(payload_section,),
            )
        )
    )
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="2",
            children=(_sec("8a", _content("old chapter 2 content")),),
        )
    )
    lo_ops: list[LegalOperation] = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="insert_8a",
            op_type="INSERT",
            target_section="8a",
            target_unit_kind="section",
            target_chapter="5",
            source_statute="2022/33",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="8a",
        target_chapter="5",
        target_address=LegalAddress(path=(("chapter", "5"), ("section", "8a"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="8a",
        target_chapter="5",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2022/33",
        source_title="Insert",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    assert all(op.op_id != "snapshot_move_repeal_8a" for op in lo_ops)
    assert any(op.target.path == (("chapter", "5"), ("section", "8a")) for op in lo_ops)


def test_materialize_pit_chapter_heading_shell_does_not_mask_older_section_children() -> None:
    from lawvm.core.ir import IRStatute, ProvisionTimeline, ProvisionVersion
    from lawvm.core.timeline import materialize_pit

    chapter_addr = LegalAddress(path=(("chapter", "8a"),))
    section_addr = LegalAddress(path=(("chapter", "8a"), ("section", "72a")))
    timelines = {
        chapter_addr: ProvisionTimeline(
            address=chapter_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-12-21",
                    enacted="2001-12-21",
                    variant_kind="permanent",
                    content=IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="8a",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="8 a luku"),
                            IRNode(kind=IRNodeKind.HEADING, text="Heading shell"),
                        ),
                    ),
                )
            ],
        ),
        section_addr: ProvisionTimeline(
            address=section_addr,
            versions=[
                ProvisionVersion(
                    effective="1996-09-01",
                    enacted="1996-09-01",
                    variant_kind="permanent",
                    content=_sec("72a", _sub("1", _content("section body"))),
                )
            ],
        ),
    }
    base = IRStatute(statute_id="test/1", title="Test", body=_body())

    pit = materialize_pit(
        timelines,
        as_of="9999-12-31",
        base=base,
    )

    body = pit.body
    chapter = next(child for child in body.children if child.kind == IRNodeKind.CHAPTER and child.label == "8a")
    assert any(child.kind == IRNodeKind.SECTION and child.label == "72a" for child in chapter.children)


def test_emit_section_snapshot_does_not_repeal_other_live_same_label_section() -> None:
    payload_section = _sec("1", _content("chapter 3 replacement"))
    state = _make_state(
        _body(
            IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(_sec("1", _content("chapter 1 stays")),)),
            IRNode(kind=IRNodeKind.CHAPTER, label="3", children=(payload_section,)),
        )
    )
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_1",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
            payload=_sec("1", _content("chapter 1 stays")),
            source=OperationSource(statute_id="older", title="Old", enacted="2000-01-01"),
            group_id="finland-johto:older",
        )
    ]

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_1",
            op_type="REPLACE",
            target_section="1",
            target_unit_kind="section",
            target_chapter="3",
            source_statute="2015/1752",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload_section,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="3",
        target_address=LegalAddress(path=(("chapter", "3"), ("section", "1"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="section",
        target_norm="1",
        target_chapter="3",
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2015/1752",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
    )

    assert all(op.op_id != "snapshot_move_repeal_1" for op in lo_ops)


def test_emit_section_snapshot_skips_container_child_for_cross_chapter_standalone_replace() -> None:
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Oikaisuvaatimus ja muutoksenhaku"),
            _sec("18", _content("section 18")),
            _sec("19", _content("section 19")),
            _sec("23", _content("stale chapter 5 shell")),
        ),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Old heading"),
                    _sec("18", _content("section 18")),
                    _sec("19", _content("section 19")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(_sec("23", _content("live chapter 6 section 23")),),
            ),
        )
    )
    lo_ops: list[LegalOperation] = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_5_chapter",
            op_type="REPLACE",
            target_section="5",
            target_unit_kind="chapter",
            source_statute="1997/1251",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="5",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "5"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="5",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="1997/1251",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
        standalone_section_targets=frozenset({(None, "6", "23")}),
    )

    assert any(op.target.path == (("chapter", "5"), ("section", "18")) for op in lo_ops)
    assert any(op.target.path == (("chapter", "5"), ("section", "19")) for op in lo_ops)
    assert not any(op.target.path == (("chapter", "5"), ("section", "23")) for op in lo_ops)


def test_emit_section_snapshot_skips_container_child_when_label_belongs_to_other_chapter() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(_sec("10", _content("chapter 3 section 10")),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(_sec("23", _content("chapter 6 section 23")),),
            ),
        )
    )
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Chapter 3"),
            _sec("10", _content("chapter 3 section 10 updated")),
            _sec("23", _content("shadowed section 23")),
        ),
    )
    lo_ops = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="insert_3",
            op_type="INSERT",
            target_section="3",
            target_unit_kind="chapter",
            source_statute="2004/543",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "3"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="3",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2004/543",
        source_title="Insert",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
    )

    assert any(op.target.path == (("chapter", "3"), ("section", "10")) for op in lo_ops)
    assert not any(op.target.path == (("chapter", "3"), ("section", "23")) for op in lo_ops)


def test_emit_section_snapshot_container_replace_repeals_missing_base_sections_under_part_wrapped_chapter() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.PART,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(
                        IRNode(kind=IRNodeKind.HEADING, text="Jatkuva tiedonantovelvollisuus"),
                        _sec("1", _content("base 1")),
                        _sec("2", _content("base 2")),
                        _sec("3", _content("base 3")),
                        _sec("4", _content("base 4")),
                    ),
                ),
            ),
        ),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="6",
                        children=(
                            IRNode(kind=IRNodeKind.HEADING, text="Jatkuva tiedonantovelvollisuus"),
                            _sec("1", _content("new 1")),
                            _sec("2", _content("new 2")),
                        ),
                    ),
                ),
            ),
        )
    )
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Jatkuva tiedonantovelvollisuus"),
            _sec("1", _content("new 1")),
            _sec("2", _content("new 2")),
        ),
    )
    lo_ops: list[LegalOperation] = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_6_chapter",
            op_type="REPLACE",
            target_section="6",
            target_unit_kind="chapter",
            source_statute="2016/519",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_address=LegalAddress(path=(("part", "3"), ("chapter", "6"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_part="3",
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2016/519",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    assert any(
        op.action is StructuralAction.REPEAL and op.target.path == (("part", "3"), ("chapter", "6"), ("section", "3"))
        for op in lo_ops
    )
    assert any(
        op.action is StructuralAction.REPEAL and op.target.path == (("part", "3"), ("chapter", "6"), ("section", "4"))
        for op in lo_ops
    )


def test_emit_section_snapshot_keeps_historic_container_replace_as_replace_and_repeals_missing_children() -> None:
    base_ir = _body(
        IRNode(
            kind=IRNodeKind.CHAPTER,
            label="7",
            children=(
                IRNode(kind=IRNodeKind.HEADING, text="Base chapter 7"),
                _sec("1", _content("base 1")),
            ),
        ),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7a",
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Historic chapter 7 a"),
                    _sec("1", _content("live 1")),
                    _sec("2", _content("live 2")),
                    _sec("2a", _content("stale 2a")),
                ),
            ),
        )
    )
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 a luku"),
            IRNode(kind=IRNodeKind.HEADING, text="Historic chapter 7 a"),
            _sec("1", _content("new 1")),
            _sec("2", _content("new 2")),
            _sec("3", _content("new 3")),
        ),
    )
    lo_ops: list[LegalOperation] = [
        LegalOperation(
            op_id="snapshot_chapter_7a",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7a"),)),
            payload=chapter_payload,
            source=OperationSource(
                statute_id="2014/998",
                title="Insert chapter 7a",
                enacted="2014-11-28",
                effective="2014-11-28",
            ),
            group_id="finland-johto:2014/998",
        ),
        LegalOperation(
            op_id="snapshot_section_2a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7a"), ("section", "2a"))),
            payload=_sec("2a", _content("historic 2a")),
            source=OperationSource(
                statute_id="2018/1032",
                title="Insert section 2a",
                enacted="2018-11-23",
                effective="2019-01-01",
            ),
            group_id="finland-johto:2018/1032",
        ),
        LegalOperation(
            op_id="snapshot_section_2b",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7a"), ("section", "2b"))),
            payload=_sec("2b", _content("historic 2b")),
            source=OperationSource(
                statute_id="2018/1032",
                title="Insert section 2b",
                enacted="2018-11-23",
                effective="2019-01-01",
            ),
            group_id="finland-johto:2018/1032",
        ),
        LegalOperation(
            op_id="snapshot_section_2c",
            sequence=3,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7a"), ("section", "2c"))),
            payload=_sec("2c", _content("historic 2c")),
            source=OperationSource(
                statute_id="2018/1032",
                title="Insert section 2c",
                enacted="2018-11-23",
                effective="2019-01-01",
            ),
            group_id="finland-johto:2018/1032",
        ),
    ]

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_7a_chapter",
            op_type="REPLACE",
            target_section="7a",
            target_unit_kind="chapter",
            source_statute="2024/1116",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="7a",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "7a"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="7a",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2024/1116",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=base_ir,
    )

    chapter_snapshot = next(
        op
        for op in lo_ops
        if op.target.path == (("chapter", "7a"),)
        and op.source is not None
        and op.source.statute_id == "2024/1116"
    )
    assert chapter_snapshot.action is StructuralAction.REPLACE
    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "7a"), ("section", "2b"))
        and op.source is not None
        and op.source.statute_id == "2024/1116"
        for op in lo_ops
    )
    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "7a"), ("section", "2c"))
        and op.source is not None
        and op.source.statute_id == "2024/1116"
        for op in lo_ops
    )


def test_emit_section_snapshot_keeps_container_child_when_same_label_exists_in_other_chapter() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter 6"),
                    _sec("1", _content("live chapter 6 section 1")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(_sec("1", _content("live chapter 15 section 1")),),
            ),
        )
    )
    chapter_payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Chapter 6"),
            _sec("1", _content("updated chapter 6 section 1")),
        ),
    )
    lo_ops: list[LegalOperation] = []

    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_6_chapter_keep_1",
            op_type="REPLACE",
            target_section="6",
            target_unit_kind="chapter",
            source_statute="2016/519",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "6"),)),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2016/519",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
        standalone_section_targets=frozenset({(None, "15", "1")}),
    )

    assert any(
        op.action is StructuralAction.REPLACE and op.target.path == (("chapter", "6"), ("section", "1"))
        for op in lo_ops
    )


def test_emit_section_snapshot_keeps_part_wrapped_container_child_when_timeline_path_strips_hcontainer() -> None:
    provisions = IRNode(
        kind=IRNodeKind.HCONTAINER,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="6",
                        children=(
                            IRNode(kind=IRNodeKind.HEADING, text="Chapter 6"),
                            _sec("1", _content("updated chapter 6 section 1")),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="12",
                        children=(_sec("1", _content("other chapter section 1")),),
                    ),
                ),
            ),
        ),
    )
    state = _make_state(_body(provisions))
    lo_ops: list[LegalOperation] = []

    chapter_payload = provisions.children[0].children[0]
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_part_wrapped_6_chapter_keep_1",
            op_type="REPLACE",
            target_section="6",
            target_unit_kind="chapter",
            target_part="3",
            source_statute="2016/519",
            source_issue_date=_DATE,
        ),
        muutos_ir=chapter_payload,
        cross_ir=None,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_address=LegalAddress(path=(("part", "3"), ("chapter", "6"))),
    )

    _emit_section_snapshot(
        state=state,
        target_unit_kind="chapter",
        target_norm="6",
        target_chapter=None,
        target_part="3",
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2016/519",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
    )

    snapshot = next(
        op
        for op in lo_ops
        if op.action is StructuralAction.REPLACE
        and op.op_id == "snapshot_section_1_from_chapter_6"
        and op.target.path == (("part", "3"), ("chapter", "6"), ("section", "1"))
    )
    assert snapshot.payload is not None
    assert snapshot.payload.attrs["lawvm_tail_policy"] == "replace_if_target_scope_requires"
    assert snapshot.payload.attrs["lawvm_payload_completeness_kind"] == "complete"


def test_emit_section_snapshot_prefers_unique_substantive_section_over_repeal_placeholder_when_unscoped() -> None:
    from lawvm.core import tree_ops as _tops

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(_sub("1", _content("repealed old section")),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2a",
                children=(
                    _sec(
                        "8",
                        _sub("1", _content("live substantive section")),
                        _sub("2", _content("later substantive section")),
                    ),
                ),
            ),
        )
    )
    lo_ops: list[LegalOperation] = []
    payload = _sec(
        "8",
        _sub("1", _content("updated substantive section")),
        _sub("2", _content("continued")),
    )
    rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="replace_unscoped_8",
            op_type="REPLACE",
            target_section="8",
            target_unit_kind="section",
            source_statute="2018/1313",
            source_issue_date=_DATE,
        ),
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="8",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "2a"), ("section", "8"))),
    )

    replaced_state = state.with_ir(
        _tops.replace_at(state.ir, (("chapter", "2a"), ("section", "8")), payload),
        preserve_provision_index=False,
    )

    _emit_section_snapshot(
        state=replaced_state,
        target_unit_kind="section",
        target_norm="8",
        target_chapter=None,
        target_part=None,
        group_rops=[rop],
        lo_ops_out=lo_ops,
        amendment_id="2018/1313",
        source_title="Replace",
        source_issue_date=_DATE,
        source_effective_date=_DATE,
        base_ir=state.ir,
    )

    assert any(
        op.action is StructuralAction.REPLACE
        and op.target.path == (("chapter", "2a"), ("section", "8"))
        for op in lo_ops
    )


def test_valid_target_group_path_hint_prefers_unique_substantive_section_over_repeal_placeholder_when_unscoped() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(IRNode(kind=IRNodeKind.NUM, text="8 §"),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2a",
                children=(
                    _sec(
                        "8",
                        _sub("1", _content("live substantive section")),
                    ),
                ),
            ),
        )
    )

    hint = _valid_target_group_path_hint(
        state,
        "section",
        "8",
        None,
        None,
        (("chapter", "2"), ("section", "8")),
    )

    assert hint == (("chapter", "2a"), ("section", "8"))


def test_expired_temporary_section_merge_base_keeps_current_live_section_after_first_change() -> None:
    base_section = _sec(
        "3",
        _sub("1", _content("base 1")),
        _sub("2", _content("base 2")),
    )
    temp_section = _sec(
        "3",
        _sub("1", _content("temp 1")),
        _sub("2", _content("temp 2")),
    )
    current_live_section = _sec(
        "3",
        _sub("1", _content("temp 1")),
        _sub("2", _content("new 2")),
    )
    section_path = (("section", "3"),)
    replay_history = [
        LegalOperation(
            op_id="snapshot_section_3",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=base_section,
            source=OperationSource(
                effective="2019-01-01",
                enacted="2019-01-01",
                expires="",
                statute_id="2018/522",
            ),
        ),
        LegalOperation(
            op_id="snapshot_section_3",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=temp_section,
            source=OperationSource(
                effective="2019-01-01",
                enacted="2019-01-01",
                expires="2019-12-31",
                statute_id="2018/523",
            ),
        ),
    ]
    op = ResolvedOp.from_amendment_op(
        _op(op_type="REPLACE", target_section="3", target_paragraph=1),
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        op_source=OperationSource(
            statute_id="2019/1223",
            enacted="2019-12-04",
            effective="2020-01-01",
            expires="",
        ),
    )

    got = _expired_temporary_section_merge_base(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        base_ir=_body(base_section),
        current_live_section=current_live_section,
    )

    assert got == current_live_section
    rebase_kind, latest_expires = _expired_temporary_section_merge_base_rebase_info(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        current_live_section=current_live_section,
    )
    assert rebase_kind == "expired_latest_snapshot_current_live_section"
    assert latest_expires == "2019-12-31"


def test_expired_temporary_section_merge_base_case_b_suppressed_when_current_wave_modified() -> None:
    """CASE B must not fire when current_live was legitimately modified by current-wave ops.

    Regression test for the 2007/527 §24 bug: amendment 2022/331 had multiple
    ops targeting the same section (sub:4 REPLACE, sub:3 REPLACE, sub:1 item:1 REPLACE).
    The first two ops legitimately modified current_live beyond the expired-temp state.
    CASE B was incorrectly rebasing the third op to latest_snapshot (2022/300's OLD text),
    discarding the valid changes made by the first two ops.

    Setup:
    - perm_section (latest_snapshot): permanent state from an earlier amendment
    - temp_section (previous_snapshot): expired temporary state
    - current_live_section: perm_section with sub:2 legitimately updated by a prior
      current-wave op (no longer equal to temp_section.payload)
    - current_live_section != temp_section  → CASE B should NOT fire → return None
    """
    perm_section = _sec(
        "24",
        _sub("1", _content("perm sub1")),
        _sub("2", _content("perm sub2")),
        _sub("3", _content("perm sub3")),
    )
    temp_section = _sec(
        "24",
        _sub("1", _content("temp sub1")),
        _sub("2", _content("temp sub2")),
        _sub("3", _content("temp sub3")),
    )
    # current-wave op already updated sub:3; live differs from perm_section BUT
    # is NOT equal to temp_section — it was modified legitimately
    current_live_section = _sec(
        "24",
        _sub("1", _content("perm sub1")),
        _sub("2", _content("perm sub2")),
        _sub("3", _content("new sub3 from current wave")),
    )
    section_path = (("section", "24"),)
    replay_history = [
        LegalOperation(
            op_id="snapshot_section_24",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=temp_section,
            source=OperationSource(
                effective="2021-01-01",
                enacted="2021-01-01",
                expires="2022-04-30",
                statute_id="2021/541",
            ),
        ),
        LegalOperation(
            op_id="snapshot_section_24",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=perm_section,
            source=OperationSource(
                effective="2022-01-01",
                enacted="2022-01-01",
                expires="",
                statute_id="2022/300",
            ),
        ),
    ]
    # Third op in same amendment group (sub:1 item:1 REPLACE) — previous ops
    # already legitimately modified current_live
    op = ResolvedOp.from_amendment_op(
        _op(op_type="REPLACE", target_section="24", target_paragraph=1, target_item="1"),
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="24",
        target_chapter=None,
        op_source=OperationSource(
            statute_id="2022/331",
            enacted="2022-03-11",
            effective="2022-05-01",
            expires="",
        ),
    )

    got = _expired_temporary_section_merge_base(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        base_ir=_body(perm_section),
        current_live_section=current_live_section,
    )
    # CASE B must not fire — current_live has been legitimately modified
    # beyond temp_section; returning perm_section here would revert that work
    assert got is None

    rebase_kind, latest_expires = _expired_temporary_section_merge_base_rebase_info(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        current_live_section=current_live_section,
    )
    assert rebase_kind is None
    assert latest_expires is None


def test_expired_temporary_section_merge_base_case_b_fires_when_live_is_temp_state() -> None:
    """CASE B must still fire when current_live IS the expired-temp payload.

    This tests the original CASE B intent: when the live section holds the expired
    temporary text (not legitimately modified by current-wave ops), the function
    must rebase to the latest permanent snapshot.
    """
    perm_section = _sec(
        "24",
        _sub("1", _content("perm sub1")),
        _sub("2", _content("perm sub2")),
    )
    temp_section = _sec(
        "24",
        _sub("1", _content("temp sub1")),
        _sub("2", _content("temp sub2")),
    )
    # current_live IS the expired temp state (contaminated, not legitimately modified)
    current_live_section = temp_section
    section_path = (("section", "24"),)
    replay_history = [
        LegalOperation(
            op_id="snapshot_section_24",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=temp_section,
            source=OperationSource(
                effective="2021-01-01",
                enacted="2021-01-01",
                expires="2022-04-30",
                statute_id="2021/541",
            ),
        ),
        LegalOperation(
            op_id="snapshot_section_24",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=perm_section,
            source=OperationSource(
                effective="2022-01-01",
                enacted="2022-01-01",
                expires="",
                statute_id="2022/300",
            ),
        ),
    ]
    op = ResolvedOp.from_amendment_op(
        _op(op_type="REPLACE", target_section="24", target_paragraph=1, target_item="1"),
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="24",
        target_chapter=None,
        op_source=OperationSource(
            statute_id="2022/331",
            enacted="2022-03-11",
            effective="2022-05-01",
            expires="",
        ),
    )

    got = _expired_temporary_section_merge_base(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        base_ir=_body(perm_section),
        current_live_section=current_live_section,
    )
    # CASE B must fire — current_live IS the expired temp state
    assert got == perm_section

    rebase_kind, latest_expires = _expired_temporary_section_merge_base_rebase_info(
        op=op,
        section_path=section_path,
        replay_history_ops=replay_history,
        current_live_section=current_live_section,
    )
    assert rebase_kind == "temporary_previous_snapshot_latest_snapshot"


def test_apply_whole_section_replace_emits_temporary_section_rebase_pathology() -> None:
    base_section = _sec(
        "3",
        _sub("1", _content("base 1")),
        _sub("2", _content("base 2")),
    )
    temp_section = _sec(
        "3",
        _sub("1", _content("temp 1")),
        _sub("2", _content("temp 2")),
    )
    current_live_section = _sec(
        "3",
        _sub("1", _content("temp 1")),
        _sub("2", _content("new 2")),
    )
    state = _make_state(_body(current_live_section))
    section_path = (("section", "3"),)
    replay_history = [
        LegalOperation(
            op_id="snapshot_section_3",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=base_section,
            source=OperationSource(
                effective="2019-01-01",
                enacted="2019-01-01",
                expires="",
                statute_id="2018/522",
            ),
        ),
        LegalOperation(
            op_id="snapshot_section_3",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=section_path),
            payload=temp_section,
            source=OperationSource(
                effective="2019-01-01",
                enacted="2019-01-01",
                expires="2019-12-31",
                statute_id="2018/523",
            ),
        ),
    ]
    muutos_ir = _sec("3", _sub("1", _content("replacement 1")), _sub("2", _content("replacement 2")))
    op = ResolvedOp.from_amendment_op(
        _op(op_type="REPLACE", target_section="3"),
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        op_source=OperationSource(
            statute_id="2019/1223",
            enacted="2019-12-04",
            effective="2020-01-01",
            expires="",
        ),
    )
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        section_path,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "3 §",
        base_ir=_body(base_section),
        replay_history_ops=replay_history,
        source_pathologies_out=pathologies,
    )

    assert result is not None
    result = _modified(state, result)
    live = result.find_section("3")
    assert live is not None
    assert "replacement 1" in irnode_to_text(live)
    assert pathologies
    assert pathologies[0].code == "TEMPORARY_SECTION_REBASE"
    assert pathologies[0].detail["rebase_context"] == "section_replace"
    assert pathologies[0].detail["rebase_kind"] == "expired_latest_snapshot_current_live_section"
    assert pathologies[0].detail["latest_snapshot_expires"] == "2019-12-31"


def _paragraph_labels(sub: IRNode) -> List[str]:
    return [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label is not None]


# ---------------------------------------------------------------------------
# _resolve_subsection_index
# ---------------------------------------------------------------------------


class TestResolveSubsectionIndex:
    def test_simple_no_intro_list_shape(self):
        subsecs = [_sub("1"), _sub("2"), _sub("3")]
        assert _resolve_subsection_index(subsecs, 1) == 0
        assert _resolve_subsection_index(subsecs, 2) == 1
        assert _resolve_subsection_index(subsecs, 3) == 2

    def test_out_of_range_returns_n_minus_1(self):
        subsecs = [_sub("1")]
        # No intro-list shape — just n-1
        assert _resolve_subsection_index(subsecs, 5) == 4

    def test_intro_list_shape_item_target_para1(self):
        """With intro-list shape, item target para 1 → index 1 (the list subsection)."""
        subsecs = [
            _sub("1", _intro("intro text:")),
            _sub("2", _para("1"), _para("2")),
        ]
        assert _resolve_item_subsection_index(subsecs, 1) == 1

    def test_intro_list_shape_moment_target_para2_resolves_exact_label(self):
        """With intro-list shape, generic moment lookup stays label-based."""
        subsecs = [
            _sub("1", _intro("intro:")),
            _sub("2", _para("1"), _para("2")),
            _sub("3", _content("three")),
        ]
        assert _resolve_subsection_index(subsecs, 2) == 1

    def test_intro_list_shape_accepts_consecutive_numeric_items_beyond_one_two(self):
        subsecs = [
            _sub("1", _intro("intro text:")),
            _sub("2", _para("3"), _para("4"), _para("5")),
            _sub("3", _content("three")),
        ]
        assert _resolve_item_subsection_index(subsecs, 1) == 1
        assert _resolve_subsection_index(subsecs, 2) == 1

    def test_intro_list_shape_does_not_require_trailing_colon(self):
        subsecs = [
            _sub("1", _intro("intro text without colon")),
            _sub("2", _para("1"), _para("2")),
            _sub("3", _content("three")),
        ]
        assert _resolve_item_subsection_index(subsecs, 1) == 1
        assert _resolve_subsection_index(subsecs, 2) == 1

    def test_prefers_exact_surviving_label_over_shifted_position(self):
        subsecs = [
            _sub("2", _content("second")),
            _sub("3", _content("third")),
        ]
        assert _resolve_subsection_index(subsecs, 3) == 1

    def test_label_based_after_insert_creates_letter_suffix_subsection(self):
        """Pattern C regression: after INSERT of '1a', targeting momentti 2 must
        find label '2' at index 2, not use positional index 1 (which is '1a').

        Before the grafter_simple fix, _apply_subsection_replace used
        ``idx = momentti - 1 = 1`` which would target the '1a' subsection
        instead of the '2' subsection.  This test verifies that
        _resolve_subsection_index (and the fixed grafter_simple logic) use
        label-based matching so that momentti 2 always finds label '2'
        regardless of how many inserted subsections precede it.
        """
        # After INSERT of subsection '1a', the positional layout is:
        #   index 0 → label '1'
        #   index 1 → label '1a'   ← positional (momentti-1) would land here
        #   index 2 → label '2'    ← label-based lookup must find this
        #   index 3 → label '3'
        subsecs = [
            _sub("1", _content("first")),
            _sub("1a", _content("first-a inserted")),
            _sub("2", _content("second")),
            _sub("3", _content("third")),
        ]
        assert _resolve_subsection_index(subsecs, 2) == 2, (
            "momentti 2 must resolve to the subsection labeled '2' (index 2), "
            "not the positional index 1 which is '1a'"
        )
        assert _resolve_subsection_index(subsecs, 3) == 3, (
            "momentti 3 must resolve to the subsection labeled '3' (index 3)"
        )

    def test_skips_content_only_continuation_fragment_when_resolving_exact_label(self):
        subsecs = [
            _sub("1", _content("First moment.")),
            _sub(
                "2",
                _intro("List:"),
                _para("1", "item a;"),
                _para("2", "item b and its carried tail"),
            ),
            _sub("3", _content("tuomita kokonaan tai osaksi valtiolle menetetyksi.")),
            _sub("4", _content("Actual third moment.")),
        ]

        assert _resolve_subsection_index(subsecs, 3) == 2

    def test_skips_duplicate_suffix_continuation_fragment_when_resolving_exact_label(self):
        subsecs = [
            _sub("1", _content("First moment.")),
            _sub(
                "2",
                _intro("List:"),
                _para("1", "item a;"),
                _para(
                    "2",
                    "4) hallussapidetty, valmistettu, koottu, luovutettu, maahan tuotu tai maasta viety "
                    "aine, laite, laitteisto tai ydinenergia-alan tietoaineisto tai sen arvo "
                    "tuomita kokonaan tai osaksi valtiolle menetetyksi.",
                ),
            ),
            _sub("3", _content("tuomita kokonaan tai osaksi valtiolle menetetyksi.")),
            _sub("4", _content("Actual third moment.")),
        ]

        assert _resolve_subsection_index(subsecs, 3) == 2

    def test_replace_removes_stale_continuation_fragment_and_preserves_legal_label(self):
        state = _make_state(
            _body(
                _sec(
                    "73",
                    _sub("1", _content("First moment.")),
                    _sub(
                        "2",
                        _intro("List:"),
                        _para("1", "item a;"),
                        _para(
                            "2",
                            "4) hallussapidetty, valmistettu, koottu, luovutettu, maahan tuotu tai maasta viety "
                            "aine, laite, laitteisto tai ydinenergia-alan tietoaineisto tai sen arvo "
                            "tuomita kokonaan tai osaksi valtiolle menetetyksi.",
                        ),
                    ),
                    _sub("3", _content("tuomita kokonaan tai osaksi valtiolle menetetyksi.")),
                    _sub("4", _content("Old real third moment.")),
                )
            )
        )
        amend_sub = _sub("3", _content("Lisäksi on soveltuvin osin noudatettava, mitä rikoslain 10 luvussa säädetään."))
        op = AmendmentOp(
            op_id="test_sparse_tail_fragment_replace",
            op_type="REPLACE",
            target_section="73",
            target_unit_kind="section",
            target_paragraph=3,
            source_statute="2001/880",
            source_issue_date=_DATE,
        )

        pathologies: list[SourcePathology] = []
        result = apply_op(
            state,
            op,
            _ctx(),
            _sec("73", amend_sub),
            amend_sub_ir=amend_sub,
            replay_mode="legal_pit",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        sec = result.find_section("73")
        assert sec is not None
        subsecs = [child for child in sec.children if child.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in subsecs] == ["1", "2", "3"]
        assert "tuomita kokonaan tai osaksi valtiolle menetetyksi." not in irnode_to_text(subsecs[-1])
        assert "Lisäksi on soveltuvin osin noudatettava" in irnode_to_text(subsecs[-1])
        assert [p.code for p in pathologies] == [
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "SUBSECTION_TARGET_REBOUND",
        ]
        assert pathologies[1].detail["rebound_kind"] == "continuation_fragment_skip"

    def test_replace_rejects_stale_continuation_fragment_rebound_in_strict_mode(self):
        state = _make_state(
            _body(
                _sec(
                    "73",
                    _sub("1", _content("First moment.")),
                    _sub(
                        "2",
                        _intro("List:"),
                        _para("1", "item a;"),
                        _para(
                            "2",
                            "4) hallussapidetty, valmistettu, koottu, luovutettu, maahan tuotu tai maasta viety "
                            "aine, laite, laitteisto tai ydinenergia-alan tietoaineisto tai sen arvo "
                            "tuomita kokonaan tai osaksi valtiolle menetetyksi.",
                        ),
                    ),
                    _sub("3", _content("tuomita kokonaan tai osaksi valtiolle menetetyksi.")),
                    _sub("4", _content("Old real third moment.")),
                )
            )
        )
        amend_sub = _sub("3", _content("Lisäksi on soveltuvin osin noudatettava, mitä rikoslain 10 luvussa säädetään."))
        op = AmendmentOp(
            op_id="test_sparse_tail_fragment_replace_strict",
            op_type="REPLACE",
            target_section="73",
            target_unit_kind="section",
            target_paragraph=3,
            source_statute="2001/880",
            source_issue_date=_DATE,
        )

        pathologies: list[SourcePathology] = []
        failed_ops: list[FailedOp] = []
        result = apply_op(
            state,
            op,
            _ctx(),
            _sec("73", amend_sub),
            amend_sub_ir=amend_sub,
            replay_mode="legal_pit",
            failed_ops_out=failed_ops,
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is state
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "continuation_fragment_skip"
        assert [f.reason for f in failed_ops] == ["no deterministic path"]

class TestApplyContainerInsert:
    def test_insert_creates_part_and_records_chapter_move_lineage(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.CHAPTER, label="20", children=(_sec("1", _content("chapter 20")),)),
                        IRNode(kind=IRNodeKind.CHAPTER, label="21", children=(_sec("1", _content("chapter 21")),)),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.CHAPTER, label="25", children=(_sec("1", _content("chapter 25")),)),),
                ),
            )
        )
        op = AmendmentOp(
            op_id="insert_chapter_19a_under_new_part",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="19a",
            source_statute="2019/209",
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="19a",
            attrs={
                "lawvm_amendment_part_hint": "iva",
                "lawvm_amendment_part_sibling_chapters": ("20", "21"),
            },
            children=(
                IRNode(kind=IRNodeKind.NUM, text="19 a luku"),
                _sec("1", _content("new chapter 19a")),
            ),
        )
        migration_ledger = MigrationLedger()

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2019/209] INSERT 19a luku",
            migration_ledger=migration_ledger,
        )

        result = _modified(state, result)
        iva_part = next(child for child in result.ir.children if child.kind == IRNodeKind.PART and child.label == "iva")
        assert [child.label for child in iva_part.children if child.kind == IRNodeKind.CHAPTER] == ["19a", "20", "21"]
        move_events = [event for event in migration_ledger.events if event.kind == "move"]
        assert [(event.from_address.path, event.to_address.path) for event in move_events] == [
            ((("part", "5"), ("chapter", "20")), (("part", "iva"), ("chapter", "20"))),
            ((("part", "5"), ("chapter", "21")), (("part", "iva"), ("chapter", "21"))),
        ]

    def test_insert_consumes_non_base_same_numbered_chapter_scaffold_in_legal_pit(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3a",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                        IRNode(kind=IRNodeKind.HEADING, text="Old temporary chapter"),
                        _sec("38a", IRNode(kind=IRNodeKind.NUM, text="38 a §"), _content("old")),
                    ),
                )
            )
        )
        base_ir = _body()
        op = AmendmentOp(
            op_id="insert_chapter_3a",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="3a",
            source_statute="2003/1310",
            source_issue_date=_DATE,
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3a",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                IRNode(kind=IRNodeKind.HEADING, text="New chapter"),
                _sec("29a", IRNode(kind=IRNodeKind.NUM, text="29 a §"), _content("new")),
            ),
        )

        pathologies: list[SourcePathology] = []
        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2003/1310] INSERT 3a luku",
            base_ir=base_ir,
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        chapters = [child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER]
        assert [(child.kind, child.label) for child in chapters] == [(IRNodeKind.CHAPTER, "3a")]
        chapter = chapters[0]
        section_labels = [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]
        assert section_labels == ["29a"]
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert [p.detail["recovery_kind"] for p in pathologies] == [
            "container_insert_non_base_scaffold_consume",
        ]
        assert pathologies[0].detail["recovery_kind"] == "container_insert_non_base_scaffold_consume"

    def test_insert_chapter_respects_target_part_when_same_label_exists_in_other_part(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                                _sec("1", _content("part 4 chapter 2")),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                _sec("1", _content("part 5 chapter 1")),
                            ),
                        ),
                    ),
                ),
            )
        )
        base_ir = state.ir
        op = AmendmentOp(
            op_id="insert_part5_chapter2",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="2",
            target_part="5",
            source_statute="2018/301",
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                _sec("1", _content("new part 5 chapter 2")),
            ),
        )

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2018/301] INSERT V osan 2 luku",
            base_ir=base_ir,
        )

        result = _modified(state, result)
        part_4 = next(child for child in result.ir.children if child.kind is IRNodeKind.PART and child.label == "4")
        part_5 = next(child for child in result.ir.children if child.kind is IRNodeKind.PART and child.label == "5")
        part_4_chapters = [child.label for child in part_4.children if child.kind is IRNodeKind.CHAPTER]
        part_5_chapters = [child.label for child in part_5.children if child.kind is IRNodeKind.CHAPTER]
        assert part_4_chapters == ["2"]
        assert part_5_chapters == ["1", "2"]
        chapter_2 = next(child for child in part_5.children if child.kind is IRNodeKind.CHAPTER and child.label == "2")
        assert [child.label for child in chapter_2.children if child.kind is IRNodeKind.SECTION] == ["1"]

    def test_insert_chapter_keeps_child_when_shadow_target_exists_only_in_other_part(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                                _sec("1", _content("part 4 chapter 2 section 1")),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),),
                        ),
                    ),
                ),
            )
        )
        op = AmendmentOp(
            op_id="insert_part5_chapter2",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="2",
            target_part="5",
            source_statute="2018/301",
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                _sec("1", _content("new part 5 chapter 2 section 1")),
            ),
        )

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2018/301] INSERT V osan 2 luku",
            standalone_section_targets=frozenset({("4", "2", "1")}),
        )

        result = _modified(state, result)
        part_5 = next(child for child in result.ir.children if child.kind is IRNodeKind.PART and child.label == "5")
        chapter_2 = next(child for child in part_5.children if child.kind is IRNodeKind.CHAPTER and child.label == "2")
        assert [child.label for child in chapter_2.children if child.kind is IRNodeKind.SECTION] == ["1"]

    def test_container_replace_fragmentary_heading_payload_merges_live_sections(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="iia",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
                                _sec("1", _content("pykala 1")),
                                _sec("2", _content("vanha pykala 2")),
                                _sec("3", _content("pykala 3")),
                            ),
                        ),
                    ),
                ),
            )
        )
        op = AmendmentOp(
            op_id="replace_part_iia_chapter_1_heading_fragment",
            op_type="REPLACE",
            target_unit_kind="chapter",
            target_section="1",
            target_part="iia",
            source_statute="2018/984",
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="1",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Uusi otsikko"),
                _sec("2", _content("uusi pykala 2")),
            ),
        )
        pathologies: list[SourcePathology] = []

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2018/984] REPLACE IIa osan 1 luvun otsikko",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        part_iia = next(
            child
            for child in result.ir.children
            if child.kind is IRNodeKind.PART and child.label == "iia"
        )
        chapter_1 = next(
            child
            for child in part_iia.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "1"
        )
        heading = next(
            child for child in chapter_1.children if child.kind is IRNodeKind.HEADING
        )
        section_labels = [
            child.label for child in chapter_1.children if child.kind is IRNodeKind.SECTION
        ]
        section_2 = next(
            child
            for child in chapter_1.children
            if child.kind is IRNodeKind.SECTION and child.label == "2"
        )

        assert heading.text == "Uusi otsikko"
        assert section_labels == ["1", "2", "3"]
        assert "uusi pykala 2" in irnode_to_text(section_2)
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert [p.detail["recovery_kind"] for p in pathologies] == [
            "container_replace_fragmentary_heading_merge"
        ]

    def test_insert_merges_into_existing_base_chapter_emits_pathology(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3a",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                        IRNode(kind=IRNodeKind.HEADING, text="Old temporary chapter"),
                        _sec("38a", IRNode(kind=IRNodeKind.NUM, text="38 a §"), _content("old")),
                    ),
                )
            )
        )
        base_ir = _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3a",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Old temporary chapter"),
                    _sec("38a", IRNode(kind=IRNodeKind.NUM, text="38 a §"), _content("old")),
                ),
            )
        )
        pathologies: list[SourcePathology] = []
        op = AmendmentOp(
            op_id="insert_chapter_3a_merge",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="3a",
            source_statute="2003/1310",
            source_issue_date=_DATE,
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3a",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                IRNode(kind=IRNodeKind.HEADING, text="New chapter"),
                _sec("29a", IRNode(kind=IRNodeKind.NUM, text="29 a §"), _content("new")),
            ),
        )

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2003/1310] INSERT 3a luku",
            base_ir=base_ir,
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        chapters = [child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER]
        assert [(child.kind, child.label) for child in chapters] == [(IRNodeKind.CHAPTER, "3a")]
        chapter = chapters[0]
        section_labels = [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]
        assert section_labels == ["29a", "38a"]
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "container_insert_base_chapter_merge"

    def test_insert_merge_duplicate_labels_preserves_live_chapter_and_emits_skip_pathology(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3a",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                        _sec("29a", IRNode(kind=IRNodeKind.NUM, text="29 a §"), _content("old first")),
                        _sec("29a", IRNode(kind=IRNodeKind.NUM, text="29 a §"), _content("old duplicate")),
                    ),
                )
            )
        )
        base_ir = state.ir
        pathologies: list[SourcePathology] = []
        op = AmendmentOp(
            op_id="insert_chapter_3a_duplicate_merge",
            op_type="INSERT",
            target_unit_kind="chapter",
            target_section="3a",
            source_statute="2003/1310",
            source_issue_date=_DATE,
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="3a",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="3 a luku"),
                _sec("30", IRNode(kind=IRNodeKind.NUM, text="30 §"), _content("new")),
            ),
        )

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2003/1310] INSERT 3a luku",
            base_ir=base_ir,
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        chapter = next(child for child in result.ir.children if child.kind is IRNodeKind.CHAPTER and child.label == "3a")
        section_labels = [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION]

        assert section_labels == ["29a", "29a"]
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK", "DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert [p.detail["recovery_kind"] for p in pathologies] == [
            "container_insert_base_chapter_merge_duplicate_labels",
            "container_insert_base_chapter_merge",
        ]

    def test_fragmentary_replace_duplicate_labels_preserves_live_chapter_and_emits_skip_pathology(self):
        state = _make_state(
            _body(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                        IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
                        _sec("1", IRNode(kind=IRNodeKind.NUM, text="1 §"), _content("first one")),
                        _sec("1", IRNode(kind=IRNodeKind.NUM, text="1 §"), _content("second one")),
                        _sec("2", IRNode(kind=IRNodeKind.NUM, text="2 §"), _content("old two")),
                    ),
                )
            )
        )
        pathologies: list[SourcePathology] = []
        op = AmendmentOp(
            op_id="replace_chapter_1_duplicate_fragment",
            op_type="REPLACE",
            target_unit_kind="chapter",
            target_section="1",
            source_statute="2018/984",
            source_issue_date=_DATE,
        )
        muutos_ir = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="1",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Uusi otsikko"),
                _sec("2", IRNode(kind=IRNodeKind.NUM, text="2 §"), _content("new two")),
            ),
        )

        result = _apply_container_op(
            state,
            op,
            muutos_ir,
            _LEGAL_PIT,
            "[2018/984] REPLACE 1 luku",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        chapter = next(child for child in result.ir.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        heading = next(child for child in chapter.children if child.kind is IRNodeKind.HEADING)
        section_labels = [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION]

        assert heading.text == "Vanha otsikko"
        assert section_labels == ["1", "1", "2"]
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK", "DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert [p.detail["recovery_kind"] for p in pathologies] == [
            "container_replace_fragmentary_heading_merge_duplicate_labels",
            "container_replace_fragmentary_heading_merge",
        ]


def test_replay_1977_53_section_6_keeps_bank_of_finland_tail() -> None:
    state = _make_state(
        _body(
            _sec(
                "6",
                _sub(
                    "1",
                    _content(
                        "Suhdannetalletus on suoritettava tarkoitusta varten avatulle verotoimiston "
                        "postisiirtotilille. Suoritetut suhdannetalletukset on siirrettävä erityiselle "
                        "tilille Suomen Pankkiin siksi ajaksi, kunnes valtio maksaa suhdannetalletuksen "
                        "takaisin talletusvelvolliselle."
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        _content("Suhdannetalletuksen kantaa ja palauttaa lääninverovirasto."),
    )
    op = AmendmentOp(
        op_id="test_1977_53_section_6_preserve_tail",
        op_type="REPLACE",
        target_section="6",
        target_unit_kind="section",
        target_paragraph=1,
        source_statute="1978/618",
        source_issue_date=_DATE,
    )

    result = apply_op(
        state,
        op,
        _ctx(),
        _sec("6", amend_sub),
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
    )

    result = _modified(state, result)
    sec = result.find_section("6")
    assert sec is not None
    text = irnode_to_text(sec)
    assert "Suhdannetalletuksen kantaa ja palauttaa lääninverovirasto." in text
    assert "Suoritetut suhdannetalletukset on siirrettävä erityiselle tilille Suomen Pankkiin" in text


def test_apply_container_whole_chapter_replace_keeps_cross_chapter_same_labeled_section() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Old heading"),
                    _sec("18", _content("section 18")),
                    _sec("19", _content("section 19")),
                ),
            )
        )
    )
    op = AmendmentOp(
        op_id="replace_5_chapter",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="5",
        source_statute="1997/1251",
        source_issue_date=_DATE,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 luku"),
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            _sec("18", _content("section 18")),
            _sec("19", _content("section 19")),
            _sec("23", _content("section 23 must stay in chapter 5")),
        ),
    )

    result = _apply_container_op(
        state,
        op,
        muutos_ir,
        _LEGAL_PIT,
        "[1997/1251] REPLACE 5 luku otsikko",
        standalone_section_targets=frozenset({("6", "23")}),
    )

    result = _modified(state, result)
    chapter = next(child for child in result.ir.children if child.kind == IRNodeKind.CHAPTER and child.label == "5")
    heading = next(child for child in chapter.children if child.kind == IRNodeKind.HEADING)
    section_labels = [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]

    assert heading.text == "New heading"
    assert section_labels == ["18", "19", "23"]


# ---------------------------------------------------------------------------
# Roman numeral osa/part REPEAL normalization
# ---------------------------------------------------------------------------


class TestContainerPartRomanRepeal:
    """Verify that REPEAL of Roman numeral part targets (e.g. 'III osa')
    correctly resolves against master trees that use Arabic labels.

    Bug B context: amendment 1987/411 targets 'III osa' and 'V osa' of
    Rikoslaki but the master tree stores parts as part:3, part:5.
    """

    def _make_state_with_parts(self) -> ReplayState:
        """Build a body with part:1, part:3, part:5 containing chapters."""
        part1 = IRNode(
            kind=IRNodeKind.PART,
            label="1",
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(_sec("1", _content("p1 ch1 s1")),)),),
        )
        part3 = IRNode(
            kind=IRNodeKind.PART,
            label="3",
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        _sec("10", _content("marriage law s10")),
                        _sec("11", _content("marriage law s11")),
                    ),
                ),
            ),
        )
        part5 = IRNode(
            kind=IRNodeKind.PART,
            label="5",
            children=(
                _sec("38", _content("property law s38")),
                _sec("39", _content("property law s39")),
            ),
        )
        return _make_state(_body(part1, part3, part5))

    def _part_op(self, section: str, op_type: OpType = "REPEAL") -> AmendmentOp:
        return AmendmentOp(
            op_id="test_part_op",
            op_type=op_type,
            target_section=section,
            target_unit_kind="part",
            source_statute="1987/411",
            source_issue_date=_DATE,
        )

    def test_repeal_roman_iii_removes_part_3(self):
        """REPEAL III osa → removes part:3 from tree."""
        state = self._make_state_with_parts()
        op = self._part_op("III")
        result = _apply_container_op(state, op, None, _LEGAL_PIT, "[1987/411] REPEAL III osa")
        result = _modified(state, result)
        parts = [c for c in result.ir.children if c.kind == IRNodeKind.PART]
        assert [p.label for p in parts] == ["1", "5"]

    def test_repeal_roman_v_removes_part_5(self):
        """REPEAL V osa → removes part:5 from tree."""
        state = self._make_state_with_parts()
        op = self._part_op("V")
        result = _apply_container_op(state, op, None, _LEGAL_PIT, "[1987/411] REPEAL V osa")
        result = _modified(state, result)
        parts = [c for c in result.ir.children if c.kind == IRNodeKind.PART]
        assert [p.label for p in parts] == ["1", "3"]

    def test_repeal_arabic_3_removes_part_3(self):
        """REPEAL 3 osa → removes part:3 (Arabic label, no conversion needed)."""
        state = self._make_state_with_parts()
        op = self._part_op("3")
        result = _apply_container_op(state, op, None, _LEGAL_PIT, "[test] REPEAL 3 osa")
        result = _modified(state, result)
        parts = [c for c in result.ir.children if c.kind == IRNodeKind.PART]
        assert [p.label for p in parts] == ["1", "5"]

    def test_repeal_nonexistent_part_returns_same_state(self):
        """REPEAL of non-existent part returns unchanged state."""
        state = self._make_state_with_parts()
        op = self._part_op("X")  # part 10 doesn't exist
        result = _apply_container_op(state, op, None, _LEGAL_PIT, "[test] REPEAL X osa")
        assert _unchanged(state, result)


class TestGroupPlanRomanNormalization:
    """Verify that target_group_key normalizes Roman numerals for part targets."""

    def test_roman_iii_normalized_to_3(self):
        from lawvm.finland.group_plan import target_group_key

        op = AmendmentOp(
            op_id="test",
            op_type="REPEAL",
            target_section="III",
            target_unit_kind="part",
            source_statute="1987/411",
            source_issue_date=_DATE,
        )
        kind, norm, chapter, part = target_group_key(op)
        assert kind == IRNodeKind.PART
        assert norm == "3"
        assert chapter is None
        assert part is None

    def test_roman_v_normalized_to_5(self):
        from lawvm.finland.group_plan import target_group_key

        op = AmendmentOp(
            op_id="test",
            op_type="REPEAL",
            target_section="V",
            target_unit_kind="part",
            source_statute="1987/411",
            source_issue_date=_DATE,
        )
        _, norm, _, _ = target_group_key(op)
        assert norm == "5"

    def test_arabic_3_unchanged(self):
        from lawvm.finland.group_plan import target_group_key

        op = AmendmentOp(
            op_id="test",
            op_type="REPLACE",
            target_section="3",
            target_unit_kind="part",
            source_statute="1987/411",
            source_issue_date=_DATE,
        )
        _, norm, _, _ = target_group_key(op)
        assert norm == "3"

    def test_chapter_kind_not_affected(self):
        """Chapter (L) targets should NOT get Roman conversion."""
        from lawvm.finland.group_plan import target_group_key

        op = AmendmentOp(
            op_id="test",
            op_type="REPLACE",
            target_section="III",
            target_unit_kind="chapter",
            source_statute="2020/1",
            source_issue_date=_DATE,
        )
        kind, norm, _, _ = target_group_key(op)
        assert kind == IRNodeKind.CHAPTER
        # Chapters don't get Roman conversion — 'iii' is the raw lowercased label
        assert norm == "iii"

    def test_chapter_group_key_keeps_part_scope(self):
        """Chapter targets must preserve explicit part scope in grouping."""
        from lawvm.finland.group_plan import target_group_key

        op = AmendmentOp(
            op_id="test",
            op_type="INSERT",
            target_section="2",
            target_part="V",
            target_unit_kind="chapter",
            source_statute="2018/301",
            source_issue_date=_DATE,
        )
        kind, norm, chapter, part = target_group_key(op)
        assert kind == IRNodeKind.CHAPTER
        assert norm == "2"
        assert chapter is None
        assert part == "5"


# ---------------------------------------------------------------------------
# _apply_subsection_repeal
# ---------------------------------------------------------------------------


class TestApplySubsectionRepeal:
    def _make_sec_and_path(self):
        sec = _sec("1", _sub("1", _content("first")), _sub("2", _content("second")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        return state, sec_path, sec

    def test_repeal_synthesizes_placeholder(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "1 § 1 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        assert new_sub.attrs.get("lawvm_repeal_placeholder") == "1"

    def test_repeal_removes_without_placeholder(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_repeal(state, op, sec_path, sec, subsecs, _LEGAL_PIT, "1 § 1 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        assert len([c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]) == 1

    def test_repeal_out_of_range_returns_state(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=99)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "1 § 99 mom")
        assert _unchanged(state, result)

    def test_not_applicable_for_item_op(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=1, target_item="2")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "1 § 1 mom 2 k")
        assert result is None


# ---------------------------------------------------------------------------
# _apply_subsection_replace
# ---------------------------------------------------------------------------


class TestApplySubsectionReplace:
    def _make_sec_and_path(self):
        sec = _sec("1", _sub("1", _content("original")), _sub("2", _content("second")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        return state, sec_path, sec

    def test_replace_subsection(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement text"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_replace(
            state, op, sec_path, sec, subsecs, amend_sub, None, _FINLEX_ORACLE, "1 § 1 mom"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub1 = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION][0]
        text = " ".join(c.text or "" for c in sub1.children)
        assert "replacement text" in text

    def test_replace_subsection_does_not_collapse_intro_list_payload_without_live_intro_list_shape(self):
        """Intro-list collapse is only valid when the live section already has that shape.

        Otherwise a target_paragraph=1 replace must use the amendment subsection
        itself, not the collapsed intro-list view of the whole amendment statute.
        """
        state = _make_state(
            _body(
                _sec(
                    "1",
                    _sub("1", _content("old opening")),
                    _sub("2", _content("old middle")),
                    _sub("3", _content("old tail")),
                )
            )
        )
        sec_path = [("section", "1")]
        sec = state.ir.children[0]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_sub = _sub("1", _content("replacement text"))
        muutos_ir = _sec(
            "1",
            _sub("1", _intro("New opening:")),
            _sub("2", _para("1", "first item"), _para("2", "second item")),
        )
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)

        result = _apply_subsection_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            _FINLEX_ORACLE,
            "1 § 1 mom",
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]

        assert new_subs[0].children[0].text == "replacement text"
        assert all("first item" not in irnode_to_text(ch) for ch in new_subs)
        assert all("second item" not in irnode_to_text(ch) for ch in new_subs)
        assert new_subs[1].children[0].text == "old middle"
        assert new_subs[2].children[0].text == "old tail"

    def test_replace_subsection_explicitly_rebounds_intro_list_shape(self) -> None:
        state = _make_state(
            _body(
                _sec(
                    "1",
                    _sub("1", _intro("intro text:")),
                    _sub("2", _para("1", "first item"), _para("2", "second item")),
                    _sub("3", _content("old tail")),
                )
            )
        )
        sec_path = [("section", "1")]
        sec = state.ir.children[0]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        amend_sub = _sub("2", _content("replacement text"))
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            None,
            _FINLEX_ORACLE,
            "1 § 2 mom",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in new_subs] == ["1", "2", "3"]
        assert irnode_to_text(new_subs[2]) == "replacement text"
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK", "SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["recovery_kind"] == "subsection_replace_omission_merge_fallback"
        assert pathologies[1].detail["rebound_kind"] == "intro_list_moment_shape"

    def test_replace_bracketed_subsection_rewrite_emits_pathology(self) -> None:
        state = _make_state(
            _body(
                _sec(
                    "10",
                    _sub("1", _content("first live subsection")),
                    _sub("2", _content("shared prefix replacement old wording")),
                    _sub("3", _content("third live subsection")),
                    _sub("4", _content("fourth live subsection")),
                )
            )
        )
        sec_path = [("section", "10")]
        sec = state.ir.children[0]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        amend_sub = _sub("3", _content("shared prefix replacement new wording"))
        muutos_ir = _sec(
            "10",
            IRNode(kind=IRNodeKind.OMISSION),
            _sub("3", _content("payload subsection")),
            IRNode(kind=IRNodeKind.OMISSION),
        )
        op = _op(op_type="REPLACE", target_section="10", target_paragraph=3)
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            _FINLEX_ORACLE,
            "10 § 3 mom",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        rewritten_subs = [child for child in new_sec.children if child.kind is IRNodeKind.SUBSECTION]
        assert [child.label for child in rewritten_subs] == ["1", "2", "3", "4"]
        assert irnode_to_text(rewritten_subs[1]) == "third live subsection"
        assert irnode_to_text(rewritten_subs[2]) == "shared prefix replacement new wording"
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "omission_bracketed_single_subsection_rewrite"

    def test_not_applicable_for_item_op(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1, target_item="2")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_replace(
            state, op, sec_path, sec, subsecs, None, None, _FINLEX_ORACLE, "1 § 1 mom 2 k"
        )
        assert result is None


def test_apply_op_section_repeal_removes_non_base_insert_even_in_finlex_oracle() -> None:
    state = _make_state(_body(_sec("2a", _sub("1", _content("inserted later")))))
    op = _op(op_type="REPEAL", target_section="2a")
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
    )

    replaced = result.find_section("2a")
    assert replaced is not None
    assert replaced.attrs.get("lawvm_repeal_placeholder") == "1"
    assert len(mutation_events) == 1
    assert mutation_events[0].helper == "_apply_whole_section_op"
    assert mutation_events[0].outcome == "applied"
    assert mutation_events[0].action == "repeal"


def test_apply_whole_section_replace_moves_unique_same_label_section_into_target_chapter() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    _sec("33", _content("old chapter six text")),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="33", target_chapter="5")
    muutos_ir = _sec("33", _content("new chapter five text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "5 luku 33 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    moved = result.find_section("33", "5")
    assert moved is not None
    moved_text = " ".join(child.text or "" for child in moved.children)
    assert "new chapter five text" in moved_text
    assert result.find_section("33", "6") is None
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_move_replace_destination_rebind"


def test_apply_whole_section_replace_moves_unique_root_section_into_target_chapter_emits_pathology() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter three"),
                ),
            ),
            _sec("22", _content("root chapterless 22")),
        )
    )
    op = _op(op_type="REPLACE", target_section="22", target_chapter="3")
    muutos_ir = _sec("22", _content("new chapter three text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "3 luku 22 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    moved = result.find_section("22", "3")
    assert moved is not None
    moved_text = " ".join(child.text or "" for child in moved.children)
    assert "new chapter three text" in moved_text
    assert result.find_section("22") is not None
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_move_replace_destination_rebind"


def test_apply_whole_section_replace_materializes_inside_existing_chapter_for_missing_section() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    _sec("34", _content("existing 34")),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="33", target_chapter="5")
    ctx = _ctx(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("33", _content("new chapter five text")),
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    assert result is not state
    assert result.find_section("33", "5") is not None
    assert not any(child.kind == IRNodeKind.SECTION and child.label == "33" for child in result.ir.children)
    assert len(mutation_events) == 1
    assert mutation_events[0].helper in {"_apply_materialization", "_apply_whole_section_op"}
    assert mutation_events[0].outcome == "applied"


def test_apply_whole_section_replace_bootstrap_respects_target_part_scope() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="iia",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                            _sec("3a", _content("part iia chapter 2 text")),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                            _sec("1", _content("part 4 chapter 2 scaffold")),
                        ),
                    ),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="3a", target_chapter="2", target_part="4")
    muutos_ir = _sec("3a", _content("part 4 chapter 2 replacement"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "IV osa 2 luku 3a §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    target = result.find_section("3a", "2", "4")
    untouched = result.find_section("3a", "2", "iia")
    assert target is not None
    assert untouched is not None
    assert "part 4 chapter 2 replacement" in irnode_to_text(target)
    assert "part iia chapter 2 text" in irnode_to_text(untouched)
    assert len(pathologies) == 1
    assert pathologies[0].detail["recovery_kind"] == "section_replace_bootstrap_gap_establish"


def test_apply_whole_section_replace_records_missing_bootstrap_parent() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="iia",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                            _sec("3a", _content("part iia chapter 2 text")),
                        ),
                    ),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="3a", target_chapter="2", target_part="4")
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        _sec("3a", _content("part 4 chapter 2 replacement")),
        None,
        _LEGAL_PIT,
        "IV osa 2 luku 3a §",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert len(pathologies) == 1
    assert pathologies[0].code == "SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING"
    assert pathologies[0].source_statute == "2020/1"
    assert pathologies[0].detail["target_part"] == "4"
    assert pathologies[0].detail["target_chapter"] == "2"
    assert pathologies[0].detail["target_section"] == "3a"
    assert pathologies[0].detail["recovery_kind"] == "section_replace_bootstrap_parent_missing"
    assert pathologies[0].detail["strict_disposition"] == "block"


def test_apply_whole_section_replace_does_not_synthesize_root_insert_for_missing_root_section() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    _sec("1", _content("existing 1")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                    _sec("2", _content("existing 2")),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="14")
    ctx = _ctx(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("14", _content("new top-level text")),
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    assert result is state
    assert result.find_section("14") is None
    assert len(mutation_events) == 1
    assert mutation_events[0].helper == "apply_op"
    assert mutation_events[0].outcome == "failed"


def test_apply_whole_section_insert_moves_unique_same_label_placeholder_into_target_chapter() -> None:
    # A repeal-placeholder §33 in chapter:6 should be moved to chapter:5 when
    # an INSERT targets chapter:5 with new §33 content.
    placeholder_33 = IRNode(
        kind=IRNodeKind.SECTION,
        label="33",
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    placeholder_33,
                ),
            ),
        )
    )
    op = _op(op_type="INSERT", target_section="33", target_chapter="5")
    muutos_ir = _sec("33", _content("new chapter five text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "5 luku 33 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    moved = result.find_section("33", "5")
    assert moved is not None
    moved_text = " ".join(child.text or "" for child in moved.children)
    assert "new chapter five text" in moved_text
    assert result.find_section("33", "6") is None
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_move_insert_destination_rebind"


def test_apply_whole_section_insert_moves_unique_root_section_into_target_chapter() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter three"),
                ),
            ),
            _sec("22", _content("root chapterless 22")),
        )
    )
    op = _op(op_type="INSERT", target_section="22", target_chapter="3")
    muutos_ir = _sec("22", IRNode(kind=IRNodeKind.NUM, text="22 §"), _content("new chapter three text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "3 luku 22 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)

    def _section_paths(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> list[tuple[tuple[str, str], ...]]:
        found: list[tuple[tuple[str, str], ...]] = []
        if node.kind == IRNodeKind.SECTION and node.label == "22":
            found.append(path)
        for child in node.children:
            found.extend(_section_paths(child, path + ((child.kind.value, child.label or ""),)))
        return found

    assert _section_paths(result.ir) == [(("chapter", "3"), ("section", "22"))]
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_move_insert_destination_rebind"


def test_apply_whole_section_insert_does_not_rebind_unique_same_label_across_parts() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="iia",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                            _sec("2a", _content("part iia chapter 1 text")),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="6",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                            _sec("1", _content("part 6 chapter 1 scaffold")),
                        ),
                    ),
                ),
            ),
        )
    )
    op = _op(op_type="INSERT", target_section="2a", target_chapter="1", target_part="6")
    muutos_ir = _sec("2a", _content("part 6 chapter 1 insert"))

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "VI osa 1 luku 2a §",
        source_pathologies_out=[],
    )

    result = _modified(state, result)
    target = result.find_section("2a", "1", "6")
    untouched = result.find_section("2a", "1", "iia")
    assert target is not None
    assert untouched is not None
    assert "part 6 chapter 1 insert" in irnode_to_text(target)
    assert "part iia chapter 1 text" in irnode_to_text(untouched)


def test_apply_whole_section_insert_does_not_move_live_unique_section_into_target_chapter() -> None:
    # A live (non-placeholder) §33 in chapter:6 must NOT be displaced when a
    # different §33 is inserted into chapter:5.  Both sections should coexist.
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    _sec("33", _content("live chapter six text")),
                ),
            ),
        )
    )
    op = _op(op_type="INSERT", target_section="33", target_chapter="5")
    muutos_ir = _sec("33", _content("new chapter five text"))

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "5 luku 33 §",
    )

    result = _modified(state, result)
    # New §33 landed in chapter:5
    inserted = result.find_section("33", "5")
    assert inserted is not None
    assert "new chapter five text" in " ".join(child.text or "" for child in inserted.children)
    # Original live §33 in chapter:6 is untouched
    live = result.find_section("33", "6")
    assert live is not None
    assert "live chapter six text" in " ".join(child.text or "" for child in live.children)


def test_apply_whole_section_insert_moves_unique_parent_section_into_letter_suffix_chapter_emits_pathology() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                    _sec("55", _content("parent chapter text")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7c",
                children=(IRNode(kind=IRNodeKind.NUM, text="7c luku"),),
            ),
        )
    )
    op = _op(op_type="INSERT", target_section="55", target_chapter="7c")
    muutos_ir = _sec("55", _content("new subchapter text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "7c luku 55 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    moved = result.find_section("55", "7c")
    assert moved is not None
    assert "new subchapter text" in " ".join(child.text or "" for child in moved.children)
    assert result.find_section("55", "7") is None
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_move_insert_destination_rebind"


def test_apply_whole_section_insert_into_existing_chapter_emits_pathology() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            )
        )
    )
    op = _op(op_type="INSERT", target_section="33", target_chapter="5")
    muutos_ir = _sec("33", _content("new chapter five text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "5 luku 33 §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    inserted = result.find_section("33", "5")
    assert inserted is not None
    assert "new chapter five text" in " ".join(child.text or "" for child in inserted.children)
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_insert_chapter_merge_absorb"


def test_apply_whole_section_insert_consumes_non_base_root_scaffold_emits_pathology() -> None:
    base_state = _make_state(_body())
    live_state = _make_state(_body(_sec("14", _content("scaffold 14 live text"))))
    op = _op(op_type="INSERT", target_section="14")
    muutos_ir = _sec("14", _content("new scaffold text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        live_state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "14 §",
        base_ir=base_state.ir,
        source_pathologies_out=pathologies,
    )

    result = _modified(live_state, result)
    inserted = result.find_section("14")
    assert inserted is not None
    assert "new scaffold text" in irnode_to_text(inserted)
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "section_insert_non_base_scaffold_consume"


def test_apply_whole_section_insert_into_new_letter_suffix_chapter_absorbs_trailing_wrapper_sections() -> None:
    wrapper = IRNode(
        kind=IRNodeKind.HCONTAINER,
        attrs={"name": "statuteProvisionsWrapper"},
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="8", children=(IRNode(kind=IRNodeKind.NUM, text="8 luku"),)),
            IRNode(kind=IRNodeKind.CHAPTER, label="8a", children=(IRNode(kind=IRNodeKind.NUM, text="8 a luku"),)),
            _sec("72a", _content("existing 72a")),
            _sec("72b", _content("existing 72b")),
            _sec("72c", _content("existing 72c")),
            _sec("99", _content("existing 99")),
            IRNode(kind=IRNodeKind.CHAPTER, label="9", children=(IRNode(kind=IRNodeKind.NUM, text="9 luku"),)),
        ),
    )
    state = _make_state(_body(wrapper))
    op = _op(op_type="INSERT", target_section="72d", target_chapter="8a")
    muutos_ir = _sec("72d", _content("new 72d text"))
    pathologies: list[SourcePathology] = []

    result = _apply_whole_section_op(
        state,
        op,
        None,
        muutos_ir,
        None,
        _LEGAL_PIT,
        "8 a luku 72 d §",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    wrapper_after = next(child for child in result.ir.children if child.kind is IRNodeKind.HCONTAINER)
    chapter_8a = next(child for child in wrapper_after.children if child.kind is IRNodeKind.CHAPTER and child.label == "8a")
    chapter_labels = [child.label for child in chapter_8a.children if child.kind is IRNodeKind.SECTION]
    assert chapter_labels == ["72a", "72b", "72c", "72d", "99"]

    loose_labels = [child.label for child in wrapper_after.children if child.kind is IRNodeKind.SECTION]
    assert loose_labels == []

    assert any(
        pathology.detail.get("recovery_kind") == "section_insert_chapter_merge_absorb_trailing_siblings"
        for pathology in pathologies
    )


def test_apply_whole_section_insert_omission_merge_failure_blocks_raw_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _make_state(_body(_sec("33", _sub("1", _content("live first moment")))))
    op = _op(op_type="INSERT", target_section="33")
    muutos_ir = _sec(
        "33",
        _sub("1", _content("replacement first moment")),
        IRNode(kind=IRNodeKind.OMISSION),
    )
    pathologies: list[Any] = []

    monkeypatch.setattr(
        "lawvm.finland.apply_structure_ops._merge_section_with_omission_ir",
        lambda _master, _amend: None,
    )

    result = _apply_whole_section_op(
        state,
        op,
        (("section", "33"),),
        muutos_ir,
        None,
        _FINLEX_ORACLE,
        "33 §",
        source_pathologies_out=pathologies,
    )

    assert result is state
    live = state.find_section("33")
    assert live is not None
    assert "live first moment" in irnode_to_text(live)
    assert pathologies
    assert pathologies[0].code == "PARTIAL_WHOLE_SECTION_PAYLOAD"
    assert pathologies[0].detail["diagnostic_reason"] == "section_insert_omission_merge_failed"


def test_apply_whole_section_insert_omission_merge_emits_pathology() -> None:
    state = _make_state(_body(_sec("33", _sub("1", _content("live first moment")))))
    op = _op(op_type="INSERT", target_section="33")
    muutos_ir = _sec(
        "33",
        _sub("1", _content("replacement first moment")),
        IRNode(kind=IRNodeKind.OMISSION),
    )
    pathologies: list[Any] = []

    result = _apply_whole_section_op(
        state,
        op,
        (("section", "33"),),
        muutos_ir,
        None,
        _FINLEX_ORACLE,
        "33 §",
        source_pathologies_out=pathologies,
    )

    assert result is not state
    live = result.find_section("33")
    assert live is not None
    assert "replacement first moment" in irnode_to_text(live)
    assert pathologies
    assert pathologies[0].code == "PARTIAL_WHOLE_SECTION_PAYLOAD"
    assert pathologies[0].detail["diagnostic_reason"] == "section_insert_omission_merge_applied"


def test_apply_whole_section_replace_preserves_unstated_live_subsection_tail() -> None:
    state = _make_state(
        _body(
            _sec(
                "20",
                IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
                _sub("", _content("old first moment")),
                _sub("", _content("old second moment")),
                _sub("", _content("old third moment")),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="20")
    muutos_ir = _sec(
        "20",
        IRNode(kind=IRNodeKind.HEADING, text="Uusi otsikko"),
        _sub("", _content("new first moment")),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="20",
        target_chapter=None,
        payload_completeness=PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=("same_group_descendant_scoped_single_subsection_shell",),
            tail_policy="preserve_unstated_tail",
        ),
    )

    result = _apply_whole_section_op(
        state,
        rop,
        (("section", "20"),),
        muutos_ir,
        None,
        _FINLEX_ORACLE,
        "20 §",
    )

    result = _modified(state, result)
    live = result.find_section("20")
    assert live is not None
    subsections = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    assert len(subsections) == 3
    assert "new first moment" in irnode_to_text(subsections[0])
    assert "old second moment" in irnode_to_text(subsections[1])
    assert "old third moment" in irnode_to_text(subsections[2])


def test_apply_whole_section_insert_same_label_replace_stamps_exact_tail_policy() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="12",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="12 luku"),
                    _sec(
                        "163",
                        IRNode(kind=IRNodeKind.NUM, text="163 §"),
                        _sub("1", _content("old subsection 1")),
                        _sub("2", _content("old subsection 2")),
                        _sub("3", _content("stale subsection 3")),
                        _sub("4", _content("stale subsection 4")),
                    ),
                ),
            )
        )
    )
    payload = _sec(
        "163",
        IRNode(kind=IRNodeKind.NUM, text="163 §"),
        _sub("1", _content("new subsection 1")),
        _sub("2", _content("new subsection 2")),
    )
    op = _op(op_type="INSERT", target_section="163", target_chapter="12")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="163",
        target_chapter="12",
        payload_completeness=PayloadCompletenessWitness(
            kind="complete",
            reasons=("whole_section_payload",),
            tail_policy="replace_if_target_scope_requires",
        ),
    )

    result = _apply_whole_section_op(
        state,
        rop,
        (("chapter", "12"), ("section", "163")),
        payload,
        None,
        _FINLEX_ORACLE,
        "[test] INSERT 12 luku 163 §",
    )

    result = _modified(state, result)
    section = result.find_section("163", "12")
    assert section is not None
    assert section.attrs["lawvm_tail_policy"] == "replace_if_target_scope_requires"
    assert [child.label for child in section.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2"]


def test_apply_whole_section_replace_relabels_fragmentary_subsections_from_slot_targets() -> None:
    state = _make_state(
        _body(
            _sec(
                "9",
                IRNode(kind=IRNodeKind.HEADING, text="Valvonta-asioiden rekisteri"),
                _sub("1", _content("live first moment")),
                _sub("2", _content("live second moment")),
                _sub("3", _intro("live third intro"), _para("1", "live third item 1"), _para("2", "live third item 2")),
            )
        )
    )
    payload = _sec(
        "9",
        IRNode(kind=IRNodeKind.HEADING, text="Valvonta-asioiden rekisteri"),
        _sub("1", _content("new first moment")),
        _sub("2", _intro("new third intro"), _para("2", "new third item 2")),
    )
    whole = _op(op_type="REPLACE", target_section="9")
    replace1 = _op(op_type="REPLACE", target_section="9", target_paragraph=1)
    replace3 = _op(op_type="REPLACE", target_section="9", target_paragraph=3)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=replace1.description(),
                op_type=str(replace1.op_type or ""),
                target_paragraph=1,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="1",
            ),
            SparsePayloadSlotBinding(
                op_description=replace3.description(),
                op_type=str(replace3.op_type or ""),
                target_paragraph=3,
                target_item=None,
                target_special=None,
                payload_slot_index=2,
                payload_slot_label="2",
            ),
        ),
        used_subs=(0, 1),
        unassigned_payload_slots=(),
    )
    rop = ResolvedOp.from_amendment_op(
        whole,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="9",
        target_chapter=None,
        slot_assignment=assignment,
        payload_completeness=PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=("same_group_descendant_scoped_multi_subsection_shell",),
            tail_policy="preserve_unstated_tail",
        ),
    )

    result = _apply_whole_section_op(
        state,
        rop,
        (("section", "9"),),
        payload,
        None,
        _FINLEX_ORACLE,
        "9 §",
    )

    result = _modified(state, result)
    live = result.find_section("9")
    assert live is not None
    subsections = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    assert "new first moment" in irnode_to_text(subsections[0])
    assert "live second moment" in irnode_to_text(subsections[1])
    assert "new third intro" in irnode_to_text(subsections[2])
    assert "new third item 2" in irnode_to_text(subsections[2])


def test_apply_whole_section_replace_preserves_unstated_live_subsections_by_label_order() -> None:
    state = _make_state(
        _body(
            _sec(
                "20",
                IRNode(kind=IRNodeKind.HEADING, text="Otsikko"),
                _sub("1", _content("live first moment")),
                _sub("2", _content("live second moment")),
                _sub("3", _content("live third moment")),
            )
        )
    )
    payload = _sec(
        "20",
        IRNode(kind=IRNodeKind.HEADING, text="Otsikko"),
        _sub("1", _content("new first moment")),
        _sub("3", _content("new third moment")),
    )
    op = _op(op_type="REPLACE", target_section="20")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="20",
        target_chapter=None,
        payload_completeness=PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=("same_group_descendant_scoped_multi_subsection_shell",),
            tail_policy="preserve_unstated_tail",
        ),
    )

    result = _apply_whole_section_op(
        state,
        rop,
        (("section", "20"),),
        payload,
        None,
        _FINLEX_ORACLE,
        "20 §",
    )

    result = _modified(state, result)
    live = result.find_section("20")
    assert live is not None
    subsections = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    assert "new first moment" in irnode_to_text(subsections[0])
    assert "live second moment" in irnode_to_text(subsections[1])
    assert "new third moment" in irnode_to_text(subsections[2])


def test_apply_op_skips_unique_global_chapter_fallback_for_move_clause_target() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                    _sec("32", _content("existing 32")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    _sec("33", _content("old chapter six text")),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="33", target_chapter="5", move_clause_target_unit_kind="chapter")
    ctx = _ctx(state.ir)

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("33", _content("new chapter five text")),
        replay_mode="legal_pit",
    )

    moved = result.find_section("33", "5")
    assert moved is not None
    moved_text = " ".join(child.text or "" for child in moved.children)
    assert "new chapter five text" in moved_text
    assert result.find_section("33", "6") is None


def test_apply_legacy_dispatch_does_not_reinterpret_section_suffix_target_as_item() -> None:
    state = _make_state(
        _body(
            _sec(
                "33",
                _sub(
                    "1",
                    _para("a", "first item"),
                    _para("b", "second item"),
                ),
            )
        )
    )
    op = _op(op_type="REPEAL", target_section="33a")
    ctx = _ctx(state.ir)

    result = _apply_legacy_dispatch(
        state,
        op,
        op.description(),
        ctx,
        muutos_ir=None,
        replay_mode="legal_pit",
        rop=ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="33a",
            target_chapter=None,
        ),
    )

    assert result is not None
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "33")
    sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    assert [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH] == ["a", "b"]


def test_resolve_section_path_with_fallbacks_does_not_rewrite_section_suffix_target_on_legacy_path() -> None:
    state = _make_state(
        _body(
            _sec(
                "33",
                _sub(
                    "1",
                    _para("a", "first item"),
                    _para("b", "second item"),
                ),
            )
        )
    )
    op = _op(op_type="REPEAL", target_section="33a")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="33a",
        target_chapter=None,
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        None,
        None,
        "[2020/1] REPEAL 33a §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None
    assert op.target_section == "33a"
    assert op.target_paragraph is None
    assert op.target_item is None


def test_resolve_section_path_with_fallbacks_typed_path_does_not_reinterpret_section_suffix_target() -> None:
    state = _make_state(
        _body(
            _sec(
                "33",
                _sub(
                    "1",
                    _para("a", "first item"),
                    _para("b", "second item"),
                ),
            )
        )
    )
    op = _op(op_type="REPEAL", target_section="33a")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="33a",
        target_chapter=None,
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        None,
        None,
        "[2020/1] REPEAL 33a §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None
    assert op.target_section == "33a"
    assert op.target_paragraph is None
    assert op.target_item is None


def test_resolve_section_path_with_fallbacks_does_not_reinterpret_real_letter_section_payload() -> None:
    state = _make_state(_body(_sec("33", _sub("1", _para("a", "first item")))))
    op = _op(op_type="INSERT", target_section="33a")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="33a",
        target_chapter=None,
    )
    muutos_ir = _sec("33a", _content("true lettered section payload"))

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        muutos_ir,
        None,
        "[2020/1] INSERT 33a §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None
    assert op.target_section == "33a"
    assert op.target_paragraph is None
    assert op.target_item is None


def test_resolve_section_path_with_fallbacks_rejects_unique_global_section_in_wrong_chapter() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    _sec("23", _sub("1", _content("old chapter 5 content"))),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new chapter 6 content")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[1997/1251] REPLACE 6 luku 23 §",
    )

    assert resolution.path is None, "Wrong-chapter global fallback should defer to move+replace handling."
    assert resolution.reason_code is None


def test_resolve_section_path_with_fallbacks_rejects_root_level_unique_global_fallback_for_carry_forward_scope() -> None:
    state = _make_state(_body(_sec("23", _sub("1", _content("root-level section")))))
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[1997/1251] REPLACE 6 luku 23 §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None


def test_resolve_section_path_with_fallbacks_prefers_unique_substantive_over_repeal_placeholder_for_unscoped_whole_section() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(
                            _sub("1", _content("repealed old section")),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2a",
                children=(
                    _sec("8", _sub("1", _content("live substantive section"))),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="8")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("8", _content("replacement section payload")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="8",
        target_chapter=None,
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[2018/1313] REPLACE 8 §",
    )

    assert resolution.path == (("chapter", "2a"), ("section", "8"))
    assert resolution.reason_code == "live_unique_substantive_over_placeholder"


def test_resolve_section_path_with_fallbacks_follows_same_wave_section_migration_for_descendant_replace() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="12",
                children=(
                    _sec("125", _sub("1", _content("migrated target")), _sub("3", _content("migrated third"))),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="6", target_chapter="12", target_paragraph=1)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sub("1", _content("replacement payload")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="6",
        target_chapter="12",
    )
    ledger = MigrationLedger()
    ledger.record_renumber(
        LegalAddress(path=(("chapter", "12"), ("section", "6"))),
        LegalAddress(path=(("chapter", "12"), ("section", "125"))),
        effective="2019-04-01",
        source_statute="2019/371",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[2019/371] REPLACE 12 luku 6 § 1 mom",
        migration_ledger=ledger,
    )

    assert resolution.path == (("chapter", "12"), ("section", "125"))
    assert resolution.reason_code == "follow_same_wave_migration"


def test_resolve_section_path_with_fallbacks_does_not_pick_one_when_multiple_substantive_same_label_sections_exist() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(
                            _sub("1", _content("repealed old section")),
                        ),
                    ),
                    _sec("9", _sub("1", _content("other"))),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2a",
                children=(
                    _sec("8", _sub("1", _content("first substantive"))),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2b",
                children=(
                    _sec("8", _sub("1", _content("second substantive"))),
                ),
            ),
        )
    )
    op = _op(op_type="REPLACE", target_section="8")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("8", _content("replacement section payload")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="8",
        target_chapter=None,
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[2018/1313] REPLACE 8 §",
    )

    assert resolution.path == (("chapter", "2"), ("section", "8"))
    assert resolution.reason_code is None


def test_resolve_section_path_with_fallbacks_rejects_unique_global_section_in_wrong_part() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="5",
                        children=(
                            _sec("23", _sub("1", _content("old part I chapter 5 content"))),
                        ),
                    ),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="5")
    op.target_part = "II"
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new part II chapter 5 content")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="5",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[1997/1251] REPLACE II osa 5 luku 23 §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None


def test_resolved_op_exposes_unified_scope_confidence() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )

    assert rop.resolved_scope_confidence is not None
    assert rop.resolved_scope_confidence.tag == "chapter_scope_carry_forward"
    assert rop.resolved_scope_confidence.confidence == "inferred"
    assert rop.resolved_scope_confidence.source == "carry_forward"
    assert rop.resolved_scope_confidence.resolved_chapter == "6"


def test_resolved_op_exposes_grouped_part_scope_confidence() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.target_part = "III"
    op.scope_provenance_tags = ("grouped_part_scope",)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )

    assert rop.resolved_scope_confidence is not None
    assert rop.resolved_scope_confidence.tag == "grouped_part_scope"
    assert rop.resolved_scope_confidence.confidence == "inferred"
    assert rop.resolved_scope_confidence.source == "grouped_part"
    assert rop.resolved_scope_confidence.resolved_chapter == "6"


def test_amendment_op_resolved_scope_confidence_prefers_stored_carrier_over_tags() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="7")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="3",
    )

    witness = op.resolved_scope_confidence

    assert witness is not None
    assert witness.tag == "chapter_scope_from_explicit_chunk"
    assert witness.source == "explicit_chunk"
    assert witness.confidence == "explicit"
    assert witness.resolved_chapter == "7"


def test_resolved_op_resolved_scope_confidence_prefers_stored_carrier_over_tags() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="3",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="8",
    )

    witness = rop.resolved_scope_confidence

    assert witness is not None
    assert witness.tag == "chapter_scope_from_explicit_chunk"
    assert witness.source == "explicit_chunk"
    assert witness.confidence == "explicit"
    assert witness.resolved_chapter == "8"


def test_resolved_op_stores_projection_scope_confidence_over_runtime_tag_rail() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="3",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="8",
    )

    assert rop.scope_confidence is not None
    assert rop.scope_confidence.tag == "chapter_scope_from_explicit_chunk"
    assert rop.scope_confidence.source == "explicit_chunk"
    assert rop.scope_confidence.confidence == "explicit"
    assert rop.scope_confidence.resolved_chapter == "8"


def test_runtime_scope_confidence_for_op_prefers_stored_carrier_for_both_shells() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="7")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="3",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="8",
    )

    op_witness = runtime_scope_confidence_for_op(op)
    rop_witness = runtime_scope_confidence_for_op(rop)

    assert op_witness is not None
    assert op_witness.tag == "chapter_scope_from_explicit_chunk"
    assert op_witness.source == "explicit_chunk"
    assert op_witness.confidence == "explicit"
    assert op_witness.resolved_chapter == "7"

    assert rop_witness is not None
    assert rop_witness.tag == "chapter_scope_from_explicit_chunk"
    assert rop_witness.source == "explicit_chunk"
    assert rop_witness.confidence == "explicit"
    assert rop_witness.resolved_chapter == "8"


def test_scope_authority_parity_for_op_reports_runtime_projection_disagreement() -> None:
    op = _op(op_type="REPLACE", target_section="23", target_chapter="7")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter="7",
    )

    parity = scope_authority_parity_for_op(op)

    assert parity.matches
    assert parity.mismatch_kind is None
    assert parity.runtime is not None
    assert parity.runtime.tag == "chapter_scope_from_explicit_chunk"
    assert parity.projection is not None
    assert parity.projection.tag == "chapter_scope_from_explicit_chunk"


def test_resolve_section_path_with_fallbacks_rejects_unique_global_section_in_wrong_part_for_grouped_part_scope() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="5",
                        children=(
                            _sec("23", _sub("1", _content("old part I chapter 5 content"))),
                        ),
                    ),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="5")
    op.target_part = "II"
    op.scope_provenance_tags = ("grouped_part_scope",)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new part II chapter 5 content")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="5",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[1997/1251] REPLACE II osa 5 luku 23 §",
    )

    assert resolution.path is None
    assert resolution.reason_code is None


def test_apply_op_does_not_rehome_root_level_unique_global_section_for_carry_forward_scope() -> None:
    from lawvm.core.canonical_intent import ExecutionContract, IntentKind, NodeTarget, OccupancyPolicy, Replace
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("23", _content("root-level section"))))
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(address=LegalAddress(path=(("chapter", "6"), ("section", "23")))),
        payload=cast(Any, _sec("23", _content("new root-level section"))),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = _make_rop(op, intent, muutos_ir=_sec("23", _content("new root-level section")))
    rop.scope_provenance_tags = ("chapter_scope_carry_forward",)
    ctx = _ctx(_body())
    source_pathologies: list[SourcePathology] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("23", _content("new root-level section")),
        replay_mode="legal_pit",
        source_pathologies_out=source_pathologies,
        rop=rop,
    )

    root_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "23")
    root_text = next(c for c in root_sec.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT))
    assert irnode_to_text(root_text) == "root-level section"
    assert source_pathologies == []


def test_resolve_section_path_with_fallbacks_allows_unique_global_descendant_insert() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(_sec("159", _sub("1", _content("target"))),),
                    ),
                ),
            )
        )
    )
    op = _op(op_type="INSERT", target_section="159", target_chapter="1", target_paragraph=4)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sub("4", _content("new fourth")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="159",
        target_chapter="1",
    )

    resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        rop.muutos_ir,
        None,
        "[2019/371] INSERT 1 luku 159 § 4 mom",
    )

    assert resolution.path == (("part", "4"), ("chapter", "2"), ("section", "159"))
    assert resolution.reason_code == "live_unique_global_fallback"


def test_apply_op_uses_apply_fallback_tag_not_source_pathology_for_live_unique_scope_fallback(monkeypatch) -> None:
    from lawvm.core.canonical_intent import ExecutionContract, IntentKind, NodeTarget, OccupancyPolicy, Replace
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.ops import SectionPathResolution

    state = _make_state(_body(_sec("23", _content("old section"))))
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(address=LegalAddress(path=(("chapter", "6"), ("section", "23")))),
        payload=cast(Any, _sec("23", _content("new section"))),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = _make_rop(op, intent, muutos_ir=_sec("23", _content("new section")))
    rop.scope_provenance_tags = ("chapter_scope_carry_forward",)
    ctx = _ctx(_body())
    source_pathologies: list[SourcePathology] = []
    mutation_events: list[ApplyMutationEvent] = []

    def fake_resolve(*_args, **_kwargs) -> SectionPathResolution:
        return SectionPathResolution(path=(("section", "23"),), reason_code="live_unique_global_fallback")

    monkeypatch.setattr(
        "lawvm.finland.apply_typed_dispatch._resolve_section_path_with_fallbacks",
        fake_resolve,
    )

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("23", _content("new section")),
        replay_mode="legal_pit",
        source_pathologies_out=source_pathologies,
        mutation_events_out=mutation_events,
        rop=rop,
    )

    sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "23")
    text_node = next(c for c in sec.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT))
    assert irnode_to_text(text_node) == "new section"
    assert source_pathologies == []
    assert len(mutation_events) == 1
    assert mutation_events[0].used_fallback_tags == (
        "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
        "live_unique_global_fallback",
    )


def test_apply_op_does_not_rehome_unique_global_section_across_part_for_grouped_part_scope() -> None:
    from lawvm.core import tree_ops as _tops

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="5",
                        children=(
                            _sec("23", _content("part I chapter 5 section")),
                        ),
                    ),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="5")
    op.target_part = "II"
    op.scope_provenance_tags = ("grouped_part_scope",)
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new part II chapter 5 section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="5",
    )
    ctx = _ctx(_body())
    source_pathologies: list[SourcePathology] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=_sec("23", _content("new part II chapter 5 section")),
        replay_mode="legal_pit",
        source_pathologies_out=source_pathologies,
        rop=rop,
    )

    part_i_sec = _tops.resolve(result.ir, (("part", "I"), ("chapter", "5"), ("section", "23")))
    assert part_i_sec is not None
    part_i_text = next(c for c in part_i_sec.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT))
    assert irnode_to_text(part_i_text) == "part I chapter 5 section"
    assert _tops.resolve(result.ir, (("part", "II"), ("chapter", "5"), ("section", "23"))) is None


def test_apply_op_emits_shape_loss_pathology_for_sparse_alakohta_replace_merge() -> None:
    state = _make_state(
        _body(
            _sec(
                "2",
                _sub(
                    "1",
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            _intro("1) alkuperainen"),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("a"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(_content("b"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="c", children=(_content("c"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="d", children=(_content("d"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="e", children=(_content("e"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="f", children=(_content("f"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="g", children=(_content("g"),)),
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(_intro("1) paivitetty"),),
        ),
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="h",
            children=(_content("h alakohta"),),
        ),
    )
    muutos_ir = _sec("2", amend_sub)
    op = AmendmentOp(
        op_id="test_sparse_alakohta_replace",
        op_type="REPLACE",
        target_section="2",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="1",
        source_statute="2018/1225",
        source_issue_date=_DATE,
    )
    pathologies = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "sparse_alakohta_replace_merge"


def test_apply_op_strict_blocks_sparse_alakohta_replace_merge() -> None:
    state = _make_state(
        _body(
            _sec(
                "2",
                _sub(
                    "1",
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            _intro("1) alkuperainen"),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("a"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(_content("b"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="c", children=(_content("c"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="d", children=(_content("d"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="e", children=(_content("e"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="f", children=(_content("f"),)),
                            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="g", children=(_content("g"),)),
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(_intro("1) paivitetty"),),
        ),
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="h",
            children=(_content("h alakohta"),),
        ),
    )
    muutos_ir = _sec("2", amend_sub)
    op = AmendmentOp(
        op_id="test_sparse_alakohta_replace_strict",
        op_type="REPLACE",
        target_section="2",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="1",
        source_statute="2018/1225",
        source_issue_date=_DATE,
    )
    pathologies: list[SourcePathology] = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        strict_profile=default_finland_strict_profile(),
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "sparse_alakohta_replace_merge"


def test_apply_op_prefers_slot_assignment_when_amend_sub_ir_is_absent() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("original")))))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
    amend_sub = _sub("1", _content("replacement via slot assignment"))
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): amend_sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="1",
            )
        ,),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir=None,
        amend_sub_ir=None,
        slot_assignment=assignment,
        replay_mode="legal_pit",
    )

    result = _modified(state, result)
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
    text = " ".join(c.text or "" for c in sub.children)
    assert "replacement via slot assignment" in text


def test_apply_deterministic_subsection_op_does_not_singleton_fallback_missing_amend_sub_ir() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("original")))))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
    muutos_ir = _sec("1", _sub("9", _content("under-specified singleton payload")))

    result = _apply_deterministic_subsection_op(
        state,
        op,
        (("section", "1"),),
        muutos_ir,
        None,
        None,
        _LEGAL_PIT,
        "test",
    )

    assert result is None


def test_normalize_subsection_dispatch_inputs_blocks_singleton_item_rebound_in_strict_mode() -> None:
    master_subsecs = [
        _sub("1", _para("1", "first item"), _para("2", "second item")),
    ]
    amend_sub = _sub("3", _para("3", "new third item"))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=3)
    rop = _make_rop(op, _make_replace_intent("1", amend_sub), muutos_ir=_sec("1", amend_sub))
    pathologies: list[SourcePathology] = []

    normalized_dispatch_op, normalized_rop = _normalize_subsection_dispatch_inputs(
        dispatch_op=rop,
        rop=rop,
        master_subsecs=master_subsecs,
        amend_sub_ir=amend_sub,
        ctx_label="1 § 3 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert normalized_dispatch_op is rop
    assert normalized_rop is rop
    assert rop.effective_target_paragraph == 3
    assert rop.effective_target_item_label is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "single_subsection_item_fallback"


def test_apply_op_typed_strict_blocks_singleton_item_rebound() -> None:
    state = _make_state(
        _body(
            _sec(
                "1",
                _sub("1", _para("1", "first item"), _para("2", "second item")),
            )
        )
    )
    amend_sub = _sub("3", _para("3", "new third item"))
    payload = _sec("1", amend_sub)
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=3)
    rop = _make_rop(op, _make_replace_intent("1", payload), muutos_ir=payload)
    pathologies: list[SourcePathology] = []
    failed_ops: list[FailedOp] = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir=payload,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
        failed_ops_out=failed_ops,
        strict_profile=default_finland_strict_profile(),
        rop=rop,
    )

    assert result is state
    assert any(p.code == "SUBSECTION_TARGET_REBOUND" for p in pathologies)
    assert any(
        p.code == "SUBSECTION_TARGET_REBOUND" and p.detail["rebound_kind"] == "single_subsection_item_fallback"
        for p in pathologies
    )
    assert [f.reason for f in failed_ops] == ["no deterministic path"]


def test_apply_op_prefers_slot_assignment_over_stale_amend_sub_ir() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("original")))))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
    stale_amend_sub = _sub("1", _content("stale fallback payload"))
    assigned_amend_sub = _sub("1", _content("authoritative slot assignment payload"))
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): assigned_amend_sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="1",
            )
        ,),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir=None,
        amend_sub_ir=stale_amend_sub,
        slot_assignment=assignment,
        replay_mode="legal_pit",
    )

    result = _modified(state, result)
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
    text = " ".join(c.text or "" for c in sub.children)
    assert "authoritative slot assignment payload" in text
    assert "stale fallback payload" not in text


def test_apply_op_handles_sparse_omission_payload_via_slot_assignment_without_apply_fallback() -> None:
    state = _make_state(
        _body(
            _sec(
                "1",
                _sub("1", _content("original first")),
                _sub("2", _content("original second")),
                _sub("3", _content("original third")),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=2)
    assigned_amend_sub = _sub("2", _content("replacement via slot assignment"))
    muutos_ir = _sec(
        "1",
        _sub("1", _content("preserved lead")),
        IRNode(kind=IRNodeKind.OMISSION),
        _sub("2", _content("replacement via slot assignment")),
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): assigned_amend_sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=2,
                payload_slot_label="2",
            )
        ,),
        used_subs=(1,),
        unassigned_payload_slots=(),
    )
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        muutos_ir=muutos_ir,
        amend_sub_ir=None,
        slot_assignment=assignment,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    result = _modified(state, result)
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    second_sub = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION][1]
    text = " ".join(c.text or "" for c in second_sub.children)
    assert "replacement via slot assignment" in text
    assert all("omission_offset_recovery" not in event.used_fallback_tags for event in mutation_events)


def test_apply_op_emits_shape_loss_pathology_for_content_only_row_merge() -> None:
    state = _make_state(
        _body(
            _sec(
                "9",
                _sub(
                    "1",
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Toimituksista maksetaan palkkiota seuraavasti: "
                            "A. Eläimen ruumiinavaus 29,00 "
                            "G. Laitoksen tarkastus 22,00 "
                            "H. Poronlihan tarkastus / tarkastettu ruho 1,35"
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Toimituksista maksetaan palkkiota seuraavasti: "
                "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan "
                "valvonta / tunti 32,3"
            ),
        ),
    )
    muutos_ir = _sec("9", amend_sub)
    op = AmendmentOp(
        op_id="test_content_only_row_merge",
        op_type="REPLACE",
        target_section="9",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="h",
        source_statute="2000/1",
        source_issue_date=_DATE,
    )
    pathologies = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    merged_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
    merged_text = " ".join(c.text or "" for c in merged_sub.children)
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "content_only_row_merge"
    assert "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan valvonta / tunti 32,3" in merged_text
    assert "A. Eläimen ruumiinavaus 29,00" in merged_text
    assert "G. Laitoksen tarkastus 22,00" in merged_text


def test_apply_op_strict_blocks_content_only_row_merge() -> None:
    state = _make_state(
        _body(
            _sec(
                "9",
                _sub(
                    "1",
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Toimituksista maksetaan palkkiota seuraavasti: "
                            "A. Eläimen ruumiinavaus 29,00 "
                            "G. Laitoksen tarkastus 22,00 "
                            "H. Poronlihan tarkastus / tarkastettu ruho 1,35"
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Toimituksista maksetaan palkkiota seuraavasti: "
                "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan "
                "valvonta / tunti 32,3"
            ),
        ),
    )
    muutos_ir = _sec("9", amend_sub)
    op = AmendmentOp(
        op_id="test_content_only_row_merge_strict",
        op_type="REPLACE",
        target_section="9",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="h",
        source_statute="2000/1",
        source_issue_date=_DATE,
    )
    pathologies: list[SourcePathology] = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        strict_profile=default_finland_strict_profile(),
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "content_only_row_merge"


def test_apply_op_emits_item_target_absent_for_unmatched_content_only_row_merge() -> None:
    state = _make_state(
        _body(
            _sec(
                "9",
                _sub(
                    "1",
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Toimituksista maksetaan palkkiota seuraavasti: "
                            "A. Eläimen ruumiinavaus 29,00 "
                            "G. Laitoksen tarkastus 22,00"
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        _para("h", "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan valvonta / tunti 32,3"),
    )
    muutos_ir = _sec("9", amend_sub)
    op = AmendmentOp(
        op_id="test_content_only_row_merge_absent",
        op_type="REPLACE",
        target_section="9",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="h",
        source_statute="2000/1",
        source_issue_date=_DATE,
    )
    pathologies = []

    result = apply_op(
        state,
        op,
        _ctx(),
        muutos_ir,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert [p.code for p in pathologies] == ["ITEM_TARGET_STRUCTURE_ABSENT"]
    assert pathologies[0].detail["live_has_paragraphs"] is False
    assert pathologies[0].detail["amend_has_paragraphs"] is True


def test_apply_op_sanitizes_shared_tail_from_sparse_item_replace_payload() -> None:
    state = _make_state(
        _body(
            _sec(
                "10",
                _sub(
                    "1",
                    _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            _content("ensimmainen kohta,"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2)"),
                            _content("vanha toinen kohta tai"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="3)"),
                            _content("kolmas kohta,"),
                            _content("yhteinen sanktiolause."),
                        ),
                    ),
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
        IRNode(kind=IRNodeKind.OMISSION),
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2)"),
                _intro("uusi toinen kohta tai"),
                IRNode(kind=IRNodeKind.OMISSION),
                IRNode(
                    kind=IRNodeKind.SUBPARAGRAPH,
                    children=(_content("yhteinen sanktiolause."),),
                ),
                IRNode(kind=IRNodeKind.OMISSION),
            ),
        ),
    )
    op = AmendmentOp(
        op_id="test_shared_tail_item_replace",
        op_type="REPLACE",
        target_section="10",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="2",
        source_statute="2009/1525",
        source_issue_date=_DATE,
    )
    pathologies = []

    result = apply_op(
        state,
        op,
        _ctx(),
        _sec("10", amend_sub),
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
    )

    result = _modified(state, result)
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "shared_tail_item_replace_sanitize"

    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    sub1 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    paras = [c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH]
    para2 = next(c for c in paras if c.label == "2")
    para3 = next(c for c in paras if c.label == "3")
    para2_text = " ".join(
        (child.text or "") for child in para2.children if child.kind in {IRNodeKind.NUM, IRNodeKind.INTRO, IRNodeKind.CONTENT}
    )
    para3_text = " ".join(
        (child.text or "") for child in para3.children if child.kind in {IRNodeKind.NUM, IRNodeKind.INTRO, IRNodeKind.CONTENT}
    )

    assert "uusi toinen kohta tai" in para2_text
    assert "yhteinen sanktiolause" not in para2_text
    assert "yhteinen sanktiolause" in para3_text


def test_apply_op_strict_blocks_shared_tail_item_replace_sanitize() -> None:
    para1 = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        children=(
            _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
            IRNode(kind=IRNodeKind.NUM, text="1)"),
            _content("ensimmäinen kohta tai"),
            _content("yhteinen sanktiolause."),
        ),
    )
    para2 = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2)"),
            _content("toinen kohta tai"),
            _content("yhteinen sanktiolause."),
        ),
    )
    para3 = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3)"),
            _content("kolmas kohta,"),
            _content("yhteinen sanktiolause."),
        ),
    )
    state = _make_state(
        _body(
            _sec(
                "10",
                _sub(
                    "1",
                    para1,
                    para2,
                    para3,
                ),
            )
        )
    )
    amend_sub = _sub(
        "1",
        _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
        IRNode(kind=IRNodeKind.OMISSION),
        IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2)"),
                _intro("uusi toinen kohta tai"),
                IRNode(kind=IRNodeKind.OMISSION),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, children=(_content("yhteinen sanktiolause."),)),
                IRNode(kind=IRNodeKind.OMISSION),
            ),
        ),
    )
    op = AmendmentOp(
        op_id="test_shared_tail_item_replace_strict",
        op_type="REPLACE",
        target_section="10",
        target_unit_kind="section",
        target_paragraph=1,
        target_item="2",
        source_statute="2009/1525",
        source_issue_date=_DATE,
    )
    pathologies: list[SourcePathology] = []

    result = apply_op(
        state,
        op,
        _ctx(),
        _sec("10", amend_sub),
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        strict_profile=default_finland_strict_profile(),
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "shared_tail_item_replace_sanitize"


def test_apply_op_emits_failed_mutation_event_for_missing_section() -> None:
    state = _make_state(_body())
    op = _op(op_type="REPEAL", target_section="999")
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(_body()),
        muutos_ir=None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    assert result is state
    assert len(mutation_events) == 1
    assert mutation_events[0].helper == "_apply_whole_section_op"
    assert mutation_events[0].outcome == "failed"

    def test_not_applicable_for_repeal(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_replace(state, op, sec_path, sec, subsecs, None, None, _FINLEX_ORACLE, "1 § 1 mom")
        assert result is None

    def test_no_amend_sub_returns_none(self):
        state, sec_path, sec = self._make_sec_and_path()
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_replace(state, op, sec_path, sec, subsecs, None, None, _FINLEX_ORACLE, "1 § 1 mom")
        assert result is None


# ---------------------------------------------------------------------------
# _apply_subsection_insert
# ---------------------------------------------------------------------------


class TestApplySubsectionInsert:
    def test_insert_new_subsection(self):
        sec = _sec("1", _sub("1", _content("first")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=2)
        amend_sub = _sub("2", _content("new subsection"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_insert(state, op, sec_path, sec, subsecs, amend_sub, "1 § ins 2 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        assert len([c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]) == 2

    def test_insert_new_first_subsection_renumbers_existing_head(self):
        # For a NON-temporary INSERT, inserting a new subsection 1 when subsection
        # 1 already exists must push the existing one to slot 2 (renumber).
        # The dedup guard (insert-as-replace) only applies to temporary ops.
        sec = _sec("4", _sub("1", _content("old first")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "4")]
        op = _op(op_type="INSERT", target_section="4", target_paragraph=1)
        amend_sub = _sub("1", _content("new first"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            "4 § ins 1 mom",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in new_subsecs] == ["1", "2"]
        assert "new first" in " ".join(child.text or "" for child in new_subsecs[0].children)
        assert "old first" in " ".join(child.text or "" for child in new_subsecs[1].children)
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "subsection_insert_renumber"

    def test_permanent_insert_same_payload_still_renumbers_existing_head(self):
        sec = _sec("4", _sub("1", _content("same text")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "4")]
        op = _op(op_type="INSERT", target_section="4", target_paragraph=1)
        amend_sub = _sub("1", _content("same text"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_subsection_insert(state, op, sec_path, sec, subsecs, amend_sub, "4 § ins 1 mom")

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in new_subsecs] == ["1", "2"]
        assert "same text" in " ".join(child.text or "" for child in new_subsecs[0].children)
        assert "same text" in " ".join(child.text or "" for child in new_subsecs[1].children)

    def test_routed_duplicate_same_payload_overwrites_existing_slot(self):
        sec = _sec(
            "3",
            _sub("1", _content("first")),
            _sub("2", _content("shared text")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "3")]
        op = _op(op_type="INSERT", target_section="3", target_paragraph=2)
        op.op_id = "routed_duplicate_slot"
        amend_sub = _sub("2", _content("shared text"))
        slots = SubsectionSlotMap()
        slots.assign(op, amend_sub)
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="3",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=slots,
                sparse_slot_bindings=(),
                used_subs=(0,),
                unassigned_payload_slots=(),
            ),
        )
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_subsection_insert(
            state,
            rop,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            "3 § ins 2 mom",
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in new_subsecs] == ["1", "2"]
        assert "shared text" in " ".join(child.text or "" for child in new_subsecs[1].children)
        assert len(new_subsecs) == 2

    def test_not_applicable_without_amend_sub(self):
        sec = _sec("1", _sub("1", _content("first")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=2)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_insert(state, op, sec_path, sec, subsecs, None, "1 § ins 2 mom")
        assert result is None

    def test_not_applicable_for_item_op(self):
        sec = _sec("1", _sub("1", _content("first")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="3")
        amend_sub = _sub("1", _para("3", "new item"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_subsection_insert(state, op, sec_path, sec, subsecs, amend_sub, "1 § ins 1 mom 3 k")
        assert result is None

    def test_insert_duplicate_label_replaces_not_duplicates(self):
        """Successive TEMPORARY amendments both INSERTing the same momentti should
        not create a duplicate.  The second INSERT must overwrite the first.

        Regression case: 2000/40 (Eduskunnan työjärjestys) — amendments 2020/708
        and 2022/108 both do ``lisätään väliaikaisesti 1 §:ään uusi 3 momentti``.
        Both parse to INSERT section:1 subsection:3.  The second INSERT must
        replace the existing 3rd momentti, not create a second one.

        In the generic path, the dedup guard only applies to temporary ops.
        A permanent INSERT of an existing label must insert-and-renumber (see
        test above).
        """
        # State after the first INSERT: section 1 has subsections 1, 2, 3
        sec = _sec(
            "1",
            _sub("1", _content("first")),
            _sub("2", _content("second")),
            _sub("3", _content("original temporary text")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        # Mark the op as temporary — dedup guard only applies to temporary ops
        op = _op(op_type="INSERT", target_section="1", target_paragraph=3, is_temporary=True)
        # Second amendment provides new content for subsection 3
        amend_sub = _sub("3", _content("updated temporary text"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            "1 § ins 3 mom (dedup)",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        # Must still have exactly 3 subsections — no duplicate
        assert len(new_subsecs) == 3, f"Expected 3 subsections, got {len(new_subsecs)}"
        # Labels must be 1, 2, 3 — no renumbering to 4
        assert [s.label for s in new_subsecs] == ["1", "2", "3"]
        # The 3rd subsection should contain the new text
        sub3_text = " ".join(
            child.text or "" for child in new_subsecs[2].children
        )
        assert "updated temporary text" in sub3_text, f"Expected updated text, got: {sub3_text!r}"
        # The original text must be gone — no duplicate content
        assert "original temporary text" not in sub3_text
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert pathologies[0].detail["recovery_kind"] == "subsection_insert_temporary_duplicate_label_replace"

    def test_subsection_insert_temporary_duplicate_label_replace_strict_blocks(self):
        sec = _sec(
            "1",
            _sub("1", _content("first")),
            _sub("2", _content("second")),
            _sub("3", _content("original temporary text")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=3, is_temporary=True)
        amend_sub = _sub("3", _content("updated temporary text"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            "1 § ins 3 mom (dedup strict)",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert pathologies[0].detail["recovery_kind"] == "subsection_insert_temporary_duplicate_label_replace"

    def test_subsection_insert_replaces_repeal_placeholder_slot_without_renumber(self):
        sec = _sec(
            "51",
            _sub("1", _content("first")),
            _sub("2", _content("second")),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", attrs={"lawvm_repeal_placeholder": "1"}),
            _sub("4", _content("old fourth stays fourth")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "51")]
        op = _op(op_type="INSERT", target_section="51", target_paragraph=3)
        amend_sub = _sub("3", _content("new third"))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_subsection_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            "51 § ins 3 mom (placeholder replace)",
            source_pathologies_out=pathologies,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [s.label for s in new_subsecs] == ["1", "2", "3", "4"]
        assert new_subsecs[2].children[0].text == "new third"
        assert new_subsecs[3].children[0].text == "old fourth stays fourth"
        assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
        assert pathologies[0].detail["recovery_kind"] == "subsection_insert_repeal_placeholder_replace"

    def test_in_place_merge_insert_replaces_existing_subsection_without_renumber(self):
        """Regression: 1996/627 §1 — item INSERT accumulated via
        _merge_section_inner_subsection_omission_ir should replace subsection:1
        in-place, not push it up to slot 2 and corrupt subsection:2.

        Provenance: 2012/80 added item 13b to 1996/627 §1 mom 1.  Without this
        fix the whole-section INSERT op (with the merged payload) called
        _insert_subsection_with_renumber_ir, renaming the live subsection:1 to
        subsection:2 and subsection:2 (short sentence) to subsection:3.
        """
        item_a = IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="item one"),))
        item_b = IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="item two"),))
        item_new = IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="item three NEW"),))
        sec = _sec(
            "1",
            _sub("1", item_a, item_b),
            _sub("2", _content("short sentence subsection 2")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1)
        # The merged subsection carries the lawvm_in_place_merge marker — this
        # is what _merge_section_inner_subsection_omission_ir sets when called
        # from the _is_single_subsection_insert_item_shell_ir path.
        merged_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            attrs={"lawvm_in_place_merge": "1"},
            children=(item_a, item_b, item_new),
        )
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_subsection_insert(state, op, sec_path, sec, subsecs, merged_sub, "1 § ins 1 mom 3 k (in-place merge)")

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        # Must stay at exactly 2 subsections — no spurious renumbering
        assert len(new_subsecs) == 2, f"Expected 2 subsections, got {len(new_subsecs)}: {[s.label for s in new_subsecs]}"
        assert [s.label for s in new_subsecs] == ["1", "2"]
        # Subsection 1 must have the merged content (3 items)
        sub1_items = [c for c in new_subsecs[0].children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(sub1_items) == 3, f"Expected 3 items in subsection:1, got {len(sub1_items)}"
        # Check nested text (items store text in CONTENT children)
        def _flat_text(node: IRNode) -> str:
            parts = [node.text or ""]
            for child in node.children:
                parts.append(_flat_text(child))
            return " ".join(parts)
        sub1_text = _flat_text(new_subsecs[0])
        assert "item three NEW" in sub1_text, f"Expected 'item three NEW' in sub1: {sub1_text!r}"
        # Subsection 2 must retain its original content unchanged
        sub2_text = " ".join(child.text or "" for child in new_subsecs[1].children)
        assert "short sentence subsection 2" in sub2_text


class TestSubsectionMigrationRebinding:
    def test_non_insert_subsection_replace_follows_same_wave_migration(self):
        sec = _sec(
            "36",
            _sub("1", _content("first")),
            _sub("2", _content("second")),
            _sub("4", _content("old third moved to 4")),
            _sub("5", _content("old fourth moved to 5")),
        )
        state = _make_state(_body(sec))
        sec_path = (("section", "36"),)
        amend_sub = _sub("4", _content("replacement for moved fourth"))
        op = _op(op_type="REPLACE", target_section="36", target_paragraph=4)
        op.op_id = "replace_migrated_4"
        slots = SubsectionSlotMap()
        slots.assign(op, amend_sub)
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="36",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=slots,
                sparse_slot_bindings=(),
                used_subs=(0,),
                unassigned_payload_slots=(),
            ),
        )
        ledger = MigrationLedger()
        ledger.record_renumber(
            LegalAddress(path=(("section", "36"), ("subsection", "4"))),
            LegalAddress(path=(("section", "36"), ("subsection", "5"))),
            effective="2025-01-01",
            source_statute="2024/936",
        )

        result = _apply_deterministic_subsection_op(
            state,
            rop,
            sec_path,
            None,
            None,
            rop.slot_assignment,
            _LEGAL_PIT,
            "36 § 4 mom",
            rop=rop,
            migration_ledger=ledger,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "36")
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in new_subsecs] == ["1", "2", "4", "5"]
        assert "old third moved to 4" in irnode_to_text(new_subsecs[2])
        assert "replacement for moved fourth" in irnode_to_text(new_subsecs[3])

    def test_insert_does_not_follow_same_wave_migration(self):
        sec = _sec(
            "36",
            _sub("1", _content("first")),
            _sub("2", _content("second")),
            _sub("4", _content("old third moved to 4")),
            _sub("5", _content("old fourth moved to 5")),
        )
        state = _make_state(_body(sec))
        sec_path = (("section", "36"),)
        amend_sub = _sub("3", _content("new third"))
        op = _op(op_type="INSERT", target_section="36", target_paragraph=3)
        op.op_id = "insert_new_3"
        slots = SubsectionSlotMap()
        slots.assign(op, amend_sub)
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="36",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=slots,
                sparse_slot_bindings=(),
                used_subs=(0,),
                unassigned_payload_slots=(),
            ),
        )
        ledger = MigrationLedger()
        ledger.record_renumber(
            LegalAddress(path=(("section", "36"), ("subsection", "4"))),
            LegalAddress(path=(("section", "36"), ("subsection", "5"))),
            effective="2025-01-01",
            source_statute="2024/936",
        )

        result = _apply_deterministic_subsection_op(
            state,
            rop,
            sec_path,
            None,
            None,
            rop.slot_assignment,
            _LEGAL_PIT,
            "36 § ins 3 mom",
            rop=rop,
            migration_ledger=ledger,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "36")
        new_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in new_subsecs] == ["1", "2", "3", "4", "5"]

    def test_shifted_replace_rebase_does_not_follow_same_wave_migration(self):
        op = _op(op_type="REPLACE", target_section="53", target_paragraph=6)
        op.op_id = "shifted_replace_6"
        op = dc_replace(
            op,
            target_guessing_provenance_tags=("rebase_duplicate_target_shifted_replace",),
            lo=LegalOperation(
                op_id="shifted_replace_6",
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "53"), ("subsection", "6"))),
            ),
        )
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="53",
            target_chapter=None,
            slot_assignment=SubsectionSlotAssignmentResult(
                subsec_map=SubsectionSlotMap(),
                sparse_slot_bindings=(),
                used_subs=(),
                unassigned_payload_slots=(),
            ),
        )
        ledger = MigrationLedger()
        ledger.record_renumber(
            LegalAddress(path=(("section", "53"), ("subsection", "6"))),
            LegalAddress(path=(("section", "53"), ("subsection", "7"))),
            effective="2010-01-01",
            source_statute="2009/925",
        )

        got = _follow_same_wave_subsection_migration(rop, migration_ledger=ledger)

        assert got.resolved_target_address == rop.resolved_target_address
        assert "follow_same_wave_migration" not in got.target_guessing_provenance_tags


# ---------------------------------------------------------------------------
# _apply_item_repeal
# ---------------------------------------------------------------------------


class TestApplyItemRepeal:
    def _make_sec_with_items(self):
        para1 = _para("1", "first item")
        para2 = _para("2", "second item")
        sub1 = _sub("1", _para("1", "intro only"))
        sub2 = _sub("2", para1, para2)
        sec = _sec("1", sub1, sub2)
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        return state, sec_path, sec

    def test_repeal_item(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=2, target_item="1")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _LEGAL_PIT, "1 § 2 mom 1 k")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        subs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        sub2 = subs[1]
        paras = [c for c in sub2.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(paras) == 1
        assert paras[0].label == "2"

    def test_repeal_item_not_found_returns_state(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=2, target_item="99")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _LEGAL_PIT, "1 § 2 mom 99 k")
        assert _unchanged(state, result)

    def test_not_applicable_for_subsection_repeal(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=2)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _LEGAL_PIT, "1 § 2 mom")
        assert result is None

    def test_explicit_post_repeal_shift_relabels_later_lettered_items(self):
        sec = _sec(
            "2",
            _sub(
                "1",
                _para("c", "c text"),
                _para("d", "d text"),
                _para("e", "e text"),
                _para("f", "f text"),
            ),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]
        op = _op(op_type="REPEAL", target_section="2", target_paragraph=1, target_item="d")
        op.post_repeal_item_shift_label = "d"
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "2 § 1 mom d k")

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        assert _paragraph_labels(new_sub) == ["c", "d", "e"]
        assert "d text" not in " ".join(c.text or "" for c in new_sub.children)

    def test_repeal_always_synthesizes_placeholder_in_finlex_oracle(self):
        """In finlex_oracle mode, item repeal always keeps a placeholder — not
        just for intro-list shapes.  The placeholder retains the label so later
        amendments can use it as an anchor.
        (PRO_RESPONSE_5_1 §8 — repeal and visibility are separate questions.)
        """
        para1 = _para("1", "item 1")
        para2 = _para("2", "item 2")
        para3 = _para("3", "item 3")
        # Simple subsection with numbered items — NOT an intro-list shape.
        sub = _sub("1", para1, para2, para3)
        sec = _sec("10", sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "10")]
        op = _op(op_type="REPEAL", target_section="10", target_paragraph=1, target_item="2")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "10 § 1 mom 2 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        # All three paragraph slots should still be present.
        assert len(paras) == 3, f"Expected 3 paragraphs (placeholder kept), got {len(paras)}"
        # The middle paragraph (item 2) should be a repeal placeholder.
        ph = next((p for p in paras if p.label and p.label.strip() in ("2", "2)")), None)
        assert ph is not None, "Item 2 placeholder not found"
        assert ph.attrs.get("lawvm_repeal_placeholder") == "1", (
            "Placeholder must carry lawvm_repeal_placeholder=1"
        )
        # Items 1 and 3 should be live.
        live_labels = [p.label or "" for p in paras if p.attrs.get("lawvm_repeal_placeholder") != "1"]
        assert any("1" in lbl for lbl in live_labels), f"Item 1 should be live in {live_labels}"
        assert any("3" in lbl for lbl in live_labels), f"Item 3 should be live in {live_labels}"

    def test_repeal_then_insert_anchor_after_repealed_item(self):
        """Repeal item 15, then insert item 16 anchored after item 15.
        Item 15 must be retained as a placeholder so the anchor lookup works.
        Bug: 2008/878 kohta 15 repealed → 2025/163 inserts kohta 16 after it.
        (PRO_RESPONSE_5_1 §8)
        """
        # Build section with items 14, 15.
        para14 = _para("14", "item 14 text")
        para15 = _para("15", "item 15 text")
        sub = _sub("1", para14, para15)
        sec = _sec("5", sub)
        body = _body(sec)

        # Step 1: repeal item 15 in finlex_oracle mode.
        state = _make_state(body)
        sec_path = [("section", "5")]
        repeal_op = _op(op_type="REPEAL", target_section="5", target_paragraph=1, target_item="15")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        state_after_repeal = _apply_item_repeal(state, repeal_op, sec_path, sec, subsecs, _FINLEX_ORACLE, "5 § 1 mom 15 k")
        state_after_repeal = _modified(state, state_after_repeal)

        # Item 15 placeholder must be present.
        new_sec = next(c for c in state_after_repeal.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras_after_repeal = [c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        ph15 = next((p for p in paras_after_repeal if p.label and "15" in p.label), None)
        assert ph15 is not None, "Item 15 placeholder must be retained after repeal"
        assert ph15.attrs.get("lawvm_repeal_placeholder") == "1"

        # Step 2: insert item 16 anchored after item 15.
        amend_para16 = _para("16", "item 16 text")
        amend_sub = _sub("1", amend_para16)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="5", children=(amend_sub,))
        insert_op = _op(op_type="INSERT", target_section="5", target_paragraph=1, target_item="16")

        # Re-extract the current section state.
        sec2 = next(c for c in state_after_repeal.ir.children if c.kind == IRNodeKind.SECTION)
        subsecs2 = [c for c in sec2.children if c.kind == IRNodeKind.SUBSECTION]
        state_after_insert = _apply_item_insert(
            state_after_repeal, insert_op, sec_path, sec2, subsecs2, amend_sub, muutos_ir,
            "5 § 1 mom 16 k"
        )
        state_after_insert = _modified(state_after_repeal, state_after_insert)

        # Item 16 must be placed immediately after item 15, not at the end.
        final_sec = next(c for c in state_after_insert.ir.children if c.kind == IRNodeKind.SECTION)
        final_sub = next(c for c in final_sec.children if c.kind == IRNodeKind.SUBSECTION)
        final_paras = [c for c in final_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        labels = [p.label for p in final_paras]
        # Item 16 must appear right after item 15 in the sequence.
        idx15 = next((i for i, lbl in enumerate(labels) if lbl and "15" in lbl), None)
        idx16 = next((i for i, lbl in enumerate(labels) if lbl and "16" in lbl), None)
        assert idx15 is not None, f"Item 15 not found in {labels}"
        assert idx16 is not None, f"Item 16 not found in {labels}"
        assert idx16 == idx15 + 1, (
            f"Item 16 should be immediately after item 15, but labels are {labels}"
        )

    def test_insert_over_repeal_placeholder_no_cascade(self):
        """INSERT targeting the label of a previous REPEAL placeholder replaces
        the placeholder in-place without cascading renumber.

        Scenario (from 2008/878 §5):
          1. Item 16 is originally live (with content X).
          2. Amendment A repeals item 16 → placeholder kept at label=16.
          3. Amendment B repeals item 15 → placeholder at label=15.
          4. Amendment C inserts a new item 16 (content Y).

        Without the fix, step 4 inserts *after* the label-15 placeholder
        (anchor), pushing the old label-16 placeholder to 17, and cascading
        all subsequent items up by one.  With the fix, the label-16
        placeholder is replaced in-place and the remaining items stay put.

        (PRO_RESPONSE_5_1 §8 follow-up; bug: 2008/878 → 2025/163 insert 16
        after 2021/524 repeal of 16 cascaded 17→18, 18→19 … )
        """
        # Build section: items 14, 15, 16, 17, 18.
        para14 = _para("14", "item 14 text")
        para15 = _para("15", "item 15 text")
        para16_orig = _para("16", "item 16 original text")
        para17 = _para("17", "item 17 text")
        para18 = _para("18", "item 18 text")
        sub = _sub("1", para14, para15, para16_orig, para17, para18)
        sec = _sec("5", sub)
        body = _body(sec)
        sec_path = [("section", "5")]

        # Step 1: repeal item 16.
        state = _make_state(body)
        repeal16_op = _op(op_type="REPEAL", target_section="5", target_paragraph=1, target_item="16")
        sec_node = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION)
        subsecs = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
        state = _modified(state, _apply_item_repeal(
            state, repeal16_op, sec_path, sec_node, subsecs, _FINLEX_ORACLE, "5 § 1 mom 16 k"
        ))

        # Step 2: repeal item 15.
        repeal15_op = _op(op_type="REPEAL", target_section="5", target_paragraph=1, target_item="15")
        sec_node = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION)
        subsecs = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
        state = _modified(state, _apply_item_repeal(
            state, repeal15_op, sec_path, sec_node, subsecs, _FINLEX_ORACLE, "5 § 1 mom 15 k"
        ))

        # Verify both placeholders are present.
        sec_node = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION)
        sub_node = next(c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub_node.children if c.kind == IRNodeKind.PARAGRAPH]
        ph15 = next((p for p in paras if p.label and "15" in p.label), None)
        ph16 = next((p for p in paras if p.label and p.label == "16"), None)
        assert ph15 is not None and ph15.attrs.get("lawvm_repeal_placeholder") == "1"
        assert ph16 is not None and ph16.attrs.get("lawvm_repeal_placeholder") == "1"

        # Step 3: INSERT new item 16 (should replace the placeholder, not cascade).
        amend_para16_new = _para("16", "item 16 new text")
        amend_sub = _sub("1", amend_para16_new)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="5", children=(amend_sub,))
        insert16_op = _op(op_type="INSERT", target_section="5", target_paragraph=1, target_item="16")

        sec_node = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION)
        subsecs = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
        state = _modified(state, _apply_item_insert(
            state, insert16_op, sec_path, sec_node, subsecs, amend_sub, muutos_ir,
            "5 § 1 mom 16 k"
        ))

        final_sec = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION)
        final_sub = next(c for c in final_sec.children if c.kind == IRNodeKind.SUBSECTION)
        final_paras = [c for c in final_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        labels = [p.label for p in final_paras]

        # Item 16 must be present as live (not a placeholder).
        idx16 = next((i for i, p in enumerate(final_paras) if p.label and p.label == "16"), None)
        assert idx16 is not None, f"Item 16 not found after insert, labels={labels}"
        assert final_paras[idx16].attrs.get("lawvm_repeal_placeholder") != "1", (
            "Item 16 should be live (not a placeholder) after INSERT"
        )

        # Items 17 and 18 must NOT have been shifted (cascade-renumber regression).
        idx17 = next((i for i, p in enumerate(final_paras) if p.label and p.label == "17"), None)
        idx18 = next((i for i, p in enumerate(final_paras) if p.label and p.label == "18"), None)
        assert idx17 is not None, f"Item 17 missing — cascade renumber? labels={labels}"
        assert idx18 is not None, f"Item 18 missing — cascade renumber? labels={labels}"
        # 17 and 18 must be consecutive and after 16.
        assert idx17 == idx16 + 1, (
            f"Item 17 not in expected position after insert; labels={labels}"
        )
        assert idx18 == idx17 + 1, f"Item 18 not consecutive after 17; labels={labels}"

        # Total para count: 14, placeholder-15, 16(new), 17, 18 = 5.
        assert len(final_paras) == 5, (
            f"Expected 5 paragraphs (no cascade), got {len(final_paras)}: {labels}"
        )


# ---------------------------------------------------------------------------
# _apply_item_replace
# ---------------------------------------------------------------------------


class TestApplyItemReplace:
    def _make_sec_with_items(self):
        para1 = _para("1", "first item")
        para2 = _para("2", "second item")
        sub1 = _sub("1", para1, para2)
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        return state, sec_path, sec

    def test_replace_item(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1, target_item="2")
        amend_sub = _sub("1", _para("2", "updated second item"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "1 § 1 mom 2 k")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        para2 = next(c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "2")
        text = " ".join(c.text or "" for c in para2.children)
        assert "updated second item" in text

    def test_replace_item_missing_in_nominal_subsection_does_not_mutate_sibling_subsection(self):
        sec = _sec(
            "1",
            _sub("1", _para("1", "first subsection item one")),
            _sub("2", _para("2", "second subsection item two")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1, target_item="2")
        amend_sub = _sub("1", _para("2", "replacement should not jump"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "1 § 1 mom 2 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub1 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
        sub2 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
        sub1_labels = [c.label for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH]
        sub2_labels = [c.label for c in sub2.children if c.kind == IRNodeKind.PARAGRAPH]
        para2_sub1 = next(c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "2")

        assert sub1_labels == ["1", "2"]
        assert sub2_labels == ["2"]
        assert "replacement should not jump" in " ".join(c.text or "" for c in para2_sub1.children)

    def test_replace_item_still_updates_nominal_subsection_when_item_exists_there(self):
        sec = _sec(
            "1",
            _sub("1", _para("1", "first subsection item one"), _para("2", "target item two")),
            _sub("2", _para("2", "second subsection item two")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1, target_item="2")
        amend_sub = _sub("1", _para("2", "updated nominal target item"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "1 § 1 mom 2 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub1 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
        sub2 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
        para2_sub1 = next(c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "2")
        para2_sub2 = next(c for c in sub2.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "2")
        assert "updated nominal target item" in " ".join(c.text or "" for c in para2_sub1.children)
        assert "second subsection item two" in " ".join(c.text or "" for c in para2_sub2.children)

    def test_replace_item_can_recover_as_local_numeric_insert_without_cross_subsection_retarget(self):
        sec = _sec(
            "1",
            _sub("1", _para("1", "item one"), _para("3", "item three")),
            _sub("2", _para("2", "other subsection item two")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1, target_item="2")
        amend_sub = _sub("1", _para("2", "inserted local item two"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "1 § 1 mom 2 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub1 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
        sub2 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
        sub1_labels = [c.label for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH]
        sub2_labels = [c.label for c in sub2.children if c.kind == IRNodeKind.PARAGRAPH]
        para2_sub1 = next(c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "2")

        assert sub1_labels == ["1", "2", "3"]
        assert sub2_labels == ["2"]
        assert "inserted local item two" in " ".join(c.text or "" for c in para2_sub1.children)

    def test_not_applicable_for_subsection_op(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, None, None, "1 § 1 mom")
        assert result is None

    def test_not_applicable_for_repeal(self):
        state, sec_path, sec = self._make_sec_with_items()
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=1, target_item="1")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, None, None, "1 § 1 mom 1 k")
        assert result is None

    def test_replace_strips_compound_label_subparagraph(self):
        # Provenance: 2006/603 section:2 — amendment 2014/1186 REPLACE(4)+INSERT(4a) duplication
        # The Finlex AKN body XML nests <subparagraph num="4 a)"> inside <paragraph num="4)">
        # when a sibling INSERT introduces item:4a.  REPLACE(4) must NOT carry that nested
        # subparagraph into the IR; INSERT(4a) will materialize it as a flat sibling.
        sp_4a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="4a",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="compound sub text"),),
        )
        intro_node = IRNode(kind=IRNodeKind.INTRO, text="item four text")
        amend_para_4 = IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(intro_node, sp_4a))
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para_4,))

        # Live state: section 2, subsection 1 has items 1–4 (plain)
        live_para4 = _para("4", "old item four")
        live_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                _para("2", "item two"),
                _para("3", "item three"),
                live_para4,
            ),
        )
        sec = _sec("2", live_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]

        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="4")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 4 k",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para4 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "4")

        # The compound-label subparagraph must not appear inside item:4
        assert not any(c.kind == IRNodeKind.SUBPARAGRAPH and c.label and c.label == "4a" for c in new_para4.children), (
            "compound-label subparagraph:4a must be stripped from REPLACE(4) payload"
        )
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "compound_label_subparagraph_strip"
        # The intro text is still present
        assert any(c.kind == IRNodeKind.INTRO and "item four text" in (c.text or "") for c in new_para4.children)

    def test_replace_strict_blocks_compound_label_subparagraph_strip(self):
        sp_4a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="4a",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="compound sub text"),),
        )
        intro_node = IRNode(kind=IRNodeKind.INTRO, text="item four text")
        amend_para_4 = IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(intro_node, sp_4a))
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para_4,))

        live_para4 = _para("4", "old item four")
        live_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                _para("2", "item two"),
                _para("3", "item three"),
                live_para4,
            ),
        )
        sec = _sec("2", live_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]

        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="4")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 4 k",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "compound_label_subparagraph_strip"

    def test_replace_preserves_pure_letter_subparagraphs(self):
        # Provenance: 2006/603 section:2 — amendment 2014/1186 REPLACE(4)+INSERT(4a) duplication
        # Pure letter subparagraphs (a, b, c) are genuine sub-enumerations that belong
        # inside the item; they must NOT be stripped by the compound-label guard.
        sp_a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub a"),)
        )
        sp_b = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub b"),)
        )
        intro_node = IRNode(kind=IRNodeKind.INTRO, text="item three intro")
        amend_para_3 = IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(intro_node, sp_a, sp_b))
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para_3,))

        live_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                _para("2", "item two"),
                _para("3", "old item three"),
            ),
        )
        sec = _sec("5", live_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "5")]

        op = _op(op_type="REPLACE", target_section="5", target_paragraph=1, target_item="3")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "5 § 1 mom 3 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para3 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "3")

        # Pure letter subparagraphs must be preserved
        sp_labels = {c.label for c in new_para3.children if c.kind == IRNodeKind.SUBPARAGRAPH}
        assert "a" in sp_labels, "pure-letter subparagraph 'a' must not be stripped"
        assert "b" in sp_labels, "pure-letter subparagraph 'b' must not be stripped"

    def test_replace_item_intro_preserves_existing_subparagraphs(self):
        """Provenance: 2017/252 §2 — amendment 2021/556 replaces item 1 intro ('kohdan
        johtolause') but the amendment payload has no subparagraphs.  The master item
        has subitems a–e which must be preserved."""
        sp_a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub a"),)
        )
        sp_b = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub b"),)
        )
        sp_c = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="c", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub c"),)
        )
        master_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.INTRO, text="old intro:"), sp_a, sp_b, sp_c),
        )
        master_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(master_para1,))
        sec = _sec("2", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]

        # Amendment payload: only a new intro, no subparagraphs
        amend_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.INTRO, text="new intro:"),)
        )
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para1,))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="1")
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "2 § 1 mom 1 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para1 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "1")

        # Intro text should be updated
        new_intro = next((c for c in new_para1.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)), None)
        assert new_intro is not None and "new intro" in (new_intro.text or ""), "Item intro should be updated"

        # Master subparagraphs a, b, c must be preserved
        sp_labels = sorted(c.label for c in new_para1.children if c.kind == IRNodeKind.SUBPARAGRAPH and c.label)
        assert sp_labels == ["a", "b", "c"], f"Subitems a–c must be preserved, got {sp_labels}"

    def test_replace_item_intro_uses_plain_body_fallback_when_amend_intro_missing(self):
        master_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.INTRO, text="old intro:"),),
        )
        master_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(master_para1,))
        sec = _sec("2", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="plain body"),)
        )
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para1,))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 1 k",
            source_pathologies_out=pathologies,
        )

        assert result is not None and result is not state
        assert pathologies == []
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH)
        assert [(c.kind, c.text) for c in new_para.children] == [(IRNodeKind.CONTENT, "plain body")]

    def test_replace_item_johd_plain_body_fallback_surfaces_structure_absent_pathology(self):
        master_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.INTRO, text="old intro:"),),
        )
        master_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(master_para1,))
        sec = _sec("2", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="plain body"),)
        )
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para1,))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="1", target_special="johd")
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 1 k johd",
            source_pathologies_out=pathologies,
        )

        assert result is not None and result is not state
        assert [p.code for p in pathologies] == ["ITEM_TARGET_STRUCTURE_ABSENT"]
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH)
        assert [(c.kind, c.text) for c in new_para.children] == [(IRNodeKind.CONTENT, "plain body")]

    def test_replace_item_reports_missing_exact_subsection_label_rebound(self):
        sec = _sec(
            "20",
            _sub("2", _para("1", "old item one")),
            _sub("3", _para("1", "later item one")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "20")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = _para("1", "new item one")
        amend_sub = _sub("1", amend_para1)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="20", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "20 § 1 mom 1 k",
            source_pathologies_out=pathologies,
        )

        assert result is not None and result is not state
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub2 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
        new_para = next(c for c in new_sub2.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "1")
        assert irnode_to_text(new_para) == "new item one"

    def test_replace_item_strict_blocks_missing_exact_subsection_label_rebound(self):
        sec = _sec(
            "20",
            _sub("2", _para("1", "old item one")),
            _sub("3", _para("1", "later item one")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "20")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = _para("1", "new item one")
        amend_sub = _sub("1", amend_para1)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="20", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "20 § 1 mom 1 k",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"

    def test_insert_item_reports_missing_exact_subsection_label_rebound(self):
        sec = _sec(
            "20",
            _sub("2", _para("1", "old item one")),
            _sub("3", _para("1", "later item one")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "20")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = _para("1", "new item one")
        amend_sub = _sub("1", amend_para1)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="INSERT", target_section="20", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "20 § 1 mom 1 k insert",
            source_pathologies_out=pathologies,
        )

        assert result is not None and result is not state
        assert any(p.code == "SUBSECTION_TARGET_REBOUND" for p in pathologies)
        rebound = next(p for p in pathologies if p.code == "SUBSECTION_TARGET_REBOUND")
        assert rebound.detail["rebound_kind"] == "missing_exact_subsection_label"

    def test_insert_item_strict_blocks_missing_exact_subsection_label_rebound(self):
        sec = _sec(
            "20",
            _sub("2", _para("1", "old item one")),
            _sub("3", _para("1", "later item one")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "20")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        amend_para1 = _para("1", "new item one")
        amend_sub = _sub("1", amend_para1)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="INSERT", target_section="20", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "20 § 1 mom 1 k insert",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert any(p.code == "SUBSECTION_TARGET_REBOUND" for p in pathologies)
        rebound = next(p for p in pathologies if p.code == "SUBSECTION_TARGET_REBOUND")
        assert rebound.detail["rebound_kind"] == "missing_exact_subsection_label"

    def test_repeal_item_strict_blocks_missing_exact_subsection_label_rebound(self):
        sec = _sec(
            "20",
            _sub("2", _para("1", "old item one")),
            _sub("3", _para("1", "later item one")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "20")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        op = _op(op_type="REPEAL", target_section="20", target_paragraph=1, target_item="1")
        pathologies: list[SourcePathology] = []

        result = _apply_item_repeal(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            _LEGAL_PIT,
            "20 § 1 mom 1 k repeal",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"

    def test_replace_plain_item_drops_obsolete_master_subparagraphs(self):
        """Provenance: 1987/990 §2 — later plain-item REPLACE must not preserve
        stale subparagraphs from an earlier list shape."""
        sp_a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub a"),)
        )
        sp_b = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub b"),)
        )
        sp_c = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="c", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub c"),)
        )
        master_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="old item four"), sp_a, sp_b, sp_c),
        )
        master_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("3", "item three"),
                master_para4,
                _para("5", "item five"),
            ),
        )
        sec = _sec("2", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]

        amend_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="new plain item four"),),
        )
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(amend_para4,))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        op = _op(op_type="REPLACE", target_section="2", target_paragraph=1, target_item="4")
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "2 § 1 mom 4 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para4 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "4")

        assert not any(c.kind == IRNodeKind.SUBPARAGRAPH for c in new_para4.children)
        assert any(c.kind == IRNodeKind.CONTENT and "new plain item four" in (c.text or "") for c in new_para4.children)

    def test_replace_plain_item_with_omission_context_drops_obsolete_master_subparagraphs(self):
        """Omission elsewhere in the subsection must not preserve stale child subitems."""
        sp_a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub a"),)
        )
        sp_b = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub b"),)
        )
        sp_c = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="c", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub c"),)
        )
        master_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="old item seven"), sp_a, sp_b, sp_c),
        )
        master_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                master_para7,
            ),
        )
        sec = _sec("15", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "15")]

        amend_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="new plain item seven"),),
        )
        amend_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(IRNode(kind=IRNodeKind.OMISSION), amend_para7),
        )
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        op = _op(op_type="REPLACE", target_section="15", target_paragraph=1, target_item="7")
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "15 § 1 mom 7 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para7 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "7")

        assert not any(c.kind == IRNodeKind.SUBPARAGRAPH for c in new_para7.children)
        assert any(c.kind == IRNodeKind.CONTENT and "new plain item seven" in (c.text or "") for c in new_para7.children)

    def test_merge_section_with_omission_preserves_carried_tail_subsection_until_explicit_item_prune(self):
        """Section omission merge must not delete a subsection on text coincidence."""

        tail_text = (
            "ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet "
            "eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa."
        )

        def _para_with_tail(label: str, text: str, tail: IRNode | None = None) -> IRNode:
            children = [IRNode(kind=IRNodeKind.NUM, text=f"{label})"), IRNode(kind=IRNodeKind.CONTENT, text=text)]
            if tail is not None:
                children.append(tail)
            return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=tuple(children))

        def _content_sub(label: str, text: str) -> IRNode:
            return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=(IRNode(kind=IRNodeKind.CONTENT, text=text),))

        tail_sub = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.CONTENT, text=tail_text),),
        )

        master = _sec(
            "21",
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    _para_with_tail("1", "one"),
                    _para_with_tail("7", "seven old"),
                ),
            ),
            _content_sub("2", tail_text),
            _content_sub("3", "m3"),
            _content_sub("4", "m4"),
            _content_sub("5", "m5"),
        )
        amend = _sec(
            "21",
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    _para_with_tail("1", "one new"),
                    _para_with_tail("7", "seven old", tail_sub),
                ),
            ),
            _content_sub("2", tail_text),
            IRNode(kind=IRNodeKind.OMISSION),
        )

        merged = _merge_section_with_omission_ir(master, amend)
        assert merged is not None

        subsections = [child for child in merged.children if child.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in subsections] == ["1", "2", "3", "4", "5"]

        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        assert [child.label for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH] == ["1"]

    def test_sparse_item_insert_prunes_duplicate_tail_subsection(self):
        """Provenance: 1987/990 §21 — item 7 insert must consume the carried
        tail sentence instead of leaving it as a separate subsection 2."""

        tail_text = (
            "ydinenergian käyttö muutoinkin täyttää 5―7 §:ssä säädetyt periaatteet "
            "eikä ole ristiriidassa Euratom-sopimuksen velvoitteiden kanssa."
        )

        tail_sub = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.CONTENT, text=tail_text),),
        )
        master = _sec(
            "21",
            _sub(
                "1",
                _para("1", "one"),
                _para("7", "seven old"),
            ),
            _sub("2", _content(tail_text)),
            _sub("3", _content("m3")),
            _sub("4", _content("m4")),
            _sub("5", _content("m5")),
        )
        amend = _sec(
            "21",
            _sub(
                "1",
                IRNode(kind=IRNodeKind.OMISSION),
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.CONTENT, text="seven old"),
                        tail_sub,
                    ),
                ),
                IRNode(kind=IRNodeKind.OMISSION),
            ),
        )

        state = _make_state(_body(master))
        sec = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        op = _op(op_type="INSERT", target_section="21", target_paragraph=1, target_item="7")
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            (("section", "21"),),
            sec,
            subsecs,
            amend.children[0],
            amend,
            "21 § 1 mom 7 k",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        subsections = [child for child in new_sec.children if child.kind == IRNodeKind.SUBSECTION]
        assert len(subsections) == 4
        assert all(
            tail_text
            not in " | ".join(
                (child.text or "") for child in sub.children if child.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)
            )
            for sub in subsections
        )

        seventh_para = next(
            child for child in subsections[0].children if child.kind == IRNodeKind.PARAGRAPH and child.label == "7"
        )
        assert [child.label for child in seventh_para.children if child.kind == IRNodeKind.SUBPARAGRAPH] == ["1"]
        assert [p.code for p in pathologies] == [
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
        ]
        assert [p.detail["recovery_kind"] for p in pathologies] == [
            "item_insert_suffix_renumber",
            "absorbed_tail_subsection_collapse",
        ]

    def test_item_insert_preserves_tail_subsection_without_collapse(self):
        tail_text = "absorbed carried tail text."

        master = _sec(
            "21",
            _sub("1", _para("1", "one"), _para("2", "two")),
            _sub("2", _content(tail_text)),
            _sub("3", _content("later tail subsection")),
        )
        amend = _sec(
            "21",
            _sub(
                "1",
                IRNode(kind=IRNodeKind.OMISSION),
                _para("3", "three"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, children=(_content(tail_text),)),
                IRNode(kind=IRNodeKind.OMISSION),
            ),
        )

        state = _make_state(_body(master))
        sec = next(c for c in state.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        op = _op(op_type="INSERT", target_section="21", target_paragraph=1, target_item="3")
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            (("section", "21"),),
            sec,
            subsecs,
            amend.children[0],
            amend,
            "21 § 1 mom 3 k",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        subsections = [child for child in new_sec.children if child.kind == IRNodeKind.SUBSECTION]
        assert [child.label for child in subsections] == ["1", "2", "3"]
        assert pathologies == []

    def test_replace_sparse_item_preserves_existing_subparagraphs_when_payload_has_omission(self):
        """Sparse REPLACE with omission must keep carried tail subparagraphs."""
        tail_sub = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="tail sentence"),),
        )
        master_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="old item seven"), tail_sub),
        )
        master_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                master_para7,
            ),
        )
        sec = _sec("21", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "21")]

        amend_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="new item seven"),),
        )
        amend_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.OMISSION), amend_para7))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        op = _op(op_type="REPLACE", target_section="21", target_paragraph=1, target_item="7")
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "21 § 1 mom 7 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para7 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "7")
        sp_labels = [c.label for c in new_para7.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        assert sp_labels == ["1"]

    def test_johd_item_replace_preserves_subparagraphs(self):
        """When target_special='johd' and target_item is set, replace only
        the item's intro while keeping existing subparagraphs."""
        sp_a = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub a"),)
        )
        sp_b = IRNode(
            kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(IRNode(kind=IRNodeKind.CONTENT, text="sub b"),)
        )
        master_para1 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            children=(IRNode(kind=IRNodeKind.INTRO, text="old intro:"), sp_a, sp_b),
        )
        master_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(master_para1,))
        sec = _sec("2", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]

        # Amendment provides only a new intro
        amend_sub = IRNode(
            kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.INTRO, text="replaced intro:"),)
        )
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        op = _op(
            op_type="REPLACE",
            target_section="2",
            target_paragraph=1,
            target_item="1",
            target_special="johd",
        )
        result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "2 § 1 mom 1 k johd")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        new_para1 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "1")

        # Intro text should be updated
        new_intro = next((c for c in new_para1.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)), None)
        assert new_intro is not None and "replaced intro" in (new_intro.text or ""), "Item intro should be replaced"

        # Original subparagraphs must be preserved
        sp_labels = sorted(c.label for c in new_para1.children if c.kind == IRNodeKind.SUBPARAGRAPH and c.label)
        assert sp_labels == ["a", "b"], f"Subitems must be preserved, got {sp_labels}"

    def test_sparse_item_replace_updates_real_section_70_kohta_targets(self):
        """Sparse item replaces must update the targeted kohdat in place."""
        xml_bytes = get_corpus_store().read_source("2019/1468")
        assert xml_bytes is not None
        root = etree.fromstring(xml_bytes)
        johto = get_johtolause(xml_bytes)
        replay = pinned_replay("2006/395", stop_before="2019/1468", mode="finlex_oracle", quiet=True)
        state = replay.replay_fold_state
        phase = normalize_and_compile_ops(johto, root, state, "2019/1468", "", False, parent_id="2006/395")
        path = state.find_section_path("70", None, "2")
        assert path is not None
        sec = state.resolve(path)
        assert sec is not None
        amend_sec = root.find('.//{*}section[{*}num="70 §"]')
        assert amend_sec is not None
        amend_sub = amend_sec.findall("{*}subsection")[1]
        amend_sec_ir = fi_xml_to_ir_node(amend_sec)
        amend_sub_ir = fi_xml_to_ir_node(amend_sub)

        item_state = state
        for op in [o for o in phase.output if o.target_section == "70" and o.target_item in {"4", "5", "12"}]:
            current_sec = item_state.resolve(path)
            assert current_sec is not None
            subsecs = [c for c in current_sec.children if c.kind == IRNodeKind.SUBSECTION]
            next_state = _apply_item_replace(
                item_state,
                op,
                path,
                current_sec,
                subsecs,
                amend_sub_ir,
                amend_sec_ir,
                f"70 § 3 mom {op.target_item} k",
            )
            item_state = _modified(item_state, next_state)

        new_sec = item_state.resolve(path)
        assert new_sec is not None
        sub = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION][2]
        paras = {c.label: irnode_to_text(c) for c in sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label}
        amend_paras = {
            c.label: irnode_to_text(c)
            for c in amend_sub_ir.children
            if c.kind == IRNodeKind.PARAGRAPH and c.label
        }
        assert paras["4"] == amend_paras["4"]
        assert paras["5"] == amend_paras["5"]
        assert paras["12"] == amend_paras["12"]
        assert "66 §:ssä tarkoitettua työsuhdeoption" not in paras["4"]

    def test_sparse_item_replace_updates_real_section_123_kohta_targets(self):
        """Sparse item replaces must update the targeted kohdat in place."""
        xml_bytes = get_corpus_store().read_source("2022/572")
        assert xml_bytes is not None
        root = etree.fromstring(xml_bytes)
        johto = get_johtolause(xml_bytes)
        replay = pinned_replay("2006/395", stop_before="2022/572", mode="finlex_oracle", quiet=True)
        state = replay.replay_fold_state
        phase = normalize_and_compile_ops(johto, root, state, "2022/572", "", False, parent_id="2006/395")
        path = state.find_section_path("123", None, "2")
        assert path is not None
        sec = state.resolve(path)
        assert sec is not None
        amend_sec = root.find('.//{*}section[{*}num="123 §"]')
        assert amend_sec is not None
        amend_sub = amend_sec.find("{*}subsection")
        assert amend_sub is not None
        amend_sec_ir = fi_xml_to_ir_node(amend_sec)
        amend_sub_ir = fi_xml_to_ir_node(amend_sub)

        item_state = state
        for op in [o for o in phase.output if o.target_section == "123" and o.target_item in {"8", "9", "15"}]:
            current_sec = item_state.resolve(path)
            assert current_sec is not None
            subsecs = [c for c in current_sec.children if c.kind == IRNodeKind.SUBSECTION]
            next_state = _apply_item_replace(
                item_state,
                op,
                path,
                current_sec,
                subsecs,
                amend_sub_ir,
                amend_sec_ir,
                f"123 § 1 mom {op.target_item} k",
            )
            item_state = _modified(item_state, next_state)

        new_sec = item_state.resolve(path)
        assert new_sec is not None
        sub = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION][0]
        paras = {c.label: irnode_to_text(c) for c in sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label}
        amend_paras = {
            c.label: irnode_to_text(c)
            for c in amend_sub_ir.children
            if c.kind == IRNodeKind.PARAGRAPH and c.label
        }
        assert paras["8"] == amend_paras["8"]
        assert paras["9"] == amend_paras["9"]
        assert paras["15"] == amend_paras["15"]
        assert "sosiaalihuoltolain mukaiselle toimielimelle" not in paras["8"]

    def test_sparse_section_compile_keeps_plain_moment_and_insert_targets(self):
        """Compile must retain the section-level targets that later replay applies."""
        cases = [
            (
                "2019/1468",
                "70",
                [
                    "REPLACE 70 § 2 mom",
                    "REPLACE 70 § 3 mom 4 kohta",
                    "REPLACE 70 § 3 mom 5 kohta",
                    "REPLACE 70 § 3 mom 12 kohta",
                ],
            ),
            (
                "2022/572",
                "123",
                [
                    "REPLACE 123 § johd",
                    "REPLACE 123 § 1 mom 8 kohta",
                    "REPLACE 123 § 1 mom 9 kohta",
                    "REPLACE 123 § 1 mom 15 kohta",
                    "INSERT 8 luku 123 § 2 mom",
                ],
            ),
        ]

        for amendment_id, section_num, expected_descriptions in cases:
            xml_bytes = get_corpus_store().read_source(amendment_id)
            assert xml_bytes is not None
            root = etree.fromstring(xml_bytes)
            johto = get_johtolause(xml_bytes)
            replay = pinned_replay("2006/395", stop_before=amendment_id, mode="finlex_oracle", quiet=True)
            phase = normalize_and_compile_ops(
                johto,
                root,
                replay.replay_fold_state,
                amendment_id,
                "",
                False,
                parent_id="2006/395",
            )
            descriptions = [op.description() for op in phase.output if op.target_section == section_num]
            for expected in expected_descriptions:
                assert expected in descriptions


# ---------------------------------------------------------------------------
# _apply_item_insert
# ---------------------------------------------------------------------------


class TestApplyItemInsert:
    def test_insert_item_after_predecessor(self):
        para1 = _para("1", "first item")
        para2 = _para("2", "second item")
        sub1 = _sub("1", para1, para2)
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="3")
        new_para = _para("3", "new third item")
        amend_sub = _sub("1", new_para)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_insert(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "1 § 1 mom ins 3 k")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(paras) == 3

    def test_insert_strict_blocks_sparse_alakohta_insert_merge(self):
        master_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(
                IRNode(kind=IRNodeKind.CONTENT, text="old item seven"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="tail"),)),
            ),
        )
        master_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                master_para7,
            ),
        )
        sec = _sec("21", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "21")]

        amend_para7 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="7",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="new item seven"),),
        )
        sparse_letter = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="a",
            children=(IRNode(kind=IRNodeKind.CONTENT, text="new sparse subitem a"),),
        )
        amend_sub = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(IRNode(kind=IRNodeKind.OMISSION), amend_para7, sparse_letter),
        )
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        op = _op(op_type="INSERT", target_section="21", target_paragraph=1, target_item="7")
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "21 § 1 mom 7 k insert",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "sparse_alakohta_insert_merge"

    def test_insert_item_on_intro_list_shape_emits_rebound_pathology(self) -> None:
        sec = _sec(
            "1",
            _sub("1", _intro("intro text:")),
            _sub("2", _para("1", "first item"), _para("2", "second item")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="3")
        amend_sub = _sub("1", _para("3", "new third item"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "1 § 1 mom ins 3 k",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION][1]
        paras = [c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras] == ["1", "2", "3"]
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "intro_list_moment_shape"

    def test_insert_item_on_intro_list_shape_is_rejected_in_strict_mode(self) -> None:
        sec = _sec(
            "1",
            _sub("1", _intro("intro text:")),
            _sub("2", _para("1", "first item"), _para("2", "second item")),
        )
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="3")
        amend_sub = _sub("1", _para("3", "new third item"))
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "1 § 1 mom ins 3 k",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
        assert pathologies[0].detail["rebound_kind"] == "intro_list_moment_shape"

    def test_insert_item_renumbers_existing_numeric_suffixes(self):
        para1 = _para("1", "first item")
        para2 = _para("2", "second item")
        para3 = _para("3", "third item")
        sub1 = _sub("1", para1, para2, para3)
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="2")
        amend_para = _para("2", "inserted second item")
        amend_sub = _sub("1", amend_para)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "1 § 1 mom 2 k insert",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras] == ["1", "2", "3", "4"]
        assert "inserted second item" in irnode_to_text(paras[1])
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "item_insert_suffix_renumber"

    def test_insert_strict_blocks_item_insert_suffix_renumber(self):
        para1 = _para("1", "first item")
        para2 = _para("2", "second item")
        para3 = _para("3", "third item")
        sub1 = _sub("1", para1, para2, para3)
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="2")
        amend_para = _para("2", "inserted second item")
        amend_sub = _sub("1", amend_para)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "1 § 1 mom 2 k insert",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "item_insert_suffix_renumber"

    def test_insert_compound_item_can_append_subparagraph_recovery(self):
        master_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.INTRO, text="item four"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("sub a"),)),
            ),
        )
        sec = _sec("2", _sub("1", _para("1", "item one"), master_para4))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]
        op = _op(op_type="INSERT", target_section="2", target_paragraph=1, target_item="4b")
        amend_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.INTRO, text="item four"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(_content("sub b"),)),
            ),
        )
        amend_sub = _sub("1", amend_para4)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 4b k insert",
            source_pathologies_out=pathologies,
        )
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        para4 = next(c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "4")
        sp_labels = [c.label for c in para4.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        assert sp_labels == ["a", "b"]
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "compound_item_insert_append"

    def test_insert_strict_blocks_compound_item_append_recovery(self):
        master_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.INTRO, text="item four"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("sub a"),)),
            ),
        )
        sec = _sec("2", _sub("1", _para("1", "item one"), master_para4))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "2")]
        op = _op(op_type="INSERT", target_section="2", target_paragraph=1, target_item="4b")
        amend_para4 = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.INTRO, text="item four"),
                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="b", children=(_content("sub b"),)),
            ),
        )
        amend_sub = _sub("1", amend_para4)
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        result = _apply_item_insert(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            "2 § 1 mom 4b k insert",
            source_pathologies_out=pathologies,
            strict_profile=default_finland_strict_profile(),
        )

        assert result is None
        assert len(pathologies) == 1
        assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        assert pathologies[0].detail["recovery_kind"] == "compound_item_insert_append"

    def test_not_applicable_for_subsection_op(self):
        sec = _sec("1", _sub("1", _content("text")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_paragraph=2)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_insert(state, op, sec_path, sec, subsecs, None, None, "1 § ins 2 mom")
        assert result is None

    def test_sparse_item_insert_uses_resolved_op_effective_target_fields(self):
        master_sub = _sub("1", _para("1", "item one"), _para("3", "item three"))
        sec = _sec("21", master_sub)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "21")]

        amend_sub = _sub(
            "1",
            IRNode(kind=IRNodeKind.OMISSION),
            _para("2", "new item two"),
            IRNode(kind=IRNodeKind.OMISSION),
        )
        muutos_ir = _sec("21", amend_sub)
        raw_op = _op(op_type="INSERT", target_section="21", target_paragraph=1, target_item="2")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_item_insert(state, raw_op, sec_path, sec, subsecs, amend_sub, muutos_ir, "21 § 1 mom 2 k")
        result = _modified(state, result)

        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "21")
        new_sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in new_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert [c.label for c in paras] == ["1", "2", "3"]
        assert "new item two" in irnode_to_text(next(c for c in paras if c.label == "2"))


# ---------------------------------------------------------------------------
# _apply_special_targets
# ---------------------------------------------------------------------------


class TestApplySpecialTargets:
    def test_heading_replace(self):
        heading = IRNode(kind=IRNodeKind.HEADING, text="Old heading")
        sub1 = _sub("1", _content("text"))
        sec = _sec("1", heading, sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="REPLACE", target_section="1", target_special="otsikko")
        new_heading = IRNode(kind=IRNodeKind.HEADING, text="New heading")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(new_heading,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, muutos_ir, "1 § otsikko")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        h = next((c for c in new_sec.children if c.kind == IRNodeKind.HEADING), None)
        assert h is not None
        assert h.text == "New heading"

    def test_heading_repeal(self):
        heading = IRNode(kind=IRNodeKind.HEADING, text="Old heading")
        sub1 = _sub("1", _content("text"))
        sec = _sec("1", heading, sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="REPEAL", target_section="1", target_special="otsikko")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, None, "1 § otsikko")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        assert all(c.kind != IRNodeKind.HEADING for c in new_sec.children)

    def test_heading_repeal_noops_without_heading(self):
        sec = _sec("1", _sub("1", _content("text")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="REPEAL", target_section="1", target_special="otsikko")
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, None, "1 § otsikko")
        assert _unchanged(state, result)

    def test_heading_insert_adds_heading_to_headingless_section(self):
        """INSERT otsikko adds heading to a section that has none."""
        sub1 = _sub("1", _content("text"))
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_special="otsikko")
        new_heading = IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(new_heading,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, muutos_ir, "1 § otsikko")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        h = next((c for c in new_sec.children if c.kind == IRNodeKind.HEADING), None)
        assert h is not None
        assert h.text == "Voimaantulo"
        # Heading should be before subsections
        heading_idx = next(i for i, c in enumerate(new_sec.children) if c.kind == IRNodeKind.HEADING)
        sub_idx = next(i for i, c in enumerate(new_sec.children) if c.kind == IRNodeKind.SUBSECTION)
        assert heading_idx < sub_idx

    def test_heading_insert_replaces_when_section_already_has_heading(self):
        """INSERT otsikko on a section that already has a heading acts as upsert."""
        old_heading = IRNode(kind=IRNodeKind.HEADING, text="Old")
        sub1 = _sub("1", _content("text"))
        sec = _sec("1", old_heading, sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="INSERT", target_section="1", target_special="otsikko")
        new_heading = IRNode(kind=IRNodeKind.HEADING, text="New")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(new_heading,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, muutos_ir, "1 § otsikko")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        headings = [c for c in new_sec.children if c.kind == IRNodeKind.HEADING]
        assert len(headings) == 1
        assert headings[0].text == "New"

    def test_heading_replace_adds_heading_when_none_exists(self):
        """REPLACE otsikko on a section without heading acts as upsert (insert)."""
        sub1 = _sub("1", _content("text"))
        sec = _sec("1", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="REPLACE", target_section="1", target_special="otsikko")
        new_heading = IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(new_heading,))
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, muutos_ir, "1 § otsikko")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        h = next((c for c in new_sec.children if c.kind == IRNodeKind.HEADING), None)
        assert h is not None
        assert h.text == "Voimaantulo"

    def test_not_applicable_for_regular_op(self):
        sec = _sec("1", _sub("1", _content("text")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "1")]
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        result = _apply_special_targets(state, op, sec_path, sec, subsecs, None, None, "1 § 1 mom")
        assert result is None


# ---------------------------------------------------------------------------
# Integration: _apply_deterministic_subsection_op dispatch
# ---------------------------------------------------------------------------


class TestDispatchIntegration:
    """Verify that the dispatcher correctly routes to sub-functions."""

    def test_repeal_subsection_via_dispatch(self):
        sec = _sec("1", _sub("1", _content("first")), _sub("2", _content("second")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=2)
        result = _apply_deterministic_subsection_op(state, op, sec_path, None, None, None, _LEGAL_PIT, "1 § 2 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        assert len([c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]) == 1

    def test_replace_subsection_via_dispatch(self):
        sec = _sec("1", _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        result = _apply_deterministic_subsection_op(state, op, sec_path, None, amend_sub, None, _LEGAL_PIT, "1 § 1 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        text = " ".join(c.text or "" for c in sub.children)
        assert "replacement" in text

    def test_typed_chapter_scoped_heading_insert_does_not_fallback_to_root_section(self):
        sec = _sec("22", _sub("1", _content("Tämä laki tulee voimaan.")))
        chapter = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Erinäisiä säännöksiä"),
            ),
        )
        state = _make_state(_body(chapter, sec))
        op = _op(op_type="INSERT", target_section="22", target_chapter="4", target_special="otsikko")
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=_sec("22", IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo")),
            cross_ir=None,
            target_unit_kind="section",
            target_norm="22",
            target_chapter="4",
        )

        resolution = _resolve_section_path_with_fallbacks(
            state,
            rop,
            rop.muutos_ir,
            None,
            "4 luku 22 § otsikko",
        )

        assert resolution.path is None
        assert resolution.reason_code is None

    def test_typed_chapter_scoped_heading_relabel_rejects_root_level_unique_global_fallback(self):
        sec = _sec("22", _sub("1", _content("Tämä laki tulee voimaan.")))
        state = _make_state(_body(sec))
        op = _op(op_type="INSERT", target_section="22", target_chapter="4", target_special="otsikko")
        op.scope_provenance_tags = ("chapter_scope_carry_forward",)
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=_sec("22", IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo")),
            cross_ir=None,
            target_unit_kind="section",
            target_norm="22",
            target_chapter="4",
        )
        rop._op_type_seed = "REPLACE"

        resolution = _resolve_section_path_with_fallbacks(
            state,
            rop,
            rop.muutos_ir,
            None,
            "4 luku 22 § otsikko",
        )

        assert resolution.path is None
        assert resolution.reason_code is None

    def test_heading_insert_via_dispatch_does_not_rehome_root_section_into_target_chapter(self):
        chapter = IRNode(
            kind=IRNodeKind.CHAPTER,
            label="4",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                IRNode(kind=IRNodeKind.HEADING, text="Erinäisiä säännöksiä"),
            ),
        )
        sec = _sec("22", _sub("1", _content("Tämä laki tulee voimaan.")))
        state = _make_state(_body(chapter, sec))
        sec_path = (("section", "22"),)
        op = _op(op_type="INSERT", target_section="22", target_chapter="4", target_special="otsikko")
        muutos_ir = _sec("22", IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"))

        result = _apply_deterministic_subsection_op(
            state,
            op,
            sec_path,
            muutos_ir,
            None,
            None,
            _LEGAL_PIT,
            "4 luku 22 § otsikko",
        )
        result = _modified(state, result)

        def _section_paths(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> list[tuple[tuple[str, str], ...]]:
            found: list[tuple[tuple[str, str], ...]] = []
            if node.kind == IRNodeKind.SECTION and node.label == "22":
                found.append(path)
            for child in node.children:
                found.extend(_section_paths(child, path + ((child.kind.value, child.label or ""),)))
            return found

        assert _section_paths(result.ir) == [(("section", "22"),)]
        moved = result.find_section("22")
        assert moved is not None
        assert result.find_section("22", "4") is None
        moved_text = irnode_to_text(moved)
        assert "Voimaantulo" in moved_text
        assert "Tämä laki tulee voimaan." in moved_text

    def test_insert_item_via_dispatch(self):
        para1 = _para("1", "first")
        para2 = _para("2", "second")
        sec = _sec("1", _sub("1", para1, para2))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="INSERT", target_section="1", target_paragraph=1, target_item="3")
        new_para = _para("3", "third")
        amend_sub = _sub("1", new_para)
        result = _apply_deterministic_subsection_op(
            state, op, sec_path, None, amend_sub, None, _LEGAL_PIT, "1 § 1 mom ins 3 k"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert len(paras) == 3

    def test_heading_replace_via_dispatch_skips_temporary_merge_base_retrogression(self):
        base_section = _sec(
            "3",
            IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
            _sub("1", _content("base 1")),
            _sub("2", _content("base 2")),
        )
        temp_section = _sec(
            "3",
            IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
            _sub("1", _content("temp 1")),
            _sub("2", _content("temp 2")),
        )
        current_live_section = _sec(
            "3",
            IRNode(kind=IRNodeKind.HEADING, text="Vanha otsikko"),
            _sub("1", _content("temp 1")),
            _sub("2", _content("new 2")),
        )
        state = _make_state(_body(current_live_section))
        sec_path = (("section", "3"),)
        replay_history = [
            LegalOperation(
                op_id="snapshot_section_3",
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=sec_path),
                payload=base_section,
                source=OperationSource(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    expires="",
                    statute_id="2018/522",
                ),
            ),
            LegalOperation(
                op_id="snapshot_section_3",
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=sec_path),
                payload=temp_section,
                source=OperationSource(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    expires="2019-12-31",
                    statute_id="2018/523",
                ),
            ),
        ]
        op = _op(
            op_type="REPLACE",
            target_section="3",
            target_special="otsikko",
        )
        muutos_ir = _sec("3", IRNode(kind=IRNodeKind.HEADING, text="Uusi otsikko"))

        result = _apply_deterministic_subsection_op(
            state,
            op,
            sec_path,
            muutos_ir,
            None,
            None,
            _LEGAL_PIT,
            "3 § otsikko",
            replay_history_ops=replay_history,
            base_ir=_body(base_section),
        )

        result = _modified(state, result)
        section = result.find_section("3")
        assert section is not None
        heading = next(child for child in section.children if child.kind is IRNodeKind.HEADING)
        subsection_two = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2"
        )

        assert heading.text == "Uusi otsikko"
        assert "new 2" in irnode_to_text(subsection_two)
        assert "base 2" not in irnode_to_text(subsection_two)

    def test_replace_subsection_via_dispatch_prefers_slot_assignment(self):
        sec = _sec("1", _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement from slot assignment"))
        assignment = SubsectionSlotAssignmentResult(
            subsec_map=SubsectionSlotMap({id(op): amend_sub}),
            sparse_slot_bindings=(
                SparsePayloadSlotBinding(
                    op_description=op.description(),
                    op_type=str(op.op_type or ""),
                    target_paragraph=op.target_paragraph,
                    target_item=None,
                    target_special=None,
                    payload_slot_index=1,
                    payload_slot_label="1",
                )
            ,),
            used_subs=(0,),
            unassigned_payload_slots=(),
        )
        result = _apply_deterministic_subsection_op(
            state, op, sec_path, None, None, assignment, _LEGAL_PIT, "1 § 1 mom"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        text = " ".join(c.text or "" for c in sub.children)
        assert "replacement from slot assignment" in text

    def test_dispatch_with_resolved_op_prefers_late_waist_granularity_over_stale_op(self):
        from lawvm.core.canonical_intent import (
            ExecutionContract,
            IntentKind,
            NodeTarget,
            OccupancyPolicy,
            Repeal,
        )

        sec = _sec("1", _sub("1", _content("first")), _sub("2", _content("second")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPEAL", target_section="1", target_paragraph=99)
        target_address = LegalAddress(path=(("section", "1"), ("subsection", "2")))
        intent = Repeal(
            kind=IntentKind.REPEAL,
            target=NodeTarget(address=target_address),
            contract=ExecutionContract(occupancy=OccupancyPolicy.repeal_to_tombstone()),
        )
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=None,
            cross_ir=None,
            target_unit_kind="section",
            target_norm="1",
            target_chapter=None,
            target_address=target_address,
        )
        rop.intent = intent

        normalized_op = dc_replace(op, target_paragraph=2, target_item=None, target_special=None)

        result = _apply_deterministic_subsection_op(
            state, normalized_op, sec_path, None, None, None, _LEGAL_PIT, "1 § 2 mom"
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
        assert [c.label for c in subsecs] == ["1"]

    def test_dispatch_keeps_caller_normalized_op(self):
        sec = _sec("1", _sub("1", _para("1", "first"), _para("2", "second")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        raw_op = _op(op_type="REPEAL", target_section="1", target_paragraph=2)
        normalized_op = dc_replace(raw_op, target_paragraph=1, target_item="2")

        result = _apply_deterministic_subsection_op(
            state, normalized_op, sec_path, None, None, None, _LEGAL_PIT, "1 § 1 mom 2 kohta"
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        assert [c.label for c in paras] == ["1"]

    def test_subsection_replace_updates_section_heading(self):
        """When a whole-section amendment dispatched through subsection path
        carries a new heading in muutos_ir, the section heading is updated."""
        heading_old = IRNode(kind=IRNodeKind.HEADING, text="Loma ja isyysvapaa")
        sec = _sec("44", heading_old, _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "44"),)
        op = _op(op_type="REPLACE", target_section="44", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        heading_new = IRNode(kind=IRNodeKind.HEADING, text="Loma ja vanhempainvapaa")
        muutos_ir = _sec("44", heading_new, _sub("1", _content("replacement")))
        result = _apply_deterministic_subsection_op(
            state, op, sec_path, muutos_ir, amend_sub, None, _LEGAL_PIT, "44 § 1 mom"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_heading = next(c for c in new_sec.children if c.kind == IRNodeKind.HEADING)
        assert new_heading.text == "Loma ja vanhempainvapaa"

    def test_subsection_replace_preserves_same_heading(self):
        """When muutos_ir heading matches existing section heading, no extra
        state transition occurs."""
        heading = IRNode(kind=IRNodeKind.HEADING, text="Same heading")
        sec = _sec("1", heading, _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        muutos_ir = _sec("1", IRNode(kind=IRNodeKind.HEADING, text="Same heading"), amend_sub)
        result = _apply_deterministic_subsection_op(
            state, op, sec_path, muutos_ir, amend_sub, None, _LEGAL_PIT, "1 § 1 mom"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_heading = next(c for c in new_sec.children if c.kind == IRNodeKind.HEADING)
        assert new_heading.text == "Same heading"

    def test_subsection_replace_adds_heading_when_section_had_none(self):
        """When section had no heading but muutos_ir carries one, it is added."""
        sec = _sec("1", _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        heading_new = IRNode(kind=IRNodeKind.HEADING, text="New heading")
        muutos_ir = _sec("1", heading_new, amend_sub)
        result = _apply_deterministic_subsection_op(
            state, op, sec_path, muutos_ir, amend_sub, None, _LEGAL_PIT, "1 § 1 mom"
        )
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        headings = [c for c in new_sec.children if c.kind == IRNodeKind.HEADING]
        assert len(headings) == 1
        assert headings[0].text == "New heading"

    def test_subsection_replace_no_heading_update_without_muutos_ir(self):
        """Without muutos_ir, heading is left unchanged."""
        heading = IRNode(kind=IRNodeKind.HEADING, text="Original")
        sec = _sec("1", heading, _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "1"),)
        op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        result = _apply_deterministic_subsection_op(state, op, sec_path, None, amend_sub, None, _LEGAL_PIT, "1 § 1 mom")
        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_heading = next(c for c in new_sec.children if c.kind == IRNodeKind.HEADING)
        assert new_heading.text == "Original"

    def test_subsection_replace_uses_cross_heading_when_muutos_ir_has_none(self):
        """When the amendment title is only carried as crossHeading, preserve it."""
        heading_old = IRNode(kind=IRNodeKind.HEADING, text="Original")
        sec = _sec("4", heading_old, _sub("1", _content("original")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "4"),)
        op = _op(op_type="REPLACE", target_section="4", target_paragraph=1)
        amend_sub = _sub("1", _content("replacement"))
        cross_heading = IRNode(kind=IRNodeKind.CROSS_HEADING, text="Kustannusten ja toiminnan seuraaminen")

        result = _apply_deterministic_subsection_op(
            state,
            op,
            sec_path,
            None,
            amend_sub,
            None,
            _LEGAL_PIT,
            "4 § 1 mom",
            cross_ir=cross_heading,
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_heading = next(c for c in new_sec.children if c.kind == IRNodeKind.HEADING)
        assert new_heading.text == "Kustannusten ja toiminnan seuraaminen"

    def test_subsection_replace_sparse_omission_shell_does_not_rewrite_heading(self):
        """Sparse descendant payloads must not smuggle a section heading rewrite."""
        heading_old = IRNode(kind=IRNodeKind.HEADING, text="Päätös kaikkien osakkeenomistajien rahoittamasta uudistuksesta")
        sec = _sec("31", heading_old, _sub("1", _content("old 1")), _sub("2", _content("old 2")))
        body = _body(sec)
        state = _make_state(body)
        sec_path = (("section", "31"),)
        op = _op(op_type="REPLACE", target_section="31", target_paragraph=2)
        amend_sub = _sub("2", _content("new 2"))
        muutos_ir = _sec(
            "31",
            IRNode(kind=IRNodeKind.HEADING, text="Päätös kaikkien osakkaiden rahoittamasta uudistuksesta"),
            IRNode(kind=IRNodeKind.OMISSION),
            amend_sub,
        )

        result = _apply_deterministic_subsection_op(
            state, op, sec_path, muutos_ir, amend_sub, None, _LEGAL_PIT, "31 § 2 mom"
        )

        result = _modified(state, result)
        new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
        new_heading = next(c for c in new_sec.children if c.kind == IRNodeKind.HEADING)
        assert new_heading.text == "Päätös kaikkien osakkeenomistajien rahoittamasta uudistuksesta"
        new_sub_2 = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
        assert "new 2" in "".join(child.text or "" for child in new_sub_2.children if child.kind == IRNodeKind.CONTENT)


def test_valid_target_group_path_hint_accepts_matching_section_path() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("text"))),),
            )
        )
    )
    path = (("chapter", "2"), ("section", "3"))

    assert _valid_target_group_path_hint(state, "section", "3", "2", None, path) == path


def test_valid_target_group_path_hint_accepts_roman_part_scope_hint_with_numeric_part_path() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(_sec("159", _sub("1", _content("text"))),),
                    ),
                ),
            )
        )
    )
    path = (("part", "3"), ("chapter", "2"), ("section", "159"))

    assert (
        _valid_target_group_path_hint(
            state,
            "section",
            "159",
            "2",
            "III",
            path,
        )
        == path
    )


def test_valid_target_group_path_hint_rejects_wrong_chapter_scope() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("text"))),),
            )
        )
    )
    path = (("chapter", "2"), ("section", "3"))

    assert _valid_target_group_path_hint(state, "section", "3", "4", None, path) is None


def test_valid_target_path_hint_prefers_rop_scope_over_legacy_op_scope() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(_sec("73", _sub("1", _content("text"))),),
            )
        )
    )
    path = (("chapter", "7"), ("section", "73"))
    op = _op(op_type="REPLACE", target_section="73", target_chapter="8")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
    )

    _target_norm, _target_chapter, _target_part = rop.resolved_section_lookup_scope
    assert (
        _valid_target_path_hint(
            state,
            target_unit_kind=rop.target_unit_kind,
            target_norm=rop.resolved_target_label,
            target_chapter=_target_chapter,
            target_part=_target_part,
            path_hint=path,
        )
        == path
    )


def test_replay_state_can_preserve_provision_index_across_section_internal_change() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("old"))),),
            )
        )
    )

    first_path = state.find_section_path("3", "2")
    cached_index = state._provision_index

    new_state = state.with_ir(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("new"))),),
            )
        ),
        preserve_provision_index=True,
    )

    assert first_path == (("chapter", "2"), ("section", "3"))
    assert new_state._provision_index is cached_index
    assert new_state.find_section_path("3", "2") == first_path


def test_replay_state_can_preserve_duplicate_section_labels_cache() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("old left"))),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(_sec("3", _sub("1", _content("old right"))),),
            ),
        )
    )

    duplicate_labels = state.duplicate_section_labels

    new_state = state.with_ir(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(_sec("3", _sub("1", _content("new left"))),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(_sec("3", _sub("1", _content("new right"))),),
            ),
        ),
        preserve_provision_index=True,
    )

    assert duplicate_labels == {"3"}
    assert new_state._duplicate_section_labels is duplicate_labels
    assert new_state.duplicate_section_labels == {"3"}


# ---------------------------------------------------------------------------
# Typed canonical-intent dispatch: mutation event coverage
# ---------------------------------------------------------------------------


def _make_repeal_intent(section: str) -> "object":
    """Build a minimal Repeal intent targeting a section node."""
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Repeal,
    )
    from lawvm.core.ir import LegalAddress

    return Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(
            address=LegalAddress(path=(("section", section),)),
        ),
        contract=ExecutionContract(occupancy=OccupancyPolicy.repeal_to_tombstone()),
    )


def _make_replace_intent(section: str, payload: Any) -> "object":
    """Build a minimal Replace intent targeting a whole section."""
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Replace,
    )
    from lawvm.core.ir import LegalAddress

    return Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(
            address=LegalAddress(path=(("section", section),)),
        ),
        payload=payload,
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )


def _make_insert_intent(section: str, payload: Any) -> "object":
    """Build a minimal Insert intent targeting a whole section."""
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        Insert,
        InsertOrder,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
    )
    from lawvm.core.ir import LegalAddress

    return Insert(
        kind=IntentKind.INSERT,
        target=NodeTarget(
            address=LegalAddress(path=(("section", section),)),
        ),
        payload=payload,
        contract=ExecutionContract(
            occupancy=OccupancyPolicy.fresh_insert(),
            insert_order=InsertOrder.SORTED_FAMILY,
        ),
    )


def _make_rop(op: AmendmentOp, intent: Any, muutos_ir: Optional[IRNode] = None) -> ResolvedOp:
    """Build a minimal ResolvedOp carrying a typed intent."""
    path_parts: tuple[tuple[str, str], ...] = ()
    if op.target_chapter:
        path_parts = path_parts + (("chapter", str(op.target_chapter)),)
    path_parts = path_parts + (("section", str(op.target_section or "")),)
    if op.target_paragraph is not None:
        path_parts = path_parts + (("subsection", str(op.target_paragraph)),)
    if op.target_item is not None:
        path_parts = path_parts + (("item", str(op.target_item)),)

    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind=op.target_unit_kind,
        target_norm=op.target_section or "",
        target_chapter=op.target_chapter,
        target_address=LegalAddress(path=tuple(path_parts)),
    )
    rop.intent = intent
    return rop


def test_typed_repeal_section_emits_mutation_event() -> None:
    """Typed Repeal(NodeTarget/section) path must emit a mutation event."""
    state = _make_state(_body(_sec("5", _sub("1", _content("old text")))))
    op = _op(op_type="REPEAL", target_section="5")
    intent = _make_repeal_intent("5")
    rop = _make_rop(op, intent)
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.op_id == op.op_id
    assert event.action == "repeal"
    assert event.outcome == "applied"
    assert event.placeholder_created_paths == ((("section", "5"),),)


def test_typed_replace_section_emits_mutation_event() -> None:
    """Typed Replace(NodeTarget/section) path must emit a mutation event."""
    state = _make_state(_body(_sec("3", _sub("1", _content("old")))))
    payload = _sec("3", _sub("1", _content("new")))
    op = _op(op_type="REPLACE", target_section="3")
    intent = _make_replace_intent("3", payload)
    rop = _make_rop(op, intent, muutos_ir=payload)
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.op_id == op.op_id
    assert event.action == "replace"
    assert event.outcome in {"applied", "failed"}


def test_uncovered_body_replace_declares_recovery_allowance_on_mutation_event() -> None:
    state = _make_state(_body(_sec("3", _sub("1", _content("old")))))
    payload = _sec("3", _sub("1", _content("new")))
    op = dc_replace(_op(op_type="REPLACE", target_section="3"), uncovered_body_recovery=True)
    intent = _make_replace_intent("3", payload)
    rop = _make_rop(op, intent, muutos_ir=payload)
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.declared_allowances == (
        DeclaredMutationAllowance(
            kind="recovery",
            paths=(
                (("section", "3"),),
            ),
            rule_id="uncovered_body_recovery",
        ),
    )


def test_typed_replace_missing_section_materialization_event_uses_target_address() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Replace,
    )

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter seven"),
                ),
            )
        )
    )
    payload = _sec("73", IRNode(kind=IRNodeKind.NUM, text="73 §"), _content("new text"))
    target_address = LegalAddress(path=(("chapter", "7"), ("section", "73")))
    op = _op(op_type="REPLACE", target_section="73", target_chapter="7")
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(address=target_address),
        payload=cast(Any, payload),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
        target_address=target_address,
    )
    rop.intent = intent
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    result = _modified(state, result)
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.outcome == "applied"
    assert event.resolved_target_path == (("chapter", "7"), ("section", "73"))
    assert event.created_paths == ((("chapter", "7"), ("section", "73")),)
    assert event.parent_path == (("chapter", "7"),)


def test_materialization_root_move_declares_recovery_path_allowance() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter six"),
                ),
            ),
            _sec("23", _content("root-level section")),
        )
    )
    payload = _sec("23", _content("new root-level section"))
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    intent = _make_replace_intent("23", payload)
    rop = _make_rop(op, intent, muutos_ir=payload)
    allowances = _materialization_root_move_allowances(state, rop, payload, None)

    assert allowances == (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=(
                (("section", "23"),),
            ),
            rule_id="section_materialization_root_move_destination_rebind",
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=(
                (("section", "23"),),
            ),
            rule_id="section_materialization_root_move_destination_rebind",
        ),
    )


def test_whole_section_move_replace_event_reports_allowed_non_target_touch() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    IRNode(kind=IRNodeKind.HEADING, text="Chapter six"),
                ),
            ),
            _sec("23", _content("root-level section")),
        )
    )
    op = _op(op_type="REPLACE", target_section="23", target_chapter="6")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_sec("23", _content("new root-level section")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="6",
    )
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = _apply_intent_section_level(
        state,
        rop,
        "test",
        ctx,
        _LEGAL_PIT,
        "test",
        rop.muutos_ir,
        mutation_events_out=mutation_events,
    )

    result = _modified(state, result)
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.outcome == "applied"
    assert event.resolved_target_path == (("chapter", "6"), ("section", "23"))
    assert event.created_paths == ((("chapter", "6"), ("section", "23")),)
    assert event.removed_paths == ((("section", "23"),),)
    assert event.declared_allowances == (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=(
                (("section", "23"),),
            ),
            rule_id="section_move_replace_destination_rebind",
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=(
                (("section", "23"),),
            ),
            rule_id="section_move_replace_destination_rebind",
        ),
    )
    results = analyze_apply_mutation_accounting([event])
    assert len(results) == 1
    assert results[0].code == "REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED"
    assert results[0].helper == "_apply_whole_section_op"
    assert results[0].allowed_paths == ((("section", "23"),),)
    assert results[0].matched_allowance_rule_ids == ("section_move_replace_destination_rebind",)


def test_whole_section_move_insert_declares_recovery_path_allowance() -> None:
    placeholder_33 = IRNode(
        kind=IRNodeKind.SECTION,
        label="33",
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(),
    )
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                    _sec("31", _content("existing 31")),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                    placeholder_33,
                ),
            ),
        )
    )
    payload = _sec("33", _content("new chapter five text"))
    op = _op(op_type="INSERT", target_section="33", target_chapter="5")
    intent = _make_insert_intent("33", payload)
    rop = _make_rop(op, intent, muutos_ir=payload)

    allowances = _whole_section_move_rebind_allowances(state, rop, payload, None)

    assert allowances == (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=(
                (("chapter", "6"), ("section", "33")),
            ),
            rule_id="section_move_insert_destination_rebind",
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=(
                (("chapter", "6"), ("section", "33")),
            ),
            rule_id="section_move_insert_destination_rebind",
        ),
    )


def test_apply_mutation_accounting_flags_successful_out_of_scope_touch() -> None:
    event = ApplyMutationEvent(
        op_id="op-1",
        source_statute="2024/1",
        action="replace",
        helper="_apply_whole_section_op",
        outcome="applied",
        resolved_target_path=(("chapter", "1"), ("section", "2")),
        replaced_paths=((("chapter", "1"), ("section", "3")),),
    )

    violations = check_apply_mutation_accounting([event])

    assert violations == [
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET op_id=op-1 helper=_apply_whole_section_op touched=1"
    ]


def test_apply_mutation_accounting_reports_typed_boundary_violation_details() -> None:
    event = ApplyMutationEvent(
        op_id="op-1",
        source_statute="2024/1",
        action="replace",
        helper="_apply_whole_section_op",
        outcome="applied",
        resolved_target_path=(("chapter", "1"), ("section", "2")),
        replaced_paths=((("chapter", "1"), ("section", "3")),),
    )

    results = analyze_apply_mutation_accounting([event])

    assert results == [
        ApplyMutationAccountingResult(
            code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
            op_id="op-1",
            helper="_apply_whole_section_op",
            touched_count=1,
            allowed_roots=(((("chapter", "1"), ("section", "2"))),),
            out_of_scope_paths=(((("chapter", "1"), ("section", "3"))),),
        )
    ]


def test_apply_mutation_accounting_allows_declared_non_target_touch() -> None:
    event = ApplyMutationEvent(
        op_id="op-allow",
        source_statute="2024/1",
        action="replace",
        helper="_apply_materialization",
        outcome="applied",
        resolved_target_path=(("chapter", "6"), ("section", "23")),
        created_paths=((("chapter", "6"), ("section", "23")),),
        removed_paths=((("section", "23"),),),
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery_path",
                paths=(
                    (("section", "23"),),
                ),
                rule_id="section_materialization_root_move_destination_rebind",
            ),
            DeclaredMutationAllowance(
                kind="migration_path",
                paths=(
                    (("section", "23"),),
                ),
                rule_id="section_materialization_root_move_destination_rebind",
            ),
        ),
    )

    results = analyze_apply_mutation_accounting([event])
    assert len(results) == 1
    assert results[0].code == "REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED"
    assert results[0].op_id == "op-allow"
    assert results[0].helper == "_apply_materialization"
    assert results[0].touched_count == 1
    assert results[0].allowed_roots == (((("chapter", "6"), ("section", "23"))),)
    assert results[0].allowed_paths == ((("section", "23"),),)
    assert results[0].matched_allowance_rule_ids == ("section_materialization_root_move_destination_rebind",)
    assert check_apply_mutation_accounting([event]) == []


def test_apply_mutation_invariant_report_preserves_allowed_touch_witness() -> None:
    event = ApplyMutationEvent(
        op_id="op-allow",
        source_statute="2024/1",
        action="replace",
        helper="_apply_materialization",
        outcome="applied",
        resolved_target_path=(("chapter", "6"), ("section", "23")),
        created_paths=((("chapter", "6"), ("section", "23")),),
        removed_paths=((("section", "23"),),),
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery_path",
                paths=(
                    (("section", "23"),),
                ),
                rule_id="section_materialization_root_move_destination_rebind",
            ),
            DeclaredMutationAllowance(
                kind="migration_path",
                paths=(
                    (("section", "23"),),
                ),
                rule_id="section_materialization_root_move_destination_rebind",
            ),
        ),
    )

    reports = build_apply_mutation_invariant_reports([event])

    assert reports == (
        ApplyMutationInvariantReport(
            op_id="op-allow",
            helper="_apply_materialization",
            outcome="applied",
            touched_paths=(
                (("chapter", "6"), ("section", "23")),
                (("section", "23"),),
            ),
            changed_paths=(
                (("chapter", "6"), ("section", "23")),
                (("section", "23"),),
            ),
            allowed_roots=(
                (("chapter", "6"), ("section", "23")),
            ),
            allowed_effect_region_paths=(
                (("chapter", "6"), ("section", "23")),
            ),
            declared_allowance_paths=(
                (("section", "23"),),
            ),
            declared_recovery_paths=(
                (("section", "23"),),
            ),
            declared_recovery_rule_ids=("section_materialization_root_move_destination_rebind",),
            declared_migration_paths=(
                (("section", "23"),),
            ),
            declared_migration_rule_ids=("section_materialization_root_move_destination_rebind",),
            permitted_paths=(
                (("chapter", "6"), ("section", "23")),
                (("section", "23"),),
            ),
            covered_changed_paths=(
                (("chapter", "6"), ("section", "23")),
                (("section", "23"),),
            ),
            unexplained_changed_paths=(),
            allowed_non_target_paths=(
                (("section", "23"),),
            ),
            out_of_scope_paths=(),
            matched_allowance_rule_ids=("section_materialization_root_move_destination_rebind",),
            path_set_invariant_holds=True,
            results=(
                ApplyMutationAccountingResult(
                    code="REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED",
                    op_id="op-allow",
                    helper="_apply_materialization",
                    touched_count=1,
                    allowed_roots=(
                        (("chapter", "6"), ("section", "23")),
                    ),
                    allowed_paths=(
                        (("section", "23"),),
                    ),
                    matched_allowance_rule_ids=("section_materialization_root_move_destination_rebind",),
                ),
            ),
        ),
    )


def test_apply_mutation_invariant_report_preserves_unexplained_touch_paths() -> None:
    event = ApplyMutationEvent(
        op_id="op-1",
        source_statute="2024/1",
        action="replace",
        helper="_apply_whole_section_op",
        outcome="applied",
        resolved_target_path=(("chapter", "1"), ("section", "2")),
        replaced_paths=((("chapter", "1"), ("section", "3")),),
    )

    reports = build_apply_mutation_invariant_reports([event])

    assert reports == (
        ApplyMutationInvariantReport(
            op_id="op-1",
            helper="_apply_whole_section_op",
            outcome="applied",
            touched_paths=(
                (("chapter", "1"), ("section", "3")),
            ),
            changed_paths=(
                (("chapter", "1"), ("section", "3")),
            ),
            allowed_roots=(
                (("chapter", "1"), ("section", "2")),
            ),
            allowed_effect_region_paths=(
                (("chapter", "1"), ("section", "2")),
            ),
            declared_allowance_paths=(),
            declared_recovery_paths=(),
            declared_recovery_rule_ids=(),
            declared_migration_paths=(),
            declared_migration_rule_ids=(),
            permitted_paths=(
                (("chapter", "1"), ("section", "2")),
            ),
            covered_changed_paths=(),
            unexplained_changed_paths=(
                (("chapter", "1"), ("section", "3")),
            ),
            allowed_non_target_paths=(),
            out_of_scope_paths=(
                (("chapter", "1"), ("section", "3")),
            ),
            matched_allowance_rule_ids=(),
            path_set_invariant_holds=False,
            results=(
                ApplyMutationAccountingResult(
                    code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
                    op_id="op-1",
                    helper="_apply_whole_section_op",
                    touched_count=1,
                    allowed_roots=(
                        (("chapter", "1"), ("section", "2")),
                    ),
                    out_of_scope_paths=(
                        (("chapter", "1"), ("section", "3")),
                    ),
                ),
            ),
        ),
    )


def test_apply_mutation_accounting_flags_failed_tree_touch() -> None:
    event = ApplyMutationEvent(
        op_id="op-2",
        source_statute="2024/1",
        action="replace",
        helper="_apply_whole_section_op",
        outcome="failed",
        resolved_target_path=(("chapter", "1"), ("section", "2")),
        created_paths=((("chapter", "1"), ("section", "2"), ("subsection", "1")),),
    )

    violations = check_apply_mutation_accounting([event])

    assert violations == [
        "REPLAY_FAILED_OP_MUTATED_TREE op_id=op-2 helper=_apply_whole_section_op touched=1"
    ]


def test_apply_mutation_accounting_flags_skipped_tree_touch() -> None:
    event = ApplyMutationEvent(
        op_id="op-3",
        source_statute="2024/1",
        action="replace",
        helper="_apply_whole_section_op",
        outcome="skipped",
        resolved_target_path=(("chapter", "1"), ("section", "2")),
        removed_paths=((("chapter", "1"), ("section", "2"), ("subsection", "1")),),
    )

    violations = check_apply_mutation_accounting([event])

    assert violations == [
        "REPLAY_SKIPPED_OP_MUTATED_TREE op_id=op-3 helper=_apply_whole_section_op touched=1"
    ]


def test_typed_repeal_missing_section_emits_failed_mutation_event() -> None:
    """Typed Repeal on a missing section emits a failed mutation event."""
    state = _make_state(_body())
    op = _op(op_type="REPEAL", target_section="999")
    intent = _make_repeal_intent("999")
    rop = _make_rop(op, intent)
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.outcome == "failed"
    assert event.op_id == op.op_id
    assert event.action == "repeal"
    assert event.resolved_target_path == (("section", "999"),)


def test_typed_repeal_missing_parent_section_emits_skipped_event_with_target_address() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Repeal,
    )

    state = _make_state(_body())
    target_address = LegalAddress(path=(("section", "5"), ("subsection", "1")))
    op = _op(op_type="REPEAL", target_section="5", target_paragraph=1)
    intent = Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(address=target_address),
        contract=ExecutionContract(occupancy=OccupancyPolicy.repeal_to_tombstone()),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="5",
        target_chapter=None,
        target_address=target_address,
    )
    rop.intent = intent
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.outcome == "skipped"
    assert event.action == "repeal"
    assert event.resolved_target_path == (("section", "5"), ("subsection", "1"))


def test_typed_subsection_repeal_prefers_rop_over_stale_legacy_granularity() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Repeal,
    )

    state = _make_state(_body(_sec("5", _sub("1", _content("old text")))))
    target_address = LegalAddress(path=(("section", "5"), ("subsection", "1")))
    op = _op(op_type="REPEAL", target_section="5", target_paragraph=99)
    intent = Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(address=target_address),
        contract=ExecutionContract(occupancy=OccupancyPolicy.repeal_to_tombstone()),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="5",
        target_chapter=None,
        target_address=target_address,
    )
    rop.intent = intent
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is not state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_deterministic_subsection_op"
    assert event.outcome == "applied"
    assert event.consumed_paths == (((("section", "5"), ("subsection", "1"))),)
    assert check_apply_mutation_accounting([event]) == []
    sec = next(child for child in result.ir.children if child.kind == IRNodeKind.SECTION and child.label == "5")
    assert [child for child in sec.children if child.kind == IRNodeKind.SUBSECTION] == []


def test_legacy_subsection_replace_event_records_primary_target_touch() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("old text")))))
    op = _op(op_type="REPLACE", target_section="1", target_paragraph=1)
    amend_sub = _sub("1", _content("new text"))
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        muutos_ir=None,
        amend_sub_ir=amend_sub,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
    )

    assert result is not state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_deterministic_subsection_op"
    assert event.outcome == "applied"
    assert event.replaced_paths == (((("section", "1"), ("subsection", "1"))),)
    assert check_apply_mutation_accounting([event]) == []


def test_typed_dispatch_unknown_intent_emits_failed_event() -> None:
    """Unknown intent type in typed dispatch emits a failed event and no legacy replay."""
    from dataclasses import dataclass
    from typing import Literal

    @dataclass(frozen=True)
    class _UnknownIntent:
        kind: Literal["unknown"] = "unknown"

    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    op = _op(op_type="REPLACE", target_section="1")
    unknown_intent = _UnknownIntent()
    rop = _make_rop(op, unknown_intent)  # type: ignore[arg-type]
    ctx = _ctx(_body())
    failed_ops: List[FailedOp] = []
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        failed_ops_out=failed_ops,
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(failed_ops) == 1
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_canonical_intent"
    assert event.outcome == "failed"
    assert event.resolved_target_path == (("section", "1"),)
    assert "unhandled intent type" in event.failure_reason


def test_typed_replace_unsupported_target_stops_without_legacy_dispatch() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Replace,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    payload = _sec("1", _sub("1", _content("new text")))
    op = _op(op_type="REPLACE", target_section="1")
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(
            address=LegalAddress(path=(("annex", "1"),)),
        ),
        payload=cast(Any, payload),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = _make_rop(op, intent, muutos_ir=payload)
    ctx = _ctx(_body())
    failed_ops: List[FailedOp] = []
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="finlex_oracle",
        failed_ops_out=failed_ops,
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(failed_ops) == 1
    assert failed_ops[0].reason_code == "unhandled_replace_target"
    assert failed_ops[0].reason == "unhandled Replace target: NodeTarget"
    assert failed_ops[0].target_section == "1"
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_replace"
    assert event.outcome == "skipped"
    assert event.used_fallback_tags == ()
    assert event.resolved_target_path == (("section", "1"),)
    assert "unhandled Replace target" in event.failure_reason


def test_typed_insert_unsupported_target_stops_without_legacy_dispatch() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        Insert,
        InsertOrder,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    payload = _sec("2", _sub("1", _content("new text")))
    op = _op(op_type="INSERT", target_section="2")
    intent = Insert(
        kind=IntentKind.INSERT,
        target=NodeTarget(
            address=LegalAddress(path=(("annex", "2"),)),
        ),
        payload=cast(Any, payload),
        contract=ExecutionContract(
            occupancy=OccupancyPolicy.fresh_insert(),
            insert_order=InsertOrder.SORTED_FAMILY,
        ),
    )
    rop = _make_rop(op, intent, muutos_ir=payload)
    ctx = _ctx(_body())
    failed_ops: List[FailedOp] = []
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="finlex_oracle",
        failed_ops_out=failed_ops,
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(failed_ops) == 1
    assert failed_ops[0].reason_code == "unhandled_insert_target"
    assert failed_ops[0].reason == "unhandled Insert target: NodeTarget"
    assert failed_ops[0].target_section == "2"
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_insert"
    assert event.outcome == "skipped"
    assert event.used_fallback_tags == ()
    assert event.resolved_target_path == (("section", "2"),)
    assert "unhandled Insert target" in event.failure_reason


def test_typed_repeal_unsupported_target_stops_without_legacy_dispatch() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Repeal,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    op = _op(op_type="REPEAL", target_section="1")
    intent = Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(
            address=LegalAddress(path=(("annex", "1"),)),
        ),
        contract=ExecutionContract(occupancy=OccupancyPolicy.repeal_to_tombstone()),
    )
    rop = _make_rop(op, intent)
    ctx = _ctx(_body())
    failed_ops: List[FailedOp] = []
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        failed_ops_out=failed_ops,
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(failed_ops) == 1
    assert failed_ops[0].reason_code == "unhandled_repeal_target"
    assert failed_ops[0].reason == "unhandled Repeal target: NodeTarget"
    assert failed_ops[0].target_section == "1"
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_repeal"
    assert event.outcome == "skipped"
    assert event.used_fallback_tags == ()
    assert event.resolved_target_path == (("section", "1"),)
    assert "unhandled Repeal target" in event.failure_reason


def test_typed_move_stops_without_legacy_dispatch() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        Move,
        NodeTarget,
        OccupancyPolicy,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(
        _body(
            IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(_sec("1", _sub("1", _content("text"))),)),
            IRNode(kind=IRNodeKind.CHAPTER, label="2"),
        )
    )
    op = _op(op_type="RENUMBER", target_section="1")
    intent = Move(
        kind=IntentKind.MOVE,
        source=NodeTarget(
            address=LegalAddress(path=(("section", "1"),)),
        ),
        destination_parent=LegalAddress(path=(("chapter", "2"),)),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = _make_rop(op, intent)
    rop._op_type_seed = "MOVE"
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is not state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_move"
    assert event.outcome == "applied"
    assert event.used_fallback_tags == ()
    assert event.resolved_target_path == (("chapter", "1"), ("section", "1"))
    assert event.parent_path == (("chapter", "2"),)
    assert event.renumbered_paths == (
        ((("chapter", "1"), ("section", "1")), (("chapter", "2"), ("section", "1"))),
    )
    assert state.find_section_path("1", target_chapter="2") is None
    assert result.find_section_path("1", target_chapter="2") == (("chapter", "2"), ("section", "1"))


def test_legacy_dispatch_fallback_event_keeps_target_address() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    op = _op(op_type="REPLACE", target_section="1")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    rop.intent = None
    rop._op_type_seed = "MOVE"
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        _ctx(state.ir),
        None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    event = next(
        e
        for e in mutation_events
        if e.helper == "apply_op"
        and e.used_fallback_tags == ("APPLY.LEGACY_DISPATCH_FALLBACK", "missing_canonical_intent")
    )
    assert event.helper == "apply_op"
    assert event.outcome == "skipped"
    assert event.used_fallback_tags == ("APPLY.LEGACY_DISPATCH_FALLBACK", "missing_canonical_intent")
    assert event.resolved_target_path == (("section", "1"),)
    assert event.reason_code == "missing_canonical_intent"
    assert "ResolvedOp reached apply without CanonicalIntent" in event.failure_reason


def test_legacy_dispatch_events_prefer_resolvedop_identity_when_present() -> None:
    state = _make_state(_body())
    op = AmendmentOp(
        op_id="",
        op_type="REPLACE",
        target_section="missing",
        target_unit_kind="section",
        source_statute="",
        source_issue_date=_DATE,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    rop._source_statute_override = "2020/1"
    rop.op_id = "rop_identity"
    mutation_events: List[ApplyMutationEvent] = []

    result = _apply_legacy_dispatch(
        state,
        op,
        op.description(),
        _ctx(state.ir),
        muutos_ir=None,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_whole_section_op"
    assert event.outcome == "failed"
    assert event.op_id == "rop_identity"
    assert event.source_statute == "2020/1"
    assert event.resolved_target_path == (("section", "1"),)


def test_legacy_dispatch_prefers_resolvedop_slot_binding_when_shell_lacks_stable_id() -> None:
    state = _make_state(_body(_sec("1", _sub("1", _content("original")))))
    rop_shell = AmendmentOp(
        op_id="stable_rop",
        op_type="REPLACE",
        target_section="1",
        target_unit_kind="section",
        target_paragraph=1,
        source_statute="2020/1",
        source_issue_date=_DATE,
    )
    shell_op = AmendmentOp(
        op_id="",
        op_type="REPLACE",
        target_section="1",
        target_unit_kind="section",
        target_paragraph=1,
        source_statute="2020/1",
        source_issue_date=_DATE,
    )
    assigned_amend_sub = _sub("1", _content("resolved via rop stable id"))
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(
            by_stable_op_id={"stable_rop": assigned_amend_sub},
        ),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=rop_shell.description(),
                op_type=str(rop_shell.op_type or ""),
                target_paragraph=rop_shell.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="1",
            )
        ,),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    rop = ResolvedOp.from_amendment_op(
        rop_shell,
        muutos_ir=None,
        cross_ir=None,
        slot_assignment=assignment,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )

    result = _apply_legacy_dispatch(
        state,
        shell_op,
        shell_op.description(),
        _ctx(),
        muutos_ir=None,
        amend_sub_ir=None,
        slot_assignment=assignment,
        replay_mode="legal_pit",
        rop=rop,
    )

    result = _modified(state, result)
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION)
    sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION)
    text = " ".join(c.text or "" for c in sub.children)
    assert "resolved via rop stable id" in text


def test_legacy_dispatch_prefers_resolvedop_fields_when_shell_is_stale() -> None:
    state = _make_state(_body(_sec("1", _content("original one")), _sec("2", _content("original two"))))
    stale_shell = AmendmentOp(
        op_id="",
        op_type="REPLACE",
        target_section="2",
        target_unit_kind="section",
        source_statute="2020/1",
        source_issue_date=_DATE,
    )
    rop = ResolvedOp.from_amendment_op(
        stale_shell,
        muutos_ir=_sec("1", _content("replaced via rop fields")),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    rop.op_id = "stable_rop"

    result = _apply_legacy_dispatch(
        state,
        stale_shell,
        stale_shell.description(),
        _ctx(state.ir),
        muutos_ir=None,
        replay_mode="legal_pit",
        rop=rop,
    )

    result = _modified(state, result)
    sec1 = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "1")
    sec2 = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "2")
    sec1_text = " ".join(c.text or "" for c in sec1.children)
    sec2_text = " ".join(c.text or "" for c in sec2.children)
    assert "replaced via rop fields" in sec1_text
    assert "original two" in sec2_text


def test_legacy_dispatch_does_not_cross_chapter_fallback_for_unique_section() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(_sec("23", _content("chapter five text")),),
            ),
        )
    )
    failed_ops: List[FailedOp] = []
    op = _op(op_type="REPLACE", target_section="23", target_chapter="3")

    result = _apply_legacy_dispatch(
        state,
        op,
        op.description(),
        _ctx(state.ir),
        muutos_ir=_sec("23", _content("wrong cross-chapter replacement")),
        replay_mode="legal_pit",
        failed_ops_out=failed_ops,
    )

    assert result is state
    assert len(failed_ops) == 1
    assert failed_ops[0].reason_code == "section_not_found"
    sec = state.find_section("23", "5")
    assert sec is not None
    assert irnode_to_text(sec) == "chapter five text"
    assert failed_ops
    assert failed_ops[0].target_section == "23"
    assert failed_ops[0].target_chapter == "3"
    assert failed_ops[0].reason == "master §23 not found"


def test_typed_relabel_unhandled_target_keeps_target_address() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body(_sec("1", _sub("1", _content("text")))))
    op = _op(op_type="RENUMBER", target_section="1")
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(address=LegalAddress(path=(("annex", "1"),))),
        destination=NodeTarget(address=LegalAddress(path=(("annex", "2"),))),
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
    )
    rop = _make_rop(op, intent)
    rop._op_type_seed = "RENUMBER"
    rop.target_unit_kind = "chapter"
    rop._target_address_override = LegalAddress(path=(("annex", "1"),))
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_relabel"
    assert event.outcome == "skipped"
    assert event.resolved_target_path == (("annex", "1"),)
    assert event.used_fallback_tags == ("APPLY.RELABEL_SKIPPED", "target_kind_unimplemented")
    assert event.reason_code == "target_kind_unimplemented"
    assert event.failure_reason  # non-empty failure reason


def test_typed_container_relabel_prefers_scoped_target_address() -> None:
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        Relabel,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.PART,
                label="II",
                children=(IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(_sec("10"),)),),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="III",
                children=(IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(_sec("20"),)),),
            ),
        )
    )
    op = _op(op_type="RENUMBER", target_section="2")
    op.target_unit_kind = "chapter"
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(
            address=LegalAddress(path=(("part", "III"), ("chapter", "2"))),
        ),
        destination=NodeTarget(
            address=LegalAddress(path=(("part", "III"), ("chapter", "18"))),
        ),
        contract=ExecutionContract(
            occupancy=_compat_upsert_policy(),
            coverage=CoverageMode.EXACT,
        ),
    )
    rop = _make_rop(op, intent)
    rop._op_type_seed = "RENUMBER"
    rop.target_unit_kind = "chapter"
    rop.target_norm = "2"
    rop._target_address_override = LegalAddress(path=(("part", "III"), ("chapter", "2")))
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result.find("chapter", "18") == (("part", "III"), ("chapter", "18"))
    assert result.find("chapter", "2") == (("part", "II"), ("chapter", "2"))
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_relabel"
    assert event.outcome == "applied"
    assert event.renumbered_paths == (
        ((("part", "III"), ("chapter", "2")), (("part", "III"), ("chapter", "18"))),
    )


def test_typed_container_dispatch_none_emits_skipped_event_with_target_address() -> None:
    from lawvm.core.canonical_intent import (
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Replace,
    )
    from lawvm.core.ir import LegalAddress

    state = _make_state(_body())
    payload = IRNode(kind=IRNodeKind.CHAPTER, label="3", children=(IRNode(kind=IRNodeKind.NUM, text="3 luku"),))
    op = AmendmentOp(
        op_id="container_mismatch",
        op_type="REPLACE",
        target_section="3",
        target_unit_kind="section",
        source_statute="2020/1",
        source_issue_date=_DATE,
    )
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(
            address=LegalAddress(path=(("chapter", "3"),)),
        ),
        payload=cast(Any, payload),
        contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=payload,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="3",
        target_chapter=None,
        target_address=LegalAddress(path=(("chapter", "3"),)),
    )
    rop.intent = intent
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=payload,
        replay_mode="legal_pit",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert result is state
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.helper == "_apply_intent_container"
    assert event.outcome == "skipped"
    assert event.resolved_target_path == (("chapter", "3"),)
    assert event.reason_code == "container_op_returned_none"
    assert "_apply_container_op returned None" in event.failure_reason


def test_typed_section_suffix_marker_does_not_authorize_apply_rewrite() -> None:
    state = _make_state(
        _body(
            _sec(
                "33",
                _sub(
                    "1",
                    _para("a", "first item"),
                    _para("b", "second item"),
                ),
            )
        )
    )
    op = _op(op_type="REPEAL", target_section="33a")
    intent = _make_repeal_intent("33a")
    rop = _make_rop(op, intent)
    ctx = _ctx(_body())
    mutation_events: List[ApplyMutationEvent] = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir=None,
        replay_mode="finlex_oracle",
        mutation_events_out=mutation_events,
        rop=rop,
    )

    assert len(mutation_events) == 1
    event = mutation_events[0]
    new_sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "33")
    sub = next(c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    assert [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH] == ["a", "b"]
    assert event.outcome == "applied"
    assert event.used_fallback_tags == ()


def test_partial_section_replace_diagnostics_use_typed_uncovered_body_carrier() -> None:
    master_sec = _sec(
        "1",
        _sub(
            "1",
            *[_para(str(i), f"item {i}") for i in range(1, 9)],
        ),
    )
    amend_sec = _sec(
        "1",
        _sub(
            "1",
            _para("1", "item 1"),
            _para("2", "item 2"),
        ),
    )
    op = _op(op_type="REPLACE", target_section="1")

    diag = _partial_section_replace_diagnostics_ir(op, master_sec, amend_sec)

    assert diag.get("suspicious") is True

    recovered_diag = _partial_section_replace_diagnostics_ir(
        dc_replace(op, uncovered_body_recovery=True),
        master_sec,
        amend_sec,
    )

    assert recovered_diag == {}


def test_apply_sparse_item_replace_merge_keeps_hint_empty_and_emits_pathology() -> None:
    state = _make_state(
        _body(
            _sec(
                "1",
                _sub(
                    "1",
                    _intro("Lista:"),
                    _para("1", "item 1"),
                    _para("2", "item 2"),
                    _para("3", "item 3"),
                    _para("4", "item 4"),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="1")
    muutos_ir = _sec(
        "1",
        _sub(
            "1",
            _intro("Lista:"),
            _para("2", "uusi item 2"),
            _para("4", "uusi item 4"),
        ),
    )
    ctx = _ctx(state.ir)
    pathologies = []

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir,
        replay_mode="legal_pit",
        source_pathologies_out=pathologies,
    )

    assert result is not state
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    sec = next(c for c in result.ir.children if c.kind == IRNodeKind.SECTION and c.label == "1")
    sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    assert [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4"]
    assert "uusi item 2" in irnode_to_text(sec)
    assert "uusi item 4" in irnode_to_text(sec)


def test_container_replace_missing_target_emits_pathology() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(_sec("1", _para("", "vanha")),),
            )
        )
    )
    op = AmendmentOp(
        op_id="replace_missing_chapter",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="5",
        target_paragraph=2,
    )
    muutos_ir = _sec("5", _para("", "uusi"))
    pathologies = []

    result = _apply_container_op(
        state,
        op,
        muutos_ir,
        get_replay_profile("legal_pit"),
        "test",
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert len(pathologies) == 1
    assert pathologies[0].code == "CONTAINER_REPLACE_TARGET_ABSENT"
    assert pathologies[0].detail["target_section"] == "5"
    assert pathologies[0].detail["target_paragraph"] == 2
    assert pathologies[0].detail["has_payload"] is True


def test_container_replace_missing_target_without_child_scope_fails_closed() -> None:
    state = _make_state(
        _body(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(_sec("1", _para("", "vanha")),),
            )
        )
    )
    op = AmendmentOp(
        op_id="replace_missing_chapter_body",
        op_type="REPLACE",
        target_unit_kind="chapter",
        target_section="5",
    )
    muutos_ir = _sec("5", _para("", "uusi"))
    pathologies = []

    result = _apply_container_op(
        state,
        op,
        muutos_ir,
        get_replay_profile("legal_pit"),
        "test",
        source_pathologies_out=pathologies,
    )

    assert result is state
    assert len(pathologies) == 1
    assert pathologies[0].code == "CONTAINER_REPLACE_TARGET_ABSENT"
    assert pathologies[0].detail["target_section"] == "5"
    assert pathologies[0].detail["target_paragraph"] == ""
    assert pathologies[0].detail["has_payload"] is True


def test_apply_suspicious_partial_replace_drop_keeps_hint_empty() -> None:
    state = _make_state(
        _body(
            _sec(
                "1",
                _sub(
                    "1",
                    _para("1", "Header A"),
                    _para("2", "Header B"),
                    _para("3", "Alpha"),
                    _para("4", "Beta"),
                    _para("5", "Gamma"),
                    _para("6", "Delta"),
                    _para("7", "Epsilon"),
                    _para("8", "Zeta"),
                ),
            )
        )
    )
    op = _op(op_type="REPLACE", target_section="1")
    muutos_ir = _sec(
        "1",
        _sub(
            "1",
            _para("3", "Beta"),
        ),
    )
    ctx = _ctx(state.ir)

    result = apply_op(
        state,
        op,
        ctx,
        muutos_ir,
        replay_mode="legal_pit",
    )

    assert result is state


# ============================================================================
# Bug #8 regression: subsection index consistency across apply handlers
# ============================================================================


def test_subsection_repeal_and_replace_target_same_node_in_intro_list_shape() -> None:
    """In an intro-list shaped section, repeal and replace of the same
    target_paragraph should target the same physical subsection.

    Intro-list shape: subsection 1 is intro-only (ends with ":"), subsection 2
    carries the item list. With _resolve_subsection_index, target_paragraph=2
    maps to index 2 (not 1) for non-item ops in this shape, because "moment 1"
    spans subsections 0+1, so "moment 2" is physical subsection 2.

    Before the fix, _apply_subsection_replace used raw `n = target_paragraph - 1`
    (= 1) while _apply_subsection_repeal used _resolve_subsection_index (= 2).
    After the fix, both use _resolve_subsection_index and agree.
    """
    from lawvm.core.tree_ops import resolve as tree_resolve

    sec = _sec(
        "5",
        _sub("1", _intro("Tama laki ei koske:")),
        _sub(
            "2",
            _intro("Seuraavia:"),
            _para("1", "item a"),
            _para("2", "item b"),
        ),
        _sub("3", _content("Third moment text")),
    )
    body = _body(sec)
    sec_path = [("section", "5")]

    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

    # REPEAL subsection 2 — uses _resolve_subsection_index (already did before fix)
    repeal_state = _make_state(body)
    repeal_op = _op(op_type="REPEAL", target_section="5", target_paragraph=2)
    repeal_result = _apply_subsection_repeal(
        repeal_state, repeal_op, sec_path, sec, subsecs, _FINLEX_ORACLE, "[test] REPEAL 5 § 2 mom"
    )
    repeal_result = _modified(repeal_state, repeal_result)
    repeal_sec = tree_resolve(repeal_result.ir, (("section", "5"),))
    assert repeal_sec is not None
    repeal_subsecs = [c for c in repeal_sec.children if c.kind == IRNodeKind.SUBSECTION]

    # REPLACE subsection 2 — now also uses _resolve_subsection_index (the fix)
    replace_sub = _sub("2", _content("Replaced moment"))
    replace_state = _make_state(body)
    replace_op = _op(op_type="REPLACE", target_section="5", target_paragraph=2)
    replace_result = _apply_subsection_replace(
        replace_state, replace_op, sec_path, sec, subsecs, replace_sub, None, _FINLEX_ORACLE, "[test] REPLACE 5 § 2 mom"
    )
    replace_result = _modified(replace_state, replace_result)
    replace_sec = tree_resolve(replace_result.ir, (("section", "5"),))
    assert replace_sec is not None
    replace_subsecs = [c for c in replace_sec.children if c.kind == IRNodeKind.SUBSECTION]

    # Both should leave subsection 1 (intro) intact
    assert repeal_subsecs[0].children[0].text == "Tama laki ei koske:", (
        "Repeal should not have touched subsection 1 (intro)"
    )
    assert replace_subsecs[0].children[0].text == "Tama laki ei koske:", (
        "Replace should not have touched subsection 1 (intro)"
    )

    # Both should target the rebound subsection at index 2. In this shape the
    # intro-list carrier is explicit, so repeated target_paragraph=2 binds to
    # the physical subsection that follows the live intro-list carrier.
    assert repeal_subsecs[2].attrs.get("lawvm_repeal_placeholder") == "1", (
        "Repeal should have placed placeholder at subsection 3"
    )
    assert replace_subsecs[2].children[0].text == "Replaced moment", (
        "Replace should have put new content at subsection 3"
    )

    # The rebound target is the third physical subsection; the carried intro
    # list carrier at index 1 remains intact in both paths.
    assert len(repeal_subsecs) == 3, "repeal with placeholders should keep 3 subsections"
    assert repeal_subsecs[1].children[0].text == "Seuraavia:", (
        "Repeal should not have touched subsection 2"
    )
    assert replace_subsecs[1].children[0].text == "Seuraavia:", (
        "Replace should not have touched subsection 2"
    )


def test_intro_list_moment_shape_requires_colon_led_intro() -> None:
    colon_shape = [
        _sub("1", _content("Tätä sovelletaan seuraaviin:")),
        _sub("2", _intro("Seuraavat tilanteet:"), _para("1", "item a"), _para("2", "item b")),
        _sub("3", _content("Third moment text")),
    ]
    plain_first_moment = [
        _sub("1", _content("Valtiota edustaa valtiokonttori.")),
        _sub("2", _intro("Valtiokonttorin on:"), _para("1", "item a"), _para("2", "item b")),
        _sub("3", _content("Third moment text")),
    ]

    colon_idx, _, colon_rebound, _ = _resolve_subsection_index_with_rebound_kind(colon_shape, 2)
    plain_idx, _, plain_rebound, _ = _resolve_subsection_index_with_rebound_kind(plain_first_moment, 2)

    assert _has_intro_list_moment_shape_ir(colon_shape) is True
    assert _has_intro_list_moment_shape_ir(plain_first_moment) is True
    assert colon_idx == 2
    assert colon_rebound == "intro_list_moment_shape"
    assert plain_idx == 1
    assert plain_rebound is None


def test_subsection_index_reports_missing_exact_subsection_label_rebound() -> None:
    subsecs = [
        _sub("2", _content("Second moment text")),
        _sub("3", _content("Third moment text")),
    ]

    idx, _, rebound, exact_match = _resolve_subsection_index_with_rebound_kind(subsecs, 1)

    assert idx == 0
    assert exact_match is False
    assert rebound == "missing_exact_subsection_label"


def test_subsection_repeal_strict_blocks_missing_exact_subsection_label_rebound() -> None:
    sec = _sec(
        "20",
        _sub("2", _content("Second moment text")),
        _sub("3", _content("Third moment text")),
    )
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    result = _apply_subsection_repeal(
        state,
        _op(op_type="REPEAL", target_section="20", target_paragraph=1),
        sec_path,
        sec,
        subsecs,
        _LEGAL_PIT,
        "20 § 1 mom repeal",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_collapse_intro_list_amend_subsection_accepts_consecutive_numeric_items_beyond_one_two() -> None:
    muutos_ir = _sec(
        "7",
        _sub("1", _content("Johdanto seuraavaan luetteloon:")),
        _sub("2", _content("3) kolmas kohta")),
        _sub("3", _content("4) neljäs kohta")),
        _sub("4", _content("5) viides kohta")),
    )

    collapsed = _collapse_intro_list_amend_subsection_ir(muutos_ir)

    assert collapsed is not None
    paragraphs = [child for child in collapsed.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [child.label for child in paragraphs] == ["3", "4", "5"]


def test_collapse_intro_list_amend_subsection_does_not_require_trailing_colon() -> None:
    muutos_ir = _sec(
        "8",
        _sub("1", _content("Johdanto seuraavaan luetteloon")),
        _sub("2", _content("1) ensimmainen kohta")),
        _sub("3", _content("2) toinen kohta")),
    )

    collapsed = _collapse_intro_list_amend_subsection_ir(muutos_ir)

    assert collapsed is not None
    paragraphs = [child for child in collapsed.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [child.label for child in paragraphs] == ["1", "2"]


def test_subsection_replace_missing_momentti_label_matched_appends() -> None:
    """REPLACE targeting momentti 2 must append it when live state only has
    momentti 1 — e.g. after an earlier whole-section replace that lost the
    second subsection.

    Regression: _looks_like_standalone_tail_subsection returned True for a
    content-only replacement with an uppercase opening.  The old guard then
    returned None (no deterministic path) when n >= len(subsecs).  The fix
    allows the append path when _replace_sub.label matches target_paragraph.
    """
    # Live state: section with only 1 subsection (momentti 2 was lost by an
    # earlier whole-section replace).
    sec = _sec(
        "30",
        _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus.")),
    )
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    # The replacement for momentti 2 — content-only, uppercase opening.
    # Before the fix, _looks_like_standalone_tail_subsection(replace_sub)
    # returns True and the n >= len(subsecs) guard fires, returning None.
    replace_sub = _sub("2", _content("Kalastuskiintiorekisteria pitavat maa- ja metsatalousministerio."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=2)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 2 mom",
        source_pathologies_out=pathologies,
    )
    assert result is not None, (
        "_apply_subsection_replace returned None (no deterministic path) for "
        "a label-matched append — the standalone-tail guard fired incorrectly"
    )
    result_sec = result.ir
    from lawvm.core.tree_ops import resolve as tree_resolve
    result_sec_node = tree_resolve(result.ir, (("section", "30"),))
    assert result_sec_node is not None
    result_subsecs = [c for c in result_sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(result_subsecs) == 2, (
        f"Expected 2 subsections after append, got {len(result_subsecs)}"
    )
    assert result_subsecs[0].label == "1", "Subsection 1 label intact"
    assert result_subsecs[1].label == "2", "Appended subsection should carry label '2'"
    assert [p.code for p in pathologies] == [
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
        "SUBSECTION_TARGET_REBOUND",
    ]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_append"
    assert pathologies[1].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_subsection_replace_strict_blocks_append_recovery() -> None:
    sec = _sec("30", _sub("1", _content("Momentti 1.")))
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("2", _content("Kalastuskiintiorekisteria pitavat maa- ja metsatalousministerio."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=2)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 2 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )
    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_subsection_replace_missing_sparse_local_label_appends_next_live_moment() -> None:
    """A sparse local payload label must not block append to the next live moment.

    Regression: 2019/511 targets `15 luku 2 § 5 mom`, but the sparse payload
    reproduces that tail moment as local slot label `2`. The standalone-tail
    guard must still allow the append because this is the immediate next live
    moment, not a true gap.
    """
    sec = _sec(
        "2",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
        _sub("4", _content("Momentti 4.")),
    )
    body = _body(sec)
    sec_path = [("section", "2")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

    replace_sub = _sub(
        "2",
        _content(
            "Finanssivalvonnasta annetun lain 40 §:n 1 momentissa tarkoitettuja säännöksiä "
            "ovat lisäksi tämän pykälän 1-3 momentissa tarkoitettuja säännöksiä koskevat "
            "tarkemmat säännökset."
        ),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="2", target_paragraph=5)
    result = _apply_subsection_replace(
        state, op, sec_path, sec, subsecs, replace_sub, None, _FINLEX_ORACLE, "2 § 5 mom"
    )
    assert result is not None, "Immediate next-moment sparse tail replace should append, not fail"

    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec_node = tree_resolve(result.ir, (("section", "2"),))
    assert result_sec_node is not None
    result_subsecs = [c for c in result_sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in result_subsecs] == ["1", "2", "3", "4", "5"]
    assert (
        result_subsecs[-1].children[0].text
        == "Finanssivalvonnasta annetun lain 40 §:n 1 momentissa tarkoitettuja säännöksiä ovat lisäksi tämän pykälän 1-3 momentissa tarkoitettuja säännöksiä koskevat tarkemmat säännökset."
    )


def test_subsection_replace_extracts_predecessor_tail_into_inserted_new_moment() -> None:
    """Omission-bracketed sparse replace may really mean a new shifted moment.

    Pattern from 2016/1227 <- 2022/1149 §12: the replacement text matches the
    final paragraph of the preceding live subsection, so replay must lift that
    paragraph into a new inserted moment and renumber the old target onward.
    """
    sec = _sec(
        "12",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub(
            "3",
            _intro("Lisäksi valvotaan seuraavia asioita:"),
            _para("1", "asia 1"),
            _para("2", "asia 2"),
            _para("3", "asia 3"),
            _para("4", "asia 4"),
            _content("Vanha listan jälkeinen häntä."),
        ),
        _sub("4", _content("Vanha 4 momentti.")),
    )
    body = _body(sec)
    sec_path = [("section", "12")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []
    replace_sub = _sub("4", _content("Uusi itsenäinen 4 momentti."))
    muutos_ir = _sec(
        "12",
        IRNode(kind=IRNodeKind.OMISSION),
        replace_sub,
        IRNode(kind=IRNodeKind.OMISSION),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="12", target_paragraph=4)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        muutos_ir,
        _FINLEX_ORACLE,
        "12 § 4 mom",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec = tree_resolve(result.ir, (("section", "12"),))
    assert result_sec is not None
    result_subsecs = [c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in result_subsecs] == ["1", "2", "3", "4", "5"]
    assert "Vanha listan jälkeinen häntä." not in irnode_to_text(result_subsecs[2])
    assert irnode_to_text(result_subsecs[3]) == "Uusi itsenäinen 4 momentti."
    assert irnode_to_text(result_subsecs[4]) == "Vanha 4 momentti."
    assert [p.code for p in pathologies] == [
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_predecessor_tail_extract_insert"


def test_subsection_replace_missing_target_does_not_gap_fill_before_higher_labeled_tail() -> None:
    """Missing-target REPLACE must not silently degrade into gap-fill INSERT."""
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
        _sub("6", _content("Momentti 6.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("5", _content("Momentti 5."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=5)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "13 § 5 mom",
        source_pathologies_out=pathologies,
    )
    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_ABSENT"]
    assert pathologies[0].detail["has_higher_live_numeric_label"] is True

    def test_subsection_replace_does_not_report_pathology_when_live_label_is_higher_in_range() -> None:
        sec = _sec(
            "13",
            _sub("1", _content("Momentti 1.")),
            _sub("3", _content("Momentti 3.")),
        )
        body = _body(sec)
        sec_path = [("section", "13")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
        pathologies: list[SourcePathology] = []

        replace_sub = _sub("2", _content("Momentti 2."))
        state = _make_state(body)
        op = _op(op_type="REPLACE", target_section="13", target_paragraph=2)

        result = _apply_subsection_replace(
            state,
            op,
            sec_path,
            sec,
            subsecs,
            replace_sub,
            None,
            _FINLEX_ORACLE,
            "13 § 2 mom",
            source_pathologies_out=pathologies,
        )
        assert result is None
        assert pathologies == []


def test_subsection_replace_reports_absent_target_when_gap_append_is_blocked_by_higher_tail() -> None:
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
        _sub("7", _content("Momentti 7.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("6", _content("Momentti 6."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=6)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "13 § 6 mom",
        source_pathologies_out=pathologies,
    )
    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_ABSENT"]
    assert pathologies[0].detail["live_label"] == ""


def test_subsection_replace_reports_absent_target_when_gap_label_mismatch_blocks_append() -> None:
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
        _sub("7", _content("Momentti 7.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("5", _content("Momentti 5."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=6)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "13 § 6 mom",
        source_pathologies_out=pathologies,
    )
    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_ABSENT"]
    assert pathologies[0].detail["has_higher_live_numeric_label"] is True


def test_subsection_replace_forced_append_emits_pathology() -> None:
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("6", _content("Momentti 6."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=6)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "13 § 6 mom",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec = tree_resolve(result.ir, (("section", "13"),))
    assert result_sec is not None
    result_subsecs = [c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in result_subsecs] == ["1", "2", "3", "4"]
    assert [p.code for p in pathologies] == [
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
        "SUBSECTION_TARGET_REBOUND",
    ]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_forced_append"
    assert pathologies[1].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_subsection_replace_strict_blocks_forced_append() -> None:
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("6", _content("Momentti 6."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=6)

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "13 § 6 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_subsection_replace_single_moment_content_only_emits_pathology() -> None:
    sec = _sec("30", _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus.")))
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus edelleen."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    result_sec_node = result.ir
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec = tree_resolve(result_sec_node, (("section", "30"),))
    assert result_sec is not None
    result_subsecs = [c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in result_subsecs] == ["1"]
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_standalone_tail_append"


def test_subsection_replace_strict_blocks_standalone_tail_append() -> None:
    sec = _sec("30", _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus.")))
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus edelleen."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert len(pathologies) == 1
    assert pathologies[0].code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_standalone_tail_append"


def test_subsection_replace_prunes_standalone_tail_successor_with_text_witness() -> None:
    sec = _sec(
        "30",
        _sub("1", _content("Vanha ensimmäinen virke.")),
        _sub("2", _content("Yhteinen jatkolause.")),
    )
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("1", _content("Uusi ensimmäinen virke. Yhteinen jatkolause."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec = tree_resolve(result.ir, (("section", "30"),))
    assert result_sec is not None
    result_subsecs = [c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in result_subsecs] == ["1"]
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_standalone_tail_sibling_prune"


def test_subsection_replace_strict_blocks_standalone_tail_successor_prune() -> None:
    sec = _sec(
        "30",
        _sub("1", _content("Vanha ensimmäinen virke.")),
        _sub("2", _content("Yhteinen jatkolause.")),
    )
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub("1", _content("Uusi ensimmäinen virke. Yhteinen jatkolause."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)
    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_standalone_tail_sibling_prune"


def test_subsection_replace_emits_pathology_when_omission_merge_falls_back_to_raw_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sec = _sec("30", _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus.")))
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub(
        "1",
        _intro("Uusi johdanto:"),
        _content("Kalastusluvat myontaa elinvoimakeskus edelleen."),
        IRNode(kind=IRNodeKind.OMISSION),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)

    monkeypatch.setattr("lawvm.finland.apply_subsection_ops._merge_intro_only_subsection_replace", lambda *a, **k: None)
    monkeypatch.setattr(
        "lawvm.finland.apply_subsection_ops._merge_subsection_accumulate_inner_omission_ir",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "lawvm.finland.apply_subsection_ops._merge_subsection_with_omission_ir",
        lambda *a, **k: None,
    )

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    result_sec = result.ir
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec_node = tree_resolve(result_sec, (("section", "30"),))
    assert result_sec_node is not None
    result_sub = next(c for c in result_sec_node.children if c.kind == IRNodeKind.SUBSECTION)
    assert result_sub.children[-1].kind is IRNodeKind.CONTENT
    assert result_sub.children[-1].text == "Kalastusluvat myontaa elinvoimakeskus edelleen."
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_omission_merge_fallback"


def test_subsection_replace_strict_blocks_omission_merge_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sec = _sec("30", _sub("1", _content("Kalastusluvat myontaa elinvoimakeskus.")))
    body = _body(sec)
    sec_path = [("section", "30")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    pathologies: list[SourcePathology] = []

    replace_sub = _sub(
        "1",
        _intro("Uusi johdanto:"),
        _content("Kalastusluvat myontaa elinvoimakeskus edelleen."),
        IRNode(kind=IRNodeKind.OMISSION),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="30", target_paragraph=1)

    monkeypatch.setattr("lawvm.finland.apply_subsection_ops._merge_intro_only_subsection_replace", lambda *a, **k: None)
    monkeypatch.setattr(
        "lawvm.finland.apply_subsection_ops._merge_subsection_accumulate_inner_omission_ir",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "lawvm.finland.apply_subsection_ops._merge_subsection_with_omission_ir",
        lambda *a, **k: None,
    )

    result = _apply_subsection_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        None,
        _FINLEX_ORACLE,
        "30 § 1 mom",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "subsection_replace_omission_merge_fallback"


def test_subsection_replace_promotes_content_only_intro_and_preserves_items() -> None:
    sec = _sec(
        "20",
        _sub("1", _content("Yleinen johdanto.")),
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriö voi viran puolesta muuttaa lupapäätöstä, jos:"),
            _para("1", "olosuhteet ovat muuttuneet"),
            _para("2", "perusteet olivat toisenlaiset"),
        ),
        _sub(
            "3",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriö voi viran puolesta peruuttaa luvan, jos:"),
            _para("1", "hakija on antanut virheellisiä tietoja"),
            _para("2", "lupamääräyksiä on rikottu"),
        ),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    replace_sub = _sub("1", _content("Lupaviranomainen voi viran puolesta muuttaa lupapäätöstä, jos:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=2, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        replace_sub,
        _sec("20", replace_sub),
        "20 § 2 mom johd",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    from lawvm.core.tree_ops import resolve as tree_resolve

    result_sec = tree_resolve(result.ir, (("section", "20"),))
    assert result_sec is not None
    result_sub2 = next(c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
    result_sub3 = next(c for c in result_sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "3")
    intro2 = next(c for c in result_sub2.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT))
    intro3 = next(c for c in result_sub3.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT))
    assert irnode_to_text(intro2) == "Lupaviranomainen voi viran puolesta muuttaa lupapäätöstä, jos:"
    assert irnode_to_text(intro3) == "Ministeriö voi viran puolesta peruuttaa luvan, jos:"
    assert pathologies == []


def test_johd_replace_does_not_fallback_to_section_intro_for_missing_subsection_target() -> None:
    sec = _sec(
        "20",
        IRNode(kind=IRNodeKind.INTRO, text="Pykälän johdanto."),
        _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Momentin 1 johdanto:")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Uusi kohdekohtainen johdanto:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=2, target_special="johd")

    result = _apply_special_targets(
        state, op, sec_path, sec, subsecs, amend_sub, _sec("20", amend_sub), "20 § 2 mom johd"
    )

    assert result is None


def test_johd_replace_without_subsection_target_does_not_widen_to_section_intro() -> None:
    sec = _sec(
        "20",
        IRNode(kind=IRNodeKind.INTRO, text="Pykälän johdanto."),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Uusi pykälän johdanto."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_special="johd")

    result = _apply_special_targets(
        state, op, sec_path, sec, subsecs, amend_sub, _sec("20", amend_sub), "20 § johd"
    )

    assert result is None


def test_johd_replace_reports_intro_list_shape_rebound_when_carrier_subsection_is_used() -> None:
    sec = _sec(
        "20",
        _sub("2", IRNode(kind=IRNodeKind.INTRO, text="Momentin 2 johdanto:")),
        _sub("3", _para("1", "first item"), _para("2", "second item")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Uusi kohdekohtainen johdanto:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=1, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        _sec("20", amend_sub),
        "20 § 1 mom johd",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert [p.code for p in pathologies] == [
        "SUBSECTION_TARGET_REBOUND",
        "ITEM_TARGET_STRUCTURE_ABSENT",
    ]
    assert pathologies[0].detail["rebound_kind"] == "intro_list_moment_shape"


def test_johd_replace_rejects_intro_list_shape_rebound_in_strict_mode() -> None:
    sec = _sec(
        "20",
        _sub("2", IRNode(kind=IRNodeKind.INTRO, text="Momentin 2 johdanto:")),
        _sub("3", _para("1", "first item"), _para("2", "second item")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Uusi kohdekohtainen johdanto:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=1, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        _sec("20", amend_sub),
        "20 § 1 mom johd",
        source_pathologies_out=pathologies,
        strict_profile=default_finland_strict_profile(),
    )

    assert result is None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "intro_list_moment_shape"


def test_johd_replace_reports_missing_exact_subsection_label_rebound_when_intro_target_widens() -> None:
    sec = _sec(
        "20",
        _sub("2", IRNode(kind=IRNodeKind.INTRO, text="Momentin 2 johdanto:")),
        _sub("3", IRNode(kind=IRNodeKind.INTRO, text="Momentin 3 johdanto:")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("1", IRNode(kind=IRNodeKind.INTRO, text="Uusi kohdekohtainen johdanto:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=1, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        _sec("20", amend_sub),
        "20 § 1 mom johd",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    assert [p.code for p in pathologies] == ["SUBSECTION_TARGET_REBOUND"]
    assert pathologies[0].detail["rebound_kind"] == "missing_exact_subsection_label"


def test_johd_replace_reports_absent_target_when_amend_intro_missing() -> None:
    sec = _sec(
        "20",
        _sub("2", IRNode(kind=IRNodeKind.INTRO, text="Momentin 2 johdanto:")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("2", _para("1", "plain body only"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=2, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        _sec("20", amend_sub),
        "20 § 2 mom johd",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert [p.code for p in pathologies] == ["ITEM_TARGET_STRUCTURE_ABSENT"]


def test_johd_replace_accepts_content_carrier_as_live_intro_host() -> None:
    from lawvm.core.tree_ops import resolve as tree_resolve

    sec = _sec(
        "20",
        _sub("2", _content("Momentin 2 plain body without explicit intro")),
    )
    body = _body(sec)
    sec_path = [("section", "20")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub("2", _intro("Uusi kohdekohtainen johdanto:"))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="20", target_paragraph=2, target_special="johd")
    pathologies: list[SourcePathology] = []

    result = _apply_special_targets(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        _sec("20", amend_sub),
        "20 § 2 mom johd",
        source_pathologies_out=pathologies,
    )

    assert result is not None
    updated = tree_resolve(result.ir, tuple(sec_path))
    assert updated is not None
    updated_sub = next(c for c in updated.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2")
    assert updated_sub.children[0].kind == IRNodeKind.INTRO
    assert updated_sub.children[0].text == "Uusi kohdekohtainen johdanto:"
    assert pathologies == []


def test_subsection_replace_missing_target_four_does_not_insert_before_existing_five_and_six() -> None:
    """Missing-target REPLACE must not synthesize a new subsection into a gap."""
    sec = _sec(
        "13",
        _sub("1", _content("Momentti 1.")),
        _sub("2", _content("Momentti 2.")),
        _sub("3", _content("Momentti 3.")),
        _sub("5", _content("Momentti 5.")),
        _sub("6", _content("Momentti 6.")),
    )
    body = _body(sec)
    sec_path = [("section", "13")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

    replace_sub = _sub("4", _content("Momentti 4."))
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="13", target_paragraph=4)

    result = _apply_subsection_replace(
        state, op, sec_path, sec, subsecs, replace_sub, None, _FINLEX_ORACLE, "13 § 4 mom"
    )
    assert result is None


def test_subsection_replace_exact_bound_payload_drops_stale_unmatched_item_tail() -> None:
    sec = _sec(
        "18",
        _sub("1", _content("Rangaistus metsärikoksesta säädetään rikoslaissa.")),
        _sub(
            "2",
            _intro("Joka tahallaan tai huolimattomuudesta"),
            _para("1", "old item 1"),
            _para("2", "old item 2"),
            _para("3", "old item 3"),
            _para("4", "old item 4"),
            _para("5", "old item 5"),
            _para("6", "old stale item 6"),
            IRNode(kind=IRNodeKind.WRAP_UP, text="old wrap-up"),
        ),
    )
    body = _body(sec)
    sec_path = [("section", "18")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub(
        "1",
        _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
        IRNode(kind=IRNodeKind.OMISSION),
        _para("1", "new item 1"),
        _para("2", "new item 2"),
        _para("3", "new item 3"),
        _para("4", "new item 4"),
        _para("5", "new item 5"),
        IRNode(kind=IRNodeKind.WRAP_UP, text="new wrap-up"),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="18", target_paragraph=2)
    op.has_exact_bound_payload = True

    result = _apply_subsection_replace(
        state, op, sec_path, sec, subsecs, amend_sub, _sec("18", amend_sub), _FINLEX_ORACLE, "18 § 2 mom"
    )

    result = _modified(state, result)
    live = result.find_section("18")
    assert live is not None
    live_subsecs = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    target = live_subsecs[1]
    assert [child.label for child in target.children if child.kind is IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4", "5"]
    assert "old stale item 6" not in irnode_to_text(target)
    assert "new wrap-up" in irnode_to_text(target)


def test_subsection_replace_without_wrapup_keeps_unmatched_item_tail() -> None:
    sec = _sec(
        "18",
        _sub("1", _content("Rangaistus metsärikoksesta säädetään rikoslaissa.")),
        _sub(
            "2",
            _intro("Joka tahallaan tai huolimattomuudesta"),
            _para("1", "old item 1"),
            _para("2", "old item 2"),
            _para("3", "old item 3"),
            _para("4", "old item 4"),
            _para("5", "old item 5"),
            _para("6", "old stale item 6"),
            IRNode(kind=IRNodeKind.WRAP_UP, text="old wrap-up"),
        ),
    )
    body = _body(sec)
    sec_path = [("section", "18")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_sub = _sub(
        "1",
        _intro("Joka tahallaan tai törkeästä huolimattomuudesta"),
        IRNode(kind=IRNodeKind.OMISSION),
        _para("1", "new item 1"),
        _para("2", "new item 2"),
        _para("3", "new item 3"),
        _para("4", "new item 4"),
        _para("5", "new item 5"),
    )
    state = _make_state(body)
    op = _op(op_type="REPLACE", target_section="18", target_paragraph=2)

    result = _apply_subsection_replace(
        state, op, sec_path, sec, subsecs, amend_sub, _sec("18", amend_sub), _FINLEX_ORACLE, "18 § 2 mom"
    )

    result = _modified(state, result)
    live = result.find_section("18")
    assert live is not None
    live_subsecs = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    target = live_subsecs[1]
    assert [child.label for child in target.children if child.kind is IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4", "5", "6"]
    assert "old stale item 6" in irnode_to_text(target)


# ---------------------------------------------------------------------------
# Kohta→subsection fallback: no-duplicate-label regression tests
# ---------------------------------------------------------------------------


class TestKohtaSubsectionFallbackNoDuplicate:
    """Regression tests for the kohta→subsection fallback paths in _apply_special_targets.

    The bug: when an amendment targeting "N § M mom K kohta" carries an amend_sub
    with no paragraph children, the code falls back to operating at subsection level
    (via _apply_special_targets after earlier handlers return None).
    Before the fix, REPLACE used insert_after_nth (creating a duplicate subsection
    label), and INSERT with matching label also used insert_after_nth (same problem).
    """

    def _make_content_only_amend_sub(self, label: str, text: str = "new text") -> IRNode:
        """amend_sub with content only — no PARAGRAPH children → triggers fallback."""
        return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=(_content(text),))

    # ------------------------------------------------------------------
    # Test 1: REPLACE N § 2 mom K kohta — content-only amend_sub labeled "1"
    # The target subsection is labeled "2". The amend_sub is labeled "1"
    # (as extracted from the amendment XML). Before the fix, insert_after_nth
    # would have added a second subsection "1", creating a duplicate label.
    # After the fix, replace_nth is used and the label is corrected to "2".
    # ------------------------------------------------------------------
    def test_replace_kohta_content_only_no_duplicate_subsection(self):
        """REPLACE targeting mom 2 kohta 1 with content-only amend_sub labeled '1'
        must replace subsection 2 in-place, not insert a new subsection."""
        sub1 = _sub("1", _content("first moment text"))
        sub2 = _sub("2", _content("second moment text"))
        sec = _sec("5", sub1, sub2)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "5")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        # amend_sub has label "1" (amendment XML artifact) but targets mom=2
        amend_sub = self._make_content_only_amend_sub("1", "replaced second moment")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="5", target_paragraph=2, target_item="1")

        result = _apply_special_targets(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "5 § 2 mom 1 k")
        assert result is None

    # ------------------------------------------------------------------
    # Test 2: INSERT N § 1 mom K kohta with amend_sub labeled "1" where
    # subsection:1 already exists (target has paragraphs).
    # Before the fix, insert_after_nth would create a second subsection "1".
    # After the fix, matching labels trigger replace_nth.
    # ------------------------------------------------------------------
    def test_insert_kohta_same_label_as_target_no_duplicate_subsection(self):
        """INSERT targeting mom 1 kohta 3 with amend_sub labeled '1' matching
        the existing subsection label must replace, not insert-after, to avoid
        a duplicate-labeled subsection."""
        sub1 = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                _para("2", "item two"),
            ),
        )
        sec = _sec("3", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "3")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        # amend_sub: content-only (no paragraphs), label "1" = same as target
        amend_sub = self._make_content_only_amend_sub("1", "new content-only moment")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="INSERT", target_section="3", target_paragraph=1, target_item="3")

        result = _apply_special_targets(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "3 § 1 mom ins 3 k")
        assert result is None

    # ------------------------------------------------------------------
    # Test 3: REPLACE N § 2 mom K kohta — label preservation when amend_sub
    # label differs from target_sub label (the label-preservation fix at ~999).
    # amend_sub.label = "1", target subsection label = "2".
    # The result subsection must have label "2", not "1".
    # ------------------------------------------------------------------
    def test_replace_kohta_content_only_preserves_target_label(self):
        """When REPLACE content-only fallback applies, the resulting subsection
        must carry the target subsection's label, not the amendment's label."""
        sub1 = _sub("1", _content("first moment"))
        sub2 = _sub("2", _content("second moment"))
        sub3 = _sub("3", _content("third moment"))
        sec = _sec("7", sub1, sub2, sub3)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "7")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        # amend_sub label "1" — wrong label for the mom=2 target
        amend_sub = self._make_content_only_amend_sub("1", "corrected second moment")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="REPLACE", target_section="7", target_paragraph=2, target_item="1")

        result = _apply_special_targets(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "7 § 2 mom 1 k")
        assert result is None

    # ------------------------------------------------------------------
    # Test 4: INSERT N § 1 mom K kohta with amend_sub having a DIFFERENT label
    # than the target — should insert_after_nth (not a duplicate scenario).
    # This confirms the happy path still works after the fix.
    # ------------------------------------------------------------------
    def test_insert_kohta_different_label_still_inserts(self):
        """INSERT with amend_sub label differing from the target subsection label
        must still use insert_after_nth (the non-duplicate happy path)."""
        sub1 = IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(
                _para("1", "item one"),
                _para("2", "item two"),
            ),
        )
        sec = _sec("4", sub1)
        body = _body(sec)
        state = _make_state(body)
        sec_path = [("section", "4")]
        subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

        # amend_sub label "2" — different from target subsection "1"
        amend_sub = self._make_content_only_amend_sub("2", "new second moment")
        muutos_ir = IRNode(kind=IRNodeKind.SECTION, children=(amend_sub,))
        op = _op(op_type="INSERT", target_section="4", target_paragraph=1, target_item="3")

        result = _apply_special_targets(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "4 § 1 mom ins 3 k")
        assert result is None


def test_item_repeal_does_not_delete_content_only_subsection_when_item_structure_is_absent() -> None:
    sec = _sec("5", _sub("1", _content("Content-only moment.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    op = _op(op_type="REPEAL", target_section="5", target_paragraph=1, target_item="1")

    result = _apply_item_repeal(state, op, sec_path, sec, subsecs, _FINLEX_ORACLE, "5 § 1 mom 1 k repeal")

    assert result is state


def test_item_replace_does_not_widen_into_content_only_subsection_replace() -> None:
    sec = _sec("5", _sub("1", _content("Content-only moment.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = _para("1", "New item text")
    amend_sub = _sub("1", _content("Intro."), amend_para)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="REPLACE", target_section="5", target_paragraph=1, target_item="1")

    result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "5 § 1 mom 1 k replace")

    assert result is None


def test_item_replace_does_not_append_new_subsection_for_oor_item_target() -> None:
    sec = _sec("5", _sub("1", _para("1", "Item one.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = _para("1", "Replacement item")
    amend_sub = _sub("2", amend_para)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="REPLACE", target_section="5", target_paragraph=2, target_item="1")

    result = _apply_item_replace(state, op, sec_path, sec, subsecs, amend_sub, muutos_ir, "5 § 2 mom 1 k replace")

    assert result is None


def test_item_replace_reports_missing_numeric_anchor_when_exact_recovery_falls_through() -> None:
    sec = _sec("5", _sub("1", _para("1", "Item one."), _para("4", "Item four.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = _para("3", "Replacement item three")
    amend_sub = _sub("1", amend_para)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="REPLACE", target_section="5", target_paragraph=1, target_item="3")
    pathologies: list[SourcePathology] = []

    result = _apply_item_replace(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        "5 § 1 mom 3 k replace",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert pathologies == []


def test_item_insert_does_not_upsert_occupied_letter_suffix_slot() -> None:
    sec = _sec("5", _sub("1", _para("1a", "Existing slot.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = _para("1a", "Inserted slot text")
    amend_sub = _sub("1", amend_para)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="INSERT", target_section="5", target_paragraph=1, target_item="1a")
    pathologies: list[SourcePathology] = []

    result = _apply_item_insert(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        "5 § 1 mom 1a k insert",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert [p.code for p in pathologies] == ["ITEM_TARGET_SLOT_OCCUPIED", "ITEM_TARGET_STRUCTURE_ABSENT"]
    assert pathologies[0].detail["occupied_item_label"] == "1a"


def test_item_insert_compound_insert_emits_pathology() -> None:
    sec = _sec("5", _sub("1", _para("1", "Existing slot.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = _para("1", "Inserted slot text")
    amend_sp = IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("Inserted subslot text"),))
    amend_sub = _sub("1", amend_para, amend_sp)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="INSERT", target_section="5", target_paragraph=1, target_item="1a")
    pathologies: list[SourcePathology] = []

    result = _apply_item_insert(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        "5 § 1 mom 1a k insert",
        source_pathologies_out=pathologies,
    )
    assert result is None
    assert [p.code for p in pathologies] == ["ITEM_TARGET_STRUCTURE_ABSENT"]


def test_item_insert_compound_insert_reports_absent_target_when_letter_missing() -> None:
    sec = _sec("5", _sub("1", _para("1", "Existing slot.")))
    body = _body(sec)
    state = _make_state(body)
    sec_path = [("section", "5")]
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    amend_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        children=(
            _content("Inserted slot text"),
            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="a", children=(_content("Inserted subslot text"),)),
        ),
    )
    amend_sub = _sub("1", amend_para)
    muutos_ir = _sec("5", amend_sub)
    op = _op(op_type="INSERT", target_section="5", target_paragraph=1, target_item="1b")
    pathologies: list[SourcePathology] = []

    result = _apply_item_insert(
        state,
        op,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        "5 § 1 mom 1b k insert",
        source_pathologies_out=pathologies,
    )

    assert result is None
    assert [p.code for p in pathologies] == ["ITEM_TARGET_STRUCTURE_ABSENT"]
    assert pathologies[0].detail["live_has_paragraphs"] is True
    assert pathologies[0].detail["amend_has_paragraphs"] is True
