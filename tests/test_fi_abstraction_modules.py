"""Tests for FI abstraction iteration modules."""
from __future__ import annotations

from lawvm.core.invariant_surface_matrix import FI_REPLAY_FOLD_SURFACE, project_replay_warning_findings
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.phase_result import Finding
from lawvm.finland.elaboration_rule_registry import ELABORATION_RULE_REGISTRY, rule_by_id
from lawvm.finland.evidence_projector import project_meta_rows
from lawvm.finland.merge import MergeInvariantViolation, build_merge_invariant_findings
from lawvm.finland.recovery_authorization_registry import (
    recovery_authorization_kinds,
    recovery_authorization_rule,
)
from lawvm.finland.pathology_failed_op_projector import (
    source_pathology_execution_authorization,
    source_pathology_proof_surface_rows,
)
from lawvm.finland.proof_surface_row_helpers import kind_slug
from lawvm.finland.recovery_temporal_proof_projector import (
    recovery_execution_authorization_rows_from_projection_rows,
)
from lawvm.finland.agreement_residual_proof_projector import (
    finlex_editorial_witness_agreement_residual_rows,
)
from lawvm.finland.sparse_slot_certificate_projector import sparse_slot_candidate_set_certificate_rows
from lawvm.finland.source_witness_proof_projector import corrigendum_source_witness
from lawvm.finland.strict_report_evidence_projector import finland_strict_report_evidence_surface
from lawvm.finland.source_pathology_proof_registry import (
    registered_source_pathology_proof_rule_codes,
    source_pathology_proof_rule,
)
from lawvm.finland.recovery_rule_registry import recovery_rule_ids
from lawvm.finland.replay_fold_projection import ReplayFoldProjectionRequest, project_replay_fold
from lawvm.finland.statute import ReplayState
from lawvm.tools.audit_channels import normalize_warning_message, warnings_channel_spec


def test_elaboration_rule_registry_has_uncovered_rules() -> None:
    assert len(ELABORATION_RULE_REGISTRY) >= 10
    assert rule_by_id("fi.uncovered.body_recovery") is not None


def test_recovery_rule_registry_ids() -> None:
    ids = recovery_rule_ids()
    assert "fi_recovery_sparse_merge" in ids
    assert "fi_sparse_slot_binding_candidate_set" in ids


def test_recovery_authorization_registry_covers_apply_kinds() -> None:
    kinds = recovery_authorization_kinds()
    assert "APPLY.UNCOVERED_BODY_RECOVERY" in kinds
    rule = recovery_authorization_rule("APPLY.LEGACY_DISPATCH_FALLBACK")
    assert rule is not None
    assert rule.family == "legacy_dispatch_fallback"


def test_source_pathology_proof_registry_covers_recodification_codes() -> None:
    codes = registered_source_pathology_proof_rule_codes()
    assert "RECODIFICATION_SOURCE_CHAIN_GAP" in codes
    rule = source_pathology_proof_rule("SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING")
    assert rule.required_claim_kind == "fi.v1.SOURCE_PATHOLOGY_RESOLUTION"


def test_proof_surface_row_helpers_kind_slug() -> None:
    assert kind_slug("APPLY.LEGACY_DISPATCH") == "apply_legacy_dispatch"


def test_strict_report_evidence_projector_empty_payload() -> None:
    report = finland_strict_report_evidence_surface(
        {"statute_id": "1991/3", "profile": "FINLAND_INGESTION_V1", "ops": {"canonical": 0, "failed": 0, "total": 0}}
    )
    assert report["report_kind"] == "finland_strict_report"
    assert report["replay_claims"] is False


def test_source_witness_projector_corrigendum_digest() -> None:
    witness = corrigendum_source_witness(
        {
            "source_pdf": "/data/corrigendum.pdf",
            "amendment_id": "2020/100",
            "sha256": "abc123",
        }
    ).to_dict()
    assert witness["artifact_id"] == "/data/corrigendum.pdf"
    assert witness["digest"] == "abc123"


