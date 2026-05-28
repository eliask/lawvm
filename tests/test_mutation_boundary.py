from __future__ import annotations

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.mutation_boundary import (
    build_mutation_boundary_report,
    diff_ir_paths,
    partition_changed_paths,
    path_has_prefix,
    tree_path_from_legal_address,
    unexplained_changed_paths,
)
from lawvm.core.semantic_types import IRNodeKind

TREE_PATHS = st.lists(
    st.tuples(
        st.sampled_from(("part", "chapter", "section", "subsection", "paragraph", "item")),
        st.text(min_size=0, max_size=3),
    ),
    max_size=3,
).map(tuple)


def test_tree_path_from_legal_address_uses_boundary_path_shape() -> None:
    address = LegalAddress(path=(("section", "1"), ("subsection", "2")))

    assert tree_path_from_legal_address(address) == (
        ("section", "1"),
        ("subsection", "2"),
    )


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


@settings(max_examples=100)
@given(
    st.lists(TREE_PATHS, max_size=12).map(tuple),
    st.lists(TREE_PATHS, max_size=6).map(tuple),
)
def test_partition_changed_paths_is_total_and_prefix_correct(
    changed_paths: tuple[tuple[tuple[str, str], ...], ...],
    allowed_prefixes: tuple[tuple[tuple[str, str], ...], ...],
) -> None:
    partition = partition_changed_paths(changed_paths, allowed_prefixes)

    assert Counter(partition.covered_changed_paths) + Counter(partition.unexplained_changed_paths) == Counter(
        changed_paths
    )
    assert all(path_has_prefix(path, allowed_prefixes) for path in partition.covered_changed_paths)
    assert not any(path_has_prefix(path, allowed_prefixes) for path in partition.unexplained_changed_paths)
    assert unexplained_changed_paths(changed_paths, allowed_prefixes) == partition.unexplained_changed_paths


def test_build_mutation_boundary_report_partitions_changed_paths() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="old one"),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="old two"),
        ),
    )
    after = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="new one"),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="new two"),
        ),
    )

    report = build_mutation_boundary_report(
        before,
        after,
        allowed_prefixes=((("section", "1"),),),
    )

    assert report.changed_paths == (
        (("section", "1"),),
        (("section", "2"),),
    )
    assert report.covered_changed_paths == ((("section", "1"),),)
    assert report.unexplained_changed_paths == ((("section", "2"),),)
