"""Shared timeline/materialization result carriers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Optional, get_args

from lawvm.contracts import ArtifactEnvelope, ProcessingStatus
from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE, OBSERVATION_ROLE
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
    "excluded_authority_context",
    "ambiguous_missing_scope",
    "equal_rank_same_source_selection_conflict",
    "empty_same_day_interval",
    "missing_operation_date",
    "missing_renumber_destination",
    "missing_renumber_source",
    "missing_insert_payload",
    "missing_replace_payload",
    "missing_replace_target",
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


MaterializationStatus = Literal[
    "materialized",
    "degraded_missing_scope",
    "degraded_timeline_issues",
]

_TIMELINE_ISSUE_KINDS = frozenset(get_args(TimelineIssueKind))
_MATERIALIZATION_STATUSES = frozenset(get_args(MaterializationStatus))
_MATERIALIZATION_QUERY_TYPES = frozenset({"governing", "in_force"})


@dataclass(frozen=True)
class MaterializationCertificate:
    """Positive certificate summarizing one PIT materialization decision."""

    as_of: str
    query_type: Literal["governing", "in_force"]
    territory: Optional[str] = None
    selected_address_count: int = 0
    ambiguous_address_count: int = 0
    required_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.as_of, str) or not self.as_of:
            raise ValueError("MaterializationCertificate.as_of must be a non-empty string")
        if self.query_type not in _MATERIALIZATION_QUERY_TYPES:
            raise ValueError("MaterializationCertificate.query_type is not supported")
        if self.territory is not None and not isinstance(self.territory, str):
            raise TypeError("MaterializationCertificate.territory must be a string or None")
        if not isinstance(self.selected_address_count, int) or isinstance(
            self.selected_address_count, bool
        ):
            raise TypeError("MaterializationCertificate.selected_address_count must be an integer")
        if not isinstance(self.ambiguous_address_count, int) or isinstance(
            self.ambiguous_address_count, bool
        ):
            raise TypeError("MaterializationCertificate.ambiguous_address_count must be an integer")
        if self.selected_address_count < 0:
            raise ValueError("MaterializationCertificate.selected_address_count must be non-negative")
        if self.ambiguous_address_count < 0:
            raise ValueError("MaterializationCertificate.ambiguous_address_count must be non-negative")
        object.__setattr__(self, "required_dimensions", tuple(self.required_dimensions))
        if any(not isinstance(dimension, str) or not dimension for dimension in self.required_dimensions):
            raise ValueError("MaterializationCertificate.required_dimensions must contain strings")


def _address_wire_path(address: Optional[LegalAddress]) -> tuple[dict[str, str], ...]:
    if address is None:
        return ()
    return tuple({"kind": kind, "label": label} for kind, label in address.path)


@dataclass(frozen=True)
class TimelineIssue:
    """Typed diagnostic emitted while compiling timelines."""

    kind: TimelineIssueKind
    message: str
    address: Optional[LegalAddress] = None
    source_statute: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _TIMELINE_ISSUE_KINDS:
            raise ValueError("TimelineIssue.kind is not supported")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("TimelineIssue.message must be a non-empty string")
        if self.address is not None and not isinstance(self.address, LegalAddress):
            raise TypeError("TimelineIssue.address must be LegalAddress or None")
        if not isinstance(self.source_statute, str):
            raise TypeError("TimelineIssue.source_statute must be a string")

    @property
    def rule_id(self) -> str:
        """Stable rule/finding identifier for persisted timeline evidence."""
        return f"timeline.{self.kind}"

    @property
    def phase(self) -> Literal["timeline"]:
        return "timeline"

    @property
    def blocking(self) -> bool:
        """Timeline issues represent unproven timeline execution in strict mode."""
        if self.kind == "empty_same_day_interval":
            return False
        return True

    @property
    def strict_disposition(self) -> Literal["block", "record"]:
        return "block" if self.blocking else "record"

    @property
    def quirks_disposition(self) -> Literal["record"]:
        return "record"

    def to_jsonable_dict(self) -> dict[str, object]:
        """Return the stable wire shape for this timeline issue."""
        return {
            "kind": self.kind,
            "rule_id": self.rule_id,
            "phase": self.phase,
            "message": self.message,
            "address": _address_wire_path(self.address),
            "source_statute": self.source_statute,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


def timeline_issue_to_finding(issue: TimelineIssue) -> Finding:
    """Project one timeline issue into the governed finding ledger shape.

    Timeline issues are execution diagnostics, not compile-time findings stored
    on ``CompileFacade``. Report/tool boundaries can use this projection when
    they need one unified evidence surface.
    """

    if issue.kind == "empty_same_day_interval":
        kind = "TIME.EMPTY_SAME_DAY_INTERVAL"
        role = OBSERVATION_ROLE
    else:
        kind = "TIME.TIMELINE_EXECUTION_ISSUE"
        role = OBLIGATION_ROLE
    return Finding(
        kind=kind,
        role=role,
        stage=issue.phase,
        source_statute=issue.source_statute,
        blocking=issue.blocking,
        detail=issue.to_jsonable_dict(),
    )


def timeline_issues_to_findings(issues: tuple[TimelineIssue, ...]) -> tuple[Finding, ...]:
    """Project timeline issues into findings, preserving issue order."""

    return tuple(timeline_issue_to_finding(issue) for issue in issues)


@dataclass(frozen=True)
class MaterializationResult:
    """Explicit PIT materialization result with degradation metadata."""

    status: MaterializationStatus
    statute: IRStatute
    required_dimensions: tuple[str, ...] = ()
    ambiguous_addresses: tuple[LegalAddress, ...] = ()
    issues: tuple[TimelineIssue, ...] = ()
    certificate: Optional[MaterializationCertificate] = None

    def __post_init__(self) -> None:
        if self.status not in _MATERIALIZATION_STATUSES:
            raise ValueError("MaterializationResult.status is not supported")
        if not isinstance(self.statute, IRStatute):
            raise TypeError("MaterializationResult.statute must be IRStatute")
        object.__setattr__(self, "required_dimensions", tuple(self.required_dimensions))
        if any(not isinstance(dimension, str) or not dimension for dimension in self.required_dimensions):
            raise ValueError("MaterializationResult.required_dimensions must contain strings")
        object.__setattr__(self, "ambiguous_addresses", tuple(self.ambiguous_addresses))
        if any(not isinstance(address, LegalAddress) for address in self.ambiguous_addresses):
            raise TypeError("MaterializationResult.ambiguous_addresses must contain LegalAddress")
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, TimelineIssue) for issue in self.issues):
            raise TypeError("MaterializationResult.issues must contain TimelineIssue")
        if self.certificate is not None and not isinstance(
            self.certificate, MaterializationCertificate
        ):
            raise TypeError("MaterializationResult.certificate must be MaterializationCertificate or None")

        blocking_issues = tuple(issue for issue in self.issues if issue.blocking)
        if self.status == "materialized" and blocking_issues:
            raise ValueError("MaterializationResult materialized status cannot carry blocking issues")
        if (
            self.status == "degraded_missing_scope"
            and not self.required_dimensions
            and not self.ambiguous_addresses
        ):
            raise ValueError("MaterializationResult degraded_missing_scope requires required_dimensions")
        if self.status == "degraded_timeline_issues" and not blocking_issues:
            raise ValueError("MaterializationResult degraded_timeline_issues requires blocking issues")
        if self.certificate is not None:
            if self.certificate.ambiguous_address_count != len(self.ambiguous_addresses):
                raise ValueError(
                    "MaterializationResult certificate ambiguous_address_count "
                    "must match ambiguous_addresses"
                )
            if self.certificate.required_dimensions != self.required_dimensions:
                raise ValueError(
                    "MaterializationResult certificate required_dimensions "
                    "must match result required_dimensions"
                )

    @property
    def is_degraded(self) -> bool:
        return self.status != "materialized"

    def to_wire_artifact(
        self,
        *,
        producer: str = "lawvm.core.timeline",
        version: str = "1",
    ) -> ArtifactEnvelope[dict[str, object]]:
        """Wrap PIT materialization metadata and issues in a durable artifact."""
        blockers = tuple(issue.rule_id for issue in self.issues if issue.blocking)
        status = ProcessingStatus(kind="partial", blockers=blockers) if blockers else ProcessingStatus(kind="complete")
        certificate_payload: Optional[dict[str, object]] = None
        if self.certificate is not None:
            certificate_payload = {
                "as_of": self.certificate.as_of,
                "query_type": self.certificate.query_type,
                "territory": self.certificate.territory,
                "selected_address_count": self.certificate.selected_address_count,
                "ambiguous_address_count": self.certificate.ambiguous_address_count,
                "required_dimensions": self.certificate.required_dimensions,
            }
        return ArtifactEnvelope(
            schema="lawvm.materialization_result",
            producer=producer,
            version=version,
            payload={
                "status": self.status,
                "statute_id": self.statute.statute_id,
                "required_dimensions": self.required_dimensions,
                "ambiguous_addresses": tuple(
                    _address_wire_path(address) for address in self.ambiguous_addresses
                ),
                "issues": tuple(issue.to_jsonable_dict() for issue in self.issues),
                "certificate": certificate_payload,
            },
            status=status,
        )


@dataclass(frozen=True)
class TimelineCompilationResult:
    """Explicit compile_timelines result with typed diagnostics."""

    timelines: Timelines
    issues: tuple[TimelineIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.timelines, Mapping):
            raise TypeError("TimelineCompilationResult.timelines must be a mapping")
        normalized_timelines: dict[LegalAddress, ProvisionTimeline] = {}
        for address, timeline in self.timelines.items():
            if not isinstance(address, LegalAddress):
                raise TypeError("TimelineCompilationResult.timelines keys must be LegalAddress")
            if not isinstance(timeline, ProvisionTimeline):
                raise TypeError(
                    "TimelineCompilationResult.timelines values must be ProvisionTimeline"
                )
            if timeline.address != address:
                raise ValueError(
                    "TimelineCompilationResult timeline address must match mapping key"
                )
            normalized_timelines[address] = timeline
        object.__setattr__(self, "timelines", MappingProxyType(normalized_timelines))
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, TimelineIssue) for issue in self.issues):
            raise TypeError("TimelineCompilationResult.issues must contain TimelineIssue")

    def to_wire_artifact(
        self,
        *,
        producer: str = "lawvm.core.timeline",
        version: str = "1",
    ) -> ArtifactEnvelope[dict[str, object]]:
        """Wrap timeline compilation metadata and issues in a durable artifact."""
        blockers = tuple(issue.rule_id for issue in self.issues if issue.blocking)
        status = ProcessingStatus(kind="partial", blockers=blockers) if blockers else ProcessingStatus(kind="complete")
        return ArtifactEnvelope(
            schema="lawvm.timeline_compilation_result",
            producer=producer,
            version=version,
            payload={
                "timelines_count": len(self.timelines),
                "issues": tuple(issue.to_jsonable_dict() for issue in self.issues),
            },
            status=status,
        )
