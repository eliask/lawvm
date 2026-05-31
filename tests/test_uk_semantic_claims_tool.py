from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path

import pytest

from lawvm.tools import uk_semantic_claims


def _workqueue_row(*, source_preview: str = "insert the row") -> dict[str, object]:
    source_hash = hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
    return {
        "schema": "lawvm.uk_manual_compile_frontier.v1",
        "work_item_id": "uk-manual-frontier-demo",
        "statute_id": "ukpga/2000/1",
        "effect_id": "eff-1",
        "manual_compile_rule_id": "uk_manual_frontier_table_entry_placement_insert",
        "affecting_act_id": "ukpga/2001/2",
        "affected_provisions": "s. 1",
        "affecting_provisions": "Sch. 1 para. 2",
        "source": {
            "text_preview": source_preview,
            "text_preview_sha256": source_hash,
        },
        "suggested_claim_template": {
            "action_family": "table_surface_mutation",
            "source_target_address": "section:1/table:1",
            "required_ownership": [
                "source_named_table_surface",
                "mutation_boundary",
            ],
            "required_validator_checks": [
                "claim_identifies_exact_table_carrier",
                "changed_paths_are_within_claimed_table_surface",
            ],
        },
    }


def _claim_row(*, source_preview: str = "insert the row") -> dict[str, object]:
    source_hash = hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
    return {
        "schema": "lawvm.uk_semantic_compile_claim.v1",
        "claim_id": "claim-demo",
        "claim_kind": "semantic_compile",
        "claim_status": "proposed",
        "jurisdiction": "uk",
        "statute_id": "ukpga/2000/1",
        "effect_id": "eff-1",
        "manual_compile_rule_id": "uk_manual_frontier_table_entry_placement_insert",
        "action_family": "table_surface_mutation",
        "claimant": "test-reviewer",
        "work_item_id": "uk-manual-frontier-demo",
        "affecting_act_id": "ukpga/2001/2",
        "affected_provisions": "s. 1",
        "affecting_provisions": "Sch. 1 para. 2",
        "target_context": {
            "source_target_address": "section:1/table:1",
        },
        "ownership_claims": [
            {
                "ownership_id": "source_named_table_surface",
                "status": "claimed_not_proved",
            },
            {
                "ownership_id": "mutation_boundary",
                "status": "claimed_not_proved",
            },
        ],
        "source_witness": {
            "source_preview_sha256": source_hash,
            "text_preview": source_preview,
        },
        "proposed_outcome": {
            "outcome_kind": "canonical_operations",
            "operations": [
                {
                    "op_id": "manual-op-1",
                    "action": "INSERT",
                    "target": "section:1/table:1/row:2",
                    "mutation_boundary": {
                        "changed_paths": ["section:1/table:1/row:2"],
                        "target_region": ["section:1/table:1/row:2"],
                    },
                },
            ],
            "validator_checks": [
                {
                    "check_id": "claim_identifies_exact_table_carrier",
                    "status": "claimed_not_proved",
                },
                {
                    "check_id": "changed_paths_are_within_claimed_table_surface",
                    "status": "claimed_not_proved",
                },
            ],
        },
    }


def _live_target_row(
    *,
    statute_id: str = "ukpga/2000/1",
    target_paths: list[str] | None = None,
    target_fingerprints: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": "lawvm.uk_live_target_index.v1",
        "statute_id": statute_id,
        "target_paths": target_paths
        or [
            "section:1",
            "section:1/table:1",
        ],
        "target_fingerprints": target_fingerprints or {},
    }


def test_validate_semantic_claim_accepts_schema_and_workqueue_provenance_only() -> None:
    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (_claim_row(),),
        workqueue_rows=(_workqueue_row(),),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "lawvm.uk_semantic_compile_claim_validation.v1"
    assert row["validator_status"] == "validated_provenance_only"
    assert row["rule_id"] == "uk_semantic_claim_validated_provenance_only"
    assert row["family"] == "manual_compilation"
    assert row["phase"] == "claim_validation"
    assert (
        row["validator_scope"]
        == "schema_workqueue_shape_and_declared_obligations_non_executable"
    )
    assert row["matched_work_item_id"] == "uk-manual-frontier-demo"
    assert row["proposed_outcome_kind"] == "canonical_operations"
    assert row["validation_issues"] == []
    assert row["executable"] is False
    assert row["replay_authorized"] is False
    assert row["blocking"] is False


