from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS,
    NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
    NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_SCOPE_COMPLETE_SET,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZDryRunRefusal,
    NZDryRunReport,
    NZDryRunScopeCompleteness,
    NZMutationBoundaryProof,
)
from lawvm.new_zealand.dry_run_corpus import (
    NZDryRunRepealCorpusReport,
    build_nz_dry_run_repeal_corpus_report,
)


def _proof(
    *,
    op_id: str,
    oracle_match: str,
    oracle_rule_id: str,
    oracle_present: bool = True,
    occupancy: str = "tombstone",
    neighbors_unchanged: bool = True,
) -> NZMutationBoundaryProof:
    return NZMutationBoundaryProof(
        op_id=op_id,
        action="REPEAL",
        target_address="section:108",
        selected_source_path=("prov:108",),
        target_xml_id="DLM1",
        target_digest_before="before",
        target_digest_after="after",
        operation_payload="payload=tombstone",
        occupancy_before="substantive",
        occupancy_after="tombstone",
        parent_source_path=(),
        parent_digest_before="",
        parent_digest_after="",
        unaffected_neighbor_paths=(),
        unaffected_neighbor_digests_before=(),
        unaffected_neighbor_digests_after=(),
        neighbors_unchanged=neighbors_unchanged,
        oracle_version_id="v1",
        oracle_target_present=oracle_present,
        oracle_target_occupancy=occupancy,
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
    )


def _agree_report(work_id: str, *, op_ids: tuple[str, ...]) -> NZDryRunReport:
    return NZDryRunReport(
        work_id=work_id,
        operation_family="repeal",
        proofs=tuple(
            _proof(
                op_id=op_id,
                oracle_match="agrees",
                oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            )
            for op_id in op_ids
        ),
        refusals=(),
        preflight_status="ready_for_dry_run_replay",
    )


def test_corpus_aggregates_agreement_rate_across_multiple_works() -> None:
    # Two ready works: one fully agreeing, one with a residual.
    work_a = _agree_report("act_public_2010_1", op_ids=("op-a1", "op-a2"))
    work_b = NZDryRunReport(
        work_id="act_public_2011_2",
        operation_family="repeal",
        proofs=(
            _proof(
                op_id="op-b1",
                oracle_match="agrees",
                oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            ),
            _proof(
                op_id="op-b2",
                oracle_match="target_missing",
                oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
                oracle_present=False,
                occupancy="absent",
            ),
        ),
        refusals=(),
        preflight_status="ready_for_dry_run_replay",
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(work_a, work_b),
        selected_work_ids=("act_public_2010_1", "act_public_2011_2"),
        available_work_count=1577,
        max_works=2,
    )

    summary = report.summary()
    assert summary["works_attempted"] == 2
    assert summary["works_with_ready_preflight"] == 2
    assert summary["works_with_dry_run_proofs"] == 2
    assert summary["total_repeal_ops_dry_run"] == 4
    assert summary["dry_run_oracle_agreements"] == 3
    assert summary["dry_run_oracle_residuals"] == 1
    assert summary["dry_run_oracle_agreement_rate"] == pytest.approx(3 / 4)
    assert summary["neighbors_unchanged_all"] is True
    # Never authorizes actual replay.
    assert summary["replay_claims"] is False
    assert summary["actual_replay_agreements"] == 0
    assert summary["dry_run_claims"] is True
    assert summary["oracle_match_family_counts"] == {"agrees": 3, "target_missing": 1}
    assert summary["residual_oracle_match_family_counts"] == {"target_missing": 1}
    assert summary["residual_family_exemplars"]["target_missing"] == ["act_public_2011_2:op-b2"]


