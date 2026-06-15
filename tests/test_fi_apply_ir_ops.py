from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland import apply_ir_ops
from lawvm.finland.apply_ir_ops import _strip_standalone_subsection_item_prefixes_ir


def test_strip_standalone_subsection_item_prefixes_uses_compiled_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_re_match(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recursive hot path must use the compiled prefix regex")

    monkeypatch.setattr(apply_ir_ops.re, "match", fail_re_match)

    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.CONTENT,
                                text="1) Capitalized carried text.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    stripped = _strip_standalone_subsection_item_prefixes_ir(body)

    subsection = stripped.children[0].children[0]
    assert subsection.children[0].text == "Capitalized carried text."


def test_strip_standalone_subsection_item_prefixes_does_not_strip_lowercase() -> None:
    node = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text="1) lowercase carried text.",
            ),
        ),
    )

    assert _strip_standalone_subsection_item_prefixes_ir(node) is node
