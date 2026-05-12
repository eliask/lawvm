from __future__ import annotations

import csv
import warnings
from collections import Counter

import pytest

from lawvm.tools import bench


class _DummyReplay:
    def serialize_text(self) -> str:
        return "foo"


def test_score_one_defaults_to_fast_replay(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_replay_xml(sid: str, mode: str = "finlex_oracle", **kwargs):
        seen["sid"] = sid
        seen["mode"] = mode
        seen.update(kwargs)
        return _DummyReplay()

    monkeypatch.setattr(bench, "replay_xml", fake_replay_xml)
    monkeypatch.setattr(bench, "_structural_sim", lambda _sid, _master: (1.0, {}))

    sid, sim, status = bench._score_one("2000/1")

    assert (sid, sim, status) == ("2000/1", 1.0, "OK")
    assert seen["quiet"] is True
    assert seen["build_full_products"] is True
    assert seen["oracle_selector"] == bench._BENCH_CONSOLIDATED_SELECTOR


def test_score_one_can_request_diagnostic_replay(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_replay_xml(sid: str, mode: str = "finlex_oracle", **kwargs):
        seen["sid"] = sid
        seen["mode"] = mode
        seen.update(kwargs)
        return _DummyReplay()

    monkeypatch.setattr(bench, "replay_xml", fake_replay_xml)
    monkeypatch.setattr(bench, "_structural_sim", lambda _sid, _master: (1.0, {}))

    sid, sim, status = bench._score_one("2000/1", diagnostic_replay=True)

    assert (sid, sim, status) == ("2000/1", 1.0, "OK")
    assert seen["quiet"] is False
    assert seen["build_full_products"] is True
    assert seen["oracle_selector"] == bench._BENCH_CONSOLIDATED_SELECTOR


def test_is_digit_renesting_mismatch_detects_pure_encoding_difference() -> None:
    """Flat digit-item oracle vs merged LawVM output: pure encoding difference → filtered."""
    sd = {"structural": 4, "label": 0, "text": 3}
    events = [
        {"kind": "facet_removed", "facet_kind": "intro", "unit_kind": "intro",
         "left_text": "Hankkeen edellytyksenä on, että:", "right_text": ""},
        {"kind": "wording_text_changed", "facet_kind": "wording", "unit_kind": "subsection",
         "left_text": "", "right_text": "Hankkeen edellytyksenä on, että:"},
        {"kind": "unit_missing_right", "unit_kind": "item",
         "left_text": "kustannukset ovat kohtuulliset;", "right_text": ""},
        {"kind": "wording_text_changed", "facet_kind": "wording", "unit_kind": "subsection",
         "left_text": "jatko-teksti", "right_text": "1) kustannukset ovat kohtuulliset;"},
        {"kind": "unit_missing_left", "unit_kind": "subsection",
         "left_text": "", "right_text": "jatko-teksti"},
    ]
    assert bench._is_digit_renesting_mismatch(sd, events) is True


def test_is_digit_renesting_mismatch_rejects_content_difference() -> None:
    """When text content differs (not just encoding), do NOT filter."""
    sd = {"structural": 4, "label": 0, "text": 3}
    events = [
        {"kind": "facet_removed", "facet_kind": "intro", "unit_kind": "intro",
         "left_text": "Uusi virasto voi myöntää:", "right_text": ""},
        {"kind": "wording_text_changed", "facet_kind": "wording", "unit_kind": "subsection",
         "left_text": "", "right_text": "Vanha virasto voi myöntää:"},  # DIFFERENT text
        {"kind": "unit_missing_right", "unit_kind": "item",
         "left_text": "kustannukset ovat kohtuulliset;", "right_text": ""},
        {"kind": "unit_missing_left", "unit_kind": "subsection",
         "left_text": "", "right_text": "1) kustannukset ovat kohtuulliset;"},
    ]
    assert bench._is_digit_renesting_mismatch(sd, events) is False


def test_is_digit_renesting_mismatch_rejects_label_changes() -> None:
    """Label changes make the section a real error, not a pure encoding mismatch."""
    sd = {"structural": 2, "label": 1, "text": 0}
    events = [
        {"kind": "facet_removed", "facet_kind": "intro", "unit_kind": "intro",
         "left_text": "Tarkoitetaan:", "right_text": ""},
        {"kind": "unit_missing_right", "unit_kind": "item",
         "left_text": "vesistöllä vesilain mukaista;", "right_text": ""},
        {"kind": "unit_missing_left", "unit_kind": "subsection",
         "left_text": "", "right_text": "1) vesistöllä vesilain mukaista;"},
    ]
    assert bench._is_digit_renesting_mismatch(sd, events) is False


def test_is_digit_renesting_mismatch_rejects_unexpected_event_kinds() -> None:
    """Extra event kinds (e.g. facet_added) prevent filtering."""
    sd = {"structural": 2, "label": 0, "text": 1}
    events = [
        {"kind": "facet_removed", "facet_kind": "intro", "unit_kind": "intro",
         "left_text": "Tarkoitetaan:", "right_text": ""},
        {"kind": "unit_missing_right", "unit_kind": "item",
         "left_text": "vesistöllä;", "right_text": ""},
        {"kind": "unit_missing_left", "unit_kind": "subsection",
         "left_text": "", "right_text": "1) vesistöllä;"},
        {"kind": "facet_added", "facet_kind": "wording", "unit_kind": "wording",
         "left_text": "", "right_text": "extra oracle text"},
    ]
    assert bench._is_digit_renesting_mismatch(sd, events) is False


def test_is_wording_whitespace_only_diff_detects_ocr_word_fusion() -> None:
    """OCR word-fusion: words fused without spaces in replay, corrected in oracle."""
    sd = {"structural": 0, "label": 0, "text": 2}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "kuolemansyynselvittämiseksi ole suoritettava",
         "right_text": "kuolemansyyn selvittämiseksi ole suoritettava"},
        {"kind": "wording_text_changed",
         "left_text": "hoidossakuollut henkilö",
         "right_text": "hoidossa kuollut henkilö"},
    ]
    assert bench._is_wording_whitespace_only_diff(sd, events) is True


