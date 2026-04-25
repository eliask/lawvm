from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.core.elaboration_context import (
    PayloadElaborationContext,
    ReplayLookups,
    TargetContext,
    TargetUnitKind,
    build_payload_elaboration_context,
)
from lawvm.finland.apply_runtime_support import _build_subsection_override_map
from lawvm.finland.helpers import _norm_row_anchor_text
from lawvm.finland.payload_normalize import SubsectionSlotMap
from lawvm.finland.ops import AmendmentOp, ReplayProfile, get_replay_profile
from lawvm.finland.payload_normalize import (
    PayloadCompletenessWitness,
    ElaborationObservation,
    GroupPayloadNormalizationResult,
    SparseSubsectionElaborationResult,
    SourcePathology,
    _assign_subsection_slots,
    _build_subsection_slot_assignment,
    _collect_subsection_slot_inputs,
    _collapse_intro_list_subsections_inside_section_ir,
    _prune_carried_subsections_outside_single_target_moment_ir,
    _align_sparse_omission_subsections_to_live,
    _fold_intro_list_continuation_subsection_before_omission,
    _normalize_item_like_target,
    _prune_container_payload_sections_shadowed_by_standalone_targets,
    _rebase_item_targets_to_sparse_slot_labels,
    SparsePayloadSlotBinding,
    SubsectionSlotAssignmentResult,
    prepare_payload_surface,
    elaborate_payload_against_live,
    summarize_slot_assignment,
)


def _observations(
    result: Any,
) -> tuple[ElaborationObservation, ...]:
    observations = result.elaboration_observations
    assert observations is not None
    return tuple(observations)


def _pathologies(
    result: Any,
) -> tuple[SourcePathology, ...]:
    pathologies = result.source_pathologies
    assert pathologies is not None
    return tuple(pathologies)


def _completeness(
    result: Any,
) -> PayloadCompletenessWitness:
    completeness = result.payload_completeness
    assert completeness is not None
    return completeness


def _slot_assignment_result(
    result: Any,
) -> SubsectionSlotAssignmentResult:
    assignment = result.slot_assignment
    assert assignment is not None
    return assignment


def _replay_profile_stub() -> ReplayProfile:
    return get_replay_profile("legal_pit")


def _muutos_ir(
    result: Any,
) -> IRNode:
    muutos_ir = result.muutos_ir
    assert muutos_ir is not None
    return muutos_ir


def _mock_ctx(
    target_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str] = None,
    *,
    live_node: Optional[IRNode] = None,
    parent_node: Optional[IRNode] = None,
) -> PayloadElaborationContext:
    """Build a minimal PayloadElaborationContext for tests.

    Replaces the old ``SimpleNamespace(find_section=...)`` mock pattern.
    """
    live_subsections = ()
    subsection_by_label = {}
    item_index = {}
    row_anchor_index = {}
    subsection_slots = ()
    if live_node is not None:
        from lawvm.core.elaboration_context import _make_subsection_slot

        subs = []
        ordinal = 0
        for child in live_node.children:
            if child.kind == IRNodeKind.SUBSECTION:
                ordinal += 1
                subs.append(child)
                if child.label:
                    subsection_by_label[child.label] = child
                for grandchild in child.children:
                    if grandchild.kind == IRNodeKind.PARAGRAPH:
                        if grandchild.label:
                            item_index[(ordinal, grandchild.label)] = grandchild
                        row_anchor = grandchild.attrs.get("row_anchor", "")
                        if row_anchor:
                            row_anchor_index[row_anchor] = grandchild
        live_subsections = tuple(subs)
        subsection_slots = tuple(_make_subsection_slot(i + 1, sub) for i, sub in enumerate(subs))

    lookups = ReplayLookups(
        snapshot_rev=0,
        unique_section_paths={},
        chapter_members={},
        part_members={},
        all_section_labels=frozenset(),
    )

    return PayloadElaborationContext(
        target_unit_kind=target_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        live_node=live_node,
        parent_node=parent_node,
        subsection_slots=subsection_slots,
        live_subsections=live_subsections,
        subsection_by_label=subsection_by_label,
        item_index=item_index,
        row_anchor_index=row_anchor_index,
        container_member_labels=None,
        lookups=lookups,
    )


def test_payload_normalize_item_like_target_rewrites_flat_item_as_subsection_item() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=10,
        lo=LegalOperation(
            op_id="t1",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"), ("subsection", "10"))),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="10) uusi kohta"),),
    )

    got = _normalize_item_like_target(ctx, op, IRNode(kind=IRNodeKind.SECTION, label="2", children=(amend_sub,)))

    assert got.lo is not None
    assert dict(got.lo.target.path) == {"section": "2", "subsection": "1", "item": "10"}
    assert got.target_guessing_provenance_tags == ("normalize_item_like_target",)


def test_elaborate_payload_against_live_observes_item_like_target_rewrite() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=10,
        lo=LegalOperation(
            op_id="t1",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"), ("subsection", "10"))),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="10) uusi kohta"),),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        IRNode(kind=IRNodeKind.SECTION, label="2", children=(amend_sub,)),
        set(),
    )

    observations = _observations(got)
    normalize_obs = [obs for obs in observations if obs.kind == "ELAB.NORMALIZE_ITEM_LIKE_TARGET"]
    assert len(normalize_obs) == 1
    detail = normalize_obs[0].detail
    assert detail is not None
    assert detail["rewrite_count"] == 1
    assert detail["rewrites"][0]["target_item"] == "10"
    rebase_obs = [obs for obs in observations if obs.kind == "ELAB.REBASE_ITEM_TARGET_TO_SPARSE_SLOT_LABEL"]
    assert len(rebase_obs) == 1
    rebase_detail = rebase_obs[0].detail
    assert rebase_detail is not None


def test_build_payload_elaboration_context_normalizes_row_anchor_index() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"row_anchor": "Käräjäoikeuden, Helsinki:"},
                    ),
                ),
            ),
        ),
    )
    target_ctx = TargetContext(
        target_unit_kind="section",
        target_norm="2",
        target_chapter=None,
        node_path=(("section", "2"),),
        parent_path=(),
        live_node=live_sec,
        parent_node=IRNode(kind=IRNodeKind.BODY, children=(live_sec,)),
        sibling_labels=("2",),
        subsection_slots=(),
    )
    lookups = ReplayLookups(
        snapshot_rev=1,
        unique_section_paths={},
        chapter_members={},
        part_members={},
        all_section_labels=frozenset({"2"}),
    )

    ctx = build_payload_elaboration_context(
        target_ctx,
        lookups,
        row_anchor_normalizer=_norm_row_anchor_text,
    )

    assert "helsinki" in ctx.row_anchor_index


def test_build_payload_elaboration_context_indexes_item_children() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.ITEM, label="a", attrs={"row_anchor": "Example"}),),
            ),
        ),
    )
    target_ctx = TargetContext(
        target_unit_kind="section",
        target_norm="2",
        target_chapter=None,
        node_path=(("section", "2"),),
        parent_path=(),
        live_node=live_sec,
        parent_node=IRNode(kind=IRNodeKind.BODY, children=(live_sec,)),
        sibling_labels=("2",),
        subsection_slots=(),
    )
    lookups = ReplayLookups(
        snapshot_rev=1,
        unique_section_paths={},
        chapter_members={},
        part_members={},
        all_section_labels=frozenset({"2"}),
    )

    ctx = build_payload_elaboration_context(
        target_ctx,
        lookups,
        row_anchor_normalizer=_norm_row_anchor_text,
    )

    assert ctx.item_index[(1, "a")].kind == IRNodeKind.ITEM
    assert ctx.row_anchor_index["example"].kind == IRNodeKind.ITEM




def test_slot_assignment_resolve_apply_subsection_ir_does_not_singleton_fallback_from_muutos_ir() -> None:
    op = AmendmentOp(
        op_id="op0",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
        source_statute="2020/1",
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="payload"),)
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )

    muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="14", children=(amend_sub,))

    assert assignment.resolve_apply_subsection_ir(op, None) is None


def test_slot_assignment_resolve_apply_subsection_ir_for_stable_op_id_does_not_singleton_fallback_from_muutos_ir() -> None:
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="payload"),)
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )

    muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="14", children=(amend_sub,))

    assert assignment.resolve_apply_subsection_ir_for_stable_op_id("missing", None) is None


def test_payload_normalize_item_like_target_preserves_sparse_real_subsections() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "5", live_node=live_sec)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="5",
        target_paragraph=3,
        lo=LegalOperation(
            op_id="t2",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "5"), ("subsection", "3"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Velvollinen tekemään ilmoituksen vaalirahoituksesta on:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi myöhempi momentti."),),
            ),
        ),
    )

    got = _normalize_item_like_target(ctx, op, muutos_ir)

    assert got.lo is not None
    assert dict(got.lo.target.path) == {"section": "5", "subsection": "3"}


def test_payload_normalize_item_like_target_keeps_real_subsection_when_group_has_item_ops() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="123",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Luettelo on seuraava:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="8"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="9"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="15"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "123", live_node=live_sec)
    item_op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="123",
        target_paragraph=1,
        target_item="8",
    )
    insert_op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="123",
        target_paragraph=2,
        lo=LegalOperation(
            op_id="t123",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "123"), ("subsection", "2"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="123",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Luettelo on seuraava:"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="8"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="9"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="15"),
                ),
            ),
        ),
    )

    got = _normalize_item_like_target(ctx, insert_op, muutos_ir, [item_op, insert_op])

    assert got.lo is not None
    assert dict(got.lo.target.path) == {"section": "123", "subsection": "2"}
    assert got.target_guessing_provenance_tags == ()


def test_align_sparse_omission_subsections_to_live_uses_mixed_group_logical_targets() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="70",
        children=tuple(IRNode(kind=IRNodeKind.SUBSECTION, label=str(i)) for i in range(1, 6)),
    )
    ctx = _mock_ctx("section", "70", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="70",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="new 2 mom"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="new 3 mom"),)),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="70",
            target_paragraph=2,
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="70",
            target_paragraph=3,
            target_item="4",
        ),
    ]

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "70", None, muutos_ir, ops)

    assert changed is True
    assert got is not None
    subsections = [child for child in got.children if child.kind == IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["2", "3"]


def test_payload_normalize_keeps_new_sections_in_container_with_standalone_target() -> None:
    """New sections (not in live master) should be kept in the container payload.

    When a whole-chapter replacement introduces new sections AND those sections
    also appear in standalone_section_targets, the container pruning must NOT
    drop them.  The standalone PEG op will redundantly replace the section after
    the container op inserts the chapter — that is harmless.  Pruning them loses
    the section entirely (Bug C from Rikoslaki investigation).
    """
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
        ),
    )
    ctx = _mock_ctx("chapter", "3", live_node=live_container)
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="26"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(ctx, "chapter", "3", muutos_ir, {"26"}
    )

    # Section "26" is NEW (not in live_member_labels {"14","15"}).
    # It must be kept — no pruning should occur.
    assert changed is False
    assert got is not None
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == ["14", "15", "26"]


