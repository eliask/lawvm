"""Tests for the core IdentityLedger read-only lineage carrier."""

from __future__ import annotations

from lawvm.core.identity_ledger import IdentityLedger
from lawvm.core.ir import LegalAddress
from lawvm.core.provenance import MigrationEvent
from lawvm.finland.migration_ledger import (
    MigrationLedger,
    current_address_with_prefix_migrations_from_events,
)


def identity_ledger_from_migration_ledger(ledger: MigrationLedger) -> IdentityLedger:
    """Snapshot a mutable replay ledger into a frozen IdentityLedger."""
    return IdentityLedger.from_events(ledger.events)


def _addr(*segments: tuple[str, str]) -> LegalAddress:
    return LegalAddress(path=segments)


def test_identity_ledger_from_events_is_immutable_snapshot() -> None:
    ledger = MigrationLedger()
    ledger.record_renumber(
        _addr(("section", "5")),
        _addr(("section", "6")),
        effective="2020-01-01",
        source_statute="2020/100",
    )
    identity = identity_ledger_from_migration_ledger(ledger)
    assert len(identity) == 1
    assert identity.events[0].kind == "renumber"


def test_identity_ledger_current_address_follows_chain() -> None:
    event = MigrationEvent(
        event_id="mig:test",
        kind="renumber",
        from_address=_addr(("section", "5")),
        to_address=_addr(("section", "6")),
        effective="2020-01-01",
        source_statute="2020/100",
    )
    identity = IdentityLedger.from_events((event,))
    resolved = identity.current_address(_addr(("section", "5")))
    assert resolved == _addr(("section", "6"))


def test_finland_prefix_migration_on_identity_ledger() -> None:
    ledger = MigrationLedger()
    ledger.record_renumber(
        _addr(("part", "III")),
        _addr(("part", "IV")),
        effective="2019-01-01",
        source_statute="2019/50",
    )
    identity = identity_ledger_from_migration_ledger(ledger)
    resolved = current_address_with_prefix_migrations_from_events(
        _addr(("part", "III"), ("chapter", "2"), ("section", "10")),
        identity.events,
        "2020-01-01",
    )
    assert resolved.path[0] == ("part", "4")
