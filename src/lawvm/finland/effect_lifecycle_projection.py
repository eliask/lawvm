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
from lawvm.core.temporal import TemporalEvent
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride, EffectRelationSignal


def _typed_override_rows(
    lifecycle_overrides: Sequence[EffectLifecycleOverride],
) -> tuple[EffectLifecycleOverride, ...]:
    rows: list[EffectLifecycleOverride] = []
    for row in lifecycle_overrides:
        if not isinstance(row, EffectLifecycleOverride):
            raise TypeError("lifecycle_overrides must contain EffectLifecycleOverride rows")
        rows.append(row)
    return tuple(rows)


def _typed_relation_signals(
    relation_signals: Sequence[EffectRelationSignal],
) -> tuple[EffectRelationSignal, ...]:
    rows: list[EffectRelationSignal] = []
    for row in relation_signals:
        if not isinstance(row, EffectRelationSignal):
            raise TypeError("relation_signals must contain EffectRelationSignal rows")
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
    source_effects: Sequence[EffectRef] = (),
    effect_id: str | None = None,
) -> EffectRef | None:
    source = event.source
    if source is None or not source.statute_id:
        return None
    matched = _matching_operation_effect_for_temporal_event(event, source_effects)
    if matched is not None:
        return matched
    instrument = SourceInstrumentRef.from_operation_source(source)
    witness = SourceProvisionRef(
        instrument=instrument,
        path=(str(event.group_id or event.event_id),),
        span_id=event.event_id,
        text_excerpt=source.raw_text,
        rule_id="fi.temporal_event.lifecycle_projection",
    )
    return EffectRef(
        effect_id=effect_id or f"fi-effect:{source.statute_id}:{event.group_id or event.event_id}",
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
    effect_id: str | None = None,
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
        effect_id=effect_id or f"fi-effect:{source.statute_id}:{op.op_id or op.sequence}",
        source_instrument=instrument,
        target_statute=target_statute,
        target_address=op.target,
        projection_group_id=str(op.group_id or ""),
        source_provision=witness,
    )


