from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.invariant_detectors import InvariantDetectorResult
from lawvm.core.invariant_detectors import run_invariant_detector, run_invariant_detector_messages
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


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
