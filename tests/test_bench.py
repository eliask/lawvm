from __future__ import annotations

import argparse
import csv
import warnings
from collections import Counter
from types import SimpleNamespace
from typing import Any, cast

import Levenshtein
import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import bench
from lawvm.tools import bench_diagnostic_tiers


class _DummyReplay:
    def serialize_text(self) -> str:
        return "foo"


def test_bench_comparison_text_prefers_materialized_pit_ir() -> None:
    fold_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="expired"),))
    materialized_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="live"),))
    master = SimpleNamespace(
        ir=fold_ir,
        materialized_state=SimpleNamespace(ir=materialized_ir),
        serialize_text=lambda: "expired",
    )

    assert bench._comparison_text(master) == "live"


def test_bench_levenshtein_text_preserves_replay_serializer_surface() -> None:
    fold_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="fold"),))
    materialized_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CONTENT, text="live"),))
    master = SimpleNamespace(
        ir=fold_ir,
        materialized_state=SimpleNamespace(ir=materialized_ir),
        serialize_text=lambda: "historical replay text",
    )

    assert bench._comparison_text(master) == "live"
    assert bench._levenshtein_comparison_text(master) == "historical replay text"


def test_bench_comparison_text_prunes_timeline_inactive_sections() -> None:
    expired_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="25b",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="expired temporary"),),
    )
    live_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="live permanent"),),
    )
    materialized_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(expired_section,),
            ),
            live_section,
        ),
    )
    master = SimpleNamespace(
        ir=materialized_ir,
        materialized_state=SimpleNamespace(ir=materialized_ir),
        products=SimpleNamespace(materialization_spec=SimpleNamespace(as_of="2026-01-01")),
        timelines={
            LegalAddress(path=(("chapter", "7"), ("section", "25b"))): ProvisionTimeline(
                address=LegalAddress(path=(("chapter", "7"), ("section", "25b"))),
                versions=[
                    ProvisionVersion(
                        effective="2020-01-01",
                        expires="2021-01-01",
                        content=expired_section,
                    )
                ],
            ),
            LegalAddress(path=(("section", "1"),)): ProvisionTimeline(
                address=LegalAddress(path=(("section", "1"),)),
                versions=[
                    ProvisionVersion(
                        effective="2020-01-01",
                        content=live_section,
                    )
                ],
            ),
        },
        serialize_text=lambda: "expired temporary live permanent",
    )

    comparison = bench._comparison_text(master)

    assert comparison == "live permanent"


def test_bench_levenshtein_ratio_helper_matches_python_levenshtein() -> None:
    pairs = (
        ("", ""),
        ("abc", ""),
        ("abc", "abc"),
        ("abc", "axc"),
        ("momentti kumotaan", "momentti muutetaan"),
    )

    for left, right in pairs:
        assert bench._levenshtein_ratio(left, right) == pytest.approx(
            Levenshtein.ratio(left, right)
        )


def test_fi_bench_worker_count_defaults_to_bounded_cpu_count(monkeypatch) -> None:
    monkeypatch.setattr(bench.os, "cpu_count", lambda: 32)
    assert bench._fi_bench_worker_count(argparse.Namespace(parallel=None)) == 16

    monkeypatch.setattr(bench.os, "cpu_count", lambda: 8)
    assert bench._fi_bench_worker_count(argparse.Namespace(parallel=None)) == 8

    monkeypatch.setattr(bench.os, "cpu_count", lambda: None)
    assert bench._fi_bench_worker_count(argparse.Namespace(parallel=None)) == 1


def test_fi_bench_worker_count_uses_explicit_parallel() -> None:
    assert bench._fi_bench_worker_count(argparse.Namespace(parallel=3)) == 3


def test_fi_bench_worker_count_rejects_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        bench._fi_bench_worker_count(argparse.Namespace(parallel=0))

    assert raised.value.code == 2
    assert "--parallel must be a positive integer" in capsys.readouterr().err


