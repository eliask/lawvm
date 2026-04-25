"""CrossHair symbolic execution tests for trusted kernels.

These tests use CrossHair to symbolically verify function contracts
by exploring code paths with SMT-backed inputs. Unlike hypothesis
(random sampling) or exhaustive enumeration (small spaces), CrossHair
analyzes the actual branching structure of Python functions.

CrossHair cannot natively generate IRNode instances (the NodeKind Literal
type causes a TypeError in get_type_hints). Instead, each test constructs
concrete IR trees and uses CrossHair's symbolic strings/ints to explore
the branching logic within tree_ops, timeline, and ir functions.

Run with: uv run python -m pytest tests/test_crosshair_kernels.py -v
Note: CrossHair tests are slow (10-30s each). Marked as slow.
"""

from __future__ import annotations

import pytest

crosshair = pytest.importorskip("crosshair")
from crosshair.core_and_libs import analyze_function, AnalysisKind  # noqa: E402
from crosshair.options import AnalysisOptionSet  # noqa: E402
from crosshair.statespace import MessageType  # noqa: E402
from lawvm.core.semantic_types import IRNodeKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAIL_STATES = frozenset({MessageType.POST_FAIL, MessageType.EXEC_ERR, MessageType.POST_ERR})

_DEFAULT_OPTS = AnalysisOptionSet(
    analysis_kind=[AnalysisKind.asserts],
    per_condition_timeout=20.0,
    per_path_timeout=15.0,
)


def _run_crosshair(fn, opts=None):
    """Run CrossHair on fn and return list of failure messages."""
    opts = opts or _DEFAULT_OPTS
    failures = []
    for chk in analyze_function(fn, opts):
        for msg in chk.analyze():
            if msg.state in _FAIL_STATES:
                failures.append(f"{msg.state.name}: {msg.message}")
    return failures


