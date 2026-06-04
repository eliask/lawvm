"""Tests for lawvm fi-proposals-competing command.

Per AGENTS.md §15 — all 7 categories:
  1. Synthetic filter test (competing HEs detected with correct columns)
  2. Real corpus regression (n/a — pure SQL query)
  3. Finding/observation test (stderr message, no-competition message)
  4. Negative test (statute with 0 or 1 HE → no competition)
  5. Strict-mode test (n/a — no strict-mode path in this command)
  6. No-leak test (n/a — no internal markers)
  7. Cross-jurisdiction error (fi-only command rejects 'ee')
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

try:
    import duckdb as _duckdb_mod  # noqa: F401
    _HAS_DUCKDB = True
except ImportError:
    _HAS_DUCKDB = False

duckdb_required = pytest.mark.skipif(not _HAS_DUCKDB, reason="duckdb not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmp_competing_dir(tmp_path_factory) -> Path:
    """Create synthetic fi_he_corpus.parquet + fi_he_law_refs.parquet fixtures."""
    if not _HAS_DUCKDB:
        pytest.skip("duckdb not installed")

    import duckdb

    d = tmp_path_factory.mktemp("competing")

    # fi_he_corpus: 5 HEs
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_he_corpus AS SELECT * FROM (VALUES
          ('HE 176/2025 vp', 2025, 176, 'fi.vm', 'Valtiovarainministeriö',
           'Verotusmenettelylaki muutos A', '2025-11-27', 'pending'),
          ('HE 157/2025 vp', 2025, 157, 'fi.vm', 'Valtiovarainministeriö',
           'Verotusmenettelylaki muutos B', '2025-10-01', 'pending'),
          ('HE 141/2025 vp', 2025, 141, 'fi.vm', 'Valtiovarainministeriö',
           'Verotusmenettelylaki muutos C', '2025-09-01', 'pending'),
          ('HE 100/2024 vp', 2024, 100, 'fi.vm', 'Valtiovarainministeriö',
           'Verotusmenettelylaki vanha muutos', '2024-05-01', 'enacted'),
          ('HE 191/2025 vp', 2025, 191, 'fi.okm', 'Opetus- ja kulttuuriministeriö',
           'Ammattikorkeakoululaki muutos', '2025-12-01', 'pending')
        ) t(he_id, he_year, he_number, ministry_canonical_id, ministry_show_as,
             title, date_issued, finlex_state)
    """)
    con.execute(f"COPY fi_he_corpus TO '{d}/fi_he_corpus.parquet' (FORMAT PARQUET)")
    con.close()

    # fi_he_law_refs: competing amendments to 1995/1558 (verotusmenettelylaki)
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE fi_he_law_refs AS SELECT * FROM (VALUES
          -- Three pending HEs amend 1995/1558
          ('HE 176/2025 vp', '1995/1558', '1995/1558/31',   'enacted_statute'),
          ('HE 176/2025 vp', '1995/1558', '1995/1558/32',   'enacted_statute'),
          ('HE 157/2025 vp', '1995/1558', '1995/1558/31',   'enacted_statute'),
          ('HE 157/2025 vp', '1995/1558', '1995/1558/40',   'enacted_statute'),
          ('HE 141/2025 vp', '1995/1558', '1995/1558/55',   'enacted_statute'),
          -- One enacted HE (should be excluded in pending window)
          ('HE 100/2024 vp', '1995/1558', '1995/1558/10',   'enacted_statute'),
          -- One pending HE for a different statute (AMK)
          ('HE 191/2025 vp', '572/2014',  '572/2014/5',     'enacted_statute')
        ) t(he_id, target_statute_id, target_provision_ref_str, ref_kind)
    """)
    con.execute(f"COPY fi_he_law_refs TO '{d}/fi_he_law_refs.parquet' (FORMAT PARQUET)")
    con.close()

    return d


# ---------------------------------------------------------------------------
# 1. Synthetic filter tests
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingSyntheticFilter:
    @duckdb_required
    def test_finds_competing_pending_hes(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        # Three pending HEs reference 1995/1558
        assert len(out) == 3
        he_ids = {r["he_id"] for r in out}
        assert "HE 176/2025 vp" in he_ids
        assert "HE 157/2025 vp" in he_ids
        assert "HE 141/2025 vp" in he_ids
        # HE 100/2024 (enacted) excluded
        assert "HE 100/2024 vp" not in he_ids

    @duckdb_required
    def test_all_window_includes_enacted(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="all",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        # All 4 HEs (including enacted) should appear
        assert len(out) == 4
        he_ids = {r["he_id"] for r in out}
        assert "HE 100/2024 vp" in he_ids

    @duckdb_required
    def test_provision_overlap_detects_shared_provisions(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            provision_overlap=True,
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 3
        # All rows should have conflict_provisions column
        assert all("conflict_provisions" in r for r in out)
        # HE 176 and HE 157 both touch 1995/1558/31 — both should show conflict
        he176 = next(r for r in out if r["he_id"] == "HE 176/2025 vp")
        he157 = next(r for r in out if r["he_id"] == "HE 157/2025 vp")
        # 31 is contested between 176 and 157
        assert "1995/1558/31" in he176["conflict_provisions"]
        assert "1995/1558/31" in he157["conflict_provisions"]

    @duckdb_required
    def test_provision_overlap_noncontested_is_empty(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            provision_overlap=True,
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        # HE 141 only touches 55, which no other HE touches
        he141 = next(r for r in out if r["he_id"] == "HE 141/2025 vp")
        assert he141["conflict_provisions"] == ""

    @duckdb_required
    def test_columns_present(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert len(out) >= 1
        row = out[0]
        assert "he_id" in row
        assert "he_year" in row
        assert "ministry_show_as" in row
        assert "finlex_state" in row
        assert "provisions_touched" in row


# ---------------------------------------------------------------------------
# 2. Output format tests
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingOutputFormats:
    @duckdb_required
    def test_output_format_table(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="table",
        )
        out = capsys.readouterr().out
        assert "he_id" in out
        assert "HE 176/2025 vp" in out

    @duckdb_required
    def test_output_format_jsonl(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="jsonl",
        )
        out = capsys.readouterr().out
        lines = [ln for ln in out.strip().split("\n") if ln]
        assert len(lines) == 3
        obj = json.loads(lines[0])
        assert "he_id" in obj

    @duckdb_required
    def test_output_format_csv(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="csv",
        )
        out = capsys.readouterr().out
        reader = csv.DictReader(io.StringIO(out))
        rows = list(reader)
        assert len(rows) == 3
        assert "he_id" in reader.fieldnames


# ---------------------------------------------------------------------------
# 3. Finding/observation: stderr messages
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingStderrMessage:
    @duckdb_required
    def test_stderr_message_fires(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="1995/1558",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        err = capsys.readouterr().err
        assert "Using projections from" in err

    @duckdb_required
    def test_no_competition_message_on_singleton(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        # 572/2014 only has one pending HE (HE 191)
        run_fi_proposals_competing(
            statute="572/2014",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        err = capsys.readouterr().err
        assert "no competition" in err.lower() or "only one HE" in err


# ---------------------------------------------------------------------------
# 4. Negative test: no competing HEs
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingNegative:
    @duckdb_required
    def test_unknown_statute_empty_result(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        run_fi_proposals_competing(
            statute="9999/0001",
            lifecycle_window="all",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert out == []

    @duckdb_required
    def test_enacted_only_excluded_when_pending_window(self, tmp_competing_dir, capsys):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        # 1995/1558 has one enacted HE (100/2024). With pending window, should return 3 not 4.
        run_fi_proposals_competing(
            statute="1995/1558",
            lifecycle_window="pending",
            data_dir=str(tmp_competing_dir),
            output_format="json",
        )
        out = json.loads(capsys.readouterr().out)
        assert all(r["finlex_state"] == "pending" for r in out)


# ---------------------------------------------------------------------------
# 5. Cross-jurisdiction / error tests
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingJurisdiction:
    @duckdb_required
    def test_cross_jurisdiction_rejects_ee(self, tmp_competing_dir):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        with pytest.raises(SystemExit) as exc_info:
            run_fi_proposals_competing(
                statute="1995/1558",
                data_dir=str(tmp_competing_dir),
                jurisdiction="ee",
            )
        assert exc_info.value.code == 1

    @duckdb_required
    def test_missing_corpus_parquet_exits(self, tmp_path):
        from lawvm.tools.fi_proposals_competing import run_fi_proposals_competing
        with pytest.raises(SystemExit) as exc_info:
            run_fi_proposals_competing(
                statute="1995/1558",
                data_dir=str(tmp_path),
            )
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 6. CLI parser tests
# ---------------------------------------------------------------------------


class TestFiProposalsCompetingParser:
    def test_command_registered(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        args = parser.parse_args(["fi-proposals-competing", "--statute", "1995/1558"])
        assert args.command == "fi-proposals-competing"
        assert args.statute == "1995/1558"
        assert args.lifecycle_window == "pending"
        assert args.provision_overlap is False
        assert args.data_dir == "data/fi/v1"
        assert args.output_format == "table"

    def test_lifecycle_window_choices_accepted(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        for window in ("pending", "active-this-year", "all"):
            args = parser.parse_args(
                ["fi-proposals-competing", "--statute", "x", "--lifecycle-window", window]
            )
            assert args.lifecycle_window == window

    def test_invalid_lifecycle_window_rejected(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(
                ["fi-proposals-competing", "--statute", "x", "--lifecycle-window", "bogus"]
            )
        assert exc_info.value.code != 0

    def test_statute_required(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["fi-proposals-competing"])
        assert exc_info.value.code != 0

    def test_optional_flags_parsed(self):
        from lawvm.tools import cli
        parser = cli._build_parser()
        args = parser.parse_args([
            "fi-proposals-competing",
            "--statute", "1995/1558",
            "--as-of", "2025-01-01",
            "--lifecycle-window", "all",
            "--provision-overlap",
            "--limit", "5",
            "--data-dir", "/custom",
            "-o", "jsonl",
        ])
        assert args.as_of == "2025-01-01"
        assert args.lifecycle_window == "all"
        assert args.provision_overlap is True
        assert args.limit == 5
        assert args.data_dir == "/custom"
        assert args.output_format == "jsonl"