def test_is_wording_whitespace_only_diff_rejects_content_change() -> None:
    """Real content difference (not just whitespace) must not be filtered."""
    sd = {"structural": 0, "label": 0, "text": 1}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "terveydenhuollon ammattihenkilöitä",
         "right_text": "sosiaalihuollon ammattihenkilöitä"},  # different word
    ]
    assert bench._is_wording_whitespace_only_diff(sd, events) is False


def test_is_wording_whitespace_only_diff_rejects_structural_diff() -> None:
    """Structural differences prevent the filter from firing."""
    sd = {"structural": 1, "label": 0, "text": 1}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "kuolemansyynselvittämiseksi",
         "right_text": "kuolemansyyn selvittämiseksi"},
    ]
    assert bench._is_wording_whitespace_only_diff(sd, events) is False


def test_is_wording_whitespace_only_diff_rejects_non_wording_event() -> None:
    """Any non-wording_text_changed event prevents the filter."""
    sd = {"structural": 0, "label": 0, "text": 1}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "kuolemansyynselvittämiseksi",
         "right_text": "kuolemansyyn selvittämiseksi"},
        {"kind": "facet_added", "facet_kind": "intro",
         "left_text": "", "right_text": "extra text"},
    ]
    assert bench._is_wording_whitespace_only_diff(sd, events) is False


def test_is_wording_whitespace_only_diff_rejects_quote_char_difference() -> None:
    """Character differences (not just whitespace) like quote marks are NOT filtered."""
    sd = {"structural": 0, "label": 0, "text": 1}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "koordinaatit ovat 60°36,6'P ja 19°13,0'I",
         "right_text": "koordinaatit ovat 60°36,6\"P ja 19°13,0\"I"},  # ' vs "
    ]
    assert bench._is_wording_whitespace_only_diff(sd, events) is False


def test_clean_strips_generic_temporary_residue_without_valiaikaisesti() -> None:
    replay = "3 b §"
    oracle = "3 b § 3 b § oli voimassa 1.10.2021–30.4.2022 L:lla 18.6.2021/540."

    assert bench._clean(replay) == bench._clean(oracle)


