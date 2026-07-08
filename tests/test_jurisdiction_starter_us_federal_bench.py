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

import json
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
    WindowStatus,
    derive_window_law_locators,
    evaluate_window,
    load_corpus,
    run_bench,
    run_bench_parallel,
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
    assert result.window_status == "evaluated"
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
    assert result.window_status == "skipped"
    assert result.skip_rule_id == US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID


def test_missing_edition_is_a_typed_skip() -> None:
    archive = _FakeArchive({"us://usc/2023/title99.htm": BEFORE_HTM})
    result = evaluate_window(archive, _synthetic_window())
    assert result.window_status == "skipped"
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
    assert win["window_status"] == "evaluated"
    assert "coverage_fraction" in win


def test_window_result_skip_jsonable_omits_evaluation_scalars() -> None:
    result = WindowResult(
        window=_synthetic_window(),
        window_status=WindowStatus.SKIPPED,
        skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
    )
    payload = result.to_jsonable()
    assert payload["window_status"] == "skipped"
    assert payload["skip_rule_id"] == US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID
    assert "coverage_fraction" not in payload


def test_empty_bench_report_aggregate_is_none_coverage_not_a_crash() -> None:
    report = BenchReport(corpus_path="empty")
    agg = report.aggregate()
    assert agg["coverage_fraction"] is None
    assert agg["oracle_changed_section_total"] == 0


# ---------------------------------------------------------------------------
# Source-present aggregate (coverage_source_present)
# ---------------------------------------------------------------------------


def test_source_present_aggregate_present_and_correctly_labelled() -> None:
    """Both coverage aggregates are present, labelled, and self-consistent."""
    report = run_bench(_synthetic_archive(), [_synthetic_window()], corpus_path="fixture")
    agg = report.aggregate()
    # Both aggregate keys must be present.
    assert "coverage_all" in agg, "coverage_all missing from aggregate"
    assert "coverage_source_present" in agg, "coverage_source_present missing from aggregate"
    # coverage_all must carry numerator / denominator / fraction + a note.
    ca = agg["coverage_all"]
    assert {"numerator", "denominator", "fraction", "denominator_note"} <= set(ca)
    # coverage_source_present must carry its own fields including exclusion counts.
    sp = agg["coverage_source_present"]
    assert {
        "numerator", "denominator", "fraction",
        "excluded_oracle_suspect", "excluded_sunset_reversion",
        "excluded_deferred_op",
        "excluded_total", "denominator_note",
    } <= set(sp)
    # The same numerator (agreements) must appear in both aggregates.
    assert ca["numerator"] == agg["agreements_total"]
    assert sp["numerator"] == agg["agreements_total"]
    # coverage_source_present denominator <= coverage_all denominator (we exclude,
    # never add sections).
    assert sp["denominator"] <= ca["denominator"]
    # excluded_total = oracle_suspect + sunset_reversion + deferred_op.
    assert sp["excluded_total"] == (
        sp["excluded_oracle_suspect"]
        + sp["excluded_sunset_reversion"]
        + sp["excluded_deferred_op"]
    )
    # coverage_source_present >= coverage_all: subtracting a non-negative count of
    # structurally-unwitnessable deltas from the denominator cannot increase the denominator,
    # so the fraction can only stay the same or increase.
    if ca["fraction"] is not None and sp["fraction"] is not None:
        assert sp["fraction"] >= ca["fraction"] - 1e-9, (
            f"coverage_source_present ({sp['fraction']:.4f}) must be >= "
            f"coverage_all ({ca['fraction']:.4f})"
        )


