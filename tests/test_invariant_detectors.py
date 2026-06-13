from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.invariant_profiles import TreeInvariantProfile
from lawvm.core.invariant_profiles import collect_tree_invariant_dicts
from lawvm.core.invariant_profiles import collect_tree_invariant_messages
from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.invariant_profiles import replay_delta_minimal_profile
from lawvm.core.invariant_profiles import replay_invariant_profile
from lawvm.core.invariant_profiles import structural_product_hierarchical_profile
from lawvm.core.invariant_profiles import structural_product_strict_profile
from lawvm.core.invariant_detectors import InvariantDetectorResult
from lawvm.core.invariant_detectors import SUPPORTED_INVARIANT_DETECTORS
from lawvm.core.invariant_detectors import run_descendant_sibling_loss_detector
from lawvm.core.invariant_detectors import run_invariant_detector, run_invariant_detector_messages
from lawvm.core.invariant_detectors import run_label_normalization_collision_detector
from lawvm.core.invariant_detectors import run_same_source_descendant_snapshot_shadow_detector
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.tools.cli import _INVARIANT_DETECTOR_CHOICES
from lawvm.tools.invariant_bisect import _run_fi_invariant_detector_messages


def test_tree_invariant_profile_projects_surface_messages_and_typed_rows() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(IRNode(kind=IRNodeKind.SECTION, label="148"),),
            ),
            IRNode(kind=IRNodeKind.SECTION, label="149"),
        ),
    )
    profile = TreeInvariantProfile(
        surface="materialized_tree",
        families=("mixed_hierarchy_child",),
    )

    assert collect_tree_invariant_messages(tree, profile) == (
        "materialized_tree:body: direct section:149 alongside chapter:15",
    )
    row = collect_tree_invariant_dicts(tree, profile)[0]
    assert row["kind"] == "mixed_hierarchy_child"
    assert row["surface"] == "materialized_tree"
    assert row["profile_id"] == "custom"


def test_core_tree_invariant_profile_presets_name_reusable_family_sets() -> None:
    strict = structural_product_strict_profile("replay_fold_tree")
    hierarchical = structural_product_hierarchical_profile("materialized_tree")
    replay_delta = replay_delta_minimal_profile("after_op")

    assert strict.profile_id == "core_structural_product_strict"
    assert strict.families == ("duplicate_label", "unexpected_child_kind")
    assert hierarchical.profile_id == "core_structural_product_hierarchical"
    assert hierarchical.families == ("duplicate_label", "unexpected_child_kind", "mixed_hierarchy_child")
    assert replay_delta.profile_id == "core_replay_delta_minimal"
    assert replay_delta.families == ("duplicate_label", "sort_order")


def test_core_replay_invariant_profile_is_declarative_and_opt_in() -> None:
    profile = core_replay_strict_profile("after_op")

    data = profile.to_dict()

    assert data["profile_id"] == "core_replay_strict_v1"
    assert data["mutation_accounting"] == "hard"
    assert data["transition_detectors"] == (
        "descendant_sibling_loss",
        "same_source_descendant_snapshot_shadow",
    )
    assert data["timeline_invariants"] == (
        "temporal_overlap",
        "temporary_overlay",
        "expiry_chain",
        "replay_timeline",
    )
    assert data["warnings"] == ("text_duplication", "flattened_sublist_family")
    assert data["local_allowance_policy"] == "frontend_required"
    assert data["local_classifier_policy"] == "frontend_required"
    assert data["replay_authorization_claims"] is False
    assert data["tree_profiles"] == (
        {
            "surface": "after_op",
            "profile_id": "core_replay_delta_minimal",
            "families": ("duplicate_label", "sort_order"),
        },
    )


def test_replay_invariant_profile_rejects_unknown_shared_family_names() -> None:
    with pytest.raises(ValueError, match="transition_detectors"):
        replay_invariant_profile(
            profile_id="bad",
            transition_detectors=cast(Any, ("frontend_specific_detector",)),
        )
    with pytest.raises(ValueError, match="mutation_accounting"):
        replay_invariant_profile(
            profile_id="bad",
            mutation_accounting=cast(Any, "execute"),
        )


def test_run_invariant_detector_returns_typed_tree_results_with_legacy_messages() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="2"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
        ),
    )

    results = run_invariant_detector(tree, "duplicate_label")

    assert [result.kind for result in results] == ["duplicate_label"]
    assert results[0].path_text == "body"
    assert results[0].message == "body: duplicate section:1 (2 times)"
    assert isinstance(results[0].detail, FrozenDict)


