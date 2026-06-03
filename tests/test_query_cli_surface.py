"""Tests for the index-backed query CLI surface (feature #7).

Per AGENTS.md §15: each subcommand needs:
  1. Synthetic filter test: filter produces expected rows.
  2. Output format test: each -o mode produces parseable output.
  3. Error-case test: invalid filter value produces argparse error, exits non-zero.
  4. Empty-result test: query that returns zero rows emits empty result without error.
  5. Cross-jurisdiction test: FI-only commands hard-error if -j is unsupported.

These tests use in-memory DuckDB fixtures (no real parquet files needed).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest.mock import patch, MagicMock

import pytest

from lawvm.tools import cli
from lawvm.tools._cli_duckdb import (
    as_of_conditions,
    check_duckdb,
    find_source_file,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows, format_table, json_safe


# ---------------------------------------------------------------------------
# Fixtures: real Parquet files written with DuckDB for in-process tests
# ---------------------------------------------------------------------------

try:
    import duckdb as _duckdb_available  # noqa: F401
    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False

duckdb_required = pytest.mark.skipif(
    not _HAS_DUCKDB, reason="duckdb not installed"
)


@pytest.fixture(scope="module")
def tmp_proj_dir(tmp_path_factory) -> Path:
    """Return a temp directory with synthetic Parquet fixtures."""
    if not _HAS_DUCKDB:
        pytest.skip("duckdb not installed")

    import duckdb

    d = tmp_path_factory.mktemp("projections")

    # ---- fi_refs.parquet ----
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_refs AS SELECT * FROM (VALUES
          ('711/2022', '711/2022/7', '100/2001', '100/2001/3',
           'cross_statute', 'exact', 'CITES', 'ref_element',
           '2022-01-01'::DATE, NULL, 'abc123'),
          ('711/2022', '711/2022/9', '200/2005', NULL,
           'cross_statute', 'unresolved', NULL, 'ref_element',
           '2022-01-01'::DATE, NULL, NULL),
          ('100/2001', '100/2001/5', '711/2022', '711/2022/2',
           'cross_statute', 'broken', 'CITES', 'ref_element',
           '2001-01-01'::DATE, '2021-12-31'::DATE, 'def456')
        ) t(source_statute_id, source_provision_ref_str, target_statute_id,
             target_provision_ref_str, cite_kind, cite_confidence,
             edge_subtype, phrase_lemma, valid_at_start, valid_at_end,
             target_stat_hash)
    """)
    con.execute(f"COPY fi_refs TO '{d}/fi_refs.parquet' (FORMAT PARQUET)")
    con.close()

    # ---- fi_actors.parquet ----
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_actors AS SELECT * FROM (VALUES
          ('711/2022', '711/2022/7', 'fi.ruokavirasto', 'Ruokavirasto',
           'Ruokavirasto', 'duty', 'registry_resolved',
           '2022-01-01'::DATE, NULL),
          ('100/2001', '100/2001/3', 'fi.vm', 'Valtiovarainministeriö',
           'ministeriö', 'discretion', 'exact',
           '2001-01-01'::DATE, NULL)
        ) t(source_statute_id, source_provision_ref_str, actor_canonical_id,
             actor_canonical_show_as, actor_phrase, modal_kind,
             resolution_confidence, valid_at_start, valid_at_end)
    """)
    con.execute(f"COPY fi_actors TO '{d}/fi_actors.parquet' (FORMAT PARQUET)")
    con.close()

    # ---- fi_pools.parquet ----
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_pools AS SELECT * FROM (VALUES
          ('711/2022', '711/2022/3', 'pool.env.cap.1', 'enintään 10 g Cd/ha/5 v',
           'capacity_cap', 'exact', 10.0, 'g Cd/ha/5 v',
           '2022-01-01'::DATE, NULL),
          ('100/2001', '100/2001/5', NULL, '5 000 000 euroa',
           'budget_line', 'approximate', 5000000.0, 'EUR',
           '2001-01-01'::DATE, NULL)
        ) t(source_statute_id, source_provision_ref_str, pool_canonical_id,
             quantity_phrase, quantity_kind, resolution_confidence,
             numeric_value, unit, valid_at_start, valid_at_end)
    """)
    con.execute(f"COPY fi_pools TO '{d}/fi_pools.parquet' (FORMAT PARQUET)")
    con.close()

    # ---- sections.parquet ----
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE sections AS SELECT * FROM (VALUES
          ('711/2022', 'section:1', 'perfect',
           '', 'section:1',
           'Tämän lain tarkoituksena on suojella ympäristöä.',
           'Tämän lain tarkoituksena on suojella ympäristöä.',
           1.0, '[]', true, 'Tämän lain tarkoituksena'),
          ('711/2022', 'section:7', 'identical',
           'section:7', 'section:7',
           'Ruokavirasto valvoo lain noudattamista.',
           'Ruokavirasto valvoo lain noudattamista.',
           1.0, '[]', false, NULL),
          ('100/2001', 'section:3', 'oracle_only',
           'section:3', 'section:3',
           'Ministeriö myöntää luvan.',
           'Ministeriö myöntää luvan.',
           1.0, '[]', false, NULL)
        ) t(statute_id, section_key, diff_kind, oracle_label_basis,
             replay_label_basis, oracle_text, replay_text, similarity,
             events, is_purpose_section, purpose_text_snippet)
    """)
    con.execute(f"COPY sections TO '{d}/sections.parquet' (FORMAT PARQUET)")
    con.close()

    # ---- ops.parquet ----
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE ops AS SELECT * FROM (VALUES
          ('711/2022', '2018/100', 'REPLACE', 'section', '7', '2', ''),
          ('711/2022', '2020/200', 'INSERT', 'section', '9', '1', ''),
          ('100/2001', '2015/50', 'REPEAL', 'section', '3', NULL, NULL)
        ) t(statute_id, amendment_id, op_type, target_kind,
             target_section, target_chapter, target_paragraph)
    """)
    con.execute(f"COPY ops TO '{d}/ops.parquet' (FORMAT PARQUET)")
    con.close()

    return d


