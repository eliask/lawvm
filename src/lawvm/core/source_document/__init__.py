"""SourceDocumentIR ingestion — generic carrier + adjudication boundary (D0).

Jurisdiction-neutral source-document induction: EVERY extractor (pdfplumber,
OCR, layout, a vision model, a prior layer) is a noisy candidate producer, none
privileged. High assurance comes from an ``Adjudicator`` — an LLM workflow, a
human — that reconciles several candidates and composes a higher-quality node,
possibly iteratively. Assurance is a property of adjudication, not of producer
species (the pdfplumber-vs-model dichotomy is false). See
``notes_internal/pro_on_unstructured_input_ingest.md``.

This package owns the LawVM IR + proof boundary for unstructured legal sources.
External document parsers are *replaceable candidate producers*: they emit
``ExtractionAssertion`` proposals, never LawVM truth
(``notes_internal/pro_on_unstructured_input_ingest_deps.md``).

Public surface (D0): ``SourceManifestation`` input; ``SourceAnchor``;
``ExtractionRun`` / ``ExtractionAssertion`` / ``SourceExtractionBackend``
Protocol; ``SourceDocumentNode`` carrier + governed kinds + producer-neutral
``AssuranceTier``; structural pre-adjudication gates (``validate_anchored`` …);
the ``Adjudicator`` protocol + ``Adjudication`` carrier + ``assurance_for``
policy; coverage partition + residual taxonomy. Finland document families +
lowerers land in ``lawvm.finland.source_document`` (D2+); product lowerers
(InitialStateEvent / ProposalPackage) land in D6/D7.
"""
from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.coverage import (
    CoverageReport,
    QualityIssue,
    QualityIssueFamily,
    RegionOwnership,
    Residual,
    ResidualFamily,
    coverage_report,
    detect_quality_issues,
)
from lawvm.core.source_document.adjudication import (
    Adjudication,
    AdjudicationMethod,
    Adjudicator,
    assurance_for,
)
from lawvm.core.source_document.extraction import (
    ExtractionAffordances,
    ExtractionAssertion,
    ExtractionRun,
    SourceExtractionBackend,
    SourceManifestation,
)
from lawvm.core.source_document.composition import (
    ComposedDocument,
    ContinuationJudge,
    DefaultContinuationJudge,
    compose_pages,
)
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.core.source_document.proposal import (
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    ProposalPackage,
)
from lawvm.core.source_document.validation import (
    ValidationResult,
    is_structurally_valid,
    validate_anchored,
    validate_governed_kind,
)

__all__ = [
    "Adjudication",
    "AdjudicationMethod",
    "Adjudicator",
    "AssuranceTier",
    "BBox",
    "CandidateOperation",
    "ComposedDocument",
    "ConditionalBranch",
    "ContinuationJudge",
    "CoverageReport",
    "DefaultContinuationJudge",
    "ProposalAuthorityStatus",
    "ProposalPackage",
    "ExtractionAffordances",
    "ExtractionAssertion",
    "ExtractionRun",
    "QualityIssue",
    "QualityIssueFamily",
    "RegionOwnership",
    "Residual",
    "ResidualFamily",
    "SourceAnchor",
    "SourceDocumentNode",
    "SourceDocumentNodeKind",
    "SourceExtractionBackend",
    "SourceManifestation",
    "ValidationResult",
    "assurance_for",
    "compose_pages",
    "coverage_report",
    "detect_quality_issues",
    "is_structurally_valid",
    "validate_anchored",
    "validate_governed_kind",
]
