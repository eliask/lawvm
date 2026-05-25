from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sys
import types
import pytest
from lawvm.uk_legislation import uk_prefetch

from lawvm.tools import cli, uk_bench, uk_candidates, uk_effect, uk_effects, uk_eids, uk_replay
from lawvm.tools.replay_payloads import build_uk_replay_payload
from scripts import acquire_uk_corpus, fetch_uk_affecting_acts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_uk_archives_default_to_data_dir_in_tool_modules() -> None:
    expected = _repo_root() / "data" / "uk_legislation.farchive"

    assert uk_bench._DEFAULT_DB == expected
    assert uk_candidates._DEFAULT_DB == expected
    assert uk_effect._DEFAULT_DB == expected
    assert uk_effects._DEFAULT_DB == expected
    assert uk_replay._DEFAULT_DB == expected
    assert uk_eids._DEFAULT_DB == expected
    assert fetch_uk_affecting_acts._DEFAULT_DB == expected
    assert acquire_uk_corpus._DEFAULT_ARCHIVE == expected


def test_uk_cli_help_strings_reference_data_archive_default(capsys) -> None:
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-replay", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--include-enacted-affecting" in text
    assert "--commencement" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-fetch-affecting", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--include-enacted-affecting" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-effect", "ukpga/2000/10", "key", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--json" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-effects", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--summary-only" in text
    assert "--candidate-only" in text
    assert "--non-candidate-only" in text
    assert "--json" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-eids", "ukpga/2000/10", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--json" in text

    with pytest.raises(SystemExit):
        parser.parse_args(["uk-candidates", "--help"])
    text = capsys.readouterr().out
    assert "data/uk_legislation.farchive" in text
    assert "--json" in text
    assert "--summary-only" in text
    assert "--effect-budget" in text
    assert "--residual-budget" in text


def test_uk_bench_rejects_negative_limit_before_archive_access(capsys) -> None:
    args = Namespace(
        history=False,
        show=None,
        compare=None,
        db="does-not-matter.farchive",
        limit=-1,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 2
    assert "--limit must be zero or a positive integer" in capsys.readouterr().err


def test_uk_bench_parser_accepts_replay_regime_flags() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "bench",
            "-j",
            "uk",
            "--replay",
            "--no-metadata-backfill",
            "--no-oracle-alignment",
            "--applicability-mode",
            "effective_date_only",
            "--authority-mode",
            "source_text_only",
        ]
    )

    assert args.uk_allow_metadata_backfill is False
    assert args.uk_allow_oracle_alignment is False
    assert args.uk_applicability_mode == "effective_date_only"
    assert args.uk_authority_mode == "source_text_only"


def test_uk_bench_parallel_help_describes_memory_safe_replay_default(capsys) -> None:
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["bench", "-j", "uk", "--help"])

    text = capsys.readouterr().out
    assert "UK replay default is memory-safe, max 4" in text
    assert "UK/EE default: cpu_count" not in text


def test_uk_bench_parser_accepts_no_save_smoke_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--limit", "1", "--no-save"])

    assert args.no_save is True
    assert args.limit == 1


def test_uk_bench_parser_accepts_summary_only_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--summary-only"])

    assert args.summary_only is True


def test_uk_bench_parser_accepts_worker_recycling_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--worker-max-tasks", "50"])

    assert args.worker_max_tasks == 50


def test_uk_bench_parser_accepts_curated_corpus_flags() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "bench",
            "-j",
            "uk",
            "--curate-corpus",
            "data/uk/bench_corpus_tight.csv",
            "--curate-size",
            "150",
        ]
    )

    assert args.curate_corpus == "data/uk/bench_corpus_tight.csv"
    assert args.curate_size == 150


def test_uk_bench_parser_accepts_source_closure_stats_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--corpus-stats", "--source-closure-stats"])

    assert args.corpus_stats is True
    assert args.source_closure_stats is True


def test_uk_bench_parser_accepts_source_closure_curation_gate() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "bench",
            "-j",
            "uk",
            "--curate-corpus",
            "data/uk/bench_corpus_tight_source_closed.csv",
            "--curate-require-source-closure",
        ]
    )

    assert args.curate_corpus == "data/uk/bench_corpus_tight_source_closed.csv"
    assert args.curate_require_source_closure is True


def test_uk_bench_parser_accepts_curated_corpus_preset() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--curate-preset", "modern-tight"])

    assert args.curate_preset == "modern-tight"
    assert args.curate_corpus is None
    assert args.curate_size is None


def test_uk_bench_parser_accepts_hard_curated_corpus_preset() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["bench", "-j", "uk", "--curate-preset", "hard-tight"])

    assert args.curate_preset == "hard-tight"


