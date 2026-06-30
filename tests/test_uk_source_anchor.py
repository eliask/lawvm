"""Byte-span ``SourceAnchor`` arm for the UK frontend (LawVM task #92).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``), the Norway arm
(``tests/test_no_source_anchor.py``), and the Sweden arm
(``tests/test_se_source_anchor.py``) in SHAPE, but records a rigorous PARTIAL /
BLOCKED feasibility result for the UK — a valid per-frontend outcome per the task
brief.

FEASIBILITY VERDICT: BLOCKED on the canonical compile artifact, for a
frontend-specific reason distinct from EE/NO (which are REACHABLE) and from SE
(which is encoding-BLOCKED). The cause here is STRUCTURAL:

  STRUCTURED-SOURCE TAG-BOUNDARY RECONSTRUCTION. The recorded ``source.raw_text``
  is ``_text_content(extracted_el)`` (``uk_legislation/xml_helpers.py``) — the
  affecting-act provision element's text, ``itertext``-collected across its child
  nodes and whitespace-collapsed (``" ".join(" ".join(parts).split())``), exactly
  the EE/NO flattening shape. But UK affecting-act XML is STRUCTURED: a clause's
  number lives in a ``<Pnumber>`` element (often with interleaved
  ``<CommentaryRef/>`` children) and its body in a sibling ``<P1para><Text>``. So
  the flattened clause (e.g. ``"10 In this Act, omit paragraph 7(4) of Schedule
  11."``) is reconstructed ACROSS element boundaries and is NEVER a contiguous
  verbatim byte run of the raw XML. ``compute_source_anchor`` correctly refuses
  (returns ``None``).

MEASURED on the canonical sample (3 affected acts, 709 ops):
``no_typed_anchor_rate`` stays 100% — 0/709 ops anchor. The lone op whose clause
happens to be a verbatim byte run of its affecting XML
(``"s the Enterprise Act 2002"``) occurs MORE THAN ONCE (ambiguous), so it is
correctly refused too. Every absence is justified — fail-loud, never a fabricated
offset.

This is the EE/NO "reconstructed across tag boundaries" MINORITY case made
UNIVERSAL by UK's structured affecting-act source. The ``mint_uk_source_anchors``
post-pass is nonetheless wired in faithfully (identical shape to EE/NO/SE): it
mints a TRUE, re-verifiable anchor the moment a per-op clause is a unique verbatim
byte run of its artifact, and these tests prove (a) any anchor it DOES mint
re-verifies byte-exactly, (b) the pass is grounding-neutral, (c) every absence is
justified, and (d) the blockage is the structured-source property, not a defect:
on an artifact where the SAME clause IS a contiguous run, the post-pass mints a
TRUE anchor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import SourceAnchor, compute_source_anchor
from lawvm.uk_legislation.uk_amendment_replay import (
    UKReplayPipeline,
    mint_uk_source_anchors,
    reset_uk_raw_source_context,
    set_uk_raw_source_context,
)

# The same pinned affected-act ids the provenance-totality UK sampler measures
# (lawvm.tools.provenance_totality_report._sample_uk).
_SAMPLE_IDS = ["asc/2021/1", "ukpga/2000/27", "ukpga/1998/11"]


def _uk_archive_path() -> Path | None:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        candidate = Path(root) / "data" / "uk_legislation.farchive"
        if candidate.exists():
            return candidate
    fallback = (
        Path(__file__).resolve().parent.parent / "data" / "uk_legislation.farchive"
    )
    return fallback if fallback.exists() else None


_ARCHIVE_PATH = _uk_archive_path()
pytestmark = pytest.mark.skipif(
    _ARCHIVE_PATH is None,
    reason="uk_legislation.farchive not available (set LAWVM_CANONICAL_DATA_ROOT)",
)


def _data_root() -> Path:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    return Path(root) if root else Path(__file__).resolve().parent.parent


def _compile_sample() -> list[tuple[str, list[LegalOperation]]]:
    """Compile the canonical UK sample exactly as the provenance sampler does."""
    from farchive import Farchive

    pipeline = UKReplayPipeline(_data_root())
    out: list[tuple[str, list[LegalOperation]]] = []
    with Farchive(str(_ARCHIVE_PATH), readonly=True) as archive:
        for statute_id in _SAMPLE_IDS:
            ops = pipeline.compile_ops_for_statute(statute_id, archive=archive)
            out.append((statute_id, list(ops)))
    return out


def _raw_xml_by_artifact(
    artifact_ids: set[str],
) -> dict[str, bytes]:
    """Load each affecting act's raw XML bytes (the artifact the anchor offsets into)."""
    from farchive import Farchive

    from lawvm.uk_legislation.effect_source_selection import (
        get_affecting_act_xml_from_archive,
    )

    out: dict[str, bytes] = {}
    with Farchive(str(_ARCHIVE_PATH), readonly=True) as archive:
        for aid in artifact_ids:
            raw = get_affecting_act_xml_from_archive(aid, archive)
            if raw is not None:
                out[aid] = raw
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


