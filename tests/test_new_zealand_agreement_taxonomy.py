"""Phase-5 agreement-residual taxonomy tests.

Covers (1) every standalone-comparator node status typed into a core family,
(2) the actual-replay refusal lane typed with a source-honest-vs-replay-bug
split, and (3) the ``--from-actual-replay`` feed that consumes ACTUAL replay
output (the materialized after-tree) instead of a hand-picked candidate XML.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
    NZActualReplayRefusal,
    _refusal_residual,
)
from lawvm.new_zealand.agreement import (
    NZAgreementRow,
    compare_actual_replay_to_oracle,
    compare_source_documents,
)
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
    NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID,
    NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
)
from lawvm.new_zealand.dry_run_oracle import (
    classify_comparator_status_family,
    classify_refusal_family,
)
from lawvm.new_zealand.source_tree import parse_nz_source_document


_WORK_ID = "act_public_1992_122"
_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


# --- 1. Comparator-status typing -------------------------------------------


def test_comparator_status_family_types_every_status() -> None:
    assert classify_comparator_status_family("exact") == "agreement"
    assert classify_comparator_status_family("changed") == "non_commensurable_surface"
    assert classify_comparator_status_family("oracle_only") == "topology_granularity_mismatch"
    assert classify_comparator_status_family("candidate_only") == "topology_granularity_mismatch"
    assert classify_comparator_status_family("text_exact_identity_drift") == "oracle_editorial_pathology"
    assert classify_comparator_status_family("text_exact_history_drift") == "oracle_editorial_pathology"


def test_comparator_status_family_unknown_is_explicit() -> None:
    # A status the map does not know is loudly ``unknown``, never silently agreement.
    assert classify_comparator_status_family("brand_new_status") == "unknown"
    assert classify_comparator_status_family("") == "unknown"


def test_compare_source_documents_surface_types_every_row() -> None:
    candidate_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Same</text></para></prov.body></prov>
    <prov id="S2"><label>2</label><heading>Changed</heading><prov.body><para><text>Candidate</text></para></prov.body></prov>
    <prov id="S3"><label>3</label><heading>Candidate only</heading></prov>
  </body>
</act>
"""
    oracle_xml = b"""\
<act>
  <body>
    <prov id="S1O"><label>1</label><heading>Title</heading><prov.body><para><text>Same</text></para></prov.body></prov>
    <prov id="S2O"><label>2</label><heading>Changed</heading><prov.body><para><text>Oracle</text></para></prov.body></prov>
    <prov id="S4O"><label>4</label><heading>Oracle only</heading></prov>
  </body>
</act>
"""

    report = compare_source_documents(
        parse_nz_source_document(candidate_xml, xml_locator="candidate", version_id="cand-v1"),
        parse_nz_source_document(oracle_xml, xml_locator="oracle", version_id="ora-v1"),
    )
    surface = report.agreement_surface()

    # Every row is typed; no row is left without a family.
    family_counts: dict[str, int] = {}
    for residual in surface.residuals:
        family_counts[residual.family] = family_counts.get(residual.family, 0) + 1
    assert family_counts == {
        "oracle_editorial_pathology": 1,  # text_exact_identity_drift (label 1)
        "non_commensurable_surface": 1,  # changed (label 2)
        "topology_granularity_mismatch": 2,  # candidate_only + oracle_only
    }
    # The standalone comparator never applies an op, so it never produces a bug.
    assert all(residual.family != "replay_bug" for residual in surface.residuals)
    assert surface.materialization_kind == "unknown"
    jsonable = report.to_jsonable()
    assert jsonable["residual_family_counts"]["non_commensurable_surface"] == 1
    assert jsonable["agreement_surface"]["residuals"]


# --- 2. Refusal-lane typing: source-honest vs replay bug --------------------


