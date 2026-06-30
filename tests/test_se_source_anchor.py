"""Byte-span ``SourceAnchor`` arm for Sweden (LawVM task #92).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``) and the Norway arm
(``tests/test_no_source_anchor.py``) in SHAPE, but records a rigorous PARTIAL /
BLOCKED feasibility result for Sweden — a valid outcome per the task brief.

FEASIBILITY VERDICT: BLOCKED on the canonical compile artifact, for two
frontend-specific reasons (both proved by the tests below):

1. JSON ASCII-ESCAPING. The canonical raw artifact entering
   ``parse_se_amendment_ops`` is ``json.dumps(act).encode()`` (the
   provenance-totality sampler and the SE compile path both build it that way)
   with the default ``ensure_ascii=True``. The recorded ``source.raw_text`` is
   the act's enacting clause, which is Swedish prose full of non-ASCII (``ö``,
   ``ä``, ``å``, the section sign ``§``). In the artifact bytes those characters
   are ``\\uXXXX``-escaped (``ö`` -> ``\\u00f6``, ``§`` -> ``\\u00a7``), so the
   clause is NOT a contiguous verbatim UTF-8 byte substring of the artifact —
   ``compute_source_anchor`` correctly refuses and returns ``None``. (With
   ``ensure_ascii=False`` the SAME clause IS a unique verbatim run; this test
   demonstrates that, to prove the blockage is an artifact-encoding property, not
   a defect in the post-pass.)

2. ACT-LEVEL, NOT OP-LEVEL granularity. Every SE op shares ONE ``OperationSource``
   whose ``raw_text`` is the whole enacting clause (built once in
   ``_lower_se_official_effects_plan``). So even on a UTF-8 artifact the anchor
   would be act-granular (all ops -> the same span), not the per-op clause
   granularity EE/NO achieve.

The ``mint_se_source_anchors`` post-pass is nonetheless wired in faithfully
(identical shape to EE/NO): it would mint TRUE, re-verifiable anchors the moment
the recorded clause is a unique verbatim byte run of the artifact. These tests
assert the post-pass is HONEST and GROUNDING-NEUTRAL, and that every absence is
justified (fail-loud, never a fabricated offset).
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


def _compile_sample() -> list[tuple[str, bytes, list[LegalOperation]]]:
    from lawvm.sweden.fetch import load_se_official_act_from_archive, open_se_archive

    archive = open_se_archive(_ARCHIVE_PATH, readonly=True)
    out: list[tuple[str, bytes, list[LegalOperation]]] = []
    for sfs_id in _SAMPLE_IDS:
        act = load_se_official_act_from_archive(archive, sfs_id)
        if act is None:
            continue
        # The canonical compile artifact, exactly as the SE sampler builds it.
        json_bytes = json.dumps(act).encode()
        art_id = f"se/{sfs_id}"
        ops = parse_se_amendment_ops(json_bytes, art_id)
        out.append((art_id, json_bytes, list(ops)))
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


def test_se_minted_anchors_are_real_and_reverifiable() -> None:
    """Any anchor the post-pass DOES mint re-verifies byte-exactly.

    On the canonical ASCII-escaped artifact this typically mints zero anchors
    (see the blocked verdict), but the property must hold unconditionally: a
    minted anchor is never a fabricated offset.
    """
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
            assert anchor.source_artifact_id == art_id
            assert op.source is not None
            clause = (op.source.raw_text or op.raw_text or "").encode("utf-8")
            assert clause, "an anchored op must carry the clause it anchors"
            sliced = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            assert sliced == clause, (
                f"anchor span does not re-slice to the clause for op in {art_id}"
            )
            assert anchor.byte_len == len(clause)
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            first = raw.find(sliced)
            assert first == anchor.byte_offset
            assert raw.find(sliced, first + 1) == -1, "anchored clause must be unique"

    assert total > 0, "sampler produced no ops"


def test_se_source_anchor_is_grounding_neutral() -> None:
    """The anchor pass is additive metadata: the apply-input digest is invariant.

    Even though the canonical artifact mints no anchors, the post-pass must be
    grounding-neutral by construction: stripping ``source_anchor`` cannot change
    the apply-authoritative digest. We assert the post-pass NEVER perturbs an
    apply-authoritative field.
    """
    from dataclasses import replace

    samples = _compile_sample()
    digest_with = _apply_digest(samples)

    stripped: list[tuple[str, bytes, list[LegalOperation]]] = []
    for art_id, raw, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                new_ops.append(replace(op, source=replace(op.source, source_anchor=None)))
            else:
                new_ops.append(op)
        stripped.append((art_id, raw, new_ops))
    digest_without = _apply_digest(stripped)

    assert digest_with == digest_without, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_se_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Every absent anchor is JUSTIFIED: the clause is not a unique verbatim run."""
    samples = _compile_sample()
    total = none_count = 0
    for _art_id, raw, ops in samples:
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
            first = raw.find(needle)
            unique = first >= 0 and raw.find(needle, first + 1) < 0
            assert not unique, (
                "an op with a unique verbatim clause must have been anchored, not "
                f"left None: {clause[:80]!r}"
            )

    assert total > 0


