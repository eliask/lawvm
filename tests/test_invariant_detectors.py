from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.invariant_detectors import InvariantDetectorResult
from lawvm.core.invariant_detectors import SUPPORTED_INVARIANT_DETECTORS
from lawvm.core.invariant_detectors import run_descendant_sibling_loss_detector
from lawvm.core.invariant_detectors import run_invariant_detector, run_invariant_detector_messages
from lawvm.core.invariant_detectors import run_same_source_descendant_snapshot_shadow_detector
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.tools.cli import _INVARIANT_DETECTOR_CHOICES


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
