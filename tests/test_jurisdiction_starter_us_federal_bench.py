"""U.S. federal dry-run bench harness over a committed fixture corpus.

No network. Two layers, mirroring the dry-run test's discipline:

1. A tiny committed FIXTURE corpus (Title 99 synthetic, one window) driven through
   a fake in-memory archive that serves the committed synthetic editions and the
   synthetic strike-insert Public Law. This exercises the whole harness:
   witness-delta window-law derivation, the per-window evaluation, the aggregate
   coverage + typed disposition breakdown, the empty-delta + missing-edition typed
   skips, and the stable JSON report shape — entirely offline.

2. The real committed bench corpus, run from the canonical archive when present
   (skipped otherwise). This pins the CURRENT witness-anchored US eval state shape
   without asserting brittle exact numbers (the kernel is actively improving).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.us_federal.bench import (
    DEFAULT_CORPUS_PATH,
    US_BENCH_WINDOW_EDITION_MISSING_RULE_ID,
    US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
    BenchReport,
    BenchWindow,
    WindowResult,
    derive_window_law_locators,
    evaluate_window,
    load_corpus,
    run_bench,
)
from lawvm.us_federal.sources import plaw_locator

FIXTURES = Path(__file__).parent / "fixtures" / "us_federal"
BEFORE_HTM = (FIXTURES / "usc-dryrun-before.htm").read_bytes()
AFTER_HTM = (FIXTURES / "usc-dryrun-after.htm").read_bytes()
PLAW_STRIKE_INSERT = (FIXTURES / "plaw-dryrun-strike-insert.xml").read_bytes()

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeArchive:
    """In-memory archive serving the committed Title 99 synthetic window."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = dict(blobs)

    def get(self, locator: str) -> bytes | None:
        return self._blobs.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        return list(self._blobs)


def _synthetic_archive() -> _FakeArchive:
    return _FakeArchive(
        {
            "us://usc/2023/title99.htm": BEFORE_HTM,
            "us://usc/2024/title99.htm": AFTER_HTM,
            plaw_locator(99, 2): PLAW_STRIKE_INSERT,
        }
    )


def _synthetic_window(*, include: bool = True) -> BenchWindow:
    return BenchWindow(
        title=99,
        before_year=2023,
        after_year=2024,
        include=include,
        window_law_count=1,
        prior_edition_years=(),
        note="synthetic",
    )


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def test_load_corpus_parses_committed_corpus_and_marks_includes() -> None:
    windows = load_corpus(REPO_ROOT / DEFAULT_CORPUS_PATH)
    assert windows, "the committed corpus must be non-empty"
    # Every row is typed; both included and excluded (empty-delta) windows exist.
    assert any(w.include for w in windows)
    assert any(not w.include for w in windows)
    # The Title 11 2018->2020 window is the canonical multi-law proof window.
    keys = {w.key for w in windows}
    assert "title11:2018->2020" in keys


def test_load_corpus_parses_prior_edition_years() -> None:
    windows = {w.key: w for w in load_corpus(REPO_ROOT / DEFAULT_CORPUS_PATH)}
    # The 2023->2024 Title 11 row carries prior editions for the F2 sunset channel.
    w = windows["title11:2023->2024"]
    assert w.prior_edition_years == (2018, 2022)


# ---------------------------------------------------------------------------
# Witness-delta window-law derivation
# ---------------------------------------------------------------------------


def test_window_law_derivation_is_the_witness_delta_not_a_curated_list() -> None:
    archive = _synthetic_archive()
    locators = derive_window_law_locators(
        archive, title=99, before_year=2023, after_year=2024
    )
    # The after edition newly credits PL 99-2 (the before edition credits only
    # PL 99-1) — a fact of the two editions, derived, never hand-listed.
    assert locators == {"PL 99-2": plaw_locator(99, 2)}


def test_window_law_derivation_returns_none_for_a_missing_edition() -> None:
    archive = _FakeArchive({"us://usc/2023/title99.htm": BEFORE_HTM})
    assert (
        derive_window_law_locators(archive, title=99, before_year=2023, after_year=2024)
        is None
    )


# ---------------------------------------------------------------------------
# Per-window evaluation + aggregation
# ---------------------------------------------------------------------------


def test_evaluate_window_produces_the_witness_anchored_row() -> None:
    result = evaluate_window(_synthetic_archive(), _synthetic_window())
    assert result.status == "evaluated"
    assert result.derived_window_laws == ("PL 99-2",)
    # The synthetic window: 2 oracle-changed sections (§10 amended, §30 after-only);
    # §10 materializes in agreement; §30 is the missing-source gap.
    assert result.oracle_changed == 2
    assert result.agreements == 1
    assert result.coverage_fraction == pytest.approx(0.5)
    assert result.missing_source == 1
    # The dry-run gate stays closed throughout.
    assert result.report is not None
    assert result.report.replay_authorized is False


