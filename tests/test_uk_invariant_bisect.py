"""Tests for invariant-bisect -j uk (UK path in build_uk_invariant_bisect_bundle).

Unit tests (no archive required):
  - dispatch: unsupported jurisdiction raises SystemExit(2)
  - bundle schema: required keys are present

Integration tests (require data/uk_legislation.farchive):
  - duplicate_label on ukpga/1978/30: no violations found, bundle is well-formed
  - all_tree on ukpga/1978/30: enacted base is now clean under UK-aware nesting
    rules; the first bad amendment is identified and its violations are listed
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
@pytest.mark.slow
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
@pytest.mark.slow
def test_uk_invariant_bisect_all_tree_clean_ukpga_1978_30() -> None:
    """ukpga/1978/30 all_tree: no structural violations across the chain.

    Core tree invariants now model UK canonical nesting (crossheadings in
    parts/chapters, p1group wrappers, section-like children).  The enacted base
    no longer triggers unexpected_child_kind violations.  The previous first bad
    amendment (ukpga/2000/26) no longer produces the malformed
    ``subsection inside p1group`` shape because subsection-level source payloads
    are no longer relabelled as whole-section replacements.

    The UK ``all_tree`` gate deliberately excludes ``sort_order`` from the hard
    structural family: UK sections are inserted at source-declared positions
    (e.g. "after section 23") and the resulting sibling order may therefore
    differ from strict label collation.  The ``sort_order`` detector remains
    available as a standalone lint for diagnosing suspect ordering.
    """
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="all_tree",
        db_path=_DB_PATH,
    )
    assert bundle["jurisdiction"] == "uk"
    # Enacted base is clean under UK-aware nesting rules
    assert bundle["initial_clean"] is True
    assert len(bundle["initial_violations"]) == 0
    # No structural violations anywhere in the chain
    assert bundle["failure_count"] == 0
    assert bundle["first_bad_amendment"] == ""
    assert bundle["monotone_failure"] is False
    assert bundle["transient_failure"] is False
    # The fixed p1group/subsection nesting violation must not appear.
    assert not any(
        "unexpected subsection inside p1group" in v
        for step in bundle["steps"]
        for v in step["violations"]
    )


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
def test_uk_invariant_bisect_sort_order_still_reports_source_order_ukpga_1978_30() -> None:
    """sort_order remains available as a standalone lint for source-order anomalies."""
    from lawvm.tools.invariant_bisect import build_uk_invariant_bisect_bundle

    bundle = build_uk_invariant_bisect_bundle(
        "ukpga/1978/30",
        detector="sort_order",
        db_path=_DB_PATH,
    )
    assert bundle["jurisdiction"] == "uk"
    assert bundle["first_bad_amendment"] == "ukpga/2018/16"
    assert any(
        "section out of order: 23ZA > 23A" in v
        for v in bundle["first_bad_violations"]
    )
    assert bundle["monotone_failure"] is True


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live UK invariant-bisect test",
)
@pytest.mark.slow
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
@pytest.mark.slow
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
