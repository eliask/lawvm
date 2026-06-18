"""Immutable identity ledger over migration events (read-only lineage carrier).

Runtime replay accumulates :class:`~lawvm.core.provenance.MigrationEvent`
records in the mutable Finland :class:`~lawvm.finland.migration_ledger.MigrationLedger`.
This module owns the **frozen post-replay snapshot** used for audit, evidence
projection, and cross-frontend lineage queries without mutating replay state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from lawvm.core.ir import LegalAddress
from lawvm.core.provenance import MigrationEvent
from lawvm.core.timeline import current_address_from_migration_events


@dataclass(frozen=True, slots=True)
class IdentityLedger:
    """Read-only lineage ledger: a frozen tuple of migration events."""

    events: tuple[MigrationEvent, ...] = ()

    @classmethod
    def from_events(cls, events: Iterable[MigrationEvent]) -> IdentityLedger:
        return cls(events=tuple(events))

    def __len__(self) -> int:
        return len(self.events)

    def __bool__(self) -> bool:
        return bool(self.events)

    def query_lineage(self, address: LegalAddress) -> tuple[MigrationEvent, ...]:
        """Return events where *address* appears as source or destination."""
        return tuple(
            event
            for event in self.events
            if event.from_address == address or event.to_address == address
        )

    def current_address(
        self,
        original_address: LegalAddress,
        as_of_date: str = "",
    ) -> LegalAddress:
        """Follow renumber/move links forward from *original_address*."""
        return current_address_from_migration_events(
            original_address,
            self.events,
            as_of_date=as_of_date,
        )


__all__ = ["IdentityLedger"]
