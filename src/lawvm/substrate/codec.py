"""Storage-layer codec seam (OBJECT_MODEL_AND_PACK_V0.md §3; design §7).

A codec is a **pure byte transform beneath a stable content address**. It NEVER
touches semantic identity: a layer's rows are content-addressed by their
``object_hash`` (``semantic_hash`` over the canonical JSON), and the layer's
``uncompressed_sha256`` fixes the canonical JSONL bytes. A codec only decides how
those canonical bytes are framed *on disk / on the wire* — it maps

    canonical_bytes  <->  storage_bytes

with an EXACT round-trip (``decode(codec, encode(codec, x)) == x`` byte for
byte). The three-hash split guarantees that swapping ``identity`` <-> ``zstd``
re-packs the SAME objects under the SAME ``semantic_hash`` / ``uncompressed_sha256``;
only ``storage_sha256`` / ``storage_blob_hash`` move (they fold ``codec_id`` in).

v0 codecs:

* ``identity`` — raw canonical JSONL, no transform. Always available. Default.
* ``zstd``     — whole-file Zstandard frame, no dictionary (``dict_id = ""``).
                 Available iff the ``zstandard`` library is importable.

The file-name suffix per codec (``""`` for identity, ``".zst"`` for zstd) lets a
pack carry both codecs' layers side by side and stay self-describing: the
manifest's per-layer ``codec`` + ``path`` say exactly how to read each layer
back, so ``identity`` and ``zstd`` packs are interchangeable and verifiable.
"""

from __future__ import annotations

IDENTITY_CODEC = "identity"
ZSTD_CODEC = "zstd"

# Compression level for the whole-file zstd frame. 19 is a deliberate
# size-over-speed choice: packs are write-once / verify-many archival artifacts,
# so we pay the encoder cost once to minimise the on-disk / on-wire footprint.
_ZSTD_LEVEL = 19


def zstd_available() -> bool:
    """True iff the optional ``zstandard`` library can be imported."""
    try:
        import zstandard  # noqa: F401
    except Exception:  # pragma: no cover - import-environment dependent
        return False
    return True


def available_codecs() -> tuple[str, ...]:
    """The codec ids usable in this environment (identity always; zstd if present)."""
    if zstd_available():
        return (IDENTITY_CODEC, ZSTD_CODEC)
    return (IDENTITY_CODEC,)


def storage_suffix(codec_id: str) -> str:
    """The on-disk file-name suffix a codec appends to the canonical path.

    ``identity`` writes the raw ``*.jsonl``; ``zstd`` writes ``*.jsonl.zst``.
    """
    if codec_id == IDENTITY_CODEC:
        return ""
    if codec_id == ZSTD_CODEC:
        return ".zst"
    raise ValueError(f"unknown storage codec {codec_id!r}")


def encode(codec_id: str, canonical_bytes: bytes) -> bytes:
    """Transform canonical (uncompressed) bytes into their on-disk storage bytes.

    Pure and deterministic per ``codec_id``. For ``identity`` this is the
    identity function; for ``zstd`` a whole-file frame (no dictionary).
    """
    if not isinstance(canonical_bytes, (bytes, bytearray)):
        raise TypeError(f"encode needs bytes, got {type(canonical_bytes).__name__}")
    raw = bytes(canonical_bytes)
    if codec_id == IDENTITY_CODEC:
        return raw
    if codec_id == ZSTD_CODEC:
        import zstandard

        return zstandard.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)
    raise ValueError(f"unknown storage codec {codec_id!r}")


def decode(codec_id: str, storage_bytes: bytes) -> bytes:
    """Inverse of :func:`encode`: recover the canonical bytes from storage bytes.

    ``decode(codec, encode(codec, x)) == x`` byte for byte for every codec.
    """
    if not isinstance(storage_bytes, (bytes, bytearray)):
        raise TypeError(f"decode needs bytes, got {type(storage_bytes).__name__}")
    blob = bytes(storage_bytes)
    if codec_id == IDENTITY_CODEC:
        return blob
    if codec_id == ZSTD_CODEC:
        import zstandard

        # max_output_size=0 → grow as needed; whole frame is present in memory.
        return zstandard.ZstdDecompressor().decompress(blob)
    raise ValueError(f"unknown storage codec {codec_id!r}")
