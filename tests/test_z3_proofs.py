"""Test harness for Z3 proof obligations.

Imports and runs all proof harnesses from the proofs/ directory.
Each proof returns True (UNSAT for negation = proved) or raises
with a counterexample.
"""
import sys
from pathlib import Path

# Ensure proofs/ is importable
_proofs_dir = Path(__file__).resolve().parent.parent / "proofs"
if str(_proofs_dir.parent) not in sys.path:
    sys.path.insert(0, str(_proofs_dir.parent))


def test_temporal_selector_proofs():
    from proofs.z3_temporal_selector import prove_all
    results = prove_all()
    for name, ok in results.items():
        assert ok, f"Z3 proof {name} failed"


def test_occupancy_proofs():
    from proofs.z3_occupancy import prove_all
    results = prove_all()
    for name, ok in results.items():
        assert ok, f"Z3 proof {name} failed"


def test_claim_precedence_proofs():
    from proofs.z3_claim_precedence import prove_all
    results = prove_all()
    for name, ok in results.items():
        assert ok, f"Z3 proof {name} failed"
