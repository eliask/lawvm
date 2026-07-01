"""Base-authored final-provisions mixed_hierarchy allowance (self-consistency).

These are pure-projector tests for the benign final-provisions bare-block
suppression: a base-authored trailing final/transitional/repeal section that
sits alongside the chapters at the product surface must NOT be flagged as a
mixed_hierarchy self-consistency signal, while a genuine replay INSERT-hoist
(label absent from the base final-provisions block) must stay flagged.
"""
from __future__ import annotations

from lawvm.core.invariant_profiles import (
    structural_product_hierarchical_profile,
)
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.replay_products import fi_product_tree_invariant_violations
from lawvm.finland.tree_invariant_allowances import (
    base_final_provisions_section_labels,
    is_base_authored_final_provisions_section_violation,
)

_PROFILE = structural_product_hierarchical_profile("replay_fold_tree")


def _section(label: str, text: str = "x") -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
    )


def _chapter(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))


def _bare_hcontainer(*sections: IRNode) -> IRNode:
    # num-less final-provisions block sibling of chapters
    return IRNode(kind=IRNodeKind.HCONTAINER, children=tuple(sections))


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def test_witness_collects_bare_block_and_direct_sibling_sections() -> None:
    base = _body(
        _chapter("1", _section("1")),
        _bare_hcontainer(_section("33"), _section("34")),
        _section("35"),  # section directly alongside the chapter
    )
    labels = base_final_provisions_section_labels(base)
    assert "33" in labels
    assert "34" in labels
    assert "35" in labels
    # sections nested inside a chapter must NOT count as final-provisions labels
    assert "1" not in labels


def test_witness_ignores_sections_without_container_sibling() -> None:
    # flat body with only sections (no chapter/part) is not the mixed shape
    base = _body(_section("1"), _section("2"))
    assert base_final_provisions_section_labels(base) == frozenset()


def test_base_authored_final_provisions_section_is_suppressed() -> None:
    # product tree: §33 landed as a bare sibling of chapter:1 — same shape as
    # a base final-provisions section, and §33 IS base-authored.
    product = _body(_chapter("1", _section("1")), _section("33"))
    base_labels = frozenset({"33"})
    violations = fi_product_tree_invariant_violations(
        product, _PROFILE, base_final_provisions_labels=base_labels
    )
    assert not any(v.kind == "mixed_hierarchy_child" for v in violations)


def test_replay_hoisted_section_is_still_flagged() -> None:
    # §48a is NOT in the base final-provisions block -> genuine INSERT-hoist.
    product = _body(_chapter("8", _section("48")), _section("48a"))
    base_labels = frozenset({"76", "77"})  # base block held only §76-77
    violations = fi_product_tree_invariant_violations(
        product, _PROFILE, base_final_provisions_labels=base_labels
    )
    mixed = [v for v in violations if v.kind == "mixed_hierarchy_child"]
    assert mixed, "genuine replay INSERT-hoist must stay flagged"
    assert any(v.label == "48a" for v in mixed)


def test_mixed_statute_splits_benign_from_real() -> None:
    # §40 base-authored final provision (benign) AND §48a replay-hoist (real)
    product = _body(
        _chapter("8", _section("48")),
        _section("40"),
        _section("48a"),
    )
    base_labels = frozenset({"40"})
    violations = fi_product_tree_invariant_violations(
        product, _PROFILE, base_final_provisions_labels=base_labels
    )
    mixed_labels = {v.label for v in violations if v.kind == "mixed_hierarchy_child"}
    assert "40" not in mixed_labels  # benign suppressed
    assert "48a" in mixed_labels  # real kept


def test_empty_witness_suppresses_nothing() -> None:
    product = _body(_chapter("1", _section("1")), _section("33"))
    violations = fi_product_tree_invariant_violations(
        product, _PROFILE, base_final_provisions_labels=frozenset()
    )
    assert any(v.kind == "mixed_hierarchy_child" for v in violations)


def test_predicate_ignores_non_section_and_non_mixed_kinds() -> None:
    # a non-mixed_hierarchy violation is never allowed by this predicate
    from lawvm.core.tree_ops import TreeInvariantViolation

    sort_v = TreeInvariantViolation(
        kind="sort_order", path=(("body", None),), child_kind="section",
        previous_label="5", next_label="3",
    )
    assert not is_base_authored_final_provisions_section_violation(
        sort_v, frozenset({"5", "3"})
    )