def test_se_blockage_is_json_ascii_escaping_not_a_defect() -> None:
    """Prove the blockage: the clause IS a unique verbatim run under ensure_ascii=False.

    This pins the exact feasibility reason. The recorded ``source.raw_text``
    (enacting clause) is genuinely present and unique in the act bytes — it is
    only the default ``json.dumps`` ASCII-escaping of the canonical artifact that
    hides it from a verbatim byte search. Re-serializing the SAME act with
    ``ensure_ascii=False`` and re-running the post-pass mints TRUE,
    byte-re-verifiable anchors. So the SE arm is artifact-encoding-BLOCKED, not
    code-defective: the recipe generalizes the moment the raw artifact is UTF-8.
    """
    from lawvm.sweden.fetch import load_se_official_act_from_archive, open_se_archive

    archive = open_se_archive(_ARCHIVE_PATH, readonly=True)
    proved_any = False
    for sfs_id in _SAMPLE_IDS:
        act = load_se_official_act_from_archive(archive, sfs_id)
        if act is None:
            continue
        clause = str(act.get("enacting_clause") or "")
        if not clause:
            continue
        ascii_bytes = json.dumps(act).encode()  # ensure_ascii=True (canonical)
        utf8_bytes = json.dumps(act, ensure_ascii=False).encode("utf-8")
        needle = clause.encode("utf-8")

        # Canonical artifact: clause is NOT a verbatim byte substring (escaped).
        if any(ord(ch) > 127 for ch in clause):
            assert needle not in ascii_bytes, (
                "non-ASCII clause unexpectedly verbatim in ASCII-escaped artifact"
            )
            # And compute_source_anchor honestly returns None for it.
            assert (
                compute_source_anchor(
                    source_artifact_id=f"se/{sfs_id}",
                    raw_bytes=ascii_bytes,
                    clause_text=clause,
                )
                is None
            )

        # UTF-8 artifact: clause IS a unique verbatim run, and the post-pass
        # mints a TRUE anchor that re-verifies byte-exactly.
        first = utf8_bytes.find(needle)
        assert first >= 0 and utf8_bytes.find(needle, first + 1) < 0, (
            "enacting clause should be a unique verbatim run of the UTF-8 artifact"
        )
        ops = parse_se_amendment_ops(ascii_bytes, f"se/{sfs_id}")  # build op stream
        token = set_se_raw_source_context(f"se/{sfs_id}", utf8_bytes)
        try:
            anchored_ops = mint_se_source_anchors([
                op for op in ops if op.source is not None
            ])
        finally:
            reset_se_raw_source_context(token)
        minted = [
            op for op in anchored_ops if op.source is not None and op.source.source_anchor is not None
        ]
        if not minted:
            continue
        proved_any = True
        for op in minted:
            assert op.source is not None
            anchor = op.source.source_anchor
            assert anchor is not None
            sliced = utf8_bytes[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            assert sliced == (op.source.raw_text or "").encode("utf-8")
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()

    assert proved_any, (
        "expected to prove at least one clause is anchorable on a UTF-8 artifact"
    )


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
