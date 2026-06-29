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
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.stage_result import (
    EMPTY_EVIDENCE,
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    Residual,
    StageResult,
)

Timelines = dict[LegalAddress, ProvisionTimeline]
_LINEAGE_PLAN_MODES = frozenset(
    {"raw_with_migrations", "rekeyed_with_migrations", "rekeyed_only"}
)
_LINEAGE_TIMELINE_SOURCES = frozenset({"raw", "rekeyed"})


def _normalize_timelines(timelines: Mapping[LegalAddress, ProvisionTimeline]) -> Mapping[LegalAddress, ProvisionTimeline]:
    if not isinstance(timelines, Mapping):
        raise TypeError("timelines must be a mapping")
    normalized: dict[LegalAddress, ProvisionTimeline] = {}
    for address, timeline in timelines.items():
        if not isinstance(address, LegalAddress):
            raise TypeError("timeline keys must be LegalAddress")
        if not isinstance(timeline, ProvisionTimeline):
            raise TypeError("timeline values must be ProvisionTimeline")
        if timeline.address != address:
            raise ValueError("timeline address must match mapping key")
        normalized[address] = timeline
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class MaterializationLineagePlan:
    """Typed PIT lineage plan chosen by a producer or caller."""

    mode: Literal["raw_with_migrations", "rekeyed_with_migrations", "rekeyed_only"]
    migration_events: tuple[MigrationEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in _LINEAGE_PLAN_MODES:
            raise ValueError("MaterializationLineagePlan.mode is not supported")
        object.__setattr__(self, "migration_events", tuple(self.migration_events))
        if any(not isinstance(event, MigrationEvent) for event in self.migration_events):
            raise TypeError(
                "MaterializationLineagePlan.migration_events must contain MigrationEvent"
            )
        # D2 APPLY.LINEAGE_DAG_ACYCLICITY (§1.6 unstated migration / §2.8 lineage
        # identity): a cycle in the migration graph breaks materialization
        # convergence (`lineage_segments` would silently traverse it forever, only
        # masked by a visited-set). Reject at typed-carrier construction so the
        # production path (`materialize_pit_ex` builds the plan) surfaces the
        # invariant before quiet non-termination.
        try:
            assert_acyclic(self.migration_events)
        except LineageCycleError as exc:
            raise ValueError(
                "MaterializationLineagePlan.migration_events form a directed cycle; "
                "lineage resolution would not terminate. "
                f"Cycle events: {tuple(ev.event_id for ev in exc.cycle_events)}"
            ) from exc


class LineageCycleError(Exception):
    """Raised when migration events form a directed cycle in the lineage graph.

    A back-edge in ``from_address -> to_address`` edges means
    :func:`lineage_segments` resolution would not terminate. The audit
    discriminates a true cycle (a directed back-edge through the resolved
    graph) from reflexive ``from == to`` events, which are benign identity
    no-ops.
    """

    cycle_events: tuple[MigrationEvent, ...]

    def __init__(self, cycle_events: tuple[MigrationEvent, ...]) -> None:
        if not isinstance(cycle_events, tuple):
            raise TypeError("LineageCycleError.cycle_events must be a tuple")
        if not cycle_events:
            raise ValueError("LineageCycleError.cycle_events must be non-empty")
        if any(not isinstance(ev, MigrationEvent) for ev in cycle_events):
            raise TypeError("LineageCycleError.cycle_events must contain MigrationEvent")
        object.__setattr__(self, "cycle_events", cycle_events)
        super().__init__(
            "lineage migration events form a directed cycle: "
            + " -> ".join(ev.event_id for ev in cycle_events)
        )


def assert_acyclic(migration_events: tuple[MigrationEvent, ...]) -> None:
    """Raise :class:`LineageCycleError` if the migration-event graph has a cycle.

    Nodes are canonical :class:`LegalAddress` values keyed by their string form;
    edges are non-degenerate ``MigrationEvent`` instances where
    ``from_address != to_address``.  Reflexive events (``from == to``) are
    ignored: a renumber-to-self is a benign identity no-op, not a cycle.

    Implements §1.6 (no unstated migration) and §2.8 (lineage/identity).  A
    cycle in the migration graph would make :func:`lineage_segments` resolution
    non-terminating without this guard; the typed carrier
    :class:`MaterializationLineagePlan` calls this in ``__post_init__`` so any
    production caller that builds a lineage plan fails loud at construction.
    """
    adjacency: dict[str, list[MigrationEvent]] = {}
    for event in migration_events:
        if event.from_address == event.to_address:
            continue  # reflexive: benign identity no-op
        adjacency.setdefault(str(event.from_address), []).append(event)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    parent_edge: dict[str, MigrationEvent] = {}

    def _visit(root: str) -> tuple[MigrationEvent, ...] | None:
        # Iterative DFS; returns cycle events in chronological order if found.
        stack: list[tuple[str, int]] = [(root, 0)]
        color[root] = GRAY
        while stack:
            node, idx = stack[-1]
            edges = adjacency.get(node, [])
            if idx < len(edges):
                stack[-1] = (node, idx + 1)
                edge = edges[idx]
                target = str(edge.to_address)
                target_color = color.get(target, WHITE)
                if target_color == GRAY:
                    # Back-edge: cycle.  Walk parent_edge back from `node` to
                    # `target`, collecting the cycle edges in chronological
                    # order: [target->...->node, node->target].
                    cycle: list[MigrationEvent] = []
                    walker = node
                    while walker != target:
                        p_edge = parent_edge.get(walker)
                        if p_edge is None:
                            break  # safety: should not happen, but don't hang
                        cycle.append(p_edge)
                        walker = str(p_edge.from_address)
                    cycle.append(edge)  # closing back-edge
                    return tuple(cycle)
                if target_color == WHITE and target in adjacency:
                    color[target] = GRAY
                    parent_edge[target] = edge
                    stack.append((target, 0))
            else:
                color[node] = BLACK
                stack.pop()
        return None

    for root in adjacency:
        if color.get(root, WHITE) != WHITE:
            continue
        cycle = _visit(root)
        if cycle is not None:
            raise LineageCycleError(cycle)


@dataclass(frozen=True)
class MaterializationLineageDecision:
    """Typed PIT lineage decision coupling timeline source and execution plan."""

    timelines: Timelines
    timeline_source: Literal["raw", "rekeyed"]
    lineage_plan: MaterializationLineagePlan
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "timelines", _normalize_timelines(self.timelines))
        if self.timeline_source not in _LINEAGE_TIMELINE_SOURCES:
            raise ValueError("MaterializationLineageDecision.timeline_source is not supported")
        if not isinstance(self.lineage_plan, MaterializationLineagePlan):
            raise TypeError(
                "MaterializationLineageDecision.lineage_plan must be MaterializationLineagePlan"
            )
        if not isinstance(self.reason, str):
            raise TypeError("MaterializationLineageDecision.reason must be a string")

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
    "duplicate_selected_child_replaced_by_exact_child_overlay",
    "duplicate_same_label_child_valid_temporal_overlay",
    "duplicate_same_label_child_migration_collision",
    "duplicate_same_label_child_carried_continuity",
    "duplicate_same_label_child_stale_source_shadow",
    "duplicate_same_label_child_unresolved",
    # D7 / LS-23 COMMENCEMENT.EFFECT_TOTALITY: an op reached compile-timelines
    # without a matching commence/revive TemporalEvent and without a
    # pending/unresolved/manual-frontier classification. Mirrors the
    # registry code COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION; surfaced
    # as a TimelineIssue here so a compile_timelines consumer that does not
    # route PhaseResult.Observations still sees it on the issue_sink.
    "commencement_op_without_temporal_authorization",
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
class MaterializationCoverage:
    """Positive certificate summarizing one PIT materialization decision."""

    as_of: str
    query_type: Literal["governing", "in_force"]
    territory: Optional[str] = None
    selected_address_count: int = 0
    ambiguous_address_count: int = 0
    required_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.as_of, str) or not self.as_of:
            raise ValueError("MaterializationCoverage.as_of must be a non-empty string")
        if self.query_type not in _MATERIALIZATION_QUERY_TYPES:
            raise ValueError("MaterializationCoverage.query_type is not supported")
        if self.territory is not None and not isinstance(self.territory, str):
            raise TypeError("MaterializationCoverage.territory must be a string or None")
        if not isinstance(self.selected_address_count, int) or isinstance(
            self.selected_address_count, bool
        ):
            raise TypeError("MaterializationCoverage.selected_address_count must be an integer")
        if not isinstance(self.ambiguous_address_count, int) or isinstance(
            self.ambiguous_address_count, bool
        ):
            raise TypeError("MaterializationCoverage.ambiguous_address_count must be an integer")
        if self.selected_address_count < 0:
            raise ValueError("MaterializationCoverage.selected_address_count must be non-negative")
        if self.ambiguous_address_count < 0:
            raise ValueError("MaterializationCoverage.ambiguous_address_count must be non-negative")
        object.__setattr__(self, "required_dimensions", tuple(self.required_dimensions))
        if any(not isinstance(dimension, str) or not dimension for dimension in self.required_dimensions):
            raise ValueError("MaterializationCoverage.required_dimensions must contain strings")


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
    def quirks_disposition(self) -> QuirksDisposition:
        return QuirksDisposition.RECORD

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


