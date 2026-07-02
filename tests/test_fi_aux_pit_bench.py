"""Tests for the all-historical-PIT aux target promoted into the bench (#160).

Corpus-free. Exercises the ADDITIVE seam (explicit ``oracle_selector`` /
``as_of`` threaded through ``compute_statute_section_diffs``), the reused
structural scorer (:func:`bench._score_sections_structural`), the per-statute
trajectory aggregation and its headline "min-over-life < latest" metric, and
the ``--mode all_pit`` CLI wiring.

The end-to-end scorer is validated on real statutes (1969/10, 1978/38) in
``notes_internal/FI_AUX_PIT_FULLBUILD_2026_07_01.md``; it needs the corpus and a
live replay, so it is intentionally not exercised here.
"""
from __future__ import annotations

import argparse

import pytest

from lawvm.tools import bench


# ---------------------------------------------------------------------------
# Seam: compute_statute_section_diffs default path is byte-identical
# ---------------------------------------------------------------------------


def test_compute_diffs_default_path_uses_mode_selector(monkeypatch) -> None:
    """With no explicit ``oracle_selector`` (the default), the selector is
    resolved from ``oracle_selector_mode`` exactly as before — proving the new
    parameter cannot change existing callers' behaviour.
    """
    from lawvm.tools import structural_review as sr

    captured: dict[str, object] = {}

    def _fake_selector_from_mode(mode: str):
        captured["mode"] = mode
        return ("selector-from-mode", mode)

    def _fake_replay(*, request):
        captured["replay_selector"] = request.oracle_selector
        captured["replay_as_of"] = request.as_of
        return None  # short-circuits section extraction (materialized_state None)

    def _fake_ground_truth(statute_id, *, corpus, selector):
        captured["oracle_selector"] = selector
        return None  # not content-absent, no sections

    monkeypatch.setattr(sr, "_selector_from_mode", _fake_selector_from_mode)
    monkeypatch.setattr(
        "lawvm.finland.replay_request.call_replay_xml",
        lambda fn, *, request: _fake_replay(request=request),
    )
    monkeypatch.setattr(
        "lawvm.finland.corpus.get_ground_truth_tree", _fake_ground_truth
    )

    sections, absent = sr.compute_statute_section_diffs(
        "2015/359", corpus=object(), support_mode="diff_only"
    )

    assert captured["mode"] == "bench_comparable"
    # both replay + oracle use the mode-derived selector; as_of stays empty
    assert captured["replay_selector"] == ("selector-from-mode", "bench_comparable")
    assert captured["oracle_selector"] == ("selector-from-mode", "bench_comparable")
    assert captured["replay_as_of"] == ""
    assert sections == {} and absent is False


def test_compute_diffs_explicit_selector_and_as_of_override(monkeypatch) -> None:
    """An explicit ``oracle_selector`` + ``as_of`` bypass ``_selector_from_mode``
    entirely and thread straight through — the all_pit code path.
    """
    from lawvm.tools import structural_review as sr

    captured: dict[str, object] = {}

    def _must_not_call(mode: str):  # pragma: no cover - asserts non-invocation
        raise AssertionError("explicit selector must not consult _selector_from_mode")

    def _fake_replay(*, request):
        captured["replay_selector"] = request.oracle_selector
        captured["replay_as_of"] = request.as_of
        return None

    def _fake_ground_truth(statute_id, *, corpus, selector):
        captured["oracle_selector"] = selector
        return None

    monkeypatch.setattr(sr, "_selector_from_mode", _must_not_call)
    monkeypatch.setattr(
        "lawvm.finland.replay_request.call_replay_xml",
        lambda fn, *, request: _fake_replay(request=request),
    )
    monkeypatch.setattr(
        "lawvm.finland.corpus.get_ground_truth_tree", _fake_ground_truth
    )

    explicit = ("explicit-selector",)
    sr.compute_statute_section_diffs(
        "1969/10",
        corpus=object(),
        mode="legal_pit",
        oracle_selector=explicit,
        as_of="2020-06-01",
        support_mode="diff_only",
    )

    assert captured["replay_selector"] is explicit
    assert captured["oracle_selector"] is explicit
    assert captured["replay_as_of"] == "2020-06-01"


