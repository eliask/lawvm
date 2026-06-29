"""Guard-liveness tests for the closed ``MISAPPLY_REASONS`` and
``MISAPPLY_SURFACES`` enums on ``_record_misapplied``.

Per AGENTS.md §2.9: every new guard needs a test that drives a
known-violating input through the **full production path** and asserts
the diagnostic fires — not just a unit test of the guard function.
"""
from __future__ import annotations

from lawvm.finland import corrigendum as corr


def test_record_misapplied_rejects_unrecognised_reason() -> None:
    """An unrecognised reason string emits ``FINLAND.MISAPPLY_REASON_NOT_IN_CLOSED_SET``."""
    corr.clear_misapplied_records()
    corr._record_misapplied(
        op_id="test/closed_enum/reason",
        amendment_id="test",
        statute_id="test",
        reason="not_a_real_reason",
        wrong_text="wrong",
        correct_text="correct",
        surface="upstream_corrigendum",
    )
    records = corr.get_misapplied_records()
    assert len(records) == 1
    assert records[0]["reason"] == "FINLAND.MISAPPLY_REASON_NOT_IN_CLOSED_SET"
    assert records[0]["unrecognised_reason"] == "not_a_real_reason"
    assert records[0]["surface"] == "upstream_corrigendum"
    corr.clear_misapplied_records()


def test_record_misapplied_rejects_unrecognised_surface() -> None:
    """An unrecognised surface string emits ``FINLAND.MISAPPLY_SURFACE_NOT_IN_CLOSED_SET``."""
    corr.clear_misapplied_records()
    corr._record_misapplied(
        op_id="test/closed_enum/surface",
        amendment_id="test",
        statute_id="test",
        reason="miss",
        wrong_text="wrong",
        correct_text="correct",
        surface="not_a_real_surface",
    )
    records = corr.get_misapplied_records()
    assert len(records) == 1
    assert records[0]["reason"] == "FINLAND.MISAPPLY_SURFACE_NOT_IN_CLOSED_SET"
    assert records[0]["unrecognised_surface"] == "not_a_real_surface"
    corr.clear_misapplied_records()


def test_record_misapplied_accepts_known_reason_and_surface() -> None:
    """A known reason + known surface produces the expected record."""
    corr.clear_misapplied_records()
    corr._record_misapplied(
        op_id="test/closed_enum/valid",
        amendment_id="test",
        statute_id="test",
        reason="miss",
        wrong_text="wrong",
        correct_text="correct",
        surface="source_defect",
    )
    records = corr.get_misapplied_records()
    assert len(records) == 1
    assert records[0]["reason"] == "miss"
    assert records[0]["surface"] == "source_defect"
    assert "unrecognised_reason" not in records[0]
    assert "unrecognised_surface" not in records[0]
    corr.clear_misapplied_records()


def test_misapply_reasons_includes_pit_effective_date_gate() -> None:
    """The closed set includes the PIT-effective-date gate reason."""
    assert "FINLAND.CORRIGENDUM_EFFECTIVE_DATE_NOT_YET_REACHED" in corr.MISAPPLY_REASONS


def test_misapply_surfaces_includes_all_carrier_surfaces() -> None:
    """The closed surface set covers all three carrier surfaces."""
    assert "upstream_corrigendum" in corr.MISAPPLY_SURFACES
    assert "retry_overlay" in corr.MISAPPLY_SURFACES
    assert "source_defect" in corr.MISAPPLY_SURFACES
    assert "oracle_override" in corr.MISAPPLY_SURFACES
