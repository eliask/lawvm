from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.replay_lints import build_text_duplication_findings
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