def test_source_present_aggregate_no_structural_exclusion_when_no_pathology() -> None:
    """When no structurally-unwitnessable sections exist both fractions coincide.

    The synthetic fixture has 2 oracle-changed sections: section 10 (agrees) and
    section 30 (missing_source gap, no oracle pathology, no sunset).  The excluded
    count must be 0 and the two fractions must be equal.
    """
    report = run_bench(_synthetic_archive(), [_synthetic_window()], corpus_path="fixture")
    agg = report.aggregate()
    sp = agg["coverage_source_present"]
    ca = agg["coverage_all"]
    # The synthetic fixture has no oracle_suspect, sunset_reversion, or deferred_op sections.
    assert sp["excluded_oracle_suspect"] == 0
    assert sp["excluded_sunset_reversion"] == 0
    assert sp["excluded_deferred_op"] == 0
    assert sp["excluded_total"] == 0
    # When nothing is excluded both denominators (and fractions) must be equal.
    assert sp["denominator"] == ca["denominator"]
    assert sp["fraction"] == pytest.approx(ca["fraction"])


def test_source_present_aggregate_excludes_deferred_op_but_not_billable_gaps() -> None:
    """F3 ``deferred_op`` is structurally unwitnessable; real gaps stay scored."""
    window = _synthetic_window()
    report = BenchReport(corpus_path="synthetic-deferred")
    report.results.append(
        WindowResult(
            window=window,
            window_status=WindowStatus.EVALUATED,
            oracle_changed=5,
            agreements=1,
            lawvm_wrong=1,
            oracle_suspect=1,
            missing_source=1,
            sunset_reversion=0,
            deferred_op=1,
            coverage_fraction=0.2,
        )
    )

    agg = report.aggregate()
    sp = agg["coverage_source_present"]

    assert agg["coverage_all"]["denominator"] == 5
    assert sp["excluded_oracle_suspect"] == 1
    assert sp["excluded_sunset_reversion"] == 0
    assert sp["excluded_deferred_op"] == 1
    assert sp["excluded_total"] == 2
    assert sp["denominator"] == 3
    assert sp["fraction"] == pytest.approx(1 / 3)
    assert agg["disposition_breakdown"]["lawvm_wrong"] == 1
    assert agg["disposition_breakdown"]["missing_source"] == 1


def test_aggregate_shape_includes_coverage_source_present_in_json() -> None:
    """``to_jsonable()`` serialises both aggregate keys so downstream consumers see them."""
    report = run_bench(_synthetic_archive(), [_synthetic_window()], corpus_path="fixture")
    payload = report.to_jsonable()
    agg = payload["aggregate"]
    assert "coverage_all" in agg
    assert "coverage_source_present" in agg


# ---------------------------------------------------------------------------
# Title-class aggregate (positive-law vs non-positive)
# ---------------------------------------------------------------------------


def test_positive_law_title_set_is_the_canonical_olrc_enumeration() -> None:
    """Guard the positive-law title set against accidental edits.

    27 titles are positive law; representative members/non-members are pinned so a
    silent change to the set fails loudly rather than silently re-bucketing coverage.
    """
    from lawvm.us_federal.bench import POSITIVE_LAW_TITLES

    assert len(POSITIVE_LAW_TITLES) == 27
    # Positive law (title is the law): Bankruptcy(11), Crimes(18), Armed Forces(10), Patents(35).
    assert {10, 11, 18, 35} <= POSITIVE_LAW_TITLES
    # Non-positive (editorial compilations): Agriculture(7), Public Health(42), IRC(26), Commerce(15).
    assert not ({7, 42, 26, 15} & POSITIVE_LAW_TITLES)


def test_coverage_by_title_class_partitions_the_denominator_exactly() -> None:
    """The two class buckets partition the evaluated population with no overlap or loss."""
    report = run_bench(_synthetic_archive(), [_synthetic_window()], corpus_path="fixture")
    agg = report.aggregate()
    tc = agg["coverage_by_title_class"]
    assert set(tc) == {"positive_law", "non_positive"}
    pos, non = tc["positive_law"], tc["non_positive"]
    for b in (pos, non):
        assert {"numerator", "denominator", "fraction", "titles"} <= set(b)
    # Partition completeness: the two classes sum to the whole evaluated aggregate.
    assert pos["numerator"] + non["numerator"] == agg["agreements_total"]
    assert pos["denominator"] + non["denominator"] == agg["oracle_changed_section_total"]
    # The synthetic fixture is title 99 (non-positive); positive-law class is empty.
    assert pos["denominator"] == 0
    assert pos["fraction"] is None
    assert non["denominator"] == agg["oracle_changed_section_total"]
    assert 99 in non["titles"]


