"""Project-level pytest configuration.

Automatically skips @pytest.mark.slow and @pytest.mark.network tests unless the
user explicitly opts in (--run-slow / --run-network, or -m slow / -m network).

Skipping `network` (live-HTTP) tests by default keeps the suite HERMETIC: in an
offline, sandboxed, or network-restricted CI environment, outbound connections
are often *dropped* rather than refused, so an unguarded live fetch hangs
indefinitely instead of failing fast. Hermetic-by-default makes the suite
runnable anywhere; opt in where live network is actually available.

Usage:
    uv run pytest tests/                        # skips slow + network tests
    uv run pytest tests/ --run-slow             # also includes slow tests
    uv run pytest tests/ --run-network          # also includes network tests
    uv run pytest tests/ -m slow                # runs only slow tests
    uv run pytest tests/ -m network             # runs only network tests
"""
import os
import re
import warnings

import pytest

# ortools 9.15 cp_model.py uses ~False and ~True as integer sentinels (-1, -2).
# Python 3.13+ warns that bitwise inversion on bool is deprecated (removed in 3.16).
# This is an upstream bug in ortools, not our code.  Suppress until fixed upstream.
# Must be set here (conftest module-load time) because the warning fires during
# bytecode compilation of cp_model.py on first import, before pytest.ini filterwarnings.
warnings.filterwarnings(
    "ignore",
    message="Bitwise inversion.*bool.*deprecated",
    category=DeprecationWarning,
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_local_llm: test requires a local LLM server at http://localhost:11434",
    )
    mark_expr = config.getoption("-m", default="") or ""
    explicit = mark_expr if isinstance(mark_expr, str) else ""
    if config.getoption("--run-slow", default=False) or _marker_requested(explicit, "slow"):
        os.environ["LAWVM_PYTEST_COLLECT_SLOW_GOLD"] = "1"
    else:
        os.environ.pop("LAWVM_PYTEST_COLLECT_SLOW_GOLD", None)


def _marker_requested(mark_expr: str, marker: str) -> bool:
    """Return True when a -m expression positively selects ``marker``."""
    return bool(re.search(rf"\b{re.escape(marker)}\b", mark_expr)) and not bool(
        re.search(rf"\bnot\s+{re.escape(marker)}\b", mark_expr)
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Also run tests marked @pytest.mark.slow (default: skipped).",
    )
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help=(
            "Also run tests marked @pytest.mark.network — live HTTP "
            "(default: skipped, so the suite is hermetic and cannot hang on a "
            "blackholed/offline network)."
        ),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip slow- and network-marked tests unless opted in.

    Mirrors a single rule for both resource-gated marker families: skip by
    default, run when the matching --run-* flag (or an explicit -m <marker>
    selection) is given.  `network` is included so a live-HTTP test cannot hang
    the suite in an offline/sandboxed environment (see module docstring).
    """
    # If the user passed an explicit -m expression, don't interfere — let
    # pytest's own marker filtering handle selection (e.g. -m network runs only
    # network tests; -m "not slow" excludes slow ones).
    mark_expr = config.getoption("-m", default="") or ""
    explicit = mark_expr if isinstance(mark_expr, str) else ""

    skips: dict[str, pytest.MarkDecorator] = {}
    if not (config.getoption("--run-slow", default=False) or _marker_requested(explicit, "slow")):
        skips["slow"] = pytest.mark.skip(
            reason="slow test — run with --run-slow or -m slow"
        )
    if not (
        config.getoption("--run-network", default=False)
        or _marker_requested(explicit, "network")
    ):
        skips["network"] = pytest.mark.skip(
            reason="network test (live HTTP) — run with --run-network or -m network"
        )

    if not skips:
        return
    for item in items:
        for marker_name, skip_marker in skips.items():
            if item.get_closest_marker(marker_name):
                item.add_marker(skip_marker)
