from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import uk_live_targets


def test_target_paths_from_ir_exports_kind_label_paths_without_body_root() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                        ),
                        IRNode(
                            kind=IRNodeKind.TABLE,
                            label="1",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ROW,
                                    label="2",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="3"),),
            ),
        ),
    )

    assert uk_live_targets.target_paths_from_ir(statute) == (
        "schedule:1",
        "schedule:1/paragraph:3",
        "section:1",
        "section:1/subsection:1",
        "section:1/table:1",
        "section:1/table:1/row:2",
    )


def test_target_paths_from_ir_collapses_empty_presentation_wrappers() -> None:
    statute = IRStatute(
        statute_id="ukpga/1978/30",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CROSS_HEADING,
                    children=(
                        IRNode(
                            kind=IRNodeKind.P1GROUP,
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="1",
                                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert uk_live_targets.target_paths_from_ir(statute) == (
        "section:1",
        "section:1/subsection:1",
    )


def test_target_fingerprints_from_ir_exports_text_and_subtree_hashes() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Section text",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Child"),),
                ),
            ),
        ),
    )

    fingerprints = uk_live_targets.target_fingerprints_from_ir(statute)

    assert fingerprints["section:1"]["kind"] == "section"
    assert fingerprints["section:1"]["text_sha256"] == sha256(
        b"Section text"
    ).hexdigest()
    assert fingerprints["section:1"]["child_count"] == 1
    assert len(fingerprints["section:1"]["subtree_sha256"]) == 64
    assert fingerprints["section:1/subsection:1"]["text_preview"] == "Child"


def test_target_index_row_from_absent_bytes_preserves_statute_and_source() -> None:
    row = uk_live_targets.target_index_row_from_bytes(
        "ukpga/2000/1",
        "current",
        None,
    )

    assert row["schema"] == "lawvm.uk_live_target_index.v1"
    assert row["statute_id"] == "ukpga/2000/1"
    assert row["source"] == "current"
    assert row["source_status"] == "absent"
    assert row["target_paths"] == []


def test_live_target_index_json_report_is_validation_evidence_only(tmp_path) -> None:
    report = uk_live_targets.live_target_index_report_jsonable(
        (
            {
                "schema": "lawvm.uk_live_target_index.v1",
                "statute_id": "ukpga/2000/1",
                "source": "current",
                "source_status": "available",
                "target_paths": ["section:1"],
                "target_fingerprints": {
                    "section:1": {
                        "text_sha256": "a" * 64,
                        "subtree_sha256": "b" * 64,
                    }
                },
            },
        ),
        source="current",
        db_path=Path("data/uk_legislation.farchive"),
        out_path=tmp_path / "live-targets.jsonl",
    )

    assert report["jurisdiction"] == "uk"
    assert report["report_kind"] == "uk_live_target_index_report"
    assert report["schema"] == "lawvm.uk_live_target_index_report.v1"
    assert report["truth_claim"] == "uk_live_target_index_validation_evidence_only"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["row_count"] == 1
    assert report["summary"]["source_status_counts"] == {"available": 1}
    assert report["summary"]["total_target_paths"] == 1
    assert report["summary"]["total_target_fingerprints"] == 1
    assert report["evidence_jsonl"]["target_index_jsonl"]["row_count"] == 1
    assert "live_target_index_as_target_authority" in report["forbidden_shortcuts"]
    assert "mutation_boundary_proof" in report["next_promotion_requires"]
    assert len(report["rows"]) == 1
