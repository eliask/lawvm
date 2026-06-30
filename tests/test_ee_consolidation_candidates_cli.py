"""Tests for the `lawvm ee-consolidation-candidates` CLI surface.

These exercise the bench-run aggregation entry point and the tool payload
builder with injected fakes, so they never touch the Riigi Teataja archive or
the real replay path. They cover:

* CLI registration (the subcommand parses with its flags);
* the run-level aggregation ranks strong-before-triage across pairs;
* one raising pair is recorded as a typed error row, not silently dropped;
* the tool payload's `--tier` / `--top` filtering and `--json` round-trip;
* determinism of the run-level JSON projection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, cast

import lawvm.tools.ee_consolidation_candidates as cli_tool
from lawvm.estonia.consolidation_error_candidates import (
    ConsolidationCandidatePairInput,
    build_consolidation_candidate_run_report,
    consolidation_error_candidates,
    run_report_to_jsonable,
)
from lawvm.estonia.residual_inventory import EEResidualBucket, EEResidualRecord
from lawvm.estonia.residual_reporting import EEResidualSummary
from lawvm.tools.cli import _build_parser as build_parser


# ---------------------------------------------------------------------------
# Lightweight fakes (no archive, no replay) — mirror the per-pair module tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeAddress:
    path: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _FakeDivergence:
    address: _FakeAddress
    divergence_type: str
    ops_text: Optional[str]
    consolidated_text: Optional[str]


@dataclass
class _FakeResult:
    base_id: str
    oracle_id: str
    base_title: str = ""
    comparison_class: str = ""
    divergences: list = field(default_factory=list)
    compiled_ops: tuple = ()
    applied_snapshot_ops: tuple = ()


def _addr(spec: str) -> _FakeAddress:
    path: tuple[tuple[str, str], ...] = tuple(
        cast("tuple[str, str]", tuple(seg.split(":", 1))) for seg in spec.split("/")
    )
    return _FakeAddress(path=path)


def _record(address: str, bucket: str) -> EEResidualRecord:
    return EEResidualRecord(
        address=address,
        bucket=cast("EEResidualBucket", bucket),
        evidence=f"evidence for {address}",
    )


def _summary(base: str, oracle: str, records: dict[str, EEResidualRecord]) -> EEResidualSummary:
    return EEResidualSummary(
        base_id=base,
        oracle_id=oracle,
        statute_title="Test Statute",
        comparison_class="commensurable_delta",
        residual_count=len(records),
        bucket_counts={},
        matched_current_divergence_count=len(records),
        matched_current_bucket_counts={},
        unknown_current_divergence_count=0,
        unknown_current_divergence_addresses=(),
        record_by_address=dict(records),
    )


def _strong_pair() -> tuple[_FakeResult, EEResidualSummary]:
    div = _FakeDivergence(
        address=_addr("section:5"),
        divergence_type="MISMATCH",
        ops_text="replay text for section 5",
        consolidated_text="stale consolidation text for section 5",
    )
    result = _FakeResult(
        base_id="BASE_A",
        oracle_id="ORACLE_A",
        base_title="Statute A",
        comparison_class="commensurable_delta",
        divergences=[div],
    )
    summary = _summary("BASE_A", "ORACLE_A", {"section:5": _record("section:5", "source_oracle_drift")})
    return result, summary


def _triage_pair() -> tuple[_FakeResult, EEResidualSummary]:
    div = _FakeDivergence(
        address=_addr("section:9/subsection:2"),
        divergence_type="OPS_MISSING",
        ops_text=None,
        consolidated_text="oracle-only text under section 9",
    )
    result = _FakeResult(
        base_id="BASE_B",
        oracle_id="ORACLE_B",
        base_title="Statute B",
        comparison_class="commensurable_delta",
        divergences=[div],
    )
    summary = _summary("BASE_B", "ORACLE_B", {})
    return result, summary


def _fake_entry_factory(results: dict[str, tuple[_FakeResult, EEResidualSummary]]):
    """A drop-in for `consolidation_error_candidates` keyed by base_id."""

    def _entry(*, base_id, as_of, oracle_id=None, archive=None, **_):
        if base_id == "BOOM":
            raise RuntimeError("replay exploded")
        result, summary = results[base_id]
        return consolidation_error_candidates(
            base_id=base_id,
            as_of=as_of,
            oracle_id=oracle_id,
            result=result,
            residual_summary=summary,
        )

    return _entry


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_command_is_registered_and_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "ee-consolidation-candidates",
            "--label",
            "some_run",
            "--tier",
            "strong",
            "--top",
            "5",
            "--json",
        ]
    )
    assert args.command == "ee-consolidation-candidates"
    assert args.label == "some_run"
    assert args.tier == "strong"
    assert args.top == 5
    assert args.json is True


def test_tier_choices_are_constrained() -> None:
    parser = build_parser()
    for tier in ("strong", "triage", "all"):
        args = parser.parse_args(["ee-consolidation-candidates", "--tier", tier])
        assert args.tier == tier


# ---------------------------------------------------------------------------
# Run-level aggregation
# ---------------------------------------------------------------------------


def _run_aggregation(extra_pairs=()):
    strong_result, strong_summary = _strong_pair()
    triage_result, triage_summary = _triage_pair()
    results = {
        "BASE_A": (strong_result, strong_summary),
        "BASE_B": (triage_result, triage_summary),
    }
    pairs = (
        ConsolidationCandidatePairInput("BASE_A", "ORACLE_A", "Statute A", "2024-01-01"),
        ConsolidationCandidatePairInput("BASE_B", "ORACLE_B", "Statute B", "2024-01-01"),
        *extra_pairs,
    )
    return build_consolidation_candidate_run_report(
        pairs,
        run_label="fixture_run",
        replay=_fake_entry_factory(results),
    )


def test_run_report_ranks_strong_before_triage() -> None:
    report = _run_aggregation()
    ranked = report.ranked_candidates()
    tiers = [c.tier for c in ranked]
    assert tiers == ["strong", "triage"]
    assert report.strong_total == 1
    assert report.triage_total == 1
    assert report.scored_pair_count == 2


def test_run_report_strong_and_triage_views_partition() -> None:
    report = _run_aggregation()
    strong = report.strong_candidates()
    triage = report.triage_candidates()
    assert [c.tier for c in strong] == ["strong"]
    assert [c.tier for c in triage] == ["triage"]
    assert strong[0].base_id == "BASE_A"
    assert strong[0].residual_bucket == "source_oracle_drift"
    assert triage[0].base_id == "BASE_B"


def test_raising_pair_recorded_as_typed_error_not_dropped() -> None:
    report = _run_aggregation(
        extra_pairs=(ConsolidationCandidatePairInput("BOOM", "ORACLE_X", "Boom", "2024-01-01"),)
    )
    assert report.pair_count == 3
    assert report.scored_pair_count == 2  # the two good pairs still scored
    assert len(report.errors) == 1
    err = report.errors[0]
    assert err.base_id == "BOOM"
    assert "replay exploded" in err.error


def test_run_report_json_is_deterministic() -> None:
    first = run_report_to_jsonable(_run_aggregation())
    second = run_report_to_jsonable(_run_aggregation())
    assert first == second
    assert first["ranked_candidates"][0]["tier"] == "strong"


# ---------------------------------------------------------------------------
# Tool payload (tier / top filtering, --json round-trip)
# ---------------------------------------------------------------------------


class _StubArchive:
    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _patch_tool(monkeypatch, tmp_path) -> str:
    """Point the tool at a tiny fixture CSV and stub the archive/replay path."""
    run = tmp_path / "fixture_run.csv"
    run.write_text(
        "base_id,oracle_id,title,status,n_divs\n"
        "BASE_A,ORACLE_A,Statute A,OK,1\n"
        "BASE_B,ORACLE_B,Statute B,OK,1\n"
        "BASE_ZERO,ORACLE_ZERO,Zero,OK,0\n"  # excluded: no divergences
        "BASE_FAIL,ORACLE_FAIL,Fail,FAIL,3\n",  # excluded: status not OK
        encoding="utf-8",
    )
    strong_result, strong_summary = _strong_pair()
    triage_result, triage_summary = _triage_pair()
    results = {
        "BASE_A": (strong_result, strong_summary),
        "BASE_B": (triage_result, triage_summary),
    }
    entry = _fake_entry_factory(results)

    monkeypatch.setattr(cli_tool, "open_rt_archive", lambda *a, **k: _StubArchive())
    monkeypatch.setattr(cli_tool, "_resolve_as_of", lambda oracle_id, archive: "2024-01-01")
    monkeypatch.setattr(cli_tool, "build_consolidation_candidate_run_report",
                        lambda pairs, **kw: build_consolidation_candidate_run_report(pairs, replay=entry, **{k: v for k, v in kw.items() if k != "archive"}))
    return str(run)


def test_payload_strong_tier_filter(monkeypatch, tmp_path) -> None:
    run_path = _patch_tool(monkeypatch, tmp_path)
    payload = cli_tool.build_consolidation_candidates_payload(run_path, tier="strong", top=20)
    assert payload["candidate_row_count"] == 2  # zero-div and FAIL rows excluded
    assert payload["tier_filter"] == "strong"
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["tier"] == "strong"
    assert payload["selected"][0]["residual_bucket"] == "source_oracle_drift"


def test_payload_all_tier_and_top_limit(monkeypatch, tmp_path) -> None:
    run_path = _patch_tool(monkeypatch, tmp_path)
    payload = cli_tool.build_consolidation_candidates_payload(run_path, tier="all", top=1)
    # all-tier has both, but top=1 keeps only the strong one (strong ranks first)
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["tier"] == "strong"

    full = cli_tool.build_consolidation_candidates_payload(run_path, tier="all", top=0)
    assert [c["tier"] for c in full["selected"]] == ["strong", "triage"]


def test_payload_triage_tier_filter(monkeypatch, tmp_path) -> None:
    run_path = _patch_tool(monkeypatch, tmp_path)
    payload = cli_tool.build_consolidation_candidates_payload(run_path, tier="triage", top=20)
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["tier"] == "triage"
    assert payload["selected"][0]["base_id"] == "BASE_B"
