"""Output equivalence for the shared tree-invariant scanner.

Surgical pin: ``iter_tree_invariant_violations`` yields the same typed
violations, in the same order, regardless of which family pre-resolution or
per-child ``kind_str`` caching the implementation applies. The perf hot path
(``_check`` in ``core/tree_ops.py``) is visited ~17M times on a full UK
statute replay (cProfile 2026-06-24 ``ukpga/1988/1``): the per-call
``_kind_str`` calls dominate ``enum.__hash__`` at 102M calls / 21s. A refactor
that hoists the family predicates out of the closure and caches the
child->kind_str mapping per call must not change what violations are emitted
or their relative order.

These are family-equivalence assertions, not exact-count snapshots — they
survive any same-output reorder of the internal walk.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import iter_tree_invariant_violations

# All four UK-replay families in one tree so the perf path runs through each
# ``_wants(...)`` branch in a single recursion.
_UK_REPLAY_FAMILIES = (
    "duplicate_label",
    "sort_order",
    "unexpected_child_kind",
    "mixed_hierarchy_child",
)


def _uk_kind(value: str) -> IRNodeKind:
    return IRNodeKind(value)


def _tree_with_all_violation_kinds() -> IRNode:
    """A BODY containing every violation family the UK replay scanner checks."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            # duplicate_label + sort_order: two equal-label sections plus one out-of-order
            IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                children=(),
            ),
            IRNode(kind=IRNodeKind.SECTION, label="3", children=()),
            IRNode(kind=IRNodeKind.SECTION, label="5", children=()),
            # mixed_hierarchy_child: a chapter after a sibling section
            IRNode(kind=IRNodeKind.CHAPTER, label="9", children=()),
            # unexpected_child_kind: a NUM sitting directly in BODY (no Part/Chapter wrap)
            IRNode(
                kind=IRNodeKind.NUM,
                label="1",
                children=(
                    # nested duplicate_label + sort_order on a deeper level
                    IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=()),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=()),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=()),
                ),
            ),
        ),
    )


def test_uk_replay_family_subset_emits_each_kind_present() -> None:
    tree = _tree_with_all_violation_kinds()
    violations = list(
        iter_tree_invariant_violations(tree, families=frozenset(_UK_REPLAY_FAMILIES))
    )
    kinds = {v.kind for v in violations}
    # Each family that should fire on this fixture is present.
    assert "duplicate_label" in kinds
    assert "sort_order" in kinds
    assert "mixed_hierarchy_child" in kinds
    assert "unexpected_child_kind" in kinds


def test_kind_strings_are_canonical_in_emitted_violations() -> None:
    """Per-child ``kind_str`` caching must use the canonical IRNodeKind.value
    string, not its ``__str__`` representation. The path/parent_kind/child_kind
    fields must read as the lowercase enum value (``section`` not ``SECTION``)."""
    tree = _tree_with_all_violation_kinds()
    violations = list(
        iter_tree_invariant_violations(tree, families=frozenset(_UK_REPLAY_FAMILIES))
    )
    for v in violations:
        # Every violation with a path step uses canonical kind strings.
        for kind_str_value, _label in v.path:
            assert isinstance(kind_str_value, str)
            assert kind_str_value == kind_str_value.lower()
        if v.parent_kind is not None:
            assert v.parent_kind == v.parent_kind.lower()
        if v.child_kind is not None:
            assert v.child_kind == v.child_kind.lower()


def test_repeated_calls_are_stable_and_identical() -> None:
    """A subtle per-child caching refactor could drift if the cache leaks
    across recursion levels. Pin determinism: a second scan of the same tree
    under the same family selection returns the same violations in the same
    order."""
    tree = _tree_with_all_violation_kinds()
    families = frozenset(_UK_REPLAY_FAMILIES)
    first = list(iter_tree_invariant_violations(tree, families=families))
    second = list(iter_tree_invariant_violations(tree, families=families))
    assert len(first) == len(second)
    for a, b in zip(first, second, strict=True):
        assert a == b


def test_empty_families_filter_emits_nothing() -> None:
    tree = _tree_with_all_violation_kinds()
    violations = list(iter_tree_invariant_violations(tree, families=frozenset()))
    assert violations == []
