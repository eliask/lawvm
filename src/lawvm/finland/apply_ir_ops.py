"""Pure IR rewrite helpers for Finland apply/grafter flows.

This module holds the structural tree surgery that used to live near the top of
``apply.py``: repeal placeholders, relabel helpers, and insert-with-renumber
operations. Keeping these helpers separate lets the execution dispatcher shrink
without changing the compatibility surface used by grafter-adjacent modules.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import normalized_label_key

from lawvm.finland.helpers import _is_omission_ir
from lawvm.finland.source_pathology import build_destructive_shape_loss_risk_pathology


def _kumottu_attribution(source_id: str, issue_date: Optional[dt.date] = None, source_title: str = "") -> str:
    """Format Finlex-style repeal attribution: ' L:lla DD.MM.YYYY/NUM'.

    .. deprecated::
        Kumottu editorial text is an oracle presentation concern, not replay IR.
        Retained only for item-level placeholders where structural numbering
        requires visible text.  Section/subsection repeals use empty placeholders
        with ``lawvm_repeal_placeholder`` attribute instead.
    """
    if "/" not in source_id:
        return ""
    parts = source_id.split("/")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return ""
    year, num = parts[0], parts[1]
    title_lower = source_title.lower().strip()
    if title_lower.startswith("valtioneuvoston asetus") or title_lower.startswith("vna"):
        prefix = "A:lla"
    elif title_lower.startswith("asetus") or "ministeriön asetus" in title_lower:
        prefix = "A:lla"
    elif "päätös" in title_lower:
        prefix = "P:llä"
    else:
        prefix = "L:lla"
    if issue_date:
        return f" {prefix} {issue_date.day}.{issue_date.month}.{issue_date.year}/{num}"
    return f" {prefix} {num}/{year}"


def _build_repeal_placeholder_ir(
    sec_node: IRNode, op_label: str, source_id: str = "", issue_date: Optional[dt.date] = None, source_title: str = ""
) -> IRNode:
    """Build an IRNode repeal placeholder section.

    The placeholder carries ``lawvm_repeal_placeholder=1`` as the authoritative
    signal.  No editorial "on kumottu" text is synthesized — that is an oracle
    presentation concern handled by the comparison layer.
    """
    num_child = next((c for c in sec_node.children if c.kind == IRNodeKind.NUM), None)
    children: list[IRNode] = []
    if num_child:
        children.append(num_child)
    attrs = dict(sec_node.attrs)
    attrs["lawvm_repeal_placeholder"] = "1"
    return IRNode(kind=IRNodeKind.SECTION, label=sec_node.label, attrs=attrs, children=tuple(children))


def _build_repeal_placeholder_from_label_ir(
    op_label: str,
    source_id: str = "",
    issue_date: Optional[dt.date] = None,
    source_title: str = "",
) -> IRNode:
    """Build a repeal placeholder section when the original section node is absent."""
    display_label = re.sub(r"^(\d+)([a-z])$", r"\1 \2", op_label, flags=re.I)
    num_text = f"{display_label} §"
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=op_label,
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(
            IRNode(kind=IRNodeKind.NUM, text=num_text),
        ),
    )


def _relabel_chapter_ir(chapter: IRNode, new_label: str) -> IRNode:
    """Clone a chapter node with an updated label and num-child text."""
    new_children: List[IRNode] = []
    for child in chapter.children:
        if child.kind == IRNodeKind.NUM:
            new_children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=f"{new_label} luku",
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
        else:
            new_children.append(child)
    return IRNode(
        kind=chapter.kind,
        label=new_label,
        text=chapter.text,
        attrs=dict(chapter.attrs),
        children=tuple(new_children),
    )


def _relabel_section_ir(section: IRNode, new_label: str) -> IRNode:
    """Clone a section with an updated visible section label."""
    children: List[IRNode] = []
    saw_num = False
    for child in section.children:
        if child.kind == IRNodeKind.NUM:
            children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=f"{new_label} §",
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
            saw_num = True
        else:
            children.append(child)
    if not saw_num:
        children.insert(0, IRNode(kind=IRNodeKind.NUM, text=f"{new_label} §"))
    return IRNode(
        kind=section.kind,
        label=new_label,
        text=section.text,
        attrs=dict(section.attrs),
        children=tuple(children),
    )


def _relabel_paragraph_ir(paragraph: IRNode, new_label: str) -> IRNode:
    """Return paragraph with updated label and visible number marker."""
    display_label = re.sub(r"^(\d+)([a-z])$", r"\1 \2", new_label, flags=re.I)
    new_children: List[IRNode] = []
    num_updated = False
    for child in paragraph.children:
        if child.kind == IRNodeKind.NUM and not num_updated:
            child_text = child.text or ""
            if child_text:
                new_text = re.sub(
                    r"^(\s*)(?:\d+\s*[a-z]?|[a-z])",
                    lambda m: f"{m.group(1)}{display_label}",
                    child_text,
                    count=1,
                    flags=re.I,
                )
            else:
                new_text = f"{display_label})"
            new_children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=new_text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
            num_updated = True
        else:
            new_children.append(child)
    if not num_updated:
        new_children = [IRNode(kind=IRNodeKind.NUM, text=f"{display_label})")] + new_children
    return IRNode(
        kind=paragraph.kind,
        label=new_label,
        text=paragraph.text,
        attrs=dict(paragraph.attrs),
        children=tuple(new_children),
    )


def _shift_lettered_item_labels_after_repeal(sub: IRNode, repealed_label: str) -> IRNode:
    """Shift later single-letter item labels down by one after an explicit repeal."""
    if not re.fullmatch(r"[a-z]", repealed_label, flags=re.I):
        return sub

    repealed_ord = ord(repealed_label.lower())
    new_children: List[IRNode] = []
    for child in sub.children:
        if child.kind == IRNodeKind.PARAGRAPH and child.label:
            label_norm = normalized_label_key(str(child.label))
            if re.fullmatch(r"[a-z]", label_norm, flags=re.I) and ord(label_norm) > repealed_ord:
                child = _relabel_paragraph_ir(child, chr(ord(label_norm) - 1))
        new_children.append(child)

    return IRNode(
        kind=sub.kind,
        label=sub.label,
        text=sub.text,
        attrs=dict(sub.attrs),
        children=tuple(new_children),
    )


def _relabel_subsection_ir(subsection: IRNode, new_label: str) -> IRNode:
    return IRNode(
        kind=subsection.kind,
        label=new_label,
        text=subsection.text,
        attrs=dict(subsection.attrs),
        children=tuple(subsection.children),
    )


def _rebuild_section_with_subsections_ir(sec: IRNode, new_subsections: List[IRNode]) -> IRNode:
    """Replace a section's subsection sequence while preserving non-subsection children."""
    rebuilt_children: List[IRNode] = []
    sub_idx = 0
    for child in sec.children:
        if child.kind == IRNodeKind.SUBSECTION:
            if sub_idx < len(new_subsections):
                rebuilt_children.append(new_subsections[sub_idx])
                sub_idx += 1
            continue
        rebuilt_children.append(child)
    if sub_idx < len(new_subsections):
        rebuilt_children.extend(new_subsections[sub_idx:])
    return _tops._with_children(sec, rebuilt_children)


