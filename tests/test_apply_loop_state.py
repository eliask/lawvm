"""Isolation tests for the resolved-op apply-fold state machine.

``ApplyGroupState`` threads the group-boundary bookkeeping that ``apply_ops_to_tree``
previously carried as four bare locals. These tests exercise the state machine in
isolation: group lifecycle (boundary detection, reset), the failed-apply replay
barrier, the live path-hint slot, and the per-op typed audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from lawvm.finland.apply_loop_state import ApplyGroupState, ApplyOpAudit
from lawvm.finland.ops import ResolvedGroupKeyView


@dataclass
class _StubRop:
    """Minimal stand-in for ResolvedOp: only the fields append_rop reads."""

    op_id: str = ""

    def description(self) -> str:
        return f"stub:{self.op_id}"


def _key(label: str, chapter: str | None = None) -> ResolvedGroupKeyView:
    return ResolvedGroupKeyView(
        unit_kind="section", target_norm=label, target_chapter=chapter, target_part=None
    )


def test_audit_rejects_unknown_disposition() -> None:
    with pytest.raises(ValueError, match="unknown apply disposition"):
        ApplyOpAudit(op_id="x", description="d", disposition="BOGUS")


def test_audit_accepts_each_known_disposition() -> None:
    for disp in ("APPLIED", "APPLY_FAILED", "NO_APPLY_PASS"):
        assert ApplyOpAudit(op_id="x", description="d", disposition=disp).disposition == disp


def test_fresh_state_opens_a_boundary_for_any_key() -> None:
    grp = ApplyGroupState()
    # prev_group_key is None → any real key opens a new group.
    assert grp.is_group_boundary(_key("1")) is True


def test_start_group_resets_per_group_accumulators() -> None:
    grp = ApplyGroupState()
    grp.start_group(_key("1"))
    grp.append_rop(_StubRop(op_id="a"), disposition="APPLIED")  # type: ignore[arg-type]
    grp.mark_failed_apply()
    grp.set_path_hint(((("section", "1")),))
    # New boundary resets ops/hint/failure, retains audit trail.
    assert grp.is_group_boundary(_key("2")) is True
    grp.start_group(_key("2"))
    assert grp.group_rops == []
    assert grp.group_path_hint is None
    assert grp.group_had_failed_apply is False
    # Same key is no longer a boundary.
    assert grp.is_group_boundary(_key("2")) is False


def test_failed_apply_is_a_sticky_group_barrier() -> None:
    grp = ApplyGroupState()
    grp.start_group(_key("1"))
    assert grp.group_had_failed_apply is False
    grp.mark_failed_apply()
    assert grp.group_had_failed_apply is True
    # Appending more ops does not clear the barrier.
    grp.append_rop(_StubRop(op_id="b"), disposition="APPLIED")  # type: ignore[arg-type]
    assert grp.group_had_failed_apply is True


def test_append_rop_records_one_audit_per_op() -> None:
    grp = ApplyGroupState()
    grp.start_group(_key("1"))
    grp.append_rop(_StubRop(op_id="a"), disposition="APPLIED")  # type: ignore[arg-type]
    grp.append_rop(_StubRop(op_id="b"), disposition="NO_APPLY_PASS")  # type: ignore[arg-type]
    grp.start_group(_key("2"))  # boundary does NOT discard the audit log
    grp.append_rop(_StubRop(op_id="c"), disposition="APPLY_FAILED")  # type: ignore[arg-type]
    assert [a.op_id for a in grp.audits] == ["a", "b", "c"]
    assert [a.disposition for a in grp.audits] == ["APPLIED", "NO_APPLY_PASS", "APPLY_FAILED"]
    assert grp.audits[0].description == "stub:a"
    # group_rops only holds the most recent group's ops.
    assert [r.op_id for r in grp.group_rops] == ["c"]


def test_set_path_hint_updates_live_slot() -> None:
    grp = ApplyGroupState()
    grp.start_group(_key("1"))
    hint = ((("section", "1")),)
    grp.set_path_hint(hint)
    assert grp.group_path_hint == hint
    grp.set_path_hint(None)
    assert grp.group_path_hint is None
