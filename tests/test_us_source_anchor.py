"""Byte-span ``SourceAnchor`` arm for the US Federal frontend (LawVM task #100).

Mirrors the Estonia pilot (``tests/test_ee_source_anchor.py``), the Norway arm
(``tests/test_no_source_anchor.py``), the Sweden arm (``tests/test_se_source_anchor.py``),
and the UK arm (``tests/test_uk_source_anchor.py``) in SHAPE, and now records a
REACHABLE (partial) feasibility result for US Federal via PER-ELEMENT anchoring.

FEASIBILITY VERDICT: REACHABLE (partial) via per-element anchoring. The prior
task-#92 arm anchored the FLATTENED WHOLE CLAUSE string and minted 0/43, because
govinfo PLAW USLM is DENSELY structured: a single amendatory clause's number lives
in ``<num>``, its caption in ``<heading><inline>``, its lead-in prose in
``<chapeau>`` (with the USC target in a nested ``<ref>``), and its payload in
``<quotedText>``/``<quotedContent>``. So the flattened clause (e.g. ``"(b)
Reserve.—Section 8908 of title 40 … the following:“(c) …”."``) is reconstructed
ACROSS MANY element boundaries and is never a contiguous verbatim byte run of the
raw XML.

  PER-ELEMENT FIX. The anchored unit is RE-SCOPED from the flattened whole clause
  to the operative BODY ELEMENT it came from. ``mint_us_source_anchors`` re-parses
  the Public Law, collects every descendant element whose ``_text_of`` flattening is
  a single, contiguous, GLOBALLY UNIQUE verbatim byte run of the raw bytes (the
  ``<quotedText>``/``<quotedContent>``/``<chapeau>``/``<ref>`` leaves with no
  interleaved inline markup), and anchors the op against the LONGEST such body that
  is a substring of the op's flattened clause — so the anchored span provably
  belongs to THIS op. The byte-exact uniqueness proof is carried forward as a
  ``SourceAnchor`` record before stamping.

MEASURED on the canonical sample (3 Public Laws, 43 ops):
``no_typed_anchor_rate`` drops from 100% (task #92) to a real majority of ops
carrying a TRUE, byte-re-verifiable per-element anchor. The remaining honest
``None`` cases are the operative text reconstructed across INLINE
``<quotedText>`` markup, where no descendant element body is a contiguous byte
run. Every absence is justified — fail-loud, never a fabricated offset.

These tests prove (a) every anchor the post-pass mints re-verifies byte-exactly
(the span re-slices to its anchored body, which is a substring of the op's clause,
``quote_hash`` matches, occurrence is unique), (b) the pass is grounding-neutral
(the apply digest is invariant under stripping the anchor), (c) every absence is
justified (no op left ``None`` had a unique-byte-run body inside its clause), (d)
the reachable fraction is a real, non-trivial majority, and (e) on a synthetic
artifact where a clause body IS a contiguous run the post-pass mints a TRUE anchor.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource, SourceAnchor, compute_source_anchor
from lawvm.core.semantic_types import StructuralAction
import lawvm.us_federal.amendatory as amendatory
from lawvm.us_federal.amendatory import (
    _collapse_ws_strip,
    _itertext_excluding_sidenotes,
    _unique_byte_run_body_records,
    _unique_byte_run_bodies,
    lower_plaw_amendatory,
    mint_us_source_anchors,
    reset_us_raw_source_context,
    set_us_raw_source_context,
)
from lawvm.us_federal.sources import (
    open_us_federal_farchive,
    parse_plaw_locator,
    plaw_locator,
    read_plaw_locator,
)

# The same pinned Public Laws the provenance-totality US sampler measures
# (lawvm.tools.provenance_totality_report._sample_us_federal).
_SAMPLE_LOCATORS = [plaw_locator(108, 126), plaw_locator(108, 121), plaw_locator(108, 128)]


def _us_archive_path() -> Path | None:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root:
        candidate = Path(root) / "data" / "us_federal.farchive"
        if candidate.exists():
            return candidate
    fallback = Path(__file__).resolve().parent.parent / "data" / "us_federal.farchive"
    return fallback if fallback.exists() else None


_ARCHIVE_PATH = _us_archive_path()
pytestmark = pytest.mark.skipif(
    _ARCHIVE_PATH is None,
    reason="us_federal.farchive not available (set LAWVM_CANONICAL_DATA_ROOT)",
)


def _compile_sample() -> list[tuple[str, bytes, list[LegalOperation]]]:
    """Compile the canonical US sample exactly as the provenance sampler does.

    Returns ``(statute_id, raw_uslm_bytes, ops)`` per Public Law — the raw bytes are
    the canonical artifact a verifier reloads and the byte-span anchor offsets into.
    """
    out: list[tuple[str, bytes, list[LegalOperation]]] = []
    archive = open_us_federal_farchive(_ARCHIVE_PATH, readonly=True)
    try:
        for locator in _SAMPLE_LOCATORS:
            ident = parse_plaw_locator(locator)
            data = read_plaw_locator(archive, locator)
            assert data is not None and ident is not None, f"missing US bytes for {locator}"
            statute_id = f"PL {ident.congress}-{ident.number}"
            report = lower_plaw_amendatory(data, statute_id=statute_id)
            out.append((statute_id, data, list(report.operations())))
    finally:
        archive.close()
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
    rows = [[_op_apply_view(op) for op in ops] for _sid, _raw, ops in samples]
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_us_minted_anchors_are_real_and_reverifiable() -> None:
    """Every anchor the post-pass mints re-verifies byte-exactly.

    PER-ELEMENT: the anchored span is the operative BODY ELEMENT (a substring of the
    op's flattened clause), not the whole clause. The span must re-slice to exactly
    that body, its ``quote_hash`` must be the sha256 of those bytes, its occurrence
    must be unique, and the body must be a substring of the op's recorded clause (so
    the span provably belongs to THIS op). A minted anchor is never a fabricated
    offset.
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
            assert op.source is not None
            # Anchor points back at THIS Public Law's raw artifact.
            assert anchor.source_artifact_id == art_id
            assert anchor.source_artifact_id == op.source.statute_id
            clause = op.source.raw_text or op.raw_text or ""
            assert clause, "an anchored op must carry the clause it anchors"
            sliced = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
            body = sliced.decode("utf-8")
            # The anchored span is a per-element BODY that provably belongs to this
            # op: a substring of the recorded clause.
            assert body in clause, (
                f"anchored body is not a substring of the op clause in {art_id}: "
                f"{body[:80]!r}"
            )
            assert anchor.byte_len == len(sliced)
            assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()
            first = raw.find(sliced)
            assert first == anchor.byte_offset
            assert raw.find(sliced, first + 1) == -1, "anchored body must be unique"

    assert total > 0, "sampler produced no ops"
    assert anchored > 0, "per-element anchoring should mint a non-trivial number of anchors"