# ---------------------------------------------------------------------------
# Unified bench contract render (default headline)
# ---------------------------------------------------------------------------


def test_unified_render_summary_is_the_default_us_headline() -> None:
    """``main`` renders the shared unified summary; assert its meaningful shape.

    The unified headline is the worst-of-axes summary over the contract
    ``BenchUnitResult``s — for US that is the structural (verified-agreement)
    axis only (no text axis). Asserts the status partition counts, the
    residue-reconciliation honesty line, and that the per-window structural
    error reconciles with the typed disposition residue (no silent error).
    """
    from lawvm.core.bench_aggregate import compute_distribution, render_summary
    from lawvm.core.bench_contract import BenchStatus, check_residue_reconciliation
    from lawvm.us_federal.bench import us_bench_unit_result

    windows = [_synthetic_window(), _synthetic_window(include=False)]
    report = run_bench(_synthetic_archive(), windows, corpus_path="fixture")

    unit_results = [us_bench_unit_result(r) for r in report.results]
    # One window evaluates (1 agreement / 2 oracle-changed = structural_err 0.5);
    # the excluded window is a typed non-scored skip, not a failure.
    scored = [u for u in unit_results if u.bench_unit_status is BenchStatus.SCORED]
    assert len(scored) == 1
    assert scored[0].structural_err == pytest.approx(0.5)
    assert scored[0].text_err is None  # US has no text axis
    for u in unit_results:
        check_residue_reconciliation(u)

    dist = compute_distribution(unit_results)
    assert dist.n == 1  # one scored unit
    assert dist.errors == 1  # the excluded window has no headline accuracy

    lines = render_summary(unit_results, "fixture", jurisdiction="us")
    text = "\n".join(lines)
    assert "=== UNIFIED BENCH SUMMARY" in text
    assert "jurisdiction=us" in text
    # worst-of headline error = 1 - mean accuracy = 50.00% on this fixture.
    assert "Mean error : 50.00%" in text
    assert "1 scored" in text and "excluded(non-scored): 1" in text
    # Honesty property surfaced: structural error explained by typed residue.
    assert "Residue reconciliation: OK" in text
    # No spurious text-axis line for an axis the jurisdiction does not attempt.
    assert "text" not in text.lower()


# ---------------------------------------------------------------------------
# Real committed corpus over the canonical archive (archive-gated, no network)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


# Worker count for the parallelized real-corpus bench. Bounded so the test stays well
# under the WSL2 memory ceiling (each worker holds an open farchive handle).
_BENCH_TEST_WORKERS = min(8, (os.cpu_count() or 2))

