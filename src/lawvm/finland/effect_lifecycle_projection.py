"""Finland projection into core effect-lifecycle carriers."""

from __future__ import annotations

from typing import Sequence

from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectLifecycleEventKind,
    EffectRef,
    EffectRelation,
    EffectRelationKind,
    SourceInstrumentRef,
    SourceProvisionRef,
)
from lawvm.core.ir import LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.temporal import TemporalEvent
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride


def _typed_override_rows(
    lifecycle_overrides: Sequence[EffectLifecycleOverride],
) -> tuple[EffectLifecycleOverride, ...]:
    rows: list[EffectLifecycleOverride] = []
    for row in lifecycle_overrides:
        if not isinstance(row, EffectLifecycleOverride):
            raise TypeError("lifecycle_overrides must contain EffectLifecycleOverride rows")
        rows.append(row)
    return tuple(rows)


def _source_instrument(source_statute: str, title: str = "") -> SourceInstrumentRef | None:
    source_statute = str(source_statute or "").strip()
    if not source_statute:
        return None
    return SourceInstrumentRef(instrument_id=source_statute, title=str(title or ""))


def _source_witness(
    *,
    source_statute: str,
    title: str = "",
    path: tuple[str, ...] = (),
    rule_id: str,
    text_excerpt: str = "",
) -> SourceProvisionRef | None:
    instrument = _source_instrument(source_statute, title)
    if instrument is None:
        return None
    return SourceProvisionRef(
        instrument=instrument,
        path=path,
        rule_id=rule_id,
        text_excerpt=text_excerpt,
    )


def _relation_id(*parts: object) -> str:
    return "fi-effect-relation:" + ":".join(str(part or "-").replace(" ", "_") for part in parts)


def _lifecycle_id(*parts: object) -> str:
    return "fi-effect-lifecycle:" + ":".join(str(part or "-").replace(" ", "_") for part in parts)


def _effect_ref_for_temporal_event(
    event: TemporalEvent,
    *,
    target_statute: str,
) -> EffectRef | None:
    source = event.source
    if source is None or not source.statute_id:
        return None
    instrument = SourceInstrumentRef.from_operation_source(source)
    witness = SourceProvisionRef(
        instrument=instrument,
        path=(str(event.group_id or event.event_id),),
        span_id=event.event_id,
        text_excerpt=source.raw_text,
        rule_id="fi.temporal_event.lifecycle_projection",
    )
    return EffectRef(
        effect_id=f"fi-effect:{source.statute_id}:{event.group_id or event.event_id}",
        source_instrument=instrument,
        target_statute=event.scope.target_statute or target_statute,
        target_address=event.scope.exact_addresses[0] if len(event.scope.exact_addresses) == 1 else None,
        projection_group_id=str(event.group_id or ""),
        source_provision=witness,
    )


def _effect_ref_for_legal_operation(
    op: LegalOperation,
    *,
    target_statute: str,
) -> EffectRef | None:
    source = op.source
    if source is None or not source.statute_id:
        return None
    instrument = SourceInstrumentRef.from_operation_source(source)
    witness = SourceProvisionRef(
        instrument=instrument,
        path=(op.op_id or str(op.target),),
        span_id=op.op_id,
        text_excerpt=source.raw_text,
        rule_id=op.witness_rule_id or "fi.legal_operation.effect_declaration",
    )
    return EffectRef(
        effect_id=f"fi-effect:{source.statute_id}:{op.op_id or op.sequence}",
        source_instrument=instrument,
        target_statute=target_statute,
        target_address=op.target,
        projection_group_id=str(op.group_id or ""),
        source_provision=witness,
    )


def _source_effects_from_ops_and_temporal_events(
    *,
    target_statute: str,
    canonical_ops: Sequence[LegalOperation],
    temporal_events: Sequence[TemporalEvent],
) -> tuple[EffectRef, ...]:
    effects_by_id: dict[str, EffectRef] = {}
    for op in canonical_ops:
        effect = _effect_ref_for_legal_operation(op, target_statute=target_statute)
        if effect is not None:
            effects_by_id.setdefault(effect.effect_id, effect)
    for event in temporal_events:
        effect = _effect_ref_for_temporal_event(event, target_statute=target_statute)
        if effect is not None:
            effects_by_id.setdefault(effect.effect_id, effect)
    return tuple(effects_by_id.values())


