"""Tests for FI abstraction iteration modules."""
from __future__ import annotations

from typing import cast

from lawvm.core.invariant_surface_matrix import (
    FI_MATERIALIZED_PRODUCT_SURFACE,
    FI_REPLAY_DIAGNOSTIC_SURFACES,
    FI_REPLAY_FOLD_SURFACE,
    project_replay_warning_findings,
)
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE
from lawvm.finland.elaboration_rule_registry import ELABORATION_RULE_REGISTRY, rule_by_id
from lawvm.finland.elaboration_rule_dispatch import PROCESS_AMENDMENT_PIPELINE
from lawvm.finland.evidence_projector import (
    EvidenceProjectionRequest,
    MetaProjection,
    project_evidence,
    project_meta_rows,
)
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
from lawvm.finland.periodic_table import (
    cell_by_id,
    finland_periodic_table_cells,
    periodic_table_summary,
    render_finland_periodic_table_markdown,
)
from lawvm.finland.sparse_slot_certificate_projector import sparse_slot_candidate_set_coverage_rows
from lawvm.finland.source_witness_proof_projector import corrigendum_source_witness
from lawvm.finland.strict_report_evidence_projector import finland_strict_report_evidence_surface
from lawvm.finland.strict_report_proof_projector import (
    finland_strict_report_candidate_set_coverages,
)
from lawvm.finland.source_pathology_proof_registry import (
    registered_source_pathology_proof_rule_codes,
    source_pathology_proof_rule,
)
from lawvm.finland.recovery_rule_registry import recovery_rule_ids
from lawvm.finland.replay_fold_projection import ReplayFoldProjectionRequest, project_replay_fold
from lawvm.finland.statute import ReplayState
from lawvm.tools.audit_channels import normalize_warning_message, warnings_channel_spec


def test_elaboration_rule_registry_has_uncovered_rules() -> None:
    assert len(ELABORATION_RULE_REGISTRY) >= 25
    assert rule_by_id("fi.uncovered.body_recovery") is not None
    assert rule_by_id("fi.process.pipeline") is not None


def test_process_amendment_pipeline_registry_observation() -> None:
    for rule_id in PROCESS_AMENDMENT_PIPELINE:
        assert rule_by_id(rule_id) is not None


def test_family_census_classify_partitions_key_sets() -> None:
    from lawvm.finland.legal_surface.family_census import CENSUS_BUCKETS, classify

    assert classify({"a"}, {"a"}, declined=False) == "match"
    assert classify({"a", "b"}, {"a"}, declined=False) == "superset"
    assert classify({"a"}, {"a", "b"}, declined=False) == "miss"
    assert classify(set(), set(), declined=True) == "decline"
    assert len(CENSUS_BUCKETS) == 4


def test_process_pipeline_emits_registry_stages_on_corpus_miss() -> None:
    from dataclasses import dataclass
    from types import SimpleNamespace

    from lawvm.finland.process_pipeline import process_muutoslaki
    from lawvm.finland.process_request import ProcessAmendmentRequest
    from lawvm.finland.process_result_builder import ProcessAmendmentSinks

    @dataclass(frozen=True, slots=True)
    class _CorpusMiss:
        def read_source(self, amendment_id: str) -> None:
            return None

        def read_source_staged(self, amendment_id: str) -> None:
            # A corpus miss: the staged read preserves the None contract.
            return None

    body = IRNode(kind=IRNodeKind.BODY)
    state = ReplayState(ir=body)
    ctx = SimpleNamespace(
        id="2000/1",
        title="Test",
        issue_date="2000-01-01",
        base_ir=body,
    )

    result = process_muutoslaki(
        ProcessAmendmentRequest(
            amendment_id="2010/100",
            state=state,
            ctx=ctx,  # type: ignore
            replay_mode="legal_pit",
            parent_id="2000/1",
            corpus=_CorpusMiss(),  # type: ignore
        ),
        ProcessAmendmentSinks(),
    )
    assert result.output is state
    findings = result.findings()
    stage_rule_ids = [
        finding.detail.get("rule_id")
        for finding in findings
        if finding.kind == "ELAB.REGISTRY_STAGE"
    ]
    assert "fi.process.runtime" in stage_rule_ids
    assert "fi.process.pipeline" in stage_rule_ids
    assert "fi.process.result_builder" in stage_rule_ids
    pipeline_findings = [
        finding for finding in findings if finding.kind == "ELAB.REGISTRY_PIPELINE"
    ]
    assert len(pipeline_findings) == 1
    assert pipeline_findings[0].detail.get("pipeline_family") == "process_amendment"


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


