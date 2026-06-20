"""Static AST lint: a ``blocking = False`` downgrade must carry a witness.

This is the *syntactic* complement of the runtime witness-required-for-downgrade
invariant (:mod:`lawvm.core.downgrade_witness`). It catches the silent
suppression at the source level, before it ever runs: a function that flips a
diagnostic/rejection record to ``record["blocking"] = False`` (downgrading it
out of the blocking set) MUST, in the same function, also set a reclassification
witness — either ``record["nonblocking_reclassification_rule_id"]`` or
``record["reclassification_reason"]`` (in practice both).

Scope is deliberately TIGHT to keep the false-positive rate near zero (per the
project lint discipline — a noisy lint gets ``# noqa``'d into uselessness):

- it only inspects subscript assignments with the literal string key
  ``"blocking"`` set to the literal ``False`` (the exact downgrade move);
- it only requires a witness when such a downgrade is present;
- the witness may be any of the recognised reclassification-witness keys.

This shape is currently clean across ``src/lawvm/`` (every blocking-downgrade
site already records a witness), so the gate codifies the existing discipline
and prevents a future witnessless downgrade from landing silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"

#: Keys whose presence (set to a non-empty value) constitutes a recorded
#: downgrade witness. Any one present in the same function clears the lint.
_WITNESS_KEYS = frozenset(
    {
        "nonblocking_reclassification_rule_id",
        "reclassification_reason",
        "reclassification_rule_id",
    }
)

#: Precise file:line allowlist for confirmed sites that legitimately set
#: ``blocking=False`` without a sibling witness (e.g. a pure record-copy helper
#: that propagates an already-witnessed row). Empty today.
_ALLOWLIST: frozenset[str] = frozenset()


def _subscript_string_key(target: ast.expr) -> str | None:
    if (
        isinstance(target, ast.Subscript)
        and isinstance(target.slice, ast.Constant)
        and isinstance(target.slice.value, str)
    ):
        return target.slice.value
    return None


def _assigns_blocking_false(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    if not (isinstance(node.value, ast.Constant) and node.value.value is False):
        return False
    return any(_subscript_string_key(t) == "blocking" for t in node.targets)


def _assigns_witness_key(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    return any(_subscript_string_key(t) in _WITNESS_KEYS for t in node.targets)


def _scan() -> list[str]:
    violations: list[str] = []
    for pyfile in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            source = pyfile.read_text()
            if '"blocking"' not in source and "'blocking'" not in source:
                continue
            tree = ast.parse(source, filename=str(pyfile))
        except Exception:
            continue
        rel = pyfile.relative_to(_REPO_ROOT).as_posix()
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            downgrade_lines = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Assign) and _assigns_blocking_false(node)
            ]
            if not downgrade_lines:
                continue
            has_witness = any(_assigns_witness_key(stmt) for stmt in ast.walk(func))
            if has_witness:
                continue
            site = f"{rel}:{func.lineno}"
            if site in _ALLOWLIST:
                continue
            violations.append(
                f"{site}: function {func.name!r} sets `record['blocking'] = False` "
                f"(line {downgrade_lines[0]}) but records no reclassification witness "
                f"({sorted(_WITNESS_KEYS)}). A blocking-downgrade with no recorded "
                "reason is indistinguishable from a silent suppression of a real bug."
            )
    return violations


def test_no_witnessless_blocking_downgrade() -> None:
    violations = _scan()
    assert not violations, (
        "Witnessless blocking-downgrade(s) found (a `blocking = False` flip with no "
        "reclassification witness in the same function):\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Anti-vacuity: the gate must actually flag the banned pattern.
# ---------------------------------------------------------------------------


def test_gate_detects_synthetic_witnessless_downgrade() -> None:
    src = (
        "def downgrade(rec):\n"
        "    rec['blocking'] = False\n"
        "    return rec\n"
    )
    tree = ast.parse(src)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    has_downgrade = any(_assigns_blocking_false(s) for s in ast.walk(func))
    has_witness = any(_assigns_witness_key(s) for s in ast.walk(func))
    assert has_downgrade and not has_witness, "gate failed to flag a witnessless downgrade"


def test_gate_passes_a_witnessed_downgrade() -> None:
    src = (
        "def downgrade(rec):\n"
        "    rec['blocking'] = False\n"
        "    rec['reclassification_reason'] = 'out of replay scope'\n"
        "    return rec\n"
    )
    tree = ast.parse(src)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    has_downgrade = any(_assigns_blocking_false(s) for s in ast.walk(func))
    has_witness = any(_assigns_witness_key(s) for s in ast.walk(func))
    assert has_downgrade and has_witness, "gate must accept a witnessed downgrade"