def test_us_source_anchor_is_grounding_neutral() -> None:
    """The anchor pass is additive metadata: the apply-input digest is invariant.

    Stripping ``source_anchor`` cannot change the apply-authoritative digest; the
    post-pass must never perturb an apply-authoritative field (the field that drives
    ``dry_run.py::_materialize_one`` and the AGREE/RESIDUAL rows).
    """
    from dataclasses import replace

    samples = _compile_sample()
    digest_with = _apply_digest(samples)

    stripped: list[tuple[str, bytes, list[LegalOperation]]] = []
    stripped_any = False
    for sid, raw, ops in samples:
        new_ops: list[LegalOperation] = []
        for op in ops:
            if op.source is not None and op.source.source_anchor is not None:
                stripped_any = True
                new_ops.append(replace(op, source=replace(op.source, source_anchor=None)))
            else:
                new_ops.append(op)
        stripped.append((sid, raw, new_ops))
    digest_without = _apply_digest(stripped)

    assert stripped_any, "expected at least one anchor to strip (the pass minted some)"
    assert digest_with == digest_without, (
        "stamping a SourceAnchor changed an apply-authoritative field — NOT "
        "grounding-neutral"
    )


def test_us_unanchorable_ops_are_honest_none_not_fabricated() -> None:
    """Every absent anchor is JUSTIFIED: no unique-byte-run body is inside the clause.

    For each op left without an anchor, NO descendant element body of its Public Law
    that is a unique contiguous verbatim byte run is a substring of the op's clause —
    so the refusal is correct, not a missed anchor. (The operative text is
    reconstructed across INLINE ``<quotedText>`` markup, leaving no contiguous
    element body inside the clause.)
    """
    samples = _compile_sample()
    bodies_by_art: dict[str, list[str]] = {}
    total = none_count = 0
    for art_id, raw, ops in samples:
        bodies = bodies_by_art.setdefault(art_id, _unique_byte_run_bodies(raw))
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
            if not clause:
                continue  # no clause text at all → nothing to anchor, honest
            # If ANY unique-byte-run body were a substring of this clause, the op
            # would have been anchored. None being a substring is the honest cause.
            assert not any(b and b in clause for b in bodies), (
                "an op with a unique-byte-run body inside its clause must have been "
                f"anchored, not left None: {clause[:80]!r}"
            )

    assert total > 0
    # REACHABLE-PARTIAL: a non-trivial MAJORITY of ops anchor; the honest-None
    # residual (inline-markup reconstruction) is a real minority, not the whole.
    assert none_count < total, "expected the per-element pass to anchor a majority of ops"


