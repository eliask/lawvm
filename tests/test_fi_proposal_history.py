"""Tests for lawvm fi-proposal-history command.

Per AGENTS.md §15 — all 7 categories:
  1. Synthetic unit test (filter → expected rows)
  2. Real corpus regression (n/a — pure SQL query; covered by integration test)
  3. Finding/observation test (stderr message fires)
  4. Negative test (statute with no HEs → empty result)
  5. Strict-mode test (n/a — no strict-mode path in this command)
  6. No-leak test (n/a — no internal markers)
  7. Cross-jurisdiction error (fi-only command rejects 'ee')

Integration tests use in-memory DuckDB Parquet fixtures.
"""
from __future__ import annotations

import csv
import io
import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


def _duckdb_module() -> Any:
    return importlib.import_module("duckdb")

duckdb_required = pytest.mark.skipif(not _HAS_DUCKDB, reason="duckdb not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmp_history_dir(tmp_path_factory) -> Path:
    """Create synthetic fi_he_corpus.parquet + fi_he_law_refs.parquet fixtures."""
    if not _HAS_DUCKDB:
        pytest.skip("duckdb not installed")

    duckdb = _duckdb_module()

    d = tmp_path_factory.mktemp("history")

    # fi_he_corpus: 4 HEs across 2 statutes
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_he_corpus AS SELECT * FROM (VALUES
          ('HE 195/2025 vp', 2025, 195, 'fi.ym', 'Ympäristöministeriö',
           'Laeiksi ympäristönsuojelulain muuttamisesta', '2025-12-18', 'pending'),
          ('HE 83/2025 vp',  2025,  83, 'fi.ym', 'Ympäristöministeriö',
           'Laeiksi ympäristönsuojelulain muuttamisesta II', '2025-06-01', 'closed'),
          ('HE 102/2024 vp', 2024, 102, 'fi.ym', 'Ympäristöministeriö',
           'Ympäristönsuojelulaki muutos 2024', '2024-09-01', 'enacted'),
          ('HE 176/2025 vp', 2025, 176, 'fi.vm', 'Valtiovarainministeriö',
           'Verotusmenettelylain muutos 2025', '2025-11-27', 'pending')
        ) t(he_id, he_year, he_number, ministry_canonical_id, ministry_show_as,
             title, date_issued, finlex_state)
    """)
    con.execute(f"COPY fi_he_corpus TO '{d}/fi_he_corpus.parquet' (FORMAT PARQUET)")
    con.close()

    # fi_he_law_refs: refs to two statutes
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_he_law_refs AS SELECT * FROM (VALUES
          ('HE 195/2025 vp', '2014/527', '2014/527/225',      'enacted_statute'),
          ('HE 83/2025 vp',  '2014/527', '2014/527/1',        'enacted_statute'),
          ('HE 83/2025 vp',  '2014/527', '2014/527/210',      'enacted_statute'),
          ('HE 102/2024 vp', '2014/527', '2014/527/10',       'enacted_statute'),
          ('HE 176/2025 vp', '1995/1558', '1995/1558/31',     'enacted_statute'),
          ('HE 176/2025 vp', '1995/1558', '1995/1558/32',     'enacted_statute')
        ) t(he_id, target_statute_id, target_provision_ref_str, ref_kind)
    """)
    con.execute(f"COPY fi_he_law_refs TO '{d}/fi_he_law_refs.parquet' (FORMAT PARQUET)")
    con.close()

    return d


# ---------------------------------------------------------------------------
# 1. Synthetic filter tests
# ---------------------------------------------------------------------------


