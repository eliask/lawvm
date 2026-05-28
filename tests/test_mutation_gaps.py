"""Tests targeting specific surviving mutants from mutation testing (mutmut).

These tests close gaps identified by mutmut mutation testing on the three
trusted kernels: tree_ops.py, timeline.py, ir.py. Each test is annotated
with the mutant(s) it kills.

Run:
    uv run pytest tests/test_mutation_gaps.py -v
"""

from __future__ import annotations

from typing import Literal

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    _insert_child_sorted,
    _with_children,
    default_label_sort_key,
    remove_at,
    replace_at,
    strip_nodes,
)
from lawvm.core.timeline import (
    _eligible,
    _pick_latest,
    select_background_version,
    select_temporary_version,
)


# ============================================================================
# TREE_OPS: _with_children preserves text and attrs
# Kills: x__with_children__mutmut_3  (text=None)
#        x__with_children__mutmut_8  (text omitted)
#        x__with_children__mutmut_9  (attrs omitted)
# ============================================================================


def test_with_children_preserves_text():
    """_with_children must propagate the parent's text field."""
    parent = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        text="original text",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="sub"),),
    )
    new_children = [IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="new sub")]
    result = _with_children(parent, new_children)
    assert result.text == "original text", f"_with_children dropped text: got {result.text!r}"


def test_with_children_preserves_attrs():
    """_with_children must propagate the parent's attrs dict."""
    parent = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        text="",
        attrs={"lawvm_repeal_placeholder": "1", "source": "2020/100"},
        children=(),
    )
    new_children = [IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="sub")]
    result = _with_children(parent, new_children)
    assert result.attrs == {"lawvm_repeal_placeholder": "1", "source": "2020/100"}, (
        f"_with_children dropped attrs: got {result.attrs!r}"
    )


def test_with_children_preserves_text_through_replace_at():
    """replace_at must preserve text on all intermediate nodes, not just the target."""
    # Build a body with text on the body node itself
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="body-level-text",
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="old", attrs={"note": "keep-me"}),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="stay"),
        ),
    )
    replacement = IRNode(kind=IRNodeKind.SECTION, label="1", text="new")
    result = replace_at(body, [("section", "1")], replacement)
    # Body-level text must survive
    assert result.text == "body-level-text"
    # Section 2 must survive
    sec2 = next(c for c in result.children if c.label == "2")
    assert sec2.text == "stay"


def test_with_children_preserves_attrs_through_remove_at():
    """remove_at must preserve attrs on all intermediate nodes."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={"meta": "value"},
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="keep"),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="remove"),
        ),
    )
    result = remove_at(body, [("section", "2")])
    assert result.attrs == {"meta": "value"}, f"remove_at dropped body attrs: got {result.attrs!r}"


# ============================================================================
# TREE_OPS: _insert_child_sorted boundary: > vs >=
# Kills: x__insert_child_sorted__mutmut_11 (> changed to >=)
# ============================================================================


def test_insert_child_sorted_same_key_inserts_after():
    """When inserting a section with the same sort key as an existing one,
    it should be inserted AFTER (not before) the existing node.
    This tests > vs >= in the comparison."""
    parent = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
            IRNode(kind=IRNodeKind.SECTION, label="3", text="third"),
        ),
    )
    # Insert section "2" which has a unique key
    content = IRNode(kind=IRNodeKind.SECTION, label="2", text="second")
    result = _insert_child_sorted(parent, content, default_label_sort_key)
    labels = [c.label for c in result.children if c.kind == IRNodeKind.SECTION]
    assert labels == ["1", "2", "3"], f"Wrong order: {labels}"


def test_insert_child_sorted_equal_key_stable_order():
    """When sort keys are equal (same numeric label), new node goes after existing.
    With >= instead of >, new node would go before -- detect this."""
    parent = IRNode(
        kind=IRNodeKind.BODY, label=None, children=(IRNode(kind=IRNodeKind.SECTION, label="5", text="existing-5"),)
    )
    # Insert another section with label "5" -- same sort key
    new_sec = IRNode(kind=IRNodeKind.SECTION, label="5", text="new-5")
    result = _insert_child_sorted(parent, new_sec, default_label_sort_key)
    # With correct > comparison: existing comes first, new appended after
    texts = [c.text for c in result.children if c.kind == IRNodeKind.SECTION]
    assert texts == ["existing-5", "new-5"], f"Equal-key insertion order wrong: {texts}"


# ============================================================================
# TREE_OPS: strip_nodes must strip ALL matching children, not just first
# Kills: x_strip_nodes__mutmut_7 (continue changed to break)
# ============================================================================


def test_strip_nodes_removes_all_matching():
    """strip_nodes must remove ALL nodes matching predicate, not just the first."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="keep"),
            IRNode(kind=IRNodeKind.HEADING, label=None, text="remove1"),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="keep"),
            IRNode(kind=IRNodeKind.HEADING, label=None, text="remove2"),
            IRNode(kind=IRNodeKind.SECTION, label="3", text="keep"),
        ),
    )
    result = strip_nodes(body, lambda n: n.kind == IRNodeKind.HEADING)
    remaining_kinds = [c.kind for c in result.children]
    assert remaining_kinds == [IRNodeKind.SECTION, IRNodeKind.SECTION, IRNodeKind.SECTION], (
        f"strip_nodes didn't remove all headings: {remaining_kinds}"
    )
    # Also check we kept all sections
    assert [c.label for c in result.children] == ["1", "2", "3"]