def test_refusal_family_replay_bug_is_kernel_divergence() -> None:
    # A dry-run proof that did not agree (a materialized candidate disagreed with
    # the oracle), and a composited slice divergence, are genuine replay bugs.
    assert (
        classify_refusal_family(refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID)
        == "replay_bug"
    )
    assert (
        classify_refusal_family(
            refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID
        )
        == "replay_bug"
    )


def test_refusal_family_source_honest_is_frontier_not_bug() -> None:
    # A target the source recovered (not exact) or a source-change-only payload
    # was correctly declined before any mutation: source-honest, NOT a replay bug.
    for dry_rule in (
        NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
        NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID,
    ):
        family = classify_refusal_family(
            refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
            dry_run_refusal_rule_id=dry_rule,
        )
        assert family == "accepted_non_executable_frontier"
    # A structural family with no operation surface is also source-honest.
    assert (
        classify_refusal_family(refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID)
        == "accepted_non_executable_frontier"
    )


def test_refusal_family_temporal_and_footing_distinct() -> None:
    assert (
        classify_refusal_family(refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_MISSING_VERSION_WINDOW_RULE_ID)
        == "temporal_mismatch"
    )
    assert (
        classify_refusal_family(
            refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
            dry_run_refusal_rule_id=NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
        )
        == "temporal_mismatch"
    )
    assert (
        classify_refusal_family(refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID)
        == "source_footing_gap"
    )
    assert (
        classify_refusal_family(refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_ORACLE_XML_UNREADABLE_RULE_ID)
        == "source_footing_gap"
    )
    assert (
        classify_refusal_family(
            refusal_rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
            dry_run_refusal_rule_id=NZ_DRY_RUN_REFUSED_BEFORE_XML_UNREADABLE_RULE_ID,
        )
        == "source_footing_gap"
    )


def test_refusal_residual_status_tracks_family() -> None:
    bug = _refusal_residual(
        _WORK_ID,
        0,
        NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
            message="proof did not agree",
            amendment_date_iso="2010-08-06",
            op_ids=("nz:work:row:repeal",),
            detail={"oracle_match": "target_not_tombstone_in_oracle"},
        ),
        classify_refusal_family,
    )
    assert bug.family == "replay_bug"
    assert bug.agreement_residual_status == "residual"

    frontier = _refusal_residual(
        _WORK_ID,
        1,
        NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
            message="declined by dry run",
            op_ids=("nz:work:row:replace",),
            detail={"dry_run_refusal_rule_id": NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID},
        ),
        classify_refusal_family,
    )
    assert frontier.family == "accepted_non_executable_frontier"
    assert frontier.agreement_residual_status == "frontier"
    assert frontier.missing_proofs == (NZ_DRY_RUN_REFUSED_SOURCE_CHANGE_ONLY_RULE_ID,)


def test_classify_refusal_family_is_total_over_declared_refusal_reasons() -> None:
    """TOTALITY: every declared actual-replay refusal reason is explicitly typed.

    The classifier is fail-CLOSED — an UNMAPPED rule id (a new refusal constant
    nobody typed) resolves to the CTSF-failing ``unknown`` family, never a silent
    benign default. This test enumerates every ``NZ_ACTUAL_REPLAY_REFUSED_*``
    constant declared on the actual-replay module (plus the carried family-level
    receipt) and proves each has an explicit, non-``unknown`` typing, so adding a
    new refusal reason without typing it in ``classify_refusal_family`` FAILS red.
    """
    import lawvm.new_zealand.actual_replay as ar

    declared = {
        name: getattr(ar, name)
        for name in dir(ar)
        if name.startswith("NZ_ACTUAL_REPLAY_REFUSED_") and name.endswith("_RULE_ID")
    }
    # Sanity: the module actually declares the refusal vocabulary we sweep.
    assert declared, "no NZ_ACTUAL_REPLAY_REFUSED_* rule-id constants found"

    for name, rule_id in sorted(declared.items()):
        family = classify_refusal_family(refusal_rule_id=rule_id)
        assert family != "unknown", (
            f"declared refusal reason {name} is not typed by classify_refusal_family "
            f"(fell through to the fail-closed 'unknown' family) — add an explicit "
            f"mapping"
        )

    # The carried family-level dry-run refusal receipt also reaches the classifier
    # and must be typed (never 'unknown').
    assert (
        classify_refusal_family(
            refusal_rule_id=ar.NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID
        )
        != "unknown"
    )

    # BITE: an unmapped / bogus rule id fails CLOSED to the CTSF-failing family.
    assert (
        classify_refusal_family(refusal_rule_id="nz_actual_replay_refused_totally_new_unmapped_reason")
        == "unknown"
    )
    assert classify_refusal_family(refusal_rule_id="") == "unknown"


