"""Finland source-document profiles + lowerings (D2+).

Finland-specific document families (PDF attachment / corrigendum / scanned
statute, draft HE) wrapped as source-document lanes over the generic
``lawvm.core.source_document`` carrier. PDF is the priority; vision LLM
proposal lane for residuals; lowering from SourceDocumentIR to LawVM IRNode
for structured output (attachments, PDF-only bodies, draft HE bill text).

See the approved plan at ``.claude/plans/calm-kindling-wand.md``.
"""
from lawvm.finland.source_document.he_draft import (
    HeDocKind,
    classify_he_document,
    extract_conditional_branch,
    he_pdf_to_proposal,
    reading_order_text_from_pdf,
)
from lawvm.finland.source_document.pdf_profiles import (
    HeDraftProposal,
    PdfIngestResult,
    extract_he_draft_proposal,
    ingest_pdf_manifestation,
    load_manifestation_from_farchive,
    source_document_to_ir_node,
)

__all__ = [
    "HeDocKind",
    "HeDraftProposal",
    "PdfIngestResult",
    "classify_he_document",
    "extract_conditional_branch",
    "he_pdf_to_proposal",
    "reading_order_text_from_pdf",
    "extract_he_draft_proposal",
    "ingest_pdf_manifestation",
    "load_manifestation_from_farchive",
    "source_document_to_ir_node",
]