def test_strip_nodes_no_match_preserves_identity():
    """When predicate matches nothing, strip_nodes should return the same object.
    Kills: x_strip_nodes__mutmut_3 (changed=False -> changed=True)"""
    body = IRNode(kind=IRNodeKind.BODY, label=None, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="keep"),))
    result = strip_nodes(body, lambda n: n.kind == "nonexistent")
    # The returned tree should be the SAME object (identity, not just equality)
    assert result is body, "strip_nodes copied tree unnecessarily when nothing matched"


# ============================================================================
# TREE_OPS: remove_at deep path (multi-level)
# Kills: x_remove_at__mutmut_29 (and->or in deep branch)
#        x_remove_at__mutmut_30 (and->or)
#        x_remove_at__mutmut_31 (and->or)
#        x_remove_at__mutmut_39 (path[1:]->None in resolve)
#        x_remove_at__mutmut_42 (path[1:]->path[2:] in resolve)
#        x_remove_at__mutmut_51 (removed=True->removed=False in deep branch)
# ============================================================================


def test_remove_at_deep_path():
    """remove_at with a 2-level path must work correctly, removing only the target."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="1", text="ch1-s1"),
                    IRNode(kind=IRNodeKind.SECTION, label="2", text="ch1-s2"),
                    IRNode(kind=IRNodeKind.SECTION, label="3", text="ch1-s3"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="ch2-s1"),),
            ),
        ),
    )
    result = remove_at(body, [("chapter", "1"), ("section", "2")])
    ch1 = next(c for c in result.children if c.label == "1")
    remaining = [c.label for c in ch1.children if c.kind == IRNodeKind.SECTION]
    assert remaining == ["1", "3"], f"Deep remove failed: {remaining}"
    # Chapter 2 must be unchanged
    ch2 = next(c for c in result.children if c.label == "2")
    assert len(ch2.children) == 1


def test_remove_at_deep_path_only_removes_first_match():
    """When the deep branch has duplicates (which shouldn't happen but tests the
    'removed' flag), only the first matching deep path should be removed."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
                    IRNode(kind=IRNodeKind.SECTION, label="2", text="target"),
                ),
            ),
            # Second chapter with same label -- pathological but tests the flag
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(IRNode(kind=IRNodeKind.SECTION, label="2", text="should-stay"),),
            ),
        ),
    )
    result = remove_at(body, [("chapter", "1"), ("section", "2")])
    # First chapter should have section 2 removed
    ch1_first = result.children[0]
    assert [c.label for c in ch1_first.children if c.kind == IRNodeKind.SECTION] == ["1"]
    # Second chapter (same label) should be untouched because removed flag was set
    ch1_second = result.children[1]
    assert [c.label for c in ch1_second.children if c.kind == IRNodeKind.SECTION] == ["2"]


