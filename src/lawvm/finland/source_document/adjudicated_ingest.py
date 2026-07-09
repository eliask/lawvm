"""Per-page adjudicated ingest → whole-document strict IR.

The full structure pipeline, wiring the pieces together:

1. PER PAGE, several producers read the page — the vision transcription (correct
   reading order + block kinds) as the structural backbone, and a page-ordered
   text extraction as an independent cross-witness.
2. The page's two reads are ADJUDICATED (``lawvm.core.source_document.adjudication``)
   — if they corroborate, the page's nodes are MULTI_WITNESS_ADJUDICATED, else
   SINGLE_WITNESS. Producer-neutral: no reader is trusted by species.
3. The per-page node lists are COMPOSED across pages
   (``compose_pages``) — a multi-page table becomes one ``TABLE``, footnotes one
   unified set, page-split paragraphs stitched — into one whole-document
   ``SourceDocumentIR``.

TRANSCRIPTION MODALITY: the page read is an explicit STRUCTURAL BUILD SCRIPT
(``struct_wire``) — one node per line naming id/kind/parent/src — over ONE
grammar. ``transcription_modality`` selects only how a TEXT LEAF is populated
(economics per the mekanismirealismi LLM guide: local output tokens ~40× input,
design input-heavy/output-sparse):
  * ``struct_span`` — text leaves reference reading-order lines (span-copied by
    code); near-zero output tokens on a text-native page.
  * ``struct_full`` — text leaves inline-transcribed by the model.
  * ``struct_auto`` — per-leaf choice.
  * ``struct_patch`` — span-copy PLUS addressed char-span PATCH deltas correcting
    extraction errors (output-sparse alternative to inline re-transcription;
    iterable to convergence over the same source).
Whatever the lane, the vision read stays a WITNESS adjudicated against the
reading-order text — the composed IR and assurance tiers have the same shape.

Discipline (AGENTS.md §1.9, §1.10): typed carriers; the per-page node building
and tier assignment are pure and testable without a server.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

from lawvm.core.source_document.adjudication import Adjudicator
from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.composition import ComposedDocument, compose_pages
from lawvm.core.source_document.extraction import ExtractionAssertion, SourceManifestation
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)


def reading_order_pages_from_pdf(pdf_bytes: bytes, *, max_pages: int = 500) -> List[str]:
    """Per-page reading-order text (pypdfium2) — the independent cross-witness."""
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(min(len(doc), max_pages))]
    finally:
        doc.close()


# The structural build-script lanes (one shared grammar; the suffix selects only
# how a TEXT LEAF is populated). This is the single transcription surface.
TRANSCRIPTION_MODALITIES = (
    "struct_span",
    "struct_full",
    "struct_auto",
    "struct_patch",
)

# Structured lane → the leaf-content source passed to ``propose_page_struct``.
_STRUCT_LEAF_MODE = {
    "struct_span": "span",
    "struct_full": "inline",
    "struct_auto": "auto",
    "struct_patch": "patch",
}


def _struct_node_to_source_node(
    node: "object",  # StructBuildNode
    tier: AssuranceTier,
    region: SourceAnchor,
    digest: str,
    page_num: int,
) -> SourceDocumentNode:
    """Lower one v2 ``StructBuildNode`` subtree into a ``SourceDocumentNode``.

    An IMAGE node's anchor is a page+bbox image anchor and its attrs carry the
    content-addressed ``image_digest`` / ``media_type`` / intrinsic px dims /
    ``role`` / ``bit_exact_source`` — the model NEVER re-encodes pixels. Text
    leaves carry the code-resolved ``.text`` (span-copied or inline). The tree
    STRUCTURE is a single model claim at ``tier`` (raised only where a
    deterministic structure witness corroborates; not this pass).
    """
    from lawvm.core.source_document.anchors import BBox

    attrs: dict[str, str] = {}
    anchor = region
    if node.kind is SourceDocumentNodeKind.IMAGE_REGION and node.image is not None:  # ty: ignore[unresolved-attribute]
        img = node.image  # ty: ignore[unresolved-attribute]
        x0, y0, x1, y1 = img.bbox
        anchor = SourceAnchor(
            artifact_digest=digest,
            locator=f"page={page_num};bbox={x0},{y0},{x1},{y1}",
            page_num=page_num,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        )
        attrs = {
            "image_digest": img.digest,
            "image_index": str(img.index),
            "media_type": img.media_type,
            "px_width": str(img.width),
            "px_height": str(img.height),
            "role": img.role,
        }
    children = tuple(
        _struct_node_to_source_node(c, tier, region, digest, page_num)
        for c in node.children  # ty: ignore[unresolved-attribute]
    )
    return SourceDocumentNode(
        kind=node.kind,  # ty: ignore[unresolved-attribute]
        assurance_tier=tier,
        anchor=anchor,
        text=node.text,  # ty: ignore[unresolved-attribute]
        children=children,
        attrs=attrs,
    )


def _page_assurance(
    vision_text: str,
    reading_order_text: str,
    adjudicator: Optional[Adjudicator],
    region: SourceAnchor,
) -> AssuranceTier:
    """Adjudicate the page's two independent reads → the page's assurance tier.

    With no adjudicator (or no second witness) the vision read is single-witness.
    When the vision and reading-order reads of the page corroborate, the page is
    MULTI_WITNESS_ADJUDICATED.
    """
    if adjudicator is None or not reading_order_text.strip():
        return AssuranceTier.SINGLE_WITNESS
    candidates = (
        ExtractionAssertion(
            run_id="vision:page", fragment_kind="paragraph", text=vision_text[:1500], anchor=region
        ),
        ExtractionAssertion(
            run_id="reading_order:page",
            fragment_kind="paragraph",
            text=reading_order_text[:1500],
            anchor=region,
        ),
    )
    return adjudicator.adjudicate(region, candidates).assurance


class _StructVisionProducer(Protocol):
    """Structural typing for the build-script vision backend this ingest consumes."""

    def propose_page_struct(
        self,
        manifestation: SourceManifestation,
        page_num: int,
        page_elements: "object",
        *,
        leaf_mode: str = "span",
    ) -> "object": ...


def _struct_text_of(node: "object") -> str:
    """Concatenate a struct subtree's text (for the page-tier adjudication witness)."""
    parts = [node.text] if node.text else []  # ty: ignore[unresolved-attribute]
    for c in node.children:  # ty: ignore[unresolved-attribute]
        parts.append(_struct_text_of(c))
    return "\n".join(p for p in parts if p)