def test_uk_bench_statute_filter_runs_single_corpus_row(monkeypatch, tmp_path, capsys) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    seen: dict[str, object] = {}

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(
        uk_bench,
        "_load_corpus_csv",
        lambda *, types, archive: [
            {"statute_id": "ukpga/2000/1", "type": "ukpga", "year": 2000},
            {"statute_id": "ukpga/2001/2", "type": "ukpga", "year": 2001},
        ],
    )

    def fake_run_bench(
        corpus: list[dict[str, object]],
        archive: DummyArchive,
        **kwargs: object,
    ) -> list[object]:
        seen["corpus"] = corpus
        seen["archive_closed_before_run"] = archive.closed
        return []

    monkeypatch.setattr(uk_bench, "_run_bench", fake_run_bench)
    monkeypatch.setattr(uk_bench, "_print_report", lambda results, label, **kwargs: None)
    monkeypatch.setattr(uk_bench, "_save_results", lambda results, label, **kwargs: None)

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=None,
        types=None,
        label="one-statute",
        min_year=None,
        max_year=None,
        statute="ukpga/2001/2",
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=True,
    )

    uk_bench.main(args)

    assert seen["corpus"] == [{"statute_id": "ukpga/2001/2", "type": "ukpga", "year": 2001}]
    assert seen["archive_closed_before_run"] is False
    assert "Statute filter: ukpga/2001/2 → 1 statutes" in capsys.readouterr().out


