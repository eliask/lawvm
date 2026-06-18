"""Locate Finland amendment-body payloads and convert them to IR.

This module owns the XML-to-IR boundary for selecting the amendment body
surface used by compile-group elaboration. It is deliberately separate from
``grafter.py`` so compile-group code can depend on payload lookup without
depending on the replay driver.
"""

from __future__ import annotations

import copy
import re
from typing import Optional, Tuple

import lxml.etree as etree

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_ir_ops import _relabel_subsection_ir
from lawvm.finland.constraints import _find_muutos_node
from lawvm.finland.helpers import (
    _fi_label_postprocessor,
    _is_omission_ir,
    _norm_num_token,
    _roman_label_to_arabic,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node

_PAYLOAD_NORMALIZATION_RULE_ATTR = "lawvm_payload_normalization_rule"
_UNLABELED_ADJACENT_SECTION_CONTINUATION_RULE_ID = (
    "ELAB.UNLABELED_ADJACENT_SECTION_CONTINUATION"
)


def _tag(el: etree._Element) -> str:
    return str(el.tag).split("}")[-1]


def _subsection_intro_numeric_label_ir(sub_ir: IRNode) -> Optional[str]:
    for child in sub_ir.children:
        if child.kind not in {IRNodeKind.INTRO, IRNodeKind.CONTENT}:
            continue
        text = (child.text or "").strip()
        m = re.match(r"^(\d+)\.\s", text)
        if m is not None and int(m.group(1)) > 1:
            return m.group(1)
    return None


def _relabel_sparse_omission_subsections_from_intro_ir(node: IRNode) -> IRNode:
    if not node.children:
        return node

    changed = False
    new_children: list[IRNode] = []
    for child in node.children:
        if child.children:
            relabelled = _relabel_sparse_omission_subsections_from_intro_ir(child)
            if relabelled is not child:
                changed = True
            new_children.append(relabelled)
        else:
            new_children.append(child)

    if node.kind is IRNodeKind.SECTION:
        seen_prior_omission = False
        seen_labels = {
            child.label
            for child in new_children
            if child.kind is IRNodeKind.SUBSECTION and child.label
        }
        adjusted_children: list[IRNode] = []
        for child in new_children:
            if _is_omission_ir(child):
                seen_prior_omission = True
                adjusted_children.append(child)
                continue
            if seen_prior_omission and child.kind is IRNodeKind.SUBSECTION and (child.label or "").isdigit():
                intro_label = _subsection_intro_numeric_label_ir(child)
                if intro_label is not None and intro_label != child.label and intro_label not in seen_labels:
                    seen_labels.discard(child.label)
                    child = _relabel_subsection_ir(child, intro_label)
                    seen_labels.add(intro_label)
                    changed = True
            adjusted_children.append(child)
        new_children = adjusted_children

    if not changed:
        return node
    return _tops._with_children(node, new_children)


def _subsection_with_flat_text_ir(sub_ir: IRNode, flat_text: str) -> IRNode:
    content_child = IRNode(kind=IRNodeKind.CONTENT, text=flat_text.strip())
    return IRNode(
        kind=sub_ir.kind,
        label=sub_ir.label,
        text=sub_ir.text,
        attrs=dict(sub_ir.attrs),
        children=(content_child,),
    )


def _embedded_letter_suffix_section_ir(
    sec_el: etree._Element,
    *,
    target_unit_kind: str,
    target_norm: str,
) -> Optional[IRNode]:
    if target_unit_kind != "section":
        return None
    num_el = sec_el.find("{*}num")
    base_norm = _norm_num_token(num_el.text if num_el is not None and num_el.text else "")
    if not base_norm or not base_norm.isdigit():
        return None

    subsections = sec_el.findall("./{*}subsection")
    if len(subsections) < 2:
        return None

    first_text = " ".join("".join(str(_t) for _t in subsections[0].itertext()).split())
    m = re.search(rf"\b{re.escape(base_norm)}\s*([a-z])\s*§\s*$", first_text, flags=re.I)
    if not m:
        return None

    suffix = m.group(1).lower()
    embedded_label = f"{base_norm}{suffix}"
    if target_norm not in {base_norm, embedded_label}:
        return None

    if target_norm == base_norm:
        # lxml elements are mutable and may be re-parented by the parser, so
        # clone the source subsection before handing it to the IR converter.
        first_sub_ir = fi_xml_to_ir_node(copy.deepcopy(subsections[0]), _fi_label_postprocessor)
        trimmed_text = re.sub(
            rf"\s*{re.escape(base_norm)}\s*{re.escape(suffix)}\s*§\s*$",
            "",
            " ".join(irnode_to_text(first_sub_ir).split()),
            flags=re.I,
        ).strip()
        clean_first = _subsection_with_flat_text_ir(first_sub_ir, trimmed_text)
        num_text = (num_el.text or "").strip() if num_el is not None and num_el.text else f"{base_norm} §"
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=base_norm,
            children=(IRNode(kind=IRNodeKind.NUM, text=num_text), clean_first),
        )

    embedded_subs = [
        # Same XML detachment boundary as above: each subsection is cloned before
        # conversion so the original subtree stays untouched.
        fi_xml_to_ir_node(copy.deepcopy(sub), _fi_label_postprocessor)
        for sub in subsections[1:]
    ]
    if not embedded_subs:
        return None
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=embedded_label,
        children=(IRNode(kind=IRNodeKind.NUM, text=f"{base_norm} {suffix} §"), *embedded_subs),
    )


