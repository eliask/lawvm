"""Temporal layer for LawVM: compile_timelines + materialize_pit.

Phase 7 implementation. Three ingestion patterns:
  ops-first — compile_timelines(base, ops)
  states-first — ingest_uk_snapshots({date: IRStatute})
  dual — consolidated text as oracle, amendment chain as verification

All three produce the same output type: Dict[LegalAddress, ProvisionTimeline].
Query functions (Phase 8): select_active_version, diff_statute, provision_lineage.

API tier
--------
Stable kernel query/materialization surface, with explicitly marked policy seams
where policy remains caller-specific (for example default applicability
behavior when required scope is omitted).

Executable temporal authority is carried by explicit temporal events.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.statute_facets import is_statute_title_address, statute_title_address
from lawvm.core.temporal import TemporalEvent
from lawvm.core.timeline_addresses import (
    _address_prefix_matches,
    _iter_nodes_with_address,
    _iter_statute_nodes_with_address,
    _retarget_root_node,
    _sort_label_key,
)
from lawvm.core.timeline_consistency import (
    ConsistencyDivergence,
    ingest_consolidated,
    ingest_uk_snapshots,
    verify_consistency,
)
from lawvm.core.timeline_lineage import (
    affecting_acts as _affecting_acts,
    current_address_from_migration_events as _current_address_from_migration_events,
    diff_statute as _diff_statute,
    lineage_address_chain as _lineage_address_chain_helper,
    modified_by_act as _modified_by_act,
    provision_lineage as _provision_lineage,
)
from lawvm.core.timeline_materialization import (
    MaterializationSelectionState as _MaterializationSelectionState,
    apply_overlays as _apply_overlays_impl,
    materialize_body as _materialize_body,
    materialize_root_nodes as _materialize_root_nodes,
    project_materialization_selection_states as _project_materialization_selection_states,
    top_level_supplement_active as _top_level_supplement_active,
)
from lawvm.core.timeline_results import (
    MaterializationCertificate,
    MaterializationLineagePlan,
    MaterializationResult,
    MaterializationStatus,
    TimelineCompilationResult,
    TimelineIssue,
    TimelineIssueKind,
    Timelines,
)
from lawvm.core.timeline_selection import (
    VersionSelectionResult,
    content_is_repeal_placeholder as _content_is_repeal_placeholder,
    eligible as _eligible,
    equal_rank_same_source_conflicts as _equal_rank_same_source_conflicts,
    pick_latest as _pick_latest,
    select_active_version as _select_active_version,
    select_active_version_ex as _select_active_version_ex,
    select_background_version as _select_background_version,
    select_temporary_version as _select_temporary_version,
)
from lawvm.core.timeline_temporal_events import (
    apply_standalone_temporal_event as _apply_standalone_temporal_event,
    matching_temporal_events_for_op as _matching_temporal_events_for_op,
    op_sort_date as _op_sort_date,
    temporal_event_execution_date as _temporal_event_execution_date,
    temporal_overrides_for_op as _temporal_overrides_for_op,
)

# Compatibility re-exports while timeline address helpers migrate out.
_TIMELINE_ADDRESS_COMPAT_EXPORTS = (_iter_nodes_with_address,)
_TIMELINE_LINEAGE_COMPAT_EXPORTS = (_current_address_from_migration_events, _lineage_address_chain_helper)
_TIMELINE_CONSISTENCY_COMPAT_EXPORTS = (ConsistencyDivergence, ingest_consolidated, ingest_uk_snapshots, verify_consistency)

_BODY_TOP_LEVEL_KINDS: frozenset[str] = frozenset(
    {"part", "chapter", "section", "division", "recital", "preamble", "p1group", "final"}
)
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR = (
    "lawvm_materialize_as_absent_under_detached_horizon"
)

def _apply_overlays(
    content: IRNode,
    parent_address: LegalAddress,
    active: Dict[LegalAddress, Optional[IRNode]],
    label_norm: Optional[Callable[[str], str]] = None,
    active_prefixes: Optional[Set[Tuple[Tuple[str, str], ...]]] = None,
    issue_sink: Optional[List[TimelineIssue]] = None,
    emit_warnings: bool = True,
) -> IRNode:
    return _apply_overlays_impl(
        content,
        parent_address,
        active,
        label_norm=label_norm,
        active_prefixes=active_prefixes,
        issue_sink=issue_sink,
        emit_warnings=emit_warnings,
        record_issue=_record_timeline_issue,
    )


_TIMELINE_MATERIALIZATION_COMPAT_EXPORTS = (_sort_label_key, _apply_overlays)

def _latest_eligible_version_without_scope(
    timeline: ProvisionTimeline,
    as_of: str,
) -> Optional[ProvisionVersion]:
    return _pick_latest(
        [v for v in timeline.versions if _eligible(v, as_of, "governing")]
    )


def _latest_substantive_version_at_or_before(
    timeline: ProvisionTimeline,
    as_of: str,
) -> Optional[ProvisionVersion]:
    return _pick_latest(
        [
            v
            for v in timeline.versions
            if v.effective <= as_of and v.content is not None
        ]
    )


def _record_timeline_issue(
    issue_sink: Optional[List[TimelineIssue]],
    *,
    kind: TimelineIssueKind,
    message: str,
    address: Optional[LegalAddress] = None,
    source_statute: str = "",
    emit_warnings: bool = True,
) -> None:
    issue = TimelineIssue(
        kind=kind,
        message=message,
        address=address,
        source_statute=source_statute,
    )
    if issue_sink is not None:
        issue_sink.append(issue)


# ---------------------------------------------------------------------------
# Core query: select active version at a date
# ---------------------------------------------------------------------------


def select_active_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: Literal["governing", "in_force"] = "governing",
    territory: Optional[str] = None,
) -> Optional[ProvisionVersion]:
    return _select_active_version(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
    )


def select_background_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: Literal["governing", "in_force"] = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    return _select_background_version(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )


def select_temporary_version(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: Literal["governing", "in_force"] = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> Optional[ProvisionVersion]:
    return _select_temporary_version(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )


def select_active_version_ex(
    timeline: ProvisionTimeline,
    as_of: str,
    query_type: Literal["governing", "in_force"] = "governing",
    territory: Optional[str] = None,
    expires_as_of: str = "",
) -> VersionSelectionResult:
    return _select_active_version_ex(
        timeline,
        as_of,
        query_type=query_type,
        territory=territory,
        expires_as_of=expires_as_of,
    )


# ---------------------------------------------------------------------------
# compile_timelines: ops-first path
# ---------------------------------------------------------------------------
# TODO: Removed @icontract.ensure(lambda result: all(k.path for k in result))
# because icontract's AST parser fails on the complex lambda in some call stacks
# (broke 2017/320 evidence-review with "Expected the module AST" error).
# The invariant (all timeline keys have non-empty address paths) is enforced by
# LegalAddress.__post_init__ which validates path elements at construction.


def compile_timelines(
    base: IRStatute,
    ops: List[LegalOperation],
    base_date: str = "",
    label_norm: Optional[Callable[[str], str]] = None,
    temporal_events: Tuple[TemporalEvent, ...] = (),
    issue_sink: Optional[List[TimelineIssue]] = None,
) -> Timelines:
    """Build a ProvisionTimeline for each addressable provision.

    Seeds from base (one initial ProvisionVersion per provision at base_date),
    then appends a new ProvisionVersion for each LegalOperation. Operations
    are applied in explicit temporal-carrier order when available. Source
    provenance does not provide executable temporal authority. `OperationSource.enacted` remains
    provenance on the carrier and is not used as execution authority.

    Args:
        base:       The base statute (unamended original or earliest known state).
        ops:        LegalOperations; this function re-sorts them by explicit
                    temporal carrier.
        base_date:  Effective date of the base statute. Falls back to
                    base.metadata["enacted_date"] then "0000-00-00".

    Returns:
        Dict mapping each LegalAddress to its complete ProvisionTimeline.

    """
    emit_warnings = False
    timelines: Timelines = {}

    # Step 1: seed from base statute
    effective_base = base_date or base.metadata.get("enacted_date", "") or "0000-00-00"

    def _norm_addr(addr: LegalAddress) -> LegalAddress:
        """Normalize labels in address using label_norm callback."""
        if label_norm is None:
            return addr
        return LegalAddress(
            path=tuple((k, label_norm(v)) for k, v in addr.path),
            special=addr.special,
        )

    for address, node in _iter_statute_nodes_with_address(base):
        address = _norm_addr(address)
        tl = ProvisionTimeline(address=address)
        tl.versions.append(
            ProvisionVersion(
                effective=effective_base,
                enacted=effective_base,
                content=node,
                content_hash=irnode_content_hash(node),
            )
        )
        timelines[address] = tl
    title_address = _norm_addr(statute_title_address())
    title_node = IRNode(
        kind=IRNodeKind.CONTENT,
        text=base.title,
        attrs={"facet": "statute_title"},
    )
    timelines[title_address] = ProvisionTimeline(
        address=title_address,
        versions=[
            ProvisionVersion(
                effective=effective_base,
                enacted=effective_base,
                content=title_node,
                content_hash=irnode_content_hash(title_node),
            )
        ],
    )

    # Exact-address lookup only: callers must provide the canonical source
    # address they intend to modify. Renumber destinations are the one exception
    # to the "must already exist" rule because the new canonical address is
    # often introduced by the move itself.
    def _resolve_target(t: LegalAddress) -> Optional[LegalAddress]:
        """Return the canonical base address for op target t."""
        if t in timelines:
            return t
        return None

    def _active_ancestor_contains_target(
        target: LegalAddress,
        effective: str,
    ) -> bool:
        def _labels_match(lhs: str, rhs: str) -> bool:
            if label_norm is None:
                return lhs == rhs
            return label_norm(lhs) == label_norm(rhs)

        def _content_has_relative_path(content: IRNode, relative_path: Tuple[Tuple[str, str], ...]) -> bool:
            node = content
            for kind_name, label in relative_path:
                child = next(
                    (
                        candidate
                        for candidate in node.children
                        if candidate.kind.value == kind_name
                        and candidate.label is not None
                        and _labels_match(candidate.label, label)
                    ),
                    None,
                )
                if child is None:
                    return False
                node = child
            return True

        for depth in range(len(target.path) - 1, 0, -1):
            parent = LegalAddress(path=target.path[:depth])
            parent_timeline = timelines.get(parent)
            if parent_timeline is None:
                continue
            parent_active = select_active_version_ex(
                parent_timeline,
                effective,
                query_type="governing",
                territory=None,
            ).version
            if (
                parent_active is not None
                and parent_active.content is not None
                and _content_has_relative_path(parent_active.content, target.path[depth:])
            ):
                return True
        return False

    def _active_temporary_expiry_for_target_or_ancestor(
        target: LegalAddress,
        effective: str,
    ) -> str:
        """Find a temporary expiry to inherit for a replace/text_replace.

        Exact-target temporary versions preserve their own expiry only when the
        target has no older durable background beneath that temporary overlay.
        This keeps later durable child updates alive even if replay emitted a
        temporary child snapshot from a broader parent replace. Ancestor expiry
        is inherited only when the target itself has no active durable
        pre-existing version at ``effective``.
        """
        tl = timelines.get(target)
        if tl is not None:
            prev_active = select_active_version_ex(
                tl,
                effective,
                query_type="governing",
                territory=None,
            ).version
            if prev_active is not None:
                if prev_active.expires and prev_active.expires > effective:
                    background = select_background_version(
                        tl,
                        effective,
                        query_type="governing",
                        territory=None,
                    )
                    if background is not None:
                        return ""
                    return prev_active.expires
                return ""

        current = LegalAddress(path=target.path[:-1])
        while current.path:
            tl = timelines.get(current)
            if tl is not None:
                prev_active = select_active_version_ex(
                    tl,
                    effective,
                    query_type="governing",
                    territory=None,
                ).version
                if prev_active is not None and prev_active.expires and prev_active.expires > effective:
                    return prev_active.expires
            current = LegalAddress(path=current.path[:-1])
        return ""

    # Step 2: apply operations in explicit temporal-carrier order
    def _resolved_touched_addresses(op: LegalOperation) -> Tuple[LegalAddress, ...]:
        addresses: list[LegalAddress] = []
        resolved_target = _resolve_target(op.target)
        if resolved_target is not None:
            addresses.append(resolved_target)
        elif op.action is StructuralAction.INSERT:
            addresses.append(op.target)
        if op.destination is not None:
            resolved_destination = _resolve_target(op.destination)
            destination = resolved_destination if resolved_destination is not None else op.destination
            if destination not in addresses:
                addresses.append(destination)
        return tuple(addresses)

    def _record_empty_same_day_interval(
        *,
        address: LegalAddress,
        source_statute: str,
        effective: str,
    ) -> None:
        _record_timeline_issue(
            issue_sink,
            kind="empty_same_day_interval",
            message=(
                "compile_timelines: provision version has an empty same-day temporal "
                f"interval at {effective}; recording the zero-length interval without "
                "treating it as a strict blocker"
            ),
            address=address,
            source_statute=source_statute,
            emit_warnings=emit_warnings,
        )

    def _append_version(
        timeline: ProvisionTimeline,
        version: ProvisionVersion,
    ) -> None:
        timeline.versions.append(version)
        if version.expires and version.effective == version.expires:
            _record_empty_same_day_interval(
                address=timeline.address,
                source_statute=version.source.statute_id if version.source and version.source.statute_id else "",
                effective=version.effective,
            )

    matched_temporal_event_ids = {
        event.event_id
        for op in ops
        for event in _matching_temporal_events_for_op(
            op,
            temporal_events,
            target_statute=base.statute_id,
            touched_addresses=_resolved_touched_addresses(op),
        )
    }
    standalone_events: List[Tuple[str, TemporalEvent]] = sorted(
        (
            (_temporal_event_execution_date(event), event)
            for event in temporal_events
            if event.event_id not in matched_temporal_event_ids
        ),
        key=lambda pair: (pair[0], pair[1].event_id),
    )
    sorted_ops = sorted(
        (
            (
                _op_sort_date(
                    op,
                    temporal_events,
                    target_statute=base.statute_id,
                    touched_addresses=_resolved_touched_addresses(op),
                ),
                op,
            )
            for op in ops
        ),
        key=lambda pair: (pair[0], pair[1].sequence),
    )
    renumber_wave_source_snapshots: Dict[
        Tuple[str, str, str],
        Dict[LegalAddress, Optional[ProvisionVersion]],
    ] = {}
    standalone_index = 0
    for op_date, op in sorted_ops:
        while standalone_index < len(standalone_events) and standalone_events[standalone_index][0] <= op_date:
            _, event = standalone_events[standalone_index]
            _apply_standalone_temporal_event(
                event,
                timelines,
                target_statute=base.statute_id,
                issue_sink=issue_sink,
                emit_warnings=emit_warnings,
                record_issue=_record_timeline_issue,
                latest_eligible_version_without_scope=_latest_eligible_version_without_scope,
                latest_substantive_version_at_or_before=_latest_substantive_version_at_or_before,
            )
            standalone_index += 1
        if op.target.special is not None and not is_statute_title_address(op.target):
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="unsupported_facet_target",
                message=(
                    "compile_timelines: rejecting facet-targeted op from "
                    f"{_src_id} — core timeline execution is node-addressed only "
                    f"(target={op.target}, action={op.action})"
                ),
                address=op.target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue
        target = _resolve_target(op.target)
        touched_addresses = _resolved_touched_addresses(op)
        temporal_overrides = _temporal_overrides_for_op(
            op,
            temporal_events,
            target_statute=base.statute_id,
            touched_addresses=touched_addresses,
        )
        # Safety: contingent temporal events without a resolved effective date
        # must NOT be applied.  Falling back to enacted date would silently
        # apply unresolved deferred-commencement effects.
        if temporal_overrides.has_contingent and not temporal_overrides.effective:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="skipped_contingent_unresolved",
                message=(
                    f"compile_timelines: skipping contingent op from {_src_id} — "
                    f"no resolved effective date for target {target} "
                    f"(group_id={op.group_id!r})"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue
        # Visibility: when temporal_events were provided and op carries a group_id
        # but no event matched, record the gap so migration remains trackable.
        if temporal_events and op.group_id and not temporal_overrides.matched:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="temporal_event_not_matched",
                message=(
                    f"compile_timelines: no TemporalEvent matched group_id={op.group_id!r} "
                    f"for op from {_src_id} targeting {target}; "
                    f"explicit temporal carrier is required"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )

        if temporal_overrides.matched:
            effective = temporal_overrides.effective
        elif temporal_events and op.group_id:
            effective = ""
        else:
            effective = ""
        enacted = op.source.enacted if op.source else ""
        if temporal_overrides.unsupported_applicability_dimensions:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="unsupported_applicability_dimension",
                message=(
                    "compile_timelines: ignoring unsupported applicability "
                    f"predicates {temporal_overrides.unsupported_applicability_dimensions!r} "
                    f"for group_id={op.group_id!r} target={target}; only territory "
                    "applicability is executable in core"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
        if not effective:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="missing_operation_date",
                message=(
                    f"compile_timelines: skipping op from {_src_id} — "
                    f"no explicit temporal carrier date available for target {target}"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue

        if target is None and op.action in (
            StructuralAction.REPLACE,
            StructuralAction.HEADING_REPLACE,
            StructuralAction.TEXT_REPLACE,
        ):
            if _active_ancestor_contains_target(op.target, effective):
                target = op.target

        if target is None:
            if op.action in (StructuralAction.INSERT, StructuralAction.REPEAL):
                target = op.target
            else:
                _src_id = op.source.statute_id if op.source else "?"
                _record_timeline_issue(
                    issue_sink,
                    kind="missing_replace_target",
                    message=(
                        "compile_timelines: skipping replace-family op from "
                        f"{_src_id} — target {op.target} is not present in the active timeline"
                    ),
                    address=op.target,
                    source_statute=_src_id,
                    emit_warnings=emit_warnings,
                )
                continue

        if op.action is StructuralAction.RENUMBER:
            if op.destination is None:
                _src_id = op.source.statute_id if op.source else "?"
                _record_timeline_issue(
                    issue_sink,
                    kind="missing_renumber_destination",
                    message=(
                        f"compile_timelines: skipping renumber from {_src_id} — "
                        f"no destination available for target {target}"
                    ),
                    address=target,
                    source_statute=_src_id,
                    emit_warnings=emit_warnings,
                )
                continue
            destination = _resolve_target(op.destination) or op.destination
            wave_key = (
                op.source.statute_id if op.source is not None else "",
                effective,
                enacted,
            )
            if wave_key not in renumber_wave_source_snapshots:
                renumber_wave_source_snapshots[wave_key] = {
                    address: (
                        select_active_version_ex(
                            timeline,
                            effective,
                            query_type="governing",
                            territory=None,
                        ).version
                        if timeline.versions
                        else None
                    )
                    for address, timeline in timelines.items()
                }
            source_active = renumber_wave_source_snapshots[wave_key].get(target)
            if source_active is None or source_active.content is None:
                _src_id = op.source.statute_id if op.source else "?"
                _record_timeline_issue(
                    issue_sink,
                    kind="missing_renumber_source",
                    message=(
                        f"compile_timelines: skipping renumber from {_src_id} — "
                        f"no active source content available for target {target}"
                    ),
                    address=target,
                    source_statute=_src_id,
                    emit_warnings=emit_warnings,
                )
                continue
            if destination not in timelines:
                timelines[destination] = ProvisionTimeline(address=destination)
            migrated_expires = (
                temporal_overrides.expires
                if temporal_overrides.matched
                else source_active.expires
            )
            migrated_applicability = (
                list(temporal_overrides.applicability)
                if temporal_overrides.applicability
                else list(op.applicability)
                if op.applicability
                else list(source_active.applicability)
            )
            migrated_content = _retarget_root_node(source_active.content, destination)
            _append_version(
                timelines[destination],
                ProvisionVersion(
                    effective=effective,
                    enacted=enacted,
                    expires=migrated_expires,
                    variant_kind="temporary" if migrated_expires else "permanent",
                    content=migrated_content,
                    source=op.source,
                    applicability=migrated_applicability,
                    content_hash=irnode_content_hash(migrated_content),
                )
            )
            _append_version(
                timelines[target],
                ProvisionVersion(
                    effective=effective,
                    enacted=enacted,
                    content=None,
                    source=op.source,
                )
            )
            continue

        if op.action in (
            StructuralAction.REPLACE,
            StructuralAction.INSERT,
            StructuralAction.HEADING_REPLACE,
        ):
            content = op.payload
        elif op.action is StructuralAction.TEXT_REPLACE:
            content = op.payload
        elif op.action is StructuralAction.REPEAL:
            content = None  # tombstone
        else:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="unsupported_text_action",
                message=(
                    "compile_timelines: rejecting text-level op from "
                    f"{_src_id} — action {op.action!r} is not executable in the "
                    "node timeline lane"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue

        if op.action is StructuralAction.INSERT and content is None:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="missing_insert_payload",
                message=(
                    f"compile_timelines: skipping op from {_src_id} — insert payload is missing for target {target}"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue
        if op.action in (
            StructuralAction.REPLACE,
            StructuralAction.HEADING_REPLACE,
            StructuralAction.TEXT_REPLACE,
        ) and content is None:
            _src_id = op.source.statute_id if op.source else "?"
            _record_timeline_issue(
                issue_sink,
                kind="missing_replace_payload",
                message=(
                    "compile_timelines: skipping op from "
                    f"{_src_id} — replace payload is missing for target {target}"
                ),
                address=target,
                source_statute=_src_id,
                emit_warnings=emit_warnings,
            )
            continue

        if target not in timelines:
            timelines[target] = ProvisionTimeline(address=target)

        expires = temporal_overrides.expires if temporal_overrides.matched else ""
        # Replacing a live temporary provision without an explicit new expiry
        # normally preserves the sunset date rather than making the provision
        # permanent by accident. Inherit the active temporary expiry here.
        # Exception: repeal placeholders must NEVER inherit a temporary expiry —
        # repeal is permanent, even for temporary provisions. A repealed section
        # doesn't come back when the parent law's sunset date passes.
        _is_repeal_placeholder = _content_is_repeal_placeholder(content)
        if not expires and op.action in (
            StructuralAction.REPLACE,
            StructuralAction.INSERT,
            StructuralAction.TEXT_REPLACE,
        ) and not _is_repeal_placeholder:
            expires = _active_temporary_expiry_for_target_or_ancestor(target, effective)

        if expires:
            _variant_kind: Literal["permanent", "temporary"] = "temporary"
        else:
            _variant_kind = "permanent"

        _append_version(
            timelines[target],
            ProvisionVersion(
                effective=effective,
                enacted=enacted,
                expires=expires,
                variant_kind=_variant_kind,
                content=content,
                source=op.source,
                applicability=(
                    list(temporal_overrides.applicability)
                    if temporal_overrides.applicability
                    else list(op.applicability)
                ),
                content_hash=irnode_content_hash(content),
            )
        )

    while standalone_index < len(standalone_events):
        _, event = standalone_events[standalone_index]
        _apply_standalone_temporal_event(
            event,
            timelines,
            target_statute=base.statute_id,
            issue_sink=issue_sink,
            emit_warnings=emit_warnings,
            record_issue=_record_timeline_issue,
            latest_eligible_version_without_scope=_latest_eligible_version_without_scope,
            latest_substantive_version_at_or_before=_latest_substantive_version_at_or_before,
        )
        standalone_index += 1

    # Step 3: sort each timeline chronologically
    for tl in timelines.values():
        tl.versions.sort(key=lambda v: (v.effective, v.enacted))

    return timelines


def compile_timelines_ex(
    base: IRStatute,
    ops: List[LegalOperation],
    base_date: str = "",
    label_norm: Optional[Callable[[str], str]] = None,
    temporal_events: Tuple[TemporalEvent, ...] = (),
) -> TimelineCompilationResult:
    """Explicit compile_timelines result with typed issues.
    """
    issues: List[TimelineIssue] = []
    timelines = compile_timelines(
        base,
        ops,
        base_date=base_date,
        label_norm=label_norm,
        temporal_events=temporal_events,
        issue_sink=issues,
    )
    return TimelineCompilationResult(timelines=timelines, issues=tuple(issues))


def materialize_pit(
    timelines: Timelines,
    as_of: str,
    base: Optional[IRStatute] = None,
    territory: Optional[str] = None,
    query_type: Literal["governing", "in_force"] = "governing",
    label_norm: Optional[Callable[[str], str]] = None,
    expires_as_of: str = "",
    migration_events: Tuple[MigrationEvent, ...] = (),
    lineage_plan: MaterializationLineagePlan | None = None,
) -> IRStatute:
    """Materialize a PIT statute or fail when required scope is omitted."""
    result = materialize_pit_ex(
        timelines,
        as_of,
        base=base,
        territory=territory,
        query_type=query_type,
        label_norm=label_norm,
        expires_as_of=expires_as_of,
        migration_events=migration_events,
        lineage_plan=lineage_plan,
    )
    if result.status == "degraded_missing_scope":
        raise ValueError(
            "materialize_pit requires explicit scope when PIT selection is degraded by "
            f"missing {result.required_dimensions!r}; use materialize_pit_ex() for an "
            "explicit degradation result."
        )
    return result.statute


def materialize_pit_ex(
    timelines: Timelines,
    as_of: str,
    base: Optional[IRStatute] = None,
    territory: Optional[str] = None,
    query_type: Literal["governing", "in_force"] = "governing",
    label_norm: Optional[Callable[[str], str]] = None,
    expires_as_of: str = "",
    migration_events: Tuple[MigrationEvent, ...] = (),
    lineage_plan: MaterializationLineagePlan | None = None,
) -> MaterializationResult:
    """Reconstruct an IRStatute at a specific point in time.

    Uses overlay semantics: a deeper-path version overrides a shallower one.
    E.g. if §12 was replaced in 2020 and §12(2) amended in 2023, the result
    at 2023 is the 2020 §12 with the 2023 §12(2) content grafted in.

    Args:
        timelines:    Output of compile_timelines() or ingest_uk_snapshots().
        as_of:        Date string "YYYY-MM-DD".
        base:         Optional base IRStatute for statute-level metadata.
        territory:    Optional territory filter. When provided, version selection
                      only uses versions whose territory applicability admits the
                      requested scope.
        query_type:   "governing" (Q2, default) or "in_force" (Q1).
                      See select_active_version() for semantics.
        expires_as_of: Separate expiry horizon for the ``v.expires`` check.
                      When empty, uses ``as_of``.
                      Use to split effective-date and expiry-date filtering —
                      e.g. ``finlex_oracle`` mode uses effective=``9999-12-31``
                      but expiry=oracle-PIT so temporary sections active at the
                      oracle snapshot date are correctly included.
        migration_events: Explicit lineage migrations to apply when projecting
                      selected versions onto the address visible at ``as_of``.
        lineage_plan: Typed lineage plan for PIT materialization. When passed,
                      callers must not also pass bare ``migration_events``.

    Returns:
        A materialization result carrying the PIT statute and any explicit
        degradations caused by missing required scope.
    """
    if lineage_plan is not None and migration_events:
        raise ValueError(
            "materialize_pit_ex accepts either lineage_plan or migration_events, not both"
        )
    if lineage_plan is not None:
        migration_events = lineage_plan.migration_events
    expiry_horizon = expires_as_of or as_of

    def _projects_as_absent_under_detached_horizon(version: ProvisionVersion) -> bool:
        content = version.content
        if content is None:
            return False
        return content.attrs.get(_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR) == "1"

    base_addresses: Set[LegalAddress] = set()
    if base is not None:
        base_addresses = {address for address, _node in _iter_statute_nodes_with_address(base)}
    # Step 1: active-version selection with explicit scope ambiguity tracking.
    degraded_dimensions: Set[str] = set()
    selection_states: List[_MaterializationSelectionState] = []
    selection_issues: List[TimelineIssue] = []
    for address, tl in timelines.items():
        for conflict in _equal_rank_same_source_conflicts(
            tl,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            expires_as_of=expires_as_of,
        ):
            _record_timeline_issue(
                selection_issues,
                kind="equal_rank_same_source_selection_conflict",
                message=(
                    "materialize_pit_ex: active version selection has "
                    f"{conflict.candidate_count} same-source equal-rank "
                    f"{conflict.variant_kind} candidates at effective "
                    f"{conflict.effective} enacted {conflict.enacted}; "
                    "preserving current deterministic winner but recording "
                    "the unproven precedence"
                ),
                address=address,
                source_statute=conflict.source_statute,
                emit_warnings=False,
            )
        selection = select_active_version_ex(
            tl,
            as_of,
            query_type=query_type,
            territory=territory,
            expires_as_of=expires_as_of,
        )
        if selection.status == "ambiguous_missing_scope":
            degraded_dimensions.update(selection.required_dimensions)
            selection_states.append(
                _MaterializationSelectionState(
                    address=address,
                    status="ambiguous_missing_scope",
                )
            )
            continue
        if selection.version is not None:
            if _projects_as_absent_under_detached_horizon(selection.version):
                selection_states.append(
                    _MaterializationSelectionState(
                        address=address,
                        status="inactive",
                    )
                )
                continue
            # Detect permanently-introduced-as-temporary sections: a permanent base
            # version selected because later versions all expired.  This happens when
            # a VÄLIAIKAINEN amendment inserts a new section (no prior permanent
            # content) but its corpus record lacks an explicit expiry date, while
            # later amendments DO carry an expiry that has now passed.
            # Only applies when there are later versions AND none of them are active
            # AND none of them are permanently non-expiring.
            # Only applies when _sv.source is not None (introduced by an amendment,
            # not the original statute): a base-statute permanent version (src=None)
            # represents the permanent baseline that should be restored after a
            # temporary modification expires, never suppressed.
            _sv = selection.version
            if _sv.expires == "" and _sv.variant_kind == "permanent" and _sv.source is not None:
                _later = [v for v in tl.versions if v.effective > _sv.effective]
                if _later and not any(
                    (not v.expires or v.expires > expiry_horizon) for v in _later
                ) and not any(not v.expires for v in _later):
                    # All later versions are temporary and have expired.
                    selection_states.append(
                        _MaterializationSelectionState(
                            address=address,
                            status="inactive",
                        )
                    )
                    continue
            selection_states.append(
                _MaterializationSelectionState(
                    address=address,
                    status="selected",
                    version=selection.version,
                )
            )
            continue
        # Known but inactive — address has a timeline but no eligible version.
        # Emit tombstone so _overlay_on_container omits base content.
        if any(v.expires and v.expires <= expiry_horizon for v in tl.versions):
            selection_states.append(
                _MaterializationSelectionState(
                    address=address,
                    status="inactive",
                )
            )

    active, active_versions, ambiguous_address_tuple = _project_materialization_selection_states(
        selection_states,
        migration_events,
        as_of=as_of,
    )
    title_address = statute_title_address()
    title = base.title if base else ""
    title_content = active.pop(title_address, None)
    active_versions.pop(title_address, None)
    if title_content is not None:
        title = title_content.text
    ambiguous_addresses = list(ambiguous_address_tuple)
    issues: List[TimelineIssue] = selection_issues + [
        TimelineIssue(
            kind="ambiguous_missing_scope",
            message=(f"materialize_pit_ex: omitted required scope prevents selection for {address}"),
            address=address,
        )
        for address in ambiguous_addresses
    ]

    # Step 1b: Remove child-level entries superseded by a parent replacement.
    # When a section is replaced wholesale (e.g. at 2020), base-seeded child
    # entries (e.g. paragraph:1 from 0000) should NOT be grafted into the new
    # section content.  Only child entries newer than the parent replacement
    # should survive (e.g. a 2023 subsection amendment on the 2020 section).
    # Newness is determined by (effective, enacted); this preserves equal-effective
    # overrides where a later-enacted parent replaces stale child content.
    superseded: Set[LegalAddress] = set()

    def _parent_content_masks_child(
        content: IRNode,
        parent_addr: LegalAddress,
        child_addr: LegalAddress,
    ) -> bool:
        if parent_addr.leaf_kind() not in {"chapter", "part", "section"}:
            return True

        relative_path = child_addr.path[len(parent_addr.path) :]
        if not relative_path:
            return True

        parent_leaf = parent_addr.leaf_kind()
        if parent_leaf == "part":
            child_kinds = {"chapter"}
        elif parent_leaf == "chapter":
            child_kinds = {"section"}
        else:
            child_kinds = {"subsection", "item"}
        has_structural_children = any(
            getattr(child, "label", None) and child.kind.value in child_kinds
            for child in content.children
        )
        if parent_addr.leaf_kind() == "section":
            tail_policy = str(content.attrs.get("lawvm_tail_policy") or "")
            if tail_policy == "replace_if_target_scope_requires":
                return True
            # A newer section root masks only the child addresses it actually
            # carries. Older subsection/item timelines that are absent from the
            # replacement payload must survive and be reattached during
            # materialization.
            if not has_structural_children:
                return parent_addr in base_addresses
        if parent_addr.leaf_kind() in {"chapter", "part"} and not has_structural_children:
            return parent_addr in base_addresses

        node = content
        for kind_name, label in relative_path:
            child = next(
                (
                    candidate
                    for candidate in node.children
                    if candidate.kind.value == kind_name and candidate.label == label
                ),
                None,
            )
            if child is None:
                return False
            node = child
        return True

    def _same_source_section_snapshot_masks_child(
        parent_v: ProvisionVersion,
        parent_addr: LegalAddress,
        child_v: ProvisionVersion,
    ) -> bool:
        if parent_addr.leaf_kind() != "section" or parent_v.content is None:
            return False
        if parent_v.effective != child_v.effective or parent_v.enacted != child_v.enacted:
            return False
        parent_source = parent_v.source.statute_id if parent_v.source is not None else ""
        child_source = child_v.source.statute_id if child_v.source is not None else ""
        if not parent_source or parent_source != child_source:
            return False
        return any(
            child.kind.value in {"subsection", "item"} and child.label
            for child in parent_v.content.children
        )

    for addr in list(active):
        if len(addr.path) <= 1:
            continue
        for depth in range(1, len(addr.path)):
            parent_path = addr.path[:depth]
            parent_addr = LegalAddress(path=parent_path)
            parent_v = active_versions.get(parent_addr)
            child_v = active_versions.get(addr)
            if (
                parent_v
                and parent_v.content is not None
                and child_v
                and (
                    (
                        _parent_content_masks_child(parent_v.content, parent_addr, addr)
                        and parent_v.effective > child_v.effective
                    )
                    or (
                        _parent_content_masks_child(parent_v.content, parent_addr, addr)
                        and parent_v.effective == child_v.effective
                        and parent_v.enacted > child_v.enacted
                    )
                    or (
                        parent_v.effective == child_v.effective
                        and _same_source_section_snapshot_masks_child(parent_v, parent_addr, child_v)
                    )
                )
            ):
                superseded.add(addr)
                break
    # Step 1c: Deduplicate body-level vs chapter-qualified entries for the same section.
    # When a section exists at both body level (section:M) and inside a chapter
    # (chapter:N/section:M), the body-level entry is always superseded (the section
    # should appear inside its chapter, not at the body root).
    # However, if the body-level version is NEWER, its content should be promoted
    # into the chapter-qualified address (the later amendment provides better content
    # even though the snapshot was emitted without chapter context).
    #
    # Key is (kind, label) — not label alone — so that a chapter:M and a section:M
    # with the same numeric label never collide in the dedup lookup.
    depth2_section_labels: Dict[Tuple[str, str], LegalAddress] = {}
    for addr in active:
        if len(addr.path) == 2 and addr.path[0][0] in ("chapter", "part"):
            kind, label = addr.path[1]
            if kind == "section":
                depth2_section_labels[(kind, label)] = addr
    for addr in list(active):
        if len(addr.path) == 1 and addr.path[0][0] == "section":
            kind1, label = addr.path[0]
            if (kind1, label) in depth2_section_labels:
                d2_addr = depth2_section_labels[(kind1, label)]
                d2_v = active_versions.get(d2_addr)
                d1_v = active_versions.get(addr)
                if d2_v and d1_v:
                    if d1_v.effective > d2_v.effective:
                        # Body-level is newer — promote its content into the
                        # chapter-qualified address (preserving placement).
                        active[d2_addr] = active[addr]
                        active_versions[d2_addr] = d1_v
                    # Always remove body-level entry (section lives in chapter)
                    superseded.add(addr)

    # A shallow chapter-scoped section can survive a recodification even after
    # the same provision has been projected into a more specific part/chapter
    # frame. Only collapse this when the deeper section label is unique; if
    # multiple deeper same-label sections exist, the shallow address is
    # ambiguous evidence rather than a safe duplicate.
    deeper_section_labels: Dict[Tuple[str, str], List[LegalAddress]] = {}
    for addr, content in active.items():
        if content is None:
            continue
        if any(active.get(LegalAddress(path=addr.path[:depth])) is None for depth in range(1, len(addr.path))):
            continue
        if len(addr.path) > 2 and addr.path[-1][0] == "section":
            deeper_section_labels.setdefault(addr.path[-1], []).append(addr)
    unique_deeper_section_labels = {
        key: addresses[0]
        for key, addresses in deeper_section_labels.items()
        if len(addresses) == 1
    }
    for addr in list(active):
        if len(addr.path) >= 3 or not addr.path or addr.path[-1][0] != "section":
            continue
        deeper_addr = unique_deeper_section_labels.get(addr.path[-1])
        if deeper_addr is None:
            continue
        superseded.add(addr)

    for addr in superseded:
        del active[addr]
        active_versions.pop(addr, None)

    # Step 1d: Temporary-ancestor subtree masking.
    # If a temporary ancestor is active, background descendants must be hidden —
    # the temporary content is self-contained and should not have permanent
    # child amendments grafted into it.
    temp_ancestors: Set[Tuple[Tuple[str, str], ...]] = set()
    for addr, ver in active_versions.items():
        if ver.variant_kind == "temporary":
            temp_ancestors.add(addr.path)
    if temp_ancestors:
        masked: Set[LegalAddress] = set()
        for addr, ver in active_versions.items():
            if ver.variant_kind != "permanent":
                continue
            # Check if any prefix of this address is a temporary ancestor
            for depth in range(1, len(addr.path)):
                if addr.path[:depth] in temp_ancestors:
                    masked.add(addr)
                    break
        for addr in masked:
            del active[addr]
            del active_versions[addr]

    # Step 2-3: Build body by overlaying onto base (preserves unlabeled nodes)
    # or reconstructing from timeline entries when no base is available.
    body = _materialize_body(
        active,
        active_versions,
        base,
        label_norm=label_norm,
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_timeline_issue,
    )
    supplements = _materialize_root_nodes(
        list(base.supplements) if base else [],
        _top_level_supplement_active(active, base=base, body_top_level_kinds=_BODY_TOP_LEVEL_KINDS),
        label_norm=label_norm,
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_timeline_issue,
    )

    statute_id = base.statute_id if base else "unknown/unknown"
    metadata: Dict[str, Any] = dict(base.metadata) if base else {}
    metadata["materialized_as_of"] = as_of
    status: MaterializationStatus = "materialized"
    if ambiguous_addresses:
        status = "degraded_missing_scope"
        metadata["materialization_status"] = status
        metadata["required_scope_dimensions"] = list(sorted(degraded_dimensions))
        metadata["ambiguous_scope_addresses"] = [
            "/".join(f"{kind}:{label}" for kind, label in address.path)
            for address in sorted(ambiguous_addresses, key=lambda addr: addr.path)
        ]
    elif any(issue.blocking for issue in issues):
        status = "degraded_timeline_issues"
        metadata["materialization_status"] = status
        metadata["timeline_issue_rule_ids"] = tuple(
            issue.rule_id for issue in issues if issue.blocking
        )

    statute = IRStatute(
        statute_id=statute_id,
        title=title,
        body=body,
        supplements=supplements,
        metadata=metadata,
    )
    return MaterializationResult(
        status=status,
        statute=statute,
        required_dimensions=tuple(sorted(degraded_dimensions)),
        ambiguous_addresses=tuple(sorted(ambiguous_addresses, key=lambda addr: addr.path)),
        issues=tuple(issues),
        certificate=MaterializationCertificate(
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            selected_address_count=len(active_versions),
            ambiguous_address_count=len(ambiguous_addresses),
            required_dimensions=tuple(sorted(degraded_dimensions)),
        ),
    )


# ---------------------------------------------------------------------------
# Phase 8: query functions
# ---------------------------------------------------------------------------


def diff_statute(
    timelines: Timelines,
    date1: str,
    date2: str,
) -> Dict[LegalAddress, Tuple[Optional[ProvisionVersion], Optional[ProvisionVersion]]]:
    """Find all provisions that changed between two dates."""
    return _diff_statute(
        timelines,
        date1,
        date2,
        select_active_version_ex_fn=select_active_version_ex,
    )


def current_address_from_migration_events(
    original_address: LegalAddress,
    migration_events: Tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
) -> LegalAddress:
    return _current_address_from_migration_events(
        original_address,
        migration_events,
        as_of_date=as_of_date,
        address_prefix_matches=_address_prefix_matches,
    )


def _lineage_address_chain(
    original_address: LegalAddress,
    migration_events: Tuple[MigrationEvent, ...],
    *,
    as_of_date: str = "",
) -> tuple[LegalAddress, ...]:
    return _lineage_address_chain_helper(
        original_address,
        migration_events,
        as_of_date=as_of_date,
        address_prefix_matches=_address_prefix_matches,
    )


def provision_lineage(
    timelines: Timelines,
    address: LegalAddress,
    *,
    migration_events: Tuple[MigrationEvent, ...] = (),
    as_of_date: str = "",
) -> List[ProvisionVersion]:
    """Return the complete version history of a provision, oldest first."""
    return _provision_lineage(
        timelines,
        address,
        migration_events=migration_events,
        as_of_date=as_of_date,
        lineage_address_chain_fn=_lineage_address_chain,
    )


def affecting_acts(
    timelines: Timelines,
    address: LegalAddress,
) -> List[str]:
    """Return statute_ids of all acts that affected a given provision."""
    return _affecting_acts(timelines, address)


def modified_by_act(
    timelines: Timelines,
    source_statute_id: str,
) -> List[LegalAddress]:
    """Return all addresses with at least one version sourced from source_statute_id.

    Inverse of affecting_acts(). Answers: "What provisions did amendment act Y modify?"
    Useful for: cross-statute impact analysis, change certificates, diff rendering.

    Args:
        timelines:          Output of compile_timelines() or ingest_uk_snapshots().
        source_statute_id:  The amending statute ID to query (e.g. "2017/794").

    Returns:
        List of LegalAddresses modified by that act, in address path order.
    """
    return _modified_by_act(timelines, source_statute_id)
