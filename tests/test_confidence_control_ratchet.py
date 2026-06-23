"""Monotone confidence-as-control ratchet gate (audit registry OV-03).

Enforces "no float/string confidence value branches replay/apply/legal-state
control flow" over ``src/lawvm/{core,finland}``: an ``if`` / ``while`` / ternary
whose test compares a ``confidence`` / ``certified`` / ``certainty`` / ``selected``
-named value against a RAW numeric or graded-string LITERAL threshold
(``conf > 0.8``, ``certified == "high"``) is the violation. A typed-enum / None /
bool branch, a categorical-label equality (a lane name, ``certainty ==
"deterministic"``), and a count/index guard (``selected_count < 0``) are the
sanctioned typed branches and are NOT counted.

Mirrors ``tests/test_regex_ratchet.py``: a committed baseline
(``tests/data/confidence_control_ratchet_baseline.json``, currently 0 — all
audited sites use typed enums / discrete labels) that may only FALL; a NEW raw
confidence threshold deciding flow trips the gate. Waive with
``# lawvm-confidence-control: <reason>``.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_control_flow_smells.py"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_control_flow_smells_conf", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.CONFIDENCE_CONTROL_BASELINE_PATH
    assert path.exists(), (
        f"Missing confidence-control ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_control_flow_smells.py "
        "--ratchet confidence --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestConfidenceControlRatchet:
    def test_no_new_confidence_threshold_branch(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_confidence_control_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} un-waived confidence-threshold branches "
                    f"(baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[CONFIDENCE-CONTROL RATCHET] NEW raw confidence/certified/"
                "certainty/selected threshold deciding control flow:\n"
                + "\n".join(increases)
                + "\n\nA confidence value must NOT decide replay/apply/legal-state "
                "flow via a raw numeric (`conf > 0.8`) or graded-string "
                "(`certified == \"high\"`) threshold. Instead:\n"
                "  (1) branch on a typed ENUM member "
                "(`status == Rail.SELECTED`, `x.confidence != Conf.UNRESOLVED`), "
                "or\n"
                "  (2) compare against a discrete CATEGORICAL label, not a graded "
                "level, or\n"
                "  (3) if genuinely intentional, mark it "
                "`# lawvm-confidence-control: <reason>`.\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row OV-03."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_confidence_control_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[CONFIDENCE-CONTROL RATCHET] The count DROPPED — lower the "
                "baseline to lock it in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python scripts/inventory_control_flow_smells.py "
                "--ratchet confidence --update-baseline"
            )

    def test_total_unwaived_matches_baseline_invariant(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_confidence_control_ratchet(_REPO_ROOT)
        assert baseline["total_unwaived"] == sum(
            baseline["unwaived_counts"].values()
        ), "Baseline total_unwaived is inconsistent with its per-file counts."
        assert state["total_unwaived"] <= baseline["total_unwaived"], (
            f"Total un-waived confidence-threshold branches "
            f"{state['total_unwaived']} exceeds baseline "
            f"{baseline['total_unwaived']}."
        )


# ---------------------------------------------------------------------------
# Guard-liveness: the scan must trip on a raw confidence threshold, ignore the
# sanctioned typed/categorical/count branches, and honor a waiver. Each fixture
# drives the REAL production scan (scan_file_confidence_control_sites).
# ---------------------------------------------------------------------------


class TestConfidenceControlGuardLiveness:
    _F = "src/lawvm/finland/x.py"

    def _scan(self, text: str) -> list[Any]:
        return _INV.scan_file_confidence_control_sites(self._F, text)

    def test_numeric_threshold_if_is_counted(self) -> None:
        text = "def f(op):\n    if op.confidence > 0.8:\n        apply(op)\n"
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["counts"] is True

    def test_numeric_threshold_reversed_operand_order(self) -> None:
        text = "def f(op):\n    if 0.8 < op.confidence:\n        apply(op)\n"
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["counts"] is True

    def test_graded_string_equality_is_counted(self) -> None:
        text = 'def f(op):\n    if op.certified == "high":\n        apply(op)\n'
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["counts"] is True

    def test_ternary_confidence_threshold_is_counted(self) -> None:
        text = 'x = apply(op) if op.certified == "low" else None\n'
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["counts"] is True

    def test_while_confidence_threshold_is_counted(self) -> None:
        text = "def f(op):\n    while op.confidence < 0.5:\n        retry()\n"
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["counts"] is True

    # ---- NEGATIVES: the sanctioned typed branches must NOT trip ----

    def test_enum_branch_not_counted(self) -> None:
        text = "def f(x):\n    if x.confidence != Conf.UNRESOLVED:\n        go()\n"
        assert self._scan(text) == []

    def test_categorical_label_equality_not_counted(self) -> None:
        text = 'def f(s):\n    if selected_lane == "preamble":\n        go()\n'
        assert self._scan(text) == []

    def test_determinism_category_not_counted(self) -> None:
        text = (
            'def f(form):\n'
            '    if form.certainty == "deterministic":\n'
            "        go()\n"
        )
        assert self._scan(text) == []

    def test_count_guard_not_counted(self) -> None:
        text = "def f(self):\n    if self.selected_address_count < 0:\n        go()\n"
        assert self._scan(text) == []

    def test_none_branch_not_counted(self) -> None:
        text = "def f(b):\n    if b.selected_version is None:\n        go()\n"
        assert self._scan(text) == []

    # ---- waiver ----

    def test_waiver_clears_site(self) -> None:
        text = (
            "def f(op):\n"
            "    if op.confidence > 0.8:  # lawvm-confidence-control: legacy, tracked\n"
            "        apply(op)\n"
        )
        recs = self._scan(text)
        assert len(recs) == 1
        assert recs[0]["waived"] is True
        assert recs[0]["counts"] is False

    def test_unparseable_text_yields_no_hits(self) -> None:
        text = "def f(  # broken\n    if op.confidence > 0.8:\n"
        assert self._scan(text) == []
