from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    AmbiguousLookupError,
    MissingPathError,
    build_label_index,
    find,
    find_all,
    find_unique,
    insert_sorted_required,
    insert_after_nth,
    remove_nth,
    remove_at_required,
    replace_at_required,
    replace_nth,
    resolve,
    resolve_required,
)


def _body_with_duplicate_sections() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
            ),),
    )


def test_find_all_preserves_ambiguous_candidates() -> None:
    body = _body_with_duplicate_sections()
    idx = build_label_index(body)

    got = find_all(body, "section", "5", label_index=idx)

    assert len(got) == 2
    assert got[0][-1] == ("section", "5")
    assert got[1][-1] == ("section", "5")


def test_find_unique_raises_on_ambiguous_lookup() -> None:
    body = _body_with_duplicate_sections()
    idx = build_label_index(body)

    try:
        find_unique(body, "section", "5", label_index=idx)
    except AmbiguousLookupError as exc:
        assert "Ambiguous lookup" in str(exc)
    else:
        raise AssertionError("expected AmbiguousLookupError")


def test_find_remains_compatibility_first_match_surface() -> None:
    body = _body_with_duplicate_sections()
    idx = build_label_index(body)

    got = find(body, "section", "5", label_index=idx)

    assert got == (("chapter", "1"), ("section", "5"))


def test_resolve_accepts_list_paths_and_returns_matching_node() -> None:
    body = _body_with_duplicate_sections()

    node = resolve(body, [("chapter", "1"), ("section", "5")])

    assert node is not None
    assert node.kind == IRNodeKind.SECTION
    assert node.label == "5"


def test_resolve_required_raises_on_missing_path() -> None:
    body = _body_with_duplicate_sections()

    try:
        resolve_required(body, [("chapter", "9")])
    except MissingPathError as exc:
        assert "Missing tree path" in str(exc)
    else:
        raise AssertionError("expected MissingPathError")


def test_replace_at_required_raises_on_missing_path() -> None:
    body = _body_with_duplicate_sections()
    replacement = IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement")

    try:
        replace_at_required(body, [("chapter", "1"), ("section", "9")], replacement)
    except MissingPathError as exc:
        assert "section" in str(exc)
    else:
        raise AssertionError("expected MissingPathError")


def test_remove_at_required_raises_on_missing_path() -> None:
    body = _body_with_duplicate_sections()

    try:
        remove_at_required(body, [("chapter", "1"), ("section", "9")])
    except MissingPathError as exc:
        assert "section" in str(exc)
    else:
        raise AssertionError("expected MissingPathError")


def test_insert_sorted_required_raises_on_missing_parent() -> None:
    body = _body_with_duplicate_sections()
    inserted = IRNode(kind=IRNodeKind.SECTION, label="9", text="inserted")

    try:
        insert_sorted_required(body, [("chapter", "9")], inserted)
    except MissingPathError as exc:
        assert "chapter" in str(exc)
    else:
        raise AssertionError("expected MissingPathError")


def test_indexed_child_ops_reject_negative_indices() -> None:
    body = _body_with_duplicate_sections()
    replacement = IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement")

    try:
        replace_nth(body, "section", -1, replacement)
    except ValueError as exc:
        assert "n >= 0" in str(exc)
    else:
        raise AssertionError("expected ValueError from replace_nth")

    try:
        remove_nth(body, "section", -1)
    except ValueError as exc:
        assert "n >= 0" in str(exc)
    else:
        raise AssertionError("expected ValueError from remove_nth")

    try:
        insert_after_nth(body, "section", -1, replacement)
    except ValueError as exc:
        assert "n >= 0" in str(exc)
    else:
        raise AssertionError("expected ValueError from insert_after_nth")
