"""Private temporal-event helper cluster for timeline compilation.

This module owns the matching, override, and standalone-event execution
helpers that feed ``compile_timelines()``. The public issue/result surfaces
remain in ``timeline.py``; callers provide selection and issue-recording
callbacks so this module stays cycle-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from lawvm.core.ir import LegalAddress, LegalOperation, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.temporal import (
    FIXED_DATE_KIND,
    IMMEDIATE_KIND,
    PENDING_CONDITION_KIND,
    PENDING_DECREE_KIND,
    TemporalEvent,
    TemporalScope,
)


@dataclass(frozen=True)
class TemporalOpOverrides:
    matched: bool = False
    effective: str = ""
    expires: str = ""
    applicability: tuple[Any, ...] = ()
    unsupported_applicability_dimensions: tuple[str, ...] = ()
    has_contingent: bool = False


IssueRecorder = Callable[..., None]
LatestEligibleVersionPicker = Callable[[ProvisionTimeline, str], ProvisionVersion | None]
LatestSubstantiveVersionPicker = Callable[[ProvisionTimeline, str], ProvisionVersion | None]


def matches_prefix(address: LegalAddress, prefix: LegalAddress) -> bool:
    if len(prefix.path) > len(address.path):
        return False
    if address.path[: len(prefix.path)] != prefix.path:
        return False
    if prefix.special:
        return prefix.special == address.special
    return True


def matches_scope_addresses(
    scope: TemporalScope,
    addresses: tuple[LegalAddress, ...],
) -> bool:
    if scope.exact_addresses:
        if any(address in addresses for address in scope.exact_addresses):
            pass
        elif scope.include_future_descendants and any(
            matches_prefix(address, exact)
            for address in addresses
            for exact in scope.exact_addresses
        ):
            pass
        else:
            return False
    if scope.address_prefixes and not any(
        matches_prefix(address, prefix)
        for address in addresses
        for prefix in scope.address_prefixes
    ):
        return False
    return True


def scope_matches_exactly(
    scope: TemporalScope,
    addresses: tuple[LegalAddress, ...],
) -> bool:
    """Return True iff the scope matches via a direct exact_addresses hit (not descendant).

    Used to prefer a more-specific expire event over a broader ancestor-derived one.
    """
    if not scope.exact_addresses:
        return False
    return any(address in addresses for address in scope.exact_addresses)


def matches_temporal_scope(
    scope: TemporalScope,
    *,
    target_statute: str,
    addresses: tuple[LegalAddress, ...],
) -> bool:
    if target_statute and scope.target_statute and scope.target_statute != target_statute:
        return False
    return matches_scope_addresses(scope, addresses)


def matching_temporal_events_for_op(
    op: LegalOperation,
    temporal_events: tuple[TemporalEvent, ...],
    *,
    target_statute: str = "",
    touched_addresses: tuple[LegalAddress, ...] | None = None,
) -> tuple[TemporalEvent, ...]:
    if not temporal_events or not op.group_id:
        return ()
    if touched_addresses is None:
        touched = [op.target]
        if op.destination is not None and op.destination not in touched:
            touched.append(op.destination)
        touched_addresses = tuple(touched)
    return tuple(
        event
        for event in temporal_events
        if event.group_id == op.group_id
        and matches_temporal_scope(
            event.scope,
            target_statute=target_statute,
            addresses=touched_addresses,
        )
    )


def temporal_overrides_for_op(
    op: LegalOperation,
    temporal_events: tuple[TemporalEvent, ...],
    *,
    target_statute: str = "",
    touched_addresses: tuple[LegalAddress, ...] | None = None,
) -> TemporalOpOverrides:
    """Derive additive temporal overrides for one op from matching TemporalEvents."""
    matches = matching_temporal_events_for_op(
        op,
        temporal_events,
        target_statute=target_statute,
        touched_addresses=touched_addresses,
    )
    if not matches:
        return TemporalOpOverrides()

    effective_candidates: list[str] = []
    expiry_candidates: list[str] = []
    contingent = False
    applicability_preds: list[Any] = []
    unsupported_applicability_dimensions: set[str] = set()
    for event in matches:
        rule = event.activation_rule
        if event.kind in {"commence", "revive"}:
            if event.effective:
                effective_candidates.append(event.effective)
                continue
            if rule is not None:
                if rule.kind == FIXED_DATE_KIND and rule.effective_date:
                    effective_candidates.append(rule.effective_date)
                elif rule.kind == IMMEDIATE_KIND and rule.effective_date:
                    effective_candidates.append(rule.effective_date)
                elif rule.kind in {PENDING_DECREE_KIND, PENDING_CONDITION_KIND}:
                    contingent = True
            continue
        if event.kind in {"expire", "suspend"} and event.expires:
            expiry_candidates.append(event.expires)
            continue
        if rule is not None and rule.kind in {PENDING_DECREE_KIND, PENDING_CONDITION_KIND}:
            contingent = True
        if event.kind == "set_applicability" and event.scope.predicates:
            for predicate in event.scope.predicates:
                if predicate.dimension == "territory":
                    applicability_preds.append(predicate)
                else:
                    unsupported_applicability_dimensions.add(predicate.dimension)
    expiry_candidates = sorted(expiry_candidates)
    return TemporalOpOverrides(
        matched=True,
        effective=sorted(effective_candidates)[0] if effective_candidates else "",
        expires=expiry_candidates[0] if expiry_candidates else "",
        applicability=tuple(applicability_preds),
        unsupported_applicability_dimensions=tuple(sorted(unsupported_applicability_dimensions)),
        has_contingent=contingent,
    )


def op_sort_date(
    op: LegalOperation,
    temporal_events: tuple[TemporalEvent, ...],
    *,
    target_statute: str = "",
    touched_addresses: tuple[LegalAddress, ...] | None = None,
) -> str:
    """Best chronological key for one op using explicit temporal carriers."""
    overrides = temporal_overrides_for_op(
        op,
        temporal_events,
        target_statute=target_statute,
        touched_addresses=touched_addresses,
    )
    return overrides.effective


def temporal_event_execution_date(event: TemporalEvent) -> str:
    if event.kind in {"expire", "suspend"}:
        return event.expires
    if event.effective:
        return event.effective
    if event.activation_rule is not None and event.activation_rule.kind == FIXED_DATE_KIND:
        return event.activation_rule.effective_date
    return ""


def scope_target_addresses_for_event(
    event: TemporalEvent,
    *,
    target_statute: str,
    timelines: dict[LegalAddress, ProvisionTimeline],
) -> tuple[LegalAddress, ...]:
    if target_statute and event.scope.target_statute and event.scope.target_statute != target_statute:
        return ()
    if not event.scope.exact_addresses and not event.scope.address_prefixes:
        return tuple(timelines.keys())
    return tuple(
        address
        for address in timelines
        if matches_scope_addresses(event.scope, (address,))
    )


def apply_standalone_temporal_event(
    event: TemporalEvent,
    timelines: dict[LegalAddress, ProvisionTimeline],
    *,
    target_statute: str,
    issue_sink: Any,
    emit_warnings: bool,
    record_issue: IssueRecorder,
    latest_eligible_version_without_scope: LatestEligibleVersionPicker,
    latest_substantive_version_at_or_before: LatestSubstantiveVersionPicker,
) -> None:
    event_date = temporal_event_execution_date(event)
    target_addresses = scope_target_addresses_for_event(
        event,
        target_statute=target_statute,
        timelines=timelines,
    )
    if not target_addresses:
        record_issue(
            issue_sink,
            kind="temporal_event_not_matched",
            message=(
                "compile_timelines: standalone TemporalEvent did not match any "
                f"timeline address (event_id={event.event_id!r}, kind={event.kind!r})"
            ),
            source_statute=event.source.statute_id if event.source else "",
            emit_warnings=emit_warnings,
        )
        return
    if event.kind == "set_applicability":
        if not event_date:
            record_issue(
                issue_sink,
                kind="missing_operation_date",
                message=(
                    "compile_timelines: skipping standalone set_applicability "
                    f"event {event.event_id!r} — no explicit effective date available"
                ),
                source_statute=event.source.statute_id if event.source else "",
                emit_warnings=emit_warnings,
            )
            return
        supported_preds = tuple(pred for pred in event.scope.predicates if pred.dimension == "territory")
        unsupported_dims = tuple(
            sorted({pred.dimension for pred in event.scope.predicates if pred.dimension != "territory"})
        )
        if unsupported_dims:
            record_issue(
                issue_sink,
                kind="unsupported_applicability_dimension",
                message=(
                    "compile_timelines: ignoring unsupported standalone applicability "
                    f"predicates {unsupported_dims!r} for event_id={event.event_id!r}; "
                    "only territory applicability is executable in core"
                ),
                source_statute=event.source.statute_id if event.source else "",
                emit_warnings=emit_warnings,
            )
        if not supported_preds:
            return
        for address in target_addresses:
            timeline = timelines[address]
            active = latest_eligible_version_without_scope(timeline, event_date)
            if active is None or active.content is None:
                record_issue(
                    issue_sink,
                    kind="temporal_event_not_matched",
                    message=(
                        "compile_timelines: standalone set_applicability event "
                        f"{event.event_id!r} found no active content for {address}"
                    ),
                    address=address,
                    source_statute=event.source.statute_id if event.source else "",
                    emit_warnings=emit_warnings,
                )
                continue
            timeline.versions.append(
                ProvisionVersion(
                    effective=event_date,
                    enacted=active.enacted,
                    expires=active.expires,
                    variant_kind=active.variant_kind,
                    content=active.content,
                    source=event.source,
                    applicability=list(supported_preds),
                    content_hash=irnode_content_hash(active.content),
                )
            )
        return
    if not event_date:
        record_issue(
            issue_sink,
            kind="missing_operation_date",
            message=(
                "compile_timelines: skipping standalone TemporalEvent "
                f"{event.event_id!r} — no explicit executable date available"
            ),
            source_statute=event.source.statute_id if event.source else "",
            emit_warnings=emit_warnings,
        )
        return
    for address in target_addresses:
        timeline = timelines[address]
        if event.kind in {"expire", "suspend"}:
            active = latest_eligible_version_without_scope(timeline, event_date)
            if active is None or active.content is None:
                record_issue(
                    issue_sink,
                    kind="temporal_event_not_matched",
                    message=(
                        "compile_timelines: standalone temporal end event "
                        f"{event.event_id!r} found no active content for {address}"
                    ),
                    address=address,
                    source_statute=event.source.statute_id if event.source else "",
                    emit_warnings=emit_warnings,
                )
                continue
            active.expires = event_date
            active.variant_kind = "temporary"
            continue
        if event.kind in {"commence", "revive"}:
            active = latest_eligible_version_without_scope(timeline, event_date)
            if active is not None and active.content is not None:
                continue
            source_version = latest_substantive_version_at_or_before(timeline, event_date)
            if source_version is None or source_version.content is None:
                record_issue(
                    issue_sink,
                    kind="temporal_event_not_matched",
                    message=(
                        "compile_timelines: standalone temporal start event "
                        f"{event.event_id!r} found no prior substantive content for {address}"
                    ),
                    address=address,
                    source_statute=event.source.statute_id if event.source else "",
                    emit_warnings=emit_warnings,
                )
                continue
            timeline.versions.append(
                ProvisionVersion(
                    effective=event_date,
                    enacted=event.source.enacted if event.source and event.source.enacted else source_version.enacted,
                    content=source_version.content,
                    source=event.source,
                    applicability=list(source_version.applicability),
                    content_hash=irnode_content_hash(source_version.content),
                )
            )
