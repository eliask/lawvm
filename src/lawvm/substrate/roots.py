"""The four named root constructors (OBJECT_MODEL_AND_PACK_V0.md §2; design §9).

    LeafHash(domain, obj)        = sha256("lawvm:"+domain+"\\x00"      + cjson(obj))
    SetRoot(domain, hashes)      = sha256("lawvm:"+domain+":set\\x00"  + cjson(sorted(hashes)))
    SeqRoot(domain, hashes)      = sha256("lawvm:"+domain+":list\\x00" + cjson(list(hashes)))
    MapRoot(domain, {key: hash}) = sha256("lawvm:"+domain+":map\\x00"  + cjson(sorted([[k,v]…])))

All return ``"sha256:" + hexdigest()``.

``LeafHash`` / ``SetRoot`` / ``SeqRoot`` are byte-for-byte re-implementations of
the verified ``certificate_bundle`` constructors (``leaf_hash`` :213,
``list_root`` :221 = ``SeqRoot``, ``set_root`` :232). ``MapRoot`` is new
(§2.1): ``SetRoot``-over-pairs with a ``:map`` suffix — emit each entry as
``[key, value_hash]``, sort by key, reject duplicate keys, hash the sorted
pair-array. The map form exists so a checker can detect a **missing** or
**surplus** key, not just verify present rows (§2.1, open-reconciliation §8.4).
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Mapping, Sequence

from lawvm.substrate.canonical_json import JsonValue, canonical_json_bytes


class RootError(ValueError):
    """A root-construction invariant was violated (duplicate leaf / key)."""


def _rendered(digest: "hashlib._Hash") -> str:
    return "sha256:" + digest.hexdigest()


def leaf_hash(domain: str, obj: JsonValue) -> str:
    """``LeafHash(domain, obj)`` — domain tag + canonical bytes (§2.1)."""
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b"\x00")
    h.update(canonical_json_bytes(obj))
    return _rendered(h)


# Spec alias: ``LeafHash`` is the design-name for ``leaf_hash``.
LeafHash = leaf_hash


def seq_root(domain: str, ordered_leaf_hashes: Sequence[str]) -> str:
    """``SeqRoot(domain, ordered)`` (§2.1). Order significant; duplicate INVALID.

    Spec alias of the verified ``list_root`` (``:list`` suffix).
    """
    ordered = list(ordered_leaf_hashes)
    if len(set(ordered)) != len(ordered):
        raise RootError(f"duplicate leaf under SeqRoot({domain!r}) — INVALID per spec §2.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":list\x00")
    h.update(canonical_json_bytes(ordered))
    return _rendered(h)


# Spec alias: ``SeqRoot`` is ``list_root`` renamed (Seq = List by design name).
SeqRoot = seq_root


def set_root(domain: str, leaf_hashes: Iterable[str]) -> str:
    """``SetRoot(domain, leaves)`` (§2.1). Sorted; duplicate leaf INVALID."""
    leaves = sorted(leaf_hashes)
    for a, b in zip(leaves, leaves[1:], strict=False):
        if a == b:
            raise RootError(f"duplicate leaf under SetRoot({domain!r}) — INVALID per spec §2.1")
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":set\x00")
    h.update(canonical_json_bytes(leaves))
    return _rendered(h)


SetRoot = set_root


def map_root(domain: str, mapping: Mapping[str, str]) -> str:
    """``MapRoot(domain, {key: value_hash})`` (§2.1; build-new, §8.4).

    Root over sorted ``[key, value_hash]`` pairs under a ``:map`` suffix. Keys
    are stable string ids (e.g. ``address_id``, ``selection_key``). The map
    form lets a checker detect a missing or surplus key — changing,
    adding, or removing any key changes the root.

    Empty-map root: ``map_root(domain, {})`` is well-defined (hash of the empty
    pair-array ``[]``), mirroring ``set_root`` / ``seq_root`` over an empty
    sequence. Duplicate keys are structurally impossible in a Python
    ``Mapping``; an explicit guard rejects any caller that smuggles in a
    repeated key via a non-dedup mapping type, matching the duplicate-rejection
    convention of the existing constructors.
    """
    pairs: list[list[str]] = []
    seen: set[str] = set()
    for key in mapping:
        if not isinstance(key, str):
            raise RootError(f"MapRoot({domain!r}) key must be a string, got {type(key).__name__}")
        if key in seen:
            raise RootError(f"duplicate key {key!r} under MapRoot({domain!r}) — INVALID per §2.1")
        seen.add(key)
        value = mapping[key]
        if not isinstance(value, str):
            raise RootError(
                f"MapRoot({domain!r}) value for key {key!r} must be a hash string, "
                f"got {type(value).__name__}"
            )
        pairs.append([key, value])
    pairs.sort(key=lambda pair: pair[0])
    h = hashlib.sha256()
    h.update(b"lawvm:" + domain.encode("utf-8") + b":map\x00")
    h.update(canonical_json_bytes(pairs))
    return _rendered(h)


MapRoot = map_root