def test_validate_semantic_claim_accepts_supplied_live_insert_parent() -> None:
    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (_claim_row(),),
        workqueue_rows=(_workqueue_row(),),
        live_target_rows=(_live_target_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_and_live_targets_only"
    assert (
        row["rule_id"]
        == "uk_semantic_claim_validated_provenance_and_live_targets_only"
    )
    assert row["live_state_checked"] is True
    assert (
        row["validator_scope"]
        == "schema_workqueue_shape_live_targets_and_declared_obligations_non_executable"
    )
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_missing_live_target_index_statute() -> None:
    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (_claim_row(),),
        live_target_rows=(_live_target_row(statute_id="ukpga/1999/9"),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_live_state_missing"
    assert row["rule_id"] == "uk_semantic_claim_live_state_missing"
    assert "no live target index row matched statute_id 'ukpga/2000/1'" in row[
        "validation_issues"
    ]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_insert_with_absent_live_parent() -> None:
    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (_claim_row(),),
        live_target_rows=(_live_target_row(target_paths=["section:1"]),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_live_state_mismatch"
    assert (
        "canonical_operations[1].target parent 'section:1/table:1' is absent "
        "from supplied live target index for insert target 'section:1/table:1/row:2'"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_replace_with_absent_live_target() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(_live_target_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_live_state_mismatch"
    assert (
        "canonical_operations[1].target 'section:1/table:1/row:2' is absent "
        "from supplied live target index"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_declared_live_target_precondition() -> None:
    target_text_hash = hashlib.sha256(b"table text").hexdigest()
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["live_target_preconditions"] = [
        {
            "path": "section:1/table:1",
            "text_sha256": target_text_hash,
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1/table:1": {
                        "text_sha256": target_text_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_live_targets_and_preconditions_only"
    )
    assert (
        row["rule_id"]
        == "uk_semantic_claim_validated_provenance_live_targets_and_preconditions_only"
    )
    assert row["live_state_checked"] is True
    assert row["live_state_preconditions_checked"] is True
    assert (
        row["validator_scope"]
        == "schema_workqueue_shape_live_target_preconditions_and_declared_obligations_non_executable"
    )
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_live_target_precondition_mismatch() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["live_target_preconditions"] = [
        {
            "path": "section:1/table:1",
            "subtree_sha256": "expected",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1/table:1": {
                        "text_sha256": hashlib.sha256(b"table text").hexdigest(),
                        "subtree_sha256": "actual",
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_live_state_mismatch"
    assert row["rule_id"] == "uk_semantic_claim_live_target_precondition_mismatch"
    assert (
        "live_target_preconditions[1].subtree_sha256 mismatch for "
        "'section:1/table:1': claim='expected' live='actual'"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_workqueue_mismatch() -> None:
    claim = _claim_row()
    claim["action_family"] = "wrong_family"
    claim["source_witness"] = {"source_preview_sha256": "wrong-hash"}

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert row["rule_id"] == "uk_semantic_claim_workqueue_mismatch"
    assert row["blocking"] is True
    assert row["strict_disposition"] == "block"
    assert any("action_family mismatch" in issue for issue in row["validation_issues"])
    assert any(
        "source_preview_sha256 mismatch" in issue
        for issue in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_missing_workqueue_provenance_field() -> None:
    claim = _claim_row()
    claim.pop("affecting_provisions")

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert "affecting_provisions is required by matched workqueue" in row[
        "validation_issues"
    ]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_claim_source_preview_hash_mismatch() -> None:
    claim = _claim_row()
    claim["source_witness"] = {
        "source_preview_sha256": "0" * 64,
        "text_preview": "insert the row",
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert any(
        issue.startswith(
            "source_witness.source_preview_sha256 does not match "
            "source_witness.text_preview"
        )
        for issue in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


@pytest.mark.parametrize(
    ("field_owner", "field_name", "value", "expected_issue"),
    [
        (
            "claim",
            "executable",
            True,
            "claim.executable cannot be true in the non-executable validator",
        ),
        (
            "claim",
            "replay_authorized",
            "true",
            "claim.replay_authorized cannot be true in the non-executable validator",
        ),
        (
            "proposed_outcome",
            "executable",
            "yes",
            "proposed_outcome.executable cannot be true in the non-executable validator",
        ),
        (
            "proposed_outcome",
            "replay_authorized",
            True,
            "proposed_outcome.replay_authorized cannot be true in the non-executable validator",
        ),
    ],
)
def test_validate_semantic_claim_rejects_authorization_assertions(
    field_owner: str,
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    claim = _claim_row()
    if field_owner == "claim":
        claim[field_name] = value
    else:
        proposed_outcome = claim["proposed_outcome"]
        assert isinstance(proposed_outcome, dict)
        proposed_outcome[field_name] = value

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert expected_issue in row["validation_issues"]
    assert row["executable"] is False
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_workqueue_source_preview_hash_mismatch() -> None:
    workqueue = _workqueue_row()
    source = workqueue["source"]
    assert isinstance(source, dict)
    source["text_preview_sha256"] = "0" * 64

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (_claim_row(),),
        workqueue_rows=(workqueue,),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert any(
        issue.startswith(
            "workqueue.source.text_preview_sha256 does not match "
            "workqueue.source.text_preview"
        )
        for issue in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_source_text_precondition() -> None:
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "contains": "entry relating to X",
            "sha256": hashlib.sha256(b"entry relating to X").hexdigest(),
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(
            _workqueue_row(source_preview="after the entry relating to X insert the row"),
        ),
    )

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_and_source_text_only"
    assert row["rule_id"] == "uk_semantic_claim_validated_provenance_and_source_text_only"
    assert row["source_text_preconditions_checked"] is True
    assert row["live_state_checked"] is False
    assert (
        row["validator_scope"]
        == "schema_workqueue_shape_source_text_preconditions_declared_obligations_non_executable"
    )
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_source_text_precondition_mismatch() -> None:
    claim = _claim_row(source_preview="insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "contains": "entry relating to X",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_source_text_mismatch"
    assert row["rule_id"] == "uk_semantic_claim_source_text_precondition_mismatch"
    assert (
        "source_text_preconditions[1].contains 'entry relating to X' is absent "
        "from supplied source text"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_operation_family_proof_refs() -> None:
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-anchor",
            "contains": "entry relating to X",
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-insert-anchor",
            "operation_family": "table_surface_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-anchor"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_and_source_text_only"
    assert row["operation_family_proofs_checked"] is True
    assert (
        row["validator_scope"]
        == "schema_workqueue_shape_source_text_preconditions_operation_family_proofs_declared_obligations_non_executable"
    )
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_malformed_operation_family_proof_refs() -> None:
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-anchor",
            "contains": "entry relating to X",
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-insert-anchor",
            "operation_family": "wrong_family",
            "operation_ids": ["missing-op"],
            "validator_check_ids": ["missing-check"],
            "source_text_precondition_ids": ["missing-source-precondition"],
            "status": "proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].operation_family mismatch: "
        "proof='wrong_family' claim='table_surface_mutation'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].status 'proved' cannot be claimed by this "
        "non-executable validator"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].operation_ids references unknown operation "
        "'missing-op'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].validator_check_ids references undeclared check "
        "'missing-check'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_text_precondition_ids references unknown "
        "precondition 'missing-source-precondition'"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_table_insert_family_proof_semantic() -> None:
    target_text_hash = hashlib.sha256(b"table text").hexdigest()
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-anchor",
            "contains": "entry relating to X",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-table-carrier",
            "path": "section:1/table:1",
            "text_sha256": target_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-insert-anchor",
            "proof_semantic": "table_surface_insert_anchor_and_live_carrier",
            "operation_family": "table_surface_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-anchor"],
            "live_target_precondition_ids": ["live-table-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1/table:1": {
                        "text_sha256": target_text_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["operation_family_proof_count"] == 1
    assert row["operation_family_proof_semantics"] == [
        "table_surface_insert_anchor_and_live_carrier",
    ]
    assert row["operation_family_proof_families"] == ["table_surface_mutation"]
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_table_insert_family_proof_semantic_gap() -> None:
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-anchor",
            "contains": "entry relating to X",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "wrong-live-carrier",
            "path": "section:1/table:2",
            "text_sha256": hashlib.sha256(b"other table").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-insert-anchor",
            "proof_semantic": "table_surface_insert_anchor_and_live_carrier",
            "operation_family": "table_surface_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-anchor"],
            "live_target_precondition_ids": ["wrong-live-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].table_surface_insert_anchor_and_live_carrier "
        "operation 'manual-op-1' target parent 'section:1/table:1' is outside "
        "declared live carrier preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_text_rewrite_family_proof_semantic() -> None:
    target_text_hash = hashlib.sha256(b"old heading").hexdigest()
    claim = _claim_row(source_preview='for "old heading" substitute "new heading"')
    claim["action_family"] = "facet_text_rewrite"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:1"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1"],
        "target_region": ["section:1"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-preimage",
            "contains": "old heading",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-heading-target",
            "path": "section:1",
            "text_sha256": target_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-heading-text-rewrite",
            "proof_semantic": "text_rewrite_source_preimage_and_live_target",
            "operation_family": "facet_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-preimage"],
            "live_target_precondition_ids": ["live-heading-target"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1": {
                        "text_sha256": target_text_hash,
                        "subtree_sha256": "b" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_text_rewrite_family_proof_semantic_gap() -> None:
    claim = _claim_row(source_preview='for "old heading" substitute "new heading"')
    claim["action_family"] = "facet_text_rewrite"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/table:1/row:2"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/table:1/row:2"],
        "target_region": ["section:1/table:1/row:2"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-preimage",
            "contains": "old heading",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-heading-target",
            "path": "section:1",
            "text_sha256": hashlib.sha256(b"old heading").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-heading-text-rewrite",
            "proof_semantic": "text_rewrite_source_preimage_and_live_target",
            "operation_family": "facet_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-preimage"],
            "live_target_precondition_ids": ["live-heading-target"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].text_rewrite_source_preimage_and_live_target "
        "operation 'manual-op-1' must be a text or heading rewrite action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].text_rewrite_source_preimage_and_live_target "
        "operation 'manual-op-1' target 'section:1/table:1/row:2' is outside "
        "declared live target preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_structural_insert_family_proof_semantic() -> None:
    parent_text_hash = hashlib.sha256(b"parent text").hexdigest()
    claim = _claim_row(source_preview="before subsection 2 insert subsection 1A")
    claim["action_family"] = "structural_sibling_insert"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:1A"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:1A"],
        "target_region": ["section:1/subsection:1A"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-carries-inserted-payload",
            "contains": "insert subsection 1A",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-parent-carrier",
            "path": "section:1",
            "text_sha256": parent_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-structural-insert-payload",
            "proof_semantic": "structural_insert_source_payload_and_live_parent",
            "operation_family": "structural_sibling_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-carries-inserted-payload"],
            "live_target_precondition_ids": ["live-parent-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1": {
                        "text_sha256": parent_text_hash,
                        "subtree_sha256": "c" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_structural_insert_family_proof_semantic_gap() -> None:
    claim = _claim_row(source_preview="before subsection 2 insert subsection 1A")
    claim["action_family"] = "structural_sibling_insert"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:1A"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:1A"],
        "target_region": ["section:1/subsection:1A"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-carries-inserted-payload",
            "contains": "insert subsection 1A",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "wrong-live-parent",
            "path": "section:2",
            "text_sha256": hashlib.sha256(b"other parent").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-structural-insert-payload",
            "proof_semantic": "structural_insert_source_payload_and_live_parent",
            "operation_family": "structural_sibling_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-carries-inserted-payload"],
            "live_target_precondition_ids": ["wrong-live-parent"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].structural_insert_source_payload_and_live_parent "
        "operation 'manual-op-1' target parent 'section:1' is outside declared "
        "live parent preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_schedule_list_entry_family_proof_semantic() -> None:
    carrier_hash = hashlib.sha256(b"schedule entries").hexdigest()
    claim = _claim_row(
        source_preview='after the entry relating to "X" insert "Y"',
    )
    claim["action_family"] = "schedule_list_entry_mutation"
    claim["ownership_claims"] = [
        {"ownership_id": "source_named_entry_anchor", "status": "claimed_not_proved"},
        {"ownership_id": "entry_carrier", "status": "claimed_not_proved"},
        {
            "ownership_id": "sibling_insertion_or_replacement_boundary",
            "status": "claimed_not_proved",
        },
        {"ownership_id": "mutation_boundary", "status": "claimed_not_proved"},
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "schedule:1/entry:Y"
    operation["entry_anchor"] = "entry relating to X"
    operation["schedule_entry_label"] = "Y"
    operation["mutation_boundary"] = {
        "changed_paths": ["schedule:1/entry:Y"],
        "target_region": ["schedule:1/entry:Y"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "entry-anchor", "contains": "entry relating to"},
        {"precondition_id": "entry-payload", "contains": 'insert "Y"'},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-entry-carrier",
            "path": "schedule:1",
            "text_sha256": carrier_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-schedule-list-entry",
            "proof_semantic": "schedule_list_entry_anchor_boundary_claim",
            "operation_family": "schedule_list_entry_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["entry-anchor", "entry-payload"],
            "entry_anchor_precondition_ids": ["entry-anchor"],
            "entry_payload_precondition_ids": ["entry-payload"],
            "entry_ownership_ids": [
                "source_named_entry_anchor",
                "entry_carrier",
                "sibling_insertion_or_replacement_boundary",
            ],
            "live_target_precondition_ids": ["live-entry-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["schedule:1"],
                target_fingerprints={
                    "schedule:1": {
                        "text_sha256": carrier_hash,
                        "subtree_sha256": "f" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_schedule_list_entry_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='after the entry relating to "X" insert "Y"',
    )
    claim["action_family"] = "schedule_list_entry_mutation"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "schedule:2/entry:Y"
    operation["mutation_boundary"] = {
        "changed_paths": ["schedule:2/entry:Y"],
        "target_region": ["schedule:2/entry:Y"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "entry-anchor", "contains": "entry relating to"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-entry-carrier",
            "path": "schedule:1",
            "text_sha256": hashlib.sha256(b"schedule entries").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-schedule-list-entry",
            "proof_semantic": "schedule_list_entry_anchor_boundary_claim",
            "operation_family": "schedule_list_entry_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["entry-anchor"],
            "entry_anchor_precondition_ids": ["entry-anchor"],
            "live_target_precondition_ids": ["live-entry-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "requires entry_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "requires entry_ownership_ids to include 'source_named_entry_anchor'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "operation 'manual-op-1' must be an entry insert or replacement action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "operation 'manual-op-1' must declare an entry anchor or insertion "
        "position"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "operation 'manual-op-1' must declare a schedule entry label or text"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].schedule_list_entry_anchor_boundary_claim "
        "operation 'manual-op-1' target 'schedule:2/entry:Y' is outside "
        "declared live schedule-entry preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_definition_entry_insert_family_proof_semantic() -> None:
    list_hash = hashlib.sha256(b"definition list").hexdigest()
    claim = _claim_row(
        source_preview='at the end insert- "registered provider" means X',
    )
    claim["action_family"] = "definition_entry_insert"
    claim["ownership_claims"] = [
        {
            "ownership_id": "inserted_definition_term_identity",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "complete_definition_entry_payload",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "definition_list_target_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "insertion_position_or_list_end_boundary",
            "status": "claimed_not_proved",
        },
        {"ownership_id": "mutation_boundary", "status": "claimed_not_proved"},
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/definition:registered provider"
    operation["inserted_definition_term"] = "registered provider"
    operation["definition_entry_text"] = '"registered provider" means X'
    operation["list_end_boundary"] = "definition-list-end"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/definition:registered provider"],
        "target_region": ["section:1/definition:registered provider"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "definition-term", "contains": "registered provider"},
        {"precondition_id": "definition-payload", "contains": "means X"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition-list",
            "path": "section:1",
            "text_sha256": list_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-entry-insert",
            "proof_semantic": "definition_entry_insert_term_boundary_claim",
            "operation_family": "definition_entry_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "definition-term",
                "definition-payload",
            ],
            "definition_term_precondition_ids": ["definition-term"],
            "definition_entry_payload_precondition_ids": ["definition-payload"],
            "definition_ownership_ids": [
                "inserted_definition_term_identity",
                "complete_definition_entry_payload",
                "definition_list_target_boundary",
                "insertion_position_or_list_end_boundary",
            ],
            "live_target_precondition_ids": ["live-definition-list"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1"],
                target_fingerprints={
                    "section:1": {
                        "text_sha256": list_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_definition_entry_insert_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='at the end insert- "registered provider" means X',
    )
    claim["action_family"] = "definition_entry_insert"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:2/definition:registered provider"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/definition:registered provider"],
        "target_region": ["section:2/definition:registered provider"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "definition-term", "contains": "registered provider"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition-list",
            "path": "section:1",
            "text_sha256": hashlib.sha256(b"definition list").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-entry-insert",
            "proof_semantic": "definition_entry_insert_term_boundary_claim",
            "operation_family": "definition_entry_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["definition-term"],
            "definition_term_precondition_ids": ["definition-term"],
            "live_target_precondition_ids": ["live-definition-list"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "requires definition_entry_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "requires definition_ownership_ids to include "
        "'inserted_definition_term_identity'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "operation 'manual-op-1' must be an INSERT"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "operation 'manual-op-1' must declare an inserted definition term"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "operation 'manual-op-1' must declare a definition entry payload"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "operation 'manual-op-1' must declare an insertion anchor, position, or "
        "list-end boundary"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_entry_insert_term_boundary_claim "
        "operation 'manual-op-1' target 'section:2/definition:registered provider' "
        "is outside declared live definition-list preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_savings_qualified_omission_family_proof_semantic() -> None:
    target_text_hash = hashlib.sha256(b"text with saved reference").hexdigest()
    claim = _claim_row(
        source_preview=(
            "omit the reference to the Magistrates' Courts Act 1980 except in "
            "the case of proceedings begun before commencement"
        ),
    )
    claim["action_family"] = "savings_qualified_text_omission"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPEAL"
    operation["target"] = "section:1"
    operation["applicability_scope"] = {
        "savings_condition": "except in the case of proceedings begun before commencement",
    }
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1"],
        "target_region": ["section:1"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-omitted-reference",
            "contains": "reference to the Magistrates' Courts Act 1980",
        },
        {
            "precondition_id": "source-names-savings-condition",
            "contains": "except in the case of proceedings",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-text-carrier",
            "path": "section:1",
            "text_sha256": target_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-savings-qualified-omission",
            "proof_semantic": "savings_qualified_omission_applicability_scope",
            "operation_family": "savings_qualified_text_omission",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-names-omitted-reference",
                "source-names-savings-condition",
            ],
            "omitted_reference_precondition_ids": [
                "source-names-omitted-reference",
            ],
            "savings_condition_precondition_ids": [
                "source-names-savings-condition",
            ],
            "live_target_precondition_ids": ["live-text-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1": {
                        "text_sha256": target_text_hash,
                        "subtree_sha256": "d" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_unscoped_savings_qualified_omission_proof_semantic() -> None:
    claim = _claim_row(
        source_preview=(
            "omit the reference to the Magistrates' Courts Act 1980 except in "
            "the case of proceedings begun before commencement"
        ),
    )
    claim["action_family"] = "savings_qualified_text_omission"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "section:1"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1"],
        "target_region": ["section:1"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-omitted-reference",
            "contains": "reference to the Magistrates' Courts Act 1980",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "wrong-live-text-carrier",
            "path": "section:2",
            "text_sha256": hashlib.sha256(b"other text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-savings-qualified-omission",
            "proof_semantic": "savings_qualified_omission_applicability_scope",
            "operation_family": "savings_qualified_text_omission",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-omitted-reference"],
            "omitted_reference_precondition_ids": [
                "source-names-omitted-reference",
            ],
            "live_target_precondition_ids": ["wrong-live-text-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].savings_qualified_omission_applicability_scope "
        "requires savings_condition_precondition_ids or "
        "applicability_scope_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].savings_qualified_omission_applicability_scope "
        "operation 'manual-op-1' must be a text omission action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].savings_qualified_omission_applicability_scope "
        "operation 'manual-op-1' must declare applicability_scope or "
        "savings_condition"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].savings_qualified_omission_applicability_scope "
        "operation 'manual-op-1' target 'section:1' is outside declared live "
        "target preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_whole_act_listed_enactments_family_proof_semantic() -> None:
    target_text_hash = hashlib.sha256(b"text with old phrase").hexdigest()
    claim = _claim_row(
        source_preview=(
            'in the enactments listed in Schedule 1, for "old phrase" '
            'substitute "new phrase", except words amended by paragraph 4'
        ),
    )
    claim["action_family"] = "whole_act_listed_enactments_text_patch"
    claim["ownership_claims"] = [
        {
            "ownership_id": "same_schedule_and_same_act_exclusions",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:1"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1"],
        "target_region": ["section:1"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-lists-affected-enactment",
            "contains": "enactments listed in Schedule 1",
        },
        {
            "precondition_id": "source-names-quoted-preimage",
            "contains": "old phrase",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-text-carrier",
            "path": "section:1",
            "text_sha256": target_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-whole-act-listed-enactment",
            "proof_semantic": "whole_act_listed_enactments_scope_and_exclusions",
            "operation_family": "whole_act_listed_enactments_text_patch",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-lists-affected-enactment",
                "source-names-quoted-preimage",
            ],
            "list_membership_precondition_ids": [
                "source-lists-affected-enactment",
            ],
            "quoted_preimage_precondition_ids": [
                "source-names-quoted-preimage",
            ],
            "exclusion_ownership_ids": [
                "same_schedule_and_same_act_exclusions",
            ],
            "excluded_surface_families": ["title_or_short_title"],
            "live_target_precondition_ids": ["live-text-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_fingerprints={
                    "section:1": {
                        "text_sha256": target_text_hash,
                        "subtree_sha256": "e" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_whole_act_listed_enactments_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview=(
            'in the enactments listed in Schedule 1, for "old phrase" '
            'substitute "new phrase"'
        ),
    )
    claim["action_family"] = "whole_act_listed_enactments_text_patch"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "title:short"
    operation["mutation_boundary"] = {
        "changed_paths": ["title:short"],
        "target_region": ["title:short"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-lists-affected-enactment",
            "contains": "enactments listed in Schedule 1",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-text-carrier",
            "path": "section:1",
            "text_sha256": hashlib.sha256(b"text with old phrase").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-whole-act-listed-enactment",
            "proof_semantic": "whole_act_listed_enactments_scope_and_exclusions",
            "operation_family": "whole_act_listed_enactments_text_patch",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-lists-affected-enactment"],
            "list_membership_precondition_ids": [
                "source-lists-affected-enactment",
            ],
            "live_target_precondition_ids": ["live-text-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].whole_act_listed_enactments_scope_and_exclusions "
        "requires quoted_preimage_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].whole_act_listed_enactments_scope_and_exclusions "
        "requires exclusion_ownership_ids to include "
        "'same_schedule_and_same_act_exclusions'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].whole_act_listed_enactments_scope_and_exclusions "
        "requires excluded_surface_families to include 'title_or_short_title'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].whole_act_listed_enactments_scope_and_exclusions "
        "operation 'manual-op-1' must be a whole-Act text patch action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].whole_act_listed_enactments_scope_and_exclusions "
        "operation 'manual-op-1' target 'title:short' is an excluded title or "
        "short-title surface"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_appropriate_place_family_proof_semantic() -> None:
    parent_text_hash = hashlib.sha256(b"parent text").hexdigest()
    anchor_text_hash = hashlib.sha256(b"anchor text").hexdigest()
    claim = _claim_row(
        source_preview='at the appropriate place insert "new listed entry"',
    )
    claim["action_family"] = "appropriate_place_mutation"
    claim["ownership_claims"] = [
        {
            "ownership_id": "validated_predecessor_or_successor_anchor",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "target_container_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:1A"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:1A"],
        "target_region": ["section:1/subsection:1A"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-uses-appropriate-place",
            "contains": "appropriate place",
        },
        {
            "precondition_id": "source-carries-payload",
            "contains": "new listed entry",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-parent-carrier",
            "path": "section:1",
            "text_sha256": parent_text_hash,
        },
        {
            "precondition_id": "live-anchor",
            "path": "section:1/subsection:1",
            "text_sha256": anchor_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-appropriate-place-anchor",
            "proof_semantic": "appropriate_place_anchor_or_ordering_claim",
            "operation_family": "appropriate_place_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-uses-appropriate-place",
                "source-carries-payload",
            ],
            "payload_precondition_ids": ["source-carries-payload"],
            "anchor_or_ordering_ownership_ids": [
                "validated_predecessor_or_successor_anchor",
            ],
            "live_target_precondition_ids": [
                "live-parent-carrier",
                "live-anchor",
            ],
            "anchor_live_target_precondition_ids": ["live-anchor"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1", "section:1/subsection:1"],
                target_fingerprints={
                    "section:1": {
                        "text_sha256": parent_text_hash,
                        "subtree_sha256": "f" * 64,
                    },
                    "section:1/subsection:1": {
                        "text_sha256": anchor_text_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_appropriate_place_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='at the appropriate place insert "new listed entry"',
    )
    claim["action_family"] = "appropriate_place_mutation"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:2/subsection:1A"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/subsection:1A"],
        "target_region": ["section:2/subsection:1A"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-uses-appropriate-place",
            "contains": "appropriate place",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-parent-carrier",
            "path": "section:1",
            "text_sha256": hashlib.sha256(b"parent text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-appropriate-place-anchor",
            "proof_semantic": "appropriate_place_anchor_or_ordering_claim",
            "operation_family": "appropriate_place_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-uses-appropriate-place"],
            "live_target_precondition_ids": ["live-parent-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].appropriate_place_anchor_or_ordering_claim "
        "requires payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].appropriate_place_anchor_or_ordering_claim "
        "requires anchor_or_ordering_ownership_ids to include "
        "'validated_predecessor_or_successor_anchor'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].appropriate_place_anchor_or_ordering_claim "
        "requires anchor_live_target_precondition_ids, "
        "anchor_live_target_precondition_paths, or ordering_rule_id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].appropriate_place_anchor_or_ordering_claim "
        "operation 'manual-op-1' must be an INSERT"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].appropriate_place_anchor_or_ordering_claim "
        "operation 'manual-op-1' target parent 'section:2' is outside declared "
        "live parent preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_range_to_container_family_proof_semantic() -> None:
    container_text_hash = hashlib.sha256(b"chapter container").hexdigest()
    claim = _claim_row(
        source_preview=(
            "for sections 3 to 12 and the cross-heading substitute Chapter 1 "
            "Bus services improvement partnerships"
        ),
    )
    claim["action_family"] = "range_to_container_substitution"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_range",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "container_payload",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "lineage_or_migration_events",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "part:2/chapter:1"
    operation["mutation_boundary"] = {
        "changed_paths": ["part:2/chapter:1"],
        "target_region": ["part:2/chapter:1"],
        "declared_migration_paths": ["section:3", "section:4"],
        "migration_event_id": "migration-range-container-1",
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-range",
            "contains": "sections 3 to 12",
        },
        {
            "precondition_id": "source-carries-container-payload",
            "contains": "Chapter 1 Bus services improvement partnerships",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-container",
            "path": "part:2/chapter:1",
            "text_sha256": container_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-range-to-container",
            "proof_semantic": "range_to_container_source_range_payload_and_lineage",
            "operation_family": "range_to_container_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-names-range",
                "source-carries-container-payload",
            ],
            "source_range_precondition_ids": ["source-names-range"],
            "container_payload_precondition_ids": [
                "source-carries-container-payload",
            ],
            "migration_ownership_ids": ["lineage_or_migration_events"],
            "live_target_precondition_ids": ["live-container"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["part:2/chapter:1"],
                target_fingerprints={
                    "part:2/chapter:1": {
                        "text_sha256": container_text_hash,
                        "subtree_sha256": "b" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_range_to_container_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview=(
            "for sections 3 to 12 and the cross-heading substitute Chapter 1 "
            "Bus services improvement partnerships"
        ),
    )
    claim["action_family"] = "range_to_container_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "part:3/chapter:1"
    operation["mutation_boundary"] = {
        "changed_paths": ["part:3/chapter:1"],
        "target_region": ["part:3/chapter:1"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-range",
            "contains": "sections 3 to 12",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-container",
            "path": "part:2/chapter:1",
            "text_sha256": hashlib.sha256(b"chapter container").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-range-to-container",
            "proof_semantic": "range_to_container_source_range_payload_and_lineage",
            "operation_family": "range_to_container_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-range"],
            "source_range_precondition_ids": ["source-names-range"],
            "live_target_precondition_ids": ["live-container"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "requires container_payload_precondition_ids or payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "requires migration_ownership_ids to include 'lineage_or_migration_events'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "operation 'manual-op-1' must be a REPLACE"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "operation 'manual-op-1' must declare migration paths"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "operation 'manual-op-1' must declare a lineage or migration event id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].range_to_container_source_range_payload_and_lineage "
        "operation 'manual-op-1' target 'part:3/chapter:1' is outside declared "
        "live container preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_table_repeal_or_omission_family_proof_semantic() -> None:
    table_text_hash = hashlib.sha256(b"table text").hexdigest()
    claim = _claim_row(source_preview="In the table, omit the entry for old licence.")
    claim["action_family"] = "table_repeal_or_omission"
    claim["ownership_claims"] = [
        {
            "ownership_id": "repealed_row_column_or_cell_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "unclaimed_table_surface_preservation",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "section:1/table:1/row:2"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/table:1/row:2"],
        "target_region": ["section:1/table:1/row:2"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-table",
            "contains": "In the table",
        },
        {
            "precondition_id": "source-names-repealed-entry",
            "contains": "entry for old licence",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-table-carrier",
            "path": "section:1/table:1",
            "text_sha256": table_text_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-repeal-boundary",
            "proof_semantic": "table_repeal_or_omission_boundary_preservation",
            "operation_family": "table_repeal_or_omission",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-names-table",
                "source-names-repealed-entry",
            ],
            "table_surface_precondition_ids": ["source-names-table"],
            "repealed_boundary_ownership_ids": [
                "repealed_row_column_or_cell_boundary",
                "unclaimed_table_surface_preservation",
            ],
            "live_target_precondition_ids": ["live-table-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "section:1",
                    "section:1/table:1",
                    "section:1/table:1/row:2",
                ],
                target_fingerprints={
                    "section:1/table:1": {
                        "text_sha256": table_text_hash,
                        "subtree_sha256": "c" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_table_repeal_or_omission_family_proof_semantic_gap() -> None:
    claim = _claim_row(source_preview="In the table, omit the entry for old licence.")
    claim["action_family"] = "table_repeal_or_omission"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:2/table:1/row:2"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/table:1/row:2"],
        "target_region": ["section:2/table:1/row:2"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-table",
            "contains": "In the table",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-table-carrier",
            "path": "section:1/table:1",
            "text_sha256": hashlib.sha256(b"table text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-repeal-boundary",
            "proof_semantic": "table_repeal_or_omission_boundary_preservation",
            "operation_family": "table_repeal_or_omission",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-table"],
            "live_target_precondition_ids": ["live-table-carrier"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].table_repeal_or_omission_boundary_preservation "
        "requires table_surface_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].table_repeal_or_omission_boundary_preservation "
        "requires repealed_boundary_ownership_ids to include "
        "'repealed_row_column_or_cell_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].table_repeal_or_omission_boundary_preservation "
        "requires repealed_boundary_ownership_ids to include "
        "'unclaimed_table_surface_preservation'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].table_repeal_or_omission_boundary_preservation "
        "operation 'manual-op-1' must be a table repeal or text omission action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].table_repeal_or_omission_boundary_preservation "
        "operation 'manual-op-1' target 'section:2/table:1/row:2' is outside "
        "declared live table preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_cross_container_renumber_family_proof_semantic() -> None:
    source_hash = hashlib.sha256(b"source schedule").hexdigest()
    destination_hash = hashlib.sha256(b"destination schedule").hexdigest()
    claim = _claim_row(
        source_preview="Schedule 22 paragraph 88 is renumbered as Schedule 2 paragraph 88(1).",
    )
    claim["action_family"] = "cross_container_renumber_migration"
    claim["ownership_claims"] = [
        {
            "ownership_id": "lineage_or_migration_events",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "cross_container_destination_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "RENUMBER"
    operation["target"] = "schedule:22/paragraph:88"
    operation["destination"] = "schedule:2/paragraph:88/subparagraph:1"
    operation["mutation_boundary"] = {
        "changed_paths": [
            "schedule:22/paragraph:88",
            "schedule:2/paragraph:88/subparagraph:1",
        ],
        "target_region": ["schedule:22/paragraph:88"],
        "declared_migration_paths": [
            "schedule:22/paragraph:88",
            "schedule:2/paragraph:88/subparagraph:1",
        ],
        "migration_event_id": "migration-cross-container-1",
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-target",
            "contains": "Schedule 22 paragraph 88",
        },
        {
            "precondition_id": "destination-target",
            "contains": "Schedule 2 paragraph 88(1)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-source-schedule",
            "path": "schedule:22",
            "text_sha256": source_hash,
        },
        {
            "precondition_id": "live-destination-schedule",
            "path": "schedule:2",
            "text_sha256": destination_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-cross-container-renumber",
            "proof_semantic": (
                "cross_container_renumber_source_destination_and_lineage"
            ),
            "operation_family": "cross_container_renumber_migration",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-target",
                "destination-target",
            ],
            "source_target_precondition_ids": ["source-target"],
            "destination_target_precondition_ids": ["destination-target"],
            "migration_ownership_ids": [
                "lineage_or_migration_events",
                "cross_container_destination_boundary",
            ],
            "live_target_precondition_ids": [
                "live-source-schedule",
                "live-destination-schedule",
            ],
            "source_live_target_precondition_paths": ["schedule:22"],
            "destination_live_target_precondition_paths": ["schedule:2"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "schedule:22",
                    "schedule:22/paragraph:88",
                    "schedule:2",
                    "schedule:2/paragraph:88",
                ],
                target_fingerprints={
                    "schedule:22": {
                        "text_sha256": source_hash,
                        "subtree_sha256": "d" * 64,
                    },
                    "schedule:2": {
                        "text_sha256": destination_hash,
                        "subtree_sha256": "e" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_cross_container_renumber_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview="Schedule 22 paragraph 88 is renumbered as Schedule 2 paragraph 88(1).",
    )
    claim["action_family"] = "cross_container_renumber_migration"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "schedule:23/paragraph:88"
    operation["mutation_boundary"] = {
        "changed_paths": ["schedule:23/paragraph:88"],
        "target_region": ["schedule:23/paragraph:88"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-target",
            "contains": "Schedule 22 paragraph 88",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-source-schedule",
            "path": "schedule:22",
            "text_sha256": hashlib.sha256(b"source schedule").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-cross-container-renumber",
            "proof_semantic": (
                "cross_container_renumber_source_destination_and_lineage"
            ),
            "operation_family": "cross_container_renumber_migration",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-target"],
            "source_target_precondition_ids": ["source-target"],
            "source_live_target_precondition_paths": ["schedule:22"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "requires destination_target_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "requires migration_ownership_ids to include 'lineage_or_migration_events'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "requires migration_ownership_ids to include "
        "'cross_container_destination_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "requires destination_live_target_precondition_paths"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "operation 'manual-op-1' must be a RENUMBER"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "operation 'manual-op-1' must declare migration paths"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "operation 'manual-op-1' must declare a lineage or migration event id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "operation 'manual-op-1' must declare a destination"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].cross_container_renumber_source_destination_and_lineage "
        "operation 'manual-op-1' target 'schedule:23/paragraph:88' is outside "
        "declared source live preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_amendment_program_family_proof_semantic() -> None:
    program_hash = hashlib.sha256(b"amendment program text").hexdigest()
    claim = _claim_row(
        source_preview="In paragraph (a), after sub-paragraph (ii) insert item (iia).",
    )
    claim["action_family"] = "amendment_program_target_mutation"
    claim["ownership_claims"] = [
        {
            "ownership_id": "amendment_program_target_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "payload_ownership",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "schedule:1/paragraph:2/subparagraph:a/item:iia"
    operation["amendment_program_target_id"] = "amendment-program-target-1"
    operation["mutation_boundary"] = {
        "changed_paths": ["schedule:1/paragraph:2/subparagraph:a/item:iia"],
        "target_region": ["schedule:1/paragraph:2/subparagraph:a/item:iia"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-program-target",
            "contains": "paragraph (a), after sub-paragraph (ii)",
        },
        {
            "precondition_id": "source-inserted-payload",
            "contains": "insert item (iia)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-program-parent",
            "path": "schedule:1/paragraph:2/subparagraph:a",
            "text_sha256": program_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-amendment-program-target",
            "proof_semantic": (
                "amendment_program_target_source_payload_and_boundary"
            ),
            "operation_family": "amendment_program_target_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "source-program-target",
                "source-inserted-payload",
            ],
            "source_target_precondition_ids": ["source-program-target"],
            "inserted_payload_precondition_ids": ["source-inserted-payload"],
            "boundary_ownership_ids": [
                "amendment_program_target_boundary",
                "payload_ownership",
            ],
            "live_target_precondition_ids": ["live-program-parent"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "schedule:1/paragraph:2/subparagraph:a",
                ],
                target_fingerprints={
                    "schedule:1/paragraph:2/subparagraph:a": {
                        "text_sha256": program_hash,
                        "subtree_sha256": "f" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_amendment_program_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview="In paragraph (a), after sub-paragraph (ii) insert item (iia).",
    )
    claim["action_family"] = "amendment_program_target_mutation"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "schedule:2/paragraph:2/subparagraph:a/item:iia"
    operation["mutation_boundary"] = {
        "changed_paths": ["schedule:2/paragraph:2/subparagraph:a/item:iia"],
        "target_region": ["schedule:2/paragraph:2/subparagraph:a/item:iia"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-program-target",
            "contains": "paragraph (a), after sub-paragraph (ii)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-program-parent",
            "path": "schedule:1/paragraph:2/subparagraph:a",
            "text_sha256": hashlib.sha256(b"amendment program text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-amendment-program-target",
            "proof_semantic": (
                "amendment_program_target_source_payload_and_boundary"
            ),
            "operation_family": "amendment_program_target_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-program-target"],
            "source_target_precondition_ids": ["source-program-target"],
            "live_target_precondition_ids": ["live-program-parent"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "requires inserted_payload_precondition_ids or payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "requires boundary_ownership_ids to include "
        "'amendment_program_target_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "requires boundary_ownership_ids to include 'payload_ownership'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "operation 'manual-op-1' must be an amendment-program insert or "
        "replacement action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "operation 'manual-op-1' must declare an amendment program target id "
        "or source target"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].amendment_program_target_source_payload_and_boundary "
        "operation 'manual-op-1' target "
        "'schedule:2/paragraph:2/subparagraph:a/item:iia' is outside declared "
        "live amendment-program preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_definition_child_text_tail_family_proof_semantic() -> None:
    definition_hash = hashlib.sha256(b"definition text").hexdigest()
    claim = _claim_row(
        source_preview=(
            'for paragraph (d) of the definition of "NHS body in England" '
            'and the "or" at the end substitute new text'
        ),
    )
    claim["action_family"] = "definition_child_and_tail_substitution"
    claim["ownership_claims"] = [
        {
            "ownership_id": "definition_child_text_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "post_child_tail_connector_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "replacement_payload",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:15/subsection:7/definition:NHS body in England/paragraph:d"
    operation["definition_term"] = "NHS body in England"
    operation["definition_child_label"] = "paragraph d"
    operation["mutation_boundary"] = {
        "changed_paths": [
            "section:15/subsection:7/definition:NHS body in England/paragraph:d",
        ],
        "target_region": [
            "section:15/subsection:7/definition:NHS body in England/paragraph:d",
        ],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "definition-term",
            "contains": 'definition of "NHS body in England"',
        },
        {
            "precondition_id": "definition-child",
            "contains": "paragraph (d)",
        },
        {
            "precondition_id": "tail-connector",
            "contains": '"or" at the end',
        },
        {
            "precondition_id": "replacement-payload",
            "contains": "substitute new text",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:15/subsection:7/definition:NHS body in England",
            "text_sha256": definition_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-tail",
            "proof_semantic": "definition_child_text_tail_boundary_claim",
            "operation_family": "definition_child_and_tail_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "definition-term",
                "definition-child",
                "tail-connector",
                "replacement-payload",
            ],
            "definition_term_precondition_ids": ["definition-term"],
            "definition_child_precondition_ids": ["definition-child"],
            "tail_connector_precondition_ids": ["tail-connector"],
            "replacement_payload_precondition_ids": ["replacement-payload"],
            "boundary_ownership_ids": [
                "definition_child_text_boundary",
                "post_child_tail_connector_boundary",
                "replacement_payload",
            ],
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "section:15/subsection:7/definition:NHS body in England",
                    (
                        "section:15/subsection:7/definition:NHS body in England/"
                        "paragraph:d"
                    ),
                ],
                target_fingerprints={
                    "section:15/subsection:7/definition:NHS body in England": {
                        "text_sha256": definition_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_definition_child_text_tail_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview=(
            'for paragraph (d) of the definition of "NHS body in England" '
            'and the "or" at the end substitute new text'
        ),
    )
    claim["action_family"] = "definition_child_and_tail_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:16/subsection:7/definition:NHS body in England/paragraph:d"
    operation["mutation_boundary"] = {
        "changed_paths": [
            "section:16/subsection:7/definition:NHS body in England/paragraph:d",
        ],
        "target_region": [
            "section:16/subsection:7/definition:NHS body in England/paragraph:d",
        ],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "definition-term",
            "contains": 'definition of "NHS body in England"',
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:15/subsection:7/definition:NHS body in England",
            "text_sha256": hashlib.sha256(b"definition text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-tail",
            "proof_semantic": "definition_child_text_tail_boundary_claim",
            "operation_family": "definition_child_and_tail_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["definition-term"],
            "definition_term_precondition_ids": ["definition-term"],
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "requires definition_child_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "requires replacement_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "requires tail_connector_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "requires boundary_ownership_ids to include "
        "'definition_child_text_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "operation 'manual-op-1' must be a bounded definition-child text "
        "replacement action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "operation 'manual-op-1' must declare a definition term"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "operation 'manual-op-1' must declare a definition child label"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_text_tail_boundary_claim "
        "operation 'manual-op-1' target "
        "'section:16/subsection:7/definition:NHS body in England/paragraph:d' "
        "is outside declared live definition preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_definition_child_structural_family_proof_semantic() -> None:
    definition_hash = hashlib.sha256(b"definition text").hexdigest()
    claim = _claim_row(
        source_preview=(
            'in the definition of "relevant authority", for paragraph (a) '
            'substitute structured child payload'
        ),
    )
    claim["action_family"] = "definition_child_structural_substitution"
    claim["ownership_claims"] = [
        {
            "ownership_id": "definition_term_scope",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "definition_child_identity",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "replacement_child_payload_shape",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "post_child_tail_connector_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:177/subsection:6/definition:relevant authority/paragraph:a"
    operation["definition_term"] = "relevant authority"
    operation["definition_child_label"] = "paragraph a"
    operation["mutation_boundary"] = {
        "changed_paths": [
            "section:177/subsection:6/definition:relevant authority/paragraph:a",
        ],
        "target_region": [
            "section:177/subsection:6/definition:relevant authority/paragraph:a",
        ],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "definition-term",
            "contains": 'definition of "relevant authority"',
        },
        {
            "precondition_id": "definition-child",
            "contains": "paragraph (a)",
        },
        {
            "precondition_id": "replacement-payload",
            "contains": "structured child payload",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:177/subsection:6/definition:relevant authority",
            "text_sha256": definition_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-structural",
            "proof_semantic": "definition_child_structural_payload_boundary_claim",
            "operation_family": "definition_child_structural_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "definition-term",
                "definition-child",
                "replacement-payload",
            ],
            "definition_term_precondition_ids": ["definition-term"],
            "definition_child_precondition_ids": ["definition-child"],
            "replacement_child_payload_precondition_ids": ["replacement-payload"],
            "boundary_ownership_ids": [
                "definition_term_scope",
                "definition_child_identity",
                "replacement_child_payload_shape",
                "post_child_tail_connector_boundary",
            ],
            "includes_tail_connector": True,
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "section:177/subsection:6/definition:relevant authority",
                    (
                        "section:177/subsection:6/definition:relevant authority/"
                        "paragraph:a"
                    ),
                ],
                target_fingerprints={
                    "section:177/subsection:6/definition:relevant authority": {
                        "text_sha256": definition_hash,
                        "subtree_sha256": "b" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_definition_child_structural_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview=(
            'in the definition of "relevant authority", for paragraph (a) '
            'substitute structured child payload'
        ),
    )
    claim["action_family"] = "definition_child_structural_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:178/subsection:6/definition:relevant authority/paragraph:a"
    operation["mutation_boundary"] = {
        "changed_paths": [
            "section:178/subsection:6/definition:relevant authority/paragraph:a",
        ],
        "target_region": [
            "section:178/subsection:6/definition:relevant authority/paragraph:a",
        ],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "definition-term",
            "contains": 'definition of "relevant authority"',
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:177/subsection:6/definition:relevant authority",
            "text_sha256": hashlib.sha256(b"definition text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-structural",
            "proof_semantic": "definition_child_structural_payload_boundary_claim",
            "operation_family": "definition_child_structural_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["definition-term"],
            "definition_term_precondition_ids": ["definition-term"],
            "includes_tail_connector": True,
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "requires definition_child_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "requires replacement_child_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "requires boundary_ownership_ids to include 'definition_term_scope'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "requires boundary_ownership_ids to include "
        "'post_child_tail_connector_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "operation 'manual-op-1' must be a bounded definition-child structural "
        "replacement action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "operation 'manual-op-1' must declare a definition term"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "operation 'manual-op-1' must declare a definition child label"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_payload_boundary_claim "
        "operation 'manual-op-1' target "
        "'section:178/subsection:6/definition:relevant authority/paragraph:a' "
        "is outside declared live definition preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_definition_child_structural_insert_family_proof_semantic() -> None:
    definition_hash = hashlib.sha256(b"definition text").hexdigest()
    claim = _claim_row(
        source_preview=(
            'in the definition of "care provider", before paragraph (b) insert '
            'paragraph (ba) and the "or" at the end of paragraph (b)'
        ),
    )
    claim["action_family"] = "definition_child_structural_insert"
    claim["ownership_claims"] = [
        {"ownership_id": "definition_term_scope", "status": "claimed_not_proved"},
        {
            "ownership_id": "anchor_definition_child_identity",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "inserted_child_payload_shape",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "existing_tail_connector_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "connector_migration_or_preservation_rule",
            "status": "claimed_not_proved",
        },
        {"ownership_id": "mutation_boundary", "status": "claimed_not_proved"},
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:5/definition:care provider/paragraph:ba"
    operation["definition_term"] = "care provider"
    operation["anchor_definition_child_label"] = "paragraph b"
    operation["inserted_definition_child_label"] = "paragraph ba"
    operation["tail_connector_handling"] = "preserve_or_at_anchor"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:5/definition:care provider/paragraph:ba"],
        "target_region": ["section:5/definition:care provider/paragraph:ba"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "definition-term", "contains": 'definition of "care provider"'},
        {"precondition_id": "anchor-child", "contains": "paragraph (b)"},
        {"precondition_id": "inserted-payload", "contains": "paragraph (ba)"},
        {"precondition_id": "tail-connector", "contains": 'the "or" at the end'},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:5/definition:care provider",
            "text_sha256": definition_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-insert",
            "proof_semantic": "definition_child_structural_insert_boundary_claim",
            "operation_family": "definition_child_structural_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "definition-term",
                "anchor-child",
                "inserted-payload",
                "tail-connector",
            ],
            "definition_term_precondition_ids": ["definition-term"],
            "anchor_child_precondition_ids": ["anchor-child"],
            "inserted_payload_precondition_ids": ["inserted-payload"],
            "tail_connector_precondition_ids": ["tail-connector"],
            "boundary_ownership_ids": [
                "definition_term_scope",
                "anchor_definition_child_identity",
                "inserted_child_payload_shape",
                "existing_tail_connector_boundary",
                "connector_migration_or_preservation_rule",
            ],
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:5/definition:care provider"],
                target_fingerprints={
                    "section:5/definition:care provider": {
                        "text_sha256": definition_hash,
                        "subtree_sha256": "b" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_definition_child_structural_insert_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='in the definition of "care provider", before paragraph (b) insert paragraph (ba)',
    )
    claim["action_family"] = "definition_child_structural_insert"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:6/definition:care provider/paragraph:ba"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:6/definition:care provider/paragraph:ba"],
        "target_region": ["section:6/definition:care provider/paragraph:ba"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "definition-term", "contains": 'definition of "care provider"'},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-definition",
            "path": "section:5/definition:care provider",
            "text_sha256": hashlib.sha256(b"definition text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-definition-child-insert",
            "proof_semantic": "definition_child_structural_insert_boundary_claim",
            "operation_family": "definition_child_structural_insert",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["definition-term"],
            "definition_term_precondition_ids": ["definition-term"],
            "live_target_precondition_ids": ["live-definition"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "requires anchor_child_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "requires inserted_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "requires tail_connector_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "requires boundary_ownership_ids to include 'definition_term_scope'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' must be an INSERT"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' must declare a definition term"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' must declare an anchor definition child label"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' must declare an inserted definition child label"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' must declare tail connector handling"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].definition_child_structural_insert_boundary_claim "
        "operation 'manual-op-1' target parent "
        "'section:6/definition:care provider' is outside declared live definition "
        "preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_mixed_body_heading_split_family_proof_semantic() -> None:
    body_hash = hashlib.sha256(b"body text").hexdigest()
    heading_hash = hashlib.sha256(b"heading text").hexdigest()
    claim = _claim_row(
        source_preview='in section 10 and the heading, for "old" substitute "new"',
    )
    claim["action_family"] = "mixed_body_heading_text_substitution_split"
    claim["ownership_claims"] = [
        {"ownership_id": "body_text_target_boundary", "status": "claimed_not_proved"},
        {"ownership_id": "heading_facet_boundary", "status": "claimed_not_proved"},
        {"ownership_id": "split_operation_boundary", "status": "claimed_not_proved"},
        {"ownership_id": "unclaimed_surface_preservation", "status": "claimed_not_proved"},
        {"ownership_id": "mutation_boundary", "status": "claimed_not_proved"},
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["operations"] = [
        {
            "op_id": "manual-op-body",
            "action": "TEXT_REPLACE",
            "target": "section:10",
            "surface_role": "body_text",
            "mutation_boundary": {
                "changed_paths": ["section:10"],
                "target_region": ["section:10"],
            },
        },
        {
            "op_id": "manual-op-heading",
            "action": "HEADING_REPLACE",
            "target": "section:10/heading",
            "surface_role": "heading_facet",
            "mutation_boundary": {
                "changed_paths": ["section:10/heading"],
                "target_region": ["section:10/heading"],
            },
        },
    ]
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "body-target", "contains": "section 10"},
        {"precondition_id": "heading-facet", "contains": "the heading"},
        {"precondition_id": "preimage", "contains": '"old"'},
        {"precondition_id": "replacement", "contains": '"new"'},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {"precondition_id": "live-body", "path": "section:10", "text_sha256": body_hash},
        {
            "precondition_id": "live-heading",
            "path": "section:10/heading",
            "text_sha256": heading_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-mixed-split",
            "proof_semantic": "mixed_body_heading_split_boundary_claim",
            "operation_family": "mixed_body_heading_text_substitution_split",
            "operation_ids": ["manual-op-body", "manual-op-heading"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "body-target",
                "heading-facet",
                "preimage",
                "replacement",
            ],
            "body_target_precondition_ids": ["body-target"],
            "heading_facet_precondition_ids": ["heading-facet"],
            "per_surface_preimage_precondition_ids": ["preimage"],
            "replacement_precondition_ids": ["replacement"],
            "split_ownership_ids": [
                "body_text_target_boundary",
                "heading_facet_boundary",
                "split_operation_boundary",
                "unclaimed_surface_preservation",
            ],
            "live_target_precondition_ids": ["live-body", "live-heading"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:10", "section:10/heading"],
                target_fingerprints={
                    "section:10": {"text_sha256": body_hash, "subtree_sha256": "c" * 64},
                    "section:10/heading": {
                        "text_sha256": heading_hash,
                        "subtree_sha256": "d" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_mixed_body_heading_split_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='in section 10 and the heading, for "old" substitute "new"',
    )
    claim["action_family"] = "mixed_body_heading_text_substitution_split"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:11"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:11"],
        "target_region": ["section:11"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "body-target", "contains": "section 10"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-body",
            "path": "section:10",
            "text_sha256": hashlib.sha256(b"body text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-mixed-split",
            "proof_semantic": "mixed_body_heading_split_boundary_claim",
            "operation_family": "mixed_body_heading_text_substitution_split",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["body-target"],
            "body_target_precondition_ids": ["body-target"],
            "live_target_precondition_ids": ["live-body"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "requires heading_facet_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "requires split_ownership_ids to include 'body_text_target_boundary'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "requires at least two split operations"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "operation 'manual-op-1' must be a body text or heading rewrite action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "operation 'manual-op-1' must declare surface_role 'body_text' or "
        "'heading_facet'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "operation 'manual-op-1' target 'section:11' is outside declared "
        "live split-surface preconditions"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "requires a 'body_text' operation"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].mixed_body_heading_split_boundary_claim "
        "requires a 'heading_facet' operation"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_structural_child_range_family_proof_semantic() -> None:
    range_hash = hashlib.sha256(b"range text").hexdigest()
    claim = _claim_row(
        source_preview='for paragraphs (a) to (c) substitute paragraphs (a) and (b)',
    )
    claim["action_family"] = "structural_child_range_substitution"
    claim["ownership_claims"] = [
        {"ownership_id": "source_named_child_range", "status": "claimed_not_proved"},
        {"ownership_id": "replacement_payload_shape", "status": "claimed_not_proved"},
        {"ownership_id": "removed_child_identities", "status": "claimed_not_proved"},
        {"ownership_id": "parent_text_or_tail_boundary", "status": "claimed_not_proved"},
        {"ownership_id": "mutation_boundary", "status": "claimed_not_proved"},
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:2/subsection:1/paragraph:a"
    operation["child_range_id"] = "paragraphs-a-to-c"
    operation["removed_child_ids"] = ["paragraph:a", "paragraph:b", "paragraph:c"]
    operation["replacement_payload_shape"] = "paragraph_children"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/subsection:1/paragraph:a"],
        "target_region": ["section:2/subsection:1/paragraph:a"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "child-range", "contains": "paragraphs (a) to (c)"},
        {"precondition_id": "removed-child", "contains": "paragraphs (a) to (c)"},
        {"precondition_id": "replacement-payload", "contains": "substitute paragraphs"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-range",
            "path": "section:2/subsection:1",
            "text_sha256": range_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-structural-child-range",
            "proof_semantic": "structural_child_range_source_payload_boundary_claim",
            "operation_family": "structural_child_range_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "child-range",
                "removed-child",
                "replacement-payload",
            ],
            "child_range_precondition_ids": ["child-range"],
            "removed_child_precondition_ids": ["removed-child"],
            "replacement_payload_precondition_ids": ["replacement-payload"],
            "boundary_ownership_ids": [
                "source_named_child_range",
                "replacement_payload_shape",
                "removed_child_identities",
                "parent_text_or_tail_boundary",
            ],
            "live_target_precondition_ids": ["live-range"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=[
                    "section:2/subsection:1",
                    "section:2/subsection:1/paragraph:a",
                ],
                target_fingerprints={
                    "section:2/subsection:1": {
                        "text_sha256": range_hash,
                        "subtree_sha256": "e" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_structural_child_range_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='for paragraphs (a) to (c) substitute paragraphs (a) and (b)',
    )
    claim["action_family"] = "structural_child_range_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operation = proposed_outcome["operations"][0]
    assert isinstance(operation, dict)
    operation["action"] = "MOVE"
    operation["target"] = "section:2/subsection:2/paragraph:a"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/subsection:2/paragraph:a"],
        "target_region": ["section:2/subsection:2/paragraph:a"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {"precondition_id": "child-range", "contains": "paragraphs (a) to (c)"},
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-range",
            "path": "section:2/subsection:1",
            "text_sha256": hashlib.sha256(b"range text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-structural-child-range",
            "proof_semantic": "structural_child_range_source_payload_boundary_claim",
            "operation_family": "structural_child_range_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["child-range"],
            "child_range_precondition_ids": ["child-range"],
            "live_target_precondition_ids": ["live-range"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "requires removed_child_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "requires replacement_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "requires boundary_ownership_ids to include 'source_named_child_range'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "operation 'manual-op-1' must be a bounded child-range substitution action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "operation 'manual-op-1' must declare a source child range"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "operation 'manual-op-1' must declare removed_child_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].structural_child_range_source_payload_boundary_claim "
        "operation 'manual-op-1' target 'section:2/subsection:2/paragraph:a' "
        "is outside declared live child-range preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_referent_qualified_family_proof_semantic() -> None:
    target_hash = hashlib.sha256(b"target text").hexdigest()
    claim = _claim_row(
        source_preview='for "he" and "him", where they refer to the Rail Regulator, substitute "it"',
    )
    claim["action_family"] = "referent_qualified_text_substitution"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_qualified_referent_entity",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "quoted_preimage_terms",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "replacement_text",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "per_occurrence_coreference_decision",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:4/subsection:4"
    operation["referent_entity"] = "Rail Regulator"
    operation["occurrence_ids"] = ["he-1", "him-1"]
    operation["mutation_boundary"] = {
        "changed_paths": ["section:4/subsection:4"],
        "target_region": ["section:4/subsection:4"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "referent-entity",
            "contains": "Rail Regulator",
        },
        {
            "precondition_id": "quoted-preimage",
            "contains": '"he" and "him"',
        },
        {
            "precondition_id": "replacement",
            "contains": 'substitute "it"',
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-target",
            "path": "section:4/subsection:4",
            "text_sha256": target_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-referent-qualified",
            "proof_semantic": "referent_qualified_occurrence_scope_claim",
            "operation_family": "referent_qualified_text_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "referent-entity",
                "quoted-preimage",
                "replacement",
            ],
            "referent_entity_precondition_ids": ["referent-entity"],
            "quoted_preimage_precondition_ids": ["quoted-preimage"],
            "replacement_precondition_ids": ["replacement"],
            "referent_ownership_ids": [
                "source_qualified_referent_entity",
                "per_occurrence_coreference_decision",
            ],
            "live_target_precondition_ids": ["live-target"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:4/subsection:4"],
                target_fingerprints={
                    "section:4/subsection:4": {
                        "text_sha256": target_hash,
                        "subtree_sha256": "c" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_referent_qualified_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='for "he" and "him", where they refer to the Rail Regulator, substitute "it"',
    )
    claim["action_family"] = "referent_qualified_text_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPEAL"
    operation["target"] = "section:5/subsection:4"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:5/subsection:4"],
        "target_region": ["section:5/subsection:4"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "referent-entity",
            "contains": "Rail Regulator",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-target",
            "path": "section:4/subsection:4",
            "text_sha256": hashlib.sha256(b"target text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-referent-qualified",
            "proof_semantic": "referent_qualified_occurrence_scope_claim",
            "operation_family": "referent_qualified_text_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["referent-entity"],
            "referent_entity_precondition_ids": ["referent-entity"],
            "live_target_precondition_ids": ["live-target"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "requires quoted_preimage_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "requires replacement_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "requires referent_ownership_ids to include "
        "'source_qualified_referent_entity'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "operation 'manual-op-1' must be a referent-qualified text replacement "
        "action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "operation 'manual-op-1' must declare a referent entity, scope, or "
        "coreference rule"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "operation 'manual-op-1' must declare occurrence_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].referent_qualified_occurrence_scope_claim "
        "operation 'manual-op-1' target 'section:5/subsection:4' is outside "
        "declared live text preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_source_carried_multi_subunit_family_proof_semantic() -> None:
    child_hash = hashlib.sha256(b"child text").hexdigest()
    claim = _claim_row(
        source_preview='in paragraphs (a) and (b), for "old" substitute "new"',
    )
    claim["action_family"] = "source_carried_multi_subunit_text_rewrite"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_named_child_unit_set",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "per_child_text_preimage",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "per_child_replacement_or_repeal_payload",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:1/subsection:1/paragraph:a"
    operation["child_unit_label"] = "paragraph a"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:1/paragraph:a"],
        "target_region": ["section:1/subsection:1/paragraph:a"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "child-units",
            "contains": "paragraphs (a) and (b)",
        },
        {
            "precondition_id": "child-preimage",
            "contains": '"old"',
        },
        {
            "precondition_id": "replacement-payload",
            "contains": 'substitute "new"',
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-child-a",
            "path": "section:1/subsection:1/paragraph:a",
            "text_sha256": child_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-multi-subunit",
            "proof_semantic": "source_carried_multi_subunit_boundary_claim",
            "operation_family": "source_carried_multi_subunit_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "child-units",
                "child-preimage",
                "replacement-payload",
            ],
            "child_unit_precondition_ids": ["child-units"],
            "per_child_preimage_precondition_ids": ["child-preimage"],
            "replacement_or_repeal_payload_precondition_ids": [
                "replacement-payload",
            ],
            "boundary_ownership_ids": [
                "source_named_child_unit_set",
                "per_child_text_preimage",
                "per_child_replacement_or_repeal_payload",
            ],
            "live_target_precondition_ids": ["live-child-a"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1/subsection:1/paragraph:a"],
                target_fingerprints={
                    "section:1/subsection:1/paragraph:a": {
                        "text_sha256": child_hash,
                        "subtree_sha256": "d" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_source_carried_multi_subunit_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='in paragraphs (a) and (b), for "old" substitute "new"',
    )
    claim["action_family"] = "source_carried_multi_subunit_text_rewrite"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:2/paragraph:a"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:2/paragraph:a"],
        "target_region": ["section:1/subsection:2/paragraph:a"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "child-units",
            "contains": "paragraphs (a) and (b)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-child-a",
            "path": "section:1/subsection:1/paragraph:a",
            "text_sha256": hashlib.sha256(b"child text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-multi-subunit",
            "proof_semantic": "source_carried_multi_subunit_boundary_claim",
            "operation_family": "source_carried_multi_subunit_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["child-units"],
            "child_unit_precondition_ids": ["child-units"],
            "live_target_precondition_ids": ["live-child-a"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "requires per_child_preimage_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "requires replacement_or_repeal_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "requires boundary_ownership_ids to include "
        "'source_named_child_unit_set'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "operation 'manual-op-1' must be a bounded child-unit text rewrite action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "operation 'manual-op-1' must declare a child unit id or label"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_multi_subunit_boundary_claim "
        "operation 'manual-op-1' target 'section:1/subsection:2/paragraph:a' "
        "is outside declared live child-unit preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_source_carried_child_tail_family_proof_semantic() -> None:
    tail_hash = hashlib.sha256(b"tail text").hexdigest()
    claim = _claim_row(
        source_preview='in paragraph (a), for the words after "old" substitute "new"',
    )
    claim["action_family"] = "source_carried_child_tail_text_rewrite"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_named_child_anchor",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "tail_text_preimage_or_repeal_scope",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "replacement_or_repeal_payload",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "TEXT_REPLACE"
    operation["target"] = "section:1/subsection:1/paragraph:a#tail"
    operation["child_anchor"] = "paragraph (a)"
    operation["tail_boundary"] = 'after "old"'
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:1/paragraph:a#tail"],
        "target_region": ["section:1/subsection:1/paragraph:a#tail"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "child-anchor",
            "contains": "paragraph (a)",
        },
        {
            "precondition_id": "tail-scope",
            "contains": 'after "old"',
        },
        {
            "precondition_id": "replacement-payload",
            "contains": 'substitute "new"',
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-tail",
            "path": "section:1/subsection:1/paragraph:a#tail",
            "text_sha256": tail_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-child-tail",
            "proof_semantic": "source_carried_child_tail_boundary_claim",
            "operation_family": "source_carried_child_tail_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "child-anchor",
                "tail-scope",
                "replacement-payload",
            ],
            "child_anchor_precondition_ids": ["child-anchor"],
            "tail_scope_precondition_ids": ["tail-scope"],
            "replacement_or_repeal_payload_precondition_ids": [
                "replacement-payload",
            ],
            "boundary_ownership_ids": [
                "source_named_child_anchor",
                "tail_text_preimage_or_repeal_scope",
                "replacement_or_repeal_payload",
            ],
            "live_target_precondition_ids": ["live-tail"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1/subsection:1/paragraph:a#tail"],
                target_fingerprints={
                    "section:1/subsection:1/paragraph:a#tail": {
                        "text_sha256": tail_hash,
                        "subtree_sha256": "e" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_source_carried_child_tail_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='in paragraph (a), for the words after "old" substitute "new"',
    )
    claim["action_family"] = "source_carried_child_tail_text_rewrite"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:2/paragraph:a#tail"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:2/paragraph:a#tail"],
        "target_region": ["section:1/subsection:2/paragraph:a#tail"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "child-anchor",
            "contains": "paragraph (a)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-tail",
            "path": "section:1/subsection:1/paragraph:a#tail",
            "text_sha256": hashlib.sha256(b"tail text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-child-tail",
            "proof_semantic": "source_carried_child_tail_boundary_claim",
            "operation_family": "source_carried_child_tail_text_rewrite",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["child-anchor"],
            "child_anchor_precondition_ids": ["child-anchor"],
            "live_target_precondition_ids": ["live-tail"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "requires tail_scope_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "requires replacement_or_repeal_payload_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "requires boundary_ownership_ids to include 'source_named_child_anchor'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "operation 'manual-op-1' must be a bounded child-tail text rewrite action"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "operation 'manual-op-1' must declare a child anchor or child unit id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "operation 'manual-op-1' must declare a tail scope or boundary"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_child_tail_boundary_claim "
        "operation 'manual-op-1' target 'section:1/subsection:2/paragraph:a#tail' "
        "is outside declared live child-tail preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_source_carried_structured_family_proof_semantic() -> None:
    parent_hash = hashlib.sha256(b"parent text").hexdigest()
    claim = _claim_row(
        source_preview='in subsection (2), after paragraph (a) insert paragraph (aa)',
    )
    claim["action_family"] = "source_carried_structured_text_patch"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_parent_formula_anchor",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "source_carried_payload_units",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "child_target_boundaries",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "INSERT"
    operation["target"] = "section:1/subsection:2/paragraph:aa"
    operation["payload_unit_id"] = "paragraph-aa"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:2/paragraph:aa"],
        "target_region": ["section:1/subsection:2/paragraph:aa"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "parent-anchor",
            "contains": "subsection (2)",
        },
        {
            "precondition_id": "payload-unit",
            "contains": "paragraph (aa)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-parent",
            "path": "section:1/subsection:2",
            "text_sha256": parent_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-structured",
            "proof_semantic": "source_carried_structured_payload_boundary_claim",
            "operation_family": "source_carried_structured_text_patch",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "parent-anchor",
                "payload-unit",
            ],
            "parent_formula_anchor_precondition_ids": ["parent-anchor"],
            "payload_unit_precondition_ids": ["payload-unit"],
            "boundary_ownership_ids": [
                "source_parent_formula_anchor",
                "source_carried_payload_units",
                "child_target_boundaries",
            ],
            "live_target_precondition_ids": ["live-parent"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1/subsection:2"],
                target_fingerprints={
                    "section:1/subsection:2": {
                        "text_sha256": parent_hash,
                        "subtree_sha256": "f" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_source_carried_structured_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='in subsection (2), after paragraph (a) insert paragraph (aa)',
    )
    claim["action_family"] = "source_carried_structured_text_patch"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "section:1/subsection:3/paragraph:aa"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:3/paragraph:aa"],
        "target_region": ["section:1/subsection:3/paragraph:aa"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "parent-anchor",
            "contains": "subsection (2)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-parent",
            "path": "section:1/subsection:2",
            "text_sha256": hashlib.sha256(b"parent text").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-structured",
            "proof_semantic": "source_carried_structured_payload_boundary_claim",
            "operation_family": "source_carried_structured_text_patch",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["parent-anchor"],
            "parent_formula_anchor_precondition_ids": ["parent-anchor"],
            "live_target_precondition_ids": ["live-parent"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].source_carried_structured_payload_boundary_claim "
        "requires payload_unit_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_payload_boundary_claim "
        "requires boundary_ownership_ids to include 'source_parent_formula_anchor'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_payload_boundary_claim "
        "operation 'manual-op-1' must be a bounded structured payload operation"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_payload_boundary_claim "
        "operation 'manual-op-1' must declare a payload unit or child target id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_payload_boundary_claim "
        "operation 'manual-op-1' target 'section:1/subsection:3/paragraph:aa' "
        "is outside declared live structured child-target preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_source_carried_structured_tail_family_proof_semantic() -> None:
    tail_hash = hashlib.sha256(b"tail range").hexdigest()
    claim = _claim_row(
        source_preview='for the words after paragraph (a) substitute paragraphs (aa) and (ab)',
    )
    claim["action_family"] = "source_carried_structured_tail_substitution"
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_tail_range_preimage",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "source_carried_structured_payload_units",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "child_target_boundaries",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "flattened_patch_replacement_boundary",
            "status": "claimed_not_proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPLACE"
    operation["target"] = "section:1/subsection:2/paragraph:aa"
    operation["tail_range_id"] = "after-paragraph-a"
    operation["payload_unit_id"] = "paragraph-aa"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:2/paragraph:aa"],
        "target_region": ["section:1/subsection:2/paragraph:aa"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "tail-range",
            "contains": "after paragraph (a)",
        },
        {
            "precondition_id": "payload-unit",
            "contains": "paragraphs (aa) and (ab)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-tail",
            "path": "section:1/subsection:2/paragraph:aa",
            "text_sha256": tail_hash,
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-structured-tail",
            "proof_semantic": "source_carried_structured_tail_boundary_claim",
            "operation_family": "source_carried_structured_tail_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": [
                "tail-range",
                "payload-unit",
            ],
            "tail_range_precondition_ids": ["tail-range"],
            "structured_payload_unit_precondition_ids": ["payload-unit"],
            "boundary_ownership_ids": [
                "source_tail_range_preimage",
                "source_carried_structured_payload_units",
                "child_target_boundaries",
                "flattened_patch_replacement_boundary",
            ],
            "live_target_precondition_ids": ["live-tail"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        live_target_rows=(
            _live_target_row(
                target_paths=["section:1/subsection:2/paragraph:aa"],
                target_fingerprints={
                    "section:1/subsection:2/paragraph:aa": {
                        "text_sha256": tail_hash,
                        "subtree_sha256": "a" * 64,
                    },
                },
            ),
        ),
    )

    row = rows[0]
    assert (
        row["validator_status"]
        == "validated_provenance_source_text_live_targets_and_preconditions_only"
    )
    assert row["operation_family_proofs_checked"] is True
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_source_carried_structured_tail_family_proof_semantic_gap() -> None:
    claim = _claim_row(
        source_preview='for the words after paragraph (a) substitute paragraphs (aa) and (ab)',
    )
    claim["action_family"] = "source_carried_structured_tail_substitution"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["action"] = "REPEAL"
    operation["target"] = "section:1/subsection:3/paragraph:aa"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/subsection:3/paragraph:aa"],
        "target_region": ["section:1/subsection:3/paragraph:aa"],
    }
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "tail-range",
            "contains": "after paragraph (a)",
        },
    ]
    proposed_outcome["live_target_preconditions"] = [
        {
            "precondition_id": "live-tail",
            "path": "section:1/subsection:2/paragraph:aa",
            "text_sha256": hashlib.sha256(b"tail range").hexdigest(),
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-source-carried-structured-tail",
            "proof_semantic": "source_carried_structured_tail_boundary_claim",
            "operation_family": "source_carried_structured_tail_substitution",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["tail-range"],
            "tail_range_precondition_ids": ["tail-range"],
            "live_target_precondition_ids": ["live-tail"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "requires structured_payload_unit_precondition_ids"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "requires boundary_ownership_ids to include 'source_tail_range_preimage'"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "operation 'manual-op-1' must be a bounded structured tail substitution "
        "operation"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "operation 'manual-op-1' must declare a tail range or boundary"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "operation 'manual-op-1' must declare a structured payload unit or child "
        "target id"
    ) in row["validation_issues"]
    assert (
        "operation_family_proofs[1].source_carried_structured_tail_boundary_claim "
        "operation 'manual-op-1' target 'section:1/subsection:3/paragraph:aa' "
        "is outside declared live structured tail preconditions"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_missing_template_required_ownership() -> None:
    claim = _claim_row()
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_named_table_surface",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert "required_ownership missing: mutation_boundary" in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_ownership_claim_without_status() -> None:
    claim = _claim_row()
    claim["ownership_claims"] = [
        "source_named_table_surface",
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "ownership_claim source_named_table_surface status is required"
        in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_ownership_claimed_as_proved() -> None:
    claim = _claim_row()
    claim["ownership_claims"] = [
        {
            "ownership_id": "source_named_table_surface",
            "status": "proved",
        },
        {
            "ownership_id": "mutation_boundary",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "ownership_claim source_named_table_surface status 'proved' cannot be "
        "claimed by this non-executable validator"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_missing_template_source_target_address() -> None:
    claim = _claim_row()
    claim.pop("target_context")

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert "source_target_address is required by matched template" in row[
        "validation_issues"
    ]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_checks_template_destination_address() -> None:
    workqueue = _workqueue_row()
    template = workqueue["suggested_claim_template"]
    assert isinstance(template, dict)
    template["destination_address"] = "section:2/table:1"

    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["target_context"] = {
        "source_target_address": "section:1/table:1",
        "destination_address": "section:2/table:1",
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(workqueue,),
    )

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_only"
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_operation_target_under_template_destination() -> None:
    workqueue = _workqueue_row()
    template = workqueue["suggested_claim_template"]
    assert isinstance(template, dict)
    template["destination_address"] = "section:2/table:1"

    claim = _claim_row()
    target_context = claim["target_context"]
    assert isinstance(target_context, dict)
    target_context["destination_address"] = "section:2/table:1"
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["target"] = "section:2/table:1/row:2"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:2/table:1/row:2"],
        "target_region": ["section:2/table:1/row:2"],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(workqueue,),
    )

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_only"
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_operation_target_outside_template_carrier() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["target"] = "section:9/table:1/row:2"
    operation["mutation_boundary"] = {
        "changed_paths": ["section:9/table:1/row:2"],
        "target_region": ["section:9/table:1/row:2"],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "canonical_operations[1].target 'section:9/table:1/row:2' is outside "
        "matched template source_target_address/destination_address"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_template_destination_address_mismatch() -> None:
    workqueue = _workqueue_row()
    template = workqueue["suggested_claim_template"]
    assert isinstance(template, dict)
    template["destination_address"] = "section:2/table:1"

    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["target_context"] = {
        "source_target_address": "section:1/table:1",
        "destination_address": "section:3/table:1",
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(workqueue,),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "destination_address mismatch: "
        "claim='section:3/table:1' template='section:2/table:1'"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_missing_template_validator_checks() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["validator_checks"] = [
        {
            "check_id": "claim_identifies_exact_table_carrier",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert row["rule_id"] == "uk_semantic_claim_workqueue_mismatch"
    assert any(
        issue
        == (
            "required_validator_checks missing: "
            "changed_paths_are_within_claimed_table_surface"
        )
        for issue in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_template_check_without_status() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["validator_checks"] = [
        "claim_identifies_exact_table_carrier",
        {
            "check_id": "changed_paths_are_within_claimed_table_surface",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "validator_check claim_identifies_exact_table_carrier status is required"
        in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_template_check_claimed_as_passed() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["validator_checks"] = [
        {
            "check_id": "claim_identifies_exact_table_carrier",
            "status": "passed",
        },
        {
            "check_id": "changed_paths_are_within_claimed_table_surface",
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows(
        (claim,),
        workqueue_rows=(_workqueue_row(),),
    )

    row = rows[0]
    assert row["validator_status"] == "rejected_workqueue_mismatch"
    assert (
        "validator_check claim_identifies_exact_table_carrier status 'passed' "
        "cannot be claimed by this non-executable validator"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_schema_without_operations() -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {"outcome_kind": "canonical_operations"}

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert row["rule_id"] == "uk_semantic_claim_schema_rejected"
    assert "canonical_operations outcome requires operations" in row[
        "validation_issues"
    ]
    assert row["replay_authorized"] is False


@pytest.mark.parametrize(
    ("outcome_kind", "expected_issue"),
    [
        (
            "non_replayable_finding",
            "non_replayable_finding outcome requires finding",
        ),
        (
            "source_pathology",
            "source_pathology outcome requires pathology",
        ),
        (
            "oracle_adjudication",
            "oracle_adjudication outcome requires adjudication",
        ),
        (
            "request_more_source_evidence",
            "request_more_source_evidence outcome requires requested_evidence",
        ),
    ],
)
def test_validate_semantic_claim_rejects_empty_non_operation_outcomes(
    outcome_kind: str,
    expected_issue: str,
) -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {"outcome_kind": outcome_kind}

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert expected_issue in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_shaped_non_replayable_finding() -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {
        "outcome_kind": "non_replayable_finding",
        "finding": {
            "rule_id": "uk_manual_claim_non_replayable_table_gap",
            "reason_code": "public_source_lacks_table_cell_boundary",
            "reason": "The available public source does not prove the table cell boundary.",
        },
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_only"
    assert row["proposed_outcome_kind"] == "non_replayable_finding"
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_shaped_source_evidence_request() -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {
        "outcome_kind": "request_more_source_evidence",
        "requested_evidence": [
            {
                "evidence_kind": "source_table_cell_boundary",
                "reason": "The source table row is visible but cell boundaries are unresolved.",
            },
        ],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_only"
    assert row["proposed_outcome_kind"] == "request_more_source_evidence"
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_operation_without_boundary() -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {
        "outcome_kind": "canonical_operations",
        "operations": [
            {
                "op_id": "manual-op-1",
                "action": "INSERT",
                "target": "section:1/table:1/row:2",
            },
        ],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "canonical_operations[1].mutation_boundary is required"
        in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_operation_without_target() -> None:
    claim = _claim_row()
    claim["proposed_outcome"] = {
        "outcome_kind": "canonical_operations",
        "operations": [
            {
                "op_id": "manual-op-1",
                "action": "text_patch",
                "mutation_boundary": {
                    "changed_paths": ["section:1"],
                    "target_region": ["section:1"],
                },
            },
        ],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert "canonical_operations[1].target is required" in row["validation_issues"]
    assert (
        "canonical_operations[1].action is not a canonical StructuralAction"
        in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_duplicate_operation_ids() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operations.append(dict(operations[0]))

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "canonical_operations[2].op_id duplicates earlier operation id 'manual-op-1'"
        in row["validation_issues"]
    )
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_changed_path_outside_target_region() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/table:2/row:1"],
        "target_region": ["section:1/table:1"],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "canonical_operations[1].mutation_boundary.changed_paths contains "
        "'section:1/table:2/row:1' outside target_region or declared exception paths"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_accepts_declared_boundary_exception_path() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/table:2/row:1"],
        "target_region": ["section:1/table:1"],
        "declared_recovery_paths": ["section:1/table:2"],
        "recovery_rule_id": "uk_manual_claim_declared_recovery_surface",
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "validated_provenance_only"
    assert row["validation_issues"] == []
    assert row["replay_authorized"] is False


def test_validate_semantic_claim_rejects_declared_exception_without_reason() -> None:
    claim = _claim_row()
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    operations = proposed_outcome["operations"]
    assert isinstance(operations, list)
    operation = operations[0]
    assert isinstance(operation, dict)
    operation["mutation_boundary"] = {
        "changed_paths": ["section:1/table:2/row:1"],
        "target_region": ["section:1/table:1"],
        "declared_recovery_paths": ["section:1/table:2"],
    }

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))

    row = rows[0]
    assert row["validator_status"] == "rejected_schema"
    assert (
        "canonical_operations[1].mutation_boundary.declared_recovery_paths requires "
        "recovery_rule_id, recovery_reason, or recovery_observation_id"
    ) in row["validation_issues"]
    assert row["replay_authorized"] is False


def test_uk_semantic_claims_validate_main_writes_jsonl_and_fails_on_rejected(
    tmp_path: Path,
    capsys,
) -> None:
    input_path = tmp_path / "claims.jsonl"
    workqueue_path = tmp_path / "workqueue.jsonl"
    validation_path = tmp_path / "validation.jsonl"
    claim = _claim_row()
    claim["manual_compile_rule_id"] = "uk_manual_frontier_other"
    input_path.write_text(json.dumps(claim) + "\n", encoding="utf-8")
    workqueue_path.write_text(json.dumps(_workqueue_row()) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        uk_semantic_claims.main(
            Namespace(
                input=str(input_path),
                workqueue_jsonl=str(workqueue_path),
                json=True,
                summary_only=False,
                validation_jsonl=str(validation_path),
                fail_on_rejected=True,
                fail_on_input_error=False,
            )
        )

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_kind"] == "uk_semantic_claim_validation_report"
    assert payload["summary"]["accepted_count"] == 0
    assert payload["summary"]["rejected_count"] == 1
    assert payload["summary"]["input_error_count"] == 0
    assert payload["summary"]["replay_authorized_count"] == 0
    assert payload["validation_jsonl"] == {
        "path": str(validation_path),
        "rows": 1,
    }
    validation_row = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation_row["validator_status"] == "rejected_workqueue_mismatch"
    assert validation_row["replay_authorized"] is False


def test_uk_semantic_claims_validation_report_summarizes_proof_semantics(
    tmp_path: Path,
) -> None:
    claim = _claim_row(source_preview="after the entry relating to X insert the row")
    proposed_outcome = claim["proposed_outcome"]
    assert isinstance(proposed_outcome, dict)
    proposed_outcome["source_text_preconditions"] = [
        {
            "precondition_id": "source-names-anchor",
            "contains": "entry relating to X",
        },
    ]
    proposed_outcome["operation_family_proofs"] = [
        {
            "proof_id": "proof-table-insert-anchor",
            "proof_semantic": "table_surface_insert_anchor_and_live_carrier",
            "operation_family": "table_surface_mutation",
            "operation_ids": ["manual-op-1"],
            "validator_check_ids": ["claim_identifies_exact_table_carrier"],
            "source_text_precondition_ids": ["source-names-anchor"],
            "status": "claimed_not_proved",
        },
    ]

    rows = uk_semantic_claims.validate_semantic_claim_rows((claim,))
    report = uk_semantic_claims._validation_report_jsonable(
        input_path=tmp_path / "claims.jsonl",
        rows=rows,
        summary_only=True,
    )

    assert "rows" not in report
    summary = report["summary"]
    assert summary["operation_family_proof_semantic_counts"] == {
        "table_surface_insert_anchor_and_live_carrier": 1,
    }
    assert summary["operation_family_proof_family_counts"] == {
        "table_surface_mutation": 1,
    }
