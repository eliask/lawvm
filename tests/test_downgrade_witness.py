"""Witness-required-for-downgrade gate.

Validates :mod:`lawvm.core.downgrade_witness` — the invariant that any
adjudication downgrading a (blocking) bug-kind to a non-blocking observation
must carry a non-empty witness (a reclassification rule id AND a reason).

Validated BOTH ways: a witnessed downgrade passes; a witnessless one fires. A
real spot-check exercises the actual UK source-pathology downgrade path and
asserts every reclassified record it produces satisfies the invariant.

Motivating cases (see ``notes/DISCIPLINE_GATES.md``):

- a ``sort_order`` tree-invariant violation dropped from the blocking set as
  "spurious" without a recorded reason;
- a UK compile rejection flipped ``blocking=False`` without a
  ``reclassification_reason``.
"""

from __future__ import annotations

import pytest

from lawvm.core.downgrade_witness import (
    DowngradeRecord,
    DowngradeWitnessError,
    check_downgrade_witness,
    downgrade_witness_violation,
    downgrade_witness_violations,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures: witnessed passes, witnessless fires.
# ---------------------------------------------------------------------------


def test_witnessed_downgrade_passes() -> None:
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="sort_order",
        downgraded_to_nonblocking=True,
        reclassification_rule_id="no_sort_order_spurious_roman_recheck",
        reclassification_reason="sibling group is ordered under roman semantics",
    )
    assert rec.requires_witness
    assert rec.is_witnessed
    assert downgrade_witness_violation(rec) is None
    check_downgrade_witness(rec)  # does not raise


def test_witnessless_downgrade_fires() -> None:
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="sort_order",
        downgraded_to_nonblocking=True,
        # no rule id, no reason — the silent-suppression shape
    )
    msg = downgrade_witness_violation(rec)
    assert msg is not None
    assert "reclassification_rule_id" in msg
    assert "reclassification_reason" in msg
    with pytest.raises(DowngradeWitnessError, match="witnessless downgrade"):
        check_downgrade_witness(rec)


def test_downgrade_missing_only_reason_fires() -> None:
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="sort_order",
        downgraded_to_nonblocking=True,
        reclassification_rule_id="some_rule",
        reclassification_reason="",
    )
    msg = downgrade_witness_violation(rec)
    assert msg is not None and "reclassification_reason" in msg
    assert "reclassification_rule_id" not in msg


def test_downgrade_missing_only_rule_id_fires() -> None:
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="sort_order",
        downgraded_to_nonblocking=True,
        reclassification_rule_id="",
        reclassification_reason="a reason",
    )
    msg = downgrade_witness_violation(rec)
    assert msg is not None and "reclassification_rule_id" in msg


def test_finding_that_stays_blocking_needs_no_witness() -> None:
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="sort_order",
        downgraded_to_nonblocking=False,
    )
    assert not rec.requires_witness
    assert downgrade_witness_violation(rec) is None


def test_non_bug_observation_needs_no_witness() -> None:
    """Downgrading a non-bug-kind finding is benign; only bug-kinds need a witness."""
    rec = DowngradeRecord(
        finding_id="f1",
        bug_kind="",  # not a bug-kind — a plain informational observation
        downgraded_to_nonblocking=True,
    )
    assert not rec.requires_witness
    assert downgrade_witness_violation(rec) is None


def test_violations_collects_only_witnessless() -> None:
    records = [
        DowngradeRecord("ok", "sort_order", True, "r", "reason"),
        DowngradeRecord("bad", "sort_order", True),
        DowngradeRecord("blocking_kept", "sort_order", False),
    ]
    msgs = downgrade_witness_violations(records)
    assert len(msgs) == 1
    assert "bad" in msgs[0]


# ---------------------------------------------------------------------------
# Real spot-check: the actual UK source-pathology downgrade path produces
# witnessed records.
# ---------------------------------------------------------------------------


def _uk_rejection_to_downgrade_record(rejection: dict, *, was_blocking: bool) -> DowngradeRecord:
    """Map a UK lowering-rejection dict into the jurisdiction-agnostic record.

    The UK rejection carries the witness under
    ``nonblocking_reclassification_rule_id`` + ``reclassification_reason``; the
    bug-kind is the rejection's ``reason_code`` (the kind it was raised under).
    A downgrade happened iff the row was blocking before and is non-blocking now.
    """
    now_blocking = bool(rejection.get("blocking", True))
    return DowngradeRecord(
        finding_id=str(rejection.get("reason_code") or rejection.get("rule_id") or "uk"),
        bug_kind=str(rejection.get("reason_code") or "uk_lowering_rejection"),
        downgraded_to_nonblocking=was_blocking and not now_blocking,
        reclassification_rule_id=str(rejection.get("nonblocking_reclassification_rule_id") or ""),
        reclassification_reason=str(rejection.get("reclassification_reason") or ""),
    )


def test_uk_source_pathology_downgrade_is_witnessed() -> None:
    """The real UK source-pathology nonblocking reclassification carries a witness."""
    from lawvm.uk_legislation.lowering_records import (
        mark_source_pathology_nonreplay_lowering_rejections_nonblocking,
    )

    # A blocking compile rejection that source-pathology classification proves is
    # outside direct replay (the path that legitimately downgrades it).
    rejection = {
        "blocking": True,
        "reason_code": "nonstructural_root_gap",
        "reason": "synthetic blocking lowering rejection",
        "rule_id": "uk_effect_lowered_to_no_ops",
    }
    rejections = [rejection]
    changed = mark_source_pathology_nonreplay_lowering_rejections_nonblocking(
        source_pathology="nonstructural_root_gap",
        lowering_rejections=rejections,
        start_index=0,
    )
    assert changed, "expected the source-pathology path to downgrade the blocking row"
    assert rejections[0]["blocking"] is False

    record = _uk_rejection_to_downgrade_record(rejections[0], was_blocking=True)
    assert record.requires_witness, "a real bug-kind downgrade must require a witness"
    # The crux: the real downgrade carries BOTH witness fields, so the invariant
    # passes. If the UK path ever dropped the reason, this would fire.
    assert downgrade_witness_violation(record) is None, downgrade_witness_violation(record)
    check_downgrade_witness(record)


def test_uk_out_of_scope_source_pathology_does_not_downgrade() -> None:
    """A source pathology NOT in the out-of-scope set leaves the row blocking."""
    from lawvm.uk_legislation.lowering_records import (
        mark_source_pathology_nonreplay_lowering_rejections_nonblocking,
    )

    rejections = [{"blocking": True, "reason_code": "x", "reason": "y"}]
    changed = mark_source_pathology_nonreplay_lowering_rejections_nonblocking(
        source_pathology="not_an_out_of_scope_pathology",
        lowering_rejections=rejections,
        start_index=0,
    )
    assert not changed
    assert rejections[0]["blocking"] is True
    record = _uk_rejection_to_downgrade_record(rejections[0], was_blocking=True)
    assert not record.requires_witness  # stayed blocking -> no downgrade
