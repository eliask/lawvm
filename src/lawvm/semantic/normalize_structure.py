"""Normalize semantic structure dicts for viewer consumption.

This module bridges the gap between the Python semantic pipeline's
``SemanticStructureNode.to_dict()`` output and the exact JSON shape the
viewer expects.  Previously the viewer performed ~130 LOC of client-side
normalization (kind canonicalization, label cleanup, facet separation,
ordinal assignment, text extraction).  That logic now runs server-side
in this module so the viewer receives pre-normalized, ready-to-render JSON.

The Python semantic pipeline (``projection.py``) already performs all of
these normalizations, so for current pipeline output the function is
essentially a validation pass that stamps a ``_normalized`` marker.
For legacy or non-pipeline data the function applies the full
normalization chain.
"""
from __future__ import annotations

import re
from typing import Any

from lawvm.semantic.model import (
    SEMANTIC_STRUCTURE_KINDS,
    canonical_structure_kind,
    normalize_semantic_label,
)


_FACET_CHILD_KINDS = frozenset({"heading", "intro"})

_ORDINAL_KINDS = frozenset({"subsection", "item", "subitem"})

_TEXT_BEARING_CHILD_KINDS = frozenset({"content", "p", "block"})

_WS_RE = re.compile(r"\s+")


def _norm_ws(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _raw_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children")
    if isinstance(children, list):
        return children
    return []


def _extract_num_label(node: dict[str, Any]) -> str:
    """Extract label from a ``num`` child node, matching JS ``extractStructureNum``."""
    if not node:
        return ""
    if str(node.get("kind") or "").strip() == "num":
        return _norm_ws(node.get("text") or "")
    for child in _raw_children(node):
        if str(child.get("kind") or "").strip() == "num":
            return _norm_ws(child.get("text") or "")
    return ""


def _extract_text_from_content_children(
    node: dict[str, Any], kind: str
) -> str:
    """Extract text from content/p/block children, matching JS ``normalizeStructureText``."""
    own_text = _norm_ws(node.get("text") or "")
    if kind in ("heading", "intro"):
        return own_text

    parts: list[str] = []
    for child in _raw_children(node):
        child_kind = str(child.get("kind") or "").strip()
        if child_kind == "content":
            p_children = [
                gc
                for gc in _raw_children(child)
                if str(gc.get("kind") or "").strip() == "p"
            ]
            if p_children:
                for p_child in p_children:
                    text = _norm_ws(p_child.get("text") or "")
                    if text:
                        parts.append(text)
            else:
                text = _norm_ws(child.get("text") or "")
                if text:
                    parts.append(text)
        elif child_kind in ("p", "block"):
            text = _norm_ws(child.get("text") or "")
            if text:
                parts.append(text)

    if parts:
        return _norm_ws(" ".join(parts))
    return own_text


def _assign_ordinals(children: list[dict[str, Any]]) -> None:
    """Auto-label unlabeled ordinal children, matching JS ``assignStructureOrdinals``."""
    next_ordinals: dict[str, int] = {}
    # First pass: find max existing ordinals
    for child in children:
        child_kind = child.get("kind", "")
        if child_kind not in _ORDINAL_KINDS:
            continue
        label = str(child.get("label") or "")
        m = re.match(r"^(\d+)", label)
        if m:
            next_ordinals[child_kind] = max(
                next_ordinals.get(child_kind, 0), int(m.group(1))
            )
    # Second pass: assign ordinals to unlabeled children
    for child in children:
        child_kind = child.get("kind", "")
        if child_kind not in _ORDINAL_KINDS or child.get("label"):
            continue
        next_ordinals[child_kind] = next_ordinals.get(child_kind, 0) + 1
        child["label"] = str(next_ordinals[child_kind])


def normalize_structure_for_viewer(node: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a JSON structure node for viewer consumption.

    Applies: kind canonicalization, label normalization, ordinal assignment,
    text extraction, facet separation.

    If the node was already normalized (has ``_normalized`` marker), returns
    it unchanged.

    The output shape matches what the viewer rendering expects::

        {
            "kind": str,
            "label"?: str,
            "text"?: str,
            "facets"?: {"heading"?: {"text": str}, "intro"?: {"text": str}},
            "children"?: [<normalized child>, ...],
            "_normalized": True,
        }
    """
    if node is None or not isinstance(node, dict):
        return None

    # Already normalized — pass through.
    if node.get("_normalized"):
        return node

    raw_kind = str(node.get("kind") or "").strip()
    kind = canonical_structure_kind(raw_kind)
    # Accept already-canonical kinds (e.g. "subitem" from to_dict() output).
    if not kind and raw_kind in SEMANTIC_STRUCTURE_KINDS:
        kind = raw_kind

    # Recursively normalize children.
    raw_children: list[dict[str, Any]] = []
    for child in _raw_children(node):
        normalized_child = normalize_structure_for_viewer(child)
        if normalized_child is not None:
            raw_children.append(normalized_child)

    # Separate structural children from facet children (heading, intro).
    structural_children: list[dict[str, Any]] = []
    facets: dict[str, dict[str, str]] = {}
    for child in raw_children:
        child_kind = child.get("kind", "")
        if child_kind in _FACET_CHILD_KINDS:
            text = child.get("text") or ""
            if text:
                facets[child_kind] = {"text": text}
            continue
        structural_children.append(child)

    # Merge facets from the node's own ``facets`` dict (from ``to_dict()``).
    raw_facets = node.get("facets")
    wording_text = ""
    if isinstance(raw_facets, dict):
        for facet_kind in ("heading", "intro"):
            raw_facet = raw_facets.get(facet_kind)
            if not isinstance(raw_facet, dict):
                continue
            text = _norm_ws(raw_facet.get("text") or "")
            if text:
                facets[facet_kind] = {"text": text}
        raw_wording = raw_facets.get("wording")
        if isinstance(raw_wording, dict):
            wording_text = _norm_ws(raw_wording.get("text") or "")

    # Collect table data from the wording facet so it survives normalization.
    # SemanticStructureFacet.to_dict() serializes tables under the "wording" facet
    # key; extract them here to pass through to the normalized node.
    wording_tables: list[dict[str, Any]] | None = None
    if isinstance(raw_facets, dict):
        raw_wording_for_tables = raw_facets.get("wording")
        if isinstance(raw_wording_for_tables, dict):
            raw_tables = raw_wording_for_tables.get("tables")
            if isinstance(raw_tables, list) and raw_tables:
                wording_tables = raw_tables

    # Assign ordinals to unlabeled ordinal children.
    _assign_ordinals(structural_children)

    # If kind is empty (not a semantic kind), wrap as group.
    if not kind:
        if not structural_children:
            return None
        return {"kind": "group", "children": structural_children, "_normalized": True}

    # Build the normalized node.
    normalized: dict[str, Any] = {"kind": kind}
    label = normalize_semantic_label(
        kind, node.get("label") or _extract_num_label(node)
    )
    text = wording_text or _extract_text_from_content_children(node, kind)
    if label:
        normalized["label"] = label
    if text:
        normalized["text"] = text
    if facets:
        normalized["facets"] = facets
    if wording_tables is not None:
        normalized["tables"] = wording_tables
    if structural_children:
        normalized["children"] = structural_children
    normalized["_normalized"] = True
    return normalized
