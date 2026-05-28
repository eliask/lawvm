"""Amend-payload extraction helpers for Finland apply/grafter flows.

These helpers recover item/introduction payloads from amendment subtrees and
recognize a few Finland-specific intro-list shapes. They are used both by the
runtime executor and grafter-facing compatibility surfaces, so they live
outside ``apply.py`` once the executor starts splitting structurally.
"""

from __future__ import annotations

import re
from typing import List, Optional

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops

from lawvm.finland.helpers import _is_omission_ir, _norm_num_token
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.apply_ir_ops import _relabel_paragraph_ir


def _has_consecutive_numeric_labels(labels: List[str]) -> bool:
    """Return True when labels contain at least two consecutive numeric items."""
    if len(labels) < 2:
        return False
    if not all(label.isdigit() for label in labels):
        return False
    numbers = [int(label) for label in labels]
    return all(cur == prev + 1 for prev, cur in zip(numbers, numbers[1:], strict=False))


def _is_plain_intro_only_subsection(sub: IRNode) -> bool:
    """Return True when a subsection is intro/content prose without item structure."""
    if any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children):
        return False
    text_parts = [
        (child.text or "").strip()
        for child in sub.children
        if child.kind in {IRNodeKind.INTRO, IRNodeKind.CONTENT}
    ]
    text = " ".join(part for part in text_parts if part).strip()
    if not text:
        return False
    return re.match(r"^\d+[.)]\s+", text) is None


