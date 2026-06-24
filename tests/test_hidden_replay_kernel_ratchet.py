"""No-hidden-replay-kernel-in-a-frontend ratchet (audit row XJUR-02, §2.3).

Registry assertion (LAWVM_AUDIT_INVARIANT_REGISTRY.md row XJUR-02; AGENTS §2.3
"core owns proven-shared primitives … a frontend must not grow a hidden replay
kernel"): core owns legal-state IR mutation (``core/tree_ops``),
PIT-materialization + timeline + lineage (``core/timeline*``) and op-effect
semantics. A frontend may CALL these (a thin adapter is fine) but must not (a)
carry a mutable IRNode-shadow it edits in place, nor (b) re-derive a
point-in-time / timeline by rebuilding a date-filtered tree itself. Two replay
engines drift — that drift is the §2.3 risk this audit makes visible.

This row had ZERO prior coverage, so the gate is HONEST-DISCOVERY: the committed
baseline records the ACTUAL current state, and a real frontend replay kernel is
recorded as EXISTING DEBT (with a stated per-file reason) and surfaced for a
future refactor wave — NOT failed or refactored here. The gate is a monotone
ratchet: no NEW hidden-kernel signal may appear in any frontend, and the recorded
debt may only ever fall.

The scan
(``scripts/inventory_architecture_smells.py:scan_hidden_replay_kernel``) emits two
signals per frontend file:
  * ``parallel_mutable_ir_shadow`` — a non-frozen ``@dataclass`` mirroring core
    ``IRNode`` (``children`` + ``attrs``/``text``);
  * ``frontend_pit_materialization`` — a ``materialize_*``/``*_to_pit``/
    ``compile_timelines``/``fold_timeline`` function that REBUILDS IR by hand
    (constructs ``IRNode``/``IRStatute`` in its own body) AND does not delegate to
    a core PIT/timeline entry or ``tree_ops`` mutator.

The current baseline is **4 signals across 2 files** (the discovered real state):
  * ``uk_legislation/mutable_ir.py`` — UKMutableNode, the substrate of the UK
    replay kernel (~26 modules apply op-effects over it in place);
  * ``sweden/grafter.py`` — _SEMutableNode (a benign parse builder) + two genuine
    hand-rolled PIT-materialization functions.
Both are recorded as existing debt with reasons in the baseline ``_findings``;
this wave AUDITS the boundary, it does not refactor it. The proposed finding-kind
``XJUR.HIDDEN_REPLAY_KERNEL_IN_FRONTEND`` is carried in the failure message; like
the other static ratchets it registers no ``observation_registry`` kind — there
is no production sink, the gate IS the enforcement.
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
    path = _REPO_ROOT / _INV.HIDDEN_REPLAY_KERNEL_BASELINE_PATH
    assert path.exists(), (
        f"Missing hidden-replay-kernel ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_architecture_smells.py --ratchet "
        "hidden_kernel --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet (per-file counts may only ever fall)
# ---------------------------------------------------------------------------


class TestHiddenReplayKernelRatchet:
    def test_no_new_hidden_kernel_signal(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_hidden_replay_kernel(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["signal_counts"]
        current_counts: dict[str, int] = state["signal_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} hidden-replay-kernel signal(s) "
                    f"(baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[XJUR.HIDDEN_REPLAY_KERNEL_IN_FRONTEND] NEW hidden-replay-kernel "
                "signal(s) in a frontend (§2.3 boundary):\n"
                + "\n".join(increases)
                + "\n\nCore owns legal-state IR mutation (core/tree_ops), "
                "PIT-materialization + timeline (core/timeline*) and op-effect "
                "semantics. A frontend must CALL core, not grow its own replay "
                "kernel: do not add a mutable IRNode-shadow dataclass (route edits "
                "through core/tree_ops on frozen IRNode), and do not hand-rebuild a "
                "point-in-time (call core/timeline.materialize_pit). If this is a "
                "deliberate, reviewed exception, record it in the baseline with a "
                "stated reason via `uv run python "
                "scripts/inventory_architecture_smells.py --ratchet hidden_kernel "
                "--update-baseline`."
            )

    def test_ratchet_only_tightens(self) -> None:
        """The committed total is a permanent upper bound (may only ever fall)."""
        baseline = _load_baseline()
        state = _INV.scan_hidden_replay_kernel(_REPO_ROOT)
        assert state["total_signals"] <= baseline["total_signals"], (
            f"Total hidden-replay-kernel signals {state['total_signals']} exceeds "
            f"baseline {baseline['total_signals']}."
        )
        decreases: list[str] = []
        baseline_counts: dict[str, int] = baseline["signal_counts"]
        current_counts: dict[str, int] = state["signal_counts"]
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(f"  {rel}: now {count} (baseline {allowed})")
        if decreases:
            pytest.fail(
                "\n[XJUR.HIDDEN_REPLAY_KERNEL_IN_FRONTEND] The signal count DROPPED "
                "(a frontend was disciplined off a hidden kernel — well done) — "
                "lower and re-commit the baseline:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet hidden_kernel --update-baseline"
            )


class TestHiddenReplayKernelBaselineShape:
    """The recorded debt is HONEST: every file in the baseline carries a stated
    finding reason, and the recorded findings match the discovered real state."""

    def test_every_signal_file_has_a_stated_reason(self) -> None:
        baseline = _load_baseline()
        findings: dict[str, str] = baseline.get("_findings", {})
        for rel in baseline["signal_counts"]:
            assert rel in findings and findings[rel].strip(), (
                f"Baseline file {rel} carries hidden-kernel signals but no stated "
                "reason in `_findings`. This row is honest-discovery: every "
                "recorded kernel must state WHY it is recorded (real debt vs benign "
                "parse builder)."
            )

    def test_recorded_real_kernels_are_present(self) -> None:
        """Guards the two high-value findings against silent baseline laundering:
        the UK mutable-IR kernel and the SE hand-rolled PIT must stay recorded."""
        baseline = _load_baseline()
        counts = baseline["signal_counts"]
        assert counts.get("src/lawvm/uk_legislation/mutable_ir.py", 0) >= 1
        assert counts.get("src/lawvm/sweden/grafter.py", 0) >= 1
        assert baseline["kind_counts"].get("frontend_pit_materialization", 0) >= 1
        assert baseline["kind_counts"].get("parallel_mutable_ir_shadow", 0) >= 1


# ---------------------------------------------------------------------------
# Guard-liveness: drive synthetic inputs through the production scan function so
# the gate cannot pass vacuously by being blind to the kernel it claims to fence
# (AGENTS.md §6 / §2.9). The scan must FIRE on a synthetic frontend kernel and
# stay SILENT on a sanctioned thin adapter.
# ---------------------------------------------------------------------------


class TestHiddenReplayKernelGuardLiveness:
    _FILE = "src/lawvm/example_frontend/replay.py"

    def _scan(self, text: str) -> list[dict[str, Any]]:
        return _INV.scan_file_hidden_replay_kernel(self._FILE, text)

    # --- signal (1): parallel mutable IR shadow ---------------------------

    def test_mutable_ir_shadow_is_a_kernel_signal(self) -> None:
        text = (
            "from dataclasses import dataclass, field\n"
            "@dataclass\n"
            "class FEMutableNode:\n"
            "    kind: str\n"
            "    label: str = ''\n"
            "    text: str = ''\n"
            "    attrs: dict = field(default_factory=dict)\n"
            "    children: list = field(default_factory=list)\n"
        )
        records = self._scan(text)
        kinds = [r["kind"] for r in records]
        assert "parallel_mutable_ir_shadow" in kinds
        assert any(r["name"] == "FEMutableNode" for r in records)

    def test_frozen_dataclass_is_not_a_shadow(self) -> None:
        """A FROZEN dataclass is immutable — it cannot be the substrate of an
        in-place edit kernel, so it is NOT flagged (matches core's frozen IR)."""
        text = (
            "from dataclasses import dataclass, field\n"
            "@dataclass(frozen=True)\n"
            "class FENode:\n"
            "    text: str = ''\n"
            "    attrs: dict = field(default_factory=dict)\n"
            "    children: tuple = ()\n"
        )
        assert self._scan(text) == []

    def test_dataclass_without_attrs_or_text_is_not_a_shadow(self) -> None:
        """A ``children``-only dataclass (e.g. a grouping/index) is not an IRNode
        mirror — the attrs/text twin is what marks the legal-state shadow."""
        text = (
            "from dataclasses import dataclass, field\n"
            "@dataclass\n"
            "class Group:\n"
            "    label: str = ''\n"
            "    children: list = field(default_factory=list)\n"
        )
        assert self._scan(text) == []

    # --- signal (2): hand-rolled PIT materialization ----------------------

    def test_handrolled_pit_materialization_is_a_kernel_signal(self) -> None:
        """A materialize_* function that date-filters and rebuilds IRNode itself,
        without delegating to core, is a hand-rolled PIT re-derivation."""
        text = (
            "def materialize_fe_as_of(node, as_of):\n"
            "    new_children = []\n"
            "    for child in node.children:\n"
            "        if is_active_on(child, as_of):\n"
            "            new_children.append(materialize_fe_as_of(child, as_of))\n"
            "    return IRNode(kind=node.kind, children=tuple(new_children))\n"
        )
        records = self._scan(text)
        kinds = [r["kind"] for r in records]
        assert "frontend_pit_materialization" in kinds
        assert any(r["name"] == "materialize_fe_as_of" for r in records)

    def test_adapter_calling_core_materialize_pit_is_silent(self) -> None:
        """A thin adapter that delegates to core/timeline.materialize_pit is fine
        (the EE replay_ee_to_pit shape) — even though it constructs an IRStatute
        wrapper, the core delegation exempts it."""
        text = (
            "from lawvm.core.timeline import compile_timelines, materialize_pit\n"
            "def replay_fe_to_pit(base, as_of):\n"
            "    timelines = compile_timelines(base)\n"
            "    pit = materialize_pit(timelines, as_of=as_of)\n"
            "    return IRStatute(statute_id='x', title='t', body=pit)\n"
        )
        assert self._scan(text) == []

    def test_op_effect_adapter_routing_through_tree_ops_is_silent(self) -> None:
        """An op-effect applier that names materialize but routes the IR mutation
        through core tree_ops is a disciplined adapter (the NO apply_no_ops shape),
        not a kernel — even though it constructs an IRStatute at the boundary."""
        text = (
            "from lawvm.core import tree_ops\n"
            "def materialize_fe_replayed(base, ops, as_of):\n"
            "    body = base.body\n"
            "    for op in ops:\n"
            "        body = tree_ops.replace_at(body, op.path, op.payload)\n"
            "    return IRStatute(statute_id=base.statute_id, title=base.title, body=body)\n"
        )
        assert self._scan(text) == []

    def test_materialize_orchestrator_constructing_no_ir_is_silent(self) -> None:
        """A function named materialize_* that builds nothing itself but routes the
        work through a builder helper (the FI/UK transition-graph + NZ/US dry-run
        oracle shape) constructs no IR in its own body, so it is NOT a kernel."""
        text = (
            "def materialize_fe_tree(bundle, as_of):\n"
            "    products = build_replay_products(bundle, as_of=as_of)\n"
            "    return products.materialized_state.ir\n"
        )
        assert self._scan(text) == []

    def test_non_pit_named_ir_builder_is_silent(self) -> None:
        """A plain parse-time IR builder (not materialize_*/*_to_pit-named) is not
        a PIT re-derivation — the name gate keeps forward parsing out of scope."""
        text = (
            "def build_fe_section(label, text):\n"
            "    return IRNode(kind='section', label=label, text=text)\n"
        )
        assert self._scan(text) == []
