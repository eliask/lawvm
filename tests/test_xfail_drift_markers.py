"""Guard-liveness meta-test for ``tests/conftest.py`` XFAIL drift markers.

F3 from the guard-liveness review (iter2 W6): ``tests/conftest.py`` marks 22
prefixes with ``pytest.mark.xfail(strict=False)`` via
``_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES`` (the "still drifting as of
2026-06-27" tier). ``strict=False`` means an XPASS is silently allowed —
unlike ``strict=True``, pytest treats an XPASS as a regular pass and does
not fail CI.  That is the right choice for keeping re-adjudication work
unblocked (a strict-True marker would break CI the moment the underlying
divergence closes).  But it has a silent-drift failure mode: if a future
fix closes the drift for one prefix and the operator forgets to remove
that prefix from ``_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES`` (i.e.
does not perform the tier-1 "drift closed -> remove marker entirely"
protocol), CI will not flag the now-stale drift bookkeeping.

This meta-test catches that drift: it runs every drift prefix as a single
pytest invocation and asserts the outcome is ``xfailed`` (NOT ``xpassed``).
If any prefix XPASSes, the meta-test fails with the offending node ids,
prompting the operator to remove the prefix from the drift list (see
``_POST_GRAMMAR_MERGE_REPLAY_PIN_REMOVED_PREFIXES`` for the tier-1
precedent of "drift closed, marker removed entirely, not switched to
strict=True").

Marked ``@pytest.mark.slow``: each prefix is a corpus replay run via
subprocess (the worst-case item is ``letter_suffix_insert_skips_large_single_chapter_recodification_batch``
which takes seconds-to-minutes alone).  The meta-test re-runs 22 such
prefixes in one pytest subprocess (xdist default is 4 workers); it is
skipped by default and runs only with ``--run-slow`` or via the periodic
drift-check surface.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFTEST_PATH = REPO_ROOT / "tests" / "conftest.py"


# ``python -m pytest`` summary-line tokens (case-insensitive on the pytest
# summary format; both "XPASSED" and "xpassed" have been observed).
_XPASS_SUMMARY_RE = re.compile(r"\b(\d+)\s+xpassed\b", re.IGNORECASE)
_XFAIL_SUMMARY_RE = re.compile(r"\b(\d+)\s+xfailed\b", re.IGNORECASE)

# With ``-rX``, pytest prints an ``XPASS <node>`` line per xpassed test in the
# short summary section.  Capture the node for the diagnostic message.
_XPASS_LINE_RE = re.compile(r"^XPASS\s+(?P<node>\S+)\s*$")


def _load_drift_prefixes() -> tuple[str, ...]:
    """Import ``tests/conftest.py`` and read the drift-prefix tuple.

    ``importlib.util`` keeps us decoupled from pytest's conftest-load
    semantics (which would re-trigger collection).  Reading the in-tree
    value guarantees we are asserting against the actual list, not a stale
    copy in this test file.
    """
    spec = importlib.util.spec_from_file_location(
        "tests_conftest_drift_markers_for_meta",
        CONFTEST_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load conftest: {CONFTEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module._POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES)


def _collect_xpass_nodes(combined_output: str) -> list[str]:
    """Extract the ``XPASS <node>`` lines emitted by ``pytest -rX``."""
    nodes: list[str] = []
    for line in combined_output.splitlines():
        m = _XPASS_LINE_RE.match(line.strip())
        if m:
            nodes.append(m.group("node"))
    return nodes


@pytest.mark.slow
def test_each_drift_prefix_still_xfails_without_xpass() -> None:
    """Every prefix in ``_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES`` must XFAIL.

    Failure modes surfaced:

    * **XPASS drift** (the F3 guard-liveness target): a prefix XPASSed but
      was not removed from the drift list.  Fail with the offending node
      ids; the operator applies tier-1 (remove the prefix from
      ``_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES``).
    * **Missing-XFAIL-count sanity**: pytest subprocess reports fewer
      ``xfailed`` outcomes than ``len(drift_prefixes)``.  A prefix may
      have errored during collection (file gone, import error) or been
      deselected (marker changed).  Surface the actual pytest summary line.
    """
    drift_prefixes = _load_drift_prefixes()
    assert drift_prefixes, (
        "expected non-empty _POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES in tests/conftest.py"
    )

    # Use the same Python interpreter that's running this test.  Under
    # ``uv run pytest``, ``sys.executable`` is the uv-managed venv python;
    # ``python -m pytest`` inherits the working directory + conftest.py.
    cmd = [
        sys.executable, "-m", "pytest",
        "--no-header",
        "--tb=no",
        "-q",
        "-rX",  # report XPASSed tests by name in the short summary
        # Per ``conftest.py`` at the project root, slow tests are skipped by
        # default unless ``--run-slow`` is passed. Several drift prefixes in
        # ``_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES`` are themselves
        # ``@slow`` corpus replays (e.g. ``test_letter_suffix_insert_skips_*``
        # per the conftest header) — without ``--run-slow`` they would be
        # SKIPPED in the subprocess, dropping the XFAIL count below
        # ``len(drift_prefixes)`` and failing the sanity guard. The outer
        # meta-test is already ``@pytest.mark.slow`` (opt-in only), so passing
        # ``--run-slow`` to the subprocess is consistent with the user's
        # explicit opt-in to slow-test execution.
        "--run-slow",
        *drift_prefixes,
    ]
    # The pytest subprocess inherits the project's `addopts = "-n 4 --tb=short"`
    # (from pyproject.toml); the ``--tb=no -q`` we pass here overrides ``--tb``
    # for our run. Pass through whatever xdist defaults are configured.
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    combined = result.stdout + "\n" + result.stderr

    # Primary signal: the explicit XPASS list emitted by ``-rX``.
    xpass_nodes = _collect_xpass_nodes(combined)
    if xpass_nodes:
        summary_line = ""
        if result.stdout.strip():
            summary_line = result.stdout.strip().splitlines()[-1]
        msg_lines = [
            "",
            "Drift prefixes XPASSed without being removed from",
            "_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES in tests/conftest.py:",
            "",
        ]
        for node in xpass_nodes:
            msg_lines.append(f"  - {node}")
        msg_lines.extend([
            "",
            "Per conftest.py tier-1 protocol (precedent:",
            "_POST_GRAMMAR_MERGE_REPLAY_PIN_REMOVED_PREFIXES), remove the XPASSing",
            "prefix from _POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES so it",
            "becomes a regular passing test rather than XPASS-with-strict=False.",
            "Do NOT switch to strict=True — that would make CI fail on every",
            "XPASS instead of prompting a marker-removal (per conftest.py header).",
            "",
            f"pytest subprocess exit code: {result.returncode}",
            f"pytest summary: {summary_line or '(no summary line found)'}",
        ])
        pytest.fail("\n".join(msg_lines))

    # Sanity check: every drift prefix should have actually run and XFAILed.
    # If fewer than ``len(drift_prefixes)`` XFAIL outcomes appear, a prefix
    # may have errored during collection or been deselected (e.g. by a
    # ``-m`` filter inherited from the project's ``addopts``).
    xfail_match = _XFAIL_SUMMARY_RE.search(result.stdout)
    xfail_count = int(xfail_match.group(1)) if xfail_match else 0
    summary_tail = ""
    if result.stdout.strip():
        summary_tail = result.stdout.strip().splitlines()[-1]
    assert xfail_count == len(drift_prefixes), (
        f"Expected {len(drift_prefixes)} XFAIL outcomes from pytest subprocess, "
        f"got {xfail_count}. A drift prefix may have errored during collection, "
        f"been deselected by a marker filter, or no longer exists. Run\n"
        f"  uv run pytest {' '.join(drift_prefixes)} -v --tb=short\n"
        f"to inspect the actual outcomes.\n"
        f"pytest subprocess exit code: {result.returncode}\n"
        f"pytest summary: {summary_tail or '(no summary line found)'}"
    )
