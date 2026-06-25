from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.new_zealand.dry_run_oracle import (
    _FAMILY_REPEAL_TARGET_DIVERGES,
    _FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED,
    _FAMILY_UNAPPLIED_NON_REPEAL,
    NZ_DRY_RUN_ORACLE_REPEAL_TARGET_DIVERGES_RULE_ID,
    build_dry_run_oracle_comparison,
    materialize_candidate_after_document,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZCanonicalEffectCandidateRow,
    build_effect_candidate_preflight,
)
from lawvm.new_zealand.source_tree import parse_nz_source_document


_WORK_ID = "act_public_2005_87"
_BEFORE_VERSION = "act_public_2005_87_en_2017-04-19"
_AFTER_VERSION = "act_public_2005_87_en_2019-10-24"

# Before: section 108 substantive; sibling 109 substantive; sibling 110 substantive.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>No review</heading>
      <prov.body><para><text>Old section 108 text.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Other</heading>
      <prov.body><para><text>Other original text.</text></para></prov.body></prov>
  </body>
</act>
"""

# Oracle (on-or-after) reflects ALL window changes, only one of which is our
# repeal:
#   108 -> repealed tombstone, body text erased (NZ consolidation convention);
#   109 -> untouched (exact agreement);
#   110 -> text changed by some *other* (non-repeal) amendment we did not apply.
_AFTER_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status="repealed"><label>108</label><heading>No review</heading>
      <prov.body></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Other</heading>
      <prov.body><para><text>Other REPLACED text from a non-repeal amendment.</text></para></prov.body></prov>
  </body>
</act>
"""

# A divergent oracle where the path we repealed is NOT a tombstone in the oracle:
# section 108 stays substantive (oracle disagrees with the applied repeal).
_AFTER_XML_DIVERGENT = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>No review</heading>
      <prov.body><para><text>Section 108 was never repealed in this oracle.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Other</heading>
      <prov.body><para><text>Other original text.</text></para></prov.body></prov>
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


def _archive(after_xml: bytes = _AFTER_XML) -> _FakeArchive:
    return _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{_BEFORE_VERSION}/": _version_detail(
                _BEFORE_VERSION, "2017-04-19"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2017-04-19.xml": _BEFORE_XML,
            f"https://api.legislation.govt.nz/v0/versions/{_AFTER_VERSION}/": _version_detail(
                _AFTER_VERSION, "2019-10-24"
            ),
            "https://www.legislation.govt.nz/act/public/2005/87/en/2019-10-24.xml": after_xml,
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


def _repeal_row() -> NZCanonicalEffectCandidateRow:
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-1",
        operation_row_id="nz-opw-1",
        effect_readiness_row_id="nz-readiness-1",
        candidate_status="candidate_emitted",
        action=str(StructuralAction.REPEAL),
        target_address="section:108",
        operation=_repeal_operation(),
        source_path=("prov:108",),
        amendment_date_iso="2019-10-24",
        repeal_payload_corroboration_status="not_required_non_direct_repeal_payload",
        latest_oracle_target_resolution_status="",
    )


def _preflight(rows: tuple[NZCanonicalEffectCandidateRow, ...] = (_repeal_row(),)):
    report = NZCanonicalEffectCandidateReport(work_id=_WORK_ID, rows=rows)
    return build_effect_candidate_preflight(report)


# --- 1. Full after-document materialization -------------------------------


def test_materialize_after_tombstones_target_and_keeps_others_byte_identical() -> None:
    before = parse_nz_source_document(_BEFORE_XML, xml_locator="loc", version_id=_BEFORE_VERSION)
    after = materialize_candidate_after_document(before, (("prov:108",),))

    assert len(after.nodes) == len(before.nodes)
    assert after.version_id == before.version_id
    assert after.xml_locator == before.xml_locator

    before_by_path = {node.path: node for node in before.nodes}
    after_by_path = {node.path: node for node in after.nodes}
    assert set(before_by_path) == set(after_by_path)

    # The target became a tombstone (addressability + text preserved by the
    # boring kernel; only deletion_status flips).
    target = after_by_path[("prov:108",)]
    assert target.deletion_status == "repealed"
    assert target.text == before_by_path[("prov:108",)].text
    assert target.xml_id == before_by_path[("prov:108",)].xml_id
    assert target.heading == before_by_path[("prov:108",)].heading

    # Every other node is the exact same immutable object (byte-identical).
    for path, before_node in before_by_path.items():
        if path == ("prov:108",):
            continue
        assert after_by_path[path] is before_node


def test_materialize_is_noop_on_already_tombstoned_target() -> None:
    before = parse_nz_source_document(_AFTER_XML, xml_locator="loc", version_id="v")
    # prov:108 is already repealed in _AFTER_XML; re-tombstoning is a no-op.
    after = materialize_candidate_after_document(before, (("prov:108",),))
    after_by_path = {node.path: node for node in after.nodes}
    before_by_path = {node.path: node for node in before.nodes}
    assert after_by_path[("prov:108",)] is before_by_path[("prov:108",)]


# --- 2/3. Whole-tree comparison classification ------------------------------


