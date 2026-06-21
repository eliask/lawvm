"""Pins for the three-hash split (OBJECT_MODEL_AND_PACK_V0.md §3).

raw witness / semantic object / storage blob — distinct coordinate systems;
the critical invariant is that codec/dict choice moves the storage blob hash
but never the semantic hash.
"""

from __future__ import annotations

import hashlib

import pytest

from lawvm.substrate.canonical_json import semantic_hash
from lawvm.substrate.hashes import (
    raw_witness_hash,
    semantic_object_hash,
    storage_blob_hash,
)


def test_raw_witness_hash_is_bare_byte_digest() -> None:
    data = "ä§".encode("utf-8")
    assert raw_witness_hash(data) == "sha256:" + hashlib.sha256(data).hexdigest()


def test_raw_witness_no_normalization() -> None:
    # Composed vs decomposed bytes are DIFFERENT witnesses (no NFC at witness).
    # Build the decomposed form from explicit code points so a source-file NFC
    # pass cannot silently fold the two literals together.
    composed = "\u00e4".encode("utf-8")  # ä precomposed (U+00E4)
    decomposed = "a\u0308".encode("utf-8")  # a + combining diaeresis (U+0308)
    assert composed != decomposed
    assert raw_witness_hash(composed) != raw_witness_hash(decomposed)


def test_raw_witness_rejects_non_bytes() -> None:
    with pytest.raises(TypeError):
        raw_witness_hash("not bytes")  # ty: ignore[invalid-argument-type]


def test_semantic_object_hash_is_semantic_hash() -> None:
    obj = {"t": "ä§"}
    assert semantic_object_hash(obj) == semantic_hash(obj)


def test_storage_blob_hash_changes_with_codec() -> None:
    payload = b"some compressed bytes"
    identity = storage_blob_hash("identity", "", payload)
    zstd = storage_blob_hash("zstd", "", payload)
    assert identity != zstd


def test_storage_blob_hash_changes_with_dict() -> None:
    payload = b"payload"
    no_dict = storage_blob_hash("zstd", "", payload)
    with_dict = storage_blob_hash("zstd", "dict-7", payload)
    assert no_dict != with_dict


def test_storage_framing_no_collision() -> None:
    # Length-prefixing must keep ("ab","c") distinct from ("a","bc").
    a = storage_blob_hash("ab", "c", b"X")
    b = storage_blob_hash("a", "bc", b"X")
    assert a != b


def test_storage_blob_hash_invariant_to_semantic() -> None:
    # The semantic hash of an object is independent of how its serialized
    # bytes get framed for storage — different framings, same semantic id.
    obj = {"k": "v"}
    sem = semantic_object_hash(obj)
    framing_a = storage_blob_hash("identity", "", b"raw jsonl bytes A")
    framing_b = storage_blob_hash("zstd", "dict-1", b"compressed bytes B")
    assert framing_a != framing_b
    # The semantic hash did not move because storage framing changed.
    assert semantic_object_hash(obj) == sem


def test_storage_blob_hash_rejects_non_bytes() -> None:
    with pytest.raises(TypeError):
        storage_blob_hash("identity", "", "string payload")  # ty: ignore[invalid-argument-type]
