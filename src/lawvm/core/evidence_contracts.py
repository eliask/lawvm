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
from enum import Enum
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


class CorpusRowStatus(Enum):
    """Cross-frontend corpus operation/effect row disposition."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    MATCHED = "matched"
    DIVERGED = "diverged"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CorpusOperationEvidenceRow:
    """Minimal shared operation/effect row for corpus evidence exports."""

    row_id: str
    frontend_id: str
    source_artifact_id: str
    source_unit_id: str = ""
    source_locator: str = ""
    effect_family: str = ""
    canonical_family: str = ""
    original_target: str = ""
    resolved_target: str = ""
    status: CorpusRowStatus = CorpusRowStatus.ACCEPTED
    blocking: bool = False
    strict_disposition: str = ""
    quirks_disposition: str = ""
    finding_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class CorpusFindingEvidenceRow:
    """Minimal shared finding row for corpus evidence exports."""

    finding_id: str
    frontend_id: str
    family: str
    rule_id: str
    phase: str
    message: str
    source_artifact_id: str = ""
    source_unit_id: str = ""
    related_row_ids: tuple[str, ...] = ()
    blocking: bool = False
    strict_disposition: str = ""
    quirks_disposition: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = dict(self.evidence)
        return data
