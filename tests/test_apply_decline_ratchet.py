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

The committed baseline carries zero recorded debt: both the apply-decline count
and the producerless-code set ratchet at zero. The two original debt items were:
  - apply_structure_ops.py (_apply_container_op): a container REPEAL/RENUMBER
    whose chapter/part target is absent returned state with only a replay_print —
    now witnessed by a typed CONTAINER_OP_TARGET_ABSENT pathology.
  - BASE_MISSING_CHAPTER_SPAN: a registry-only code never CONSTRUCTED in
    production whose real condition (an abridged base omitting a chapter span) is
    already witnessed by fi_chapter_seed_abridged_base_chapter_unreconstructable —
    removed as dead code (registry entry + consumer declaration).
The ratchet still enforces both invariants: the gate fails if either grows, and
demands the baseline be lowered if it ever shrinks again.
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
        # apply_structure_ops.py witnessed sites: the REPLACE-target-absent arm
        # (1253), the container REPEAL/RENUMBER-target-absent arm (1269), the
        # otsikko ABSENT-target arm (1293, witnessed by the G2 hardening round —
        # was the live G2-masked drop), and the container-otsikko no-heading /
        # fall-through arms (1320, 1347).
        for line in (1253, 1269, 1293, 1320, 1347):
            status = by_loc.get(("apply_structure_ops.py", line))
            assert status == "witnessed", (
                f"apply_structure_ops.py:{line} should read as a witnessed "
                f"decline (got {status!r})."
            )
        # apply_subsection_ops.py: the momentti REPEAL out-of-range-target arm
        # (906) — the second G2-masked live drop, now witnessed.
        assert by_loc.get(("apply_subsection_ops.py", 906)) == "witnessed", (
            "apply_subsection_ops.py:906 (momentti REPEAL out-of-range target) "
            "should read as a witnessed decline."
        )
        # apply_typed_dispatch.py case-arm declines (G1): the unhandled-target /
        # unknown-intent `case _:` arms MUST be (a) VISIBLE to the detector at
        # all and (b) read as witnessed (each stamps a mutation event / _fail).
        for line in (1169, 1273, 1372, 2195, 2207):
            status = by_loc.get(("apply_typed_dispatch.py", line))
            assert status == "witnessed", (
                f"apply_typed_dispatch.py:{line} (a `case _:` decline) should be "
                f"visible AND witnessed (got {status!r}); the G1 match/case walk "
                f"or the _fail/_emit witness recognition regressed."
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

    def test_renamed_replaystate_param_op_handler_decline_is_detected(self) -> None:
        # E5 regression lock (widening): an op-handler whose ReplayState param is
        # named something OTHER than `state` (here `st`, recognized by its
        # `ReplayState` annotation) and which is wired to a witness sink IS an
        # op-handler — a bare `return st` decline must be detected. Keying on the
        # literal name `state` alone (the old behavior) made this invisible.
        src = (
            "def _apply_op(st: 'ReplayState', op, source_pathologies_out=None):\n"
            "    if op is None:\n"
            "        return st\n"
        )
        records = self._records(src)
        assert len(records) == 1, (
            "a `return <renamed-ReplayState-param>` op-handler decline must be "
            "detected by annotation, not only the literal name `state` (E5)"
        )
        assert records[0]["status"] == "unwitnessed"
        assert records[0]["function"] == "_apply_op"

    def test_renamed_replaystate_param_handler_witness_reads_witnessed(self) -> None:
        # The witness dominance check must work for a renamed ReplayState param
        # too: a typed witness emit before `return st` reads as witnessed.
        src = (
            "def _apply_op(st: 'ReplayState', op, source_pathologies_out=None):\n"
            "    if op is None:\n"
            "        source_pathologies_out.append(build_x_pathology())\n"
            "        return st\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "witnessed"

    def test_replaystate_helper_without_witness_sink_is_not_flagged(self) -> None:
        # E5 false-positive guard: a function that takes a ReplayState (named
        # `result`) and an op but NO witness-sink parameter is a pure
        # state-transform HELPER, not an op-handler. Returning its ReplayState
        # param is a side-effect no-op, NOT an un-witnessed op drop — it must be
        # OUT of scope. This is the shape of `_maybe_update_section_heading`.
        src = (
            "def _maybe_update_heading(\n"
            "    result: 'ReplayState', dispatch_op, muutos_ir=None\n"
            "):\n"
            "    if muutos_ir is None:\n"
            "        return result\n"
            "    return _rebuilt(result)\n"
        )
        records = self._records(src)
        assert records == [], (
            "a ReplayState-typed helper with an op param but no witness sink is "
            "not an op-handler and must not be flagged as an un-witnessed decline "
            "(E5 false-positive guard; the _maybe_update_section_heading shape)"
        )

    def test_real_maybe_update_section_heading_is_excluded(self) -> None:
        # End-to-end lock against the actual production function: the live
        # `_maybe_update_section_heading` (ReplayState param `result`, op param
        # `dispatch_op`, NO witness sink) must NOT appear in the scan records.
        state = _INV.scan_apply_declines(_REPO_ROOT)
        offenders = [
            r for r in state["records"]
            if r["function"] == "_maybe_update_section_heading"
        ]
        assert offenders == [], (
            "_maybe_update_section_heading is a state-transform helper (no witness "
            f"sink), not an op-handler; it must be excluded, got {offenders!r}"
        )

    def test_g1_bare_return_state_inside_case_arm_is_detected(self) -> None:
        # G1 regression lock: a bare `return state` inside a `match`/`case` arm
        # must be VISIBLE to the detector (the original walkers never descended
        # into `ast.Match.cases[*].body`, so it read as 0 records).
        src = (
            "def _apply_op(state, op):\n"
            "    match op:\n"
            "        case _:\n"
            "            return state\n"
        )
        records = self._records(src)
        assert len(records) == 1, (
            "a `return state` inside a `case` arm must be detected (G1)"
        )
        assert records[0]["status"] == "unwitnessed"

    def test_g1_witnessed_return_state_inside_case_arm_reads_witnessed(self) -> None:
        # A witness emit on the case arm's own path dominates the case return.
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    match op:\n"
            "        case _:\n"
            "            if source_pathologies_out is not None:\n"
            "                source_pathologies_out.append(build_x_pathology())\n"
            "            return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "witnessed"

    def test_g2_witness_in_disjoint_sibling_case_does_not_witness(self) -> None:
        # G2 regression lock: a witness inside a DIFFERENT, control-flow-disjoint
        # `case` arm must NOT make a later bare `return state` read as witnessed.
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    match op:\n"
            "        case 1:\n"
            "            source_pathologies_out.append(build_x_pathology())\n"
            "            return state\n"
            "        case _:\n"
            "            return state\n"
        )
        records = self._records(src)
        by_line = {r["line"]: r["status"] for r in records}
        # The witnessed arm (line 5) reads witnessed; the disjoint `case _:`
        # decline (line 7) must read UN-witnessed, not credited by the sibling.
        assert by_line.get(5) == "witnessed"
        assert by_line.get(7) == "unwitnessed", (
            "a witness in a disjoint sibling `case` arm must NOT dominate a "
            "later bare `return state` (G2)"
        )

    def test_g2_witness_in_disjoint_sibling_if_does_not_witness(self) -> None:
        # G2 regression lock for plain if-branches: a witness inside a preceding
        # `if` arm that ITSELF returns (an early-exit, disjoint path) must NOT
        # dominate a later fall-through bare `return state`.
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    if op == 1:\n"
            "        source_pathologies_out.append(build_x_pathology())\n"
            "        return None\n"
            "    return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["line"] == 5
        assert records[0]["status"] == "unwitnessed", (
            "a witness inside a preceding if-arm that returns (a disjoint path) "
            "must NOT dominate a later fall-through `return state` (G2)"
        )

    def test_g2_falls_through_guard_if_still_witnesses(self) -> None:
        # The G2 fix must KEEP the production convention working: a preceding
        # guard `if source_pathologies_out is not None:` that appends and FALLS
        # THROUGH to the return is a real dominator.
        src = (
            "def _apply_op(state, op, source_pathologies_out=None):\n"
            "    if source_pathologies_out is not None:\n"
            "        source_pathologies_out.append(build_x_pathology())\n"
            "    return state\n"
        )
        records = self._records(src)
        assert len(records) == 1
        assert records[0]["status"] == "witnessed", (
            "a preceding fall-through guard-if witness must still dominate (G2 "
            "must not over-correct and reject real guard-nested witnesses)"
        )

    def test_g3_discovery_globs_apply_files(self) -> None:
        # G3 regression lock: file discovery globs apply_*.py rather than a
        # hardcoded allowlist, so coverage auto-includes every apply file.
        discovered = set(_INV.discover_apply_files(_REPO_ROOT))
        for rel in (
            "src/lawvm/finland/apply_structure_ops.py",
            "src/lawvm/finland/apply_subsection_ops.py",
            "src/lawvm/finland/apply_typed_dispatch.py",
        ):
            assert rel in discovered, f"{rel} must be auto-discovered (G3)"
        # No discovered file may be silently dropped except via the documented
        # explicit exclusion set.
        for rel in discovered:
            assert rel not in _INV.EXCLUDED_APPLY_FILES

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