def test_us_reachable_fraction_is_a_real_majority() -> None:
    """The per-element pass anchors a real, non-trivial MAJORITY of the op stream.

    Pins the reachable fraction the byte-span friction map reports: > half of the
    canonical US op stream carries a TRUE per-element byte-span anchor — US is
    CRACKED per-element exactly as UK was, and to a HIGHER fraction (USLM's
    ``<quotedText>``/``<quotedContent>`` payloads are large contiguous bodies).
    """
    samples = _compile_sample()
    total = anchored = 0
    for _art_id, _raw, ops in samples:
        for op in ops:
            total += 1
            if op.source is not None and op.source.source_anchor is not None:
                anchored += 1

    assert total > 0
    # A genuine majority (the measured fraction is 31/43 ≈ 72%); guard against a
    # silent regression to the BLOCKED state (0) or a trivial fraction.
    assert anchored * 2 > total, (
        f"per-element anchoring should reach a majority of ops; got {anchored}/{total}"
    )


def test_us_per_element_body_re_scope_mints_a_true_anchor() -> None:
    """Prove the per-element re-scope: a body inside a NON-run clause mints an anchor.

    Two-part proof, pinning the exact feasibility mechanism:

    1. The flattened WHOLE clause is NOT a contiguous byte run of its Public Law's
       USLM XML (the structured-source signature) — yet a descendant element BODY of
       that Public Law IS a unique byte run and IS a substring of the clause. That
       body is what the per-element pass anchors.

    2. POST-PASS IS NOT DEFECTIVE. Re-running ``mint_us_source_anchors`` over that
       single op (with the same Public Law published in context) mints a TRUE anchor
       whose span re-slices to that body and re-verifies byte-exactly via the shared
       core helper — so the US arm is REACHABLE per-element, not code-defective.
    """
    samples = _compile_sample()

    chosen: tuple[str, bytes, LegalOperation, str] | None = None
    for art_id, raw, ops in samples:
        bodies = _unique_byte_run_bodies(raw)
        for op in ops:
            if op.source is None:
                continue
            clause = op.source.raw_text or op.raw_text or ""
            if not clause:
                continue
            # The case we want: whole clause is NOT a byte run, but a body element IS.
            if clause.encode("utf-8") in raw:
                continue
            body = next((b for b in bodies if b and b in clause), None)
            if body is None:
                continue
            chosen = (art_id, raw, op, body)
            break
        if chosen is not None:
            break

    assert chosen is not None, (
        "expected at least one US op whose flattened clause is NOT a byte run but "
        "whose operative body element IS — the per-element re-scope signature"
    )
    art_id, raw, op, body = chosen
    assert op.source is not None

    # Part 1 cross-check: the body is a unique contiguous byte run of the raw bytes
    # and a substring of the op's clause, while the whole clause is neither.
    needle = body.encode("utf-8")
    first = raw.find(needle)
    assert first >= 0 and raw.find(needle, first + 1) < 0, "body must be a unique run"
    clause = op.source.raw_text or op.raw_text or ""
    assert body in clause
    assert clause.encode("utf-8") not in raw

    # Part 2: re-run the post-pass over this single op with the SAME canonical Public
    # Law published in context; the anchor must mint and re-verify byte-exactly.
    from dataclasses import replace

    op_no_anchor = replace(op, source=replace(op.source, source_anchor=None))
    token = set_us_raw_source_context(art_id, raw)
    try:
        anchored_ops = mint_us_source_anchors([op_no_anchor])
    finally:
        reset_us_raw_source_context(token)

    minted = [
        o
        for o in anchored_ops
        if o.source is not None and o.source.source_anchor is not None
    ]
    assert len(minted) == 1, "post-pass should mint a TRUE per-element anchor"
    minted_src = minted[0].source
    assert minted_src is not None
    anchor = minted_src.source_anchor
    assert anchor is not None
    assert anchor.source_artifact_id == art_id
    sliced = raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
    assert sliced == needle
    assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()

    # And cross-check directly via the shared core helper, proving no fabrication.
    direct = compute_source_anchor(
        source_artifact_id=art_id, raw_bytes=raw, clause_text=body
    )
    assert direct == anchor


