from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.target_kind import TargetKind
from lawvm.core.elaboration_context import (
    PayloadElaborationContext,
    ReplayLookups,
    TargetContext,
    TargetUnitKind,
    build_payload_elaboration_context,
)
from lawvm.core.payload_elaboration import payload_elaboration_evidence_report
from lawvm.core.proof_surfaces import proof_surface_from_evidence_report
from lawvm.finland.apply_runtime_support import _build_subsection_override_map
from lawvm.finland.compile_group_elaboration import _payload_normalization_observation_rows
from lawvm.finland.helpers import _norm_row_anchor_text
from lawvm.finland.johtolause_supplements import _tag_numbered_table_target_clause_ops
from lawvm.finland.johto_scope_mentions import collect_johto_numbered_table_targets_by_section
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
    _rebase_numbered_table_offset_targets_to_sparse_slot_labels,
    _slot_ir_has_item,
    SparsePayloadSlotBinding,
    SubsectionSlotAssignmentResult,
    payload_elaboration_projection_from_group_result,
    prepare_payload_surface,
    elaborate_payload_against_live,
    summarize_slot_assignment,
)
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.table_target_merge import merge_numbered_table_targets_into_live_section


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


def _table_subsection(label: str, table_label: str, cell_text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=f"Taulukko {table_label}. Paloluokat",
                children=(
                    IRNode(
                        kind=IRNodeKind.TABLE,
                        children=(
                            IRNode(
                                kind=IRNodeKind.ROW,
                                children=(IRNode(kind=IRNodeKind.CELL, text=cell_text),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _table_subsection_with_rows(label: str, table_label: str, row_texts: tuple[str, ...]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=f"Taulukko {table_label}. Paloluokat",
                children=(
                    IRNode(
                        kind=IRNodeKind.TABLE,
                        children=tuple(
                            IRNode(
                                kind=IRNodeKind.ROW,
                                children=(IRNode(kind=IRNodeKind.CELL, text=row_text),),
                            )
                            for row_text in row_texts
                        ),
                    ),
                ),
            ),
        ),
    )


def test_collect_johto_numbered_table_targets_by_section() -> None:
    johto = (
        "Muutetaan 13 §:n taulukko 4, 15 §:n taulukko 5, "
        "33 §:n taulukko 11 ja 2 momentti."
    )

    assert collect_johto_numbered_table_targets_by_section(johto) == {
        "13": frozenset({"4"}),
        "15": frozenset({"5"}),
        "33": frozenset({"11"}),
    }


def test_numbered_table_target_supplement_tags_and_adds_ops() -> None:
    johto = (
        "Muutetaan 13 §:n taulukko 4, 15 §:n taulukko 5 sekä "
        "33 §:n taulukko 11 ja 2 momentti."
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="13",
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="33",
            target_paragraph=2,
        ),
    ]

    got = _tag_numbered_table_target_clause_ops(ops, johto)

    by_target = {(op.target_section, op.target_paragraph): op for op in got}
    assert by_target[("13", None)].numbered_table_targets == ("4",)
    assert by_target[("15", None)].numbered_table_targets == ("5",)
    assert by_target[("33", None)].numbered_table_targets == ("11",)
    assert by_target[("33", 2)].numbered_table_targets == ()


def test_numbered_table_target_merge_replaces_only_table_child() -> None:
    live = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Live heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="live prose"),),
            ),
            _table_subsection("2", "4", "old table"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="trailing prose"),),
            ),
        ),
    )
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Copied heading"),
            _table_subsection("1", "4", "new table"),
        ),
    )

    result = merge_numbered_table_targets_into_live_section(live, amendment, ("4",))

    assert result.rewritten
    assert result.node is not None
    texts = [child.children[0].text for child in result.node.children if child.kind is IRNodeKind.SUBSECTION]
    assert texts == ["live prose", "Taulukko 4. Paloluokat", "trailing prose"]
    assert "new table" in irnode_to_text(result.node)
    assert "old table" not in irnode_to_text(result.node)


def test_numbered_table_target_merge_prunes_duplicate_table_note_block() -> None:
    live = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Live heading"),
            _table_subsection_with_rows(
                "2",
                "4",
                ("old row", "Qfi,k on vanha."),
            ),
        ),
    )
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Copied heading"),
            _table_subsection_with_rows(
                "1",
                "4",
                (
                    "new row",
                    "Qfi,k on tilastollisesti määritetty.",
                    "Kellarikerrokset mitoitetaan palo- ja jäähtymisvaiheen rasituksille.",
                    "1) Ylin kellarikerros, vähintään 600 MJ/m2.",
                    "2) Ylimmän kellarikerroksen alapuolella sijaitsevat kellarikerrokset, 2,0*Qfi,k, vähintään 900 MJ/m2.",
                    "Q fi,k on tilastollisesti määritetty.",
                    "Kellarikerrokset mitoitetaan palo- ja jäähtymisvaiheen rasituksille.",
                    "1) Ylin kellarikerros, vähintään 600 MJ/m 2.",
                    "2) Ylimmän kellarikerroksen alapuolella sijaitsevat kellarikerrokset, 2,0*Q fi,k, vähintään 900 MJ/m 2.",
                ),
            ),
        ),
    )

    result = merge_numbered_table_targets_into_live_section(live, amendment, ("4",))

    assert result.rewritten
    assert result.node is not None
    text = irnode_to_text(result.node)
    assert "MJ/m2" not in text
    assert "MJ/m 2" in text
    rules = result.node.children[1].attrs["lawvm_payload_normalization_rule"]
    assert "ELAB.NUMBERED_TABLE_TARGET_MERGE" in rules
    assert "ELAB.DUPLICATE_TABLE_NOTE_BLOCK_PRUNED" in rules


def test_prepare_payload_surface_merges_numbered_table_target_without_whole_section_replace() -> None:
    live = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Live heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="live prose"),),
            ),
            _table_subsection("2", "4", "old table"),
        ),
    )
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Copied heading"),
            _table_subsection("1", "4", "new table"),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ctx = _mock_ctx("section", "13", live_node=live)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="13",
        numbered_table_targets=("4",),
    )

    prepared = prepare_payload_surface(
        ctx,
        [op],
        amendment,
        _replay_profile_stub(),
        strict_profile=None,
    )

    assert prepared is not None
    assert irnode_to_text(prepared).count("Taulukko 4.") == 1
    assert "live prose" in irnode_to_text(prepared)
    assert "new table" in irnode_to_text(prepared)
    assert "old table" not in irnode_to_text(prepared)


def test_sparse_slot_binding_excludes_numbered_table_payload_from_child_moment() -> None:
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="26",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Heading"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="new third moment"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            _table_subsection("2", "8", "new table"),
        ),
    )
    op = AmendmentOp(
        op_id="insert_26_3",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="26",
        target_paragraph=3,
        numbered_table_targets=("8",),
    )

    slot_inputs = _collect_subsection_slot_inputs(amendment, [op])
    assert slot_inputs is not None
    assert [irnode_to_text(sub) for sub in slot_inputs.amend_subs] == ["new third moment"]

    assignment = _assign_subsection_slots(slot_inputs)
    mapped = assignment.resolve_for_op(op)

    assert mapped is not None
    assert irnode_to_text(mapped) == "new third moment"
    assert "new table" not in irnode_to_text(mapped)


