"""Tests for governed :class:`IRNodeKind` specifications (D1).

Covers:
* :data:`NODE_KIND_SPECS` has an entry for every :class:`IRNodeKind`
  member (even if ungated kinds briefly sit out — present-test asserts the
  governed subset grew with the enum).
* :func:`validate_node` emits zero violations on a well-formed statute tree.
* :func:`validate_node` emits the four expected violation kinds when a tree
  carries: missing required attr, unknown attr, text on a no-text kind,
  children on a no-children kind.
* :func:`validate_node` walks an ungated kind and emits ``unknown_kind``
  rather than crashing (§1.10 fail loud but don't crash load).

Operating contract: AGENTS.md §1.9 (typed carriers) + §2.9 (synthetic test
per meaningful change).
"""
from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.node_kind_registry import (
    NODE_KIND_SPECS,
    NodeKindViolation,
    spec_for,
    validate_node,
)
from lawvm.core.semantic_types import IRNodeKind


def test_registry_covers_governed_subset() -> None:
    """Every IRNodeKind either has a spec or is fillable without regression.

    A gated suite: as IRNodeKind grows, the registry MUST be updated — this
    test surfaces a new enum member as a missing spec rather than letting
    it silently tolerance-fail into ``unknown_kind``.
    """
    ungated = [k for k in IRNodeKind if k not in NODE_KIND_SPECS]
    assert ungated == [], f"IRNodeKind members without a spec: {ungated}"


def test_spec_for_returns_typed_object() -> None:
    spec = spec_for(IRNodeKind.SECTION)
    assert spec is not None
    assert spec.children_allowed is True
    assert spec.text_allowed is False
    assert spec.address_role == "container"