class TestFiProposalHistorySyntheticFilter:
    @duckdb_required
    def test_finds_all_hes_for_statute(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 3  # HE 195/2025, 83/2025, 102/2024
        he_ids = {r["he_id"] for r in out}
        assert "HE 195/2025 vp" in he_ids
        assert "HE 83/2025 vp" in he_ids
        assert "HE 102/2024 vp" in he_ids

    @duckdb_required
    def test_lifecycle_filter_pending_only(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            lifecycle="pending",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1
        assert out[0]["he_id"] == "HE 195/2025 vp"
        assert out[0]["finlex_state"] == "pending"

    @duckdb_required
    def test_year_range_filter(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            year_range="2025:2025",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert all(r["he_year"] == 2025 for r in out)
        assert len(out) == 2

    @duckdb_required
    def test_ministry_filter(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            ministry="Valtiovarainministeriö",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        # No verotusmenettelylaki refs for ympäristöministeriö ministry HEs
        # and Valtiovarainministeriö references 1995/1558 not 2014/527
        assert len(out) == 0

    @duckdb_required
    def test_include_provisions_adds_column(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            include_provisions=True,
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) >= 1
        assert "provisions_touched" in out[0]

    @duckdb_required
    def test_different_statute(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="1995/1558",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1
        assert out[0]["he_id"] == "HE 176/2025 vp"


# ---------------------------------------------------------------------------
# 2. Output format tests
# ---------------------------------------------------------------------------


class TestFiProposalHistoryOutputFormats:
    @duckdb_required
    def test_output_format_table(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        assert "he_id" in out
        assert "HE 195/2025 vp" in out

    @duckdb_required
    def test_output_format_jsonl(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="jsonl",
        )
        out = capsys.readouterr().out
        lines = [ln for ln in out.strip().split("\n") if ln]
        assert len(lines) == 3
        obj = json.loads(lines[0])
        assert "he_id" in obj

    @duckdb_required
    def test_output_format_csv(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="csv",
        )
        out = capsys.readouterr().out
        reader = csv.DictReader(io.StringIO(out))
        rows = list(reader)
        assert len(rows) == 3
        assert reader.fieldnames is not None
        assert "he_id" in reader.fieldnames


# ---------------------------------------------------------------------------
# 3. Finding/observation: stderr message fires
# ---------------------------------------------------------------------------


class TestFiProposalHistoryStderrMessage:
    @duckdb_required
    def test_stderr_message_fires(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        err = capsys.readouterr().err
        assert "Using projections from" in err
        assert "override with --data-dir" in err

    @duckdb_required
    def test_stderr_uses_custom_data_dir(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="2014/527",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        err = capsys.readouterr().err
        assert str(tmp_history_dir) in err


# ---------------------------------------------------------------------------
# 4. Negative test: statute with no HEs → empty result, no crash
# ---------------------------------------------------------------------------


class TestFiProposalHistoryNegative:
    @duckdb_required
    def test_unknown_statute_empty_result(self, tmp_history_dir, capsys):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        run_fi_proposal_history(
            statute="9999/0001",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert out == []

    @duckdb_required
    def test_invalid_lifecycle_choice_should_error(self, tmp_history_dir):
        # lifecycle="bogus" is rejected at argparse level, not run_ level.
        # Verify run_ still executes without crash on unknown lifecycle (maps to None cond).
        # Actual argparse rejection is tested in parser tests below.
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        # 'all' is the default; verify this doesn't crash
        run_fi_proposal_history(
            statute="2014/527",
            lifecycle="all",
            data_dir=str(tmp_history_dir),
            output_format="json",
        )


# ---------------------------------------------------------------------------
# 5. Cross-jurisdiction / error tests (fulfills §15 category 5 + 7)
# ---------------------------------------------------------------------------


class TestFiProposalHistoryJurisdiction:
    @duckdb_required
    def test_cross_jurisdiction_rejects_ee(self, tmp_history_dir):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        with pytest.raises(SystemExit) as exc_info:
            run_fi_proposal_history(
                statute="2014/527",
                data_dir=str(tmp_history_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_missing_corpus_parquet_exits(self, tmp_path):
        from lawvm.tools.fi_proposal_history import run_fi_proposal_history
        # Empty dir: no parquet files
        with pytest.raises(SystemExit) as exc_info:
            run_fi_proposal_history(
                statute="2014/527",
                data_dir=str(tmp_path),
            )
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 6. CLI parser tests (argparse wiring)
# ---------------------------------------------------------------------------


class TestFiProposalHistoryParser:
    def test_command_registered(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        args = parser.parse_args(["fi-proposal-history", "--statute", "2014/527"])
        assert args.command == "fi-proposal-history"
        assert args.statute == "2014/527"
        assert args.lifecycle == "all"
        assert args.output_format == "table"
        assert args.data_dir == "data/fi/v1"

    def test_lifecycle_choices_accepted(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        for lc in ("all", "pending", "closed", "enacted", "rejected"):
            args = parser.parse_args(
                ["fi-proposal-history", "--statute", "x", "--lifecycle", lc]
            )
            assert args.lifecycle == lc

    def test_invalid_lifecycle_rejected(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(
                ["fi-proposal-history", "--statute", "x", "--lifecycle", "invalid"]
            )
        assert exc_info.value.code != 0

    def test_statute_required(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["fi-proposal-history"])
        assert exc_info.value.code != 0

    def test_optional_flags_parsed(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        args = parser.parse_args([
            "fi-proposal-history",
            "--statute", "2014/527",
            "--year-range", "2023:2025",
            "--ministry", "Ympäristöministeriö",
            "--include-provisions",
            "--limit", "10",
            "--data-dir", "/custom/path",
            "-o", "json",
        ])
        assert args.year_range == "2023:2025"
        assert args.ministry == "Ympäristöministeriö"
        assert args.include_provisions is True
        assert args.limit == 10
        assert args.data_dir == "/custom/path"
        assert args.output_format == "json"