def _find_amend_paragraph(
    item_norm: str,
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Find a paragraph with matching label in the amendment subsection or muutos tree."""

    def _leading_item_label(p: IRNode) -> Optional[str]:
        for ch in p.children:
            if ch.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT) and ch.text:
                text = ch.text.lstrip()
                compact = re.sub(r"(\d+)\s+([a-z])", r"\1\2", text)
                m = re.match(r"^(\d+[a-z]?)\s*[\).]", compact, re.I)
                if m:
                    return _tops._norm(m.group(1))
        return None

    def _paragraph_with_explicit_item_label(container: IRNode) -> Optional[IRNode]:
        for p in container.children:
            if p.kind != IRNodeKind.PARAGRAPH:
                continue
            explicit = _leading_item_label(p)
            if explicit == item_norm:
                return IRNode(
                    kind=p.kind,
                    label=item_norm,
                    text=p.text,
                    attrs=dict(p.attrs),
                    children=tuple(p.children),
                )
        return None

    if amend_sub is not None:
        result = _paragraph_with_explicit_item_label(amend_sub)
        if result is not None:
            return result
    if muutos_ir is not None:
        for sub_child in muutos_ir.children:
            if sub_child.kind == IRNodeKind.SUBSECTION:
                result = _paragraph_with_explicit_item_label(sub_child)
                if result is not None:
                    return result

    if amend_sub is not None:
        for p in amend_sub.children:
            if p.kind == IRNodeKind.PARAGRAPH and p.label and _tops._norm(p.label) == item_norm:
                return p
    if muutos_ir is not None:
        for sub_child in muutos_ir.children:
            if sub_child.kind == IRNodeKind.SUBSECTION:
                for p in sub_child.children:
                    if p.kind == IRNodeKind.PARAGRAPH and p.label and _tops._norm(p.label) == item_norm:
                        return p

    def _check_subparagraph(container: IRNode) -> Optional[IRNode]:
        for child in container.children:
            if child.kind in (IRNodeKind.SUBSECTION, IRNodeKind.PARAGRAPH):
                for sp in child.children:
                    if sp.kind == IRNodeKind.SUBPARAGRAPH and sp.label and _tops._norm(sp.label) == item_norm:
                        return IRNode(kind=IRNodeKind.PARAGRAPH, label=item_norm, children=sp.children, text=sp.text)
        return None

    if amend_sub is not None:
        result = _check_subparagraph(amend_sub)
        if result is not None:
            return result
    if muutos_ir is not None:
        result = _check_subparagraph(muutos_ir)
        if result is not None:
            return result

    def _check_flattened(sub: IRNode) -> Optional[IRNode]:
        has_paragraphs = any(c.kind == IRNodeKind.PARAGRAPH for c in sub.children)
        if has_paragraphs:
            return None
        content_node = next((c for c in sub.children if c.kind == IRNodeKind.CONTENT), None)
        if content_node is None:
            return None
        content_text = (content_node.text or "").lstrip()
        content_compact = re.sub(r"(\d+)\s+([a-z])", r"\1\2", content_text)
        if re.match(re.escape(item_norm) + r"\s*\)", content_compact):
            return IRNode(kind=IRNodeKind.PARAGRAPH, label=item_norm, children=content_node.children, text=content_node.text)
        return None

    if amend_sub is not None:
        result = _check_flattened(amend_sub)
        if result is not None:
            return result
    if muutos_ir is not None:
        subs_list = [c for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION]
        for idx, sub_child in enumerate(subs_list):
            result = _check_flattened(sub_child)
            if result is not None:
                # Collect subsequent letter-labeled subsections as subparagraphs
                # of this digit-labeled item.  Finlex sometimes encodes:
                #   subsection "10) hankkeella:" / subsection "a) ..." / subsection "b) ..."
                # Stop at the next digit-labeled subsection or end of list.
                letter_children: list[IRNode] = []
                for later in subs_list[idx + 1:]:
                    later_content = next((c for c in later.children if c.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO)), None)
                    if later_content is None:
                        break
                    later_text = (later_content.text or "").lstrip()
                    if re.match(r"[a-z]\s*\)", later_text):
                        lbl_m = re.match(r"([a-z])\s*\)", later_text)
                        if lbl_m:
                            letter_children.append(IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label=lbl_m.group(1),
                                children=tuple(later.children),
                                text=later_content.text,
                            ))
                    elif re.match(r"\d+", later_text):
                        break  # next digit-labeled item
                    else:
                        break
                if letter_children:
                    result = IRNode(
                        kind=result.kind,
                        label=result.label,
                        text=result.text,
                        attrs=dict(result.attrs),
                        children=tuple(result.children) + tuple(letter_children),
                    )
                return result

    def _check_intro_keyed(sub: IRNode) -> Optional[IRNode]:
        for p in sub.children:
            if p.kind != IRNodeKind.PARAGRAPH:
                continue
            explicit = _leading_item_label(p)
            if explicit == item_norm:
                return IRNode(kind=p.kind, label=item_norm, text=p.text, attrs=dict(p.attrs), children=tuple(p.children))
        return None

    if amend_sub is not None:
        result = _check_intro_keyed(amend_sub)
        if result is not None:
            return result
    if muutos_ir is not None:
        for sub_child in muutos_ir.children:
            if sub_child.kind == IRNodeKind.SUBSECTION:
                result = _check_intro_keyed(sub_child)
                if result is not None:
                    return result

    return None


def _find_amend_intro(
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
) -> Optional[IRNode]:
    """Find an intro/content element in the amendment subsection or muutos tree."""
    if amend_sub is not None:
        intro = next((c for c in amend_sub.children if c.kind == IRNodeKind.INTRO), None)
        if intro is None:
            intro = next((c for c in amend_sub.children if c.kind == IRNodeKind.CONTENT), None)
        if intro is not None:
            return intro
    if muutos_ir is not None:
        for s in muutos_ir.children:
            if s.kind == IRNodeKind.SUBSECTION:
                intro = next((c for c in s.children if c.kind == IRNodeKind.INTRO), None)
                if intro is None:
                    intro = next((c for c in s.children if c.kind == IRNodeKind.CONTENT), None)
                if intro is not None:
                    return intro
    return None


def _sanitize_shared_tail_item_replace_paragraph_ir(
    master_sub: IRNode,
    master_para: IRNode,
    amend_para: IRNode,
    *,
    para_idx: int,
) -> Optional[IRNode]:
    """Strip a malformed embedded shared-tail block from an item replace payload."""
    later_paras = [child for child in master_sub.children if child.kind == IRNodeKind.PARAGRAPH][para_idx + 1 :]
    if not later_paras:
        return None
    if any(child.kind == IRNodeKind.SUBPARAGRAPH and child.label for child in amend_para.children):
        return None
    if not any(_is_omission_ir(child) for child in amend_para.children):
        return None

    embedded_tail = [
        child for child in amend_para.children if child.kind in {IRNodeKind.SUBPARAGRAPH, IRNodeKind.CONTENT, IRNodeKind.INTRO} and not child.label
    ]
    if not any(child.kind == IRNodeKind.SUBPARAGRAPH for child in embedded_tail):
        return None

    kept_children = [child for child in amend_para.children if child.kind in {IRNodeKind.NUM, IRNodeKind.INTRO, IRNodeKind.CONTENT}]
    if not kept_children:
        return None
    if kept_children == list(amend_para.children):
        return None

    return IRNode(
        kind=amend_para.kind,
        label=amend_para.label,
        text=amend_para.text,
        attrs=dict(amend_para.attrs),
        children=tuple(kept_children),
    )


def _flattened_item_paragraph_from_subsection_ir(sub: IRNode) -> Optional[IRNode]:
    """Promote a content-only item subsection like ``1) ...`` into a paragraph node."""
    has_paragraphs = any(c.kind == IRNodeKind.PARAGRAPH for c in sub.children)
    if has_paragraphs:
        return None
    content_node = next((c for c in sub.children if c.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO)), None)
    if content_node is None:
        return None
    content_text = (content_node.text or "").lstrip()
    content_compact = re.sub(r"(\d+)\s+([a-z])", r"\1\2", content_text)
    m = re.match(r"^(\d+[a-z]?)\s*\)", content_compact)
    if not m:
        return None
    item_label = _tops._norm(m.group(1))
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=item_label,
        children=tuple(content_node.children),
        text=content_node.text,
    )


def _has_single_intro_numbered_item_list_ir(sub: IRNode) -> bool:
    """Return True when a subsection is an intro plus a numeric item list."""
    intro = next((c for c in sub.children if c.kind == IRNodeKind.INTRO), None)
    paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
    if intro is None or len(paras) < 3:
        return False
    for p in paras:
        if not (p.label and _tops._norm(p.label).isdigit()):
            return False
        text = irnode_to_text(p).lstrip()
        compact = re.sub(r"(\d+)\s+([a-z])", r"\1\2", text, flags=re.I)
        if not re.match(r"^" + re.escape(_tops._norm(p.label)) + r"\s*[\).]", compact):
            return False
    return True


def _collapse_intro_list_amend_subsection_ir(muutos_ir: Optional[IRNode]) -> Optional[IRNode]:
    """Collapse an intro-only first subsection plus item subsections into one subsection."""
    if muutos_ir is None:
        return None
    subs = [c for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION]
    if len(subs) < 2:
        return None
    first_sub = subs[0]
    if not _is_plain_intro_only_subsection(first_sub):
        return None

    paras: List[IRNode] = []
    for sub in subs[1:]:
        direct_paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        if direct_paras:
            paras.extend(direct_paras)
            continue
        flat_para = _flattened_item_paragraph_from_subsection_ir(sub)
        if flat_para is None:
            return None
        paras.append(flat_para)

    para_labels = [_tops._norm(p.label) for p in paras if p.label]
    if not _has_consecutive_numeric_labels(para_labels):
        return None

    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=first_sub.label,
        text=first_sub.text,
        attrs=dict(first_sub.attrs),
        children=tuple(first_sub.children) + tuple(paras),
    )


def _make_item_repeal_placeholder_ir(paragraph: IRNode, op: AmendmentOp | ResolvedOp) -> IRNode:
    """Return a paragraph-scoped repeal placeholder preserving the visible item label."""
    op_target_item = op.effective_target_item_label if isinstance(op, ResolvedOp) else op.target_item
    label = paragraph.label or (op_target_item or "")
    placeholder = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        attrs={"lawvm_repeal_placeholder": "1"},
    )
    return _relabel_paragraph_ir(placeholder, label)


def _has_intro_list_moment_shape_ir(subsecs: List[IRNode]) -> bool:
    """True when subsection 1 is intro-only and subsection 2 carries item list of moment 1."""
    if len(subsecs) < 2:
        return False
    first_sub = subsecs[0]
    second_sub = subsecs[1]
    if not _is_plain_intro_only_subsection(first_sub):
        return False
    second_labels = [_norm_num_token(c.label) for c in second_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label]
    return _has_consecutive_numeric_labels(second_labels)
