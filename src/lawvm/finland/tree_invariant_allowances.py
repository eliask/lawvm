"""Finland-specific tree-invariant allowances."""

from __future__ import annotations

from lawvm.core.invariant_profiles import TreeInvariantViolation
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import normalized_label_key


def _kind_value(node: IRNode) -> str:
    return node.kind.value if isinstance(node.kind, IRNodeKind) else str(node.kind)


def _resolve_invariant_path(
    tree: IRNode,
    path: tuple[tuple[str, str | None], ...],
) -> IRNode | None:
    if not path:
        return None
    first_kind, first_label = path[0]
    if _kind_value(tree) != first_kind or tree.label != first_label:
        return None
    node = tree
    for kind, label in path[1:]:
        match = next(
            (
                child
                for child in node.children
                if _kind_value(child) == kind and child.label == label
            ),
            None,
        )
        if match is None:
            return None
        node = match
    return node


def _has_fi_commencement_heading(section: IRNode) -> bool:
    for child in section.children:
        if child.kind is IRNodeKind.HEADING and "voimaantulo" in irnode_to_text(child).casefold():
            return True
    prefix = irnode_to_text(section)[:240].casefold()
    return "voimaantulo" in prefix or "tulee voimaan" in prefix


def base_final_provisions_section_labels(base_ir: IRNode) -> frozenset[str]:
    """Normalized labels of base-authored final-provisions sections.

    The Finnish AKN source keeps a trailing "final-provisions" block
    (commencement ``Voimaantulo``, transitional ``Siirtymäsäännökset``, repeal
    ``Kumottavat säädökset``, and occasionally plain substantive final sections)
    as a bare (num-less) ``hcontainer``/``body`` that is a *sibling* of the
    chapters/parts rather than nested under the last chapter — or, less often,
    as sections sitting directly alongside the chapters.  Both shapes reproduce
    the exact ``direct section:X alongside chapter:Y`` mixed_hierarchy shape at
    the product surface, but they are base-authored editorial artifacts, not a
    replay malformation.  This pure scan of the *base* IR collects their
    normalized labels so the invariant allowance can distinguish them from
    genuine replay INSERT-hoists (whose label is absent from the base block).
    """
    labels: set[str] = set()

    def walk(node: IRNode) -> None:
        children = node.children
        has_container = any(
            _kind_value(child) in {"part", "chapter"} for child in children
        )
        if has_container:
            for child in children:
                if _kind_value(child) == "section" and child.label:
                    labels.add(normalized_label_key(child.label))
                elif (
                    _kind_value(child) in {"hcontainer", "body"}
                    and not child.label
                ):
                    for grandchild in child.children:
                        if _kind_value(grandchild) == "section" and grandchild.label:
                            labels.add(normalized_label_key(grandchild.label))
        for child in children:
            walk(child)

    walk(base_ir)
    return frozenset(labels)


def is_base_authored_final_provisions_section_violation(
    violation: TreeInvariantViolation,
    base_final_provisions_labels: frozenset[str],
) -> bool:
    """Allow a mixed_hierarchy section that is base-authored in a bare block.

    The section landed as a bare sibling of the chapters at the product surface,
    but the *same* section label already sits in a base-authored bare
    final-provisions block (see ``base_final_provisions_section_labels``).  That
    makes the mixed_hierarchy shape a faithful reproduction of a source editorial
    artifact rather than a replay INSERT-hoist, so it is a benign self-consistency
    signal.  Genuine replay-hoisted sections (label not in the base block) are
    left flagged.
    """
    if (
        not base_final_provisions_labels
        or violation.kind != "mixed_hierarchy_child"
        or violation.child_kind != IRNodeKind.SECTION.value
        or violation.label is None
    ):
        return False
    return normalized_label_key(violation.label) in base_final_provisions_labels


def is_terminal_fi_commencement_section_violation(
    tree: IRNode,
    violation: TreeInvariantViolation,
) -> bool:
    """Allow source-authored final FI commencement sections outside chapters.

    Some Finnish base statutes are chaptered but keep the entry-into-force
    section as a root-level final-provisions section. The safe allowance is:
    commencement text, at least one container sibling, and no following direct
    section. Ordinary mixed direct sections remain flagged.
    """
    if (
        violation.kind != "mixed_hierarchy_child"
        or violation.child_kind != IRNodeKind.SECTION.value
        or violation.label is None
    ):
        return False
    parent = _resolve_invariant_path(tree, violation.path)
    if parent is None:
        return False
    labeled_children = [
        child
        for child in parent.children
        if child.label is not None and _kind_value(child) in {"part", "chapter", "section"}
    ]
    for index, child in enumerate(labeled_children):
        if child.kind is not IRNodeKind.SECTION or child.label != violation.label:
            continue
        has_container_sibling = any(
            _kind_value(sibling) in {"part", "chapter"}
            for sibling in (*labeled_children[:index], *labeled_children[index + 1 :])
        )
        has_following_direct_section = any(
            right.kind is IRNodeKind.SECTION for right in labeled_children[index + 1 :]
        )
        return (
            has_container_sibling
            and not has_following_direct_section
            and _has_fi_commencement_heading(child)
        )
    return False
