"""Extractor-agnostic extraction runs + assertions (D0).

An external parser — pdfplumber, pypdfium2, python-docx, a local vision model,
or a future OCR engine — is never LawVM's truth boundary. It emits
``ExtractionAssertion`` proposals over source regions; LawVM validates and
lowers accepted assertions into ``SourceDocumentIR`` and, on acceptance, into
the provenance graph as a ``lawvm.core.provenance_graph.ProvenanceAssertion``
with ``layer="extraction"``. See AGENTS.md §0 (generators propose; typed
validators authorize; replay consumes only authorized output) and the
dependency admission rule in ``notes_internal/pro_on_unstructured_input_ingest_deps.md``.

Producer identity REUSES ``lawvm.core.provenance_graph.Producer``:
``producer_kind`` is metadata for policy queries, NEVER a precedence input
(no 'human beats llm' in the kernel). Authority comes from validation, source
anchoring, and admissibility policy (``validation.py``).

Reproducibility (D8): ``output_digest`` makes a cache hit byte-stable ARTIFACT
reproducibility — NOT model rerun determinism. A new model run is a new
``ExtractionRun``; the trusted object is the stored artifact, not the hope that
a model regenerates it.

Discipline (AGENTS.md §1.9): typed frozen carriers; tuple fields, never lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Tuple

from lawvm.core.provenance_graph import Producer
from lawvm.core.source_document.anchors import SourceAnchor


@dataclass(frozen=True, slots=True)
class SourceManifestation:
    """Raw observed source bytes + identity — the pipeline input.

    One per fetched source artifact (PDF / DOCX / HTML / XML / image). Carries
    its SHA-256 ``artifact_digest`` so every anchor and run references a stable
    artifact identity.
    """

    artifact_digest: str
    source_bytes: bytes
    locator: str
    """Where it was fetched from / its stable id (Finlex AKN id, CELEX, file path)."""
    source_role: str
    """Jurisdiction-neutral role of the fetched artifact:
    'statute' | 'attachment' | 'corrigendum' | 'government_proposal_draft' |
    'committee_report' | ... . A frontend maps its own document taxonomy onto
    these neutral roles (a Finnish HE luonnos / a draft SI / a COM proposal all
    map to 'government_proposal_draft')."""
    fetched_at: datetime
    media_type: str = ""
    """'application/pdf' | 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' | ..."""

    def __post_init__(self) -> None:
        for name in ("artifact_digest", "locator", "source_role"):
            if not getattr(self, name):
                raise ValueError(f"SourceManifestation.{name} must be non-empty")
        if not isinstance(self.source_bytes, (bytes, bytearray)):
            raise TypeError("SourceManifestation.source_bytes must be bytes")
        if not isinstance(self.fetched_at, datetime):
            raise TypeError("SourceManifestation.fetched_at must be a datetime")


@dataclass(frozen=True, slots=True)
class ExtractionAffordances:
    """Deterministic signals handed to a proposal backend as scaffold.

    A model / OCR backend never reads from scratch: it corrects or refines the
    deterministic affordance bundle (the review's principle 2 — affordances, not
    from-scratch). D0 carries the waist; D2 (``native_pdf`` / ``native_docx``)
    populates ``native_text`` and ``layout_digest`` for the regions deterministic
    extraction already owns.
    """

    native_text: str = ""
    layout_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    """One run of one extractor over one source manifestation.

    ``output_digest`` hashes the canonical assertion bundle, so a cache hit is
    byte-stable artifact reproducibility (D8) — a re-run with the same cache key
    returns the same stored artifact; a cache miss is a new ``ExtractionRun``.
    """

    run_id: str
    producer: Producer
    backend_id: str
    """'native_pdf' | 'native_docx' | 'native_xml' | 'pdf_vision' | 'manual_review' | ..."""
    backend_version: str
    source_artifact_digest: str
    input_affordance_digest: str
    """Hash of the deterministic affordance bundle this run consumed."""
    output_digest: str
    started_at: datetime
    ended_at: datetime
    license_summary: str = ""

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "backend_id",
            "backend_version",
            "source_artifact_digest",
            "input_affordance_digest",
            "output_digest",
        ):
            if not getattr(self, name):
                raise ValueError(f"ExtractionRun.{name} must be non-empty")
        if not isinstance(self.producer, Producer):
            raise TypeError("ExtractionRun.producer must be a provenance_graph.Producer")
        if not isinstance(self.started_at, datetime) or not isinstance(self.ended_at, datetime):
            raise TypeError("ExtractionRun.started_at/ended_at must be datetimes")
        if self.ended_at < self.started_at:
            raise ValueError("ExtractionRun.ended_at must be >= started_at")


@dataclass(frozen=True, slots=True)
class ExtractionAssertion:
    """One extraction proposal: a claimed fragment anchored to a source region.

    PRE-VALIDATION. ``authority_tier`` is assigned by the validators (D5), NEVER
    by the producer — a generator does not authorize itself (AGENTS.md §0).
    ``fragment_kind`` is a ``SourceDocumentNodeKind`` value (checked at the IR
    layer); ``uncertainty_flags`` carry the model's own self-reported doubt,
    which the validators may read but never treat as verification.
    """

    run_id: str
    fragment_kind: str
    text: str
    anchor: SourceAnchor
    uncertainty_flags: Tuple[str, ...] = ()
    raw_output_ref: str = ""
    """Digest/ref into the run's cached raw output (D8 reproducibility evidence)."""

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("ExtractionAssertion.run_id must be non-empty")
        if not self.fragment_kind:
            raise ValueError("ExtractionAssertion.fragment_kind must be non-empty")
        if not isinstance(self.anchor, SourceAnchor):
            raise TypeError("ExtractionAssertion.anchor must be a SourceAnchor")
        if not isinstance(self.uncertainty_flags, tuple):
            raise TypeError("ExtractionAssertion.uncertainty_flags must be a tuple")


class SourceExtractionBackend(Protocol):
    """Replaceable extraction witness.

    Every external extractor — deterministic or model-backed — implements this
    Protocol. LawVM owns the IR + trust boundary; backends only propose. The
    pipeline does not care whether text came from pdfplumber, DOCX XML, a local
    vision model, human review, or a future OCR engine; it cares about
    provenance and validation status.
    """

    backend_id: str
    backend_version: str
    license_summary: str

    def extract(
        self,
        manifestation: SourceManifestation,
        affordances: ExtractionAffordances,
    ) -> Tuple[ExtractionAssertion, ...]:
        """Emit region-anchored extraction assertions for one source.

        Implementations MUST route only residual / low-confidence regions to
        expensive model backends (the review §3: region-level, not page-level),
        and MUST anchor every emitted fragment to a concrete source region —
        an unanchorable fragment is a hallucination and is rejected by D5.
        """
        ...