def _lifecycle_from_temporal_events(
    temporal_events: Sequence[TemporalEvent],
    *,
    target_statute: str,
) -> tuple[EffectLifecycleEvent, ...]:
    lifecycle_events: list[EffectLifecycleEvent] = []
    kind_map: dict[str, EffectLifecycleEventKind] = {
        "commence": "commence_effect",
        "expire": "expire_effect",
        "suspend": "suspend_effect",
        "revive": "revive_effect",
    }
    for event in temporal_events:
        lifecycle_kind = kind_map.get(event.kind)
        if lifecycle_kind is None:
            continue
        effect = _effect_ref_for_temporal_event(event, target_statute=target_statute)
        if effect is None or effect.source_provision is None:
            continue
        lifecycle_events.append(
            EffectLifecycleEvent(
                lifecycle_event_id=_lifecycle_id(event.event_id),
                kind=lifecycle_kind,
                source_provision=effect.source_provision,
                effect=effect,
                effective=event.effective,
                expires=event.expires,
                temporal_event=event,
                executable=True,
                detail={
                    "projection": "temporal_event",
                    "temporal_event_id": event.event_id,
                },
            )
        )
    return tuple(lifecycle_events)


def _effect_matches_override_scope(effect: EffectRef, row: EffectLifecycleOverride) -> bool:
    if effect.source_instrument.instrument_id != row.target_statute:
        return False
    if row.scope.kind == "instrument":
        return True
    if effect.target_address is None:
        return False
    if row.scope.kind == "address":
        return effect.target_address in row.scope.addresses
    if row.scope.kind == "section":
        section_labels = set(row.scope.labels)
        return any(
            kind == "section" and label in section_labels
            for kind, label in effect.target_address.path
        )
    if row.scope.kind == "mixed":
        if effect.target_address in row.scope.addresses:
            return True
        section_labels = set(row.scope.labels)
        return any(
            kind == "section" and label in section_labels
            for kind, label in effect.target_address.path
        )
    return False


def _matched_target_effects(
    *,
    row: EffectLifecycleOverride,
    source_effects: Sequence[EffectRef],
) -> tuple[EffectRef, ...]:
    matches: list[EffectRef] = []
    seen: set[str] = set()
    for effect in source_effects:
        if effect.effect_id in seen:
            continue
        if not _effect_matches_override_scope(effect, row):
            continue
        seen.add(effect.effect_id)
        matches.append(effect)
    return tuple(matches)


def _relations_from_lifecycle_overrides(
    lifecycle_overrides: Sequence[EffectLifecycleOverride],
    *,
    target_statute: str,
    source_effects: Sequence[EffectRef] = (),
) -> tuple[EffectRelation, ...]:
    relations: list[EffectRelation] = []
    for row in _typed_override_rows(lifecycle_overrides):
        witness = _source_witness(
            source_statute=row.source_statute,
            path=("voimaantulo",),
            rule_id="fi.commencement_lifecycle_override",
        )
        if witness is None:
            continue
        if row.effective:
            kind: EffectRelationKind = "changes_effect_commencement"
        elif row.context == "repeal_clause":
            kind = "repeals_effect"
        else:
            kind = "extends_effect_expiry"
        target_effects = _matched_target_effects(row=row, source_effects=source_effects)
        if not target_effects:
            target_instrument = _source_instrument(row.target_statute)
            if target_instrument is None:
                continue
            relations.append(
                EffectRelation(
                    relation_id=_relation_id(
                        row.source_statute,
                        kind,
                        row.target_statute,
                        row.scope.key,
                    ),
                    kind=kind,
                    source_provision=witness,
                    target_instrument=target_instrument,
                    detail={
                        **row.to_meta_row(),
                        "resolved": False,
                        "unresolved_reason": "no matching source effect in current projection context",
                    },
                )
            )
            continue
        for target_effect in target_effects:
            relations.append(
                EffectRelation(
                    relation_id=_relation_id(
                        row.source_statute,
                        kind,
                        target_effect.effect_id,
                    ),
                    kind=kind,
                    source_provision=witness,
                    target_effect=target_effect,
                    detail={
                        **row.to_meta_row(),
                        "resolved": True,
                    },
                )
            )
    return tuple(relations)


def _intended_lifecycle_kind_for_relation(relation: EffectRelation) -> EffectLifecycleEventKind | None:
    if relation.kind == "changes_effect_commencement":
        return "change_effect_commencement"
    if relation.kind == "repeals_effect":
        return "repeal_effect"
    if relation.kind == "extends_effect_expiry":
        return "change_effect_expiry"
    return None


