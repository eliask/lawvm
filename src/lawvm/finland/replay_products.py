"""Typed replay/materialization products for the Finnish frontend."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace as dc_replace
from typing import TYPE_CHECKING, Callable, Literal, Optional, cast

from lawvm.core.provenance import MigrationEvent
from lawvm.core.ir import IRNode, IRStatute, LegalAddress
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.ir import LegalOperation
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.temporal import FIXED_DATE_KIND, ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.timeline_lineage import (
    MaterializationLineageBridgeClassification,
    classify_materialization_lineage_bridge,
    choose_materialization_lineage_decision,
    rekey_timelines_with_migration_events as _core_rekey_timelines_with_migration_events,
)
from lawvm.core.timeline_results import (
    MaterializationLineageDecision,
    MaterializationLineagePlan,
)
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.tree_ops import check_invariants, resort_children as _resort_children
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.finland.apply_ir_ops import _strip_standalone_subsection_item_prefixes_ir

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState, StatuteContext


_FI_LABEL_NORMALIZER_NAME = "fi_label_norm_v1"
_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS = "rekeyed_with_migrations"
_FI_LINEAGE_MODE_REKEYED_ONLY = "rekeyed_only"
_FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS = "raw_with_migrations"
_FI_LINEAGE_REASON_DEFAULT = "default_migration_projection"
_FI_LINEAGE_REASON_NATIVE_REBIRTH = "native_rebirth_after_renumber"
_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER = "leaf_stable_scope_renumber"
_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY = "destination_occupancy_collision"
_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK = "scope_changing_migration_fallback"
_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE = "fi_sourceless_base_merge_cleanup_v1"
_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR = (
    "lawvm_materialize_as_absent_under_detached_horizon"
)


FinlandLineageBridgeClassification = MaterializationLineageBridgeClassification


@dataclass(frozen=True)
class MaterializationSpec:
    """Typed description of how PIT materialization was derived."""

    as_of: str
    query_type: Literal["governing", "in_force"] = "governing"
    label_normalizer: str = _FI_LABEL_NORMALIZER_NAME
    bridge_classification: FinlandLineageBridgeClassification = FinlandLineageBridgeClassification()
    lineage_plan: MaterializationLineagePlan = MaterializationLineagePlan(
        mode=_FI_LINEAGE_MODE_REKEYED_WITH_MIGRATIONS
    )
    lineage_reason: Literal[
        "default_migration_projection",
        "native_rebirth_after_renumber",
        "leaf_stable_scope_renumber",
        "destination_occupancy_collision",
        "scope_changing_migration_fallback",
    ] = _FI_LINEAGE_REASON_DEFAULT

    @property
    def lineage_mode(self) -> Literal[
        "rekeyed_with_migrations",
        "rekeyed_only",
        "raw_with_migrations",
    ]:
        return self.lineage_plan.mode


@dataclass
class ReplayProducts:
    """Replay artifacts after folding and PIT materialization."""

    replay_fold_state: "ReplayState"
    materialized_state: "ReplayState"
    timelines: Optional[dict]
    temporal_events: tuple[TemporalEvent, ...] = ()
    migration_events: tuple[MigrationEvent, ...] = ()
    materialization_spec: Optional[MaterializationSpec] = None
    source_adjudication: Optional[SourceAdjudication] = None

def _assert_finland_timeline_safe_ops(lo_ops_out: list[LegalOperation]) -> None:
    """Reject Finland replay ops that still depend on core tombstone quirks.

    Finland should not rely on payload-less ``replace`` semantics in
    ``compile_timelines()``. If a replay path still emits that shape, the fix
    belongs upstream in Finland replay emission, not as a replay-products shim.
    """
    for op in lo_ops_out:
        if op.action is not StructuralAction.REPLACE:
            continue
        if op.payload is not None:
            continue
        if op.op_id.startswith("snapshot_"):
            continue
        raise RuntimeError(
            "FI_TIMELINE_PAYLOADLESS_REPLACE: Finland replay emitted "
            f"payload-less replace for {op.target} (op_id={op.op_id or '<missing-op-id>'}). "
            "Emit explicit repeal semantics or a real replacement payload before "
            "timeline compilation."
        )


def fi_label_norm(label: str) -> str:
    """Normalize Finnish legacy labels for timeline materialization."""
    return re.sub(r"[^a-zA-Z0-9äöå]+$", "", label).strip() or label


def _fi_root_num_text(kind: IRNodeKind, label: str) -> str | None:
    """Return Finnish-facing NUM child text for migrated roots."""
    kind_value = str(kind)
    if kind_value == IRNodeKind.SECTION.value:
        return f"{label} §"
    if kind_value == IRNodeKind.CHAPTER.value:
        return f"{label} luku"
    return None


def _temporal_events_from_lo_ops(
    lo_ops: list[LegalOperation],
    *,
    target_statute: str,
    covered_commence_group_ids: frozenset[str] = frozenset(),
    covered_expiry_signatures: frozenset[tuple[str, str, str]] = frozenset(),
) -> tuple[TemporalEvent, ...]:
    """Project replay ops into explicit temporal authority for timeline mode.

    Finland replay still carries bounded fallback synthesis for replay-owned
    structural groups whose executable temporal authority has not yet been
    emitted earlier in the pipeline. Frontend-supplied temporal events remain
    authoritative; this shim only preserves existing replay behavior while the
    producer path finishes migrating fully onto explicit carriers.
    """
    events: list[TemporalEvent] = []
    seen_group_ids: set[str] = set()
    seen_expiry_keys: set[tuple[str, str, str]] = set()
    for op in lo_ops:
        group_id = str(getattr(op, "group_id", "") or "")
        if not group_id:
            continue
        source = getattr(op, "source", None)
        if source is None:
            continue
        effective_from = str(getattr(source, "effective", "") or "")
        if (
            effective_from
            and group_id not in seen_group_ids
            and group_id not in covered_commence_group_ids
        ):
            seen_group_ids.add(group_id)
            scope = TemporalScope(target_statute=target_statute)
            events.append(
                TemporalEvent(
                    event_id=f"fi-temporal:{group_id}:commence",
                    kind="commence",
                    scope=scope,
                    effective=effective_from,
                    source=source,
                    activation_rule=ActivationRule(
                        kind=FIXED_DATE_KIND,
                        effective_date=effective_from,
                        raw_text=str(getattr(source, "raw_text", "") or ""),
                    ),
                    group_id=group_id,
                )
            )
        expires = str(getattr(source, "expires", "") or "")
        if not expires:
            continue
        target_address = getattr(op, "target", None)
        target_key = str(target_address) if target_address is not None else ""
        expiry_key = (group_id, target_key, expires)
        if expiry_key in seen_expiry_keys:
            continue
        if expiry_key in covered_expiry_signatures:
            continue
        seen_expiry_keys.add(expiry_key)
        expire_scope = TemporalScope(
            target_statute=target_statute,
            exact_addresses=(target_address,) if target_address is not None else (),
        )
        events.append(
            TemporalEvent(
                event_id=f"fi-temporal:{group_id}:expire:{target_key or 'target'}",
                kind="expire",
                scope=expire_scope,
                expires=expires,
                source=source,
                group_id=group_id,
            )
        )
    return tuple(events)


def _merge_temporal_events(
    existing: tuple[TemporalEvent, ...],
    synthesized: tuple[TemporalEvent, ...],
) -> tuple[TemporalEvent, ...]:
    """Merge temporal events without dropping pre-existing executable carriers."""
    merged = list(existing)

    def _signature(event: TemporalEvent) -> tuple[object, ...]:
        if event.kind == "expire":
            exact_addresses = tuple(
                str(address)
                for address in getattr(event.scope, "exact_addresses", ()) or ()
            )
            return (
                event.kind,
                event.group_id,
                event.expires,
                exact_addresses,
            )
        return (
            event.kind,
            event.group_id,
        )

    seen = {_signature(event) for event in merged}
    for event in synthesized:
        signature = _signature(event)
        if signature in seen:
            continue
        merged.append(event)
        seen.add(signature)
    return tuple(merged)


def _normalize_repeal_op_sources(lo_ops: list[LegalOperation]) -> list[LegalOperation]:
    """Keep repeal placeholders/tombstones from inheriting a temporary expiry.

    Whole-section repeal semantics should remain visible after the repeal date.
    If we keep the source expiry on a tombstone-like op, PIT materialization can
    fall back to the pre-repeal permanent version once the temporary horizon
    passes. That revives text that should stay suppressed.

    This normalization is intentionally narrow: only explicit repeal ops and
    ops that already carry a repeal placeholder payload lose their source
    expiry. Other temporary amendments still keep their sunset behavior.
    """
    normalized: list[LegalOperation] = []
    for op in lo_ops:
        payload = getattr(op, "payload", None)
        is_repeal_placeholder = bool(
            payload is not None and getattr(payload, "attrs", {}).get("lawvm_repeal_placeholder") == "1"
        )
        if (
            op.source is not None
            and op.source.expires
            and (op.action is StructuralAction.REPEAL or is_repeal_placeholder)
        ):
            normalized_payload = op.payload
            if (
                normalized_payload is not None
                and is_repeal_placeholder
                and op.source.expires == op.source.effective
            ):
                normalized_payload = IRNode(
                    kind=normalized_payload.kind,
                    label=normalized_payload.label,
                    text=normalized_payload.text,
                    attrs={
                        **dict(normalized_payload.attrs),
                        _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                    },
                    children=normalized_payload.children,
                )
            normalized.append(
                dc_replace(
                    op,
                    payload=normalized_payload,
                    source=dc_replace(op.source, expires=""),
                )
            )
            continue
        normalized.append(op)
    return normalized


def _rekey_timelines_with_migration_events(
    timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> dict["LegalAddress", ProvisionTimeline]:
    """Project Finland timelines onto migrated addresses for the requested PIT.

    Finland replay emits historical snapshots at the address valid when the
    amendment was applied. For PIT materialization, later container renumber
    waves can move those snapshots onto a different current address. Shared
    core timelines do not yet consume migration events directly, so Finland
    rekeys its replay-owned timelines here before materialization.
    """
    from lawvm.core.timeline import _address_prefix_matches
    from lawvm.finland.migration_ledger import current_address_with_prefix_migrations_from_events

    return _core_rekey_timelines_with_migration_events(
        timelines,
        migration_events,
        as_of_date=as_of,
        current_address_with_prefix_migrations_fn=current_address_with_prefix_migrations_from_events,
        address_prefix_matches=_address_prefix_matches,
        retarget_version_content_fn=lambda version, address: _retarget_version_content(
            version,
            address,
            root_num_text_fn=_fi_root_num_text,
        ),
        merge_bucket_cleanup_fn=_cleanup_sourceless_base_merge_conflicts,
    )


def _cleanup_sourceless_base_merge_conflicts(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Prune replay-bucket collisions between base snapshots and newer lineage.

    This is a temporary Finland-local cleanup policy. Some rekeyed buckets can
    contain a source-less base snapshot plus later lineage versions that are
    not semantically additive. Until core owns a better non-textual rule for
    that identity/materialization family, Finland keeps the base snapshot and
    only the later versions that clearly extend beyond the base wording span.

    The rule name is stable on purpose:
    `_FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE`.
    """
    if not any(existing_version.source is None for existing_version in versions):
        return versions

    def _title_prefix_len(node: IRNode | None) -> int:
        if node is None:
            return 0
        text = irnode_to_text(node)
        prefix = text.split(" Tässä", 1)[0]
        return len(prefix)

    base_title_lengths = [
        _title_prefix_len(existing_version.content)
        for existing_version in versions
        if existing_version.source is None and existing_version.content is not None
    ]
    if not base_title_lengths:
        return versions
    base_effective = max(
        existing_version.effective
        for existing_version in versions
        if existing_version.source is None
    )
    base_title_len = max(base_title_lengths)
    cleaned = [
        existing_version
        for existing_version in versions
        if existing_version.source is None
        or (
            existing_version.content is not None
            and (
                existing_version.effective > base_effective
                or _title_prefix_len(existing_version.content) > base_title_len
            )
        )
    ]
    return _dedupe_same_source_semantic_versions(cleaned)