def test_corpus_residual_family_tallying_separates_families() -> None:
    work = NZDryRunReport(
        work_id="act_public_2012_3",
        operation_family="repeal",
        proofs=(
            _proof(
                op_id="op-1",
                oracle_match="target_missing",
                oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
                oracle_present=False,
                occupancy="absent",
            ),
            _proof(
                op_id="op-2",
                oracle_match="target_not_tombstone",
                oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
                occupancy="substantive",
            ),
            _proof(
                op_id="op-3",
                oracle_match="agrees",
                oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            ),
        ),
        refusals=(),
        preflight_status="ready_for_dry_run_replay",
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(work,),
        selected_work_ids=("act_public_2012_3",),
        available_work_count=1,
    )
    summary = report.summary()
    assert summary["residual_oracle_match_family_counts"] == {
        "target_missing": 1,
        "target_not_tombstone": 1,
    }
    assert summary["dry_run_oracle_residuals"] == 2
    assert summary["dry_run_oracle_agreements"] == 1
    # Each family carries its rule id and an exemplar.
    assert summary["oracle_match_family_rule_ids"]["target_not_tombstone"] == [
        NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID
    ]
    assert summary["residual_family_exemplars"]["target_not_tombstone"] == ["act_public_2012_3:op-2"]


def test_corpus_refusal_tallying_by_rule_id() -> None:
    # A non-ready work yields a whole-set refusal; another work refuses one op
    # for a missing version window. Refusals tally by rule_id with exemplars.
    not_ready = NZDryRunReport(
        work_id="act_public_2013_4",
        operation_family="repeal",
        proofs=(),
        refusals=(
            NZDryRunRefusal(
                op_id="act_public_2013_4",
                rule_id=NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
                message="not ready",
            ),
        ),
        preflight_status="blocked_incomplete_candidate_set",
    )
    missing_window = NZDryRunReport(
        work_id="act_public_2014_5",
        operation_family="repeal",
        proofs=(),
        refusals=(
            NZDryRunRefusal(
                op_id="op-x",
                rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
                message="missing window",
            ),
        ),
        preflight_status="ready_for_dry_run_replay",
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(not_ready, missing_window),
        selected_work_ids=("act_public_2013_4", "act_public_2014_5"),
        available_work_count=2,
    )
    summary = report.summary()
    assert summary["works_attempted"] == 2
    assert summary["works_with_ready_preflight"] == 1
    assert summary["total_repeal_ops_dry_run"] == 0
    assert summary["dry_run_oracle_agreement_rate"] is None
    assert summary["refusal_rule_counts"] == {
        NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID: 1,
        NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID: 1,
    }
    assert summary["refusal_rule_exemplars"][NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID] == [
        "act_public_2013_4:act_public_2013_4"
    ]
    assert summary["preflight_status_counts"] == {
        "blocked_incomplete_candidate_set": 1,
        "ready_for_dry_run_replay": 1,
    }


def test_corpus_discloses_cap_truncation() -> None:
    # The cap bit: selected_work_count < available_work_count under --max-works.
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(_agree_report("act_public_2015_6", op_ids=("op-1",)),),
        selected_work_ids=("act_public_2015_6",),
        available_work_count=1577,
        max_works=1,
    )
    context = report.selection_context()
    assert context["available_work_count"] == 1577
    assert context["selected_work_count"] == 1
    assert context["max_works"] == 1
    assert context["truncated_by_max_works"] is True

    # No cap: not truncated.
    full = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(_agree_report("act_public_2016_7", op_ids=("op-1",)),),
        selected_work_ids=("act_public_2016_7",),
        available_work_count=1,
        max_works=None,
    )
    assert full.selection_context()["truncated_by_max_works"] is False


def test_corpus_exemplar_cap_is_bounded() -> None:
    # More than the exemplar limit of agreeing ops: count is exact, exemplars capped.
    work = _agree_report(
        "act_public_2017_8",
        op_ids=tuple(f"op-{index}" for index in range(8)),
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(work,),
        selected_work_ids=("act_public_2017_8",),
        available_work_count=1,
    )
    summary = report.summary()
    assert summary["oracle_match_family_counts"]["agrees"] == 8
    assert len(summary["oracle_match_family_exemplars"]["agrees"]) == 5


def _selected_family_completeness(
    *,
    total_repeal: int,
    repeal_in_scope: int,
    repeal_not_in_scope: dict[str, int],
) -> NZDryRunScopeCompleteness:
    return NZDryRunScopeCompleteness(
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        family="repeal",
        total_operation_witnesses=total_repeal,
        in_scope_operation_witnesses=repeal_in_scope,
        not_in_scope_operation_witnesses=total_repeal - repeal_in_scope,
        not_in_scope_reason_counts=dict(repeal_not_in_scope),
        total_repeal_operation_witnesses=total_repeal,
        repeal_witnesses_in_scope=repeal_in_scope,
        repeal_witnesses_not_in_scope_reason_counts=dict(repeal_not_in_scope),
    )


