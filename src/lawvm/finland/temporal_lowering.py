"""Finland-specific lowering of commencement semantics to typed ActivationRules.

This module bridges Finland's parse-layer commencement representations
(``Commencement`` EffectIntents and ``TemporalEvent`` instances) into the
shared-kernel ``ActivationRule`` type.

It also bridges the Phase 7 surface meta-clause layer
(``SurfaceMetaClause``) into activation rules, providing a direct path
from ``parse_clause().meta_clauses`` into the typed temporal model.

The lowering is one-directional and additive: it creates ``ActivationRule``
objects alongside existing TemporalEvent / EffectIntent material.  It does
NOT modify or replace the existing temporal pipeline or fabricate retired
activation-shell types.

API tier
--------
Finland-local lowering surface.  Do not import this from other jurisdictions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Optional

from lawvm.core.effect_intent import Commencement, EffectIntent
from lawvm.core.phase_result import Finding
from lawvm.core.temporal import ActivationRule, TemporalEvent
from lawvm.core.semantic_types import MetaClauseKind
from lawvm.finland.johtolause.surface_model import SurfaceMetaClause


# ---------------------------------------------------------------------------
# Pattern constants for Finnish conditional commencement detection
# ---------------------------------------------------------------------------

_SIMULTANEOUS_PATTERN = re.compile(
    r"samanaikaisesti\s+kuin"
    r"|yhtä\s+aikaa\s+.{0,40}kanssa"
    r"|samaan\s+aikaan\s+kuin",
    re.IGNORECASE,
)

_DECREE_SET_PATTERN = re.compile(
    r"asetuksella\s+säädettävänä\s+ajankohtana"
    r"|valtioneuvoston\s+(?:asetuksella|päätöksellä)"
    r"|erikseen\s+säädettävän[aä]"
    r"|voimaantulosta\s+säädetään\s+asetuksella",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Lowering: Commencement → ActivationRule
# ---------------------------------------------------------------------------


def activation_rule_from_commencement(intent: Commencement) -> ActivationRule:
    """Lower a Finland Commencement EffectIntent to an ActivationRule.

    Mapping:

    - ``is_contingent=False`` + ``effective_date`` present
      → ``ActivationRule(kind="fixed_date", effective_date=...)``
    - ``is_contingent=False`` + ``effective_date`` absent
      → ``ActivationRule(kind="immediate")``
    - ``is_contingent=True`` + decree-set text pattern
      → ``ActivationRule(kind="pending_decree")``
    - ``is_contingent=True`` + simultaneous-entry pattern
      → ``ActivationRule(kind="pending_condition", condition_ref=...)``
    - ``is_contingent=True`` + no further pattern
      → ``ActivationRule(kind="pending_decree")`` (default contingent)
    """
    raw = intent.raw_text

    if not intent.is_contingent:
        if intent.effective_date is not None:
            return ActivationRule(
                kind="fixed_date",
                effective_date=intent.effective_date.isoformat(),
                raw_text=raw,
            )
        # No date and not contingent: treat as immediate
        return ActivationRule(kind="immediate", raw_text=raw)

    # Contingent: distinguish pending_decree vs pending_condition
    if _SIMULTANEOUS_PATTERN.search(raw):
        # Extract condition reference from text (best-effort)
        condition_ref = _extract_simultaneous_ref(raw)
        return ActivationRule(
            kind="pending_condition",
            condition_ref=condition_ref,
            raw_text=raw,
        )

    # Default contingent → pending_decree
    return ActivationRule(
        kind="pending_decree",
        raw_text=raw,
    )


def _extract_simultaneous_ref(raw: str) -> str:
    """Extract a best-effort reference from a simultaneous-entry clause.

    Example: "tulee voimaan samanaikaisesti kuin laki X" → "laki X"
    This is a heuristic — returns the raw text tail after the pattern match.
    """
    m = _SIMULTANEOUS_PATTERN.search(raw)
    if m is None:
        return ""
    # Take the text after the pattern match as the reference
    tail = raw[m.end():].strip().rstrip(".")
    # Limit to a reasonable length
    if len(tail) > 200:
        tail = tail[:200]
    return tail


# ---------------------------------------------------------------------------
# Lowering: TemporalEvent → ActivationRule
# ---------------------------------------------------------------------------


def activation_rule_from_temporal_event(event: TemporalEvent) -> Optional[ActivationRule]:
    """Lower a TemporalEvent to an ActivationRule if it is a commencement event.

    Non-commencement events (expire, suspend, revive, set_applicability)
    return None — they do not have activation semantics in the current model.
    """
    if event.kind != "commence":
        return None

    if event.activation_rule is not None:
        return event.activation_rule

    if event.effective:
        return ActivationRule(
            kind="fixed_date",
            effective_date=event.effective,
            raw_text=event.derived_from_effect_intent or "",
        )

    return ActivationRule(
        kind="immediate",
        raw_text=event.derived_from_effect_intent or "",
    )


# ---------------------------------------------------------------------------
# Bulk lowering helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemporalActivationLoweringResult:
    activation_rules: tuple[ActivationRule, ...]
    findings: tuple[Finding, ...]


def _activation_rule_input_skipped(
    *,
    lane: str,
    input_kind: str,
    detail: dict[str, object],
) -> Finding:
    return Finding(
        kind="TIME.ACTIVATION_RULE_INPUT_SKIPPED",
        role="observation",
        stage="temporal_lowering",
        detail={
            "message": "Temporal input skipped because it does not lower to ActivationRule",
            "lane": lane,
            "input_kind": input_kind,
            **detail,
        },
        blocking=False,
    )


def lower_commencement_intents(
    intents: Sequence[EffectIntent],
) -> list[ActivationRule]:
    """Lower all Commencement intents from a list of EffectIntents.

    Non-Commencement intents are silently skipped.
    """
    return list(lower_commencement_intents_with_findings(intents).activation_rules)


def lower_commencement_intents_with_findings(
    intents: Sequence[EffectIntent],
) -> TemporalActivationLoweringResult:
    rules: list[ActivationRule] = []
    findings: list[Finding] = []
    for intent in intents:
        if isinstance(intent, Commencement):
            rules.append(activation_rule_from_commencement(intent))
        else:
            findings.append(
                _activation_rule_input_skipped(
                    lane="effect_intent",
                    input_kind=type(intent).__name__,
                    detail={"raw_text": intent.raw_text},
                )
            )
    return TemporalActivationLoweringResult(tuple(rules), tuple(findings))


def lower_temporal_events_to_activation_rules(
    events: tuple[TemporalEvent, ...] | list[TemporalEvent],
) -> list[ActivationRule]:
    """Lower commencement TemporalEvents to ActivationRules.

    Non-commencement events are silently skipped.
    """
    return list(lower_temporal_events_to_activation_rules_with_findings(events).activation_rules)


def lower_temporal_events_to_activation_rules_with_findings(
    events: tuple[TemporalEvent, ...] | list[TemporalEvent],
) -> TemporalActivationLoweringResult:
    rules: list[ActivationRule] = []
    findings: list[Finding] = []
    for event in events:
        rule = activation_rule_from_temporal_event(event)
        if rule is not None:
            rules.append(rule)
        else:
            findings.append(
                _activation_rule_input_skipped(
                    lane="temporal_event",
                    input_kind=event.kind,
                    detail={
                        "event_id": event.event_id,
                        "group_id": event.group_id or "",
                        "effective": event.effective,
                        "expires": event.expires,
                        "derived_from_effect_intent": event.derived_from_effect_intent or "",
                    },
                )
            )
    return TemporalActivationLoweringResult(tuple(rules), tuple(findings))


# ---------------------------------------------------------------------------
# SurfaceMetaClause → ActivationRule
# ---------------------------------------------------------------------------

# Re-use the contingent detection patterns defined above for SurfaceMetaClause
# text classification. The _DECREE_SET_PATTERN and _SIMULTANEOUS_PATTERN are
# the same Finnish-language patterns used for Commencement lowering.

_COMMENCEMENT_DATE_PATTERN = re.compile(
    r"(\d{1,2})\s+päivän[aä]\s+([a-zäöå]+)\s+(\d{4})",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "tammikuuta": 1,
    "helmikuuta": 2,
    "maaliskuuta": 3,
    "huhtikuuta": 4,
    "toukokuuta": 5,
    "kesäkuuta": 6,
    "heinäkuuta": 7,
    "elokuuta": 8,
    "syyskuuta": 9,
    "lokakuuta": 10,
    "marraskuuta": 11,
    "joulukuuta": 12,
}


def _extract_date_from_text(text: str) -> str:
    """Extract ISO-8601 date from Finnish commencement text, or empty string."""
    m = _COMMENCEMENT_DATE_PATTERN.search(text)
    if m is None:
        return ""
    month = _MONTH_MAP.get(m.group(2).lower())
    if month is None:
        return ""
    try:
        import datetime as dt

        d = dt.date(int(m.group(3)), month, int(m.group(1)))
        return d.isoformat()
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Expiry date extraction — "on voimassa ... DD päivään MM YYYY"
# ---------------------------------------------------------------------------

# Finnish expiry clause: "on voimassa NN päivään MM YYYY asti"
# The ordinal suffix on the day is "päivään" (allative) not "päivänä" (essive).
# Both forms can appear; match both.
_EXPIRY_DATE_PATTERN = re.compile(
    r"(\d{1,2})\s+päivä[äa]n\s+([a-zäöå]+)\s+(\d{4})"
    r"|(\d{1,2})\s+päivän[aä]\s+([a-zäöå]+)\s+(\d{4})",
    re.IGNORECASE,
)


def _extract_expiry_date_from_text(text: str) -> str:
    """Extract ISO-8601 expiry date from Finnish expiry clause text, or empty string.

    Finnish expiry clauses have the form:
        "Tämä laki on voimassa 31 päivään joulukuuta 2025."
        "on voimassa 31 päivänä joulukuuta 2025 asti"

    Both "päivään" (allative) and "päivänä" (essive) are matched — the
    essive form also appears in commencement clauses so the caller should
    only invoke this on EXPIRY-classified sentences.
    """
    m = _EXPIRY_DATE_PATTERN.search(text)
    if m is None:
        return ""
    # Group layout: (g1,g2,g3) for päivään form, (g4,g5,g6) for päivänä form
    if m.group(1):
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
    else:
        day_s, month_s, year_s = m.group(4), m.group(5), m.group(6)
    month = _MONTH_MAP.get(month_s.lower())
    if month is None:
        return ""
    try:
        import datetime as _dt
        d = _dt.date(int(year_s), month, int(day_s))
        return d.isoformat()
    except ValueError:
        return ""


def extract_expiry_date_from_meta_clauses(
    meta_clauses: Sequence[SurfaceMetaClause],
) -> str:
    """Extract an ISO-8601 expiry date from EXPIRY-classified meta clauses.

    Returns the first expiry date found, or empty string if none present.
    This extracts the "on voimassa ... päivään" date from expiry sentences
    so it can be attached to the live TemporalEvent emitted for a temporary op.
    """
    for clause in meta_clauses:
        if clause.kind != MetaClauseKind.EXPIRY:
            continue
        date_str = _extract_expiry_date_from_text(clause.text)
        if date_str:
            return date_str
    return ""


def activation_rules_from_meta_clauses(
    meta_clauses: Sequence[SurfaceMetaClause],
) -> list[ActivationRule]:
    """Extract ActivationRules from SurfaceMetaClause objects.

    Processes meta_clauses from ``ClauseParseResult.meta_clauses`` (which are
    ``SurfaceMetaClause`` instances with ``kind`` (MetaClauseKind) and ``text`` attributes).

    Only ``COMMENCEMENT`` meta_clauses produce activation rules:

    - If the text matches a decree-set / contingent pattern
      → ``ActivationRule(kind="pending_decree")``
    - If the text matches a simultaneous-entry pattern
      → ``ActivationRule(kind="pending_condition")``
    - If the text contains a recognizable Finnish date
      → ``ActivationRule(kind="fixed_date")``
    - Otherwise
      → ``ActivationRule(kind="immediate")``

    Other meta_clauses (expiry, transition, delegation, etc.) are
    silently skipped — they carry different temporal semantics not modeled
    by ActivationRule.

    Parameters
    ----------
    meta_clauses
        Sequence of SurfaceMetaClause objects (duck-typed on ``kind``
        (MetaClauseKind enum) and ``text`` attributes).

    Returns
    -------
    list[ActivationRule]
        One ActivationRule per commencement meta_clause.
    """
    return list(activation_rules_from_meta_clauses_with_findings(meta_clauses).activation_rules)


def activation_rules_from_meta_clauses_with_findings(
    meta_clauses: Sequence[SurfaceMetaClause],
) -> TemporalActivationLoweringResult:
    rules: list[ActivationRule] = []
    findings: list[Finding] = []
    for clause in meta_clauses:
        if clause.kind != MetaClauseKind.COMMENCEMENT:
            findings.append(
                _activation_rule_input_skipped(
                    lane="meta_clause",
                    input_kind=clause.kind.value,
                    detail={
                        "text": clause.text,
                        "has_witness": clause.witness is not None,
                    },
                )
            )
            continue
        text = clause.text

        if _DECREE_SET_PATTERN.search(text):
            rules.append(ActivationRule(
                kind="pending_decree",
                raw_text=text,
            ))
            continue

        if _SIMULTANEOUS_PATTERN.search(text):
            condition_ref = _extract_simultaneous_ref(text)
            rules.append(ActivationRule(
                kind="pending_condition",
                condition_ref=condition_ref,
                raw_text=text,
            ))
            continue

        date_str = _extract_date_from_text(text)
        if date_str:
            rules.append(ActivationRule(
                kind="fixed_date",
                effective_date=date_str,
                raw_text=text,
            ))
            continue

        rules.append(ActivationRule(
            kind="immediate",
            raw_text=text,
        ))

    return TemporalActivationLoweringResult(tuple(rules), tuple(findings))


# ---------------------------------------------------------------------------
# Default activation rule
# ---------------------------------------------------------------------------


def default_activation_rule() -> ActivationRule:
    """Return the Finnish default activation rule: immediate entry into force.

    In Finnish law, when no explicit commencement clause is present, the
    statute enters into force immediately upon publication.  This function
    returns the canonical ``ActivationRule(kind="immediate")`` for that case.
    """
    return ActivationRule(kind="immediate")


# ---------------------------------------------------------------------------
# Backward-compat bridge: classify_contingent
# ---------------------------------------------------------------------------


def classify_contingent(rule: ActivationRule) -> bool:
    """Bridge from typed ActivationRule to legacy ``is_contingent`` boolean.

    Returns ``True`` if the rule represents a contingent activation
    (``pending_decree`` or ``pending_condition``), ``False`` otherwise
    (``immediate`` or ``fixed_date``).

    This exists to keep the existing ``is_contingent`` flag working during
    the migration period.  Once all consumers read the typed ActivationRule
    directly, this function can be removed.
    """
    return rule.kind in ("pending_decree", "pending_condition")


__all__ = [
    "activation_rule_from_commencement",
    "activation_rule_from_temporal_event",
    "activation_rules_from_meta_clauses",
    "classify_contingent",
    "default_activation_rule",
    "extract_expiry_date_from_meta_clauses",
    "lower_commencement_intents",
    "lower_temporal_events_to_activation_rules",
]