# ---------------------------------------------------------------------------
# Reused structural scorer
# ---------------------------------------------------------------------------


def _section(kind: str, *, events: list | None = None, **flags):
    sd: dict[str, object] = {"kind": kind, "events": events or []}
    if events:
        sd["structural"] = len(events)
    return {"semantic_diff": sd, **flags}


def test_score_sections_all_editorial_is_perfect() -> None:
    """Editorial-only (tombstone) sections are excluded from num + denom; a
    statute with only such sections scores 1.0 with zero denominator."""
    sections = {"1 §": _section("editorial_only"), "2 §": _section("editorial_only")}
    sim, n, pen, events = bench._score_sections_structural(sections)
    assert sim == 1.0 and n == 0 and pen == 0 and not events


def test_score_sections_penalises_only_non_neutralised(monkeypatch) -> None:
    """Denominator = non-editorial sections; an editorial-only section is
    excluded from num + denom; a bench-neutralised section is not penalised; an
    amb-matched section is forgiven — mirroring the headline ``_structural_sim``.

    The neutralizer heuristics are exercised elsewhere; here we stub the single
    ``_section_diff_is_bench_neutralized`` predicate so the test pins the
    ACCOUNTING (denominator/penalty/event-count) that ``_score_sections_structural``
    contributes on top of the reused predicates.
    """
    # "2 §" is a genuine divergence; "4 §" is one the bench neutralizes.
    monkeypatch.setattr(
        bench,
        "_section_diff_is_bench_neutralized",
        lambda sd, events: sd.get("_neutral", False),
    )
    sections = {
        "1 §": _section("editorial_only"),
        "2 §": _section(
            "structural_change",
            events=[{"kind": "wording_text_changed"}],
        ),
        "3 §": _section(
            "structural_change",
            events=[{"kind": "unit_missing_left"}],
            amb_alternate_match=True,
        ),
        "4 §": _section(
            "structural_change",
            events=[{"kind": "unit_missing_right"}],
        ),
    }
    sections["4 §"]["semantic_diff"]["_neutral"] = True

    sim, n, pen, events = bench._score_sections_structural(sections)
    # denom = 3 non-editorial sections (2,3,4); only "2 §" penalised
    assert n == 3 and pen == 1
    assert sim == pytest.approx(2 / 3)
    # every diverging section's events are counted, even forgiven/neutralized ones
    assert events["wording_text_changed"] == 1
    assert events["unit_missing_left"] == 1
    assert events["unit_missing_right"] == 1


# ---------------------------------------------------------------------------
# Trajectory aggregation + headline metric
# ---------------------------------------------------------------------------


def _snap(as_of, sim, *, status="OK"):
    return bench._AllPitSnapshotResult(
        version_tag=(as_of or "x").replace("-", ""),
        amendment_id="a",
        as_of=as_of,
        struct_sim=sim,
        n_sections=10,
        n_penalized=0,
        phase_status=status,
    )


def test_hidden_mid_life_divergence_when_min_below_latest() -> None:
    res = bench._AllPitStatuteResult(
        sid="1969/10",
        snapshots=(
            _snap("2016-01-01", 0.625),
            _snap("2020-06-01", 0.913),
            _snap("2026-01-01", 1.0),
        ),
    )
    assert res.min_over_life == pytest.approx(0.625)
    assert res.latest_scored == pytest.approx(1.0)
    assert res.has_hidden_mid_life_divergence is True


def test_no_hidden_divergence_when_monotone_up_to_latest() -> None:
    res = bench._AllPitStatuteResult(
        sid="x/1",
        snapshots=(_snap("2020-01-01", 0.9), _snap("2021-01-01", 0.9)),
    )
    assert res.has_hidden_mid_life_divergence is False


def test_latest_scored_skips_trailing_unscored_snapshots() -> None:
    """``latest`` is the newest SCORED snapshot; a trailing content-absent /
    unplaceable snapshot must not become the comparison baseline."""
    res = bench._AllPitStatuteResult(
        sid="x/2",
        snapshots=(
            _snap("2020-01-01", 0.8),
            _snap("2021-01-01", 0.95),
            _snap(None, -1.0, status="UNPLACEABLE:no derivable as-of date"),
        ),
    )
    assert res.latest_scored == pytest.approx(0.95)
    assert res.min_over_life == pytest.approx(0.8)
    assert res.has_hidden_mid_life_divergence is True