def _has_direct_num_or_heading(el: etree._Element) -> bool:
    for child in el:
        if _tag(child) in {"num", "heading"}:
            return True
    return False


def _with_payload_normalization_rule(node: IRNode, rule_id: str) -> IRNode:
    existing = node.attrs.get(_PAYLOAD_NORMALIZATION_RULE_ATTR, ())
    rules = (
        tuple(existing)
        if isinstance(existing, tuple)
        else ((str(existing),) if existing else ())
    )
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs={
            **dict(node.attrs),
            _PAYLOAD_NORMALIZATION_RULE_ATTR: tuple(dict.fromkeys((*rules, rule_id))),
        },
        children=node.children,
    )


def _next_numeric_subsection_label(children: list[IRNode]) -> Optional[str]:
    numeric_labels = [
        int(child.label)
        for child in children
        if child.kind is IRNodeKind.SUBSECTION and child.label and child.label.isdigit()
    ]
    if not numeric_labels:
        return None
    return str(max(numeric_labels) + 1)


def _renumber_ir_node(node: IRNode, label: str) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=node.children,
    )


def _split_unlabeled_continuation_subsection(
    continuation: IRNode,
    *,
    first_label: str,
) -> tuple[IRNode, ...]:
    if not continuation.children:
        return (_renumber_ir_node(continuation, first_label),)

    split_children: list[IRNode] = []
    suffix_children: list[IRNode] = []
    reached_suffix = False
    for child in continuation.children:
        if child.kind is IRNodeKind.WRAP_UP:
            reached_suffix = True
        if reached_suffix:
            suffix_children.append(child)
        else:
            split_children.append(child)

    if not suffix_children:
        return (_renumber_ir_node(continuation, first_label),)

    body_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=first_label,
        text=continuation.text,
        attrs=dict(continuation.attrs),
        children=tuple(split_children),
    )
    wrap_up_subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=str(int(first_label) + 1),
        text=None,
        attrs=dict(continuation.attrs),
        children=tuple(suffix_children),
    )
    return (body_subsection, wrap_up_subsection)


def _merge_unlabeled_adjacent_section_continuation_ir(
    muutos_sec: etree._Element,
    muutos_ir: IRNode,
    *,
    target_unit_kind: str,
) -> IRNode:
    """Attach a following unlabeled source section as continuation payload.

    Historical Finlex amendment XML sometimes splits one legal section across
    adjacent ``<section>`` siblings: the first has ``<num>N §</num>`` and a
    substantive body, while the next has no ``num`` or heading and contains the
    continuation prose/list for that same section.  The unlabeled sibling has no
    independent legal identity, so keeping it outside the selected payload drops
    source-owned text.
    """
    if (
        target_unit_kind != "section"
        or muutos_ir.kind is not IRNodeKind.SECTION
        or not muutos_ir.label
    ):
        return muutos_ir

    next_el = muutos_sec.getnext()
    if next_el is None or _tag(next_el) != "section" or _has_direct_num_or_heading(next_el):
        return muutos_ir

    next_ir = fi_xml_to_ir_node(copy.deepcopy(next_el), _fi_label_postprocessor)
    continuation_subsections = [
        child for child in next_ir.children if child.kind is IRNodeKind.SUBSECTION
    ]
    if (
        next_ir.label is not None
        or len(continuation_subsections) != 1
        or any(child.kind in {IRNodeKind.NUM, IRNodeKind.HEADING} for child in next_ir.children)
    ):
        return muutos_ir

    children = list(muutos_ir.children)
    next_label = _next_numeric_subsection_label(children)
    if next_label is None:
        return muutos_ir

    continuation = continuation_subsections[0]
    continuation_children = _split_unlabeled_continuation_subsection(
        continuation,
        first_label=next_label,
    )
    merged = IRNode(
        kind=muutos_ir.kind,
        label=muutos_ir.label,
        text=muutos_ir.text,
        attrs=dict(muutos_ir.attrs),
        children=(*children, *continuation_children),
    )
    return _with_payload_normalization_rule(
        merged,
        _UNLABELED_ADJACENT_SECTION_CONTINUATION_RULE_ID,
    )


