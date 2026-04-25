"""Tests for the replay occupancy model (OccupancyClass, SlotIdentity, SlotState,
validate_transition).

Covers valid and invalid occupancy transitions as defined in the replay
constitution (LAWVM_CONSTITUTION.md §4).
"""

from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    OccupancyClass,
    SlotIdentity,
    SlotState,
    VALID_TRANSITIONS,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(label: str = "6", kind: str = "section") -> SlotIdentity:
    return SlotIdentity(parent_path=(), kind=kind, label=label)


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


def test_replace_substantive_stays_substantive():
    """replace on SUBSTANTIVE yields SUBSTANTIVE (content update)."""
    result = validate_transition(OccupancyAction.REPLACE, OccupancyClass.SUBSTANTIVE)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_absent_yields_substantive():
    """insert on ABSENT yields SUBSTANTIVE (new content)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.ABSENT)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_tombstone_yields_substantive():
    """insert on TOMBSTONE yields SUBSTANTIVE (reenactment)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.TOMBSTONE)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_scaffold_yields_substantive():
    """insert on SCAFFOLD yields SUBSTANTIVE (compatibility reenactment)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.SCAFFOLD)
    assert result == OccupancyClass.SUBSTANTIVE


def test_repeal_substantive_yields_tombstone():
    """repeal on SUBSTANTIVE yields TOMBSTONE (preserves addressability)."""
    result = validate_transition(OccupancyAction.REPEAL, OccupancyClass.SUBSTANTIVE)
    assert result == OccupancyClass.TOMBSTONE


def test_repeal_tombstone_is_idempotent() -> None:
    """repeal on TOMBSTONE stays TOMBSTONE (idempotent repeal)."""
    result = validate_transition(OccupancyAction.REPEAL, OccupancyClass.TOMBSTONE)
    assert result == OccupancyClass.TOMBSTONE


def test_occupancy_action_is_value_stringified_not_string_comparable() -> None:
    assert str(OccupancyAction.REPLACE) == "replace"
    assert OccupancyAction.REPLACE != "replace"


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


def test_replace_absent_is_invalid():
    """replace on ABSENT is not valid — slot does not exist yet."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPLACE, OccupancyClass.ABSENT)


def test_replace_tombstone_is_invalid():
    """replace on TOMBSTONE is not valid — tombstone must be reenacted first."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPLACE, OccupancyClass.TOMBSTONE)


def test_repeal_absent_is_invalid():
    """repeal on ABSENT is not valid — cannot repeal something that never existed."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPEAL, OccupancyClass.ABSENT)


def test_unknown_action_is_invalid():
    """Unknown action enum raises InvalidOccupancyTransition."""

    # Create a fake OccupancyAction-like object with unknown value
    class FakeAction:
        value = "frobnicate"

    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(cast(Any, FakeAction()), OccupancyClass.SUBSTANTIVE)


# ---------------------------------------------------------------------------
# SlotIdentity and SlotState construction
# ---------------------------------------------------------------------------


def test_slot_identity_is_frozen():
    """SlotIdentity is immutable (frozen dataclass)."""
    identity = _identity()
    with pytest.raises(Exception):
        cast(Any, identity).label = "7"


def test_slot_state_carries_tombstone_text():
    """SlotState can carry tombstone text for display."""
    identity = _identity("82 a")
    state = SlotState(
        identity=identity,
        occupancy=OccupancyClass.TOMBSTONE,
        last_modified_by="2020/766",
        tombstone_text="82 a § on kumottu L:lla 13.11.2020/766",
    )
    assert state.occupancy == OccupancyClass.TOMBSTONE
    assert "kumottu" in (state.tombstone_text or "")


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS coverage
# ---------------------------------------------------------------------------


def test_valid_transitions_table_has_six_entries():
    """The canonical valid transitions table has exactly the documented cases."""
    assert len(VALID_TRANSITIONS) == 6