def test_uk_bench_statute_filter_rejects_missing_statute(monkeypatch, tmp_path, capsys) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(
        uk_bench,
        "_load_corpus_csv",
        lambda *, types, archive: [
            {"statute_id": "ukpga/2000/1", "type": "ukpga", "year": 2000},
        ],
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=None,
        types=None,
        label="missing-statute",
        min_year=None,
        max_year=None,
        statute="ukpga/2099/999",
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 1
    assert "ERROR: statute 'ukpga/2099/999' not found in UK bench corpus." in capsys.readouterr().err


def test_uk_bench_rejects_empty_filtered_corpus_before_saving(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(
        uk_bench,
        "_load_corpus_csv",
        lambda *, types, archive: [
            {"statute_id": "ukpga/2000/1", "type": "ukpga", "year": 2000},
        ],
    )
    monkeypatch.setattr(
        uk_bench,
        "_save_results",
        lambda results, label: pytest.fail("_save_results should not be called"),
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=None,
        types=None,
        label="empty-filter",
        min_year=2099,
        max_year=None,
        statute=None,
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=False,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 1
    assert "ERROR: no statutes remain after UK bench filters." in capsys.readouterr().err


def test_uk_bench_limit_zero_allows_empty_diagnostic_budget_after_filters(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    seen: dict[str, object] = {}

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(
        uk_bench,
        "_load_corpus_csv",
        lambda *, types, archive: [
            {"statute_id": "ukpga/2000/1", "type": "ukpga", "year": 2000},
        ],
    )

    def fake_run_bench(
        corpus: list[dict[str, object]],
        archive: DummyArchive,
        **kwargs: object,
    ) -> list[object]:
        seen["corpus"] = corpus
        return []

    monkeypatch.setattr(uk_bench, "_run_bench", fake_run_bench)
    monkeypatch.setattr(uk_bench, "_print_report", lambda results, label, **kwargs: None)
    monkeypatch.setattr(uk_bench, "_save_results", lambda results, label, **kwargs: None)

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=0,
        types=None,
        label="empty-budget",
        min_year=2099,
        max_year=None,
        statute=None,
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=True,
    )

    uk_bench.main(args)

    assert seen["corpus"] == []
    out = capsys.readouterr().out
    assert "Year filter: 2099-... → 0 statutes" in out
    assert "Limited to first 0 statutes" in out


def test_uk_bench_rejects_nonpositive_parallel_before_archive_access(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_bench,
        "Farchive",
        lambda *args, **kwargs: pytest.fail("Farchive should not be opened"),
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db="does-not-matter.farchive",
        limit=None,
        types=None,
        label="bad-parallel",
        min_year=None,
        max_year=None,
        statute=None,
        replay=False,
        no_commencement=True,
        parallel=0,
        no_save=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 2
    assert "error: --parallel must be a positive integer" in capsys.readouterr().err


def test_uk_bench_rejects_nonpositive_worker_max_tasks_before_archive_access(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_bench,
        "Farchive",
        lambda *args, **kwargs: pytest.fail("Farchive should not be opened"),
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db="does-not-matter.farchive",
        limit=None,
        types=None,
        label="bad-worker-max-tasks",
        min_year=None,
        max_year=None,
        statute=None,
        replay=False,
        no_commencement=True,
        parallel=None,
        worker_max_tasks=0,
        no_save=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 2
    assert "error: --worker-max-tasks must be a positive integer" in capsys.readouterr().err


def test_uk_bench_rejects_inverted_year_range_before_archive_access(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_bench,
        "Farchive",
        lambda *args, **kwargs: pytest.fail("Farchive should not be opened"),
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db="does-not-matter.farchive",
        limit=None,
        types=None,
        label="bad-years",
        min_year=2025,
        max_year=2020,
        statute=None,
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_bench.main(args)

    assert excinfo.value.code == 2
    assert "error: --min-year must be less than or equal to --max-year" in capsys.readouterr().err


def test_uk_replay_regime_flags_are_exposed_on_all_diagnostic_entrypoints() -> None:
    parser = cli._build_parser()

    evidence_args = parser.parse_args(
        [
            "-j",
            "uk",
            "evidence",
            "ukpga/2000/1",
            "--source-first-candidate",
            "--no-metadata-only-effects",
        ]
    )
    prove_args = parser.parse_args(
        [
            "-j",
            "uk",
            "prove-oracle",
            "ukpga/2000/1",
            "--source-first-candidate",
            "--no-metadata-only-effects",
        ]
    )
    review_args = parser.parse_args(
        [
            "-j",
            "uk",
            "evidence-review",
            "--statute-id",
            "ukpga/2000/1",
            "--source-first-candidate",
            "--no-metadata-only-effects",
        ]
    )
    replay_args = parser.parse_args(
        [
            "replay",
            "-j",
            "uk",
            "ukpga/2000/1",
            "--as-of",
            "2024-01-01",
            "--source-first-candidate",
        ]
    )
    uk_replay_args = parser.parse_args(
        [
            "uk-replay",
            "ukpga/2000/1",
            "--source-first-candidate",
            "--fetch-missing",
            "--include-enacted-affecting",
        ]
    )
    bench_args = parser.parse_args(["bench", "-j", "uk", "--replay", "--source-first-candidate"])

    for args in (evidence_args, prove_args, review_args, replay_args, uk_replay_args, bench_args):
        assert args.uk_source_first_candidate is True

    assert evidence_args.uk_allow_metadata_only_effects is False
    assert prove_args.uk_allow_metadata_only_effects is False
    assert review_args.uk_allow_metadata_only_effects is False
    assert not hasattr(replay_args, "uk_allow_metadata_only_effects")
    assert not hasattr(uk_replay_args, "uk_allow_metadata_only_effects")
    assert not hasattr(bench_args, "uk_allow_metadata_only_effects")
    assert uk_replay_args.fetch_missing is True
    assert uk_replay_args.include_enacted_affecting is True


def test_uk_replay_parser_accepts_commencement_diagnostic_lane() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["uk-replay", "asp/2002/11", "--commencement"])

    assert args.commencement is True


def test_uk_replay_commencement_score_lens_is_symmetric() -> None:
    oracle_eids = {"root", "s1", "s2"}
    commenced_eids = {"s1"}

    assert uk_replay._commenced_oracle_eids(oracle_eids, commenced_eids) == {"s1"}
    assert uk_replay._score_commenced_eids({"s1"}, {"s1"}) == 1.0
    assert uk_replay._score_commenced_eids(set(), {"s1"}) == -1.0


def test_uk_replay_commencement_summary_preserves_observation_rules() -> None:
    summary = uk_replay._uk_commencement_score_summary(
        enabled=True,
        applicability_mode="effective_date_plus_feed_applied",
        observations=[
            {
                "rule_id": "uk_commencement_undated_effects_block_self_commencement",
                "phase": "commencement_filter",
            }
        ],
        commenced_eids=set(),
        commenced_enacted_eids=set(),
        commenced_replayed_eids=set(),
        commenced_oracle_eids=set(),
    )

    assert summary["rule_id"] == "uk_replay_commencement_score_lane"
    assert summary["commencement_score"] == -1.0
    assert summary["replay_commencement_score"] == -1.0
    assert summary["observation_rule_counts"] == {
        "uk_commencement_undated_effects_block_self_commencement": 1
    }
    assert uk_replay._uk_commencement_score_text_lines(summary) == [
        (
            "Commencement EID score: enacted=not computed replay=not computed "
            "commenced=0 oracle=0"
        ),
        "Commencement observations: uk_commencement_undated_effects_block_self_commencement=1",
    ]


def test_uk_replay_commencement_summary_can_use_replay_compare_oracle_surface() -> None:
    summary = uk_replay._uk_commencement_score_summary(
        enabled=True,
        applicability_mode="effective_date_plus_feed_applied",
        commenced_eids={"schedule-2", "schedule-2-paragraph-1"},
        commenced_enacted_eids={"schedule-2", "schedule-2-paragraph-1"},
        commenced_replayed_eids={"schedule-2"},
        commenced_oracle_eids={"schedule-2", "schedule-2-paragraph-1"},
        replay_commencement_oracle_eids={"schedule-2"},
    )

    assert summary["commencement_score"] == 1.0
    assert summary["replay_commencement_score"] == 1.0
    assert summary["commenced_replayed_common_count"] == 1


def test_uk_effects_parser_accepts_diagnostic_family_filters() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-effects",
            "asc/2021/1",
            "--source-pathology",
            "instruction_text_reused_as_payload",
            "--lowering-rule",
            "uk_effect_overlap_substitution_unlowered",
            "--source-acquisition-rule",
            "uk_affecting_act_xml_missing_rejected",
            "--manual-compile-status",
            "manual_compile_candidate",
            "--manual-compile-rule",
            "uk_manual_frontier_heading_facet_candidate",
            "--source-first-candidate",
            "--no-metadata-only-effects",
            "--evidence-jsonl",
            ".tmp/uk-manual.jsonl",
        ]
    )

    assert args.source_pathology == "instruction_text_reused_as_payload"
    assert args.lowering_rule == "uk_effect_overlap_substitution_unlowered"
    assert args.source_acquisition_rule == "uk_affecting_act_xml_missing_rejected"
    assert args.manual_compile_status == "manual_compile_candidate"
    assert args.manual_compile_rule == "uk_manual_frontier_heading_facet_candidate"
    assert args.uk_source_first_candidate is True
    assert args.uk_allow_metadata_only_effects is False
    assert args.evidence_jsonl == ".tmp/uk-manual.jsonl"


def test_uk_candidates_parser_accepts_manual_compile_evidence_jsonl() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-candidates",
            "--label",
            "demo",
            "--manual-compile-evidence-jsonl",
            ".tmp/uk-candidates-manual.jsonl",
        ]
    )

    assert args.manual_compile_evidence_jsonl == ".tmp/uk-candidates-manual.jsonl"
    assert args.manual_compile_evidence_status is None


def test_uk_candidates_parser_accepts_replay_adjudication_evidence_jsonl() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-candidates",
            "--label",
            "demo",
            "--replay-adjudication-kind",
            "text_duplication_warning",
            "--replay-adjudication-evidence-jsonl",
            ".tmp/uk-replay-adjudications.jsonl",
        ]
    )

    assert args.replay_adjudication_kind == ["text_duplication_warning"]
    assert args.replay_adjudication_evidence_jsonl == (
        ".tmp/uk-replay-adjudications.jsonl"
    )


