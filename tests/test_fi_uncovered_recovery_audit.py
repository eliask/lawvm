from __future__ import annotations

from lawvm.finland.uncovered_recovery_state import (
    FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
    UncoveredCandidateAudit,
)
from lawvm.finland.apply_resolved_op import (
    FI_APPLY_RESOLVED_OP_RULE_ID,
    ApplyResolvedOpAudit,
)
from lawvm.finland.replay_evidence_projection import (
    ReplayEvidenceProjectionRequest,
    project_replay_evidence,
)


def test_uncovered_candidate_audit_projects_to_replay_meta() -> None:
    observation = UncoveredCandidateAudit(
        section="7",
        chapter="3",
        part="",
        disposition="INSERT",
        reason="uncovered_body_section",
        op_id="uncovered_insert_7",
    ).to_observation(source_statute="1996/1261")
    replay_meta: dict[str, object] = {}

    project_replay_evidence(
        ReplayEvidenceProjectionRequest(
            parent_id="1996/1261",
            replay_findings=[],
            source_pathologies=[],
            elaboration_observations=[observation],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            regex_recognition_coverages=[],
            commencement_expiry_overrides=[],
            write_audits=[],
            mutation_events=[],
            restructure_plans=[],
            source_pathologies_out=[],
            replay_meta_out=replay_meta,
            strict_profile=None,
            replay_print=lambda _message: None,
        )
    )

    assert replay_meta["uncovered_body_candidate_audits"] == [
        {
            "source_statute": "1996/1261",
            "rule_id": FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
            "target_section": "7",
            "target_chapter": "3",
            "target_part": "",
            "disposition": "INSERT",
            "reason": "uncovered_body_section",
            "op_id": "uncovered_insert_7",
        }
    ]


def test_apply_resolved_op_audit_projects_to_replay_meta() -> None:
    observation = ApplyResolvedOpAudit(
        source_statute="12/2015",
        op_id="replace_1",
        action_type="REPLACE",
        description="replace section 1",
        target_unit_kind="section",
        target_norm="1",
        target_chapter="",
        target_part="",
        target_paragraph="",
        target_item="",
        target_special="",
        disposition="APPLIED",
    ).to_observation()
    replay_meta: dict[str, object] = {}

    project_replay_evidence(
        ReplayEvidenceProjectionRequest(
            parent_id="100/2010",
            replay_findings=[],
            source_pathologies=[],
            elaboration_observations=[observation],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            regex_recognition_coverages=[],
            commencement_expiry_overrides=[],
            write_audits=[],
            mutation_events=[],
            restructure_plans=[],
            source_pathologies_out=[],
            replay_meta_out=replay_meta,
            strict_profile=None,
            replay_print=lambda _message: None,
        )
    )

    assert replay_meta["apply_resolved_op_audits"] == [
        {
            "source_statute": "12/2015",
            "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
            "op_id": "replace_1",
            "action_type": "REPLACE",
            "description": "replace section 1",
            "target_unit_kind": "section",
            "target_norm": "1",
            "target_chapter": "",
            "target_part": "",
            "target_paragraph": "",
            "target_item": "",
            "target_special": "",
            "disposition": "APPLIED",
        }
    ]