def test_us_synthetic_contiguous_run_mints_anchor() -> None:
    """On a synthetic artifact where a body IS a contiguous run the pass mints it.

    Guards the floor: build a synthetic UTF-8 Public Law where the operative body is
    a single contiguous unique verbatim run inside a larger clause, and confirm the
    per-element pass mints a TRUE, byte-re-verifiable anchor on the body.
    """
    # The chapeau body is a unique contiguous run; the flattened WHOLE clause is not
    # (the <num> prefix "(1) " lives in a sibling element and breaks the run). The
    # per-element pass anchors the chapeau body — a strict substring of the clause.
    body = "Section 8901(2) of title 40 is amended by striking the following text."
    clause = f"(1) {body}"
    art_id = "PL synthetic-1"
    synthetic = (
        b"<section><num>(1) </num><chapeau>"
        + body.encode("utf-8")
        + b"</chapeau></section>"
    )
    assert _unique_byte_run_bodies(synthetic)  # at least one addressable body
    assert body.encode("utf-8") in synthetic
    assert clause.encode("utf-8") not in synthetic

    op = LegalOperation(
        op_id=f"{art_id}#op1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=()),
        raw_text=clause,
        source=OperationSource(statute_id=art_id, raw_text=clause),
    )
    token = set_us_raw_source_context(art_id, synthetic)
    try:
        anchored = mint_us_source_anchors([op])
    finally:
        reset_us_raw_source_context(token)
    assert anchored[0].source is not None
    anchor = anchored[0].source.source_anchor
    assert anchor is not None
    sliced = synthetic[anchor.byte_offset : anchor.byte_offset + anchor.byte_len]
    assert sliced == body.encode("utf-8")
    assert anchor.quote_hash == "sha256:" + hashlib.sha256(sliced).hexdigest()


def test_us_unique_byte_run_bodies_include_uslm_body_carrier_tags() -> None:
    """The optimized candidate scan must retain real USLM body-carrier tags."""
    raw = b"""\
<section>
  <chapeau>Chapeau body is a unique byte run.</chapeau>
  <quotedText>Quoted text body is unique.</quotedText>
  <quotedContent>Quoted content body is unique.</quotedContent>
  <ref>Reference body is unique.</ref>
  <heading>Heading body is not an operative anchor candidate.</heading>
</section>
"""

    bodies = set(_unique_byte_run_bodies(raw))

    assert "Chapeau body is a unique byte run." in bodies
    assert "Quoted text body is unique." in bodies
    assert "Quoted content body is unique." in bodies
    assert "Reference body is unique." in bodies
    assert "Heading body is not an operative anchor candidate." not in bodies


def test_us_collapse_ws_strip_preserves_regex_collapse_semantics() -> None:
    assert _collapse_ws_strip("") == ""
    assert _collapse_ws_strip("Section") == "Section"
    assert _collapse_ws_strip("Section 10 A") == "Section 10 A"
    assert _collapse_ws_strip("  Section   10\nA\t") == "Section 10 A"
    assert _collapse_ws_strip("A\u00a0B") == "A B"