def _lifecycle_event_from_unmatched_override_relation(
    *,
    row: EffectLifecycleOverride,
    relation: EffectRelation,
) -> EffectLifecycleEvent | None:
    lifecycle_kind = _intended_lifecycle_kind_for_relation(relation)
    if lifecycle_kind is None:
        return None
    return EffectLifecycleEvent(
        lifecycle_event_id=_lifecycle_id(relation.relation_id, "unresolved"),
        kind="unresolved_effect_target",
        source_provision=relation.source_provision,
        relation=relation,
        executable=False,
        detail={
            "projection": "source_lifecycle_override",
            "executable_projection": False,
            "intended_lifecycle_kind": lifecycle_kind,
            "non_executable_reason": "override row did not match a source-backed target effect",
            **row.to_meta_row(),
        },
    )


def _lifecycle_event_from_override_relation(
    *,
    row: EffectLifecycleOverride,
    relation: EffectRelation,
) -> EffectLifecycleEvent | None:
    if relation.target_effect is None:
        return None
    if relation.kind == "changes_effect_commencement":
        lifecycle_kind: EffectLifecycleEventKind = "change_effect_commencement"
        effective = row.effective
        expires = ""
    elif relation.kind == "repeals_effect":
        lifecycle_kind = "repeal_effect"
        effective = row.effective or row.expiry
        expires = row.expiry or row.effective
    elif relation.kind == "extends_effect_expiry":
        lifecycle_kind = "change_effect_expiry"
        effective = ""
        expires = row.expiry
    else:
        return None
    return EffectLifecycleEvent(
        lifecycle_event_id=_lifecycle_id(relation.relation_id, lifecycle_kind),
        kind=lifecycle_kind,
        source_provision=relation.source_provision,
        effect=relation.target_effect,
        relation=relation,
        effective=effective,
        expires=expires,
        executable=True,
        detail={
            "projection": "source_lifecycle_override",
            "executable_projection": True,
            **row.to_meta_row(),
        },
    )


def _lifecycle_events_from_lifecycle_overrides(
    lifecycle_overrides: Sequence[EffectLifecycleOverride],
    *,
    target_statute: str,
    source_effects: Sequence[EffectRef] = (),
) -> tuple[EffectLifecycleEvent, ...]:
    events: list[EffectLifecycleEvent] = []
    seen: set[str] = set()
    for row in _typed_override_rows(lifecycle_overrides):
        relations = _relations_from_lifecycle_overrides(
            (row,),
            target_statute=target_statute,
            source_effects=source_effects,
        )
        for relation in relations:
            if relation.target_effect is None:
                event = _lifecycle_event_from_unmatched_override_relation(
                    row=row,
                    relation=relation,
                )
            else:
                event = _lifecycle_event_from_override_relation(row=row, relation=relation)
            if event is None or event.lifecycle_event_id in seen:
                continue
            seen.add(event.lifecycle_event_id)
            events.append(event)
    return tuple(events)


def _pending_relation_from_finding(finding: Finding) -> EffectRelation | None:
    detail = dict(finding.detail)
    source_statute = str(finding.source_statute or "")
    target_id = str(detail.get("target_amendment_id") or "")
    target_title = str(detail.get("target_amendment_title") or "")
    if not source_statute or not target_id:
        return None
    witness = _source_witness(
        source_statute=source_statute,
        path=("routing",),
        rule_id="fi.pending_amendment_of_parent_effect_relation",
        text_excerpt=str(detail.get("message") or ""),
    )
    if witness is None:
        return None
    return EffectRelation(
        relation_id=_relation_id(source_statute, "pending_amendment", target_id),
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=SourceInstrumentRef(instrument_id=target_id, title=target_title),
        detail={
            "source_finding": finding.kind,
            "target_amendment_id": target_id,
            "target_amendment_title": target_title,
            "base_parent_id": str(detail.get("base_parent_id") or ""),
            "resolved": finding.kind == "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
        },
    )


def _pending_relations_from_findings(findings: Sequence[Finding]) -> tuple[EffectRelation, ...]:
    relations: list[EffectRelation] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.kind not in {
            "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
            "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
        }:
            continue
        relation = _pending_relation_from_finding(finding)
        if relation is None or relation.relation_id in seen:
            continue
        seen.add(relation.relation_id)
        relations.append(relation)
    return tuple(relations)


