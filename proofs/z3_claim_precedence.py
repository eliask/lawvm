"""Z3 proof harness for evidence claim tier precedence.

Encodes the tier ordering from lawvm.tools._evidence_helpers._PRIMARY_TIER_ORDER:
  PROVED_HTML_XML_NONCOMMENSURABLE = 0  (strongest — most exculpating)
  PROVED_SOURCE_PATHOLOGY           = 1
  PROVED_ORACLE_INCORRECT           = 2
  PROVED_REPLAY_BUG                 = 3
  UNRESOLVED                        = 4  (weakest)

Precedence rule: lower tier number = higher precedence (wins selection).
This matches _PRIMARY_TIER_ORDER list order where index 0 is the strongest
non-replay explanation and the last entry is weakest.

Proves four properties (P11-P14) about the precedence relation.
"""
from __future__ import annotations

from z3 import (
    And,
    Consts,
    If,
    IntSort,
    Not,
    Solver,
    unsat,
)

# Tier encoding matching _PRIMARY_TIER_ORDER list indices
PROVED_HTML_XML_NONCOMMENSURABLE = 0
PROVED_SOURCE_PATHOLOGY = 1
PROVED_ORACLE_INCORRECT = 2
PROVED_REPLAY_BUG = 3
UNRESOLVED = 4

ALL_TIERS = [
    PROVED_HTML_XML_NONCOMMENSURABLE,
    PROVED_SOURCE_PATHOLOGY,
    PROVED_ORACLE_INCORRECT,
    PROVED_REPLAY_BUG,
    UNRESOLVED,
]

MIN_TIER = min(ALL_TIERS)
MAX_TIER = max(ALL_TIERS)


def _is_valid_tier(x):
    """Z3 constraint: x is a valid tier value."""
    return And(x >= MIN_TIER, x <= MAX_TIER)


def _precedes(a, b):
    """Z3 expression: tier a strictly precedes (is stronger than) tier b.

    Lower number = stronger precedence.
    """
    return a < b


def _precedes_or_eq(a, b):
    """Z3 expression: tier a precedes or equals tier b."""
    return a <= b


def prove_p11_antisymmetric() -> bool:
    """P11: Precedence is antisymmetric: if A > B then not B > A.

    Using strict precedence (lower number wins): if A < B, then not B < A.
    This is a property of the integer < relation.
    """
    s = Solver()
    a, b = Consts("a b", IntSort())

    s.add(_is_valid_tier(a))
    s.add(_is_valid_tier(b))

    # Try to find: a < b AND b < a (should be UNSAT)
    s.add(_precedes(a, b))
    s.add(_precedes(b, a))

    result = s.check()
    if result == unsat:
        return True
    else:
        print(f"P11 COUNTEREXAMPLE: {s.model()}")
        raise AssertionError(f"P11 failed: found symmetric precedence: {s.model()}")


def prove_p12_total() -> bool:
    """P12: Precedence is total: for any two tiers, one is >= the other.

    For integers: for all a, b: a <= b OR b <= a.
    """
    s = Solver()
    a, b = Consts("a b", IntSort())

    s.add(_is_valid_tier(a))
    s.add(_is_valid_tier(b))

    # Try to find: NOT(a <= b) AND NOT(b <= a) (should be UNSAT)
    s.add(Not(_precedes_or_eq(a, b)))
    s.add(Not(_precedes_or_eq(b, a)))

    result = s.check()
    if result == unsat:
        return True
    else:
        print(f"P12 COUNTEREXAMPLE: {s.model()}")
        raise AssertionError(f"P12 failed: found incomparable tiers: {s.model()}")


def prove_p13_transitive() -> bool:
    """P13: Precedence is transitive: if A > B and B > C then A > C.

    For strict < on integers: if A < B and B < C then A < C.
    """
    s = Solver()
    a, b, c = Consts("a b c", IntSort())

    s.add(_is_valid_tier(a))
    s.add(_is_valid_tier(b))
    s.add(_is_valid_tier(c))

    # Assume A < B and B < C, try to find NOT(A < C) (should be UNSAT)
    s.add(_precedes(a, b))
    s.add(_precedes(b, c))
    s.add(Not(_precedes(a, c)))

    result = s.check()
    if result == unsat:
        return True
    else:
        print(f"P13 COUNTEREXAMPLE: {s.model()}")
        raise AssertionError(f"P13 failed: transitivity violated: {s.model()}")


def prove_p14_monotonicity() -> bool:
    """P14: Adding a candidate with a strictly stronger tier always changes the selection.

    Model: current best tier is `current`. New candidate has tier `candidate`.
    Selection rule: winner = min(current, candidate).
    If candidate < current (strictly stronger), then winner != current.

    This is the monotonicity property: a stronger claim always displaces
    the current selection.
    """
    s = Solver()
    current, candidate = Consts("current candidate", IntSort())

    s.add(_is_valid_tier(current))
    s.add(_is_valid_tier(candidate))

    # Candidate is strictly stronger
    s.add(_precedes(candidate, current))

    # Selection: winner = min(current, candidate) = If(candidate < current, candidate, current)
    winner = If(candidate < current, candidate, current)

    # Try to find: winner == current (should be UNSAT when candidate < current)
    s.add(winner == current)

    result = s.check()
    if result == unsat:
        return True
    else:
        print(f"P14 COUNTEREXAMPLE: {s.model()}")
        raise AssertionError(f"P14 failed: stronger tier did not change selection: {s.model()}")


def prove_all() -> dict[str, bool]:
    """Run all claim precedence proofs. Returns {name: True} for each proved."""
    results = {}
    for name, fn in [
        ("P11_antisymmetric", prove_p11_antisymmetric),
        ("P12_total", prove_p12_total),
        ("P13_transitive", prove_p13_transitive),
        ("P14_monotonicity", prove_p14_monotonicity),
    ]:
        results[name] = fn()
    return results


if __name__ == "__main__":
    results = prove_all()
    for name, ok in results.items():
        status = "PROVED" if ok else "FAILED"
        print(f"  {name}: {status}")
