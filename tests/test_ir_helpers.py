from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import kind_for_tag, is_zombie, kind_str
from lawvm.core.ir_helpers import ir_statute_from_dict, irnode_content_hash, irnode_from_dict, irnode_to_text
from lawvm.core.semantic_types import IRNodeKind


def test_kind_for_tag_returns_known_enum_members() -> None:
    assert kind_for_tag("section") is IRNodeKind.SECTION
    assert kind_for_tag("body") is IRNodeKind.BODY
    assert kind_for_tag("not-a-tag") is None


def test_kind_str_accepts_enum_and_plain_strings() -> None:
    assert kind_str(IRNodeKind.SECTION) == "section"
    assert kind_str("section") == "section"


def test_irnode_to_text_flattens_text_and_children() -> None:
    node = IRNode(
        kind=IRNodeKind.SECTION,
        text="Parent",
        children=(
            IRNode(kind=IRNodeKind.CONTENT, text="first"),
            IRNode(kind=IRNodeKind.CONTENT, text="second"),
        ),
    )

    assert irnode_to_text(node) == "Parent first second"


def test_irnode_content_hash_matches_text_and_is_tombstone_safe() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, text="A section")
    assert irnode_content_hash(node) == irnode_content_hash(node)
    assert len(irnode_content_hash(node)) == 64
    assert irnode_content_hash(None) == ""


def test_irnode_from_dict_rejects_unknown_kind_strings() -> None:
    with pytest.raises(ValueError, match="not-a-tag"):
        irnode_from_dict({"kind": "not-a-tag"})


def test_irnode_from_dict_rejects_artifact_envelopes() -> None:
    with pytest.raises(ValueError, match="unpack the artifact envelope first"):
        irnode_from_dict(
            {
                "schema": "lawvm.ir",
                "producer": "test",
                "version": "1",
                "payload": {},
                "status": {"kind": "complete"},
            }
        )


def test_ir_statute_from_dict_rejects_artifact_envelopes() -> None:
    with pytest.raises(ValueError, match="unpack the artifact envelope first"):
        ir_statute_from_dict(
            {
                "schema": "lawvm.ir",
                "producer": "test",
                "version": "1",
                "payload": {},
                "status": {"kind": "complete"},
            }
        )


def test_is_zombie_matches_irnode_method_semantics() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, text="....")
    child = IRNode(kind=IRNodeKind.CONTENT, text="live")
    parent = IRNode(kind=IRNodeKind.SECTION, children=(node, child))

    assert is_zombie(node)
    assert not is_zombie(child)
    assert not is_zombie(parent)
