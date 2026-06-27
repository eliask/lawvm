"""Tests for the SE coverage-scan universe — content-addressed corpus root.

Brings the SE aggregate coverage scan into the "no hidden universe" invariant
(pro-note §6 UniverseSpec). Mirrors the discipline of
``tests/test_assumption_register.py`` and ``tests/test_se_assumptions.py`` (sibling
evidence-plane dossier types): the universe root is deterministic, order-
independent, unique per scanned-act set, and an unknown outcome raises loud.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.sweden.se_coverage_universe import (
    se_coverage_universe_entry,
    se_coverage_universe_root,
)


def _entry(sfs_id: str = "2026:1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "amending_sfs_id": sfs_id,
        "base_sfs_id": "2026:2",
        "outcome": "replay_ok",
        "bucket_genuine_match_count": 1,
        "bucket_oracle_version_mismatch_count": 0,
        "bucket_genuine_mismatch_count": 0,
        "bucket_unknown_count": 0,
        "recovery_mode": "",
    }
    base.update(overrides)
    return cast(dict[str, Any], se_coverage_universe_entry(**base))


# --------------------------------------------------------------------------- #
# Universe entry construction.                                                #
# --------------------------------------------------------------------------- #


def test_entry_is_well_formed_for_replay_ok() -> None:
    entry = _entry("2026:1")
    assert entry["schema"] == "lawvm.se_coverage_universe.v0"
    assert entry["amending_sfs_id"] == "2026:1"
    assert entry["outcome"] == "replay_ok"
    assert entry["bucket_genuine_match_count"] == 1


def test_entry_is_well_formed_for_older_base_required() -> None:
    entry = _entry("2026:1", outcome="older_base_required", bucket_genuine_match_count=0)
    assert entry["outcome"] == "older_base_required"


def test_entry_is_well_formed_for_error_outcome() -> None:
    entry = _entry("2026:1", outcome="error", bucket_genuine_match_count=0)
    assert entry["outcome"] == "error"
    assert entry["recovery_mode"] == ""


def test_entry_unknown_outcome_raises_loud() -> None:
    """§1.10 fail-loud: an outcome not in the closed taxonomy raises KeyError."""
    with pytest.raises(KeyError, match="not in the closed valid set"):
        _entry("2026:1", outcome="unknown_future_outcome")


def test_entry_empty_outcome_is_valid() -> None:
    """A summary the scan could not classify has outcome=''."""
    entry = _entry("2026:1", outcome="")
    assert entry["outcome"] == ""


# --------------------------------------------------------------------------- #
# Universe root invariants.                                                    #
# --------------------------------------------------------------------------- #


def test_root_is_deterministic() -> None:
    a = se_coverage_universe_root([_entry("2026:1"), _entry("2026:2")])
    b = se_coverage_universe_root([_entry("2026:1"), _entry("2026:2")])
    assert a == b
    assert a.startswith("sha256:")


def test_root_is_order_independent_set_semantics() -> None:
    """A universe is a SET — the root is insensitive to argument order so a
    next run can compare two scans against the same committed universe regardless
    of how each caller walked the act list."""
    a = se_coverage_universe_root([_entry("2026:1"), _entry("2026:2")])
    b = se_coverage_universe_root([_entry("2026:2"), _entry("2026:1")])
    assert a == b


def test_root_changes_when_act_added() -> None:
    base = se_coverage_universe_root([_entry("2026:1")])
    more = se_coverage_universe_root([_entry("2026:1"), _entry("2026:2")])
    assert base != more


def test_root_changes_when_act_dropped() -> None:
    full = se_coverage_universe_root([_entry("2026:1"), _entry("2026:2")])
    dropped = se_coverage_universe_root([_entry("2026:1")])
    assert full != dropped


def test_root_changes_when_act_outcome_flips() -> None:
    """The single most load-bearing invariant: an act whose bucket flips
    (e.g. a genuine_match becoming a genuine_mismatch) must change the root —
    otherwise the universe cannot detect a per-act state regression."""
    before = se_coverage_universe_root(
        [_entry("2026:1", bucket_genuine_match_count=1, bucket_genuine_mismatch_count=0)]
    )
    after = se_coverage_universe_root(
        [_entry("2026:1", bucket_genuine_match_count=0, bucket_genuine_mismatch_count=1)]
    )
    assert before != after


def test_root_changes_when_act_outcome_changes_otherwise() -> None:
    """The same act moving from replay_ok to older_base_required in a
    subsequent scan must change the root."""
    before = se_coverage_universe_root([_entry("2026:1", outcome="replay_ok")])
    after = se_coverage_universe_root([_entry("2026:1", outcome="older_base_required", bucket_genuine_match_count=0)])
    assert before != after


def test_root_empty_set_is_well_defined() -> None:
    """Mirrors set_root over empty — the v0 'declares nothing' case is a
    committed empty SetRoot, not skipped."""
    assert se_coverage_universe_root([]).startswith("sha256:")


def test_root_distinguishes_distinct_sfs_ids_with_same_outcome() -> None:
    """Two acts with identical statistics but distinct SFS ids must produce a
    distinct root from each other — the act identity is part of the universe."""
    only_one = se_coverage_universe_root([_entry("2026:1")])
    different_act = se_coverage_universe_root([_entry("2026:99")])
    assert only_one != different_act


# --------------------------------------------------------------------------- #
# Wired into aggregate_se_official_coverage integration.                       #
# --------------------------------------------------------------------------- #


def test_aggregate_se_official_coverage_commits_a_coverage_universe_root() -> None:
    """Guard-liveness (§2.9): the universe root is ACTUALLY surfaced on the
    aggregate result dict — not just built in a module nobody calls. Drives a
    minimal two-act summary set through the production aggregate function and
    asserts the root field is present and well-formed."""
    from lawvm.sweden.fetch import aggregate_se_official_coverage

    summaries = [
        {
            "amending_sfs_id": "2026:1",
            "base_sfs_id": "2026:2",
            "outcome": "replay_ok",
            "target_count": 1,
            "match_count": 1,
            "genuine_content_match_count": 1,
            "editorial_match_count": 0,
            "official_oracle_match_count": 0,
            "bucket_genuine_match_count": 1,
            "bucket_oracle_version_mismatch_count": 0,
            "bucket_genuine_mismatch_count": 0,
            "bucket_unknown_count": 0,
            "classification_counts": {"exact": 1},
            "recovery_mode": "",
        },
        {
            "amending_sfs_id": "2026:3",
            "base_sfs_id": "2026:4",
            "outcome": "older_base_required",
            "recovery_mode": "older_base_rebuild",
        },
    ]
    aggregate = aggregate_se_official_coverage(summaries)
    assert "coverage_universe_root" in aggregate
    assert aggregate["coverage_universe_root"].startswith("sha256:")


def test_aggregate_coverage_universe_is_deterministic_across_argument_orders() -> None:
    """Caller-walks-the-act-list-in-different-order should produce the same
    root — the universe contract holds even on caller ordering variance."""
    from lawvm.sweden.fetch import aggregate_se_official_coverage

    def _two_act_summary(order: tuple[str, str]) -> list[dict[str, object]]:
        return [
            {
                "amending_sfs_id": s,
                "base_sfs_id": "2026:99",
                "outcome": "error",
                "recovery_mode": "",
            }
            for s in order
        ]

    reverse = aggregate_se_official_coverage(_two_act_summary(("2026:2", "2026:1")))
    forward = aggregate_se_official_coverage(_two_act_summary(("2026:1", "2026:2")))
    assert reverse["coverage_universe_root"] == forward["coverage_universe_root"]