def test_uk_candidates_parser_accepts_residual_claim_evidence_jsonl() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-candidates",
            "--label",
            "demo",
            "--residual-claim-evidence-jsonl",
            ".tmp/uk-residual-claims.jsonl",
        ]
    )

    assert args.residual_claim_evidence_jsonl == ".tmp/uk-residual-claims.jsonl"


def test_uk_candidates_parser_accepts_manual_compile_evidence_statuses() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-candidates",
            "--label",
            "demo",
            "--manual-compile-evidence-jsonl",
            ".tmp/uk-candidates-frontier.jsonl",
            "--manual-compile-evidence-status",
            "manual_compile_candidate",
            "--manual-compile-evidence-status",
            "actionable",
        ]
    )

    assert args.manual_compile_evidence_status == [
        "manual_compile_candidate",
        "actionable",
    ]


def test_uk_manual_frontier_validate_parser_defaults() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-manual-frontier-validate",
            ".tmp/uk-manual-frontier.jsonl",
            "--json",
            "--summary-only",
            "--validation-jsonl",
            ".tmp/uk-manual-validation.jsonl",
            "--remaining-jsonl",
            ".tmp/uk-manual-remaining.jsonl",
            "--fail-on-stale",
            "--fail-on-validation-error",
            "--fail-on-remaining",
        ]
    )

    assert args.command == "uk-manual-frontier-validate"
    assert args.input == ".tmp/uk-manual-frontier.jsonl"
    assert args.db is None
    assert args.json is True
    assert args.summary_only is True
    assert args.validation_jsonl == ".tmp/uk-manual-validation.jsonl"
    assert args.remaining_jsonl == ".tmp/uk-manual-remaining.jsonl"
    assert args.fail_on_stale is True
    assert args.fail_on_validation_error is True
    assert args.fail_on_remaining is True