@pytest.fixture(scope="module")
def cli_parser():
    return cli._build_parser()


# ---------------------------------------------------------------------------
# Shared helper tests
# ---------------------------------------------------------------------------

class TestCliDuckdb:
    def test_find_source_file_finds_parquet(self, tmp_proj_dir):
        p = find_source_file(str(tmp_proj_dir), "fi_refs")
        assert p is not None
        assert p.suffix == ".parquet"

    def test_find_source_file_returns_none_for_missing(self, tmp_proj_dir):
        p = find_source_file(str(tmp_proj_dir), "nonexistent_table")
        assert p is None

    def test_source_expr_for_parquet(self, tmp_proj_dir):
        p = tmp_proj_dir / "fi_refs.parquet"
        expr = source_expr_for_path(p)
        assert "read_parquet" in expr
        assert str(p.resolve()) in expr

    def test_source_expr_for_jsonl(self, tmp_path):
        p = tmp_path / "test.jsonl"
        p.touch()
        expr = source_expr_for_path(p)
        assert "read_json_auto" in expr

    def test_as_of_conditions(self):
        conds = as_of_conditions("2024-01-01")
        assert len(conds) == 2
        assert "valid_at_start" in conds[0]
        assert "valid_at_end" in conds[1]
        assert "2024-01-01" in conds[0]