# ---------------------------------------------------------------------------
# Contract 1: tree_ops.find() — if find returns a path, the resolved node's
# kind matches the query kind
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_find_returns_matching_kind():
    """If find() returns a path, resolve() at that path yields a node whose
    kind equals the queried kind."""

    def find_kind_contract(kind: str, label: str) -> None:
        """If find returns a path, the resolved node's kind matches the query."""
        if not kind or not label:
            return
        from lawvm.core.ir import IRNode
        from lawvm.core.tree_ops import find, resolve

        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="second"),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="3", text="nested"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="deep"),
                    ),
                ),
            ),
        )
        path = find(tree, kind, label)
        if path is not None:
            node = resolve(tree, path)
            assert node is not None, "find returned path but resolve returned None"
            assert node.kind == kind, f"find returned node with kind={node.kind!r}, expected {kind!r}"

    failures = _run_crosshair(find_kind_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 2: tree_ops.find() — if find returns a path, the resolved node's
# label matches the query label (after normalization)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_find_returns_matching_label():
    """If find() returns a path, resolve() yields a node whose normalized
    label matches the queried label."""

    def find_label_contract(label: str) -> None:
        """If find returns a path for a section, the label normalizes to match."""
        if not label:
            return
        import re
        from lawvm.core.ir import IRNode
        from lawvm.core.tree_ops import find, resolve

        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="first"),
                IRNode(kind=IRNodeKind.SECTION, label="2a", text="second"),
                IRNode(kind=IRNodeKind.SECTION, label="10", text="tenth"),
            ),
        )

        def norm(s: str) -> str:
            return re.sub(r"[^\d\w]+", "", s).lower()

        path = find(tree, "section", label)
        if path is not None:
            node = resolve(tree, path)
            assert node is not None
            assert norm(node.label or "") == norm(label), (
                f"Label mismatch: node has {node.label!r}, query was {label!r}"
            )

    failures = _run_crosshair(find_label_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 3: tree_ops.replace_at() — result tree has same root kind and is
# a new object (persistent data structure guarantee)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_replace_at_preserves_root_kind_and_identity():
    """replace_at returns a new tree with the same root kind. The returned
    tree must not be the same object as input (persistent data structure)."""

    def replace_at_contract(label: str, new_text: str) -> None:
        """replace_at preserves root kind and returns a new object."""
        if not label:
            return
        from lawvm.core.ir import IRNode
        from lawvm.core.tree_ops import replace_at

        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="old1"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="old2"),
                IRNode(kind=IRNodeKind.SECTION, label="3", text="old3"),
            ),
        )
        replacement = IRNode(kind=IRNodeKind.SECTION, label=label, text=new_text)
        path = [("section", label)]
        result = replace_at(tree, path, replacement)

        assert result.kind == tree.kind, f"Root kind changed: {tree.kind!r} -> {result.kind!r}"
        # Persistent data structure: result must be a new object when tree has children
        assert result is not tree, "replace_at returned the same tree object"

    failures = _run_crosshair(replace_at_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 4: tree_ops.replace_at() — child count is preserved
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_replace_at_preserves_child_count():
    """replace_at does not add or remove children at the replaced level."""

    def replace_at_count_contract(label: str) -> None:
        """replace_at on a direct child preserves body child count."""
        if not label:
            return
        from lawvm.core.ir import IRNode
        from lawvm.core.tree_ops import replace_at

        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="a"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="b"),
            ),
        )
        original_count = len(tree.children)
        replacement = IRNode(kind=IRNodeKind.SECTION, label=label, text="new")
        result = replace_at(tree, [("section", label)], replacement)
        assert len(result.children) == original_count, (
            f"Child count changed: {original_count} -> {len(result.children)}"
        )

    failures = _run_crosshair(replace_at_count_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 5: select_active_version() — returned version effective <= as_of
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_select_active_version_effective_bound():
    """select_active_version never returns a version with effective > as_of."""

    def select_active_contract(eff1: str, eff2: str, as_of: str) -> None:
        """Returned version (if any) must have effective <= as_of."""
        # Require date-shaped strings to avoid nonsense
        if not as_of or not eff1 or not eff2:
            return
        if len(as_of) != 10 or len(eff1) != 10 or len(eff2) != 10:
            return
        # Ensure distinct effective dates for interesting exploration
        if eff1 == eff2:
            return

        from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
        from lawvm.core.timeline import select_active_version

        tl = ProvisionTimeline(
            address=LegalAddress(path=(("section", "1"),)),
            versions=[
                ProvisionVersion(
                    effective=eff1,
                    enacted=eff1,
                    content=IRNode(kind=IRNodeKind.CONTENT, text="v1"),
                ),
                ProvisionVersion(
                    effective=eff2,
                    enacted=eff2,
                    content=IRNode(kind=IRNodeKind.CONTENT, text="v2"),
                ),
            ],
        )
        result = select_active_version(tl, as_of)
        if result is not None:
            assert result.effective <= as_of, f"Returned version effective={result.effective!r} > as_of={as_of!r}"

    failures = _run_crosshair(select_active_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 6: select_active_version() — temporary version wins when both exist
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_select_active_version_temporary_overlay_wins():
    """When both a permanent and a temporary version are active at as_of,
    select_active_version returns the temporary one (overlay wins)."""

    def temp_overlay_contract(as_of: str) -> None:
        """Active temporary overlay must win over permanent background."""
        if not as_of or len(as_of) != 10:
            return
        # Construct a timeline where both a permanent and temporary are active
        if as_of < "2020-01-01" or as_of >= "2025-12-31":
            return

        from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
        from lawvm.core.timeline import select_active_version

        tl = ProvisionTimeline(
            address=LegalAddress(path=(("section", "5"),)),
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.CONTENT, text="permanent"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    expires="2025-12-31",
                    variant_kind="temporary",
                    content=IRNode(kind=IRNodeKind.CONTENT, text="temporary"),
                ),
            ],
        )
        result = select_active_version(tl, as_of)
        assert result is not None, f"Expected active version at {as_of}"
        assert result.variant_kind == "temporary", f"Expected temporary overlay, got {result.variant_kind}"

    failures = _run_crosshair(temp_overlay_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 7: irnode_to_text() — always returns a string
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_irnode_to_text_returns_string():
    """irnode_to_text always returns a str, regardless of tree structure."""

    def irnode_to_text_contract(text: str, child_text: str) -> None:
        """irnode_to_text must always return a str."""
        from lawvm.core.ir import IRNode
        from lawvm.core.ir_helpers import irnode_to_text

        # Test leaf node
        leaf = IRNode(kind=IRNodeKind.CONTENT, text=text)
        result1 = irnode_to_text(leaf)
        assert isinstance(result1, str), f"Expected str, got {type(result1)}"

        # Test parent with child
        parent = IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(IRNode(kind=IRNodeKind.CONTENT, text=child_text),),
        )
        result2 = irnode_to_text(parent)
        assert isinstance(result2, str), f"Expected str, got {type(result2)}"

        # Test empty node
        empty = IRNode(kind=IRNodeKind.BODY)
        result3 = irnode_to_text(empty)
        assert isinstance(result3, str), f"Expected str, got {type(result3)}"

    failures = _run_crosshair(irnode_to_text_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 8: irnode_to_text() — leaf node returns its own text
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_irnode_to_text_leaf_identity():
    """For a leaf node with text and no children, irnode_to_text returns
    exactly that text."""

    def irnode_to_text_leaf_contract(text: str) -> None:
        """Leaf node's irnode_to_text equals its .text field."""
        from lawvm.core.ir import IRNode
        from lawvm.core.ir_helpers import irnode_to_text

        leaf = IRNode(kind=IRNodeKind.CONTENT, text=text)
        result = irnode_to_text(leaf)
        if text:  # irnode_to_text returns node.text when truthy
            assert result == text, f"Expected {text!r}, got {result!r}"
        else:
            # Empty text: irnode_to_text returns "" from joining empty children
            assert result == "", f"Expected empty string, got {result!r}"

    failures = _run_crosshair(irnode_to_text_leaf_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 9: irnode_content_hash() — deterministic and length-correct
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_irnode_content_hash_deterministic():
    """irnode_content_hash returns a 64-char hex string for live content,
    empty string for None, and is deterministic (same input -> same output)."""

    def hash_contract(text: str) -> None:
        """Hash is deterministic, 64 hex chars for content, empty for None."""
        from lawvm.core.ir import IRNode
        from lawvm.core.ir_helpers import irnode_content_hash

        node = IRNode(kind=IRNodeKind.CONTENT, text=text)
        h1 = irnode_content_hash(node)
        h2 = irnode_content_hash(node)
        assert h1 == h2, "Hash is not deterministic"
        assert len(h1) == 64, f"Hash length is {len(h1)}, expected 64"

        # None -> empty string
        h_none = irnode_content_hash(None)
        assert h_none == "", f"Expected empty hash for None, got {h_none!r}"

    failures = _run_crosshair(hash_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Contract 10: resolve() round-trip — resolve after replace_at finds the replacement
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_resolve_finds_replaced_content():
    """After replace_at, resolve at the same path returns the replacement content."""

    def resolve_roundtrip_contract(new_text: str) -> None:
        """resolve(replace_at(tree, path, R), path) returns R."""
        from lawvm.core.ir import IRNode
        from lawvm.core.tree_ops import replace_at, resolve

        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="original"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="other"),
            ),
        )
        replacement = IRNode(kind=IRNodeKind.SECTION, label="1", text=new_text)
        path = [("section", "1")]
        result = replace_at(tree, path, replacement)

        found = resolve(result, path)
        assert found is not None, "resolve returned None after replace_at"
        assert found.text == new_text, f"Expected text={new_text!r}, got {found.text!r}"

    failures = _run_crosshair(resolve_roundtrip_contract)
    assert failures == [], "CrossHair found counterexamples:\n" + "\n".join(failures)
