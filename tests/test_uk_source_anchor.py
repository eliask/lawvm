"""Byte-span ``SourceAnchor`` arm for the UK frontend (LawVM task #95).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``), the Norway arm
(``tests/test_no_source_anchor.py``), and the Sweden arm
(``tests/test_se_source_anchor.py``) in SHAPE, and now records a REACHABLE
(partial) feasibility result for the UK via PER-ELEMENT anchoring.

FEASIBILITY VERDICT: REACHABLE (partial) via per-element anchoring. The prior
task-#92 arm anchored the FLATTENED WHOLE-CLAUSE string and minted 0/709, because
UK affecting-act XML is STRUCTURED: a clause's number lives in a ``<Pnumber>``
element (often with interleaved ``<CommentaryRef/>`` children) and its body in a
sibling ``<P1para><Text>``, so the flattened clause (e.g. ``"10 In this Act, omit
paragraph 7(4) of Schedule 11."``) is reconstructed ACROSS element boundaries and
is never a contiguous verbatim byte run of the raw XML.

  PER-ELEMENT FIX. The anchored unit is RE-SCOPED from the flattened whole clause
  to the operative BODY ELEMENT it came from. ``mint_uk_source_anchors`` re-parses
  the affecting act, collects every descendant element whose ``_text_content`` is a
  single, contiguous, GLOBALLY UNIQUE verbatim byte run of the raw bytes (the
  ``<Text>``/``<Pnumber>`` leaves with no interleaved inline markup), and anchors
  the op against the LONGEST such body that is a substring of the op's flattened
  clause — so the anchored span provably belongs to THIS op. ``compute_source_anchor``
  re-verifies byte-exactness and uniqueness before minting.

MEASURED on the canonical sample (3 affected acts, 709 ops):
``no_typed_anchor_rate`` drops from 100% (709/709, task #92) to ~38% — the majority
of ops now carry a TRUE, byte-re-verifiable per-element anchor. The remaining ops
are honest ``None``: the operative text is reconstructed across INLINE markup
(``<Quotation>``/``<Term>``/``<Addition>`` for substituted/defined terms), so no
descendant element body is a contiguous byte run. Every absence is justified —
fail-loud, never a fabricated offset.

These tests prove (a) every anchor the post-pass mints re-verifies byte-exactly
(the span re-slices to its anchored body, ``quote_hash`` matches, occurrence is
unique), (b) the pass is grounding-neutral (the apply digest is invariant under
stripping the anchor), (c) every absence is justified (no op left ``None`` had a
unique-byte-run body inside its clause), (d) the reachable fraction is a real,
non-trivial majority, and (e) on a synthetic artifact where a clause IS a
contiguous run the post-pass mints a TRUE anchor.
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
    _unique_byte_run_bodies,
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
    """Every anchor the post-pass mints re-verifies byte-exactly.

    The anchor is now per-element: the span re-slices to the operative BODY element
    the post-pass selected (a unique byte run of the affecting XML), that body is a
    substring of the op's flattened clause (so it provably belongs to this op), the
    ``quote_hash`` matches the sliced bytes, and the body occurs exactly once. A
    minted anchor is never a fabricated offset.
    """
    samples = _compile_sample()
    artifact_ids = {
        op.source.statute_id
        for _sid, ops in samples
        for op in ops
        if op.source is not None
    }
    raw_by_artifact = _raw_xml_by_artifact(artifact_ids)
    bodies_by_artifact = {
        aid: set(_unique_byte_run_bodies(raw)) for aid, raw in raw_by_artifact.items()
    }

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
            sliced = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            # The anchored span re-slices to a unique-byte-run BODY element of the
            # affecting act, and that body belongs to this op's flattened clause.
            body = sliced.decode("utf-8")
            assert body in bodies_by_artifact[art_id], (
                f"anchored span is not a unique-byte-run body of {art_id}"
            )
            clause = op.source.raw_text or op.raw_text or ""
            assert body in clause, (
                "anchored body must be a substring of the op's flattened clause "
                f"(provably belongs to this op): {body[:80]!r}"
            )
            assert anchor.byte_len == len(sliced)
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            first = raw.find(sliced)
            assert first == anchor.byte_offset
            assert raw.find(sliced, first + 1) == -1, "anchored body must be unique"

    assert total > 0, "sampler produced no ops"
    # The per-element re-scope makes the byte-span program REACHABLE for the UK on a
    # real, non-trivial majority of the canonical op stream (not the prior 0/709).
    assert anchored > total // 2, (
        f"expected a majority of ops to anchor per-element; got {anchored}/{total}"
    )


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
    """Every absent anchor is JUSTIFIED: no unique-byte-run body inside the clause.

    For each op left without an anchor, the affecting act must genuinely have NO
    descendant-element body that is BOTH a unique contiguous verbatim byte run of the
    raw XML AND a substring of the op's flattened clause — so the refusal is correct,
    not a missed per-element anchor. (These are the operative-text-across-inline-markup
    cases: substituted/defined terms whose ``<Quotation>``/``<Term>`` children break
    every body into non-contiguous fragments.)
    """
    samples = _compile_sample()
    artifact_ids = {
        op.source.statute_id
        for _sid, ops in samples
        for op in ops
        if op.source is not None
    }
    raw_by_artifact = _raw_xml_by_artifact(artifact_ids)
    bodies_by_artifact = {
        aid: _unique_byte_run_bodies(raw) for aid, raw in raw_by_artifact.items()
    }

    total = none_count = anchored = 0
    for _sid, ops in samples:
        for op in ops:
            total += 1
            anchor = op.source.source_anchor if op.source is not None else None
            if anchor is not None:
                anchored += 1
                continue
            none_count += 1
            if op.source is None:
                continue
            raw = raw_by_artifact.get(op.source.statute_id)
            if raw is None:
                continue  # no addressable artifact — absence trivially justified
            clause = op.source.raw_text or op.raw_text or ""
            if not clause:
                continue
            anchorable_body = next(
                (
                    body
                    for body in bodies_by_artifact.get(op.source.statute_id, [])
                    if body and body in clause
                ),
                None,
            )
            assert anchorable_body is None, (
                "an op whose clause contains a unique-byte-run body must have been "
                f"anchored, not left None: {anchorable_body!r:.80}"
            )

    assert total > 0
    # The UK verdict is now REACHABLE (partial): a real majority anchor per-element,
    # the rest are honest None. If the reachable fraction collapses to 0 again the
    # per-element re-scope has regressed.
    assert anchored > 0, "per-element anchoring should reach a non-empty fraction"
    assert none_count + anchored == total


def test_uk_whole_clause_is_tag_boundary_reconstructed_but_body_anchors() -> None:
    """Pin WHY the whole clause won't anchor and WHY the per-element body does.

    Two-part proof:

    1. WHOLE-CLAUSE CAUSE. For a real op whose flattened clause is not a verbatim
       byte run of its affecting XML, the clause's word-sequence DOES appear once
       the XML's tags are stripped — proving the whole clause was reconstructed
       across element boundaries (``<Pnumber>``/``<Text>``), so anchoring the
       flattened whole clause directly is correctly refused (the task-#92 0/709).

    2. PER-ELEMENT POST-PASS MINTS. On a SYNTHETIC artifact in which that clause is
       a single contiguous verbatim byte run inside a ``<Text>`` body, the post-pass
       re-scopes to that body element and mints a TRUE anchor that re-verifies
       byte-exactly — the mechanism by which the canonical sample reaches ~62% of
       ops: each anchored op's operative body IS such a run.
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
    #
    # Strip any anchor the real compile already stamped on this op (the post-pass is
    # idempotent — it skips ops that already carry an anchor — so a stale real-artifact
    # anchor would otherwise be re-sliced against the synthetic bytes).
    from dataclasses import replace as _replace

    sample_op = _replace(
        sample_op, source=_replace(sample_op.source, source_anchor=None)
    )
    assert sample_op.source is not None
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


def test_uk_unique_byte_run_bodies_include_paragraph_and_amendment_body_tags() -> None:
    """The optimized candidate scan must retain real UK body-carrier tags."""
    raw = b"""\
<Legislation>
  <P2para>Paragraph body is a direct unique byte run.</P2para>
  <InlineAmendment>Inline amendment body is unique.</InlineAmendment>
  <BlockAmendment>Block amendment body is unique.</BlockAmendment>
  <Heading>Heading text is not an operative body anchor candidate.</Heading>
</Legislation>
"""

    bodies = set(_unique_byte_run_bodies(raw))

    assert "Paragraph body is a direct unique byte run." in bodies
    assert "Inline amendment body is unique." in bodies
    assert "Block amendment body is unique." in bodies
    assert "Heading text is not an operative body anchor candidate." not in bodies


def test_uk_unique_byte_run_bodies_can_prefilter_to_op_clauses() -> None:
    raw = b"""\
<Legislation>
  <P2para>Referenced amendment body is unique.</P2para>
  <P2para>Unreferenced amendment body is also unique.</P2para>
</Legislation>
"""

    all_bodies = set(_unique_byte_run_bodies(raw))
    filtered_bodies = set(
        _unique_byte_run_bodies(
            raw,
            candidate_clauses=("The op clause uses Referenced amendment body is unique.",),
        )
    )

    assert "Referenced amendment body is unique." in all_bodies
    assert "Unreferenced amendment body is also unique." in all_bodies
    assert filtered_bodies == {"Referenced amendment body is unique."}


def test_uk_unique_byte_run_bodies_prefilter_does_not_match_across_clauses() -> None:
    raw = b"""\
<Legislation>
  <P2para>Alpha Beta</P2para>
</Legislation>
"""

    bodies = _unique_byte_run_bodies(
        raw,
        candidate_clauses=("prefix Alpha", "Beta suffix"),
    )

    assert bodies == []


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