def test_sparse_slot_binding_maps_numbered_table_xml_offset_to_following_subsection() -> None:
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="33",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="live first"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="new legal second"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="live fourth"),)),
        ),
    )
    op = AmendmentOp(
        op_id="replace_33_2",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="33",
        target_paragraph=2,
        numbered_table_targets=("11",),
    )

    slot_inputs = _collect_subsection_slot_inputs(amendment, [op])
    assert slot_inputs is not None
    assignment = _assign_subsection_slots(slot_inputs)
    mapped = assignment.resolve_for_op(op)

    assert mapped is not None
    assert mapped.label == "3"
    assert irnode_to_text(mapped) == "new legal second"
    assert [obs.kind for obs in assignment.binding_observations] == [
        "ELAB.NUMBERED_TABLE_XML_SUBSECTION_OFFSET"
    ]


def test_rebase_numbered_table_offset_targets_to_structural_subsection_label() -> None:
    op = AmendmentOp(
        op_id="replace_33_2",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="33",
        target_paragraph=2,
        numbered_table_targets=("11",),
    )
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(
            by_op_id={id(op): IRNode(kind=IRNodeKind.SUBSECTION, label="3")}
        ),
        sparse_slot_bindings=(),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    rebased, changed, details = _rebase_numbered_table_offset_targets_to_sparse_slot_labels(
        [op],
        assignment,
    )

    assert changed is True
    assert rebased[0].target_paragraph == 3
    assert rebased[0].target_guessing_provenance_tags == (
        "numbered_table_xml_subsection_offset",
    )
    assert details == [
        {
            "op_description": "REPLACE 33 § 2 mom",
            "source_target_paragraph": 2,
            "structural_target_paragraph": 3,
            "payload_slot_label": "3",
            "original_sparse_subsection_label": "",
            "numbered_table_targets": ["11"],
        }
    ]


def test_payload_elaboration_projection_from_group_result_records_slot_bindings() -> None:
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description="REPLACE P 5 1",
                op_type="REPLACE",
                target_paragraph=1,
                target_item=None,
                target_special=None,
                payload_slot_index=0,
                payload_slot_label="1",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    result = GroupPayloadNormalizationResult(
        muutos_ir=None,
        group_ops=(),
        subsec_map=SubsectionSlotMap(),
        slot_assignment=assignment,
        payload_completeness=PayloadCompletenessWitness(kind="complete"),
    )

    projection = payload_elaboration_projection_from_group_result(result, subject_id="fi-demo")
    data = projection.to_dict()

    assert data["result_id"] == "fi:payload_elaboration:fi-demo"
    assert data["owner_phase"] == "payload_elaboration"
    assert data["replay_authorized"] is False
    assert data["completeness_kind"] == "complete"
    assert data["payload_completeness"]["kind"] == "complete"
    assert data["slot_binding_report"]["binding_count"] == 1
    binding = data["slot_binding_report"]["bindings"][0]
    assert binding["source_slot_id"] == "1"
    assert binding["target_slot_id"] == "subsection:1"
    assert "treat_payload_projection_as_replay_authorization" in data["forbidden_shortcuts"]

    report = payload_elaboration_evidence_report(
        projection,
        report_kind="finland_payload_elaboration",
    )
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert report_data["replay_claims"] is False
    assert report_data["summary"]["slot_binding_count"] == 1
    assert report_data["filters"]["owner_phase"] == "payload_elaboration"
    assert proof_surface["surface_kind"] == "finland_payload_elaboration"
    assert {row["row_kind"] for row in proof_surface["rows"]} == {
        "payload_elaboration_result",
        "payload_completeness_witness",
        "slot_binding_report",
        "slot_binding",
    }


def _mock_ctx(
    target_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str] = None,
    *,
    target_part: Optional[str] = None,
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
        target_part=target_part,
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


def test_payload_normalize_item_like_target_preserves_labelled_sparse_insert_subsection() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="9",
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
    ctx = _mock_ctx("section", "9", live_node=live_sec)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="9",
        target_paragraph=2,
        lo=LegalOperation(
            op_id="t9",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "9"), ("subsection", "2"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="9",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Uuden momentin johdanto."),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="ensimmäinen kohta"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="toinen kohta"),
                ),
            ),
        ),
    )

    got = _normalize_item_like_target(ctx, op, muutos_ir)

    assert got.lo is not None
    assert dict(got.lo.target.path) == {"section": "9", "subsection": "2"}
    assert got.target_guessing_provenance_tags == ()


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
    assert isinstance(got, IRNode)
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
    assert isinstance(got, IRNode)
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
    assert isinstance(got, IRNode)
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
    assert isinstance(got, IRNode)
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == [
        "1",
        "2",
        "9",
        "9b",
        "11",
        "12",
    ]


def test_container_payload_prunes_sparse_foreign_scoped_replace_nonmembers() -> None:
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="42"),
            IRNode(kind=IRNodeKind.SECTION, label="43"),
        ),
    )
    ctx = dc_replace(
        _mock_ctx("chapter", "7", target_chapter="7", live_node=live_container),
        container_member_labels=frozenset({"42", "43"}),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="42"),
            IRNode(kind=IRNodeKind.SECTION, label="43"),
            IRNode(kind=IRNodeKind.SECTION, label="51"),
            IRNode(kind=IRNodeKind.SECTION, label="61"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        ctx,
        "chapter",
        "7",
        muutos_ir,
        {"51", "61"},
        foreign_scoped_replace_section_targets={"51", "61"},
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["51", "61"]
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == ["42", "43"]


def test_container_payload_keeps_dense_foreign_replace_bridge() -> None:
    live_container = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="9",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="13"),
            IRNode(kind=IRNodeKind.SECTION, label="17"),
        ),
    )
    ctx = dc_replace(
        _mock_ctx("chapter", "9", target_chapter="9", live_node=live_container),
        container_member_labels=frozenset({"13", "17"}),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="9",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="13"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="16"),
            IRNode(kind=IRNodeKind.SECTION, label="17"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        ctx,
        "chapter",
        "9",
        muutos_ir,
        {"14", "15", "16"},
        foreign_scoped_replace_section_targets={"14", "15", "16"},
    )

    assert changed is False
    assert isinstance(got, IRNode)
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == [
        "13",
        "14",
        "15",
        "16",
        "17",
    ]


def test_container_payload_keeps_dense_foreign_standalone_bridge_with_replace_overlap() -> None:
    ctx = _mock_ctx("chapter", "7", live_node=None)
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="10"),
            IRNode(kind=IRNodeKind.SECTION, label="11"),
            IRNode(kind=IRNodeKind.SECTION, label="12"),
            IRNode(kind=IRNodeKind.SECTION, label="13"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="16"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        ctx,
        "chapter",
        "7",
        muutos_ir,
        {"11", "12", "13", "14", "15"},
        foreign_scoped_standalone_section_targets={"11", "12", "13", "14", "15"},
        foreign_scoped_replace_section_targets={"12", "15"},
        preserve_dense_new_container_payload=True,
    )

    assert changed is False
    assert isinstance(got, IRNode)
    assert pruned == []
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == [
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
    ]


