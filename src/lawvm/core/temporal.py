"""Typed temporal activation and resolution for legal commencement semantics.

This module introduces the minimal typed temporal activation model specified
in the Pro conditional-enactment architecture review.  The core idea: legal
effect time is rule-valued (not always a plain date), and resolution of
contingent rules is a separate fact layer.

Two primary types:

  ActivationRule  — what kind of commencement rule governs an effect
  ResolutionFact  — whether a contingent rule has been resolved by a later
                    legal instrument, remains unknown, or is certified
                    untriggered by source coverage.

And a derived status type:

  TemporalStatus  — the evaluated temporal status at a point in time

The derivation function ``derive_temporal_status`` computes the status from
the rule + optional resolution + an ``as_of`` date.  The projection function
``project_temporal_status`` handles multi-rule / multi-resolution sets.

These types live in core because they are cross-jurisdiction: any legal system
can have fixed-date, decree-set, or conditional commencement.  Jurisdiction-
specific lowering (e.g. entry-into-force patterns to ActivationRule) belongs
in the respective frontend module.

API tier
--------
Shared kernel.  Stable once landed.  Do not add jurisdiction-specific logic
here.

See also
--------
- ``notes/PRO_ON_CONDITIONAL_ENACTMENT_ETC.md`` — the architecture spec
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional

if TYPE_CHECKING:
    from lawvm.core.ir import LegalAddress, OperationSource


ActivationKind = Literal[
    "immediate",
    "fixed_date",
    "pending_decree",
    "pending_condition",
]

IMMEDIATE_KIND: ActivationKind = "immediate"
FIXED_DATE_KIND: ActivationKind = "fixed_date"
PENDING_DECREE_KIND: ActivationKind = "pending_decree"
PENDING_CONDITION_KIND: ActivationKind = "pending_condition"


@dataclass(frozen=True)
class ActivationRule:
    """Typed activation rule for a legal temporal effect."""

    kind: ActivationKind
    effective_date: str = ""
    condition_ref: str = ""
    raw_text: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {
            IMMEDIATE_KIND,
            FIXED_DATE_KIND,
            PENDING_DECREE_KIND,
            PENDING_CONDITION_KIND,
        }:
            raise ValueError(f"ActivationRule.kind must be one of {{'immediate', 'fixed_date', 'pending_decree', 'pending_condition'}}, got {self.kind!r}")
        if self.kind == FIXED_DATE_KIND and not self.effective_date:
            raise ValueError(
                "ActivationRule(kind='fixed_date') requires a non-empty effective_date"
            )
        if self.kind in (IMMEDIATE_KIND, FIXED_DATE_KIND) and self.condition_ref:
            raise ValueError(
                f"ActivationRule(kind={self.kind!r}) should not have a condition_ref"
            )


ResolutionStatus = Literal["resolved", "unresolved", "untriggered_certified", "superseded"]

RESOLVED_STATUS: ResolutionStatus = "resolved"
UNRESOLVED_STATUS: ResolutionStatus = "unresolved"
UNTRIGGERED_CERTIFIED_STATUS: ResolutionStatus = "untriggered_certified"
SUPERSEDED_STATUS: ResolutionStatus = "superseded"

TriggerCoverageStatus = Literal[
    "complete_no_resolution",
    "complete_with_resolution",
    "incomplete",
    "unknown",
]

TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION: TriggerCoverageStatus = "complete_no_resolution"
TRIGGER_COVERAGE_COMPLETE_WITH_RESOLUTION: TriggerCoverageStatus = "complete_with_resolution"
TRIGGER_COVERAGE_INCOMPLETE: TriggerCoverageStatus = "incomplete"
TRIGGER_COVERAGE_UNKNOWN: TriggerCoverageStatus = "unknown"

_TRIGGER_COVERAGE_STATUSES = frozenset({
    TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION,
    TRIGGER_COVERAGE_COMPLETE_WITH_RESOLUTION,
    TRIGGER_COVERAGE_INCOMPLETE,
    TRIGGER_COVERAGE_UNKNOWN,
})


@dataclass(frozen=True)
class TriggerCoverageCertificate:
    """Epistemic coverage record for contingent trigger source searches."""

    certificate_id: str
    status: TriggerCoverageStatus
    as_of: str = ""
    activation_rule_ref: str = ""
    source_scope: tuple[str, ...] = ()
    checked_sources: tuple[str, ...] = ()
    missing_sources: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.certificate_id:
            raise ValueError("TriggerCoverageCertificate.certificate_id must be non-empty")
        if self.status not in _TRIGGER_COVERAGE_STATUSES:
            raise ValueError(f"unsupported trigger coverage status: {self.status!r}")
        if self.status in {
            TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION,
            TRIGGER_COVERAGE_COMPLETE_WITH_RESOLUTION,
        }:
            if not self.as_of:
                raise ValueError(
                    f"TriggerCoverageCertificate(status={self.status!r}) requires as_of"
                )
            if not self.checked_sources:
                raise ValueError(
                    f"TriggerCoverageCertificate(status={self.status!r}) requires checked_sources"
                )
        if self.status == TRIGGER_COVERAGE_INCOMPLETE and not self.missing_sources:
            raise ValueError(
                "TriggerCoverageCertificate(status='incomplete') requires missing_sources"
            )

    @property
    def certifies_untriggered(self) -> bool:
        return self.status == TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate_id": self.certificate_id,
            "status": self.status,
            "as_of": self.as_of,
            "activation_rule_ref": self.activation_rule_ref,
            "source_scope": self.source_scope,
            "checked_sources": self.checked_sources,
            "missing_sources": self.missing_sources,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ResolutionFact:
    """Resolution state for a contingent activation rule.

    ``unresolved`` means source coverage does not establish the trigger state.
    ``untriggered_certified`` means checked source coverage establishes that
    the contingent trigger has not happened as of the relevant query horizon.
    """

    status: ResolutionStatus
    resolved_effective: str = ""
    authority_source: str = ""
    coverage_certificate_id: str = ""

    def __post_init__(self) -> None:
        if self.status == RESOLVED_STATUS and not self.resolved_effective:
            raise ValueError(
                "ResolutionFact(status='resolved') requires a non-empty resolved_effective"
            )
        if (
            self.status == UNTRIGGERED_CERTIFIED_STATUS
            and not self.authority_source
            and not self.coverage_certificate_id
        ):
            raise ValueError(
                "ResolutionFact(status='untriggered_certified') requires authority_source "
                "or coverage_certificate_id"
            )

    @property
    def is_resolved(self) -> bool:
        return self.status == RESOLVED_STATUS

    @property
    def is_unresolved(self) -> bool:
        return self.status == UNRESOLVED_STATUS

    @property
    def is_untriggered_certified(self) -> bool:
        return self.status == UNTRIGGERED_CERTIFIED_STATUS

    @property
    def is_superseded(self) -> bool:
        return self.status == SUPERSEDED_STATUS


# ---------------------------------------------------------------------------
# TemporalStatus — derived evaluation at a point in time
# ---------------------------------------------------------------------------

TemporalStatus = Literal[
    "active",
    "scheduled",
    "pending_external_resolution",
    "inactive",
]


# ---------------------------------------------------------------------------
# Derivation logic
# ---------------------------------------------------------------------------


def derive_temporal_status(
    rule: ActivationRule,
    resolution: ResolutionFact | None,
    as_of: str,
) -> TemporalStatus:
    """Derive the temporal status of an effect at ``as_of`` from its activation rule.

    Parameters
    ----------
    rule
        The activation rule governing this effect.
    resolution
        Optional resolution fact for contingent rules.  Ignored for
        ``"immediate"`` and ``"fixed_date"`` kinds.
    as_of
        ISO-8601 date string for the evaluation point.

    Returns
    -------
    TemporalStatus
        One of ``"active"``, ``"scheduled"``,
        ``"pending_external_resolution"``, ``"inactive"``.
    """
    if rule.kind == IMMEDIATE_KIND:
        return "active"

    if rule.kind == FIXED_DATE_KIND:
        # Compare lexicographically — ISO dates sort correctly
        if rule.effective_date <= as_of:
            return "active"
        return "scheduled"

    # Contingent kinds: pending_decree, pending_condition
    if resolution is None:
        return "pending_external_resolution"

    if resolution.is_superseded:
        return "inactive"

    if resolution.is_unresolved:
        return "pending_external_resolution"

    if resolution.is_untriggered_certified:
        return "inactive"

    # resolved
    if resolution.resolved_effective <= as_of:
        return "active"
    return "scheduled"


# ---------------------------------------------------------------------------
# Projection over multiple rules
# ---------------------------------------------------------------------------


def project_temporal_status(
    activation_rules: list[ActivationRule],
    resolution_facts: list[ResolutionFact],
    as_of: str,
) -> TemporalStatus:
    """Project the overall temporal status from multiple activation rules.

    When multiple rules exist (e.g. stacked amendments with different
    commencement conditions), the projection is conservative:

    - If any rule is ``"pending_external_resolution"``, the overall status
      is ``"pending_external_resolution"`` (uncertainty dominates).
    - Otherwise, if any rule is ``"active"``, the overall is ``"active"``.
    - Otherwise, if any rule is ``"scheduled"``, the overall is ``"scheduled"``.
    - Otherwise ``"inactive"``.

    The ``resolution_facts`` list is matched positionally to
    ``activation_rules``: ``resolution_facts[i]`` resolves
    ``activation_rules[i]``.  If the resolution list is shorter, missing
    entries are treated as ``None`` (no resolution).

    Parameters
    ----------
    activation_rules
        One or more activation rules for the effect(s).
    resolution_facts
        Resolution facts, positionally matched.  May be shorter than
        ``activation_rules``.
    as_of
        ISO-8601 date for evaluation.

    Returns
    -------
    TemporalStatus
        Conservative projection across all rules.
    """
    if not activation_rules:
        return "inactive"

    statuses: list[TemporalStatus] = []
    for i, rule in enumerate(activation_rules):
        res = resolution_facts[i] if i < len(resolution_facts) else None
        statuses.append(derive_temporal_status(rule, res, as_of))

    # Uncertainty dominates
    if "pending_external_resolution" in statuses:
        return "pending_external_resolution"
    if "active" in statuses:
        return "active"
    if "scheduled" in statuses:
        return "scheduled"
    return "inactive"


@dataclass(frozen=True)
class TemporalScope:
    """Operational scope for a temporal event.

    This is the adopted long-term temporal scope carrier. Some producer lanes
    may populate temporal fields at the boundary, but core does not treat
    them as a second authority surface.
    """

    target_statute: str = ""
    exact_addresses: tuple["LegalAddress", ...] = ()
    address_prefixes: tuple["LegalAddress", ...] = ()
    predicates: tuple[Any, ...] = ()
    include_future_descendants: bool = False


@dataclass(frozen=True)
class TemporalEvent:
    """Operational temporal carrier for executable timeline/PIT selection."""

    event_id: str
    kind: Literal["commence", "expire", "suspend", "revive", "set_applicability"]
    scope: TemporalScope
    effective: str = ""
    expires: str = ""
    source: Optional["OperationSource"] = None
    activation_rule: Optional[ActivationRule] = None
    group_id: Optional[str] = None
    derived_from_effect_intent: Optional[str] = None

    @property
    def has_activation_rule(self) -> bool:
        """True when this temporal event carries an embedded activation rule."""
        return self.activation_rule is not None

    @property
    def activation_rule_kind(self) -> str:
        """Return the embedded activation rule kind, or empty string if absent."""
        if self.activation_rule is None:
            return ""
        return self.activation_rule.kind


__all__ = [
    "ActivationKind",
    "ActivationRule",
    "IMMEDIATE_KIND",
    "FIXED_DATE_KIND",
    "PENDING_DECREE_KIND",
    "PENDING_CONDITION_KIND",
    "TriggerCoverageStatus",
    "TriggerCoverageCertificate",
    "TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION",
    "TRIGGER_COVERAGE_COMPLETE_WITH_RESOLUTION",
    "TRIGGER_COVERAGE_INCOMPLETE",
    "TRIGGER_COVERAGE_UNKNOWN",
    "ResolutionStatus",
    "ResolutionFact",
    "RESOLVED_STATUS",
    "UNRESOLVED_STATUS",
    "UNTRIGGERED_CERTIFIED_STATUS",
    "SUPERSEDED_STATUS",
    "TemporalStatus",
    "TemporalEvent",
    "TemporalScope",
    "derive_temporal_status",
    "project_temporal_status",
]
