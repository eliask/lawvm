"""Typed legal-time write-interval scheduling for provision timelines.

This is a narrow bridge from already-compiled ``LegalOperation`` payloads to
PIT read-model timelines.  It does not parse source text and it does not
authorize replay mutations.  If the compiled operation, window diagnostic, and
deferred occupant version do not agree exactly, the caller keeps the existing
timeline-integrity break.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Protocol, TypeIs

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
)
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.semantic_types import StructuralAction


@dataclass(frozen=True, slots=True)
class TemporalWriteInterval:
    """One proved write payload occupying a legal-time interval."""

    write_id: str
    op_id: str
    fold_sequence: int
    target_address: LegalAddress
    action: str
    effective: str
    expires: str
    enacted: str
    variant_kind: str
    payload_hash: str
    source_work_id: str
    source_locator: str
    receipt_id: str
    origin_rule_id: str
    provenance_findings: tuple[str, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "write_id": self.write_id,
            "op_id": self.op_id,
            "fold_sequence": self.fold_sequence,
            "target_address": str(self.target_address),
            "action": self.action,
            "effective": self.effective,
            "expires": self.expires,
            "bounds": "start_inclusive_end_exclusive",
            "enacted": self.enacted,
            "variant_kind": self.variant_kind,
            "payload_hash": self.payload_hash,
            "source_work_id": self.source_work_id,
            "source_locator": self.source_locator,
            "receipt_id": self.receipt_id,
            "origin_rule_id": self.origin_rule_id,
            "provenance_findings": list(self.provenance_findings),
        }


@dataclass(frozen=True, slots=True)
class TemporalScheduleDelta:
    """Read-model delta produced by scheduling a temporal write interval."""

    schedule_status: str
    scheduled_version_id: str
    interval: TemporalWriteInterval
    diagnostic_code: str
    occupant_source_work_id: str
    occupant_effective: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "schedule_status": self.schedule_status,
            "scheduled_version_id": self.scheduled_version_id,
            "diagnostic_code": self.diagnostic_code,
            "occupant_source_work_id": self.occupant_source_work_id,
            "occupant_effective": self.occupant_effective,
            "interval": self.interval.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class TemporalScheduleResult:
    """Output of applying proved temporal windows to timelines."""

    timelines: Mapping[LegalAddress, ProvisionTimeline]
    deltas: tuple[TemporalScheduleDelta, ...]
    unresolved_breaks: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class TemporalWindowPayload:
    """Proved temporal interval plus payload selected for scheduling."""

    interval: TemporalWriteInterval
    payload: IRNode
    applicability: tuple[ScopePredicate, ...]


class _TimelineBreakLike(Protocol):
    amendment_id: str
    diagnostic_code: str
    scope: str
    target_section: str
    reason: str
    window_start: str
    window_end: str
    occupant_source_statute: str
    occupant_effective: str
    rule_id: str


_TIMELINE_BREAK_FIELDS = (
    "amendment_id",
    "diagnostic_code",
    "scope",
    "target_section",
    "reason",
    "window_start",
    "window_end",
    "occupant_source_statute",
    "occupant_effective",
    "rule_id",
)


def _is_timeline_break_like(item: object) -> TypeIs[_TimelineBreakLike]:
    return all(hasattr(item, field_name) for field_name in _TIMELINE_BREAK_FIELDS)


def materialize_temporal_write_windows(
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    ops: Iterable[LegalOperation],
    timeline_breaks: Iterable[Any],
) -> TemporalScheduleResult:
    """Splice proved temporary-twin windows into provision timelines.

    The only accepted Stage-1 family is the fail-loud window diagnostic emitted
    for a temporary insert whose legal interval ends exactly when a deferred
    permanent occupant begins.  Unproved windows are returned unchanged in
    ``unresolved_breaks``.
    """

    op_list = tuple(ops)
    next_timelines: dict[LegalAddress, ProvisionTimeline] = dict(timelines)
    deltas: list[TemporalScheduleDelta] = []
    unresolved: list[Any] = []
    for item in timeline_breaks:
        if not _is_timeline_break_like(item) or str(item.scope or "") != "window":
            unresolved.append(item)
            continue
        interval_and_payload = _interval_for_window(item, op_list)
        if interval_and_payload is None:
            unresolved.append(item)
            continue
        timeline = next_timelines.get(interval_and_payload.interval.target_address)
        if timeline is None or not _window_has_deferred_occupant(item, timeline):
            unresolved.append(item)
            continue
        interval = interval_and_payload.interval
        if not (interval.effective and interval.expires and interval.effective < interval.expires):
            unresolved.append(item)
            continue
        occupant_effective = str(item.occupant_effective or "")
        if occupant_effective and interval.expires > occupant_effective:
            unresolved.append(item)
            continue
        if _timeline_already_has_interval(timeline, interval):
            continue
        version = ProvisionVersion(
            effective=interval.effective,
            enacted=interval.enacted,
            expires=interval.expires,
            variant_kind="temporary",
            content=interval_and_payload.payload,
            source=_matching_source(interval, op_list),
            applicability=list(interval_and_payload.applicability),
            content_hash=interval.payload_hash,
        )
        scheduled_versions = [*timeline.versions, version]
        scheduled_versions.sort(
            key=lambda v: (
                v.effective,
                v.expires or "9999-99-99",
                v.variant_kind,
                _version_source_id(v),
                v.content_hash,
            )
        )
        next_timelines[interval.target_address] = replace(timeline, versions=scheduled_versions)
        deltas.append(
            TemporalScheduleDelta(
                schedule_status="materialized",
                scheduled_version_id=_scheduled_version_id(interval),
                interval=interval,
                diagnostic_code=str(item.diagnostic_code or ""),
                occupant_source_work_id=str(item.occupant_source_statute or ""),
                occupant_effective=occupant_effective,
            )
        )
    return TemporalScheduleResult(
        timelines=next_timelines,
        deltas=tuple(deltas),
        unresolved_breaks=tuple(unresolved),
    )


def _interval_for_window(
    item: _TimelineBreakLike,
    ops: tuple[LegalOperation, ...],
) -> TemporalWindowPayload | None:
    source_work_id = str(item.amendment_id or "")
    target_section = str(item.target_section or "")
    effective = str(item.window_start or "")
    expires = str(item.window_end or "")
    rule_id = str(item.rule_id or "") or str(item.reason or "")
    if not (source_work_id and target_section and effective and expires):
        return None
    candidates = [
        op
        for op in ops
        if _op_source_id(op) == source_work_id
        and _label_key(op.target.leaf_label()) == _label_key(target_section)
        and op.target.leaf_kind() == "section"
        and op.payload is not None
        and op.action in {StructuralAction.INSERT, StructuralAction.REPLACE}
    ]
    exact = [
        op
        for op in candidates
        if op.source is not None
        and op.source.effective == effective
        and op.source.expires == expires
        and isinstance(op.payload, IRNode)
    ]
    if len(exact) != 1:
        return None
    op = exact[0]
    assert op.source is not None
    assert isinstance(op.payload, IRNode)
    payload_hash = irnode_content_hash(op.payload)
    interval = TemporalWriteInterval(
        write_id=_write_id(op, payload_hash),
        op_id=op.op_id,
        fold_sequence=op.sequence,
        target_address=op.target,
        action=_action_value(op.action),
        effective=effective,
        expires=expires,
        enacted=op.source.enacted,
        variant_kind="temporary",
        payload_hash=payload_hash,
        source_work_id=source_work_id,
        source_locator=f"finlex:{source_work_id}",
        receipt_id=f"temporal-window:{source_work_id}:{op.op_id}:{payload_hash}",
        origin_rule_id=rule_id or "temporally_disjoint_twin_insert",
        provenance_findings=("TEMPORAL.WINDOW_UNMATERIALIZED",),
    )
    return TemporalWindowPayload(
        interval=interval,
        payload=op.payload,
        applicability=tuple(op.applicability),
    )


def _window_has_deferred_occupant(item: _TimelineBreakLike, timeline: ProvisionTimeline) -> bool:
    occupant_effective = str(item.occupant_effective or "")
    occupant_source = str(item.occupant_source_statute or "")
    if not occupant_effective:
        return False
    for version in timeline.versions:
        if version.effective != occupant_effective:
            continue
        if occupant_source and _version_source_id(version) != occupant_source:
            continue
        if version.content is None:
            continue
        return True
    return False


def _timeline_already_has_interval(
    timeline: ProvisionTimeline,
    interval: TemporalWriteInterval,
) -> bool:
    def _version_hash(version: ProvisionVersion) -> str:
        if version.content_hash:
            return version.content_hash
        if version.content is None:
            return ""
        return irnode_content_hash(version.content)

    return any(
        version.effective == interval.effective
        and version.expires == interval.expires
        and version.variant_kind == interval.variant_kind
        and _version_source_id(version) == interval.source_work_id
        and _version_hash(version) == interval.payload_hash
        for version in timeline.versions
    )


def _matching_source(interval: TemporalWriteInterval, ops: tuple[LegalOperation, ...]) -> Any:
    for op in ops:
        if op.op_id == interval.op_id and _op_source_id(op) == interval.source_work_id:
            return op.source
    return None


def _op_source_id(op: LegalOperation) -> str:
    return str(op.source.statute_id if op.source is not None else "")


def _version_source_id(version: ProvisionVersion) -> str:
    return str(version.source.statute_id if version.source is not None else "")


def _action_value(action: StructuralAction) -> str:
    return action.value


def _label_key(label: str) -> str:
    return str(label or "").replace(" ", "").replace("§", "").lower()


def _write_id(op: LegalOperation, payload_hash: str) -> str:
    return "temporal-write:" + _sha256(
        {
            "op_id": op.op_id,
            "sequence": op.sequence,
            "target": str(op.target),
            "source_work_id": _op_source_id(op),
            "payload_hash": payload_hash,
        }
    )


def _scheduled_version_id(interval: TemporalWriteInterval) -> str:
    return "temporal-version:" + _sha256(
        {
            "write_id": interval.write_id,
            "target": str(interval.target_address),
            "effective": interval.effective,
            "expires": interval.expires,
            "payload_hash": interval.payload_hash,
        }
    )


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "TemporalScheduleDelta",
    "TemporalScheduleResult",
    "TemporalWriteInterval",
    "materialize_temporal_write_windows",
]