# --- 3. ``--from-actual-replay`` feed on the real canary --------------------


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_from_actual_replay_feeds_materialized_after_tree_against_oracle() -> None:
    report = compare_actual_replay_to_oracle(db_path=_REAL_DB, work_id=_WORK_ID)
    summary = report.summary()

    # The agreement consumes ACTUAL replay output: one comparison per replayed
    # transition, each candidate side being a materialized after-tree.
    assert summary["transitions_compared"] >= 1
    assert len(report.transitions) == summary["transitions_compared"]

    # Every comparator row across every transition is typed, and the materialized
    # after-tree agrees with the oracle on the overwhelming majority of nodes
    # (the divergences are the unapplied non-promotable ops, source-honest).
    transition_families = summary["transition_residual_family_counts"]
    assert transition_families.get("agreement", 0) > 0
    assert all(family != "" for family in transition_families)
    for transition in report.transitions:
        for residual in transition.report.agreement_residuals():
            assert residual.family  # no untyped row
        # This comparator never applies an op, so it never emits a replay bug.
        assert "replay_bug" not in {r.family for r in transition.report.agreement_residuals()}


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_from_actual_replay_refusal_lane_splits_source_honest_from_replay_bug() -> None:
    report = compare_actual_replay_to_oracle(db_path=_REAL_DB, work_id=_WORK_ID)
    refusal_families = report.summary()["refusal_residual_family_counts"]

    # The canary exercises both lanes: genuine replay bugs (a materialized
    # candidate disagreed with the oracle) AND source-honest declines.
    assert refusal_families.get("replay_bug", 0) > 0
    assert refusal_families.get("accepted_non_executable_frontier", 0) > 0
    # Source-honest disagreement is a DISTINCT family from a replay bug.
    assert "replay_bug" != "accepted_non_executable_frontier"
    # Every refusal residual carries a typed family.
    assert all(row["family"] for row in report.refusal_residuals)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_from_actual_replay_report_is_jsonable_and_claims_replay() -> None:
    report = compare_actual_replay_to_oracle(db_path=_REAL_DB, work_id=_WORK_ID)
    jsonable = report.to_jsonable()
    assert jsonable["report_kind"] == "actual_replay_materialized_after_vs_oracle_agreement"
    assert jsonable["replay_claims"] is True
    assert jsonable["transitions"]
    # The refusal residuals are queryable (Phase-8 will count by family).
    assert jsonable["refusal_residuals"]
    families = {row["family"] for row in jsonable["refusal_residuals"]}
    assert "replay_bug" in families
    assert "accepted_non_executable_frontier" in families


def test_agreement_row_surface_handles_empty_report() -> None:
    # Degenerate empty comparison still produces a valid (empty) typed surface.
    from lawvm.new_zealand.agreement import NZAgreementReport

    empty = NZAgreementReport(
        candidate_version_id="c",
        oracle_version_id="o",
        candidate_xml_locator="c.xml",
        oracle_xml_locator="o.xml",
        rows=(NZAgreementRow(path=("prov:1",), agreement_status="exact"),),
    )
    surface = empty.agreement_surface()
    assert len(surface.residuals) == 1
    assert surface.residuals[0].family == "agreement"
    assert surface.residuals[0].agreement_residual_status == "agrees"
