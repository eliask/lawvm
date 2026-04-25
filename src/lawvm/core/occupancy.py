"""Replay occupancy type definitions for addressable legal provisions.

Typed enumeration of slot states and operation transitions used by the
replay layer when it wants to *describe* what an operation expects or
produces. The `VALID_TRANSITIONS` table and `validate_transition` helper
are currently used **observationally only**: Finland's apply layer calls
them via `_observe_occupancy_transition` to emit debug/warning signals,
but no frontend currently *blocks* an operation on a bad occupancy
transition — operations proceed regardless and tree-level invariants
catch the fallout later.

So this module sits between "type definitions that a future execution
layer could consume" and "observational telemetry for the layer that
does exist." It is **not** currently the replay constitution; it is a
typed vocabulary waiting for a consumer that enforces it.

Current consumers:
- `src/lawvm/finland/apply_policy.py` — observational-only
- `src/lawvm/core/canonical_intent.py` — defines a richer
  `OccupancyPolicy` contract (with `primary_expected_from`,
  `allowed_from`, `result`) that is also not execution-enforced today

Other frontends (Estonia, UK) carry their own tombstone / repeal
handling independently of this module. Norway and Sweden do not model
slot occupancy at all.

Promotion to a true enforcement surface would mean: (a) every frontend
threads its replay through an occupancy-aware gate that can reject
invalid transitions, (b) the two-layer split between this module and
`canonical_intent.OccupancyPolicy` gets consolidated, and (c) the
cross-jurisdiction tombstone semantics are harmonized. None of those
are blockers for the current Finland frontend, and none are scheduled.

See `notes/OCCUPANCY_RESOLUTION_2026-04-15.md` for the investigation
that produced this downgrade.

API tier
--------
Typed vocabulary, observational use. Not execution-enforced today.
Frontends that need replay-time enforcement should layer their own
checks on top of tree_ops invariants; this module's types are safe to
consume as documentation or telemetry but are not a validity gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class OccupancyAction(Enum):
    """Actions that cause occupancy transitions.

    These are the operations that change the occupancy state of a slot.
    Each member's .value matches the string used in VALID_TRANSITIONS.
    """

    REPLACE = "replace"
    INSERT = "insert"
    REPEAL = "repeal"
    REENACT = "reenact"

    def __str__(self) -> str:
        return self.value


class OccupancyClass(Enum):
    """What kind of content occupies an addressable slot."""

    ABSENT = "absent"  # never existed or not yet created
    SUBSTANTIVE = "substantive"  # live content
    TOMBSTONE = "tombstone"  # repealed, preserves addressability
    SCAFFOLD = "scaffold"  # temporary placeholder for ordering


@dataclass(frozen=True)
class SlotIdentity:
    """Exact identity of an addressable slot in a statute.

    Replay identity is:
    - exact parent path
    - exact kind (section, subsection, item)
    - exact normalized label

    Separate from identity:
    - ordered sibling family (14a, 14b, 14c)
    - stem family (14, 14a)
    - presentation range family (14a-14c) — rendering only
    """

    parent_path: Tuple[str, ...]
    kind: str
    label: str


@dataclass(frozen=True)
class SlotState:
    """Current state of an addressable slot."""

    identity: SlotIdentity
    occupancy: OccupancyClass
    last_modified_by: Optional[str] = None  # amendment ID
    tombstone_text: Optional[str] = None  # e.g. "82 a § on kumottu L:lla 13.11.2020/766"


@dataclass(frozen=True)
class OccupancyTransition:
    """A valid occupancy transition caused by an operation.

    Not all transitions are valid:
    - REPLACE: SUBSTANTIVE -> SUBSTANTIVE (content update)
    - INSERT: ABSENT -> SUBSTANTIVE (new content)
    - REPEAL: SUBSTANTIVE -> TOMBSTONE (preserves address)
    - REENACT: TOMBSTONE -> SUBSTANTIVE (reinstatement)
    """

    from_state: OccupancyClass
    to_state: OccupancyClass
    action: OccupancyAction


# Valid transitions: (action, from_occupancy) -> to_occupancy
VALID_TRANSITIONS: dict[tuple[OccupancyAction, OccupancyClass], OccupancyClass] = {
    (OccupancyAction.REPLACE, OccupancyClass.SUBSTANTIVE): OccupancyClass.SUBSTANTIVE,
    (OccupancyAction.INSERT, OccupancyClass.ABSENT): OccupancyClass.SUBSTANTIVE,
    (OccupancyAction.INSERT, OccupancyClass.TOMBSTONE): OccupancyClass.SUBSTANTIVE,  # reenactment
    (OccupancyAction.INSERT, OccupancyClass.SCAFFOLD): OccupancyClass.SUBSTANTIVE,  # scaffold cleanup
    (OccupancyAction.REPEAL, OccupancyClass.SUBSTANTIVE): OccupancyClass.TOMBSTONE,
    (OccupancyAction.REPEAL, OccupancyClass.TOMBSTONE): OccupancyClass.TOMBSTONE,  # idempotent repeal
}


class InvalidOccupancyTransition(ValueError):
    """Raised when an operation is attempted on a slot with incompatible occupancy."""

    pass


def validate_transition(action: OccupancyAction, current: OccupancyClass) -> OccupancyClass:
    """Check if a proposed operation is valid given current occupancy.

    Args:
        action: The operation being applied — one of OccupancyAction members.
        current: The current occupancy class of the target slot.

    Returns:
        The resulting OccupancyClass if the transition is valid.

    Raises:
        InvalidOccupancyTransition: If the (action, current) pair is not permitted.
    """
    key = (action, current)
    if key not in VALID_TRANSITIONS:
        raise InvalidOccupancyTransition(
            f"Action '{action.value}' is not valid on a slot with occupancy {current.value!r}. "
            f"Valid transitions for '{action.value}': "
            + ", ".join(
                f"{from_occ.value} -> {to_occ.value}"
                for (act, from_occ), to_occ in VALID_TRANSITIONS.items()
                if act == action
            )
        )
    return VALID_TRANSITIONS[key]
