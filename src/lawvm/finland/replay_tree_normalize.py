"""Replay-tree normalization helpers for Finland."""

from __future__ import annotations

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import may_attach_post_list_loppukappale


def hoist_trailing_wrapup_ir(node: IRNode) -> IRNode:
    """Promote trailing prose after numbered items to ``wrapUp`` nodes."""
    node_kind = getattr(node.kind, "value", str(node.kind))
    if not node.children:
        return node

    new_children = [hoist_trailing_wrapup_ir(child) for child in node.children]

    def _child_kind(child: IRNode) -> str:
        return getattr(child.kind, "value", str(child.kind))

    def _paragraph_has_num_ir(child: IRNode) -> bool:
        return any(_child_kind(grandchild) == "num" for grandchild in child.children)

    def _paragraph_is_content_only_ir(child: IRNode) -> bool:
        return _child_kind(child) == "paragraph" and not _paragraph_has_num_ir(child) and all(
            _child_kind(grandchild) == "content" for grandchild in child.children
        )

    if node_kind == "subsection":
        numbered_positions = [
            idx
            for idx, child in enumerate(new_children)
            if _child_kind(child) == "paragraph" and _paragraph_has_num_ir(child)
        ]
        if numbered_positions:
            last_numbered_idx = numbered_positions[-1]
            trailing = new_children[last_numbered_idx + 1 :]
            candidate = IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=node.label,
                text=node.text,
                attrs=node.attrs,
                children=tuple(new_children),
            )
            if (
                trailing
                and may_attach_post_list_loppukappale(candidate)
                and all(
                    _paragraph_is_content_only_ir(child) or _child_kind(child) == "content"
                    for child in trailing
                )
            ):
                rewritten: list[IRNode] = list(new_children[: last_numbered_idx + 1])
                for child in trailing:
                    wrap_text = irnode_to_text(child).strip()
                    if not wrap_text:
                        continue
                    rewritten.append(IRNode(kind=IRNodeKind.WRAP_UP, text=wrap_text, attrs=dict(child.attrs)))
                return _tops._with_children(node, rewritten)

    if tuple(new_children) == node.children:
        return node
    return _tops._with_children(node, new_children)