def _rewrite_bracketed_single_subsection_replace_ir(
    sec: IRNode,
    replacement_sub: IRNode,
    target_paragraph: int,
    muutos_ir: Optional[IRNode],
    source_statute_id: str,
) -> Optional[IRNode]:
    """Handle omission-bracketed single-subsection replacements that collapse one stale predecessor."""
    if muutos_ir is None or target_paragraph <= 1:
        return None

    slot_kinds = [c.kind for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION or _is_omission_ir(c)]
    if slot_kinds != [IRNodeKind.OMISSION, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION]:
        return None

    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    if any(c.kind == IRNodeKind.HEADING for c in sec.children) or any(c.kind == IRNodeKind.HEADING for c in muutos_ir.children):
        return None

    # Label-based resolution: find subsection with matching label, not by index.
    target_label = str(target_paragraph)
    n = next(
        (i for i, s in enumerate(subsecs)
         if s.label and re.sub(r"[)\s.]", "", s.label).strip() == target_label),
        None,
    )
    if n is None:
        return None
    if not (0 <= n < len(subsecs)):
        return None
    if n - 1 < 0 or n + 1 > len(subsecs):
        return None

    # Verify the predecessor (subsecs[n-1]) is a stale version of the
    # replacement content.  When subsection INSERTs are applied in ascending
    # order, the predecessor may hold different (non-stale) content and the
    # normal replace path should handle the op instead.
    predecessor = subsecs[n - 1]
    pred_text = " ".join(irnode_to_text(predecessor).split()).strip()[:30]
    repl_text = " ".join(irnode_to_text(replacement_sub).split()).strip()[:30]
    if len(pred_text) < 10 or len(repl_text) < 10:
        return None
    if pred_text[:10] != repl_text[:10]:
        return None

    preserved_prefix = list(subsecs[: n - 1])
    shifted_live = _relabel_subsection_ir(subsecs[n], str(target_paragraph - 1))
    replacement = (
        replacement_sub
        if replacement_sub.label == str(target_paragraph)
        else _relabel_subsection_ir(replacement_sub, str(target_paragraph))
    )

    rebuilt_subs: List[IRNode] = preserved_prefix + [shifted_live, replacement]
    next_label = target_paragraph + 1
    for sub in subsecs[n + 1 :]:
        relabelled = sub
        norm = re.sub(r"[)\s.]", "", sub.label or "").strip().lower()
        if norm.isdigit():
            relabelled = _relabel_subsection_ir(sub, str(next_label))
        rebuilt_subs.append(relabelled)
        if norm.isdigit():
            next_label += 1

    return _rebuild_section_with_subsections_ir(sec, rebuilt_subs)