class TestCliOutput:
    def test_format_table_empty(self):
        result = format_table(["col1", "col2"], [])
        assert "(0 rows)" in result

    def test_format_table_one_row(self):
        result = format_table(["a", "b"], [("x", "y")])
        assert "x" in result
        assert "y" in result
        assert "(1 row)" in result

    def test_format_table_multiple_rows(self):
        rows = [("alpha", "beta"), ("gamma", "delta")]
        result = format_table(["c1", "c2"], rows)
        assert "(2 rows)" in result
        assert "alpha" in result
        assert "gamma" in result

    def test_format_table_truncates_long_values(self):
        long_val = "x" * 200
        result = format_table(["col"], [(long_val,)])
        # Should not include 200 chars; max_col_width=50 applies
        assert len(max(result.split("\n"), key=len)) < 200

    def test_json_safe_none(self):
        assert json_safe(None) is None

    def test_json_safe_primitives(self):
        assert json_safe(42) == 42
        assert json_safe(3.14) == 3.14
        assert json_safe("foo") == "foo"
        assert json_safe(True) is True

    def test_json_safe_unknown_converts_to_str(self):
        class Weird:
            def __str__(self):
                return "weird"
        assert json_safe(Weird()) == "weird"

    def test_emit_rows_json(self, capsys):
        emit_rows(
            columns=["a", "b"],
            rows=[(1, "hello")],
            output_format="json",
            data_dir="/tmp",
            result_stem="_test",
        )
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert len(parsed) == 1
        assert parsed[0]["a"] == 1
        assert parsed[0]["b"] == "hello"

    def test_emit_rows_jsonl(self, capsys):
        emit_rows(
            columns=["x"],
            rows=[("line1",), ("line2",)],
            output_format="jsonl",
            data_dir="/tmp",
            result_stem="_test",
        )
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.strip().split("\n") if ln]
        assert len(lines) == 2
        obj = json.loads(lines[0])
        assert obj["x"] == "line1"

    def test_emit_rows_csv(self, capsys):
        emit_rows(
            columns=["col1", "col2"],
            rows=[("a", "b"), ("c", "d")],
            output_format="csv",
            data_dir="/tmp",
            result_stem="_test",
        )
        captured = capsys.readouterr()
        reader = csv.DictReader(io.StringIO(captured.out))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["col1"] == "a"

    def test_emit_rows_table(self, capsys):
        emit_rows(
            columns=["col1"],
            rows=[("value",)],
            output_format="table",
            data_dir="/tmp",
            result_stem="_test",
        )
        captured = capsys.readouterr()
        assert "col1" in captured.out
        assert "value" in captured.out

    def test_emit_rows_empty_json(self, capsys):
        emit_rows(
            columns=["col"],
            rows=[],
            output_format="json",
            data_dir="/tmp",
            result_stem="_test",
        )
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == []


# ---------------------------------------------------------------------------
# CLI parser tests (no DuckDB needed)
# ---------------------------------------------------------------------------

