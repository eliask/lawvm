"""Shared provenance carriers for core timeline and replay surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, FrozenSet, Literal, Tuple

from lawvm.core.authority import AuthorityLayer, BranchContext, COMMENCED_STATUS, ENACTED_AUTHORITY, LegalStatus

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

    def __post_init__(self) -> None:
        labels = frozenset(self.section_labels)
        if not all(isinstance(label, str) for label in labels):
            raise ValueError("ExpiryOverride.section_labels must contain strings")
        object.__setattr__(self, "section_labels", labels)


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
    # Authority/branch provenance: default empty branch is enacted/current law.
    authority_layer: AuthorityLayer = ENACTED_AUTHORITY
    legal_status: LegalStatus = COMMENCED_STATUS
    branch_id: str = ""
    scenario_id: str = ""

    def __post_init__(self) -> None:
        expiry_chain = tuple(self.expiry_chain)
        if not all(isinstance(override, ExpiryOverride) for override in expiry_chain):
            raise ValueError("OperationSource.expiry_chain must contain ExpiryOverride records")
        object.__setattr__(self, "expiry_chain", expiry_chain)
        BranchContext(
            authority_layer=self.authority_layer,
            legal_status=self.legal_status,
            branch_id=self.branch_id,
            scenario_id=self.scenario_id,
        )


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

    def __post_init__(self) -> None:
        from lawvm.core.ir import LegalAddress  # noqa: PLC0415

        if not self.event_id:
            raise ValueError("MigrationEvent.event_id must be non-empty")
        if self.kind not in {"renumber", "move", "split", "merge"}:
            raise ValueError(f"unsupported MigrationEvent.kind: {self.kind!r}")
        if not isinstance(self.from_address, LegalAddress):
            raise ValueError("MigrationEvent.from_address must be a LegalAddress")
        if not isinstance(self.to_address, LegalAddress):
            raise ValueError("MigrationEvent.to_address must be a LegalAddress")


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
