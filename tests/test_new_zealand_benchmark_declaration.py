"""Tests for the NZ Phase 8 benchmark declaration ladder + replay-status unification.

Two lanes:

* Pure unit tests over :func:`compute_nz_declaration` — no farchive needed. They
  pin the cumulative 5-rung ladder, and crucially the *boundary* where a rung is
  NOT met (so a higher rung can never be silently claimed) and where the deeper
  rungs are ``not_evaluated`` rather than passed when their lanes were not run.
* A unification test over ``build_nz_benchmark_report`` proving the per-work
  replay/oracle-agreement lanes are sourced from the real actual-replay summary
  (not the stale hardcoded "blocked" status) when ``include_actual_replay`` runs,
  and report ``not_evaluated`` (never a false "blocked") when it does not.
"""

from __future__ import annotations

from typing import Any

from lawvm.new_zealand.benchmark import (
    NZ_DECLARATION_CANDIDATE_COMPLETE,
    NZ_DECLARATION_DRY_RUN_COMPLETE,
    NZ_DECLARATION_INCOMPLETE,
    NZ_DECLARATION_JURISDICTION_COMPLETE,
    NZ_DECLARATION_REPLAY_COMPLETE,
    NZ_DECLARATION_SOURCE_COMPLETE,
    compute_nz_declaration,
)