# Opt-in: run the WHOLE corpus through the SERIAL runner in one process (historical
# path). The default below runs the full corpus through the byte-identical parallel
# runner so the shard stays fast.
_FULL_CORPUS_SERIAL = os.environ.get("LAWVM_US_FULL_CORPUS_TEST") == "1"


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_corpus_runs_and_produces_a_witness_anchored_aggregate() -> None:
    """Full real corpus witness-anchored aggregate — parallelized.

    The POINT is the aggregate over the *whole* real corpus (coverage fraction, the
    agreements floor, the typed disposition partitions), so coverage is NOT sampled:
    every included window is evaluated. Only the EXECUTION MODEL changes — the windows
    are sharded across worker processes via :func:`run_bench_parallel`, whose
    determinism contract (pinned byte-identical by
    ``test_parallel_bench_aggregate_is_byte_identical_to_serial``) makes the aggregate
    identical to the serial runner. The corpus grew to 175+ windows (the title-42
    ACA-era window alone composes 800+ Public Laws); serial evaluation was ~15-20 min.
    Set ``LAWVM_US_FULL_CORPUS_TEST=1`` to run the full sweep serially instead.
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    windows = load_corpus(REPO_ROOT / DEFAULT_CORPUS_PATH)
    if _FULL_CORPUS_SERIAL:
        archive = open_us_federal_farchive(readonly=True)
        try:
            report = run_bench(archive, windows, corpus_path=str(DEFAULT_CORPUS_PATH))
        finally:
            archive.close()
    else:
        report = run_bench_parallel(
            windows,
            workers=_BENCH_TEST_WORKERS,
            corpus_path=str(DEFAULT_CORPUS_PATH),
        )

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
    # (observed 23 -> 13). The relative-prose / leading-subsection target threading
    # (nested instruction lists, "of such title") then lifted coverage to 24 by
    # claiming sections the lowering previously never targeted. Floor guards both
    # classes; it can only rise as lowering coverage improves, never regress.
    assert agg["agreements_total"] >= 24
    # "Covered" is strictly the agreement partition: the typed residual partitions
    # are reported separately and never folded into coverage.
    breakdown = agg["disposition_breakdown"]
    assert breakdown["agreement"] == agg["agreements_total"]
    assert {
        "lawvm_wrong",
        "oracle_suspect",
        "missing_source",
        "sunset_reversion",
        "deferred_op",
    } <= set(breakdown)
    # The gate stays shut: the report kind never authorizes replay.
    assert report.to_jsonable()["replay_authorized"] is False
    # The parallel runner strips the heavy per-window report at the process
    # boundary (report=None) to bound memory; the serial path keeps it. When the
    # per-window report is present, assert the gate is shut on each one too — the
    # parallel path's byte-identical-aggregate contract (pinned by
    # test_parallel_bench_aggregate_is_byte_identical_to_serial) carries the same
    # invariant the serial per-window check verifies here.
    for result in report.evaluated():
        if result.report is not None:
            assert result.report.replay_authorized is False


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_parallel_bench_aggregate_is_byte_identical_to_serial() -> None:
    """The parallel runner reproduces the serial aggregate exactly.

    Determinism contract: sharding windows across worker processes (each with its
    own read-only farchive handle) and reassembling in corpus order yields a
    report whose ``to_jsonable()`` view is byte-identical to the serial runner's
    on the same corpus — same per-window results, same disposition breakdown,
    same agreement count.

    Runs on a small sub-corpus (the always-present Title 11 windows plus one
    excluded window if available) so the test stays fast while still exercising
    multi-shard reassembly and the include=false skip path.
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    all_windows = load_corpus(REPO_ROOT / DEFAULT_CORPUS_PATH)
    # Keep it small but multi-window so reassembly across shards is exercised.
    title11 = [w for w in all_windows if w.title == 11][:4]
    excluded = [w for w in all_windows if not w.include][:1]
    sub_corpus = title11 + excluded
    assert len(sub_corpus) >= 2, "need >=2 windows to exercise sharding"

    archive = open_us_federal_farchive(readonly=True)
    try:
        serial = run_bench(archive, sub_corpus, corpus_path="sub")
    finally:
        archive.close()

    parallel = run_bench_parallel(sub_corpus, workers=2, corpus_path="sub")

    # Byte-identical aggregate: identical JSON serialization of the whole report
    # (which excludes the heavy per-window report object on both paths).
    serial_json = json.dumps(serial.to_jsonable(), sort_keys=True)
    parallel_json = json.dumps(parallel.to_jsonable(), sort_keys=True)
    assert parallel_json == serial_json

    # And explicitly on the headline scalars the bench reports.
    sagg = serial.aggregate()
    pagg = parallel.aggregate()
    assert pagg["agreements_total"] == sagg["agreements_total"]
    assert pagg["disposition_breakdown"] == sagg["disposition_breakdown"]
    assert pagg["windows_evaluated"] == sagg["windows_evaluated"]
    assert pagg["windows_skipped"] == sagg["windows_skipped"]

    # Per-window order + agreement counts are stable (corpus order, not completion).
    assert [r.window.key for r in parallel.results] == [
        r.window.key for r in serial.results
    ]
    assert [r.agreements for r in parallel.results] == [
        r.agreements for r in serial.results
    ]
