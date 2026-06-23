"""Monotone raw-string scope-source ratchet gate (Audit D).

A Finland chapter-scope decision must compare a scope witness ``.source`` against
a ``ScopeResolutionSource`` enum member, never a raw string literal. A raw-string
compare is "authority bleed": the typed source rail leaks back into stringly-typed
control flow.

How it works:
  - An AST scan over ``src/lawvm/finland/**`` finds ``Compare`` nodes where one
    operand is a scope-related ``.source`` attribute access and the other is a str
    literal (or a set/tuple/list of str literals), plus
    ``group_has_scope_source(..., "<literal>")`` raw-string helper calls.
  - The committed baseline (``tests/data/scope_source_ratchet_baseline.json``)
    records the per-file count. It is 0 in the already-migrated comparison files
    (apply_structure_ops / frontend_compile / scope / standalone_targets) and N
    (the residue) in the scope-source PRODUCER files. This test FAILS if any
    file's count INCREASES (a reintroduced raw-string compare) and also FAILS —
    with an instruction to commit the lowered baseline — if any count DROPS (a
    producer migrated to the enum).
  - The four comparison files are additionally PINNED at 0: any raw-string
    scope-source compare reappearing there fails CI regardless of the baseline.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_architecture_smells.py"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_architecture_smells_scope", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.SCOPE_SOURCE_BASELINE_PATH
    assert path.exists(), (
        f"Missing scope-source ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_architecture_smells.py --ratchet scope "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestScopeSourceRatchet:
    def test_no_new_raw_string_scope_source_compare(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_scope_source_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["raw_string_counts"]
        current_counts: dict[str, int] = state["raw_string_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} raw-string scope-source compares "
                    f"(baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[SCOPE SOURCE RATCHET] NEW raw-string scope-source compare(s) "
                "added:\n"
                + "\n".join(increases)
                + "\n\nCompare a scope witness `.source` against a "
                "ScopeResolutionSource enum member, not a string literal:\n"
                "  witness.source is ScopeResolutionSource.CARRY_FORWARD\n"
                "  witness.source in {ScopeResolutionSource.PREAMBLE, ...}\n"
                "See src/lawvm/finland/ops.py (ScopeResolutionSource) and "
                "notes_internal/STAGERESULT_ENDGAME.md (Audit D)."
            )

    def test_comparison_files_are_pinned_at_zero(self) -> None:
        """The migrated comparison files must NEVER carry a raw-string scope-source
        compare, regardless of the baseline."""
        state = _INV.scan_scope_source_ratchet(_REPO_ROOT)
        counts: dict[str, int] = state["raw_string_counts"]
        offenders: list[str] = []
        for rel in _INV.SCOPE_SOURCE_COMPARISON_FILES:
            count = counts.get(rel, 0)
            if count != 0:
                offenders.append(f"  {rel}: {count} (must be 0)")
        if offenders:
            sites = [
                f"    {r['file']}:{r['line']} ({r['kind']})"
                for r in state["records"]
                if r["file"] in set(_INV.SCOPE_SOURCE_COMPARISON_FILES)
            ]
            pytest.fail(
                "\n[SCOPE SOURCE RATCHET] A migrated comparison file reintroduced a "
                "raw-string scope-source compare (these files are pinned at 0):\n"
                + "\n".join(offenders)
                + "\n"
                + "\n".join(sites)
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_scope_source_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["raw_string_counts"]
        current_counts: dict[str, int] = state["raw_string_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} (baseline {allowed}, -{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[SCOPE SOURCE RATCHET] The raw-string scope-source count DROPPED "
                "— good work, but the baseline must be lowered to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet scope --update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_is_a_consistent_upper_bound(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_scope_source_ratchet(_REPO_ROOT)
        assert baseline["total_raw_string"] == sum(
            baseline["raw_string_counts"].values()
        ), "Baseline total_raw_string is inconsistent with its per-file counts."
        assert state["total_raw_string"] <= baseline["total_raw_string"], (
            f"Total raw-string scope-source compares {state['total_raw_string']} "
            f"exceeds baseline {baseline['total_raw_string']}."
        )


# ---------------------------------------------------------------------------
# Guard-liveness: the scan must catch the raw-string shapes and NOT over-flag the
# enum-member compares. Drives synthetic inputs through the production scan path.
# ---------------------------------------------------------------------------


class TestScopeSourceGuardLiveness:
    _FILE = "src/lawvm/finland/example.py"

    def _scan(self, text: str) -> list[dict[str, Any]]:
        return _INV.scan_file_scope_source_compares(self._FILE, text)

    def test_eq_str_literal_is_flagged(self) -> None:
        text = "if witness.source == 'carry_forward':\n    pass\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "compare"

    def test_in_str_set_is_flagged(self) -> None:
        text = "x = scope_confidence.source in {'preamble', 'grouped_part'}\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "compare"

    def test_group_has_scope_source_literal_is_flagged(self) -> None:
        text = "y = group_has_scope_source(group_ops, 'carry_forward')\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "group_has_scope_source_literal"

    def test_enum_member_compare_is_not_flagged(self) -> None:
        text = "if witness.source is ScopeResolutionSource.CARRY_FORWARD:\n    pass\n"
        assert self._scan(text) == []

    def test_enum_member_set_compare_is_not_flagged(self) -> None:
        text = (
            "x = scope_witness.source in {ScopeResolutionSource.PREAMBLE, "
            "ScopeResolutionSource.GROUPED_PART}\n"
        )
        assert self._scan(text) == []

    def test_source_compared_to_variable_is_not_flagged(self) -> None:
        """The helper internal `witness.source == source_norm` compares against a
        variable, not a literal — it is not a raw-string compare."""
        text = "ok = witness.source == source_norm\n"
        assert self._scan(text) == []

    def test_unrelated_dot_source_is_not_flagged(self) -> None:
        """`op.source == 'x'` on a non-scope receiver must not be flagged."""
        text = "if op.source == 'finlex':\n    pass\n"
        assert self._scan(text) == []

    def test_group_has_scope_source_variable_arg_is_not_flagged(self) -> None:
        text = "y = group_has_scope_source(group_ops, source_norm)\n"
        assert self._scan(text) == []