def test_agreement_residual_projector_editorial_witness_confirmed() -> None:
    rows = finlex_editorial_witness_agreement_residual_rows(
        (
            {
                "kind": "editorial_witness_confirmed",
                "slot_address": "section:3",
                "amendment_id": "2021/1030",
            },
        ),
        statute_id="2013/331",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "agrees"
    assert rows[0]["family"] == "agreement"


def test_sparse_slot_certificate_projector_binding_row() -> None:
    certs = sparse_slot_candidate_set_certificate_rows(
        (
            {
                "kind": "ELAB.SPARSE_SLOT_BINDING",
                "detail": {
                    "source_statute": "2020/100",
                    "target_unit_kind": "section",
                    "target_norm": "5",
                    "payload_slot_index": 1,
                    "payload_slot_label": "a",
                },
            },
        ),
        statute_id="1991/3",
    )
    assert len(certs) == 1
    assert certs[0]["rule_id"] == "fi_sparse_slot_binding_candidate_set"
    assert certs[0]["completeness_status"] == "partial"


def test_recovery_temporal_projector_blocks_strict_recovery() -> None:
    rows = recovery_execution_authorization_rows_from_projection_rows(
        (
            {
                "kind": "ELAB.STRICT_REJECTED_OPERATION",
                "source": "2020/100",
                "message": "blocked",
                "detail": {},
            },
        ),
        strict_fail_reasons=("ELAB.STRICT_REJECTED_OPERATION",),
        statute_id="1991/3",
    )
    assert len(rows) == 1
    assert rows[0]["authorization_status"] == "strict_recovery_blocked"


def test_pathology_failed_op_projector_emits_authorization_rows() -> None:
    from lawvm.core.compile_result import SourcePathology

    pathology = SourcePathology(
        code="EMPTY_OPERATIVE_BODY",
        message="no operative body",
        source_statute="2020/100",
    )
    rows = source_pathology_proof_surface_rows((pathology,), statute_id="1991/3")
    assert len(rows["source_pathology_execution_authorizations"]) == 1
    auth = source_pathology_execution_authorization(pathology)
    assert auth.replay_authorized is False


def test_build_merge_invariant_findings_emits_observation() -> None:
    violation = MergeInvariantViolation(
        code="OMISSION_SURVIVES_MERGE",
        severity="hard",
        message="Omission marker survives",
        detail={"op_id": "op-1"},
    )
    findings = build_merge_invariant_findings((violation,), source_statute="1991/3", op_id="op-1")
    assert len(findings) == 1
    assert findings[0].kind == "merge_invariant_violation"
    assert findings[0].blocking is False


def test_replay_fold_emits_flattened_sublist_warning() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=tuple(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label)
                    for label in ("a", "b", "1", "2", "ba", "bb")
                ),
            ),
        ),
    )
    findings: list[Finding] = []
    meta: dict[str, object] = {}
    project_replay_fold(
        ReplayFoldProjectionRequest(
            state=ReplayState(ir=body),
            parent_id="1991/3",
            replay_findings=findings,
            replay_meta_out=meta,
            replay_print=lambda _message: None,
        )
    )
    assert meta.get("flattened_sublist_warnings")
    assert any(finding.kind == "flattened_sublist_family_warning" for finding in findings)


def test_invariant_surface_matrix_projects_all_warning_families() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=())
    findings: list[Finding] = []
    project_replay_warning_findings(
        tree=body,
        phase="replay_fold",
        source_statute="test/1",
        warnings=FI_REPLAY_FOLD_SURFACE.replay_profile.warnings,
        replay_findings=findings,
        replay_meta_out={},
        replay_print=lambda _message: None,
    )
    assert isinstance(findings, list)


def test_evidence_projector_dedups_meta_rows() -> None:
    meta: dict[str, object] = {}
    project_meta_rows(
        [{"kind": "a", "rule_id": "r1"}],
        meta_key="proof_rows",
        replay_meta_out=meta,
        dedup_keys=("kind", "rule_id"),
    )
    project_meta_rows(
        [{"kind": "a", "rule_id": "r1"}],
        meta_key="proof_rows",
        replay_meta_out=meta,
        dedup_keys=("kind", "rule_id"),
    )
    rows = meta["proof_rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1


def test_audit_warnings_channel_spec() -> None:
    spec = warnings_channel_spec()
    assert spec.channel.value == "warnings"
    assert normalize_warning_message("1991/3 § 5 warning") == "<SID> <SEC> warning"


def test_export_multi_projection_tail_writes_jsonl(tmp_path) -> None:
    from lawvm.tools.export_persistence import MultiTableExportSpec, export_multi_projection_tail

    counts = export_multi_projection_tail(
        data_dir=tmp_path,
        tables=[
            MultiTableExportSpec("fi_test_a", [{"id": "1"}], None),
            MultiTableExportSpec("fi_test_b", [{"id": "2"}], None),
        ],
        use_parquet=False,
    )
    assert counts == {"fi_test_a": 1, "fi_test_b": 1}
    assert (tmp_path / "fi_test_a.jsonl").is_file()
    assert (tmp_path / "fi_test_b.jsonl").is_file()
