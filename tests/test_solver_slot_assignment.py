"""Tests for the CP-SAT solver slot assignment pilot.

Covers:
  1. Single slot, single live -> unique
  2. Two slots matching two lives exactly -> unique
  3. Two slots, same label -> ambiguous
  4. No compatible assignment -> infeasible
  5. Monotone order preference works
  6. Normalized label matching
  7. diagnose_assignment detects heuristic disagreement
  8. Empty inputs -> trivial unique
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.tools.solver_slot_assignment import (
    diagnose_assignment,
    solve_slot_assignment,
)


# ---------------------------------------------------------------------------
# 1. Single slot, single live -> unique
# ---------------------------------------------------------------------------

def test_single_slot_single_live_unique():
    witness = solve_slot_assignment(["2"], ["2"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 0}
    assert witness.payload_slot_count == 1
    assert witness.live_slot_count == 1
    assert witness.solver == "cp_sat"
    assert witness.problem_kind == "slot_assignment"
    assert witness.solve_time_ms >= 0


# ---------------------------------------------------------------------------
# 2. Two slots matching two lives exactly -> unique
# ---------------------------------------------------------------------------

def test_two_slots_exact_match_unique():
    witness = solve_slot_assignment(["1", "3"], ["1", "2", "3"])
    assert witness.status == "unique"
    assert witness.selected_assignment is not None
    # payload 0 ("1") -> live 0 ("1"), payload 1 ("3") -> live 2 ("3")
    assert witness.selected_assignment[0] == 0
    assert witness.selected_assignment[1] == 2


# ---------------------------------------------------------------------------
# 3. Two slots, same label -> ambiguous
# ---------------------------------------------------------------------------

def test_two_identical_labels_ambiguous():
    """Two payload slots both labeled "2", three live slots also "2".

    Three live slots labeled "2": any 2-of-3 assignment to payload is valid.
    With monotone preference, {0:0, 1:1}, {0:0, 1:2}, {0:1, 1:2} are
    all monotone. Exact-match scores are equal, so multiple optimal
    solutions exist -> ambiguous.
    """
    witness = solve_slot_assignment(["2", "2"], ["2", "2", "2"])
    assert witness.status == "ambiguous"
    assert witness.alternative_model_count >= 2
    assert witness.selected_assignment is not None


def test_two_identical_labels_with_monotone_tiebreak():
    """Two payload slots labeled "2", two live slots labeled "2".

    Both hard-feasible assignments exist (swap), but monotone preference
    (weight 3) makes {0:0, 1:1} uniquely optimal.
    """
    witness = solve_slot_assignment(["2", "2"], ["2", "2"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 0, 1: 1}


# ---------------------------------------------------------------------------
# 4. No compatible assignment -> infeasible
# ---------------------------------------------------------------------------

def test_incompatible_labels_infeasible():
    """Payload label "5" cannot match any live label ["1", "2"]."""
    witness = solve_slot_assignment(["5"], ["1", "2"])
    assert witness.status == "infeasible"
    assert witness.selected_assignment is None


def test_more_payload_than_live_infeasible():
    """3 payload slots but only 1 compatible live slot -> infeasible."""
    witness = solve_slot_assignment(["1", "2", "3"], ["1"])
    assert witness.status == "infeasible"
    assert witness.selected_assignment is None


# ---------------------------------------------------------------------------
# 5. Monotone order preference works
# ---------------------------------------------------------------------------

def test_monotone_order_preference():
    """Given ambiguous labels, solver prefers monotone order.

    Payload: ["1", "2"], Live: ["1", "2", "3"].
    The exact-match assignment is {0: 0, 1: 1}, which is also monotone.
    """
    witness = solve_slot_assignment(["1", "2"], ["1", "2", "3"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 0, 1: 1}


def test_monotone_preference_with_positional():
    """Two unlabeled payload slots into two unlabeled live slots.

    Both empty-empty, so compatible with any pair. Monotone preference
    should pick 0->0, 1->1 over 0->1, 1->0.
    """
    witness = solve_slot_assignment(["", ""], ["", ""])
    assert witness.selected_assignment is not None
    # With monotone preference (weight 3), the solver prefers
    # 0->0, 1->1 because that gives monotone bonus.
    # But both solutions have same "exact match" and "norm match" scores,
    # so monotone is the tiebreaker.
    assert witness.selected_assignment[0] == 0
    assert witness.selected_assignment[1] == 1
    # However, the reversed assignment is also optimal if monotone
    # weight doesn't dominate. It IS ambiguous since both-empty match
    # doesn't care about order at the hard constraint level.
    # The status depends on whether monotone bonus creates a unique optimum.
    # 0->0, 1->1 gets mono bonus of 3. 0->1, 1->0 gets 0.
    # So the unique optimal is 0->0, 1->1.
    assert witness.status == "unique"


# ---------------------------------------------------------------------------
# 6. Normalized label matching
# ---------------------------------------------------------------------------

def test_normalized_label_match():
    """Labels differ in surface form but match after normalization.

    "2 §." normalizes to "2", matching live "2".
    """
    witness = solve_slot_assignment(["2 §."], ["1", "2", "3"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 1}


def test_custom_normalizer():
    """A custom normalizer enables matching."""

    def strip_prefix(label: str) -> str:
        return label.removeprefix("mom_").strip()

    witness = solve_slot_assignment(
        ["mom_1", "mom_3"],
        ["1", "2", "3"],
        label_normalizer=strip_prefix,
    )
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 0, 1: 2}


# ---------------------------------------------------------------------------
# 7. diagnose_assignment detects heuristic disagreement
# ---------------------------------------------------------------------------

def test_diagnose_heuristic_matches():
    """Heuristic agrees with solver -> no disagreement."""
    result = diagnose_assignment(
        ["1", "3"],
        ["1", "2", "3"],
        heuristic_assignment={0: 0, 1: 2},
    )
    assert result["heuristic_matches_solver"] is True
    assert result["disagreement_slots"] == []
    assert result["solver_status"] == "unique"


def test_diagnose_heuristic_disagrees():
    """Heuristic assigns differently from solver -> disagreement detected."""
    result = diagnose_assignment(
        ["1", "3"],
        ["1", "2", "3"],
        heuristic_assignment={0: 0, 1: 1},  # wrong: "3" mapped to live[1]="2"
    )
    assert result["heuristic_matches_solver"] is False
    assert 1 in result["disagreement_slots"]


def test_diagnose_infeasible():
    """Solver finds infeasible, heuristic has an assignment -> disagrees."""
    result = diagnose_assignment(
        ["5"],
        ["1", "2"],
        heuristic_assignment={0: 0},
    )
    assert result["solver_status"] == "infeasible"
    assert result["heuristic_matches_solver"] is False


# ---------------------------------------------------------------------------
# 8. Empty inputs -> trivial unique
# ---------------------------------------------------------------------------

def test_empty_payload_trivial_unique():
    witness = solve_slot_assignment([], ["1", "2", "3"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {}
    assert witness.payload_slot_count == 0


def test_empty_both_trivial_unique():
    witness = solve_slot_assignment([], [])
    assert witness.status == "unique"
    assert witness.selected_assignment == {}


def test_empty_live_with_payload_infeasible():
    """Payload slots exist but no live slots to assign to."""
    witness = solve_slot_assignment(["1"], [])
    assert witness.status == "infeasible"


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

def test_witness_is_frozen():
    """SlotAssignmentWitness is immutable."""
    witness = solve_slot_assignment(["1"], ["1"])
    with pytest.raises(AttributeError):
        cast(Any, witness).status = "ambiguous"


def test_three_payload_three_live_distinct():
    """Three distinct labels map uniquely to three lives."""
    witness = solve_slot_assignment(["1", "2", "3"], ["1", "2", "3"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 0, 1: 1, 2: 2}


def test_sparse_assignment_skips_live_slots():
    """Two payload slots into five live slots, labels match non-adjacent."""
    witness = solve_slot_assignment(["2", "4"], ["1", "2", "3", "4", "5"])
    assert witness.status == "unique"
    assert witness.selected_assignment == {0: 1, 1: 3}


def test_solve_time_is_reasonable():
    """Solve time for small problems should be under 1 second."""
    witness = solve_slot_assignment(
        ["1", "2", "3", "4", "5"],
        ["1", "2", "3", "4", "5", "6", "7"],
    )
    assert witness.solve_time_ms < 1000
    assert witness.status == "unique"
