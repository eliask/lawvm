"""Z3 proof harness for the occupancy state machine.

Encodes the transition table from lawvm.core.occupancy:
  States:  ABSENT=0, SUBSTANTIVE=1, TOMBSTONE=2
  Actions: INSERT=0, REPLACE=1, REPEAL=2, REENACT=3

Valid transitions (from occupancy.py VALID_TRANSITIONS):
  (insert,  ABSENT)      -> SUBSTANTIVE
  (insert,  TOMBSTONE)   -> SUBSTANTIVE  (reenactment via insert)
  (replace, SUBSTANTIVE) -> SUBSTANTIVE
  (repeal,  SUBSTANTIVE) -> TOMBSTONE

All other (action, state) pairs are INVALID (represented as state -1).

Proves six properties (P5-P10) about the state machine.
"""
from __future__ import annotations

from z3 import (
    And,
    If,
    Not,
    Or,
    Solver,
    unsat,
)

# State constants
ABSENT = 0
SUBSTANTIVE = 1
TOMBSTONE = 2
INVALID = -1

# Action constants
INSERT = 0
REPLACE = 1
REPEAL = 2
REENACT = 3

# All valid states and actions
ALL_STATES = [ABSENT, SUBSTANTIVE, TOMBSTONE]
ALL_ACTIONS = [INSERT, REPLACE, REPEAL, REENACT]


def _transition(state: int, action: int) -> int:
    """Python-level transition function matching VALID_TRANSITIONS."""
    table = {
        (INSERT, ABSENT): SUBSTANTIVE,
        (INSERT, TOMBSTONE): SUBSTANTIVE,  # reenactment via insert
        (REPLACE, SUBSTANTIVE): SUBSTANTIVE,
        (REPEAL, SUBSTANTIVE): TOMBSTONE,
        (REENACT, TOMBSTONE): SUBSTANTIVE,
    }
    return table.get((action, state), INVALID)


def _build_z3_transition():
    """Build Z3 function encoding the transition table."""
    # Encode as nested If chain
    def t(s, a):
        """Z3 expression for transition(s, a)."""
        return (
            If(And(a == INSERT, s == ABSENT), SUBSTANTIVE,
            If(And(a == INSERT, s == TOMBSTONE), SUBSTANTIVE,
            If(And(a == REPLACE, s == SUBSTANTIVE), SUBSTANTIVE,
            If(And(a == REPEAL, s == SUBSTANTIVE), TOMBSTONE,
            If(And(a == REENACT, s == TOMBSTONE), SUBSTANTIVE,
            INVALID)))))
        )

    return t


def prove_p5_insert_on_absent() -> bool:
    """P5: INSERT on ABSENT -> SUBSTANTIVE (and INSERT is only valid from ABSENT or TOMBSTONE)."""
    s = Solver()
    t = _build_z3_transition()

    # Part 1: INSERT on ABSENT gives SUBSTANTIVE
    s.push()
    s.add(t(ABSENT, INSERT) != SUBSTANTIVE)
    r1 = s.check()
    s.pop()

    if r1 != unsat:
        print(f"P5a COUNTEREXAMPLE: INSERT on ABSENT != SUBSTANTIVE: {s.model()}")
        raise AssertionError("P5a failed")

    # Part 2: INSERT on SUBSTANTIVE is INVALID
    s.push()
    s.add(t(SUBSTANTIVE, INSERT) != INVALID)
    r2 = s.check()
    s.pop()

    if r2 != unsat:
        print(f"P5b COUNTEREXAMPLE: INSERT on SUBSTANTIVE should be INVALID: {s.model()}")
        raise AssertionError("P5b failed")

    return True


def prove_p6_replace_only_from_substantive() -> bool:
    """P6: REPLACE is only valid from SUBSTANTIVE."""
    s = Solver()
    t = _build_z3_transition()

    # REPLACE on SUBSTANTIVE -> SUBSTANTIVE (valid)
    s.push()
    s.add(t(SUBSTANTIVE, REPLACE) != SUBSTANTIVE)
    r1 = s.check()
    s.pop()
    if r1 != unsat:
        raise AssertionError(f"P6a failed: REPLACE on SUBSTANTIVE: {s.model()}")

    # REPLACE on ABSENT -> INVALID
    s.push()
    s.add(t(ABSENT, REPLACE) != INVALID)
    r2 = s.check()
    s.pop()
    if r2 != unsat:
        raise AssertionError(f"P6b failed: REPLACE on ABSENT should be INVALID: {s.model()}")

    # REPLACE on TOMBSTONE -> INVALID
    s.push()
    s.add(t(TOMBSTONE, REPLACE) != INVALID)
    r3 = s.check()
    s.pop()
    if r3 != unsat:
        raise AssertionError(f"P6c failed: REPLACE on TOMBSTONE should be INVALID: {s.model()}")

    return True


