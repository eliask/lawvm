"""Lineage and lightweight query helpers for timeline consumers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Mapping, Protocol

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.provenance import MigrationEvent, migration_event_sort_key
from lawvm.core.semantic_types import FacetKind
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.timeline_results import (
    MaterializationLineageDecision,
    MaterializationLineagePlan,
)


class _SelectionResult(Protocol):
    version: ProvisionVersion | None


@dataclass(frozen=True, slots=True)
class _PrefixMigrationEvent:
    from_address: LegalAddress
    to_address: LegalAddress
    effective: str
    source_statute: str


@dataclass(frozen=True, slots=True)
class _PrefixMigrationWave:
    events_by_specificity: tuple[_PrefixMigrationEvent, ...]
    source_paths: frozenset[TreePath]


@dataclass(frozen=True, slots=True)
class _PrefixMigrationWavePlan:
    waves: tuple[_PrefixMigrationWave, ...]


@dataclass(frozen=True, slots=True)
class _PrefixMigrationEventSignature:
    event_id: str
    kind: str
    from_path: TreePath
    from_special: FacetKind | None
    to_path: TreePath
    to_special: FacetKind | None
    effective: str
    source_statute: str


PrefixMigrationEventSignature = _PrefixMigrationEventSignature


def _migration_event_signature(event: MigrationEvent) -> _PrefixMigrationEventSignature:
    source_statute = event.source_statute if event.source_statute is not None else ""
    return _PrefixMigrationEventSignature(
        event_id=event.event_id,
        kind=event.kind,
        from_path=event.from_address.path,
        from_special=event.from_address.special,
        to_path=event.to_address.path,
        to_special=event.to_address.special,
        effective=event.effective,
        source_statute=source_statute,
    )


def prefix_migration_event_signatures(
    migration_events: tuple[MigrationEvent, ...],
) -> tuple[PrefixMigrationEventSignature, ...]:
    """Return stable prefix-migration cache keys for migration events."""
    return tuple(_migration_event_signature(event) for event in migration_events)


def _prefix_migration_wave_plan(
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str,
    not_before: str,
) -> _PrefixMigrationWavePlan:
    return _prefix_migration_wave_plan_from_signature(
        tuple(_migration_event_signature(event) for event in migration_events),
        as_of_date,
        not_before,
    )


@lru_cache(maxsize=16)
def _prefix_migration_wave_plan_from_signature(
    events_signature: tuple[PrefixMigrationEventSignature, ...],
    as_of_date: str,
    not_before: str,
) -> _PrefixMigrationWavePlan:
    waves: dict[tuple[str, str], list[_PrefixMigrationEvent]] = {}
    for event_signature in events_signature:
        if as_of_date and event_signature.effective and event_signature.effective > as_of_date:
            continue
        if not_before and event_signature.effective and event_signature.effective < not_before:
            continue
        waves.setdefault((event_signature.effective, event_signature.source_statute), []).append(
            _PrefixMigrationEvent(
                from_address=LegalAddress(
                    path=event_signature.from_path,
                    special=event_signature.from_special,
                ),
                to_address=LegalAddress(
                    path=event_signature.to_path,
                    special=event_signature.to_special,
                ),
                effective=event_signature.effective,
                source_statute=event_signature.source_statute,
            )
        )

    wave_items: list[_PrefixMigrationWave] = []
    for _, wave_events in sorted(waves.items(), key=lambda item: item[0]):
        sorted_events = tuple(
            sorted(
                wave_events,
                key=lambda item: (
                    len(item.from_address.path),
                    str(item.from_address),
                    str(item.to_address),
                ),
                reverse=True,
            )
        )
        wave_items.append(
            _PrefixMigrationWave(
                events_by_specificity=sorted_events,
                source_paths=frozenset(event.from_address.path for event in sorted_events),
            )
        )
    return _PrefixMigrationWavePlan(waves=tuple(wave_items))


def _path_may_match_any_prefix(path: TreePath, source_paths: frozenset[TreePath]) -> bool:
    return any(path[:depth] in source_paths for depth in range(1, len(path) + 1))


@dataclass(frozen=True)
class LineageSegment:
    from_address: LegalAddress
    to_address: LegalAddress
    event: MigrationEvent | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.from_address, LegalAddress):
            raise ValueError("LineageSegment.from_address must be a LegalAddress")
        if not isinstance(self.to_address, LegalAddress):
            raise ValueError("LineageSegment.to_address must be a LegalAddress")
        if self.event is not None and not isinstance(self.event, MigrationEvent):
            raise ValueError("LineageSegment.event must be a MigrationEvent or None")


# Blocking finding code this build-time guard raises under (registered in
# ``core/observation_registry.py`` as a ``violation``/``hard_fail``). Referenced
# here at the emit site so the registry/producer-consistency guard can find a
# real producer, not just the registry declaration.
LINEAGE_CYCLE_FINDING_CODE = "LINEAGE.CYCLE"


class LineageCycleError(ValueError):
    """A migration/lineage segment graph contains a cycle.

    A cycle means an eId migrates (directly or transitively) back into its own
    ancestry. Address resolution (``current_address_from_migration_events`` /
    the prefix-wave resolvers) only terminates because of a ``visited`` guard
    that silently truncates the walk at the first revisit; the underlying
    relation is non-terminating. Materializing a PIT under such a ledger yields
    order-dependent, repeated-PIT hash drift, so this must fail loud at ledger
    build, not be silently swallowed by the resolver.
    """

    def __init__(self, cycle: tuple[LegalAddress, ...]) -> None:
        self.cycle = cycle
        self.finding_code = LINEAGE_CYCLE_FINDING_CODE
        rendered = " → ".join(str(address) for address in cycle)
        super().__init__(
            f"{LINEAGE_CYCLE_FINDING_CODE}: migration/lineage segments form a cycle "
            f"(an eId migrates into its own ancestry): {rendered}"
        )


@dataclass(frozen=True, slots=True)
class LineageAcyclicityResult:
    """Typed verdict for the lineage/migration DAG acyclicity audit (LS-11)."""

    acyclic: bool
    cycle: tuple[LegalAddress, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.acyclic, bool):
            raise ValueError("LineageAcyclicityResult.acyclic must be a bool")
        if self.acyclic and self.cycle:
            raise ValueError(
                "LineageAcyclicityResult marked acyclic but carries a cycle witness"
            )
        if not self.acyclic and not self.cycle:
            raise ValueError(
                "LineageAcyclicityResult marked cyclic but carries no cycle witness"
            )


def _migration_edges(
    migration_events: tuple[MigrationEvent, ...],
) -> dict[LegalAddress, list[LegalAddress]]:
    """Build the directed migration edge graph ``from_address -> to_address``.

    Each migration event is one directed edge. Following these edges is exactly
    what the address resolvers do (a section at ``from_address`` continues at
    ``to_address``); a directed cycle here is a non-terminating lineage walk.
    Self-edges (``from == to``) are not cycles — they are identity no-ops the
    resolvers skip — so they are not recorded as edges.
    """
    edges: dict[LegalAddress, list[LegalAddress]] = {}
    for event in migration_events:
        if event.from_address == event.to_address:
            continue
        edges.setdefault(event.from_address, []).append(event.to_address)
    return edges


def check_lineage_acyclic(
    migration_events: tuple[MigrationEvent, ...],
) -> LineageAcyclicityResult:
    """Return a typed acyclicity verdict over the migration edge graph (LS-11).

    Detects a directed cycle in the ``from_address -> to_address`` migration
    graph via iterative DFS three-colouring (white/grey/black). The first cycle
    found is returned as an ordered address witness so the failure is
    self-evidencing. Deterministic: nodes and successors are visited in sorted
    address order, so the witnessed cycle is stable run-to-run.
    """
    edges = _migration_edges(migration_events)
    if not edges:
        return LineageAcyclicityResult(acyclic=True)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[LegalAddress, int] = {}

    for root in sorted(edges, key=str):
        if colour.get(root, WHITE) != WHITE:
            continue
        # Iterative DFS carrying the active path so a back-edge yields the cycle.
        stack: list[tuple[LegalAddress, int]] = [(root, 0)]
        path: list[LegalAddress] = []
        colour[root] = GREY
        path.append(root)
        while stack:
            node, index = stack[-1]
            successors = sorted(edges.get(node, ()), key=str)
            if index < len(successors):
                stack[-1] = (node, index + 1)
                succ = successors[index]
                succ_colour = colour.get(succ, WHITE)
                if succ_colour == GREY:
                    # Back-edge into the active path: extract the cycle slice.
                    start = path.index(succ)
                    cycle = tuple(path[start:]) + (succ,)
                    return LineageAcyclicityResult(acyclic=False, cycle=cycle)
                if succ_colour == WHITE:
                    colour[succ] = GREY
                    path.append(succ)
                    stack.append((succ, 0))
                continue
            colour[node] = BLACK
            stack.pop()
            if path and path[-1] == node:
                path.pop()

    return LineageAcyclicityResult(acyclic=True)


def assert_acyclic(
    migration_events: tuple[MigrationEvent, ...],
) -> None:
    """Fail loud (LINEAGE.CYCLE) if the migration/lineage segments are cyclic.

    Build-time guard for LS-11. A cyclic migration ledger implies
    non-terminating materialization / repeated-PIT hash drift, so a cycle is a
    blocking contract break rather than a recoverable residual.
    """
    result = check_lineage_acyclic(migration_events)
    if not result.acyclic:
        raise LineageCycleError(result.cycle)


@dataclass(frozen=True)
class ScopeMigrationClassification:
    active_scope_changing: bool
    noncolliding: bool
    destination_occupancy_collision: bool

    def __post_init__(self) -> None:
        _validate_bool_field("ScopeMigrationClassification", "active_scope_changing", self.active_scope_changing)
        _validate_bool_field("ScopeMigrationClassification", "noncolliding", self.noncolliding)
        _validate_bool_field(
            "ScopeMigrationClassification",
            "destination_occupancy_collision",
            self.destination_occupancy_collision,
        )


@dataclass(frozen=True)
class MaterializationLineageBridgeClassification:
    """Typed bridge-family classification for PIT lineage planning."""

    native_rebirth_after_renumber: bool = False
    leaf_stable_scope_renumber: bool = False
    active_scope_changing: bool = False
    noncolliding_scope_migrations: bool = False
    destination_occupancy_collision: bool = False

    def __post_init__(self) -> None:
        _validate_bool_field(
            "MaterializationLineageBridgeClassification",
            "native_rebirth_after_renumber",
            self.native_rebirth_after_renumber,
        )
        _validate_bool_field(
            "MaterializationLineageBridgeClassification",
            "leaf_stable_scope_renumber",
            self.leaf_stable_scope_renumber,
        )
        _validate_bool_field(
            "MaterializationLineageBridgeClassification",
            "active_scope_changing",
            self.active_scope_changing,
        )
        _validate_bool_field(
            "MaterializationLineageBridgeClassification",
            "noncolliding_scope_migrations",
            self.noncolliding_scope_migrations,
        )
        _validate_bool_field(
            "MaterializationLineageBridgeClassification",
            "destination_occupancy_collision",
            self.destination_occupancy_collision,
        )


@dataclass(frozen=True, slots=True)
class TimelineSplitBucket:
    """Version bucket after splitting a timeline at a native-renumber boundary."""

    address: LegalAddress
    versions: list[ProvisionVersion]
    force_native: bool
    native_boundary: str = ""


@dataclass(frozen=True, slots=True)
class RekeyTimelineEntry:
    """One timeline bucket projected to its migrated PIT address."""

    is_native_lineage: bool
    source_address: LegalAddress
    migrated_address: LegalAddress
    timeline: ProvisionTimeline


@dataclass(frozen=True, slots=True)
class MigrationBoundary:
    """A migration event paired with the effective date proven by a timeline."""

    event: MigrationEvent
    effective: str


def _validate_bool_field(carrier_name: str, field_name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{carrier_name}.{field_name} must be a bool")


_RetargetVersionContentFn = Callable[[ProvisionVersion, LegalAddress], ProvisionVersion]
_MergeBucketCleanupFn = Callable[[list[ProvisionVersion]], list[ProvisionVersion]]


def _migration_effective_from_timeline(
    event: MigrationEvent,
    timeline: ProvisionTimeline,
) -> str:
    """Return the migration boundary date witnessed by the affected timeline."""
    if event.effective:
        return event.effective
    if not event.source_statute:
        return ""
    candidates = [
        version.effective
        for version in timeline.versions
        if version.effective
        and version.source is not None
        and version.source.statute_id == event.source_statute
    ]
    return min(candidates, default="")


def has_native_rebirth_after_renumber(
    timelines: Mapping[LegalAddress, ProvisionTimeline] | None,
    migration_events: tuple[MigrationEvent, ...],
    *,
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> bool:
    """Return whether a renumber leaves a new native same-label lineage behind."""
    if not timelines or not migration_events:
        return False

    for address, timeline in timelines.items():
        matching_renumbers = (
            MigrationBoundary(event=event, effective=effective)
            for event in migration_events
            if event.kind == "renumber"
            and address_prefix_matches(address, event.from_address)
            and (effective := _migration_effective_from_timeline(event, timeline))
        )
        event_boundary = min(
            matching_renumbers,
            key=lambda boundary: (boundary.effective, migration_event_sort_key(boundary.event)),
            default=None,
        )
        if event_boundary is None:
            continue
        has_before = any(version.effective < event_boundary.effective for version in timeline.versions)
        has_after = any(version.effective >= event_boundary.effective for version in timeline.versions)
        if has_before and has_after:
            return True
    return False


def classify_materialization_lineage_bridge(
    timelines: Mapping[LegalAddress, ProvisionTimeline] | None,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> MaterializationLineageBridgeClassification:
    """Classify the current lineage/materialization bridge families.

    Core owns the family predicates that feed PIT lineage planning. Frontends
    may still keep local rekey shims temporarily, but the branch taxonomy
    itself should not stay frontend-specific.
    """
    scope_migration_classification = classify_scope_migrations(
        timelines,
        migration_events,
        as_of_date=as_of_date,
        address_prefix_matches=address_prefix_matches,
    )
    return MaterializationLineageBridgeClassification(
        native_rebirth_after_renumber=has_native_rebirth_after_renumber(
            timelines,
            migration_events,
            address_prefix_matches=address_prefix_matches,
        ),
        leaf_stable_scope_renumber=has_only_leaf_stable_scope_renumbers(
            timelines,
            migration_events,
            address_prefix_matches=address_prefix_matches,
        ),
        active_scope_changing=scope_migration_classification.active_scope_changing,
        noncolliding_scope_migrations=scope_migration_classification.noncolliding,
        destination_occupancy_collision=scope_migration_classification.destination_occupancy_collision,
    )


def has_only_leaf_stable_scope_renumbers(
    timelines: Mapping[LegalAddress, ProvisionTimeline] | None,
    migration_events: tuple[MigrationEvent, ...],
    *,
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> bool:
    """Return whether scope-depth changes are pure ancestor-prefix renumbers.

    This is the family where:
    - every scope-changing event is a ``renumber``;
    - affected descendants keep the same leaf identity after migration.
    """
    if not timelines or not migration_events:
        return False

    relevant_addresses: list[LegalAddress] = []
    for address in timelines:
        if any(
            len(event.from_address.path) != len(event.to_address.path)
            and address_prefix_matches(address, event.from_address)
            for event in migration_events
        ):
            relevant_addresses.append(address)
    if not relevant_addresses:
        return False

    for event in migration_events:
        if len(event.from_address.path) == len(event.to_address.path):
            continue
        if event.kind != "renumber":
            return False

    for address in relevant_addresses:
        migrated_address = current_address_from_migration_events(
            address,
            migration_events,
            as_of_date="9999-12-31",
            address_prefix_matches=address_prefix_matches,
        )
        if not address.path or not migrated_address.path:
            return False
        if address.path[-1] != migrated_address.path[-1]:
            return False
    return True


def choose_materialization_lineage_decision(
    *,
    raw_timelines: dict[LegalAddress, ProvisionTimeline],
    rekeyed_timelines: dict[LegalAddress, ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    native_rebirth_after_renumber: bool = False,
    leaf_stable_scope_renumber: bool = False,
    noncolliding_scope_migrations: bool = False,
    destination_occupancy_collision: bool = False,
    scope_changing_migration_fallback: bool = False,
    default_reason: str = "default_migration_projection",
    native_rebirth_reason: str = "native_rebirth_after_renumber",
    leaf_stable_reason: str = "leaf_stable_scope_renumber",
    destination_occupancy_reason: str = "destination_occupancy_collision",
    scope_changing_fallback_reason: str = "scope_changing_migration_fallback",
) -> MaterializationLineageDecision:
    """Choose PIT lineage decision from already-classified branch families.

    Shared core owns the mapping from branch family to:
    - timeline source (`raw` vs `rekeyed`)
    - execution plan (`MaterializationLineagePlan`)
    - typed reason string

    Callers may still own the predicate classification itself.
    """
    if native_rebirth_after_renumber:
        return MaterializationLineageDecision(
            timelines=rekeyed_timelines,
            timeline_source="rekeyed",
            lineage_plan=MaterializationLineagePlan(
                mode="rekeyed_only",
                migration_events=(),
            ),
            reason=native_rebirth_reason,
        )
    if leaf_stable_scope_renumber:
        return MaterializationLineageDecision(
            timelines=rekeyed_timelines,
            timeline_source="rekeyed",
            lineage_plan=MaterializationLineagePlan(
                mode="rekeyed_with_migrations",
                migration_events=migration_events,
            ),
            reason=leaf_stable_reason,
        )
    if noncolliding_scope_migrations:
        return MaterializationLineageDecision(
            timelines=rekeyed_timelines,
            timeline_source="rekeyed",
            lineage_plan=MaterializationLineagePlan(
                mode="rekeyed_with_migrations",
                migration_events=migration_events,
            ),
            reason=default_reason,
        )
    if destination_occupancy_collision:
        return MaterializationLineageDecision(
            timelines=raw_timelines,
            timeline_source="raw",
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=migration_events,
            ),
            reason=destination_occupancy_reason,
        )
    if scope_changing_migration_fallback:
        return MaterializationLineageDecision(
            timelines=raw_timelines,
            timeline_source="raw",
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=migration_events,
            ),
            reason=scope_changing_fallback_reason,
        )
    return MaterializationLineageDecision(
        timelines=rekeyed_timelines,
        timeline_source="rekeyed",
        lineage_plan=MaterializationLineagePlan(
            mode="rekeyed_with_migrations",
            migration_events=migration_events,
        ),
        reason=default_reason,
    )


def classify_scope_migrations(
    timelines: Mapping[LegalAddress, ProvisionTimeline] | None,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> ScopeMigrationClassification:
    """Classify active scope-changing migration families for PIT planning."""
    if not timelines or not migration_events:
        return ScopeMigrationClassification(
            active_scope_changing=False,
            noncolliding=False,
            destination_occupancy_collision=False,
        )

    active_scope_events = tuple(
        event
        for event in migration_events
        if len(event.from_address.path) != len(event.to_address.path)
        and (not as_of_date or not event.effective or event.effective <= as_of_date)
    )
    if not active_scope_events:
        return ScopeMigrationClassification(
            active_scope_changing=False,
            noncolliding=False,
            destination_occupancy_collision=False,
        )

    relevant_addresses = {
        address
        for address in timelines
        if any(
            address_prefix_matches(address, event.from_address)
            for event in active_scope_events
        )
    }
    if not relevant_addresses:
        return ScopeMigrationClassification(
            active_scope_changing=False,
            noncolliding=False,
            destination_occupancy_collision=False,
        )

    allowed_kinds = all(event.kind in {"renumber", "move"} for event in active_scope_events)
    relevant_set = set(relevant_addresses)
    seen_migrated_addresses: set[LegalAddress] = set()
    noncolliding = allowed_kinds
    destination_occupancy_collision = False

    for address in relevant_addresses:
        migrated_address = current_address_from_migration_events(
            address,
            migration_events,
            as_of_date=as_of_date,
            address_prefix_matches=address_prefix_matches,
        )
        if migrated_address == address:
            noncolliding = False
        if migrated_address in seen_migrated_addresses:
            noncolliding = False
        if migrated_address in timelines and migrated_address not in relevant_set:
            destination_occupancy_collision = True
            noncolliding = False
        seen_migrated_addresses.add(migrated_address)

    return ScopeMigrationClassification(
        active_scope_changing=True,
        noncolliding=noncolliding,
        destination_occupancy_collision=destination_occupancy_collision,
    )


def current_address_from_migration_events(
    original_address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> LegalAddress:
    current = original_address
    visited: set[str] = {str(current)}
    ordered_events = sorted(
        (
            event
            for event in migration_events
            if not as_of_date or not event.effective or event.effective <= as_of_date
        ),
        key=migration_event_sort_key,
    )

    changed = True
    while changed:
        changed = False
        for event in ordered_events:
            if not address_prefix_matches(current, event.from_address):
                continue
            prefix_len = len(event.from_address.path)
            next_path = event.to_address.path + current.path[prefix_len:]
            next_addr = LegalAddress(path=next_path, special=current.special)
            addr_key = str(next_addr)
            if addr_key in visited:
                continue
            visited.add(addr_key)
            current = next_addr
            changed = True

    return current


def current_address_with_prefix_migrations_from_events(
    original_address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    not_before: str = "",
    normalize_address_fn: Callable[[LegalAddress], LegalAddress] | None = None,
) -> LegalAddress:
    """Follow renumber/move links across any matching address prefix.

    Same-wave migration clauses are interpreted against the wave's pre-act
    reference frame, then applied in specificity order. Frontends may supply
    an address normalizer when migration matching needs jurisdiction-local
    label normalization, but the wave/prefix execution semantics are shared.

    ``not_before`` is a lower-bound effective date for the address's own
    content lineage. Renumber/move waves with ``effective < not_before``
    happened before this content existed, so they relabeled a *prior* occupant
    of the slot and must not be followed. This prevents a section born into a
    label that was earlier vacated by a renumber (slot reuse / chapter rebirth)
    from inheriting the prior occupant's stale renumber chain. Same-wave
    follows (``effective == not_before``) remain in scope.
    """
    return current_address_with_prefix_migrations_from_event_signatures(
        original_address,
        prefix_migration_event_signatures(migration_events),
        as_of_date=as_of_date,
        not_before=not_before,
        normalize_address_fn=normalize_address_fn,
    )


def current_address_with_prefix_migrations_from_event_signatures(
    original_address: LegalAddress,
    migration_event_signatures: tuple[PrefixMigrationEventSignature, ...],
    *,
    as_of_date: str = "",
    not_before: str = "",
    normalize_address_fn: Callable[[LegalAddress], LegalAddress] | None = None,
) -> LegalAddress:
    """Follow prefix migrations using precomputed event signatures."""
    normalize = normalize_address_fn or (lambda address: address)
    current = normalize(original_address)
    visited: set[str] = {str(current)}
    wave_plan = _prefix_migration_wave_plan_from_signature(
        migration_event_signatures,
        as_of_date=as_of_date,
        not_before=not_before,
    )

    for wave in wave_plan.waves:
        wave_start = normalize(current)
        if not _path_may_match_any_prefix(wave_start.path, wave.source_paths):
            continue
        applicable_wave_events: list[tuple[_PrefixMigrationEvent, LegalAddress]] = []
        applied_specificity: list[int] = []
        allowed_destination_source_prefixes: set[TreePath] = set()
        for event in wave.events_by_specificity:
            normalized_event_from = normalize(event.from_address)
            if not wave_start.has_path_prefix(normalized_event_from):
                continue
            applicable_wave_events.append((event, normalized_event_from))
        for event, normalized_event_from in applicable_wave_events:
            normalized_current = normalize(current)
            if not normalized_current.has_path_prefix(normalized_event_from):
                continue
            prefix_len = len(event.from_address.path)
            next_path = event.to_address.path + current.path[prefix_len:]
            next_addr = normalize(LegalAddress(path=next_path, special=current.special))
            addr_key = str(next_addr)
            if addr_key in visited:
                continue
            visited.add(addr_key)
            current = next_addr
            event_specificity = len(normalized_event_from.path)
            applied_specificity.append(event_specificity)
            normalized_event_to = normalize(event.to_address)
            for prefix_len in range(1, min(event_specificity, len(normalized_event_to.path) + 1)):
                allowed_destination_source_prefixes.add(normalized_event_to.path[:prefix_len])

        # Some recodification waves express a descendant relabel destination in
        # a parent source frame that is itself relabeled later in the same act.
        # Follow only those newly exposed ancestor relabels. This keeps sibling
        # ancestor-only chains such as part III->IV and IV->V in the pre-act
        # frame, while allowing section-level moves into a relabeled parent
        # frame to land in the live post-wave container.
        if not applied_specificity:
            continue
        max_specificity = max(applied_specificity)
        for event in wave.events_by_specificity:
            normalized_event_from = normalize(event.from_address)
            if len(normalized_event_from.path) >= max_specificity:
                continue
            if normalized_event_from.path not in allowed_destination_source_prefixes:
                continue
            normalized_current = normalize(current)
            if not normalized_current.has_path_prefix(normalized_event_from):
                continue
            prefix_len = len(event.from_address.path)
            next_path = event.to_address.path + current.path[prefix_len:]
            next_addr = normalize(LegalAddress(path=next_path, special=current.special))
            addr_key = str(next_addr)
            if addr_key in visited:
                continue
            visited.add(addr_key)
            current = next_addr
    return current


def rekey_timelines_with_migration_events(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str,
    current_address_with_prefix_migrations_fn: Callable[[LegalAddress, tuple[MigrationEvent, ...], str], LegalAddress],
    current_address_with_prefix_migration_signatures_fn: Callable[
        [LegalAddress, tuple[PrefixMigrationEventSignature, ...], str, str],
        LegalAddress,
    ]
    | None = None,
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
    renumber_source_prefix_may_match_fn: Callable[[LegalAddress], bool] | None = None,
    retarget_version_content_fn: _RetargetVersionContentFn = _retarget_version_content,
    merge_bucket_cleanup_fn: _MergeBucketCleanupFn | None = None,
) -> dict[LegalAddress, ProvisionTimeline]:
    """Project replay-owned timelines onto migrated addresses for PIT planning.

    Core owns the generic native-rebirth split, migration projection, and
    bucket merge semantics. Frontends may still supply jurisdiction-local
    address normalization, migrated-root formatting, or merge cleanup policy
    while the last replay-product residue is being retired.
    """
    if not migration_events:
        return dict(timelines)
    migration_event_signatures = prefix_migration_event_signatures(migration_events)
    renumber_events = tuple(
        event for event in migration_events if event.kind == "renumber"
    )
    renumber_events_with_effective = tuple(
        event for event in renumber_events if event.effective
    )
    renumber_events_by_effective: dict[str, tuple[MigrationEvent, ...]] = {}
    renumber_events_before_effective: dict[str, tuple[MigrationEvent, ...]] = {}
    native_boundary_signatures: dict[str, tuple[PrefixMigrationEventSignature, ...]] = {}
    native_boundary_events: dict[str, tuple[MigrationEvent, ...]] = {}

    def _signatures_after_native_boundary(
        native_boundary: str,
    ) -> tuple[PrefixMigrationEventSignature, ...]:
        cached = native_boundary_signatures.get(native_boundary)
        if cached is not None:
            return cached
        filtered = tuple(
            event_signature
            for event_signature in migration_event_signatures
            if native_boundary
            and event_signature.effective
            and event_signature.effective > native_boundary
        )
        native_boundary_signatures[native_boundary] = filtered
        return filtered

    def _events_after_native_boundary(
        native_boundary: str,
    ) -> tuple[MigrationEvent, ...]:
        cached = native_boundary_events.get(native_boundary)
        if cached is not None:
            return cached
        filtered = tuple(
            event
            for event in migration_events
            if native_boundary and event.effective and event.effective > native_boundary
        )
        native_boundary_events[native_boundary] = filtered
        return filtered

    def _renumber_events_at_effective(effective: str) -> tuple[MigrationEvent, ...]:
        cached = renumber_events_by_effective.get(effective)
        if cached is not None:
            return cached
        filtered = tuple(
            event for event in renumber_events_with_effective if event.effective == effective
        )
        renumber_events_by_effective[effective] = filtered
        return filtered

    def _renumber_events_before_effective(effective: str) -> tuple[MigrationEvent, ...]:
        cached = renumber_events_before_effective.get(effective)
        if cached is not None:
            return cached
        filtered = tuple(
            event for event in renumber_events_with_effective if event.effective < effective
        )
        renumber_events_before_effective[effective] = filtered
        return filtered

    def _current_address_after_prefix_migrations(
        address: LegalAddress,
        events: tuple[MigrationEvent, ...],
        event_signatures: tuple[PrefixMigrationEventSignature, ...],
        *,
        not_before: str = "",
    ) -> LegalAddress:
        if current_address_with_prefix_migration_signatures_fn is not None:
            return current_address_with_prefix_migration_signatures_fn(
                address,
                event_signatures,
                as_of_date,
                not_before,
            )
        return current_address_with_prefix_migrations_fn(address, events, as_of_date)

    def _has_prior_incoming_migration_prefix(
        address: LegalAddress,
        *,
        before_effective: str,
    ) -> bool:
        return any(
            address_prefix_matches(address, prior_event.to_address)
            for prior_event in _renumber_events_before_effective(before_effective)
        )

    def _has_same_wave_incoming_migration_prefix(
        address: LegalAddress,
        *,
        at_effective: str,
    ) -> bool:
        return any(
            address_prefix_matches(address, event.to_address)
            for event in _renumber_events_at_effective(at_effective)
        )

    def _has_same_wave_incoming_to_source_prefix(
        source_prefix: LegalAddress,
        *,
        at_effective: str,
    ) -> bool:
        return any(
            event.to_address.path == source_prefix.path
            for event in _renumber_events_at_effective(at_effective)
        )

    def _source_prefix_has_native_rebirth(
        source_address: LegalAddress,
        *,
        at_effective: str,
    ) -> bool:
        source_timeline = timelines.get(source_address)
        if source_timeline is None:
            return False
        return (
            any(version.effective < at_effective for version in source_timeline.versions)
            and any(version.effective >= at_effective for version in source_timeline.versions)
        )

    def _split_versions_at_native_renumber_boundary(
        address: LegalAddress,
        versions: list[ProvisionVersion],
    ) -> list[TimelineSplitBucket]:
        if (
            renumber_source_prefix_may_match_fn is not None
            and not renumber_source_prefix_may_match_fn(address)
        ):
            return [TimelineSplitBucket(address=address, versions=versions, force_native=False)]
        timeline = ProvisionTimeline(address=address, versions=versions)
        matching_renumbers = [
            MigrationBoundary(event=event, effective=effective)
            for event in renumber_events
            if address_prefix_matches(address, event.from_address)
            and (effective := _migration_effective_from_timeline(event, timeline))
        ]
        if not matching_renumbers:
            return [TimelineSplitBucket(address=address, versions=versions, force_native=False)]
        # A same-label slot can be vacated and re-occupied across the statute's
        # life: an outgoing renumber frees the label, and a later wave authors a
        # brand-new container into the freed slot. Renumbers enacted before this
        # lineage's own content existed relabeled a *prior* occupant of the
        # slot, so they must not anchor this lineage's boundary (otherwise the
        # newly-born container inherits the prior occupant's forward renumber
        # chain and is relocated off its freed label). Anchor on the first
        # matching renumber at or after the lineage's birth. Birth is the
        # earliest *enacted* date, not effective: a delayed-commencement version
        # (enacted early, effective late) already existed when an intervening
        # renumber took effect and must still follow it, so anchoring on
        # effective would wrongly treat delayed sections as freshly born.
        lineage_birth = min(
            (version.enacted for version in versions if version.enacted),
            default="",
        )
        renumbers_from_birth = [
            boundary
            for boundary in matching_renumbers
            if not lineage_birth or boundary.effective >= lineage_birth
        ]
        boundary_renumbers = renumbers_from_birth or matching_renumbers
        boundary = min(
            boundary_renumbers,
            key=lambda item: (item.effective, migration_event_sort_key(item.event)),
        )
        event = boundary.event
        boundary_effective = boundary.effective
        before_versions = [version for version in versions if version.effective < boundary_effective]
        native_versions = [version for version in versions if version.effective >= boundary_effective]
        if before_versions and not native_versions:
            return [TimelineSplitBucket(address=address, versions=versions, force_native=False)]
        if native_versions and not before_versions:
            same_wave_incoming = _has_same_wave_incoming_migration_prefix(
                address,
                at_effective=boundary_effective,
            )
            same_wave_incoming_to_source_prefix = _has_same_wave_incoming_to_source_prefix(
                event.from_address,
                at_effective=boundary_effective,
            )
            force_native = (
                _has_prior_incoming_migration_prefix(
                    address,
                    before_effective=boundary_effective,
                )
                or same_wave_incoming_to_source_prefix
                or (
                    not same_wave_incoming
                    and _source_prefix_has_native_rebirth(
                        event.from_address,
                        at_effective=boundary_effective,
                    )
                )
            )
            return [
                TimelineSplitBucket(
                    address=address,
                    versions=versions,
                    force_native=force_native,
                    native_boundary=boundary_effective if force_native else "",
                )
            ]
        buckets: list[TimelineSplitBucket] = []
        if before_versions:
            buckets.append(TimelineSplitBucket(address=address, versions=before_versions, force_native=False))
        if native_versions:
            buckets.append(
                TimelineSplitBucket(
                    address=address,
                    versions=native_versions,
                    force_native=True,
                    native_boundary=boundary_effective,
                )
            )
        return buckets

    entries: list[RekeyTimelineEntry] = []
    for address, timeline in timelines.items():
        split_buckets = _split_versions_at_native_renumber_boundary(address, list(timeline.versions))
        for bucket in split_buckets:
            migrated_address = (
                _current_address_after_prefix_migrations(
                    bucket.address,
                    (
                        ()
                        if current_address_with_prefix_migration_signatures_fn is not None
                        else _events_after_native_boundary(bucket.native_boundary)
                    ),
                    _signatures_after_native_boundary(bucket.native_boundary),
                    not_before=bucket.native_boundary,
                )
                if bucket.force_native
                else _current_address_after_prefix_migrations(
                    bucket.address,
                    migration_events,
                    migration_event_signatures,
                )
            )
            entries.append(
                RekeyTimelineEntry(
                    is_native_lineage=bucket.force_native or migrated_address == bucket.address,
                    source_address=bucket.address,
                    migrated_address=migrated_address,
                    timeline=ProvisionTimeline(address=bucket.address, versions=bucket.versions),
                )
            )
    native_addresses = {
        entry.migrated_address
        for entry in entries
        if entry.is_native_lineage
    }
    migrated_descendant_prefix_paths = {
        entry.migrated_address.path[:prefix_len]
        for entry in entries
        for prefix_len in range(1, len(entry.migrated_address.path))
    }
    migrated_prefix_addresses = {
        outer.migrated_address
        for outer in entries
        if outer.migrated_address.path in migrated_descendant_prefix_paths
    }
    rekeyed: dict[LegalAddress, ProvisionTimeline] = {}
    for entry in sorted(
        entries,
        key=lambda item: (0 if not item.is_native_lineage else 1, str(item.migrated_address), str(item.source_address)),
    ):
        address = entry.source_address
        migrated_address = entry.migrated_address
        timeline = entry.timeline
        source_leaf_label = address.path[-1][1] if address.path else ""
        destination_leaf_label = migrated_address.path[-1][1] if migrated_address.path else ""
        preserve_migrated_history = source_leaf_label == destination_leaf_label
        migrated_versions = list(timeline.versions)
        if migrated_address != address:
            migrated_versions = [
                retarget_version_content_fn(version, migrated_address)
                for version in timeline.versions
            ]
        bucket = rekeyed.get(migrated_address)
        if bucket is None:
            if (
                migrated_address != address
                and migrated_address in native_addresses
                and migrated_address not in migrated_prefix_addresses
                and not preserve_migrated_history
            ):
                continue
            rekeyed[migrated_address] = ProvisionTimeline(
                address=migrated_address,
                versions=migrated_versions,
            )
            continue
        if (
            migrated_address != address
            and migrated_address in native_addresses
            and migrated_address not in migrated_prefix_addresses
            and not preserve_migrated_history
        ):
            continue
        existing_version_keys = {
            (version.effective, version.enacted, version.expires, version.content_hash)
            for version in bucket.versions
        }
        for version in migrated_versions:
            if version.content is None and any(
                existing_version.content is not None
                and existing_version.effective == version.effective
                and existing_version.enacted == version.enacted
                for existing_version in bucket.versions
            ):
                continue
            version_key = (version.effective, version.enacted, version.expires, version.content_hash)
            if version_key in existing_version_keys:
                continue
            bucket.versions.append(version)
            existing_version_keys.add(version_key)
        if merge_bucket_cleanup_fn is not None:
            bucket.versions = merge_bucket_cleanup_fn(list(bucket.versions))
    for timeline in rekeyed.values():
        timeline.versions.sort(key=lambda v: (v.effective, v.enacted))
    return rekeyed


def lineage_segments(
    original_address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> tuple[LineageSegment, ...]:
    """Return typed lineage segments from origin to resolved address."""
    segments = [LineageSegment(from_address=original_address, to_address=original_address)]
    current = original_address
    visited: set[str] = {str(current)}
    ordered_events = sorted(
        (
            event
            for event in migration_events
            if not as_of_date or not event.effective or event.effective <= as_of_date
        ),
        key=migration_event_sort_key,
    )

    changed = True
    while changed:
        changed = False
        for event in ordered_events:
            if not address_prefix_matches(current, event.from_address):
                continue
            prefix_len = len(event.from_address.path)
            next_path = event.to_address.path + current.path[prefix_len:]
            next_addr = LegalAddress(path=next_path, special=current.special)
            addr_key = str(next_addr)
            if addr_key in visited:
                continue
            visited.add(addr_key)
            segments.append(
                LineageSegment(
                    from_address=current,
                    to_address=next_addr,
                    event=event,
                )
            )
            current = next_addr
            changed = True

    return tuple(segments)


def lineage_address_chain(
    original_address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
) -> tuple[LegalAddress, ...]:
    """Return the address chain from the original address to the resolved one."""
    return tuple(
        segment.to_address
        for segment in lineage_segments(
            original_address,
            migration_events,
            as_of_date=as_of_date,
            address_prefix_matches=address_prefix_matches,
        )
    )


def provision_lineage(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    address: LegalAddress,
    *,
    migration_events: tuple[MigrationEvent, ...] = (),
    as_of_date: str = "",
    lineage_address_chain_fn: Callable[..., tuple[LegalAddress, ...]],
) -> list[ProvisionVersion]:
    """Return the complete version history of a provision, oldest first."""
    if migration_events:
        lineage_addresses = lineage_address_chain_fn(
            address,
            migration_events,
            as_of_date=as_of_date,
        )
        versions: list[ProvisionVersion] = []
        for lineage_address in lineage_addresses:
            tl = timelines.get(lineage_address)
            if tl is None:
                continue
            versions.extend(tl.versions)
        return versions
    tl = timelines.get(address)
    if tl is None:
        return []
    return list(tl.versions)


def diff_statute(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    date1: str,
    date2: str,
    *,
    select_active_version_ex_fn: Callable[[ProvisionTimeline, str], _SelectionResult],
) -> dict[LegalAddress, tuple[ProvisionVersion | None, ProvisionVersion | None]]:
    """Find all provisions that changed between two dates."""
    changed: dict[LegalAddress, tuple[ProvisionVersion | None, ProvisionVersion | None]] = {}
    for addr, tl in timelines.items():
        v1 = select_active_version_ex_fn(tl, date1).version
        v2 = select_active_version_ex_fn(tl, date2).version
        if v1 is not v2:
            changed[addr] = (v1, v2)
    return changed


def affecting_acts(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    address: LegalAddress,
) -> list[str]:
    """Return statute_ids of all acts that affected a given provision."""
    tl = timelines.get(address)
    if tl is None:
        return []
    result = []
    for version in tl.versions:
        if version.source and version.source.statute_id:
            sid = version.source.statute_id
            if sid not in result:
                result.append(sid)
    return result


def modified_by_act(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    source_statute_id: str,
) -> list[LegalAddress]:
    """Return all addresses with at least one version sourced from source_statute_id."""
    result: list[LegalAddress] = []
    for address, tl in timelines.items():
        for version in tl.versions:
            if version.source and version.source.statute_id == source_statute_id:
                result.append(address)
                break
    return sorted(result, key=lambda a: a.path)