def test_complete_set_scope_reports_no_repeal_witness_census() -> None:
    # Without per-work census (complete_set scope), the loop fraction is
    # unavailable rather than misleadingly computed from proofs alone.
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(_agree_report("act_public_2010_1", op_ids=("op-1",)),),
        selected_work_ids=("act_public_2010_1",),
        available_work_count=1,
        scope=NZ_DRY_RUN_SCOPE_COMPLETE_SET,
    )
    coverage = report.summary()["repeal_witness_coverage"]
    assert coverage["census_available"] is False
    assert coverage["dry_run_agreeing_fraction"] is None
    assert report.summary()["scope"] == NZ_DRY_RUN_SCOPE_COMPLETE_SET


def test_selected_family_scope_reports_repeal_witness_coverage_scoreboard() -> None:
    # Work A: 3 repeal witnesses, 1 dry-run-agreeing, 2 blocked not-in-scope.
    work_a = NZDryRunReport(
        work_id="act_public_2010_1",
        operation_family="repeal",
        proofs=(
            _proof(
                op_id="op-a1",
                oracle_match="agrees",
                oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
            ),
        ),
        refusals=(),
        preflight_status="blocked_incomplete_candidate_set",
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        scope_completeness=_selected_family_completeness(
            total_repeal=3,
            repeal_in_scope=1,
            repeal_not_in_scope={NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 2},
        ),
    )
    # Work B: 2 repeal witnesses, 1 residual, 1 per-op refused.
    work_b = NZDryRunReport(
        work_id="act_public_2011_2",
        operation_family="repeal",
        proofs=(
            _proof(
                op_id="op-b1",
                oracle_match="target_not_tombstone",
                oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
                occupancy="substantive",
            ),
        ),
        refusals=(
            NZDryRunRefusal(
                op_id="op-b2",
                rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
                message="missing window",
            ),
        ),
        preflight_status="blocked_incomplete_candidate_set",
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        scope_completeness=_selected_family_completeness(
            total_repeal=2,
            repeal_in_scope=2,
            repeal_not_in_scope={},
        ),
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(work_a, work_b),
        selected_work_ids=("act_public_2010_1", "act_public_2011_2"),
        available_work_count=2,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )
    summary = report.summary()
    # Even though no work has ready preflight, the partial scope still produced
    # proofs from the ready repeal candidates inside incomplete works.
    assert summary["works_with_ready_preflight"] == 0
    assert summary["works_with_dry_run_proofs"] == 2

    coverage = summary["repeal_witness_coverage"]
    assert coverage["census_available"] is True
    # 5 repeal witnesses total: 1 agree + 1 residual + 1 refused + 2 blocked.
    assert coverage["total_repeal_operation_witnesses"] == 5
    assert coverage["dry_run_agreeing"] == 1
    assert coverage["dry_run_residual"] == 1
    assert coverage["per_op_refused"] == 1
    assert coverage["not_in_scope"] == 2
    assert coverage["blocked_or_unready"] == 0
    assert coverage["not_in_scope_reason_counts"] == {
        NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 2
    }
    # The loop metric: dry-run-agreeing fraction of all repeal witnesses.
    assert coverage["dry_run_agreeing_fraction"] == pytest.approx(1 / 5)


