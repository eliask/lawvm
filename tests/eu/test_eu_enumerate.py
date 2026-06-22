"""Unit tests for the EU enumeration-driven acquisition lane (eu_enumerate).

No network: a SAVED fixture SPARQL response is parsed. Covers:
* fixture → SORTED + de-duplicated CELEX list;
* EnumerationSnapshot content-hash is stable + reproducible;
* the static_manifest / official_signed_registry universe ACCEPTS
  closed_world_claim=true, while observed_crawl REJECTS it (the invariant);
* sampled acquisition logs the cap (acquisition_sampled) and never silently
  truncates;
* flipping the query parameter (regulation → directive) changes the query text +
  its hash + the witness locator slug.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from farchive import Farchive

from lawvm.eu import eu_enumerate
from lawvm.eu.eu_acquire import CelexIngestRun
from lawvm.eu.eu_enumerate import (
    EnumerationError,
    EnumerationQuery,
    EnumerationSnapshot,
    directives_in_force_query,
    enumerate_and_acquire,
    enumerate_snapshot,
    parse_sparql_celexes,
    regulations_in_force_query,
    snapshot_universe,
    store_snapshot,
)
from lawvm.substrate.corpus_totality import (
    CorpusTotalityError,
    CorpusTotalityUniverse,
)

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT_DATE = "2026-06-22"
FETCHED_AT = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _archive(tmp_path: Path) -> Farchive:
    return Farchive(str(tmp_path / "eu_cellar.farchive"))


# --------------------------------------------------------------------------- #
# Parse: fixture → sorted, de-duplicated CELEX list                           #
# --------------------------------------------------------------------------- #


def test_parse_sorts_and_dedupes() -> None:
    data = _fixture_bytes("sparql_regulations_in_force_sample.json")
    celexes = parse_sparql_celexes(data)
    # The fixture has 5 bindings with a duplicate (32016R0679) and is unsorted;
    # the parse de-duplicates and sorts.
    assert celexes == ("31958R0001", "31958R0005", "31958R0006", "32016R0679")
    assert list(celexes) == sorted(celexes)


def test_parse_rejects_html_error_page() -> None:
    data = _fixture_bytes("sparql_error_page.html")
    with pytest.raises(EnumerationError, match="not JSON"):
        parse_sparql_celexes(data)


def test_parse_rejects_empty() -> None:
    with pytest.raises(EnumerationError, match="empty"):
        parse_sparql_celexes(b"")


def test_parse_rejects_non_results_json() -> None:
    with pytest.raises(EnumerationError, match="results"):
        parse_sparql_celexes(b'{"head": {"vars": ["celex"]}}')


# --------------------------------------------------------------------------- #
# Snapshot content hash: stable + reproducible                                #
# --------------------------------------------------------------------------- #


def _fixture_snapshot(name: str = "sparql_regulations_in_force_sample.json") -> EnumerationSnapshot:
    data = _fixture_bytes(name)

    def _fetch(_q: str, _e: str, _t: int) -> bytes:
        return data

    return enumerate_snapshot(snapshot_date=SNAPSHOT_DATE, _fetch=_fetch)


def test_snapshot_id_stable_and_reproducible() -> None:
    snap1 = _fixture_snapshot()
    snap2 = _fixture_snapshot()
    assert snap1.snapshot_id == snap2.snapshot_id
    assert snap1.snapshot_id.startswith("sha256:")
    # Reproducible: rebuilding the snapshot from its own normalized fields gives
    # the identical id (the hash is a pure function of the canonical body).
    rebuilt = EnumerationSnapshot(
        endpoint=snap1.endpoint,
        query_text=snap1.query_text,
        snapshot_date=snap1.snapshot_date,
        resource_type_uri=snap1.resource_type_uri,
        in_force=snap1.in_force,
        celexes=snap1.celexes,
    )
    assert rebuilt.snapshot_id == snap1.snapshot_id


def test_snapshot_normalizes_unsorted_duplicate_input() -> None:
    a = EnumerationSnapshot(
        endpoint=eu_enumerate.SPARQL_ENDPOINT,
        query_text="Q",
        snapshot_date=SNAPSHOT_DATE,
        resource_type_uri=eu_enumerate.RESOURCE_TYPE_REGULATION,
        in_force=True,
        celexes=("32016R0679", "31958R0001", "32016R0679", ""),
    )
    # De-duplicated, sorted, empties dropped.
    assert a.celexes == ("31958R0001", "32016R0679")
    assert a.count == 2


def test_snapshot_id_changes_with_date() -> None:
    snap = _fixture_snapshot()
    other = EnumerationSnapshot(
        endpoint=snap.endpoint,
        query_text=snap.query_text,
        snapshot_date="2026-06-23",
        resource_type_uri=snap.resource_type_uri,
        in_force=snap.in_force,
        celexes=snap.celexes,
    )
    assert other.snapshot_id != snap.snapshot_id


# --------------------------------------------------------------------------- #
# Universe: closed_world_claim=true legal for manifest, illegal for crawl      #
# --------------------------------------------------------------------------- #


def test_manifest_universe_accepts_closed_world_claim() -> None:
    snap = _fixture_snapshot()
    universe = snapshot_universe(snap, universe_kind="static_manifest")
    assert universe.closed_world_claim is True
    assert universe.universe_kind == "static_manifest"
    # The completeness claim is grounded in the snapshot witness + query hash.
    assert snap.witness_locator in universe.enumeration_source_refs
    assert snap.snapshot_id in universe.enumeration_source_refs


def test_official_signed_registry_also_accepts_closed_world_claim() -> None:
    snap = _fixture_snapshot()
    universe = snapshot_universe(snap, universe_kind="official_signed_registry")
    assert universe.closed_world_claim is True
    assert universe.universe_kind == "official_signed_registry"


def test_observed_crawl_rejects_closed_world_claim() -> None:
    # The invariant: a crawl is NOT a closed-world enumeration. This is what makes
    # the manifest universe the only honest home for the closed-world claim.
    with pytest.raises(CorpusTotalityError, match="observed_crawl"):
        CorpusTotalityUniverse(
            universe_kind="observed_crawl",
            enumeration_source_refs=(),
            enumeration_policy_id="x",
            closed_world_claim=True,
        )


def test_snapshot_universe_refuses_non_manifest_kind() -> None:
    snap = _fixture_snapshot()
    with pytest.raises(ValueError, match="manifest/registry"):
        snapshot_universe(snap, universe_kind="observed_crawl")


# --------------------------------------------------------------------------- #
# Snapshot persistence (witness store)                                        #
# --------------------------------------------------------------------------- #


def test_store_snapshot_round_trips(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    snap = _fixture_snapshot()
    locator = store_snapshot(archive, snap, observed_at=FETCHED_AT)
    assert locator == "eu-enumeration://regulations-in-force/2026-06-22"
    assert archive.get(locator) == snap.witness_bytes()
    span = archive.resolve(locator)
    assert span is not None
    md = span.last_metadata
    assert md is not None
    assert md["snapshot_id"] == snap.snapshot_id
    assert md["count"] == str(snap.count)
    assert "© European Union" in md["reuse_notice"]
    assert md["copyright"] == "© European Union"
    archive.close()


# --------------------------------------------------------------------------- #
# Orchestration: bounded sampled acquisition logs the cap, never truncates     #
# --------------------------------------------------------------------------- #


def _fake_acquire(celex: str, **kwargs: object) -> CelexIngestRun:
    return CelexIngestRun(
        celex=celex,
        consolidation_date="enacted",
        expression_language=str(kwargs.get("language", "fin")),
        fetched_at=FETCHED_AT,
        farchive_path="memory",
        added=2,
    )


def test_sampled_acquisition_logs_cap(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    snap = _fixture_snapshot()  # 4 distinct CELEX
    run = enumerate_and_acquire(
        snap,
        fetched_at=FETCHED_AT,
        farchive=archive,
        sample_limit=2,
        _acquire_celex=_fake_acquire,
    )
    # FULL enumeration is the completeness artifact (count preserved)...
    assert run.enumerated_count == 4
    # ...but acquisition was BOUNDED and the cap is OWNED, not silent.
    assert run.acquisition_sampled is True
    assert run.sample_limit == 2
    assert len(run.acquired_celexes) == 2
    # Deterministic: the first two CELEX in sorted order.
    assert run.acquired_celexes == ("31958R0001", "31958R0005")
    # The universe is the closed-world manifest universe.
    assert run.universe.closed_world_claim is True
    archive.close()


def test_non_act_celex_skipped_not_acquired_not_dropped(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    # A snapshot mixing act CELEX and a corrigendum ...R(NN) id (real registry
    # member, but not a well-formed ACT CELEX).
    snap = EnumerationSnapshot(
        endpoint=eu_enumerate.SPARQL_ENDPOINT,
        query_text="Q",
        snapshot_date=SNAPSHOT_DATE,
        resource_type_uri=eu_enumerate.RESOURCE_TYPE_REGULATION,
        in_force=True,
        celexes=("31958R0001", "31958R0001(01)", "32016R0679"),
    )
    run = enumerate_and_acquire(
        snap,
        fetched_at=FETCHED_AT,
        farchive=archive,
        sample_limit=10,
        _acquire_celex=_fake_acquire,
    )
    # The corrigendum id REMAINS in the snapshot (completeness preserved)...
    assert "31958R0001(01)" in snap.celexes
    assert run.enumerated_count == 3
    # ...is NOT acquired (only act-level works are acquirable)...
    assert set(run.acquired_celexes) == {"31958R0001", "32016R0679"}
    # ...and is OWNED as skipped, never silently dropped.
    assert "31958R0001(01)" in run.non_act_celexes_skipped
    archive.close()


def test_acquire_all_when_under_limit(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    snap = _fixture_snapshot()  # 4 distinct CELEX
    run = enumerate_and_acquire(
        snap,
        fetched_at=FETCHED_AT,
        farchive=archive,
        sample_limit=10,
        _acquire_celex=_fake_acquire,
    )
    assert run.acquisition_sampled is False
    assert len(run.acquired_celexes) == 4
    archive.close()


def test_enumerate_only_no_acquire(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    snap = _fixture_snapshot()
    run = enumerate_and_acquire(
        snap,
        fetched_at=FETCHED_AT,
        farchive=archive,
        acquire=False,
        _acquire_celex=_fake_acquire,
    )
    assert run.acquired_celexes == ()
    assert run.acquire_runs == []
    # The snapshot witness is stored regardless of acquisition.
    assert archive.get(run.snapshot_locator) == snap.witness_bytes()
    archive.close()


# --------------------------------------------------------------------------- #
# Query parameterization: regulation → directive flips text, hash, slug        #
# --------------------------------------------------------------------------- #


def test_query_parameter_flip_regulation_to_directive() -> None:
    reg = regulations_in_force_query()
    dir_ = directives_in_force_query()
    assert reg.resource_type_uri.endswith("/REG")
    assert dir_.resource_type_uri.endswith("/DIR")
    assert reg.sparql_text() != dir_.sparql_text()
    assert reg.query_sha256 != dir_.query_sha256
    # Both render the in-force boolean and the celex projection.
    for q in (reg, dir_):
        assert 'resource_legal_in-force "true"^^xsd:boolean' in q.sparql_text()
        assert "?celex" in q.sparql_text()


def test_in_force_flag_flips_query_text() -> None:
    in_force = EnumerationQuery(in_force=True)
    repealed = EnumerationQuery(in_force=False)
    assert '"true"^^xsd:boolean' in in_force.sparql_text()
    assert '"false"^^xsd:boolean' in repealed.sparql_text()
    assert in_force.query_sha256 != repealed.query_sha256


def test_directive_snapshot_locator_slug() -> None:
    snap = EnumerationSnapshot(
        endpoint=eu_enumerate.SPARQL_ENDPOINT,
        query_text=directives_in_force_query().sparql_text(),
        snapshot_date=SNAPSHOT_DATE,
        resource_type_uri=eu_enumerate.RESOURCE_TYPE_DIRECTIVE,
        in_force=True,
        celexes=("31970L0001",),
    )
    assert snap.witness_locator == "eu-enumeration://directives-in-force/2026-06-22"
