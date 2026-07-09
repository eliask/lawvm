"""Derived-IR store: persist the LawVM IR parsed from a source PDF, content-addressed.

WHY a separate store (and not finlex.farchive):
  * finlex.farchive holds the AUTHORITATIVE, bit-exact SOURCE artifacts (XML, PDF
    blobs). The structured IR we parse OUT of a PDF is a DERIVED product of a
    specific pipeline version — regenerable, versioned, and it would churn/bloat
    the source store. Keeping source vs derived apart preserves LawVM's
    source-is-authoritative / reconstruction-is-reproducible split (source
    immutable, mutable parsing elsewhere, evidence tiers preserved).
  * Parsing is EXPENSIVE (vision LLM ~15s/page) and NON-deterministic at the LLM
    layer — it "cannot be functional only": the output must be persisted.
  * The determinism firewall requires LLM-derived results to enter downstream
    consumers ONLY as content-addressed records carrying the producing model /
    pipeline id in provenance. This store IS that record cache.

THE ROUTE: every PDF goes through the UNIFIED, LLM-orchestrated adjudication —
independent producers (vision transcription + reading-order text, adjudicated
per page, composed across pages). No producer species is privileged (the
pdfplumber-vs-LLM dichotomy is false), and there is NO deterministic fallback
route: the LLM is assumed present; if the parse backend is unreachable that is a
FAIL-LOUD error, not a silent degradation. (A single page too dense for the
token budget degrades that PAGE's tier within the adjudicated route — it does not
switch routes.)

HOW stored: the derived store is simply ANOTHER farchive
(``data/fi_parsed_ir.farchive``). Each record's locator is content-addressed by
``(source digest, pipeline id, pipeline version)`` — where the version embeds the
vision + adjudicator model ids — so the same PDF under the same models is a cache
HIT, and a model/pipeline UPGRADE writes a NEW keyed record without overwriting
the old (versioned, auditable). Payload is JSON: the canonical LawVM ``IRNode``
(``to_jsonable_dict``) plus a manifest (pipeline, producers, model ids,
assurance-tier histogram, source provenance).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import SourceDocumentNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lawvm.core.source_document.adjudication import Adjudication
    from lawvm.core.source_document.anchors import SourceAnchor
    from lawvm.core.source_document.extraction import ExtractionAssertion

PARSED_STORE_DEFAULT = "data/fi_parsed_ir.farchive"

# The one route: unified, LLM-orchestrated adjudication (the only route for PDFs).
ADJUDICATED_PIPELINE_ID = "adjudicated_vision"
ADJUDICATED_COMPOSE_VERSION = "compose.v1"


class ParseBackendUnavailable(RuntimeError):
    """The LLM parse backend is unreachable. Parsing REQUIRES it — fail loud."""


def parsed_ir_locator(source_digest: str, pipeline_id: str, version: str) -> str:
    """Content-addressed derived-store key: source digest × pipeline × version."""
    return f"parsed/{source_digest}/{pipeline_id}@{version}"


def _serialize_parsed_record(ir_dict: Dict[str, Any], manifest: Dict[str, Any]) -> bytes:
    """Serialize {ir, manifest} to deterministic JSON bytes (sorted keys)."""
    payload = {"ir": ir_dict, "manifest": manifest}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """A parsed-IR store record: the LawVM IR dict + its producing manifest."""

    ir: Dict[str, Any]
    manifest: Dict[str, Any]
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """The resolved adjudicated route (probed once, reused across a bulk run)."""

    pipeline_id: str
    version: str
    vision: object
    adjudicator: object
    transcription_modality: str = "auto"


class _TolerantVision:
    """Vision producer wrapper: a page too dense for the token budget yields no
    vision witness for that page (its tier degrades) instead of aborting the run.
    This is a per-PAGE tier degradation WITHIN the adjudicated route, not a
    route switch."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def is_available(self) -> bool:
        return self._inner.is_available()  # ty: ignore[unresolved-attribute]

    def propose_page(self, manifestation: SourceManifestation, page_num: int) -> Tuple[Any, ...]:
        from lawvm.finland.llm_backends.vision_producer import VisionProducerTruncated

        try:
            return self._inner.propose_page(manifestation, page_num)  # ty: ignore[unresolved-attribute]
        except VisionProducerTruncated:
            return ()

    def propose_page_spans(
        self, manifestation: SourceManifestation, page_num: int, reading_order_text: str
    ) -> Tuple[Any, ...]:
        from lawvm.finland.llm_backends.vision_producer import VisionProducerTruncated

        try:
            return self._inner.propose_page_spans(  # ty: ignore[unresolved-attribute]
                manifestation, page_num, reading_order_text
            )
        except VisionProducerTruncated:
            return ()


