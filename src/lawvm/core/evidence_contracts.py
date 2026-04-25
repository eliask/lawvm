"""Reserved evidence/proof artifact contracts.

These dataclasses are kept as candidate wire/reporting contracts for future
cross-jurisdiction evidence export, but they are not a live shared surface
today. There are currently no production importers under ``src/lawvm/``; the
only direct consumer is the shared-contract shape test.

API tier
--------
Reserved contract header. Stable enough to keep around, but not a currently
adopted cross-cutting runtime surface.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class EvidenceSummary:
    """Reserved evidence/proof bundle summary shape."""

    jurisdiction: str
    base_id: str
    primary_tier: str = ""
    status: str = "ok"
    error: str | None = None
    claim_count: int = 0
    divergence_count: int = 0
    actionable_count: int = 0
    unresolved_count: int = 0
    tiers: tuple[str, ...] = ()
    claim_kinds: tuple[str, ...] = ()
    trigger_sources: tuple[str, ...] = ()
    artifact_families: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data
