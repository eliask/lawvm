from __future__ import annotations

import pytest

from jurisdiction_starter.p5_runtime_scaffold import (
    P5_BLOCKED_RULE_SUFFIX,
    P5_BLOCKED_STATUS,
    StarterP5SourceUnit,
    build_blocked_p5_runtime_scaffold,
)


def test_blocked_p5_runtime_scaffold_emits_non_claim_rows_for_every_source_unit() -> None:
    artifact = build_blocked_p5_runtime_scaffold(
        frontend_id="starter",
        run_id="run-1",
        source_id="amending-act-1",
        base_id="base-act-1",
        source_units=(
            StarterP5SourceUnit(
                source_artifact_id="artifact-1",
                source_unit_id="unit-1",
                source_locator="xml:/act/body/section[1]",
                raw_text="Section 3 is amended.",
            ),
            StarterP5SourceUnit(
                source_artifact_id="artifact-1",
                source_unit_id="unit-2",
                source_locator="xml:/act/body/section[2]",
                raw_text="The table is amended.",
            ),
        ),
    )

    clauses = artifact.clause_surface["clauses"]
    assert len(clauses) == 2
    assert [row["status"] for row in clauses] == [P5_BLOCKED_STATUS, P5_BLOCKED_STATUS]
    assert [row["confidence"] for row in clauses] == ["unresolved", "unresolved"]
    assert [row["target_hints"] for row in clauses] == [[], []]
    assert [row["strict_disposition"] for row in clauses] == ["block", "block"]
    assert [row["quirks_disposition"] for row in clauses] == ["skip_with_finding", "skip_with_finding"]

    claim_summary = artifact.evidence_pack_summary["claim_summary"]
    assert claim_summary["accepted_p5_clause_rows"] == 0
    assert claim_summary["operation_effect_rows"] == 0
    assert claim_summary["accepted_operation_effect_rows"] == 0
    assert claim_summary["replay_attempts"] == 0
    assert claim_summary["replay_successes"] == 0

    non_claim_summary = artifact.evidence_pack_summary["non_claim_summary"]
    assert non_claim_summary == {
        "blocked_p5_clause_rows": 2,
        "blocking_findings": 2,
    }


def test_blocked_p5_runtime_scaffold_links_stable_findings_to_clause_rows() -> None:
    artifact = build_blocked_p5_runtime_scaffold(
        frontend_id="starter",
        run_id="run-1",
        source_id="amending-act-1",
        base_id=None,
        source_units=(
            StarterP5SourceUnit(
                source_artifact_id="artifact-1",
                source_unit_id="unit-1",
                source_locator="xml:/act/body/section[1]",
                raw_text="Section 3 is amended.",
            ),
        ),
    )

    clause = artifact.clause_surface["clauses"][0]
    finding = artifact.findings[0]
    assert clause["finding_ids"] == [finding["finding_id"]]
    assert finding["rule_id"] == f"starter.{P5_BLOCKED_RULE_SUFFIX}"
    assert finding["phase"] == "P5"
    assert finding["family"] == "blocked_clause_surface"
    assert finding["related_operation_effect_row_ids"] == []
    assert finding["related_replay_row_ids"] == []
    assert finding["blocking"] is True
    assert finding["strict_disposition"] == "block"
    assert finding["quirks_disposition"] == "skip_with_finding"


def test_blocked_p5_runtime_scaffold_rejects_duplicate_source_unit_identity() -> None:
    duplicate = StarterP5SourceUnit(
        source_artifact_id="artifact-1",
        source_unit_id="unit-1",
        source_locator="xml:/act/body/section[1]",
        raw_text="Section 3 is amended.",
    )

    with pytest.raises(ValueError, match="duplicate P5 source unit"):
        build_blocked_p5_runtime_scaffold(
            frontend_id="starter",
            run_id="run-1",
            source_id="amending-act-1",
            base_id=None,
            source_units=(duplicate, duplicate),
        )


def test_blocked_p5_runtime_scaffold_rejects_generated_clause_id_collision() -> None:
    with pytest.raises(ValueError, match="duplicate generated P5 clause id"):
        build_blocked_p5_runtime_scaffold(
            frontend_id="starter",
            run_id="run-1",
            source_id="amending-act-1",
            base_id=None,
            source_units=(
                StarterP5SourceUnit(
                    source_artifact_id="artifact-1",
                    source_unit_id="unit 1",
                    source_locator="xml:/act/body/section[1]",
                    raw_text="Section 3 is amended.",
                ),
                StarterP5SourceUnit(
                    source_artifact_id="artifact-1",
                    source_unit_id="unit-1",
                    source_locator="xml:/act/body/section[2]",
                    raw_text="Section 4 is amended.",
                ),
            ),
        )