def test_all_unscored_snapshots_have_no_divergence_signal() -> None:
    res = bench._AllPitStatuteResult(
        sid="x/3",
        snapshots=(_snap(None, -1.0, status="NO_ORACLE"),),
    )
    assert res.scored == []
    assert res.min_over_life is None and res.latest_scored is None
    assert res.has_hidden_mid_life_divergence is False


# ---------------------------------------------------------------------------
# Driver + summary (monkeypatched scorer — no corpus)
# ---------------------------------------------------------------------------


def test_run_all_pit_sequential_dispatches_per_statute(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_score(sid: str):
        calls.append(sid)
        return bench._AllPitStatuteResult(sid=sid, snapshots=(_snap("2020-01-01", 1.0),))

    monkeypatch.setattr(bench, "_all_pit_score_one_statute", _fake_score)
    results = bench._run_all_pit(["a/1", "b/2"], workers=1, verbose=False)
    assert calls == ["a/1", "b/2"]
    assert [r.sid for r in results] == ["a/1", "b/2"]


def test_show_all_pit_summary_reports_headline(capsys) -> None:
    results = [
        bench._AllPitStatuteResult(
            sid="1969/10",
            snapshots=(_snap("2016-01-01", 0.625), _snap("2026-01-01", 1.0)),
        ),
        bench._AllPitStatuteResult(
            sid="clean/1",
            snapshots=(_snap("2020-01-01", 1.0), _snap("2021-01-01", 1.0)),
        ),
        bench._AllPitStatuteResult(sid="broken/1", snapshots=(), phase_status="ERROR:boom"),
    ]
    bench._show_all_pit_summary(results)
    out = capsys.readouterr().out
    assert "HEADLINE hidden-mid-life    : 1" in out
    assert "1969/10" in out and "broken/1" in out


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_accepts_all_pit_mode() -> None:
    """``lawvm bench --mode all_pit`` parses; the choice is registered on the
    real ``bench`` subcommand, and the two pre-existing modes still parse."""
    from lawvm.tools import cli

    parser = cli._build_parser()
    args = parser.parse_args(["bench", "--mode", "all_pit", "--no-save"])
    assert args.mode == "all_pit"
    for legacy in ("official_consolidation", "legal_pit"):
        legacy_args = parser.parse_args(["bench", "--mode", legacy])
        assert legacy_args.mode == legacy


def test_cli_rejects_unknown_mode() -> None:
    from lawvm.tools import cli

    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["bench", "--mode", "not_a_mode"])


def test_main_routes_all_pit_mode(monkeypatch) -> None:
    """``main`` with ``mode=all_pit`` calls the all_pit runner and returns
    WITHOUT touching the standard scoring/save flow."""
    routed: dict[str, object] = {}

    def _fake_run_all_pit_mode(corpus, *, workers, anchor_touch=False):
        routed["corpus"] = corpus
        routed["workers"] = workers
        routed["anchor_touch"] = anchor_touch

    def _boom(*a, **k):  # pragma: no cover - standard flow must not run
        raise AssertionError("all_pit must not enter the standard bench flow")

    monkeypatch.setattr(bench, "_run_all_pit_mode", _fake_run_all_pit_mode)
    monkeypatch.setattr(bench, "_run_benchmark", _boom)
    monkeypatch.setattr(bench, "_load_corpus", lambda path: [(1, "1969/10")])
    monkeypatch.setattr(bench, "_default_corpus_path", lambda: "x.csv")
    monkeypatch.setattr("os.path.exists", lambda p: True)

    args = argparse.Namespace(
        mode="all_pit",
        parallel=1,
        no_save=True,
        corpus=None,
        statute=None,
        limit=None,
    )
    bench.main(args)
    assert routed["corpus"] == [(1, "1969/10")]
    assert routed["workers"] == 1
    # the anchor-touch attribution report is opt-in and defaults off, so a plain
    # all_pit route must not request it (#183 additive-discipline invariant).
    assert routed["anchor_touch"] is False