def _apply_digest(samples: list[tuple[str, list[LegalOperation]]]) -> str:
    rows = [[_op_apply_view(op) for op in ops] for _sid, ops in samples]
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_uk_minted_anchors_are_real_and_reverifiable() -> None:
    """Any anchor the post-pass DOES mint re-verifies byte-exactly.

    On the canonical structured artifact this mints zero anchors (see the blocked
    verdict), but the property must hold unconditionally: a minted anchor is never
    a fabricated offset — it always re-slices to exactly the clause it anchors.
    """
    samples = _compile_sample()
    artifact_ids = {
        op.source.statute_id
        for _sid, ops in samples
        for op in ops
        if op.source is not None
    }
    raw_by_artifact = _raw_xml_by_artifact(artifact_ids)

    total = anchored = 0
    for _sid, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is None:
                continue
            anchored += 1
            assert isinstance(anchor, SourceAnchor)
            assert op.source is not None
            art_id = op.source.statute_id
            assert anchor.source_artifact_id == art_id
            raw = raw_by_artifact[art_id]
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


def test_uk_source_anchor_is_grounding_neutral() -> None:
    """The anchor pass is additive metadata: the apply-input digest is invariant.

    Stripping ``source_anchor`` cannot change the apply-authoritative digest; the
    post-pass must never perturb an apply-authoritative field.
    """
    from dataclasses import replace

    samples = _compile_sample()
    digest_with = _apply_digest(samples)

    stripped: list[tuple[str, list[LegalOperation]]] = []
    for sid, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                new_ops.append(
                    replace(op, source=replace(op.source, source_anchor=None))
                )
            else:
                new_ops.append(op)
        stripped.append((sid, new_ops))
    digest_without = _apply_digest(stripped)

    assert digest_with == digest_without, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_uk_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Every absent anchor is JUSTIFIED: the clause is not a unique verbatim run.

    For each op left without an anchor, the recorded clause must genuinely NOT be a
    unique contiguous verbatim byte substring of its affecting-act XML — so the
    refusal is correct, not a missed anchor.
    """
    samples = _compile_sample()
    artifact_ids = {
        op.source.statute_id
        for _sid, ops in samples
        for op in ops
        if op.source is not None
    }
    raw_by_artifact = _raw_xml_by_artifact(artifact_ids)

    total = none_count = 0
    for _sid, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is not None:
                continue
            none_count += 1
            if op.source is None:
                continue
            raw = raw_by_artifact.get(op.source.statute_id)
            if raw is None:
                continue  # no addressable artifact — absence trivially justified
            clause = (op.source.raw_text or op.raw_text or "").encode("utf-8")
            if not clause:
                continue
            first = raw.find(clause)
            unique = first >= 0 and raw.find(clause, first + 1) < 0
            assert not unique, (
                "an op with a unique verbatim clause must have been anchored, not "
                f"left None: {clause[:80]!r}"
            )

    assert total > 0
    # The canonical UK verdict: every op is honestly unanchored (structured-source
    # tag-boundary reconstruction). If this ever flips, the verdict has changed and
    # the reachable-anchor tests above carry the byte-exact proof.
    assert none_count == total, (
        "UK canonical sample expected fully BLOCKED (0 anchors); some op anchored — "
        "update the feasibility verdict if the source surface changed"
    )


def test_uk_blockage_is_tag_boundary_reconstruction_not_a_defect() -> None:
    """Prove the blockage cause and that the post-pass works once the clause is a run.

    Two-part proof, pinning the exact feasibility reason:

    1. STRUCTURAL CAUSE. For a real op whose flattened clause is not a verbatim
       byte run of its affecting XML, the clause's word-sequence DOES appear once
       the XML's tags are stripped — proving the clause was reconstructed across
       element boundaries (``<Pnumber>``/``<Text>``), not merely whitespace-shifted.

    2. POST-PASS IS NOT DEFECTIVE. On a SYNTHETIC artifact in which the SAME clause
       IS a single contiguous verbatim byte run, the post-pass mints a TRUE anchor
       that re-verifies byte-exactly. So the UK arm is structured-source-BLOCKED,
       not code-defective: the recipe generalizes the moment a per-op clause is a
       verbatim run of its artifact.
    """
    samples = _compile_sample()
    artifact_ids = {
        op.source.statute_id
        for _sid, ops in samples
        for op in ops
        if op.source is not None
    }
    raw_by_artifact = _raw_xml_by_artifact(artifact_ids)

    # Pick a real op whose clause is NOT a verbatim byte run of its raw XML but IS
    # recoverable after tag-stripping (the structural signature).
    proved_structural = False
    sample_op: LegalOperation | None = None
    for _sid, ops in samples:
        for op in ops:
            if op.source is None:
                continue
            raw = raw_by_artifact.get(op.source.statute_id)
            clause = op.source.raw_text or op.raw_text or ""
            if not raw or not clause:
                continue
            needle = clause.encode("utf-8")
            if needle in raw:
                continue  # this one IS a byte run — not the case we want to prove
            raw_text = raw.decode("utf-8", errors="ignore")
            tag_stripped = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_text))
            if clause in tag_stripped:
                proved_structural = True
                if sample_op is None:
                    sample_op = op
                break
        if proved_structural:
            break

    assert proved_structural, (
        "expected at least one UK op whose flattened clause is recoverable only "
        "after tag-stripping (the structured-source tag-boundary signature)"
    )
    assert sample_op is not None and sample_op.source is not None

    # Part 2: build a SYNTHETIC UTF-8 artifact where the SAME clause is a single
    # contiguous, unique verbatim run, then run the post-pass over a single-op
    # stream. The anchor must mint and re-verify byte-exactly.
    clause = sample_op.source.raw_text or sample_op.raw_text or ""
    art_id = sample_op.source.statute_id
    synthetic = (
        b"<Pblock><P1><Pnumber/><P1para><Text>"
        + clause.encode("utf-8")
        + b"</Text></P1para></P1></Pblock>"
    )
    needle = clause.encode("utf-8")
    first = synthetic.find(needle)
    assert first >= 0 and synthetic.find(needle, first + 1) < 0, (
        "clause should be a unique verbatim run of the synthetic UTF-8 artifact"
    )

    token = set_uk_raw_source_context({art_id: synthetic})
    try:
        anchored_ops = mint_uk_source_anchors([sample_op])
    finally:
        reset_uk_raw_source_context(token)

    minted = [
        op
        for op in anchored_ops
        if op.source is not None and op.source.source_anchor is not None
    ]
    assert len(minted) == 1, "post-pass should mint a TRUE anchor on the synthetic run"
    minted_op = minted[0]
    assert minted_op.source is not None
    anchor = minted_op.source.source_anchor
    assert anchor is not None
    assert anchor.source_artifact_id == art_id
    sliced = synthetic[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
    assert sliced == needle
    assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()

    # And cross-check directly via the shared core helper, proving no fabrication.
    direct = compute_source_anchor(
        source_artifact_id=art_id, raw_bytes=synthetic, clause_text=clause
    )
    assert direct == anchor


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