def _meta_repeal_relation_from_finding(finding: Finding) -> EffectRelation | None:
    detail = dict(finding.detail)
    source_statute = str(finding.source_statute or "")
    target_id = str(detail.get("target_amendment_id") or "")
    if not source_statute or not target_id:
        return None
    witness = _source_witness(
        source_statute=source_statute,
        path=("routing",),
        rule_id="fi.meta_repeal_effect_relation",
        text_excerpt=str(detail.get("message") or ""),
    )
    if witness is None:
        return None
    return EffectRelation(
        relation_id=_relation_id(source_statute, "meta_repeal", target_id),
        kind="repeals_effect",
        source_provision=witness,
        target_instrument=SourceInstrumentRef(instrument_id=target_id),
        detail={
            "source_finding": finding.kind,
            "target_amendment_id": target_id,
            "route_reason": str(detail.get("route_reason") or ""),
            "resolved": True,
        },
    )


def _meta_repeal_relations_from_findings(findings: Sequence[Finding]) -> tuple[EffectRelation, ...]:
    relations: list[EffectRelation] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.kind != "APPLY.META_REPEAL_EFFECT_RECORDED":
            continue
        relation = _meta_repeal_relation_from_finding(finding)
        if relation is None or relation.relation_id in seen:
            continue
        seen.add(relation.relation_id)
        relations.append(relation)
    return tuple(relations)


def _unresolved_lifecycle_from_pending_findings(
    findings: Sequence[Finding],
) -> tuple[EffectLifecycleEvent, ...]:
    events: list[EffectLifecycleEvent] = []
    for finding in findings:
        if finding.kind != "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED":
            continue
        relation = _pending_relation_from_finding(finding)
        if relation is None:
            continue
        events.append(
            EffectLifecycleEvent(
                lifecycle_event_id=_lifecycle_id(finding.source_statute, "pending_unresolved"),
                kind="unresolved_effect_target",
                source_provision=relation.source_provision,
                relation=relation,
                executable=False,
                detail={
                    "source_finding": finding.kind,
                    **dict(finding.detail),
                },
            )
        )
    return tuple(events)


def _unresolved_lifecycle_from_meta_repeal_findings(
    findings: Sequence[Finding],
) -> tuple[EffectLifecycleEvent, ...]:
    events: list[EffectLifecycleEvent] = []
    for finding in findings:
        if finding.kind != "APPLY.META_REPEAL_EFFECT_UNRESOLVED":
            continue
        source_statute = str(finding.source_statute or "")
        if not source_statute:
            continue
        witness = _source_witness(
            source_statute=source_statute,
            path=("routing",),
            rule_id="fi.meta_repeal_effect_unresolved",
            text_excerpt=str(dict(finding.detail).get("message") or ""),
        )
        if witness is None:
            continue
        events.append(
            EffectLifecycleEvent(
                lifecycle_event_id=_lifecycle_id(source_statute, "meta_repeal_unresolved"),
                kind="unresolved_effect_target",
                source_provision=witness,
                executable=False,
                detail={
                    "source_finding": finding.kind,
                    **dict(finding.detail),
                },
            )
        )
    return tuple(events)


def build_finland_effect_lifecycle(
    *,
    target_statute: str,
    canonical_ops: Sequence[LegalOperation],
    temporal_events: Sequence[TemporalEvent],
    findings: Sequence[Finding],
    lifecycle_overrides: Sequence[EffectLifecycleOverride] = (),
    known_source_effects: Sequence[EffectRef] = (),
) -> tuple[tuple[EffectRef, ...], tuple[EffectRelation, ...], tuple[EffectLifecycleEvent, ...]]:
    """Build Finland's current effect-lifecycle evidence projection.

    Direct source-effect identity is minted from canonical operations and
    temporal carriers. Executable lifecycle projection currently comes from
    ``temporal_events``, which are already the replay-owned projection of those
    operations.
    """
    source_effects = _source_effects_from_ops_and_temporal_events(
        target_statute=target_statute,
        canonical_ops=canonical_ops,
        temporal_events=temporal_events,
    )
    source_effects_by_id = {
        effect.effect_id: effect
        for effect in tuple(known_source_effects) + tuple(source_effects)
    }
    source_effect_context = tuple(source_effects_by_id.values())
    relations = (
        _relations_from_lifecycle_overrides(
            lifecycle_overrides,
            target_statute=target_statute,
            source_effects=source_effect_context,
        )
        + _pending_relations_from_findings(findings)
        + _meta_repeal_relations_from_findings(findings)
    )
    lifecycle_events = (
        _lifecycle_from_temporal_events(temporal_events, target_statute=target_statute)
        + _lifecycle_events_from_lifecycle_overrides(
            lifecycle_overrides,
            target_statute=target_statute,
            source_effects=source_effect_context,
        )
        + _unresolved_lifecycle_from_pending_findings(findings)
        + _unresolved_lifecycle_from_meta_repeal_findings(findings)
    )
    return source_effects, relations, lifecycle_events
