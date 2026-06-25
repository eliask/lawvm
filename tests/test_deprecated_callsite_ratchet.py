"""Monotone ratchet over PEP 702 ``@deprecated`` FI legacy-fallback call sites.

The FI legacy fallback cohort (regex/field-based lanes demoted behind typed /
owning parsers, kept only as strangled safety nets) is decorated with
``warnings.deprecated`` (PEP 702).  On Python 3.14 ``ty`` emits
``warning[deprecated]`` at every call site and the runtime emits a
``DeprecationWarning`` — a checker signal plus fire-rate telemetry that enforces
the standing legacy-elimination goal.

This test pins the current call-site count/set per tracked symbol against a
committed baseline (``tests/data/deprecated_callsite_baseline.json``) and FAILS
if any count INCREASES — i.e. a NEW caller bound a strangled legacy lane without
explicit acknowledgment.  The ratchet is one-way: counts may only fall as the
legacy cohort is retired (regenerate the baseline to lock a drop in).

Model: mirrors ``tests/test_regex_ratchet.py`` (monotone baseline + guard
liveness via the production scan function).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_deprecated_callsites.py"


def _load_inventory_module() -> Any:
    """Import scripts/inventory_deprecated_callsites.py (not a package module)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_deprecated_callsites", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.BASELINE_PATH
    assert path.exists(), (
        f"Missing deprecated call-site baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_deprecated_callsites.py "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Baseline / cohort integrity
# ---------------------------------------------------------------------------


class TestCohortIntegrity:
    def test_tracked_symbols_are_actually_deprecated(self) -> None:
        """Every tracked symbol must carry the runtime ``@deprecated`` marker so
        the checker signal and the runtime telemetry are both live."""
        import importlib

        # Symbol -> dotted module path (parallel to DEPRECATED_SYMBOLS values).
        # NB: the three rank-3 normalize fallback op-heuristics were
        # de-deprecated after a census proved them load-bearing required
        # residuals (see scripts/inventory_deprecated_callsites.py and
        # tests/test_fi_normalize_fallback_heuristic_census.py); they left this
        # cohort honestly and so are no longer tracked here.
        symbol_modules = {
            "extract_plain_text_statute_mentions": (
                "lawvm.finland.references.ref_mention_extractor"
            ),
            "strip_legacy_roman_division_heading_prefix": (
                "lawvm.finland.oracle_comparison"
            ),
            "strip_legacy_numbered_section_heading_prefix": (
                "lawvm.finland.oracle_comparison"
            ),
        }
        assert set(symbol_modules) == set(_INV.DEPRECATED_SYMBOLS), (
            "Test cohort drifted from scanner's DEPRECATED_SYMBOLS."
        )
        for symbol, module_path in symbol_modules.items():
            module = importlib.import_module(module_path)
            func = getattr(module, symbol)
            # PEP 702 @deprecated sets __deprecated__ on the wrapped callable.
            assert getattr(func, "__deprecated__", None), (
                f"{module_path}.{symbol} is tracked by the ratchet but is not "
                "decorated with warnings.deprecated; either decorate it or drop "
                "it from DEPRECATED_SYMBOLS."
            )

    def test_baseline_total_is_consistent(self) -> None:
        baseline = _load_baseline()
        assert baseline["total"] == sum(baseline["counts"].values()), (
            "Baseline total is inconsistent with its per-symbol counts."
        )

    def test_baseline_covers_all_tracked_symbols(self) -> None:
        baseline = _load_baseline()
        assert set(baseline["counts"]) == set(_INV.DEPRECATED_SYMBOLS), (
            "Baseline counts do not cover exactly the tracked symbol set; "
            "regenerate the baseline."
        )


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestDeprecatedCallsiteRatchet:
    def test_no_new_callsites(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_deprecated_callsites(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["counts"]
        current_counts: dict[str, int] = state["counts"]

        increases: list[str] = []
        for symbol in sorted(current_counts):
            allowed = baseline_counts.get(symbol, 0)
            count = current_counts[symbol]
            if count > allowed:
                new_sites = sorted(
                    set(state["sites"][symbol]) - set(baseline["sites"].get(symbol, []))
                )
                increases.append(
                    f"  {symbol}: {count} call sites (baseline {allowed}, "
                    f"+{count - allowed})\n    new: " + ", ".join(new_sites)
                )

        if increases:
            pytest.fail(
                "\n[DEPRECATED RATCHET] NEW call site(s) bound a @deprecated FI "
                "legacy-fallback symbol:\n"
                + "\n".join(increases)
                + "\n\nThese symbols are demoted legacy lanes being strangled "
                "out. Either:\n"
                "  (1) route the new caller through the owning typed parser "
                "named in the symbol's deprecation message, or\n"
                "  (2) if the new internal call site is itself a legitimate "
                "owning/fallback path, regenerate the baseline to acknowledge "
                "it:\n"
                "      uv run python scripts/inventory_deprecated_callsites.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_ratchet_only_tightens(self) -> None:
        """If any count is now lower, the baseline MUST be re-committed lower."""
        baseline = _load_baseline()
        state = _INV.scan_deprecated_callsites(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["counts"]
        current_counts: dict[str, int] = state["counts"]

        decreases: list[str] = []
        for symbol in sorted(baseline_counts):
            allowed = baseline_counts[symbol]
            count = current_counts.get(symbol, 0)
            if count < allowed:
                decreases.append(
                    f"  {symbol}: now {count} call sites (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[DEPRECATED RATCHET] The @deprecated call-site count DROPPED — "
                "good work, but the baseline must be lowered to lock the gain "
                "in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_deprecated_callsites.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_total_is_upper_bounded_by_baseline(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_deprecated_callsites(_REPO_ROOT)
        assert state["total"] <= baseline["total"], (
            f"Total @deprecated call sites {state['total']} exceeds baseline "
            f"{baseline['total']}."
        )


# ---------------------------------------------------------------------------
# Guard liveness: drive synthetic inputs through the production scan helper so
# the gate provably catches a NEW call site and ignores def/comment lines.
# ---------------------------------------------------------------------------


class TestRatchetGuardLiveness:
    def test_call_site_is_detected(self) -> None:
        line = "    ops = parse_ops_fallback_heuristic(johto)"
        assert _INV._is_call_site(line, "parse_ops_fallback_heuristic") is True

    def test_def_line_is_not_a_call_site(self) -> None:
        line = "def parse_ops_fallback_heuristic(johto: str) -> list:"
        assert _INV._is_call_site(line, "parse_ops_fallback_heuristic") is False

    def test_comment_line_is_not_a_call_site(self) -> None:
        line = "    # parse_ops_fallback_heuristic(johto) is the legacy lane"
        assert _INV._is_call_site(line, "parse_ops_fallback_heuristic") is False

    def test_bare_mention_without_call_is_not_a_call_site(self) -> None:
        line = '    source="parse_ops_fallback_heuristic",'
        assert _INV._is_call_site(line, "parse_ops_fallback_heuristic") is False

    def test_async_def_line_is_not_a_call_site(self) -> None:
        line = "async def parse_ops_fallback_heuristic(johto):"
        assert _INV._is_call_site(line, "parse_ops_fallback_heuristic") is False