def test_container_payload_prunes_dense_foreign_standalone_bridge_without_new_container_insert() -> None:
    ctx = _mock_ctx("chapter", "7", live_node=None)
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="10"),
            IRNode(kind=IRNodeKind.SECTION, label="11"),
            IRNode(kind=IRNodeKind.SECTION, label="12"),
            IRNode(kind=IRNodeKind.SECTION, label="13"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="16"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        ctx,
        "chapter",
        "7",
        muutos_ir,
        {"11", "12", "13", "14", "15"},
        foreign_scoped_standalone_section_targets={"11", "12", "13", "14", "15"},
        foreign_scoped_replace_section_targets={"12", "15"},
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["11", "12", "13", "14", "15"]
    assert [c.label for c in got.children if c.kind == IRNodeKind.SECTION] == [
        "10",
        "16",
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


def test_build_subsection_slot_assignment_shares_in_place_intro_item_slot() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                attrs={"lawvm_in_place_merge": "1"},
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="new intro"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="new item two"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="new item three"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="different item three"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_paragraph=1,
            target_special="johd",
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_paragraph=1,
            target_item="2",
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_paragraph=1,
            target_item="3",
        ),
    ]

    assignment = _build_subsection_slot_assignment(muutos_ir, ops)
    op2_node = assignment.for_op(ops[2])
    assert op2_node is not None
    assert op2_node.label == "1"
    assert any(obs.kind == "ELAB.SAME_TARGET_ITEM_SLOT_SHARING" for obs in assignment.binding_observations)

    unmerged_slots = tuple(
        IRNode(
            kind=child.kind,
            label=child.label,
            text=child.text,
            attrs={},
            children=child.children,
        )
        if child.kind is IRNodeKind.SUBSECTION
        else child
        for child in muutos_ir.children
    )
    unmerged_muutos_ir = IRNode(kind=muutos_ir.kind, label=muutos_ir.label, children=unmerged_slots)
    unmerged_assignment = _build_subsection_slot_assignment(unmerged_muutos_ir, ops)
    unmerged_op2_node = unmerged_assignment.for_op(ops[2])
    assert unmerged_op2_node is not None
    assert unmerged_op2_node.label == "5"
    assert not any(
        obs.kind == "ELAB.SAME_TARGET_ITEM_SLOT_SHARING"
        for obs in unmerged_assignment.binding_observations
    )


def test_slot_item_matching_does_not_alias_roman_glyph_to_arabic_item() -> None:
    slot = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4", text="numeric item"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="iv", text="roman-looking item"),
        ),
    )

    assert _slot_ir_has_item(slot, "4") is True
    assert _slot_ir_has_item(slot, "iv") is True
    assert _slot_ir_has_item(
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1",
            children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="4", text="numeric item"),),
        ),
        "iv",
    ) is False


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


def test_fold_intro_list_continuation_preserves_multiple_explicit_sparse_targets() -> None:
    """Multi-target sparse moment bodies must align before any tail folding.

    `2001/807 <- 2010/6 / 9 §` targets 3, 5 and 14 mom.  The body serializes
    the first changed moment as an intro/list, then a content-only second
    changed moment with local label 2, then an omission and the final changed
    moment.  The local label 2 is a sparse payload slot, not a lowercase tail
    artifact of the first changed moment.
    """
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="9",
        children=tuple(
            IRNode(kind=IRNodeKind.SUBSECTION, label=str(index))
            for index in range(1, 15)
        ),
    )
    ctx = _mock_ctx("section", "9", live_node=live_sec)
    ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="9", target_paragraph=3),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="9", target_paragraph=5),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="9", target_paragraph=14),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="9",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Päällysmerkintöjen sisältö"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Valmisteen merkintöihin tulee nimet:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="ensimmäinen raja;"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="toinen raja."),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Aineen nimi ilmoitetaan säädetyllä nimellä."),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Varoitusmerkit määrätään liitteessä."),),
            ),
        ),
    )

    prepared = prepare_payload_surface(ctx, ops, muutos_ir, _replay_profile_stub(), None)
    assert prepared is not None
    assert [child.label for child in prepared.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2", "3"]

    normalized = elaborate_payload_against_live(ctx, ops, prepared, set())
    assignment = _slot_assignment_result(normalized)
    assert [assignment.for_op(op).label if assignment.for_op(op) is not None else None for op in ops] == [
        "3",
        "5",
        "14",
    ]
    assert assignment.unassigned_payload_slots == ()


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


def test_fold_intro_list_continuation_folds_single_nonfirst_item_tail() -> None:
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
                        text="Kansainvälisen mittayksikköjärjestelmän perusyksiköt määritellään seuraavasti:",
                    ),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="5)"),
                            IRNode(
                                kind=IRNodeKind.CONTENT,
                                text=(
                                    "lämpötilan yksikkö kelvin, termodynaamisen "
                                    "lämpötilan yksikkö, on 1/273,16 veden"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="kolmoispisteen termodynaamisesta lämpötilasta;",
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_paragraph=1,
            target_item="5",
        )
    ]

    got = _fold_intro_list_continuation_subsection_before_omission("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1"]
    assert (
        "1/273,16 veden kolmoispisteen termodynaamisesta lämpötilasta"
        in irnode_to_text(subs[0])
    )
    paragraphs = [child for child in subs[0].children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["5"]
    assert (
        "1/273,16 veden kolmoispisteen termodynaamisesta lämpötilasta"
        in irnode_to_text(paragraphs[0])
    )


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


def test_normalize_group_payload_splits_single_target_carried_live_tail() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Tietosuojavaltuutetun nimittää tasavallan presidentti "
                            "valtioneuvoston esityksestä."
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Tietosuojavaltuutetuksi nimitetty vapautuu hoitamasta muuta virkaa "
                            "tai tointa siksi ajaksi, jonka hän toimii tietosuojavaltuutettuna."
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Tietosuojavaltuutetusta ja toimistosta säädetään asetuksella.",
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "6", live_node=live_sec)
    group_ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=1),
    ]
    carried_second = irnode_to_text(live_sec.children[1])
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Tietosuojavaltuutetun nimittää valtioneuvosto virkaa haettavaksi "
                            f"julistamatta määräajaksi. {carried_second}"
                        ),
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, group_ops, muutos_ir, set())

    normalized = _muutos_ir(got)
    target_sub = next(child for child in normalized.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    target_text = irnode_to_text(target_sub)
    assert "valtioneuvosto virkaa haettavaksi" in target_text
    assert carried_second not in target_text
    assert got.subsec_map[id(got.group_ops[0])] is target_sub
    observations = _observations(got)
    assert any(
        obs.kind == "ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL"
        and obs.detail is not None
        and obs.detail["target_subsection"] == "1"
        and obs.detail["first_carried_subsection"] == "2"
        for obs in observations
    )
    completeness = _completeness(got)
    assert completeness.kind == "sparse_certified"
    assert completeness.tail_policy == "preserve_unstated_tail"


def test_normalize_group_payload_splits_sparse_replaces_on_changed_live_sentence_anchor() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="7",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha ensimmäinen momentti."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Verotoimisto antaa verovelvolliselle todistuksen "
                            "investointitalletuksen nosto-oikeudesta 18 §:n 1 momentissa "
                            "ja 21 §:ssä tarkoitetuissa tapauksissa."
                        ),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "7", live_node=live_sec)
    group_ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7", target_paragraph=1),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7", target_paragraph=2),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="7",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text=(
                            "Uusi ensimmäinen momentti. "
                            "Verotoimisto antaa verovelvolliselle todistuksen "
                            "investointitalletuksen nosto-oikeudesta 21 §:ssä "
                            "tarkoitetuissa tapauksissa."
                        ),
                    ),
                ),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, group_ops, muutos_ir, set())

    mapped_first = got.subsec_map.for_op(got.group_ops[0])
    mapped_second = got.subsec_map.for_op(got.group_ops[1])
    assert mapped_first is not None
    assert mapped_second is not None
    assert mapped_first.label == "1"
    assert mapped_second.label == "2"
    assert irnode_to_text(mapped_first) == "Uusi ensimmäinen momentti."
    assert "21 §:ssä" in irnode_to_text(mapped_second)
    assert "18 §:n" not in irnode_to_text(mapped_second)
    assert [obs.kind for obs in _observations(got)] == ["ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE"]


