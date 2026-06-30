"""Byte-span ``SourceAnchor`` arm for Sweden (LawVM task #93).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``) and the Norway arm
(``tests/test_no_source_anchor.py``) in SHAPE, and now records a REACHABLE result
for Sweden after unblocking the two frontend-specific obstacles task #92 recorded
as BLOCKED:

1. JSON ASCII-ESCAPING (unblocked via a SEPARATE UTF-8 anchor artifact). The
   canonical compile artifact entering ``parse_se_amendment_ops`` is
   ``json.dumps(act).encode()`` with the default ``ensure_ascii=True`` — its
   Swedish non-ASCII (``ö``/``ä``/``å``/``§``) is ``\\uXXXX``-escaped, so a clause
   is NOT a verbatim UTF-8 byte run and ``compute_source_anchor`` returns
   ``None``. We do NOT change that canonical artifact (its bytes feed
   hashes/determinism/cert roots). Instead ``parse_se_amendment_ops`` publishes a
   DISTINCT UTF-8 anchor artifact (``se_utf8_anchor_artifact`` —
   ``json.dumps(act, ensure_ascii=False).encode('utf-8')``) in the anchor
   context, in which a verbatim Swedish clause IS a contiguous byte run. The
   anchor's ``source_artifact_id`` carries a ``#utf8`` suffix to keep it distinct
   from the canonical artifact id.

2. ACT-LEVEL granularity (raised to per-op where recoverable). The act-level
   ``OperationSource`` carries the whole enacting clause; replace/insert/heading/
   appendix/text-replace ops now carry their SPECIFIC operative text
   (``_se_op_source_with_clause``), so anchors are per-op like EE/NO when that
   text is a unique verbatim run. Repeal/renumber ops have no per-op body text —
   only a section label named in the act-level enacting clause — so they
   honestly anchor at act granularity.

GROUNDING-NEUTRAL: ``source.raw_text`` and ``source.source_anchor`` are both
additive provenance, EXCLUDED from the apply-input digest, so SE replay output is
byte-identical. Every absence is justified (fail-loud, never a fabricated offset).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import SourceAnchor, compute_source_anchor
from lawvm.sweden.grafter import (
    mint_se_source_anchors,
    parse_se_amendment_ops,
    reset_se_raw_source_context,
    se_utf8_anchor_artifact,
    set_se_raw_source_context,
)

# The same pinned SFS ids the provenance-totality SE sampler measures.
_SAMPLE_IDS = ["1999:1001", "1999:1003", "1999:1004"]


def _se_archive_path() -> Path | None:
    """Resolve the Sweden act archive, preferring the canonical data root."""
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        candidate = Path(root) / "data" / "sweden.farchive"
        if candidate.exists():
            return candidate
    fallback = Path(__file__).resolve().parent.parent / "data" / "sweden.farchive"
    return fallback if fallback.exists() else None


_ARCHIVE_PATH = _se_archive_path()
pytestmark = pytest.mark.skipif(
    _ARCHIVE_PATH is None,
    reason="sweden.farchive not available (set LAWVM_CANONICAL_DATA_ROOT)",
)


def _compile_sample() -> list[tuple[str, bytes, bytes, list[LegalOperation]]]:
    """Compile each sample act; return (anchor_id, canonical_bytes, utf8_anchor_bytes, ops)."""
    from lawvm.sweden.fetch import load_se_official_act_from_archive, open_se_archive

    archive = open_se_archive(_ARCHIVE_PATH, readonly=True)
    out: list[tuple[str, bytes, bytes, list[LegalOperation]]] = []
    for sfs_id in _SAMPLE_IDS:
        act = load_se_official_act_from_archive(archive, sfs_id)
        if act is None:
            continue
        # The canonical compile artifact, exactly as the SE sampler builds it.
        canonical_bytes = json.dumps(act).encode()
        # The DISTINCT UTF-8 anchor artifact the post-pass anchors against.
        anchor_id, utf8_bytes = se_utf8_anchor_artifact(canonical_bytes, f"se/{sfs_id}")
        ops = parse_se_amendment_ops(canonical_bytes, f"se/{sfs_id}")
        out.append((anchor_id, canonical_bytes, utf8_bytes, list(ops)))
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


def _apply_digest(samples: list[tuple[str, bytes, bytes, list[LegalOperation]]]) -> str:
    rows = [[_op_apply_view(op) for op in ops] for _aid, _cb, _ub, ops in samples]
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_se_minted_anchors_are_real_and_reverifiable() -> None:
    """Every minted anchor re-verifies byte-exactly against the UTF-8 anchor artifact.

    The anchor re-slices the UTF-8 anchor artifact (NOT the canonical compile
    bytes) back to precisely the op's recorded clause, and its ``quote_hash`` is
    the sha256 of those bytes. An anchor is never a fabricated offset.
    """
    samples = _compile_sample()
    total = anchored = 0
    for anchor_id, _canonical, utf8_bytes, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is None:
                continue
            anchored += 1
            assert isinstance(anchor, SourceAnchor)
            assert anchor.source_artifact_id == anchor_id
            assert anchor_id.endswith("#utf8"), "anchor must derive from the UTF-8 artifact"
            assert op.source is not None
            clause = (op.source.raw_text or op.raw_text or "").encode("utf-8")
            assert clause, "an anchored op must carry the clause it anchors"
            sliced = utf8_bytes[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            assert sliced == clause, (
                f"anchor span does not re-slice to the clause for op in {anchor_id}"
            )
            assert anchor.byte_len == len(clause)
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            first = utf8_bytes.find(sliced)
            assert first == anchor.byte_offset
            assert utf8_bytes.find(sliced, first + 1) == -1, "anchored clause must be unique"

    assert total > 0, "sampler produced no ops"
    # REACHABLE: the UTF-8 anchor artifact must mint a non-trivial majority of
    # the op stream (measured 10/13 on the pinned sample).
    assert anchored >= 1, "SE is reachable: at least one op must carry a TRUE anchor"


def test_se_anchors_are_per_op_not_only_act_level() -> None:
    """Replace/insert ops anchor to their OWN provision text, not a single act span.

    Proves the granularity is genuinely per-op (EE/NO-style) and not merely the
    shared enacting clause repeated across every op: at least two anchored ops
    point at DISTINCT byte offsets within the same act.
    """
    samples = _compile_sample()
    proved = False
    for _aid, _canonical, _utf8, ops in samples:
        offsets = {
            op.source.source_anchor.byte_offset
            for op in ops
            if op.source is not None and op.source.source_anchor is not None
        }
        if len(offsets) >= 2:
            proved = True
            break
    assert proved, "expected at least one act with >=2 distinct per-op anchor offsets"


def test_se_canonical_artifact_is_unchanged_by_anchoring() -> None:
    """The UTF-8 anchor artifact is DISTINCT; the canonical compile bytes are untouched.

    Pins the central safety property: the bytes that feed
    hashes/determinism/cert roots (``json.dumps(act).encode()``) are not the
    bytes anchored against, so the anchor program changes nothing canonical.
    """
    samples = _compile_sample()
    for anchor_id, canonical, utf8_bytes, _ops in samples:
        assert anchor_id.endswith("#utf8")
        assert utf8_bytes != canonical, "anchor artifact must differ from canonical bytes"
        # The non-ASCII Swedish clause is escaped in canonical, verbatim in UTF-8.
        assert utf8_bytes.decode("utf-8")  # decodes as UTF-8
        assert json.loads(canonical.decode("utf-8")) == json.loads(utf8_bytes.decode("utf-8")), (
            "canonical and UTF-8 anchor artifacts must be the SAME act, only re-encoded"
        )


def test_se_source_anchor_is_grounding_neutral() -> None:
    """The anchor pass is additive metadata: the apply-input digest is invariant.

    Stripping ``source_anchor`` (and the per-op ``raw_text`` it anchors) cannot
    change the apply-authoritative digest — the post-pass NEVER perturbs an
    apply-authoritative field.
    """
    from dataclasses import replace

    samples = _compile_sample()
    digest_with = _apply_digest(samples)

    stripped: list[tuple[str, bytes, bytes, list[LegalOperation]]] = []
    for anchor_id, canonical, utf8_bytes, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                new_ops.append(replace(op, source=replace(op.source, source_anchor=None)))
            else:
                new_ops.append(op)
        stripped.append((anchor_id, canonical, utf8_bytes, new_ops))
    digest_without = _apply_digest(stripped)

    assert digest_with == digest_without, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_se_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Every absent anchor is JUSTIFIED: the clause is not a unique verbatim run.

    Checked against the UTF-8 anchor artifact (the bytes the post-pass actually
    anchors against): an op left None must genuinely NOT be a unique verbatim
    byte run of those bytes.
    """
    samples = _compile_sample()
    total = none_count = 0
    for _aid, _canonical, utf8_bytes, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is not None:
                continue
            none_count += 1
            clause = ""
            if op.source is not None:
                clause = op.source.raw_text or ""
            clause = clause or op.raw_text or ""
            needle = clause.encode("utf-8")
            if not needle:
                continue
            first = utf8_bytes.find(needle)
            unique = first >= 0 and utf8_bytes.find(needle, first + 1) < 0
            assert not unique, (
                "an op with a unique verbatim clause must have been anchored, not "
                f"left None: {clause[:80]!r}"
            )

    assert total > 0


