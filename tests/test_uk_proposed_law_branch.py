from __future__ import annotations

import json
import types

import pytest

from lawvm.core.authority import PROPOSAL_AUTHORITY
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.tools import cli
from lawvm.tools.uk_branch_demo import build_uk_branch_demo_payload, main
from lawvm.uk_legislation.proposed_law_branch import (
    UKProposedLawOperationSpec,
    build_uk_proposed_law_branch_payload,
)


def test_uk_proposed_law_branch_payload_is_graph_only_and_non_enacted() -> None:
    payload = build_uk_proposed_law_branch_payload(
        source_artifact_id="uk/bill/2026/test-bill",
        title="Test Bill",
        specs=(
            UKProposedLawOperationSpec(
                operation_id="uk-test-proposal-op-1",
                source_unit_id="clause:2",
                target_statute_id="ukpga/1978/30",
                target=LegalAddress(path=(("section", "2"),)),
                action=StructuralAction.INSERT,
                proposed_node=IRNode(kind=IRNodeKind.SECTION, label="2", text="Proposed section."),
                current_text="Current text.",
                proposed_text="Proposed section.",
            ),
        ),
    )

    exported = payload.to_dict()
    assert exported["default_enacted_operation_ids"] == ()
    assert exported["branch_operation_ids"] == ("uk-test-proposal-op-1",)
    assert exported["branch_overlay_operation_ids"] == ("uk-test-proposal-op-1",)
    assert payload.branch.authority_layer == PROPOSAL_AUTHORITY
    assert payload.branch_edges[0].edge_kind == "would_insert"
    assert payload.branch_edges[0].source_unit_id == "clause:2"
    assert payload.branch_edges[0].target_statute_id == "ukpga/1978/30"
    assert payload.lifecycle_events[0].event_kind == "introduced"

    row = payload.impact_projection.rows[0]
    assert row.current_text == "Current text."
    assert row.branch_text == "Proposed section."
    assert exported["graph_counts"]["branches"] == 1
    assert exported["graph_counts"]["branch_edges"] == 1
    assert exported["graph_counts"]["branch_lifecycle_events"] == 1


def test_uk_proposed_law_branch_payload_can_record_failed_lifecycle() -> None:
    payload = build_uk_proposed_law_branch_payload(
        source_artifact_id="uk/bill/2026/failed-bill",
        title="Failed Bill",
        legal_status="failed",
        failed_event_id="uk-failed-bill:event:failed",
        failed_date="2026-04-01",
        specs=(
            UKProposedLawOperationSpec(
                operation_id="uk-failed-proposal-op-1",
                source_unit_id="clause:3",
                target_statute_id="ukpga/1978/30",
                target=LegalAddress(path=(("section", "3"),)),
                action=StructuralAction.REPEAL,
            ),
        ),
    )

    assert payload.branch.legal_status == "failed"
    assert payload.branch.terminated_by == "uk-failed-bill:event:failed"
    assert [event.event_kind for event in payload.lifecycle_events] == ["introduced", "failed"]


def test_uk_proposed_law_branch_payload_rejects_empty_spec_list() -> None:
    with pytest.raises(ValueError, match="at least one proposed-law operation spec"):
        build_uk_proposed_law_branch_payload(
            source_artifact_id="uk/bill/2026/empty",
            title="Empty Bill",
            specs=(),
        )


def test_uk_branch_demo_payload_uses_uk_ids_and_proposal_layer() -> None:
    payload = build_uk_branch_demo_payload()

    assert payload["branch"]["branch_id"] == "proposal:uk:uk-bill-2026-example-bill"
    assert payload["branch"]["authority_layer"] == "proposal"
    assert payload["branch_edges"][0]["target_statute_id"] == "ukpga/1978/30"
    assert payload["impact_projection"]["rows"][0]["source_unit_id"] == "clause:1"


def test_uk_branch_demo_main_outputs_json(capsys) -> None:
    main(types.SimpleNamespace(pretty=False))

    data = json.loads(capsys.readouterr().out)
    assert data["branch"]["authority_layer"] == "proposal"
    assert data["default_enacted_operation_ids"] == []


def test_uk_branch_demo_cli_parser_accepts_pretty_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["uk-branch-demo", "--pretty"])

    assert args.command == "uk-branch-demo"
    assert args.pretty is True
