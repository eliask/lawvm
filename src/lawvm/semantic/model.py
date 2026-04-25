from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lawvm.core.table_model import TableBody


_CANONICAL_STRUCTURE_KIND = {
    "section": "section",
    "subsection": "subsection",
    "paragraph": "item",
    "item": "item",
    "subparagraph": "subitem",
    "intro": "intro",
    "heading": "heading",
    "wrapUp": "wrapUp",
}

SEMANTIC_STRUCTURE_KINDS = frozenset(
    {
        "section",
        "subsection",
        "item",
        "subitem",
        "intro",
        "heading",
        "wrapUp",
    }
)

_FACET_KINDS = ("heading", "intro", "wrapUp")
_WORDING_FACET_KIND = "wording"
_SEMANTIC_FACET_KINDS = frozenset(_FACET_KINDS)


@dataclass(frozen=True)
class SemanticStructureFacet:
    kind: str
    text: str = ""
    # Structured table data carried alongside flat text for backward-compatible
    # comparison.  When a wording facet includes table content, ``tables`` holds
    # the typed ``TableBody`` projections so downstream layers can do
    # table-aware diff/rendering without re-parsing the IR.
    tables: tuple[TableBody, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {}
        if self.text:
            item["text"] = self.text
        if self.tables:
            item["tables"] = [
                {
                    "table_id": t.table_id,
                    "caption": t.caption,
                    "columns": list(t.columns),
                    "rows": [
                        {
                            "row_key": {
                                "basis": r.row_key.basis,
                                "value": r.row_key.value,
                                "strength": r.row_key.strength,
                            },
                            "cells": [
                                {
                                    "column_key": c.column_key,
                                    "text": c.text,
                                    **({"rowspan": c.rowspan} if c.rowspan != 1 else {}),
                                    **({"colspan": c.colspan} if c.colspan != 1 else {}),
                                }
                                for c in r.cells
                            ],
                            **({"source_basis": r.source_basis} if r.source_basis else {}),
                        }
                        for r in t.rows
                    ],
                }
                for t in self.tables
            ]
        return item

    def display_badge(self) -> str:
        if self.kind == "heading":
            return "otsikko"
        if self.kind == "intro":
            return "johdanto"
        if self.kind == "wrapUp":
            return "loppukappale"
        if self.kind == _WORDING_FACET_KIND:
            return "sanamuoto"
        return self.kind


@dataclass(frozen=True)
class SemanticStructureNode:
    kind: str
    label: str = ""
    visible_label: str = ""
    label_basis: str = "explicit"
    text: str = ""
    facets: tuple[SemanticStructureFacet, ...] = ()
    children: tuple["SemanticStructureNode", ...] = ()
    # Typed projection defects emitted instead of UserWarning.
    # Each entry is a short observation token like
    # "ORACLE_DUPLICATE_CHILD_LABEL:item:2" or
    # "REPLAY_OUT_OF_ORDER_CHILDREN:subsection:5,4".
    defects: tuple[str, ...] = ()

    @staticmethod
    def _is_opaque_label(text: str) -> bool:
        """Return True if *text* is a synthetic opaque internal discriminator.

        Opaque markers (``__ord_N``, ``__continuation__``, ``__tail_prose__``,
        etc.) are internal-only identifiers.  They must never be serialized into
        persisted artifacts, user-visible output, or evidence payloads — see
        corrigendum §1.2.
        """
        return text.startswith("__")

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {"kind": self.kind}
        if self.label and not self._is_opaque_label(self.label):
            item["label"] = self.label
        # visible_label: suppress if it IS opaque or if it duplicates label
        _vis = self.visible_label
        if _vis and not self._is_opaque_label(_vis) and _vis != self.label:
            item["visible_label"] = _vis
        if self.label_basis and self.label_basis != "explicit":
            item["label_basis"] = self.label_basis
        if self.text:
            item["text"] = self.text
        if self.facets:
            item["facets"] = {facet.kind: facet.to_dict() for facet in self.facets}
        if self.children:
            item["children"] = [child.to_dict() for child in self.children]
        if self.defects:
            item["defects"] = list(self.defects)
        return item

    def display_badge(self) -> str:
        # Use label for badge generation (it is already normalized).
        # Synthetic ordinal-fallback labels start with "__ord_" and must never
        # appear to users — treat them as absent (empty badge).
        raw = self.label if self.label and not self.label.startswith("__ord_") else ""
        label = display_structure_label(raw) if raw else ""
        if self.kind == "section":
            return f"{label} §" if label else "pykälä"
        if self.kind == "subsection":
            return f"{label} mom." if label else "mom."
        if self.kind == "item":
            return f"{label} kohta" if label else "kohta"
        if self.kind == "subitem":
            return f"{label} alakohta" if label else "alakohta"
        if self.kind == "heading":
            return "otsikko"
        if self.kind == "intro":
            return "johdanto"
        return label or self.kind

    def key(self, index: int = 0) -> str:
        if self.label:
            return f"{self.kind}:{self.label}"
        if self.kind in _SEMANTIC_FACET_KINDS:
            return self.kind
        return f"{self.kind}:{index}"


@dataclass(frozen=True)
class SemanticDiffStats:
    structural: int = 0
    label: int = 0
    text: int = 0
    editorial: int = 0


@dataclass(frozen=True)
class SemanticDiffResult:
    stats: SemanticDiffStats
    kind: str
    summary: str


@dataclass(frozen=True)
class SemanticPathPart:
    kind: str
    label: str = ""

    def to_token(self) -> str:
        return f"{self.kind}:{self.label}" if self.label else self.kind

    def to_dict(self) -> dict[str, str]:
        item = {"kind": self.kind}
        if self.label:
            item["label"] = self.label
        return item

    @classmethod
    def from_token(cls, token: str) -> "SemanticPathPart":
        kind, _, label = str(token or "").partition(":")
        return cls(kind=kind, label=label)


@dataclass(frozen=True)
class SemanticPath:
    parts: tuple[SemanticPathPart, ...] = ()

    def append(self, kind: str, label: str = "") -> "SemanticPath":
        return SemanticPath(parts=self.parts + (SemanticPathPart(kind=kind, label=label),))

    def to_tokens(self) -> list[str]:
        return [part.to_token() for part in self.parts]

    def to_dict_list(self) -> list[dict[str, str]]:
        return [part.to_dict() for part in self.parts]

    @classmethod
    def from_tokens(cls, tokens: tuple[str, ...] | list[str]) -> "SemanticPath":
        return cls(parts=tuple(SemanticPathPart.from_token(token) for token in tokens))


@dataclass(frozen=True)
class AlignedSemanticNode:
    left: SemanticStructureNode | None
    right: SemanticStructureNode | None
    match_basis: str = "unmatched"
    children: tuple["AlignedSemanticNode", ...] = ()

    def kind(self) -> str:
        node = self.left or self.right
        return node.kind if node is not None else ""

    def label(self) -> str:
        node = self.left or self.right
        return node.label if node is not None else ""

    def to_dict(self) -> dict[str, Any]:
        from lawvm.semantic.align import _aligned_semantic_facets_to_dict

        item: dict[str, Any] = {
            "kind": self.kind(),
            "label": self.label(),
        }
        if self.left is not None:
            item["left"] = self.left.to_dict()
        if self.right is not None:
            item["right"] = self.right.to_dict()
        item["match_basis"] = self.match_basis
        aligned_facets = _aligned_semantic_facets_to_dict(self.left, self.right)
        if aligned_facets:
            item["facets"] = aligned_facets
        if self.children:
            item["children"] = [child.to_dict() for child in self.children]
        return item


@dataclass(frozen=True)
class SemanticDiffEvent:
    kind: str
    semantic_path: SemanticPath | tuple[str, ...]
    match_basis: str
    unit_kind: str
    unit_label: str = ""
    facet_kind: str = ""
    left_text: str = ""
    right_text: str = ""
    left_badge: str = ""
    right_badge: str = ""
    oracle_diagnosis: str = ""

    def to_dict(self) -> dict[str, Any]:
        semantic_path_obj = (
            self.semantic_path
            if isinstance(self.semantic_path, SemanticPath)
            else SemanticPath.from_tokens(self.semantic_path)
        )
        item: dict[str, Any] = {
            "kind": self.kind,
            "semantic_path": semantic_path_obj.to_tokens(),
            "semantic_path_parts": semantic_path_obj.to_dict_list(),
            "match_basis": self.match_basis,
            "unit_kind": self.unit_kind,
        }
        if self.unit_label:
            item["unit_label"] = self.unit_label
        if self.facet_kind:
            item["facet_kind"] = self.facet_kind
        if self.left_text:
            item["left_text"] = self.left_text
        if self.right_text:
            item["right_text"] = self.right_text
        if self.left_badge:
            item["left_badge"] = self.left_badge
        if self.right_badge:
            item["right_badge"] = self.right_badge
        if self.oracle_diagnosis:
            item["oracle_diagnosis"] = self.oracle_diagnosis
        return item


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def canonical_structure_kind(kind: str) -> str:
    return _CANONICAL_STRUCTURE_KIND.get(str(kind or "").strip(), "")


def normalize_semantic_label(kind: str, label: str) -> str:
    raw = _normalize_text(label)
    if not raw:
        return ""
    if kind == "section":
        raw = re.sub(r"\s*§\s*$", "", raw, flags=re.IGNORECASE)
        # Compress "11 a" → "11a" for letter-suffixed sections
        return re.sub(r"^(\d+)\s+([a-zäöå])$", r"\1\2", raw, flags=re.IGNORECASE)
    if kind == "subsection":
        match = re.match(r"^(\d+[a-zäöå]?)", raw, flags=re.IGNORECASE)
        return match.group(1) if match else raw
    if kind == "item":
        raw = re.sub(r"\s+kohta\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"[)\s.]+$", "", raw)
        return re.sub(r"^(\d+)\s+([a-zäöå])$", r"\1\2", raw, flags=re.IGNORECASE)
    if kind == "subitem":
        raw = re.sub(r"\s+alakohta\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"[)\s.]+$", "", raw)
        return re.sub(r"^(\d+)\s+([a-zäöå])$", r"\1\2", raw, flags=re.IGNORECASE)
    return raw


def display_structure_label(label: str) -> str:
    raw = str(label or "").strip()
    if not raw:
        return ""
    match = re.match(r"^(\d+)([a-zäöå])$", raw, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return raw


def normalize_visible_semantic_label(kind: str, label: str) -> str:
    """Normalize visible label with kind-aware suffix stripping.

    Strips the same decorative suffixes as normalize_semantic_label (§, mom.,
    kohta, alakohta) and compresses letter-suffix spacing (11 a → 11a)
    so that only genuine visible-label changes are detected.
    """
    raw = _normalize_text(label)
    if not raw:
        return ""
    if kind == "section":
        raw = re.sub(r"\s*§\s*$", "", raw, flags=re.IGNORECASE)
        return re.sub(r"^(\d+)\s+([a-zäöå])$", r"\1\2", raw, flags=re.IGNORECASE)
    if kind == "subsection":
        match = re.match(r"^(\d+[a-zäöå]?)", raw, flags=re.IGNORECASE)
        return match.group(1) if match else raw
    if kind in ("item", "subitem"):
        suffix = "alakohta" if kind == "subitem" else "kohta"
        raw = re.sub(rf"\s+{suffix}\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"[)\s.]+$", "", raw)
        return re.sub(r"^(\d+)\s+([a-zäöå])$", r"\1\2", raw, flags=re.IGNORECASE)
    return raw


def _join_text_parts(parts: list[str]) -> str:
    return _normalize_text(" ".join(part for part in parts if part))


def is_semantic_facet_kind(kind: str) -> bool:
    return kind in _SEMANTIC_FACET_KINDS


def semantic_structural_children(
    children: tuple[SemanticStructureNode, ...] | list[SemanticStructureNode],
) -> tuple[SemanticStructureNode, ...]:
    return tuple(child for child in children if not is_semantic_facet_kind(child.kind))


def semantic_facet_children(
    children: tuple[SemanticStructureNode, ...] | list[SemanticStructureNode],
) -> tuple[SemanticStructureNode, ...]:
    return tuple(child for child in children if is_semantic_facet_kind(child.kind))


def _facet_map_from_tuple(
    facets: tuple[SemanticStructureFacet, ...],
) -> dict[str, SemanticStructureFacet]:
    return {facet.kind: facet for facet in facets}


def _with_wording_facet(
    facets: tuple[SemanticStructureFacet, ...],
    text: str,
    tables: tuple[TableBody, ...] = (),
) -> tuple[SemanticStructureFacet, ...]:
    wording = _normalize_text(text)
    if not wording and not tables:
        return facets
    if any(facet.kind == _WORDING_FACET_KIND for facet in facets):
        # If tables were provided but an existing wording facet exists,
        # replace it with one that includes the tables.
        if tables:
            return tuple(
                SemanticStructureFacet(kind=f.kind, text=f.text, tables=tables)
                if f.kind == _WORDING_FACET_KIND
                else f
                for f in facets
            )
        return facets
    return facets + (SemanticStructureFacet(kind=_WORDING_FACET_KIND, text=wording, tables=tables),)


def _node_wording_facet(node: SemanticStructureNode | None) -> SemanticStructureFacet | None:
    if node is None:
        return None
    wording = _facet_map_from_tuple(node.facets).get(_WORDING_FACET_KIND)
    if wording is not None:
        return wording
    text = _normalize_text(node.text)
    if not text:
        return None
    return SemanticStructureFacet(kind=_WORDING_FACET_KIND, text=text)


def _partition_semantic_children(
    children: tuple[SemanticStructureNode, ...],
) -> tuple[tuple[SemanticStructureFacet, ...], tuple[SemanticStructureNode, ...]]:
    facets: list[SemanticStructureFacet] = []
    structural_children: list[SemanticStructureNode] = []
    for child in children:
        if is_semantic_facet_kind(child.kind):
            facets.append(SemanticStructureFacet(kind=child.kind, text=child.text))
        else:
            structural_children.append(child)
    return tuple(facets), tuple(structural_children)
