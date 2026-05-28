"""Shared provenance carriers for core timeline and replay surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, FrozenSet, Literal, Tuple

if TYPE_CHECKING:
    from lawvm.core.ir import LegalAddress
    from lawvm.core.mutation_boundary import TreePath


@dataclass(frozen=True)
class ExpiryOverride:
    """One link in a temporary amendment's expiry extension chain."""

    source_statute_id: str
    source_title: str = ""
    enacted: str = ""
    effective: str = ""
    new_expires: str = ""
    section_labels: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class OperationSource:
    """Provenance for a legal operation.

    This carrier records source-side timing and textual provenance. Executable
    temporal authority lives on `TemporalEvent` / `ProvisionVersion`, not here.
    """

    statute_id: str
    title: str = ""
    enacted: str = ""  # when the amending act was created
    effective: str = ""  # source-side effective date carried into lowering
    expires: str = ""  # source-side expiry provenance carried into lowering
    expires_original: str = ""  # original temporary-act expiry before extensions
    expiry_chain: Tuple[ExpiryOverride, ...] = ()  # audit trail of expiry overrides
    raw_text: str = ""  # original amendment language
    corrected_by: str = ""  # corrigendum ID that patched this source (e.g. "corr/984/2018/1")
    # UK commencement provenance: text-writing act vs force-activating SI
    commencement_source: str = ""  # SI/order that brings this into force
    commencement_title: str = ""  # title of the commencement instrument


@dataclass(frozen=True)
class MigrationEvent:
    """Address continuity through an explicit migration event."""

    event_id: str
    kind: Literal["renumber", "move", "split", "merge"]
    from_address: "LegalAddress"
    to_address: "LegalAddress"
    effective: str = ""
    source_statute: str = ""
    witness: object | None = None


def migration_event_sort_key(
    event: MigrationEvent,
) -> tuple[str, int, TreePath, TreePath, str, str]:
    """Return the deterministic canonical ordering key for lineage waves."""
    return (
        event.effective,
        len(event.from_address.path),
        event.from_address.path,
        event.to_address.path,
        event.source_statute,
        event.event_id,
    )
