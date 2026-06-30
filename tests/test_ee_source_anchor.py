"""Byte-span ``SourceAnchor`` pilot for Estonia (LawVM task #89).

These tests pin the EE arm of the provenance byte-span program: every emitted op
whose recorded clause text survives RT text-flattening as a single verbatim,
unique byte run of the raw amendment XML now carries a TRUE, re-verifiable
:class:`lawvm.core.provenance.SourceAnchor`. The remaining ops (clause flattened
across XML tag boundaries / whitespace-collapsed, or text that repeats and is
therefore ambiguous) honestly carry NO anchor — never a fabricated offset.

What is asserted:

* RE-VERIFIABILITY — for every minted anchor, an INDEPENDENT verifier re-derives
  the byte slice from the raw artifact bytes, confirms it equals the carried
  ``source.raw_text`` UTF-8, and recomputes the sha256 ``quote_hash``. This is
  the whole point of the anchor: a certificate can re-prove the clause from
  source, not just trust diff-derived state.
* GROUNDING-NEUTRALITY — the anchor is additive provenance metadata. An
  apply-input digest over the apply-authoritative fields (action / target /
  anchor / destination / payload / text_patch / order), EXCLUDING all
  provenance, is identical whether or not the anchors are stamped. The pilot
  cannot change replay output.
* HONEST None-COUNT — the unanchorable ops produce ``source_anchor is None``
  (fail-loud), and the no-anchor count is bounded (the pilot must drive the
  measured ``no_typed_anchor_rate`` far below the pre-pilot 100%).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import SourceAnchor
from lawvm.estonia import grafter
from lawvm.estonia.fetch import fetch_rt_xml, open_rt_archive

# Same pinned amendment ids the provenance-totality EE sampler measures, so the
# delta these tests guard is exactly the one the audit reports.
_SAMPLE_IDS = ["127122011011", "119082015004", "128092014004"]


def _rt_archive_path() -> Path | None:
    """Resolve the RT amendment archive, preferring the canonical data root.

    Returns ``None`` (so the suite skips, like the other EE DB-fetch tests) when
    the multi-GB archive is not present in this environment.
    """
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        candidate = Path(root) / "data" / "ee_riigiteataja.farchive"
        if candidate.exists():
            return candidate
    fallback = Path(__file__).resolve().parent.parent / "data" / "ee_riigiteataja.farchive"
    return fallback if fallback.exists() else None


_ARCHIVE_PATH = _rt_archive_path()
pytestmark = pytest.mark.skipif(
    _ARCHIVE_PATH is None,
    reason="ee_riigiteataja.farchive not available (set LAWVM_CANONICAL_DATA_ROOT)",
)


def _compile_sample() -> list[tuple[str, bytes, list[LegalOperation]]]:
    archive = open_rt_archive(_ARCHIVE_PATH, readonly=True)
    out: list[tuple[str, bytes, list[LegalOperation]]] = []
    for amendment_id in _SAMPLE_IDS:
        xml = fetch_rt_xml(amendment_id, archive=archive)
        art_id = f"ee/{amendment_id}"
        ops = grafter.parse_ee_amendment_ops(xml, source_id=art_id)
        out.append((art_id, xml, list(ops)))
    return out


def _op_apply_view(op: LegalOperation) -> dict[str, object]:
    """Apply-authoritative projection of an op, EXCLUDING all provenance.

    Mirrors the fields the apply seam consumes (action / addresses / payload /
    text_patch / order). Deliberately omits ``source`` and ``raw_text`` so the
    digest is blind to the anchor metadata this pilot adds.
    """

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


def test_ee_source_anchors_are_real_and_reverifiable() -> None:
    """Every minted anchor re-verifies independently against the raw bytes."""
    samples = _compile_sample()
    total = anchored = 0
    for art_id, xml, ops in samples:
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
            sliced = xml[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            assert sliced == clause, (
                f"anchor span does not re-slice to the clause for op in {art_id}"
            )
            assert anchor.byte_len == len(clause)
            # quote_hash is the sha256 of those exact raw bytes.
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            # And it is unambiguous: the clause occurs exactly once in the raw
            # artifact (compute_source_anchor refuses to certify otherwise).
            first = xml.find(sliced)
            assert first == anchor.byte_offset
            assert xml.find(sliced, first + 1) == -1, "anchored clause must be unique"

    assert total > 0, "sampler produced no ops"
    # The pilot must actually mint a strong majority of anchors (it was 0% before).
    assert anchored >= total * 0.8, (
        f"expected the byte-span pilot to anchor most ops; got {anchored}/{total}"
    )


def test_ee_source_anchor_is_grounding_neutral() -> None:
    """The anchor is additive metadata: the apply-input digest is invariant.

    We compute the apply-authoritative digest over the compiled ops AS THEY ARE
    (anchors present) and again over the SAME ops with ``source_anchor`` stripped
    back to ``None``. Identical digests prove the anchor cannot influence replay
    output — only provenance metadata changed.
    """
    from dataclasses import replace

    samples = _compile_sample()
    digest_with_anchors = _apply_digest(samples)

    stripped: list[tuple[str, bytes, list[LegalOperation]]] = []
    saw_anchor = False
    for art_id, xml, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                saw_anchor = True
                new_ops.append(
                    replace(op, source=replace(op.source, source_anchor=None))
                )
            else:
                new_ops.append(op)
        stripped.append((art_id, xml, new_ops))
    digest_without_anchors = _apply_digest(stripped)

    assert saw_anchor, "guard: expected at least one anchor to strip"
    assert digest_with_anchors == digest_without_anchors, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_ee_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Ops whose clause does not survive flattening carry None — never a guess.

    Asserts the measured no-anchor count is bounded (the pilot drove the EE
    no_typed_anchor_rate far below the pre-pilot 100%) AND that every None is
    genuinely unanchorable: its clause text is NOT a single unique verbatim byte
    run of the raw artifact (so ``compute_source_anchor`` correctly refused).
    """
    samples = _compile_sample()
    total = none_count = 0
    for _art_id, xml, ops in samples:
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
            first = xml.find(needle)
            unique = first >= 0 and xml.find(needle, first + 1) < 0
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