def test_uk_replay_payload_preserves_effect_source_diagnostic_lanes() -> None:
    payload = build_uk_replay_payload(
        statute_id="ukpga/2000/1",
        pit_date=None,
        enacted_only=False,
        db_path="/tmp/uk.farchive",
        n_effects=2,
        n_ops=1,
        similarity=None,
        comparison_class=None,
        oracle_available=False,
        n_provisions=0,
        n_versions=None,
        pit_materialized_eids=None,
        timeline_mode="states_first",
        effect_source_pathology_observations=[
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "source_pathology": "missing_extracted_source",
                "blocking": False,
            }
        ],
        manual_compile_frontier_observations=[
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
                "blocking": False,
            }
        ],
        source_acquisition_rejections=[
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            }
        ],
        uk_commencement_summary={
            "enabled": True,
            "rule_id": "uk_replay_commencement_score_lane",
            "replay_commencement_score": 1.0,
        },
    )

    assert payload["uk_commencement_summary"] == {
        "enabled": True,
        "rule_id": "uk_replay_commencement_score_lane",
        "replay_commencement_score": 1.0,
    }
    assert payload["compile_observation_lane_counts"]["effect_source_pathology"] == 1
    assert payload["compile_observation_lane_counts"]["manual_compile_frontier"] == 1
    assert payload["compile_observation_lane_counts"]["source_acquisition"] == 1
    assert payload["blocking_compile_rejection_lane_counts"]["effect_source_pathology"] == 0
    assert payload["blocking_compile_rejection_lane_counts"]["manual_compile_frontier"] == 0
    assert payload["blocking_compile_rejection_lane_counts"]["source_acquisition"] == 1
    assert payload["compile_observation_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_effect_source_pathology_classified": 1,
        "uk_manual_compile_frontier_classified": 1,
    }
    assert payload["manual_compile_status_counts"] == {"manual_compile_candidate": 1}
    assert payload["manual_compile_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 1,
    }
    assert payload["blocking_compile_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert payload["compile_observations"]["effect_source_pathology"][0]["source_pathology"] == (
        "missing_extracted_source"
    )
    assert payload["compile_observations"]["manual_compile_frontier"][0][
        "manual_compile_status"
    ] == "manual_compile_candidate"
    assert payload["compile_rejections"]["source_acquisition"][0]["rule_id"] == (
        "uk_affecting_act_xml_missing_rejected"
    )


def test_uk_replay_regime_payload_uses_contract_lanes() -> None:
    payload = uk_replay._uk_replay_regime_payload(
        enacted_only=False,
        oracle_alignment_enabled=True,
        metadata_backfill_op_count=0,
        allow_metadata_backfill=True,
        allow_metadata_only_effects=True,
        applicability_mode="effective_date_plus_feed_applied",
        authority_mode="current_mixed",
    )

    assert payload["semantic_replay_lane"] == "effects_assisted_replay"
    assert payload["oracle_alignment_lane"] == "oracle_alignment_adapter"
    assert payload["source_purity_lane"] == "source_backed_with_oracle_adapter"
    assert payload["source_semantics_clean"] is False
    assert payload["source_first_candidate"] is False
    assert payload["source_first_candidate_reasons"] == [
        "oracle_alignment_adapter_active",
        "metadata_only_effects_enabled",
        "authority_mode_not_source_text_only",
    ]


def test_uk_replay_regime_payload_marks_source_first_candidate_without_legacy_lane() -> None:
    payload = uk_replay._uk_replay_regime_payload(
        enacted_only=False,
        oracle_alignment_enabled=False,
        metadata_backfill_op_count=0,
        allow_metadata_backfill=False,
        allow_metadata_only_effects=False,
        applicability_mode="effective_date_plus_feed_applied",
        authority_mode="source_text_only",
    )

    assert payload["semantic_replay_lane"] == "effects_assisted_replay"
    assert payload["source_purity_lane"] == "source_backed_effects_assisted"
    assert payload["source_semantics_clean"] is True
    assert payload["source_first_candidate"] is True
    assert payload["source_first_candidate_reasons"] == []


def test_uk_replay_regime_payload_source_first_requires_no_metadata_only_effects() -> None:
    payload = uk_replay._uk_replay_regime_payload(
        enacted_only=False,
        oracle_alignment_enabled=False,
        metadata_backfill_op_count=0,
        allow_metadata_backfill=False,
        allow_metadata_only_effects=True,
        applicability_mode="effective_date_plus_feed_applied",
        authority_mode="source_text_only",
    )

    assert payload["source_purity_lane"] == "source_backed_effects_assisted"
    assert payload["source_semantics_clean"] is False
    assert payload["source_first_candidate"] is False
    assert payload["source_first_candidate_reasons"] == ["metadata_only_effects_enabled"]


def test_uk_replay_regime_payload_source_semantics_clean_requires_source_authority() -> None:
    payload = uk_replay._uk_replay_regime_payload(
        enacted_only=False,
        oracle_alignment_enabled=False,
        metadata_backfill_op_count=0,
        allow_metadata_backfill=False,
        allow_metadata_only_effects=False,
        applicability_mode="effective_date_plus_feed_applied",
        authority_mode="current_mixed",
    )

    assert payload["source_purity_lane"] == "source_backed_effects_assisted"
    assert payload["source_semantics_clean"] is False
    assert payload["source_first_candidate"] is False
    assert payload["source_first_candidate_reasons"] == [
        "authority_mode_not_source_text_only",
    ]


