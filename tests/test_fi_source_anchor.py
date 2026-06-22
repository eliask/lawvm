"""Byte-level source-clause anchoring (SourceAnchor) end-to-end.

These pin the vertical slice that retires the deferred source-anchor debt:

1. ``compute_source_anchor`` captures a verbatim contiguous byte span and is
   fail-loud (returns None, never fabricates) when the clause is absent or
   ambiguous.
2. A real landed receipt produced by the PRODUCTION receipt collector
   (``_collect_op_write_receipt``) carries the anchor it inherited from the
   resolved op's ``OperationSource`` — proving the anchor is not severed before
   the apply boundary.
3. The certificate writer flips ``CERT.SOURCE_ANCHOR_UNAVAILABLE`` to a
   certified ``source_anchor`` only when the anchor RE-VERIFIES against the
   bundled source bytes, and keeps the fail-loud residual otherwise.

The first two are self-contained (no corpus). The certificate flip is tested at
the writer-logic level here; a real-corpus end-to-end build is in
``tests/test_certificate_bundle.py``-adjacent coverage.
"""

from __future__ import annotations

import hashlib
from typing import Any, List, cast

from lawvm.core.provenance import OperationSource, SourceAnchor, compute_source_anchor
from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_resolved_op import (
    ApplyResolvedOpSinks,
    _collect_op_write_receipt,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.statute import ReplayState


# ---------------------------------------------------------------------------
# 1. primitive: capture + fail-loud
# ---------------------------------------------------------------------------


def test_compute_source_anchor_captures_verbatim_span() -> None:
    raw = b"<formula>Muutetaan lain 5 pykala seuraavasti:</formula>"
    clause = "Muutetaan lain 5 pykala seuraavasti:"
    anchor = compute_source_anchor(
        source_artifact_id="2020/1", raw_bytes=raw, clause_text=clause
    )
    assert anchor is not None
    # The byte span re-extracts the exact clause and the quote_hash matches.
    quoted = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
    assert quoted.decode("utf-8") == clause
    assert anchor.quote_hash == "sha256:" + hashlib.sha256(quoted).hexdigest()
    assert anchor.source_artifact_id == "2020/1"


def test_compute_source_anchor_absent_clause_is_fail_loud() -> None:
    raw = b"<formula>Muutetaan lain 5 pykala</formula>"
    assert (
        compute_source_anchor(
            source_artifact_id="2020/1", raw_bytes=raw, clause_text="not present"
        )
        is None
    )


def test_compute_source_anchor_ambiguous_clause_is_fail_loud() -> None:
    # The clause occurs twice — we cannot certify which occurrence drove a write.
    raw = b"kumotaan 1 pykala. kumotaan 1 pykala."
    assert (
        compute_source_anchor(
            source_artifact_id="2020/1",
            raw_bytes=raw,
            clause_text="kumotaan 1 pykala.",
        )
        is None
    )


def test_compute_source_anchor_empty_inputs_fail_loud() -> None:
    assert compute_source_anchor(source_artifact_id="x", raw_bytes=b"", clause_text="a") is None
    assert compute_source_anchor(source_artifact_id="x", raw_bytes=b"abc", clause_text="") is None
    assert compute_source_anchor(source_artifact_id="", raw_bytes=b"abc", clause_text="a") is None


def test_source_anchor_jsonable_is_byte_unit() -> None:
    anchor = SourceAnchor(
        source_artifact_id="2020/1",
        byte_offset=3,
        byte_len=10,
        quote_hash="sha256:" + "0" * 64,
    )
    j = anchor.as_jsonable()
    assert j["span_unit"] == "byte"
    assert j["byte_offset"] == 3
    assert j["byte_len"] == 10
    assert j["source_artifact_id"] == "2020/1"


# ---------------------------------------------------------------------------
# 2. the anchor reaches the production receipt consumer (not severed)
# ---------------------------------------------------------------------------


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sec(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=(_content(text),))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _resolved_op_with_anchor(op_id: str, anchor: SourceAnchor | None) -> ResolvedOp:
    op = AmendmentOp(
        op_id=op_id,
        op_type=cast("Any", "REPLACE"),
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
    )
    source = OperationSource(statute_id="2020/1", source_anchor=anchor)
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
        op_source=source,
    )


def test_landed_receipt_carries_source_anchor_to_production_consumer() -> None:
    """A landed write's receipt carries the op's SourceAnchor — not severed.

    This drives the SAME production receipt collector the replay fold uses
    (``_collect_op_write_receipt``), so the anchor demonstrably survives all the
    way to ``ApplyOpsSinks.write_receipts_out`` — the accumulator the
    certificate stage reads via ``ReplayResult.write_receipts``.
    """
    anchor = SourceAnchor(
        source_artifact_id="2020/1",
        byte_offset=11,
        byte_len=20,
        quote_hash="sha256:" + "a" * 64,
    )
    before = _body(_sec("1", "one"), _sec("2", "two"))
    after = _body(_sec("1", "ONE"), _sec("2", "two"))
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    rop = _resolved_op_with_anchor("anchored_op", anchor)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )

    assert len(sinks.write_receipts_out) == 1
    receipt = sinks.write_receipts_out[0]
    assert receipt.source_anchor is anchor


def test_landed_receipt_without_anchor_stays_none() -> None:
    """When the op carries no anchor, the receipt's anchor stays None (fail-loud)."""
    before = _body(_sec("1", "one"))
    after = _body(_sec("1", "ONE"))
    prev_state, new_state = ReplayState(ir=before), ReplayState(ir=after)
    rop = _resolved_op_with_anchor("unanchored_op", None)
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=None,
        source_statute="2020/1",
        sinks=sinks,
    )
    assert len(sinks.write_receipts_out) == 1
    assert sinks.write_receipts_out[0].source_anchor is None