def test_label_normalization_collision_detector_uses_injected_slot_identity() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="4a"),
            IRNode(kind=IRNodeKind.SECTION, label="iva"),
        ),
    )

    def normalizer(label: str) -> str:
        return "4a" if label in {"4a", "iva"} else label

    results = run_label_normalization_collision_detector(tree, normalizer)

    assert [result.kind for result in results] == ["label_normalization_collision"]
    assert results[0].path_text == "body"
    assert results[0].message == (
        "body: label-normalization collision section:4a from labels 4a, iva"
    )
    assert results[0].detail["labels"] == ("4a", "iva")


def test_default_duplicate_detector_does_not_import_jurisdiction_label_semantics() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="4a"),
            IRNode(kind=IRNodeKind.SECTION, label="iva"),
        ),
    )

    assert run_invariant_detector(tree, "duplicate_label") == []


def test_sort_order_detector_exposes_same_kind_child_ordering() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="5"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
        ),
    )

    results = run_invariant_detector(tree, "sort_order")

    assert len(results) == 1
    assert results[0].detector == "sort_order"
    assert results[0].kind == "sort_order"
    assert results[0].path_text == "body"
    assert results[0].message == "body: section out of order: 5 > 2"
    assert results[0].detail["previous_label"] == "5"
    assert results[0].detail["next_label"] == "2"


def test_sort_order_detector_filters_by_parent_path() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="3"),
                    IRNode(kind=IRNodeKind.SECTION, label="1"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="4"),
                    IRNode(kind=IRNodeKind.SECTION, label="2"),
                ),
            ),
        ),
    )

    messages = run_invariant_detector_messages(tree, "sort_order", target_path="chapter:2")

    assert messages == ["body/chapter:2: section out of order: 4 > 2"]


def test_sort_order_detector_only_compares_same_ordered_child_kinds() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CONTENT, label="z"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
            IRNode(kind=IRNodeKind.HEADING, label="late"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
        ),
    )

    messages = run_invariant_detector_messages(tree, "sort_order")

    assert messages == ["body: section out of order: 2 > 1"]


def test_mixed_hierarchy_detector_flags_direct_section_alongside_chapter() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="149a"),
        ),
    )

    results = run_invariant_detector(tree, "mixed_hierarchy")

    assert len(results) == 1
    assert results[0].kind == "mixed_hierarchy_child"
    assert results[0].message == "body: direct section:149a alongside chapter:15"
    assert results[0].detail["parent_kind"] == "body"
    assert results[0].detail["child_kind"] == "section"
    assert results[0].detail["label"] == "149a"
    assert results[0].detail["container_kind"] == "chapter"
    assert results[0].detail["container_label"] == "15"


def test_mixed_hierarchy_detector_flags_direct_section_alongside_part() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.PART, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
        ),
    )

    messages = run_invariant_detector_messages(tree, "mixed_hierarchy")

    assert messages == ["body: direct section:2 alongside part:1"]


def test_mixed_hierarchy_detector_allows_section_subsections() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
                ),
            ),
        ),
    )

    assert run_invariant_detector(tree, "mixed_hierarchy") == []


def test_mixed_hierarchy_detector_is_not_part_of_default_tree_invariants() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="149a"),
        ),
    )

    assert run_invariant_detector(tree, "all_tree") == []


def test_fi_invariant_detector_messages_use_finland_label_normalizer() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.PART, label="4a"),
            IRNode(kind=IRNodeKind.PART, label="iva"),
        ),
    )

    assert _run_fi_invariant_detector_messages(
        tree,
        "label_normalization_collision",
    ) == ["body: label-normalization collision part:4a from labels 4a, iva"]


def test_run_invariant_detector_filters_by_typed_path_before_message_projection() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                ),
            ),
        ),
    )

    messages = run_invariant_detector_messages(tree, "duplicate_label", target_path="section:2")

    assert messages == ["body/section:2: duplicate subsection:1 (2 times)"]


def test_run_invariant_detector_rejects_unknown_detector() -> None:
    tree = IRNode(kind=IRNodeKind.BODY)

    with pytest.raises(ValueError, match="unsupported invariant detector 'typo_detector'"):
        run_invariant_detector(tree, "typo_detector")


