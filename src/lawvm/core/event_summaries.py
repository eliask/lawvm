"""Shared event-summary helpers for core carrier surfaces."""

from __future__ import annotations

from typing import Any, Iterable


def distinct_event_kinds(events: Iterable[Any]) -> tuple[str, ...]:
    """Return sorted distinct string kinds for an event sequence."""
    return tuple(sorted({event.kind for event in events}))


def count_events_with_activation_rules(events: Iterable[Any]) -> int:
    """Return how many temporal events carry embedded activation rules."""
    return sum(1 for event in events if event.has_activation_rule)


def count_events_with_source(events: Iterable[Any]) -> int:
    """Return how many temporal events carry a provenance source carrier."""
    return sum(1 for event in events if event.source is not None)


def distinct_activation_rule_kinds(events: Iterable[Any]) -> tuple[str, ...]:
    """Return sorted distinct activation-rule kinds for temporal events."""
    return tuple(sorted({event.activation_rule_kind for event in events if event.has_activation_rule}))