def test_empty_witness_delta_is_a_typed_skip_not_a_zero_evaluation() -> None:
    # Same edition on both sides => the after edition credits no NEW public law.
    archive = _FakeArchive(
        {
            "us://usc/2023/title99.htm": BEFORE_HTM,
            "us://usc/2024/title99.htm": BEFORE_HTM,
        }
    )
    result = evaluate_window(archive, _synthetic_window())
    assert result.status == "skipped"
    assert result.skip_rule_id == US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID


def test_missing_edition_is_a_typed_skip() -> None:
    archive = _FakeArchive({"us://usc/2023/title99.htm": BEFORE_HTM})
    result = evaluate_window(archive, _synthetic_window())
    assert result.status == "skipped"
    assert result.skip_rule_id == US_BENCH_WINDOW_EDITION_MISSING_RULE_ID


def test_run_bench_aggregates_evaluated_and_records_excluded_windows() -> None:
    windows = [_synthetic_window(), _synthetic_window(include=False)]
    report = run_bench(_synthetic_archive(), windows, corpus_path="fixture")
    assert len(report.evaluated()) == 1
    assert len(report.skipped()) == 1
    agg = report.aggregate()
    # Witness-anchored aggregate: 1 agreement / 2 oracle-changed sections.
    assert agg["oracle_changed_section_total"] == 2
    assert agg["agreements_total"] == 1
    assert agg["coverage_fraction"] == pytest.approx(0.5)
    # "Covered" never folds in the typed residual partitions.
    breakdown = agg["disposition_breakdown"]
    assert breakdown["agreement"] == 1
    assert breakdown["missing_source"] == 1


def test_report_shape_is_stable_and_never_authorizes_replay() -> None:
    report = run_bench(_synthetic_archive(), [_synthetic_window()], corpus_path="fixture")
    payload = report.to_jsonable()
    assert payload["jurisdiction"] == "us_federal"
    assert payload["report_kind"] == "dry_run_bench"
    assert payload["replay_authorized"] is False
    # Stable top-level keys for downstream consumers.
    assert set(payload) >= {
        "jurisdiction",
        "report_kind",
        "truth_claim",
        "replay_authorized",
        "corpus",
        "aggregate",
        "windows",
    }
    agg = payload["aggregate"]
    assert set(agg) >= {
        "windows_evaluated",
        "windows_skipped",
        "oracle_changed_section_total",
        "agreements_total",
        "coverage_fraction",
        "disposition_breakdown",
        "refusals_total",
    }
    # Each window row is self-describing.
    win = payload["windows"][0]
    assert win["window"] == "title99:2023->2024"
    assert win["status"] == "evaluated"
    assert "coverage_fraction" in win


def test_window_result_skip_jsonable_omits_evaluation_scalars() -> None:
    result = WindowResult(
        window=_synthetic_window(),
        status="skipped",
        skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
    )
    payload = result.to_jsonable()
    assert payload["status"] == "skipped"
    assert payload["skip_rule_id"] == US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID
    assert "coverage_fraction" not in payload


def test_empty_bench_report_aggregate_is_none_coverage_not_a_crash() -> None:
    report = BenchReport(corpus_path="empty")
    agg = report.aggregate()
    assert agg["coverage_fraction"] is None
    assert agg["oracle_changed_section_total"] == 0


# ---------------------------------------------------------------------------
# Real committed corpus over the canonical archive (archive-gated, no network)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_corpus_runs_and_produces_a_witness_anchored_aggregate() -> None:
    from lawvm.us_federal.sources import open_us_federal_farchive

    windows = load_corpus(REPO_ROOT / DEFAULT_CORPUS_PATH)
    archive = open_us_federal_farchive(readonly=True)
    try:
        report = run_bench(archive, windows, corpus_path=str(DEFAULT_CORPUS_PATH))
    finally:
        archive.close()

    agg = report.aggregate()
    # At least one window evaluates (the Title 11 windows are always present).
    assert agg["windows_evaluated"] >= 1
    # The denominator is the sum of oracle-changed sections — a fact of the editions.
    assert agg["oracle_changed_section_total"] > 0
    # Witness-anchored coverage is a real fraction in [0, 1]; agreements never
    # exceed the oracle-changed denominator (monotone north-star).
    assert 0.0 <= agg["coverage_fraction"] <= 1.0
    assert agg["agreements_total"] <= agg["oracle_changed_section_total"]
    # Regression floor: a sub-section-targeted TEXT_REPLACE whose node the split
    # cannot locate must fall back to an unambiguous section-level string replace,
    # not emit empty materialization. Without the fallback the aggregate collapses
    # (observed 23 -> 13). Floor guards that class; it can only rise as lowering
    # coverage improves.
    assert agg["agreements_total"] >= 20
    # "Covered" is strictly the agreement partition: the typed residual partitions
    # are reported separately and never folded into coverage.
    breakdown = agg["disposition_breakdown"]
    assert breakdown["agreement"] == agg["agreements_total"]
    assert {"lawvm_wrong", "oracle_suspect", "missing_source", "sunset_reversion"} <= set(
        breakdown
    )
    # The gate stays shut for every evaluated window.
    for result in report.evaluated():
        assert result.report is not None
        assert result.report.replay_authorized is False
