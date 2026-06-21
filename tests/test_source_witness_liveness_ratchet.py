"""Monotone source-witness liveness ratchet gate (StageResult WAIST #1).

The content-addressed read witnesses on the corpus store
(``read_source_witness`` / ``read_amendment_witness`` / ``read_oracle_witness``)
build a sha256 ``DigestWitness`` over the actual bytes. They were once SEVERED —
only tests called them — which is the recurring "witness built-then-severed"
failure class (memory ``feedback_witness_must_reach_production_consumer``). WAIST
#1 un-severs the source/amendment witnesses by reading them through
``read_source_staged`` (the process pipeline's dominant source consumer) and the
certificate ``source_bundle_root`` producer.

This ratchet locks that in:
  - An AST scan over ``src/lawvm/**`` (non-test files only) counts every NON-TEST
    caller of each tracked witness method.
  - ``read_source_witness`` / ``read_amendment_witness`` MUST have >= 1 non-test
    caller (the un-severed invariant — they reach a production consumer).
  - The committed baseline records the per-method non-test caller count; the count
    may only RISE or HOLD (a one-way ratchet). A DROP fails — with the lowering
    instruction — so a future severance (deleting the production read) is caught,
    and a future un-severing must be committed (e.g. a later waist giving
    ``read_oracle_witness`` a production consumer ratchets its floor up).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_architecture_smells.py"

# The two witnesses WAIST #1 un-severs; they must have a production consumer.
_REQUIRED_LIVE_METHODS = ("read_source_witness", "read_amendment_witness")


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_architecture_smells_witness", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.WITNESS_LIVENESS_BASELINE_PATH
    assert path.exists(), (
        f"Missing source-witness liveness baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_architecture_smells.py --ratchet witness "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


class TestSourceWitnessLivenessRatchet:
    def test_un_severed_witnesses_have_a_production_consumer(self) -> None:
        """The witnesses WAIST #1 un-severs must each have >= 1 non-test caller."""
        state = _INV.scan_witness_liveness_ratchet(_REPO_ROOT)
        counts: dict[str, int] = state["nontest_caller_counts"]
        severed = [m for m in _REQUIRED_LIVE_METHODS if counts.get(m, 0) < 1]
        if severed:
            pytest.fail(
                "\n[SOURCE WITNESS LIVENESS] These content witnesses have NO "
                "non-test caller (severed — built but never read in production):\n"
                + "\n".join(f"  {m}" for m in severed)
                + "\n\nThe witness must reach a production consumer "
                "(read_source_staged / the certificate source_bundle_root). A "
                "field only tests read is the built-then-severed failure class. See "
                "src/lawvm/finland/transparent_store.py and "
                "notes_internal/WAVE2_DESIGN.md (WAIST #1)."
            )

    def test_caller_counts_only_rise(self) -> None:
        """Monotone: a per-method non-test caller count may not fall below the
        committed baseline (severance cannot regress)."""
        baseline = _load_baseline()
        state = _INV.scan_witness_liveness_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["nontest_caller_counts"]
        current_counts: dict[str, int] = state["nontest_caller_counts"]

        drops: list[str] = []
        for method, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(method, 0)
            if count < allowed:
                drops.append(
                    f"  {method}: now {count} non-test caller(s) "
                    f"(baseline {allowed}, -{allowed - count})"
                )
        if drops:
            pytest.fail(
                "\n[SOURCE WITNESS LIVENESS] A witness lost a production consumer "
                "(severance regression):\n"
                + "\n".join(drops)
                + "\n\nRestore the production read. If the drop is intentional and "
                "a witness was deliberately removed, regenerate the baseline:\n"
                "  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet witness --update-baseline"
            )

    def test_rise_must_be_committed(self) -> None:
        """If a method gained non-test callers (e.g. a later waist un-severs the
        oracle witness), the baseline floor must be re-committed at the higher
        value to lock the gain in."""
        baseline = _load_baseline()
        state = _INV.scan_witness_liveness_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["nontest_caller_counts"]
        current_counts: dict[str, int] = state["nontest_caller_counts"]

        rises: list[str] = []
        for method, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(method, 0)
            if count > allowed:
                rises.append(
                    f"  {method}: now {count} non-test caller(s) "
                    f"(baseline {allowed}, +{count - allowed})"
                )
        if rises:
            pytest.fail(
                "\n[SOURCE WITNESS LIVENESS] A witness gained production consumers "
                "— good; commit the higher floor:\n"
                + "\n".join(rises)
                + "\n\nRegenerate the baseline:\n"
                "  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet witness --update-baseline"
            )

    def test_baseline_total_is_consistent(self) -> None:
        baseline = _load_baseline()
        assert baseline["total_nontest_callers"] == sum(
            baseline["nontest_caller_counts"].values()
        ), "Baseline total_nontest_callers is inconsistent with its per-method counts."


class TestWitnessLivenessGuardLiveness:
    """The scan must classify method-call sites correctly (drives the production
    scan function on synthetic inputs)."""

    _FILE = "src/lawvm/finland/example.py"

    def _scan(self, text: str) -> list[dict[str, Any]]:
        return _INV.scan_file_witness_callers(self._FILE, text)

    def test_attribute_call_is_a_caller(self) -> None:
        text = "def f(corpus, sid):\n    return corpus.read_source_witness(sid)\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["method"] == "read_source_witness"

    def test_self_call_is_a_caller(self) -> None:
        text = "def f(self, sid):\n    return self.read_amendment_witness(sid)\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["method"] == "read_amendment_witness"

    def test_method_definition_is_not_a_caller(self) -> None:
        text = (
            "class C:\n"
            "    def read_source_witness(self, sid):\n"
            "        return None\n"
        )
        # A def is not a Call; only the call expression counts.
        assert self._scan(text) == []

    def test_unrelated_method_is_ignored(self) -> None:
        text = "def f(corpus, sid):\n    return corpus.read_source(sid)\n"
        assert self._scan(text) == []
