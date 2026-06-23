"""Monotone untyped-authority-boundary ratchet gate (Audit B).

The central authority predicate ``lawvm.core.compile_records.is_blocking_compile_record``
decides whether a compile/evidence row blocks strict replay. That decision is an
authority-boundary act, so its input should be the typed ``CompileRecord`` carrier,
not a raw ``dict``/``Mapping`` row passed through the back-compat ``Mapping``
overload.

How it works:
  - An AST scan over ``src/lawvm/**`` classifies every
    ``is_blocking_compile_record(<arg>)`` call site as TYPED (arg is a
    ``CompileRecord(...)`` / ``CompileRecord.from_mapping(...)`` construction, or a
    name annotated/assigned as ``CompileRecord``) or UNTYPED (a bare row name, a
    dict literal, a ``.get(...)``-style row).
  - The committed baseline (``tests/data/authority_boundary_ratchet_baseline.json``)
    records the per-file UNTYPED count — the back-compat residue still passing raw
    rows. This test FAILS if any file's UNTYPED count INCREASES (a new untyped
    authority crossing) and also FAILS — with an instruction to commit the lowered
    baseline — if any count DROPS (a permanent ratchet tightening, i.e. a producer
    was converted to the typed carrier).

The residue sites (currently in ``tools/`` + evidence, passing raw rows via the
``Mapping`` overload) are fenced, not hidden: a NEW raw-dict authority crossing
anywhere under ``src/lawvm`` fails CI.
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
    """Import scripts/inventory_architecture_smells.py (not on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_architecture_smells", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.AUTHORITY_BOUNDARY_BASELINE_PATH
    assert path.exists(), (
        f"Missing authority-boundary ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_architecture_smells.py --ratchet authority "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestAuthorityBoundaryRatchet:
    def test_no_new_untyped_authority_crossing(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_authority_boundary_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["untyped_counts"]
        current_counts: dict[str, int] = state["untyped_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} UNTYPED is_blocking_compile_record(...) call "
                    f"sites (baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[AUTHORITY BOUNDARY RATCHET] NEW untyped (raw dict/Mapping) "
                "crossing(s) of is_blocking_compile_record:\n"
                + "\n".join(increases)
                + "\n\nThe authority predicate decides strict-replay blocking; its "
                "input must be the typed CompileRecord carrier. Convert the row at "
                "the boundary:\n"
                "  is_blocking_compile_record(CompileRecord.from_mapping(row))\n"
                "or hold a CompileRecord-typed value. See "
                "src/lawvm/core/compile_records.py and "
                "notes_internal/STAGERESULT_ENDGAME.md (Audit B)."
            )

    def test_ratchet_only_tightens(self) -> None:
        """If any committed UNTYPED count is now achievable lower, the baseline MUST
        be re-committed at the lower value (the ratchet is permanent)."""
        baseline = _load_baseline()
        state = _INV.scan_authority_boundary_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["untyped_counts"]
        current_counts: dict[str, int] = state["untyped_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} UNTYPED (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[AUTHORITY BOUNDARY RATCHET] The untyped-authority-crossing count "
                "DROPPED — good work, but the baseline must be lowered to lock the "
                "gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet authority --update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_untyped_is_a_consistent_upper_bound(self) -> None:
        """The committed total must be the sum of the per-file counts and must be an
        upper bound on the current total (defence in depth)."""
        baseline = _load_baseline()
        state = _INV.scan_authority_boundary_ratchet(_REPO_ROOT)
        assert baseline["total_untyped"] == sum(
            baseline["untyped_counts"].values()
        ), "Baseline total_untyped is inconsistent with its per-file counts."
        assert state["total_untyped"] <= baseline["total_untyped"], (
            f"Total untyped authority crossings {state['total_untyped']} exceeds "
            f"baseline {baseline['total_untyped']}."
        )

    def test_predicate_call_sites_are_actually_present(self) -> None:
        """Liveness: the scan must observe real call sites (typed + untyped). If
        this drops to zero the scan stopped seeing the predicate (a false-green)."""
        state = _INV.scan_authority_boundary_ratchet(_REPO_ROOT)
        assert state["total_untyped"] + state["total_typed"] > 0, (
            "No is_blocking_compile_record call sites found at all — the scan is "
            "blind; the ratchet would be vacuously green."
        )
        # The migrated consumers (uk_legislation/*) hold the typed carrier.
        assert state["total_typed"] > 0


# ---------------------------------------------------------------------------
# Guard-liveness: the gate must classify TYPED vs UNTYPED correctly. Drives
# synthetic inputs through the production scan function (AGENTS.md §2.9).
# ---------------------------------------------------------------------------


class TestAuthorityBoundaryGuardLiveness:
    _FILE = "src/lawvm/tools/example.py"

    def _scan(self, text: str) -> list[dict[str, Any]]:
        return _INV.scan_file_authority_calls(self._FILE, text)

    def test_from_mapping_arg_is_typed(self) -> None:
        text = (
            "def f(row):\n"
            "    return is_blocking_compile_record(CompileRecord.from_mapping(row))\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is True

    def test_compile_record_construction_arg_is_typed(self) -> None:
        text = (
            "def f(b):\n"
            "    return is_blocking_compile_record(CompileRecord(blocking=b))\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is True

    def test_named_local_assigned_from_carrier_is_typed(self) -> None:
        text = (
            "def f(row):\n"
            "    compile_record = CompileRecord.from_mapping(row)\n"
            "    return is_blocking_compile_record(compile_record)\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is True

    def test_annotated_param_is_typed(self) -> None:
        text = (
            "def f(rec: CompileRecord) -> bool:\n"
            "    return is_blocking_compile_record(rec)\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is True

    def test_bare_row_name_is_untyped(self) -> None:
        text = (
            "def f(row):\n"
            "    return is_blocking_compile_record(row)\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is False
        assert records[0]["arg_kind"] == "name:row"

    def test_comprehension_bare_name_is_untyped(self) -> None:
        text = (
            "def f(rows):\n"
            "    return [r for r in rows if is_blocking_compile_record(r)]\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is False

    def test_dict_literal_arg_is_untyped(self) -> None:
        text = (
            "def f():\n"
            "    return is_blocking_compile_record({'blocking': True})\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["typed"] is False
        assert records[0]["arg_kind"] == "dict_literal"

    def test_typed_name_does_not_leak_across_scopes(self) -> None:
        """A CompileRecord-typed local in one function must not type a same-named
        bare row in a sibling function with no typed binding."""
        text = (
            "def typed(row):\n"
            "    rec = CompileRecord.from_mapping(row)\n"
            "    return is_blocking_compile_record(rec)\n"
            "def untyped(rec):\n"
            "    return is_blocking_compile_record(rec)\n"
        )
        records = self._scan(text)
        by_line = {r["line"]: r for r in records}
        assert by_line[3]["typed"] is True   # typed()
        assert by_line[5]["typed"] is False  # untyped(): rec is a plain param

    def test_non_predicate_call_is_ignored(self) -> None:
        text = "x = some_other_predicate(row)\n"
        assert self._scan(text) == []
