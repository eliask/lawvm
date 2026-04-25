"""Tests for content-based version drift detection."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from lawvm.core.replay_contracts import ReplayCheckpoint
from lawvm.tools.version_drift import (
    VersionDriftCollector,
    _clean,
    detect_content_version_drift,
)


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_clean_strips_non_alnum():
    assert _clean("Foo 123 §  äöå!") == "foo123äöå"


def test_clean_empty():
    assert _clean("") == ""


# ---------------------------------------------------------------------------
# VersionDriftCollector unit tests
# ---------------------------------------------------------------------------


def _make_checkpoint(
    parent_id: str,
    amendment_id: str,
    step_index: int,
    total_steps: int,
    text: str,
) -> ReplayCheckpoint:
    return ReplayCheckpoint(
        parent_id=parent_id,
        amendment_id=amendment_id,
        step_index=step_index,
        total_steps=total_steps,
        serialize_text=lambda t=text: t,
    )


def test_collector_detects_one_step_behind():
    """When step N-2 matches but final step N-1 doesn't, behind_by is 1."""
    oracle = "the final legal text"
    c = VersionDriftCollector(oracle)

    c(_make_checkpoint("1999/1", "2020/1", 0, 3, "initial text"))
    c(_make_checkpoint("1999/1", "2021/2", 1, 3, "the final legal text"))  # perfect
    c(_make_checkpoint("1999/1", "2022/3", 2, 3, "the final legal text plus new stuff"))

    result = c.result()
    assert result is not None
    assert result["behind_by"] == 1
    assert result["matched_at"] == "2021/2"
    assert result["unapplied"] == ["2022/3"]
    assert result["detection_method"] == "checkpoint"


def test_collector_detects_two_steps_behind():
    """When step 1 of 4 matches, behind_by is 2."""
    oracle = "the correct text"
    c = VersionDriftCollector(oracle)

    c(_make_checkpoint("1999/1", "2019/1", 0, 4, "wrong"))
    c(_make_checkpoint("1999/1", "2020/2", 1, 4, "the correct text"))  # perfect
    c(_make_checkpoint("1999/1", "2021/3", 2, 4, "the correct text amended"))
    c(_make_checkpoint("1999/1", "2022/4", 3, 4, "the correct text amended again"))

    result = c.result()
    assert result is not None
    assert result["behind_by"] == 2
    assert result["matched_at"] == "2020/2"
    assert result["unapplied"] == ["2021/3", "2022/4"]


def test_collector_no_drift_when_final_matches():
    """No drift if the final step is already a perfect match."""
    oracle = "final text"
    c = VersionDriftCollector(oracle)

    c(_make_checkpoint("1999/1", "2020/1", 0, 2, "initial"))
    c(_make_checkpoint("1999/1", "2021/2", 1, 2, "final text"))

    result = c.result()
    assert result is None


def test_collector_no_drift_when_no_intermediate_matches():
    """No drift if no intermediate step produces a perfect match."""
    oracle = "something entirely different"
    c = VersionDriftCollector(oracle)

    c(_make_checkpoint("1999/1", "2020/1", 0, 3, "text a"))
    c(_make_checkpoint("1999/1", "2021/2", 1, 3, "text b"))
    c(_make_checkpoint("1999/1", "2022/3", 2, 3, "text c"))

    result = c.result()
    assert result is None


def test_collector_single_step_returns_none():
    """Can't detect drift with only 1 amendment."""
    oracle = "text"
    c = VersionDriftCollector(oracle)
    c(_make_checkpoint("1999/1", "2020/1", 0, 1, "different"))
    assert c.result() is None


def test_collector_empty_oracle():
    """Empty oracle text produces no drift."""
    c = VersionDriftCollector("")
    c(_make_checkpoint("1999/1", "2020/1", 0, 2, "text"))
    c(_make_checkpoint("1999/1", "2021/2", 1, 2, "more"))
    assert c.result() is None


# ---------------------------------------------------------------------------
# detect_content_version_drift wrapper tests
# ---------------------------------------------------------------------------


def test_perfect_score_returns_none():
    """No drift detection needed when full score is 100%."""
    result = detect_content_version_drift("1999/731", 1.0)
    assert result is None


def test_nearly_perfect_score_returns_none():
    """No drift detection for scores >= 0.9999."""
    result = detect_content_version_drift("1999/731", 0.9999)
    assert result is None


@patch("lawvm.finland.grafter._get_corpus_store")
@patch("lawvm.finland.grafter._resolve_applicable_amendment_records")
def test_single_amendment_returns_none(mock_resolve, mock_corpus):
    """Statutes with 0-1 amendments can't detect drift."""
    mock_corpus.return_value = MagicMock()
    mock_resolve.return_value = (
        [{"statute_id": "2020/100"}],
        None,
        None,
    )
    result = detect_content_version_drift("1999/731", 0.85)
    assert result is None


# ---------------------------------------------------------------------------
# Test the rule in evidence_statute_rules
# ---------------------------------------------------------------------------


def test_rule_content_based_version_drift_emits_claim():
    """The rule emits a claim when drift proof is present."""
    from lawvm.tools.evidence_statute_rules import (
        rule_content_based_version_drift,
    )

    ctx = _make_minimal_ctx(
        content_version_drift={
            "matched_at": "2021/200",
            "behind_by": 1,
            "unapplied": ["2022/300"],
            "scores": {"full": 0.85, "N-1": 1.0},
        }
    )
    claims = rule_content_based_version_drift(ctx)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "oracle_cutoff_version_drift"
    assert claim.claim.support["detection_method"] == "content_based"
    assert claim.claim.support["behind_by"] == 1
    assert claim.claim.support["matched_at"] == "2021/200"


def test_rule_content_based_version_drift_no_drift():
    """The rule returns nothing when no drift proof."""
    from lawvm.tools.evidence_statute_rules import rule_content_based_version_drift

    ctx = _make_minimal_ctx(content_version_drift=None)
    claims = rule_content_based_version_drift(ctx)
    assert len(claims) == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_ctx(content_version_drift=None):
    """Build a minimal StatuteEvidenceContext for testing."""
    from lawvm.tools.evidence_statute_rules import (
        StatuteBisectIndex,
        StatuteEvidenceContext,
    )

    empty_family = MagicMock()
    bisect = StatuteBisectIndex(
        preexisting=empty_family,
        negligible_preexisting_drop=empty_family,
        improved=empty_family,
        repeal_only_without_payload=empty_family,
        payload_prefers_replay=empty_family,
        sparse_elaboration=empty_family,
        deterministic_sparse_oracle_stale=empty_family,
        baseline_same_chapter_drift=empty_family,
        baseline_same_section_structure_drift=empty_family,
    )
    return StatuteEvidenceContext(
        section_results=(),
        stale_sections=(),
        replay_bug_pool=(),
        source_pathologies=(),
        html_error="",
        html_noncommensurable_reason="",
        missing_from_xml=(),
        extra_in_xml=(),
        contingent_effective_sources=(),
        corrigendum_support=(),
        oracle_suspect_detail="",
        oracle_suspect_pending="",
        bisect_index=bisect,
        alternative_replay_matches={},
        oracle_range_matches={},
        cross_chapter_oracle_matches={},
        cross_chapter_replay_matches={},
        selected_replay_divergence_sections=frozenset(),
        selected_section_tiers=frozenset(),
        selected_section_kinds=frozenset(),
        selected_section_outcomes=(),
        has_section_results=False,
        all_sections_match=False,
        apply_section_claims_gate=False,
        section_claims_rows=None,
        content_version_drift=content_version_drift,
    )
