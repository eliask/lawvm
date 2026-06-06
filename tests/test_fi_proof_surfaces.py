from lawvm.core.compile_result import SourcePathology
from lawvm.finland.proof_surfaces import (
    source_pathology_execution_authorization,
    source_pathology_frontier_work_item,
    source_pathology_proof_rule,
    source_pathology_proof_surface_rows,
)


def test_source_pathology_rule_owns_high_value_family() -> None:
    rule = source_pathology_proof_rule("DESTRUCTIVE_SHAPE_LOSS_RISK")

    assert rule.owner_phase == "replay_apply"
    assert rule.frontier_family == "fi_destructive_shape_loss_risk"
    assert "mutation_boundary_proof_before_replay_promotion" in rule.required_proofs


def test_source_pathology_authorization_is_not_replay_authority() -> None:
    pathology = SourcePathology.from_scope(
        code="MALFORMED_BROAD_REPLACE_BODY",
        message="Broad replace source body is partial.",
        source_statute="2001/748",
        target_unit_kind="section",
        target_label="section 6",
        detail={"diagnostic_reason": "partial_body_only"},
    )

    authorization = source_pathology_execution_authorization(pathology).to_dict()

    assert authorization["executable"] is False
    assert authorization["replay_authorized"] is False
    assert authorization["owner_phase"] == "payload_normalization"
    assert authorization["strict_disposition"] == "block"
    assert authorization["quirks_disposition"] == "record"
    assert authorization["authorization_status"] == "source_pathology_not_replay_authority"
    assert "treat_source_pathology_as_replay_authorization" in authorization["forbidden_shortcuts"]


def test_source_pathology_frontier_work_item_is_non_executable() -> None:
    pathology = {
        "code": "SPARSE_ITEM_BODY_MISSING",
        "message": "Sparse omission payload did not reproduce the targeted item body.",
        "source_statute": "2020/1",
        "target_unit_kind": "section",
        "target_label": "section 5 subsection 2 item 3",
        "detail": {
            "target_section": "5",
            "target_paragraph": "2",
            "target_item": "3",
        },
    }

    item = source_pathology_frontier_work_item(pathology, statute_id="1999/1").to_dict()

    assert item["jurisdiction"] == "fi"
    assert item["frontier_family"] == "fi_sparse_item_body_missing"
    assert item["owner_phase"] == "typed_elaboration"
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["authorization_status"] == "source_pathology_not_replay_authority"
    assert item["source_witness"]["source_role"] == "finland_source_pathology"
    assert item["target_witness"]["target_label"] == "section 5 subsection 2 item 3"
    assert "validate_source_pathology_resolution_claim" in item["required_validator_checks"]
    assert item["detail"]["execution_authorization"]["replay_authorized"] is False


def test_source_pathology_proof_surface_rows_bundle_authorization_and_frontier() -> None:
    rows = source_pathology_proof_surface_rows(
        (
            {
                "code": "RECODIFICATION_SOURCE_CHAIN_GAP",
                "message": "Recodification source-chain gap.",
                "source_statute": "2024/1",
                "target_unit_kind": "chapter",
                "target_label": "chapter 2",
                "detail": {"diagnostic_reason": "pre_wave_source_missing"},
            },
        ),
        statute_id="1990/1",
    )

    assert len(rows["source_pathology_execution_authorizations"]) == 1
    assert len(rows["source_pathology_frontier_work_items"]) == 1
    assert rows["source_pathology_execution_authorizations"][0]["owner_phase"] == "source_chain_elaboration"
    assert rows["source_pathology_frontier_work_items"][0]["frontier_status"] == "source_chain_frontier"
