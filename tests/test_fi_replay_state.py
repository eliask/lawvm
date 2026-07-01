from __future__ import annotations

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.statute import ReplayState


def _sec(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _chap(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=sections)


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=children)


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=children)


def test_replaced_provision_subtree_index_matches_full_rebuild_with_duplicate_labels() -> None:
    body = _body(
        _chap("1", _sec("2")),
        _chap("2", _sec("1")),
        _chap("3", _sec("2")),
    )
    state = ReplayState(ir=body)
    assert state.provision_index

    chapter_path = (("chapter", "2"),)
    old_chapter = _tops.resolve_required(body, chapter_path)
    new_chapter = _chap("2", _sec("1"), _sec("2"))
    new_ir = _tops.replace_at(body, chapter_path, new_chapter)

    updated = state.with_replaced_provision_subtree_index(
        new_ir,
        path=chapter_path,
        old_subtree=old_chapter,
        new_subtree=new_chapter,
    )

    assert updated.provision_index == ReplayState(ir=new_ir).provision_index
    assert updated.provision_index[("section", "2")] == [
        (("chapter", "1"), ("section", "2")),
        (("chapter", "2"), ("section", "2")),
        (("chapter", "3"), ("section", "2")),
    ]


def test_replaced_provision_subtree_index_falls_back_when_index_is_not_primed() -> None:
    body = _body(_chap("1", _sec("1")))
    state = ReplayState(ir=body)
    chapter_path = (("chapter", "1"),)
    old_chapter = _tops.resolve_required(body, chapter_path)
    new_chapter = _chap("1", _sec("1"), _sec("2"))
    new_ir = _tops.replace_at(body, chapter_path, new_chapter)

    updated = state.with_replaced_provision_subtree_index(
        new_ir,
        path=chapter_path,
        old_subtree=old_chapter,
        new_subtree=new_chapter,
    )

    assert updated.provision_index == ReplayState(ir=new_ir).provision_index


def test_replaced_provision_subtree_index_uses_replacement_root_label() -> None:
    body = _body(_chap("1", _sec("1")))
    state = ReplayState(ir=body)
    assert state.provision_index

    old_path = (("chapter", "1"),)
    old_chapter = _tops.resolve_required(body, old_path)
    new_chapter = _chap("2", _sec("1"))
    new_ir = _tops.replace_at(body, old_path, new_chapter)

    updated = state.with_replaced_provision_subtree_index(
        new_ir,
        path=old_path,
        old_subtree=old_chapter,
        new_subtree=new_chapter,
    )

    assert updated.provision_index == ReplayState(ir=new_ir).provision_index
    assert updated.resolve((("chapter", "1"), ("section", "1"))) is None
    assert updated.resolve((("chapter", "2"), ("section", "1"))) is not None


def test_replaced_provision_subtree_index_treats_sections_as_terminal() -> None:
    body = _body(_chap("1", _sec("1")))
    state = ReplayState(ir=body)
    assert state.provision_index

    section_path = (("chapter", "1"), ("section", "1"))
    old_section = _tops.resolve_required(body, section_path)
    nested_section = IRNode(kind=IRNodeKind.SECTION, label="99")
    new_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(_sub("1", nested_section),),
    )
    new_ir = _tops.replace_at(body, section_path, new_section)

    updated = state.with_replaced_provision_subtree_index(
        new_ir,
        path=section_path,
        old_subtree=old_section,
        new_subtree=new_section,
    )

    assert updated.provision_index == ReplayState(ir=new_ir).provision_index
    assert ("section", "99") not in updated.provision_index


def test_replaced_provision_subtree_index_falls_back_when_changed_path_is_not_live() -> None:
    body = _body(_chap("1", _sec("1")))
    state = ReplayState(ir=body)
    assert state.provision_index

    old_path = (("chapter", "1"),)
    old_chapter = _tops.resolve_required(body, old_path)
    new_ir = _body()

    updated = state.with_replaced_provision_subtree_index(
        new_ir,
        path=old_path,
        old_subtree=old_chapter,
        new_subtree=old_chapter,
    )

    assert updated.provision_index == ReplayState(ir=new_ir).provision_index


def test_preserved_provision_index_dead_section_path_rebuilds_on_lookup() -> None:
    body = _body(_chap("6a", _sec("25"), _sec("25a")))
    state = ReplayState(ir=body)
    old_path = state.find_section_path("25", "6a")
    cached_index = state._provision_index

    new_ir = _body(_chap("6a", _sec("25a")))
    updated = state.with_ir(new_ir, preserve_provision_index=True)

    assert old_path == (("chapter", "6a"), ("section", "25"))
    assert updated._provision_index is cached_index
    assert updated.find_section_path("25", "6a") is None
    assert updated.find_section_path("25a", "6a") == (("chapter", "6a"), ("section", "25a"))
    assert updated._provision_index == ReplayState(ir=new_ir).provision_index
