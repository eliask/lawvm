"""Tests for the corpus-wide reconcile sweep (Q2 self-audit).

These exercise selection ranking, per-statute aggregation, worst-first ranking
and report emission WITHOUT running the (slow) real replay — the replay/oracle
seam is monkeypatched so the test stays fast and deterministic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools import reconcile_sweep as rs


# ---------------------------------------------------------------------------
# Selection ranking
# ---------------------------------------------------------------------------


def test_select_ranks_by_amendment_count(monkeypatch) -> None:
    monkeypatch.setattr(
        rs,
        "_amendment_counts",
        lambda: {"a/1": 5, "b/2": 50, "c/3": 1, "d/4": 20},
    )
    chosen, log = rs.select_statutes(sample=2, min_amendments=1)
    assert [sid for sid, _ in chosen] == ["b/2", "d/4"]
    assert log["selected"] == 2
    assert log["skipped_lower_ranked"] == 2
    assert log["selection_floor_amendments"] == 20


def test_select_respects_min_amendments(monkeypatch) -> None:
    monkeypatch.setattr(
        rs, "_amendment_counts", lambda: {"a/1": 5, "b/2": 50, "c/3": 1}
    )
    chosen, log = rs.select_statutes(sample=None, min_amendments=10)
    assert [sid for sid, _ in chosen] == ["b/2"]
    assert log["eligible_count"] == 1


def test_select_explicit_ids(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_amendment_counts", lambda: {"x/9": 3})
    chosen, log = rs.select_statutes(
        sample=None, explicit_ids=["x/9", "y/8"]
    )
    assert dict(chosen) == {"x/9": 3, "y/8": 0}
    assert log["mode"] == "explicit"


# ---------------------------------------------------------------------------
# Ranking + report emission
# ---------------------------------------------------------------------------


def _result(sid, amendments, diverging, replay_error=""):
    r = rs.StatuteSweepResult(
        statute_id=sid, amendment_count=amendments, as_of="2026-06-01"
    )
    r.sections_checked = 10
    r.diverging = diverging
    r.replay_error = replay_error
    return r


def test_rank_puts_data_defect_first() -> None:
    a = _result("a/1", 100, [{"divergence_class": "editorial"}] * 5)
    b = _result("b/2", 10, [], replay_error="boom")
    c = _result("c/3", 50, [{"divergence_class": "temporal"}])
    ranked = rs.rank_results([a, b, c])
    assert ranked[0].statute_id == "b/2"  # data_defect ranks worst-first


def test_class_counts_includes_replay_error() -> None:
    r = _result("a/1", 1, [{"divergence_class": "editorial"}], replay_error="x")
    cc = r.class_counts
    assert cc["editorial"] == 1
    assert cc["data_defect"] == 1


def test_write_reports_emits_csv_and_md(tmp_path: Path) -> None:
    results = [
        _result(
            "a/1",
            100,
            [
                {
                    "statute_id": "a/1",
                    "locator": "section:1",
                    "verdict": "DISAGREE",
                    "divergence_class": "temporal",
                    "agree_ratio": 0.7,
                    "detail": "",
                }
            ],
        ),
        _result("b/2", 10, [], replay_error="FileNotFoundError: missing"),
    ]
    log = {"mode": "ranked_by_amendment_count", "selected": 2}
    csv_path, md_path = rs.write_reports(
        results, log, label="t", out_dir=str(tmp_path)
    )
    assert csv_path.exists() and md_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "temporal" in csv_text
    assert "data_defect" in csv_text  # replay-error row
    md_text = md_path.read_text(encoding="utf-8")
    assert "Reconcile Sweep" in md_text
    assert "temporal" in md_text
    assert "data_defect" in md_text


# ---------------------------------------------------------------------------
# reconcile_statute classifies a replay failure as data_defect
# ---------------------------------------------------------------------------


def test_reconcile_statute_replay_error_is_data_defect(monkeypatch) -> None:
    import lawvm.finland.grafter as grafter

    def _boom(*a, **k):
        raise FileNotFoundError("Statute z/9 not found in corpus")

    monkeypatch.setattr(grafter, "replay_xml", _boom)
    r = rs.reconcile_statute("z/9", 3, as_of="2026-06-01")
    assert r.replay_error
    assert "data_defect" in r.class_counts


def test_memoized_provision_replay_caches_no_outparam_calls() -> None:
    import lawvm.finland.grafter as grafter

    calls = {"n": 0}
    real = grafter.replay_xml

    def _counting(parent_id, *a, **k):
        calls["n"] += 1
        return f"master::{parent_id}"

    grafter.replay_xml = _counting
    try:
        with rs._memoized_provision_replay():
            # Same statute, no out-params: cached after first call.
            a1 = grafter.replay_xml("x/1", quiet=True)
            a2 = grafter.replay_xml("x/1", quiet=True)
            assert a1 == a2 == "master::x/1"
            assert calls["n"] == 1
            # Different statute: a fresh call.
            grafter.replay_xml("y/2", quiet=True)
            assert calls["n"] == 2
        # Restored after context exit.
        assert grafter.replay_xml is _counting
    finally:
        grafter.replay_xml = real