def test_fi_bench_banner_reports_worker_count() -> None:
    banner = bench._format_bench_run_banner(
        statute_count=3545,
        label="run_test",
        mode="official_consolidation",
        workers=16,
        section_score_mode=False,
        fast_mode=False,
        text_scores=True,
        diagnostic_replay=False,
    )

    assert "workers=16" in banner
    assert "[quiet-replay]" in banner


def test_save_run_preserves_non_scored_status_labels(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bench, "_runs_dir", lambda: tmp_path)

    path = bench._save_run(
        [
            (1, "1987/182", -1.0, "NO_TRUTH", 0.5),
            (1, "2000/1", -1.0, "RuntimeError('boom')", 0.1),
        ],
        label="run_test",
        timestamp="2026-06-19T06:45:00",
        lev_sims={"1987/182": -1.0, "2000/1": -1.0},
    )

    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["similarity"] == "NO_TRUTH"
    assert rows[0]["lev_similarity"] == "NO_TRUTH"
    assert rows[0]["status"] == "NO_TRUTH"
    assert rows[1]["similarity"] == "ERR"
    assert rows[1]["lev_similarity"] == "ERR"
    assert rows[1]["status"] == "RuntimeError('boom')"


def test_score_one_defaults_to_fast_replay(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_replay_xml(sid: str, mode: str = "official_consolidation", **kwargs):
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


def test_diagnostic_replay_enables_timeline_invariants_env(monkeypatch) -> None:
    import os

    seen: dict[str, object] = {}

    def fake_call_replay_xml(_replay_xml, *, request, sinks=None):
        seen["timeline"] = os.environ.get("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS")
        seen["sinks"] = sinks
        return _DummyReplay()

    monkeypatch.setattr(bench, "call_replay_xml", fake_call_replay_xml)
    bench._run_replay_with_bench_warning_capture(
        "2009/953",
        mode="legal_pit",
        diagnostic_replay=True,
        replay_kwargs={"quiet": False},
    )
    assert seen["timeline"] == "1"
    assert os.environ.get("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS") is None
    assert seen["sinks"] is not None
    sinks = cast(Any, seen["sinks"])
    assert sinks.replay_meta_out == {}


def test_quiet_bench_skips_replay_meta_unless_timeline_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS", raising=False)
    seen: dict[str, object] = {}

    def fake_call_replay_xml(_replay_xml, *, request, sinks=None):
        seen["sinks"] = sinks
        return _DummyReplay()

    monkeypatch.setattr(bench, "call_replay_xml", fake_call_replay_xml)
    bench._run_replay_with_bench_warning_capture(
        "2000/1",
        mode="official_consolidation",
        diagnostic_replay=False,
        replay_kwargs={"quiet": True},
    )
    assert seen["sinks"] is None


def test_diagnostic_replay_merges_typed_findings_into_diagnostics(monkeypatch) -> None:
    from lawvm.core.phase_result import Finding, OBSERVATION_ROLE

    class _ReplayWithFinding:
        findings = (
            Finding(
                kind="timeline_invariant_violation",
                role=OBSERVATION_ROLE,
                stage="timeline_invariants",
                blocking=False,
                detail={"code": "timeline_without_ir"},
            ),
        )

        def serialize_text(self) -> str:
            return "foo"

    def fake_call_replay_xml(_replay_xml, *, request, sinks=None):
        return _ReplayWithFinding()

    monkeypatch.setattr(bench, "call_replay_xml", fake_call_replay_xml)
    _master, counts = bench._run_replay_with_bench_warning_capture(
        "2009/953",
        mode="legal_pit",
        diagnostic_replay=True,
        replay_kwargs={"quiet": False},
    )
    assert counts["timeline_robust:timeline_without_ir"] == 1


def test_score_one_can_request_diagnostic_replay(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_replay_xml(sid: str, mode: str = "official_consolidation", **kwargs):
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
            stacklevel=2,
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
        lambda sid, mode="official_consolidation", *, diagnostic_replay=False, fast=False, text_scores=True: (
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
    assert "warnings: temporal: same_day_empty_interval×1 | audit: coverage_degraded×2" in out


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
    summary = bench._format_bench_warning_summary(counts)
    assert summary.startswith("  diagnostics: ")
    assert "operative: ELAB.SOURCE_PATHOLOGY×2" in summary
    assert "oracle: oracle_suspect×1" in summary


def test_format_tiered_bench_warning_summary_collapses_registry_stage() -> None:
    counts = Counter(
        {
            "finding:ELAB.REGISTRY_STAGE": 120,
            "finding:ELAB.REGISTRY_PIPELINE": 30,
            "timeline_robust:content_mismatch": 2,
            "coverage_degraded": 1,
        }
    )
    summary = bench_diagnostic_tiers.format_tiered_bench_warning_summary(counts)
    assert "timeline_robust: content_mismatch×2" in summary
    assert "audit: registry_stage×150, coverage_degraded×1" in summary


def test_save_bench_diagnostic_sidecar_writes_structured_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bench, "_runs_dir", lambda: tmp_path)
    run_path = tmp_path / "20260619T1200_demo.csv"
    run_path.write_text("amendments,statute_id,similarity,status,elapsed_s\n", encoding="utf-8")

    sidecar_path = bench._save_bench_diagnostic_sidecar(
        run_path=run_path,
        label="demo",
        results=[(1, "2000/1", 0.9, "OK", 1.0)],
        diagnostic_counts={
            "2000/1": {
                "timeline_robust:content_mismatch": 1,
                "finding:ELAB.REGISTRY_STAGE": 18,
            }
        },
    )

    assert sidecar_path is not None
    text = sidecar_path.read_text(encoding="utf-8")
    assert text.count('"schema": "fi_bench_diagnostic.v1"') == 2
    assert '"diagnostic_tier": "timeline_robust"' in text
    assert '"diagnostic_key": "timeline_robust:content_mismatch"' in text
    assert '"diagnostic_tier": "audit"' in text


def test_oracle_check_diagnostics_are_oracle_tier() -> None:
    from lawvm.tools import bench_diagnostic_tiers

    key = "oracle_check:top_diagnosis:ORACLE_STALE"

    assert bench_diagnostic_tiers.classify_bench_diagnostic_key(key) == "oracle"
    summary = bench_diagnostic_tiers.format_tiered_bench_warning_summary(Counter({key: 1}))
    assert "oracle: oracle_check:ORACLE_STALE×1" in summary


def test_oracle_stale_adjusted_stats_enriches_diagnostic_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        bench,
        "_run_oracle_checks_parallel",
        lambda sids, workers, mode="official_consolidation", progress=False: {
            "2000/1": {"top_diagnosis": "ORACLE_STALE"},
            "2000/2": {"top_diagnosis": "REPLAY_EXTRA"},
            "2000/3": {"top_diagnosis": "UNKNOWN"},
        },
    )
    diagnostic_counts: dict[str, dict[str, int]] = {}

    summary = bench._oracle_stale_adjusted_stats(
        [
            (1, "2000/1", 0.8, "OK", 0.1),
            (1, "2000/2", 0.7, "OK", 0.1),
            (1, "2000/3", 0.6, "OK", 0.1),
        ],
        workers=1,
        diagnostic_counts=diagnostic_counts,
    )

    assert summary is not None
    assert summary["excluded"] == ["2000/1"]
    assert diagnostic_counts["2000/1"]["oracle_check:top_diagnosis:ORACLE_STALE"] == 1
    assert diagnostic_counts["2000/2"]["oracle_check:top_diagnosis:REPLAY_EXTRA"] == 1
    assert "2000/3" not in diagnostic_counts


def test_print_bench_diagnostic_tier_rollup(capsys: pytest.CaptureFixture[str]) -> None:
    from lawvm.tools import bench_diagnostic_tiers

    bench_diagnostic_tiers.print_bench_diagnostic_tier_rollup(
        Counter(
            {
                "timeline_robust:content_mismatch": 2,
                "finding:ELAB.REGISTRY_STAGE": 40,
            }
        )
    )
    out = capsys.readouterr().out
    assert "Diagnostic tier rollup:" in out
    assert "timeline_robust: content_mismatch×2" in out
    assert "audit: registry_stage×40" in out


def test_enrich_bench_finding_counts_splits_timeline_by_tier() -> None:
    from lawvm.core.phase_result import Finding, OBSERVATION_ROLE

    master = type(
        "ReplayResult",
        (),
        {
            "findings": (
                Finding(
                    kind="timeline_invariant_violation",
                    role=OBSERVATION_ROLE,
                    stage="timeline_invariants",
                    blocking=False,
                    detail={"code": "overlapping_permanent", "tier": "robust"},
                ),
                Finding(
                    kind="timeline_invariant_violation",
                    role=OBSERVATION_ROLE,
                    stage="timeline_invariants",
                    blocking=False,
                    detail={"code": "duplicate_permanent_version_row", "tier": "materialization_variant"},
                ),
            ),
            "source_adjudication": None,
        },
    )()

    counts = bench_diagnostic_tiers.enrich_bench_finding_counts(master)
    assert counts["timeline_robust:overlapping_permanent"] == 1
    assert counts["timeline_variant:duplicate_permanent_version_row"] == 1


def test_merge_bench_structural_diagnostics_counts_event_families() -> None:
    counts = bench._merge_bench_structural_diagnostics(
        Counter({"coverage_degraded": 1}),
        {"missing_section": 2, "extra_section": 0},
    )

    assert counts["coverage_degraded"] == 1
    assert counts["structural:missing_section"] == 2
    assert counts["structural:extra_section"] == 0


def test_score_one_with_warning_summary_preserves_structural_event_counts(monkeypatch) -> None:
    monkeypatch.setattr(bench, "is_known_missing_source", lambda sid: False)
    monkeypatch.setattr(
        bench,
        "_run_replay_with_bench_warning_capture",
        lambda sid, *, mode, diagnostic_replay, replay_kwargs: (_DummyReplay(), Counter({"coverage_degraded": 1})),
    )
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda sid, master, *, text_scores: bench._BenchSemanticScore(
            structural_similarity=0.9,
            adjusted_levenshtein_similarity=0.95,
            event_counts=Counter({"missing_section": 2}),
        ),
    )

    sid, sim, status, lev_sim, counts = bench._score_one_with_warning_summary("2000/1")

    assert (sid, sim, status, lev_sim) == ("2000/1", 0.9, "OK", 0.95)
    assert counts["coverage_degraded"] == 1
    assert counts["structural:missing_section"] == 2


