from __future__ import annotations

import json
from argparse import Namespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import diagnose_phase


def test_diagnose_phase_json_suppresses_raw_replay_chatter_for_1978_38(capsys) -> None:
    diagnose_phase.main(
        Namespace(
            statute_id="1978/38",
            source="2003/741",
            target="",
            detector="duplicate_label",
            mode="legal_pit",
            first_bad_amendment="",
            certificate=False,
            json=True,
        )
    )

    captured = capsys.readouterr()
    merged = captured.out + captured.err

    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" not in merged
    payload = json.loads(captured.out)
    assert payload["statute_id"] == "1978/38"
    assert payload["source_id"] == "2003/741"
    assert "phases" in payload


def test_diagnose_phase_flattened_sublist_detector_uses_typed_lint_bridge() -> None:
    tree = IRNode(
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

    result = diagnose_phase._run_tree_detector(tree, "flattened_sublist_family")

    assert result == ["body/section:1: flattened paragraph family interleaved (alpha) [a, b, 1, 2, a, b]"]
