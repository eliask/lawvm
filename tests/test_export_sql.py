"""Tests for export_parquet and sql_query modules.

These tests verify the projection and SQL query infrastructure without
requiring a populated farchive corpus or duckdb.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


class TestExportParquet:
    """Tests for the JSONL export infrastructure."""

    def test_write_jsonl_creates_file(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _write_jsonl

        rows = [
            {"statute_id": "2006/1299", "score": 0.95},
            {"statute_id": "2017/794", "score": 0.88},
        ]
        path = tmp_path / "test.jsonl"
        count = _write_jsonl(path, rows)

        assert count == 2
        assert path.exists()

        # Verify JSONL format
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        row0 = json.loads(lines[0])
        assert row0["statute_id"] == "2006/1299"
        assert row0["score"] == 0.95

    def test_write_jsonl_empty(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _write_jsonl

        path = tmp_path / "empty.jsonl"
        count = _write_jsonl(path, [])
        assert count == 0
        assert path.exists()
        assert path.read_text() == ""

    def test_write_jsonl_creates_parent_dirs(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _write_jsonl

        path = tmp_path / "sub" / "dir" / "test.jsonl"
        _write_jsonl(path, [{"x": 1}])
        assert path.exists()

    def test_load_corpus_format(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _load_corpus

        csv_path = tmp_path / "corpus.csv"
        csv_path.write_text("5,2006/1299\n3,2017/794\n1,2020/100\n")
        corpus = _load_corpus(str(csv_path))

        assert len(corpus) == 3
        assert corpus[0] == (5, "2006/1299")
        assert corpus[1] == (3, "2017/794")
        assert corpus[2] == (1, "2020/100")

    def test_load_corpus_skips_malformed(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _load_corpus

        csv_path = tmp_path / "corpus.csv"
        csv_path.write_text("5,2006/1299\nbad\n\n1,2020/100\n")
        corpus = _load_corpus(str(csv_path))
        assert len(corpus) == 2

    def test_try_write_parquet_returns_false_without_pyarrow(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _try_write_parquet

        # pyarrow is not installed, so this should return False
        result = _try_write_parquet(
            tmp_path / "test.parquet",
            [{"x": 1}],
        )
        assert result is False

    def test_try_write_parquet_empty_rows(self, tmp_path: Path) -> None:
        from lawvm.tools.export_parquet import _try_write_parquet

        result = _try_write_parquet(tmp_path / "empty.parquet", [])
        assert result is False

    def test_project_one_statute_emits_finding_when_section_diff_fails(
        self,
        monkeypatch,
    ) -> None:
        from lawvm.tools.export_parquet import (
            SECTION_DIFF_FAILED_RULE_ID,
            _project_one_statute,
        )

        levenshtein = types.ModuleType("Levenshtein")
        cast(Any, levenshtein).ratio = lambda _left, _right: 1.0

        grafter = types.ModuleType("lawvm.finland.grafter")
        grafter_patch = cast(Any, grafter)

        class FakeMaster:
            title = "Synthetic statute"
            materialized_state = SimpleNamespace(ir=object())

            def serialize_text(self) -> str:
                return "synthetic replay text"

        grafter_patch.replay_xml = lambda *args, **kwargs: FakeMaster()
        grafter_patch.get_ground_truth = lambda _statute_id: "synthetic replay text"
        grafter_patch._oracle_version_label = lambda _statute_id: "synthetic-oracle"

        section_keys = types.ModuleType("lawvm.tools.section_keys")
        section_keys_patch = cast(Any, section_keys)
        section_keys_patch.extract_ir_sections = lambda _ir: {"1": object()}
        section_keys_patch.extract_oracle_sections = lambda _root: {}
        section_keys_patch.reconcile_unique_unscoped_aliases = lambda replay_sections, oracle_sections: (replay_sections, oracle_sections)

        corpus = types.ModuleType("lawvm.finland.corpus")
        corpus_patch = cast(Any, corpus)

        def fail_ground_truth_tree(_statute_id: str) -> object:
            raise RuntimeError("synthetic section diff failure")

        corpus_patch.get_ground_truth_tree = fail_ground_truth_tree

        monkeypatch.setitem(sys.modules, "Levenshtein", levenshtein)
        monkeypatch.setitem(sys.modules, "lawvm.finland.grafter", grafter)
        monkeypatch.setitem(sys.modules, "lawvm.tools.section_keys", section_keys)
        monkeypatch.setitem(sys.modules, "lawvm.finland.corpus", corpus)

        result = _project_one_statute("2099/1", 3)

        assert result["statute"][0]["status"] == "OK"
        assert result["sections"] == []
        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        assert finding["rule_id"] == SECTION_DIFF_FAILED_RULE_ID
        assert finding["claim_kind"] == SECTION_DIFF_FAILED_RULE_ID
        assert finding["phase"] == "projection"
        assert finding["blocking"] is False
        assert finding["strict_disposition"] == "record"
        assert finding["quirks_disposition"] == "record"
        assert finding["status"] == "section_diff_failed"
        assert finding["error_type"] == "RuntimeError"
        assert "synthetic section diff failure" in finding["detail"]


class TestSqlQuery:
    """Tests for the SQL query infrastructure."""

    def test_check_duckdb_returns_bool(self) -> None:
        from lawvm.tools.sql_query import _check_duckdb

        # duckdb is not installed in test env
        result = _check_duckdb()
        assert isinstance(result, bool)

    def test_discover_tables_empty_dir(self, tmp_path: Path) -> None:
        from lawvm.tools.sql_query import _discover_tables

        tables = _discover_tables(tmp_path)
        assert tables == {}

    def test_discover_tables_finds_jsonl(self, tmp_path: Path) -> None:
        from lawvm.tools.sql_query import _discover_tables

        (tmp_path / "statutes.jsonl").write_text('{"x": 1}\n')
        (tmp_path / "sections.jsonl").write_text('{"y": 2}\n')

        tables = _discover_tables(tmp_path)
        assert "statutes" in tables
        assert "sections" in tables
        assert tables["statutes"].suffix == ".jsonl"

    def test_discover_tables_parquet_preferred(self, tmp_path: Path) -> None:
        from lawvm.tools.sql_query import _discover_tables

        (tmp_path / "statutes.jsonl").write_text('{"x": 1}\n')
        (tmp_path / "statutes.parquet").write_text("fake-parquet")

        tables = _discover_tables(tmp_path)
        assert tables["statutes"].suffix == ".parquet"

    def test_format_results_empty(self) -> None:
        from lawvm.tools.sql_query import _format_results

        result = _format_results(["a", "b"], [])
        assert result == "(0 rows)"

    def test_format_results_with_data(self) -> None:
        from lawvm.tools.sql_query import _format_results

        result = _format_results(
            ["id", "score"],
            [("2006/1299", 0.95), ("2017/794", 0.88)],
        )
        assert "2006/1299" in result
        assert "0.95" in result
        assert "(2 rows)" in result

    def test_json_safe(self) -> None:
        from lawvm.tools.sql_query import _json_safe

        assert _json_safe(None) is None
        assert _json_safe(42) == 42
        assert _json_safe("hello") == "hello"
        assert _json_safe([1, 2]) == [1, 2]
        assert isinstance(_json_safe(object()), str)