def _timeline_version_semantic_text_key(node: IRNode | None) -> str:
    if node is None:
        return ""
    text = " ".join(irnode_to_text(node).split())
    return re.sub(r"^(\d+[a-z]?)\s*§", r"\1 §", text)


def _dedupe_same_source_semantic_versions(
    versions: list[ProvisionVersion],
) -> list[ProvisionVersion]:
    """Collapse same-source timeline duplicates created by lineage projection.

    A whole-container replacement can emit a child snapshot while a migration
    event for the same source/effective date retargets the old child lineage to
    that same address. If the resulting texts are semantically identical, keep
    one version so PIT selection has a single source-backed state transition.
    """
    deduped: list[ProvisionVersion] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id or version.content is None:
            deduped.append(version)
            continue
        key = (
            source_id,
            version.effective,
            version.enacted,
            version.expires,
            version.variant_kind,
            tuple(version.applicability),
            _timeline_version_semantic_text_key(version.content),
        )
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(version)
            continue
        deduped[existing_index] = version
    return deduped


def _classify_finland_lineage_bridge(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
) -> FinlandLineageBridgeClassification:
    from lawvm.core.timeline import _address_prefix_matches

    return classify_materialization_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of_date=as_of,
        address_prefix_matches=_address_prefix_matches,
    )