def test_payload_normalize_keeps_existing_standalone_sections_in_container() -> None:
    """Existing sections with standalone targets remain in the container payload.

    When a section already exists in the master chapter AND has a standalone PEG
    op, it should be kept in the container so the whole-chapter REPLACE includes
    it.  The standalone op will then update the section in-place.
    """
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
        ),
    )
    ctx = _mock_ctx("chapter", "3", live_node=live_container)
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(ctx, "chapter", "3", muutos_ir, {"14"}
    )

    # Section "14" exists in live — kept in container (no pruning).
    assert changed is False
    assert got is not None
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == ["14", "15"]


def test_payload_normalize_keeps_mix_of_new_and_existing_standalone_sections() -> None:
    """Mix of new and existing standalone-target sections: all kept in container.

    Exercises the scenario from Rikoslaki amendment 1990/769 where a whole-chapter
    replacement introduces new sections (e.g. 28/9b, 28/11-14) alongside existing
    sections that have standalone PEG ops.
    """
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="28",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="28 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
            IRNode(kind=IRNodeKind.SECTION, label="9"),
        ),
    )
    ctx = _mock_ctx("chapter", "28", live_node=live_container)
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="28",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="28 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
            IRNode(kind=IRNodeKind.SECTION, label="9"),
            IRNode(kind=IRNodeKind.SECTION, label="9b"),
            IRNode(kind=IRNodeKind.SECTION, label="11"),
            IRNode(kind=IRNodeKind.SECTION, label="12"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(ctx, "chapter", "28", muutos_ir, {"9", "9b", "11", "12"}
    )

    # Section "9" exists in live — kept.  Sections "9b","11","12" are NEW — kept.
    # No sections pruned.
    assert changed is False
    assert got is not None
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == [
        "1",
        "2",
        "9",
        "9b",
        "11",
        "12",
    ]


def test_payload_normalize_aligns_sparse_omission_subsections_to_live() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="14b",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    ctx = _mock_ctx("section", "14b", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14b",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "14b", None, muutos_ir)

    assert changed is True
    assert got is not None
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["1", "3"]
def test_payload_normalize_aligns_sparse_omission_subsections_with_duplicate_targets() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="11a",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5"),
        ),
    )
    ctx = _mock_ctx("section", "11a", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="11a",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.SUBSECTION),
            IRNode(kind=IRNodeKind.SUBSECTION),
            IRNode(kind=IRNodeKind.SUBSECTION),
            IRNode(kind=IRNodeKind.SUBSECTION),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=3),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=4),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5),
    ]

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "11a", None, muutos_ir, ops)

    assert changed is True
    assert got is not None
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["3", "4", "5", "6"]


def test_payload_normalize_aligns_sparse_middle_block_to_explicit_targets() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5"),
        ),
    )
    ctx = _mock_ctx("section", "5", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 3 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 4 mom"),)
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=3),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=4),
    ]

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "5", None, muutos_ir, ops)

    assert changed is True
    assert got is not None
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["3", "4"]


def test_payload_normalize_aligns_explicit_sparse_omission_target_without_live_section() -> None:
    ctx = _mock_ctx("section", "20", live_node=None)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="20",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="20",
            target_paragraph=2,
        )
    ]

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "20", None, muutos_ir, ops)

    assert changed is True
    assert got is not None
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["2"]


def test_payload_normalize_does_not_relabel_item_only_sparse_omission_payload() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    ctx = _mock_ctx("section", "13", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 5 kohta"),),
            ),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="13",
            target_paragraph=1,
            target_item="5",
        )
    ]

    got, changed = _align_sparse_omission_subsections_to_live(ctx, "section", "13", None, muutos_ir, ops)

    assert changed is False
    assert got is muutos_ir
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["1", "5"]


def test_fold_intro_list_continuation_preserves_terminal_real_second_moment() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="17",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="17 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Kunnan on huolehdittava seuraavista palveluista:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Kunnan on myös huolehdittava muista palveluista."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = _fold_intro_list_continuation_subsection_before_omission("section", None, muutos_ir)

    assert got is not None
    assert [(c.kind, c.label) for c in got.children] == [
        (IRNodeKind.NUM, None),
        (IRNodeKind.SUBSECTION, "1"),
        (IRNodeKind.SUBSECTION, "2"),
        (IRNodeKind.OMISSION, None),
    ]


def test_fold_intro_list_continuation_merges_terminal_continuation_for_single_target() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="48",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="48 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Joka tahallaan tai huolimattomuudesta"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="on tuomittava tuotantotukisäännösten rikkomisesta."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="48", target_paragraph=1)]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1"]
    assert irnode_to_text(subs[0]).strip().endswith("tuotantotukisäännösten rikkomisesta.")


def test_fold_intro_list_continuation_skips_fold_when_continuation_is_explicit_target() -> None:
    """Content-only subsection 2 that IS an explicit REPLACE target must not be folded.

    This is the kaivoslaki §156 / amendment 2023/505 shape:
      sub 1 (intro + items)
      sub 2 (content-only) ← explicit REPLACE 2 target
      omission
      sub 3
    The fold must NOT fire for sub 2 because it is a real independent moment.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="156",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="156 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Kaivosviranomaisen on:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Kaivosviranomaisen on lisäksi kiellettävä sellainen."),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Kaivosviranomaisen on valvottava."),),
            ),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="156", target_paragraph=1),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="156", target_paragraph=2),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="156", target_paragraph=4),
    ]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    # sub 1 and sub 2 must both survive as separate subsections
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "2", "3"]
    # sub 1 must NOT have the continuation text appended
    sub1_text = irnode_to_text(subs[0])
    assert "lisäksi kiellettävä" not in sub1_text
    # sub 2 must retain its own content
    assert "lisäksi kiellettävä" in irnode_to_text(subs[1])


def test_fold_intro_list_continuation_still_folds_when_continuation_is_not_a_target() -> None:
    """Content-only subsection that is NOT an explicit target should still be folded.

    Same structural shape as the kaivoslaki §156 case, but ops only target sub 1.
    The continuation sub 2 is an encoding artifact completing sub 1 — fold must fire.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="48",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="48 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Joka tahallaan tai huolimattomuudesta"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="on tuomittava sakkoon tai vankeuteen."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Yritys on myös rangaistava."),),
            ),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="48", target_paragraph=1),
        # No op for target_paragraph=2 — sub 2 is a continuation artifact, not a real target
    ]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    # sub 2 must be folded into sub 1; only sub 1 and sub 3 survive
    assert [c.label for c in subs] == ["1", "3"]
    # sub 1 must contain the continuation text
    assert "tuomittava sakkoon" in irnode_to_text(subs[0])


