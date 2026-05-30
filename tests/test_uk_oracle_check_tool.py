"""Tests for lawvm -j uk oracle-check / classify / diff commands.

Unit tests (no archive required):
  - _classify_divergences produces correct bucket assignments
  - All bucket keys are present even when empty
  - manual-frontier rejections promote only_oracle EIDs to manual_frontier
  - repeal-not-warranted diagnostics keep only_replay EIDs in oracle_suspect

Integration tests (require data/uk_legislation.farchive):
  - oracle_check_uk_statute returns non-empty string with expected headers
  - Three-bucket summary line is present
  - Similarity + EID counts appear

Guard tests (no archive required):
  - lawvm -j uk audit-trail exits 2
  - lawvm -j uk lower-audit exits 2
  - lawvm -j uk step-attribution exits 2
  - lawvm -j uk blame exits 2
  - lawvm -j uk dump exits 2
  - lawvm -j uk source-dump exits 2
  - lawvm -j uk inspect-amendment exits 2
  - lawvm -j uk snapshot-debug exits 2
  - lawvm -j uk product-debug exits 2
  - lawvm -j uk replay-debug exits 2
  - lawvm -j uk replay-inspect exits 2
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lawvm.tools.uk_oracle_check import (
    _classify_divergences,
    _grounding_collateral_eids,
    _is_manual_frontier_rule,
    _REPEAL_NOT_WARRANTED_RULE_ID,
)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"

# ---------------------------------------------------------------------------
# Unit: _is_manual_frontier_rule
# ---------------------------------------------------------------------------


def test_is_manual_frontier_rule_prefix_match() -> None:
    assert _is_manual_frontier_rule("uk_manual_frontier_commencement_effect_out_of_scope")
    assert _is_manual_frontier_rule("uk_manual_frontier_appropriate_place_candidate")


def test_is_manual_frontier_rule_non_mf() -> None:
    assert not _is_manual_frontier_rule("uk_effect_lowering_no_supported_action_rejected")
    assert not _is_manual_frontier_rule("")
    assert not _is_manual_frontier_rule(_REPEAL_NOT_WARRANTED_RULE_ID)


# ---------------------------------------------------------------------------
# Unit: _classify_divergences — basic buckets
# ---------------------------------------------------------------------------


def test_classify_divergences_empty() -> None:
    result = _classify_divergences(
        only_replay=set(),
        only_oracle=set(),
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert set(result) == {"deterministic_gap", "manual_frontier", "oracle_suspect", "text_diff"}
    for bucket in result.values():
        assert bucket == []


def test_classify_divergences_only_oracle_goes_to_deterministic_gap() -> None:
    result = _classify_divergences(
        only_replay=set(),
        only_oracle={"section-5"},
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert "section-5" in result["deterministic_gap"]
    assert "section-5" not in result["manual_frontier"]
    assert "section-5" not in result["oracle_suspect"]


def test_classify_divergences_only_replay_goes_to_oracle_suspect() -> None:
    result = _classify_divergences(
        only_replay={"section-99"},
        only_oracle=set(),
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert "section-99" in result["oracle_suspect"]
    assert "section-99" not in result["deterministic_gap"]


def test_classify_divergences_text_diff_bucket() -> None:
    result = _classify_divergences(
        only_replay=set(),
        only_oracle=set(),
        text_diff={"section-7"},
        lowering_rejections=[],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert "section-7" in result["text_diff"]


def test_classify_divergences_mf_rejection_promotes_to_manual_frontier() -> None:
    """An only_oracle EID whose affected_provisions match a manual-frontier
    rejection goes to manual_frontier, not deterministic_gap."""
    result = _classify_divergences(
        only_replay=set(),
        only_oracle={"section-3"},
        text_diff=set(),
        lowering_rejections=[
            {
                "rule_id": "uk_manual_frontier_appropriate_place_candidate",
                "affected_provisions": "section-3",
                "effect_type": "insertion",
            }
        ],
        effect_diagnostics=[],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert "section-3" in result["manual_frontier"]
    assert "section-3" not in result["deterministic_gap"]


def test_classify_divergences_repeal_not_warranted_keeps_in_oracle_suspect() -> None:
    """An only_replay EID with a repeal-not-warranted diagnostic stays in
    oracle_suspect (the standard bucket for only_replay EIDs)."""
    result = _classify_divergences(
        only_replay={"section-10"},
        only_oracle=set(),
        text_diff=set(),
        lowering_rejections=[],
        effect_diagnostics=[
            {
                "rule_id": _REPEAL_NOT_WARRANTED_RULE_ID,
                "affected_provisions": "section-10",
            }
        ],
        effect_feed_parse_rejections=[],
        authority_rejections=[],
    )
    assert "section-10" in result["oracle_suspect"]
    assert "section-10" not in result["deterministic_gap"]


# ---------------------------------------------------------------------------
# Integration: oracle_check_uk_statute with real archive
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_oracle_check_uk_statute_returns_string() -> None:
    from lawvm.tools.uk_oracle_check import oracle_check_uk_statute

    result = oracle_check_uk_statute("ukpga/1978/30", db_path=_DB_PATH)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_oracle_check_uk_statute_has_required_headers() -> None:
    from lawvm.tools.uk_oracle_check import oracle_check_uk_statute

    result = oracle_check_uk_statute("ukpga/1978/30", db_path=_DB_PATH)
    assert "ukpga/1978/30" in result
    assert "UK oracle-check" in result
    assert "DIVERGENCE BUCKET SUMMARY" in result
    assert "deterministic_gap" in result
    assert "manual_frontier" in result
    assert "oracle_suspect" in result
    assert "Similarity:" in result
    assert "Similarity excluding grounding collateral:" in result
    assert "Mutation boundary:" in result


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_oracle_check_uk_statute_missing_archive_returns_error_string() -> None:
    from lawvm.tools.uk_oracle_check import oracle_check_uk_statute

    result = oracle_check_uk_statute("ukpga/1978/30", db_path=Path("/nonexistent/db"))
    assert "ERROR" in result
    assert "Archive not found" in result


# ---------------------------------------------------------------------------
# Guard tests: FI-only commands must exit 2 for -j uk
# ---------------------------------------------------------------------------

_GUARD_COMMANDS: list[tuple[str, list[str]]] = [
    ("audit-trail", ["ukpga/1978/30"]),
    ("lower-audit", ["ukpga/1978/30"]),
    ("step-attribution", ["ukpga/1978/30"]),
    ("blame", ["ukpga/1978/30"]),
    ("dump", ["ukpga/1978/30"]),
    ("source-dump", ["ukpga/1978/30"]),
    # inspect-amendment, snapshot-debug, product-debug require --source
    ("inspect-amendment", ["ukpga/1978/30", "--source", "ukpga/2012/10"]),
    ("snapshot-debug", ["ukpga/1978/30", "--source", "ukpga/2012/10"]),
    ("product-debug", ["ukpga/1978/30", "--source", "ukpga/2012/10"]),
    ("replay-debug", ["ukpga/1978/30"]),
    # replay-inspect requires --section
    ("replay-inspect", ["ukpga/1978/30", "--section", "1"]),
]


@pytest.mark.parametrize("cmd,extra_args", _GUARD_COMMANDS)
def test_uk_guard_exits_2(cmd: str, extra_args: list[str]) -> None:
    """Each FI-only command must exit with code 2 when -j uk is given."""
    proc = subprocess.run(
        [sys.executable, "-m", "lawvm.tools.cli", "-j", "uk", cmd, *extra_args],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, (
        f"Expected exit code 2 for 'lawvm -j uk {cmd}', got {proc.returncode}. "
        f"stderr: {proc.stderr!r}"
    )
    assert "does not yet support -j uk" in proc.stderr, (
        f"Expected 'does not yet support -j uk' in stderr for 'lawvm -j uk {cmd}'. "
        f"stderr: {proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Unit: _grounding_collateral_eids
# ---------------------------------------------------------------------------


def test_grounding_collateral_flags_minted_not_in_oracle() -> None:
    replayed = {"section-1", "annex-I-paragraph-1", "annex-I-paragraph-2"}
    oracle = {"section-1"}
    events = [
        {"match_method": "local_fallback", "after_eid": "annex-I-paragraph-1"},
        {"match_method": "local_fallback", "after_eid": "annex-I-paragraph-2"},
    ]
    assert _grounding_collateral_eids(replayed, oracle, events) == [
        "annex-I-paragraph-1",
        "annex-I-paragraph-2",
    ]


def test_grounding_collateral_excludes_minted_present_in_oracle() -> None:
    replayed = {"section-1A"}
    oracle = {"section-1a"}  # oracle has it (case-insensitive) -> not collateral
    events = [{"match_method": "local_fallback", "after_eid": "section-1A"}]
    assert _grounding_collateral_eids(replayed, oracle, events) == []


def test_grounding_collateral_ignores_non_local_fallback_methods() -> None:
    replayed = {"section-9"}
    oracle: set[str] = set()
    # a fuzzy/flat match aligns to a real oracle id; not minting -> not collateral
    events = [
        {"match_method": "fuzzy", "after_eid": "section-9"},
        {"match_method": "flat", "after_eid": "section-9"},
    ]
    assert _grounding_collateral_eids(replayed, oracle, events) == []


def test_grounding_collateral_empty_when_no_events() -> None:
    assert _grounding_collateral_eids({"a", "b"}, set(), []) == []


def test_grounding_collateral_score_excludes_minted_replay_eids() -> None:
    from lawvm.uk_legislation.grounding_collateral import (
        score_with_grounding_collateral_excluded,
    )

    score = score_with_grounding_collateral_excluded(
        {"section-1", "annex-I-paragraph-1", "annex-I-paragraph-2"},
        {"section-1"},
        [
            {"match_method": "local_fallback", "after_eid": "annex-I-paragraph-1"},
            {"match_method": "local_fallback", "after_eid": "annex-I-paragraph-2"},
        ],
    )

    assert round(score.raw_similarity, 3) == 0.333
    assert score.collateral_excluded_similarity == 1.0
    assert score.collateral_eids == (
        "annex-I-paragraph-1",
        "annex-I-paragraph-2",
    )
