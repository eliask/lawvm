"""Replay-side conservation gate: every source effect leaves with a receipt.

This is the *twin* of the scoring-side ``check_residue_reconciliation`` test.
It validates :mod:`lawvm.core.replay_conservation` — the jurisdiction-agnostic
"effect -> {op | typed finding}" partition law — against synthetic fixtures that
exhibit each silent-failure class, plus a real-corpus spot-check that lifts the
Finland five-bucket census through the shared partition primitive.

Every gate here is validated BOTH ways: it FIRES on the violation (the
silent-failure class it exists to catch) and PASSES on the correct behaviour. A
gate that cannot catch its own motivating pattern is theatre.

Discipline classes enforced (see ``notes/DISCIPLINE_GATES.md``):

- silent-drop   : a source effect with no receipt at all.
- silent-consume: ``OP_EMITTED`` with zero ops and no finding.
- silent-widen  : an emitted op with no source warrant / a receipt for an
                  effect the source never produced.
- partition leak: a census bucket outside the closed set, or a count that does
                  not sum to the total.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.replay_conservation import (
    EffectDisposition,
    EffectLedger,
    EffectReceipt,
    PartitionCensus,
    ReplayConservationError,
    census_from_bucket_assignments,
    check_effect_conservation,
    check_partition,
    conservation_violations,
    partition_violations,
)


# ---------------------------------------------------------------------------
# Per-receipt invariants (the silent-consume / silent-widen / untyped classes
# are enforced eagerly at construction).
# ---------------------------------------------------------------------------


def test_op_emitted_with_ops_and_warrant_is_well_formed() -> None:
    r = EffectReceipt(
        effect_id="e1",
        disposition=EffectDisposition.OP_EMITTED,
        op_count=2,
        warrant="src-span:10-20",
    )
    assert r.is_typed_receipt
    assert r.op_count == 2


def test_op_emitted_with_zero_ops_is_silent_consume() -> None:
    """The exact 'handled=True returned with no ops and no finding' class."""
    with pytest.raises(ReplayConservationError, match="SILENT-CONSUME"):
        EffectReceipt(
            effect_id="e1",
            disposition=EffectDisposition.OP_EMITTED,
            op_count=0,
            warrant="src",
        )


def test_op_emitted_without_warrant_is_silent_widen() -> None:
    with pytest.raises(ReplayConservationError, match="phantom op|silent-widen"):
        EffectReceipt(
            effect_id="e1",
            disposition=EffectDisposition.OP_EMITTED,
            op_count=1,
            warrant="",
        )


def test_typed_rejection_requires_a_kind() -> None:
    """A typed disposition with no kind string is an untyped (silent) drop."""
    with pytest.raises(ReplayConservationError, match="empty finding_kind"):
        EffectReceipt(
            effect_id="e1",
            disposition=EffectDisposition.TYPED_REJECTION,
            finding_kind="",
        )


def test_typed_rejection_with_a_kind_is_well_formed() -> None:
    r = EffectReceipt(
        effect_id="e1",
        disposition=EffectDisposition.TYPED_REJECTION,
        finding_kind="fi.out_of_scope.registered_class_x",
    )
    assert r.is_typed_receipt


def test_typed_observation_cannot_also_emit_ops() -> None:
    with pytest.raises(ReplayConservationError, match="exactly one disposition"):
        EffectReceipt(
            effect_id="e1",
            disposition=EffectDisposition.TYPED_OBSERVATION,
            finding_kind="presentation_only",
            op_count=1,
        )


def test_empty_effect_id_is_rejected() -> None:
    with pytest.raises(ReplayConservationError, match="effect_id must be non-empty"):
        EffectReceipt(effect_id="", disposition=EffectDisposition.OP_EMITTED, op_count=1, warrant="w")


# ---------------------------------------------------------------------------
# Ledger-level partition invariants.
# ---------------------------------------------------------------------------


def _ok_receipt(eid: str) -> EffectReceipt:
    return EffectReceipt(
        effect_id=eid,
        disposition=EffectDisposition.OP_EMITTED,
        op_count=1,
        warrant=f"warrant:{eid}",
    )


def test_clean_ledger_has_no_violations() -> None:
    ledger = EffectLedger(
        unit_id="2024/1",
        source_effect_ids=("e1", "e2", "e3"),
        receipts=(
            _ok_receipt("e1"),
            EffectReceipt("e2", EffectDisposition.TYPED_REJECTION, finding_kind="k"),
            EffectReceipt("e3", EffectDisposition.TYPED_OBSERVATION, finding_kind="obs"),
        ),
    )
    assert conservation_violations(ledger) == []
    check_effect_conservation(ledger)  # does not raise


def test_silent_drop_effect_with_no_receipt_fires() -> None:
    ledger = EffectLedger(
        unit_id="2024/1",
        source_effect_ids=("e1", "e2"),
        receipts=(_ok_receipt("e1"),),  # e2 dropped
    )
    msgs = conservation_violations(ledger)
    assert any("SILENT DROP" in m and "e2" in m for m in msgs), msgs
    with pytest.raises(ReplayConservationError, match="SILENT DROP"):
        check_effect_conservation(ledger)


def test_phantom_effect_receipt_fires() -> None:
    ledger = EffectLedger(
        unit_id="2024/1",
        source_effect_ids=("e1",),
        receipts=(_ok_receipt("e1"), _ok_receipt("e_phantom")),
    )
    msgs = conservation_violations(ledger)
    assert any("PHANTOM EFFECT" in m and "e_phantom" in m for m in msgs), msgs


def test_duplicate_receipt_fires() -> None:
    ledger = EffectLedger(
        unit_id="2024/1",
        source_effect_ids=("e1",),
        receipts=(_ok_receipt("e1"), _ok_receipt("e1")),
    )
    msgs = conservation_violations(ledger)
    assert any("EXACTLY ONE disposition" in m for m in msgs), msgs


def test_duplicate_source_effect_ids_fires() -> None:
    ledger = EffectLedger(
        unit_id="2024/1",
        source_effect_ids=("e1", "e1"),
        receipts=(_ok_receipt("e1"),),
    )
    msgs = conservation_violations(ledger)
    assert any("denominator must be a set" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# Partition census primitive.
# ---------------------------------------------------------------------------

_BUCKETS = ("owned", "registered_fallback", "unregistered", "genuine_delta")


def test_clean_partition_passes() -> None:
    census = PartitionCensus(
        total=10,
        bucket_ids=_BUCKETS,
        counts={"owned": 7, "registered_fallback": 2, "unregistered": 0, "genuine_delta": 1},
        unaccounted_bucket_ids=("unregistered",),
    )
    assert partition_violations(census) == []
    check_partition(census)


def test_partition_sum_mismatch_fires() -> None:
    census = PartitionCensus(
        total=10,
        bucket_ids=_BUCKETS,
        counts={"owned": 7, "registered_fallback": 2, "unregistered": 0, "genuine_delta": 0},
    )
    assert any("vanished untyped" in m for m in partition_violations(census))


def test_undeclared_bucket_is_a_partition_leak() -> None:
    census = PartitionCensus(
        total=3,
        bucket_ids=_BUCKETS,
        counts={
            "owned": 1,
            "registered_fallback": 1,
            "unregistered": 0,
            "genuine_delta": 0,
            "other": 1,  # the untyped 'other' bucket — the thing the law forbids
        },
    )
    msgs = partition_violations(census)
    assert any("undeclared bucket" in m and "other" in m for m in msgs), msgs


def test_missing_declared_bucket_fires() -> None:
    census = PartitionCensus(
        total=2,
        bucket_ids=_BUCKETS,
        counts={"owned": 2},  # other three buckets not materialized
    )
    assert any("missing declared bucket" in m for m in partition_violations(census))


def test_closed_set_breach_bucket_fires() -> None:
    census = PartitionCensus(
        total=5,
        bucket_ids=_BUCKETS,
        counts={"owned": 4, "registered_fallback": 0, "unregistered": 1, "genuine_delta": 0},
        unaccounted_bucket_ids=("unregistered",),
    )
    msgs = partition_violations(census)
    assert any("closed-set BREACH" in m and "unregistered" in m for m in msgs), msgs


def test_census_from_assignments_roundtrips() -> None:
    census = census_from_bucket_assignments(
        ["owned", "owned", "registered_fallback", "genuine_delta"],
        _BUCKETS,
        unaccounted_bucket_ids=("unregistered",),
    )
    assert census.total == 4
    assert census.counts["owned"] == 2
    assert census.is_partition()
    assert partition_violations(census) == []


def test_census_from_assignments_surfaces_out_of_set_assignment() -> None:
    census = census_from_bucket_assignments(
        ["owned", "mystery_bucket"],
        _BUCKETS,
    )
    # An assignment to a bucket outside the closed set is materialized under that
    # id and flagged as undeclared — never silently dropped or coerced.
    assert census.counts["mystery_bucket"] == 1
    assert any("undeclared bucket" in m for m in partition_violations(census))


# ---------------------------------------------------------------------------
# Real-corpus spot-check: the Finland five-bucket census IS this partition law.
# Gated on the canonical archive; skips loudly otherwise (never fakes data).
# ---------------------------------------------------------------------------


def _canonical_finlex_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


@pytest.mark.skipif(
    not _canonical_finlex_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_fi_census_satisfies_the_shared_partition_law() -> None:
    """The live FI five-bucket census is a clean partition under the core primitive.

    This is the concrete witness that the jurisdiction-agnostic conservation
    primitive describes the same property the FI census already enforces: every
    amendment clause lands in exactly one of the five closed buckets, the buckets
    sum to the total, and the closed-set breach bucket
    (``legacy_fallback_unregistered``) is zero.
    """
    from lawvm.finland.johtolause.census_accounting import (
        CENSUS_ACCOUNTING_BUCKETS,
        census_accounting,
    )

    # A bounded slice keeps the spot-check fast; the partition property holds at
    # any prefix (every clause is independently classified).
    result = census_accounting(limit=400)
    census = PartitionCensus(
        total=result.total_amendment_clauses,
        bucket_ids=CENSUS_ACCOUNTING_BUCKETS,
        counts=result.buckets,
        unaccounted_bucket_ids=("legacy_fallback_unregistered",),
    )
    assert partition_violations(census) == [], partition_violations(census)
    check_partition(census)
    # Sanity: the slice actually exercised some clauses (guard against a vacuous
    # empty-archive pass).
    assert result.total_amendment_clauses > 0