def _select_pit_lineage_inputs(
    raw_timelines: dict["LegalAddress", ProvisionTimeline],
    rekeyed_timelines: dict["LegalAddress", ProvisionTimeline],
    migration_events: tuple[MigrationEvent, ...],
    *,
    as_of: str,
    bridge_classification: FinlandLineageBridgeClassification | None = None,
) -> MaterializationLineageDecision:
    """Choose the canonical PIT lineage inputs for Finland replay products.

    Native rebirth must outrank the scope-changing migration fallback. Once a
    same-label native provision is born on the renumber date, replay products
    need the rekeyed split lineage and must stop forwarding the migration
    events into PIT materialization for that case. Otherwise the old lineage
    and the reborn native lineage compete across two authority surfaces:
    Finland's rekey shim and core migration materialization.
    """
    classification = bridge_classification or _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    return choose_materialization_lineage_decision(
        raw_timelines=raw_timelines,
        rekeyed_timelines=rekeyed_timelines,
        migration_events=migration_events,
        native_rebirth_after_renumber=classification.native_rebirth_after_renumber,
        leaf_stable_scope_renumber=classification.leaf_stable_scope_renumber,
        noncolliding_scope_migrations=classification.noncolliding_scope_migrations,
        destination_occupancy_collision=classification.destination_occupancy_collision,
        scope_changing_migration_fallback=classification.active_scope_changing,
        default_reason=_FI_LINEAGE_REASON_DEFAULT,
        native_rebirth_reason=_FI_LINEAGE_REASON_NATIVE_REBIRTH,
        leaf_stable_reason=_FI_LINEAGE_REASON_LEAF_STABLE_SCOPE_RENUMBER,
        destination_occupancy_reason=_FI_LINEAGE_REASON_DESTINATION_OCCUPANCY,
        scope_changing_fallback_reason=_FI_LINEAGE_REASON_SCOPE_CHANGING_FALLBACK,
    )


