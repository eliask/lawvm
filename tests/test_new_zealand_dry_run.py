from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS,
    NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY,
    NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID,
    NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID,
    NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
    NZ_DRY_RUN_SCOPE_COMPLETE_SET,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    build_dry_run_repeal,
    scope_from_arg,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZCanonicalEffectCandidateRow,
    build_effect_candidate_preflight,
)


_WORK_ID = "act_public_2005_87"
_BEFORE_VERSION = "act_public_2005_87_en_2017-04-19"
_AFTER_VERSION = "act_public_2005_87_en_2019-10-24"

# Section 108 substantive in before, repealed (tombstone) in on-or-after.
# Sibling section 109 stays substantive across both versions.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>No review</heading>
      <prov.body><para><text>Old section 108 text.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""

_AFTER_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status="repealed"><label>108</label><heading>No review</heading>
      <prov.body><para><text>Old section 108 text.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
  </body>
</act>
"""


class _FakeArchive:
    def __init__(self, rows: dict[str, bytes]) -> None:
        self.rows = rows

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self.rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        prefix = pattern[:-1] if pattern.endswith("%") else pattern
        return sorted(locator for locator in self.rows if locator.startswith(prefix))

    def close(self) -> None:
        pass


def _version_detail(version_id: str, date: str) -> bytes:
    return json.dumps(
        {
            "version_id": version_id,
            "formats": [
                {
                    "type": "xml",
                    "url": f"https://www.legislation.govt.nz/act/public/2005/87/en/{date}.xml",
                }
            ],
        }
    ).encode()


def _archive_with_before_after() -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, "2017-04-19"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2017-04-19.xml": _BEFORE_XML,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2019-10-24.xml": _AFTER_XML,
        }
    )


def _repeal_operation() -> LegalOperation:
    return LegalOperation(
        op_id=f"nz:{_WORK_ID}:nz-opw-1:repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "108"),)),
        payload=None,
        source=OperationSource(statute_id="act_public_2019_5", effective="2019-10-24"),
        provenance_tags=("new_zealand", "history_note", "candidate_only", "not_replayed"),
        witness_rule_id="nz_repeal_candidate_from_history_note_payload_witness",
    )


def _repeal_row(
    *,
    operation: LegalOperation | None = None,
    amendment_date_iso: str = "2019-10-24",
    target_resolution_status: str = "",
    status: str = "candidate_emitted",
) -> NZCanonicalEffectCandidateRow:
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-1",
        operation_row_id="nz-opw-1",
        effect_readiness_row_id="nz-readiness-1",
        status=status,
        action=str(StructuralAction.REPEAL),
        target_address="section:108",
        operation=operation if operation is not None else _repeal_operation(),
        source_path=("prov:108",),
        amendment_date_iso=amendment_date_iso,
        operation_family="repealed",
        repeal_payload_corroboration_status="not_required_non_direct_repeal_payload",
        latest_oracle_target_resolution_status=target_resolution_status,
    )


def _blocked_repeal_row() -> NZCanonicalEffectCandidateRow:
    # A repeal operation witness that did not reach a candidate (still blocked).
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-2",
        operation_row_id="nz-opw-2",
        effect_readiness_row_id="nz-readiness-2",
        status="blocked",
        target_address="section:200",
        blocking_rule_id="nz_effect_candidate_not_ready",
        operation_family="repealed",
    )


def _preflight_from_rows(rows: tuple[NZCanonicalEffectCandidateRow, ...]):
    report = NZCanonicalEffectCandidateReport(work_id=_WORK_ID, rows=rows)
    return build_effect_candidate_preflight(report)


def test_dry_run_repeal_tombstones_target_and_agrees_with_oracle() -> None:
    preflight = _preflight_from_rows((_repeal_row(),))
    assert preflight.summary()["preflight_status"] == "ready_for_dry_run_replay"

    archive = _archive_with_before_after()
    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    summary = report.summary()
    assert summary["operations_dry_run"] == 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True
    assert summary["replay_claims"] is False
    assert summary["actual_replay_agreements"] == 0
    assert summary["dry_run_claims"] is True

    assert len(report.proofs) == 1
    proof = report.proofs[0]
    # Boring kernel: substantive -> tombstone, addressability preserved.
    assert proof.occupancy_before == "substantive"
    assert proof.occupancy_after == "tombstone"
    assert proof.selected_source_path == ("prov:108",)
    assert proof.target_xml_id == "DLM360602"
    assert proof.target_digest_before != proof.target_digest_after
    # Unaffected-neighbour proof: sibling section 109 untouched.
    assert proof.unaffected_neighbor_paths == (("prov:109",),)
    assert proof.unaffected_neighbor_digests_before == proof.unaffected_neighbor_digests_after
    assert proof.neighbors_unchanged is True
    # Oracle partition: matched, on-or-after node is a tombstone.
    assert proof.oracle_match == "agrees"
    assert proof.oracle_match_rule_id == NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID
    assert proof.oracle_target_occupancy == "tombstone"
    assert proof.oracle_version_id == _AFTER_VERSION


def test_dry_run_repeal_reports_dry_run_agreement_surface_separately() -> None:
    preflight = _preflight_from_rows((_repeal_row(),))
    archive = _archive_with_before_after()
    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_dry_run_repeal"
    assert surface["comparison_kind"] == "dry_run_after_tree_vs_archived_on_or_after_xml"
    # Dry-run materialization is a proposed branch compared to an official view,
    # never an actual-replay or source-truth claim.
    assert surface["materialization_kind"] == "proposed_future_branch"
    assert surface["comparison_materialization_kind"] == "official_consolidation_view"
    assert all(
        "dry_run_agreement_as_replay_authorization" in residual["forbidden_shortcuts"]
        for residual in surface["residuals"]
    )
    assert surface["exact_ratio"] == 1.0
    assert [residual["family"] for residual in surface["residuals"]] == ["agreement"]

    payload = report.to_jsonable()
    assert payload["replay_claims"] is False
    assert payload["dry_run_claims"] is True


def test_dry_run_refuses_recovered_target() -> None:
    # Target resolved via a non-exact carrier must never mutate.
    row = _repeal_row(target_resolution_status="via_unlabeled_source_carrier")
    preflight = _preflight_from_rows((row,))
    archive = _archive_with_before_after()

    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    assert report.summary()["operations_dry_run"] == 0
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID
    assert report.proofs == ()


def test_dry_run_refuses_missing_version_window() -> None:
    # Amendment date has no archived before/after version bracket in the archive.
    preflight = _preflight_from_rows((_repeal_row(amendment_date_iso="2099-01-01"),))
    archive = _archive_with_before_after()

    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    assert report.summary()["operations_dry_run"] == 0
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_MISSING_VERSION_WINDOW_RULE_ID
    assert report.proofs == ()


def test_dry_run_refuses_when_preflight_not_ready() -> None:
    # A blocked sibling row makes the whole candidate set non-ready; refuse
    # without mutating anything (no per-op proof is produced).
    blocked = NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-2",
        operation_row_id="nz-opw-2",
        effect_readiness_row_id="nz-readiness-2",
        status="blocked",
        blocking_rule_id="nz_effect_candidate_not_ready",
    )
    preflight = _preflight_from_rows((_repeal_row(), blocked))
    assert preflight.summary()["preflight_status"] != "ready_for_dry_run_replay"

    archive = _archive_with_before_after()
    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID


def test_dry_run_refuses_when_no_repeal_candidate_present() -> None:
    # An emitted text-replace candidate is not a repeal; the surface refuses
    # rather than touching a non-repeal family.
    text_replace = NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-1",
        operation_row_id="nz-opw-1",
        effect_readiness_row_id="nz-readiness-1",
        status="candidate_emitted",
        action=str(StructuralAction.TEXT_REPLACE),
        target_address="section:108",
        operation=LegalOperation(
            op_id="nz:text:1",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "108"),)),
            text_patch=None,
            witness_rule_id="nz_text_replace_candidate_from_direct_instruction_workqueue",
        ),
        latest_oracle_target_resolution_status="exact_source_path",
    )
    preflight = _preflight_from_rows((text_replace,))

    archive = _archive_with_before_after()
    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID


def test_default_scope_is_complete_set_and_unchanged() -> None:
    # The default (no scope arg) must keep the original strict whole-work gate:
    # a blocked sibling refuses the whole work without any per-op proof.
    preflight = _preflight_from_rows((_repeal_row(), _blocked_repeal_row()))
    assert preflight.summary()["preflight_status"] != "ready_for_dry_run_replay"

    archive = _archive_with_before_after()
    report = build_dry_run_repeal(archive, work_id=_WORK_ID, preflight=preflight)

    assert report.scope == NZ_DRY_RUN_SCOPE_COMPLETE_SET
    assert report.scope_completeness is None
    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_PREFLIGHT_NOT_READY_RULE_ID
    summary = report.summary()
    assert summary["scope"] == NZ_DRY_RUN_SCOPE_COMPLETE_SET
    assert summary["scope_completeness"] is None


def test_selected_family_repeal_dry_runs_ready_repeal_despite_blocked_sibling() -> None:
    # Same incomplete work, partial scope: the ready repeal is dry-run and
    # agrees, while the blocked repeal witness is carried as not-in-scope.
    preflight = _preflight_from_rows((_repeal_row(), _blocked_repeal_row()))
    assert preflight.summary()["preflight_status"] != "ready_for_dry_run_replay"

    archive = _archive_with_before_after()
    report = build_dry_run_repeal(
        archive,
        work_id=_WORK_ID,
        preflight=preflight,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )

    # The whole-work gate is relaxed: the ready repeal is dry-run and agrees.
    summary = report.summary()
    assert report.scope == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
    assert summary["operations_dry_run"] == 1
    assert summary["dry_run_oracle_agreements"] == 1
    assert summary["operations_refused"] == 0
    assert report.proofs[0].oracle_match == "agrees"

    # Replay is still blocked; the relaxation is scope-only, never replay.
    assert summary["replay_claims"] is False
    assert summary["actual_replay_agreements"] == 0

    # The partial scope is declared honestly, with the not-in-scope blocked
    # repeal witness counted, never hidden.
    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.is_partial is True
    assert completeness.family == "repeal"
    assert completeness.in_scope_operation_witnesses == 1
    assert completeness.not_in_scope_operation_witnesses == 1
    assert completeness.not_in_scope_reason_counts == {
        NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 1
    }
    # Repeal-witness census: 2 repeal witnesses (1 dry-run, 1 blocked).
    assert completeness.total_repeal_operation_witnesses == 2
    assert completeness.repeal_witnesses_in_scope == 1
    assert completeness.repeal_witnesses_not_in_scope_reason_counts == {
        NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 1
    }
    # The disclosure round-trips into the summary and JSON.
    assert summary["scope_completeness"]["is_partial"] is True
    assert report.to_jsonable()["scope_completeness"]["not_in_scope_operation_witnesses"] == 1


def test_selected_family_repeal_does_not_relax_per_op_checks() -> None:
    # Partial scope only relaxes the WHOLE-WORK gate. A recovered (non-exact)
    # target still refuses per-op, even though the work would now be attempted.
    row = _repeal_row(target_resolution_status="via_unlabeled_source_carrier")
    preflight = _preflight_from_rows((row, _blocked_repeal_row()))
    archive = _archive_with_before_after()

    report = build_dry_run_repeal(
        archive,
        work_id=_WORK_ID,
        preflight=preflight,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )

    assert report.proofs == ()
    assert len(report.refusals) == 1
    assert report.refusals[0].rule_id == NZ_DRY_RUN_REFUSED_TARGET_RECOVERED_RULE_ID
    # The recovered repeal was in-scope (a ready candidate row) but refused
    # per-op; the blocked sibling is still carried as not-in-scope.
    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.repeal_witnesses_in_scope == 1
    assert completeness.repeal_witnesses_not_in_scope_reason_counts == {
        NZ_DRY_RUN_NOT_IN_SCOPE_BLOCKED_OPERATION_WITNESS: 1
    }


def test_selected_family_repeal_counts_non_repeal_family_as_not_in_scope() -> None:
    # A candidate text-replace alongside a ready repeal: the repeal is dry-run,
    # the text-replace is carried as not-in-scope (non-repeal family).
    text_replace = NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-3",
        operation_row_id="nz-opw-3",
        effect_readiness_row_id="nz-readiness-3",
        status="candidate_emitted",
        action=str(StructuralAction.TEXT_REPLACE),
        target_address="section:109",
        operation=LegalOperation(
            op_id="nz:text:1",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "109"),)),
            text_patch=None,
            witness_rule_id="nz_text_replace_candidate_from_direct_instruction_workqueue",
        ),
        operation_family="amended",
        latest_oracle_target_resolution_status="exact_source_path",
    )
    preflight = _preflight_from_rows((_repeal_row(), text_replace))
    archive = _archive_with_before_after()

    report = build_dry_run_repeal(
        archive,
        work_id=_WORK_ID,
        preflight=preflight,
        scope=NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    )

    assert report.summary()["operations_dry_run"] == 1
    completeness = report.scope_completeness
    assert completeness is not None
    assert completeness.not_in_scope_reason_counts == {
        NZ_DRY_RUN_NOT_IN_SCOPE_NON_REPEAL_FAMILY: 1
    }
    # The text-replace is not a repeal witness, so it is not in the repeal census.
    assert completeness.total_repeal_operation_witnesses == 1
    assert completeness.repeal_witnesses_in_scope == 1


def test_scope_from_arg_normalizes_and_rejects_unknown() -> None:
    assert scope_from_arg(None) == NZ_DRY_RUN_SCOPE_COMPLETE_SET
    assert scope_from_arg("") == NZ_DRY_RUN_SCOPE_COMPLETE_SET
    assert scope_from_arg("complete-set") == NZ_DRY_RUN_SCOPE_COMPLETE_SET
    assert scope_from_arg("selected-family-repeal") == NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL
    with pytest.raises(ValueError):
        scope_from_arg("everything")


_REAL_DB = Path("<DATA_ROOT>/data/nz_legislation.farchive")


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_dry_run_canary_agrees_against_archived_oracle() -> None:
    from lawvm.new_zealand.dry_run import build_archived_work_dry_run_repeal

    report = build_archived_work_dry_run_repeal(_REAL_DB, _WORK_ID)
    summary = report.summary()

    assert summary["preflight_status"] == "ready_for_dry_run_replay"
    assert summary["operations_dry_run"] >= 1
    assert summary["operations_refused"] == 0
    assert summary["dry_run_oracle_agreements"] == summary["operations_dry_run"]
    assert summary["dry_run_oracle_residuals"] == 0
    assert summary["neighbors_unchanged_all"] is True
    assert all(proof.oracle_match == "agrees" for proof in report.proofs)
