"""Shared Finland rule-family dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from lawvm.finland.rulebook.common import (
    EmitRef,
    GuardRef,
    PatternAtom,
    RuleFamilyId,
    RuleHeader,
    RulebookValidationError,
)


class _HeaderedRule(Protocol):
    header: RuleHeader


RuleT = TypeVar("RuleT", bound=_HeaderedRule)


@dataclass(frozen=True, slots=True)
class ClauseRule:
    header: RuleHeader
    when: tuple[PatternAtom, ...]
    guards: tuple[GuardRef, ...] = ()
    emits: tuple[EmitRef, ...] = ()


@dataclass(frozen=True, slots=True)
class PayloadRule:
    header: RuleHeader
    when: tuple[PatternAtom, ...]
    guards: tuple[GuardRef, ...] = ()
    emits: tuple[EmitRef, ...] = ()


@dataclass(frozen=True, slots=True)
class TemporalRule:
    header: RuleHeader
    when: tuple[PatternAtom, ...]
    guards: tuple[GuardRef, ...] = ()
    emits: tuple[EmitRef, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRule:
    header: RuleHeader
    when: tuple[PatternAtom, ...]
    guards: tuple[GuardRef, ...] = ()
    emits: tuple[EmitRef, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleFamily(Generic[RuleT]):
    family_id: RuleFamilyId
    rules: tuple[RuleT, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if len({rule.header.rule_id for rule in self.rules}) != len(self.rules):
            raise RulebookValidationError(
                f"{self.family_id}: duplicate rule_id within family"
            )