def build_replay_products(
    *,
    ctx: "StatuteContext",
    statute_id: str,
    replay_fold_state: "ReplayState",
    lo_ops_out: Optional[list],
    source_adjudication: Optional[SourceAdjudication] = None,
    as_of: str = "9999-12-31",
    query_type: Literal["governing", "in_force"] = "governing",
    synthesize_repeal_placeholders: bool = False,
    repeal_placeholder_normalizer: Optional[Callable[[object], object]] = None,
    build_full_products: bool = True,
    temporal_events: tuple[TemporalEvent, ...] = (),
    strict_johto_temporal: bool = True,
    migration_events: tuple[MigrationEvent, ...] = (),
    expires_as_of: str = "",
) -> ReplayProducts:
    """Build typed PIT materialization artifacts from a replay fold state.

    Callers must perform explicit temporal lowering before calling this
    function. Use ``lawvm.core.effect_lowering.lower_effect_intents_to_temporal_events``
    to convert parse-layer ``EffectIntent`` objects into executable
    ``TemporalEvent`` instances and pass the result as ``temporal_events``.

    Finland replay/materialization prefers explicit ``TemporalEvent`` carriers,
    but replay products still preserve a bounded fallback synthesis from
    replay-owned structural ops until the producer path is fully migrated.
    """
    resolved_temporal_events = tuple(temporal_events)
    if not build_full_products:
        return ReplayProducts(
            replay_fold_state=replay_fold_state,
            materialized_state=replay_fold_state,
            timelines=None,
            temporal_events=resolved_temporal_events,
            migration_events=migration_events,
            materialization_spec=None,
            source_adjudication=source_adjudication,
        )

    from lawvm.core.timeline import compile_timelines, materialize_pit

    base_ir = IRStatute(
        statute_id=statute_id,
        title=ctx.title,
        body=ctx.base_ir,
    )
    lo_ops = list(lo_ops_out or [])
    lo_ops = _normalize_repeal_op_sources(lo_ops)
    _assert_finland_timeline_safe_ops(lo_ops)
    covered_commence_group_ids = frozenset(
        group_id
        for event in resolved_temporal_events
        if event.kind == "commence"
        and isinstance((group_id := getattr(event, "group_id", "")), str)
        and group_id
    )
    covered_expiry_signatures = frozenset(
        (
            str(getattr(event, "group_id", "") or ""),
            str(next(iter(getattr(event.scope, "exact_addresses", ()) or ()), "") or ""),
            str(getattr(event, "expires", "") or ""),
        )
        for event in resolved_temporal_events
        if event.kind == "expire"
        and isinstance(getattr(event, "group_id", ""), str)
        and getattr(event, "group_id", "")
        and getattr(event, "expires", "")
    )
    synthesized_temporal_events = _temporal_events_from_lo_ops(
        lo_ops,
        target_statute=base_ir.statute_id,
        covered_commence_group_ids=covered_commence_group_ids,
        covered_expiry_signatures=covered_expiry_signatures,
    )
    if synthesized_temporal_events:
        resolved_temporal_events = _merge_temporal_events(
            resolved_temporal_events,
            synthesized_temporal_events,
        )
    raw_timelines = compile_timelines(
        base_ir,
        lo_ops,
        label_norm=fi_label_norm,
        temporal_events=resolved_temporal_events,
    )
    timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    bridge_classification = _classify_finland_lineage_bridge(
        raw_timelines,
        migration_events,
        as_of=as_of,
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        timelines,
        migration_events,
        as_of=as_of,
        bridge_classification=bridge_classification,
    )
    pit = materialize_pit(
        lineage_decision.timelines,
        as_of=as_of,
        base=base_ir,
        query_type=query_type,
        label_norm=fi_label_norm,
        expires_as_of=expires_as_of,
        lineage_plan=lineage_decision.lineage_plan,
    )
    materialized_state = replay_fold_state.with_ir(pit.body)
    materialized_state = materialized_state.with_ir(
        _strip_standalone_subsection_item_prefixes_ir(materialized_state.ir)
    )
    # Sort labeled children back into canonical order.  PIT materialization can
    # produce out-of-order siblings (e.g. paragraphs within a subsection) for
    # the same reason the replay fold can — amendment ops insert at arbitrary
    # positions and materialize_pit preserves that order.
    materialized_state = materialized_state.with_ir(
        _resort_children(materialized_state.ir)
    )
    if synthesize_repeal_placeholders and repeal_placeholder_normalizer is not None:
        materialized_state = materialized_state.with_ir(
            cast(IRNode, repeal_placeholder_normalizer(materialized_state.ir))
        )

    return ReplayProducts(
        replay_fold_state=replay_fold_state,
        materialized_state=materialized_state,
        timelines=timelines,
        temporal_events=resolved_temporal_events,
        migration_events=migration_events,
        materialization_spec=MaterializationSpec(
            as_of=as_of,
            query_type=query_type,
            label_normalizer=_FI_LABEL_NORMALIZER_NAME,
            bridge_classification=bridge_classification,
            lineage_plan=lineage_decision.lineage_plan,
            lineage_reason=cast(
                Literal[
                    "default_migration_projection",
                    "native_rebirth_after_renumber",
                    "leaf_stable_scope_renumber",
                    "destination_occupancy_collision",
                    "scope_changing_migration_fallback",
                ],
                lineage_decision.reason,
            ),
        ),
        source_adjudication=source_adjudication,
    )


