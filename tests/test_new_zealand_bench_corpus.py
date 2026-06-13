from __future__ import annotations

import csv
from pathlib import Path

import pytest

from lawvm.new_zealand.bench_corpus import (
    CORPUS_CSV_FIELDS,
    SMOKE_PINNED_WORK_IDS,
    NZBenchCorpusError,
    NZBenchCorpusRow,
    read_corpus_work_ids,
    scan_amendment_bearing_works,
    select_smoke_rows,
    write_corpus_csv,
)

_FARCHIVE = Path("data/nz_legislation.farchive")


def _row(work_id: str, *, families: dict[str, int]) -> NZBenchCorpusRow:
    n = sum(families.values())
    return NZBenchCorpusRow(
        work_id=work_id,
        work_type="act_public",
        year=2010,
        n_amendment_operations=n,
        n_history_witnesses=n,
        operation_families=families,
        latest_version_id=f"{work_id}_en_2010-01-01",
    )


def _pinned_rows() -> list[NZBenchCorpusRow]:
    # One row per pinned canary, each with at least one amendment op.
    return [_row(work_id, families={"repealed": 1}) for work_id in SMOKE_PINNED_WORK_IDS]


# --- CSV roundtrip + reader ------------------------------------------------


def test_write_then_read_roundtrips_work_ids_in_order(tmp_path: Path) -> None:
    rows = (
        _row("act_public_2010_1", families={"repealed": 2}),
        _row("act_public_2011_2", families={"inserted": 1}),
        _row("act_public_2012_3", families={"substituted": 3}),
    )
    path = tmp_path / "bench_corpus.csv"
    write_corpus_csv(path, rows)

    # Header schema is exactly the canonical field list.
    with open(path, newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert tuple(header) == CORPUS_CSV_FIELDS

    assert read_corpus_work_ids(path) == (
        "act_public_2010_1",
        "act_public_2011_2",
        "act_public_2012_3",
    )


def test_write_is_deterministic(tmp_path: Path) -> None:
    rows = (
        _row("act_public_2010_1", families={"repealed": 1, "inserted": 2}),
        _row("act_public_2011_2", families={"substituted": 1}),
    )
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    write_corpus_csv(first, rows)
    write_corpus_csv(second, rows)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    # Families are emitted sorted, deterministically.
    assert "inserted:2;repealed:1" in first.read_text(encoding="utf-8")


def test_reader_dedupes_preserving_first_seen_order(tmp_path: Path) -> None:
    path = tmp_path / "dupe.csv"
    path.write_text(
        "work_id,work_type\n"
        "act_public_2010_1,act_public\n"
        "act_public_2011_2,act_public\n"
        "act_public_2010_1,act_public\n",
        encoding="utf-8",
    )
    assert read_corpus_work_ids(path) == ("act_public_2010_1", "act_public_2011_2")


def test_reader_rejects_missing_work_id_column(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("statute_id,type\nfoo,act\n", encoding="utf-8")
    with pytest.raises(NZBenchCorpusError, match="no 'work_id' column"):
        read_corpus_work_ids(path)


def test_reader_rejects_empty_corpus(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("work_id,work_type\n", encoding="utf-8")
    with pytest.raises(NZBenchCorpusError, match="no work_id rows"):
        read_corpus_work_ids(path)


def test_reader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NZBenchCorpusError, match="not found"):
        read_corpus_work_ids(tmp_path / "does_not_exist.csv")


# --- smoke-slice curation (determinism + canary pinning + diversity) -------


def test_smoke_slice_always_includes_pinned_canaries() -> None:
    rows = tuple(_pinned_rows() + [_row(f"act_public_2020_{i}", families={"inserted": 1}) for i in range(40)])
    smoke = select_smoke_rows(rows, smoke_size=30)
    smoke_ids = {r.work_id for r in smoke}
    for pinned in SMOKE_PINNED_WORK_IDS:
        assert pinned in smoke_ids
    assert len(smoke) == 30


def test_smoke_slice_is_deterministic_and_sorted() -> None:
    rows = tuple(_pinned_rows() + [_row(f"act_public_2020_{i:02d}", families={"inserted": 1}) for i in range(40)])
    a = select_smoke_rows(rows, smoke_size=20)
    b = select_smoke_rows(rows, smoke_size=20)
    assert [r.work_id for r in a] == [r.work_id for r in b]
    # Emitted in work_id order.
    assert [r.work_id for r in a] == sorted(r.work_id for r in a)


def test_smoke_slice_prefers_family_diversity() -> None:
    # Pinned canaries cover only "repealed". Provide candidates introducing new
    # families plus many duplicates of an already-covered family; the diverse
    # ones must be picked first.
    diverse = [
        _row("act_public_2030_1", families={"inserted": 1}),
        _row("act_public_2030_2", families={"substituted": 1}),
        _row("act_public_2030_3", families={"amended": 1}),
        _row("act_public_2030_4", families={"definition repealed": 1}),
    ]
    redundant = [_row(f"act_public_2031_{i:02d}", families={"repealed": 1}) for i in range(20)]
    rows = tuple(_pinned_rows() + diverse + redundant)
    smoke = select_smoke_rows(rows, smoke_size=len(SMOKE_PINNED_WORK_IDS) + 4)
    smoke_ids = {r.work_id for r in smoke}
    for diverse_row in diverse:
        assert diverse_row.work_id in smoke_ids, diverse_row.work_id
    # No redundant-family row should crowd out a diverse one within budget.
    assert not (smoke_ids & {r.work_id for r in redundant})


def test_smoke_slice_refuses_when_canary_not_amendment_bearing() -> None:
    # Drop one canary from the scanned population -> loud refusal, not silent drop.
    rows = tuple(_pinned_rows()[:-1] + [_row("act_public_2020_1", families={"inserted": 1})])
    with pytest.raises(NZBenchCorpusError, match="pinned canaries are not amendment-bearing"):
        select_smoke_rows(rows, smoke_size=10)


# --- CLI wiring ------------------------------------------------------------


def test_build_corpus_cli_parse_defaults() -> None:
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args(["nz-corpus", "build-corpus"])
    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "build-corpus"
    assert args.db == "data/nz_legislation.farchive"
    assert args.out_dir == "data/nz"
    assert args.work_id_prefix == "act_public_"
    # Default tracks DEFAULT_SMOKE_SIZE (bumped to keep the text-substitution
    # canary pins additive rather than evicting prior diversity picks).
    from lawvm.new_zealand.bench_corpus import DEFAULT_SMOKE_SIZE

    assert args.smoke_size == DEFAULT_SMOKE_SIZE
    assert DEFAULT_SMOKE_SIZE == 33


def test_dry_run_corpus_cli_accepts_corpus_flag() -> None:
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args(
        ["nz-corpus", "dry-run-corpus", "--corpus", "data/nz/bench_corpus_smoke.csv"]
    )
    assert args.corpus == "data/nz/bench_corpus_smoke.csv"


def test_benchmark_cli_accepts_corpus_flag() -> None:
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args(["nz-corpus", "benchmark", "--corpus", "data/nz/bench_corpus.csv"])
    assert args.corpus == "data/nz/bench_corpus.csv"


def test_dry_run_corpus_main_reads_corpus_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # --corpus supplies work_ids to the report builder (no sampler).
    corpus = tmp_path / "smoke.csv"
    write_corpus_csv(
        corpus,
        (
            _row("act_public_2010_1", families={"repealed": 1}),
            _row("act_public_2011_2", families={"inserted": 1}),
        ),
    )

    import lawvm.new_zealand.dry_run_corpus as drc

    captured: dict[str, object] = {}

    def _fake_builder(db_path: Path, **kwargs: object) -> object:
        captured["work_ids"] = kwargs.get("work_ids")

        class _R:
            def to_jsonable(self, *, summary_only: bool = False) -> dict[str, object]:
                return {}

            def summary(self) -> dict[str, object]:
                return {
                    "scope": "complete_set",
                    "selection_context": {
                        "selected_work_count": 2,
                        "available_work_count": 2,
                        "max_works": None,
                        "truncated_by_max_works": False,
                    },
                    "dry_run_oracle_agreement_rate": None,
                    "works_attempted": 2,
                    "works_with_ready_preflight": 0,
                    "works_with_dry_run_proofs": 0,
                    "total_repeal_ops_dry_run": 0,
                    "dry_run_oracle_agreements": 0,
                    "dry_run_oracle_residuals": 0,
                    "neighbors_unchanged_all": True,
                    "repeal_witness_coverage": {"census_available": False},
                    "preflight_status_counts": {},
                    "oracle_match_family_counts": {},
                    "residual_oracle_match_family_counts": {},
                    "refusal_rule_counts": {},
                    "actual_replay_blocking_rule_id": "x",
                }

        return _R()

    monkeypatch.setattr(drc, "build_nz_dry_run_repeal_corpus_report", _fake_builder)

    import argparse

    args = argparse.Namespace(
        db=str(tmp_path / "nz.farchive"),
        work_id=[],
        corpus=str(corpus),
        max_works=None,
        work_id_prefix="",
        min_version_year=None,
        sample_strategy="head",
        scope="complete-set",
        summary_only=True,
        json=False,
    )

    drc.main(args)
    assert captured["work_ids"] == ("act_public_2010_1", "act_public_2011_2")


def test_bench_dash_j_nz_fails_loudly_without_running_finland(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: 'bench -j nz' must NOT silently run the Finland bench.
    import lawvm.tools.bench as fi_bench
    from lawvm.tools.cli import _main_impl

    def _explode(_args: object) -> None:  # pragma: no cover - must never run
        raise AssertionError("bench -j nz silently invoked the Finland bench")

    monkeypatch.setattr(fi_bench, "main", _explode)
    monkeypatch.setattr("sys.argv", ["lawvm", "bench", "-j", "nz"])

    with pytest.raises(SystemExit) as excinfo:
        _main_impl()
    assert excinfo.value.code == 2


# --- scan filtering against the real archive (gated) -----------------------


@pytest.mark.skipif(not _FARCHIVE.exists(), reason="NZ farchive not available")
def test_scan_keeps_only_amendment_bearing_works() -> None:
    # A narrow prefix keeps the scan fast. Every kept row must have >0 ops and
    # the stats must account for every scanned work (no silent drops).
    rows, stats = scan_amendment_bearing_works(_FARCHIVE, work_id_prefix="act_public_2009_")
    for row in rows:
        assert row.n_amendment_operations > 0
        assert row.work_id.startswith("act_public_2009_")
    # Scan order is deterministic (lexicographic by work_id).
    assert [r.work_id for r in rows] == sorted(r.work_id for r in rows)
    assert stats["kept"] == len(rows)
    # Every scanned work is either kept, zero-op, or parse-failed.
    assert stats["scanned"] == stats["kept"] + stats["zero_op"] + stats["parse_failed"]
    assert stats["scanned"] == stats["population"]
    # The known canary in this prefix carries amendments and must be kept.
    assert "act_public_2009_38" in {r.work_id for r in rows}