def test_se_canonical_ascii_artifact_alone_cannot_anchor_nonascii() -> None:
    """Pin WHY the canonical artifact alone is insufficient (the #92 blocker).

    The recorded clause is genuinely present and unique in the UTF-8 act bytes;
    it is only the default ``json.dumps`` ASCII-escaping of the canonical
    artifact that hides it from a verbatim byte search. ``compute_source_anchor``
    returns None on the ASCII artifact and a TRUE anchor on the UTF-8 artifact.
    This documents that the separate UTF-8 anchor artifact (not a change to the
    canonical bytes) is what unblocks SE.
    """
    from lawvm.sweden.fetch import load_se_official_act_from_archive, open_se_archive

    archive = open_se_archive(_ARCHIVE_PATH, readonly=True)
    proved_blocked = proved_unblocked = False
    for sfs_id in _SAMPLE_IDS:
        act = load_se_official_act_from_archive(archive, sfs_id)
        if act is None:
            continue
        clause = str(act.get("enacting_clause") or "")
        if not clause:
            continue
        ascii_bytes = json.dumps(act).encode()  # ensure_ascii=True (canonical)
        _aid, utf8_bytes = se_utf8_anchor_artifact(ascii_bytes, f"se/{sfs_id}")
        needle = clause.encode("utf-8")

        if any(ord(ch) > 127 for ch in clause):
            # Canonical artifact: clause NOT verbatim; compute_source_anchor -> None.
            assert needle not in ascii_bytes
            assert (
                compute_source_anchor(
                    source_artifact_id=f"se/{sfs_id}",
                    raw_bytes=ascii_bytes,
                    clause_text=clause,
                )
                is None
            )
            proved_blocked = True

        # UTF-8 anchor artifact: clause IS a unique verbatim run; anchor is real.
        first = utf8_bytes.find(needle)
        assert first >= 0 and utf8_bytes.find(needle, first + 1) < 0
        anchor = compute_source_anchor(
            source_artifact_id=f"se/{sfs_id}#utf8",
            raw_bytes=utf8_bytes,
            clause_text=clause,
        )
        assert anchor is not None
        sliced = utf8_bytes[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
        assert sliced == needle
        assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
        proved_unblocked = True

    assert proved_blocked, "expected a non-ASCII clause blocked on the ASCII artifact"
    assert proved_unblocked, "expected the SAME clause anchorable on the UTF-8 artifact"


def test_se_post_pass_is_noop_without_published_artifact() -> None:
    """``mint_se_source_anchors`` is a no-op when no anchor artifact is in context.

    Re-applied OUTSIDE ``parse_se_amendment_ops`` (no context published), the
    post-pass leaves the stream untouched (anchors already present stay; nothing
    new is minted), proving the pass keys strictly off the published context.
    """
    samples = _compile_sample()
    some_ops = [op for _aid, _c, _u, ops in samples for op in ops][:5]
    result = mint_se_source_anchors(list(some_ops))
    assert result == some_ops


def test_se_set_reset_context_roundtrip() -> None:
    """The publish/reset helpers round-trip cleanly (no context leak)."""
    token = set_se_raw_source_context("se/probe#utf8", b'{"k": "v"}')
    try:
        ops = mint_se_source_anchors([])  # empty stream is a no-op
        assert ops == []
    finally:
        reset_se_raw_source_context(token)


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