def test_normalize_group_payload_does_not_split_multi_subsection_target_payload() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 1."),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Vanha 2."),)),
        ),
    )
    ctx = _mock_ctx("section", "10", live_node=live_sec)
    group_ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="10", target_paragraph=1),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="10 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi 1. Vanha 2."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi 2."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, group_ops, muutos_ir, set())

    normalized = _muutos_ir(got)
    target_sub = next(child for child in normalized.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    assert "Vanha 2." in irnode_to_text(target_sub)
    observations = _observations(got)
    assert not any(obs.kind == "ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL" for obs in observations)


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


def test_text_table_row_subsections_fold_into_single_targeted_moment() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "6", live_node=live_sec)
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=1)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Museot seuraavasti: Museo mk"),),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alikartano 15"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Hvitträsk 25"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Olavinlinna 30"),)),
        ),
    )

    prepared = prepare_payload_surface(ctx, [op], muutos_ir, _replay_profile_stub(), None)
    got = elaborate_payload_against_live(ctx, [op], prepared, set())

    assert got.muutos_ir is not None
    sub = got.subsec_map.for_op(got.group_ops[0])
    assert sub is not None
    assert "Alikartano 15" in irnode_to_text(sub)
    assert "Hvitträsk 25" in irnode_to_text(sub)
    assert "Olavinlinna 30" in irnode_to_text(sub)
    assert _slot_assignment_result(got).unassigned_payload_slots == ()
    rows = _payload_normalization_observation_rows(
        got.muutos_ir,
        source_statute="2000/1157",
        target_unit_kind="section",
        target_norm="6",
        target_chapter=None,
    )
    assert [row["kind"] for row in rows] == ["ELAB.TEXT_TABLE_ROW_CONTINUATION"]


def test_text_table_row_subsections_do_not_fold_with_multiple_moment_targets() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"), IRNode(kind=IRNodeKind.SUBSECTION, label="2")),
    )
    ctx = _mock_ctx("section", "6", live_node=live_sec)
    op1 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=1)
    op2 = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="6", target_paragraph=2)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="6",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Museot seuraavasti: Museo mk"),),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alikartano 15"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Hvitträsk 25"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op1, op2], muutos_ir, set())

    assert _slot_assignment_result(got).unassigned_payload_slots == ("3:3",)


def test_numbered_table_prefix_does_not_absorb_explicit_moment_payload() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="33",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4"),
        ),
    )
    ctx = _mock_ctx("section", "33", target_chapter="6", live_node=live_sec)
    op = AmendmentOp(
        op_id="replace_33_2",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="33",
        target_chapter="6",
        target_paragraph=2,
        numbered_table_targets=("11",),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="33",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="33 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Taulukko 11. Uloskäytävien vähimmäislukumäärä"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Yhtä uloskäytävää voidaan pitää riittävänä:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="pienessä rakennuksessa;"),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )

    prepared = prepare_payload_surface(ctx, [op], muutos_ir, _replay_profile_stub(), None)
    got = elaborate_payload_against_live(ctx, [op], prepared, set())

    assert got.rejected_ops == ()
    assert [op.target_paragraph for op in got.group_ops] == [3]
    mapped = _slot_assignment_result(got).for_stable_op_id("replace_33_2")
    assert mapped is not None
    assert "Yhtä uloskäytävää" in irnode_to_text(mapped)
    assert "Taulukko 11" not in irnode_to_text(mapped)
    observation_kinds = {observation.kind for observation in _observations(got)}
    assert "ELAB.NUMBERED_TABLE_XML_SUBSECTION_OFFSET" in observation_kinds


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


def _province_table_row(header: str, *cells: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.ROW,
        children=tuple(IRNode(kind=IRNodeKind.CELL, text=cell) for cell in (header, *cells)),
    )


def test_normalize_group_payload_merges_named_row_province_table_blocks() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="13 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        children=(
                            IRNode(
                                kind=IRNodeKind.TABLE,
                                children=(
                                    _province_table_row("Lääni ja kunta", "Veroluokat"),
                                    _province_table_row("Uudenmaan lääni"),
                                    _province_table_row("Espoo", "1,0"),
                                    _province_table_row("Kymen lääni"),
                                    _province_table_row("Hamina", "2,0"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="13 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        children=(
                            IRNode(
                                kind=IRNodeKind.TABLE,
                                children=(
                                    _province_table_row("Lääni ja kunta", "Veroluokat"),
                                    _province_table_row("Kymen lääni"),
                                    _province_table_row("Hamina", "9,9"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "13", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="13",
        source_statute="1992/1009",
        named_row_targets=("kymen",),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())
    merged = _muutos_ir(got)
    text = irnode_to_text(merged)

    assert "Uudenmaan lääni" in text
    assert "Espoo 1,0" in text
    assert "Hamina 9,9" in text
    assert "Hamina 2,0" not in text
    assert any(
        o.kind == "ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE"
        for o in (got.elaboration_observations or ())
    )


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


def test_prepare_group_payload_collapses_first_moment_intro_list_with_lettered_subitems() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="4 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Arviointiselostuksen sisältö"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Selostuksessa esitetään:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="1) hankkeen kuvaus erityisesti:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="a) sijainti;"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="b) koko;"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="2) aikataulu;"),),
            ),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
        )
    ]

    got = _collapse_intro_list_subsections_inside_section_ir("section", ops, muutos_ir)

    assert got is not None
    subs = [c for c in got.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(subs) == 1
    assert subs[0].attrs.get("lawvm_payload_normalization_rule") == (
        "ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST",
    )
    observation_rows = _payload_normalization_observation_rows(
        got,
        source_statute="2021/1163",
        target_unit_kind="section",
        target_norm="4",
        target_chapter=None,
    )
    assert [row["kind"] for row in observation_rows] == [
        "ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST"
    ]
    paragraphs = [c for c in subs[0].children if c.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["1", "2"]
    subparagraphs = [c for c in paragraphs[0].children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert [subparagraph.label for subparagraph in subparagraphs] == ["a", "b"]
    assert irnode_to_text(paragraphs[0]) == "1) hankkeen kuvaus erityisesti: a) sijainti; b) koko;"


def test_flattened_first_moment_collapse_ignores_already_structured_trailing_omission() -> None:
    """A trailing omission on an already-numbered first moment is not a flattened row."""
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Lain soveltamisala"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Tätä lakia sovelletaan:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="ensimmäinen laki;"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="toinen laki."),),
                    ),
                    IRNode(kind=IRNodeKind.OMISSION),
                ),
            ),
        ),
    )
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_paragraph=1,
        )
    ]

    got = _collapse_intro_list_subsections_inside_section_ir("section", ops, muutos_ir)

    assert got is muutos_ir
    sub = next(c for c in got.children if c.kind is IRNodeKind.SUBSECTION)
    assert [child.kind for child in sub.children] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.OMISSION,
    ]
    assert _payload_normalization_observation_rows(
        got,
        source_statute="2017/542",
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    ) == []


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


