"""PhaseResult — typed wrapper for pipeline stage outputs in the PRO_RESPONSE architecture.

## Role in the pipeline

LawVM's compilation pipeline is composed of discrete stages that were earlier
implemented through mutable list-passing signatures:

    adjudications_out          → earlier out-parameter for findings/obligations
    elaboration_observations_out → informational signals from elaboration
    source_pathologies_out     → source-quality findings from apply
    compiled_ops_out           → intermediate op dicts from frontend

This pattern works but is invisible to type-checkers and makes it hard to
reason about what a stage produces.  PhaseResult replaces out-parameters with
a typed return value that carries the primary output *plus* all side-channel
signals as first-class fields.

## Mapping from current out-parameters

    adjudications_out              → PhaseResult.findings() filtered by role
                                     (blocking=True for hard stops,
                                      blocking=False for visible-but-soft)
    elaboration_observations_out   → PhaseResult.findings() filtered by role
                                     (kind = observation kind string)
    source_pathologies_out         → PhaseResult.findings() filtered by role
                                     (kind = "ELAB.SOURCE_PATHOLOGY")
    compiled_ops_out               → part of PhaseResult.output
                                     (wrapped in a typed container alongside
                                      canonical ops, failures, etc.)

## Adoption strategy

PhaseResult is purely additive.  Existing stages keep their current
signatures; new stages and refactored stages return PhaseResult.  A caller
that receives a PhaseResult can:

    result = some_stage(...)
    if result.has_blocking:
        # surface obligations before continuing
        ...
    do_next_thing(result.output)

The ``merge`` method supports cascading stages:

    a = stage_one(...)
    b = stage_two(a.output, ...)
    combined = a.merge(b)          # combined.output == b.output
                                   # combined.findings() by role == a + b

API tier
--------
Internal stage-boundary contract. This is part of the core pipeline surface,
but not the preferred persisted/public dossier shape for downstream consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Iterable, List, Mapping, Tuple, TypeVar

import icontract

from lawvm.core.observation_registry import (
    FindingRole,
    get_finding_spec,
    validate_finding_projection,
)
from lawvm.core.event_summaries import (
    count_events_with_activation_rules,
    count_events_with_source,
    distinct_activation_rule_kinds,
    distinct_event_kinds,
)

if TYPE_CHECKING:
    from lawvm.core.provenance import MigrationEvent
    from lawvm.core.temporal import TemporalEvent


T = TypeVar("T")

OBSERVATION_ROLE: FindingRole = "observation"
OBLIGATION_ROLE: FindingRole = "obligation"
VIOLATION_ROLE: FindingRole = "violation"


@dataclass(frozen=True)
class Observation:
    """Something noticed during compilation — informational, not blocking.

    Observations accumulate across pipeline stages and are surfaced through the
    finding ledger / facade projections for reporting. They never prevent
    output from being produced.

    Common kinds (all registered in observation_registry.FINDING_REGISTRY):
        "PARSE.DUPLICATE_TARGET_OP"          — two ops address the same provision
        "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" — move/renumber op collapsed to same label
        "LOWER.CONTEXT_DEPENDENT_ANCHOR"     — op depends on chapter-scope carry-forward
        "LOWER.SCOPE_CARRY_FORWARD"          — chapter_scope_carry_forward hint present
        "ELAB.SOURCE_PATHOLOGY"              — structural anomaly in source XML
        "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE" — sparse payload aligned to live tree
    """

    kind: str          # e.g. "duplicate_target", "semantic_collapse"
    stage: str         # which pipeline phase produced this
    detail: Mapping[str, Any]  # phase-specific payload
    source_statute: str = ""

    def __post_init__(self) -> None:
        validate_finding_projection(self.kind, "observation", False)


@dataclass(frozen=True)
class Obligation:
    """Something the pipeline MUST address before producing valid output.

    Obligations are the canonical strictness carrier. A blocking obligation
    itself is the authority: downstream output should be treated as invalid
    until the obligation is resolved or explicitly overridden by a permissive
    StrictProfile.

    Common kinds:
        "missing_payload"            — op has no body to apply
        "unresolved_target"          — address could not be resolved in tree
        "invariant_violation"        — tree or replay-product invariant broken
        "strict_rejected_guessing"   — target-guessing rejected by profile
        "TIME.CONTINGENT_EFFECTIVE_DATE"  — effective date not deterministic
    """

    kind: str          # e.g. "missing_payload", "unresolved_target"
    stage: str
    detail: Mapping[str, Any]
    blocking: bool = True  # if True, output is invalid until resolved

    def __post_init__(self) -> None:
        validate_finding_projection(self.kind, "obligation", self.blocking)


@dataclass(frozen=True)
class Violation:
    """An impossible or contract-broken state -- always blocking, never recoverable.

    Unlike Obligations which can be overridden by permissive profiles,
    Violations indicate genuine contract breaks that must be fixed.
    Violations always make ``PhaseResult.has_blocking`` True.
    """

    kind: str
    stage: str
    detail: Mapping[str, Any]
    source_statute: str = ""

    def __post_init__(self) -> None:
        validate_finding_projection(self.kind, "violation", True)


@dataclass(frozen=True)
class Finding:
    """Unified projected finding view over observations, obligations, and violations.

    Registry barrier kinds stay on the registry/verdict rails.
    """

    kind: str
    role: FindingRole
    stage: str
    detail: Mapping[str, Any]
    source_statute: str = ""
    blocking: bool = False

    def __post_init__(self) -> None:
        validate_finding_projection(self.kind, self.role, self.blocking)


@dataclass(frozen=True, init=False)
class PhaseResult(Generic[T]):
    """Typed output from a pipeline phase.

    Every pipeline stage can return ``PhaseResult`` where the primary output
    is carried in ``output`` alongside zero or more ``Observation`` and
    ``Obligation`` records.

    Observations are informational — they accumulate through the final
    facade/finding ledger but do not prevent output.

    Obligations are requirements — a blocking obligation means the output
    should be treated as invalid until the obligation is resolved or a
    permissive StrictProfile explicitly allows the recovery path.

        Barrier kinds stay on the registry/verdict rails and do not become
        runtime findings.

    Usage::

        result = normalize_and_compile_ops_pr(preamble_text, tree, master, mid, ...)
        if result.has_blocking:
            # surface before continuing
            for finding in result.findings():
                if finding.role == OBLIGATION_ROLE and finding.blocking:
                    adjudications_out.append(...)
        ops = result.output

    The ``merge`` method supports cascading stages::

        a = stage_one(...)
        b = stage_two(a.output, ...)
        combined = a.merge(b)   # output=b.output, findings from both

    Status
    ------
    Internal stage-boundary contract. Findings plus executable temporal and
    migration events are the authority-bearing side channels. Parse-layer
    `EffectIntent` objects must be lowered before they cross this core phase
    boundary.
    """

    output: T                                      # the primary result
    finding_ledger: Tuple[Finding, ...]
    temporal_events: Tuple["TemporalEvent", ...]
    migration_events: Tuple["MigrationEvent", ...]

    def __init__(
        self,
        output: T,
        temporal_events: Tuple["TemporalEvent", ...] = (),
        migration_events: Tuple["MigrationEvent", ...] = (),
        findings: Tuple[Finding, ...] = (),
    ) -> None:
        finding_ledger = tuple(findings)
        for finding in finding_ledger:
            if not isinstance(finding, Finding):
                raise TypeError(
                    "PhaseResult.findings must contain Finding instances"
                )
            validate_finding_projection(finding.kind, finding.role, finding.blocking)
        resolved_temporal_events = tuple(temporal_events)
        resolved_migration_events = tuple(migration_events)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "finding_ledger", finding_ledger)
        object.__setattr__(self, "temporal_events", resolved_temporal_events)
        object.__setattr__(self, "migration_events", resolved_migration_events)

    @property
    def has_blocking(self) -> bool:
        """True if any projected finding is blocking in the runtime ledger."""
        return any(finding.blocking for finding in self.finding_ledger)

    @property
    def migration_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct migration-event kinds carried by this phase."""
        return distinct_event_kinds(self.migration_events)

    @property
    def temporal_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct temporal-event kinds carried by this phase."""
        return distinct_event_kinds(self.temporal_events)

    @property
    def temporal_events_with_activation_rules(self) -> int:
        """Return the number of temporal events carrying an embedded activation rule."""
        return count_events_with_activation_rules(self.temporal_events)

    @property
    def temporal_events_with_source(self) -> int:
        """Return the number of temporal events carrying provenance source data."""
        return count_events_with_source(self.temporal_events)

    @property
    def temporal_event_activation_rule_kinds(self) -> tuple[str, ...]:
        """Return the distinct activation-rule kinds carried by this phase."""
        return distinct_activation_rule_kinds(self.temporal_events)

    @icontract.ensure(
        lambda self, other, result: (
            sum(1 for finding in result.findings() if finding.role == OBSERVATION_ROLE)
            >= max(
                sum(1 for finding in self.findings() if finding.role == OBSERVATION_ROLE),
                sum(1 for finding in other.findings() if finding.role == OBSERVATION_ROLE),
            )
            and sum(1 for finding in result.findings() if finding.role == OBLIGATION_ROLE)
            >= max(
                sum(1 for finding in self.findings() if finding.role == OBLIGATION_ROLE),
                sum(1 for finding in other.findings() if finding.role == OBLIGATION_ROLE),
            )
            and sum(1 for finding in result.findings() if finding.role == VIOLATION_ROLE)
            >= max(
                sum(1 for finding in self.findings() if finding.role == VIOLATION_ROLE),
                sum(1 for finding in other.findings() if finding.role == VIOLATION_ROLE),
            )
        ),
        "merged result must accumulate at least as many signals as either input",
    )
    def merge(self, other: "PhaseResult[T]") -> "PhaseResult[T]":
        """Combine two PhaseResults, accumulating observations, obligations, and violations.

        The *later* stage's output wins.  All observations, obligations,
        and violations from both stages are preserved.

        Args:
            other: The downstream PhaseResult to merge into this one.

        Returns:
            A new PhaseResult with ``other.output`` and the union of signals.
        """
        return PhaseResult(
            output=other.output,
            findings=self.finding_ledger + other.finding_ledger,
            temporal_events=self.temporal_events + other.temporal_events,
            migration_events=self.migration_events + other.migration_events,
        )

    def findings(self) -> "Tuple[Finding, ...]":
        """Return the stored finding ledger."""
        return self.finding_ledger


@dataclass
class PhaseBuilder(Generic[T]):
    """Mutable local builder for PhaseResult — use inside one stage only.

    Not a pipeline-wide accumulator. Each stage creates its own builder,
    collects direct Findings from local stage logic, then freezes into a
    PhaseResult.

    Usage::

        def some_stage(...) -> PhaseResult:
            b = PhaseBuilder()
            local_findings: list[Finding] = []

            result = _inner(
                ...,
                findings_out=local_findings,
            )

            b.add_findings(local_findings)
            return b.finish(result)

    Status
    ------
    Internal mutable stage-local helper. Use it inside one phase, then freeze
    to `PhaseResult`; do not treat it as a pipeline-wide accumulator API.
    """

    _findings: List[Finding] = field(default_factory=list)
    _temporal_events: List["TemporalEvent"] = field(default_factory=list)
    _migration_events: List["MigrationEvent"] = field(default_factory=list)

    def _append_finding(
        self,
        *,
        kind: str,
        role: FindingRole,
        stage: str,
        detail: Mapping[str, Any],
        blocking: bool,
        source_statute: str = "",
    ) -> None:
        self._findings.append(
            Finding(
                kind=kind,
                role=role,
                stage=stage,
                detail=dict(detail),
                source_statute=source_statute,
                blocking=blocking,
            )
        )

    def observe(self, kind: str, stage: str, detail: Mapping[str, Any], source_statute: str = "") -> None:
        self._append_finding(
            kind=kind,
            role=OBSERVATION_ROLE,
            stage=stage,
            detail=detail,
            blocking=False,
            source_statute=source_statute,
        )

    def oblige(self, kind: str, stage: str, detail: Mapping[str, Any], blocking: bool = True) -> None:
        """Record a blocking requirement.

        Barrier-taxonomy registry codes are not a runtime endpoint.  They
        belong on the registry/verdict rails only.
        """
        spec = get_finding_spec(kind)
        if spec is not None and spec.role == "barrier":
            raise ValueError(
                f"Finding.kind={kind!r} is a barrier registry code and cannot be recorded "
                "through PhaseBuilder.oblige(); barrier kinds are registry/verdict only"
            )
        self._append_finding(
            kind=kind,
            role=OBLIGATION_ROLE,
            stage=stage,
            detail=detail,
            blocking=blocking,
        )

    def violate(self, kind: str, stage: str, detail: Mapping[str, Any], source_statute: str = "") -> None:
        """Record a contract violation -- always blocking, never recoverable."""
        spec = get_finding_spec(kind)
        if spec is not None and spec.role == "barrier":
            raise ValueError(
                f"Finding.kind={kind!r} is a barrier registry code and cannot be recorded "
                "through PhaseBuilder.violate(); barrier kinds are registry/verdict only"
            )
        self._append_finding(
            kind=kind,
            role=VIOLATION_ROLE,
            stage=stage,
            detail=detail,
            blocking=True,
            source_statute=source_statute,
        )

    def add_findings(self, findings: Iterable[Finding]) -> None:
        """Append already-constructed canonical findings."""
        for finding in findings:
            validate_finding_projection(finding.kind, finding.role, finding.blocking)
            self._findings.append(finding)

    def add_temporal_event(self, event: "TemporalEvent") -> None:
        self._temporal_events.append(event)

    def add_temporal_events(self, events: Iterable["TemporalEvent"]) -> None:
        self._temporal_events.extend(events)

    def add_migration_event(self, event: "MigrationEvent") -> None:
        self._migration_events.append(event)

    def add_migration_events(self, events: Iterable["MigrationEvent"]) -> None:
        self._migration_events.extend(events)

    @icontract.ensure(
        lambda self, result: (
            len(result.findings()) == len(self._findings)
        ),
        "finish must capture all accumulated signals without loss",
    )
    def finish(self, output: T) -> PhaseResult[T]:
        return PhaseResult(
            output=output,
            findings=tuple(self._findings),
            temporal_events=tuple(self._temporal_events),
            migration_events=tuple(self._migration_events),
        )
