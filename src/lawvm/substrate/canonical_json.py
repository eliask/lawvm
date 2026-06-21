"""``lawvm.canonical_json.v1`` — the substrate identity encoding.

Frozen profile (OBJECT_MODEL_AND_PACK_V0.md §1.1; design §21.1):

    canonical_json_bytes(obj) = json.dumps(
        obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    semantic_hash = "sha256:" + sha256(canonical_json_bytes(obj)).hexdigest()

This is a byte-for-byte re-implementation of the verified-on-disk trust spine
(``lawvm.tools.certificate_bundle.canonical_json_bytes``). ``tests/substrate/
test_canonical_json.py`` pins equality with that source so the two cannot
drift. It is kept local here to keep the substrate package import-light (the
cert-bundle module pulls in the whole replay/export stack).

NFC handling (§1.2): semantic-text string fields are normalized with
``unicodedata.normalize("NFC", s)`` **at object construction** — never at hash
time. The hasher is a pure byte function and does no normalization. Use
:func:`nfc` when building objects whose profile declares ``semantic_text``.

The hash is **never** a member of the object it hashes (§1.3). Every semantic
object is transported as a two-field JSONL row ``{"object_hash": …,
"object": {…}}``; :func:`wrap_row` builds one and :func:`unwrap_and_verify`
recomputes and checks it (L0 integrity).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Mapping, Sequence, Union, cast

# A JSON value admissible in a hashed object body. ``float`` is intentionally
# absent: §1.4 forbids float/NaN/Inf (dates are ISO-8601 strings). ``set`` is
# absent: emit a sorted array. ``dict`` is fine — ``sort_keys=True`` orders it.
JsonScalar = Union[str, int, bool, None]
JsonValue = Union[JsonScalar, Sequence["JsonValue"], Mapping[str, "JsonValue"]]


class CanonicalJsonError(ValueError):
    """A value cannot be encoded under ``lawvm.canonical_json.v1`` (§1.4)."""


def nfc(s: str) -> str:
    """NFC-normalize a semantic-text string (§1.2, normalize-at-construction).

    NFC **only**: NBSP, the section sign ``§`` and every legally-visible glyph
    are preserved. NBSP collapsing / display folding belong to
    ``display_text``/``search_text`` profiles, never to the hashed form.
    """
    return unicodedata.normalize("NFC", s)


def _reject_non_canonical(obj: object, path: str = "<root>") -> None:
    """Fail loudly on any value §1.4 forbids in a hashed object.

    ``bool`` is checked before ``int`` because ``isinstance(True, int)`` is
    True in Python; both are allowed, but the order keeps the float guard
    (``isinstance`` of ``int`` excludes ``bool`` already) unambiguous.
    """
    if obj is None or isinstance(obj, (str, bool)):
        return
    if isinstance(obj, float):
        raise CanonicalJsonError(
            f"float is forbidden in a hashed object at {path} "
            f"(value {obj!r}); §1.4 — dates are ISO strings, no floats"
        )
    if isinstance(obj, int):
        return
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise CanonicalJsonError(
                    f"non-string mapping key {key!r} at {path}; §1.4 requires string keys"
                )
            _reject_non_canonical(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            _reject_non_canonical(value, f"{path}[{index}]")
        return
    # set / frozenset / bytes / datetime / Decimal / arbitrary objects.
    raise CanonicalJsonError(
        f"value of type {type(obj).__name__} is not canonical-JSON at {path}; §1.4"
    )


def canonical_json_bytes(obj: JsonValue) -> bytes:
    """UTF-8 bytes of the ``lawvm.canonical_json.v1`` encoding (§1.1, frozen).

    Byte-for-byte identical to ``certificate_bundle.canonical_json_bytes``.
    The forbidden-type guard runs first so a ``float``/``set``/``bytes`` never
    silently reaches ``json.dumps`` (which would either accept a float or raise
    a generic ``TypeError`` without a substrate-named diagnostic).
    """
    _reject_non_canonical(obj)
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def semantic_hash(obj: JsonValue) -> str:
    """``"sha256:" + sha256(canonical_json_bytes(obj)).hexdigest()`` (§1.1)."""
    return "sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def wrap_row(obj: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Build a ``{"object_hash": …, "object": …}`` JSONL row (§1.3).

    ``object_hash`` is ``semantic_hash`` over the object body alone — i.e.
    ``leaf_hash`` *without* a domain tag (root membership applies the domain
    tag separately, §2). The hash is never a member of ``object``.
    """
    body: dict[str, JsonValue] = {key: value for key, value in obj.items()}
    return {"object_hash": semantic_hash(body), "object": body}


def unwrap_and_verify(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Recompute the row hash and return ``object`` iff it matches (§1.3).

    L0 integrity: the reader recomputes ``semantic_hash(row["object"])`` and
    rejects the row if it differs from ``row["object_hash"]``.
    """
    if "object_hash" not in row or "object" not in row:
        raise CanonicalJsonError("row is not a {object_hash, object} wrapper (§1.3)")
    declared = row["object_hash"]
    body = row["object"]
    if not isinstance(declared, str):
        raise CanonicalJsonError("object_hash must be a string (§1.3)")
    if not isinstance(body, Mapping):
        raise CanonicalJsonError("object must be a JSON object (§1.3)")
    typed_body = cast(Mapping[str, JsonValue], body)
    recomputed = semantic_hash(typed_body)
    if recomputed != declared:
        raise CanonicalJsonError(
            f"object_hash mismatch (tamper/corruption): declared {declared}, "
            f"recomputed {recomputed} (§1.3 L0 integrity)"
        )
    result: dict[str, JsonValue] = {key: value for key, value in typed_body.items()}
    return result