def test_summarize_bench_warning_diagnostics_collects_logger_and_python_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn(
            "ProvisionVersion effective == expires (2013-11-08) — empty same-day temporal interval (source=2014/415)",
            UserWarning,
        )

    counts = bench._summarize_bench_warning_diagnostics(
        "  [1986/385] COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED: 25/26 units uncovered\n"
        "  WARNING product invariant: example\n",
        "",
        list(caught),
    )

    assert counts["coverage_degraded"] == 1
    assert counts["product_invariant"] == 1
    assert counts["same_day_empty_interval"] == 1


def test_run_benchmark_prints_warning_summary_per_row(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        bench,
        "_score_one_with_warning_summary",
        lambda sid, mode="finlex_oracle", *, diagnostic_replay=False, fast=False: (
            sid,
            0.9,
            "OK",
            0.95,
            {"coverage_degraded": 2, "same_day_empty_interval": 1},
        ),
    )

    results, _lev_sims = bench._run_benchmark([(1, "2000/1")], verbose=True, workers=1)

    assert results[0][:4] == (1, "2000/1", 0.9, "OK")
    out = capsys.readouterr().out
    assert "warnings: coverage_degraded×2, same_day_empty_interval×1" in out


def test_summarize_bench_replay_result_diagnostics_counts_findings() -> None:
    master = type(
        "ReplayResult",
        (),
        {
            "findings": (
                type("Finding", (), {"kind": "ELAB.SOURCE_PATHOLOGY"})(),
                type("Finding", (), {"kind": "ELAB.SOURCE_PATHOLOGY"})(),
            ),
            "source_adjudication": type("SourceAdjudication", (), {"oracle_suspect": "stale_oracle"})(),
        },
    )()

    counts = bench._summarize_bench_replay_result_diagnostics(master, Counter({"coverage_degraded": 1}))

    assert counts["coverage_degraded"] == 1
    assert counts["finding:ELAB.SOURCE_PATHOLOGY"] == 2
    assert counts["source_adjudication:oracle_suspect"] == 1
    assert bench._format_bench_warning_summary(counts).startswith("  diagnostics: ")


def test_run_benchmark_can_emit_diagnostic_summaries_for_persistence(monkeypatch) -> None:
    monkeypatch.setattr(
        bench,
        "_score_one_with_warning_summary",
        lambda sid, mode="finlex_oracle", *, diagnostic_replay=False, fast=False: (
            sid,
            0.9,
            "OK",
            0.95,
            {"coverage_degraded": 2},
        ),
    )
    diagnostics_out: dict[str, str] = {}

    results, _lev_sims = bench._run_benchmark(
        [(1, "2000/1")],
        verbose=False,
        workers=1,
        diagnostic_summaries_out=diagnostics_out,
    )

    assert results[0][:4] == (1, "2000/1", 0.9, "OK")
    assert diagnostics_out == {"2000/1": "  warnings: coverage_degraded×2"}


def test_save_run_persists_diagnostics_summary_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bench, "_runs_dir", lambda: tmp_path)

    path = bench._save_run(
        [(1, "2000/1", 0.9, "OK", 1.23)],
        "demo",
        "2026-05-12T12:00:00Z",
        lev_sims={"2000/1": 0.95},
        diagnostic_summaries={"2000/1": "  diagnostics: finding:ELAB.SOURCE_PATHOLOGY×1"},
    )

    rows = list(csv.DictReader(path.open(newline="")))
    assert rows[0]["diagnostics_summary"] == "  diagnostics: finding:ELAB.SOURCE_PATHOLOGY×1"
    assert rows[0]["lev_similarity"] == "0.950000"


def test_bench_tail_proof_summary_uses_display_tier_and_mixed_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda sid, mode="legal_pit", include_bisect=True: {
            "primary_proof_tier": "UNRESOLVED",
            "proof_claims": [{"kind": "trivially_empty"}],
            "strict_fail_reasons": ["APPLY.TREE_INVARIANT_VIOLATION"],
            "section_claims": [{"selected_kind": "replay_divergence"}],
        },
    )

    got = bench._bench_tail_proof_summary("2021/177")

    assert got["primary_proof_tier"] == "UNRESOLVED"
    assert got["display_primary_tier"] == "BENIGN_TRIVIALLY_EMPTY"
    assert got["mixed_replay_risk"] is True


