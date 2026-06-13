"""Scope recovery helpers for Finland group lowering."""

from __future__ import annotations

from collections.abc import Iterable

import lxml.etree as etree

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.finland.constraints import _find_muutos_node
from lawvm.finland.helpers import _normalize_source_part_num, _normalize_source_section_num, _norm_num_token
from lawvm.finland.ops import AmendmentOp, projection_scope_confidence


def group_has_scope_source(group_ops: Iterable[AmendmentOp], source: str) -> bool:
    source_norm = str(source or "").strip()
    if not source_norm:
        return False
    return any(
        (
            (
                witness := projection_scope_confidence(
                    scope_confidence=op.scope_confidence,
                    scope_provenance_tags=op.scope_provenance_tags,
                    resolved_chapter=op.target_chapter,
                )
            )
            is not None
            and witness.source == source_norm
        )
        for op in group_ops
    )


def allow_unscoped_live_section_retarget(
    group_ops: Iterable[AmendmentOp],
) -> str | None:
    if group_has_scope_source(group_ops, "carry_forward"):
        return "carry_forward"
    if group_has_scope_source(group_ops, "explicit_scope_rewrite"):
        return "explicit_scope_rewrite"
    if group_has_scope_source(group_ops, "explicit_chunk"):
        return "explicit_chunk"
    return None


def source_body_chapter_for_scoped_section_target(
    *,
    muutos_tree: etree._Element,
    target_norm: str,
    target_chapter: str,
    target_part: str | None,
) -> str | None:
    """Return the source body chapter that actually contains the target section.

    `_find_muutos_ir(...)` may legally fall back to a same-numbered section in a
    different chapter when the requested chapter is absent from the amendment
    body.  Compile-time scope preservation must distinguish that fallback from a
    true payload that already lives under the scoped target chapter.
    """
    node = _find_muutos_node(
        muutos_tree,
        "section",
        target_norm,
        target_chapter,
        target_part,
    )
    if node is None:
        return None
    parent = node.getparent() if hasattr(node, "getparent") else None
    while parent is not None:
        tag = str(parent.tag).rsplit("}", 1)[-1] if isinstance(parent.tag, str) else ""
        if tag == "chapter":
            num_el = parent.find("{*}num")
            if num_el is None or not num_el.text:
                return None
            return _norm_num_token(num_el.text).removesuffix("luku") or None
        parent = parent.getparent()
    return None


def source_body_scope_for_section_target(
    *,
    muutos_tree: etree._Element,
    target_norm: str,
) -> tuple[str | None, str | None] | None:
    """Return the unique body-backed (part, chapter) scope for one section label."""
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return None

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "part":
                part_num = parent.find("{*}num")
                if part_num is None or not part_num.text:
                    return None
                return _normalize_source_part_num(part_num.text) or None
            parent = parent.getparent()
        return None

    def _chapter_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "chapter":
                chapter_num = parent.find("{*}num")
                if chapter_num is None or not chapter_num.text:
                    return None
                return _norm_num_token(chapter_num.text).removesuffix("luku") or None
            parent = parent.getparent()
        return None

    scopes: set[tuple[str | None, str | None]] = set()
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _normalize_source_section_num(num_el.text)
        if sec_label != target_norm:
            continue
        scopes.add((_part_label_for_element(sec), _chapter_label_for_element(sec)))

    if len(scopes) != 1:
        return None
    return next(iter(scopes))


def resolve_group_surface_scope(
    *,
    muutos_tree: etree._Element,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    group_ops: Iterable[AmendmentOp],
) -> tuple[str | None, str | None]:
    """Return the Stage-1 payload-extraction scope for one target group.

    This is intentionally source-facing. It may differ from the live/effective
    target scope when the amendment body still carries the section payload under
    an earlier chapter wrapper even though the lowering path has already been
    retargeted to the current live chapter.
    """
    surface_target_chapter = target_chapter
    surface_target_part = target_part
    carry_forward_scoped = group_has_scope_source(group_ops, "carry_forward")

    if target_unit_kind != "section":
        return surface_target_chapter, surface_target_part

    body_scope = source_body_scope_for_section_target(
        muutos_tree=muutos_tree,
        target_norm=target_norm,
    )
    if carry_forward_scoped and body_scope == (None, None):
        return None, None
    if target_chapter and body_scope is not None:
        body_part, body_chapter = body_scope
        scoped_node = _find_muutos_node(
            muutos_tree,
            "section",
            target_norm,
            target_chapter,
            target_part,
        )
        body_node = _find_muutos_node(
            muutos_tree,
            "section",
            target_norm,
            body_chapter,
            body_part,
        )
        if (
            scoped_node is None
            and body_node is not None
            and (body_chapter != target_chapter or body_part != target_part)
        ):
            return body_chapter, body_part

    return surface_target_chapter, surface_target_part