@dataclass(frozen=True, slots=True)
class TombstoneRecord:
    """Typed tombstone for an address whose selected PIT version is a sourced repeal.

    A tombstone is a materialization-time absence with an underlying repeal op:
    the address's selected version at the PIT ``as_of`` carries ``content=None``
    (the silent placeholder) AND a sourced repeal (``version.source`` is set, so
    the absence traces to a statute — never a base-state artifact). The IR tree
    produced by :func:`materialize_pit` drops such an address silently; this
    carrier surfaces each dropped address with metadata so a projection surface
    (``lawvm show`` / ``lawvm dump``) can render the tombstone inline at its
    target position rather than letting over-repeal stay invisible
    (AGENTS.md §0 — over-repeal visibility).

    The carrier is *evidence*, not legal state: surfacing a tombstone here never
    re-mints the address in the IR tree. ``op_id`` defaults to empty because the
    op identity rides the :class:`LegalOperation` stream above the timeline
    waist; the timeline owner sees ``ProvisionVersion.source``.
    """

    address: LegalAddress
    kind: str
    label: str
    source_statute: str
    effective: str = ""
    enacted: str = ""
    variant_kind: Literal["permanent", "temporary"] = "permanent"
    op_id: str = ""

    def __post_init__(self) -> None:
        if not self.address.path:
            raise ValueError("TombstoneRecord.address must have a non-empty path")
        if not self.kind:
            raise ValueError("TombstoneRecord.kind must be non-empty")
        if not self.label:
            raise ValueError("TombstoneRecord.label must be non-empty")
        if not self.source_statute:
            raise ValueError("TombstoneRecord.source_statute must be non-empty")
        if self.variant_kind not in {"permanent", "temporary"}:
            raise ValueError(
                "TombstoneRecord.variant_kind must be 'permanent' or 'temporary'"
            )


