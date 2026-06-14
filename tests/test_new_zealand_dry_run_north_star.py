from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.new_zealand.dry_run_north_star import (
    NZDryRunNorthStarReport,
    NZWorkNorthStarCensus,
    build_nz_dry_run_north_star_report,
)


def _census(
    work_id: str,
    *,
    history: dict[str, int],
    agreeing: dict[str, tuple[str, ...]] | None = None,
) -> NZWorkNorthStarCensus:
    return NZWorkNorthStarCensus(
        work_id=work_id,
        history_family_counts=dict(history),
        agreeing_witness_row_ids={"repeal": (), "text_replace": (), **(agreeing or {})},
    )


def test_denominator_is_history_witness_count_partitioned_into_pinned_buckets() -> None:
    # A single work whose history notes span every bucket. The denominator is the
    # ground-truth witness count, NOT any candidate-derived count.
    work = _census(
        "act_public_2010_1",
        history={
            "repealed": 10,
            "amended": 20,
            "inserted": 5,
            "added": 3,
            "replaced": 4,
            "substituted": 2,
            "brought into force": 1,
            "editorial change": 6,
            "expired": 1,
            "__missing__": 2,
            "__unclassified__": 1,
        },
        agreeing={"repeal": ("nz-opw-1", "nz-opw-2"), "text_replace": ("nz-opw-3",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()

    # Total denominator universe = sum of all history-note witnesses.
    assert summary["total_amendment_operation_witnesses"] == 55
    # Supported = repealed + amended + replaced + substituted + inserted + added.
    assert summary["supported_family_witnesses"] == 44
    # Frontier is empty: inserted/added moved to the supported insert family.
    assert summary["remaining_frontier_witnesses"] == 0
    # Non-executable-by-design is a separate bucket, NOT a coverage miss.
    assert summary["non_executable_by_design_witnesses"] == 8
    # Unclassified is its own bucket too.
    assert summary["unclassified_witnesses"] == 3
    # The four buckets partition the universe exactly (exhaustive, disjoint).
    assert (
        summary["supported_family_witnesses"]
        + summary["remaining_frontier_witnesses"]
        + summary["non_executable_by_design_witnesses"]
        + summary["unclassified_witnesses"]
        == summary["total_amendment_operation_witnesses"]
    )


def test_per_family_coverage_is_agreeing_over_pinned_denominator() -> None:
    work = _census(
        "act_public_2010_1",
        history={"repealed": 10, "amended": 20},
        agreeing={"repeal": ("nz-opw-1", "nz-opw-2"), "text_replace": ("nz-opw-3",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    per_family = report.summary()["per_family"]

    # repeal: 2 agreeing of 10 repealed history witnesses.
    assert per_family["repeal"]["operation_witnesses"] == 10
    assert per_family["repeal"]["dry_run_agreeing"] == 2
    assert per_family["repeal"]["coverage_fraction"] == pytest.approx(2 / 10)
    assert per_family["repeal"]["history_families"] == ["repealed"]

    # text_replace: 1 agreeing of 20 amended history witnesses.
    assert per_family["text_replace"]["operation_witnesses"] == 20
    assert per_family["text_replace"]["dry_run_agreeing"] == 1
    assert per_family["text_replace"]["coverage_fraction"] == pytest.approx(1 / 20)
    assert per_family["text_replace"]["history_families"] == ["amended"]


def test_combined_north_star_is_supported_agreeing_over_supported_total() -> None:
    work = _census(
        "act_public_2010_1",
        history={"repealed": 10, "amended": 20, "inserted": 100},
        agreeing={
            "repeal": ("nz-opw-1", "nz-opw-2"),
            "text_replace": ("nz-opw-3",),
            "insert": ("nz-opw-4", "nz-opw-5"),
        },
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()

    # The north-star numerator/denominator count every SUPPORTED family. The
    # insert family is now supported, so inserted=100 is part of the supported
    # denominator (not a separate frontier bucket).
    assert summary["supported_family_dry_run_agreeing"] == 5
    assert summary["supported_family_witnesses"] == 130
    assert summary["combined_coverage_fraction"] == pytest.approx(5 / 130)
    assert summary["remaining_frontier_witnesses"] == 0


def test_denominator_is_stable_under_candidate_extraction_growth() -> None:
    # The integrity guarantee: when extraction improves so that MORE witnesses
    # agree (numerator rises) the denominator does NOT move, so the fraction
    # rises monotonically. This is the cross-cycle north-star property the
    # candidate-derived denominator violated (45->84 growth dropped 0.60->0.51).
    history = {"repealed": 100, "amended": 200}

    cycle_n = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(
            _census(
                "w",
                history=history,
                agreeing={
                    "repeal": tuple(f"nz-opw-{i}" for i in range(20)),
                    "text_replace": tuple(f"nz-opw-{1000 + i}" for i in range(27)),
                },
            ),
        ),
    )
    cycle_n_plus_1 = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(
            _census(
                "w",
                history=history,  # identical ground truth
                agreeing={
                    "repeal": tuple(f"nz-opw-{i}" for i in range(25)),
                    "text_replace": tuple(f"nz-opw-{1000 + i}" for i in range(43)),
                },
            ),
        ),
    )
    s_n = cycle_n.summary()
    s_n1 = cycle_n_plus_1.summary()

    # Denominator pinned: identical across cycles despite more agreeing.
    assert s_n["supported_family_witnesses"] == s_n1["supported_family_witnesses"] == 300
    assert s_n["per_family"]["text_replace"]["operation_witnesses"] == 200
    assert s_n1["per_family"]["text_replace"]["operation_witnesses"] == 200
    # Numerator rose, so the fraction rose (monotone progress).
    assert s_n1["combined_coverage_fraction"] > s_n["combined_coverage_fraction"]
    assert s_n1["per_family"]["text_replace"]["coverage_fraction"] == pytest.approx(43 / 200)


def test_non_executable_bucket_is_separated_not_a_coverage_miss() -> None:
    # A work whose history is entirely non-executable-by-design contributes ZERO
    # to the supported denominator (so coverage is undefined, not 0/N), and the
    # non-executable count is reported as its own bucket.
    work = _census(
        "act_public_2010_1",
        history={"brought into force": 3, "editorial change": 2, "expired": 1},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()
    assert summary["non_executable_by_design_witnesses"] == 6
    assert summary["non_executable_family_counts"] == {
        "brought into force": 3,
        "editorial change": 2,
        "expired": 1,
    }
    # No supported witnesses -> the combined fraction is unavailable, not 0.0
    # (so the non-executable bucket can never be confused with a coverage miss).
    assert summary["supported_family_witnesses"] == 0
    assert summary["combined_coverage_fraction"] is None


def test_all_known_executable_families_are_supported_no_frontier() -> None:
    # repealed/amended/replaced/substituted/inserted/added are all supported now,
    # so a work spanning only those leaves an empty remaining-frontier bucket.
    work = _census(
        "act_public_2010_1",
        history={
            "repealed": 5,
            "inserted": 1400,
            "replaced": 350,
            "substituted": 190,
            "added": 60,
            "amended": 12,
        },
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()
    assert summary["remaining_frontier_family_counts"] == {}
    assert summary["remaining_frontier_witnesses"] == 0


def test_unbucketed_real_family_surfaces_as_frontier_loudly() -> None:
    # A real (classified) history family that is in no pinned bucket must surface
    # as remaining frontier, never be silently dropped — the loud-surface guard.
    work = _census(
        "act_public_2010_1",
        history={"repealed": 5, "some_future_executable_family": 7},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    counts = report.summary()["remaining_frontier_family_counts"]
    assert counts == {"some_future_executable_family": 7}


def test_aggregation_sums_across_works_and_dedupes_witness_rows() -> None:
    work_a = _census(
        "act_public_2010_1",
        history={"repealed": 4, "amended": 6},
        agreeing={"repeal": ("nz-opw-1", "nz-opw-1", "nz-opw-2")},  # duplicate id
    )
    work_b = _census(
        "act_public_2011_2",
        history={"repealed": 3, "amended": 9},
        agreeing={"text_replace": ("nz-opw-5",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work_a, work_b),
        selected_work_ids=("act_public_2010_1", "act_public_2011_2"),
    )
    summary = report.summary()
    # Denominators sum across works.
    assert summary["per_family"]["repeal"]["operation_witnesses"] == 7
    assert summary["per_family"]["text_replace"]["operation_witnesses"] == 15
    # Numerator dedupes witness rows: work_a repeal agreeing = 2 distinct, not 3.
    assert summary["per_family"]["repeal"]["dry_run_agreeing"] == 2
    assert summary["per_family"]["text_replace"]["dry_run_agreeing"] == 1
    assert summary["supported_family_dry_run_agreeing"] == 3


def test_measurement_only_never_claims_replay() -> None:
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(_census("act_public_2010_1", history={"repealed": 1}),),
    )
    summary = report.summary()
    assert summary["replay_claims"] is False
    assert summary["actual_replay_agreements"] == 0
    assert summary["dry_run_claims"] is True
    jsonable = report.to_jsonable()
    assert jsonable["replay_claims"] is False
    assert jsonable["report_kind"] == "dry_run_north_star"


def test_corpus_run_cache_memoizes_parse_locators_and_bytes() -> None:
    # DB-free unit test of the run-scoped cache mechanics: one parse per
    # (locator, version_id), one locators() SQL scan per pattern, bytes memoized
    # per locator, and the shared archive's close() is a no-op (the cache owns
    # the lifecycle).
    from lawvm.new_zealand.corpus_cache import (
        active_corpus_run_cache,
        corpus_run_cache,
    )

    class _FakeArchive:
        def __init__(self) -> None:
            self.locator_calls: list[str] = []
            self.get_calls: list[str] = []
            self.closed = 0

        def locators(self, pattern: str = "%") -> list[str]:
            self.locator_calls.append(pattern)
            return ["loc-a", "loc-b"]

        def get(self, locator: str, *, at: object = None) -> bytes:
            self.get_calls.append(locator)
            return b"<xml/>"

        def close(self) -> None:
            self.closed += 1

    real = _FakeArchive()
    parse_calls: list[tuple[str, str]] = []

    def _fake_parser(xml_bytes: bytes, *, xml_locator: str, version_id: str) -> object:
        parse_calls.append((xml_locator, version_id))
        return object()

    assert active_corpus_run_cache() is None
    with corpus_run_cache() as cache:
        assert cache is not None
        shared = cache.open_archive(Path("data/nz_legislation.farchive"), lambda _p: real)
        # Same path returns the same shared handle (no re-open).
        again = cache.open_archive(Path("data/nz_legislation.farchive"), lambda _p: real)
        assert shared is again

        # locators() memoized per pattern.
        assert shared.locators("pre%") == ["loc-a", "loc-b"]
        assert shared.locators("pre%") == ["loc-a", "loc-b"]
        assert real.locator_calls == ["pre%"]

        # bytes memoized per locator.
        assert shared.get("loc-a") == b"<xml/>"
        assert shared.get("loc-a") == b"<xml/>"
        assert real.get_calls == ["loc-a"]

        # parse memoized per (locator, version_id) and returns the SAME object.
        doc1 = cache.parse_document(b"<xml/>", xml_locator="loc-a", version_id="v1", parser=_fake_parser)
        doc2 = cache.parse_document(b"<xml/>", xml_locator="loc-a", version_id="v1", parser=_fake_parser)
        assert doc1 is doc2
        assert parse_calls == [("loc-a", "v1")]

        # reset_parsed drops parses + bytes + the locator memo (distinct works use
        # disjoint prefixes/locators) while keeping the shared archive handle.
        cache.reset_parsed()
        assert shared.get("loc-a") == b"<xml/>"
        assert real.get_calls == ["loc-a", "loc-a"]  # bytes re-fetched after reset
        assert shared.locators("pre%") == ["loc-a", "loc-b"]
        assert real.locator_calls == ["pre%", "pre%"]  # locator memo also reset
        cache.parse_document(b"<xml/>", xml_locator="loc-a", version_id="v1", parser=_fake_parser)
        assert parse_calls == [("loc-a", "v1"), ("loc-a", "v1")]  # re-parsed after reset

        # The shared handle's close() is a no-op while the run is active.
        shared.close()
        assert real.closed == 0

    # The owning context closes the real archive exactly once on exit.
    assert real.closed == 1
    assert active_corpus_run_cache() is None


def test_corpus_run_cache_is_reentrant() -> None:
    from lawvm.new_zealand.corpus_cache import active_corpus_run_cache, corpus_run_cache

    with corpus_run_cache() as outer:
        with corpus_run_cache() as inner:
            # A nested activation reuses the outer cache (does not shadow it).
            assert inner is outer
        # The inner exit must not tear down the outer cache.
        assert active_corpus_run_cache() is outer
    assert active_corpus_run_cache() is None


def test_parse_uncached_when_locator_empty() -> None:
    # An empty locator is not an identity, so it must never be memoized (it would
    # risk collapsing distinct byte payloads under the same key).
    from lawvm.new_zealand.corpus_cache import corpus_run_cache

    calls: list[bytes] = []

    def _parser(xml_bytes: bytes, *, xml_locator: str, version_id: str) -> object:
        calls.append(xml_bytes)
        return object()

    with corpus_run_cache() as cache:
        a = cache.parse_document(b"AAA", xml_locator="", version_id="", parser=_parser)
        b = cache.parse_document(b"BBB", xml_locator="", version_id="", parser=_parser)
        assert a is not b
        assert calls == [b"AAA", b"BBB"]


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


_DETERMINISM_WORK_IDS = (
    "act_public_1871_23",
    "act_public_1872_13",
    "act_public_2005_87",
    "act_public_2010_1",
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_run_cache_produces_identical_report_to_uncached_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The run-scoped parse/archive cache is a pure performance layer: the report
    # built with the cache active must be byte-identical (same JSON) to the report
    # built with the cache disabled. Any difference would be a semantic change.
    import json as _json
    from contextlib import contextmanager

    import lawvm.new_zealand.corpus_cache as cache_mod

    # The cached path is the default builder.
    cached = build_nz_dry_run_north_star_report(_REAL_DB, work_ids=_DETERMINISM_WORK_IDS)

    # Disable the cache everywhere it is consulted, then rebuild.
    @contextmanager
    def _noop_cache():  # type: ignore[no-untyped-def]
        yield None

    monkeypatch.setattr(cache_mod, "corpus_run_cache", _noop_cache)
    monkeypatch.setattr(cache_mod, "active_corpus_run_cache", lambda: None)
    import lawvm.new_zealand.dry_run_north_star as ns_mod

    monkeypatch.setattr(ns_mod, "corpus_run_cache", _noop_cache)
    monkeypatch.setattr(ns_mod, "active_corpus_run_cache", lambda: None)

    uncached = build_nz_dry_run_north_star_report(_REAL_DB, work_ids=_DETERMINISM_WORK_IDS)

    assert _json.dumps(cached.to_jsonable(), ensure_ascii=False, sort_keys=True) == _json.dumps(
        uncached.to_jsonable(), ensure_ascii=False, sort_keys=True
    )


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_north_star_over_real_work_matches_pinned_ground_truth() -> None:
    # The pinned denominator must equal the operation surface's ground-truth
    # family counts, and the agreeing numerator must be bounded by it.
    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    work_id = "act_public_2005_87"
    report = build_nz_dry_run_north_star_report(_REAL_DB, work_ids=(work_id,))
    summary = report.summary()

    surface = build_archived_work_operation_surface(_REAL_DB, work_id)
    repealed = sum(1 for row in surface.rows if row.operation_family == "repealed")
    amended = sum(1 for row in surface.rows if row.operation_family == "amended")

    assert summary["per_family"]["repeal"]["operation_witnesses"] == repealed
    assert summary["per_family"]["text_replace"]["operation_witnesses"] == amended
    # Numerator is bounded by the pinned denominator (no over-count).
    assert summary["per_family"]["repeal"]["dry_run_agreeing"] <= repealed
    assert summary["per_family"]["text_replace"]["dry_run_agreeing"] <= amended
    assert summary["replay_claims"] is False