def prove_p7_repeal_to_tombstone() -> bool:
    """P7: REPEAL on SUBSTANTIVE -> TOMBSTONE."""
    s = Solver()
    t = _build_z3_transition()

    s.add(t(SUBSTANTIVE, REPEAL) != TOMBSTONE)
    result = s.check()
    if result != unsat:
        raise AssertionError(f"P7 failed: REPEAL on SUBSTANTIVE != TOMBSTONE: {s.model()}")
    return True


def prove_p8_reenact_to_substantive() -> bool:
    """P8: REENACT on TOMBSTONE -> SUBSTANTIVE."""
    s = Solver()
    t = _build_z3_transition()

    s.add(t(TOMBSTONE, REENACT) != SUBSTANTIVE)
    result = s.check()
    if result != unsat:
        raise AssertionError(f"P8 failed: REENACT on TOMBSTONE != SUBSTANTIVE: {s.model()}")
    return True


def prove_p9_tombstone_never_becomes_absent() -> bool:
    """P9: No action from TOMBSTONE produces ABSENT (tombstones are permanent markers)."""
    s = Solver()
    t = _build_z3_transition()

    # For every action, transition(TOMBSTONE, action) != ABSENT
    # We enumerate all defined actions
    for action_val in ALL_ACTIONS:
        s.push()
        s.add(t(TOMBSTONE, action_val) == ABSENT)
        result = s.check()
        s.pop()
        if result != unsat:
            action_names = {INSERT: "INSERT", REPLACE: "REPLACE", REPEAL: "REPEAL", REENACT: "REENACT"}
            raise AssertionError(
                f"P9 failed: {action_names[action_val]} on TOMBSTONE produces ABSENT"
            )

    return True


def prove_p10_transition_total() -> bool:
    """P10: Every (state, action) pair has a defined outcome (SUBSTANTIVE, TOMBSTONE, or INVALID).

    The transition function is total — no pair is undefined.
    Every result is one of: SUBSTANTIVE, TOMBSTONE, ABSENT, or INVALID.
    (ABSENT is never a result state, but we check totality over the full domain.)
    """
    s = Solver()
    t = _build_z3_transition()

    valid_results = {ABSENT, SUBSTANTIVE, TOMBSTONE, INVALID}

    for state_val in ALL_STATES:
        for action_val in ALL_ACTIONS:
            result_expr = t(state_val, action_val)
            # Check that the result is one of the defined values
            s.push()
            s.add(Not(Or(*[result_expr == r for r in valid_results])))
            result = s.check()
            s.pop()
            if result != unsat:
                raise AssertionError(
                    f"P10 failed: transition({state_val}, {action_val}) produced undefined result"
                )

    # Also verify that every pair has a concrete (non-ambiguous) result
    # by checking that the Python and Z3 tables agree
    for state_val in ALL_STATES:
        for action_val in ALL_ACTIONS:
            py_result = _transition(state_val, action_val)
            s.push()
            s.add(t(state_val, action_val) != py_result)
            result = s.check()
            s.pop()
            if result != unsat:
                raise AssertionError(
                    f"P10 failed: Z3 and Python disagree on transition({state_val}, {action_val})"
                )

    return True


def prove_all() -> dict[str, bool]:
    """Run all occupancy state machine proofs. Returns {name: True} for each proved."""
    results = {}
    for name, fn in [
        ("P5_insert_on_absent", prove_p5_insert_on_absent),
        ("P6_replace_only_from_substantive", prove_p6_replace_only_from_substantive),
        ("P7_repeal_to_tombstone", prove_p7_repeal_to_tombstone),
        ("P8_reenact_to_substantive", prove_p8_reenact_to_substantive),
        ("P9_tombstone_never_becomes_absent", prove_p9_tombstone_never_becomes_absent),
        ("P10_transition_total", prove_p10_transition_total),
    ]:
        results[name] = fn()
    return results


if __name__ == "__main__":
    results = prove_all()
    for name, ok in results.items():
        status = "PROVED" if ok else "FAILED"
        print(f"  {name}: {status}")
