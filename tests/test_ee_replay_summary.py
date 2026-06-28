"""Unit tests for ``EEPitResult.to_replay_summary()`` — the cross-jurisdiction
output contract that projects EE-specific replay results onto the shared
``core/replay_contracts.py`` ``ReplaySummary`` shape."""
from __future__ import annotations

from lawvm.core.replay_contracts import ReplaySummary
from lawvm.estonia.replay import EEPitResult


def test_to_replay_summary_projects_jurisdiction_ee() -> None:
    """``to_replay_summary()`` returns a ``ReplaySummary`` with
    ``jurisdiction="ee"``, correct ``base_id`` / ``as_of``, and the
    core count fields populated from the EE-parallel fields."""
    result = EEPitResult(
        base_id="130042020016",
        as_of="2023-09-23",
        base_title="Test Statute",
        amendments_total=["a", "b", "c"],
        amendments_applied=["a", "b"],
        amendments_skipped=["c"],
        amendments_failed=[],
        n_ops=22,
        oracle_id="120092023003",
        source_basis="pairwise_terviktekst_delta",
        comparison_class="commensurable_delta",
        grupi_id="1030442",
        divergences=[],
        n_mismatch=0,
        n_ops_missing=0,
        n_con_missing=0,
    )
    summary = result.to_replay_summary()

    assert isinstance(summary, ReplaySummary)
    assert summary.jurisdiction == "ee"
    assert summary.base_id == "130042020016"
    assert summary.as_of == "2023-09-23"
    assert summary.title == "Test Statute"
    assert summary.replay_status == "ok"
    assert summary.error is None
    assert summary.oracle_id == "120092023003"
    assert summary.amendment_count == 3
    assert summary.applied_count == 2
    assert summary.skipped_count == 1
    assert summary.failed_count == 0
    assert summary.op_count == 22
    # consistent is None when oracle IRStatute is not set (no comparison happened).
    # oracle_id is set but the actual IRStatute wasn't materialized in this fixture.
    assert summary.consistent is None
    assert summary.divergence_count is None


def test_to_replay_summary_with_error() -> None:
    """When ``error`` is set, ``replay_status="error"`` and ``consistent``
    is None (no oracle comparison happened)."""
    result = EEPitResult(
        base_id="ee/error-test",
        as_of="2024-01-01",
        amendments_total=[],
        amendments_applied=[],
        amendments_skipped=[],
        amendments_failed=["f1", "f2"],
        n_ops=0,
        error="Failed to apply ops: AttributeError",
    )
    summary = result.to_replay_summary()

    assert summary.replay_status == "error"
    assert summary.error == "Failed to apply ops: AttributeError"
    assert summary.failed_count == 2
    assert summary.op_count == 0
    assert summary.consistent is None
    assert summary.divergence_count is None


def test_to_replay_summary_detail_carries_ee_specific_fields() -> None:
    """``detail`` carries EE-specific fields (``source_basis``,
    ``comparison_class``, ``grupi_id``, mismatch counts) so downstream
    tooling can inspect the EE-specific context without parsing the
    EE-specific ``EEPitResult``."""
    result = EEPitResult(
        base_id="ee/test",
        as_of="2024-01-01",
        amendments_total=["x"],
        amendments_applied=["x"],
        amendments_skipped=[],
        amendments_failed=[],
        n_ops=1,
        source_basis="pairwise_terviktekst_delta",
        comparison_class="commensurable_delta",
        grupi_id="12345",
        divergences=[object(), object(), object()],
        n_mismatch=2,
        n_ops_missing=1,
        n_con_missing=3,
        adjudications=[],
    )
    summary = result.to_replay_summary()

    assert summary.detail["source_basis"] == "pairwise_terviktekst_delta"
    assert summary.detail["comparison_class"] == "commensurable_delta"
    assert summary.detail["grupi_id"] == "12345"
    assert summary.detail["n_mismatch"] == 2
    assert summary.detail["n_ops_missing"] == 1
    assert summary.detail["n_con_missing"] == 3
    assert summary.detail["adjudication_count"] == 0
