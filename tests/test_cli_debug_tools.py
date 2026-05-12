from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
import json
import sys

import pytest

from lawvm.tools import (
    bisect_section,
    classify,
    cli,
    inspect_amendment,
    oracle_context,
    phase_witness,
    replay_plan,
    replay_debug,
    residual_ledger,
    replay_inspect,
    sync_finlex_latest,
    trace_section,
    structural_review,
)
from lawvm.tools.snapshot_debug import build_snapshot_debug_bundle
from lawvm.corpus_store import get_corpus_store
from lawvm.finland.corpus import ConsolidatedOracleInspection
from lawvm.tools.classify_result import ClassifyResult
from tests.corpus_pin_helpers import pinned_replay


def _corpus_available() -> bool:
    try:
        return get_corpus_store().read_source("1987/1250") is not None
    except Exception:
        return False


@pytest.mark.skipif(not _corpus_available(), reason="corpus archive not available")
def test_snapshot_debug_2017_320_2019_371_keeps_restructure_plan_truth() -> None:
    bundle = build_snapshot_debug_bundle(
        statute_id="2017/320",
        source_id="2019/371",
        mode="legal_pit",
        target_path="part:2/chapter:1/section:8",
    )

    assert bundle["matched_lo_ops"] >= 1
    targets = {snap["target"] for snap in bundle["snapshots"]}
    assert "part:2/chapter:1/section:8" in targets


def test_cli_parser_accepts_new_debug_commands() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["classify", "1995/1552", "--json"])
    assert args.command == "classify"
    assert args.statute_id == "1995/1552"
    assert args.json is True

    args = parser.parse_args(["inspect-amendment", "1995/1552", "--source", "2020/162"])
    assert args.command == "inspect-amendment"
    assert args.source == "2020/162"
    assert args.stage == "all"
    assert args.show_source_normalization_facts is False

    args = parser.parse_args(["oracle-context", "1995/1552"])
    assert args.command == "oracle-context"
    assert args.statute_id == "1995/1552"
    assert args.selector_mode == "latest_cached_editorial"

    args = parser.parse_args(["verify", "2006/1299", "--stage", "parse", "--json"])
    assert args.command == "verify"
    assert args.statute_id == "2006/1299"
    assert args.stage == "parse"
    assert args.json is True

    args = parser.parse_args(
        [
            "oracle-context",
            "1995/1552",
            "--selector-mode",
            "date_consolidated_at_or_before",
            "--cutoff",
            "2024-12-19",
        ]
    )
    assert args.command == "oracle-context"
    assert args.selector_mode == "date_consolidated_at_or_before"
    assert args.cutoff == "2024-12-19"

    args = parser.parse_args(
        [
            "inspect-amendment",
            "1995/1552",
            "--source",
            "2020/162",
            "--stage",
            "source",
            "--show-source-normalization-facts",
        ]
    )
    assert args.command == "inspect-amendment"
    assert args.stage == "source"
    assert args.show_source_normalization_facts is True

    args = parser.parse_args(
        [
            "replay-debug",
            "1995/1552",
            "--source",
            "2020/162",
            "--show-clause-text",
            "--show-source-blocks",
            "--show-replay-ops",
            "--show-replay-meta",
            "--show-temporal-events",
            "--show-failed-ops",
            "--show-findings",
            "--contains",
            "14 b",
            "--limit",
            "7",
        ]
    )
    assert args.command == "replay-debug"
    assert args.source == "2020/162"
    assert args.show_clause_text is True
    assert args.show_source_blocks is True
    assert args.show_replay_ops is True
    assert args.show_replay_meta is True
    assert args.show_temporal_events is True
    assert args.show_failed_ops is True
    assert args.show_findings is True
    assert args.contains == "14 b"
    assert args.limit == 7

    args = parser.parse_args(
        [
            "phase-witness",
            "1995/1552",
            "--source",
            "2020/162",
            "--target",
            "section:4",
            "--output",
            ".tmp/demo.json",
            "--json",
        ]
    )
    assert args.command == "phase-witness"
    assert args.source == "2020/162"
    assert args.target == "section:4"
    assert args.output == ".tmp/demo.json"
    assert args.json is True

    args = parser.parse_args(["residual-ledger", "validate"])
    assert args.command == "residual-ledger"
    assert args.residual_ledger_command == "validate"
    assert args.path == "notes/RESIDUAL_BUG_LEDGER_TEMPLATE.csv"

    args = parser.parse_args(["destructive-repair-ledger", "--json"])
    assert args.command == "destructive-repair-ledger"
    assert args.json is True

    args = parser.parse_args(
        [
            "residual-ledger",
            "row",
            "--witness",
            ".tmp/witness.json",
            "--observed-symptom",
            "dropped operative text",
            "--suspected-first-bad-phase",
            "acquire",
            "--confirmed-first-bad-phase",
            "replay_fold",
            "--secondary-phase",
            "materialization",
            "--source-pathology-present",
            "no",
            "--oracle-or-editorial-witness-drift",
            "yes",
            "--fix-owner",
            "finland",
            "--regression-ids",
            "1967/550 §8",
        ]
    )
    assert args.command == "residual-ledger"
    assert args.residual_ledger_command == "row"
    assert args.witness == ".tmp/witness.json"
    assert args.observed_symptom == "dropped operative text"
    assert args.suspected_first_bad_phase == "acquire"
    assert args.confirmed_first_bad_phase == "replay_fold"
    assert args.secondary_phase == "materialization"
    assert args.source_pathology_present == "no"
    assert args.oracle_or_editorial_witness_drift == "yes"
    assert args.fix_owner == "finland"
    assert args.regression_ids == "1967/550 §8"

    args = parser.parse_args(["trace-section", "1995/1552", "--source", "2020/162", "--section", "4 §"])
    assert args.command == "trace-section"
    assert args.section == "4 §"

    args = parser.parse_args(["source-dump", "1995/1552", "--address", "chapter:3/section:12"])
    assert args.command == "source-dump"
    assert args.statute_id == "1995/1552"
    assert args.address == "chapter:3/section:12"

    args = parser.parse_args(["bisect-section", "1995/1552", "--section", "4 §", "--verbose"])
    assert args.command == "bisect-section"
    assert args.verbose is True

    args = parser.parse_args(
        ["replay-inspect", "1995/1552", "--section", "4 §", "--chapter", "1", "--part", "I"]
    )
    assert args.command == "replay-inspect"
    assert args.section == "4 §"
    assert args.chapter == "1"
    assert args.part == "I"

    args = parser.parse_args(
        [
            "explain",
            "1995/1552",
            "--oracle-selector-mode",
            "bench_comparable",
            "--oracle-version-amendment-id",
            "2019/112",
        ]
    )
    assert args.command == "explain"
    assert args.oracle_selector_mode == "bench_comparable"
    assert args.oracle_version_amendment_id == "2019/112"

    args = parser.parse_args(["explain", "1995/1552", "--threshold", "0.95"])
    assert args.command == "explain"
    assert args.oracle_selector_mode == "latest_cached_editorial"
    assert args.oracle_version_amendment_id is None

    args = parser.parse_args(
        [
            "replay-plan",
            "1995/1552",
            "--selector-mode",
            "bench_comparable",
            "--mode",
            "legal_pit",
        ]
    )
    assert args.command == "replay-plan"
    assert args.statute_id == "1995/1552"
    assert args.selector_mode == "bench_comparable"
    assert args.mode == "legal_pit"

    args = parser.parse_args(["sync-finlex-latest"])
    assert args.command == "sync-finlex-latest"
    assert args.delay == 1.0
    assert args.corpus is None
    assert args.diagnostics_jsonl is None

    args = parser.parse_args(["bench", "--oracle-aware-headline"])
    assert args.command == "bench"
    assert args.oracle_aware_headline is True

    args = parser.parse_args(["structural-review", "1995/1552", "--dump"])
    assert args.command == "structural-review"
    assert args.oracle_selector_mode == "bench_comparable"

    args = parser.parse_args(
        ["structural-review", "1995/1552", "--dump", "--oracle-selector-mode", "latest_cached_editorial"]
    )
    assert args.command == "structural-review"
    assert args.oracle_selector_mode == "latest_cached_editorial"

    args = parser.parse_args(["import-zip", "--dry-run"])
    assert args.command == "import-zip"
    assert args.dry_run is True

    eu_replay_args = parser.parse_args(["eu-replay", "32016R0679", "--pit-date", "2026-01-01"])
    assert eu_replay_args.command == "eu-replay"
    assert eu_replay_args.celex == "32016R0679"
    assert eu_replay_args.pit_date == "2026-01-01"

    eu_reul_args = parser.parse_args(
        ["eu-reul", "resolve", "retained-law://celex/32016R0679/article/1", "sample.xml"]
    )
    assert eu_reul_args.command == "eu-reul"
    assert eu_reul_args.eu_reul_command == "resolve"


