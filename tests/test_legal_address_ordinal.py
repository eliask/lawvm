"""Tests for the optional ORDINAL disambiguator on ``LegalAddress`` (#186 §5.4).

Duplicate labels at one level are a real (defective-but-enacted) statute
condition: a ``(kind, label)`` path element then does not uniquely name a slot.
``LegalAddress.ordinals`` carries an OPTIONAL, sparse ``(path_index, ordinal)``
selector so the resolver can pick the Nth occurrence (1-indexed, per the US
phrasing "the second paragraph (1)"). The field is ADDITIVE: an ordinal-free
address must be byte-identical to the pre-ordinal ``LegalAddress`` (same
equality, same hash, same ``path``, same JSON projection), so no existing replay
output changes. These tests pin:

  * ordinal-free equality / hash / str / path are unchanged (byte-identity);
  * ``resolve_with_ordinals`` with no ordinals is identical to ``resolve``;
  * an ordinal selects the Nth of a DUPLICATE label;
  * an ordinal on a UNIQUE label is a consistent no-op (ordinal 1 == first);
  * an out-of-range ordinal makes the path absent (``None``);
  * ordinals participate in equality/hash (differ-only-in-ordinal are distinct);
  * ``ordinal_at`` and ``parent`` behave correctly;
  * ``__post_init__`` validation rejects malformed ordinals.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple, cast

import pytest

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops


def _resolve_text(
    tree: IRNode,
    path: Tuple[Tuple[str, str], ...],
    ordinals: Mapping[int, int],
) -> str:
    """Resolve and assert a hit, returning its ``text`` (narrows Optional)."""
    node = tree_ops.resolve_with_ordinals(tree, path, ordinals)
    assert node is not None
    return node.text or ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _dup_tree() -> IRNode:
    """A body/section/paragraph tree with a DUPLICATE ``paragraph`` label ``1``."""
    p1 = IRNode(kind=IRNodeKind.ITEM, label="1", text="first")
    p2 = IRNode(kind=IRNodeKind.ITEM, label="1", text="second")
    p3 = IRNode(kind=IRNodeKind.ITEM, label="2", text="unique-two")
    sec = IRNode(kind=IRNodeKind.SECTION, label="5", children=(p1, p2, p3))
    return IRNode(kind=IRNodeKind.BODY, children=(sec,))


_ITEM = IRNodeKind.ITEM.value
_SECTION = IRNodeKind.SECTION.value


# ---------------------------------------------------------------------------
# Byte-identity: ordinal-free addresses are unchanged
# ---------------------------------------------------------------------------


def test_ordinal_free_address_is_byte_identical() -> None:
    a = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")))
    b = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")))
    assert a == b
    assert hash(a) == hash(b)
    # Default field is the empty tuple — no ordinal present.
    assert a.ordinals == ()
    assert a.ordinal_at(0) is None
    assert a.ordinal_at(1) is None
    # ``path`` shape and JSON-facing projections are unchanged.
    assert a.path == ((_SECTION, "5"), (_ITEM, "1"))
    assert str(a) == f"{_SECTION}:5/{_ITEM}:1"
    assert a.leaf_kind() == _ITEM and a.leaf_label() == "1"


def test_ordinal_free_hash_matches_positional_construction() -> None:
    # Whether ``ordinals`` is omitted or passed explicitly empty, the address is
    # identical — the default must not perturb equality/hash.
    a = LegalAddress(path=((_SECTION, "5"),))
    b = LegalAddress(path=((_SECTION, "5"),), ordinals=())
    assert a == b
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# Ordinals participate in equality / hash
# ---------------------------------------------------------------------------


def test_ordinal_distinguishes_addresses() -> None:
    base = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")))
    ord2 = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")), ordinals=((1, 2),))
    ord1 = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")), ordinals=((1, 1),))
    assert base != ord2
    assert ord1 != ord2
    # Distinct in a set (participates in hash).
    assert len({base, ord1, ord2}) == 3
    assert ord2.ordinal_at(1) == 2
    assert ord2.ordinal_at(0) is None


def test_ordinal_at_accessor() -> None:
    a = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")), ordinals=((0, 3), (1, 2)))
    assert a.ordinal_at(0) == 3
    assert a.ordinal_at(1) == 2


def test_parent_preserves_ancestor_ordinals_and_drops_leaf() -> None:
    a = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")), ordinals=((0, 2), (1, 3)))
    parent = a.parent()
    assert parent is not None
    assert parent.path == ((_SECTION, "5"),)
    # Ancestor ordinal survives; the dropped leaf's ordinal is elided.
    assert parent.ordinals == ((0, 2),)
    assert parent.ordinal_at(0) == 2
    # Ordinal-free parent stays byte-identical.
    plain = LegalAddress(path=((_SECTION, "5"), (_ITEM, "1")))
    plain_parent = plain.parent()
    assert plain_parent == LegalAddress(path=((_SECTION, "5"),))
    assert plain_parent is not None and plain_parent.ordinals == ()


# ---------------------------------------------------------------------------
# Resolver: resolve_with_ordinals
# ---------------------------------------------------------------------------


def test_resolve_with_empty_ordinals_matches_resolve() -> None:
    tree = _dup_tree()
    path = ((_SECTION, "5"), (_ITEM, "1"))
    baseline = tree_ops.resolve(tree, path)
    assert baseline is not None
    with_empty = tree_ops.resolve_with_ordinals(tree, path, {})
    assert with_empty is baseline
    # The default (no ordinal) picks the FIRST match — pre-ordinal behavior.
    assert baseline.text == "first"


def test_ordinal_selects_nth_duplicate() -> None:
    tree = _dup_tree()
    path = ((_SECTION, "5"), (_ITEM, "1"))
    assert _resolve_text(tree, path, {1: 1}) == "first"
    assert _resolve_text(tree, path, {1: 2}) == "second"


def test_ordinal_out_of_range_is_absent() -> None:
    tree = _dup_tree()
    path = ((_SECTION, "5"), (_ITEM, "1"))
    # Only two ``(item, 1)`` siblings exist — the third occurrence is absent.
    assert tree_ops.resolve_with_ordinals(tree, path, {1: 3}) is None


def test_ordinal_on_unique_label_is_noop() -> None:
    tree = _dup_tree()
    path = ((_SECTION, "5"), (_ITEM, "2"))
    # Label "2" is unique; ordinal 1 == first == the only match.
    unique = tree_ops.resolve(tree, path)
    assert unique is not None and unique.text == "unique-two"
    assert tree_ops.resolve_with_ordinals(tree, path, {1: 1}) is unique
    # Ordinal 2 on a unique label is absent (there is no second occurrence).
    assert tree_ops.resolve_with_ordinals(tree, path, {1: 2}) is None


def test_ordinal_on_ancestor_level() -> None:
    # Duplicate SECTION labels at the top level; the ordinal on index 0 selects.
    sec_a = IRNode(kind=IRNodeKind.SECTION, label="5", children=(IRNode(kind=IRNodeKind.ITEM, label="x", text="in-A"),))
    sec_b = IRNode(kind=IRNodeKind.SECTION, label="5", children=(IRNode(kind=IRNodeKind.ITEM, label="x", text="in-B"),))
    body = IRNode(kind=IRNodeKind.BODY, children=(sec_a, sec_b))
    path = ((_SECTION, "5"), (_ITEM, "x"))
    assert _resolve_text(body, path, {0: 1}) == "in-A"
    assert _resolve_text(body, path, {0: 2}) == "in-B"
    # No ordinal: first-match with backtracking (unchanged).
    baseline = tree_ops.resolve(body, path)
    assert baseline is not None and baseline.text == "in-A"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_ordinal_index_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="out of range"):
        LegalAddress(path=((_SECTION, "5"),), ordinals=((1, 2),))


def test_ordinal_must_be_one_indexed() -> None:
    with pytest.raises(ValueError, match="1-indexed"):
        LegalAddress(path=((_SECTION, "5"),), ordinals=((0, 0),))


def test_ordinal_duplicate_index_rejected() -> None:
    with pytest.raises(ValueError, match="more than once"):
        LegalAddress(path=((_SECTION, "5"),), ordinals=((0, 1), (0, 2)))


def test_ordinal_from_list_is_normalized_to_tuple() -> None:
    # ``__post_init__`` coerces list inputs to tuples (mirrors ``path``).
    a = LegalAddress(
        path=cast(Any, [(_SECTION, "5")]),
        ordinals=cast(Any, [(0, 2)]),
    )
    assert a.ordinals == ((0, 2),)
    assert isinstance(a.ordinals, tuple)