def _insert_subsection_with_renumber_ir(
    sec: IRNode,
    amend_sub: IRNode,
    target_paragraph: int,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
) -> IRNode:
    """Insert a numeric subsection and renumber later numeric siblings when needed."""
    insert_label = str(target_paragraph)
    inserted_sub = amend_sub if amend_sub.label == insert_label else _relabel_subsection_ir(amend_sub, insert_label)
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

    # Label-based insertion: find the first subsection whose numeric label
    # is >= target_paragraph so the insert point is correct even when prior
    # operations have shifted subsection positions.
    label_idx = next(
        (i for i, s in enumerate(subsecs)
         if s.label
         and re.sub(r"[)\s.]", "", s.label).strip().isdigit()
         and int(re.sub(r"[)\s.]", "", s.label).strip()) >= target_paragraph),
        None,
    )
    insert_idx = label_idx if label_idx is not None else min(max(target_paragraph - 1, 0), len(subsecs))
    rebuilt_subsecs = list(subsecs[:insert_idx]) + [inserted_sub] + list(subsecs[insert_idx:])

    if not insert_label.isdigit():
        return _rebuild_section_with_subsections_ir(sec, rebuilt_subsecs)

    next_num = int(insert_label) + 1
    renumbered_count = 0
    for idx in range(insert_idx + 1, len(rebuilt_subsecs)):
        child = rebuilt_subsecs[idx]
        if child.label:
            norm = re.sub(r"[)\s.]", "", child.label).strip().lower()
            if norm.isdigit():
                current_num = int(norm)
                if current_num < next_num:
                    child = _relabel_subsection_ir(child, str(next_num))
                    current_num = next_num
                    renumbered_count += 1
                rebuilt_subsecs[idx] = child
                next_num = current_num + 1
    if renumbered_count and source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute="",
                target_unit_kind="section",
                target_label=f"{target_paragraph} mom",
                recovery_kind="subsection_insert_renumber",
                live_sibling_count=len(subsecs),
                payload_sibling_count=renumbered_count,
            )
        )
    return _rebuild_section_with_subsections_ir(sec, rebuilt_subsecs)