@dataclass(frozen=True, slots=True)
class StructIngestResult:
    """A v2 build-script ingest: the composed document + collected image blobs.

    ``document`` is the composed whole-document tree. ``images`` are every
    embedded/rasterized image element the pages
    surfaced (deduped by digest) so the caller can content-address + store them.
    ``struct_findings`` gathers the per-page build findings (dropped nodes,
    re-parented orphans) and ``terminator_stats`` the 0x1F-compliance counts.
    """

    document: ComposedDocument
    images: Tuple["object", ...]
    struct_findings: Tuple[str, ...]
    terminator_stats: Tuple[int, int]  # (terminated_command_lines, total_command_lines)


def struct_document_ingest(
    manifestation: SourceManifestation,
    *,
    vision: "object",
    page_element_producer: "object",
    adjudicator: Optional[Adjudicator] = None,
    max_pages: int = 200,
    transcription_modality: str = "struct_span",
) -> "StructIngestResult":
    """Ingest a PDF through the v2 build-script lane → composed doc + image blobs.

    Each page's numbered reading-order lines + embedded image elements
    (``page_element_producer``) go to the vision producer's ``propose_page_struct``
    over ONE build-script grammar; ``transcription_modality`` selects only the
    leaf-content source (``struct_span`` / ``struct_full`` / ``struct_auto``). The
    per-page forests are composed across pages exactly like the flat lanes, and
    the surfaced image blobs are returned for content-addressed storage.
    """
    if transcription_modality not in _STRUCT_LEAF_MODE:
        raise ValueError(
            f"struct_document_ingest requires a struct_* modality; got "
            f"{transcription_modality!r} (one of {tuple(_STRUCT_LEAF_MODE)})"
        )
    leaf_mode = _STRUCT_LEAF_MODE[transcription_modality]
    ro_pages = reading_order_pages_from_pdf(manifestation.source_bytes, max_pages=max_pages)
    pages: List[Tuple[SourceDocumentNode, ...]] = []
    images_by_digest: dict = {}
    findings: List[str] = []
    terminated = total = 0

    for idx, ro_text in enumerate(ro_pages):
        page_num = idx + 1
        region = SourceAnchor(
            artifact_digest=manifestation.artifact_digest,
            locator=f"page={page_num}",
            page_num=page_num,
        )
        page_elements = page_element_producer.page_elements(  # ty: ignore[unresolved-attribute]
            manifestation.source_bytes, page_num
        )
        result = vision.propose_page_struct(  # ty: ignore[unresolved-attribute]
            manifestation, page_num, page_elements, leaf_mode=leaf_mode
        )
        build = result.build
        reconstructed = "\n".join(_struct_text_of(n) for n in build.roots)
        tier = _page_assurance(reconstructed, ro_text, adjudicator, region)
        nodes = tuple(
            _struct_node_to_source_node(
                n, tier, region, manifestation.artifact_digest, page_num
            )
            for n in build.roots
        )
        pages.append(nodes)
        for img in result.images:
            images_by_digest[img.element.digest] = img
        findings.extend(f"page {page_num}: {f}" for f in build.findings)
        terminated += build.terminated_command_lines
        total += build.total_command_lines

    root_anchor = SourceAnchor(artifact_digest=manifestation.artifact_digest, locator="manifestation")
    document = compose_pages(pages, root_anchor)
    return StructIngestResult(
        document=document,
        images=tuple(images_by_digest.values()),
        struct_findings=tuple(findings),
        terminator_stats=(terminated, total),
    )
