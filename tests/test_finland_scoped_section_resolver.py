from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import Path
from lawvm.finland.scoped_section_resolver import (
    find_scoped_section_insert_parent_path,
    find_scoped_section_path,
    section_paths_for_label,
    unique_root_or_only_section_path,
    unique_same_part_different_chapter_section_path,
)


def _sec(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _chapter(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(children))


def _part(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def test_scoped_section_path_normalizes_roman_part_scope_without_dropping_it() -> None:
    ir = _body(
        _part("1", _chapter("1", _sec("8"))),
        _part("2", _chapter("1", _sec("8"))),
    )
    index = _tops.build_label_index(ir)

    def find_path(kind: str, label: str, scope_kind: str | None, scope_label: str | None) -> Path | None:
        return _tops.find(
            ir,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
            label_index=index,
        )

    assert find_scoped_section_path(
        ir,
        target_section="8",
        target_chapter="1",
        target_part="II",
        find_path=find_path,
    ) == (("part", "2"), ("chapter", "1"), ("section", "8"))

    assert find_scoped_section_path(
        ir,
        target_section="9",
        target_chapter="1",
        target_part="II",
        find_path=find_path,
    ) is None


def test_scoped_section_insert_parent_path_keeps_missing_scope_policies_explicit() -> None:
    ir = _body(
        _part("1", _chapter("1", _sec("8"))),
        _part("2", _sec("root")),
    )

    def find_part_path(label: str) -> Path | None:
        return _tops.find(ir, "part", label)

    def fallback_parent(_chapter_label: str | None) -> Path:
        return (("chapter", "fallback"),)

    assert find_scoped_section_insert_parent_path(
        ir,
        chapter_label="9",
        part_label="2",
        find_part_path=find_part_path,
        find_insert_parent_path=fallback_parent,
        missing_part_policy="fallback",
        missing_chapter_in_part_policy="part",
    ) == (("part", "2"),)

    assert find_scoped_section_insert_parent_path(
        ir,
        chapter_label="9",
        part_label="2",
        find_part_path=find_part_path,
        find_insert_parent_path=fallback_parent,
        missing_part_policy="not_found",
        missing_chapter_in_part_policy="not_found",
    ) is None

    assert find_scoped_section_insert_parent_path(
        ir,
        chapter_label="1",
        part_label="3",
        find_part_path=find_part_path,
        find_insert_parent_path=fallback_parent,
        missing_part_policy="fallback",
        missing_chapter_in_part_policy="part",
    ) == (("chapter", "fallback"),)

    assert find_scoped_section_insert_parent_path(
        ir,
        chapter_label="1",
        part_label="3",
        find_part_path=find_part_path,
        find_insert_parent_path=fallback_parent,
        missing_part_policy="not_found",
        missing_chapter_in_part_policy="not_found",
    ) is None


def test_section_candidate_selectors_keep_part_and_chapter_boundaries() -> None:
    ir = _body(
        _sec("8"),
        _part("1", _chapter("1", _sec("8"))),
        _part("2", _chapter("1", _sec("8")), _chapter("2", _sec("8"))),
    )
    index = _tops.build_label_index(ir)

    all_paths = section_paths_for_label(index, "8")
    assert unique_root_or_only_section_path(all_paths) == (("section", "8"),)

    part_two_paths = section_paths_for_label(index, "8", target_part="II")
    assert part_two_paths == (
        (("part", "2"), ("chapter", "1"), ("section", "8")),
        (("part", "2"), ("chapter", "2"), ("section", "8")),
    )
    assert unique_root_or_only_section_path(part_two_paths) is None
    assert unique_same_part_different_chapter_section_path(
        all_paths,
        target_part="II",
        target_chapter="1",
    ) == (("part", "2"), ("chapter", "2"), ("section", "8"))