def test_phase_witness_main_writes_json_artifact(monkeypatch, tmp_path, capsys) -> None:
    output_path = tmp_path / "witness.json"

    monkeypatch.setattr(
        phase_witness,
        "build_phase_witness_bundle",
        lambda statute_id, source_id, mode, target_path="": {
            "schema": "lawvm.phase_witness.v1",
            "statute_id": statute_id,
            "source_id": source_id,
            "mode": mode,
            "target_path": target_path,
        },
    )

    phase_witness.main(
        Namespace(
            statute_id="1995/1552",
            source="2020/162",
            mode="legal_pit",
            target="section:4",
            output=str(output_path),
            json=False,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["statute_id"] == "1995/1552"
    assert payload["source_id"] == "2020/162"
    assert payload["target_path"] == "section:4"
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema"] == "lawvm.phase_witness.v1"


def test_residual_ledger_main_validate_and_row(monkeypatch, tmp_path, capsys) -> None:
    ledger_path = tmp_path / "ledger.csv"
    ledger_path.write_text(
        ",".join(residual_ledger.RESIDUAL_LEDGER_COLUMNS) + "\n",
        encoding="utf-8",
    )

    residual_ledger.main(
        Namespace(
            residual_ledger_command="validate",
            path=str(ledger_path),
            json=True,
        )
    )
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["ok"] is True

    witness_path = tmp_path / "witness.json"
    witness_path.write_text(
        json.dumps(
            {
                "statute_id": "1962/184",
                "source_id": "1967/551",
                "target_path": "section:17",
                "acquisition": {"source_lane_used": "preamble"},
            }
        ),
        encoding="utf-8",
    )

    residual_ledger.main(
        Namespace(
            residual_ledger_command="row",
            witness=str(witness_path),
            observed_symptom="operative repeal text dropped",
            path="",
            interaction_family="body_prose_only_repeal",
            suspected_first_bad_phase="acquire",
            status="open",
            notes="",
            json=True,
        )
    )
    row_payload = json.loads(capsys.readouterr().out)
    assert row_payload["statute_id"] == "1962/184"
    assert row_payload["path"] == "section:17"
    assert row_payload["source_lane_used"] == "preamble"
    assert row_payload["suspected_first_bad_phase"] == "acquire"


def test_structural_review_dump_section_filter_is_forwarded(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_dump_statute(
        statute_id,
        *,
        corpus=None,
        mode="finlex_oracle",
        oracle_selector_mode="bench_comparable",
        compact=False,
        section_filter=None,
    ):
        captured["statute_id"] = statute_id
        captured["oracle_selector_mode"] = oracle_selector_mode
        captured["compact"] = compact
        captured["section_filter"] = section_filter
        return "=== dump ===\n"

    monkeypatch.setattr(structural_review, "dump_statute", fake_dump_statute)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lawvm", "structural-review", "1995/1552", "--dump", "--section", "4 §"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert out == "=== dump ===\n"
    assert captured["statute_id"] == "1995/1552"
    assert captured["oracle_selector_mode"] == "bench_comparable"
    assert captured["compact"] is False
    assert captured["section_filter"] == "4 §"


def test_structural_review_dump_statute_filters_to_one_section(monkeypatch) -> None:
    monkeypatch.setattr(structural_review, "_get_statute_title", lambda statute_id, corpus: "Test title")
    monkeypatch.setattr(
        structural_review,
        "compute_statute_section_diffs",
        lambda statute_id, *, corpus=None, mode="finlex_oracle", oracle_selector_mode="bench_comparable": (
            {
                "section:4": {
                    "semantic_diff": {"kind": "text", "structural": 0, "label": 0, "text": 1, "events": [{"kind": "text_change"}]},
                    "replay": {"kind": "section", "label": "4", "text": "LawVM 4"},
                    "oracle": {"kind": "section", "label": "4", "text": "Finlex 4"},
                },
                "section:5": {
                    "semantic_diff": {"kind": "text", "structural": 0, "label": 0, "text": 1, "events": [{"kind": "text_change"}]},
                    "replay": {"kind": "section", "label": "5", "text": "LawVM 5"},
                    "oracle": {"kind": "section", "label": "5", "text": "Finlex 5"},
                },
            },
            False,
        ),
    )

    out = structural_review.dump_statute("1995/1552", section_filter="4 §")

    assert "--- section:4 [text] ---" in out
    assert "4 §" in out
    assert "section:5" not in out


def test_structural_review_dump_statute_forwards_selector_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(structural_review, "_get_statute_title", lambda statute_id, corpus: "Test title")

    def fake_compute(statute_id, *, corpus=None, mode="finlex_oracle", oracle_selector_mode="bench_comparable"):
        captured["oracle_selector_mode"] = oracle_selector_mode
        return ({}, False)

    monkeypatch.setattr(structural_review, "compute_statute_section_diffs", fake_compute)

    out = structural_review.dump_statute(
        "1995/1552",
        oracle_selector_mode="latest_cached_editorial",
    )

    assert out == ""
    assert captured["oracle_selector_mode"] == "latest_cached_editorial"


def test_cli_parser_accepts_norway_debug_and_coverage_commands() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        ["no-source-excerpt", "no/lov/2022-03-11-9", "needle", "--mode", "current", "--max-hits", "2"]
    )
    assert args.command == "no-source-excerpt"
    assert args.source_id == "no/lov/2022-03-11-9"
    assert args.needles == ["needle"]
    assert args.mode == "current"
    assert args.max_hits == 2

    args = parser.parse_args(
        ["no-op-trace", "no/lov/2022-03-11-9", "--path", "section:6-3", "--limit", "4"]
    )
    assert args.command == "no-op-trace"
    assert args.base_id == "no/lov/2022-03-11-9"
    assert args.path == ["section:6-3"]
    assert args.limit == 4

    args = parser.parse_args(["no-divergence", "no/lov/2022-03-11-9", "--max-divergences", "3"])
    assert args.command == "no-divergence"
    assert args.base_id == "no/lov/2022-03-11-9"
    assert args.max_divergences == 3

    args = parser.parse_args(["no-coverage", "no/lov/2022-03-11-9", "--limit", "7"])
    assert args.command == "no-coverage"
    assert args.base_id == "no/lov/2022-03-11-9"
    assert args.limit == 7

    args = parser.parse_args(["no-debug", "no/lov/2022-03-11-9", "--path", "section:6-3"])
    assert args.command == "no-debug"
    assert args.base_id == "no/lov/2022-03-11-9"
    assert args.path == ["section:6-3"]


def test_cli_parser_rejects_eu_reul_without_subcommand() -> None:
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["eu-reul"])


def test_sync_finlex_latest_main_uses_archive_ids(monkeypatch, capsys) -> None:
    calls: dict[str, tuple[object, list[str], float, bool]] = {}

    def fake_sync_latest_pits(archive, sids, delay=1.0, verbose=False, diagnostics_out=None):
        calls["payload"] = (archive, list(sids), delay, verbose)
        return {"statutes": len(sids), "fetched": 1, "cached": 1, "skipped": 0, "errors": 0}

    class DummyArchive:
        def __init__(self, path) -> None:
            self.path = path
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def locators(self, pattern: str = "%") -> list[str]:
            assert pattern == "finlex://sd/%/fin/main.xml"
            return [
                "finlex://sd/1987/990/fin/main.xml",
                "finlex://sd/2016/1227/fin/main.xml",
            ]

    monkeypatch.setattr(sync_finlex_latest, "Farchive", lambda path: DummyArchive(path))
    monkeypatch.setattr(sync_finlex_latest, "sync_latest_pits", fake_sync_latest_pits)

    sync_finlex_latest.main(Namespace(db="data/finlex.farchive", corpus=None, delay=0.75, verbose=True))

    out = capsys.readouterr().out
    assert "Syncing latest Finnish PIT XMLs" in out
    archive, sids, delay, verbose = calls["payload"]
    assert sids == ["1987/990", "2016/1227"]
    assert delay == 0.75
    assert verbose is True


def test_sync_finlex_latest_main_uses_explicit_sids(monkeypatch, capsys) -> None:
    calls: dict[str, tuple[object, list[str], float, bool]] = {}

    def fake_sync_latest_pits(archive, sids, delay=1.0, verbose=False, diagnostics_out=None):
        calls["payload"] = (archive, list(sids), delay, verbose)
        return {"statutes": len(sids), "fetched": 2, "cached": 0, "skipped": 0, "errors": 0}

    class DummyArchive:
        def __init__(self, path) -> None:
            self.path = path
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def locators(self, pattern: str = "%") -> list[str]:
            raise AssertionError("explicit SIDs should bypass archive discovery")

    monkeypatch.setattr(sync_finlex_latest, "Farchive", lambda path: DummyArchive(path))
    monkeypatch.setattr(sync_finlex_latest, "sync_latest_pits", fake_sync_latest_pits)

    sync_finlex_latest.main(
        Namespace(
            db="data/finlex.farchive",
            corpus=None,
            sid=["2010/182", "2016/1227"],
            delay=0.5,
            verbose=False,
        )
    )

    out = capsys.readouterr().out
    assert "explicit --sid arguments" in out
    archive, sids, delay, verbose = calls["payload"]
    assert sids == ["2010/182", "2016/1227"]
    assert delay == 0.5
    assert verbose is False


def test_sync_finlex_latest_main_writes_diagnostics_jsonl(monkeypatch, tmp_path, capsys) -> None:
    diagnostic = {
        "rule_id": "fi_sync_latest_pit_discovery_failed",
        "phase": "acquisition",
        "family": "source_pathology",
        "statute_id": "2010/182",
        "pit_version": "",
        "locator": "",
        "reason": "RuntimeError",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }

    def fake_sync_latest_pits(archive, sids, delay=1.0, verbose=False, diagnostics_out=None):
        assert archive is not None
        assert sids == ["2010/182"]
        assert delay == 1.0
        assert verbose is False
        assert diagnostics_out is not None
        diagnostics_out.append(diagnostic)
        return {"statutes": 1, "fetched": 0, "cached": 0, "skipped": 0, "errors": 1}

    class DummyArchive:
        def __init__(self, path) -> None:
            self.path = path
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def locators(self, pattern: str = "%") -> list[str]:
            raise AssertionError("explicit SIDs should bypass archive discovery")

    diagnostics_jsonl = tmp_path / "finlex-sync-diagnostics.jsonl"
    monkeypatch.setattr(sync_finlex_latest, "Farchive", lambda path: DummyArchive(path))
    monkeypatch.setattr(sync_finlex_latest, "sync_latest_pits", fake_sync_latest_pits)

    with pytest.raises(SystemExit) as excinfo:
        sync_finlex_latest.main(
            Namespace(
                db="data/finlex.farchive",
                corpus=None,
                sid=["2010/182"],
                delay=1.0,
                verbose=False,
                diagnostics_jsonl=str(diagnostics_jsonl),
            )
        )

    assert excinfo.value.code == 1
    assert [json.loads(line) for line in diagnostics_jsonl.read_text(encoding="utf-8").splitlines()] == [diagnostic]
    assert "diagnostics=1" in capsys.readouterr().out


def test_oracle_context_main_renders_selected_context(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_get_consolidated_oracle_inspection(statute_id, corpus=None, selector=None):
        captured["statute_id"] = statute_id
        captured["selector_mode"] = selector.mode.value if selector is not None else ""
        return ConsolidatedOracleInspection(
            locator="finlex://sd-cons/2014/1429/fin@20190112/main.xml",
            cutoff_date=None,
            oracle_version_amendment_id="2019/112",
            selector_mode="latest_cached_editorial",
        )

    monkeypatch.setattr(oracle_context, "get_consolidated_oracle_inspection", fake_get_consolidated_oracle_inspection)

    oracle_context.main(Namespace(statute_id="2014/1429", selector_mode="latest_cached_editorial", json=False))

    out = capsys.readouterr().out
    assert captured["statute_id"] == "2014/1429"
    assert captured["selector_mode"] == "latest_cached_editorial"
    assert "Selected oracle locator : finlex://sd-cons/2014/1429/fin@20190112/main.xml" in out
    assert "Oracle version amendment: 2019/112" in out
    assert "Selector mode           : latest_cached_editorial" in out


def test_replay_plan_main_renders_lineage_and_context(monkeypatch, capsys) -> None:
    def fake_build_replay_plan_inspection(args):
        assert args.statute_id == "1995/1552"
        return {
            "statute_id": "1995/1552",
            "mode": "finlex_oracle",
            "selector_mode": "bench_comparable",
            "oracle_context": {
                "locator": "finlex://sd-cons/1995/1552/fin@20240621/main.xml",
                "cutoff_date": "2024-06-21",
                "oracle_version_amendment_id": "2024/621",
            },
            "amendment_chain": ["1996/10", "1997/20"],
            "amendment_records": [
                {
                    "statute_id": "1996/10",
                    "included": True,
                    "issue_date": "1996-01-01",
                    "effective_date": "1996-03-01",
                    "title": "First amendment",
                },
                {
                    "statute_id": "1997/20",
                    "included": False,
                    "issue_date": "1997-01-01",
                    "effective_date": "",
                    "title": "Second amendment",
                },
            ],
            "cutoff_date": "2024-06-21",
            "oracle_version_amendment_id": "2024/621",
            "oracle_suspect": "none",
        }

    monkeypatch.setattr(replay_plan, "build_replay_plan_inspection", fake_build_replay_plan_inspection)

    replay_plan.main(Namespace(statute_id="1995/1552", mode="finlex_oracle", json=False))
    out = capsys.readouterr().out
    assert "Selector mode  : bench_comparable" in out
    assert "Oracle locator : finlex://sd-cons/1995/1552/fin@20240621/main.xml" in out
    assert "Chain length   : 2" in out
    assert "1996/10" in out
    assert "1997/20" in out


def test_classify_main_prints_counts(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        classify,
        "_classify_statute",
        lambda sid, mode: ClassifyResult(
            sid=sid,
            mode=mode,
            overall_score=0.93,
            section_score=0.95,
            section_results=[
                {"section": "section:4", "diagnosis": "REPLAY_EXTRA", "blame_source": "2020/162"},
                {"section": "section:5", "diagnosis": "ORACLE_STALE", "blame_source": "2020/162"},
            ],
            source_pathologies=[{"code": "DESTRUCTIVE_SHAPE_LOSS_RISK"}],
            contingent_effective_sources=[],
        ),
    )

    classify.main(Namespace(statute_id="1995/1552", mode="legal_pit", json=False))

    out = capsys.readouterr().out
    assert "Statute      : 1995/1552" in out
    assert "Pathologies  : DESTRUCTIVE_SHAPE_LOSS_RISK" in out
    assert "REPLAY_EXTRA=1" in out
    assert "section:4: REPLAY_EXTRA" in out


def test_inspect_amendment_main_prints_group_details(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        inspect_amendment,
        "build_amendment_bundle",
        lambda statute_id, source_id, mode: {
            "statute_id": statute_id,
            "source_id": source_id,
            "mode": mode,
            "source_title": "Test title",
            "route": {"should_apply": True, "reason": "ok"},
            "used_sec1_fallback": False,
            "johtolause": "Muutetaan 4 §",
            "source_payload": {
                "raw_ir": {"kind": "section", "label": "4", "children": 1, "text": "raw source"},
                "normalized_ir": {"kind": "section", "label": "4", "children": 1, "text": "normalized source"},
                "source_normalization_facts": [
                    {
                        "kind": "NUMBERING_REPAIR",
                        "basis": "MONOTONIC_LOCAL_REPAIR",
                        "before": "before text",
                        "after": "after text",
                        "explanation": "why text",
                        "path": ["body:?", "section:4"],
                        "confidence": 1.0,
                    }
                ],
            },
            "compile_projection_rows": [{"kind": "x", "message": "note"}],
            "compiled_ops": ["REPLACE 4 § 4 mom"],
            "groups": [
                {
                    "target_unit_kind": "section",
                    "target_norm": "4",
                    "target_chapter": "",
                    "raw_payload": {"kind": "section", "label": "4", "children": 1, "text": "raw"},
                    "prepared_payload": {"kind": "section", "label": "4", "children": 1, "text": "prepared"},
                    "normalized_payload": {"kind": "section", "label": "4", "children": 1, "text": "normalized"},
                    "ops_final": ["REPLACE 4 § 4 mom"],
                    "subsection_map": [
                        {
                            "op": "REPLACE 4 § 4 mom",
                            "mapped_payload": {"kind": "subsection", "label": "4", "children": 1, "text": "payload"},
                        }
                    ],
                    "sparse_slot_bindings": [
                        {
                            "op": "REPLACE 4 § 4 mom",
                            "slot_index": 1,
                            "slot_label": "4",
                            "target_paragraph": 4,
                            "target_item": "",
                            "target_special": "",
                        }
                    ],
                    "source_pathologies": [{"code": "DESTRUCTIVE_SHAPE_LOSS_RISK", "source_statute": "2020/162", "target_label": "4 §"}],
                    "elaboration_observations": [
                        {
                            "stage": "group_payload_normalization",
                            "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                            "detail": {"target_norm": "4"},
                        },
                        {
                            "stage": "group_payload_normalization",
                            "kind": "ELAB.UNASSIGNED_SPARSE_SLOTS",
                            "detail": {
                                "unassigned_slots": ["2:5", "3:(unlabeled)"],
                                "unassigned_count": 2,
                            },
                        },
                        {
                            "stage": "group_payload_normalization",
                            "kind": "ELAB.PAYLOAD_COMPLETENESS",
                            "detail": {
                                "payload_completeness_kind": "fragmentary",
                                "reasons": ["unassigned_sparse_payload_slots"],
                                "tail_policy": "preserve_unstated_tail",
                                "unassigned_payload_slots": ["2:5", "3:(unlabeled)"],
                            },
                        },
                    ],
                }
            ],
        },
    )

    inspect_amendment.main(
        Namespace(
            statute_id="1995/1552",
            source="2020/162",
            mode="legal_pit",
            json=False,
            stage="all",
            show_source_normalization_facts=True,
        )
    )

    out = capsys.readouterr().out
    assert "Amendment    : 2020/162" in out
    assert "Source payload:" in out
    assert "Raw IR        :" in out
    assert "Normalized IR :" in out
    assert "Source normalization facts:" in out
    assert "NUMBERING_REPAIR MONOTONIC_LOCAL_REPAIR path=body:?/section:4 confidence=1.00" in out
    assert "before: before text" in out
    assert "after : after text" in out
    assert "why   : why text" in out
    assert "Compiled ops (1):" in out
    assert "Group 1: P 4" in out
    assert "Subsection map:" in out
    assert "Sparse slot bindings:" in out
    assert "REPLACE 4 § 4 mom -> slot 1:4" in out
    assert "Slot assignment summary: bindings=1 leftovers=2" in out
    assert "Unassigned sparse payload slots: 2:5, 3:(unlabeled)" in out
    assert "Payload completeness: fragmentary tail_policy=preserve_unstated_tail" in out
    assert "Reasons: unassigned_sparse_payload_slots" in out
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK 2020/162 4 §" in out
    assert "Elaboration observations:" in out
    assert 'group_payload_normalization:ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE {"target_norm": "4"}' in out


def test_inspect_amendment_main_stage_source_omits_group_details(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        inspect_amendment,
        "build_amendment_bundle",
        lambda statute_id, source_id, mode: {
            "statute_id": statute_id,
            "source_id": source_id,
            "mode": mode,
            "source_title": "Test title",
            "route": {"should_apply": True, "reason": "ok"},
            "used_sec1_fallback": False,
            "johtolause": "Muutetaan 4 §",
            "source_payload": {
                "raw_ir": {"kind": "section", "label": "4", "children": 1, "text": "raw source"},
                "normalized_ir": {"kind": "section", "label": "4", "children": 1, "text": "normalized source"},
                "source_normalization_facts": [],
            },
            "compile_projection_rows": [{"kind": "x", "message": "note"}],
            "compiled_ops": ["REPLACE 4 § 4 mom"],
            "groups": [{"target_unit_kind": "section", "target_norm": "4", "target_chapter": "", "ops_final": ["REPLACE 4 § 4 mom"]}],
        },
    )

    inspect_amendment.main(
        Namespace(
            statute_id="1995/1552",
            source="2020/162",
            mode="legal_pit",
            json=False,
            stage="source",
            show_source_normalization_facts=False,
        )
    )

    out = capsys.readouterr().out
    assert "Source payload:" in out
    assert "Compiled ops (1):" not in out
    assert "Group 1: P 4" not in out


def test_trace_section_build_trace_bundle_exposes_paths_and_oracle_context(monkeypatch) -> None:
    class _Master:
        def __init__(self, ir: str) -> None:
            self.materialized_state = type("State", (), {"ir": ir})()

    def fake_resolve_applicable_amendment_records(_statute_id, _mode):
        return ([{"statute_id": "2020/162"}, {"statute_id": "2020/999"}], None, None)

    def fake_replay_xml(_statute_id, mode, stop_before, quiet):
        if stop_before == "2020/162":
            return _Master("before")
        return _Master("after")

    def fake_extract_ir_sections(ir):
        if ir == "before":
            return {"chapter:5/section:63": "before node"}
        return {"chapter:5/section:63": "after node"}

    def fake_extract_oracle_sections(_root):
        return {"part:i/chapter:5/section:63": "oracle node"}

    def fake_get_ground_truth_tree(_statute_id):
        return object()

    class _OracleCtx:
        locator = "finlex://sd-cons/2014/1429/fin@20190112/main.xml"
        cutoff_date = type("D", (), {"isoformat": lambda self: "2024-12-19"})()
        oracle_version_amendment_id = "2019/112"

    monkeypatch.setattr(trace_section, "replay_xml", fake_replay_xml)
    monkeypatch.setattr(trace_section, "_resolve_applicable_amendment_records", fake_resolve_applicable_amendment_records)
    monkeypatch.setattr(trace_section, "extract_ir_sections", fake_extract_ir_sections)
    monkeypatch.setattr(trace_section, "extract_oracle_sections", fake_extract_oracle_sections)
    monkeypatch.setattr(trace_section, "get_ground_truth_tree", fake_get_ground_truth_tree)
    monkeypatch.setattr(trace_section, "get_consolidated_oracle_context", lambda *_args, **_kwargs: _OracleCtx())
    monkeypatch.setattr(
        trace_section,
        "_resolve_applicable_amendment_records",
        lambda *_args, **_kwargs: ([{"statute_id": "2020/162"}, {"statute_id": "2020/999"}], None, None),
    )

    bundle = trace_section.build_trace_bundle("2014/1429", "2020/162", "chapter:5/section:63", "legal_pit")

    assert bundle["requested_section"] == "chapter:5/section:63"
    assert bundle["replay_path"] == "chapter:5/section:63"
    assert bundle["oracle_path"] == "part:i/chapter:5/section:63"
    assert bundle["oracle_context"] == {
        "locator": "finlex://sd-cons/2014/1429/fin@20190112/main.xml",
        "cutoff_date": "2024-12-19",
        "oracle_version_amendment_id": "2019/112",
    }
    assert bundle["before_text"] == "before node"
    assert bundle["after_text"] == "after node"
    assert bundle["oracle_text"] == "oracle node"


def test_trace_section_main_prints_paths_and_context(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        trace_section,
        "build_trace_bundle",
        lambda statute_id, source_id, section, mode: {
            "statute_id": statute_id,
            "source_id": source_id,
            "next_source_id": "2020/999",
            "mode": mode,
            "requested_section": "4 §",
            "replay_path": "chapter:1/section:4",
            "oracle_path": "part:i/chapter:1/section:4",
            "oracle_context": {
                "locator": "finlex://sd-cons/2014/1429/fin@20190112/main.xml",
                "cutoff_date": "2024-12-19",
                "oracle_version_amendment_id": "2019/112",
            },
            "before_text": "before",
            "after_text": "after",
            "oracle_text": "oracle",
            "before_vs_oracle": 0.8,
            "after_vs_oracle": 1.0,
            "changed": True,
        },
    )

    trace_section.main(
        Namespace(statute_id="1995/1552", source="2020/162", section="4 §", mode="legal_pit", json=False)
    )

    out = capsys.readouterr().out
    assert "Requested      : 4 §" in out
    assert "Replay path    : chapter:1/section:4" in out
    assert "Oracle path    : part:i/chapter:1/section:4" in out
    assert "Oracle locator : finlex://sd-cons/2014/1429/fin@20190112/main.xml" in out
    assert "Oracle cutoff  : 2024-12-19" in out
    assert "Oracle version : 2019/112" in out
    assert "Changed        : yes" in out
    assert "Before:" in out
    assert "After:" in out
    assert "Oracle:" in out


def test_replay_debug_main_prints_clause_text_and_filtered_ops(capsys, monkeypatch) -> None:
    def fake_replay_xml(*args, **kwargs):
        compiled_ops_out = kwargs.get("compiled_ops_out")
        replay_meta_out = kwargs.get("replay_meta_out")
        lo_ops_out = kwargs.get("lo_ops_out")
        failed_ops_out = kwargs.get("failed_ops_out")
        temporal_events_out = kwargs.get("temporal_events_out")
        assert compiled_ops_out is not None
        compiled_ops_out.extend(
            [
                {
                    "source_statute": "2020/162",
                    "source_title": "Source title",
                    "sequence": 1,
                    "action": "replace",
                    "target": {"container": "section", "section": "4"},
                },
                {
                    "source_statute": "2021/999",
                    "source_title": "Other title",
                    "sequence": 2,
                    "action": "repeal",
                    "target": {"container": "section", "section": "5"},
                },
            ]
        )
        if lo_ops_out is not None:
            lo_ops_out.extend(
                [
                    type(
                        "ReplayOp",
                        (),
                        {
                            "op_id": "x",
                            "sequence": 3,
                            "action": "replace",
                            "target": type(
                                "Target", (), {"path": (("chapter", "7"), ("section", "14b")), "special": None}
                            )(),
                            "payload": type(
                                "Payload",
                                (),
                                {
                                    "kind": "section",
                                    "label": "14b",
                                    "attrs": {"lawvm_repeal_placeholder": "1"},
                                    "children": [],
                                    "text": "14 b § on kumottu L:lla 30.12.2024/1116.",
                                },
                            )(),
                            "source": type("Source", (), {"statute_id": "2020/162"})(),
                        },
                    )(),
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "cutoff_date": "2024-12-19",
                    "oracle_version_amendment_id": "2019/112",
                    "oracle_suspect": "",
                    "lineage": [{"statute_id": "2020/162", "effective_date": "2020-01-01"}],
                    "apply_mutation_events": [
                        {"op_id": "peg_0", "source_statute": "2020/162", "helper": "helper", "outcome": "applied"}
                    ],
                }
            )
        if temporal_events_out is not None:
            temporal_events_out.extend(
                [
                    SimpleNamespace(
                        event_id="t1",
                        kind="commence",
                        scope=SimpleNamespace(target_statute="1995/1552"),
                        effective="2020-01-01",
                        expires="",
                        source=SimpleNamespace(statute_id="2020/162", title="14 b temporal", enacted=""),
                        activation_rule=SimpleNamespace(kind="fixed_date"),
                        group_id="g1",
                        derived_from_effect_intent="14 b commencement",
                    ),
                ]
            )
        if failed_ops_out is not None:
            failed_ops_out.extend(
                [
                    SimpleNamespace(
                        amendment_id="2020/162",
                        description="failed op 14 b",
                        reason="missing target 14 b",
                        target_section="4",
                        target_unit_kind="section",
                        target_chapter=None,
                    )
                ]
            )
        return type("Master", (), {"title": "Replay title"})()

    monkeypatch.setattr(replay_debug, "replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        replay_debug,
        "build_amendment_bundle",
        lambda statute_id, source_id, mode: {
            "source_title": "Source title",
            "johtolause": "Muutetaan 4 §",
            "route": {"should_apply": True, "reason": "ok"},
            "used_sec1_fallback": False,
        },
    )
    monkeypatch.setattr(
        replay_debug,
        "_load_source_blocks",
        lambda source_id: [{"name": "repeals", "text": "kumotaan 7 luvun 14 b §"}],
    )

    replay_debug.main(
        Namespace(
            statute_id="1995/1552",
            source="2020/162",
            target=None,
            mode="legal_pit",
            show_clause_text=True,
            show_source_blocks=True,
            show_replay_ops=True,
            show_replay_meta=True,
            show_temporal_events=True,
            show_failed_ops=True,
            contains="14 b",
            limit=5,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert "Source clause:" in out
    assert "Source blocks:" in out
    assert "[repeals] kumotaan 7 luvun 14 b §" in out
    assert "Muutetaan 4 §" in out
    assert "Compiled ops:" in out
    assert "(no operations match filters)" not in out
    assert "Replay ops:" in out
    assert "--- 2020/162" in out
    assert "chapter:7 / section:14b [placeholder]" in out
    assert "Replay meta:" in out
    assert "cutoff_date" in out


def test_replay_debug_main_prints_filtered_replay_meta_and_temporal_events(capsys, monkeypatch) -> None:
    def fake_replay_xml(*args, **kwargs):
        compiled_ops_out = kwargs.get("compiled_ops_out")
        replay_meta_out = kwargs.get("replay_meta_out")
        lo_ops_out = kwargs.get("lo_ops_out")
        failed_ops_out = kwargs.get("failed_ops_out")
        temporal_events_out = kwargs.get("temporal_events_out")
        assert compiled_ops_out is not None
        compiled_ops_out.extend(
            [
                {
                    "source_statute": "2020/162",
                    "source_title": "Source title",
                    "sequence": 1,
                    "action": "replace",
                    "target": {"container": "section", "section": "4"},
                }
            ]
        )
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "cutoff_date": "2024-12-19",
                    "oracle_version_amendment_id": "2019/112",
                    "oracle_suspect": "",
                    "lineage": [{"statute_id": "2020/162"}],
                    "source_pathologies": [{"source_statute": "2020/162", "code": "X"}],
                }
            )
        if temporal_events_out is not None:
            temporal_events_out.extend(
                [
                    SimpleNamespace(
                        event_id="t1",
                        kind="expire",
                        scope=SimpleNamespace(target_statute="1995/1552"),
                        effective="",
                        expires="2024-01-01",
                        source=SimpleNamespace(statute_id="2020/162", title="", enacted=""),
                        activation_rule=None,
                        group_id="g1",
                        derived_from_effect_intent="expiry",
                    ),
                    SimpleNamespace(
                        event_id="t2",
                        kind="commence",
                        scope=SimpleNamespace(target_statute="1995/1552"),
                        effective="2025-01-01",
                        expires="",
                        source=SimpleNamespace(statute_id="2021/999", title="", enacted=""),
                        activation_rule=None,
                        group_id="g2",
                        derived_from_effect_intent="commencement",
                    ),
                ]
            )
        return type("Master", (), {"title": "Replay title"})()

    monkeypatch.setattr(replay_debug, "replay_xml", fake_replay_xml)

    replay_debug.main(
        Namespace(
            statute_id="1995/1552",
            source="2020/162",
            target=None,
            mode="legal_pit",
            show_clause_text=False,
            show_source_blocks=False,
            show_replay_ops=False,
            show_replay_meta=True,
            show_temporal_events=True,
            show_failed_ops=False,
            contains=None,
            limit=10,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert "Replay meta:" in out
    assert "cutoff_date: 2024-12-19" in out
    assert "source_pathologies [1]:" in out


def test_replay_debug_bundle_can_include_filtered_findings(monkeypatch) -> None:
    def fake_replay_xml(*args, **kwargs):
        compiled_ops_out = kwargs.get("compiled_ops_out")
        assert compiled_ops_out is not None
        compiled_ops_out.append(
            {
                "source_statute": "2020/162",
                "source_title": "Source title",
                "sequence": 1,
                "action": "replace",
                "target": {"container": "section", "section": "4"},
            }
        )
        return SimpleNamespace(
            title="Replay title",
            findings=(
                SimpleNamespace(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="elaboration",
                    source_statute="2020/162",
                    detail={"target": "14 b", "rule_id": "fi.source_pathology"},
                    blocking=False,
                ),
                SimpleNamespace(
                    kind="ELAB.OTHER",
                    role="observation",
                    stage="elaboration",
                    source_statute="2021/999",
                    detail={"target": "99"},
                    blocking=False,
                ),
            ),
        )

    monkeypatch.setattr(replay_debug, "replay_xml", fake_replay_xml)

    bundle = replay_debug.build_replay_debug_bundle(
        statute_id="1995/1552",
        mode="legal_pit",
        source="2020/162",
        contains="14 b",
        show_findings=True,
    )

    assert len(bundle["findings"]) == 1
    assert bundle["findings"][0]["kind"] == "ELAB.SOURCE_PATHOLOGY"
    assert bundle["findings"][0]["detail"]["rule_id"] == "fi.source_pathology"
    rendered = replay_debug._format_text(bundle)
    assert "Findings:" in rendered
    assert "fi.source_pathology" in rendered


def test_replay_inspect_main_prints_section_tree_and_metadata(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        replay_inspect,
        "build_replay_inspect_bundle",
        lambda statute_id, section, mode, chapter=None, part=None: {
            "statute_id": statute_id,
            "title": "Replay title",
            "mode": mode,
            "section": section,
            "chapter": chapter or "",
            "part": part or "",
            "section_path": "body / chapter:1 / section:4",
            "section_path_steps": [
                {"kind": "chapter", "label": "1"},
                {"kind": "section", "label": "4"},
            ],
            "section_kind": "section",
            "section_label": "4",
            "section_metadata": {
                "child_count": 2,
                "text_length": 18,
                "own_text_length": 6,
                "attrs": {"eId": "s4"},
            },
            "section_text": "alpha beta",
            "section_tree": [
                "section:4 :: alpha",
                "  subsection:1 :: beta",
            ],
        },
    )

    replay_inspect.main(
        Namespace(
            statute_id="1995/1552",
            section="4 §",
            chapter="1",
            part="I",
            mode="legal_pit",
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert "Statute : 1995/1552" in out
    assert "Section : 4 §" in out
    assert "Scope   : part=I chapter=1" in out
    assert "Path    : body / chapter:1 / section:4" in out
    assert "Section metadata:" in out
    assert "attrs         : eId=s4" in out
    assert "Replay subtree:" in out
    assert "section:4 :: alpha" in out
    assert "Section text:" in out
    assert "alpha beta" in out


def test_build_replay_inspect_bundle_prefers_materialized_section_lookup(monkeypatch) -> None:
    placeholder = SimpleNamespace(
        kind="section",
        label="9",
        text="9 §",
        children=[],
        attrs={"lawvm_repeal_placeholder": "1"},
    )
    substantive = SimpleNamespace(
        kind="section",
        label="9",
        text="9 § Erityisopetusta koskevat erityissäännökset",
        children=[],
        attrs={},
    )

    class FakeState:
        def find_section_path(self, section, chapter=None, part=None):
            if section == "9":
                return (("chapter", "3"), ("section", "9"))
            if section == "9 §":
                return (("hcontainer", ""), ("chapter", "3"), ("section", "9"))
            return None

    master = SimpleNamespace(
        title="Replay title",
        state=FakeState(),
        find_section=lambda section, chapter=None, part=None: substantive if section == "9 §" else None,
    )

    monkeypatch.setattr(replay_inspect, "replay_xml", lambda *args, **kwargs: master)

    bundle = replay_inspect.build_replay_inspect_bundle(
        statute_id="2012/422",
        section="9 §",
        mode="finlex_oracle",
        chapter="3",
    )

    assert bundle["section_path"] == "body / chapter:3 / section:9"
    assert bundle["section_metadata"]["attrs"] == {}
    assert "Erityisopetusta koskevat erityissäännökset" in bundle["section_text"]


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_build_amendment_bundle_2003_558_rewrites_named_row_repeals_and_replaces() -> None:
    try:
        bundle = inspect_amendment.build_amendment_bundle("1993/821", "2003/558", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    group = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "section" and group["target_norm"] == "1"
    )

    assert group["ops_final"] == [
        "REPEAL 1 § 1 mom 3 kohta",
        "REPLACE 1 § 1 mom 21 kohta",
        "REPEAL 1 § 1 mom 34 kohta",
        "REPLACE 1 § 1 mom 59 kohta",
        "REPLACE 1 § 1 mom 71 kohta",
    ]


def test_build_amendment_bundle_2006_148_rewrites_single_named_row_clause() -> None:
    try:
        bundle = inspect_amendment.build_amendment_bundle("1993/821", "2006/148", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    group = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "section" and group["target_norm"] == "1"
    )

    assert group["ops_final"] == [
        "REPLACE 1 § 1 mom 70 kohta",
    ]


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_build_amendment_bundle_2018_441_has_no_scope_authority_parity_mismatches() -> None:
    try:
        bundle = inspect_amendment.build_amendment_bundle("2010/1396", "2018/441", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    mismatches = [
        row
        for group in bundle["groups"]
        for row in (group.get("scope_authority_parity") or [])
        if not bool(row.get("matches"))
    ]

    assert mismatches == []


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_explain_2017_320_does_not_crash_on_materialization_regression(capsys) -> None:
    try:
        replay = pinned_replay("2017/320", mode="finlex_oracle", quiet=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    assert replay.title == "Laki liikenteen palveluista"


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_build_amendment_bundle_1987_411_runtime_scope_now_matches_explicit_chunk_projection() -> None:
    try:
        bundle = inspect_amendment.build_amendment_bundle("1901/15-001", "1987/411", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    rows = [
        (
            group["target_norm"],
            group["target_chapter"],
            bool(row.get("matches")),
            row.get("mismatch_kind"),
            (row.get("runtime") or {}).get("source"),
            (row.get("projection") or {}).get("source"),
        )
        for group in bundle["groups"]
        for row in (group.get("scope_authority_parity") or [])
        if group["target_norm"] in {"46", "47", "48", "49", "50", "51"}
    ]

    assert rows == [
        ("46", "4", True, "", "explicit_chunk", "explicit_chunk"),
        ("47", "4", True, "", "explicit_chunk", "explicit_chunk"),
        ("48", "4", True, "", "explicit_chunk", "explicit_chunk"),
        ("49", "4", True, "", "explicit_chunk", "explicit_chunk"),
        ("50", "4", True, "", "explicit_chunk", "explicit_chunk"),
        ("51", "4", True, "", "explicit_chunk", "explicit_chunk"),
    ]


def test_serialize_scope_authority_parity_surfaces_runtime_projection_agreement() -> None:
    from lawvm.finland.ops import AmendmentOp, ScopeConfidence

    op = AmendmentOp(
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="23",
        target_chapter="7",
    )
    op.scope_provenance_tags = ("chapter_scope_carry_forward",)
    op.scope_confidence = ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
            resolved_chapter="7",
        )

    row = inspect_amendment._serialize_scope_authority_parity(op)

    assert row["op"] == "REPLACE 7 luku 23 §"
    assert row["matches"] is True
    assert row["mismatch_kind"] == ""
    assert row["runtime"] == {
        "tag": "chapter_scope_from_explicit_chunk",
        "source": "explicit_chunk",
        "confidence": "explicit",
        "resolved_chapter": "7",
        "fallback_reason": "",
    }
    assert row["projection"] == {
        "tag": "chapter_scope_from_explicit_chunk",
        "source": "explicit_chunk",
        "confidence": "explicit",
        "resolved_chapter": "7",
        "fallback_reason": "",
    }


def test_bisect_section_main_prints_first_bad_and_worst_drops(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        bisect_section,
        "build_bisect_bundle",
        lambda statute_id, section, mode, threshold, top: {
            "statute_id": statute_id,
            "mode": mode,
            "section": "section:4",
            "baseline_score": 1.0,
            "threshold": threshold,
            "first_drop_source": "2020/162",
            "first_bad_source": "2020/162",
            "worst_drops": [
                {
                    "index": 3,
                    "source_id": "2020/162",
                    "score_before": 1.0,
                    "score_after": 0.6,
                    "delta": -0.4,
                }
            ],
            "steps": [],
        },
    )

    bisect_section.main(
        Namespace(
            statute_id="1995/1552",
            section="4 §",
            mode="legal_pit",
            threshold=0.9999,
            top=5,
            verbose=False,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert "First drop    : 2020/162" in out
    assert "First bad     : 2020/162" in out
    assert "Worst drops:" in out
    assert "[3] 2020/162: 100.0% -> 60.0% (-40.0%)" in out


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_classify_1987_1250_reports_item_target_structure_absent_pathologies() -> None:
    result = classify._classify_statute("1987/1250", "finlex_oracle")

    assert result is not None
    labels = {
        (entry.get("source_statute"), entry.get("code"), entry.get("target_label"))
        for entry in result.source_pathologies
    }
    assert ("1995/451", "SPARSE_ITEM_BODY_MISSING", "8 § 1 mom 5 kohta") in labels
    assert ("1995/451", "SPARSE_ITEM_BODY_MISSING", "9 § 1 mom 5a kohta") in labels


@pytest.mark.skipif(not _corpus_available(), reason="corpus data not available")
def test_replay_xml_1987_1250_resolves_1999_81_johd_without_failed_op() -> None:
    failed = []

    pinned_replay("1987/1250", mode="legal_pit", failed_ops_out=failed, quiet=True, build_full_products=False)

    assert not any(
        getattr(op, "source_statute", "") == "1999/81"
        and getattr(op, "target_section", "") == "10"
        and getattr(op, "target_chapter", None) == "16"
        and getattr(op, "target_special", None) == "johd"
        for op in failed
    )
