from __future__ import annotations

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import (
    build_operation_mutation_boundary_report,
    build_mutation_boundary_report,
    dedupe_tree_paths,
    diff_ir_paths,
    operation_storage_boundary_prefixes,
    partition_changed_paths,
    path_has_prefix,
    paths_related,
    normalize_tree_path_for_relation,
    tree_path_from_legal_address,
    unexplained_changed_paths,
    validate_tree_path,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction

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


def test_dedupe_tree_paths_preserves_first_seen_normalized_paths() -> None:
    assert dedupe_tree_paths(
        [
            (("section", "1"),),
            (("section", "1"),),
            (("section", "2"),),
        ]
    ) == (
        (("section", "1"),),
        (("section", "2"),),
    )


def test_validate_tree_path_allows_root_and_empty_labels_but_rejects_empty_kinds() -> None:
    assert validate_tree_path(()) == ()
    assert validate_tree_path((("schedule", ""),)) == ()
    assert validate_tree_path((), allow_root=False) == ("tree path must not be the root path",)
    assert validate_tree_path((("", "1"),), field_name="event path") == (
        "event path step 0 requires a non-empty kind",
    )


def test_paths_related_handles_ancestor_descendant_and_ignored_kinds() -> None:
    assert paths_related(
        (("chapter", "1"), ("section", "2")),
        (("section", "2"), ("subsection", "1")),
        ignored_kinds=frozenset({"chapter"}),
    )
    assert normalize_tree_path_for_relation(
        (("part", "A"), ("chapter", "1"), ("section", "2")),
        ignored_kinds=frozenset({"part", "chapter"}),
    ) == (("section", "2"),)


def test_paths_related_handles_symbolic_sibling_labels() -> None:
    assert paths_related(
        (("section", "5"), ("subsection", "1"), ("item", "last")),
        (("section", "5"), ("subsection", "1"), ("item", "8")),
        special_labels=frozenset({"first", "last"}),
    )
    assert not paths_related(
        (("section", "5"), ("subsection", "1"), ("item", "7")),
        (("section", "5"), ("subsection", "1"), ("item", "8")),
        special_labels=frozenset({"first", "last"}),
    )


def test_operation_storage_boundary_prefixes_text_target_target_path() -> None:
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
    )

    assert operation_storage_boundary_prefixes(op) == ((("section", "1"), ("subsection", "2")),)


def test_operation_storage_boundary_prefixes_insert_target_parent_path() -> None:
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
        anchor=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
    )

    assert operation_storage_boundary_prefixes(op) == ((("section", "1"),),)


def test_operation_storage_boundary_prefixes_renumber_covers_source_and_destination_parents() -> None:
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
        destination=LegalAddress(path=(("section", "3"), ("subsection", "4"))),
    )

    assert operation_storage_boundary_prefixes(op) == (
        (("section", "1"),),
        (("section", "3"),),
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


def test_partition_changed_paths_rejects_malformed_changed_paths_and_allowed_prefixes() -> None:
    try:
        partition_changed_paths(((("", "1"),),), ())
    except ValueError as exc:
        assert "changed path step 0 requires a non-empty kind" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected malformed changed path rejection")

    try:
        partition_changed_paths((), ((("", "1"),),))
    except ValueError as exc:
        assert "allowed prefix step 0 requires a non-empty kind" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected malformed allowed prefix rejection")


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


def test_build_operation_boundary_report_covers_insert_parent_shape_change() -> None:
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
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2"),
        anchor=LegalAddress(path=(("section", "1"),)),
    )

    report = build_operation_mutation_boundary_report(before, after, op)

    assert report.changed_paths == ((),)
    assert report.covered_changed_paths == ((),)
    assert report.unexplained_changed_paths == ()


def test_build_operation_boundary_report_flags_unrelated_text_change() -> None:
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
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="new one"),
    )

    report = build_operation_mutation_boundary_report(before, after, op)

    assert report.covered_changed_paths == ((("section", "1"),),)
    assert report.unexplained_changed_paths == ((("section", "2"),),)


def test_build_operation_boundary_report_covers_replace_payload_key_change_at_parent() -> None:
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    after = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1A"),),
    )
    op = LegalOperation(
        op_id="op",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1A"),
    )

    report = build_operation_mutation_boundary_report(before, after, op)

    assert report.changed_paths == ((),)
    assert report.covered_changed_paths == ((),)
    assert report.unexplained_changed_paths == ()
