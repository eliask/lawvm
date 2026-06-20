"""Monotone apply-decline ratchet gate ("Gate 3").

Permanently closes the silent legal-state / apply divergence class that three
successive re-audits kept finding fresh tiers of (``notes_internal/EXIT_REAUDIT
.md``, ``_2.md``, ``_3.md``): an apply-path op-handler that DECLINES an authored
op (returns the unmodified ``state`` ReplayState — a no-op) WITHOUT first
appending a typed pathology / finding to a production-visible ledger, or a
registered source-pathology CODE that has no production emit site.

Two monotone invariants (both may only TIGHTEN):

  Part 1 — apply declines: the number of UN-witnessed ``return state`` op-handler
    declines (per apply file) may never increase over the committed baseline. A
    NEW silent ``return state`` (no dominating typed witness emit) FAILS CI.

  Part 2 — code producers: the set of registered source-pathology codes with NO
    production emit site (``producerless_codes``) may only shrink. A NEW
    registered code with no producer FAILS CI.

The detector (``scripts/inventory_apply_declines.py``) is AST-based; it
distinguishes a decline (``return state``) from dispatch-protocol ``return None``
("not me, try next handler") and from a real IR change (``return state.with_ir(
...)`` / ``return _with_preserved_provision_index(...)``), and recognizes the
typed witness sinks (``source_pathologies_out`` / ``findings_out`` /
``failed_ops_out`` appends and the mutation-event emit helpers). See the script
docstring for the full scope rationale.

The committed baseline carries the current recorded debt:
  - apply_structure_ops.py:1234 (_apply_container_op): a container REPEAL/RENUMBER
    whose chapter/part target is absent returns state with only a replay_print —
    a silent decline that should witness a typed pathology.
  - BASE_MISSING_CHAPTER_SPAN: registered (and consumer-declared in an
    allowed_pathology_codes claim spec) but never CONSTRUCTED in production.
This debt is RECORDED, not hidden: the gate fails if it grows, and demands the
baseline be lowered when it shrinks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_apply_declines.py"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_apply_declines", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.RATCHET_BASELINE_PATH
    assert path.exists(), (
        f"Missing apply-decline ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_apply_declines.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Part 1: the apply-decline monotone ratchet
# ---------------------------------------------------------------------------


class TestApplyDeclineRatchet:
    def test_no_new_unwitnessed_decline(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_apply_declines(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwitnessed_counts"]
        current_counts: dict[str, int] = state["unwitnessed_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                offenders = "\n".join(
                    f"      {r['file']}:{r['line']}  {r['function']}"
                    for r in state["unwitnessed"]
                    if r["file"] == rel
                )
                increases.append(
                    f"  {rel}: {count} un-witnessed `return state` declines "
                    f"(baseline {allowed}, +{count - allowed})\n{offenders}"
                )

        if increases:
            pytest.fail(
                "\n[APPLY-DECLINE RATCHET] NEW un-witnessed `return state` "
                "op-handler decline(s) added (a silent op drop):\n"
                + "\n".join(increases)
                + "\n\nAn apply-path op-handler returned the unmodified `state` "
                "(a no-op decline) without first appending a typed witness to a "
                "production ledger. Either:\n"
                "  (1) append a typed SourcePathology to source_pathologies_out "
                "(via a build_*_pathology helper) before the `return state`, or\n"
                "  (2) stamp a skipped/failed mutation event "
                "(_emit_apply_mutation_event_for_rop) that records the op's "
                "disposition, or\n"
                "  (3) if the decline is provably a satisfied no-op needing no "
                "witness, add an inline `# lawvm-apply-decline: <rationale>` "
                "waiver on or above the return (recorded debt).\n"
                "See scripts/inventory_apply_declines.py and "
                "notes_internal/EXIT_REAUDIT.md."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_apply_declines(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwitnessed_counts"]
        current_counts: dict[str, int] = state["unwitnessed_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} un-witnessed (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[APPLY-DECLINE RATCHET] The un-witnessed decline count "
                "DROPPED — good work, but the baseline must be lowered to lock "
                "the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_apply_declines.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_unwitnessed_matches_baseline_invariant(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_apply_declines(_REPO_ROOT)
        assert baseline["total_unwitnessed"] == sum(
            baseline["unwitnessed_counts"].values()
        ), "Baseline total_unwitnessed is inconsistent with its per-file counts."
        assert state["unwitnessed_count"] <= baseline["total_unwitnessed"], (
            f"Total un-witnessed apply declines {state['unwitnessed_count']} "
            f"exceeds baseline {baseline['total_unwitnessed']}."
        )

    def test_witnessed_cross_check_sites_read_as_witnessed(self) -> None:
        """The apply_item_ops / apply_structure_ops sites that were explicitly
        witnessed by the recent re-audit fixes MUST read as witnessed (zero false
        positives), or the detector is mis-tuned."""
        state = _INV.scan_apply_declines(_REPO_ROOT)
        by_loc = {
            (r["file"].split("/")[-1], r["line"]): r["status"] for r in state["records"]
        }
        # apply_item_ops.py declines that append a typed pathology before returning.
        for line in (793, 1752, 1784, 1824):
            status = by_loc.get(("apply_item_ops.py", line))
            assert status == "witnessed", (
                f"apply_item_ops.py:{line} should read as a witnessed decline "
                f"(got {status!r}); the detector regressed into a false positive."
            )
        # apply_structure_ops.py container-otsikko witnessed sites.
        for line in (1250, 1281, 1308):
            status = by_loc.get(("apply_structure_ops.py", line))
            assert status == "witnessed", (
                f"apply_structure_ops.py:{line} should read as a witnessed "
                f"decline (got {status!r})."
            )


# ---------------------------------------------------------------------------
# Part 2: registered-code -> production-emit-site monotone check
# ---------------------------------------------------------------------------


class TestPathologyCodeProducerRatchet:
    def test_no_new_producerless_code(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_pathology_code_producers(_REPO_ROOT)
        allowed = set(baseline.get("producerless_codes", []))
        current = set(state["producerless_codes"])
        new_dead = sorted(current - allowed)
        if new_dead:
            pytest.fail(
                "\n[APPLY-DECLINE RATCHET] NEW registered source-pathology "
                "code(s) with NO production emit site:\n"
                + "\n".join(f"  {code}" for code in new_dead)
                + "\n\nA code registered in source_pathology_proof_registry with "
                "blocking enforcement but never CONSTRUCTED in production "
                "(no SourcePathology(code=...)/from_scope(code=...) site) can "
                "never fire — a dead guard. Either add a production emit site "
                "(a build_*_pathology helper invoked on the relevant apply / "
                "elaboration path) or remove the registry entry."
            )

    def test_producerless_set_only_shrinks(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_pathology_code_producers(_REPO_ROOT)
        allowed = set(baseline.get("producerless_codes", []))
        current = set(state["producerless_codes"])
        fixed = sorted(allowed - current)
        if fixed:
            pytest.fail(
                "\n[APPLY-DECLINE RATCHET] code(s) that previously had no "
                "producer now have one — lower the baseline to lock it in:\n"
                + "\n".join(f"  {code}" for code in fixed)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_apply_declines.py "
                "--update-baseline"
            )

    def test_every_non_debt_code_has_a_producer(self) -> None:
        """Defence in depth: every registered code NOT on the recorded-debt list
        must have >= 1 production emit site."""
        baseline = _load_baseline()
        state = _INV.scan_pathology_code_producers(_REPO_ROOT)
        debt = set(baseline.get("producerless_codes", []))
        offenders = sorted(set(state["producerless_codes"]) - debt)
        assert offenders == [], (
            f"registered pathology codes with no producer and not on the "
            f"recorded-debt list: {offenders}"
        )


# ---------------------------------------------------------------------------
# Guard-liveness: drive synthetic inputs through the production scan functions to
# prove the gate actually catches a NEW un-witnessed decline, honors a witness
# emit, and does not misclassify dispatch-None / real-IR-change returns.
# ---------------------------------------------------------------------------


class TestApplyDeclineGuardLiveness:
    def _records(self, src: str) -> list[dict[str, Any]]:
        import ast as _ast

        tree = _ast.parse(src)
        records: list[dict[str, Any]] = []
        lines = src.splitlines()
        for node in tree.body:
            _INV._scan_module_member(node, "src/lawvm/finland/apply_synth.py", lines, records)
        return records

    def test_bare_return_state_with_no_witness_is_unwitnessed(self) -> None:
        src = (
            "def _apply_op(state, op):\n"
            "    if op is None:\n"
            "        return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "unwitnessed"

    def test_pathology_append_before_return_is_witnessed(self) -> None:
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    if op is None:\n"
            "        if source_pathologies_out is not None:\n"
            "            source_pathologies_out.append(build_x_pathology())\n"
            "        return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "witnessed"

    def test_mutation_event_emit_before_return_is_witnessed(self) -> None:
        src = (
            "def _apply_op(state, op, mutation_events_out=None):\n"
            "    if op is None:\n"
            "        _emit_apply_mutation_event_for_rop(\n"
            "            mutation_events_out, outcome='skipped')\n"
            "        return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "witnessed"

    def test_return_none_is_not_a_decline(self) -> None:
        src = (
            "def _apply_op(state, op):\n"
            "    if op is None:\n"
            "        return None\n"
        )
        records = self._records(src)
        assert records == []

    def test_return_with_ir_is_not_a_decline(self) -> None:
        src = (
            "def _apply_op(state, op):\n"
            "    return state.with_ir(new_ir)\n"
        )
        records = self._records(src)
        assert records == []

    def test_function_without_state_param_is_skipped(self) -> None:
        # ``state`` is a local here, not a parameter, so ``return state`` is a
        # real result, not a decline of an input tree.
        src = (
            "def _build(op):\n"
            "    state = make_state()\n"
            "    return state\n"
        )
        records = self._records(src)
        assert records == []

    def test_witness_in_unrelated_later_branch_does_not_witness_return(self) -> None:
        # An emit that comes AFTER the return (does not dominate it) must not
        # make the earlier bare return read as witnessed.
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    if op is None:\n"
            "        return state\n"
            "    source_pathologies_out.append(build_x_pathology())\n"
            "    return state.with_ir(x)\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["line"] == 3
        assert records[0]["status"] == "unwitnessed"

    def test_inline_waiver_marks_decline_waived(self) -> None:
        src = (
            "def _apply_op(state, op):\n"
            "    if op is None:\n"
            "        # lawvm-apply-decline: provably satisfied no-op\n"
            "        return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "waived"

    def test_producer_scan_reports_known_codes(self) -> None:
        state = _INV.scan_pathology_code_producers(_REPO_ROOT)
        assert state["registered_code_count"] >= 1
        # Codes that ARE constructed in production must not be producerless.
        assert "CONTAINER_OTSIKKO_PAYLOAD_ABSENT" not in state["producerless_codes"]
        assert "UNHANDLED_STRUCTURE_OP" not in state["producerless_codes"]