def test_fold_intro_list_continuation_preserves_mixed_item_and_later_plain_target() -> None:
    """Do not fold when explicit item targets and a later plain target share the body.

    `2003/549 <- 2006/1293 / 149 §` has `1 momentin 1–3 kohta` plus a plain
    `4 momentti` target. The content-only continuation subsection is the real
    later moment, not a tail artifact of subsection 1.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="149",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="149 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Kunnallisella eläkelaitoksella on oikeus avata tekninen käyttöyhteys:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Tämän pykälän perusteella avatun teknisen käyttöyhteyden avulla saa hakea myös salassa pidettäviä tietoja."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1, target_item="1"),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1, target_item="2"),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1, target_item="3"),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=4),
    ]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "2"]
    assert "Tämän pykälän perusteella" not in irnode_to_text(subs[0])
    assert "Tämän pykälän perusteella" in irnode_to_text(subs[1])


def test_fold_intro_list_continuation_folds_lowercase_tail_artifact_with_later_real_subsection() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Liiketoimintakieltoon voidaan määrätä 2 §:ssä tarkoitettu henkilö,"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="jos hän on olennaisesti laiminlyönyt velvollisuuksiaan; tai"),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ja hänen toimintaansa on kokonaisuutena arvioiden pidettävä vahingollisena."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Laiminlyöntien olennaisuutta arvioitaessa on otettava huomioon..."),),
            ),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=1, target_item="1"),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2),
    ]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "3"]
    assert "ja hänen toimintaansa" in irnode_to_text(subs[0])
    assert "Laiminlyöntien olennaisuutta" in irnode_to_text(subs[1])


def test_elaborate_payload_rebinds_plain_moment_after_lowercase_tail_fold() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        target_item="1",
    )
    op_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=2,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Liiketoimintakieltoon voidaan määrätä 2 §:ssä tarkoitettu henkilö,"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="jos hän on olennaisesti laiminlyönyt velvollisuuksiaan; tai"),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ja hänen toimintaansa on kokonaisuutena arvioiden pidettävä vahingollisena."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Laiminlyöntien olennaisuutta arvioitaessa on otettava huomioon..."),),
            ),
        ),
    )

    prepared = prepare_payload_surface(ctx, [op_item, op_plain], muutos_ir, _replay_profile_stub(), None)
    assert prepared is not None
    assert [c.label for c in prepared.children if c.kind == IRNodeKind.SUBSECTION] == ["1", "3"]

    normalized = elaborate_payload_against_live(ctx, [op_item, op_plain], prepared, set())
    assignment = _slot_assignment_result(normalized)
    item_sub = assignment.for_op(normalized.group_ops[0])
    plain_sub = assignment.for_op(normalized.group_ops[1])
    assert item_sub is not None
    assert plain_sub is not None
    assert item_sub.label == "1"
    assert plain_sub.label == "3"


def test_normalize_group_payload_splits_sparse_single_subsection_across_consecutive_replaces() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmainen."),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha toinen momentti. Se jatkuu viela."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Arvo-osuustilille kirjattuun panttioikeuteen ei voida kirjata panttausta. Vanha viimeinen virke.",
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "6", live_node=live_sec)
    group_ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=2),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=3),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Uusi toinen momentti. Se jatkuu viela. "
                            "Arvo-osuustilille kirjattuun panttioikeuteen ei voida kirjata panttausta. "
                            "Uusi viimeinen virke."
                        ),
                    ),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, group_ops, muutos_ir, set())

    muutos_ir = _muutos_ir(got)
    slot_assignment = _slot_assignment_result(got)
    assert [c.label for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION] == ["2", "3"]
    assert got.subsec_map[id(got.group_ops[0])].label == "2"
    assert got.subsec_map[id(got.group_ops[1])].label == "3"
    assert slot_assignment.subsec_map[id(got.group_ops[0])].label == "2"
    assert slot_assignment.subsec_map[id(got.group_ops[1])].label == "3"
    assert "Uusi toinen momentti." in irnode_to_text(got.subsec_map[id(got.group_ops[0])])
    assert "Uusi viimeinen virke." in irnode_to_text(got.subsec_map[id(got.group_ops[1])])
    observations = _observations(got)
    assert [obs.kind for obs in observations] == [
        "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
        "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
    ]


def test_normalize_group_payload_splits_fused_restarted_subsection_across_consecutive_replaces() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="51",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    ctx = _mock_ctx("section", "51", live_node=live_sec)
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="51", target_paragraph=1),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="51", target_paragraph=2),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="51",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="51 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Ensimmäisen momentin johdanto:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="eka kohta"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="toka kohta"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Toisen momentin johdanto:"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="uusi eka kohta"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="uusi toka kohta"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="3)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="uusi kolmas kohta"),
                        ),
                    ),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, ops, muutos_ir, set())

    assert [op.description() for op in got.group_ops] == ["REPLACE 51 § 1 mom", "REPLACE 51 § 2 mom"]
    mapped0 = got.subsec_map.for_op(got.group_ops[0])
    mapped1 = got.subsec_map.for_op(got.group_ops[1])
    assert mapped0 is not None
    assert mapped1 is not None
    assert mapped0.label == "1"
    assert mapped1.label == "2"
    observations = _observations(got)
    assert [obs.kind for obs in observations] == ["ELAB.SPLIT_FUSED_RESTARTED_CONSECUTIVE"]


def test_prepare_group_payload_folds_row_like_sparse_subsections_before_omission_resolution() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),)
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Taulukko, euroa"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="1. lapsi 1 x 170"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="2. lapsi 2 x 170"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = prepare_payload_surface(
        ctx,
        [],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "3"]
    assert [c.label for c in subs[-1].children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]
    assert any(c.kind == IRNodeKind.OMISSION for c in subs[-1].children)


def test_normalize_group_payload_rewrites_partial_table_section_to_row_replaces() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"row_anchor": "ahvenanmaa"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Ahvenanmaa Maarianhamina Maarianhamina"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        attrs={"row_anchor": "seinäjoki"},
                        children=(
                            IRNode(kind=IRNodeKind.CONTENT, text="Seinäjoki Seinäjoki Seinäjoki Ilmajoki Jalasjärvi"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        attrs={"row_anchor": "tampere"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Tampere Tampere Tampere"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="1",
        source_statute="1995/1145",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Käräjäoikeus Kanslia (s = sivukanslia) Istunnot"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"row_anchor": "seinäjoki"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Seinäjoki Seinäjoki Seinäjoki Jalasjärvi"),),
                    ),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [o.description() for o in got.group_ops] == ["REPLACE 1 § 1 mom 2 kohta"]
    assert got.muutos_ir is not None
    sub = [child for child in got.muutos_ir.children if child.kind == IRNodeKind.SUBSECTION][0]
    paragraphs = [child for child in sub.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["2"]
    assert irnode_to_text(paragraphs[0]) == "Seinäjoki Seinäjoki Seinäjoki Jalasjärvi"


def test_normalize_group_payload_rewrites_named_row_repeal_with_fuzzy_anchor_match() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="10",
                        attrs={"row_anchor": "ylivieska"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Ylivieska Ylivieska Ylivieska"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="11",
                        attrs={"row_anchor": "haapajärvi"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Haapajärvi Haapajärvi Haapajärvi"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPEAL",
        target_kind=TargetKind.SECTION,
        target_section="1",
        source_statute="2006/148",
        named_row_targets=("haapajärven",),
    )

    got = elaborate_payload_against_live(ctx, [op], None, set())

    assert [o.description() for o in got.group_ops] == ["REPEAL 1 § 1 mom 11 kohta"]


def test_normalize_group_payload_rewrites_named_row_repeal_at_0_80_similarity_threshold() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="12",
                        attrs={"row_anchor": "iitti"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Iitti Iitti Iitti"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPEAL",
        target_kind=TargetKind.SECTION,
        target_section="1",
        source_statute="2000/78",
        named_row_targets=("iitin",),
    )

    got = elaborate_payload_against_live(ctx, [op], None, set())

    assert [o.description() for o in got.group_ops] == ["REPEAL 1 § 1 mom 12 kohta"]


def test_normalize_group_payload_rewrites_named_row_repeals_with_genitive_candidates() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        attrs={"row_anchor": "alavus"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Alavus Alavus Alavus"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="34",
                        attrs={"row_anchor": "lapua"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Lapua Lapua Lapua"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    ops = [
        AmendmentOp(
            op_type="REPEAL",
            target_kind=TargetKind.SECTION,
            target_section="1",
            source_statute="2003/558",
            named_row_targets=("alavuden", "lapuan"),
        )
    ]

    got = elaborate_payload_against_live(ctx, ops, None, set())

    assert [o.description() for o in got.group_ops] == [
        "REPEAL 1 § 1 mom 3 kohta",
        "REPEAL 1 § 1 mom 34 kohta",
    ]


def test_normalize_group_payload_rewrites_named_row_replace_with_live_anchor_match() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="10",
                        attrs={"row_anchor": "ylivieska"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Ylivieska Ylivieska Ylivieska"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="11",
                        attrs={"row_anchor": "haapajärvi"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Haapajärvi Haapajärvi Haapajärvi"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"row_anchor": "ylivieskan"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Ylivieskan käräjäoikeus Ylivieska Ylivieska"),),
                    ),
                ),
            ),
        ),
    )
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="1",
        source_statute="2006/148",
        named_row_targets=("ylivieskan",),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    muutos_ir = _muutos_ir(got)
    assert [o.description() for o in got.group_ops] == ["REPLACE 1 § 1 mom 10 kohta"]
    sub = next(child for child in muutos_ir.children if child.kind == IRNodeKind.SUBSECTION)
    paragraphs = [child for child in sub.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["10"]


def test_normalize_group_payload_rewrites_named_row_replace_from_content_only_section() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:",
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="11",
                        attrs={"row_anchor": "iisalmi"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Iisalmi Iisalmi Iisalmi Kiuruvesi"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.CONTENT,
                text="Käräjäoikeus Kanslia Istunnot (s=sivukanslia) Iisalmi Iisalmi Iisalmi",
            ),
        ),
    )
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="1",
        source_statute="2000/1040",
        named_row_targets=("iisalmen",),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    muutos_ir = _muutos_ir(got)
    assert [o.description() for o in got.group_ops] == ["REPLACE 1 § 1 mom 11 kohta"]
    sub = next(child for child in muutos_ir.children if child.kind == IRNodeKind.SUBSECTION)
    paragraphs = [child for child in sub.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["11"]
    assert irnode_to_text(paragraphs[0]) == "Iisalmi Iisalmi Iisalmi"


def test_prepare_group_payload_collapses_intro_list_subsections_inside_section_replace() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Elinkeinotoimintaa koskevat selvitykset"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmaisen momentin teksti"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Velallisen tulee toimittaa seuraavat selvitykset:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="1) yritysmuotoa koskeva selvitys;"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="2) toiminnan paattymista koskeva selvitys;"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="3) tilinpaatosta koskeva selvitys;"),),
            ),
        ),
    )
    ops = [AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")]

    got = _collapse_intro_list_subsections_inside_section_ir("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "2"]
    assert [c.label for c in subs[1].children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2", "3"]


def test_prepare_group_payload_prunes_carried_subsections_outside_single_target_moment() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="149",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="149 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Tekninen käyttöyhteys"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="oikeus avata tekninen käyttöyhteys:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="carried second subsection"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="carried third subsection"),),
            ),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="149",
            target_paragraph=1,
            target_item="4",
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="149",
            target_paragraph=1,
            target_item="5",
        ),
    ]

    got, removed = _prune_carried_subsections_outside_single_target_moment_ir("section", ops, muutos_ir)

    assert got is not None
    assert removed == ("2", "3")
    subs = [child for child in got.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subs] == ["1"]


def test_prepare_group_payload_keeps_real_later_targeted_subsections() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="149",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="149 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="oikeus avata tekninen käyttöyhteys:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="real second subsection target"),),
            ),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="149",
            target_paragraph=1,
            target_item="2",
        ),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=2),
    ]

    got, removed = _prune_carried_subsections_outside_single_target_moment_ir("section", ops, muutos_ir)

    assert got is not None
    assert removed == ()
    subs = [child for child in got.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subs] == ["1", "2"]


def test_elaborate_payload_against_live_observes_pruned_carried_subsections() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="149",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="live 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="live 3"),)),
        ),
    )
    ctx = _mock_ctx("section", "149", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="149",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="149 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="oikeus avata tekninen käyttöyhteys:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="carried 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="carried 3"),)),
        ),
    )
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="149", target_paragraph=1),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="149",
            target_paragraph=1,
            target_item="2",
        ),
    ]

    got = elaborate_payload_against_live(ctx, ops, muutos_ir, set())

    obs = [obs for obs in _observations(got) if obs.kind == "ELAB.PRUNE_CARRIED_SUBSECTIONS_OUTSIDE_TARGET_MOMENT"]
    assert len(obs) == 1
    assert obs[0].detail == {"removed_subsections": ["2", "3"]}
    normalized_subs = [child for child in _muutos_ir(got).children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in normalized_subs] == ["1"]


def test_prepare_group_payload_folds_split_omission_prefix_into_following_intro_list() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    ctx = _mock_ctx("section", "12", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vesikulkuneuvorekisteristä annetun lain"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="(424/2014) tahallisesta tai huolimattomuudesta tapahtuneesta rikkomisesta määrätään rikesakko seuraavasti:",
                    ),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )

    got = prepare_payload_surface(
        ctx,
        [AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="12", target_paragraph=2)],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1"]
    intro = next(c for c in subs[0].children if c.kind == IRNodeKind.INTRO)
    assert intro.text == (
        "Vesikulkuneuvorekisteristä annetun lain (424/2014) tahallisesta tai "
        "huolimattomuudesta tapahtuneesta rikkomisesta määrätään rikesakko seuraavasti:"
    )
    assert [c.label for c in subs[0].children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]


def test_normalize_group_payload_preserves_real_intro_list_subsection_after_split_prefix_fold() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    ctx = _mock_ctx("section", "12", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_paragraph=2,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vesikulkuneuvorekisteristä annetun lain"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO,
                        text="(424/2014) tahallisesta tai huolimattomuudesta tapahtuneesta rikkomisesta määrätään rikesakko seuraavasti:",
                    ),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )
    got = elaborate_payload_against_live(ctx, [op], prepared, set())

    assert len(got.group_ops) == 1
    amend_sub = got.subsec_map[id(got.group_ops[0])]
    assert amend_sub.label == "2"
    intro = next(c for c in amend_sub.children if c.kind == IRNodeKind.INTRO)
    assert intro.text.startswith("Vesikulkuneuvorekisteristä annetun lain (424/2014)")
    assert [c.label for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH] == ["1", "2"]


def test_prepare_group_payload_keeps_split_prefix_when_item_ops_target_later_moment() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5"),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=2,
    )
    op_item_1 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=3,
        target_item="1",
    )
    op_item_2 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=3,
        target_item="2",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Palvelussuhteen ehtoja eivät ole seurakunnan virastojen ja laitosten "
                            "järjestysmuodon perusteet."
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Sopia ei saa:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op_plain, op_item_1, op_item_2],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    assert prepared is not None
    assert [c.label for c in prepared.children if c.kind == IRNodeKind.SUBSECTION] == ["1", "2"]

    normalized = elaborate_payload_against_live(ctx, [op_plain, op_item_1, op_item_2], prepared, set())

    assert [op.description() for op in normalized.group_ops] == [
        "REPLACE 2 § 2 mom",
        "REPLACE 2 § 3 mom 1 kohta",
        "REPLACE 2 § 3 mom 2 kohta",
    ]
    assignment = _slot_assignment_result(normalized)
    plain_sub = assignment.for_op(normalized.group_ops[0])
    item_sub_1 = assignment.for_op(normalized.group_ops[1])
    item_sub_2 = assignment.for_op(normalized.group_ops[2])
    assert plain_sub is not None
    assert item_sub_1 is not None
    assert item_sub_2 is not None
    assert plain_sub.label == "2"
    assert item_sub_1.label == "3"
    assert item_sub_2.label == "3"


def test_prepare_group_payload_folds_intro_list_continuation_before_omission() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tuomioistuin voi"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="tuomita pituudeltaan määräämänsä enintään kahdeksan kuukauden ehdottoman vankeusrangaistuksen sijasta rangaistukseksi yhdyskuntapalvelua.",
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Mitä rikoslain 7 luvun 6 §:ssä säädetään..."),),
            ),
        ),
    )

    got = prepare_payload_surface(
        ctx,
        [
            AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=1),
            AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=3),
        ],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "3"]
    assert any(
        child.kind == IRNodeKind.CONTENT and "tuomita pituudeltaan määräämänsä" in (child.text or "")
        for child in subs[0].children
    )


def test_prepare_group_payload_preserves_real_post_omission_subsection_pair() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "4", live_node=live_sec)
    ops = [
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="4", target_paragraph=5),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="4", target_paragraph=6),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="4 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Poiketen siitä, mitä 2 momentissa säädetään hyvityksestä."),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.INTRO, text="Mitä 5 momentissa säädetään hyvityksestä, sovelletaan vain jos:"
                    ),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )

    got = prepare_payload_surface(
        ctx,
        ops,
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    assert got is not None
    assert [c.label for c in got.children if c.kind == IRNodeKind.SUBSECTION] == ["1", "2"]

    normalized = elaborate_payload_against_live(ctx, ops, got, set())

    muutos_ir = _muutos_ir(normalized)
    assert [c.label for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION] == ["5", "6"]
    assert normalized.subsec_map[id(normalized.group_ops[0])].label == "5"
    assert normalized.subsec_map[id(normalized.group_ops[1])].label == "6"


def test_normalize_group_payload_keeps_shifted_sparse_replace_bound_to_trailing_subsection() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=1)
    op3 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=3)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tuomioistuin voi"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="tuomita pituudeltaan määräämänsä..."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Mitä rikoslain 7 luvun 6 §:ssä säädetään..."),),
            ),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op1, op3],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )
    got = elaborate_payload_against_live(ctx, [op1, op3], prepared, set())

    assert got.subsec_map[id(op1)].label == "1"
    assert got.subsec_map[id(op3)].label == "3"
    assert any(
        child.kind == IRNodeKind.CONTENT and "Mitä rikoslain 7 luvun 6 §:ssä" in (child.text or "")
        for child in got.subsec_map[id(op3)].children
    )
    pathologies = _pathologies(got)
    assert [p.code for p in pathologies] == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]
    assert pathologies[0].detail["recovery_kind"] == "sparse_subsection_tail_preserved"


def test_build_subsection_override_map_prefers_exact_subsection_label_match() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14b",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14b", target_paragraph=1)
    op2 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14b", target_paragraph=2)
    op3 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14b", target_paragraph=3)

    got = _build_subsection_override_map(muutos_ir, [op1, op2, op3])

    assert got[id(op1)].label == "1"
    assert id(op2) not in got
    assert got[id(op3)].label == "3"


def test_build_subsection_override_map_shifts_replace_after_same_target_insert() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="11a",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="6"),
        ),
    )
    op3 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=3)
    op4 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=4)
    op5_replace = AmendmentOp(
        op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5
    )
    op5_insert = AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)

    got = _build_subsection_override_map(muutos_ir, [op3, op4, op5_replace, op5_insert])

    assert got[id(op3)].label == "3"
    assert got[id(op4)].label == "4"
    assert got[id(op5_insert)].label == "5"
    assert got[id(op5_replace)].label == "6"


def test_build_subsection_override_map_uses_constant_offset_for_sparse_suffix_replaces() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 3 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 4 mom"),)
            ),
        ),
    )
    op1_item4 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        target_item="4",
    )
    op3 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=3)
    op4 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=4)

    got = _build_subsection_override_map(muutos_ir, [op1_item4, op3, op4])

    assert got[id(op1_item4)].label == "1"
    assert got[id(op3)].label == "2"
    assert got[id(op4)].label == "3"


def test_build_subsection_override_map_keeps_monotone_suffix_order_after_leading_exact_match() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 3 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 4 mom"),)
            ),
        ),
    )
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="13", target_paragraph=1)
    op3 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="13", target_paragraph=3)
    op4 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="13", target_paragraph=4)

    got = _build_subsection_override_map(muutos_ir, [op1, op3, op4])

    assert got[id(op1)].label == "1"
    assert got[id(op3)].label == "2"
    assert got[id(op4)].label == "3"


def test_build_subsection_override_map_shares_subsection_slot_with_intro_replace() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Ensimmainen johdanto."),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Toinen johdanto."),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                ),
            ),
        ),
    )
    op1_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
        target_special="johd",
    )
    op1_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
        target_item="1",
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=2,
        target_special="johd",
    )
    op2_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=2,
        target_item="1",
    )

    got = _build_subsection_override_map(muutos_ir, [op1_intro, op1_item, op2_intro, op2_item])

    assert got[id(op1_item)].label == "1"
    assert got[id(op1_intro)].label == "1"
    assert got[id(op2_item)].label == "2"
    assert got[id(op2_intro)].label == "2"


def test_build_subsection_override_map_maps_lone_intro_replace_by_exact_label() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=2,
        target_special="johd",
    )

    got = _build_subsection_override_map(muutos_ir, [op2_intro])

    assert got[id(op2_intro)].label == "2"


def test_build_subsection_slot_assignment_exposes_typed_result() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=2,
        target_special="johd",
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op2_intro])

    assert got.subsec_map[id(op2_intro)].label == "2"
    mapped = got.for_op(op2_intro)
    assert mapped is not None
    assert mapped.label == "2"


def test_build_subsection_slot_assignment_binds_dense_local_intro_slots_by_source_order() -> None:
    """Local sparse labels 1..N must not be mistaken for live moment labels.

    Real sparse amendment excerpts can serialize two changed johdanto moments as
    local slots "1" and "2" even when the live targets are moments 2 and 3.
    In that shape, source order is authoritative.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="20",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lupaviranomainen voi viran puolesta muuttaa lupapäätöstä, jos:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lupaviranomainen voi viran puolesta peruuttaa luvan, jos:"),),
            ),
        ),
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20",
        target_paragraph=2,
        target_special="johd",
    )
    op3_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20",
        target_paragraph=3,
        target_special="johd",
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op2_intro, op3_intro])

    assert got.subsec_map[id(op2_intro)].label == "1"
    assert got.subsec_map[id(op3_intro)].label == "2"
    assert got.unassigned_payload_slots == ()


