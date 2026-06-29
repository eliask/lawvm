"""AST-scan §2.3 firewall test: no ``lawvm.finland.<module>`` paths in core.

The W7 M13 arch-review-MEDIUM-2 fix lifted the 13 ``lawvm.finland.X.Y``
implementation-level module paths that lived inside the v0 binding set's
``AssumptionRegister.scope`` / ``public_message`` strings
(:mod:`lawvm.core.claim_assumption_binding`) to the concept-id form
``fi.frontend.<concept>``. The lift preserves the v0 contract (claim-relative
completeness over the versioned binding set; see ``CLAIM_ASSUMPTION_BINDING_VERSION``)
but decouples the v→vN module rename surface — renaming a Finland frontend
module no longer silently drifts the assumption bindings.

Mirrors the precedent pattern of
:mod:`tests.test_core_firewall_no_fi_fiscal_doctrine` (the §2.3 firewall
that keeps the W5 H1 ``PoolMention`` concrete kernel in
``lawvm.finland.pool_mention_primitive`` out of core) AND the per-file ratchet
of :mod:`tests.test_classifier_wrap_ratchet` (FW-07: raw ``re.compile`` may
only fall). A structural AST exclusion test is the only mechanism that catches
a future re-leak through code review alone — a re-introduced
``lawvm.finland.X.Y`` literal in a core file would compile fine and pass every
behavior test.

THE TWO INVARIANTS PINNED HERE:

* **Lift-invariant (claim_assumption_binding.py):** the v0 binding set is now
  fully concept-id-bound. This file has ZERO ``lawvm.finland.<module>``
  substring matches in any string literal. A future regression (re-pasting
  ``lawvm.finland.X.Y`` into a new binding's ``scope`` text) re-opens the §2.3
  leak and silently drifts on a frontend module rename — this rule stays GREEN
  forever; it is not a ratchet, it is a floor of zero.

* **Net-new ratchet (other core files):** every OTHER core file that carries
  ``lawvm.finland.<module>`` substring matches is pinned at its committed
  per-file count baseline (``tests/data/core_finland_module_paths_baseline.json``).
  The baseline MAY ONLY FALL — a NEW ``lawvm.finland.X.Y`` string literal in
  any core file trips the gate; the author must either lift it to concept-id
  form OR consciously bump the baseline (mirroring FW-07's wrap-vs-bump
  contract). A drop in count also trips the gate (forces a deliberate baseline
  update to lock the gain in) — the ratchet is one-way.

HONESTY (the generator's stopping rule). This is IMPL-at-frozen-baseline with
a NAMED HEURISTIC GAP, NOT clean-at-zero across core. The current scanned tree
carries a baseline of 44 ``lawvm.finland.<module>`` occurrences across 18
non-``claim_assumption_binding`` core files (docstring mentions, owner=/
checker_ref= fields in ``invariant_spec.py``, type-bearing field values in
``pool_mention.py``, etc.). Each of those is an out-of-scope site for M13
(rightfully so — they reference concrete frontend modules by Python module
path as load-bearing field values, not concept-id-bound assumption text); they
are pinned here so a NET-NEW one cannot land silently. Lifting them is future
work belonging to a separate task (one per file-family).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _REPO_ROOT / "src" / "lawvm" / "core"
_BASELINE_PATH = "tests/data/core_finland_module_paths_baseline.json"

# Matches ``lawvm.finland.X`` / ``lawvm.finland.X.Y`` / ``lawvm.finland.X.Y.Z``
# substrings inside Python string-literal Constant nodes. Per the §2.3
# firewall doctrine, no NEW ``lawvm.finland.<module>`` reference may land in
# ANY core file; the W7 M13 lift moves the v0 binding set to the concept-id
# form (``fi.frontend.<concept>``), which does not match this pattern.
_FINLAND_MODPATH_RE = re.compile(r"lawvm\.finland\.[A-Za-z_][A-Za-z_0-9.]*")


def _python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under *root*, sorted for stable diffs."""
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _scan_constant_matches_text(source: str) -> list[tuple[int, str]]:
    """List of ``(lineno, matched_substring)`` for every
    ``lawvm.finland.<module>`` occurrence inside the string-literal Constant
    nodes of *source* (raw Python source text).

    AST Constant nodes are exact source literals; ``re.finditer`` counts each
    individual occurrence inside a multi-line string (a docstring mentioning a
    frontend module twice counts as TWO — the count must move when ANY new
    reference lands, including a new pad inside an existing literal).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in _FINLAND_MODPATH_RE.finditer(node.value):
                out.append((node.lineno, m.group(0)))
    return out


def _scan_constant_matches(path: Path) -> list[tuple[int, str]]:
    """Path-taking overload of :func:`_scan_constant_matches_text` that reads
    *path* from disk before scanning. Used by the live (whole-core) scan; the
    text-taking overload is used by guard-liveness tests that must NOT touch
    the filesystem (xdist-safe — no concurrent worker can observe a temp
    injection)."""
    try:
        return _scan_constant_matches_text(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def scan_finland_module_paths(repo_root: Path) -> dict[str, int]:
    """``{relative_path: occurrence_count}`` of ``lawvm.finland.<module>``
    substrings inside string-literal Constant nodes across ``src/lawvm/core/**``.

    A count of ZERO is omitted from the map (mirrors the count map shape used
    by ``tests/test_classifier_wrap_ratchet``).
    """
    out: dict[str, int] = {}
    for path in _python_files(_CORE_DIR):
        n = len(_scan_constant_matches(path))
        if n:
            out[str(path.relative_to(repo_root))] = n
    return out


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _BASELINE_PATH
    assert path.exists(), (
        f"Missing M13 baseline at {path}. Generate it with "
        "`uv run python tests/test_core_firewall_no_finland_module_paths.py "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_counts(baseline: dict[str, Any]) -> dict[str, int]:
    counts = baseline["counts"]
    assert isinstance(counts, dict)
    return {str(k): int(v) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# THE LIFT-INVARIANT (M13's specific invariant — the W7 lift stays GREEN.)
# ---------------------------------------------------------------------------


def test_claim_assumption_binding_carries_no_finland_module_path_literals() -> None:
    """The 13 ``lawvm.finland.X.Y`` strings that lived in
    ``src/lawvm/core/claim_assumption_binding.py`` were lifted to
    ``fi.frontend.<concept>`` (iter2 W7 M13 arch review MEDIUM-2). A future
    regression — re-pasting a ``lawvm.finland.<module>`` literal into a binding's
    ``scope``/``public_message`` text — re-opens the §2.3 leak and silently
    drifts on a frontend module rename. This is a permanent GREEN floor at
    ZERO; the rest of the firewall is a one-way ratchet, but the lift-invariant
    must NEVER move off zero.

    Mirror of the precedent's
    ``tests/test_core_firewall_no_fi_fiscal_doctrine.py::test_core_does_not_carry_fi_budget_canonical_id_prefix``
    — a substring scan over self-asserted zero (no allowlist, no ratchet) so
    no future edit can silently re-leak under count cover.
    """
    path = _CORE_DIR / "claim_assumption_binding.py"
    matches = _scan_constant_matches(path)
    assert not matches, (
        "src/lawvm/core/claim_assumption_binding.py carries a "
        "`lawvm.finland.<module>` implementation-level path string in a "
        "string literal (M13 lift regression, AGENTS.md §2.3). The v0 "
        "binding set was lifted to concept-id form `fi.frontend.<concept>`; "
        "a re-introduced literal silently drifts assumption bindings on a "
        "frontend module rename. Offenders: "
        + "; ".join(f"line {ln}: {lit!r}" for ln, lit in matches)
    )


# ---------------------------------------------------------------------------
# THE NET-NEW RATCHET (no NEW lawvm.finland.* in any OTHER core file.)
# ---------------------------------------------------------------------------


class TestFinlandModulePathRatchet:
    def test_no_new_finland_module_path_in_any_core_file(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_finland_module_paths(_REPO_ROOT)
        increases = [
            f"  {rel}: {count} `lawvm.finland.*` occurrences "
            f"(baseline {baseline.get(rel, 0)}, "
            f"+{count - baseline.get(rel, 0)})"
            for rel, count in sorted(current.items())
            if count > baseline.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[M13 §2.3 FIREWALL] NEW `lawvm.finland.<module>` string "
                "literal(s) in a core/ file:\n"
                + "\n".join(increases)
                + "\n\nA core file may not grow a NEW frontend module path "
                "literal (AGENTS.md §2.3). The M13 precedent is to lift the "
                "string to the concept-id form `fi.frontend.<concept>` "
                "(see src/lawvm/core/claim_assumption_binding.py for the v0 "
                "binding set lift). If the new literal is a load-bearing "
                "owner=/checker_ref= field that must cite the concrete "
                "frontend module,consciously bump the baseline:\n"
                "  uv run python "
                "tests/test_core_firewall_no_finland_module_paths.py "
                "--update-baseline\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md (iter2 W7 M13)."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_finland_module_paths(_REPO_ROOT)
        decreases = [
            f"  {rel}: now {current.get(rel, 0)} occurrences "
            f"(baseline {baseline[rel]})"
            for rel, baseline_count in sorted(baseline.items())
            if current.get(rel, 0) < baseline_count
        ]
        if decreases:
            pytest.fail(
                "\n[M13 §2.3 FIREWALL] A `lawvm.finland.*` count DROPPED in "
                "a core file — lower the baseline to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python "
                "tests/test_core_firewall_no_finland_module_paths.py "
                "--update-baseline\n(the baseline is a one-way ratchet; the "
                "claim_assumption_binding lift may NOT move off zero — that "
                "floor is asserted in test_claim_assumption_binding_carries"
                "_no_finland_module_path_literals)."
            )

    def test_no_NEW_baseline_file_carries_finland_module_paths(self) -> None:
        """If a NEW core file picks up ``lawvm.finland.<module>`` literals, the
        file is not yet in the baseline — this trips before the per-file count
        test even has a baseline to compare against."""
        baseline = _baseline_counts(_load_baseline())
        current = scan_finland_module_paths(_REPO_ROOT)
        new_files = sorted(
            rel for rel, count in current.items() if rel not in baseline and count > 0
        )
        # claim_assumption_binding.py must NEVER re-appear here (lift floor);
        # it also has its own dedicated zero-floor test above. The dedicated
        # test is a hard fail with a tighter message; this one is the catch-
        # all for any OTHER file not in the baseline.
        new_files = [
            rel for rel in new_files
            if rel != "src/lawvm/core/claim_assumption_binding.py"
        ]
        assert not new_files, (
            "[M13 §2.3 FIREWALL] a NEW core file carries `lawvm.finland.*` "
            "literals and is not in the baseline — bump the baseline "
            "consciously (and lift to `fi.frontend.<concept>` if possible):\n  "
            + "\n  ".join(new_files)
        )

    def test_total_consistent_and_upper_bounded(self) -> None:
        baseline = _load_baseline()
        counts = _baseline_counts(baseline)
        assert baseline["total"] == sum(counts.values())
        current = scan_finland_module_paths(_REPO_ROOT)
        assert sum(current.values()) <= baseline["total"]

    def test_scan_is_not_blind(self) -> None:
        """Liveness: the scan observes real `lawvm.finland.*` sites elsewhere
        in core (the baseline invariant sites — owner=/checker_ref= docstring
        mentions in invariant_spec.py, pool_mention.py, etc.). Zero would mean
        the AST walk went blind (vacuously green)."""
        current = scan_finland_module_paths(_REPO_ROOT)
        assert sum(current.values()) > 0


# ---------------------------------------------------------------------------
# Guard-liveness: the AST detector counts substrings, ignores comments.
# ---------------------------------------------------------------------------


class TestFinlandModulePathDetector:
    def test_match_counted_once_per_occurrence(self) -> None:
        """Two occurrences in one constant count as TWO (so a new pad inside
        an existing literal trips the ratchet, not just a new literal)."""
        src = 'x = "see `lawvm.finland.foo` and also `lawvm.finland.bar`"\n'
        matches = _scan_constant_matches_text(src)
        assert len(matches) == 2
        assert {m for _, m in matches} == {
            "lawvm.finland.foo",
            "lawvm.finland.bar",
        }

    def test_match_captures_dotted_path(self) -> None:
        src = 'x = "lawvm.finland.references.eu_transposition_edges:fn"\n'
        matches = _scan_constant_matches_text(src)
        assert len(matches) == 1
        # The detector pattern captures the dotted module path but stops at the
        # ``:`` separator that precedes a callable name (e.g. ``module:func``) —
        # this matches the live scan contract over owner=/checker_ref= fields in
        # invariant_spec.py.
        assert matches[0][1] == "lawvm.finland.references.eu_transposition_edges"

    def test_comment_only_match_not_counted(self) -> None:
        """AST Constant nodes ignore Python comments — a ``lawvm.finland.X``
        mention in a ``#`` comment never reaches the AbstractGrammar Constant
        walk and is correctly not counted (matches the precedent's dataflow
        discipline in test_core_firewall_no_fi_fiscal_doctrine)."""
        src = '# lawvm.finland.foo is the FI bench edge surface\nx = 1\n'
        matches = _scan_constant_matches_text(src)
        assert matches == []

    def test_concept_id_form_not_counted(self) -> None:
        """The M13 lift replaces ``lawvm.finland.<module>`` with
        ``fi.frontend.<concept>``; the latter must NOT match the detector
        pattern (or the lift would itself trip the firewall)."""
        src = 'x = "see fi.frontend.bench_evidence_surface."\n'
        matches = _scan_constant_matches_text(src)
        assert matches == []

    def test_injected_literal_trips_ratchet_above_baseline(self) -> None:
        """A real core file with one extra injected ``lawvm.finland.X`` literal
        appended must scan ABOVE its committed per-file baseline → the
        ratchet would FAIL.

        Pure in-memory scan via :func:`_scan_constant_matches_text` — never
        touches the filesystem (xdist-safe; no concurrent worker can observe
        a temp injection that would flake ``test_clean_tree_at_or_below_
        baseline`` in a sibling worker)."""
        baseline = _baseline_counts(_load_baseline())
        current = scan_finland_module_paths(_REPO_ROOT)
        # Pick any non-zero baseline file currently AT its baseline (no
        # concurrent drift in that file).
        candidate_rels = [
            rel for rel in baseline
            if rel != "src/lawvm/core/claim_assumption_binding.py"
            and current.get(rel, 0) == baseline[rel]
        ]
        assert candidate_rels, (
            "no scanned core file is at its baseline for the injection test "
            f"(live scan: {current!r}; baseline: {baseline!r})"
        )
        rel = sorted(candidate_rels)[0]
        clean = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        injected = clean + '\n_INJECTED = "lawvm.finland.synthetic_injected_marker"\n'
        clean_count = len(_scan_constant_matches_text(clean))
        injected_count = len(_scan_constant_matches_text(injected))
        assert clean_count == baseline[rel], (
            f"baseline for {rel} is stale: baseline says {baseline[rel]} but "
            f"clean scan finds {clean_count}; regenerate the baseline."
        )
        # One new literal → one new match → the live ratchet would trip.
        assert injected_count == clean_count + 1, (
            f"injecting one `lawvm.finland.X` literal into {rel} did NOT move "
            f"the count by exactly 1 (clean={clean_count}, "
            f"injected={injected_count}) — the detector is blind to the "
            "synthetic injection"
        )
        # The injected literal is exactly the form the firewall forbids: a
        # `lawvm.finland.<module>` substring inside a string-literal Constant.
        injected_matches = _scan_constant_matches_text(injected)
        assert any(
            lit == "lawvm.finland.synthetic_injected_marker" for _, lit in injected_matches
        ), "injected `lawvm.finland.synthetic_injected_marker` was not detected"

    def test_clean_tree_at_or_below_baseline(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_finland_module_paths(_REPO_ROOT)
        over = {
            rel: c for rel, c in current.items()
            if c > baseline.get(rel, 0)
            and rel != "src/lawvm/core/claim_assumption_binding.py"
        }
        assert not over, f"unexpected over-baseline files: {over}"


# ---------------------------------------------------------------------------
# Baseline regeneration entry point.
# ---------------------------------------------------------------------------


def _update_baseline() -> None:
    counts = scan_finland_module_paths(_REPO_ROOT)
    # The lift-invariant file MUST stay at zero; never write it into the
    # baseline counts even if a regression let one slip in (the dedicated
    # zero-floor test will catch that — the baseline is for the ratchet over
    # the OTHER core files).
    counts.pop("src/lawvm/core/claim_assumption_binding.py", None)
    payload = {
        "_doc": (
            "M13 core/ lawvm.finland.* module-path ratchet baseline (iter2 "
            "W7 M13 arch review MEDIUM-2). Per-file count of "
            "lawvm.finland.<module> substring occurrences inside string-literal "
            "Constant nodes under src/lawvm/core/**. May only FALL. A NEW "
            "lawvm.finland.X.Y string literal in a core file trips the gate -- "
            "lift it to the concept-id form fi.frontend.<concept> (see "
            "src/lawvm/core/claim_assumption_binding.py for the W7 M13 "
            "precedent: 13 lawvm.finland.X.Y strings lifted out of the v0 "
            "binding set). Regenerate: uv run python "
            "tests/test_core_firewall_no_finland_module_paths.py "
            "--update-baseline."
        ),
        "counts": dict(sorted(counts.items())),
        "total": sum(counts.values()),
    }
    out = _REPO_ROOT / _BASELINE_PATH
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} (total {payload['total']})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(scan_finland_module_paths(_REPO_ROOT), indent=2))
