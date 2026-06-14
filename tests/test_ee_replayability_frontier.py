"""Tests for the Estonia replayability-frontier classifier.

These exercise the typed taxonomy, classifier totality, the loud-unclassified
behavior, and report determinism using synthetic replay-result fixtures. They do
NOT require the live Riigi Teataja archive — the replay/source signal is mocked
through ``SimpleNamespace`` objects shaped like ``EEPitResult`` and a fixture
``replay_pair`` callable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lawvm.estonia.replayability_frontier import (
    EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE,
    EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR,
    EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE,
    EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW,
    EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE,
    EE_REPLAYABILITY_REPLAYABLE,
    EE_REPLAYABILITY_REPLAY_ERROR_OTHER,
    EE_REPLAYABILITY_STATES,
    EE_REPLAYABILITY_UNCLASSIFIED,
    EECorpusPair,
    EEReplayabilityState,
    classify_ee_replayability,
    ee_replayability_frontier_for_corpus,
    ee_replayability_states_to_report,
    read_ee_corpus_pairs,
)


def _result(**kwargs):
    """Build an EEPitResult-shaped fixture with sensible replayable defaults."""
    base = {
        "base_id": "100",
        "oracle_id": "200",
        "grupi_id": "g",
        "as_of": "2024-01-01",
        "error": None,
        "n_ops": 3,
        "amendments_total": ["a", "b"],
        "amendments_applied": ["a", "b"],
        "amendments_failed": [],
        "replayed": SimpleNamespace(body=object()),
        "oracle": SimpleNamespace(body=object()),
        "divergences": [],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── State-by-state classification ────────────────────────────────────────────


def test_replayable_when_clean_with_ops_and_oracle() -> None:
    state = classify_ee_replayability(_result())
    assert state.state == EE_REPLAYABILITY_REPLAYABLE
    assert state.is_replayability_frontier is False
    assert state.reasons == (EE_REPLAYABILITY_REPLAYABLE,)


def test_base_source_unavailable_from_load_error() -> None:
    state = classify_ee_replayability(
        _result(error="Failed to load base: curl failed", replayed=None)
    )
    assert state.state == EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE
    assert state.is_replayability_frontier is True


def test_base_source_parse_error_from_parse_banner() -> None:
    state = classify_ee_replayability(
        _result(error="Failed to parse base: XMLSyntaxError", replayed=None)
    )
    assert state.state == EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR


def test_apply_error_maps_to_replay_error_other() -> None:
    state = classify_ee_replayability(
        _result(error="Failed to apply ops: KeyError")
    )
    assert state.state == EE_REPLAYABILITY_REPLAY_ERROR_OTHER


def test_unknown_error_falls_through_to_replay_error_other() -> None:
    state = classify_ee_replayability(_result(error="something unexpected"))
    assert state.state == EE_REPLAYABILITY_REPLAY_ERROR_OTHER


def test_amendment_source_unavailable_when_refs_failed() -> None:
    state = classify_ee_replayability(_result(amendments_failed=["c"]))
    assert state.state == EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE
    assert state.amendments_failed == ("c",)
    assert state.n_amendments_failed == 1


def test_oracle_source_unavailable_when_oracle_missing() -> None:
    state = classify_ee_replayability(_result(oracle=None))
    assert state.state == EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE


def test_no_amendments_in_window_when_clean_zero_ops() -> None:
    state = classify_ee_replayability(
        _result(n_ops=0, amendments_applied=[])
    )
    assert state.state == EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW
    assert state.is_replayability_frontier is True


def test_loud_unclassified_when_no_error_but_no_replayed_tree() -> None:
    # Oracle present, no error banner, yet no replayed tree: the signal is
    # internally inconsistent and must NOT be silently treated as replayable.
    state = classify_ee_replayability(_result(replayed=None))
    assert state.state == EE_REPLAYABILITY_UNCLASSIFIED
    assert state.is_replayability_frontier is True


def test_error_takes_precedence_over_amendment_and_oracle_signals() -> None:
    state = classify_ee_replayability(
        _result(
            error="Failed to load base: x",
            amendments_failed=["c"],
            oracle=None,
            replayed=None,
        )
    )
    assert state.state == EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE


# ── Totality ─────────────────────────────────────────────────────────────────


def test_classifier_is_total_over_taxonomy() -> None:
    fixtures = [
        _result(),
        _result(error="Failed to load base: x"),
        _result(error="Failed to parse base: x"),
        _result(error="Failed to apply ops: x"),
        _result(error="weird"),
        _result(amendments_failed=["c"]),
        _result(oracle=None),
        _result(replayed=None),
        _result(n_ops=0),
    ]
    seen = set()
    for fixture in fixtures:
        state = classify_ee_replayability(fixture)
        assert state.state in EE_REPLAYABILITY_STATES
        seen.add(state.state)
    # Every taxonomy state except the defensive guard should be reachable here.
    assert seen >= (EE_REPLAYABILITY_STATES - {EE_REPLAYABILITY_UNCLASSIFIED}) | {
        EE_REPLAYABILITY_UNCLASSIFIED
    }


def test_every_state_has_a_diagnostic_detail_reason() -> None:
    for fixture in (
        _result(),
        _result(error="Failed to load base: x"),
        _result(error="Failed to parse base: x"),
        _result(error="Failed to apply ops: x"),
        _result(amendments_failed=["c"]),
        _result(oracle=None),
        _result(replayed=None),
        _result(n_ops=0),
    ):
        state = classify_ee_replayability(fixture)
        detail = state.to_diagnostic_detail()
        assert detail["blocking"] is False
        assert detail["strict_disposition"] == "record"
        assert detail["replayability_state"] == state.state
        assert detail["reason"]


# ── Corpus scan + report determinism ─────────────────────────────────────────


def _fixture_replay_pair(_base, _oracle, _eff):
    # Map a few synthetic base ids to distinct states.
    table = {
        "100": _result(base_id="100", oracle_id="200"),
        "300": _result(base_id="300", oracle_id="400", grupi_id="", error="Failed to load base: x"),
        "500": _result(base_id="500", oracle_id="600", oracle=None),
    }
    return table[_base]


def test_corpus_scan_sorts_and_classifies() -> None:
    pairs = [
        EECorpusPair(base_id="500", oracle_id="600", grupi_id="g3"),
        EECorpusPair(base_id="100", oracle_id="200", grupi_id="g1"),
        EECorpusPair(base_id="300", oracle_id="400", grupi_id="g2"),
    ]
    states = ee_replayability_frontier_for_corpus(
        pairs, replay_pair=_fixture_replay_pair
    )
    assert [s.base_id for s in states] == ["100", "300", "500"]
    assert [s.state for s in states] == [
        EE_REPLAYABILITY_REPLAYABLE,
        EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE,
        EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE,
    ]
    # grupi_id from the corpus row is backfilled when the result lacks it.
    assert states[1].grupi_id == "g2"


def test_corpus_scan_limit_is_deterministic() -> None:
    pairs = [
        EECorpusPair(base_id="500", oracle_id="600"),
        EECorpusPair(base_id="100", oracle_id="200"),
        EECorpusPair(base_id="300", oracle_id="400"),
    ]
    states = ee_replayability_frontier_for_corpus(
        pairs, replay_pair=_fixture_replay_pair, limit=2
    )
    assert [s.base_id for s in states] == ["100", "300"]


def test_report_is_deterministic_and_complete() -> None:
    pairs = [
        EECorpusPair(base_id="500", oracle_id="600"),
        EECorpusPair(base_id="100", oracle_id="200"),
        EECorpusPair(base_id="300", oracle_id="400"),
    ]
    states_a = ee_replayability_frontier_for_corpus(
        pairs, replay_pair=_fixture_replay_pair
    )
    states_b = ee_replayability_frontier_for_corpus(
        list(reversed(pairs)), replay_pair=_fixture_replay_pair
    )
    report_a = ee_replayability_states_to_report(states_a)
    report_b = ee_replayability_states_to_report(states_b)
    assert report_a == report_b

    # Every taxonomy state appears in the counts (stable shape).
    assert set(report_a["state_counts"]) == EE_REPLAYABILITY_STATES
    assert report_a["pair_count"] == 3
    assert report_a["replayability_frontier_pair_count"] == 2
    assert report_a["replayability_frontier_pairs"] == ["300:400", "500:600"]
    # state_counts keys are sorted.
    assert list(report_a["state_counts"]) == sorted(report_a["state_counts"])


def test_report_rows_sorted_independent_of_input_order() -> None:
    s1 = EEReplayabilityState(base_id="9", oracle_id="9", state=EE_REPLAYABILITY_REPLAYABLE, reasons=(EE_REPLAYABILITY_REPLAYABLE,))
    s2 = EEReplayabilityState(base_id="1", oracle_id="1", state=EE_REPLAYABILITY_REPLAYABLE, reasons=(EE_REPLAYABILITY_REPLAYABLE,))
    report = ee_replayability_states_to_report([s1, s2])
    assert [row["base_id"] for row in report["pairs"]] == ["1", "9"]


# ── Corpus CSV reading ───────────────────────────────────────────────────────


def test_read_ee_corpus_pairs(tmp_path) -> None:
    csv_path = tmp_path / "corpus.csv"
    csv_path.write_text(
        "grupi_id,base_id,oracle_id,oracle_effective,title\n"
        "g2,300,400,2024-02-01,Beta\n"
        "g1,100,200,2024-01-01,Alpha\n"
        "gskip,,999,,Missing base\n",
        encoding="utf-8",
    )
    pairs = read_ee_corpus_pairs(csv_path)
    # Rows missing a base/oracle id are skipped; output sorted by base_id.
    assert [(p.base_id, p.oracle_id) for p in pairs] == [("100", "200"), ("300", "400")]
    assert pairs[0].title == "Alpha"
    assert pairs[1].oracle_effective == "2024-02-01"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