def test_build_subsection_slot_assignment_binds_mixed_intro_and_plain_by_source_order() -> None:
    """Mixed johd/plain sparse excerpts should keep source-order pairing.

    Mirrors 1998/28 §25 where the amendment payload has local slots "1", "2"
    but the live targets are 2 mom johd and 4 mom plain replace.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="25",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lupapäätökseen lupaviranomaisen on:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Lupaviranomainen pitää rekisteriä Etelämantereen erityissuojelu- ja hallinta-alueista sekä historiallisista paikoista ja muistomerkeistä.",
                    ),
                ),
            ),
        ),
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="25",
        target_paragraph=2,
        target_special="johd",
    )
    op4_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="25",
        target_paragraph=4,
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op2_intro, op4_plain])

    assert got.subsec_map[id(op2_intro)].label == "1"
    assert got.subsec_map[id(op4_plain)].label == "2"
    assert got.unassigned_payload_slots == ()


def test_build_subsection_slot_assignment_shares_plain_and_item_ops_on_same_moment() -> None:
    """Plain subsection ops and item ops for the same moment must share one slot.

    Mirrors the 2000/252 §3 shape where the first changed moment carries both
    a plain subsection replace and a numbered item replacement.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Pankkivaltuuston tehtävänä on:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Pankkivaltuusto nimittää ja erottaa Finanssivalvonnan johtokunnan jäsenet."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Pankkivaltuusto antaa ohjeet siitä, miten päätetään."),),
            ),
        ),
    )
    op_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
    )
    op_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        target_item="4",
    )
    op_tail = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=3,
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op_plain, op_item, op_tail])

    assert got.subsec_map[id(op_plain)].label == "1"
    assert got.subsec_map[id(op_item)].label == "1"
    assert got.subsec_map[id(op_tail)].label == "3"
    assert got.unassigned_payload_slots == ("2:2",)


def test_assign_subsection_slots_tracks_unassigned_payload_slots() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION),
        ),
    )
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op1])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    assert got.subsec_map[id(op1)].label == "1"
    assert len(got.sparse_slot_bindings) == 1
    binding = got.sparse_slot_bindings[0]
    assert binding.op_description == "REPLACE 14 § 1 mom"
    assert binding.op_type == "REPLACE"
    assert binding.target_paragraph == 1
    assert binding.target_item is None
    assert binding.target_special is None
    assert binding.payload_slot_index == 1
    assert binding.payload_slot_label == "1"
    assert got.used_subs == (0,)
    assert got.unassigned_payload_slots == ("2:2", "3:(unlabeled)")


