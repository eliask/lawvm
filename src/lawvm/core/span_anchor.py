"""Span-level anchoring for sub-section content (L0 preparation).

Provides stable, content-addressed anchors below section level.
Upper layers (L1-L5) can attach interpretive claims, practice
observations, and case law references to these anchors.

Design decisions:
- Anchors are content-addressed: same text at same structural
  position = same anchor. This survives editorial reformatting
  but changes when content changes (correct -- the claim may
  no longer apply).
- Uses LegalAddress for section_address, consistent with the stored address
  model in core.
- Walks only immediate children and one level of nesting
  (subsection->items, paragraph->items). Deeper nesting can
  be added when upper layers require it.

API tier
--------
Stable anchoring contract for below-section references. Coverage depth may
expand later, but the anchoring model itself is intended to remain shared.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Tuple

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import IRNodeKind, SpanKind

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode


# Map IR node kinds to the governed span-anchor vocabulary.
# Upper layers use these typed values to filter anchors
# (e.g. all headings or all items in subsection 2).
_KIND_MAP: Dict[IRNodeKind, SpanKind] = {
    IRNodeKind.SUBSECTION: SpanKind.SUBSECTION,
    IRNodeKind.PARAGRAPH: SpanKind.PARAGRAPH,
    IRNodeKind.ITEM: SpanKind.ITEM,
    IRNodeKind.HEADING: SpanKind.HEADING,
    IRNodeKind.INTRO: SpanKind.INTRO,
    IRNodeKind.P: SpanKind.SENTENCE,
    IRNodeKind.CONTENT: SpanKind.SENTENCE,
    IRNodeKind.SUBPARAGRAPH: SpanKind.SUBPARAGRAPH,
}

# Kinds whose children should also be anchored (one level of nesting).
_NESTED_PARENT_KINDS = frozenset({IRNodeKind.SUBSECTION, IRNodeKind.PARAGRAPH})


@dataclass(frozen=True)
class SpanAnchor:
    """A stable anchor to a sub-section text span.

    Anchors are content-addressed: same text at same structural position =
    same anchor. This survives editorial reformatting but changes when
    content changes, which is correct because the claim may no longer apply.
    """

    # Structural position
    section_address: LegalAddress
    span_kind: SpanKind
    span_index: int  # 0-based index within parent

    # Content fingerprint
    text_hash: str  # SHA-256 of normalized span text
    text_preview: str  # first 80 chars for human readability
    span_path: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.span_path and self.span_path[-1] != self.span_index:
            raise ValueError(
                "SpanAnchor.span_path must terminate at span_index"
            )

    @property
    def anchor_id(self) -> str:
        """Stable content-addressed anchor ID (24 hex chars)."""
        path = self.span_path or (self.span_index,)
        path_part = ".".join(str(i) for i in path)
        raw = (
            f"{self.section_address}/"
            f"{self.span_kind.value}:{path_part}:{self.text_hash[:16]}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class SectionAnchors:
    """All span anchors for one section."""

    section_address: LegalAddress
    anchors: Tuple[SpanAnchor, ...] = ()
    content_hash: str = ""  # whole-section hash for quick change detection


def _text_hash(text: str) -> str:
    """SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_span_anchors(section_ir: "IRNode", section_address: LegalAddress) -> SectionAnchors:
    """Extract all span-level anchors from a section IRNode.

    Walks the section's children (subsections, paragraphs, items)
    and creates a SpanAnchor for each addressable sub-element.
    Also anchors nested children one level deep (items within
    subsections, items within paragraphs).
    """
    from lawvm.core.ir_helpers import irnode_content_hash, irnode_to_text

    anchors = []

    for i, child in enumerate(section_ir.children):
        span_kind = _KIND_MAP.get(child.kind)
        if span_kind is None:
            continue
        text = irnode_to_text(child).strip()
        if not text:
            continue
        anchors.append(
            SpanAnchor(
                section_address=section_address,
                span_kind=span_kind,
                span_index=i,
                span_path=(i,),
                text_hash=_text_hash(text),
                text_preview=text[:80],
            )
        )

        # Anchor nested children within subsections/paragraphs
        if child.kind in _NESTED_PARENT_KINDS:
            for j, grandchild in enumerate(child.children):
                gc_kind = _KIND_MAP.get(grandchild.kind)
                if gc_kind is None:
                    continue
                gc_text = irnode_to_text(grandchild).strip()
                if not gc_text:
                    continue
                anchors.append(
                    SpanAnchor(
                        section_address=section_address,
                        span_kind=gc_kind,
                        span_index=j,
                        span_path=(i, j),
                        text_hash=_text_hash(gc_text),
                        text_preview=gc_text[:80],
                    )
                )

    section_hash = irnode_content_hash(section_ir)
    return SectionAnchors(
        section_address=section_address,
        anchors=tuple(anchors),
        content_hash=section_hash,
    )


def extract_all_anchors(body_ir: "IRNode") -> Dict[LegalAddress, SectionAnchors]:
    """Extract span anchors for all sections in a statute body.

    Walks body_ir.children (chapters, parts, sections at body level).
    For each section-kind child encountered (directly or within chapters/parts),
    calls extract_span_anchors.

    Returns a dict mapping LegalAddress -> SectionAnchors.
    """
    result: Dict[LegalAddress, SectionAnchors] = {}

    def _append_path(path_prefix: Tuple[Tuple[str, str], ...], node: "IRNode") -> Tuple[Tuple[str, str], ...]:
        label = node.label or "?"
        return path_prefix + ((node.kind.value, label),)

    def _walk(node: "IRNode", path_prefix: Tuple[Tuple[str, str], ...]) -> None:
        for child in node.children:
            if child.kind == IRNodeKind.SECTION:
                addr = LegalAddress(path=_append_path(path_prefix, child))
                result[addr] = extract_span_anchors(child, addr)
            elif child.kind in (IRNodeKind.CHAPTER, IRNodeKind.PART, IRNodeKind.BODY):
                _walk(child, _append_path(path_prefix, child))

    _walk(body_ir, ())
    return result
