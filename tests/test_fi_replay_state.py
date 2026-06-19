from __future__ import annotations

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.statute import ReplayState


def _sec(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _chap(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=sections)


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
