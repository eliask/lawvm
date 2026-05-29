from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.provenance import MigrationEvent
from lawvm.core.timeline_lineage import (
    LineageSegment,
    MaterializationLineageBridgeClassification,
    ScopeMigrationClassification,
)


def _address(label: str = "1") -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _migration_event() -> MigrationEvent:
    return MigrationEvent(
        event_id="mig:test:1",
        kind="renumber",
        from_address=_address("1"),
        to_address=_address("2"),
        effective="2024-01-01",
    )


def test_lineage_segment_accepts_typed_addresses_and_event() -> None:
    segment = LineageSegment(
        from_address=_address("1"),
        to_address=_address("2"),
        event=_migration_event(),
    )

    assert segment.to_address == _address("2")


def test_lineage_segment_rejects_string_addresses() -> None:
    with pytest.raises(ValueError, match="from_address"):
        LineageSegment(
            from_address=cast(Any, "section:1"),
            to_address=_address("2"),
        )


def test_lineage_segment_rejects_untyped_event() -> None:
    with pytest.raises(ValueError, match="event"):
        LineageSegment(
            from_address=_address("1"),
            to_address=_address("2"),
            event=cast(Any, object()),
        )


def test_scope_migration_classification_rejects_non_boolean_flags() -> None:
    with pytest.raises(ValueError, match="noncolliding"):
        ScopeMigrationClassification(
            active_scope_changing=True,
            noncolliding=cast(Any, "yes"),
            destination_occupancy_collision=False,
        )


def test_materialization_lineage_bridge_classification_rejects_non_boolean_flags() -> None:
    with pytest.raises(ValueError, match="native_rebirth_after_renumber"):
        MaterializationLineageBridgeClassification(
            native_rebirth_after_renumber=cast(Any, "true"),
        )
