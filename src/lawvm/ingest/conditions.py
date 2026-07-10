"""Level-NEUTRAL condition / restart carriers (conditions-and-restarts, Fable §10.1).

Extracted VERBATIM from ``lawvm.ingest.blackboard`` — these carriers model an
unexpected, out-of-vocabulary concern as a first-class CONDITION that is SIGNALED
(the signaler does NOT unwind — it stays live carrying resume state + the RESTARTS
it offers as data) and routed UP a handler chain (level_1 → composer → orchestrator
→ human, the terminal handler). The chosen ``(condition, restart, handler)``
resolution is journaled for byte-identical replay.

They live HERE (not in ``blackboard``) because the mechanism is LEVEL-NEUTRAL: a
Level-1 reader, a Level-2 composer, or the orchestrator may all signal / handle a
condition. ``blackboard`` re-exports them (``from lawvm.ingest.conditions import *``)
so every existing import site keeps working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from typing_extensions import override

from lawvm.ingest.simulacrum import SpanRef

__all__ = ["Restart", "OwnerLevel", "Escalation", "EscalationResolution"]


# The closed set of RESTARTS a signaler may offer. Restarts are DATA — a handler
# up the chain picks one and the signaler resumes from the signal point; the
# choice is journaled for byte-identical replay.
class Restart(Enum):
    """A valid way for a handler to continue an escalated condition (offered as data)."""

    ROUTE_TO_LEVEL_1 = "route-to-level-1"
    RE_READ_REGION_HIGHER_DPI = "re-read-region-higher-dpi"
    USE_FALLBACK_READER = "use-fallback-reader"
    MARK_UNRESOLVED_AND_CONTINUE = "mark-unresolved-and-continue"
    ABORT_REGION = "abort-region"
    DEFER_TO_HUMAN = "defer-to-human"

    @override
    def __str__(self) -> str:
        return self.value


# Suggested owner level for a condition (who SHOULD handle it, advisory).
class OwnerLevel(Enum):
    LEVEL_1 = "level_1"
    COMPOSER = "composer"
    ORCHESTRATOR = "orchestrator"
    HUMAN = "human"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Escalation:
    """A first-class ESCALATE CONDITION routed UP (composer → orchestrator → human).

    Modeled on Common-Lisp conditions-and-restarts (spec §10.1), NOT a fire-and-
    forget record: the signaler does NOT unwind — it stays live carrying its
    ``resume_state`` so a handler can RESUME it by choosing one of the ``restarts``
    it offers (restarts are DATA — the valid ways to continue). A handler up the
    chain picks a restart; the signaler resumes from the signal point. NEVER
    silently swallowed (``human`` is the terminal handler); never a composed-tree
    edit. Every ``(condition, chosen_restart, handler)`` resolution is journaled
    (``EscalationResolution``) so a cache-HIT re-run replays it byte-identically.
    """

    origin_producer: str
    origin_level: OwnerLevel
    region: Tuple[SpanRef, ...]
    violated_expectation: str  # the violated expectation, or the literal "unanticipated"
    restarts: Tuple[Restart, ...]  # the valid ways to continue (offered as data)
    suggested_owner: OwnerLevel
    resume_state: str = ""  # signaler state needed to resume (compact, serializable)
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class EscalationResolution:
    """The journaled ``(condition, chosen_restart, handler)`` triple (deterministic).

    Recorded so a cache-HIT re-run replays the SAME resolution byte-identically. A
    condition with no resolution yet is UNHANDLED — surfaced upward until the
    terminal (human) handler picks a restart.
    """

    condition: Escalation
    chosen_restart: Restart
    handler: OwnerLevel
