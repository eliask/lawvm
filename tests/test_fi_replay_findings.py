from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.replay_findings import _pre_dedup_duplicate_details


def test_pre_dedup_duplicate_details_uses_typed_duplicate_invariant_records() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
        ),
    )

    assert _pre_dedup_duplicate_details(tree) == [
        {"path": "body", "kind": "section", "label": "1"}
    ]