def _insert_item_with_suffix_renumber_ir(
    sub: IRNode,
    amend_para: IRNode,
    item_norm: str,
    anchor_idx: Optional[int],
    source_statute: str = "",
    source_pathologies_out: Optional[List[SourcePathology]] = None,
) -> IRNode:
    """Insert a numeric item and renumber later numeric siblings when needed.

    When a subsection has a trailing ``wrapUp`` child (conclusion text after the
    last numbered item), it is detached before insertion and re-attached at the
    end so that the conclusion floats to after any newly inserted items.
    """
    insert_label = item_norm
    inserted_para = _relabel_paragraph_ir(amend_para, insert_label)
    paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
    if anchor_idx is not None:
        insert_at = anchor_idx + 1
    elif insert_label.isdigit():
        # Numeric sort fallback: find the highest-labeled paragraph whose
        # numeric label < target, then insert immediately after it.  This
        # handles "tilalle" insertions where the predecessor item was repealed
        # and is no longer present (e.g. anchor 15 was repealed, inserting 16).
        target_num = int(insert_label)
        best_idx: Optional[int] = None
        for i, p in enumerate(paras):
            if p.label:
                p_norm = re.sub(r"[)\s.]", "", str(p.label)).strip()
                if p_norm.isdigit() and int(p_norm) < target_num:
                    best_idx = i
        insert_at = (best_idx + 1) if best_idx is not None else 0
    else:
        insert_at = len(paras)

    # Detach trailing wrapUp before insertion so it stays after all items
    trailing_wrapup: Optional[IRNode] = None
    base_children = list(sub.children)
    if base_children and base_children[-1].kind == IRNodeKind.WRAP_UP:
        trailing_wrapup = base_children.pop()

    new_children: List[IRNode] = []
    para_pos = 0
    inserted = False
    for child in base_children:
        if child.kind == IRNodeKind.PARAGRAPH and para_pos == insert_at and not inserted:
            new_children.append(inserted_para)
            inserted = True
        new_children.append(child)
        if child.kind == IRNodeKind.PARAGRAPH:
            para_pos += 1
    if not inserted:
        new_children.append(inserted_para)

    if not insert_label.isdigit():
        if trailing_wrapup is not None:
            new_children.append(trailing_wrapup)
        return _tops._with_children(sub, new_children)

    next_num = int(insert_label) + 1
    renumbered_count = 0
    seen_inserted = False
    renumbered_children: List[IRNode] = []
    for child in new_children:
        if child is inserted_para and not seen_inserted:
            renumbered_children.append(child)
            seen_inserted = True
            continue
        if seen_inserted and child.kind == IRNodeKind.PARAGRAPH and child.label:
            norm = re.sub(r"[)\s.]", "", child.label).strip().lower()
            if norm.isdigit():
                current_num = int(norm)
                if current_num < next_num:
                    child = _relabel_paragraph_ir(child, str(next_num))
                    current_num = next_num
                    renumbered_count += 1
                next_num = current_num + 1
        renumbered_children.append(child)

    # Re-attach trailing wrapUp after all paragraphs (including newly inserted)
    if trailing_wrapup is not None:
        renumbered_children.append(trailing_wrapup)
    if renumbered_count and source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=source_statute,
                target_unit_kind="section",
                target_label=item_norm,
                recovery_kind="item_insert_suffix_renumber",
                live_sibling_count=len(paras),
                payload_sibling_count=renumbered_count,
            )
        )
    return _tops._with_children(sub, renumbered_children)


def _strip_standalone_subsection_item_prefixes_ir(node: IRNode) -> IRNode:
    """Strip carried item markers from standalone subsection prose nodes."""
    new_children = tuple(_strip_standalone_subsection_item_prefixes_ir(child) for child in node.children)
    if new_children != node.children:
        node = IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=dict(node.attrs),
            children=new_children,
        )
    if node.kind is not IRNodeKind.SUBSECTION:
        return node
    if any(child.kind is IRNodeKind.PARAGRAPH for child in node.children):
        return node
    if len(node.children) != 1:
        return node
    child = node.children[0]
    if child.kind not in {IRNodeKind.CONTENT, IRNodeKind.INTRO} or not child.text:
        return node
    match = re.match(r"^\s*\d+[a-z]?\s*[\).]\s+(.*)$", child.text, flags=re.I | re.DOTALL)
    if match is None:
        return node
    stripped = match.group(1).lstrip()
    if not stripped or not stripped[:1].isalpha() or not stripped[:1].isupper():
        return node
    return _tops._with_children(
        node,
        [
            IRNode(
                kind=child.kind,
                label=child.label,
                text=stripped,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        ],
    )
