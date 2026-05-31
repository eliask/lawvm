from __future__ import annotations

from hashlib import sha256

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