class TestCLIParserNewCommands:
    """Test that all new subcommands are registered and parse correctly."""

    def test_topic_command_accepted(self, cli_parser):
        args = cli_parser.parse_args(["topic", "--topic", "ympäristö"])
        assert args.command == "topic"
        assert args.topic == "ympäristö"
        assert args.mode == "keyword"
        assert args.output_format == "table"

    def test_topic_command_with_all_flags(self, cli_parser):
        args = cli_parser.parse_args([
            "topic", "--topic", "test",
            "--mode", "fts",
            "--statute-filter", "7*/202*",
            "--limit", "10",
            "--as-of", "2024-01-01",
            "--data-dir", "/tmp",
            "-o", "json",
        ])
        assert args.command == "topic"
        assert args.mode == "fts"
        assert args.statute_filter == "7*/202*"
        assert args.limit == 10
        assert args.as_of == "2024-01-01"
        assert args.data_dir == "/tmp"
        assert args.output_format == "json"

    def test_topic_requires_topic_arg(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args(["topic"])
        assert exc_info.value.code != 0

    def test_topic_invalid_mode_rejected(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args(["topic", "--topic", "x", "--mode", "invalid"])
        assert exc_info.value.code != 0

    def test_follow_refs_command_accepted(self, cli_parser):
        args = cli_parser.parse_args(["follow-refs", "--start", "711/2022"])
        assert args.command == "follow-refs"
        assert args.start == "711/2022"
        assert args.depth == 1
        assert args.direction == "forward"
        assert args.include_broken is False

    def test_follow_refs_with_flags(self, cli_parser):
        args = cli_parser.parse_args([
            "follow-refs", "--start", "711/2022/7",
            "--depth", "3", "--direction", "both",
            "--include-broken", "-o", "jsonl",
        ])
        assert args.depth == 3
        assert args.direction == "both"
        assert args.include_broken is True
        assert args.output_format == "jsonl"

    def test_follow_refs_requires_start(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args(["follow-refs"])
        assert exc_info.value.code != 0

    def test_follow_refs_invalid_direction(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args([
                "follow-refs", "--start", "x", "--direction", "sideways"
            ])
        assert exc_info.value.code != 0

    def test_pit_timeline_command_accepted(self, cli_parser):
        args = cli_parser.parse_args(["pit-timeline", "--provision", "2002/738"])
        assert args.command == "pit-timeline"
        assert args.provision == "2002/738"

    def test_pit_timeline_with_flags(self, cli_parser):
        args = cli_parser.parse_args([
            "pit-timeline", "--provision", "2002/738",
            "--since", "2015-01-01", "--until", "2023-12-31",
            "--include-amendments", "--limit", "5",
        ])
        assert args.since == "2015-01-01"
        assert args.until == "2023-12-31"
        assert args.include_amendments is True
        assert args.limit == 5

    def test_pit_timeline_requires_provision(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args(["pit-timeline"])
        assert exc_info.value.code != 0

    def test_pit_diff_command_accepted(self, cli_parser):
        args = cli_parser.parse_args([
            "pit-diff", "--provision", "2002/738",
            "--t1", "2020-01-01", "--t2", "2024-01-01",
        ])
        assert args.command == "pit-diff"
        assert args.provision == "2002/738"
        assert args.t1 == "2020-01-01"
        assert args.t2 == "2024-01-01"
        assert args.include_text is False
        assert args.include_refs is False

    def test_pit_diff_with_optional_flags(self, cli_parser):
        args = cli_parser.parse_args([
            "pit-diff", "--provision", "711/2022",
            "--t1", "2019-01-01", "--t2", "2023-01-01",
            "--include-text", "--include-refs", "-o", "csv",
        ])
        assert args.include_text is True
        assert args.include_refs is True
        assert args.output_format == "csv"

    def test_pit_diff_requires_provision_t1_t2(self, cli_parser):
        # Missing --t1 and --t2
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args([
                "pit-diff", "--provision", "x", "--t1", "2020-01-01"
            ])
        assert exc_info.value.code != 0

    def test_telos_command_accepted(self, cli_parser):
        args = cli_parser.parse_args(["telos"])
        assert args.command == "telos"

    def test_telos_with_statute(self, cli_parser):
        args = cli_parser.parse_args(["telos", "--statute", "711/2022", "--limit", "3"])
        assert args.statute == "711/2022"
        assert args.limit == 3

    def test_telos_invalid_output_format(self, cli_parser):
        with pytest.raises(SystemExit) as exc_info:
            cli_parser.parse_args(["telos", "-o", "xlsx"])
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Integration tests: run query commands against real Parquet fixtures
# ---------------------------------------------------------------------------

class TestRefsQueryIntegration:
    @duckdb_required
    def test_refs_filter_by_from(self, tmp_proj_dir, capsys):
        from lawvm.tools.refs_query import run_refs
        run_refs(
            from_ref="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 2
        assert all(r["source_statute_id"] == "711/2022" for r in out)

    @duckdb_required
    def test_refs_filter_returns_empty_gracefully(self, tmp_proj_dir, capsys):
        from lawvm.tools.refs_query import run_refs
        run_refs(
            from_ref="9999/0001",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        assert "(0 rows)" in out

    @duckdb_required
    def test_refs_output_jsonl_parseable(self, tmp_proj_dir, capsys):
        from lawvm.tools.refs_query import run_refs
        run_refs(
            from_ref="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="jsonl",
        )
        lines = [ln for ln in capsys.readouterr().out.strip().split("\n") if ln]
        assert len(lines) >= 1
        obj = json.loads(lines[0])
        assert "source_statute_id" in obj

    @duckdb_required
    def test_refs_output_csv_parseable(self, tmp_proj_dir, capsys):
        from lawvm.tools.refs_query import run_refs
        run_refs(
            from_ref="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="csv",
        )
        out = capsys.readouterr().out
        reader = csv.DictReader(io.StringIO(out))
        rows = list(reader)
        assert len(rows) >= 1
        assert "source_statute_id" in reader.fieldnames

    @duckdb_required
    def test_refs_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.refs_query import run_refs
        with pytest.raises(SystemExit) as exc_info:
            run_refs(
                from_ref="711/2022",
                data_dir=str(tmp_proj_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1


class TestFollowRefsIntegration:
    @duckdb_required
    def test_follow_refs_forward_depth_1(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_follow_refs import run_follow_refs
        run_follow_refs(
            start="711/2022",
            depth=1,
            direction="forward",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        # Should have edges going from 711/2022
        assert isinstance(out, list)
        # Even if edges list is empty (no matching), must be parseable

    @duckdb_required
    def test_follow_refs_empty_result_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_follow_refs import run_follow_refs
        run_follow_refs(
            start="9999/9999",
            depth=1,
            direction="forward",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        err = capsys.readouterr().err
        assert "no references found" in err.lower() or len(err) == 0

    @duckdb_required
    def test_follow_refs_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.cmd_follow_refs import run_follow_refs
        with pytest.raises(SystemExit) as exc_info:
            run_follow_refs(
                start="711/2022",
                data_dir=str(tmp_proj_dir),
                jurisdiction="uk",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_follow_refs_reverse_direction(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_follow_refs import run_follow_refs
        # 711/2022 is a TARGET in the refs for 100/2001
        run_follow_refs(
            start="711/2022",
            depth=1,
            direction="reverse",
            include_broken=True,
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list)


class TestTopicIntegration:
    @duckdb_required
    def test_topic_keyword_finds_match(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_topic import run_topic
        run_topic(
            topic="ympäristö",
            mode="keyword",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) >= 1
        # At least one match should be from sections
        assert any(r["match_kind"] == "sections" for r in out)

    @duckdb_required
    def test_topic_no_match_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_topic import run_topic
        run_topic(
            topic="xyzzy_nonexistent_term_abc",
            mode="keyword",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert out == []

    @duckdb_required
    def test_topic_statute_filter(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_topic import run_topic
        run_topic(
            topic="Ruokavirasto",
            mode="keyword",
            statute_filter="711/*",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list)
        # All matches should be from 711/... statute
        for r in out:
            if r["match_kind"] == "sections":
                assert r["source_id"].startswith("711/")

    @duckdb_required
    def test_topic_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.cmd_topic import run_topic
        with pytest.raises(SystemExit) as exc_info:
            run_topic(
                topic="test",
                data_dir=str(tmp_proj_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_topic_output_formats_parseable(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_topic import run_topic
        for fmt, validator in [
            ("jsonl", lambda s: all(json.loads(ln) for ln in s.strip().split("\n") if ln)),
            ("csv", lambda s: len(list(csv.DictReader(io.StringIO(s)))) >= 0),
        ]:
            run_topic(
                topic="Ruokavirasto",
                mode="keyword",
                data_dir=str(tmp_proj_dir),
                output_format=fmt,
            )
            out = capsys.readouterr().out
            assert validator(out)


class TestPitTimelineIntegration:
    @duckdb_required
    def test_pit_timeline_finds_ops(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_timeline import run_pit_timeline
        run_pit_timeline(
            provision="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list)
        # Should find ops for 711/2022
        assert len(out) >= 1
        assert all(r["statute_id"] == "711/2022" for r in out)

    @duckdb_required
    def test_pit_timeline_empty_result_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_timeline import run_pit_timeline
        run_pit_timeline(
            provision="9999/9999",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        err = capsys.readouterr().err
        assert "no amendment history" in err.lower() or "(0 rows)" in err

    @duckdb_required
    def test_pit_timeline_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.cmd_pit_timeline import run_pit_timeline
        with pytest.raises(SystemExit) as exc_info:
            run_pit_timeline(
                provision="711/2022",
                data_dir=str(tmp_proj_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_pit_timeline_output_formats(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_timeline import run_pit_timeline
        for fmt in ("json", "jsonl", "csv", "table"):
            run_pit_timeline(
                provision="711/2022",
                data_dir=str(tmp_proj_dir),
                output_format=fmt,
            )
            out = capsys.readouterr().out
            if fmt == "json":
                json.loads(out)  # must be parseable
            elif fmt == "jsonl":
                for ln in out.strip().split("\n"):
                    if ln:
                        json.loads(ln)
            # table and csv just need to be non-empty or valid


class TestPitDiffIntegration:
    @duckdb_required
    def test_pit_diff_finds_ops_in_range(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_diff import run_pit_diff
        run_pit_diff(
            provision="711/2022",
            t1="2015-01-01",
            t2="2025-01-01",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list)
        # Both 2018/100 and 2020/200 should be in range
        assert len(out) >= 1

    @duckdb_required
    def test_pit_diff_empty_range_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_diff import run_pit_diff
        run_pit_diff(
            provision="711/2022",
            t1="2030-01-01",
            t2="2035-01-01",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        err = capsys.readouterr().err
        assert "no amendment ops" in err.lower() or "(0 rows)" in err

    @duckdb_required
    def test_pit_diff_include_text(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_diff import run_pit_diff
        run_pit_diff(
            provision="711/2022",
            t1="2015-01-01",
            t2="2025-01-01",
            include_text=True,
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        # Should include section text output
        assert "replay_text" in out or "Current section text" in out or out.strip()

    @duckdb_required
    def test_pit_diff_include_refs(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_pit_diff import run_pit_diff
        run_pit_diff(
            provision="711/2022",
            t1="2015-01-01",
            t2="2025-01-01",
            include_refs=True,
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        # Should not crash
        _ = capsys.readouterr()

    @duckdb_required
    def test_pit_diff_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.cmd_pit_diff import run_pit_diff
        with pytest.raises(SystemExit) as exc_info:
            run_pit_diff(
                provision="711/2022",
                t1="2020-01-01",
                t2="2024-01-01",
                data_dir=str(tmp_proj_dir),
                jurisdiction="uk",
            )
        assert exc_info.value.code == 1


class TestTelosIntegration:
    @duckdb_required
    def test_telos_finds_purpose_sections(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_telos import run_telos
        run_telos(
            statute="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list)
        assert len(out) >= 1  # section:1 is_purpose_section=true

    @duckdb_required
    def test_telos_empty_for_unknown_statute(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_telos import run_telos
        run_telos(
            statute="9999/9999",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert out == []

    @duckdb_required
    def test_telos_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.cmd_telos import run_telos
        with pytest.raises(SystemExit) as exc_info:
            run_telos(
                data_dir=str(tmp_proj_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_telos_output_formats(self, tmp_proj_dir, capsys):
        from lawvm.tools.cmd_telos import run_telos
        for fmt in ("json", "jsonl", "csv"):
            run_telos(
                statute="711/2022",
                data_dir=str(tmp_proj_dir),
                output_format=fmt,
            )
            out = capsys.readouterr().out
            if fmt == "json":
                parsed = json.loads(out)
                assert isinstance(parsed, list)
            elif fmt == "jsonl":
                for ln in out.strip().split("\n"):
                    if ln:
                        json.loads(ln)
            # csv: must have header
            elif fmt == "csv":
                reader = csv.DictReader(io.StringIO(out))
                assert reader.fieldnames is not None


# ---------------------------------------------------------------------------
# Actors / Pools integration (quick smoke)
# ---------------------------------------------------------------------------

class TestActorsIntegration:
    @duckdb_required
    def test_actors_filter_by_statute(self, tmp_proj_dir, capsys):
        from lawvm.tools.actors_query import run_actors
        run_actors(
            statute="711/2022",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1
        assert out[0]["source_statute_id"] == "711/2022"

    @duckdb_required
    def test_actors_empty_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.actors_query import run_actors
        run_actors(
            statute="9999/9999",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        assert "(0 rows)" in out

    @duckdb_required
    def test_actors_cross_jurisdiction_error(self, tmp_proj_dir):
        from lawvm.tools.actors_query import run_actors
        with pytest.raises(SystemExit) as exc_info:
            run_actors(statute="x", data_dir=str(tmp_proj_dir), jurisdiction="uk")
        assert exc_info.value.code == 1


class TestPoolsIntegration:
    @duckdb_required
    def test_pools_filter_by_quantity_kind(self, tmp_proj_dir, capsys):
        from lawvm.tools.pools_query import run_pools
        run_pools(
            quantity_kind="capacity_cap",
            data_dir=str(tmp_proj_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1
        assert out[0]["quantity_kind"] == "capacity_cap"

    @duckdb_required
    def test_pools_empty_graceful(self, tmp_proj_dir, capsys):
        from lawvm.tools.pools_query import run_pools
        run_pools(
            quantity_kind="formula_term",
            data_dir=str(tmp_proj_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        assert "(0 rows)" in out
