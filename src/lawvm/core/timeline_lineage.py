"""Lineage and lightweight query helpers for timeline consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.provenance import MigrationEvent, migration_event_sort_key
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.timeline_results import (
    MaterializationLineageDecision,
    MaterializationLineagePlan,
)


class _SelectionResult(Protocol):
    version: ProvisionVersion | None


@dataclass(frozen=True)
class LineageSegment:
    from_address: LegalAddress
    to_address: LegalAddress
    event: MigrationEvent | None = None


@dataclass(frozen=True)
class ScopeMigrationClassification:
    active_scope_changing: bool
    noncolliding: bool
    destination_occupancy_collision: bool


@dataclass(frozen=True)
class MaterializationLineageBridgeClassification:
    """Typed bridge-family classification for PIT lineage planning."""

    native_rebirth_after_renumber: bool = False
    leaf_stable_scope_renumber: bool = False
    active_scope_changing: bool = False
    noncolliding_scope_migrations: bool = False
    destination_occupancy_collision: bool = False


type _RetargetVersionContentFn = Callable[[ProvisionVersion, LegalAddress], ProvisionVersion]
type _MergeBucketCleanupFn = Callable[[list[ProvisionVersion]], list[ProvisionVersion]]


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
        matching_renumbers = [
            event
            for event in migration_events
            if event.kind == "renumber"
            and event.effective
            and address_prefix_matches(address, event.from_address)
        ]
        if not matching_renumbers:
            continue
        event = sorted(matching_renumbers, key=migration_event_sort_key)[0]
        has_before = any(version.effective < event.effective for version in timeline.versions)
        has_after = any(version.effective >= event.effective for version in timeline.versions)
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
    normalize_address_fn: Callable[[LegalAddress], LegalAddress] | None = None,
) -> LegalAddress:
    """Follow renumber/move links across any matching address prefix.

    Same-wave migration clauses are interpreted against the wave's pre-act
    reference frame, then applied in specificity order. Frontends may supply
    an address normalizer when migration matching needs jurisdiction-local
    label normalization, but the wave/prefix execution semantics are shared.
    """

    normalize = normalize_address_fn or (lambda address: address)
    current = normalize(original_address)
    visited: set[str] = {str(current)}
    waves: dict[tuple[str, str], list[MigrationEvent]] = {}
    for event in migration_events:
        if as_of_date and event.effective and event.effective > as_of_date:
            continue
        source_statute = event.source_statute if event.source_statute is not None else ""
        waves.setdefault((event.effective, source_statute), []).append(event)

    for wave_events in sorted(
        waves.values(),
        key=lambda events: (events[0].effective, events[0].source_statute if events[0].source_statute is not None else ""),
    ):
        wave_start = normalize(current)
        applicable_wave_events: list[MigrationEvent] = []
        for event in sorted(
            wave_events,
            key=lambda item: (
                len(item.from_address.path),
                str(item.from_address),
                str(item.to_address),
            ),
            reverse=True,
        ):
            normalized_event_from = normalize(event.from_address)
            if wave_start.path[: len(normalized_event_from.path)] != normalized_event_from.path:
                continue
            applicable_wave_events.append(event)
        for event in applicable_wave_events:
            normalized_event_from = normalize(event.from_address)
            normalized_current = normalize(current)
            if normalized_current.path[: len(normalized_event_from.path)] != normalized_event_from.path:
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
    address_prefix_matches: Callable[[LegalAddress, LegalAddress], bool],
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

    def _has_prior_incoming_migration_prefix(
        address: LegalAddress,
        *,
        before_effective: str,
    ) -> bool:
        return any(
            prior_event.effective < before_effective
            and address_prefix_matches(address, prior_event.to_address)
            for prior_event in migration_events
            if prior_event.kind == "renumber" and prior_event.effective
        )

    def _has_same_wave_incoming_migration_prefix(
        address: LegalAddress,
        *,
        at_effective: str,
    ) -> bool:
        return any(
            event.effective == at_effective
            and address_prefix_matches(address, event.to_address)
            for event in migration_events
            if event.kind == "renumber" and event.effective
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
    ) -> list[tuple[LegalAddress, list[ProvisionVersion], bool]]:
        matching_renumbers = [
            event
            for event in migration_events
            if event.kind == "renumber"
            and event.effective
            and address_prefix_matches(address, event.from_address)
        ]
        if not matching_renumbers:
            return [(address, versions, False)]
        event = sorted(matching_renumbers, key=migration_event_sort_key)[0]
        before_versions = [version for version in versions if version.effective < event.effective]
        native_versions = [version for version in versions if version.effective >= event.effective]
        if before_versions and not native_versions:
            return [(address, versions, False)]
        if native_versions and not before_versions:
            same_wave_incoming = _has_same_wave_incoming_migration_prefix(
                address,
                at_effective=event.effective,
            )
            return [
                (
                    address,
                    versions,
                    _has_prior_incoming_migration_prefix(
                        address,
                        before_effective=event.effective,
                    )
                    or (
                        not same_wave_incoming
                        and _source_prefix_has_native_rebirth(
                            event.from_address,
                            at_effective=event.effective,
                        )
                    ),
                )
            ]
        buckets: list[tuple[LegalAddress, list[ProvisionVersion], bool]] = []
        if before_versions:
            buckets.append((address, before_versions, False))
        if native_versions:
            buckets.append((address, native_versions, True))
        return buckets

    entries: list[tuple[bool, LegalAddress, LegalAddress, ProvisionTimeline]] = []
    for address, timeline in timelines.items():
        split_buckets = _split_versions_at_native_renumber_boundary(address, list(timeline.versions))
        for bucket_address, bucket_versions, force_native in split_buckets:
            migrated_address = (
                bucket_address
                if force_native
                else current_address_with_prefix_migrations_fn(
                    bucket_address,
                    migration_events,
                    as_of_date,
                )
            )
            entries.append(
                (
                    force_native or migrated_address == bucket_address,
                    bucket_address,
                    migrated_address,
                    ProvisionTimeline(address=bucket_address, versions=bucket_versions),
                )
            )
    native_addresses = {
        migrated_address
        for is_native_lineage, _address, migrated_address, _timeline in entries
        if is_native_lineage
    }
    migrated_prefix_addresses = {
        outer_migrated
        for _outer_is_native, _outer_address, outer_migrated, _outer_timeline in entries
        if any(
            inner_migrated.path[: len(outer_migrated.path)] == outer_migrated.path
            and len(inner_migrated.path) > len(outer_migrated.path)
            for _inner_is_native, _inner_address, inner_migrated, _inner_timeline in entries
        )
    }
    rekeyed: dict[LegalAddress, ProvisionTimeline] = {}
    for _is_native_lineage, address, migrated_address, timeline in sorted(
        entries,
        key=lambda item: (0 if not item[0] else 1, str(item[2]), str(item[1])),
    ):
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
