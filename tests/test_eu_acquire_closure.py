"""Tests for DAG-recursion acquisition (eu_acquire_closure).

Offline core: seams stand in for the SPARQL DAG build and the per-CELEX Cellar
acquire, so the closure walk (base + amenders, corrigendum skip, failure
recording) is hermetic. One networked smoke test exercises the real SPARQL DAG
build and tolerates the transient CELLAR 500 honestly.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from lawvm.eu import eu_acquire
from lawvm.eu.eu_amendment_graph import (
    AmendmentEdge,
    AmendmentGraph,
    AmendmentGraphError,
    build_amendment_graph,
)
from lawvm.eu.eu_acquire_closure import acquire_amendment_closure

BASE = "32016R0044"
FETCHED_AT = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)


def _graph() -> AmendmentGraph:
    return AmendmentGraph(
        base_celex=BASE,
        edges=(
            AmendmentEdge("32016R0466", BASE, "amends", "2016-04-01", "2016-04-01"),
            AmendmentEdge("32017R0488", BASE, "amends", "2017-03-23", "2017-03-23"),
            AmendmentEdge("32016R0044R(01)", BASE, "corrects"),  # not an act CELEX
        ),
    )


def _fake_acquire_factory(added_for: set[str], fail_for: set[str]):
    def _fake(celex: str, **_kw) -> eu_acquire.CelexIngestRun:
        run = eu_acquire.CelexIngestRun(
            celex=celex,
            consolidation_date="enacted",
            expression_language="eng",
            fetched_at=FETCHED_AT,
            farchive_path="(test)",
        )
        if celex in fail_for:
            run.failed = 1
        elif celex in added_for:
            run.added = 2
        return run

    return _fake


def test_closure_acquires_base_plus_amenders_skips_corrigendum() -> None:
    fake = _fake_acquire_factory(added_for={BASE, "32016R0466", "32017R0488"}, fail_for=set())
    run = acquire_amendment_closure(
        BASE,
        fetched_at=FETCHED_AT,
        _build_graph=lambda _c: _graph(),
        _acquire_celex=fake,
    )
    assert run.graph is not None
    assert run.base_run is not None and run.base_run.added == 2
    # The two well-formed act amenders were acquired; the corrigendum was skipped.
    assert set(run.acquired_celexes) == {BASE, "32016R0466", "32017R0488"}
    assert run.skipped_non_act_amenders == ["32016R0044R(01)"]
    assert run.total_added == 6  # 3 celexes x (notice + item)


def test_closure_records_failure_not_silent_skip() -> None:
    fake = _fake_acquire_factory(added_for={BASE}, fail_for={"32017R0488"})
    run = acquire_amendment_closure(
        BASE,
        fetched_at=FETCHED_AT,
        _build_graph=lambda _c: _graph(),
        _acquire_celex=fake,
    )
    assert "32017R0488" in run.failed_celexes
    assert run.total_failed == 1


def test_graph_build_error_is_recorded_not_fabricated() -> None:
    fake = _fake_acquire_factory(added_for={BASE}, fail_for=set())

    def _boom(_c: str) -> AmendmentGraph:
        raise AmendmentGraphError("CELLAR backend 500 (JDBC)")

    run = acquire_amendment_closure(
        BASE,
        fetched_at=FETCHED_AT,
        _build_graph=_boom,
        _acquire_celex=fake,
    )
    # Base still acquired; the closure is a RECORDED gap, never a silent success.
    assert run.base_run is not None and run.base_run.added == 2
    assert run.graph is None
    assert "AmendmentGraphError" in run.graph_error
    assert run.amender_runs == []


def test_max_amenders_bound_is_a_sample_not_truncation_lie() -> None:
    fake = _fake_acquire_factory(added_for={BASE, "32016R0466", "32017R0488"}, fail_for=set())
    run = acquire_amendment_closure(
        BASE,
        fetched_at=FETCHED_AT,
        max_amenders=1,
        _build_graph=lambda _c: _graph(),
        _acquire_celex=fake,
    )
    # Only the first (sorted) act amender acquired; corrigendum not in the bound.
    assert len(run.amender_runs) == 1


# --------------------------------------------------------------------------- #
# Networked smoke (opt-in via env): real SPARQL DAG build; tolerates 500       #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("LAWVM_EU_NETWORK_SMOKE") != "1",
    reason="networked CELLAR smoke; set LAWVM_EU_NETWORK_SMOKE=1 to run",
)
def test_live_sparql_amendment_graph_smoke() -> None:
    # The SPARQL endpoint is the DAG source (observed UP in Increment 0). A
    # transient CELLAR 500 surfaces as AmendmentGraphError, NOT a silent empty
    # graph — that is the contract, so either outcome is acceptable here.
    try:
        graph = build_amendment_graph("32016R0679")
    except AmendmentGraphError:
        pytest.skip("CELLAR SPARQL transiently unavailable (recorded as error)")
    # GDPR is corrected (3 corrigenda) but not substantively amended.
    assert graph.base_celex == "32016R0679"
    assert all(e.base_celex == "32016R0679" for e in graph.edges)