def test_assign_subsection_slots_reserves_johd_slot_for_intro_op() -> None:
    """Regression: INSERT op must not steal the slot reserved for a johd REPLACE.

    Pattern (mirrors 2010/182 §82a / 2024/432):
      payload slots (labels): "1", "2", "3"
      ops:
        - REPLACE mom 1 kohta 2   → item-matched → slot "1"
        - REPLACE mom 2 johd      → intro op → should get slot "2" by exact label
        - INSERT mom 5            → no exact label → should get slot "3"

    Before the fix _assign_fallback_plain_slot_ops grabbed slot "2" for INSERT,
    leaving johd unassigned and slot "3" stranded.
    """
    item_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="kohta 2 text"),)
            ),
        ),
    )
    johd_sub = IRNode(
        kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.INTRO, text="Johdantokappale text"),)
    )
    insert_sub = IRNode(
        kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="New momentti 5 text"),)
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="82a",
        children=(item_sub, johd_sub, insert_sub),
    )
    op_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="82a",
        target_paragraph=1,
        target_item="2",
    )
    op_johd = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="82a",
        target_paragraph=2,
        target_special="johd",
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="82a",
        target_paragraph=5,
    )
    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_item, op_johd, op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    # REPLACE mom 1 kohta 2 → slot "1" (item-matched)
    assert got.subsec_map[id(op_item)].label == "1"
    # REPLACE mom 2 johd → slot "2" (exact label match, not stolen by fallback)
    assert got.subsec_map[id(op_johd)].label == "2"
    # INSERT mom 5 → slot "3" (fallback skipped "2" because johd reserved it)
    assert got.subsec_map[id(op_insert)].label == "3"
    # No unassigned slots
    assert got.unassigned_payload_slots == ()


def test_assign_subsection_slots_keeps_insert_unbound_across_explicit_gap() -> None:
    """Do not force a plain subsection op across an explicit numeric gap.

    Mirrors the live sparse-omission family behind 1982/182 <- 2010/625 §40:
    after live alignment the amendment payload carries explicit subsection
    labels 2, 4, 5 while the johtolause still asks for INSERT mom 3.

    That should remain unbound and surface as sparse residue, not be rebound to
    slot "2" or "5" by fallback heuristics.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="40",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 4 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 5 mom"),)
            ),
        ),
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="40",
        target_paragraph=3,
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    assert got.subsec_map.for_op(op_insert) is None
    assert got.sparse_slot_bindings == ()
    assert got.unassigned_payload_slots == ("1:2", "2:4", "3:5")


def test_assign_subsection_slots_keeps_plain_ops_unbound_on_far_numeric_domain() -> None:
    """Do not positional-fallback plain subsection ops onto unrelated labels.

    Mirrors the live 1982/182 <- 2002/187 §21 residue where subsection ops for
    `3 mom` were being rebound onto payload slots labeled `23`, `24`, ...
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="21",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="23",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lisäkilvet ovat:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="24",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lisäkilpi 848"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="25",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Lisäkilpi 849"),),
            ),
        ),
    )
    op_replace = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="21",
        target_paragraph=3,
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="21",
        target_paragraph=3,
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_replace, op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    assert got.subsec_map.for_op(op_replace) is None
    assert got.subsec_map.for_op(op_insert) is None
    assert got.sparse_slot_bindings == ()
    assert got.unassigned_payload_slots == ("1:23", "2:24", "3:25")