def test_score_one_with_warning_summary_can_skip_text_score(monkeypatch) -> None:
    monkeypatch.setattr(bench, "is_known_missing_source", lambda sid: False)
    monkeypatch.setattr(
        bench,
        "_run_replay_with_bench_warning_capture",
        lambda sid, *, mode, diagnostic_replay, replay_kwargs: (_DummyReplay(), Counter()),
    )
    def fake_semantic_score(sid, master, *, text_scores):
        assert text_scores is False
        return bench._BenchSemanticScore(
            structural_similarity=0.9,
            adjusted_levenshtein_similarity=-1.0,
            event_counts=Counter(),
        )

    monkeypatch.setattr(bench, "_semantic_section_score", fake_semantic_score)

    sid, sim, status, lev_sim, _counts = bench._score_one_with_warning_summary(
        "2000/1",
        text_scores=False,
    )

    assert (sid, sim, status, lev_sim) == ("2000/1", 0.9, "OK", -1.0)


def test_run_benchmark_can_emit_diagnostic_summaries_for_persistence(monkeypatch) -> None:
    monkeypatch.setattr(
        bench,
        "_score_one_with_warning_summary",
        lambda sid, mode="official_consolidation", *, diagnostic_replay=False, fast=False, text_scores=True: (
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
    assert diagnostics_out == {"2000/1": "  warnings: audit: coverage_degraded×2"}


def test_run_benchmark_can_skip_levenshtein_collection(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        bench,
        "_score_one_with_warning_summary",
        lambda sid, mode="official_consolidation", *, diagnostic_replay=False, fast=False, text_scores=True: (
            sid,
            0.9,
            "OK",
            -1.0,
            {},
        ),
    )

    results, lev_sims = bench._run_benchmark(
        [(1, "2000/1")],
        verbose=True,
        workers=1,
        text_scores=False,
    )

    assert results[0][:4] == (1, "2000/1", 0.9, "OK")
    assert lev_sims is None
    assert " lev " not in capsys.readouterr().out


def test_fi_bench_main_no_save_skips_persistence(tmp_path, monkeypatch, capsys) -> None:
    corpus = tmp_path / "corpus.csv"
    corpus.write_text("amendments,statute_id\n1,2000/1\n", encoding="utf-8")
    monkeypatch.setattr(
        bench,
        "_run_benchmark",
        lambda *args, **kwargs: ([(1, "2000/1", 0.9, "OK", 0.1)], None),
    )
    monkeypatch.setattr(bench, "_show_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_show_worst", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_show_errors", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_load_verified_statutes", lambda: {})
    monkeypatch.setattr(bench, "_save_run", lambda *args, **kwargs: pytest.fail("run CSV should not be saved"))
    monkeypatch.setattr(bench, "_append_history", lambda *args, **kwargs: pytest.fail("history should not be appended"))
    monkeypatch.setattr(
        bench,
        "_write_bench_evidence_surface",
        lambda *args, **kwargs: pytest.fail("evidence should not be written"),
    )
    monkeypatch.setattr(
        bench,
        "_save_bench_diagnostic_sidecar",
        lambda *args, **kwargs: pytest.fail("diagnostics sidecar should not be written"),
    )

    bench.main(
        argparse.Namespace(
            corpus=str(corpus),
            label="nosave",
            top=5,
            no_save=True,
            no_text_scores=True,
            parallel=None,
        )
    )

    assert "Run not saved (--no-save)" in capsys.readouterr().out


def test_save_run_persists_diagnostics_summary_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bench, "_runs_dir", lambda: tmp_path)

    path = bench._save_run(
        [(1, "2000/1", 0.9, "OK", 1.23)],
        "demo",
        "2026-05-12T12:00:00Z",
        lev_sims={"2000/1": 0.95},
        diagnostic_summaries={"2000/1": "  diagnostics: operative: ELAB.SOURCE_PATHOLOGY×1"},
    )

    rows = list(csv.DictReader(path.open(newline="")))
    assert rows[0]["diagnostics_summary"] == "  diagnostics: operative: ELAB.SOURCE_PATHOLOGY×1"
    assert rows[0]["lev_similarity"] == "0.950000"


def test_bench_tail_proof_summary_uses_display_tier_and_mixed_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda sid, mode="legal_pit", include_bisect=True, **_kwargs: {
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
        lambda sids, workers, mode="official_consolidation", progress=False: {
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


# ---------------------------------------------------------------------------
# FI unified-summary env-var gate (default = legacy output unchanged)
# ---------------------------------------------------------------------------


def test_fi_unified_summary_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(bench._FI_BENCH_UNIFIED_ENV, raising=False)
    assert bench._fi_bench_unified_enabled() is False


def test_fi_unified_summary_enabled_by_env(monkeypatch) -> None:
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv(bench._FI_BENCH_UNIFIED_ENV, truthy)
        assert bench._fi_bench_unified_enabled() is True
    monkeypatch.setenv(bench._FI_BENCH_UNIFIED_ENV, "0")
    assert bench._fi_bench_unified_enabled() is False


def test_fi_unit_results_from_rows_maps_axes_and_statuses() -> None:
    from lawvm.core.bench_contract import BenchStatus

    flat = [
        ("2004/1037", 0.9, "OK"),  # scored, text-only
        ("2012/916", -1.0, "NO_TRUTH"),  # non-scored exclusion
        ("2018/1", -1.0, "SOURCE_UNAVAILABLE"),  # non-scored exclusion
        ("2020/5", -1.0, "ValueError: boom"),  # genuine crash
    ]
    units = bench._fi_unit_results_from_rows(flat)
    by_id = {u.unit_id: u for u in units}

    scored = by_id["2004/1037"]
    assert scored.status is BenchStatus.SCORED
    assert scored.text_err == pytest.approx(0.1)
    assert scored.structural_err is None  # no section score on a text-only run
    assert by_id["2012/916"].status is BenchStatus.NO_TRUTH
    assert by_id["2018/1"].status is BenchStatus.SOURCE_UNAVAILABLE
    crash = by_id["2020/5"]
    assert crash.status is BenchStatus.CRASH
    assert crash.witnesses == ("ValueError: boom",)


def test_fi_unit_results_fold_in_section_structural_axis() -> None:
    # When section scoring ran, the structural axis is the worse axis and binds
    # the worst-of headline (Liebig): section 0.80 (20% err) > text 0.95 (5% err).
    flat = [("2004/1037", 0.95, "OK")]
    units = bench._fi_unit_results_from_rows(flat, section_sims={"2004/1037": 0.80})
    u = units[0]
    assert u.structural_err == pytest.approx(0.20)
    assert u.text_err == pytest.approx(0.05)
    assert u.headline_error() == pytest.approx(0.20)


def _drive_fi_main(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.csv"
    corpus.write_text("amendments,statute_id\n1,2000/1\n", encoding="utf-8")
    monkeypatch.setattr(
        bench,
        "_run_benchmark",
        lambda *args, **kwargs: ([(1, "2000/1", 0.9, "OK", 0.1)], None),
    )
    monkeypatch.setattr(bench, "_show_worst", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_show_errors", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_load_verified_statutes", lambda: {})
    monkeypatch.setattr(bench, "_save_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_append_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_write_bench_evidence_surface", lambda *args, **kwargs: None)
    monkeypatch.setattr(bench, "_save_bench_diagnostic_sidecar", lambda *args, **kwargs: None)
    bench.main(
        argparse.Namespace(
            corpus=str(corpus),
            label="gate",
            top=5,
            no_save=True,
            no_text_scores=True,
            parallel=None,
        )
    )


def test_fi_main_default_output_has_no_unified_summary(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(bench._FI_BENCH_UNIFIED_ENV, raising=False)
    _drive_fi_main(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    # Legacy summary is present; the unified summary is NOT (default unchanged).
    assert "=== BENCHMARK SUMMARY" in out
    assert "UNIFIED BENCH SUMMARY" not in out


def test_fi_main_unified_summary_renders_under_env_var(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(bench._FI_BENCH_UNIFIED_ENV, "1")
    _drive_fi_main(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    # Both summaries present: legacy unchanged AND the opt-in unified headline.
    assert "=== BENCHMARK SUMMARY" in out
    assert "=== UNIFIED BENCH SUMMARY" in out
    assert "jurisdiction=fi" in out
    assert "1 scored" in out
    assert "Mean error : 10.00%" in out


def test_fi_unified_summary_render_is_gated_and_meaningful(capsys) -> None:
    # The render path itself (gating is exercised in main; here we assert the
    # rendered headline carries the worst-of mean error + partition + honesty).
    flat = [
        ("a", 0.90, "OK"),  # text 10% err
        ("b", -1.0, "NO_TRUTH"),  # excluded
        ("c", -1.0, "boom"),  # crash
    ]
    bench._show_unified_summary_fi(flat, "demo")
    out = capsys.readouterr().out
    assert "=== UNIFIED BENCH SUMMARY" in out
    assert "jurisdiction=fi" in out
    assert "1 scored" in out
    assert "crashed: 1" in out
    assert "excluded(non-scored): 1" in out
    assert "Mean error : 10.00%" in out
