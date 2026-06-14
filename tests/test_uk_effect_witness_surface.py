"""Tests for the read-only UK effect-feed witness attribution surface.

The surface maps each compiled op's ``witness_rule_id`` back to its source
effect witness (effect-row id, affecting-act fragment locator, action family,
owning phase, adjudication bucket). It is observation-only and must:

- emit one record per compiled op, deterministically ordered;
- never silently blank a witness — an op without a stamped ``witness_rule_id``
  is loudly tagged ``unattributed_witness_blind_spot``;
- join each op back to its effect row via ``group_id`` (== effect_id).
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.effect_witness_attribution import (
    UNATTRIBUTED_WITNESS_BLIND_SPOT,
    build_uk_effect_witness_attribution,
    uk_effect_witness_attribution_summary,
)
from lawvm.uk_legislation.effects import UKEffectRecord


def _effect(
    effect_id: str,
    *,
    effect_type: str = "inserted",
    affecting_uri: str = "http://www.legislation.gov.uk/id/uksi/1994/1935",
    affecting_provisions: str = "reg. 2",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/1985/6",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1985",
        affected_number="6",
        affected_provisions="s. 1",
        affecting_uri=affecting_uri,
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="1994",
        affecting_number="1935",
        affecting_provisions=affecting_provisions,
        affecting_title="Some Affecting Order",
    )


def _op(
    op_id: str,
    *,
    group_id: str,
    action: StructuralAction = StructuralAction.INSERT,
    target_label: str = "1",
    witness_rule_id: str | None = None,
    sequence: int = 1,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=action,
        target=LegalAddress(path=(("section", target_label),)),
        source=OperationSource(statute_id="uksi/1994/1935", title="Some Affecting Order"),
        group_id=group_id,
        witness_rule_id=witness_rule_id,
    )


def _frontier_diagnostic(
    effect_id: str,
    *,
    status: str = "deterministic_frontend_supported",
    source_lane: str = "extracted_affecting_source",
) -> dict[str, object]:
    return {
        "rule_id": "uk_manual_compile_frontier_classified",
        "effect_id": effect_id,
        "affecting_act_id": "uksi/1994/1935",
        "manual_compile_status": status,
        "manual_compile_rule_id": "uk_manual_frontier_deterministic_supported",
        "owner_phase": "canonical_op_compilation",
        "source_pathology": "",
        "source_witness": {
            "source_lane": source_lane,
            "artifact_id": "uksi/1994/1935",
            "metadata": {"affecting_provisions": "reg. 2"},
        },
    }


def test_record_shape_and_fields() -> None:
    effect = _effect("eff-1")
    op = _op("eff-1", group_id="eff-1", witness_rule_id="uk_effect_some_rule")
    records = build_uk_effect_witness_attribution(
        ops=[op],
        effect_rows=[effect],
        effect_diagnostics=[_frontier_diagnostic("eff-1")],
    )
    assert len(records) == 1
    record = records[0]
    assert record.op_id == "eff-1"
    assert record.target == "section:1"
    assert record.action == "insert"
    assert record.witness_rule_id == "uk_effect_some_rule"
    assert record.witness_attributed is True
    # "inserted" effect type has no nonstructural family, so action_family
    # falls back to the canonical structural action.
    assert record.action_family == "insert"
    assert record.phase_owner == "canonical_op_compilation"
    assert record.adjudication_bucket == "deterministic_frontend_supported"
    witness = record.source_witness
    assert witness.effect_row_id == "eff-1"
    assert witness.affecting_act_id == "uksi/1994/1935"
    assert witness.affecting_provisions == "reg. 2"
    assert witness.affecting_fragment_locator == "uksi/1994/1935 reg. 2"
    assert witness.authority_layer == "extracted_affecting_source"
    assert witness.authority_layer_source == "manual_frontier_source_witness_lane"
    assert witness.effect_row_present is True

    # to_dict shape mirrors the dataclass and is JSON-friendly.
    as_dict = record.to_dict()
    assert as_dict["witness_rule_id"] == "uk_effect_some_rule"
    assert set(as_dict["source_witness"]) == {
        "effect_row_id",
        "affecting_act_id",
        "affecting_provisions",
        "affecting_fragment_locator",
        "authority_layer",
        "authority_layer_source",
        "source_lane",
        "effect_row_present",
    }


def test_action_family_from_effect_type() -> None:
    # "added" maps to a nonstructural replay family even when the op is INSERT.
    effect = _effect("eff-a", effect_type="added")
    op = _op("eff-a", group_id="eff-a", action=StructuralAction.INSERT)
    records = build_uk_effect_witness_attribution(ops=[op], effect_rows=[effect])
    assert records[0].action_family == "added_source_structural_insert"


def test_unattributed_witness_is_loud_never_blank() -> None:
    # An op with no stamped witness_rule_id must be loudly tagged, never blank.
    op = _op("eff-2", group_id="eff-2", witness_rule_id=None)
    records = build_uk_effect_witness_attribution(
        ops=[op],
        effect_rows=[_effect("eff-2")],
    )
    record = records[0]
    assert record.witness_attributed is False
    assert record.witness_rule_id == UNATTRIBUTED_WITNESS_BLIND_SPOT
    assert record.witness_rule_id  # never empty


def test_every_record_witness_nonempty_or_tagged() -> None:
    ops = [
        _op("eff-1", group_id="eff-1", witness_rule_id="uk_rule_x", sequence=1),
        _op("eff-2", group_id="eff-2", witness_rule_id=None, sequence=2),
        _op("eff-3", group_id="eff-3", witness_rule_id="", sequence=3),
    ]
    effects = [_effect("eff-1"), _effect("eff-2"), _effect("eff-3")]
    records = build_uk_effect_witness_attribution(ops=ops, effect_rows=effects)
    for record in records:
        assert record.witness_rule_id, "witness_rule_id must never be silently blank"
        if not record.witness_attributed:
            assert record.witness_rule_id == UNATTRIBUTED_WITNESS_BLIND_SPOT


def test_deterministic_ordering() -> None:
    # Out-of-order input ops; result must be sorted by (sequence, op_id, target).
    ops = [
        _op("b", group_id="eff-1", target_label="3", sequence=2),
        _op("a", group_id="eff-1", target_label="2", sequence=2),
        _op("c", group_id="eff-1", target_label="1", sequence=1),
    ]
    records = build_uk_effect_witness_attribution(
        ops=ops, effect_rows=[_effect("eff-1")]
    )
    keys = [(r.sequence, r.op_id, r.target) for r in records]
    assert keys == sorted(keys)
    # Stable across repeated runs.
    again = build_uk_effect_witness_attribution(
        ops=ops, effect_rows=[_effect("eff-1")]
    )
    assert [r.to_dict() for r in records] == [r.to_dict() for r in again]


def test_missing_effect_row_is_surfaced_not_hidden() -> None:
    # Op whose group_id has no matching effect row: surfaced loudly.
    op = _op("orphan", group_id="missing-effect", witness_rule_id="uk_rule")
    records = build_uk_effect_witness_attribution(ops=[op], effect_rows=[])
    record = records[0]
    assert record.source_witness.effect_row_present is False
    assert record.source_witness.effect_row_id == "missing-effect"
    # Authority layer falls back to the op's branch authority (no frontier lane).
    assert record.source_witness.authority_layer_source == "op_source_branch_authority"


def test_summary_counts_are_sorted_and_complete() -> None:
    ops = [
        _op("eff-1", group_id="eff-1", witness_rule_id="uk_rule_a", sequence=1),
        _op("eff-2", group_id="eff-2", witness_rule_id=None, sequence=2),
    ]
    effects = [_effect("eff-1"), _effect("eff-2")]
    diagnostics = [_frontier_diagnostic("eff-1", status="manual_compile_candidate")]
    records = build_uk_effect_witness_attribution(
        ops=ops, effect_rows=effects, effect_diagnostics=diagnostics
    )
    summary = uk_effect_witness_attribution_summary(records)
    assert summary["n_records"] == 2
    assert summary["n_unattributed_witness_blind_spots"] == 1
    assert summary["n_missing_effect_rows"] == 0
    # eff-1 has a manual_compile_candidate frontier; eff-2 has none.
    assert summary["adjudication_bucket_counts"] == {
        "compiled_no_manual_frontier_record": 1,
        "manual_compile_candidate": 1,
    }
    assert UNATTRIBUTED_WITNESS_BLIND_SPOT in summary["witness_rule_counts"]
    # All count maps must be key-sorted for stable diffs.
    for key in (
        "witness_rule_counts",
        "action_family_counts",
        "phase_owner_counts",
        "adjudication_bucket_counts",
        "authority_layer_counts",
    ):
        counts = summary[key]
        assert list(counts) == sorted(counts)
