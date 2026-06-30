"""Tests for ``core.provenance_totality_audit`` (``PROVENANCE.SOURCE_ANCHOR_MISSING``).

Stream C — provenance totality. Every emitted LegalOperation should trace back to
a source instruction by carrying a typed source anchor (or at least textual
provenance footing). The audit surfaces the ops that carry NO footing at all.

Synthetic regression covers:

* an op with a typed byte-span ``source_anchor`` → no finding;
* an op with empty provenance (no source, no raw_text) → exactly one
  ``PROVENANCE.SOURCE_ANCHOR_MISSING`` carrying the audited fields;
* the weaker textual footings (op.raw_text / source.raw_text / source.statute_id)
  each independently suppress the finding (the predicate is "any footing");
* deterministic ordering over multiple orphans;
* empty input → empty output.

Audit-plane-only contract: the function emits observations, never raises on
shape-valid input, never mutates ops, never fabricates an anchor. ``Observation.kind``
is the registered FindingSpec code (registry anti-drift checks in
``tests/test_finding_registry.py`` cover the wire-to-registry binding).
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource, SourceAnchor
from lawvm.core.provenance_totality_audit import (
    PROVENANCE_SOURCE_ANCHOR_MISSING,
    assert_op_provenance_totality,
)
from lawvm.core.semantic_types import StructuralAction

_ADDR = LegalAddress(path=(("section", "1"),))
_ANCHOR = SourceAnchor(
    source_artifact_id="art-1",
    byte_offset=0,
    byte_len=3,
    quote_hash="sha256:" + "0" * 64,
)


def _op(op_id: str, *, source=None, raw_text: str = "", sequence: int = 1) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_ADDR,
        source=source,
        raw_text=raw_text,
    )


def test_op_with_source_anchor_emits_no_finding() -> None:
    op = _op("o1", source=OperationSource(statute_id="", source_anchor=_ANCHOR))
    assert assert_op_provenance_totality([op], source_statute="s/1") == ()


def test_op_with_empty_provenance_emits_one_finding_with_audited_fields() -> None:
    # No source object and no per-op raw_text → traces back to nothing.
    op = _op("o2", source=None, raw_text="")
    findings = assert_op_provenance_totality([op], source_statute="s/1")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == PROVENANCE_SOURCE_ANCHOR_MISSING
    assert finding.source_statute == "s/1"
    assert finding.detail["op_id"] == "o2"
    assert finding.detail["action"] == "replace"
    assert finding.detail["target"] == "section:1"
    assert finding.detail["has_source"] is False
    # Every provenance carrier is reported empty.
    present = finding.detail["provenance_present"]
    assert present == {
        "op_raw_text": False,
        "source_anchor": False,
        "source_raw_text": False,
        "source_statute_id": False,
    }
    assert finding.detail["provenance_empty"] == (
        "op_raw_text",
        "source_anchor",
        "source_raw_text",
        "source_statute_id",
    )


def test_per_op_raw_text_footing_suppresses_finding() -> None:
    op = _op("o3", source=None, raw_text="10 § kumotaan")
    assert assert_op_provenance_totality([op]) == ()


def test_source_raw_text_footing_suppresses_finding() -> None:
    op = _op("o4", source=OperationSource(statute_id="", raw_text="johtolause"))
    assert assert_op_provenance_totality([op]) == ()


def test_source_statute_id_footing_suppresses_finding() -> None:
    op = _op("o5", source=OperationSource(statute_id="fi/1990/1295"))
    assert assert_op_provenance_totality([op]) == ()


def test_deterministic_ordering_over_multiple_orphans() -> None:
    ops = [
        _op("a", source=None, sequence=1),
        _op("b", source=OperationSource(statute_id="fi/x"), sequence=2),  # footed → skip
        _op("c", source=None, sequence=3),
    ]
    findings = assert_op_provenance_totality(ops, source_statute="s/1")
    # Orphans in op-stream order: a, c (b has statute_id footing).
    assert [f.detail["op_id"] for f in findings] == ["a", "c"]
    again = assert_op_provenance_totality(ops, source_statute="s/1")
    assert [f.detail for f in findings] == [f.detail for f in again]


def test_empty_input_yields_empty_output() -> None:
    assert assert_op_provenance_totality([]) == ()