def test_build_subsection_slot_assignment_prefers_exact_intro_label_when_moments_match() -> None:
    """Regression: 1987/990 §17 / 1994/1420.

    The amendment body has real legal moment labels 1 and 2. The scoped
    ``2 momentin johdantokappale`` must bind to slot 2, not consume slot 1
    merely because it appears before exact plain-slot assignment.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="17",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi 1 momentti."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.INTRO, text="Uusi 2 momentin johdanto:"),),
            ),
        ),
    )
    op1_plain = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="17",
        target_paragraph=1,
    )
    op2_intro = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="17",
        target_paragraph=2,
        target_special="johd",
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op1_plain, op2_intro])

    assert got.subsec_map[id(op1_plain)].label == "1"
    assert got.subsec_map[id(op2_intro)].label == "2"
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


def test_assign_subsection_slots_lone_paragraph_not_reused_by_trailing_insert() -> None:
    """Regression: a trailing INSERT must not reuse a slot a REPLACE consumed.

    Mirrors 1990/1341 §14 (amendment 2016/777): the johtolause is
    ``muutetaan 14 §:n otsikko ja 2 momentti, lisätään 14 §:ään uusi 3
    momentti`` but the published body carries a single paragraph. The
    REPLACE 2 mom claims that lone slot; the INSERT 3 mom then has no
    payload of its own. The positional fallback used to bind the INSERT to
    the already-consumed last slot, emitting the same sentence twice
    (REPLAY_EXTRA double-insert against the oracle). It must instead leave
    the INSERT unbound so its missing payload surfaces as residue.
    """
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="14",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 mom"),),
            ),
        ),
    )
    op_replace = AmendmentOp(
        op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=2
    )
    op_insert = AmendmentOp(
        op_type="INSERT", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=3
    )

    got = _build_subsection_slot_assignment(muutos_ir, [op_replace, op_insert])

    # The replace consumes the lone payload slot.
    assert got.subsec_map[id(op_replace)].label == "2"
    # The insert must NOT reuse it; leaving it unbound prevents the duplicate.
    assert got.for_op(op_insert) is None
    # Only one binding emitted (the replace); no slot is double-bound.
    assert len(got.sparse_slot_bindings) == 1
    assert got.sparse_slot_bindings[0].op_type == "REPLACE"
    assert got.used_subs == (0,)


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


def test_prepare_payload_surface_does_not_merge_new_subsection_insert_inner_omission() -> None:
    """A new-moment INSERT with inner omission must not import live siblings.

    Mirrors 1994/1384 §3 under 2012/221: the source says to add a new 3 mom,
    while live §3 only has moments 1 and 2.  The inner omission belongs to the
    amendment-local list payload.  Treating it as an item insertion into an
    existing live subsection splices live moment 2 into the payload and lets the
    trailing sparse binding rule hijack the new 3 mom target.
    """
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Live heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old first moment"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old second moment"),),
            ),
        ),
    )
    amendment = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="new third moment intro"),
                    IRNode(kind=IRNodeKind.OMISSION),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new third moment item"),
                ),
            ),
        ),
    )
    op_insert = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=3,
    )
    ctx = _mock_ctx("section", "3", live_node=live_sec)

    prepared = prepare_payload_surface(
        ctx,
        [op_insert],
        amendment,
        _replay_profile_stub(),
        strict_profile=None,
    )

    assert prepared is not None
    assert "old second moment" not in irnode_to_text(prepared)
    slot_inputs = _collect_subsection_slot_inputs(prepared, [op_insert])
    assert slot_inputs is not None

    got = _assign_subsection_slots(slot_inputs)

    mapped = got.subsec_map.for_op(op_insert)
    assert mapped is not None
    assert mapped.label == "1"
    assert "new third moment intro" in irnode_to_text(mapped)
    assert "old second moment" not in irnode_to_text(mapped)
    assert not any(obs.kind == "ELAB.TRAILING_SPARSE_INSERT_BINDING" for obs in got.binding_observations)


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

    replace_subsec = got.subsec_map.for_op(op_replace)
    assert replace_subsec is not None
    assert replace_subsec.label == "1"
    insert_subsec = got.subsec_map.for_op(op_insert)
    assert insert_subsec is not None
    assert insert_subsec.label == "2"
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


def test_internal_ordered_list_payload_rewrites_broad_replace_to_item_inserts() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="List I"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Delta"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Sigma"),)),
                    IRNode(kind=IRNodeKind.CONTENT, text="List II"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Zeta"),)),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    lo = LegalOperation(
        op_id="",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress((("section", "1"),)),
        source=OperationSource(
            statute_id="test/1",
            raw_text="muutetaan 1 §:ssä olevaa listan luetteloa I seuraavasti:",
        ),
    )
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="1", lo=lo)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="List I"),)),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="Beta"),)),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.SUBSECTION, children=(IRNode(kind=IRNodeKind.CONTENT, text="Omega"),)),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [(item.op_type, item.target_paragraph, item.target_item) for item in got.group_ops] == [
        ("INSERT", 1, "2"),
        ("INSERT", 1, "4"),
    ]
    assert got.muutos_ir is not None
    rewritten_sub = next(child for child in got.muutos_ir.children if child.kind is IRNodeKind.SUBSECTION)
    assert [(child.label, irnode_to_text(child)) for child in rewritten_sub.children] == [
        ("2", "Beta"),
        ("4", "Omega"),
    ]
    assert [obs.kind for obs in _observations(got) if obs.kind == "ELAB.INTERNAL_ORDERED_LIST_INSERT_REWRITE"] == [
        "ELAB.INTERNAL_ORDERED_LIST_INSERT_REWRITE"
    ]
    assert got.rejected_ops == ()


def test_internal_ordered_list_insert_inference_ignores_leading_stereochemical_prefix() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="List I"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="Alpha"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="trans-N-[3-metyyli]"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="Rasemorfaani"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="4", children=(IRNode(kind=IRNodeKind.CONTENT, text="Sufentaniili"),)),
                ),
            ),
        ),
    )
    ctx = _mock_ctx("section", "1", live_node=live_sec)
    lo = LegalOperation(
        op_id="",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress((("section", "1"),)),
        source=OperationSource(
            statute_id="test/1",
            raw_text="muutetaan 1 §:ssä olevaa listan luetteloa I seuraavasti:",
        ),
    )
    op = AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="1", lo=lo)
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="List I"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="Remifentaniili"),)),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [(item.op_type, item.target_paragraph, item.target_item) for item in got.group_ops] == [
        ("INSERT", 1, "4")
    ]


def test_normalize_group_payload_folds_split_target_subsection_intro_list_tail() -> None:
    """A single legal moment may be split into prefix + intro/list source slots.

    Mirrors `1990/848 <- 2000/54` section 34: the johtolause owns only
    `1 momentti`, while Finlex XML serializes that one moment as two adjacent
    AKN subsections.
    """
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="34",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Vanha vahingonkorvausintro:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="vanha ensimmainen kohta;"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="vanha toinen kohta."),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Erillinen toinen momentti."),),
            ),
        ),
    )
    ctx = _mock_ctx("section", "34", live_node=live_sec)
    op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="34",
        target_paragraph=1,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="34",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="34 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Korvattava vahinko"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Uusi korvauspaalause."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Vahingonkorvausta ei kuitenkaan suoriteta:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="valtiolle aiheutuneesta vahingosta;"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="2)"),
                            IRNode(kind=IRNodeKind.CONTENT, text="muusta vahingosta."),
                        ),
                    ),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assignment = _slot_assignment_result(got)
    mapped = assignment.for_op(got.group_ops[0])
    assert mapped is not None
    assert mapped.label == "1"
    assert assignment.unassigned_payload_slots == ()
    assert "Uusi korvauspaalause." in irnode_to_text(mapped)
    assert "Vahingonkorvausta ei kuitenkaan suoriteta:" in irnode_to_text(mapped)
    assert _slot_ir_has_item(mapped, "1")
    assert _slot_ir_has_item(mapped, "2")
    observations = _observations(got)
    assert [obs.kind for obs in observations] == ["ELAB.SPLIT_TARGET_SUBSECTION_INTRO_LIST_TAIL"]
    detail = observations[0].detail
    assert detail is not None
    assert detail["prefix_payload_slot_label"] == "1"
    assert detail["tail_payload_slot_label"] == "2"
    assert _completeness(got).kind == "sparse_certified"


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


def test_normalize_group_payload_keeps_explicit_heading_and_subsection_replace_shell() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="8",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="8 §"),
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
    ctx = _mock_ctx("section", "8", target_chapter="3", live_node=live_sec)
    heading_op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_chapter="3",
        target_special="otsikko",
        source_statute="2023/1132",
    )
    subsection_op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_chapter="3",
        target_paragraph=1,
        source_statute="2023/1132",
        lo=LegalOperation(
            op_id="subsec1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "3"), ("section", "8"), ("subsection", "1"))),
            source=OperationSource(
                statute_id="2023/1132",
                raw_text="muutetaan 8 §:n otsikko ja 1 momentti seuraavasti:",
            ),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="8",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="8 §"),
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="New first moment."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [heading_op, subsection_op], muutos_ir, set())

    assert [op.description() for op in got.group_ops] == [
        "REPLACE 3 luku 8 § otsikko",
        "REPLACE 3 luku 8 § 1 mom",
    ]
    assert got.rejected_ops == ()
    # The whole-section shell over a plain-subsection-targeted replace is kept, and
    # that keep decision is now witnessed (previously a silent source-plane keep).
    assert [p.code for p in _pathologies(got)] == ["SUBSECTION_SHELL_REPLACE_KEPT"]
    assert got.subsec_map is not None
    mapped = got.subsec_map.for_op(subsection_op)
    assert mapped is not None
    assert mapped.label == "1"
    assert "New first moment" in " ".join(irnode_to_text(mapped).split())


def test_normalize_group_payload_promotes_leading_subsection_heading_for_whole_section_insert() -> None:
    ctx = _mock_ctx("section", "11a", target_chapter="1", live_node=None)
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

    normalized = _muutos_ir(got)
    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.HEADING,
        IRNodeKind.SUBSECTION,
    ]
    assert irnode_to_text(normalized.children[1]) == "Veroilmoituksen antamisaikaa koskeva poikkeava määräys"
    assert normalized.children[2].label == "1"
    assert any(
        observation.kind == "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        and (observation.detail or {})["shifted_subsection_count"] == 1
        for observation in _observations(got)
    )


def test_normalize_group_payload_does_not_promote_sentence_like_first_subsection() -> None:
    ctx = _mock_ctx("section", "11a", target_chapter="1", live_node=None)
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
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Ensimmäinen momentti on tavallista virkettä."),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Toinen momentti säilyy toisena."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    normalized = _muutos_ir(got)
    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
    ]
    assert all(
        observation.kind != "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        for observation in _observations(got)
    )


def test_normalize_group_payload_does_not_promote_inline_styled_first_subsection() -> None:
    ctx = _mock_ctx("section", "7a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="7a",
        source_statute="2024/870",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="7a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 a §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Kunnan työttömyysetuuksien rahoitusvastuun laajentamista koskevan korvauksen laskeminen",
                        attrs={"lawvm_source_inline_tags": ("i",)},
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Laskettaessa korvausta otetaan huomioon puolet summasta."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    normalized = _muutos_ir(got)
    assert [child.kind for child in normalized.children] == [
        IRNodeKind.NUM,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
    ]
    assert all(
        observation.kind != "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        for observation in _observations(got)
    )


def test_normalize_group_payload_drops_intro_only_heading_and_subsection_shell() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
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
    ctx = _mock_ctx("section", "2", target_chapter="2", live_node=live_sec)
    heading_op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_chapter="2",
        target_special="otsikko",
        source_statute="2015/1328",
    )
    subsection_op = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="2",
        target_chapter="2",
        target_paragraph=1,
        source_statute="2015/1328",
        lo=LegalOperation(
            op_id="subsec1-intro",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "2"), ("section", "2"), ("subsection", "1"))),
            source=OperationSource(
                statute_id="2015/1328",
                raw_text="muutetaan 2 §:n otsikko ja 1 momentin johdanto seuraavasti:",
            ),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 §"),
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="New intro only."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [heading_op, subsection_op], muutos_ir, set())

    assert [op.description() for op in got.group_ops] == ["REPLACE 2 luku 2 § otsikko"]
    assert [failed.reason_code for failed in got.rejected_ops] == ["STALE_WHOLE_SECTION_SHELL_REJECTED"]
    assert _pathologies(got)[0].detail["diagnostic_reason"] == "stale_whole_section_shell_heading_mismatch"


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
    assert prepared is not None

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
    assert observations[0].detail["before_child_paths"] == [
        "chapter:5c/section:19j",
        "chapter:5c/section:20a",
        "chapter:5c/section:20h",
    ]
    assert observations[0].detail["after_child_paths"] == ["chapter:5c/section:19j"]
    witnesses = observations[0].detail["pruned_section_witnesses"]
    assert [witness["path"] for witness in witnesses] == [
        "chapter:5c/section:20a",
        "chapter:5c/section:20h",
    ]
    assert all(len(witness["structural_hash"]) == 64 for witness in witnesses)
    assert completeness.kind == "complete"


def test_normalize_group_payload_prunes_foreign_descendant_insert_from_new_container() -> None:
    ctx = _mock_ctx("chapter", "6a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="6a",
        source_statute="2014/1020",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="6a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="6 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="15a"),
            IRNode(kind=IRNodeKind.SECTION, label="15b"),
            IRNode(kind=IRNodeKind.SECTION, label="26"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"26"},
        foreign_scoped_descendant_section_targets={"26"},
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["26"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == [
        "15a",
        "15b",
    ]
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_keeps_new_container_members_shadowed_only_by_foreign_replaces() -> None:
    ctx = _mock_ctx("chapter", "5a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="5a",
        source_statute="2019/581",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="43"),
            IRNode(kind=IRNodeKind.SECTION, label="44"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"44"},
        foreign_scoped_replace_section_targets={"44"},
        foreign_scoped_replace_section_target_scopes=frozenset(
            {StandaloneSectionTarget(part=None, chapter="5", label="44")}
        ),
    )

    assert _pathologies(got) == ()
    assert [obs.kind for obs in _observations(got)] == []
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == [
        "43",
        "44",
    ]
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_prunes_new_container_members_in_dense_foreign_replace_bridge() -> None:
    ctx = _mock_ctx("chapter", "7a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="7a",
        source_statute="2013/1194",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="49"),
            IRNode(kind=IRNodeKind.SECTION, label="50"),
            IRNode(kind=IRNodeKind.SECTION, label="51"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"50"},
        foreign_scoped_replace_section_targets={"50"},
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["50"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == [
        "49",
        "51",
    ]
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_prunes_new_container_members_in_broad_foreign_replace_run() -> None:
    ctx = _mock_ctx("chapter", "5", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="5",
        source_statute="1999/527",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="19"),
            IRNode(kind=IRNodeKind.SECTION, label="24"),
            IRNode(kind=IRNodeKind.SECTION, label="25"),
            IRNode(kind=IRNodeKind.SECTION, label="27"),
            IRNode(kind=IRNodeKind.SECTION, label="34"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"24", "25", "27", "34"},
        foreign_scoped_replace_section_targets={"24", "25", "27", "34"},
        foreign_scoped_replace_section_target_scopes=frozenset(
            {
                StandaloneSectionTarget(part=None, chapter="6", label="24"),
                StandaloneSectionTarget(part=None, chapter="6", label="25"),
                StandaloneSectionTarget(part=None, chapter="6", label="27"),
                StandaloneSectionTarget(part=None, chapter="7", label="34"),
            }
        ),
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["24", "25", "27", "34"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == ["19"]
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_prunes_part_scoped_suffixed_container_when_foreign_base_lacks_part_scope() -> None:
    ctx = _mock_ctx("chapter", "9a", target_part="2", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="9a",
        target_part="2",
        source_statute="1993/1158",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="9a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="78"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"78"},
        foreign_scoped_replace_section_targets={"78"},
        foreign_scoped_replace_section_target_scopes=frozenset(
            {StandaloneSectionTarget(part=None, chapter="9", label="78")}
        ),
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["78"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == []
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_prunes_base_scope_overlap_in_recodification_transfer_context() -> None:
    ctx = _mock_ctx("chapter", "9a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="9a",
        source_statute="1993/1158",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="9a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="78"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"78"},
        foreign_scoped_replace_section_targets={"78"},
        foreign_scoped_replace_section_target_scopes=frozenset(
            {StandaloneSectionTarget(part=None, chapter="9", label="78")}
        ),
        recodification_transfer_context=True,
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["78"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == []
    assert _completeness(got).kind == "complete"


def test_normalize_group_payload_prunes_plain_foreign_replaces_from_suffixed_new_container_payload() -> None:
    ctx = _mock_ctx("chapter", "7a", live_node=None)
    op = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.CHAPTER,
        target_section="7a",
        source_statute="2013/1194",
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="7a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="46a"),
            IRNode(kind=IRNodeKind.SECTION, label="49"),
        ),
    )

    got = elaborate_payload_against_live(
        ctx,
        [op],
        muutos_ir,
        {"49"},
        foreign_scoped_replace_section_targets={"49"},
    )

    observations = _observations(got)
    assert _pathologies(got) == ()
    assert [obs.kind for obs in observations] == ["ELAB.CONTAINER_PRUNED_SHADOWED"]
    assert observations[0].detail is not None
    assert observations[0].detail["pruned_sections"] == ["49"]
    assert [child.label for child in _muutos_ir(got).children if child.kind is IRNodeKind.SECTION] == ["46a"]
    assert _completeness(got).kind == "complete"


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


def test_normalize_group_payload_folds_single_insert_list_tail_subsection() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="16",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "16", live_node=live_sec)
    op = AmendmentOp(
        op_id="insert_16_2",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="16",
        target_paragraph=2,
        lo=LegalOperation(
            op_id="insert_16_2",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "16"), ("subsection", "2"))),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="16",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="16 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Työnantajan on liitettävä selvitys:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="työkyvyttömyydestä;"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="rikoksesta; sekä"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="3",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="maksetusta palkasta,"),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="mikäli selvitystä tarvitaan."),),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, [op], muutos_ir, set())

    assert [op.target_paragraph for op in got.group_ops] == [2]
    mapped = got.subsec_map[id(got.group_ops[0])]
    assert mapped.label == "2"
    assert "maksetusta palkasta" in irnode_to_text(mapped)
    assert "mikäli selvitystä tarvitaan" in irnode_to_text(mapped)
    assert [obs.kind for obs in _observations(got)] == ["ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL"]


def test_normalize_group_payload_splits_flattened_insert_subsection_tail() -> None:
    live_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="15h",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    ctx = _mock_ctx("section", "15h", target_chapter="2", live_node=live_sec)
    ops = [
        AmendmentOp(
            op_id="insert_15h_2",
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="15h",
            target_chapter="2",
            target_paragraph=2,
            lo=LegalOperation(
                op_id="insert_15h_2",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("chapter", "2"), ("section", "15h"), ("subsection", "2"))),
            ),
        ),
        AmendmentOp(
            op_id="insert_15h_3",
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="15h",
            target_chapter="2",
            target_paragraph=3,
            lo=LegalOperation(
                op_id="insert_15h_3",
                sequence=2,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("chapter", "2"), ("section", "15h"), ("subsection", "3"))),
            ),
        ),
    ]
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="15h",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="15 h §"),
            IRNode(kind=IRNodeKind.HEADING, text="Heading"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="First new moment intro:"),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.NUM, text="1)"), IRNode(kind=IRNodeKind.CONTENT, text="first item")),
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.NUM, text="2)"), IRNode(kind=IRNodeKind.CONTENT, text="second item")),
                    ),
                    IRNode(kind=IRNodeKind.WRAP_UP, text="Second new moment tail."),
                ),
            ),
        ),
    )

    got = elaborate_payload_against_live(ctx, ops, muutos_ir, set())

    prepared = _muutos_ir(got)
    subsections = [child for child in prepared.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["2", "3"]
    assert [op.target_item for op in got.group_ops] == [None, None]
    assert got.subsec_map[id(got.group_ops[0])].label == "2"
    assert got.subsec_map[id(got.group_ops[1])].label == "3"
    assert "first item" in irnode_to_text(got.subsec_map[id(got.group_ops[0])])
    assert "Second new moment tail." in irnode_to_text(got.subsec_map[id(got.group_ops[1])])
    assert "ELAB.NORMALIZE_ITEM_LIKE_TARGET" not in [obs.kind for obs in _observations(got)]
    assert "ELAB.SPLIT_FLATTENED_INSERT_SUBSECTION_TAIL" in [obs.kind for obs in _observations(got)]


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
    assert got.payload_completeness.detail["unassigned_payload_slots"] == ("2:2", "3:(unlabeled)")


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


def test_assign_subsection_slots_binds_item_ops_by_momentti_not_item_number() -> None:
    """Item ops spanning two momentit bind to their own momentti payload slot.

    Regression for 2019/906 26 § (amendment 2023/488): the amendment replaces
    and inserts kohta items in both the 4th and 5th momentti. The sparse payload
    carries one subsection per amended momentti, in source order, and both carry
    a "3) ..." and "4) ..." item. Pure item-number matching scrambled the
    bindings, applying stale momentti text. Each op must bind to the payload slot
    for its own momentti.
    """
    amend_sub_4mom = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="Viranomaisen laatimista asiakirjoista rekisteroidaan ainakin:"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="laatimisajankohta;"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4", text="asiakirjan lahettamisajankohta ja lahettamistapa."),
        ),
    )
    amend_sub_5mom = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="Asiarekisteriin rekisteroidaan lisaksi asiasta ainakin:"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="viranomaisen toimenpiteet kasittelyvaiheittain;"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4", text="viranomaisen tekeman ratkaisun ajankohta;"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="5", text="tarvittaessa tiedoksiantotapa."),
        ),
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="26",
        children=(IRNode(kind=IRNodeKind.NUM, text="26 §"), amend_sub_4mom, amend_sub_5mom),
    )
    group_ops = [
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="26", target_paragraph=4, target_item="3"),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="26", target_paragraph=4, target_item="4"),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="26", target_paragraph=5, target_item="3"),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="26", target_paragraph=5, target_item="4"),
        AmendmentOp(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="26", target_paragraph=5, target_item="5"),
    ]

    slot_inputs = _collect_subsection_slot_inputs(muutos_ir, group_ops)
    assert slot_inputs is not None
    assignment = _assign_subsection_slots(slot_inputs)

    # Every momentti-4 op binds to the momentti-4 payload subsection; every
    # momentti-5 op binds to the momentti-5 payload subsection.
    for op in group_ops:
        mapped = assignment.for_op(op)
        assert mapped is not None
        if op.target_paragraph == 4:
            assert mapped is amend_sub_4mom, (op.op_type, op.target_item)
        else:
            assert mapped is amend_sub_5mom, (op.op_type, op.target_item)


def test_assign_subsection_slots_binds_carried_renumber_destination_payload_slots() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="New inserted moment."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Changed old second moment."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Carried old third moment."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="Carried old fourth moment."),
        ),
    )
    insert2 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_paragraph=2,
    )
    replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_paragraph=3,
    )

    def _renumber(source: str, destination: str) -> AmendmentOp:
        lo = LegalOperation(
            op_id=f"renumber_{source}_to_{destination}",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "12"), ("subsection", source))),
            destination=LegalAddress(path=(("section", "12"), ("subsection", destination))),
        )
        return AmendmentOp(
            op_id=lo.op_id,
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="12",
            target_paragraph=int(source),
            lo=lo,
        )

    renumber3 = _renumber("3", "4")
    renumber4 = _renumber("4", "5")
    slot_inputs = _collect_subsection_slot_inputs(
        muutos_ir,
        [insert2, replace3, renumber3, renumber4],
    )
    assert slot_inputs is not None

    assignment = _assign_subsection_slots(slot_inputs)

    insert2_slot = assignment.for_op(insert2)
    replace3_slot = assignment.for_op(replace3)
    renumber3_slot = assignment.for_op(renumber3)
    renumber4_slot = assignment.for_op(renumber4)
    assert insert2_slot is not None
    assert replace3_slot is not None
    assert renumber3_slot is not None
    assert renumber4_slot is not None
    assert insert2_slot.label == "2"
    assert replace3_slot.label == "3"
    assert renumber3_slot.label == "4"
    assert renumber4_slot.label == "5"
    assert assignment.unassigned_payload_slots == ()
    assert [obs.kind for obs in assignment.binding_observations].count(
        "ELAB.RENUMBER_DESTINATION_PAYLOAD_SLOT"
    ) == 2


def test_assign_subsection_slots_binds_same_target_insert_before_moved_replace() -> None:
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="45",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="45 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Changed item list."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="New inserted third moment."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Changed old third moment."),
            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Carried old fourth moment."),
        ),
    )
    replace_item = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="45",
        target_paragraph=1,
        target_item="4",
    )
    insert3 = AmendmentOp(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="45",
        target_paragraph=3,
    )
    replace3 = AmendmentOp(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="45",
        target_paragraph=3,
    )
    renumber3 = AmendmentOp(
        op_id="renumber_3_to_4",
        op_type="RENUMBER",
        target_kind=TargetKind.SECTION,
        target_section="45",
        target_paragraph=3,
        lo=LegalOperation(
            op_id="renumber_3_to_4",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "45"), ("subsection", "3"))),
            destination=LegalAddress(path=(("section", "45"), ("subsection", "4"))),
        ),
    )

    slot_inputs = _collect_subsection_slot_inputs(
        muutos_ir,
        [replace_item, renumber3, insert3, replace3],
    )
    assert slot_inputs is not None

    assignment = _assign_subsection_slots(slot_inputs)
    replace_item_slot = assignment.for_op(replace_item)
    insert3_slot = assignment.for_op(insert3)
    replace3_slot = assignment.for_op(replace3)
    renumber3_slot = assignment.for_op(renumber3)

    assert replace_item_slot is not None
    assert insert3_slot is not None
    assert replace3_slot is not None
    assert renumber3_slot is not None
    assert replace_item_slot.label == "1"
    assert insert3_slot.label == "2"
    assert replace3_slot.label == "3"
    assert renumber3_slot.label == "4"
    assert assignment.unassigned_payload_slots == ()
    found_binding_observation = False
    for obs in assignment.binding_observations:
        if obs.kind != "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT" or obs.detail is None:
            continue
        if (
            obs.detail["target_paragraph"] == 3
            and obs.detail["insert_payload_slot_label"] == "2"
            and obs.detail["replace_payload_slot_label"] == "3"
        ):
            found_binding_observation = True
    assert found_binding_observation
