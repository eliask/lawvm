"""Byte-span ``SourceAnchor`` arm for Norway (LawVM task #92).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``). Every emitted
Norway op whose recorded clause text survives RT text-flattening
(``_normalize_space(" ".join(el.itertext()))``) as a single verbatim, unique
byte run of the raw Lovdata HTML now carries a TRUE, re-verifiable
:class:`lawvm.core.provenance.SourceAnchor`. The remaining ops (clause flattened
across HTML tag boundaries / whitespace-collapsed, or text that repeats and is
therefore ambiguous) honestly carry NO anchor — never a fabricated offset.

What is asserted (the three EE shapes):

* RE-VERIFIABILITY — for every minted anchor an INDEPENDENT verifier re-derives
  the byte slice from the raw artifact bytes, confirms it equals the carried
  clause text UTF-8, and recomputes the sha256 ``quote_hash``.
* GROUNDING-NEUTRALITY — the anchor is additive provenance metadata. An
  apply-input digest over the apply-authoritative fields (action / target /
  anchor / destination / payload / text_patch / order), EXCLUDING all
  provenance, is identical whether or not the anchors are stamped.
* HONEST None-COUNT — the unanchorable ops produce ``source_anchor is None``
  (fail-loud), and the no-anchor count is bounded (the pilot drives the measured
  ``no_typed_anchor_rate`` far below the pre-pilot 100%).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import SourceAnchor
from lawvm.norway.grafter import parse_no_amendment_ops
from lawvm.norway.sources import load_no_amendment_bytes, resolve_no_source_path

# The same pinned amendment ids the provenance-totality NO sampler measures, so
# the delta these tests guard is exactly the one the audit reports.
_SAMPLE_IDS = ["no/lovtid/2001-01-19-6", "no/lovtid/2001-03-02-7", "no/lovtid/2001-04-06-12"]


def _no_source_path() -> Path | None:
    """Resolve the Norway amendment archive, preferring the canonical data root.

    Returns ``None`` (so the suite skips, like the other NO archive tests) when
    the archive is not present in this environment.
    """
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        candidate = Path(root) / "data" / "norway.farchive"
        if candidate.exists():
            return resolve_no_source_path(candidate)
    fallback = Path(__file__).resolve().parent.parent / "data" / "norway.farchive"
    return resolve_no_source_path(fallback) if fallback.exists() else None


_SOURCE_PATH = _no_source_path()
pytestmark = pytest.mark.skipif(
    _SOURCE_PATH is None,
    reason="norway.farchive not available (set LAWVM_CANONICAL_DATA_ROOT)",
)


def _compile_sample() -> list[tuple[str, bytes, list[LegalOperation]]]:
    out: list[tuple[str, bytes, list[LegalOperation]]] = []
    for source_id in _SAMPLE_IDS:
        html_bytes = load_no_amendment_bytes(source_id, _SOURCE_PATH)
        assert html_bytes is not None, f"missing NO bytes for {source_id}"
        ops = parse_no_amendment_ops(html_bytes, source_id)
        out.append((source_id, html_bytes, list(ops)))
    return out


def _op_apply_view(op: LegalOperation) -> dict[str, object]:
    """Apply-authoritative projection of an op, EXCLUDING all provenance."""

    def _addr(a: LegalAddress | None) -> object:
        return None if a is None else list(a.path)

    payload: object = None
    if op.payload is not None:
        payload = {
            "text": op.payload.text,
            "attrs": {str(k): str(v) for k, v in sorted(op.payload.attrs.items())},
        }
    text_patch = None
    if getattr(op, "text_patch", None) is not None:
        text_patch = repr(op.text_patch)
    return {
        "action": op.action.value if hasattr(op.action, "value") else str(op.action),
        "target": _addr(op.target),
        "anchor": _addr(op.anchor),
        "destination": _addr(op.destination),
        "payload": payload,
        "text_patch": text_patch,
        "sequence": op.sequence,
    }


def _apply_digest(samples: list[tuple[str, bytes, list[LegalOperation]]]) -> str:
    rows = [[_op_apply_view(op) for op in ops] for _aid, _xml, ops in samples]
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_no_source_anchors_are_real_and_reverifiable() -> None:
    """Every minted anchor re-verifies independently against the raw bytes."""
    samples = _compile_sample()
    total = anchored = 0
    for art_id, raw, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is None:
                continue
            anchored += 1
            assert isinstance(anchor, SourceAnchor)
            # Anchor points back at THIS amending artifact.
            assert anchor.source_artifact_id == art_id
            # Independent re-derivation: slice the raw bytes at the anchor span
            # and confirm it is exactly the carried clause text.
            assert op.source is not None
            clause = (op.source.raw_text or op.raw_text or "").encode("utf-8")
            assert clause, "an anchored op must carry the clause it anchors"
            sliced = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            assert sliced == clause, (
                f"anchor span does not re-slice to the clause for op in {art_id}"
            )
            assert anchor.byte_len == len(clause)
            # quote_hash is the sha256 of those exact raw bytes.
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            # And it is unambiguous: the clause occurs exactly once.
            first = raw.find(sliced)
            assert first == anchor.byte_offset
            assert raw.find(sliced, first + 1) == -1, "anchored clause must be unique"

    assert total > 0, "sampler produced no ops"
    # The pilot must actually mint a strong majority of anchors (it was 0% before).
    assert anchored >= total * 0.8, (
        f"expected the byte-span pilot to anchor most ops; got {anchored}/{total}"
    )


def test_no_source_anchor_is_grounding_neutral() -> None:
    """The anchor is additive metadata: the apply-input digest is invariant."""
    from dataclasses import replace

    samples = _compile_sample()
    digest_with_anchors = _apply_digest(samples)

    stripped: list[tuple[str, bytes, list[LegalOperation]]] = []
    saw_anchor = False
    for art_id, raw, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                saw_anchor = True
                new_ops.append(replace(op, source=replace(op.source, source_anchor=None)))
            else:
                new_ops.append(op)
        stripped.append((art_id, raw, new_ops))
    digest_without_anchors = _apply_digest(stripped)

    assert saw_anchor, "guard: expected at least one anchor to strip"
    assert digest_with_anchors == digest_without_anchors, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_no_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Ops whose clause does not survive flattening carry None — never a guess."""
    samples = _compile_sample()
    total = none_count = 0
    for _art_id, raw, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is not None:
                continue
            none_count += 1
            # The absence must be JUSTIFIED: the recorded clause is either empty,
            # absent from the raw bytes, or ambiguous (occurs more than once).
            clause = ""
            if op.source is not None:
                clause = op.source.raw_text or ""
            clause = clause or op.raw_text or ""
            needle = clause.encode("utf-8")
            if not needle:
                continue  # no clause text at all → nothing to anchor, honest
            first = raw.find(needle)
            unique = first >= 0 and raw.find(needle, first + 1) < 0
            assert not unique, (
                "an op with a unique verbatim clause must have been anchored, not "
                f"left None: {clause[:80]!r}"
            )

    assert total > 0
    # Honest, bounded: the pre-pilot rate was 100%; the pilot must clear most.
    assert none_count <= total * 0.2, (
        f"too many unanchored ops ({none_count}/{total}); the pilot regressed"
    )


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