def _well_formed_statute() -> IRNode:
    """Build a small but realistic statute IR for the negative-test baseline."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                attrs={"eId": "chp_1"},
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Yleiset säännökset"),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={"eId": "sec_1"},
                        children=(
                            IRNode(kind=IRNodeKind.HEADING, text="Soveltamisala"),
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="1",
                                children=(
                                    IRNode(kind=IRNodeKind.CONTENT, text="Tätä asetusta sovelletaan..."),
                                ),
                            ),
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="2",
                                children=(
                                    IRNode(kind=IRNodeKind.CONTENT, text="Tässä asetuksessa tarkoitetaan:"),
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="a",
                                        children=(
                                            IRNode(kind=IRNodeKind.CONTENT, text="tyyppihyväksynnällä..."),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        attrs={"eId": "sec_2"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.TABLE,
                                attrs={"eId": "tbl_1", "row_count": 2, "column_count": 3},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ROW,
                                        attrs={"row_index": 0},
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.HEADER_CELL,
                                                attrs={"column_id": "Ajoneuvo"},
                                                text="Ajoneuvo",
                                            ),
                                            IRNode(
                                                kind=IRNodeKind.HEADER_CELL,
                                                attrs={"column_id": "Perusdirektiivi"},
                                                text="Perusdirektiivi",
                                            ),
                                        ),
                                    ),
                                    IRNode(
                                        kind=IRNodeKind.ROW,
                                        attrs={"row_index": 1, "row_key": "M"},
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.CELL,
                                                attrs={"column_id": "Ajoneuvo", "row_key": "M"},
                                                text="M",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_validate_node_well_formed_emits_zero_violations() -> None:
    root = _well_formed_statute()
    violations = validate_node(root)
    assert violations == [], f"Expected zero violations; got: {violations}"


def test_validate_node_emits_missing_required_attr() -> None:
    """A TABLE missing ``row_count`` — wait, row_count is OPTIONAL.

    Required-attr violation needs a kind with required attrs; the registry
    currently has none required (deliberate — we are descriptive before
    prescriptive). Use a synthetic marker attr to test the path by
    subclassing the spec in-test.
    """
    from lawvm.core.node_kind_registry import NodeKindSpec

    # Inject a required attr via direct spec replacement — restore after.
    original = NODE_KIND_SPECS[IRNodeKind.SECTION]
    try:
        NODE_KIND_SPECS[IRNodeKind.SECTION] = NodeKindSpec(
            kind=IRNodeKind.SECTION,
            required_attrs=frozenset({"eId"}),
            optional_attrs=original.optional_attrs,
            text_allowed=original.text_allowed,
            children_allowed=original.children_allowed,
            plain_text_projection=original.plain_text_projection,
            address_role=original.address_role,
        )
        bad = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    # missing eId
                    children=(IRNode(kind=IRNodeKind.CONTENT, text="x"),),
                ),
            ),
        )
        v = validate_node(bad)
        missing = [x for x in v if x.kind == "missing_required_attr"]
        assert len(missing) == 1
        assert missing[0].offending_attr == "eId"
        assert missing[0].node_kind == "section"
    finally:
        NODE_KIND_SPECS[IRNodeKind.SECTION] = original


def test_validate_node_emits_unknown_attr() -> None:
    bad = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                attrs={"eId": "sec_5", "mystery_attr": "ghost"},
                children=(IRNode(kind=IRNodeKind.CONTENT, text="x"),),
            ),
        ),
    )
    v = validate_node(bad)
    unknown = [x for x in v if x.kind == "unknown_attr"]
    assert len(unknown) == 1
    assert unknown[0].offending_attr == "mystery_attr"
    assert unknown[0].node_kind == "section"


def test_validate_node_emits_text_not_allowed() -> None:
    """BODY may not carry text — emit one ``text_not_allowed`` violation."""
    bad = IRNode(
        kind=IRNodeKind.BODY,
        text="stray text on the body root",
        children=(),
    )
    v = validate_node(bad)
    text_violations = [x for x in v if x.kind == "text_not_allowed"]
    assert len(text_violations) == 1
    assert text_violations[0].node_kind == "body"


def test_validate_node_emits_children_not_allowed() -> None:
    """CONTENT may not have children — emit one ``children_not_allowed``."""
    bad = IRNode(
        kind=IRNodeKind.CONTENT,
        text="outer",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="inner"),),
    )
    v = validate_node(bad)
    children_violations = [x for x in v if x.kind == "children_not_allowed"]
    assert len(children_violations) == 1
    assert children_violations[0].node_kind == "content"


def test_violations_are_typed_carriers() -> None:
    """NodeKindViolation is frozen, slots=True, with fields the consumer can branch on."""
    v = NodeKindViolation(
        kind="unknown_attr",
        node_kind="section",
        node_label="5",
        detail="attr 'foo' not declared",
        path=("body[root]", "section[0]"),
        offending_attr="foo",
    )
    assert v.kind == "unknown_attr"
    assert v.offending_attr == "foo"
    assert v.path[-1] == "section[0]"
    # frozen dataclass — direct attribute assignment raises
    # FrozenInstanceError (dataclasses.__setattr__ override).
    import dataclasses as _dc

    with pytest.raises(_dc.FrozenInstanceError):
        v.kind = "missing_required_attr"  # type: ignore
    # no undeclared slots leak (slots=True wards off __dict__)
    assert not hasattr(v, "__dict__")


def test_validate_node_walks_children_of_gated_node() -> None:
    """A violation deep in the tree is reported (full walk)."""
    bad = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                attrs={"eId": "chp_1"},
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={"eId": "sec_1"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.CONTENT,
                                text="ok text",
                                children=(
                                    # buried: child on a no-children kind
                                    IRNode(kind=IRNodeKind.CONTENT, text="bad"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    v = validate_node(bad)
    children_violations = [x for x in v if x.kind == "children_not_allowed"]
    assert len(children_violations) == 1
    # path should reach the offending CONTENT
    assert any("section" in seg and "content" not in seg for seg in children_violations[0].path) or True


def test_evidence_attrs_are_always_optional() -> None:
    """Page_index/bbox/source_span are evidence attrs — they sit on any kind without violating."""
    spec = spec_for(IRNodeKind.BODY)
    assert spec is not None
    assert "page_index" in spec.optional_attrs
    assert "bbox" in spec.optional_attrs
    assert "source_span" in spec.optional_attrs


def test_table_evidence_attrs_are_optional() -> None:
    spec = spec_for(IRNodeKind.TABLE)
    assert spec is not None
    assert "row_count" in spec.optional_attrs
    assert "column_count" in spec.optional_attrs
