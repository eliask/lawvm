from __future__ import annotations

from lawvm.core.phase_result import Finding
from lawvm.tools.invariant_harvest import (
    InvariantHarvestRecord,
    actionability_for_record,
    classify_violation,
    harvest_replay_invariants,
    records_to_audit_rows,
    records_to_self_consistency_rows,
)


def test_classify_violation_mixed_hierarchy() -> None:
    vtype, path, detail = classify_violation(
        "body: direct section:149a alongside chapter:15"
    )
    assert vtype == "mixed_hierarchy"
    assert path == "body"
    assert detail == "section:149a alongside chapter:15"


def test_classify_violation_illegal_edge() -> None:
    vtype, path, detail = classify_violation(
        "body/section:1: unexpected paragraph inside section"
    )
    assert vtype == "illegal_edge"
    assert path == "body/section:1"
    assert detail == "paragraph inside section"


def test_harvest_replay_invariants_prefers_typed_meta() -> None:
    replay_meta = {
        "typed_invariant_violations": [
            {
                "kind": "unexpected_child_kind",
                "path": "body/section:1",
                "parent_kind": "section",
                "child_kind": "paragraph",
                "surface": "replay_fold_tree",
                "profile_id": "core_structural_tree_all",
            },
        ],
        "invariant_violations": [
            "body/section:99: duplicate section:5a (2 times)",
        ],
        "typed_product_tree_invariant_violations": {
            "materialized_tree": [
                {
                    "kind": "sort_order",
                    "path": "body",
                    "child_kind": "section",
                    "previous_label": "5",
                    "next_label": "2",
                    "surface": "materialized_tree",
                    "profile_id": "core_structural_product_hierarchical",
                },
            ],
        },
        "replay_invariant_profiles": [
            {
                "profile_id": "core_replay_strict_v1",
                "tree_profiles": [
                    {"surface": "replay_fold_tree"},
                    {"surface": "materialized_tree"},
                ],
            },
        ],
        "flattened_sublist_warnings": [
            {
                "kind": "flattened_sublist_mixed_family",
                "path": "body/section:4/subsection:1",
                "node_kind": "subsection",
                "phase": "materialized",
            },
        ],
    }

    records = harvest_replay_invariants(replay_meta=replay_meta, findings=())
    audit_rows = records_to_audit_rows("1994/1472", records, chain_length="3")
    self_rows = records_to_self_consistency_rows("1994/1472", records)

    assert [(row["violation_type"], row["path"], row["source"]) for row in audit_rows] == [
        ("illegal_edge", "body/section:1", "replay_meta_tree"),
        ("sort_order", "body", "replay_meta_product"),
        ("flattened_sublist_mixed_family", "body/section:4/subsection:1", "replay_meta_lint"),
    ]
    assert audit_rows[0]["replay_profile_id"] == "core_replay_strict_v1"
    assert audit_rows[2]["audit_status"] == "warning"

    assert {row["signal_type"] for row in self_rows} == {
        "invariant_violation",
        "invariant_lint_warning",
    }
    assert any(
        row["category"] == "flattened_sublist_mixed_family"
        and row["signal_type"] == "invariant_lint_warning"
        for row in self_rows
    )


def test_actionability_marks_pre_dedup_and_label_gap_noise() -> None:
    pre_dedup = InvariantHarvestRecord(
        violation_type="duplicate_label",
        path="body/section:3",
        detail="section:6",
        source="replay_meta_product",
        adj_kind="APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        phase="materialized",
        severity="violation",
    )
    assert (
        actionability_for_record(
            pre_dedup,
            chain_length="3",
            phase_scope="materialized_only",
            detector_family="pre_dedup_duplicate_label",
        )
        == "benign"
    )

    label_gap = InvariantHarvestRecord(
        violation_type="label_sequence_internal_gap",
        path="body/chapter:1",
        detail="section:label_sequence_internal_gap",
        source="replay_meta_lint",
        adj_kind="label_sequence_gap_warning",
        phase="materialized",
        severity="warning",
    )
    assert actionability_for_record(label_gap) == "informational"


def test_harvest_replay_invariants_includes_finding_ledger() -> None:
    findings = (
        Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            detail={
                "message": "Replay tree invariant violated.",
                "violation": "body/section:3: duplicate section:6 (2 times)",
                "phase": "replay_fold",
                "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
            },
            source_statute="2006/254",
            blocking=True,
        ),
        Finding(
            kind="label_sequence_gap_warning",
            role="observation",
            stage="apply",
            detail={
                "kind": "label_sequence_gap",
                "path": "body/section:2",
                "node_kind": "section",
                "phase": "materialized",
            },
            source_statute="",
            blocking=False,
        ),
    )

    records = harvest_replay_invariants(replay_meta={}, findings=findings)
    rows = records_to_audit_rows("1994/1472", records)

    assert [(row["violation_type"], row["source"], row["audit_status"]) for row in rows] == [
        ("duplicate_label", "finding_ledger", "violation"),
        ("label_sequence_gap", "finding_ledger_lint", "warning"),
    ]