def test_cli_inlined_choices_match() -> None:
    """Guard against drift between cli.py's inlined tuple and the canonical source."""
    assert _INVARIANT_DETECTOR_CHOICES == SUPPORTED_INVARIANT_DETECTORS, (
        "cli._INVARIANT_DETECTOR_CHOICES is out of sync with "
        "invariant_detectors.SUPPORTED_INVARIANT_DETECTORS — update cli.py"
    )


def test_invariant_detector_result_freezes_detail_payload() -> None:
    detail = {"count": 2}

    result = InvariantDetectorResult(
        detector="duplicate_label",
        kind="duplicate_label",
        path_text="body",
        message="body: duplicate section:1 (2 times)",
        detail=detail,
    )
    detail["count"] = 9

    assert result.detail["count"] == 2
    with pytest.raises(TypeError):
        cast(Any, result.detail)["count"] = 3


def test_descendant_sibling_loss_detector_flags_sparse_broad_snapshot() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.ITEM, label="1", text="one"),
                            IRNode(kind=IRNodeKind.ITEM, label="2", text="two"),
                            IRNode(kind=IRNodeKind.ITEM, label="3", text="three"),
                        ),
                    ),
                ),
            ),
        ),
    )
    sparse_snapshot = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.ITEM, label="2", text="two replaced"),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="snapshot_section_2",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=sparse_snapshot,
    )

    results = run_descendant_sibling_loss_detector(before, (op,))

    assert [result.kind for result in results] == ["descendant_sibling_loss"]
    assert results[0].path_text == "section:2/subsection:1"
    assert "2 missing: 1, 3" in results[0].message
    assert results[0].detail["op_id"] == "snapshot_section_2"
    assert results[0].detail["missing_child_kind"] == "item"


def test_descendant_sibling_loss_detector_ignores_single_descendant_deletion() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.ITEM, label="1", text="one"),
                            IRNode(kind=IRNodeKind.ITEM, label="2", text="two"),
                        ),
                    ),
                ),
            ),
        ),
    )
    one_missing = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.ITEM, label="2", text="two replaced"),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="snapshot_section_2",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=one_missing,
    )

    assert run_descendant_sibling_loss_detector(before, (op,)) == []


def test_same_source_descendant_snapshot_shadow_detector_flags_conflicting_child_payload() -> None:
    source = OperationSource(statute_id="2022/1029")
    ancestor_snapshot = LegalOperation(
        op_id="snapshot_section_32",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="32",
            children=(
                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="new first"),
                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="stale second"),
            ),
        ),
        source=source,
    )
    descendant_snapshot = LegalOperation(
        op_id="snapshot_subsection_2_from_section_32",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="authoritative second"),
        source=source,
    )

    results = run_same_source_descendant_snapshot_shadow_detector(
        (ancestor_snapshot, descendant_snapshot)
    )

    assert [result.kind for result in results] == ["same_source_descendant_snapshot_shadow"]
    assert results[0].path_text == "section:32/subsection:2"
    assert "snapshot_section_32 conflicts with snapshot_subsection_2_from_section_32" in results[0].message
    assert results[0].detail["source_statute"] == "2022/1029"
    assert results[0].detail["ancestor_target"] == "section:32"
    assert results[0].detail["descendant_target"] == "section:32/subsection:2"


def test_same_source_descendant_snapshot_shadow_detector_ignores_matching_or_uncontained_payload() -> None:
    source = OperationSource(statute_id="2022/1029")
    ancestor_snapshot = LegalOperation(
        op_id="snapshot_section_32",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="32",
            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="same text"),),
        ),
        source=source,
    )
    matching_descendant = LegalOperation(
        op_id="snapshot_subsection_2_from_section_32",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="same text"),
        source=source,
    )
    absent_descendant = LegalOperation(
        op_id="snapshot_subsection_3_from_section_32",
        sequence=3,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"), ("subsection", "3"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="not present"),
        source=source,
    )
    other_source_descendant = LegalOperation(
        op_id="snapshot_subsection_2_from_section_32_other",
        sequence=4,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "32"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="different but other source"),
        source=OperationSource(statute_id="2023/1"),
    )

    assert (
        run_same_source_descendant_snapshot_shadow_detector(
            (
                ancestor_snapshot,
                matching_descendant,
                absent_descendant,
                other_source_descendant,
            )
        )
        == []
    )
