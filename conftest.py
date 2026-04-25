"""Project-level pytest configuration.

Provides automatic skipping of @pytest.mark.slow tests unless the user
explicitly opts in with --run-slow or -m slow.

Usage:
    uv run pytest tests/                        # skips slow tests
    uv run pytest tests/ --run-slow             # includes slow tests
    uv run pytest tests/ -m slow                # runs only slow tests
"""
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


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Also run tests marked @pytest.mark.slow (default: skipped).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip slow-marked tests unless --run-slow or -m slow was specified."""
    run_slow = config.getoption("--run-slow", default=False)
    # If the user passed -m slow (or any explicit marker expression), don't
    # interfere — let pytest's own marker filtering handle selection.
    mark_expr = config.getoption("-m", default="")
    if run_slow or (isinstance(mark_expr, str) and "slow" in mark_expr):
        return

    skip_slow = pytest.mark.skip(reason="slow test — run with --run-slow or -m slow")
    for item in items:
        if item.get_closest_marker("slow"):
            item.add_marker(skip_slow)
