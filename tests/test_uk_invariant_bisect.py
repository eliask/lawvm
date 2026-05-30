"""Tests for invariant-bisect -j uk (UK path in build_uk_invariant_bisect_bundle).

Unit tests (no archive required):
  - dispatch: unsupported jurisdiction raises SystemExit(2)
  - bundle schema: required keys are present

Integration tests (require data/uk_legislation.farchive):
  - duplicate_label on ukpga/1978/30: no violations found, bundle is well-formed
  - all_tree on ukpga/1978/30: pre-window violations from enacted base are detected;
    initial_clean is False; monotone_failure is True
  - after/before window bounds are respected (count < total_in_chain)
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"

_BUNDLE_REQUIRED_KEYS = {
    "statute_id",
    "jurisdiction",
    "target_path",
    "detector",
    "scan_window",
    "initial_clean",
    "initial_violations",
    "first_bad_amendment",
    "first_clean_amendment",
    "monotone_failure",
    "transient_failure",
    "failure_count",
    "total_scanned",
    "first_bad_violations",
    "steps",
}


# ---------------------------------------------------------------------------
# Unit: unsupported jurisdiction dispatch
# ---------------------------------------------------------------------------


def test_main_unsupported_jurisdiction_raises_systemexit() -> None:
    """invariant-bisect -j no should raise SystemExit(2) (no support yet)."""
    import argparse

    from lawvm.tools.invariant_bisect import main

    args = argparse.Namespace(
        jurisdiction="no",
        statute_id="nlo/2000/1",
        target="",
        detector="duplicate_label",
        mode="legal_pit",
        after="",
        before="",
        json=False,
        verbose=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        main(args)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Unit: missing archive raises SystemExit
# ---------------------------------------------------------------------------


def test_build_uk_invariant_bisect_bundle_missing_archive_raises() -> None:
    """SystemExit when uk_legislation.farchive does not exist."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    with pytest.raises(SystemExit):
        build_uk_invariant_bisect_bundle(
            "ukpga/1978/30",
            db_path=Path("/nonexistent/path/uk_legislation.farchive"),
        )


# ---------------------------------------------------------------------------
# Integration: ukpga/1978/30, duplicate_label
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
def test_uk_invariant_bisect_duplicate_label_no_violations_ukpga_1978_30() -> None:
    """ukpga/1978/30 has no duplicate_label violations across its amendment chain."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="duplicate_label",
        db_path=_DB_PATH,
    )
    # Schema check
    assert _BUNDLE_REQUIRED_KEYS <= set(bundle), (
        f"bundle missing keys: {_BUNDLE_REQUIRED_KEYS - set(bundle)}"
    )
    assert bundle["jurisdiction"] == "uk"
    assert bundle["statute_id"] == "ukpga/1978/30"
    assert bundle["detector"] == "duplicate_label"
    # No violations found
    assert bundle["initial_clean"] is True
    assert bundle["failure_count"] == 0
    assert bundle["first_bad_amendment"] == ""
    assert bundle["monotone_failure"] is False
    assert bundle["transient_failure"] is False
    # All 46 source amendments scanned
    assert bundle["total_scanned"] == bundle["scan_window"]["count"]
    assert bundle["scan_window"]["total_in_chain"] > 0


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
def test_uk_invariant_bisect_all_tree_base_violations_ukpga_1978_30() -> None:
    """ukpga/1978/30 all_tree: enacted base has illegal_edge violations (monotone)."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="all_tree",
        db_path=_DB_PATH,
    )
    assert bundle["jurisdiction"] == "uk"
    # The enacted base IR already contains illegal_edge violations
    assert bundle["initial_clean"] is False
    assert len(bundle["initial_violations"]) > 0
    # Violations persist monotonically through all amendments
    assert bundle["monotone_failure"] is True
    assert bundle["failure_count"] == bundle["total_scanned"]


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
def test_uk_invariant_bisect_window_bounds_respected_ukpga_1978_30() -> None:
    """--after / --before window narrows the scan to a subset of amendments."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    # First get the full chain to know amendment IDs
    full_bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="duplicate_label",
        db_path=_DB_PATH,
    )
    total = full_bundle["scan_window"]["total_in_chain"]
    steps = full_bundle["steps"]
    if len(steps) < 3:
        pytest.skip("statute has too few amendments to test window bounds")

    # Take first and last amendments from the scan
    first_mid = steps[0]["source_id"]
    last_mid = steps[-1]["source_id"]

    # Scan from after the first amendment to before the last
    windowed = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="duplicate_label",
        after_mid=first_mid,
        before_mid=last_mid,
        db_path=_DB_PATH,
    )
    assert windowed["scan_window"]["count"] < total
    assert windowed["scan_window"]["count"] == len(windowed["steps"])
    assert windowed["scan_window"]["total_in_chain"] == total


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
def test_uk_invariant_bisect_steps_list_structure_ukpga_1978_30() -> None:
    """Every step in the steps list has required keys."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="duplicate_label",
        db_path=_DB_PATH,
    )
    for step in bundle["steps"]:
        assert "source_id" in step
        assert "clean" in step
        assert "violation_count" in step
        assert "violations" in step
        assert isinstance(step["violations"], list)
