"""Tests for the Estonia witness-attribution surface.

These exercise the deterministic core over a synthetic mock replay result, so
the default run needs no Riigi Teataja archive. They assert:

  - blind-spot tagging for ops with no ``witness_rule_id`` (never hidden);
  - attribution of ops that carry a ``witness_rule_id`` to their source witness;
  - totality (every op produces exactly one record, attributed or blind-spot);
  - source-witness mapping (amending act id / locator / effective date);
  - operation-family naming, including text-patch replace discrimination;
  - byte-level determinism of the JSON projection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.estonia.witness_attribution import (
    UNATTRIBUTED_WITNESS_BLIND_SPOT_TAG,
    build_ee_op_witness_attribution,
    build_ee_op_witness_attribution_from_ops,
)


@dataclass
class _MockReplayResult:
    """Minimal stand-in for EEPitResult (only fields the surface reads)."""

    compiled_ops: tuple[LegalOperation, ...] = field(default_factory=tuple)
    oracle_id: Optional[str] = None
    error: Optional[str] = None


def _attributed_replace_op() -> LegalOperation:
    return LegalOperation(
        op_id="op-attr-1",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"), ("subsection", "3"))),
        source=OperationSource(
            statute_id="ee/123032019003",
            title="Muutmise seadus",
            enacted="2019-03-01",
            effective="2019-04-01",
            raw_text="paragrahvi 5 lõiget 3 muudetakse",
        ),
        witness_rule_id="ee_subsection_replace",
    )


def _attributed_text_patch_op() -> LegalOperation:
    return LegalOperation(
        op_id="op-attr-2",
        sequence=3,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "8"),)),
        payload=None,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="vana"),
            replacement="uus",
        ),
        source=OperationSource(
            statute_id="ee/200012020001",
            effective="2020-01-01",
            raw_text="sõna 'vana' asendatakse sõnaga 'uus'",
        ),
        witness_rule_id="ee_text_replace",
    )


def _blind_spot_op() -> LegalOperation:
    return LegalOperation(
        op_id="op-blind-1",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "9"),)),
        source=OperationSource(statute_id="ee/123032019003"),
        witness_rule_id=None,
    )


def _surface_with_all_kinds():
    ops = (
        _attributed_replace_op(),
        _attributed_text_patch_op(),
        _blind_spot_op(),
    )
    return build_ee_op_witness_attribution_from_ops(
        base_id="ee/base",
        as_of="2024-01-01",
        oracle_id="ee/oracle",
        compiled_ops=ops,
    )


def test_totality_one_record_per_op() -> None:
    surface = _surface_with_all_kinds()
    assert surface.summary.n_ops == 3
    assert len(surface.records) == 3
    # Every op is either attributed or a blind spot, never both, never neither.
    assert surface.summary.n_attributed + surface.summary.n_blind_spots == 3


def test_blind_spot_tagging_is_loud_and_never_hidden() -> None:
    surface = _surface_with_all_kinds()
    blind = [r for r in surface.records if not r.witness_attributed]
    assert len(blind) == 1
    record = blind[0]
    assert record.op_id == "op-blind-1"
    assert record.witness_rule_id is None
    assert UNATTRIBUTED_WITNESS_BLIND_SPOT_TAG in record.blind_spot_tags
    assert surface.summary.n_blind_spots == 1
    assert surface.summary.blind_spot_op_ids == ("op-blind-1",)


def test_attributed_ops_carry_source_witness() -> None:
    surface = _surface_with_all_kinds()
    attributed = {r.op_id: r for r in surface.records if r.witness_attributed}
    assert set(attributed) == {"op-attr-1", "op-attr-2"}

    rec = attributed["op-attr-1"]
    assert rec.witness_rule_id == "ee_subsection_replace"
    assert rec.source_witness is not None
    assert rec.source_witness.amending_act_id == "ee/123032019003"
    assert rec.source_witness.locator == "ee/123032019003"
    assert rec.source_witness.effective == "2019-04-01"
    assert "muudetakse" in rec.source_witness.raw_text_excerpt
    assert rec.target_address == "section:5/subsection:3"
    assert not rec.blind_spot_tags


def test_operation_family_naming() -> None:
    surface = _surface_with_all_kinds()
    by_id = {r.op_id: r for r in surface.records}
    assert by_id["op-attr-1"].operation_family == "replace"
    # A REPLACE carrying a text_patch is discriminated from a structural replace.
    assert by_id["op-attr-2"].operation_family == "replace_text_patch"
    assert by_id["op-blind-1"].operation_family == "repeal"


def test_summary_rollups_are_key_sorted() -> None:
    surface = _surface_with_all_kinds()
    fams = surface.summary.by_operation_family
    # Sorted by descending count then ascending key; all counts are 1 here.
    assert fams == tuple(sorted(fams, key=lambda kv: (-kv[1], kv[0])))
    rule_ids = dict(surface.summary.by_witness_rule_id)
    assert rule_ids == {"ee_subsection_replace": 1, "ee_text_replace": 1}
    acts = dict(surface.summary.by_amending_act_id)
    assert acts["ee/123032019003"] == 2
    assert acts["ee/200012020001"] == 1


def test_records_sorted_by_sequence() -> None:
    surface = _surface_with_all_kinds()
    seqs = [r.sequence for r in surface.records]
    assert seqs == sorted(seqs)
    assert seqs == [1, 2, 3]


def test_determinism_jsonable_is_stable() -> None:
    import json

    first = _surface_with_all_kinds().to_jsonable()
    second = _surface_with_all_kinds().to_jsonable()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    # Attribution rate is exact and reproducible.
    assert first["summary"]["n_ops"] == 3
    assert first["summary"]["n_attributed"] == 2


def test_no_source_op_counts_under_no_source_bucket() -> None:
    op = LegalOperation(
        op_id="op-nosrc",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "1"),)),
        source=None,
        witness_rule_id=None,
    )
    surface = build_ee_op_witness_attribution_from_ops(
        base_id="ee/base",
        as_of="2024-01-01",
        oracle_id="",
        compiled_ops=(op,),
    )
    rec = surface.records[0]
    assert rec.source_witness is None
    assert not rec.witness_attributed
    assert UNATTRIBUTED_WITNESS_BLIND_SPOT_TAG in rec.blind_spot_tags
    assert dict(surface.summary.by_amending_act_id)["<no_source>"] == 1


def test_build_from_mock_replay_result() -> None:
    """The result-passing path reuses replay output without re-running."""
    mock = _MockReplayResult(
        compiled_ops=(_attributed_replace_op(), _blind_spot_op()),
        oracle_id="ee/oracle-from-result",
        error=None,
    )
    surface = build_ee_op_witness_attribution(
        "ee/base",
        "2024-01-01",
        result=mock,
    )
    assert surface.oracle_id == "ee/oracle-from-result"
    assert surface.summary.n_ops == 2
    assert surface.summary.n_blind_spots == 1
    assert surface.replay_error is None


def test_empty_replay_result_is_total_and_safe() -> None:
    mock = _MockReplayResult(compiled_ops=(), oracle_id="ee/x", error="boom")
    surface = build_ee_op_witness_attribution("ee/base", "2024-01-01", result=mock)
    assert surface.summary.n_ops == 0
    assert surface.summary.n_attributed == 0
    assert surface.summary.n_blind_spots == 0
    assert surface.records == ()
    assert surface.replay_error == "boom"
    assert surface.to_jsonable()["summary"]["attribution_rate"] == 0.0
