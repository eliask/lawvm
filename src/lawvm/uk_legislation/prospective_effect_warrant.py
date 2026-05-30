"""Source-faithfulness sensor for UK prospective (uncommenced) effects.

A structural effect that the official feed dates only *prospectively* has not been
brought into force. Applying it to the current consolidation mutates legal state
with a change the source has not yet commenced — the over-application risk that
OPC Drafting Guidance Part 6.8 is about (uncommenced material must be kept in a
state in which it can be brought into force, and amendments should not take effect
before the provision they amend).

Whether the current consolidation already reflects a prospective change is, in
practice, point-in-time and editorial dependent: an empirical blanket "do not
apply prospective" gate moves replay similarity in *both* directions across the
corpus (verified mixed-sign), so this is a manual-compilation-frontier class, not
a deterministic gate. This module is the *sensor* phase: it emits an owned,
non-blocking observation per applied prospective-only structural effect, so the
population is visible and countable. A later PIT-aware resolver can decide
application per the oracle version being compared; at that point the application
of a prospective effect becomes an owned claim rather than a silent default.
"""
from __future__ import annotations

from typing import Any, Optional

from lawvm.core.diagnostic_records import diagnostic_detail

PROSPECTIVE_EFFECT_APPLIED_RULE_ID = "uk_prospective_effect_applied_to_current"


def prospective_effect_applied_observation(effect: Any) -> Optional[dict[str, Any]]:
    """Return a non-blocking observation for an applied prospective-only effect.

    Returns ``None`` when the effect is not a prospective-only structural effect,
    so this check does not apply.
    """
    if not getattr(effect, "is_prospective_only", False):
        return None
    if not getattr(effect, "is_structural", False):
        return None
    affected = getattr(effect, "affected_provisions", None)
    return diagnostic_detail(
        rule_id=PROSPECTIVE_EFFECT_APPLIED_RULE_ID,
        family="temporal_applicability",
        phase="lowering",
        blocking=False,
        reason=(
            "A structural effect whose only in-force dates are prospective is "
            "applied to the current consolidation, so replay may over-apply a "
            "change the source has not yet brought into force (OPC Drafting "
            "Guidance Part 6.8). Whether the current consolidation reflects it is "
            "point-in-time dependent, so the application is surfaced as a "
            "manual-frontier claim rather than silently trusted."
        ),
        detail={
            "effect_type": str(getattr(effect, "effect_type", "")),
            "affected_provisions": list(affected) if isinstance(affected, (list, tuple)) else str(affected or ""),
            "affecting_act_id": str(getattr(effect, "affecting_act_id", "")),
            "in_force_dates": list(getattr(effect, "in_force_dates", []) or []),
        },
    )


def collect_prospective_effect_observations(effects: Any) -> list[dict[str, Any]]:
    """Emit a non-blocking observation per applied prospective-only structural effect."""
    observations: list[dict[str, Any]] = []
    for effect in effects:
        observation = prospective_effect_applied_observation(effect)
        if observation is not None:
            observations.append(observation)
    return observations
