"""Pytest wrapper for the Phase 1.4 curated PEG test suite.

Imports CURATED_CASES from the curated data module (no data duplication) and
generates one parametrized test per case.

Uses peg3 directly — no Stanza/NLP dependency required.

Run:
    cd LawVM && uv run pytest tests/test_peg_curated.py -v
    cd LawVM && uv run pytest tests/test_peg_curated.py -v --co   # collect only
"""
from __future__ import annotations

import pytest

from lawvm.finland.johtolause.compat import parse_clause
from tests.fixtures.fi_curated_cases import CURATED_CASES


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------

def _case_ids():
    return [tc["name"] for tc in CURATED_CASES]


@pytest.mark.parametrize("tc", CURATED_CASES, ids=_case_ids())
def test_peg_case(tc):
    """One test per curated PEG case.

    Cases marked xfail=True are expected to fail (known grammar gaps).
    """
    if tc.get("xfail"):
        pytest.xfail("known failure")

    text = tc["text"]
    expected = tc["expected"]

    result = parse_clause(text)
    actual = [op.code() for op in result.parsed_ops]

    assert actual == expected, (
        f"\nInput:    {text[:120]}\n"
        f"Expected: {expected}\n"
        f"Actual:   {actual}"
    )
