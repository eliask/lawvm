"""Shared types for the Finland compare-policy rule family."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.finland.rulebook.common import EmitRef, GuardRef, PatternAtom, RuleHeader


@dataclass(frozen=True, slots=True)
class CompareRule:
    header: RuleHeader
    when: tuple[PatternAtom, ...]
    guards: tuple[GuardRef, ...] = ()
    emits: tuple[EmitRef, ...] = ()
