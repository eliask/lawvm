from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.mutation_boundary import diff_ir_paths, unexplained_changed_paths
from lawvm.core.semantic_types import IRNodeKind


def test_diff_ir_paths_reports_leaf_text_change() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="old"),),
    )
    after = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="new"),),
    )

    assert diff_ir_paths(before, after) == ((("section", "1"),),)


def test_diff_ir_paths_reports_parent_shape_change() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    after = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
        ),
    )

    assert diff_ir_paths(before, after) == ((),)


def test_unexplained_changed_paths_filters_allowed_prefixes() -> None:
    changed_paths = (
        (("section", "1"), ("subsection", "1")),
        (("section", "2"),),
    )
    allowed_prefixes = ((("section", "1"),),)

    assert unexplained_changed_paths(changed_paths, allowed_prefixes) == (
        (("section", "2"),),
    )
