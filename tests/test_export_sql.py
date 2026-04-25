"""Tests for export_parquet and sql_query modules.

These tests verify the projection and SQL query infrastructure without
requiring a populated farchive corpus or duckdb.
"""
from __future__ import annotations

import json
from pathlib import Path


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
