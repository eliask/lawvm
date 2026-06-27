from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZ_ACTUAL_REPLAY_SLICE_AGREES_RULE_ID,
    build_actual_replay,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZCanonicalEffectCandidateRow,
    build_effect_candidate_preflight,
)


_WORK_ID = "act_public_2005_87"
_BEFORE_VERSION = "act_public_2005_87_en_2017-04-19"
_AFTER_VERSION = "act_public_2005_87_en_2019-10-24"

# Before: section 108 substantive (repeal target); section 110(1) carries a
# phrase once (text-replace target); siblings 109 / 110(2) untouched.
_BEFORE_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>No review</heading>
      <prov.body><para><text>Old section 108 text.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360604s1"><label>1</label><para><text>An application must be in the prescribed form.</text></para></subprov>
        <subprov id="DLM360604s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
  </body>
</act>
"""

# Oracle (on-or-after) reflects the window changes:
#   108 -> repealed tombstone, body text erased (NZ consolidation convention).
#          The boring repeal kernel agrees in DIRECTION (tombstone) even though
#          the oracle additionally erased the body text.
#   110(1) -> "prescribed form" substituted with "approved form".
#   109 / 110(2) -> untouched.
_AFTER_XML = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status="repealed"><label>108</label><heading>No review</heading>
      <prov.body></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360604s1"><label>1</label><para><text>An application must be in the approved form.</text></para></subprov>
        <subprov id="DLM360604s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
  </body>
</act>
"""