def test_show_compare_annotates_rows_with_display_tier_and_mixed_risk(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        bench,
        "_load_run_by_label",
        lambda label: [("2000/1", 0.99), ("2000/2", 0.90)] if label == "old" else [("2000/1", 0.97), ("2000/2", 0.95)],
    )
    monkeypatch.setattr(
        bench,
        "_bench_tail_proof_summary",
        lambda sid: {
            "display_primary_tier": "PROVED_SOURCE_PATHOLOGY" if sid == "2000/1" else "BENIGN_TRIVIALLY_EMPTY",
            "mixed_replay_risk": sid == "2000/1",
        },
    )

    bench._show_compare("old", "new", top=20)

    out = capsys.readouterr().out
    assert "Regression display tiers:" in out
    assert "Improvement display tiers:" in out
    assert "2000/1" in out and "tier=PROVED_SOURCE_PATHOLOGY mixed=yes" in out
    assert "2000/2" in out and "tier=BENIGN_TRIVIALLY_EMPTY mixed=no" in out


def test_show_compare_only_classifies_changed_rows(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        bench,
        "_load_run_by_label",
        lambda label: [("same", 0.99), ("reg", 0.90)] if label == "old" else [("same", 0.99), ("reg", 0.88)],
    )
    seen: list[str] = []

    def fake_summary(sid: str) -> dict[str, object]:
        seen.append(sid)
        return {"display_primary_tier": "PROVED_SOURCE_PATHOLOGY", "mixed_replay_risk": False}

    monkeypatch.setattr(bench, "_bench_tail_proof_summary", fake_summary)

    bench._show_compare("old", "new", top=20)

    _ = capsys.readouterr().out
    assert seen == ["reg"]


def test_show_compare_top_limits_displayed_classifications(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        bench,
        "_load_run_by_label",
        lambda label: [("a", 0.90), ("b", 0.80), ("same", 0.75)] if label == "old" else [("a", 0.88), ("b", 0.70), ("same", 0.75)],
    )
    seen: list[str] = []

    def fake_summary(sid: str) -> dict[str, object]:
        seen.append(sid)
        return {"display_primary_tier": "PROVED_SOURCE_PATHOLOGY", "mixed_replay_risk": False}

    monkeypatch.setattr(bench, "_bench_tail_proof_summary", fake_summary)

    bench._show_compare("old", "new", top=1)

    out = capsys.readouterr().out
    assert 'Showing worst 1/2 regressions by error delta' in out
    assert seen == ["b"]


def test_oracle_stale_adjusted_stats_excludes_stale_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        bench,
        "_run_oracle_checks_parallel",
        lambda sids, workers, mode="finlex_oracle", progress=False: {
            "2004/1037": {"top_diagnosis": "ORACLE_STALE"},
            "2012/916": {"top_diagnosis": "REPLAY_MISSING"},
            "1993/1501": {"top_diagnosis": "EDITORIAL_CONVENTION"},
        },
    )

    stats = bench._oracle_stale_adjusted_stats(
        [
            (1, "2004/1037", 0.55, "OK", 0.1),
            (2, "2012/916", 0.68, "OK", 0.1),
            (3, "1993/1501", 0.61, "OK", 0.1),
        ],
        workers=2,
    )

    assert stats is not None
    assert stats["n"] == 2
    assert stats["excluded"] == ["2004/1037"]
    assert stats["oracle_checked"] == 3
    assert stats["mean"] == pytest.approx((0.68 + 0.61) / 2)


def test_show_summary_prints_oracle_aware_headline(capsys) -> None:
    bench._show_summary(
        [
            (1, "2004/1037", 0.55, "OK", 0.1),
            (2, "2012/916", 0.68, "OK", 0.1),
        ],
        "demo",
        oracle_stale_adjusted={"mean": 0.68, "excluded": ["2004/1037"], "n": 1},
    )

    out = capsys.readouterr().out
    assert "Oracle-aware mean error" in out
    assert "Raw mean error" in out