def _find_muutos_ir(
    muutos_tree: etree._Element,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: Optional[str] = None,
    target_part: Optional[str] = None,
) -> Tuple[Optional[IRNode], Optional[IRNode]]:
    """Find amendment section and preceding cross-heading as IRNodes.

    Returns (muutos_ir, cross_ir). Encapsulates all lxml-to-IRNode conversion
    for amendment section lookup.
    """
    muutos_sec = _find_muutos_node(
        muutos_tree,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
    )
    if muutos_sec is None:
        return None, None

    muutos_ir = _embedded_letter_suffix_section_ir(
        muutos_sec,
        target_unit_kind=str(target_unit_kind),
        target_norm=target_norm,
    )
    if muutos_ir is None:
        muutos_ir = fi_xml_to_ir_node(muutos_sec, _fi_label_postprocessor)
        muutos_ir = _merge_unlabeled_adjacent_section_continuation_ir(
            muutos_sec,
            muutos_ir,
            target_unit_kind=str(target_unit_kind),
        )
        muutos_ir = _relabel_sparse_omission_subsections_from_intro_ir(muutos_ir)
        # If this chapter is wrapped in a <part> element in the amendment body,
        # record the part label as a routing hint for multi-part statutes.
        if muutos_ir is not None and muutos_ir.kind is IRNodeKind.CHAPTER:
            _sec_parent = muutos_sec.getparent() if hasattr(muutos_sec, "getparent") else None
            if _sec_parent is not None and _tag(_sec_parent) == "part":
                _part_num_el = _sec_parent.find("{*}num")
                if _part_num_el is not None and _part_num_el.text:
                    _pnorm = _norm_num_token(_part_num_el.text.strip())
                    _pnorm = _pnorm.removesuffix("osasto").removesuffix("osa")
                    _phint_arabic = _roman_label_to_arabic(_pnorm)
                    _phint = str(_phint_arabic) if _phint_arabic is not None else (_pnorm or None)
                    if _phint:
                        _sibling_labels: list[str] = []
                        for _sib_ch in _sec_parent.findall("{*}chapter"):
                            _sib_num_el = _sib_ch.find("{*}num")
                            if _sib_num_el is not None and _sib_num_el.text:
                                _sib_norm = _norm_num_token(_sib_num_el.text.strip()).removesuffix("luku")
                                if _sib_norm and _sib_norm != muutos_ir.label:
                                    _sibling_labels.append(_sib_norm)
                        _extra_attrs: dict[str, object] = {"lawvm_amendment_part_hint": _phint}
                        if _sibling_labels:
                            _extra_attrs["lawvm_amendment_part_sibling_chapters"] = tuple(_sibling_labels)
                        muutos_ir = IRNode(
                            kind=muutos_ir.kind,
                            label=muutos_ir.label,
                            text=muutos_ir.text,
                            attrs={**dict(muutos_ir.attrs), **_extra_attrs},
                            children=muutos_ir.children,
                        )
        m_suffix = re.fullmatch(r"(\d+)([a-z])", target_norm, flags=re.I)
        if m_suffix is not None and muutos_ir.kind is IRNodeKind.SECTION and muutos_ir.label == m_suffix.group(1):
            # Older malformed source sometimes encodes a newly inserted letter-suffix
            # section as a bare base section node even though the operative target
            # is explicitly suffixed. Preserve the requested label.
            suffix = m_suffix.group(2).lower()
            num_text = next(
                (c.text for c in muutos_ir.children if c.kind is IRNodeKind.NUM and c.text),
                f"{m_suffix.group(1)} {suffix} §",
            )
            num_text = re.sub(
                rf"^{re.escape(m_suffix.group(1))}\s*§",
                f"{m_suffix.group(1)} {suffix} §",
                num_text,
                flags=re.I,
            )
            muutos_ir = IRNode(
                kind=IRNodeKind.SECTION,
                label=target_norm.lower(),
                text=muutos_ir.text,
                attrs=dict(muutos_ir.attrs),
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=num_text),
                    *tuple(c for c in muutos_ir.children if c.kind is not IRNodeKind.NUM),
                ),
            )
    prev = muutos_sec.getprevious()
    cross_ir = (
        # crossHeading is also an lxml subtree boundary; clone before IR
        # conversion so the source document remains structurally intact.
        fi_xml_to_ir_node(copy.deepcopy(prev), _fi_label_postprocessor)
        if prev is not None and _tag(prev) == "crossHeading"
        else None
    )
    return muutos_ir, cross_ir