def _work(
    *,
    work_id: str = "act_public_2020_1",
    source_status: str = "parsed",
    operation_witness_rows: int = 1,
    candidate_status_counts: dict[str, int] | None = None,
    effect_preflight_status: str = "ready_for_dry_run_replay",
    replay_status: str = "replayed",
    oracle_agreement_status: str = "agreement_by_residual_family",
    oracle_agreement_residual_family_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    counts = candidate_status_counts if candidate_status_counts is not None else {"candidate_emitted": 1}
    return {
        "work_id": work_id,
        "source_status": source_status,
        "dependency_diagnostics": 0,
        "operation_witness_rows": operation_witness_rows,
        "candidate_status_counts": counts,
        "candidate_rows": sum(counts.values()),
        "effect_preflight_status": effect_preflight_status,
        "effect_preflight_replayable_candidate_operations": 1,
        "replay_status": replay_status,
        "oracle_agreement_status": oracle_agreement_status,
        "oracle_agreement_residual_family_counts": (
            oracle_agreement_residual_family_counts
            if oracle_agreement_residual_family_counts is not None
            else {"agreement": 1}
        ),
    }


def _decl(per_work: tuple[dict[str, Any], ...], **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("include_payloads", True)
    kwargs.setdefault("include_actual_replay", True)
    return compute_nz_declaration(per_work, **kwargs)


# --- full ladder ------------------------------------------------------------


def test_clean_slice_reaches_jurisdiction_complete() -> None:
    decl = _decl((_work(),))
    assert decl["declaration_level"] == NZ_DECLARATION_JURISDICTION_COMPLETE
    assert all(decl["rungs"][level]["met"] for level in decl["ladder"])


def test_empty_slice_is_incomplete() -> None:
    decl = _decl(())
    assert decl["declaration_level"] == NZ_DECLARATION_INCOMPLETE
    assert decl["rungs"][NZ_DECLARATION_SOURCE_COMPLETE]["met"] is False


# --- per-rung NOT-met boundaries -------------------------------------------


def test_source_not_parsed_caps_at_incomplete() -> None:
    decl = _decl((_work(source_status="parse_error"),))
    assert decl["declaration_level"] == NZ_DECLARATION_INCOMPLETE
    rung = decl["rungs"][NZ_DECLARATION_SOURCE_COMPLETE]
    assert rung["met"] is False
    assert rung["source_not_parsed"] == 1
    # A failed lower rung short-circuits the cumulative climb: candidate stays unmet.
    assert decl["rungs"][NZ_DECLARATION_CANDIDATE_COMPLETE]["met"] is False


def test_uncovered_operation_witness_caps_at_source_complete() -> None:
    # 3 operation witnesses but only 1 candidate row -> 2 uncovered.
    decl = _decl(
        (
            _work(operation_witness_rows=3, candidate_status_counts={"candidate_emitted": 1}),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_SOURCE_COMPLETE
    rung = decl["rungs"][NZ_DECLARATION_CANDIDATE_COMPLETE]
    assert rung["met"] is False
    assert rung["operation_witness_uncovered_works"] == 1


def test_untyped_candidate_status_caps_at_source_complete() -> None:
    decl = _decl(
        (
            _work(candidate_status_counts={"some_unexpected_status": 1}),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_SOURCE_COMPLETE
    assert decl["rungs"][NZ_DECLARATION_CANDIDATE_COMPLETE]["met"] is False


def test_blocked_candidate_set_caps_at_candidate_complete() -> None:
    # Candidate-complete holds (every op witness is a typed blocked frontier row),
    # but the candidate set is preflight-blocked from replay and nothing replayed:
    # there is a replay-authorizable candidate with no dry-run proof.
    decl = _decl(
        (
            _work(
                candidate_status_counts={"blocked": 1},
                effect_preflight_status="blocked_incomplete_candidate_set",
                replay_status="no_declared_transitions",
                oracle_agreement_status="no_declared_transitions",
                oracle_agreement_residual_family_counts={},
            ),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_CANDIDATE_COMPLETE
    dry = decl["rungs"][NZ_DECLARATION_DRY_RUN_COMPLETE]
    assert dry["met"] is False
    assert dry["preflight_unproven_candidate_works"] == 1


def test_fail_closed_replay_caps_at_candidate_complete() -> None:
    # A declared transition was fail-closed-refused -> no dry-run proof held.
    decl = _decl(
        (
            _work(replay_status="blocked", oracle_agreement_status="blocked_no_candidate_replay"),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_CANDIDATE_COMPLETE
    dry = decl["rungs"][NZ_DECLARATION_DRY_RUN_COMPLETE]
    assert dry["met"] is False
    assert dry["replay_blocked_works"] == 1


def test_untyped_residual_caps_at_replay_complete() -> None:
    # Everything replayed (dry-run + replay rungs hold) but a replay_bug residual
    # remains -> jurisdiction-complete is NOT met; the level caps at replay-complete.
    decl = _decl(
        (
            _work(oracle_agreement_residual_family_counts={"agreement": 5, "replay_bug": 2}),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_REPLAY_COMPLETE
    juris = decl["rungs"][NZ_DECLARATION_JURISDICTION_COMPLETE]
    assert juris["met"] is False
    assert juris["untyped_residual_works"] == 1
    assert juris["untyped_residual_family_counts"] == {"replay_bug": 2}


def test_typed_frontier_residuals_do_not_block_jurisdiction_complete() -> None:
    # Accepted typed frontiers / temporal mismatches are typed residuals, not
    # untyped crashes — they MUST NOT block jurisdiction-complete.
    decl = _decl(
        (
            _work(
                oracle_agreement_residual_family_counts={
                    "agreement": 3,
                    "accepted_non_executable_frontier": 4,
                    "temporal_mismatch": 1,
                }
            ),
        )
    )
    assert decl["declaration_level"] == NZ_DECLARATION_JURISDICTION_COMPLETE


# --- lanes not run -> not_evaluated, never silently passed ------------------


def test_payload_lane_absent_makes_candidate_not_evaluated() -> None:
    decl = compute_nz_declaration(
        (_work(),),
        include_payloads=False,
        include_actual_replay=False,
    )
    assert decl["declaration_level"] == NZ_DECLARATION_SOURCE_COMPLETE
    cand = decl["rungs"][NZ_DECLARATION_CANDIDATE_COMPLETE]
    assert cand["met"] is False
    assert "not_evaluated" in cand["reason"]


def test_replay_lane_absent_makes_replay_rungs_not_evaluated() -> None:
    decl = compute_nz_declaration(
        (_work(),),
        include_payloads=True,
        include_actual_replay=False,
    )
    assert decl["declaration_level"] == NZ_DECLARATION_CANDIDATE_COMPLETE
    for level in (
        NZ_DECLARATION_DRY_RUN_COMPLETE,
        NZ_DECLARATION_REPLAY_COMPLETE,
        NZ_DECLARATION_JURISDICTION_COMPLETE,
    ):
        rung = decl["rungs"][level]
        assert rung["met"] is False
        assert "not_evaluated" in rung["reason"]


# --- benchmark.py replay-status unification ---------------------------------


class _FakeArchive:
    def __init__(self) -> None:
        self.rows: dict[str, bytes] = {}

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self.rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        return sorted(self.rows)

    def close(self) -> None:
        return None


def test_replay_status_fields_consume_real_actual_replay_summary() -> None:
    from lawvm.new_zealand.benchmark import _replay_status_fields

    # A summary with replayed transitions and a typed residual family is wired in
    # directly — the benchmark no longer hardcodes "blocked".
    replayed = _replay_status_fields(
        work_id="w1",
        latest_locator="loc",
        actual_replay_summary={
            "transitions_replayed": 2,
            "transitions_refused": 0,
            "target_slice_nodes": 4,
            "target_slice_agreements": 4,
            "residual_family_counts": {"agreement": 4},
        },
    )
    assert replayed["replay_status"] == "replayed"
    assert replayed["oracle_agreement_status"] == "agreement_by_residual_family"
    assert replayed["oracle_agreement_residual_family_counts"] == {"agreement": 4}
    assert replayed["oracle_agreement_exact_ratio"] == 1.0
    assert replayed["findings"] == ()

    # All declared transitions refused -> honestly blocked (not a hardcoded const).
    blocked = _replay_status_fields(
        work_id="w1",
        latest_locator="loc",
        actual_replay_summary={
            "transitions_replayed": 0,
            "transitions_refused": 5,
            "target_slice_nodes": 0,
            "target_slice_agreements": 0,
            "residual_family_counts": {"replay_bug": 5},
        },
    )
    assert blocked["replay_status"] == "blocked"
    assert blocked["oracle_agreement_status"] == "blocked_no_candidate_replay"
    assert [f["rule_id"] for f in blocked["findings"]] == [
        "nz_oracle_agreement_candidate_replay_missing"
    ]

    # No summary -> not_evaluated, never the stale "blocked" claim.
    absent = _replay_status_fields(work_id="w1", latest_locator="loc", actual_replay_summary=None)
    assert absent["replay_status"] == "not_evaluated"
    assert absent["oracle_agreement_status"] == "not_evaluated"
    assert absent["findings"][0]["blocking"] is False