def test_whole_tree_separates_repeal_agreement_from_unapplied_non_repeal_change() -> None:
    report = build_dry_run_oracle_comparison(_archive(), work_id=_WORK_ID, preflight=_preflight())

    summary = report.summary()
    assert summary["preflight_status"] == "ready_for_dry_run_replay"
    assert summary["windows_compared"] == 1

    # Repeal slice: the one repeal target agrees in direction (tombstone vs
    # tombstone); zero genuine divergences.
    assert summary["repeal_target_nodes"] == 1
    assert summary["repeal_target_agreements"] == 1
    assert summary["repeal_target_divergences"] == 0
    assert summary["repeal_slice_agrees"] is True

    families = summary["residual_family_counts"]
    # 108: repeal direction agrees, oracle erased body text -> source-honest.
    assert families.get(_FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED) == 1
    # 110: changed by a non-repeal amendment we did not apply -> source-honest.
    assert families.get(_FAMILY_UNAPPLIED_NON_REPEAL) == 1
    # 109 exact-agrees and emits no residual.
    assert summary["whole_tree_exact_agreements"] == 1
    # No genuine replay-direction divergence here.
    assert _FAMILY_REPEAL_TARGET_DIVERGES not in families
    assert summary["residual_core_family_counts"].get("replay_bug") is None

    # Both residual families map onto the accepted-non-executable frontier.
    assert summary["residual_core_family_counts"]["accepted_non_executable_frontier"] == 2

    # The unapplied-non-repeal residual carries the exact node path.
    unapplied = [
        residual
        for residual in report.all_residuals()
        if residual.family == _FAMILY_UNAPPLIED_NON_REPEAL
    ]
    assert [residual.path for residual in unapplied] == [("prov:110",)]


def test_whole_tree_forbidden_shortcuts_and_no_replay_claim() -> None:
    report = build_dry_run_oracle_comparison(_archive(), work_id=_WORK_ID, preflight=_preflight())

    payload = report.to_jsonable()
    assert payload["replay_claims"] is False
    assert payload["dry_run_claims"] is True
    assert "unapplied_non_repeal_residual_as_replay_bug" in payload["forbidden_shortcuts"]
    assert "oracle_consolidation_view_as_source_truth" in payload["forbidden_shortcuts"]

    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_dry_run_repeal_whole_tree"
    assert surface["materialization_kind"] == "proposed_future_branch"
    assert surface["comparison_materialization_kind"] == "official_consolidation_view"
    # The whole agreement surface never authorizes replay.
    assert all(
        "unapplied_non_repeal_residual_as_replay_bug" in residual["forbidden_shortcuts"]
        for residual in surface["residuals"]
    )
    assert payload["summary"]["actual_replay_agreements"] == 0


# --- Synthetic genuine-divergence case --------------------------------------


def test_whole_tree_flags_genuine_divergence_when_oracle_not_tombstone() -> None:
    # The oracle does NOT tombstone the path we repealed: applied repeal
    # disagrees with the oracle direction. This is a real replay-direction bug,
    # NOT an unapplied-other-change.
    report = build_dry_run_oracle_comparison(
        _archive(after_xml=_AFTER_XML_DIVERGENT), work_id=_WORK_ID, preflight=_preflight()
    )

    summary = report.summary()
    assert summary["repeal_target_divergences"] == 1
    assert summary["repeal_slice_agrees"] is False
    assert summary["residual_core_family_counts"]["replay_bug"] == 1

    diverging = report.diverging_residuals()
    assert len(diverging) == 1
    residual = diverging[0]
    assert residual.path == ("prov:108",)
    assert residual.family == _FAMILY_REPEAL_TARGET_DIVERGES
    assert residual.rule_id == NZ_DRY_RUN_ORACLE_REPEAL_TARGET_DIVERGES_RULE_ID
    assert residual.is_repeal_target is True
    assert residual.candidate_occupancy == "tombstone"
    assert residual.oracle_occupancy == "substantive"

    # A genuine divergence must never be silently flattened into the frontier.
    assert summary["residual_family_counts"].get(_FAMILY_REPEAL_TARGET_TEXT_NOT_ERASED) is None
    # replay_claims still never flips to True even with a divergence present.
    assert report.to_jsonable()["replay_claims"] is False


# --- Real archive canary ----------------------------------------------------


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_dry_run_oracle_canary_repeal_slice_agrees_against_archived_oracle() -> None:
    from lawvm.new_zealand.dry_run_oracle import (
        build_archived_work_dry_run_oracle_comparison,
    )

    report = build_archived_work_dry_run_oracle_comparison(_REAL_DB, _WORK_ID)
    summary = report.summary()

    assert summary["preflight_status"] == "ready_for_dry_run_replay"
    # The repeal slice agrees cleanly; no genuine replay-direction divergence.
    assert summary["repeal_target_divergences"] == 0
    assert summary["repeal_slice_agrees"] is True
    # The whole-tree residual is honestly the accepted-non-executable frontier
    # (other window operations + oracle body-text erasure), never a replay bug.
    assert summary["residual_core_family_counts"].get("replay_bug") is None
    assert summary["whole_tree_residuals"] > 0
    assert summary["replay_claims"] is False
