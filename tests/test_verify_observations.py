"""Tests for verify_observations — PhaseResult signal validation.

These tests exercise the observation-kind checker without touching the corpus.
They construct PhaseResult objects directly and call the relevant
check/validation logic.

Run:
    uv run pytest tests/test_verify_observations.py -v
"""
from __future__ import annotations

import pytest

from lawvm.core.phase_result import Finding, Observation, PhaseResult
from lawvm.core.observation_registry import finding_codes_by_role


OBSERVATION_CODES = set(finding_codes_by_role("observation"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(kind: str, stage: str = "test_stage") -> Observation:
    return Observation(kind=kind, stage=stage, detail={})


def _obs_finding(kind: str, stage: str = "test_stage") -> Finding:
    return Finding(kind=kind, role="observation", stage=stage, detail={}, blocking=False)


def _check_observations(phase_result: PhaseResult) -> dict:
    """Mirror the core logic from verify_observations without running replay.

    Returns:
        {
            "unregistered": list[str],  # kinds not in the observation-role registry
            "total_obs": int,
            "distinct_kinds": set,
        }
    """
    unregistered = []
    distinct_kinds: set = set()

    observations = tuple(obs for obs in phase_result.findings() if obs.role == "observation")
    for obs in observations:
        distinct_kinds.add(obs.kind)
        if obs.kind not in OBSERVATION_CODES:
            unregistered.append(obs.kind)

    return {
        "unregistered": unregistered,
        "total_obs": len(observations),
        "distinct_kinds": distinct_kinds,
    }


# ---------------------------------------------------------------------------
# Observation kind registration checks
# ---------------------------------------------------------------------------

def test_registered_kind_passes() -> None:
    """A kind present in the observation-role registry must not appear in unregistered."""
    registered_kind = next(iter(OBSERVATION_CODES))
    pr = PhaseResult(output=None, findings=(_obs_finding(registered_kind),))
    result = _check_observations(pr)
    assert result["unregistered"] == []


def test_unregistered_kind_flagged() -> None:
    """Ungoverned observation rows are rejected at construction time."""
    with pytest.raises(ValueError, match="not registered"):
        _obs_finding("totally_made_up_kind_xyz")


def test_mixed_registered_and_unregistered() -> None:
    """Mixed governed and ungoverned rows are no longer constructible."""
    registered_kind = next(iter(OBSERVATION_CODES))
    assert _obs_finding(registered_kind).kind == registered_kind
    with pytest.raises(ValueError, match="not registered"):
        _obs_finding("phantom_kind_abc")


def test_empty_phase_result_no_issues() -> None:
    """A PhaseResult with no observations reports zero issues."""
    pr = PhaseResult(output=None)
    result = _check_observations(pr)
    assert result["unregistered"] == []
    assert result["total_obs"] == 0


def test_multiple_unregistered_kinds_all_flagged() -> None:
    """Ungoverned observation rows fail before any audit pass is needed."""
    with pytest.raises(ValueError, match="not registered"):
        _obs_finding("ghost_kind_1")
    with pytest.raises(ValueError, match="not registered"):
        _obs_finding("ghost_kind_2")


def test_distinct_kinds_counted_correctly() -> None:
    """distinct_kinds counts unique governed kind strings, not total observation count."""
    registered_kind = next(iter(OBSERVATION_CODES))
    pr = PhaseResult(
        output=None,
        findings=(
            _obs_finding(registered_kind),
            _obs_finding(registered_kind),
        ),
    )
    result = _check_observations(pr)
    assert len(result["distinct_kinds"]) == 1
    assert result["total_obs"] == 2


# ---------------------------------------------------------------------------
# Interaction with PhaseResult.merge
# ---------------------------------------------------------------------------

def test_observations_from_merged_phase_results_all_checked() -> None:
    """Merged governed observation rows remain visible to observation audits."""
    registered_kind = next(iter(OBSERVATION_CODES))
    pr_a = PhaseResult(output="a", findings=(_obs_finding(registered_kind),))
    pr_b = PhaseResult(
        output="b",
        findings=(_obs_finding(registered_kind),),
    )
    merged = pr_a.merge(pr_b)
    result = _check_observations(merged)
    assert result["total_obs"] == 2
    assert result["unregistered"] == []