@dataclass(frozen=True)
class MaterializationResult:
    """Explicit PIT materialization result with degradation metadata."""

    materialization_status: MaterializationStatus
    statute: IRStatute
    required_dimensions: tuple[str, ...] = ()
    ambiguous_addresses: tuple[LegalAddress, ...] = ()
    issues: tuple[TimelineIssue, ...] = ()
    certificate: Optional[MaterializationCoverage] = None
    # Sourced-repeal tombstones dropped from the materialized IR tree. Each
    # record is an address whose selected version at PIT ``as_of`` carries
    # ``content=None`` and a sourced repeal (AGENTS.md §0 — over-repeal
    # visibility). Surfaced as additive evidence so projections can render the
    # tombstone inline at the target address's position; never re-mints the
    # address in the IR tree.
    tombstones: tuple["TombstoneRecord", ...] = ()

    def __post_init__(self) -> None:
        if self.materialization_status not in _MATERIALIZATION_STATUSES:
            raise ValueError("MaterializationResult.materialization_status is not supported")
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
            self.certificate, MaterializationCoverage
        ):
            raise TypeError("MaterializationResult.certificate must be MaterializationCoverage or None")
        object.__setattr__(self, "tombstones", tuple(self.tombstones))
        if any(not isinstance(tomb, TombstoneRecord) for tomb in self.tombstones):
            raise TypeError("MaterializationResult.tombstones must contain TombstoneRecord")

        blocking_issues = tuple(issue for issue in self.issues if issue.blocking)
        if self.materialization_status == "materialized" and blocking_issues:
            raise ValueError("MaterializationResult materialized status cannot carry blocking issues")
        if (
            self.materialization_status == "degraded_missing_scope"
            and not self.required_dimensions
            and not self.ambiguous_addresses
        ):
            raise ValueError("MaterializationResult degraded_missing_scope requires required_dimensions")
        if self.materialization_status == "degraded_timeline_issues" and not blocking_issues:
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
        return self.materialization_status != "materialized"

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
                "materialization_status": self.materialization_status,
                "statute_id": self.statute.statute_id,
                "required_dimensions": self.required_dimensions,
                "ambiguous_addresses": tuple(
                    _address_wire_path(address) for address in self.ambiguous_addresses
                ),
                "issues": tuple(issue.to_jsonable_dict() for issue in self.issues),
                "certificate": certificate_payload,
            },
            processing_status=status,
        )


