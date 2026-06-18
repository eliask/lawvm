from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.replay_lints import (
    build_flattened_sublist_findings,
    build_label_sequence_gap_findings,
    build_text_duplication_findings,
)
from lawvm.core.semantic_types import IRNodeKind


def test_build_text_duplication_findings_replay_fold_phase() -> None:
    repeated = " ".join(["sama", "teksti"] * 45)
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated),)),
            IRNode(kind=IRNodeKind.SECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text=repeated),)),),
    )

    findings = build_text_duplication_findings(
        body,
        phase="replay_fold",
        source_statute="1991/1",
    )

    assert findings
    assert findings[0].kind == "text_duplication_warning"
    assert findings[0].role == "observation"
    assert findings[0].stage == "replay_lints"
    assert findings[0].source_statute == "1991/1"
    assert findings[0].detail["message"] == "Replay output contains a suspicious duplicated text tract."
    assert findings[0].detail["phase"] == "replay_fold"


def test_build_text_duplication_findings_materialized_phase() -> None:
    shared_tail = " ".join(["yhteinen", "loppu"] * 45)
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text=f"alku a {shared_tail}"),)),
            IRNode(kind=IRNodeKind.SECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text=f"alku b {shared_tail}"),)),),
    )

    findings = build_text_duplication_findings(
        body,
        phase="materialized",
        source_statute="1991/2",
    )

    assert findings
    assert findings[0].detail["message"] == "Materialized output contains a suspicious duplicated text tract."
    assert findings[0].detail["phase"] == "materialized"


def test_build_flattened_sublist_findings_replay_fold_phase() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=tuple(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label)
                    for label in ("a", "b", "1", "2", "a", "b")
                ),
            ),
        ),
    )

    findings = build_flattened_sublist_findings(
        body,
        phase="replay_fold",
        source_statute="1991/3",
    )

    assert len(findings) == 1
    assert findings[0].kind == "flattened_sublist_family_warning"
    assert findings[0].role == "observation"
    assert findings[0].stage == "replay_lints"
    assert findings[0].blocking is False
    assert findings[0].source_statute == "1991/3"
    assert findings[0].detail["message"] == "Replay output contains a possible flattened sublist family."
    assert findings[0].detail["phase"] == "replay_fold"
    assert findings[0].detail["kind"] == "flattened_sublist_interleaved"


def test_build_flattened_sublist_findings_detects_mixed_alpha_digit_family() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="78",
                children=tuple(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label)
                    for label in ("a", "b", "c", "1", "2", "3")
                ),
            ),
        ),
    )

    findings = build_flattened_sublist_findings(
        body,
        phase="replay_fold",
        source_statute="2004/301",
    )

    assert len(findings) == 1
    assert findings[0].kind == "flattened_sublist_family_warning"
    assert findings[0].detail["kind"] == "flattened_sublist_mixed_family"
    assert findings[0].detail["families"] == ("alpha", "digit")


def test_build_label_sequence_gap_findings_replay_fold_phase() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="107",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="2",
                                children=(IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="g", text="g only"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    findings = build_label_sequence_gap_findings(
        body,
        phase="replay_fold",
        source_statute="2014/527",
    )

    assert findings
    assert all(finding.kind == "label_sequence_gap_warning" for finding in findings)
    target = next(
        finding
        for finding in findings
        if finding.detail["path"] == "body/section:107/subsection:2/paragraph:2"
        and finding.detail["node_kind"] == "subparagraph"
    )
    assert target.role == "observation"
    assert target.stage == "replay_lints"
    assert target.blocking is False
    assert target.source_statute == "2014/527"
    assert target.detail["message"] == "Replay output contains a suspicious legal-unit label sequence gap."
    assert target.detail["phase"] == "replay_fold"
    assert target.detail["kind"] == "label_sequence_starts_late"
    assert target.detail["missing_labels"] == ("a", "b", "c", "d", "e", "f")
