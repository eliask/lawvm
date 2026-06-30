"""EE op provenance totality (stream C / ``PROVENANCE.SOURCE_ANCHOR_MISSING``).

Synthetic, archive-free tests that pin the provenance-totality contract at the
Estonia lowering boundary (:func:`lawvm.estonia.peg.extract_ee_ops`):

* every emitted EE op carries a populated ``OperationSource`` (so
  ``source.statute_id`` — the amending act identity — is always present) and the
  per-op verbatim clause text (``op.raw_text`` / ``source.raw_text``);
* :func:`assert_op_provenance_totality` over a sample of EE ops emits ZERO
  ``PROVENANCE.SOURCE_ANCHOR_MISSING`` findings (no provenance-orphaned op);
* the fill is grounding-neutral — populating provenance does NOT change any
  apply-authoritative field (action/target/payload/anchor/destination/
  text_patch/order), so EE replay output is byte-identical.

The strong typed byte-span ``source_anchor`` is intentionally NOT asserted: the
raw amendment XML bytes/offsets do not reach this text-only lowering boundary,
so it cannot be computed here without fabricating an offset. The audit's weaker
footings (statute_id + raw_text) are the strongest available here and are the
ones populated; this is recorded explicitly below.
"""

from __future__ import annotations

from dataclasses import replace

from lawvm.core.ir import OperationSource
from lawvm.core.provenance_totality_audit import (
    PROVENANCE_SOURCE_ANCHOR_MISSING,
    assert_op_provenance_totality,
)
from lawvm.estonia.peg import extract_ee_ops

# Representative per-item amendment instructions covering the common lowered
# shapes (insert / repeal / text_replace / subsection add), including the
# "standard single-provision op" path that historically minted ops with no
# ``source`` and orphaned ~80% of the EE op stream.
_EE_INSTRUCTIONS = (
    "1) paragrahvi 12 täiendatakse lõikega 4 järgmises sõnastuses: „käesolev lõige”;",
    "2) paragrahv 5 tunnistatakse kehtetuks;",
    "3) paragrahvi 26 lõikes 1 asendatakse sõna „vana” sõnaga „uus”;",
    "4) paragrahvi 3 lõiget 2 täiendatakse punktiga 5 järgmises sõnastuses: „uus punkt”;",
)

_SOURCE_ID = "ee/261378"


def _lower(instruction: str, source: OperationSource):
    return extract_ee_ops(instruction, source, seq_start=1)


def test_every_ee_op_carries_statute_id_and_raw_text() -> None:
    source = OperationSource(statute_id=_SOURCE_ID, title="Test seaduse muutmise seadus")
    any_op = False
    for instruction in _EE_INSTRUCTIONS:
        ops = _lower(instruction, source)
        for op in ops:
            any_op = True
            assert op.source is not None, f"op {op.op_id} has no OperationSource"
            assert op.source.statute_id == _SOURCE_ID, (
                f"op {op.op_id} dropped the amending act identity"
            )
            # Per-op verbatim clause footing on at least one carrier.
            assert op.raw_text or op.source.raw_text, (
                f"op {op.op_id} carries no per-op clause text"
            )
    assert any_op, "fixture produced no ops"


def test_assert_op_provenance_totality_emits_no_findings_for_ee_sample() -> None:
    source = OperationSource(statute_id=_SOURCE_ID)
    all_ops = []
    for instruction in _EE_INSTRUCTIONS:
        all_ops.extend(_lower(instruction, source))
    assert all_ops, "fixture produced no ops"
    findings = assert_op_provenance_totality(tuple(all_ops), source_statute=_SOURCE_ID)
    assert findings == (), (
        f"expected zero {PROVENANCE_SOURCE_ANCHOR_MISSING} findings, got "
        f"{[f.detail.get('op_id') for f in findings]}"
    )


def test_per_op_raw_text_is_the_instruction_clause_not_empty() -> None:
    source = OperationSource(statute_id=_SOURCE_ID)
    # An instruction whose source carries no amendment-level raw_text: the per-op
    # clause text is the only textual footing, so it must be populated per-op.
    ops = _lower(_EE_INSTRUCTIONS[1], source)
    assert ops
    for op in ops:
        assert op.raw_text, f"op {op.op_id} has empty per-op raw_text"
        # The clause text is the instruction with the leading "N) " marker stripped.
        assert "tunnistatakse kehtetuks" in op.raw_text


def _apply_authoritative_signature(op) -> tuple:
    """Replay-authoritative fields only — deliberately EXCLUDES provenance."""
    payload = op.payload
    return (
        op.sequence,
        str(op.action),
        str(op.target),
        str(op.anchor) if op.anchor is not None else None,
        str(op.destination) if op.destination is not None else None,
        payload.text if payload is not None else None,
        repr(sorted(payload.attrs.items())) if payload is not None else None,
        repr(op.text_patch),
    )


def test_provenance_fill_is_grounding_neutral() -> None:
    """Filling provenance must not change any apply-authoritative field.

    Compare lowering with a bare source (statute_id only) vs a source that
    already carries an amendment-level ``raw_text``: the provenance differs, but
    every replay-authoritative field is byte-identical.
    """
    bare = OperationSource(statute_id=_SOURCE_ID)
    with_text = replace(bare, raw_text="johtolause-level amendment text")
    for instruction in _EE_INSTRUCTIONS:
        ops_bare = _lower(instruction, bare)
        ops_text = _lower(instruction, with_text)
        sig_bare = [_apply_authoritative_signature(op) for op in ops_bare]
        sig_text = [_apply_authoritative_signature(op) for op in ops_text]
        assert sig_bare == sig_text, (
            f"provenance fill changed apply-authoritative output for: {instruction!r}"
        )


def test_existing_source_raw_text_is_not_overwritten() -> None:
    """The fill is non-overwriting: an amendment-level raw_text survives."""
    johtolause = "Johtolause: käesolevas seaduses tehakse järgmised muudatused"
    source = OperationSource(statute_id=_SOURCE_ID, raw_text=johtolause)
    for instruction in _EE_INSTRUCTIONS:
        for op in _lower(instruction, source):
            if op.source is not None and op.source.raw_text:
                assert op.source.raw_text == johtolause, (
                    f"op {op.op_id} overwrote the amendment-level raw_text"
                )