def test_assign_subsection_slots_binds_lone_sparse_insert_to_trailing_slot() -> None:
    """A lone sparse insert must not steal the first payload slot by fallback.

    Mirrors `1967/550 §2` under `2005/896`, where the amendment body shows the
    preserved earlier moments plus the new tail moment, but the group carrying
    only `INSERT 5 mom` was previously bound to payload slot `1`.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 5 mom"),),
            ),
        ),
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=5,
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    mapped = got.subsec_map.for_op(op_insert)
    assert mapped is not None
    assert mapped.label == "4"
    assert len(got.sparse_slot_bindings) == 1
    assert got.sparse_slot_bindings[0].payload_slot_index == 3
    assert got.sparse_slot_bindings[0].payload_slot_label == "4"
    assert got.unassigned_payload_slots == ("1:1", "2:2")


def test_assign_subsection_slots_marks_singleton_higher_moment_local_dense_binding_owned() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="21b",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom paikallinen slot 1"),),
            ),
        ),
    )
    op_replace = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="21b",
        target_paragraph=2,
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_replace])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    mapped = got.subsec_map.for_op(op_replace)
    assert mapped is not None
    assert mapped.label == "1"
    assert len(got.binding_certificates) == 1
    assert got.binding_certificates[0].admissibility == "single"
    assert got.binding_certificates[0].candidate_count == 1
    assert any(obs.kind == "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING" for obs in got.binding_observations)


def test_assign_subsection_slots_keeps_exact_first_target_and_owned_trailing_insert_binding() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="87",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 6 mom"),),
            ),
        ),
    )
    op_replace = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="87",
        target_paragraph=1,
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="87",
        target_paragraph=6,
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_replace, op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    assert got.subsec_map.for_op(op_replace) is not None
    assert got.subsec_map.for_op(op_replace).label == "1"
    assert got.subsec_map.for_op(op_insert) is not None
    assert got.subsec_map.for_op(op_insert).label == "2"
    certs = {cert.slot_id: cert for cert in got.binding_certificates}
    assert certs[1].admissibility == "single"
    assert certs[2].admissibility == "single"
    assert any(obs.kind == "ELAB.TRAILING_SPARSE_INSERT_BINDING" for obs in got.binding_observations)


def test_payload_normalize_rebases_duplicate_target_shifted_replace_after_renumber() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="20j",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 j §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Mom 1"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Mom 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha mom 3"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha mom 4"),)),
        ),
    )
    ctx = _mock_ctx("section", "20j", target_chapter="6a", live_node=live_sec)
    renumber = AmendmentOp(
        op_type="RENUMBER",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=3,
        source_statute="2017/169",
    )
    replace2 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=2,
        source_statute="2017/169",
    )
    replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=3,
        source_statute="2017/169",
    )
    insert3 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=3,
        source_statute="2017/169",
    )
    insert5 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=5,
        source_statute="2017/169",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="20j",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 j §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 3"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 4"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 5"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [renumber, replace2, replace3, insert3, insert5], muutos_ir, set())

    descriptions = [op.description() for op in got.group_ops]
    assert "REPLACE 6a luku 20j § 4 mom" in descriptions
    assert "REPLACE 6a luku 20j § 3 mom" not in descriptions
    assert any(obs.kind == "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE" for obs in _observations(got))


def test_payload_normalize_does_not_rebase_duplicate_target_shifted_replace_without_renumber() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="20j",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 j §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Mom 1"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Mom 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha mom 3"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha mom 4"),)),
        ),
    )
    ctx = _mock_ctx("section", "20j", target_chapter="6a", live_node=live_sec)
    replace2 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=2,
        source_statute="2017/169",
    )
    replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=3,
        source_statute="2017/169",
    )
    insert3 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=3,
        source_statute="2017/169",
    )
    insert5 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="20j",
        target_chapter="6a",
        target_paragraph=5,
        source_statute="2017/169",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="20j",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 j §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 3"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 4"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi mom 5"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [replace2, replace3, insert3, insert5], muutos_ir, set())

    descriptions = [op.description() for op in got.group_ops]
    assert "REPLACE 6a luku 20j § 3 mom" in descriptions
    assert "REPLACE 6a luku 20j § 4 mom" not in descriptions
    assert all(obs.kind != "ELAB.REBASE_DUPLICATE_TARGET_SHIFTED_REPLACE" for obs in _observations(got))


def test_payload_normalize_rebases_sparse_replace_from_stale_predecessor_slot() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmainen"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen"),)),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Maatilatalouden kehittamisrahaston varoja kaytettaessa on erityisesti "
                            "edistettava aiempaa tukikautta koskevia tavoitteita."
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Rahaston varoja voidaan kayttaa maidon viitemaarien ostamiseen kansalliseen varantoon.",
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Valtioneuvosto voi vastikkeetta luovuttaa maatilatalouden kehittamisrahaston varoilla hankittua omaisuutta.",
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=4,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Maatilatalouden kehittamisrahaston varoja kaytettaessa on erityisesti "
                            "edistettava rahoituskauden 2023-2027 tavoitteita."
                        ),
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert len(got.group_ops) == 1
    rebased = got.group_ops[0]
    assert rebased.target_paragraph == 3
    assert rebased.target_guessing_provenance_tags == ("rebase_sparse_stale_predecessor",)
    assignment = _slot_assignment_result(got)
    mapped = assignment.for_op(rebased)
    assert mapped is not None
    assert mapped.label == "4"
    assert assignment.sparse_slot_bindings[0].target_paragraph == 3
    assert assignment.sparse_slot_bindings[0].payload_slot_label == "4"
    assert got.elaboration_observations is not None
    observations = [obs for obs in got.elaboration_observations if obs.kind == "ELAB.REBASE_SPARSE_STALE_PREDECESSOR"]
    assert len(observations) == 1
    assert observations[0].detail is not None
    detail = observations[0].detail
    assert detail["from_paragraph"] == 4
    assert detail["to_paragraph"] == 3
    assert detail["predecessor_label"] == "3"
    assert detail["nominal_label"] == "4"
    assert detail["op_description"] == "REPLACE 3 § 4 mom"
    assert detail["pred_score"] > detail["target_score"]


def test_payload_normalize_keeps_sparse_replace_on_nominal_target_when_live_slot_matches() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="8",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmainen"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="kolmas"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="neljas"),)),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Keksinnon selitys, tiivistelma ja patenttivaatimukset on laadittava "
                            "suomeksi, ruotsiksi tai englanniksi."
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="6",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Hakijan on suoritettava vahvistettu hakemusmaksu.",
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "8", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_paragraph=5,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="8",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Keksinnon selitys, tiivistelma ja patenttivaatimukset on laadittava "
                            "suomeksi, ruotsiksi tai englanniksi. Ne voidaan laatia useammalla kielella."
                        ),
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert len(got.group_ops) == 1
    assert got.group_ops[0].target_paragraph == 5
    assert got.group_ops[0].target_guessing_provenance_tags == ()


def test_subsection_slot_map_supports_op_identity_lookup() -> None:
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=2)
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="2")
    slots = SubsectionSlotMap()

    slots.assign(op, sub)

    assert slots.for_op(op) is sub
    assert slots[op] is sub
    assert slots.for_op(op) is sub


def test_subsection_slot_assignment_result_supports_op_lookup() -> None:
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): sub}),
        sparse_slot_bindings=(),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    assert assignment.for_op(op) is sub
    assert assignment.has_op(op) is True


def test_subsection_slot_assignment_result_supports_normalized_compat_lookup() -> None:
    op = AmendmentOp(
        op_id="bridge_slot",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
    )
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    slots = SubsectionSlotMap()
    slots.assign(op, sub)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=slots,
        sparse_slot_bindings=(),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    normalized_op = dc_replace(op, target_paragraph=1)
    assert assignment.for_op(normalized_op) is sub
    assert assignment.has_op(normalized_op) is True


def test_subsection_slot_assignment_result_supports_stable_op_id_lookup() -> None:
    op = AmendmentOp(
        op_id="stable_slot",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
    )
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    slots = SubsectionSlotMap()
    slots.assign(op, sub)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=slots,
        sparse_slot_bindings=(),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    assert assignment.for_stable_op_id("stable_slot") is sub
    assert assignment.has_stable_op_id("stable_slot") is True
    assert assignment.resolve_apply_subsection_ir_for_stable_op_id("stable_slot") is sub


def test_subsection_slot_assignment_result_binding_prefers_stable_id_then_blank_identity_then_fallback() -> None:
    blank_op = AmendmentOp(
        op_id="",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
    )
    minted_op = AmendmentOp(
        op_id="binding_slot",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="14",
        target_paragraph=1,
    )
    mapped = IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="mapped"),))
    fallback = IRNode(
        kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="fallback"),)
    )
    slots = SubsectionSlotMap()
    slots.assign(blank_op, mapped)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=slots,
        sparse_slot_bindings=(),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    assert assignment.has_binding("", blank_op) is True
    assert assignment.resolve_apply_subsection_ir_for_binding("", blank_op, fallback) is mapped
    assert assignment.resolve_apply_subsection_ir_for_binding("missing", minted_op, fallback) is fallback


def test_subsection_slot_assignment_result_binding_does_not_singleton_fallback_from_muutos_ir() -> None:
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="payload"),)
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )

    muutos_ir = IRNode(kind=IRNodeKind.SECTION, label="14", children=(amend_sub,))

    assert assignment.resolve_apply_subsection_ir_for_binding("missing", None, None) is None


def test_assign_item_matched_slot_ops_allows_sharing_single_slot() -> None:
    """Regression: REPLACE X kohta + INSERT Y kohta in the same payload subsection.

    Mirrors amendment 2021/1216 -> statute 1994/1472 section 2 §:
      payload has one subsection containing both item 28 and item 29.
      ops:
        - REPLACE 2 § 1 mom 28 kohta  -> must map to the shared slot
        - INSERT  2 § 1 mom 29 kohta  -> must also map to the same slot

    Before the fix _assign_item_matched_slot_ops marked the slot as used after
    the first item match, so the second op fell through unassigned.
    """
    shared_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="28", children=(IRNode(kind=IRNodeKind.CONTENT, text="item 28 text"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="29", children=(IRNode(kind=IRNodeKind.CONTENT, text="item 29 text"),)),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(shared_sub,),
    )
    op_replace = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=1,
        target_item="28",
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=1,
        target_item="29",
    )

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, [op_replace, op_insert])

    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    # Both ops must be assigned to the same (only) slot.
    assert got.subsec_map.for_op(op_replace) is shared_sub
    assert got.subsec_map.for_op(op_insert) is shared_sub
    # No unassigned payload slots (one slot, both ops bound).
    assert got.unassigned_payload_slots == ()


def test_subsection_slot_assignment_result_summary_surfaces_binding_and_leftover_labels() -> None:
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="2",
            )
        ,),
        used_subs=(0,),
        unassigned_payload_slots=("2:2", "3:(unlabeled)"),
    )

    assert assignment.summary() == {
        "binding_count": 1,
        "leftover_count": 2,
        "binding_labels": ["2"],
        "leftover_labels": ["2:2", "3:(unlabeled)"],
    }


def test_summarize_slot_assignment_supports_serialized_rows() -> None:
    summary = summarize_slot_assignment(
        [{"payload_slot_label": "4"}],
        ["2:5", "3:(unlabeled)"],
        leftover_count=1,
        include_leftover_slot_count=True,
    )

    assert summary == {
        "binding_count": 1,
        "leftover_count": 1,
        "leftover_slot_count": 2,
        "binding_labels": ["4"],
        "leftover_labels": ["2:5", "3:(unlabeled)"],
    }


def test_normalize_group_payload_surfaces_unassigned_sparse_payload_slots() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    ctx = _mock_ctx("section", "14", live_node=live_sec)
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),)
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="irrallinen loppu"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op1], muutos_ir, set())

    assert got.subsec_map[id(got.group_ops[0])].label == "1"
    slot_assignment = _slot_assignment_result(got)
    sparse_slot_bindings = tuple(got.sparse_slot_bindings or [])
    assert slot_assignment.subsec_map[id(got.group_ops[0])].label == "1"
    assert len(sparse_slot_bindings) == 1
    assert len(slot_assignment.sparse_slot_bindings) == 1
    assert sparse_slot_bindings[0].op_description == "REPLACE 14 § 1 mom"
    assert sparse_slot_bindings[0].payload_slot_index == 1
    assert sparse_slot_bindings[0].payload_slot_label == "1"
    assert got.unassigned_sparse_payload_slots == ("2:2", "3:(unlabeled)")
    assert slot_assignment.unassigned_payload_slots == ("2:2", "3:(unlabeled)")
    observations = _observations(got)
    first_detail = observations[0].detail
    assert first_detail is not None
    assert [obs.kind for obs in observations] == ["ELAB.UNASSIGNED_SPARSE_SLOTS"]
    assert first_detail["unassigned_slots"] == ("2:2", "3:(unlabeled)")


def test_group_payload_normalization_result_defaults_unassigned_sparse_payload_slots() -> None:
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )
    result = GroupPayloadNormalizationResult(
        muutos_ir=None,
        group_ops=(),
        subsec_map=SubsectionSlotMap(),
        slot_assignment=assignment,
        unassigned_sparse_payload_slots=None,
    )

    assert result.unassigned_sparse_payload_slots == ()
    assert result.slot_assignment is assignment
    assert result.slot_assignment.unassigned_payload_slots == ()


def test_sparse_subsection_elaboration_result_defaults_unassigned_sparse_payload_slots() -> None:
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )
    result = SparseSubsectionElaborationResult(
        muutos_ir=None,
        group_ops=(),
        subsec_map=SubsectionSlotMap(),
        source_pathologies=(),
        slot_assignment=assignment,
        unassigned_sparse_payload_slots=None,
    )

    assert result.unassigned_sparse_payload_slots == ()
    assert result.slot_assignment is assignment
    assert result.slot_assignment.unassigned_payload_slots == ()


def test_rebase_item_targets_to_sparse_slot_labels_preserves_explicit_source_paragraph() -> None:
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="149",
        target_paragraph=1,
        target_item="1",
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description="REPLACE 149 § 4 mom",
                op_type="REPLACE",
                target_paragraph=4,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="4",
            ),
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type="REPLACE",
                target_paragraph=1,
                target_item="1",
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="4",
            ),
        ),
        used_subs=(),
        unassigned_payload_slots=(),
    )

    got, changed = _rebase_item_targets_to_sparse_slot_labels([op], assignment)

    assert changed is False
    assert got == [op]


def test_normalize_group_payload_keeps_item_level_replace_under_partial_section_body() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Gamma long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="4",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Delta long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Epsilon long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="6",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Zeta long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="7",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Eta long replacement text"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="8",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Theta long replacement text"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=1,
        target_item="5",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5"),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [o.description() for o in got.group_ops] == ["REPLACE 2 § 1 mom 5 kohta"]
    assert got.subsec_map[id(op)].label == "1"


def test_normalize_group_payload_drops_sparse_item_replace_without_amendment_body() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="12"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op_missing = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=1,
        target_item="4",
    )
    op_present = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=1,
        target_item="12",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="12"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_missing, op_present],
        muutos_ir,
        set(),
    )

    assert [o.description() for o in got.group_ops] == ["REPLACE 2 § 1 mom 12 kohta"]
    assert got.subsec_map[id(op_present)].label == "1"
    slot_assignment = _slot_assignment_result(got)
    pathologies = _pathologies(got)
    observations = _observations(got)
    assert slot_assignment.for_op(op_present) is got.subsec_map[id(op_present)]
    assert [p.code for p in pathologies] == ["SPARSE_ITEM_BODY_MISSING"]
    assert pathologies[0].target_label == "2 § 1 mom 4 kohta"
    assert pathologies[0].target_unit_kind == "section"
    assert [failed.description for failed in got.rejected_ops] == ["REPLACE 2 § 1 mom 4 kohta"]
    assert [failed.reason for failed in got.rejected_ops] == ["ELAB.DROP_ITEM_REPLACES_MISSING"]
    assert [obs.kind for obs in observations] == ["ELAB.DROP_ITEM_REPLACES_MISSING"]
    first_detail = observations[0].detail
    assert first_detail is not None
    assert first_detail["dropped_targets"] == ["2 § 1 mom 4 kohta"]


def test_normalize_group_payload_keeps_tail_omission_on_typed_slot_assignment() -> None:
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=2,
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"), IRNode(kind=IRNodeKind.CONTENT, text="Gamma")),
            ),
        ),
    )
    ctx = _mock_ctx("section", "4", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi loppu"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    muutos_ir = _muutos_ir(got)
    slot_assignment = _slot_assignment_result(got)
    assert [child.kind for child in muutos_ir.children] == [IRNodeKind.NUM, IRNodeKind.SUBSECTION]
    mapped = slot_assignment.for_op(op)
    assert mapped is not None
    assert [child.kind for child in mapped.children] == [IRNodeKind.CONTENT, IRNodeKind.OMISSION]
    assert got.subsec_map.for_op(op) is mapped


def test_normalize_group_payload_drops_redundant_item_op_when_plain_sparse_slot_already_carries_item() -> None:
    op_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="20",
        target_paragraph=2,
    )
    op_item = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="20",
        target_paragraph=2,
        target_item="5a",
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="20",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Liitteet:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Liitteet:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5a"),
                ),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Tail"),)),
        ),
    )
    ctx = _mock_ctx("section", "20", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="20",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="20 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Liitteet:"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5a"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_plain, op_item],
        muutos_ir,
        set(),
    )

    assert [op.description() for op in got.group_ops] == ["REPLACE 20 § 2 mom"]
    assert [failed.description for failed in got.rejected_ops] == ["INSERT 20 § 2 mom 5a kohta"]
    assert [failed.reason for failed in got.rejected_ops] == ["ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT"]
    observations = _observations(got)
    assert [obs.kind for obs in observations] == ["ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT"]
    detail = observations[0].detail
    assert detail is not None
    assert detail["dropped_ops"] == ["INSERT 20 § 2 mom 5a kohta"]


def test_normalize_group_payload_drops_redundant_item_op_even_after_omission_is_resolved_away() -> None:
    op_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="27",
        target_paragraph=2,
    )
    op_item = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="27",
        target_paragraph=2,
        target_item="7a",
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="27",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="27 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Head"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Old body"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Tail"),)),
        ),
    )
    ctx = _mock_ctx("section", "27", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="27",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="27 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Old body"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="7"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="7a"),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_plain, op_item],
        muutos_ir,
        set(),
    )

    assert [op.description() for op in got.group_ops] == ["REPLACE 27 § 2 mom"]
    assert [failed.description for failed in got.rejected_ops] == ["INSERT 27 § 2 mom 7a kohta"]
    assert [failed.reason for failed in got.rejected_ops] == ["ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT"]
    observations = _observations(got)
    assert [obs.kind for obs in observations] == ["ELAB.DROP_REDUNDANT_ITEM_OPS_IN_SPARSE_SLOT"]
    detail = observations[0].detail
    assert detail is not None
    assert detail["dropped_ops"] == ["INSERT 27 § 2 mom 7a kohta"]


def test_normalize_group_payload_keeps_sparse_item_inserts_when_only_johd_and_item_replace_share_slot() -> None:
    op_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=1,
        target_special="johd",
    )
    op_item3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=1,
        target_item="3",
    )
    op_item4 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=1,
        target_item="4",
    )
    op_item5 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=1,
        target_item="5",
    )
    op_para2 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=2,
    )
    op_para5 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="42",
        target_paragraph=5,
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="42",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="42 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Old subsection 1"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Old subsection 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Old subsection 5"),)),
        ),
    )
    ctx = _mock_ctx("section", "42", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="42",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="42 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Maatalousyrittäjällä on oikeus kuntoutukseen, jos:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Second paragraph"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Fifth paragraph"),)),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_intro, op_item3, op_item4, op_item5, op_para2, op_para5],
        muutos_ir,
        set(),
    )

    survivor_shapes = {
        (op.op_type, op.target_paragraph, op.target_item, op.target_special)
        for op in got.group_ops
    }
    assert ("INSERT", 1, "4", None) in survivor_shapes
    assert ("INSERT", 1, "5", None) in survivor_shapes
    assert not any(failed.description in {"INSERT 42 § 1 mom 4 kohta", "INSERT 42 § 1 mom 5 kohta"} for failed in got.rejected_ops)


def test_normalize_group_payload_observes_mixed_sparse_slot_cross_paragraph() -> None:
    """Explicitly-targeted item ops are not rebased; observation is emitted instead.

    Only item ops that were heuristically normalized (normalize_item_like_target
    provenance) are eligible for paragraph rebasing.  Directly-targeted ops keep
    their source paragraph authority and trigger ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH
    when they share a slot with a plain op at a different paragraph.
    """
    op_replace_8 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="8",
    )
    op_replace_9 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="9",
    )
    op_insert_10 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="10",
    )
    op_insert_2 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=2,
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="4 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="6"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="7"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="8"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="9"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Edellä 1 momentin 1 kohdassa tarkoitettuun omistajaan..."),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "4", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="4 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="8"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="9"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="10"),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_replace_8, op_replace_9, op_insert_10, op_insert_2],
        muutos_ir,
        set(),
    )

    assert _completeness(got).kind == "complete"
    observations = _observations(got)
    # Directly-targeted item ops retain their source paragraph (1); the cross-
    # paragraph slot assignment is flagged but does not fail elaboration.
    cross_para_obs = [obs for obs in observations if obs.kind == "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH"]
    assert len(cross_para_obs) == 1
    assert cross_para_obs[0].detail is not None
    # Item ops (para 1) and plain op (para 2) share the same slot → observation fires.
    assert [(op.op_type, op.target_paragraph, op.target_item) for op in got.group_ops] == [
        ("REPLACE", 1, "8"),
        ("REPLACE", 1, "9"),
        ("INSERT", 1, "10"),
        ("INSERT", 2, None),
    ]


def test_normalize_group_payload_emits_source_pathology_for_suspicious_partial_whole_section_replace() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Gamma"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Delta"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Epsilon"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="6", children=(IRNode(kind=IRNodeKind.CONTENT, text="Zeta"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="7", children=(IRNode(kind=IRNodeKind.CONTENT, text="Eta"),)
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="8", children=(IRNode(kind=IRNodeKind.CONTENT, text="Theta"),)
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        source_statute="2010/1399",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)
                    ),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.group_ops == ()
    assert len(got.rejected_ops) == 1
    assert got.rejected_ops[0].description == op.description()
    assert "_drop_suspicious_partial_whole_section_replaces" in got.rejected_ops[0].reason
    assert got.rejected_ops[0].reason_code == "PARTIAL_WHOLE_SECTION_REPLACE_REJECTED"
    pathologies = _pathologies(got)
    assert [p.code for p in pathologies] == ["PARTIAL_WHOLE_SECTION_PAYLOAD"]
    assert pathologies[0].source_statute == "2010/1399"


def test_normalize_group_payload_drops_stale_whole_section_shell_for_subsection_target() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Käyttötarkoitukset"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Asiakkaiden tietoja voidaan käyttää:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Gamma"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Delta"),)),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", target_chapter="13", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_chapter="13",
        target_paragraph=1,
        source_statute="2022/244",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Työ- ja elinkeinotoimiston asiakastietojärjestelmä"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Stale copied subsection text."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.group_ops == ()
    assert len(got.rejected_ops) == 1
    assert got.rejected_ops[0].description == op.description()
    assert "_drop_suspicious_partial_subsection_shell_replaces" in got.rejected_ops[0].reason
    assert got.rejected_ops[0].reason_code == "STALE_WHOLE_SECTION_SHELL_REJECTED"
    pathologies = _pathologies(got)
    assert [p.code for p in pathologies] == ["PARTIAL_WHOLE_SECTION_PAYLOAD"]
    assert pathologies[0].detail["diagnostic_reason"] == "stale_whole_section_shell_heading_mismatch"


def test_prepare_payload_surface_keeps_section_omission_subsection_replace_and_preserves_live_heading() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmiä"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 1 momentti."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 2 momentti."),),
            ),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        source_statute="2021/657",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi 1 momentti."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )
    got = elaborate_payload_against_live(ctx, [op], prepared, set())

    prepared_ir = _muutos_ir(got)
    heading = next(child for child in prepared_ir.children if child.kind is IRNodeKind.HEADING)
    assert heading.text == "Määritelmiä"
    assert [op.description() for op in got.group_ops] == ["REPLACE 3 § 1 mom"]
    assert not got.rejected_ops
    assert not _pathologies(got)
    subsections = [child for child in prepared_ir.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]
    assert any((child.text or "") == "Uusi 1 momentti." for child in subsections[0].children)
    assert any((child.text or "") == "Vanha 2 momentti." for child in subsections[1].children)


def test_prepare_payload_surface_does_not_merge_stale_subsection_shell_without_section_omission() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmiä"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 1 momentti."),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 2 momentti."),)),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        source_statute="2021/657",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Määritelmät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi 1 momentti."),),
            ),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )

    heading = next(child for child in prepared.children if child.kind is IRNodeKind.HEADING)
    assert heading.text == "Määritelmät"
    assert [child.kind for child in prepared.children].count(IRNodeKind.SUBSECTION) == 1


def test_normalize_group_payload_keeps_targeted_replace_with_inner_omission_section_shell() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Opintovapaahakemuksen sisältö ja liitteet"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Opintovapaahakemuksesta tulee käydä ilmi:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 1"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 2"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 3"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 4"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha kohta 5"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="6", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha kohta 6"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="7", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha kohta 7"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="8", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha kohta 8"),)),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "6", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="6",
        target_paragraph=1,
        source_statute="1991/478",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Opintovapaahakemuksen sisältö ja liitteet"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Opintovapaahakemuksesta tulee käydä ilmi:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 1"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 2"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 3"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Kohta 4"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi kohta 5"),)),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert len(got.group_ops) == 1
    assert got.group_ops[0].target_paragraph == 1
    assert _pathologies(got) == ()


def test_elaborate_payload_marks_same_group_single_subsection_shell_fragmentary() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="15",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="15 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Tiedonantovelvollisuus"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Hakijalle on annettava tiedot:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"),)),
                ),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="", children=(IRNode(kind=IRNodeKind.CONTENT, text="Tail 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="", children=(IRNode(kind=IRNodeKind.CONTENT, text="Tail 3"),)),
        ),
    )
    ctx = _mock_ctx("section", "15", target_chapter="2", live_node=live_sec)
    whole_section = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="15",
        target_chapter="2",
        source_statute="2016/533",
    )
    scoped_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="15",
        target_chapter="2",
        target_paragraph=1,
        target_special="johd",
        source_statute="2016/533",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="15",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="15 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Tiedonantovelvollisuus"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.INTRO, text="Hakijalle on annettava tiedot:"),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [whole_section, scoped_intro], muutos_ir, set())

    completeness = _completeness(got)
    assert completeness.kind == "fragmentary"
    assert completeness.tail_policy == "preserve_unstated_tail"
    assert "same_group_descendant_scoped_single_subsection_shell" in completeness.reasons


def test_normalize_group_payload_emits_malformed_broad_replace_body_subcase() -> None:
    repeated = "pitka kuvaava tekstisisalto " * 12
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "A"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "B"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "C"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="4",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "D"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "E"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="6",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "F"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="7",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "G"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="8",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated + "H"),),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        source_statute="2010/1399",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.group_ops == ()
    pathologies = _pathologies(got)
    assert [p.code for p in pathologies] == ["PARTIAL_WHOLE_SECTION_PAYLOAD", "MALFORMED_BROAD_REPLACE_BODY"]
    assert pathologies[0].detail["diagnostic_reason"] == "shared_intro_tiny_payload"
    assert pathologies[1].detail["diagnostic_reason"] == "shared_intro_tiny_payload"


def test_normalize_group_payload_no_mismatch_for_new_standalone_sections() -> None:
    """New sections in standalone_section_targets are kept — no mismatch pathology.

    With the Bug C fix, new sections (not in live_member_labels) are kept in
    the container payload.  The CONTAINER_MEMBERSHIP_MISMATCH pathology should
    NOT fire for new sections being introduced by the amendment.
    """
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
        ),
    )
    ctx = _mock_ctx("chapter", "3", live_node=live_container)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.CHAPTER,
        target_section="3",
        source_statute="1995/1599",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="26"),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, {"26"})

    pathologies = _pathologies(got)
    observations = _observations(got)
    # Section "26" is NEW — kept in container, no pruning, no pathology.
    assert [p.code for p in pathologies] == []
    assert [obs.kind for obs in observations] == []


def test_normalize_group_payload_treats_heading_only_container_prune_as_expected_split() -> None:
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="4",
        children=(IRNode(kind=IRNodeKind.NUM, text="4 luku"),),
    )
    ctx = _mock_ctx("chapter", "4", live_node=live_container)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.CHAPTER,
        target_section="4",
        target_special="otsikko",
        source_statute="2022/603",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="4 luku"),
            IRNode(kind=IRNodeKind.HEADING, text="4 luku Uusi otsikko"),
            IRNode(kind=IRNodeKind.SECTION, label="20"),
            IRNode(kind=IRNodeKind.SECTION, label="21"),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, {"20", "21"})

    observations = _observations(got)
    completeness = _completeness(got)
    assert _pathologies(got) == ()
    # Sections "20" and "21" are NEW (not in live_member_labels).
    # With Bug C fix, new sections are kept in the container payload,
    # so no pruning observation is emitted.
    assert [obs.kind for obs in observations] == []
    assert completeness.kind == "complete"


def test_normalize_group_payload_treats_new_container_prune_as_expected_split() -> None:
    ctx = _mock_ctx("chapter", "5c", live_node=None)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.CHAPTER,
        target_section="5c",
        source_statute="2001/999",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5c",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 c luku"),
            IRNode(kind=IRNodeKind.SECTION, label="19j"),
            IRNode(kind=IRNodeKind.SECTION, label="20a"),
            IRNode(kind=IRNodeKind.SECTION, label="20h"),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, {"20a", "20h"})

    observations = _observations(got)
    completeness = _completeness(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["20a", "20h"]
    assert completeness.kind == "complete"


def test_normalize_group_payload_expands_single_tail_insert_across_post_omission_subsections() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    op = AmendmentOp(
        op_id="insert_1_2",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_paragraph=2,
        lo=LegalOperation(
            op_id="insert_1_2",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmainen uusi momentti."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="Toinen uusi momentti."),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="Kolmas uusi momentti."),)
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [op.target_paragraph for op in got.group_ops] == [2, 3, 4]
    assert len(got.subsec_map) == 3


def test_normalize_group_payload_expands_single_tail_insert_across_post_omission_subsections_with_replace() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="22",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "22", live_node=live_sec)
    replace_op = AmendmentOp(
        op_id="replace_22_1",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="22",
        target_paragraph=1,
        lo=LegalOperation(
            op_id="replace_22_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "22"), ("subsection", "1"))),
        ),
    )
    insert_op = AmendmentOp(
        op_id="insert_22_5",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="22",
        target_paragraph=5,
        lo=LegalOperation(
            op_id="insert_22_5",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "22"), ("subsection", "5"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="22",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="22 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Korvattu ensimmainen momentti."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi viides momentti."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi kuudes momentti."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [replace_op, insert_op], muutos_ir, set())

    assert [op.op_type for op in got.group_ops] == ["REPLACE", "INSERT", "INSERT"]
    assert [op.target_paragraph for op in got.group_ops] == [1, 5, 6]
    assert len(got.subsec_map) == 3


def test_payload_completeness_fragmentary_for_unassigned_sparse_slots() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
        ),
    )
    ctx = _mock_ctx("section", "14", live_node=live_sec)
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 1 mom"),)
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),)
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="irrallinen loppu"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op1], muutos_ir, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "fragmentary"
    assert got.payload_completeness.tail_policy == "preserve_unstated_tail"
    assert got.payload_completeness.detail["unassigned_payload_slots"] == ["2:2", "3:(unlabeled)"]


def test_payload_completeness_sparse_certified_for_tail_omission_binding() -> None:
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_paragraph=2,
    )
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"), IRNode(kind=IRNodeKind.CONTENT, text="Gamma")),
            ),
        ),
    )
    ctx = _mock_ctx("section", "4", live_node=live_sec)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi loppu"),)
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "sparse_certified"
    assert got.payload_completeness.tail_policy == "preserve_unstated_tail"
    assert "mapped_tail_omission" in got.payload_completeness.reasons


def test_item_targeted_sparse_slot_label_mismatch_is_not_ambiguous_binding() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="73",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="73 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Johdanto."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Luettelo:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "73", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="73",
        target_paragraph=2,
        target_item="2",
        source_statute="2011/269",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="73",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="73 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Luettelo:"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "sparse_certified"
    assert "ambiguous_binding" not in got.payload_completeness.reasons
    assert all(obs.kind != "ELAB.AMBIGUOUS_BINDING" for obs in _observations(got))


def test_payload_completeness_inline_enum_candidate_for_missing_item_body() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="12"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "2", live_node=live_sec)
    op_missing = AmendmentOp(
        op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="2", target_paragraph=1, target_item="4"
    )
    op_present = AmendmentOp(
        op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="2", target_paragraph=1, target_item="12"
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tässä laissa tarkoitetaan:"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="12"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op_missing, op_present], muutos_ir, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "inline_enum_candidate"
    assert got.payload_completeness.tail_policy == "classify_or_conservative_lift"
    assert "SPARSE_ITEM_BODY_MISSING" in got.payload_completeness.detail["pathology_codes"]


def test_payload_completeness_complete_for_plain_whole_payload() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="vanha"),),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi teksti"),),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "complete"
    assert got.payload_completeness.tail_policy == "replace_if_target_scope_requires"


def test_payload_completeness_unsupported_missing_payload_ir_emits_rejected_op() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        source_statute="2020/1",
    )

    got = elaborate_payload_against_live(ctx, [op], None, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "unsupported"
    assert got.payload_completeness.tail_policy == "classify_only"
    assert got.payload_completeness.reasons == ("missing_payload_ir",)
    assert [failed.description for failed in got.rejected_ops] == [op.description()]
    assert [failed.reason for failed in got.rejected_ops] == ["ELAB.UNSUPPORTED_PAYLOAD_MISSING_PAYLOAD_IR"]
    assert [failed.reason_code for failed in got.rejected_ops] == ["UNSUPPORTED_PAYLOAD_MISSING_PAYLOAD_IR"]


def test_payload_completeness_allows_payloadless_repeal_group() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPEAL",
        target_kind=TargetKind.SECTION,
        target_section="3",
        source_statute="2024/1049",
        voimaantulo_repeal=True,
    )

    got = elaborate_payload_against_live(ctx, [op], None, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "complete"
    assert got.payload_completeness.reasons == ("payloadless_repeal_group",)
    assert got.rejected_ops == ()


def test_payload_completeness_unsupported_shape_pathology_emits_rejected_op() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)
    op1 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=1,
        source_statute="2010/1399",
    )
    op3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=3,
        source_statute="2010/1399",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tuomioistuin voi"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="tuomita pituudeltaan määräämänsä..."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Mitä rikoslain 7 luvun 6 §:ssä säädetään..."),),
            ),
        ),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op1, op3],
        muutos_ir,
        _replay_profile_stub(),
        None,
    )
    got = elaborate_payload_against_live(ctx, [op1, op3], prepared, set())

    assert got.payload_completeness is not None
    assert got.payload_completeness.kind == "unsupported"
    assert got.payload_completeness.tail_policy == "classify_only"
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in got.payload_completeness.reasons
    assert [failed.description for failed in got.rejected_ops] == [op1.description(), op3.description()]
    assert [failed.reason for failed in got.rejected_ops] == [
        "ELAB.UNSUPPORTED_PAYLOAD_DESTRUCTIVE_SHAPE_LOSS_RISK",
        "ELAB.UNSUPPORTED_PAYLOAD_DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]
    assert [failed.reason_code for failed in got.rejected_ops] == [
        "UNSUPPORTED_PAYLOAD_DESTRUCTIVE_SHAPE_LOSS_RISK",
        "UNSUPPORTED_PAYLOAD_DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]


from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction
from lawvm.core.ir_helpers import irnode_to_text


def test_drop_redundant_case3_keeps_insert_when_lettered_item_not_in_live() -> None:
    """INSERT '3a' alongside REPLACE '3' must be kept when '3a' is new (not in live).

    Regression for 2011/507 §11: amendment 2025/1209 adds uusi 3 a kohta while
    also modifying items 3, 7, 11, 14.  The payload normaliser was incorrectly
    treating REPLACE '3' as the 'lettered-family base' owner of INSERT '3a' and
    suppressing the INSERT.  Only suppress when the item already exists in live.
    """
    op_replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="11",
        target_paragraph=1,
        target_item="3",
    )
    op_insert3a = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="11",
        target_paragraph=1,
        target_item="3a",
    )
    # Live state: subsection 1 has items 1–5 but NO '3a' yet.
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="11",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="11 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tunnuksia ovat:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="5"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "11", live_node=live_sec)
    # Amendment body: subsection 1 has updated item '3' + new '3a' + omission.
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="11",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="11 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tunnuksia ovat:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3a"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_replace3, op_insert3a],
        muutos_ir,
        set(),
    )

    # INSERT '3a' must survive — it's a new item not yet in live state.
    assert ("INSERT", 1, "3a", None) in {
        (op.op_type, op.target_paragraph, op.target_item, op.target_special)
        for op in got.group_ops
    }, "INSERT 3a kohta should not be dropped when item is new in live state"
    assert not any(
        failed.description == "INSERT 11 § 1 mom 3a kohta" for failed in got.rejected_ops
    ), "INSERT 3a should not appear in rejected_ops when item is new"


def test_drop_redundant_case3_drops_insert_when_lettered_item_exists_in_live() -> None:
    """INSERT '3a' alongside REPLACE '3' must be dropped when '3a' already exists in live.

    When the item being inserted already exists in the live state, the INSERT
    would create a duplicate label.  In this scenario a co-slot REPLACE of the
    lettered base ('3') is sufficient to suppress the INSERT.

    The muutos_ir here is already omission-free (simulating post-prepare_payload_surface),
    because omission resolution happens before _drop_redundant_item_ops_claimed_by_sparse_slot.
    """
    op_replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="11",
        target_paragraph=1,
        target_item="3",
    )
    op_insert3a = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="11",
        target_paragraph=1,
        target_item="3a",
    )
    # Live state: subsection 1 already HAS '3a'.
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="11",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="11 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tunnuksia ovat:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3a"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "11", live_node=live_sec)
    # Amendment body: already omission-resolved (as after prepare_payload_surface).
    # Subsection 1 has all live items + updated '3' and '3a' (no omissions).
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="11",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="11 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tunnuksia ovat:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3a"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4"),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op_replace3, op_insert3a],
        muutos_ir,
        set(),
    )

    # INSERT '3a' should be suppressed — item already exists in live.
    assert not any(
        (op.op_type, op.target_item) == ("INSERT", "3a") for op in got.group_ops
    ), "INSERT 3a should be dropped when item already exists in live state"