def test_finland_periodic_table_cells_resolve_promoted_modules() -> None:
    cells = finland_periodic_table_cells()
    assert len(cells) >= 20
    matrix = cell_by_id("invariant_surface_matrix")
    assert matrix is not None
    assert matrix.module == "lawvm.core.invariant_surface_matrix"
    assert matrix.status == "filled"
    identity = cell_by_id("identity_ledger")
    assert identity is not None
    assert identity.status == "filled"
    assert identity.module == "lawvm.core.identity_ledger"
    grammar = cell_by_id("grammar_census")
    assert grammar is not None
    assert grammar.status == "filled"
    strict_report = cell_by_id("strict_report_proof_projector")
    assert strict_report is not None
    assert strict_report.status == "filled"
    harvest = cell_by_id("invariant_harvest")
    assert harvest is not None
    assert harvest.module == "lawvm.tools.invariant_harvest"
    tombstone_mask = cell_by_id("chapter_part_inactive_tombstone_mask")
    assert tombstone_mask is not None
    assert tombstone_mask.status == "filled"
    assert tombstone_mask.module == "lawvm.core.timeline"
    apply_facade = cell_by_id("apply_intent_facade")
    assert apply_facade is not None
    assert apply_facade.status == "filled"
    assert apply_facade.module == "lawvm.finland.apply_intent_facade"
    timeline_hook = cell_by_id("timeline_invariants_hook")
    assert timeline_hook is not None
    assert timeline_hook.status == "filled"


def test_finland_periodic_table_summary_groups_by_axis() -> None:
    summary = periodic_table_summary()
    assert summary["catalog_kind"] == "finland_periodic_table"
    axes = summary["axes"]
    assert isinstance(axes, dict)
    assert "evidence" in axes
    assert "instrumentation" in axes
    assert cast(dict[str, int], summary["status_counts"]).get("hole", 0) == 0


def test_finland_periodic_table_markdown_renders_table() -> None:
    md = render_finland_periodic_table_markdown()
    assert "| evidence | strict_report_proof_projector |" in md
    assert "identity_ledger" in md
    assert "apply_intent_facade" in md


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
    assert rows[0]["agreement_residual_status"] == "agrees"
    assert rows[0]["family"] == "agreement"


def test_strict_report_proof_projector_emits_four_candidate_sets() -> None:
    certs = finland_strict_report_candidate_set_coverages(
        {"statute_id": "1991/3", "ops": {"canonical": 0, "failed": 0, "total": 0}}
    )
    assert len(certs) == 4
    kinds = {row["candidate_set_kind"] for row in certs}
    assert "fi_strict_report_visible_operation_rows" in kinds


def test_sparse_slot_certificate_projector_binding_row() -> None:
    certs = sparse_slot_candidate_set_coverage_rows(
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


def test_merge_event_carries_findings_not_logger_only() -> None:
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.finland.merge import ReplaceMode, build_merge_event

    master = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="a"),),
    )
    payload = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="b"),),
    )
    result = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="a"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="b"),
        ),
    )
    event = build_merge_event(result, master, payload, ReplaceMode.SPARSE_MERGE, op_id="op-1")
    assert event.findings == build_merge_invariant_findings(
        event.violations,
        source_statute="1991/3",
        op_id="op-1",
    )


def test_uncovered_recovery_pipeline_registry_observation() -> None:
    from lawvm.finland.elaboration_rule_dispatch import UNCOVERED_BODY_RECOVERY_PIPELINE
    from lawvm.finland.elaboration_rule_registry import rule_by_id

    for rule_id in UNCOVERED_BODY_RECOVERY_PIPELINE:
        assert rule_by_id(rule_id) is not None


def test_elaboration_registry_stage_observation_is_registered() -> None:
    from lawvm.core.observation_registry import get_finding_spec
    from lawvm.finland.elaboration_rule_dispatch import emit_elaboration_stage_observation

    findings: list = []
    emit_elaboration_stage_observation(
        findings,
        rule_id="fi.uncovered.recovery_prepare",
        source_statute="2002/738",
        amendment_id="2020/1",
    )
    assert len(findings) == 1
    assert findings[0].kind == "ELAB.REGISTRY_STAGE"
    assert findings[0].detail["rule_id"] == "fi.uncovered.recovery_prepare"
    assert get_finding_spec("ELAB.REGISTRY_STAGE") is not None


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


def test_invariant_surface_matrix_declares_product_surfaces() -> None:
    surface_ids = {surface.surface_id for surface in FI_REPLAY_DIAGNOSTIC_SURFACES}
    assert "replay_fold_tree" in surface_ids
    assert "materialized_tree" in surface_ids
    assert FI_MATERIALIZED_PRODUCT_SURFACE.replay_profile.warnings


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


def test_project_evidence_unified_pass_projects_meta_findings_and_proof_rows() -> None:
    finding = Finding(
        kind="ELAB.REGISTRY_PIPELINE",
        role=OBSERVATION_ROLE,
        stage="process",
        blocking=False,
        source_statute="2002/738",
        detail={"rule_id": "fi.process.pipeline", "phase": "process"},
    )
    meta: dict[str, object] = {}
    result = project_evidence(
        EvidenceProjectionRequest(
            findings=(finding,),
            meta_projections=(
                MetaProjection(
                    meta_key="sparse_slot_bindings",
                    rows=({"slot": "1", "binding": "a"},),
                    dedup_keys=("slot",),
                ),
            ),
            proof_rows=({"proof_id": "fi:2002/738:mutation-boundary:1:op-1"},),
            replay_meta_out=meta,
        )
    )
    assert result.finding_row_count == 1
    assert result.proof_row_count == 1
    assert "sparse_slot_bindings" in result.meta_keys
    assert "replay_finding_details" in result.meta_keys
    assert "proof_rows" in result.meta_keys
    assert meta["sparse_slot_bindings"] == [{"slot": "1", "binding": "a"}]


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
