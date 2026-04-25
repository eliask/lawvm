"""Shared frozen envelope types for the Finland rulebook scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from lawvm.finland.rulebook.registries import EmitId, GuardId

PatternAtom = str | StrEnum


class RulebookValidationError(ValueError):
    """Raised when a frozen rulebook object violates a structural invariant."""


class RulePhase(StrEnum):
    CLAUSE_PARSE = "clause_parse"
    CLAUSE_RESOLVE = "clause_resolve"
    PAYLOAD_NORMALIZE = "payload_normalize"
    PAYLOAD_ELABORATE = "payload_elaborate"
    TEMPORAL = "temporal"
    SOURCE_NORMALIZE = "source_normalize"
    COMPARE = "compare"


class AuthorityTier(StrEnum):
    ENACTED_TEXT = "enacted_text"
    DRAFTING_GUIDE = "drafting_guide"
    FINLEX_AKN = "finlex_akn_profile"
    ORACLE_EDITORIAL = "oracle_editorial"
    LAWVM_POLICY = "lawvm_policy"


class RuleStrength(StrEnum):
    LITERAL = "literal"
    CONVENTIONAL = "conventional"
    HEURISTIC = "heuristic"
    POLICY = "policy"


class RuleFamilyId(StrEnum):
    CLAUSE = "clause"
    PAYLOAD = "payload"
    TEMPORAL = "temporal"
    SOURCE = "source"
    COMPARE = "compare"


@dataclass(frozen=True, slots=True)
class CitationRef:
    source: str
    locator: str = ""


@dataclass(frozen=True, slots=True)
class RuleExample:
    label: str
    input_text: str = ""
    input_xml: str = ""
    expects: tuple[str, ...] = ()
    rejects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GuardRef:
    guard_id: GuardId
    args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EmitRef:
    emit_id: EmitId
    args: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RuleHeader:
    rule_id: str
    phase: RulePhase
    priority: int
    authority: AuthorityTier
    strength: RuleStrength
    purpose: str
    citations: tuple[CitationRef, ...] = ()
    defeaters: tuple[str, ...] = ()
    examples: tuple[RuleExample, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise RulebookValidationError("rule_id must be non-empty")
        if not self.purpose:
            raise RulebookValidationError(f"{self.rule_id}: purpose must be non-empty")


@dataclass(frozen=True, slots=True)
class RuleApplication:
    rule_id: str
    phase: RulePhase
    authority: AuthorityTier
    strength: RuleStrength
    matched_spans: tuple[str, ...]
    emitted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleApplicationLedger:
    applications: tuple[RuleApplication, ...] = ()

    def record(self, application: RuleApplication) -> RuleApplicationLedger:
        return RuleApplicationLedger(applications=(*self.applications, application))

    def extend(self, applications: tuple[RuleApplication, ...]) -> RuleApplicationLedger:
        return RuleApplicationLedger(applications=(*self.applications, *applications))
