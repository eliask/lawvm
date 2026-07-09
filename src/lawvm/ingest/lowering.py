"""Neutral SourceDocumentIR → LawVM IRNode lowering (jurisdiction-agnostic).

The bridge from the source-document plane to the legal-state IR. Split out of
``finland/source_document/pdf_profiles`` (Track A of the two-level pipeline) so
every jurisdiction's ingest can lower a ``SourceDocumentIR`` without importing
FI-specific idiom classification. The mapping is deliberately simple and
structural; sophisticated heading/paragraph recognition belongs in elaboration
or a dedicated classifier.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lawvm.core.source_document.ir import (
    SourceDocumentNode,
    SourceDocumentNodeKind,
)

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode


def source_document_to_ir_node(root: SourceDocumentNode) -> "IRNode":
    """Lower a (possibly vision-augmented) SourceDocumentIR into LawVM IRNode.

    This is the bridge from the source-document plane to the legal-state IR.
    Used for PDF-only attachments, corrigenda, and (for draft HE) the bill-text
    portions. Carries authority tier and source locator in attrs for provenance.

    Mapping is deliberately simple and structural; sophisticated heading/para
    recognition belongs in elaboration or a dedicated HE/attachment classifier.
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    kind_map = {
        SourceDocumentNodeKind.WORK_ROOT: IRNodeKind.HCONTAINER,
        SourceDocumentNodeKind.BODY: IRNodeKind.HCONTAINER,
        SourceDocumentNodeKind.SECTION: IRNodeKind.SECTION,
        SourceDocumentNodeKind.SUBSECTION: IRNodeKind.SUBSECTION,
        SourceDocumentNodeKind.PARAGRAPH: IRNodeKind.P,
        SourceDocumentNodeKind.HEADING: IRNodeKind.HEADING,
        SourceDocumentNodeKind.TABLE: IRNodeKind.TABLE,
        SourceDocumentNodeKind.TABLE_ROW: IRNodeKind.ROW,
        SourceDocumentNodeKind.TABLE_CELL: IRNodeKind.CELL,
        SourceDocumentNodeKind.FOOTNOTE: IRNodeKind.SCHEDULE_ENTRY,
        SourceDocumentNodeKind.ITEM: IRNodeKind.ITEM,
        # No dedicated IMAGE IRNodeKind — a generic BLOCK carrying the image facts
        # (image_digest / image_locator / bbox / dims / role) in attrs.
        SourceDocumentNodeKind.IMAGE_REGION: IRNodeKind.BLOCK,
        # Level-1 freeform escape hatches (math formula / image-baked or garbled
        # verbatim text): a generic BLOCK carrying the freeform facts (bbox +
        # freeform.reason + literal) in attrs — mirrors the IMAGE_REGION precedent.
        SourceDocumentNodeKind.MATH_REGION: IRNodeKind.BLOCK,
        SourceDocumentNodeKind.VERBATIM_REGION: IRNodeKind.BLOCK,
        SourceDocumentNodeKind.PROPOSAL_SECTION: IRNodeKind.HCONTAINER,
        SourceDocumentNodeKind.BILL_TEXT: IRNodeKind.HCONTAINER,
    }

    def _to_ir(n: SourceDocumentNode) -> IRNode:
        ir_kind = kind_map.get(n.kind, IRNodeKind.P)
        attrs: dict[str, str] = dict(n.attrs)
        attrs["assurance_tier"] = str(n.assurance_tier)
        if n.anchor and n.anchor.locator:
            attrs["source_locator"] = n.anchor.locator
            attrs["source_digest"] = n.anchor.artifact_digest[:16]
            if n.anchor.page_num is not None:
                attrs.setdefault("page", str(n.anchor.page_num))
        children = tuple(_to_ir(c) for c in n.children)
        return IRNode(
            kind=ir_kind,
            label=n.label,
            text=n.text,
            attrs=attrs,
            children=children,
        )

    return _to_ir(root)