def validate_replay_products(
    ctx: "StatuteContext",
    products: ReplayProducts,
    *,
    deep_materialization_check: bool = False,
) -> list[str]:
    """Return replay/materialization product invariant violations."""
    violations: list[str] = []

    if products.timelines is None and products.materialization_spec is not None:
        violations.append("materialization_spec_without_timelines")
    if products.timelines is not None and products.materialization_spec is None:
        violations.append("timelines_without_materialization_spec")

    if products.replay_fold_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"replay_fold_not_body:{products.replay_fold_state.ir.kind}")
    if products.materialized_state.ir.kind is not IRNodeKind.BODY:
        violations.append(f"materialized_not_body:{products.materialized_state.ir.kind}")

    for violation in check_invariants(products.replay_fold_state.ir):
        violations.append(f"replay_fold_tree:{violation}")
    for violation in check_invariants(products.materialized_state.ir):
        violations.append(f"materialized_tree:{violation}")

    # Check for temporary_unresolved versions — these represent VÄLIAIKAINEN
    # amendments with no parseable expiry date and are a product-level degradation
    # signal worth surfacing to callers.
    if products.timelines is not None:
        for tl in products.timelines.values():
            for ver in tl.versions:
                if ver.variant_kind == "temporary_unresolved":
                    violations.append("temporal_unresolved_temporary_expiry")
                    break
            else:
                continue
            break

    if deep_materialization_check and products.timelines is not None:
        from lawvm.core.timeline import materialize_pit

        base_ir = IRStatute(
            statute_id=ctx.id,
            title=ctx.title,
            body=ctx.base_ir,
        )
        spec = products.materialization_spec
        if spec is None:
            violations.append("deep_materialization_check_without_spec")
        elif spec.lineage_plan.mode == _FI_LINEAGE_MODE_RAW_WITH_MIGRATIONS:
            # Finland exposes current-address timelines after replay-owned
            # migrations are projected. Re-materializing from those already-
            # rekeyed timelines would double-apply scope-changing move
            # semantics and drift from the canonical PIT path, which instead
            # materializes from raw lineage plus explicit migration events.
            pass
        else:
            remat = materialize_pit(
                products.timelines,
                as_of=spec.as_of,
                base=base_ir,
                query_type=spec.query_type,
                label_norm=fi_label_norm,
                lineage_plan=spec.lineage_plan,
            )
            remat = dc_replace(remat, body=_resort_children(remat.body))
            lhs = irnode_to_text(remat.body)
            rhs = irnode_to_text(products.materialized_state.ir)
            if lhs != rhs:
                violations.append("materialized_state_drift_from_timelines")

    return violations


__all__ = [
    "MaterializationSpec",
    "ReplayProducts",
    "build_replay_products",
    "validate_replay_products",
    "fi_label_norm",
    "_MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR",
]
