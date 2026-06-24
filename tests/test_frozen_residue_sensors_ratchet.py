"""Monotone FW-08 frozen-residue structural-sensor ratchet (registry row FW-08).

FW-08 — *frozen-residue structural sensors gated as failing assertions at a frozen
baseline.* The §45 enforcement gate (2) names four recurring per-op-parse smell
shapes that the parser-smell inventory has long surfaced as PROSE (inventory
categories) but never WIRED as a gate. This ratchet flips them to enforcement:
each category's per-file count is frozen at a committed baseline that may only
FALL. A NEW occurrence of any of the four shapes trips CI.

The four categories (all AST-based, over the scanned semantic-plane core/finland
files — the regex-ratchet scan set, source/lexer/owning-parser/diagnostic
preclears excluded):

  * ``per_op_fstring_regex`` — an f-string is the PATTERN arg of a regex call
    (``re.compile(f"...")`` / ``re.finditer(f"...", x)``). A per-op f-string-built
    pattern bakes a value into the regex at call time (the per-op recompile smell);
    it should be a module constant or built via ``compile_classifier_regex``.
  * ``multi_finditer_same_src`` — 2+ ``.finditer(<same-name>)`` over the SAME
    searched-string name within one function scope (multi-pass scan over one
    source → span-ownership smell).
  * ``span_overlap_dedup`` — a function that both compares span endpoints
    (``.start``/``.end``/``.span``) AND keeps a ``seen``/``used``/``dedup`` set
    (the ad-hoc span-overlap-dedup shape that should be a typed coverage account).
  * ``clause_boundary_dup`` — rule-of-three: the SAME clause-boundary regex literal
    repeated verbatim 3+ times in one file (the missing-abstraction signal).

HONESTY (the generator's stopping rule)
=======================================
This is IMPL-at-frozen-baseline, NOT clean-at-0. The current tree carries 71
sites of frozen structural debt (per_op_fstring_regex 36, multi_finditer_same_src
20, clause_boundary_dup 8, span_overlap_dedup 7). FW-08's job is to FENCE that
debt: it may only fall, never grow. Each category is a STRUCTURAL sensor (a shape),
not a semantic proof — a near-duplicate clause boundary differing by one char is
NOT caught (verbatim-repeat only), and the span/dedup sensor is a co-occurrence
heuristic, not a dataflow proof. The lock is "no NEW frozen-residue shape without
a conscious baseline bump".
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_parser_smells.py"

_CATEGORIES = (
    "per_op_fstring_regex",
    "multi_finditer_same_src",
    "span_overlap_dedup",
    "clause_boundary_dup",
)


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_parser_smells_fw08", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.FROZEN_RESIDUE_SENSOR_BASELINE_PATH
    assert path.exists(), (
        f"Missing FW-08 frozen-residue baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_parser_smells.py "
        "--update-frozen-residue-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestFrozenResidueSensorsRatchet:
    def test_no_new_frozen_residue_site(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_frozen_residue_sensors(_REPO_ROOT)
        increases: list[str] = []
        for cat in _CATEGORIES:
            base_files: dict[str, int] = baseline["category_counts"].get(cat, {})
            cur_files: dict[str, int] = state["category_counts"].get(cat, {})
            for rel, count in sorted(cur_files.items()):
                allowed = base_files.get(rel, 0)
                if count > allowed:
                    increases.append(
                        f"  [{cat}] {rel}: {count} (baseline {allowed}, "
                        f"+{count - allowed})"
                    )
        if increases:
            pytest.fail(
                "\n[FW-08 FROZEN RESIDUE] NEW structural-sensor site(s):\n"
                + "\n".join(increases)
                + "\n\nA frozen-residue smell shape grew. Either remove the smell, "
                "or — if genuinely intentional — consciously bump the baseline:\n"
                "  uv run python scripts/inventory_parser_smells.py "
                "--update-frozen-residue-baseline\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row FW-08."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_frozen_residue_sensors(_REPO_ROOT)
        decreases: list[str] = []
        for cat in _CATEGORIES:
            base_files: dict[str, int] = baseline["category_counts"].get(cat, {})
            cur_files: dict[str, int] = state["category_counts"].get(cat, {})
            for rel, allowed in sorted(base_files.items()):
                count = cur_files.get(rel, 0)
                if count < allowed:
                    decreases.append(
                        f"  [{cat}] {rel}: now {count} (baseline {allowed}, "
                        f"-{allowed - count})"
                    )
        if decreases:
            pytest.fail(
                "\n[FW-08 FROZEN RESIDUE] A sensor count DROPPED — lower the "
                "baseline to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python scripts/inventory_parser_smells.py "
                "--update-frozen-residue-baseline\n(the baseline is a one-way "
                "ratchet)."
            )

    def test_totals_consistent_with_per_file(self) -> None:
        baseline = _load_baseline()
        for cat in _CATEGORIES:
            files: dict[str, int] = baseline["category_counts"].get(cat, {})
            assert baseline["totals"][cat] == sum(files.values()), cat
        assert baseline["grand_total"] == sum(baseline["totals"].values())

    def test_live_tree_at_or_below_baseline(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_frozen_residue_sensors(_REPO_ROOT)
        over: list[str] = []
        for cat in _CATEGORIES:
            base_files: dict[str, int] = baseline["category_counts"].get(cat, {})
            for rel, count in state["category_counts"].get(cat, {}).items():
                if count > base_files.get(rel, 0):
                    over.append(f"{cat}:{rel}")
        assert not over, f"live tree over baseline: {over}"

    def test_scan_is_not_blind(self) -> None:
        """Liveness: the sensors observe real debt; a zero grand total would mean
        the visitor stopped seeing the shapes (vacuously green)."""
        state = _INV.scan_frozen_residue_sensors(_REPO_ROOT)
        assert state["grand_total"] > 0


# ---------------------------------------------------------------------------
# Guard-liveness / trip-proofs: synthetic inputs through the real scan helpers.
# ---------------------------------------------------------------------------


class TestFrozenResidueGuardLiveness:
    # ---- per_op_fstring_regex ----

    def test_fstring_pattern_in_re_compile_is_flagged(self) -> None:
        tree = ast.parse('import re\nr = re.compile(f"a{x}b")\n')
        assert len(_INV._fw08_per_op_fstring_regex(tree)) == 1

    def test_fstring_pattern_in_re_search_is_flagged(self) -> None:
        tree = ast.parse('import re\nm = re.search(f"{tok}", text)\n')
        assert len(_INV._fw08_per_op_fstring_regex(tree)) == 1

    def test_constant_pattern_is_not_flagged(self) -> None:
        tree = ast.parse('import re\nr = re.compile(r"a+b")\n')
        assert _INV._fw08_per_op_fstring_regex(tree) == []

    def test_fstring_mention_in_comment_is_not_flagged(self) -> None:
        tree = ast.parse('x = 1  # re.compile(f"...") would be a smell\n')
        assert _INV._fw08_per_op_fstring_regex(tree) == []

    # ---- multi_finditer_same_src ----

    def test_two_finditer_same_name_flags_second(self) -> None:
        src = (
            "def f(text):\n"
            "    a = PAT1_RE.finditer(text)\n"
            "    b = PAT2_RE.finditer(text)\n"
            "    return a, b\n"
        )
        tree = ast.parse(src)
        assert len(_INV._fw08_multi_finditer_same_source(tree)) == 1

    def test_finditer_distinct_names_not_flagged(self) -> None:
        src = (
            "def f(a, b):\n"
            "    x = PAT_RE.finditer(a)\n"
            "    y = PAT_RE.finditer(b)\n"
            "    return x, y\n"
        )
        tree = ast.parse(src)
        assert _INV._fw08_multi_finditer_same_source(tree) == []

    def test_finditer_across_functions_not_flagged(self) -> None:
        src = (
            "def f(text):\n    return PAT_RE.finditer(text)\n"
            "def g(text):\n    return PAT_RE.finditer(text)\n"
        )
        tree = ast.parse(src)
        assert _INV._fw08_multi_finditer_same_source(tree) == []

    # ---- span_overlap_dedup ----

    def test_span_plus_seen_set_is_flagged(self) -> None:
        src = (
            "def f(matches):\n"
            "    seen = set()\n"
            "    for m in matches:\n"
            "        if m.start in seen:\n"
            "            continue\n"
            "        seen.add(m.start)\n"
            "    return seen\n"
        )
        tree = ast.parse(src)
        assert len(_INV._fw08_span_overlap_dedup(tree)) == 1

    def test_span_without_dedup_not_flagged(self) -> None:
        src = "def f(m):\n    return m.start, m.end\n"
        tree = ast.parse(src)
        assert _INV._fw08_span_overlap_dedup(tree) == []

    def test_dedup_without_span_not_flagged(self) -> None:
        src = (
            "def f(items):\n"
            "    seen = set()\n"
            "    for i in items:\n"
            "        seen.add(i)\n"
            "    return seen\n"
        )
        tree = ast.parse(src)
        assert _INV._fw08_span_overlap_dedup(tree) == []

    # ---- clause_boundary_dup ----

    def test_three_repeats_of_boundary_literal_flags_third(self) -> None:
        lit = r'"\\s*§\\s*"'
        src = f"a = {lit}\nb = {lit}\nc = {lit}\n"
        # 3 verbatim repeats of a §-boundary regex literal -> 1 over-threshold.
        assert len(_INV._fw08_clause_boundary_dup(src)) == 1

    def test_two_repeats_not_flagged(self) -> None:
        lit = r'"\\s*§\\s*"'
        src = f"a = {lit}\nb = {lit}\n"
        assert _INV._fw08_clause_boundary_dup(src) == []

    def test_distinct_boundary_literals_not_flagged(self) -> None:
        src = 'a = "§+"\nb = "subsection\\\\d+"\nc = "kohta\\\\s"\n'
        # three DISTINCT literals — no verbatim repeat -> no duplication debt.
        assert _INV._fw08_clause_boundary_dup(src) == []

    def test_non_boundary_repeat_not_flagged(self) -> None:
        src = 'a = "x+y"\nb = "x+y"\nc = "x+y"\n'
        # repeated but NOT a clause-boundary literal.
        assert _INV._fw08_clause_boundary_dup(src) == []
