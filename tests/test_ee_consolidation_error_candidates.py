"""Tests for the EE consolidation-error candidate surface.

Pure synthetic/fixture tests: a fake replay result (divergences + compiled ops)
and a fake residual summary are injected, so the default-run test never touches
the Riigi Teataja archive or `replay_ee_to_pit`. This exercises strong-vs-triage
tiering, evidence-snippet shape, ranking, determinism, and the invariant that an
unadjudicated divergence is never placed in the strong tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, cast

from lawvm.estonia.consolidation_error_candidates import (
    CONSOLIDATION_SIDE_ERROR_BUCKETS,
    UNADJUDICATED_TRIAGE_BUCKET,
    consolidation_error_candidates,
    report_to_jsonable,
)
from lawvm.estonia.residual_inventory import EEResidualBucket, EEResidualRecord
from lawvm.estonia.residual_reporting import EEResidualSummary


# ---------------------------------------------------------------------------
# Lightweight fakes (no archive, no replay)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeAddress:
    path: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _FakeDivergence:
    address: _FakeAddress
    divergence_type: str
    ops_text: Optional[str]
    consolidated_text: Optional[str]


@dataclass(frozen=True)
class _FakeSource:
    statute_id: str
    title: str = ""


@dataclass(frozen=True)
class _FakeOp:
    op_id: str
    sequence: int
    target: _FakeAddress
    witness_rule_id: Optional[str] = None
    source: Optional[_FakeSource] = None


@dataclass
class _FakeResult:
    base_id: str
    oracle_id: str
    base_title: str = ""
    comparison_class: str = ""
    divergences: list = field(default_factory=list)
    compiled_ops: tuple = ()
    applied_snapshot_ops: tuple = ()


def _addr(spec: str) -> _FakeAddress:
    path: tuple[tuple[str, str], ...] = tuple(
        cast("tuple[str, str]", tuple(seg.split(":", 1))) for seg in spec.split("/")
    )
    return _FakeAddress(path=path)


def _record(address: str, bucket: str) -> EEResidualRecord:
    return EEResidualRecord(
        address=address,
        bucket=cast("EEResidualBucket", bucket),
        evidence=f"evidence for {address}",
    )


def _summary(records: dict[str, EEResidualRecord]) -> EEResidualSummary:
    return EEResidualSummary(
        base_id="BASE",
        oracle_id="ORACLE",
        statute_title="Test Statute",
        comparison_class="commensurable_delta",
        residual_count=len(records),
        bucket_counts={},
        matched_current_divergence_count=len(records),
        matched_current_bucket_counts={},
        unknown_current_divergence_count=0,
        unknown_current_divergence_addresses=(),
        record_by_address=dict(records),
    )


def _base_fixture() -> tuple[_FakeResult, EEResidualSummary]:
    """A fixture mixing a strong-tier and a triage-tier divergence."""
    drift_div = _FakeDivergence(
        address=_addr("section:5"),
        divergence_type="MISMATCH",
        ops_text="replay text for section 5 " + "x" * 400,
        consolidated_text="stale consolidation text for section 5",
    )
    triage_div = _FakeDivergence(
        address=_addr("section:9/subsection:2"),
        divergence_type="OPS_MISSING",
        ops_text=None,
        consolidated_text="oracle-only text under section 9",
    )
    result = _FakeResult(
        base_id="BASE",
        oracle_id="ORACLE",
        base_title="Test Statute",
        comparison_class="commensurable_delta",
        divergences=[drift_div, triage_div],
        compiled_ops=(
            _FakeOp(
                op_id="op-1",
                sequence=10,
                target=_addr("section:5"),
                witness_rule_id="ee_drift_rule",
                source=_FakeSource(statute_id="AMEND_ACT_1", title="Amending Act One"),
            ),
        ),
    )
    summary = _summary({"section:5": _record("section:5", "source_oracle_drift")})
    return result, summary


def _run(result: _FakeResult, summary: EEResidualSummary):
    return consolidation_error_candidates(
        base_id=result.base_id,
        as_of="2024-01-01",
        oracle_id=result.oracle_id,
        result=result,
        residual_summary=summary,
    )


# ---------------------------------------------------------------------------
# Tiering
# ---------------------------------------------------------------------------


def test_strong_tier_holds_adjudicated_consolidation_side_error() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)

    assert report.strong_count == 1
    strong = report.strong_candidates[0]
    assert strong.tier == "strong"
    assert strong.address == "section:5"
    assert strong.residual_bucket in CONSOLIDATION_SIDE_ERROR_BUCKETS
    assert strong.residual_bucket == "source_oracle_drift"
    assert strong.residual_evidence == "evidence for section:5"


def test_unadjudicated_divergence_is_triage_not_strong() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)

    # The triage divergence must NOT appear in the strong tier.
    assert all(c.address != "section:9/subsection:2" for c in report.strong_candidates)

    assert report.triage_count == 1
    triage = report.triage_candidates[0]
    assert triage.tier == "triage"
    assert triage.address == "section:9/subsection:2"
    assert triage.residual_bucket == UNADJUDICATED_TRIAGE_BUCKET
    assert triage.residual_evidence is None


def test_non_consolidation_side_adjudicated_bucket_is_excluded() -> None:
    """An adjudicated but non-consolidation-side bucket is neither strong nor triage."""
    div = _FakeDivergence(
        address=_addr("section:7"),
        divergence_type="MISMATCH",
        ops_text="replay",
        consolidated_text="oracle",
    )
    result = _FakeResult(base_id="BASE", oracle_id="ORACLE", divergences=[div])
    summary = _summary({"section:7": _record("section:7", "replay_bug")})

    report = _run(result, summary)
    assert report.strong_count == 0
    assert report.triage_count == 0  # has a record, so not triage; not consolidation-side, so not strong


def test_oracle_correction_notice_is_strong() -> None:
    div = _FakeDivergence(
        address=_addr("section:3"),
        divergence_type="MISMATCH",
        ops_text="replay",
        consolidated_text="oracle",
    )
    result = _FakeResult(base_id="BASE", oracle_id="ORACLE", divergences=[div])
    summary = _summary({"section:3": _record("section:3", "oracle_correction_notice")})

    report = _run(result, summary)
    assert report.strong_count == 1
    assert report.strong_candidates[0].residual_bucket == "oracle_correction_notice"


# ---------------------------------------------------------------------------
# Attribution + evidence
# ---------------------------------------------------------------------------


def test_witness_rule_and_amending_act_attribution() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)

    strong = report.strong_candidates[0]
    assert strong.witness_rule_id == "ee_drift_rule"
    assert strong.amending_act == "AMEND_ACT_1"
    assert strong.amending_act_title == "Amending Act One"


def test_attribution_falls_back_to_ancestor_op() -> None:
    """A divergence at a descendant address attributes to the nearest ancestor op."""
    div = _FakeDivergence(
        address=_addr("section:5/subsection:1/item:3"),
        divergence_type="MISMATCH",
        ops_text="replay",
        consolidated_text="oracle",
    )
    result = _FakeResult(
        base_id="BASE",
        oracle_id="ORACLE",
        divergences=[div],
        compiled_ops=(
            _FakeOp(
                op_id="op-1",
                sequence=1,
                target=_addr("section:5"),
                witness_rule_id="ancestor_rule",
                source=_FakeSource(statute_id="ACT_X"),
            ),
        ),
    )
    summary = _summary(
        {"section:5/subsection:1/item:3": _record("section:5/subsection:1/item:3", "source_oracle_drift")}
    )
    report = _run(result, summary)
    strong = report.strong_candidates[0]
    assert strong.witness_rule_id == "ancestor_rule"
    assert strong.amending_act == "ACT_X"


def test_evidence_snippet_shape_is_bounded_and_single_line() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)

    strong = report.strong_candidates[0]
    assert "\n" not in strong.evidence.replay_snippet
    assert len(strong.evidence.replay_snippet) <= 200
    # Long replay text is truncated with an ellipsis.
    assert strong.evidence.replay_snippet.endswith("…")
    # Full text is retained on the carrier.
    assert strong.evidence.replay_text is not None and len(strong.evidence.replay_text) > 200
    # An OPS_MISSING (None replay) triage candidate yields an empty replay snippet.
    triage = report.triage_candidates[0]
    assert triage.evidence.replay_snippet == ""
    assert triage.evidence.consolidated_snippet


# ---------------------------------------------------------------------------
# Ranking + determinism
# ---------------------------------------------------------------------------


def test_strong_candidates_rank_before_triage() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)
    ranked = report.ranked_candidates()
    assert [c.tier for c in ranked] == ["strong", "triage"]


def test_ranking_is_stable_by_address_within_tier() -> None:
    divs = [
        _FakeDivergence(_addr("section:20"), "MISMATCH", "r20", "o20"),
        _FakeDivergence(_addr("section:3"), "MISMATCH", "r3", "o3"),
        _FakeDivergence(_addr("section:10"), "MISMATCH", "r10", "o10"),
    ]
    result = _FakeResult(base_id="BASE", oracle_id="ORACLE", divergences=divs)
    summary = _summary(
        {
            "section:20": _record("section:20", "source_oracle_drift"),
            "section:3": _record("section:3", "source_oracle_drift"),
            "section:10": _record("section:10", "source_oracle_drift"),
        }
    )
    report = _run(result, summary)
    addresses = [c.address for c in report.strong_candidates]
    assert addresses == ["section:10", "section:20", "section:3"]


def test_determinism_two_runs_identical_json() -> None:
    result, summary = _base_fixture()
    first = report_to_jsonable(_run(result, summary))
    second = report_to_jsonable(_run(result, summary))
    assert first == second


def test_report_has_adjudication_flag() -> None:
    result, summary = _base_fixture()
    report = _run(result, summary)
    assert report.has_residual_adjudication is True

    # Without a summary, everything is triage and the flag is False.
    no_adj = consolidation_error_candidates(
        base_id=result.base_id,
        as_of="2024-01-01",
        oracle_id=result.oracle_id,
        result=result,
        residual_summary=None,
    )
    assert no_adj.has_residual_adjudication is False
    assert no_adj.strong_count == 0
    assert no_adj.triage_count == 2