def test_uk_replay_regime_payload_preserves_source_unavailable_early_error_lane() -> None:
    payload = uk_replay._uk_replay_regime_payload(
        enacted_only=False,
        oracle_alignment_enabled=False,
        metadata_backfill_op_count=0,
        allow_metadata_backfill=True,
        allow_metadata_only_effects=True,
        applicability_mode="effective_date_plus_feed_applied",
        authority_mode="source_text_only",
        source_unavailable_reason="enacted_xml_unavailable",
    )

    assert payload["semantic_replay_lane"] == "not_run_source_unavailable"
    assert payload["oracle_alignment_lane"] == "not_run_source_unavailable"
    assert payload["source_purity_lane"] == "not_run_source_unavailable"
    assert payload["source_semantics_clean"] is False
    assert payload["source_first_candidate"] is False
    assert payload["source_first_candidate_reasons"] == ["source_unavailable"]


def test_uk_replay_compile_text_splits_source_pathology_and_acquisition_lanes() -> None:
    lines = uk_replay._uk_compile_rejection_text_lines(
        effect_feed_parse_rejections=[],
        effect_source_pathology_observations=[
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "source_pathology": "missing_extracted_source",
                "blocking": False,
            }
        ],
        manual_compile_frontier_observations=[
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
                "blocking": False,
            }
        ],
        source_acquisition_rejections=[
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            }
        ],
        lowering_rejections=[],
        authority_rejections=[],
    )

    assert lines[0] == (
        "Compile observations: source_parse=0 feed_parse=0 "
        "effect_source_pathology=1 manual_compile_frontier=1 "
        "source_acquisition=1 lowering=0 authority=0 total=3"
    )
    assert lines[1] == (
        "Compile rejections: source_parse=0 feed_parse=0 "
        "effect_source_pathology=0 manual_compile_frontier=0 "
        "source_acquisition=1 lowering=0 authority=0 blocking=1"
    )
    assert "effect_source_pathology rules: uk_effect_source_pathology_classified=1" in lines
    assert "manual_compile_frontier rules: uk_manual_compile_frontier_classified=1" in lines
    assert "manual_compile_frontier statuses: manual_compile_candidate=1" in lines
    assert (
        "manual_compile_frontier manual rules: "
        "uk_manual_frontier_heading_facet_candidate=1"
    ) in lines
    assert "source_acquisition rules: uk_affecting_act_xml_missing_rejected=1" in lines
    assert (
        "Compile blocking rejections: source_parse=0 feed_parse=0 "
        "effect_source_pathology=0 manual_compile_frontier=0 "
        "source_acquisition=1 lowering=0 authority=0"
    ) in lines
    assert "blocking source_acquisition rules: uk_affecting_act_xml_missing_rejected=1" in lines


def test_bench_rejects_uk_regime_flags_for_non_uk(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "bench",
            "-j",
            "fi",
            "--source-first-candidate",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 2
    assert "UK replay regime flags on 'bench' are only supported with -j uk" in capsys.readouterr().err


def test_uk_bench_limit_zero_preserves_empty_diagnostic_budget(monkeypatch, tmp_path) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    seen: dict[str, object] = {}

    def fake_load_corpus_csv(*, types: object, archive: DummyArchive) -> list[dict[str, object]]:
        seen["load_types"] = types
        seen["load_archive_closed_before_run"] = archive.closed
        return [
            {"statute_id": "ukpga/2000/1", "year": "2000"},
            {"statute_id": "ukpga/2001/2", "year": "2001"},
        ]

    def fake_run_bench(
        corpus: list[dict[str, object]],
        archive: DummyArchive,
        **kwargs: object,
    ) -> list[object]:
        seen["corpus"] = corpus
        seen["archive_closed_before_run"] = archive.closed
        seen["run_kwargs"] = kwargs
        return []

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(uk_bench, "_load_corpus_csv", fake_load_corpus_csv)
    monkeypatch.setattr(uk_bench, "_run_bench", fake_run_bench)
    monkeypatch.setattr(uk_bench, "_print_report", lambda results, label, **kwargs: None)
    save_calls: list[tuple[list[object], str]] = []
    monkeypatch.setattr(
        uk_bench,
        "_save_results",
        lambda results, label: save_calls.append((list(results), label)),
    )

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=0,
        types=None,
        label="limit-zero",
        min_year=None,
        max_year=None,
        replay=False,
        no_commencement=True,
        parallel=1,
        no_save=True,
    )

    uk_bench.main(args)

    assert seen["corpus"] == []
    assert seen["archive_closed_before_run"] is False
    assert seen["run_kwargs"] == {
        "do_replay": False,
        "repo_root": uk_bench._REPO_ROOT,
        "workers": 1,
        "do_commencement": False,
        "allow_metadata_backfill": True,
        "allow_oracle_alignment": True,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "current_mixed",
        "allow_metadata_only_effects": True,
        "score_text": True,
        "record_replay_subphases": False,
    }
    assert save_calls == []


