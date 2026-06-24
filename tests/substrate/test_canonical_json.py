"""Pins for ``lawvm.canonical_json.v1`` (OBJECT_MODEL_AND_PACK_V0.md §1).

Determinism, ensure_ascii escaping, NFC-at-construction, the
``{object_hash, object}`` wrapper round-trip + tamper detection, the
forbidden-type guard (§1.4), and **byte-for-byte equality with the verified
cert-bundle ``canonical_json_bytes``** — the trust-spine drift guard.
"""

from __future__ import annotations

import hashlib

import pytest

from lawvm.substrate.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
    nfc,
    semantic_hash,
    unwrap_and_verify,
    wrap_row,
)


def test_canonical_bytes_deterministic_across_key_orders() -> None:
    a = {"b": 1, "a": 2, "c": [3, 2, 1]}
    b = {"c": [3, 2, 1], "a": 2, "b": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b)
    # Stable across repeated calls.
    assert canonical_json_bytes(a) == canonical_json_bytes(a)
    assert canonical_json_bytes(a) == b'{"a":2,"b":1,"c":[3,2,1]}'


def test_ensure_ascii_escapes_non_ascii() -> None:
    # "ä" and "§" must be \uXXXX-escaped so a non-Python checker reproduces it.
    raw = canonical_json_bytes({"t": "ä§"})
    assert b"\\u00e4" in raw
    assert b"\\u00a7" in raw
    # No raw non-ASCII byte survives.
    assert all(byte < 0x80 for byte in raw)


def test_semantic_hash_shape_and_value() -> None:
    obj = {"x": 1}
    expected = "sha256:" + hashlib.sha256(b'{"x":1}').hexdigest()
    assert semantic_hash(obj) == expected


def test_nfc_decomposed_and_composed_share_hash() -> None:
    # "ä" composed (U+00E4) vs decomposed ("a" + U+0308 combining diaeresis).
    composed = "\u00e4"  # ä precomposed (U+00E4)
    decomposed = "a\u0308"  # a + combining diaeresis (U+0308)
    assert composed != decomposed
    # NFC at construction collapses them; equal canonical bytes + equal hash.
    assert canonical_json_bytes({"t": nfc(composed)}) == canonical_json_bytes(
        {"t": nfc(decomposed)}
    )
    assert semantic_hash({"t": nfc(composed)}) == semantic_hash({"t": nfc(decomposed)})


def test_nfc_preserves_section_sign_and_nbsp() -> None:
    # §1.2: NFC must NOT collapse NBSP or the section sign.
    assert nfc("a§b c") == "a§b c"


def test_wrap_row_roundtrip() -> None:
    obj = {"schema": "lawvm.work.v1", "work_id": "fi:act:301/2004"}
    row = wrap_row(obj)
    assert set(row) == {"object_hash", "object"}
    assert row["object_hash"] == semantic_hash(obj)
    assert unwrap_and_verify(row) == obj


def test_wrap_row_does_not_embed_hash_in_object() -> None:
    obj = {"k": "v"}
    row = wrap_row(obj)
    assert "object_hash" not in row["object"]  # ty: ignore[unsupported-operator]


def test_unwrap_detects_tamper() -> None:
    row = wrap_row({"k": "v"})
    tampered = {"object_hash": row["object_hash"], "object": {"k": "TAMPERED"}}
    with pytest.raises(CanonicalJsonError, match="mismatch"):
        unwrap_and_verify(tampered)


def test_unwrap_rejects_malformed_row() -> None:
    with pytest.raises(CanonicalJsonError, match="wrapper"):
        unwrap_and_verify({"object": {"k": "v"}})


def test_forbidden_float_rejected() -> None:
    with pytest.raises(CanonicalJsonError, match="float is forbidden"):
        canonical_json_bytes({"x": 1.5})


def test_forbidden_set_rejected() -> None:
    with pytest.raises(CanonicalJsonError, match="not canonical-JSON"):
        canonical_json_bytes({"x": {1, 2}})  # type: ignore[dict-item]


def test_bool_and_int_allowed() -> None:
    # bool is a subclass of int; both must pass and round-trip.
    raw = canonical_json_bytes({"flag": True, "n": 7, "z": None})
    assert raw == b'{"flag":true,"n":7,"z":null}'


def test_equality_with_cert_bundle_canonical_json_bytes() -> None:
    """Trust-spine drift guard: substrate bytes == cert-bundle bytes (§1.1)."""
    from lawvm.tools.certificate_bundle import canonical_json_bytes as cert_cjson

    cases = [
        {"a": 1, "b": "ä§", "c": [True, None, 2]},
        {"nested": {"z": "ä already composed?", "y": [1, 2, 3]}},
        ["x", "y", {"k": "v"}],
        "bare string with §",
        42,
        True,
        None,
    ]
    for case in cases:
        assert canonical_json_bytes(case) == cert_cjson(case), case
