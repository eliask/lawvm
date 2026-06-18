"""Finland identity-ledger helpers (prefix migration queries on frozen events)."""

from __future__ import annotations

from lawvm.core.identity_ledger import IdentityLedger
from lawvm.core.ir import LegalAddress
from lawvm.finland.migration_ledger import (
    MigrationLedger,
    current_address_with_prefix_migrations_from_events,
)


def identity_ledger_from_migration_ledger(ledger: MigrationLedger) -> IdentityLedger:
    """Snapshot a mutable replay ledger into a frozen :class:`IdentityLedger`."""
    return IdentityLedger.from_events(ledger.events)


def current_address_with_prefix_migrations(
    ledger: IdentityLedger,
    original_address: LegalAddress,
    as_of_date: str = "",
    not_before: str = "",
) -> LegalAddress:
    """Follow Finland prefix/wave migrations on a frozen identity ledger."""
    return current_address_with_prefix_migrations_from_events(
        original_address,
        ledger.events,
        as_of_date,
        not_before=not_before,
    )


__all__ = [
    "IdentityLedger",
    "current_address_with_prefix_migrations",
    "identity_ledger_from_migration_ledger",
]
