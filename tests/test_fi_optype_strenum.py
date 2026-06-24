"""OpType StrEnum migration: behaviour- and serialization-equivalence to the
former bare ``Literal["REPLACE","REPEAL","INSERT","RENUMBER"]``.

A ``StrEnum`` member subclasses ``str`` and its ``value`` equals the legacy wire
string, so on-disk / wire encodings stay byte-identical. These tests lock that
invariant so a future ``OpType`` change that would alter serialization fails
loudly instead of silently corrupting the corpus format.
"""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

from lawvm.finland.ops import AmendmentOp, OpType


LEGACY_WIRE = {
    OpType.REPLACE: "REPLACE",
    OpType.REPEAL: "REPEAL",
    OpType.INSERT: "INSERT",
    OpType.RENUMBER: "RENUMBER",
}


@pytest.mark.parametrize("member,wire", list(LEGACY_WIRE.items()))
def test_member_is_byte_identical_to_legacy_string(member: OpType, wire: str) -> None:
    # str-equality and string rendering must all collapse to the legacy wire form.
    assert member == wire
    assert str(member) == wire
    assert f"{member}" == wire
    assert member.value == wire
    # JSON must emit the plain string, never "OpType.REPLACE"/repr.
    assert json.dumps(member) == json.dumps(wire) == f'"{wire}"'


def test_amendmentop_default_unchanged() -> None:
    op = AmendmentOp(op_id="d", target_section="1", target_unit_kind="section")
    assert op.op_type == "REPLACE"
    assert str(op.op_type) == "REPLACE"


def test_serialize_then_readback_roundtrip() -> None:
    """Mirror the production serialize (str(op.op_type)) + read-back
    (OpType(...)/str(...)) path used by _compile.py / projectors."""
    for member, wire in LEGACY_WIRE.items():
        op = AmendmentOp(
            op_id="x",
            op_type=member,
            target_section="3",
            target_unit_kind="section",
        )
        serialized = str(op.op_type or "")  # production serialize pattern
        assert serialized == wire
        # _compile.py read-back: op_type=str(row.get("op_type") or "")
        rebuilt = AmendmentOp(
            op_id="x",
            op_type=OpType(serialized),
            target_section="3",
            target_unit_kind="section",
        )
        assert rebuilt.op_type == op.op_type == wire


def test_parquet_column_stores_plain_string() -> None:
    tbl = pa.table({"op_type": [str(m) for m in LEGACY_WIRE]})
    assert tbl.column("op_type").to_pylist() == list(LEGACY_WIRE.values())


def test_membership_and_set_comparisons_match_strings() -> None:
    op = AmendmentOp(
        op_id="x",
        op_type=OpType.INSERT,
        target_section="3",
        target_unit_kind="section",
    )
    assert op.op_type in (OpType.REPLACE, OpType.INSERT)
    assert op.op_type in {"REPLACE", "INSERT"}  # str-set still matches
    assert op.op_type != OpType.REPEAL