def test_us_itertext_leaf_fast_path_preserves_text_not_tail() -> None:
    root = ET.fromstring("<root><leaf>Body</leaf>Tail</root>")
    leaf = root.find("leaf")
    assert leaf is not None

    assert _itertext_excluding_sidenotes(leaf) == "Body"


def test_us_unique_byte_run_bodies_prefilters_to_candidate_clauses() -> None:
    raw = b"""\
<section>
  <chapeau>Clause body selected by an emitted operation.</chapeau>
  <paragraph>Clause body selected by a different operation.</paragraph>
  <quotedText>Unrelated body from another instruction.</quotedText>
</section>
"""

    bodies = _unique_byte_run_bodies(
        raw,
        candidate_clauses=("Prefix Clause body selected by an emitted operation.",),
    )

    assert bodies == ["Clause body selected by an emitted operation."]


def test_us_unique_byte_run_body_records_prefilters_before_unique_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"""\
<section>
  <chapeau>Clause body selected by an emitted operation.</chapeau>
  <paragraph>Clause body selected by a different operation.</paragraph>
  <quotedText>Unrelated body from another instruction.</quotedText>
</section>
"""
    seen_candidates: list[list[str]] = []

    def fake_unique_byte_run_text_positions(
        haystack: bytes, candidates: list[str]
    ) -> list[tuple[str, int]]:
        seen_candidates.append(list(candidates))
        return [
            (candidate, haystack.find(candidate.encode("utf-8")))
            for candidate in candidates
        ]

    monkeypatch.setattr(
        amendatory,
        "unique_byte_run_text_positions",
        fake_unique_byte_run_text_positions,
    )

    records = _unique_byte_run_body_records(
        raw,
        source_artifact_id="PL TEST",
        candidate_clauses=("Prefix Clause body selected by an emitted operation.",),
    )

    assert seen_candidates == [["Clause body selected by an emitted operation."]]
    assert [record.text for record in records] == [
        "Clause body selected by an emitted operation."
    ]


def test_us_unique_byte_run_body_records_reuse_verified_offsets() -> None:
    raw = b"""\
<section>
  <chapeau>Clause body selected by an emitted operation.</chapeau>
</section>
"""

    records = _unique_byte_run_body_records(
        raw,
        source_artifact_id="PL TEST",
        candidate_clauses=("Prefix Clause body selected by an emitted operation.",),
    )

    assert len(records) == 1
    record = records[0]
    encoded = record.text.encode("utf-8")
    anchor = record.source_anchor
    assert anchor.byte_offset == raw.find(encoded)
    assert raw[anchor.byte_offset : anchor.byte_offset + anchor.byte_len] == encoded
    assert anchor.source_artifact_id == "PL TEST"


def test_us_unique_byte_run_body_records_reuses_supplied_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"""\
<section>
  <chapeau>Clause body selected by an emitted operation.</chapeau>
</section>
"""
    root = ET.fromstring(raw)

    def fail_parse(_raw: bytes) -> ET.Element:
        raise AssertionError("supplied root should avoid reparsing raw bytes")

    monkeypatch.setattr(amendatory.ET, "fromstring", fail_parse)

    records = _unique_byte_run_body_records(
        raw,
        source_artifact_id="PL TEST",
        candidate_clauses=("Prefix Clause body selected by an emitted operation.",),
        root=root,
    )

    assert [record.text for record in records] == [
        "Clause body selected by an emitted operation."
    ]


def test_us_unique_byte_run_bodies_indexed_kernel_preserves_uniqueness() -> None:
    raw = b"""\
<section>
  <chapeau>Unique omnibus body selected by an emitted operation.</chapeau>
  <quotedText>Duplicate omnibus body excluded.</quotedText>
  <quotedText>Duplicate omnibus body excluded.</quotedText>
  <quotedContent>Unrelated omnibus body from another instruction.</quotedContent>
</section>
"""

    bodies = _unique_byte_run_bodies(
        raw,
        candidate_clauses=(
            "Prefix Unique omnibus body selected by an emitted operation.",
            "Duplicate omnibus body excluded.",
        ),
    )

    assert bodies == ["Unique omnibus body selected by an emitted operation."]


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(pytest.main([__file__, "-q"]))