# A divergent oracle where the repeal path is NOT a tombstone (section 108 stays
# substantive). The repeal op cannot be dry-run-verified against this oracle, so
# actual replay must FAIL CLOSED on the transition.
_AFTER_XML_REPEAL_DIVERGES = b"""\
<act>
  <body>
    <prov id="DLM360602" deletion-status=""><label>108</label><heading>No review</heading>
      <prov.body><para><text>Section 108 was never repealed in this oracle.</text></para></prov.body></prov>
    <prov id="DLM360603" deletion-status=""><label>109</label><heading>Neighbour</heading>
      <prov.body><para><text>Neighbour text.</text></para></prov.body></prov>
    <prov id="DLM360604" deletion-status=""><label>110</label><heading>Forms</heading>
      <prov.body>
        <subprov id="DLM360604s1"><label>1</label><para><text>An application must be in the approved form.</text></para></subprov>
        <subprov id="DLM360604s2"><label>2</label><para><text>The Registrar keeps the register.</text></para></subprov>
      </prov.body></prov>
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


def _text_replace_operation() -> LegalOperation:
    return LegalOperation(
        op_id=f"nz:{_WORK_ID}:nz-opw-2:text_replace",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "110"), ("subsection", "1"))),
        payload=None,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="the prescribed form", occurrence=1),
            replacement="the approved form",
        ),
        source=OperationSource(statute_id="act_public_2019_5", effective="2019-10-24"),
        provenance_tags=("new_zealand", "history_note", "candidate_only", "not_replayed"),
        witness_rule_id="nz_text_replace_candidate_from_direct_instruction_workqueue",
    )


def _text_replace_row() -> NZCanonicalEffectCandidateRow:
    return NZCanonicalEffectCandidateRow(
        row_id="nz-effect-candidate-2",
        operation_row_id="nz-opw-2",
        effect_readiness_row_id="nz-readiness-2",
        candidate_status="candidate_emitted",
        action=str(StructuralAction.TEXT_REPLACE),
        target_address="section:110/subsection:1",
        operation=_text_replace_operation(),
        amendment_date_iso="2019-10-24",
        operation_family="amended",
        old_text="the prescribed form",
        new_text="the approved form",
        latest_oracle_target_resolution_status="exact_source_path",
    )


def _preflight(rows: tuple[NZCanonicalEffectCandidateRow, ...]):
    report = NZCanonicalEffectCandidateReport(work_id=_WORK_ID, rows=rows)
    return build_effect_candidate_preflight(report)


# --- 1. Happy path: verified ops materialize and the slice agrees -----------


def test_actual_replay_materializes_verified_transition_and_slice_agrees() -> None:
    report = build_actual_replay(
        _archive(),
        work_id=_WORK_ID,
        preflight=_preflight((_repeal_row(), _text_replace_row())),
    )
    summary = report.summary()

    # Both families' ops were dry-run-verified and live in the same window, so a
    # single transition is ACTUALLY replayed with two ops; nothing is refused.
    assert summary["transitions_replayed"] == 1
    assert summary["transitions_refused"] == 0
    assert summary["ops_replayed"] == 2
    assert summary["target_slice_agreements"] == 2
    assert summary["all_slices_agree"] is True
    # This is the one NZ surface where replay_claims is True.
    assert summary["replay_claims"] is True
    assert summary["dry_run_claims"] is False

    transition = report.transitions[0]
    # Temporal witnesses: the materialized after came from the archived before
    # snapshot and is checked against the archived on-or-after snapshot.
    assert transition.before_version_id == "act_public_2005_87_en_2017-04-19"
    assert transition.oracle_version_id == "act_public_2005_87_en_2019-10-24"
    assert transition.amendment_date_iso == "2019-10-24"
    assert transition.target_slice_agrees is True

    # The materialized after-document is the actual replay output (a separate
    # artifact). The repeal target became a tombstone; the text-replace target
    # carries the substituted text. Other nodes are untouched.
    after_by_path = {node.path: node for node in transition.materialized_after.nodes}
    assert after_by_path[("prov:108",)].deletion_status == "repealed"
    assert "the approved form" in after_by_path[("prov:110", "subprov:1")].text
    assert "the prescribed form" not in after_by_path[("prov:110", "subprov:1")].text
    assert "The Registrar keeps the register." in after_by_path[("prov:110", "subprov:2")].text


# --- 2. Labels / claims / forbidden shortcuts -------------------------------


def test_actual_replay_surface_is_labeled_actual_replay_not_dry_run() -> None:
    report = build_actual_replay(
        _archive(), work_id=_WORK_ID, preflight=_preflight((_repeal_row(),))
    )
    payload = report.to_jsonable()

    assert payload["report_kind"] == "actual_replay"
    assert payload["replay_claims"] is True
    assert payload["dry_run_claims"] is False
    assert payload["fail_closed"] is True
    assert "oracle_consolidation_view_as_replay_payload_authority" in payload["forbidden_shortcuts"]
    assert "blocked_candidate_row_as_replayed_transition" in payload["forbidden_shortcuts"]

    surface = report.agreement_surface()
    assert surface["agreement_surface"] == "nz_actual_replay"
    # Actually-reconstructed legal state, NOT a proposed_future_branch candidate.
    assert surface["materialization_kind"] == "legal_text_state"
    assert surface["comparison_materialization_kind"] == "official_consolidation_view"
    assert all(residual["owner_phase"] == "actual_replay" for residual in surface["residuals"])
    # The replayed-slice agreement rows carry the slice-agrees rule id; refusal
    # rows (when present) carry their own typed rule id + family, so the surface
    # is no longer single-rule-id — but every agreeing row still uses the slice id.
    agreeing = [r for r in surface["residuals"] if r["agreement_residual_status"] == "agrees"]
    assert agreeing
    assert all(residual["rule_id"] == NZ_ACTUAL_REPLAY_SLICE_AGREES_RULE_ID for residual in agreeing)
    assert all(residual["family"] == "agreement" for residual in agreeing)


# --- 3. FAIL CLOSED: an unverified op blocks the whole transition -----------


def test_actual_replay_fails_closed_when_a_declared_op_does_not_verify() -> None:
    # The repeal op's dry-run proof will NOT agree (oracle keeps 108 substantive),
    # so the whole transition is blocked and NOTHING is materialized — even though
    # the text-replace op in the same window would verify on its own.
    report = build_actual_replay(
        _archive(after_xml=_AFTER_XML_REPEAL_DIVERGES),
        work_id=_WORK_ID,
        preflight=_preflight((_repeal_row(), _text_replace_row())),
    )
    summary = report.summary()

    assert summary["transitions_replayed"] == 0
    assert summary["ops_replayed"] == 0
    assert summary["transitions_refused"] >= 1
    # No actual-replay claim survives a fail-closed refusal of the transition.
    assert summary["replay_claims"] is False

    # The refusal is a DISTINCT NAMED diagnostic, never a silent skip.
    refusal_rules = {refusal.rule_id for refusal in report.refusals}
    assert NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID in refusal_rules
    # The same-window verified text-replace op is reported as part of the blocked
    # transition (never partially materialized).
    assert NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID in refusal_rules

    # The blocked declared transition keeps the repeal op id; the verified
    # sibling op id appears in the "whole transition blocked" refusal.
    residual_refusal = next(
        refusal
        for refusal in report.refusals
        if refusal.rule_id == NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID
    )
    assert residual_refusal.op_ids == (f"nz:{_WORK_ID}:nz-opw-1:repeal",)
    sibling_block = next(
        refusal
        for refusal in report.refusals
        if refusal.rule_id == NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID
    )
    assert sibling_block.op_ids == (f"nz:{_WORK_ID}:nz-opw-2:text_replace",)


def test_replayed_count_is_separable_from_blocked_candidate_rows() -> None:
    # Replayed-transition count and fail-closed-blocked count are always
    # separately reported, so actually-replayed work never hides behind blocked
    # candidate rows.
    blocked = build_actual_replay(
        _archive(after_xml=_AFTER_XML_REPEAL_DIVERGES),
        work_id=_WORK_ID,
        preflight=_preflight((_repeal_row(), _text_replace_row())),
    )
    assert blocked.summary()["transitions_replayed"] == 0
    assert blocked.summary()["transitions_refused"] > 0

    clean = build_actual_replay(
        _archive(), work_id=_WORK_ID, preflight=_preflight((_repeal_row(), _text_replace_row()))
    )
    assert clean.summary()["transitions_replayed"] == 1
    assert clean.summary()["transitions_refused"] == 0


# --- 4. FAIL CLOSED: a dry-run refusal blocks the op ------------------------


def test_actual_replay_fails_closed_on_target_recovered_op() -> None:
    # A target-recovered repeal candidate is refused by the dry-run kernel; it
    # must never be promoted to actual replay.
    recovered = _repeal_row()
    recovered = NZCanonicalEffectCandidateRow(
        row_id=recovered.row_id,
        operation_row_id=recovered.operation_row_id,
        effect_readiness_row_id=recovered.effect_readiness_row_id,
        candidate_status=recovered.candidate_status,
        action=recovered.action,
        target_address=recovered.target_address,
        operation=recovered.operation,
        source_path=recovered.source_path,
        amendment_date_iso=recovered.amendment_date_iso,
        repeal_payload_corroboration_status=recovered.repeal_payload_corroboration_status,
        latest_oracle_target_resolution_status="recovered_by_label",
    )
    report = build_actual_replay(
        _archive(), work_id=_WORK_ID, preflight=_preflight((recovered,))
    )
    summary = report.summary()
    assert summary["transitions_replayed"] == 0
    assert summary["transitions_refused"] >= 1
    assert summary["replay_claims"] is False
    # Distinct named diagnostic, not a silent drop.
    assert NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID in {
        refusal.rule_id for refusal in report.refusals
    }


# --- 5. Non-promotable family is rejected -----------------------------------


def test_actual_replay_rejects_non_promotable_family() -> None:
    # repeal/text_replace/replace/insert are promotable; anything else is refused
    # up front (e.g. a bare "omit" family that has no actual-replay kernel).
    with pytest.raises(ValueError, match="not promotable"):
        build_actual_replay(
            _archive(),
            work_id=_WORK_ID,
            preflight=_preflight((_repeal_row(),)),
            families=("omit",),
        )


def test_actual_replay_repeal_only_family_subset() -> None:
    report = build_actual_replay(
        _archive(),
        work_id=_WORK_ID,
        preflight=_preflight((_repeal_row(), _text_replace_row())),
        families=("repeal",),
    )
    summary = report.summary()
    assert summary["families"] == ["repeal"]
    assert summary["transitions_replayed"] == 1
    assert summary["ops_replayed"] == 1


# --- 6. Real archive canary -------------------------------------------------


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_actual_replay_canary_replays_transitions_against_archived_oracle() -> None:
    from lawvm.new_zealand.actual_replay import build_archived_work_actual_replay

    report = build_archived_work_actual_replay(_REAL_DB, _WORK_ID)
    summary = report.summary()

    # The canary actually replays its verified repeal transitions from archived
    # inputs only, and every materialized target slice agrees with the archived
    # on-or-after oracle.
    assert summary["transitions_replayed"] >= 1
    assert summary["ops_replayed"] >= 1
    assert summary["all_slices_agree"] is True
    assert summary["target_slice_agreements"] == summary["target_slice_nodes"]
    assert summary["replay_claims"] is True
    assert summary["dry_run_claims"] is False

    # Oracle agreement consumes the replay OUTPUT (the materialized after-tree),
    # not a hand-picked candidate XML.
    for transition in report.transitions:
        assert transition.materialized_after is not None
        assert transition.materialized_node_count > 0
        assert transition.target_slice_agrees is True


_COMPOSITE_BLOCK_INSERT_WORK_ID = "act_public_1956_47"
_COMPOSITE_BLOCK_INSERT_DATE = "2011-05-01"
# Witness: the 2011-05-01 transition of act_public_1956_47 declares 3
# structural whole-provision REPLACEs + 1 INSERT in its change window, and the
# insert is a block member whose oracle-adjacent predecessor is another
# block insert this work makes. The dry-run co-inserted-block carveout
# (status doc Limits #2b) admits the position; actual replay must re-confirm
# with the SAME carveout (it copies the proof's co_inserted_block_labels
# forward, never re-derives from a lossier oracle partition). Before this fix
# actual replay reconfirmed WITHOUT the carveout and the transition fail-closed
# with ``nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle``.
_COMPOSITE_BLOCK_INSERT_OP_IDS = frozenset(
    {
        f"nz:{_COMPOSITE_BLOCK_INSERT_WORK_ID}:nz-opw-1502:replace",
        f"nz:{_COMPOSITE_BLOCK_INSERT_WORK_ID}:nz-opw-1503:replace",
        f"nz:{_COMPOSITE_BLOCK_INSERT_WORK_ID}:nz-opw-1504:replace",
        f"nz:{_COMPOSITE_BLOCK_INSERT_WORK_ID}:nz-opw-40:insert",
    }
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
def test_actual_replay_confirms_block_insert_composite_transition() -> None:
    # Defence-in-depth regresssion: a dry-run-verified composite transition (3
    # replace + 1 block-insert in one change window) must materialize cleanly
    # and the materialized target slice must re-agree with the archived on-or-
    # after oracle, NOT fail-closed on a false position mismatch.
    from lawvm.new_zealand.actual_replay import (
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
        build_archived_work_actual_replay,
    )

    report = build_archived_work_actual_replay(_REAL_DB, _COMPOSITE_BLOCK_INSERT_WORK_ID)
    summary = report.summary()

    # The 2011-05-01 transition must be present as a replayed transition (not a
    # fail-closed refusal). It carries 3 replace + 1 insert ops and a target
    # slice that fully agrees with the archived on-or-after oracle.
    matching = [
        t for t in report.transitions
        if t.amendment_date_iso == _COMPOSITE_BLOCK_INSERT_DATE
    ]
    assert matching, "expected the 2011-05-01 transition to materialize (not be refused)"
    assert len(matching) == 1
    transition = matching[0]
    assert transition.target_slice_node_count == len(_COMPOSITE_BLOCK_INSERT_OP_IDS)
    assert transition.target_slice_agreements == transition.target_slice_node_count
    assert transition.target_slice_agrees is True

    # The diverge rule fired by the old buggy re-confirm path MUST be absent.
    refusal_rules = {refusal.rule_id for refusal in report.refusals}
    assert NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID not in refusal_rules

    # Sanity: the transition materialized at least one insert op. Other
    # refusals carry over as the honest per-op fail-closed set and never as
    # a materialized-slice-diverge.
    other_blocking = refusal_rules - {
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    }
    assert other_blocking == set(), f"unexpected blocking refusal rules: {other_blocking}"
    assert summary["all_slices_agree"] is True
    assert summary["replay_claims"] is True


def test_apply_verified_mutation_each_place_replaces_every_occurrence() -> None:
    # The promotion kernel materializes an each-place text substitution at EVERY
    # occurrence (not just the first), driven by the proof's ``text_each_place``
    # flag. Single-occurrence proofs still replace only the leading occurrence.
    from lawvm.new_zealand.actual_replay import _apply_verified_mutation
    from lawvm.new_zealand.dry_run import NZMutationBoundaryProof
    from lawvm.new_zealand.source_tree import NZSourceNode

    node = NZSourceNode(
        kind="subprov",
        path=("prov:108", "subprov:1"),
        xml_id="DLM1",
        xml_path="",
        source_zone="body",
        label="1",
        heading="",
        deletion_status="",
        text="the old phrase here and the old phrase there",
        history=(),
    )

    def _proof(each_place: bool) -> NZMutationBoundaryProof:
        return NZMutationBoundaryProof(
            op_id="nz:x:1:text_replace",
            action=str(StructuralAction.TEXT_REPLACE),
            target_address="section:108/subsection:1",
            selected_source_path=("prov:108", "subprov:1"),
            target_xml_id="DLM1",
            target_digest_before="",
            target_digest_after="",
            operation_payload="",
            occupancy_before="substantive",
            occupancy_after="substantive",
            parent_source_path=("prov:108",),
            parent_digest_before="",
            parent_digest_after="",
            unaffected_neighbor_paths=(),
            unaffected_neighbor_digests_before=(),
            unaffected_neighbor_digests_after=(),
            neighbors_unchanged=True,
            oracle_version_id="v",
            oracle_target_present=True,
            oracle_target_occupancy="substantive",
            oracle_match="agrees",
            oracle_match_rule_id="r",
            text_old_text="the old phrase",
            text_new_text="the new phrase",
            text_each_place=each_place,
        )

    each = _apply_verified_mutation(node, _proof(each_place=True))
    assert each.text == "the new phrase here and the new phrase there"

    single = _apply_verified_mutation(node, _proof(each_place=False))
    assert single.text == "the new phrase here and the old phrase there"


# --- Family-level dry-run refusal receipt propagation (AGENTS §1.8) ----------


def test_family_level_dry_run_refusal_is_carried_as_non_blocking_receipt() -> None:
    """A family-level dry-run refusal (no candidate / preflight not ready) is
    carried onto the actual-replay plane as a NON-blocking receipt. Pins the
    §1.8 receipt-conservation contract at the report-construction layer
    (paired with `tests/test_new_zealand_actual_replay_corpus_smoke.py`'s
    @slow corpus pin that drives the full per-work actual-replay on the smoke
    corpus -- AGENTS §2.9 synthetic+corpus pairing rule).

    Contract pinned:
    * The receipt lives in `report.family_level_dry_run_refusals`, NOT in
      `report.refusals` (so it never inflates `transitions_refused` or blocks
      a transition).
    * `summary()["family_level_dry_run_refusal_counts"]` is a `(rule_id -> n)`
      map carrying the originating dry-run refusal rule_id -- not the
      actual-replay carried rule_id -- so a benchmark can attribute "the
      family declared nothing" to the right family (repeal/replace/insert/
      preflight) without re-running the dry-run.
    * `agreement_residuals()` includes the receipt (typed into
      `accepted_non_executable_frontier` -- the family declared nothing, so
      no mutation, not a `replay_bug` / `temporal_mismatch` / `source_footing_gap`).

    Faster than the @slow corpus sweep -- exercises the report construction
    contract directly (synthetic, no archived XML required).
    """
    from lawvm.new_zealand.actual_replay import (
        NZActualReplayReport,
        NZActualReplayRefusal,
        NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
    )
    from lawvm.new_zealand.dry_run import (
        NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
        NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    )

    carried = (
        NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
            message="synthetic inserted-no-candidate receipt",
            detail={
                "family": "insert",
                "dry_run_refusal_rule_id": NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
            },
        ),
        NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
            message="synthetic repealed-no-candidate receipt",
            detail={
                "family": "repeal",
                "dry_run_refusal_rule_id": NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
            },
        ),
    )
    # A second identical dry-run rule_id to exercise the count > 1 path.
    carried = carried + (
        NZActualReplayRefusal(
            rule_id=NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
            message="synthetic second inserted-no-candidate receipt",
            detail={
                "family": "insert",
                "dry_run_refusal_rule_id": NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID,
            },
        ),
    )

    report = NZActualReplayReport(
        work_id="synthetic",
        families=("repeal", "text_replace", "replace", "insert"),
        transitions=(),
        refusals=(),
        family_level_dry_run_refusals=carried,
    )

    sm = report.summary()
    assert sm["transitions_replayed"] == 0
    assert sm["transitions_refused"] == 0
    assert sm["family_level_dry_run_refusal_counts"] == {
        NZ_DRY_RUN_REFUSED_NO_INSERT_CANDIDATE_RULE_ID: 2,
        NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID: 1,
    }, (
        "family_level_dry_run_refusal_counts must map the originating DRY-RUN "
        "refusal rule_id -> count so the receiving family (insert / repeal / etc) "
        "stays observable per AGENTS §1.8 / §2.9."
    )

    # The carry rule_id lives ONLY in family_level_dry_run_refusals -- not
    # in `refusals` (which would inflate transitions_refused).
    assert all(
        ref.rule_id != NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID
        for ref in report.refusals
    )

    # The receipt is observable on the agreement-residual projection (typed
    # accepted_non_executable_frontier: family declared nothing -> no mutation
    # -> not a replay_bug / temporal_mismatch / source_footing_gap).
    families = {
        residual.family for residual in report.agreement_residuals()
    }
    assert "accepted_non_executable_frontier" in families
    assert "replay_bug" not in families

    # The summary surfaces the receipt under a stable key a benchmark reads.
    payload = report.to_jsonable()
    assert "family_level_dry_run_refusals" in payload
    assert len(payload["family_level_dry_run_refusals"]) == 3