def materialization_result_to_stage_account(
    result: MaterializationResult,
) -> StageResult[IRStatute]:
    """Project a :class:`MaterializationResult` onto the canonical stage contract.

    This is the StageResult-endgame adapter for the timeline/materialization
    waist (program spine ``notes_internal/STAGERESULT_ENDGAME.md``). The PIT
    materialization already computes the rich account
    (:class:`MaterializationCoverage` + typed :class:`TimelineIssue` issues +
    ambiguous addresses); this surfaces that account on a
    ``StageResult[IRStatute]`` instead of discarding everything but
    ``result.statute``.

    The four-class coverage partition (``unit="addresses"``):

      * ``owned`` = selected (cleanly materialized) addresses;
      * ``residual`` = ambiguous addresses (a typed frontier, not a failure);
      * ``violation`` = BLOCKING timeline issues + (1 when the status is
        ``degraded_missing_scope``) — the genuine unowned-signal failure class.
        A ``degraded_missing_scope`` status (which carries ``required_dimensions``)
        is itself a violation: a PIT selection the engine could not resolve
        without explicit scope.
      * ``total`` = ``owned + residual + violation`` so the four classes
        partition exactly (``is_partition`` holds); ``totality_claimed`` is True.

    The blocking signal is carried REDUNDANTLY in ``coverage.violation`` (the
    branch the certificate dossier asserts) AND a blocking ``unowned_violation``
    residual, so a consumer can branch on either without re-deriving it.

    Residuals: one blocking ``unowned_violation`` residual when the status is
    ``degraded_missing_scope`` (so the incompleteness forbids a clean claim, per
    the §LEDGER requirement), plus one non-blocking ``typed_residual`` per
    ambiguous address (the tag-don't-guess frontier). Findings reuse the ready
    :func:`timeline_issues_to_findings` projection. Evidence/authority stay at
    the identity defaults — materialization mints no new source identity and
    carries no replay authority (Pro §3.A / §8 authority firewall).
    """

    certificate = result.certificate
    selected = certificate.selected_address_count if certificate is not None else 0
    ambiguous = len(result.ambiguous_addresses)
    blocking_issue_count = sum(1 for issue in result.issues if issue.blocking)
    degraded_missing_scope = result.materialization_status == "degraded_missing_scope"
    # A missing-scope degradation is itself an unowned-signal violation even when
    # it emits no blocking timeline issue (the missing dimension is the signal).
    violation = blocking_issue_count + (1 if degraded_missing_scope else 0)

    coverage = CoverageCertificate(
        unit="addresses",
        total=selected + ambiguous + violation,
        owned=selected,
        residual=ambiguous,
        violation=violation,
        totality_claimed=True,
    )

    residuals: list[Residual] = []
    if degraded_missing_scope:
        residuals.append(
            Residual(
                kind="unowned_violation",
                reason="materialization_degraded_missing_scope",
                scope=result.statute.statute_id,
                text=(
                    "PIT selection degraded by missing scope: "
                    f"{', '.join(result.required_dimensions)}"
                ),
                blocking=True,
            )
        )
    for address in result.ambiguous_addresses:
        residuals.append(
            Residual(
                kind="typed_residual",
                reason="ambiguous_address",
                scope=str(address),
                blocking=False,
            )
        )

    return StageResult(
        value=result.statute,
        evidence=EMPTY_EVIDENCE,
        residuals=tuple(residuals),
        findings=timeline_issues_to_findings(result.issues),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )


@dataclass(frozen=True)
class TimelineCompilationResult:
    """Explicit compile_timelines result with typed diagnostics."""

    timelines: Timelines
    issues: tuple[TimelineIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "timelines", _normalize_timelines(self.timelines))
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
            processing_status=status,
        )
