"""Shared timeline/materialization result carriers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline
from lawvm.core.provenance import MigrationEvent

Timelines = dict[LegalAddress, ProvisionTimeline]


@dataclass(frozen=True)
class MaterializationLineagePlan:
    """Typed PIT lineage plan chosen by a producer or caller."""

    mode: Literal["raw_with_migrations", "rekeyed_with_migrations", "rekeyed_only"]
    migration_events: tuple[MigrationEvent, ...] = ()


@dataclass(frozen=True)
class MaterializationLineageDecision:
    """Typed PIT lineage decision coupling timeline source and execution plan."""

    timelines: Timelines
    timeline_source: Literal["raw", "rekeyed"]
    lineage_plan: MaterializationLineagePlan
    reason: str = ""

TimelineIssueKind = Literal[
    "ambiguous_suffix",
    "ambiguous_suffix_prefix",
    "temporal_authority_source_expires",
    "temporal_event_not_matched",
    "unsupported_applicability_dimension",
    "skipped_contingent_unresolved",
    "ambiguous_missing_scope",
    "missing_operation_date",
    "missing_renumber_destination",
    "missing_renumber_source",
    "missing_insert_payload",
    "missing_replace_payload",
    "unsupported_facet_target",
    "unsupported_text_action",
    "duplicate_normalized_sibling_override",
    "duplicate_base_address_descendant_overlay",
    "duplicate_selected_address_descendant_overlay",
    "duplicate_same_label_child_valid_temporal_overlay",
    "duplicate_same_label_child_migration_collision",
    "duplicate_same_label_child_carried_continuity",
    "duplicate_same_label_child_stale_source_shadow",
    "duplicate_same_label_child_unresolved",
]


@dataclass(frozen=True)
class MaterializationCertificate:
    """Positive certificate summarizing one PIT materialization decision."""

    as_of: str
    query_type: Literal["governing", "in_force"]
    territory: Optional[str] = None
    selected_address_count: int = 0
    ambiguous_address_count: int = 0
    required_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimelineIssue:
    """Typed diagnostic emitted while compiling timelines."""

    kind: TimelineIssueKind
    message: str
    address: Optional[LegalAddress] = None
    source_statute: str = ""


@dataclass(frozen=True)
class MaterializationResult:
    """Explicit PIT materialization result with degradation metadata."""

    status: Literal["materialized", "degraded_missing_scope"]
    statute: IRStatute
    required_dimensions: tuple[str, ...] = ()
    ambiguous_addresses: tuple[LegalAddress, ...] = ()
    issues: tuple[TimelineIssue, ...] = ()
    certificate: Optional[MaterializationCertificate] = None

    @property
    def is_degraded(self) -> bool:
        return self.status != "materialized"


@dataclass(frozen=True)
class TimelineCompilationResult:
    """Explicit compile_timelines result with typed diagnostics."""

    timelines: Timelines
    issues: tuple[TimelineIssue, ...] = ()