def test_repeal_witness_coverage_excludes_whole_work_no_candidate_refusal() -> None:
    # A work with no replayable repeal emits a synthetic whole-work refusal
    # keyed by the work id; it must not be subtracted from the denominator.
    work = NZDryRunReport(
        work_id="act_public_2012_3",
        operation_family="repeal",
        proofs=(),
        refusals=(
            NZDryRunRefusal(
                op_id="act_public_2012_3",
                rule_id=NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
                message="no replayable repeal candidate",
            ),
        ),
        preflight_status="blocked_incomplete_candidate_set",
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
        scope_completeness=_selected_family_completeness(
            total_repeal=4,
            repeal_in_scope=0,
            repeal_not_in_scope={NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 4},
        ),
    )
    report = NZDryRunRepealCorpusReport(
        db_path="data/nz_legislation.farchive",
        work_reports=(work,),
        selected_work_ids=("act_public_2012_3",),
        available_work_count=1,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )
    coverage = report.summary()["repeal_witness_coverage"]
    assert coverage["per_op_refused"] == 0
    assert coverage["not_in_scope"] == 4
    assert coverage["total_repeal_operation_witnesses"] == 4
    assert coverage["blocked_or_unready"] == 0
    assert coverage["dry_run_agreeing_fraction"] == pytest.approx(0.0)


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_corpus_builder_runs_over_real_canary_and_neighbour() -> None:
    # The hand-picked canary plus a sampled modern act, run end to end against
    # the archive. The canary must contribute agreeing repeal proofs.
    report = build_nz_dry_run_repeal_corpus_report(
        _REAL_DB,
        work_ids=("act_public_2005_87",),
    )
    summary = report.summary()
    assert summary["works_attempted"] == 1
    assert summary["works_with_ready_preflight"] == 1
    assert summary["total_repeal_ops_dry_run"] >= 1
    assert summary["dry_run_oracle_agreements"] == summary["total_repeal_ops_dry_run"]
    assert summary["dry_run_oracle_agreement_rate"] == pytest.approx(1.0)
    assert summary["replay_claims"] is False
    assert summary["neighbors_unchanged_all"] is True


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_corpus_selected_family_scope_unblocks_incomplete_work() -> None:
    # act_public_1955_37 is blocked_incomplete_candidate_set, so complete_set
    # scope yields zero proofs. Selected-family scope dry-runs its ready repeal
    # candidates and publishes a honest repeal-witness coverage scoreboard.
    work_id = "act_public_1955_37"
    complete = build_nz_dry_run_repeal_corpus_report(_REAL_DB, work_ids=(work_id,))
    complete_summary = complete.summary()
    assert complete_summary["works_with_ready_preflight"] == 0
    assert complete_summary["total_repeal_ops_dry_run"] == 0

    partial = build_nz_dry_run_repeal_corpus_report(
        _REAL_DB,
        work_ids=(work_id,),
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )
    summary = partial.summary()
    assert summary["scope"] == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
    assert summary["works_with_ready_preflight"] == 0  # still incomplete
    assert summary["works_with_dry_run_proofs"] == 1
    assert summary["total_repeal_ops_dry_run"] >= 1

    coverage = summary["repeal_witness_coverage"]
    assert coverage["census_available"] is True
    # The denominator is every repeal witness in the work, and the parts add up.
    assert coverage["total_repeal_operation_witnesses"] == (
        coverage["dry_run_agreeing"]
        + coverage["dry_run_residual"]
        + coverage["per_op_refused"]
        + coverage["not_in_scope"]
        + coverage["blocked_or_unready"]
    )
    assert coverage["dry_run_agreeing_fraction"] is not None
    # Replay is never authorized by the relaxed scope.
    assert summary["replay_claims"] is False


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_corpus_run_cache_produces_identical_report_to_uncached_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The run-scoped parse/archive cache is a pure performance layer: the corpus
    # report built with the cache active must be byte-identical (same JSON) to the
    # report built with the cache disabled.
    import json as _json
    from contextlib import contextmanager

    import lawvm.new_zealand.corpus_cache as cache_mod

    work_ids = ("act_public_1871_23", "act_public_1872_13", "act_public_2005_87")
    cached = build_nz_dry_run_repeal_corpus_report(
        _REAL_DB, work_ids=work_ids, scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
    )

    @contextmanager
    def _noop_cache():  # type: ignore[no-untyped-def]
        yield None

    monkeypatch.setattr(cache_mod, "corpus_run_cache", _noop_cache)
    monkeypatch.setattr(cache_mod, "active_corpus_run_cache", lambda: None)
    import lawvm.new_zealand.dry_run_corpus as dc_mod

    monkeypatch.setattr(dc_mod, "corpus_run_cache", _noop_cache)
    monkeypatch.setattr(dc_mod, "active_corpus_run_cache", lambda: None)

    uncached = build_nz_dry_run_repeal_corpus_report(
        _REAL_DB, work_ids=work_ids, scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
    )

    assert _json.dumps(cached.to_jsonable(), ensure_ascii=False, sort_keys=True) == _json.dumps(
        uncached.to_jsonable(), ensure_ascii=False, sort_keys=True
    )
