"""Owned timeline snapshots for replay-fold provisions missing PIT authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, cast

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, ProvisionTimeline
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal import TemporalEvent
from lawvm.core.timeline import compile_timelines, select_active_version
from lawvm.core.timeline_lineage import prefix_migration_event_signatures
from lawvm.finland.apply_runtime_support import _stamp_exact_section_snapshot_payload

FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID = "fi.replay.fold_timeline_backfill"

_BACKFILL_STRUCTURAL_KINDS = frozenset(
    {
        IRNodeKind.SECTION,
    }
)


@dataclass(frozen=True, slots=True)
class FoldTimelineBackfillRecord:
    """Evidence for one fold-owned timeline snapshot graft."""

    address: str
    source_statute: str
    effective: str
    witness_rule_id: str = FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID


@dataclass(frozen=True, slots=True)
class FoldTimelineBackfillResult:
    """Backfill records plus the preview timelines they were derived from."""

    records: tuple[FoldTimelineBackfillRecord, ...]
    raw_timelines: dict[LegalAddress, ProvisionTimeline]
    rekeyed_timelines: dict[LegalAddress, ProvisionTimeline]
    backfill_ops: tuple[LegalOperation, ...] = ()


def _fold_backfill_op_id(address: LegalAddress) -> str:
    """Return a deterministic op_id keyed by the full section address.

    Finnish chaptered statutes repeat section labels across containers
    (``1 §`` exists in every chapter). Keying the op_id by ``node.label``
    alone collides those distinct sections, so the ``existing_op_ids`` dedup
    drops every same-labelled section after the first and its replay-owned
    content silently vanishes from PIT materialization. Deriving the id from
    the serialized address path keeps true duplicates (same address) deduped
    while distinguishing different-container same-label sections.

    The ``snapshot_section_`` prefix is preserved because downstream replay
    consumers gate on ``op_id.startswith("snapshot_section_")``.
    """
    ancestor_segments = "_".join(
        f"{kind}_{label}" for kind, label in address.path[:-1]
    )
    section_label = address.path[-1][1] if address.path else ""
    suffix = f"_in_{ancestor_segments}" if ancestor_segments else ""
    return f"snapshot_section_{section_label}{suffix}_fold_timeline_backfill"


def _content_is_repeal_placeholder(node: IRNode) -> bool:
    return node.attrs.get("lawvm_repeal_placeholder") == "1"


def _provision_roots(ir: IRNode) -> tuple[IRNode, ...]:
    if (
        len(ir.children) == 1
        and ir.children[0].kind is IRNodeKind.HCONTAINER
        and ir.children[0].attrs.get("name") == "statuteProvisionsWrapper"
    ):
        return ir.children[0].children
    return ir.children


def _iter_fold_section_nodes(
    node: IRNode,
    path: tuple[tuple[str, str], ...] = (),
) -> Iterator[tuple[tuple[tuple[str, str], ...], IRNode]]:
    if node.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER} and node.label:
        path = path + ((node.kind.value, node.label),)
        child_kinds = {IRNodeKind.PART, IRNodeKind.CHAPTER, IRNodeKind.SECTION}
        for child in node.children:
            if child.kind in child_kinds:
                yield from _iter_fold_section_nodes(child, path)
        return
    if node.kind is IRNodeKind.SECTION and node.label:
        yield path + (("section", node.label),), node
        return
    children = _provision_roots(node) if node.kind is IRNodeKind.BODY else node.children
    for child in children:
        if child.kind in {IRNodeKind.PART, IRNodeKind.CHAPTER, IRNodeKind.HCONTAINER}:
            yield from _iter_fold_section_nodes(child, path)


def _address_is_prefix(prefix: LegalAddress, address: LegalAddress) -> bool:
    if len(prefix.path) > len(address.path):
        return False
    return address.path[: len(prefix.path)] == prefix.path


def _migration_source_for_address(
    address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
) -> tuple[str, str]:
    candidates = [
        event
        for event in migration_events
        if event.source_statute
        and event.effective
        and (
            _address_is_prefix(event.to_address, address)
            or _address_is_prefix(address, event.to_address)
            or _address_is_prefix(event.from_address, address)
        )
    ]
    if not candidates:
        return "", ""
    latest = max(candidates, key=lambda event: (event.effective, event.event_id))
    return latest.source_statute, latest.effective


def _active_timeline_content(
    timelines: dict[LegalAddress, ProvisionTimeline],
    address: LegalAddress,
    *,
    as_of: str,
    cache: dict[LegalAddress, IRNode | None] | None = None,
) -> IRNode | None:
    if cache is not None and address in cache:
        return cache[address]
    timeline = timelines.get(address)
    if timeline is None:
        if cache is not None:
            cache[address] = None
        return None
    version = select_active_version(timeline, as_of)
    if version is None or version.content is None:
        if cache is not None:
            cache[address] = None
        return None
    if cache is not None:
        cache[address] = version.content
    return version.content


def _container_includes_section_label(container: IRNode, section_label: str) -> bool:
    return any(
        child.kind is IRNodeKind.SECTION and child.label == section_label
        for child in container.children
    )


def _timeline_intentionally_absent(
    timelines: dict[LegalAddress, ProvisionTimeline],
    address: LegalAddress,
    *,
    as_of: str,
    active_content_cache: dict[LegalAddress, IRNode | None] | None = None,
) -> bool:
    """Return whether PIT absence is already explained by repeal/expiry authority."""
    timeline = timelines.get(address)
    if timeline is None:
        return False
    if _active_timeline_content(timelines, address, as_of=as_of, cache=active_content_cache) is not None:
        return False
    for version in timeline.versions:
        if version.content is None:
            return True
        expires = str(getattr(version, "expires", "") or "")
        if expires and expires <= as_of:
            return True
    return False


def _has_timeline_authority(
    timelines: dict[LegalAddress, ProvisionTimeline],
    address: LegalAddress,
    *,
    as_of: str,
    active_content_cache: dict[LegalAddress, IRNode | None] | None = None,
) -> bool:
    if _timeline_intentionally_absent(
        timelines,
        address,
        as_of=as_of,
        active_content_cache=active_content_cache,
    ):
        return True
    if _active_timeline_content(timelines, address, as_of=as_of, cache=active_content_cache) is not None:
        return True
    if not address.path or address.path[-1][0] != "section":
        return False
    section_label = address.path[-1][1]
    for prefix_len in range(len(address.path) - 1, 0, -1):
        ancestor = LegalAddress(path=address.path[:prefix_len])
        if _timeline_intentionally_absent(
            timelines,
            ancestor,
            as_of=as_of,
            active_content_cache=active_content_cache,
        ):
            continue
        ancestor_content = _active_timeline_content(
            timelines,
            ancestor,
            as_of=as_of,
            cache=active_content_cache,
        )
        if ancestor_content is None:
            continue
        if _container_includes_section_label(ancestor_content, section_label):
            return True
    return False


def _preview_rekeyed_timelines(
    *,
    base_ir: IRStatute,
    lo_ops: list[LegalOperation],
    migration_events: tuple[MigrationEvent, ...],
    as_of: str,
    temporal_events: tuple[object, ...],
    base_enacted_date: str,
    raw_timelines: dict[LegalAddress, ProvisionTimeline] | None = None,
) -> FoldTimelineBackfillResult:
    from lawvm.finland.replay_products import (
        _rekey_timelines_with_migration_events,
        fi_label_norm,
    )

    if raw_timelines is None:
        raw_timelines = compile_timelines(
            base_ir,
            lo_ops,
            base_enacted_date=base_enacted_date,
            label_norm=fi_label_norm,
            temporal_events=cast(tuple[TemporalEvent, ...], temporal_events),
        )
    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    return FoldTimelineBackfillResult(
        records=(),
        raw_timelines=raw_timelines,
        rekeyed_timelines=rekeyed_timelines,
    )


def _active_migration_signature_key(
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> tuple[object, ...]:
    """Return the migration-projection state visible at ``as_of``.

    Prefix migration projection only changes when a migration event becomes
    active. Transition-graph exports materialize many non-migration change
    dates, so caching by this signature avoids rekeying the same timeline map
    for every ordinary amendment date.
    """
    return tuple(
        signature
        for signature in prefix_migration_event_signatures(migration_events)
        if not signature.effective or not as_of or signature.effective <= as_of
    )


def append_fold_timeline_backfill_ops(
    *,
    lo_ops: list[LegalOperation],
    replay_fold_ir: IRNode,
    base_ir: IRNode,
    base_statute_id: str,
    base_title: str = "",
    migration_events: tuple[MigrationEvent, ...],
    as_of: str,
    temporal_events: tuple[object, ...] = (),
    base_enacted_date: str = "",
    preview_raw_timelines: dict[LegalAddress, ProvisionTimeline] | None = None,
    preview_rekeyed_timelines_cache: dict[object, object] | None = None,
) -> FoldTimelineBackfillResult:
    """Append snapshot LOs for fold sections that lack timeline authority.

    Restructure relabel/renumber waves can leave provisions visible in the
    replay fold while timeline compilation only received payload-less RENUMBER
    operations. PIT materialization then drops those sections even though the
    fold state still carries their replay-owned content.
    """
    preview_base = IRStatute(
        statute_id=base_statute_id,
        title=base_title,
        body=base_ir,
    )
    preview_cache: dict[object, object] | None = None
    preview_cache_key: tuple[object, ...] | None = None
    preview: FoldTimelineBackfillResult | None = None
    if preview_raw_timelines is not None and preview_rekeyed_timelines_cache is not None:
        preview_cache = preview_rekeyed_timelines_cache
        preview_cache_key = (
            "fold_backfill_preview_rekeyed_timelines",
            id(preview_raw_timelines),
            len(preview_raw_timelines),
            _active_migration_signature_key(migration_events, as_of=as_of),
        )
        cached_preview = preview_cache.get(preview_cache_key)
        if isinstance(cached_preview, FoldTimelineBackfillResult):
            preview = cached_preview
    if preview is None:
        preview = _preview_rekeyed_timelines(
            base_ir=preview_base,
            lo_ops=lo_ops,
            migration_events=migration_events,
            as_of=as_of,
            temporal_events=temporal_events,
            base_enacted_date=base_enacted_date,
            raw_timelines=preview_raw_timelines,
        )
        if preview_cache is not None and preview_cache_key is not None:
            preview_cache[preview_cache_key] = preview
    existing_op_ids = {op.op_id for op in lo_ops}
    records: list[FoldTimelineBackfillRecord] = []
    backfill_ops: list[LegalOperation] = []
    active_content_cache: dict[LegalAddress, IRNode | None] = {}
    for path, node in _iter_fold_section_nodes(replay_fold_ir):
        if _content_is_repeal_placeholder(node):
            continue
        address = LegalAddress(path=path)
        if _has_timeline_authority(
            preview.rekeyed_timelines,
            address,
            as_of=as_of,
            active_content_cache=active_content_cache,
        ):
            continue
        source_statute, effective = _migration_source_for_address(
            address,
            migration_events,
        )
        if not source_statute or not effective:
            source_statute = base_statute_id
            effective = as_of
        op_id = _fold_backfill_op_id(address)
        if op_id in existing_op_ids:
            continue
        backfill_op = LegalOperation(
            op_id=op_id,
            sequence=0,
            action=StructuralAction.INSERT,
            target=address,
            payload=_stamp_exact_section_snapshot_payload(node),
            source=OperationSource(
                statute_id=source_statute,
                title="Fold timeline backfill",
                enacted=effective,
                effective=effective,
                raw_text="",
            ),
            group_id=f"finland-fold-backfill:{source_statute}:{address}",
            witness_rule_id=FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID,
        )
        lo_ops.append(backfill_op)
        backfill_ops.append(backfill_op)
        existing_op_ids.add(op_id)
        records.append(
            FoldTimelineBackfillRecord(
                address=str(address),
                source_statute=source_statute,
                effective=effective,
            )
        )
    return FoldTimelineBackfillResult(
        records=tuple(records),
        raw_timelines=preview.raw_timelines,
        rekeyed_timelines=preview.rekeyed_timelines,
        backfill_ops=tuple(backfill_ops),
    )


__all__ = [
    "FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID",
    "FoldTimelineBackfillRecord",
    "FoldTimelineBackfillResult",
    "append_fold_timeline_backfill_ops",
]
