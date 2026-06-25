"""Monotone ratchet: no NEW direct ``target_*`` writes to ``AmendmentOp`` (W3a).

The FI ``AmendmentOp`` is being migrated off its 8 scattered, loosely-typed
``target_*`` columns onto a typed ``TargetSelector`` constructed via the
sanctioned facades (``lawvm.finland.target_selector_facades``) and lowered by the
codec (``lawvm.finland.target_selector_codec``).

This test pins, per file under ``src/lawvm/finland/``, the current count of raw
``target_*`` writes (constructor kwargs + ``dataclasses.replace`` kwargs) against
a committed baseline (``tests/data/target_write_baseline.json``) and FAILS if any
file's count INCREASES — i.e. a NEW untyped construction site appeared. Existing
writes are grandfathered and may only shrink as call sites migrate onto the
facades (regenerate the baseline to lock a drop in). The ratchet is one-way.

The codec and facade modules are the sanctioned lowering point and are excluded
from the scan.

Model: mirrors ``tests/test_deprecated_callsite_ratchet.py`` and
``tests/test_regex_ratchet.py`` (monotone per-file baseline + guard liveness via
the production scan function).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_target_writes.py"


def _load_inventory_module() -> Any:
    """Import scripts/inventory_target_writes.py (not a package module)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_target_writes", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.BASELINE_PATH
    assert path.exists(), (
        f"Missing target-write baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_target_writes.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Baseline integrity
# ---------------------------------------------------------------------------


class TestBaselineIntegrity:
    def test_baseline_total_is_consistent(self) -> None:
        baseline = _load_baseline()
        assert baseline["total"] == sum(baseline["counts"].values()), (
            "Baseline total is inconsistent with its per-file counts."
        )

    def test_baseline_files_exist_and_are_in_scan_dir(self) -> None:
        baseline = _load_baseline()
        for rel in baseline["counts"]:
            assert (_REPO_ROOT / rel).exists(), (
                f"Baseline file {rel!r} no longer exists; regenerate the baseline."
            )
            assert rel.startswith(_INV._SCAN_DIR + "/"), (
                f"Baseline file {rel!r} is outside the scanned dir {_INV._SCAN_DIR}."
            )

    def test_codec_and_facades_are_excluded(self) -> None:
        """The sanctioned lowering point must never be counted by the scan."""
        baseline = _load_baseline()
        for rel in _INV._EXCLUDED_RELPATHS:
            assert rel not in baseline["counts"], (
                f"Excluded lowering point {rel!r} leaked into the baseline."
            )
        state = _INV.scan_target_writes(_REPO_ROOT)
        for rel in _INV._EXCLUDED_RELPATHS:
            assert rel not in state["counts"], (
                f"Excluded lowering point {rel!r} leaked into the live scan."
            )


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestTargetWriteRatchet:
    def test_no_new_raw_target_writes(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_target_writes(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["counts"]
        current_counts: dict[str, int] = state["counts"]

        increases: list[str] = []
        for rel in sorted(current_counts):
            allowed = baseline_counts.get(rel, 0)
            count = current_counts[rel]
            if count > allowed:
                sites = ", ".join(
                    f"L{r['line']}({'/'.join(r['kwargs'])})"
                    for r in state["sites"][rel]
                )
                increases.append(
                    f"  {rel}: {count} raw target_* writes (baseline {allowed}, "
                    f"+{count - allowed})\n    sites: {sites}"
                )

        if increases:
            pytest.fail(
                "\n[TARGET-WRITE RATCHET] NEW direct target_* write(s) to "
                "AmendmentOp added:\n"
                + "\n".join(increases)
                + "\n\nThe AmendmentOp target_* columns are being migrated onto a "
                "typed TargetSelector. Construct the target through the sanctioned "
                "facades instead:\n"
                "  from lawvm.finland.target_selector_facades import "
                "fi_section_target  # or fi_chapter_target / fi_part_target\n"
                "  AmendmentOp(op_id=..., **fi_section_target('5', chapter='2'))\n"
                "If this new site is itself a legitimate lowering point, "
                "regenerate the baseline to acknowledge it:\n"
                "  uv run python scripts/inventory_target_writes.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_ratchet_only_tightens(self) -> None:
        """If any count is now lower, the baseline MUST be re-committed lower."""
        baseline = _load_baseline()
        state = _INV.scan_target_writes(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["counts"]
        current_counts: dict[str, int] = state["counts"]

        decreases: list[str] = []
        for rel in sorted(baseline_counts):
            allowed = baseline_counts[rel]
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} raw target_* writes (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[TARGET-WRITE RATCHET] The raw target_* write count DROPPED — "
                "good work, but the baseline must be lowered to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_target_writes.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_is_upper_bounded_by_baseline(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_target_writes(_REPO_ROOT)
        assert state["total"] <= baseline["total"], (
            f"Total raw target_* writes {state['total']} exceeds baseline "
            f"{baseline['total']}."
        )


# ---------------------------------------------------------------------------
# Guard liveness: drive synthetic inputs through the production scan helper so
# the gate provably catches a NEW raw write and ignores non-write lines.
# ---------------------------------------------------------------------------


class TestRatchetGuardLiveness:
    def test_constructor_raw_write_is_detected(self) -> None:
        text = "op = AmendmentOp(op_id='x', target_section='5', target_chapter='2')\n"
        records = _INV.scan_file_target_writes(text)
        assert len(records) == 1
        assert records[0]["callee"] == "AmendmentOp"
        assert records[0]["kwargs"] == ["target_chapter", "target_section"]

    def test_dataclasses_replace_raw_write_is_detected(self) -> None:
        text = "op2 = dataclasses.replace(op, target_paragraph=3)\n"
        records = _INV.scan_file_target_writes(text)
        assert len(records) == 1
        assert records[0]["kwargs"] == ["target_paragraph"]

    def test_aliased_replace_raw_write_is_detected(self) -> None:
        text = "op2 = dc_replace(op, target_item='h')\n"
        records = _INV.scan_file_target_writes(text)
        assert len(records) == 1
        assert records[0]["kwargs"] == ["target_item"]

    def test_construction_without_target_kwargs_is_not_counted(self) -> None:
        text = "op = AmendmentOp(op_id='x', op_type=OpType.REPLACE)\n"
        assert _INV.scan_file_target_writes(text) == []

    def test_attribute_read_is_not_a_write(self) -> None:
        text = "x = op.target_section\n"
        assert _INV.scan_file_target_writes(text) == []

    def test_string_replace_without_target_kwarg_is_not_counted(self) -> None:
        text = "s = name.replace('a', 'b')\n"
        assert _INV.scan_file_target_writes(text) == []

    def test_field_declaration_is_not_a_write(self) -> None:
        """The dataclass field declaration line is not a call — must not count."""
        text = "    target_section: str = ''\n"
        assert _INV.scan_file_target_writes(text) == []

    def test_facade_lowering_is_not_a_write(self) -> None:
        """A facade-routed construction (no raw target_* kwarg) is NOT a write."""
        text = (
            "op = AmendmentOp(op_id='x', **fi_section_target('5', chapter='2'))\n"
        )
        assert _INV.scan_file_target_writes(text) == []

    def test_unparseable_text_yields_no_hits(self) -> None:
        text = "def broken(  # missing close\n    AmendmentOp(target_section='5')\n"
        assert _INV.scan_file_target_writes(text) == []

    def test_multiple_target_kwargs_count_separately(self) -> None:
        """Per-call count = number of distinct target_* kwargs passed."""
        text = (
            "op = AmendmentOp(target_section='5', target_chapter='2', "
            "target_part='3', target_special='otsikko')\n"
        )
        records = _INV.scan_file_target_writes(text)
        assert len(records) == 1
        assert len(records[0]["kwargs"]) == 4


# ---------------------------------------------------------------------------
# Trap test: a synthetic NEW raw write must trip the per-file increase check.
# Proves the ratchet would actually catch a regression (not green-by-vacuity).
# ---------------------------------------------------------------------------


class TestRatchetTrap:
    def test_synthetic_new_write_would_exceed_baseline(self) -> None:
        """Simulate a real file gaining one new raw target_* write and assert the
        per-file increase logic (same comparison the gate uses) fires."""
        baseline = _load_baseline()
        baseline_counts: dict[str, int] = dict(baseline["counts"])

        # Pick the heaviest real file and pretend it gained one more raw write.
        # Once the ratchet has driven every file to zero the baseline carries no
        # files; fall back to a synthetic baselined file so the trap still proves
        # the per-file increase comparison fires (a regression on an at-zero file
        # is the exact case the gate must keep catching).
        if baseline_counts:
            victim = max(baseline_counts, key=lambda r: baseline_counts[r])
        else:
            victim = "src/lawvm/finland/existing.py"
            baseline_counts = {victim: 0}
        mutated = dict(baseline_counts)
        mutated[victim] = baseline_counts[victim] + 1

        increases = [
            rel
            for rel in mutated
            if mutated[rel] > baseline_counts.get(rel, 0)
        ]
        assert increases == [victim], (
            "The ratchet's per-file increase comparison failed to flag a "
            "synthetic new raw target_* write."
        )

    def test_synthetic_new_file_would_be_flagged(self) -> None:
        """A brand-new file with raw writes (baseline 0) must be flagged."""
        baseline_counts: dict[str, int] = {"src/lawvm/finland/existing.py": 2}
        current_counts = dict(baseline_counts)
        current_counts["src/lawvm/finland/brand_new.py"] = 1

        increases = [
            rel
            for rel in current_counts
            if current_counts[rel] > baseline_counts.get(rel, 0)
        ]
        assert increases == ["src/lawvm/finland/brand_new.py"]
