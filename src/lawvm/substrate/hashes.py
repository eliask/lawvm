"""The three-hash split, made explicit (OBJECT_MODEL_AND_PACK_V0.md §3; design §7).

Three hashes that must **never** be collapsed:

    raw_witness_hash  = sha256(raw source bytes)                       proves what was observed; NO normalization
    semantic_hash     = sha256(canonical_json_bytes(object))            LawVM identity
    storage_blob_hash = sha256(codec_id + dict_id + compressed bytes)   transport/cache integrity ONLY

Critical invariant (§3): dictionary/codec choice changes ``storage_blob_hash``,
**never** ``semantic_hash``. A zstd recompress or dict swap re-packs the same
objects under the same semantic hashes.

v0 storage encoding is ``codec_id = "identity"`` (raw JSONL) or ``"zstd"``
(whole-file, no dict); ``dict_id = ""``. The split exists now so zstd/dicts
land later without touching semantic identity.
"""

from __future__ import annotations

import hashlib

from lawvm.substrate.canonical_json import JsonValue
from lawvm.substrate.canonical_json import semantic_hash as _semantic_hash


def raw_witness_hash(raw_bytes: bytes) -> str:
    """``"sha256:" + sha256(raw source bytes)`` — what was observed (§3).

    No normalization: this proves the exact bytes witnessed at the source, the
    coordinate system distinct from semantic identity.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"raw_witness_hash needs bytes, got {type(raw_bytes).__name__}")
    return "sha256:" + hashlib.sha256(bytes(raw_bytes)).hexdigest()


def semantic_object_hash(obj: JsonValue) -> str:
    """LawVM identity = ``semantic_hash`` over the canonical-JSON body (§3).

    Thin re-export of :func:`lawvm.substrate.canonical_json.semantic_hash` so
    callers reading the three-hash split see all three names in one place.
    """
    return _semantic_hash(obj)


def storage_blob_hash(codec_id: str, dict_id: str, compressed_bytes: bytes) -> str:
    """``"sha256:" + sha256(codec_id + dict_id + compressed bytes)`` (§3).

    Transport/cache integrity ONLY — never a semantic identity. The codec and
    dict ids are length-prefixed into the digest so distinct ``(codec, dict)``
    framings of the same payload bytes cannot collide, and any codec/dict swap
    deterministically changes the blob hash while the semantic hash holds.
    """
    if not isinstance(compressed_bytes, (bytes, bytearray)):
        raise TypeError(
            f"storage_blob_hash needs bytes payload, got {type(compressed_bytes).__name__}"
        )
    codec = codec_id.encode("utf-8")
    dictionary = dict_id.encode("utf-8")
    h = hashlib.sha256()
    # Length-prefix each framing field so "ab"+"c" cannot collide with "a"+"bc".
    h.update(len(codec).to_bytes(8, "big"))
    h.update(codec)
    h.update(len(dictionary).to_bytes(8, "big"))
    h.update(dictionary)
    h.update(bytes(compressed_bytes))
    return "sha256:" + h.hexdigest()