class _TolerantAdjudicator:
    """Adjudicator wrapper: a page too dense to adjudicate within the token budget
    degrades to SINGLE_WITNESS for that page instead of aborting the run."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.adjudicator_id = getattr(inner, "adjudicator_id", "adjudicator")

    def is_available(self) -> bool:
        return self._inner.is_available()  # ty: ignore[unresolved-attribute]

    def adjudicate(
        self,
        region: SourceAnchor,
        candidates: Sequence[ExtractionAssertion],
        *,
        prior: Optional[Adjudication] = None,
    ) -> Adjudication:
        from lawvm.core.source_document.adjudication import Adjudication, AdjudicationMethod
        from lawvm.core.source_document.ir import (
            AssuranceTier,
            SourceDocumentNodeKind,
        )
        from lawvm.core.source_document.ir import SourceDocumentNode as _Node
        from lawvm.finland.llm_backends.llm_adjudicator import AdjudicationTruncated

        try:
            return self._inner.adjudicate(region, candidates, prior=prior)  # ty: ignore[unresolved-attribute]
        except AdjudicationTruncated:
            node = _Node(
                kind=SourceDocumentNodeKind.PARAGRAPH,
                assurance_tier=AssuranceTier.SINGLE_WITNESS,
                anchor=region,
            )
            return Adjudication(
                node=node,
                assurance=AssuranceTier.SINGLE_WITNESS,
                method=AdjudicationMethod.MULTI_CANDIDATE_RECONCILED,
                source_candidate_run_ids=tuple(c.run_id for c in candidates),
                corroborating_producers=(),
                adjudicator_id=self.adjudicator_id,
                rationale="page too dense for token budget → single-witness",
            )


# Modality → version-string tag. ``full_transcription`` stays UNTAGGED so the
# pre-modality records (which were all full transcription) remain cache HITS for
# that lane; span/auto records get a DISTINCT content-addressed key and COEXIST
# with full-transcription records for the same source.
_MODALITY_VERSION_TAG = {
    "full_transcription": "",
    "span_copy": "+modality=span",
    "auto": "+modality=auto",
}


def resolve_pipeline(
    *,
    vision_max_tokens: int = 3000,
    adjudicator_max_tokens: int = 2000,
    transcription_modality: str = "auto",
) -> PipelineSpec:
    """Resolve the adjudicated parse route. RAISES ``ParseBackendUnavailable`` if
    the LLM server is unreachable — parsing requires it, there is no fallback.
    Probes the server ONCE; reuse the returned spec across a bulk run.

    ``transcription_modality`` (``auto`` | ``span_copy`` | ``full_transcription``)
    picks the per-page vision output lane (see ``adjudicated_ingest``) and is
    folded into the pipeline VERSION so each modality's records are separately
    content-addressed.
    """
    from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator
    from lawvm.finland.llm_backends.vision_producer import VisionPageProducer

    if transcription_modality not in _MODALITY_VERSION_TAG:
        raise ValueError(
            f"unknown transcription_modality {transcription_modality!r}; "
            f"expected one of {tuple(_MODALITY_VERSION_TAG)}"
        )
    vision = VisionPageProducer(max_tokens=vision_max_tokens)
    adjudicator = LlmWorkflowAdjudicator(verify_pass=False, max_tokens=adjudicator_max_tokens)
    if not (vision.is_available() and adjudicator.is_available()):
        raise ParseBackendUnavailable(
            "LLM parse backend unreachable — the source_document pipeline requires "
            "a vision+adjudication server (default localhost:8080). No deterministic "
            "fallback: bring the backend up and retry."
        )
    modality_tag = _MODALITY_VERSION_TAG[transcription_modality]
    version = (
        f"vision={vision._resolve_model()}+{adjudicator.adjudicator_id}"
        f"{modality_tag}+{ADJUDICATED_COMPOSE_VERSION}"
    )
    return PipelineSpec(
        pipeline_id=ADJUDICATED_PIPELINE_ID,
        version=version,
        vision=_TolerantVision(vision),
        adjudicator=_TolerantAdjudicator(adjudicator),
        transcription_modality=transcription_modality,
    )


def _assurance_summary(root: SourceDocumentNode) -> Dict[str, int]:
    """Histogram of assurance tiers over a SourceDocumentNode tree (provenance)."""
    counts: Dict[str, int] = {}

    def _walk(n: SourceDocumentNode) -> None:
        name = n.assurance_tier.name
        counts[name] = counts.get(name, 0) + 1
        for c in n.children:
            _walk(c)

    _walk(root)
    return counts


def parse_pdf_to_ir(
    manifestation: SourceManifestation,
    spec: PipelineSpec,
    *,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """Parse a PDF → canonical LawVM IR + provenance manifest via the adjudicated
    route (vision + reading-order, adjudicated, composed). No cache lookup;
    ``parse_and_cache`` wraps this with the content-addressed store.
    """
    from lawvm.finland.source_document.adjudicated_ingest import adjudicated_document_ingest
    from lawvm.finland.source_document.pdf_profiles import source_document_to_ir_node

    doc = adjudicated_document_ingest(
        manifestation,
        vision=spec.vision,  # ty: ignore[invalid-argument-type]
        adjudicator=spec.adjudicator,  # ty: ignore[invalid-argument-type]
        max_pages=max_pages,
        transcription_modality=spec.transcription_modality,
    )
    irnode = source_document_to_ir_node(doc.root)
    manifest = {
        "source_digest": manifestation.artifact_digest,
        "source_locator": manifestation.locator,
        "source_role": manifestation.source_role,
        "media_type": manifestation.media_type,
        "pipeline_id": spec.pipeline_id,
        "pipeline_version": spec.version,
        "transcription_modality": spec.transcription_modality,
        "producers": ["vision", "reading_order"],
        "page_count": doc.page_count,
        "assurance_summary": _assurance_summary(doc.root),
        "composition_findings": list(doc.composition_findings)[:20],
        "parsed_at": (parsed_at or datetime.now(tz=timezone.utc)).isoformat(),
    }
    return ParsedRecord(ir=irnode.to_jsonable_dict(), manifest=manifest, cache_hit=False)


class ParsedIrStore:
    """A farchive of derived LawVM IR, content-addressed by source × pipeline."""

    def __init__(self, path: str = PARSED_STORE_DEFAULT) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def has(self, locator: str) -> bool:
        return self._fa.resolve(locator) is not None

    def get(self, locator: str) -> Optional[Dict[str, Any]]:
        span = self._fa.resolve(locator)
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def put(self, locator: str, record: ParsedRecord) -> str:
        data = _serialize_parsed_record(record.ir, record.manifest)
        return self._fa.store(
            locator,
            data,
            storage_class="parsed_ir",
            metadata={
                "source_digest": record.manifest.get("source_digest", ""),
                "source_locator": record.manifest.get("source_locator", ""),
                "pipeline_id": record.manifest.get("pipeline_id", ""),
                "pipeline_version": record.manifest.get("pipeline_version", ""),
            },
        )

    def close(self) -> None:
        self._fa.close()


def parse_and_cache(
    manifestation: SourceManifestation,
    store: ParsedIrStore,
    *,
    spec: Optional[PipelineSpec] = None,
    force: bool = False,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """Parse a PDF to LawVM IR, reusing the derived store when the key is present.

    Key = ``(digest, spec.pipeline_id, spec.version)``. A cache HIT returns the
    stored record (``cache_hit=True``); a MISS parses via the adjudicated route,
    stores, and returns it. ``spec`` defaults to ``resolve_pipeline()`` (which
    probes the LLM server once and RAISES if it is unreachable — pass a
    pre-resolved spec for a bulk run).
    """
    if spec is None:
        spec = resolve_pipeline()
    locator = parsed_ir_locator(manifestation.artifact_digest, spec.pipeline_id, spec.version)
    if not force:
        cached = store.get(locator)
        if cached is not None:
            return ParsedRecord(ir=cached["ir"], manifest=cached["manifest"], cache_hit=True)
    record = parse_pdf_to_ir(manifestation, spec, max_pages=max_pages, parsed_at=parsed_at)
    store.put(locator, record)
    return record
