"""Tests for new EE tooling: ee-explain, ee-bench --statute, ee-corpus stats."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.timeline import ConsistencyDivergence
from lawvm.tools import ee_bench
from lawvm.tools import ee_corpus
from lawvm.tools import ee_explain
from lawvm.tools.ee_reporting import (
    EEBenchmarkReportingStratum,
    build_ee_benchmark_reporting_summary,
    classify_ee_benchmark_reporting_stratum,
)


# ---------------------------------------------------------------------------
# ee-explain tests
# ---------------------------------------------------------------------------


def _fake_replay(**kw):
    """Build a fake replay result with sensible defaults."""
    defaults = dict(
        base_id="193936",
        oracle_id="13336397",
        as_of="2011-03-17",
        base_title="Liiklusseadus",
        source_basis="pairwise_terviktekst_delta",
        n_ops=217,
        n_mismatch=7,
        n_ops_missing=0,
        n_con_missing=0,
        divergences=[],
        replayed=SimpleNamespace(body=object()),
        oracle=SimpleNamespace(title="Liiklusseadus"),
        error="",
        comparison_class="commensurable_delta",
        pair_plan=SimpleNamespace(amendments_to_apply=[]),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_ee_explain_builds_payload_with_residual_buckets(monkeypatch) -> None:
    """_build_ee_explain_payload attaches residual bucket info to divergences."""
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda oid, archive=None: b"<xml/>",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2011-03-17",
    )
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda *a, **k: _fake_replay(
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=(("chapter", "1"), ("section", "6"), ("subsection", "2"))),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                ),
            ],
        ),
    )

    payload = ee_explain._build_ee_explain_payload("193936", "13336397")

    assert payload.get("error") is None
    assert payload["base_id"] == "193936"
    assert payload["oracle_id"] == "13336397"
    assert payload["ops_count"] == 217
    assert payload["source_basis"] == "pairwise_terviktekst_delta"
    assert payload["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["benchmark_reporting_headline_eligible"] is True
    assert payload["comparison_policy"]["non_silent_rule_count"] == 2
    assert payload["comparison_policy"]["non_silent_rule_classes"] == [
        "lexical_institutional_drift",
        "manual_exception",
    ]
    assert payload["comparison_policy"]["non_silent_rule_names"] == [
        "politseiasutus_rename",
        "politsei_plural_rename",
    ]
    assert payload["divergence_count"] == 1
    assert payload["residual_inventory"] == {
        "residual_count": 7,
        "bucket_counts": {
            "appendix_display_pathology": 1,
            "source_oracle_drift": 6,
        },
        "matched_current_divergence_count": 1,
        "matched_current_bucket_counts": {"source_oracle_drift": 1},
        "unknown_current_divergence_count": 0,
        "unknown_current_divergence_addresses": (),
    }
    assert len(payload["divergences"]) == 1
    div = payload["divergences"][0]
    assert div["address"] == "chapter:1/section:6/subsection:2"
    assert div["type"] == "MISMATCH"
    assert div["residual_bucket"] == "source_oracle_drift"
    assert "12776187" in div["residual_evidence"]


@pytest.mark.parametrize(
    ("base_id", "oracle_id", "address_path", "expected_bucket", "evidence_snippet"),
    [
        (
            "122122021038",
            "130062023072",
            (("chapter", "6"),),
            "oracle_correction_notice",
            "<veaparandus>",
        ),
    ],
)
def test_ee_explain_surfaces_nonproved_residual_buckets(
    monkeypatch,
    base_id: str,
    oracle_id: str,
    address_path: tuple[tuple[str, str], ...],
    expected_bucket: str,
    evidence_snippet: str,
) -> None:
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda oid, archive=None: b"<xml/>",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2026-01-01",
    )
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda *a, **k: _fake_replay(
            base_id=base_id,
            oracle_id=oracle_id,
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=address_path),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                ),
            ],
        ),
    )

    payload = ee_explain._build_ee_explain_payload(base_id, oracle_id)

    assert payload.get("error") is None
    assert payload["divergences"][0]["residual_bucket"] == expected_bucket
    assert evidence_snippet in payload["divergences"][0]["residual_evidence"]
    assert payload["bucket_groups"][expected_bucket] == [payload["divergences"][0]["address"]]


def test_ee_explain_handles_error_result(monkeypatch) -> None:
    """_build_ee_explain_payload returns error dict when replay fails."""
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda oid, archive=None: b"<xml/>",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2025-07-06",
    )
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda *a, **k: _fake_replay(error="Failed to load base: 404"),
    )

    payload = ee_explain._build_ee_explain_payload("nonexistent", "123456")
    assert payload["error"] == "Failed to load base: 404"


def test_ee_explain_groups_by_bucket(monkeypatch) -> None:
    """_build_ee_explain_payload groups divergences by residual bucket."""
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda oid, archive=None: b"<xml/>",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2025-07-06",
    )
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda *a, **k: _fake_replay(
            base_id="122032024011",
            oracle_id="105072025019",
            as_of="2025-07-06",
            base_title="Väärteomenetluse seadustik",
            n_ops=25,
            n_mismatch=2,
            n_ops_missing=9,
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=(("chapter", "5"), ("section", "31_6"), ("subsection", "4"))),
                    divergence_type="MISMATCH",
                    ops_text="replay",
                    consolidated_text="oracle",
                ),
                ConsistencyDivergence(
                    address=LegalAddress(
                        path=(("chapter", "5"), ("section", "31_6"), ("subsection", "5"), ("item", "1"))
                    ),
                    divergence_type="OPS_MISSING",
                    ops_text="",
                    consolidated_text="oracle item",
                ),
            ],
            oracle=SimpleNamespace(title="Väärteomenetluse seadustik"),
        ),
    )

    payload = ee_explain._build_ee_explain_payload("122032024011", "105072025019")

    assert "bucket_groups" in payload
    assert "source_pathology" in payload["bucket_groups"]
    assert len(payload["bucket_groups"]["source_pathology"]) == 2


def test_ee_explain_prints_comparison_policy_summary(capsys, monkeypatch) -> None:
    """_print_ee_explain emits the bounded comparison-policy summary."""
    monkeypatch.setattr(
        "lawvm.estonia.fetch.fetch_rt_xml",
        lambda oid, archive=None: b"<xml/>",
    )
    monkeypatch.setattr(
        "lawvm.estonia.fetch.extract_effective_date",
        lambda xml: "2011-03-17",
    )
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda *a, **k: _fake_replay(
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=(("chapter", "1"), ("section", "6"), ("subsection", "2"))),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                ),
            ],
        ),
    )

    payload = ee_explain._build_ee_explain_payload("193936", "13336397")
    ee_explain._print_ee_explain(payload, verbose=False)

    out = capsys.readouterr().out
    assert "reporting  : EE_CORE_COMMENSURABLE (headline=yes)" in out
    assert "drift      : 2 non-silent comparison rules" in out
    assert "classes   : lexical_institutional_drift=2, manual_exception=0" in out
    assert "rules     : politseiasutus_rename, politsei_plural_rename" in out

def test_ee_reporting_stratum_classifier_maps_expected_cases() -> None:
    assert (
        classify_ee_benchmark_reporting_stratum(
            "pairwise_terviktekst_delta",
            "commensurable_delta",
        )
        == EEBenchmarkReportingStratum.CORE_COMMENSURABLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum("base_is_oracle", "base_is_oracle")
        == EEBenchmarkReportingStratum.BASE_IS_ORACLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum(
            "noncommensurable",
            "forward_looking_oracle",
        )
        == EEBenchmarkReportingStratum.FORWARD_LOOKING_ORACLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum(
            "earliest_available_terviktekst",
            "same_chain_editorial_drift",
        )
        == EEBenchmarkReportingStratum.SAME_CHAIN_EDITORIAL_DRIFT
    )
    assert (
        classify_ee_benchmark_reporting_stratum("noncommensurable", "no_oracle")
        == EEBenchmarkReportingStratum.NONCORE_SOURCE_GAP
    )
    assert build_ee_benchmark_reporting_summary(
        "pairwise_terviktekst_delta",
        "commensurable_delta",
    ) == {
        "benchmark_reporting_stratum": "EE_CORE_COMMENSURABLE",
        "benchmark_reporting_headline_eligible": True,
    }


# ---------------------------------------------------------------------------
# ee-bench --statute mode tests
# ---------------------------------------------------------------------------


def test_run_single_statute_finds_pair_by_base_id(monkeypatch, capsys) -> None:
    """_run_single_statute looks up a pair from the corpus CSV by base_id."""
    import csv
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        writer = csv.writer(f)
        writer.writerow(["grupi_id", "base_id", "oracle_id", "n_amendments", "schema"])
        writer.writerow(["g1", "base1", "oracle1", "3", "tyviseadus"])
        csv_path = f.name

    monkeypatch.setattr(ee_bench, "_CORPUS_CSV", Path(csv_path))
    monkeypatch.setattr(
        ee_bench,
        "_score_one_pair",
        lambda gid, bid, oid, title, archive: SimpleNamespace(
            status="OK",
            comparison_class="commensurable_delta",
            core_benchmark=True,
            n_ops=5,
            n_divs=2,
            sec_match=0.95,
            r_secs=10,
            o_secs=10,
            adjudicated_bucket_counts="source_oracle_drift=2",
            open_current_divergence_count=0,
        ),
    )

    args = SimpleNamespace(
        db=None,
        ee_corpus=None,
        statute="base1",
    )
    ee_bench._run_single_statute("base1", args)

    out = capsys.readouterr().out
    assert "base1" in out
    assert "oracle1" in out
    assert "sec" in out

    Path(csv_path).unlink()


def test_run_single_statute_reports_not_found(monkeypatch, capsys) -> None:
    """_run_single_statute exits cleanly when statute is not in corpus."""
    import csv
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        writer = csv.writer(f)
        writer.writerow(["grupi_id", "base_id", "oracle_id", "n_amendments", "schema"])
        writer.writerow(["g1", "base1", "oracle1", "3", "tyviseadus"])
        csv_path = f.name

    monkeypatch.setattr(ee_bench, "_CORPUS_CSV", Path(csv_path))

    args = SimpleNamespace(
        db=None,
        ee_corpus=None,
        statute="nonexistent",
    )

    try:
        ee_bench._run_single_statute("nonexistent", args)
        assert False, "Should have raised SystemExit"
    except SystemExit as e:
        assert e.code == 1

    err = capsys.readouterr().err
    assert "not found" in err

    Path(csv_path).unlink()


def test_ee_corpus_run_stats_falls_back_when_archive_stats_breaks(tmp_path, capsys) -> None:
    import sqlite3

    db_path = tmp_path / "ee.farchive"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE locator (locator TEXT)")
    conn.execute("CREATE TABLE blob (digest TEXT PRIMARY KEY, raw_size INTEGER NOT NULL, stored_self_size INTEGER NOT NULL)")
    conn.execute("INSERT INTO locator(locator) VALUES (?)", ("https://www.riigiteataja.ee/akt/123.xml",))
    conn.execute("INSERT INTO blob(digest, raw_size, stored_self_size) VALUES ('d1', 1000, 800)")
    conn.commit()
    conn.close()

    class FakeArchive:
        def __init__(self, path):
            self._conn = sqlite3.connect(path)
        def stats(self):
            raise sqlite3.OperationalError("no such table: locator_span")
        def close(self):
            self._conn.close()
        def get(self, url):
            return b'<root xmlns="tyviseadus_1_10.02.2010"><terviktekstiGrupiID>1</terviktekstiGrupiID><paragrahv/></root>'

    import farchive

    original_farchive: Any = farchive.Farchive
    setattr(cast(Any, farchive), "Farchive", FakeArchive)
    try:
        args = argparse.Namespace(db=str(db_path), json=True)
        ee_corpus.run_stats(args)
    finally:
        setattr(cast(Any, farchive), "Farchive", original_farchive)

    out = capsys.readouterr().out
    assert '"archive_path"' in out
    assert '"urls": 1' in out
    assert '"blobs": 1' in out