# ---------------------------------------------------------------------------
# 3. the certificate writer flips SOURCE_ANCHOR_UNAVAILABLE -> certified anchor
#    end-to-end (real bundle build), only when the anchor re-verifies.
# ---------------------------------------------------------------------------


def _patched_replay_with_anchor(monkeypatch: Any, anchor: SourceAnchor) -> None:
    """Make the certificate writer's replay attach ``anchor`` to its receipt.

    Drives the REAL ``build_certificate_bundle`` writer; only the landed
    receipt's anchor is substituted, exactly as a parse that captured a genuine
    byte span would have produced.
    """
    import dataclasses

    import lawvm.tools.certificate_bundle as cb

    real_run = cb.run_engine_replay

    def patched(engine_id: str) -> Any:
        bundle = real_run(engine_id)
        receipts = list(bundle.result.write_receipts)
        assert receipts, "fixture statute must land at least one receipt"
        receipts[0] = dataclasses.replace(receipts[0], source_anchor=anchor)
        bundle.result.write_receipts = tuple(receipts)
        return bundle

    monkeypatch.setattr(cb, "run_engine_replay", patched)


def _anchor_over_bundled_source(statute_id: str, byte_offset: int, byte_len: int) -> SourceAnchor:
    from lawvm.finland.corpus import _get_corpus_store

    raw = _get_corpus_store().read_amendment(statute_id)
    assert raw is not None
    quoted = raw[byte_offset : byte_offset + byte_len]
    return SourceAnchor(
        source_artifact_id=statute_id,
        byte_offset=byte_offset,
        byte_len=byte_len,
        quote_hash="sha256:" + hashlib.sha256(quoted).hexdigest(),
    )


def _read_jsonl(path: Any) -> list[dict[str, Any]]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_certificate_flips_to_certified_anchor_when_verified(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """A receipt anchor that re-verifies against bundled bytes is CERTIFIED.

    The amending source of 482/2024 is 2025/368; we attach a byte anchor over
    its real bundled source bytes and assert: (1) the transition driven by that
    source carries the certified ``source_anchor`` in the trace; (2) that
    (transition, ref) pair no longer emits a CERT.SOURCE_ANCHOR_UNAVAILABLE
    residual; (3) the rebuilt bundle re-verifies (roots recompute from disk).
    """
    from lawvm.tools.certificate_bundle import build_certificate_bundle, verify_bundle

    anchor = _anchor_over_bundled_source("2025/368", byte_offset=200, byte_len=50)
    _patched_replay_with_anchor(monkeypatch, anchor)

    out = tmp_path / "bundle"
    build_certificate_bundle("482/2024", out, graph_store_root=tmp_path / "graph")

    rows = _read_jsonl(out / "trace" / "certified_tree_transitions.jsonl")
    anchored = [r for r in rows if r.get("source_anchors")]
    assert len(anchored) >= 1
    cert_anchor = anchored[0]["source_anchors"][0]
    assert cert_anchor["span_unit"] == "byte"
    assert cert_anchor["source_artifact_id"] == "2025/368"
    assert cert_anchor["byte_offset"] == 200 and cert_anchor["byte_len"] == 50

    residuals = _read_jsonl(out / "residue" / "residuals.jsonl")
    # The exact (transition_id, ref) pairs that earned a certified anchor must
    # NOT appear as fail-loud CERT.SOURCE_ANCHOR_UNAVAILABLE residuals. Refs that
    # earned no anchor still keep their residual.
    anchored_pairs: set[tuple[str, str]] = set()
    for row in anchored:
        for a in row["source_anchors"]:
            # The transition's source_ref is the bundle artifact id; match the
            # residual rows by (transition_id, ref) below using the row refs.
            for ref in row["source_refs"]:
                anchored_pairs.add((row["transition_id"], ref))

    unavailable_pairs: set[tuple[str, str]] = set()
    for resid in residuals:
        if resid.get("diagnostic_code") != "CERT.SOURCE_ANCHOR_UNAVAILABLE":
            continue
        tid = resid.get("transition_id", "")
        for ref in resid.get("source_refs", []):
            unavailable_pairs.add((tid, ref))

    # At least one anchored pair exists, and none of the anchored pairs are
    # still emitted as fail-loud.
    assert anchored_pairs
    assert not (anchored_pairs & unavailable_pairs)

    # Self-consistent: roots recompute from the written bundle.
    verify_bundle(out)


def test_certificate_keeps_fail_loud_when_anchor_does_not_verify(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """An anchor whose bytes do not re-verify is DROPPED (stays fail-loud)."""
    from lawvm.tools.certificate_bundle import build_certificate_bundle

    # Correct offsets but a deliberately wrong quote_hash -> re-verify fails.
    bad = SourceAnchor(
        source_artifact_id="2025/368",
        byte_offset=200,
        byte_len=50,
        quote_hash="sha256:" + "0" * 64,
    )
    _patched_replay_with_anchor(monkeypatch, bad)

    out = tmp_path / "bundle"
    build_certificate_bundle("482/2024", out, graph_store_root=tmp_path / "graph")

    rows = _read_jsonl(out / "trace" / "certified_tree_transitions.jsonl")
    assert all(not r.get("source_anchors") for r in rows)
    residuals = _read_jsonl(out / "residue" / "residuals.jsonl")
    assert any(
        r.get("diagnostic_code") == "CERT.SOURCE_ANCHOR_UNAVAILABLE" for r in residuals
    )
