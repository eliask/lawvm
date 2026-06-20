"""Typed merge helpers for Finland effect graph buffers."""

from __future__ import annotations

from lawvm.core.effect_lifecycle import EffectLifecycleEvent, EffectRef, EffectRelation


def append_unique_effect_ref(
    target: list[EffectRef],
    effect: EffectRef,
    *,
    subject: str,
) -> None:
    for existing in target:
        if existing.effect_id != effect.effect_id:
            continue
        if existing != effect:
            raise ValueError(f"{subject} conflicting duplicate effect_id: {effect.effect_id!r}")
        return
    target.append(effect)


def append_unique_effect_refs(
    target: list[EffectRef],
    effects: tuple[EffectRef, ...],
    *,
    subject: str,
) -> None:
    for effect in effects:
        append_unique_effect_ref(target, effect, subject=subject)


def append_unique_effect_relation(
    target: list[EffectRelation],
    relation: EffectRelation,
    *,
    subject: str,
) -> None:
    for existing in target:
        if existing.relation_id != relation.relation_id:
            continue
        if existing != relation:
            raise ValueError(f"{subject} conflicting duplicate relation_id: {relation.relation_id!r}")
        return
    target.append(relation)


def append_unique_effect_relations(
    target: list[EffectRelation],
    relations: tuple[EffectRelation, ...],
    *,
    subject: str,
) -> None:
    for relation in relations:
        append_unique_effect_relation(target, relation, subject=subject)


def append_unique_effect_lifecycle_event(
    target: list[EffectLifecycleEvent],
    event: EffectLifecycleEvent,
    *,
    subject: str,
) -> None:
    for existing in target:
        if existing.lifecycle_event_id != event.lifecycle_event_id:
            continue
        if existing != event:
            raise ValueError(
                f"{subject} conflicting duplicate lifecycle_event_id: {event.lifecycle_event_id!r}"
            )
        return
    target.append(event)


def append_unique_effect_lifecycle_events(
    target: list[EffectLifecycleEvent],
    events: tuple[EffectLifecycleEvent, ...],
    *,
    subject: str,
) -> None:
    for event in events:
        append_unique_effect_lifecycle_event(target, event, subject=subject)
