"""Storage codec seam — pure byte-transform round-trip + framing pins.

The codec is a transform BENEATH a stable content address (OBJECT_MODEL §3):
``decode(codec, encode(codec, x)) == x`` byte for byte, and identity leaves the
bytes untouched. These pins guard the transform in isolation; the exporter
integration (address stability under identity vs zstd) lives in
``test_codec_exporter.py``.
"""

from __future__ import annotations

import hashlib

import pytest

from lawvm.substrate import codec


def _sample_bytes() -> bytes:
    # Realistic JSONL-ish payload with high redundancy (compresses well) + a
    # non-ASCII escape so we exercise raw UTF-8 byte handling.
    return (b'{"object_hash":"sha256:deadbeef","object":{"schema":"x","t":"a"}}\n' * 500) + (
        '{"t":"ä§"}\n'.encode("utf-8")
    )


def test_identity_always_available() -> None:
    assert codec.IDENTITY_CODEC in codec.available_codecs()


def test_identity_is_the_identity_function() -> None:
    raw = _sample_bytes()
    assert codec.encode(codec.IDENTITY_CODEC, raw) == raw
    assert codec.decode(codec.IDENTITY_CODEC, raw) == raw


def test_identity_suffix_is_empty() -> None:
    assert codec.storage_suffix(codec.IDENTITY_CODEC) == ""


def test_unknown_codec_rejected() -> None:
    with pytest.raises(ValueError):
        codec.encode("lz4", b"x")
    with pytest.raises(ValueError):
        codec.decode("lz4", b"x")
    with pytest.raises(ValueError):
        codec.storage_suffix("lz4")


def test_encode_decode_reject_non_bytes() -> None:
    with pytest.raises(TypeError):
        codec.encode(codec.IDENTITY_CODEC, "str")  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        codec.decode(codec.IDENTITY_CODEC, "str")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("payload", [b"", b"x", _sample_bytes()])
def test_identity_round_trip_exact(payload: bytes) -> None:
    assert codec.decode(codec.IDENTITY_CODEC, codec.encode(codec.IDENTITY_CODEC, payload)) == payload


# --------------------------------------------------------------------------- #
# zstd — only when the optional library is present                             #
# --------------------------------------------------------------------------- #

zstd_only = pytest.mark.skipif(
    not codec.zstd_available(), reason="zstandard library not installed"
)


@zstd_only
def test_zstd_listed_when_available() -> None:
    assert codec.ZSTD_CODEC in codec.available_codecs()


@zstd_only
def test_zstd_suffix() -> None:
    assert codec.storage_suffix(codec.ZSTD_CODEC) == ".zst"


@zstd_only
@pytest.mark.parametrize("payload", [b"", b"x", _sample_bytes()])
def test_zstd_round_trip_exact(payload: bytes) -> None:
    blob = codec.encode(codec.ZSTD_CODEC, payload)
    assert codec.decode(codec.ZSTD_CODEC, blob) == payload


@zstd_only
def test_zstd_actually_compresses_redundant_payload() -> None:
    raw = _sample_bytes()
    blob = codec.encode(codec.ZSTD_CODEC, raw)
    # A highly-redundant JSONL payload must shrink a LOT (well under half).
    assert len(blob) < len(raw) // 2


@zstd_only
def test_zstd_is_deterministic() -> None:
    raw = _sample_bytes()
    a = codec.encode(codec.ZSTD_CODEC, raw)
    b = codec.encode(codec.ZSTD_CODEC, raw)
    assert a == b


@zstd_only
def test_zstd_blob_differs_from_plaintext_but_decodes_to_it() -> None:
    raw = _sample_bytes()
    blob = codec.encode(codec.ZSTD_CODEC, raw)
    assert blob != raw
    # The canonical digest is over the DECODED bytes, never the blob.
    assert (
        hashlib.sha256(codec.decode(codec.ZSTD_CODEC, blob)).hexdigest()
        == hashlib.sha256(raw).hexdigest()
    )