def test_uk_bench_source_first_candidate_threads_regime(monkeypatch, tmp_path) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    seen: dict[str, object] = {}

    monkeypatch.setattr(uk_bench, "Farchive", DummyArchive)
    monkeypatch.setattr(
        uk_bench,
        "_load_corpus_csv",
        lambda *, types, archive: [
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": 2000,
                "n_effects": 0,
                "n_effect_feed_pages": 0,
                "enacted_url": "enacted",
                "current_url": "current",
            }
        ],
    )

    def fake_run_bench(
        corpus: list[dict[str, object]],
        archive: DummyArchive,
        **kwargs: object,
    ) -> list[object]:
        seen["run_kwargs"] = kwargs
        return []

    monkeypatch.setattr(uk_bench, "_run_bench", fake_run_bench)
    monkeypatch.setattr(uk_bench, "_print_report", lambda results, label, **kwargs: None)
    monkeypatch.setattr(uk_bench, "_save_results", lambda results, label, **kwargs: None)

    args = Namespace(
        history=False,
        show=None,
        compare=None,
        corpus_csv=False,
        db=str(db_path),
        limit=None,
        types=None,
        label="source-first",
        min_year=None,
        max_year=None,
        replay=True,
        no_commencement=True,
        parallel=1,
        uk_allow_metadata_backfill=None,
        uk_allow_oracle_alignment=None,
        uk_respect_feed_applied=None,
        uk_applicability_mode=None,
        uk_source_first_candidate=True,
        uk_authority_mode=None,
        uk_allow_metadata_only_effects=None,
    )

    uk_bench.main(args)

    assert seen["run_kwargs"] == {
        "do_replay": True,
        "repo_root": uk_bench._REPO_ROOT,
        "workers": 1,
        "do_commencement": False,
        "allow_metadata_backfill": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
        "allow_metadata_only_effects": False,
        "score_text": True,
        "record_replay_subphases": False,
    }


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"uk_allow_metadata_backfill": True}, "--metadata-backfill"),
        ({"uk_allow_oracle_alignment": True}, "--oracle-alignment"),
        ({"uk_respect_feed_applied": False}, "--ignore-feed-applied"),
        ({"uk_applicability_mode": "effective_date_only"}, "--applicability-mode"),
        ({"uk_authority_mode": "current_mixed"}, "--authority-mode current_mixed"),
        ({"uk_allow_metadata_only_effects": True}, "--allow-metadata-only-effects"),
    ],
)
def test_uk_bench_source_first_candidate_rejects_conflicting_flags(
    capsys,
    overrides: dict[str, object],
    expected: str,
) -> None:
    args_kwargs: dict[str, object] = {
        "uk_allow_metadata_backfill": None,
        "uk_allow_oracle_alignment": None,
        "uk_respect_feed_applied": None,
        "uk_applicability_mode": None,
        "uk_source_first_candidate": True,
        "uk_authority_mode": None,
        "uk_allow_metadata_only_effects": None,
    }
    args_kwargs.update(overrides)
    args = Namespace(**args_kwargs)

    with pytest.raises(SystemExit) as excinfo:
        uk_bench._normalize_uk_bench_replay_regime(args)

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--source-first-candidate conflicts with" in err
    assert expected in err


def test_fetch_uk_affecting_acts_main_uses_farchive(monkeypatch, tmp_path) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    calls: dict[str, object] = {}

    def fake_farchive(path: Path) -> DummyArchive:
        archive = DummyArchive(path)
        calls["archive_path"] = Path(path)
        calls["archive_obj"] = archive
        return archive

    fake_farchive_module = types.ModuleType("farchive")
    setattr(fake_farchive_module, "Farchive", fake_farchive)

    def fake_fetch_missing(
        sid: str,
        archive: object,
        delay: float = 0.8,
        dry_run: bool = False,
        verbose: bool = False,
        include_enacted: bool = False,
    ) -> tuple[int, int, int]:
        calls["sid"] = sid
        calls["delay"] = delay
        calls["dry_run"] = dry_run
        calls["verbose"] = verbose
        calls["include_enacted"] = include_enacted
        calls["fetch_archive"] = archive
        return (1, 2, 0)

    db = tmp_path / "uk_legislation.farchive"
    db.touch()

    monkeypatch.setitem(sys.modules, "farchive", fake_farchive_module)
    monkeypatch.setattr(uk_prefetch, "fetch_missing_for_statute", fake_fetch_missing)
    monkeypatch.setattr(
        fetch_uk_affecting_acts.sys,
        "argv",
        ["prog", "--statute", "ukpga/2000/10", "--db", str(db), "--verbose"],
    )

    fetch_uk_affecting_acts.main()

    assert "fetch_archive" in calls
    assert calls["sid"] == "ukpga/2000/10"
    assert calls["delay"] == 0.8
    assert calls["dry_run"] is False
    assert calls["verbose"] is True
    assert calls["include_enacted"] is False
    assert calls["archive_path"] == db
    assert calls["archive_obj"] is calls["fetch_archive"]
    assert isinstance(calls["archive_obj"], DummyArchive)
    assert calls["archive_obj"].closed is True