def _matching_operation_effect_for_temporal_event(
    event: TemporalEvent,
    source_effects: Sequence[EffectRef],
) -> EffectRef | None:
    source = event.source
    if source is None or not source.statute_id:
        return None
    span_matches = tuple(
        effect
        for effect in source_effects
        if effect.source_instrument.instrument_id == source.statute_id
        and effect.source_provision is not None
        and effect.source_provision.rule_id == "fi.temporal_event.lifecycle_projection"
        and effect.source_provision.span_id == event.event_id
    )
    if len(span_matches) == 1:
        return span_matches[0]
    if not event.group_id:
        return None
    if len(event.scope.exact_addresses) != 1:
        return None
    target_address = event.scope.exact_addresses[0]
    matches = tuple(
        effect
        for effect in source_effects
        if effect.source_instrument.instrument_id == source.statute_id
        and effect.projection_group_id == event.group_id
        and effect.target_address == target_address
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _legal_operation_effect_id(op: LegalOperation, *, duplicated_op_key: bool) -> str:
    source = op.source
    if source is None:
        raise ValueError("operation effect identity requires source")
    base_id = str(op.op_id or op.sequence)
    if not duplicated_op_key:
        return f"fi-effect:{source.statute_id}:{base_id}"
    target_key = str(op.target).replace(" ", "_")
    return f"fi-effect:{source.statute_id}:{base_id}:seq-{op.sequence}:target-{target_key}"


def _duplicated_operation_effect_keys(
    canonical_ops: Sequence[LegalOperation],
) -> frozenset[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for op in canonical_ops:
        source = op.source
        if source is None or not source.statute_id:
            continue
        key = (source.statute_id, str(op.op_id or op.sequence))
        counts[key] = counts.get(key, 0) + 1
    return frozenset(key for key, count in counts.items() if count > 1)


def _temporal_event_effect_id(event: TemporalEvent, *, duplicated_event_key: bool) -> str:
    source = event.source
    if source is None:
        raise ValueError("temporal effect identity requires source")
    base_id = str(event.group_id or event.event_id)
    if not duplicated_event_key:
        return f"fi-effect:{source.statute_id}:{base_id}"
    if len(event.scope.exact_addresses) == 1:
        target_key = str(event.scope.exact_addresses[0]).replace(" ", "_")
    else:
        target_key = f"scope-{event.scope.target_statute or '-'}"
    return f"fi-effect:{source.statute_id}:{base_id}:event-{event.event_id}:target-{target_key}"


def _duplicated_temporal_effect_keys(
    temporal_events: Sequence[TemporalEvent],
) -> frozenset[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for event in temporal_events:
        source = event.source
        if source is None or not source.statute_id:
            continue
        key = (source.statute_id, str(event.group_id or event.event_id))
        counts[key] = counts.get(key, 0) + 1
    return frozenset(key for key, count in counts.items() if count > 1)


def _source_effects_from_ops_and_temporal_events(
    *,
    target_statute: str,
    canonical_ops: Sequence[LegalOperation],
    temporal_events: Sequence[TemporalEvent],
) -> tuple[EffectRef, ...]:
    effects_by_id: dict[str, EffectRef] = {}
    effects: list[EffectRef] = []
    duplicated_op_keys = _duplicated_operation_effect_keys(canonical_ops)
    duplicated_temporal_keys = _duplicated_temporal_effect_keys(temporal_events)
    for op in canonical_ops:
        source = op.source
        duplicated_op_key = (
            source is not None
            and bool(source.statute_id)
            and (source.statute_id, str(op.op_id or op.sequence)) in duplicated_op_keys
        )
        effect = _effect_ref_for_legal_operation(
            op,
            target_statute=target_statute,
            effect_id=(
                _legal_operation_effect_id(
                    op,
                    duplicated_op_key=duplicated_op_key,
                )
                if source is not None and source.statute_id
                else None
            ),
        )
        if effect is not None:
            _append_unique_effect(effects, effects_by_id, effect, subject="operation-derived source effects")
    for event in temporal_events:
        source = event.source
        duplicated_temporal_key = (
            source is not None
            and bool(source.statute_id)
            and (source.statute_id, str(event.group_id or event.event_id)) in duplicated_temporal_keys
        )
        effect = _effect_ref_for_temporal_event(
            event,
            target_statute=target_statute,
            source_effects=tuple(effects_by_id.values()),
            effect_id=(
                _temporal_event_effect_id(
                    event,
                    duplicated_event_key=duplicated_temporal_key,
                )
                if source is not None and source.statute_id
                else None
            ),
        )
        if effect is not None:
            _append_unique_effect(effects, effects_by_id, effect, subject="temporal-derived source effects")
    return tuple(effects)


def _append_unique_effect(
    effects: list[EffectRef],
    effects_by_id: dict[str, EffectRef],
    effect: EffectRef,
    *,
    subject: str,
) -> None:
    previous = effects_by_id.get(effect.effect_id)
    if previous is None:
        effects_by_id[effect.effect_id] = effect
        effects.append(effect)
        return
    if previous != effect:
        raise ValueError(f"{subject} conflicting duplicate effect_id: {effect.effect_id!r}")


def _merge_unique_source_effect_context(
    *lanes: Sequence[EffectRef],
) -> tuple[EffectRef, ...]:
    effects_by_id: dict[str, EffectRef] = {}
    effects: list[EffectRef] = []
    for lane in lanes:
        for effect in lane:
            _append_unique_effect(
                effects,
                effects_by_id,
                effect,
                subject="Finland effect lifecycle source context",
            )
    return tuple(effects)


def _lifecycle_from_temporal_events(
    temporal_events: Sequence[TemporalEvent],
    *,
    target_statute: str,
    source_effects: Sequence[EffectRef] = (),
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
        effect = _effect_ref_for_temporal_event(
            event,
            target_statute=target_statute,
            source_effects=source_effects,
        )
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
        expiry_convention = "exclusive_cutoff"
    elif relation.kind == "repeals_effect":
        lifecycle_kind = "repeal_effect"
        effective = row.effective or row.expiry
        expires = row.expiry or row.effective
        expiry_convention = "exclusive_cutoff"
    elif relation.kind == "extends_effect_expiry":
        lifecycle_kind = "change_effect_expiry"
        effective = ""
        expires = row.expiry
        expiry_convention = "inclusive_valid_until"
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
        expiry_convention=expiry_convention,
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


def _effects_matching_relation_signal(
    signal: EffectRelationSignal,
    source_effects: Sequence[EffectRef],
) -> tuple[EffectRef, ...]:
    if not signal.resolved or not signal.target_statute:
        return ()
    matches: list[EffectRef] = []
    seen: set[str] = set()
    for effect in source_effects:
        if effect.effect_id in seen:
            continue
        if effect.source_instrument.instrument_id != signal.target_statute:
            continue
        seen.add(effect.effect_id)
        matches.append(effect)
    return tuple(matches)


def _relation_from_signal(
    signal: EffectRelationSignal,
    *,
    target_effect: EffectRef | None = None,
    detail: dict[str, object] | None = None,
) -> EffectRelation | None:
    witness = _source_witness(
        source_statute=signal.source_statute,
        path=("routing",),
        rule_id=(
            "fi.pending_amendment_of_parent_effect_relation"
            if signal.signal_kind == "pending_amendment"
            else "fi.meta_repeal_effect_relation"
        ),
        text_excerpt=signal.message,
    )
    if witness is None:
        return None
    if target_effect is not None:
        return EffectRelation(
            relation_id=_relation_id(signal.source_statute, signal.signal_kind, target_effect.effect_id),
            kind=signal.relation_kind,
            source_provision=witness,
            target_effect=target_effect,
            detail={
                **signal.to_meta_row(),
                **(detail or {}),
                "target_effect_id": target_effect.effect_id,
            },
        )
    target_instrument = (
        SourceInstrumentRef(instrument_id=signal.target_statute, title=signal.target_title)
        if signal.target_statute
        else None
    )
    if target_instrument is None:
        return None
    return EffectRelation(
        relation_id=_relation_id(signal.source_statute, signal.signal_kind, signal.target_statute),
        kind=signal.relation_kind,
        source_provision=witness,
        target_instrument=target_instrument,
        detail={**signal.to_meta_row(), **(detail or {})},
    )


def _relations_from_signals(
    relation_signals: Sequence[EffectRelationSignal],
    *,
    source_effects: Sequence[EffectRef] = (),
) -> tuple[EffectRelation, ...]:
    relations: list[EffectRelation] = []
    seen: set[str] = set()
    for signal in _typed_relation_signals(relation_signals):
        matched_effects = _effects_matching_relation_signal(signal, source_effects)
        if signal.signal_kind == "pending_amendment" and len(matched_effects) > 1:
            relation = _relation_from_signal(
                signal,
                detail={
                    "target_effect_resolution": "ambiguous_multiple_effects",
                    "matched_effect_count": len(matched_effects),
                    "non_executable_reason": (
                        "pending amendment signal names an instrument but not "
                        "a unique source-backed effect"
                    ),
                },
            )
            relation_candidates = (relation,) if relation is not None else ()
        else:
            relation_candidates = tuple(
                relation
                for effect in matched_effects
                if (relation := _relation_from_signal(signal, target_effect=effect)) is not None
            )
        if not relation_candidates:
            relation = _relation_from_signal(signal)
            relation_candidates = (relation,) if relation is not None else ()
        for relation in relation_candidates:
            if relation.relation_id in seen:
                continue
            seen.add(relation.relation_id)
            relations.append(relation)
    return tuple(relations)


def _unresolved_lifecycle_from_relation_signals(
    relation_signals: Sequence[EffectRelationSignal],
) -> tuple[EffectLifecycleEvent, ...]:
    events: list[EffectLifecycleEvent] = []
    seen: set[str] = set()
    for signal in _typed_relation_signals(relation_signals):
        if signal.resolved:
            continue
        relation = _relation_from_signal(signal)
        witness = (
            relation.source_provision
            if relation is not None
            else _source_witness(
                source_statute=signal.source_statute,
                path=("routing",),
                rule_id=(
                    "fi.pending_amendment_of_parent_effect_unresolved"
                    if signal.signal_kind == "pending_amendment"
                    else "fi.meta_repeal_effect_unresolved"
                ),
                text_excerpt=signal.message,
            )
        )
        if witness is None:
            continue
        lifecycle_id = _lifecycle_id(signal.source_statute, signal.signal_kind, "unresolved")
        if lifecycle_id in seen:
            continue
        seen.add(lifecycle_id)
        events.append(
            EffectLifecycleEvent(
                lifecycle_event_id=lifecycle_id,
                kind="unresolved_effect_target",
                source_provision=witness,
                relation=relation,
                executable=False,
                detail={
                    "projection": "effect_relation_signal",
                    "intended_relation_kind": signal.relation_kind,
                    **signal.to_meta_row(),
                },
            )
        )
    return tuple(events)


def _lifecycle_events_from_resolved_signal_relations(
    relations: Sequence[EffectRelation],
) -> tuple[EffectLifecycleEvent, ...]:
    events: list[EffectLifecycleEvent] = []
    seen: set[str] = set()
    for relation in relations:
        if relation.source_provision.rule_id != "fi.meta_repeal_effect_relation":
            continue
        if relation.kind != "repeals_effect" or relation.target_effect is None:
            continue
        lifecycle_id = _lifecycle_id(relation.relation_id, "repeal_effect")
        if lifecycle_id in seen:
            continue
        seen.add(lifecycle_id)
        events.append(
            EffectLifecycleEvent(
                lifecycle_event_id=lifecycle_id,
                kind="repeal_effect",
                source_provision=relation.source_provision,
                effect=relation.target_effect,
                relation=relation,
                executable=False,
                detail={
                    "projection": "effect_relation_signal",
                    "executable_projection": False,
                    "non_executable_reason": "meta-repeal signal did not carry a deterministic repeal date",
                    **relation.detail,
                },
            )
        )
    return tuple(events)


def build_finland_effect_lifecycle(
    *,
    target_statute: str,
    canonical_ops: Sequence[LegalOperation],
    temporal_events: Sequence[TemporalEvent],
    lifecycle_overrides: Sequence[EffectLifecycleOverride] = (),
    relation_signals: Sequence[EffectRelationSignal] = (),
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
    source_effect_context = _merge_unique_source_effect_context(known_source_effects, source_effects)
    relations = (
        _relations_from_lifecycle_overrides(
            lifecycle_overrides,
            target_statute=target_statute,
            source_effects=source_effect_context,
        )
        + _relations_from_signals(relation_signals, source_effects=source_effect_context)
    )
    lifecycle_events = (
        _lifecycle_from_temporal_events(
            temporal_events,
            target_statute=target_statute,
            source_effects=source_effect_context,
        )
        + _lifecycle_events_from_lifecycle_overrides(
            lifecycle_overrides,
            target_statute=target_statute,
            source_effects=source_effect_context,
        )
        + _lifecycle_events_from_resolved_signal_relations(relations)
        + _unresolved_lifecycle_from_relation_signals(relation_signals)
    )
    return source_effects, relations, lifecycle_events