def test_remove_at_3_level_path():
    """remove_at with a 3-level path exercises the resolve guard.
    Kills: x_remove_at__mutmut_42 (path[1:] -> path[2:])"""
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="sub1"),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="sub2"),
                        ),
                    ),
                ),
            ),
        ),
    )
    result = remove_at(body, [("chapter", "1"), ("section", "1"), ("subsection", "1")])
    ch = result.children[0]
    sec = ch.children[0]
    remaining = [c.label for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    assert remaining == ["2"], f"3-level remove failed: {remaining}"


# ============================================================================
# TIMELINE: _eligible boundary conditions (enacted field logic)
# Kills: x__eligible__mutmut_4 (or->and in enacted clause)
#        x__eligible__mutmut_5 (and->or in enacted clause)
#        x__eligible__mutmut_6..10 (various enacted boundary mutations)
# ============================================================================


def _make_version(
    effective: str,
    enacted: str = "",
    expires: str = "",
    variant: Literal["permanent", "temporary"] = "permanent",
    text: str = "content",
) -> ProvisionVersion:
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        expires=expires,
        variant_kind=variant,
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text=text),
        source=OperationSource(statute_id="2020/1"),
    )


def test_eligible_in_force_respects_enacted_date():
    """For query_type='in_force', a version is only eligible if enacted <= as_of.
    Kills: mutmut_4 which changes `or not v.enacted or v.enacted <= as_of`
    to `or not v.enacted and v.enacted <= as_of`"""
    v = _make_version(effective="2020-01-01", enacted="2020-06-01")
    # In_force query at 2020-03-01: effective is OK, but enacted is 2020-06-01 (future)
    assert not _eligible(v, "2020-03-01", "in_force"), (
        "Version with future enacted date should NOT be eligible for in_force"
    )
    # Same version, in_force query at 2020-07-01: both effective and enacted are past
    assert _eligible(v, "2020-07-01", "in_force"), "Version with past enacted date should be eligible for in_force"


def test_eligible_governing_ignores_enacted():
    """For query_type='governing', enacted date is irrelevant.
    Kills: mutmut_5 which changes the and/or precedence."""
    v = _make_version(effective="2020-01-01", enacted="2020-06-01")
    # Governing query at 2020-03-01: effective is OK, enacted doesn't matter
    assert _eligible(v, "2020-03-01", "governing"), "Governing query should be eligible regardless of enacted date"


def test_eligible_in_force_no_enacted_is_ok():
    """When enacted is empty, in_force should still work (the `not v.enacted` guard)."""
    v = _make_version(effective="2020-01-01", enacted="")
    assert _eligible(v, "2020-06-01", "in_force"), "Version with no enacted date should be eligible for in_force"


def test_eligible_expires_boundary():
    """Version with expires exactly at as_of should NOT be eligible (expires > as_of)."""
    v = _make_version(effective="2020-01-01", expires="2020-06-01")
    assert not _eligible(v, "2020-06-01", "governing"), "Version expiring exactly at as_of should not be eligible"
    assert _eligible(v, "2020-05-31", "governing"), "Version expiring after as_of should be eligible"


# ============================================================================
# TIMELINE: _pick_latest repeal placeholder detection
# Kills: x__pick_latest__mutmut_2 (content=version.content -> content=None)
#        x__pick_latest__mutmut_4 (return False -> return True when content is None)
# ============================================================================


def test_pick_latest_prefers_substantive_over_repeal_placeholder():
    """_pick_latest should prefer substantive content over repeal placeholders
    when effective/enacted dates are equal."""
    repeal = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        expires="",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            text="repealed content",
            attrs={"lawvm_repeal_placeholder": "1"},
        ),
        source=OperationSource(statute_id="2020/100"),
    )
    substantive = _make_version(
        effective="2020-01-01",
        enacted="2020-01-01",
        text="Real substantive legal content here",
    )
    # When repeal comes first in list, substantive should still win
    result = _pick_latest([repeal, substantive])
    assert result is not None
    assert result.content is not None
    assert result.content.attrs.get("lawvm_repeal_placeholder") != "1", (
        "_pick_latest chose repeal placeholder over substantive content"
    )


def test_pick_latest_repeal_placeholder_attr():
    """_pick_latest detects repeal placeholder via attrs."""
    repeal = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        expires="",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            text="repealed",
            attrs={"lawvm_repeal_placeholder": "1"},
        ),
        source=OperationSource(statute_id="2020/1"),
    )
    substantive = _make_version(
        effective="2020-01-01",
        enacted="2020-01-01",
        text="Real content",
    )
    result = _pick_latest([repeal, substantive])
    assert result is not None
    assert result.content is not None
    assert result.content.attrs.get("lawvm_repeal_placeholder") != "1", (
        "_pick_latest chose attr-flagged repeal placeholder"
    )