def test_fetch_uk_affecting_acts_writes_acquisition_events_jsonl(monkeypatch, tmp_path, capsys) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def fake_farchive(path: Path) -> DummyArchive:
        return DummyArchive(path)

    fake_farchive_module = types.ModuleType("farchive")
    setattr(fake_farchive_module, "Farchive", fake_farchive)

    event = {
        "rule_id": "uk_prefetch_http_error",
        "phase": "acquisition",
        "family": "source_pathology",
        "statute_id": "ukpga/2000/10",
        "affecting_act_id": "ukpga/1995/13",
        "locator": "leg://missing/uk/ukpga/1995/13/data.xml",
        "url": "https://www.legislation.gov.uk/ukpga/1995/13/data.xml",
        "status": "error",
        "reason": "http_500",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }

    def fake_fetch_missing(
        sid: str,
        archive: object,
        delay: float = 0.8,
        dry_run: bool = False,
        verbose: bool = False,
        include_enacted: bool = False,
    ) -> uk_prefetch.UKPrefetchReport:
        assert sid == "ukpga/2000/10"
        assert archive is not None
        assert delay == 0.8
        assert dry_run is False
        assert verbose is False
        assert include_enacted is False
        return uk_prefetch.UKPrefetchReport(0, 0, 1, (event,))

    db = tmp_path / "uk_legislation.farchive"
    db.touch()
    events_jsonl = tmp_path / "events" / "uk-prefetch.jsonl"

    monkeypatch.setitem(sys.modules, "farchive", fake_farchive_module)
    monkeypatch.setattr(uk_prefetch, "fetch_missing_for_statute", fake_fetch_missing)
    monkeypatch.setattr(
        fetch_uk_affecting_acts.sys,
        "argv",
        [
            "prog",
            "--statute",
            "ukpga/2000/10",
            "--db",
            str(db),
            "--events-jsonl",
            str(events_jsonl),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        fetch_uk_affecting_acts.main()

    out = capsys.readouterr().out
    assert excinfo.value.code == 1
    assert "Acquisition event rules: uk_prefetch_http_error=1" in out
    assert "Blocking event rules:    uk_prefetch_http_error=1" in out
    assert [json.loads(line) for line in events_jsonl.read_text(encoding="utf-8").splitlines()] == [event]


def test_uk_fetch_affecting_text_prints_acquisition_event_rules(monkeypatch, tmp_path, capsys) -> None:
    class DummyArchive:
        def __init__(self, path: Path):
            self.path = Path(path)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def fake_farchive(path: Path) -> DummyArchive:
        return DummyArchive(path)

    fake_farchive_module = types.ModuleType("farchive")
    setattr(fake_farchive_module, "Farchive", fake_farchive)

    event = {
        "rule_id": "uk_prefetch_http_error",
        "phase": "acquisition",
        "blocking": True,
    }

    def fake_fetch_missing(
        sid: str,
        archive: object,
        *,
        dry_run: bool = False,
        verbose: bool = False,
        include_enacted: bool = False,
    ) -> uk_prefetch.UKPrefetchReport:
        assert sid == "ukpga/2000/10"
        assert archive is not None
        assert dry_run is False
        assert verbose is False
        assert include_enacted is False
        return uk_prefetch.UKPrefetchReport(0, 0, 1, (event,))

    db = tmp_path / "uk_legislation.farchive"
    db.touch()

    monkeypatch.setitem(sys.modules, "farchive", fake_farchive_module)
    monkeypatch.setattr(uk_prefetch, "fetch_missing_for_statute", fake_fetch_missing)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["lawvm", "uk-fetch-affecting", "ukpga/2000/10", "--db", str(db)],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    out = capsys.readouterr().out
    assert excinfo.value.code == 1
    assert "fetched=0  already_cached=0  errors=1" in out
    assert "event_rules=uk_prefetch_http_error=1" in out
    assert "blocking_event_rules=uk_prefetch_http_error=1" in out
