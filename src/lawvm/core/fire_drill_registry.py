"""Guard-liveness state classifier over hard/strict finding codes.

A *guard* is a finding code registered with blocking enforcement that is
supposed to fire from the production pipeline when its guarded state occurs.
The recurring bug shape (guard-liveness failure) is a guard that exists, is
registered, and passes isolated unit tests, but is *structurally unsatisfiable
from production* — no live production call site can put the guard into its
firing state, so it gives false assurance.

This module is the small typed core of the "guard-liveness coverage gate". It
classifies each hard/strict finding code into exactly one of three states:

* ``live_drill`` — a production fire-drill exists that drives a real production
  builder/pipeline into the guarded state and asserts the finding reaches its
  consumer-visible surface. The set of drilled codes lives with the drills
  themselves (``tests/test_fi_guard_liveness.py``) and is passed in.
* ``recorded_dead`` — the guard's emit site has NO production call site today;
  it is reachable only from tests/drills. The deadness is OWNED here (named
  reason) rather than left silent. ``recorded_dead`` is an owned deadness, NOT
  a fix and NOT a deletion: the gate function is real, but nothing in the
  production execution path reaches it yet.
* ``no_drill_yet`` — declared, consciously-maintained debt: a blocking code
  that is genuinely drillable from production but does not yet have a drill
  (carries an owner/reason + last-reviewed date in the allowlist).

HONESTY BOUNDARY (constructive-invariant pattern)
-------------------------------------------------
* This classifier ranges over ``{hard_fail, strict_fail}`` finding codes ONLY.
  Codes that block solely via registry role (violation/obligation) without
  hard/strict enforcement are out of range — the wider blocking-partition
  ratchet in ``tests/test_fi_guard_liveness.py`` owns those.
* ``live_drill`` proves the guard FIRES from production. It does NOT prove the
  guard is semantically correct, nor that its threshold/disposition is right.
* ``recorded_dead`` is an OWNED deadness, not a fix. It asserts only that no
  production call site reaches the emit site today, with a named reason.
* ``no_drill_yet`` is declared debt with an owner; it is not a claim that the
  guard cannot fire.
* This module never asserts "all guards fire". The whole point is to make the
  three states exhaustive and mutually exclusive so that no hard/strict guard
  is silently un-accounted.

API tier
--------
Stable typed classifier surface. The state data (drilled set, allowlist) is
owned by the guard-liveness drill suite and injected; only the
``RECORDED_DEAD`` owned-deadness registry and the partition logic live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Mapping

from lawvm.core.observation_registry import FINDING_REGISTRY, FindingSpec


GuardLivenessState = Literal["live_drill", "recorded_dead", "no_drill_yet"]


# ---------------------------------------------------------------------------
# Owned deadness registry (recorded_dead)
# ---------------------------------------------------------------------------

# Hard/strict finding codes whose registered emit site has NO production call
# site today: the gate function is real, but nothing in the production apply /
# replay / compile execution path invokes it — it is reachable only from tests
# and guard-liveness drills. The deadness is OWNED here (named owner + reason)
# instead of being silently treated as a live guard. The day a production call
# site is wired, the consistency test fails (the code becomes reachable) and the
# entry must move to a live_drill.
#
# Each entry is (owner, reason). ``owner`` names the module that would have to
# gain the production call site to make the guard live.
RECORDED_DEAD: Dict[str, tuple[str, str]] = {
    # Promotion-chain integrity gates (CHAIN-/PROMOTE- families). The gate
    # functions in finland/apply_promotion_chain.py are real strict-blocking
    # checks over the §0 promotion chain, but the module is, by its own design
    # note, "NOT wired into the production apply path (sibling sessions own it);
    # they gate from tests + drills". grep confirms gate_authorization_scope_match
    # / gate_promotion_chain_links / gate_downchain_retraction have NO caller
    # outside apply_promotion_chain.py and the guard-liveness drills, so no
    # production execution reaches the emit site. Owned-dead until the apply
    # mutation path threads these gates.
    "PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH": (
        "finland.apply_promotion_chain",
        "gate_authorization_scope_match has no production call site (only drills "
        "drive it); the apply mutation path does not thread promotion-chain gates",
    ),
    "CHAIN.PROMOTION_CHAIN_INCOMPLETE": (
        "finland.apply_promotion_chain",
        "gate_promotion_chain_links has no production call site (only drills drive "
        "it); the apply mutation path does not thread promotion-chain gates",
    ),
    "CHAIN.AUTHORITY_BY_ACCUMULATION": (
        "finland.apply_promotion_chain",
        "gate_promotion_chain_links (CHAIN-02 arm) has no production call site "
        "(only drills drive it); the apply mutation path does not thread "
        "promotion-chain gates",
    ),
    "PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION": (
        "finland.apply_promotion_chain",
        "gate_downchain_retraction has no production call site (only drills drive "
        "it); the apply mutation path does not thread promotion-chain gates",
    ),
}


@dataclass(frozen=True, slots=True)
class GuardLivenessClassification:
    """The exhaustive, mutually-exclusive three-state partition over hard/strict codes."""

    live_drill: frozenset[str]
    recorded_dead: frozenset[str]
    no_drill_yet: frozenset[str]
    # Hard/strict codes that landed in NONE of the three states (a silent gap):
    # the gate exists with no account. Must always be empty for a consistent gate.
    unaccounted: frozenset[str]
    # Hard/strict codes that landed in MORE than one state (overlap): the states
    # must be mutually exclusive. Must always be empty for a consistent gate.
    overlapping: frozenset[str]

    def is_consistent(self) -> bool:
        return not self.unaccounted and not self.overlapping


def hard_or_strict_codes(
    registry: Mapping[str, FindingSpec] = FINDING_REGISTRY,
) -> frozenset[str]:
    """Return the in-range finding codes: default_enforcement in {hard_fail, strict_fail}."""
    return frozenset(
        code
        for code, spec in registry.items()
        if spec.default_enforcement in ("hard_fail", "strict_fail")
    )


def classify_guard_liveness(
    *,
    live_drill_codes: frozenset[str] | set[str],
    no_drill_yet_codes: frozenset[str] | set[str],
    recorded_dead_codes: frozenset[str] | set[str] = frozenset(RECORDED_DEAD),
    registry: Mapping[str, FindingSpec] = FINDING_REGISTRY,
) -> GuardLivenessClassification:
    """Partition every hard/strict finding code into the three guard-liveness states.

    The drilled set and the debt allowlist are owned by the guard-liveness drill
    suite and injected here; ``recorded_dead_codes`` defaults to the owned
    deadness registry in this module. The classification restricts every input
    set to the in-range (hard/strict) codes, then computes which codes are
    unaccounted (in zero states) or overlapping (in more than one state). A
    consistent gate has both empty.
    """
    in_range = hard_or_strict_codes(registry)
    live = frozenset(live_drill_codes) & in_range
    dead = frozenset(recorded_dead_codes) & in_range
    debt = frozenset(no_drill_yet_codes) & in_range

    accounted = live | dead | debt
    unaccounted = in_range - accounted

    overlapping = frozenset(
        code
        for code in in_range
        if (int(code in live) + int(code in dead) + int(code in debt)) > 1
    )
    return GuardLivenessClassification(
        live_drill=live,
        recorded_dead=dead,
        no_drill_yet=debt,
        unaccounted=unaccounted,
        overlapping=overlapping,
    )
