"""Pins for the four named root constructors (OBJECT_MODEL_AND_PACK_V0.md §2).

Byte-for-byte equality with the verified cert-bundle ``leaf_hash`` /
``list_root`` / ``set_root`` constructors, plus the new ``MapRoot``
empty/duplicate-key/key-presence rules (§8.4 open-reconciliation).
"""

from __future__ import annotations

from typing import Iterator

import pytest

from lawvm.substrate.roots import (
    RootError,
    leaf_hash,
    map_root,
    seq_root,
    set_root,
)


def test_leaf_hash_matches_cert_bundle() -> None:
    from lawvm.tools.certificate_bundle import leaf_hash as cert_leaf

    obj = {"a": 1, "t": "ä§"}
    assert leaf_hash("struct_node", obj) == cert_leaf("struct_node", obj)


def test_seq_root_matches_cert_bundle_list_root() -> None:
    from lawvm.tools.certificate_bundle import list_root as cert_list

    hashes = ["sha256:aa", "sha256:bb", "sha256:cc"]
    assert seq_root("trace", hashes) == cert_list("trace", hashes)


def test_set_root_matches_cert_bundle() -> None:
    from lawvm.tools.certificate_bundle import set_root as cert_set

    hashes = ["sha256:cc", "sha256:aa", "sha256:bb"]
    assert set_root("base", hashes) == cert_set("base", hashes)


def test_seq_root_order_significant() -> None:
    a = seq_root("d", ["sha256:1", "sha256:2"])
    b = seq_root("d", ["sha256:2", "sha256:1"])
    assert a != b


def test_set_root_order_invariant() -> None:
    a = set_root("d", ["sha256:1", "sha256:2"])
    b = set_root("d", ["sha256:2", "sha256:1"])
    assert a == b


def test_seq_and_set_reject_duplicates() -> None:
    with pytest.raises(RootError, match="duplicate"):
        seq_root("d", ["sha256:1", "sha256:1"])
    with pytest.raises(RootError, match="duplicate"):
        set_root("d", ["sha256:1", "sha256:1"])


def test_root_constructors_return_prefixed() -> None:
    assert leaf_hash("d", {"x": 1}).startswith("sha256:")
    assert seq_root("d", []).startswith("sha256:")
    assert set_root("d", []).startswith("sha256:")
    assert map_root("d", {}).startswith("sha256:")


def test_map_root_empty_is_well_defined() -> None:
    # Empty-map root mirrors empty set/seq: a deterministic value, not an error.
    r1 = map_root("selection_universe", {})
    r2 = map_root("selection_universe", {})
    assert r1 == r2
    # The empty-map root differs from a non-empty one.
    assert r1 != map_root("selection_universe", {"k": "sha256:v"})


def test_map_root_domain_separated_from_set() -> None:
    # The :map suffix must keep MapRoot distinct from a SetRoot over the values.
    mapping = {"a": "sha256:1"}
    assert map_root("d", mapping) != set_root("d", ["sha256:1"])


def test_map_root_key_presence_is_detectable() -> None:
    base = {"a": "sha256:1", "b": "sha256:2"}
    root = map_root("d", base)
    # Removing a key changes the root (missing-key detection).
    assert root != map_root("d", {"a": "sha256:1"})
    # Adding a surplus key changes the root.
    assert root != map_root("d", {**base, "c": "sha256:3"})
    # Changing a key name (same value) changes the root.
    assert root != map_root("d", {"a": "sha256:1", "B": "sha256:2"})


def test_map_root_value_change_detectable() -> None:
    root = map_root("d", {"a": "sha256:1"})
    assert root != map_root("d", {"a": "sha256:2"})


def test_map_root_key_order_invariant() -> None:
    # Dict insertion order must not affect the root (sorted by key).
    a = map_root("d", {"a": "sha256:1", "b": "sha256:2"})
    b = map_root("d", {"b": "sha256:2", "a": "sha256:1"})
    assert a == b


def test_map_root_rejects_non_string_value() -> None:
    with pytest.raises(RootError, match="hash string"):
        map_root("d", {"a": 1})  # ty: ignore[invalid-argument-type]


class _RepeatedKeyMapping:
    """A Mapping-like that yields a duplicate key — exercises the dup guard.

    Plain ``dict`` literals dedup keys, so the duplicate-key rejection is
    reachable only by a non-dedup mapping type smuggling a repeated key.
    """

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def __iter__(self) -> "Iterator[str]":
        return iter(key for key, _ in self._pairs)

    def __getitem__(self, key: str) -> str:
        for candidate, value in self._pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def __len__(self) -> int:
        return len(self._pairs)


def test_map_root_rejects_duplicate_key() -> None:
    smuggled = _RepeatedKeyMapping([("a", "sha256:1"), ("a", "sha256:2")])
    with pytest.raises(RootError, match="duplicate key"):
        map_root("d", smuggled)  # ty: ignore[invalid-argument-type]