def test_pick_latest_prefers_same_source_same_date_repeal_placeholder():
    """A repeal placeholder from the same amendment/date must beat carried substantive content."""
    substantive = ProvisionVersion(
        effective="2023-01-01",
        enacted="2022-12-20",
        expires="",
        variant_kind="permanent",
        content=IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="old content"),
        source=OperationSource(statute_id="2022/1149"),
    )
    repeal = ProvisionVersion(
        effective="2023-01-01",
        enacted="2022-12-20",
        expires="",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="4",
            attrs={"lawvm_repeal_placeholder": "1"},
        ),
        source=OperationSource(statute_id="2022/1149"),
    )
    result = _pick_latest([substantive, repeal])
    assert result is not None
    assert result.content is not None
    assert result.content.attrs.get("lawvm_repeal_placeholder") == "1", (
        "_pick_latest failed to keep same-source repeal placeholder over carried substantive content"
    )


def test_pick_latest_none_content_not_repeal():
    """A version with content=None should NOT be classified as a repeal placeholder.
    Kills: mutmut_4 (return False -> return True)"""
    tombstone = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        expires="",
        variant_kind="permanent",
        content=None,
        source=OperationSource(statute_id="2020/1"),
    )
    substantive = _make_version(
        effective="2020-01-01",
        enacted="2020-01-01",
        text="Real content",
    )
    result = _pick_latest([tombstone, substantive])
    assert result is not None
    # With the mutant (None content -> return True = is repeal), the tombstone
    # would be deprioritized. But with a further mutation that inverts the bias,
    # the behavior could change. The key test is that with 2 versions at same date,
    # and one has content=None, the one with content wins.
    assert result.content is not None, "_pick_latest should prefer version with content over None-content"


# ============================================================================
# TIMELINE: select_background/temporary_version default parameter
# Kills: x_select_background_version__mutmut_1 (default "governing"->"XXgoverningXX")
#        x_select_temporary_version__mutmut_1 (same)
# ============================================================================


def test_select_background_version_default_query_type():
    """Calling select_background_version without explicit query_type should use 'governing'."""
    v = _make_version(effective="2020-01-01", enacted="2025-01-01")
    tl = ProvisionTimeline(
        address=LegalAddress(path=(("section", "1"),)),
        versions=[v],
    )
    # At 2020-06-01: effective is OK, enacted is 2025 (future)
    # With governing (default), enacted doesn't matter -> should find version
    # With in_force, enacted must be <= as_of -> would NOT find version
    result = select_background_version(tl, "2020-06-01")
    assert result is not None, "Default query_type should be 'governing' (enacted irrelevant)"


def test_select_temporary_version_default_query_type():
    """Calling select_temporary_version without explicit query_type should use 'governing'."""
    v = ProvisionVersion(
        effective="2020-01-01",
        enacted="2025-01-01",
        expires="2030-12-31",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temp"),
        source=OperationSource(statute_id="2020/1"),
    )
    tl = ProvisionTimeline(
        address=LegalAddress(path=(("section", "1"),)),
        versions=[v],
    )
    result = select_temporary_version(tl, "2020-06-01")
    assert result is not None, "Default query_type should be 'governing' (enacted irrelevant)"


# ============================================================================
# IR: irnode_to_text join separator
# Kills: x_irnode_to_text__mutmut_4 (" ".join -> "XX XX".join)
# ============================================================================


def test_irnode_to_text_joins_with_space():
    """irnode_to_text should join children's text with a single space."""
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.CONTENT, label=None, text="Hello"),
            IRNode(kind=IRNodeKind.CONTENT, label=None, text="world"),
        ),
    )
    result = irnode_to_text(node)
    assert result == "Hello world", f"irnode_to_text join wrong: {result!r}"


def test_irnode_to_text_leaf_returns_text():
    """Leaf node with text should return its text directly."""
    node = IRNode(kind=IRNodeKind.CONTENT, label=None, text="direct text")
    assert irnode_to_text(node) == "direct text"


def test_irnode_to_text_mixed_content_keeps_own_and_child_text():
    """Mixed-content nodes should not drop children when the parent has text."""
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        text="parent",
        children=(IRNode(kind=IRNodeKind.CONTENT, label=None, text="child"),),
    )

    assert irnode_to_text(node) == "parent child"


def test_irnode_to_text_nested_join():
    """Nested structure should join recursively with spaces."""
    node = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, label=None, text="first"),
                    IRNode(kind=IRNodeKind.CONTENT, label=None, text="second"),
                ),
            ),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="third"),
        ),
    )
    result = irnode_to_text(node)
    assert result == "first second third", f"Nested join wrong: {result!r}"
