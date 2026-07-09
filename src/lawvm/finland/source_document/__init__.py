"""Finland source-document profiles + lowerings (D2+).

Finland-specific document families (PDF attachment / corrigendum / scanned
statute, draft HE) wrapped as source-document lanes over the generic
``lawvm.core.source_document`` carrier. PDF is the priority; vision LLM
proposal lane for residuals; lowering from SourceDocumentIR to LawVM IRNode
for structured output (attachments, PDF-only bodies, draft HE bill text).

See the approved plan at ``.claude/plans/calm-kindling-wand.md``.
"""
from lawvm.finland.source_document.adjudicated_ingest import (
    adjudicated_document_ingest,
    reading_order_pages_from_pdf,
)
from lawvm.finland.source_document.branch_conflicts import (
    BranchConflictFinding,
    BranchConflictKind,
    BranchConflictSeverity,
    diagnose_branch_conflicts,
)
from lawvm.finland.source_document.branch_lowering import (
    candidate_op_to_legal_operation,
    conditional_branch_to_legal_branch,
)
from lawvm.finland.source_document.lausuntopalvelu import (
    HeFetchError,
    fetch_he_draft,
    resolve_dossier,
)
from lawvm.finland.source_document.materialize import (
    MaterializedProvision,
    apply_candidate_op,
    load_enacted_provision,
    materialize_conditional_provision,
)
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
    "BranchConflictFinding",
    "BranchConflictKind",
    "BranchConflictSeverity",
    "HeFetchError",
    "candidate_op_to_legal_operation",
    "conditional_branch_to_legal_branch",
    "diagnose_branch_conflicts",
    "fetch_he_draft",
    "resolve_dossier",
    "HeDocKind",
    "MaterializedProvision",
    "apply_candidate_op",
    "load_enacted_provision",
    "materialize_conditional_provision",
    "adjudicated_document_ingest",
    "reading_order_pages_from_pdf",
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
