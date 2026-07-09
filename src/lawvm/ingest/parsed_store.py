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
from typing import TYPE_CHECKING, Any, Dict, Optional

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import SourceDocumentNode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lawvm.core.source_document.adjudication import Adjudication
    from lawvm.core.source_document.anchors import SourceAnchor
    from lawvm.core.source_document.extraction import ExtractionAssertion
    from lawvm.ingest.defacsimile import DeFacsimileLedger
    from lawvm.ingest.page_elements import PageElementProducer
    from lawvm.ingest.simulacrum import PageSimulacrum, SpanRef

# Neutral default derived-IR store path. Jurisdiction callers pass their own
# path (FI uses ``lawvm.finland.source_document.FI_PARSED_STORE`` =
# ``data/fi_parsed_ir.farchive``); this default is intentionally generic so the
# store is not FI-anchored.
PARSED_STORE_DEFAULT = "data/parsed_ir.farchive"

# The one route: unified, LLM-orchestrated adjudication (the only route for PDFs).
ADJUDICATED_PIPELINE_ID = "adjudicated_vision"
ADJUDICATED_COMPOSE_VERSION = "compose.v1"

# Rasterization DPI for image-region crops with NO embedded XObject. Folded into
# the pipeline version so a crop re-rendered at a new DPI is a NEW content-
# addressed blob under a NEW path (coexists, exactly like an IR record). Kept in
# sync with ``page_elements.RASTERIZE_DPI``.
RASTERIZE_DPI = 200


def parsed_image_locator(source_digest: str, pipeline_id: str, version: str, blob_name: str) -> str:
    """Content-addressed image-blob key: same per-(source,pipeline,version) prefix as the IR.

    ``blob_name`` is the zero-padded ``{N}``-indexed blob (e.g. ``0003.png``) so
    an IR IMAGE node's ``I{N}`` reference maps 1:1 to its stored blob. The
    ``<pipeline>@<version>`` prefix versions rasterized crops for free.
    """
    return f"parsed/{source_digest}/{pipeline_id}@{version}/{blob_name}"


class ParseBackendUnavailable(RuntimeError):
    """The LLM parse backend is unreachable. Parsing REQUIRES it — fail loud."""


def parsed_ir_locator(source_digest: str, pipeline_id: str, version: str) -> str:
    """Content-addressed derived-store key: source digest × pipeline × version."""
    return f"parsed/{source_digest}/{pipeline_id}@{version}"


def page_simulacrum_locator(
    source_digest: str, pipeline_id: str, version: str, page_num: int
) -> str:
    """Content-addressed per-page simulacrum key (Decision 11 / §1 interface out).

    ``parsed/<digest>/<pipeline>@<version>/page/NNNN`` — the immutable Level-1
    EVIDENCE record for one page, under the SAME per-record prefix as the IR (so a
    Level-2 re-run reuses cached simulacra and NEVER re-runs the model). Mirrors
    ``parsed_image_locator`` (a sibling ``/page/`` namespace, zero-padded index)."""
    return f"parsed/{source_digest}/{pipeline_id}@{version}/page/{page_num:04d}"


def defacsimile_ledger_locator(source_digest: str, pipeline_id: str, version: str) -> str:
    """Sibling blob key for the Level-2 de-facsimile ledger (Decision 5).

    Shares the IR's per-record ``parsed/<digest>/<pipeline>@<version>/`` prefix so
    the full ledger JSON coexists with the IR under one content-addressed record
    (the manifest carries only histograms + this locator/digest, not the claims).
    """
    return f"parsed/{source_digest}/{pipeline_id}@{version}/defacsimile_ledger.json"


def _serialize_parsed_record(ir_dict: Dict[str, Any], manifest: Dict[str, Any]) -> bytes:
    """Serialize {ir, manifest} to deterministic JSON bytes (sorted keys)."""
    payload = {"ir": ir_dict, "manifest": manifest}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _spanref_to_jsonable(ref: "SpanRef") -> Dict[str, Any]:
    return {"page_num": ref.page_num, "node_path": list(ref.node_path)}


def serialize_defacsimile_ledger(ledger: "DeFacsimileLedger") -> bytes:
    """Full ledger → deterministic sorted-keys JSON bytes (Decision 5).

    Every claim's op / targets / tier / corroborating producers / absorbed /
    method / rationale is preserved verbatim so the ledger is reversible against
    the immutable simulacra — the sibling blob IS the audit record.
    """
    claims = [
        {
            "op": claim.op.value,
            "targets": [_spanref_to_jsonable(t) for t in claim.targets],
            "tier": claim.tier.value,
            "corroborating_producers": list(claim.corroborating_producers),
            "absorbed": [_spanref_to_jsonable(a) for a in claim.absorbed],
            "method": claim.method,
            "rationale": claim.rationale,
        }
        for claim in ledger.claims
    ]
    return json.dumps({"claims": claims}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def deserialize_defacsimile_ledger(data: bytes) -> "DeFacsimileLedger":
    """Round-trip a serialized ledger blob back to a ``DeFacsimileLedger``."""
    from lawvm.core.source_document.ir import AssuranceTier
    from lawvm.ingest.defacsimile import (
        DeFacsimileClaim,
        DeFacsimileLedger,
        DeFacsimileOp,
    )
    from lawvm.ingest.simulacrum import SpanRef

    payload = json.loads(data.decode("utf-8"))

    def _ref(d: Dict[str, Any]) -> SpanRef:
        return SpanRef(page_num=int(d["page_num"]), node_path=tuple(d["node_path"]))

    claims = tuple(
        DeFacsimileClaim(
            op=DeFacsimileOp(c["op"]),
            targets=tuple(_ref(t) for t in c["targets"]),
            tier=AssuranceTier(c["tier"]),
            corroborating_producers=tuple(c["corroborating_producers"]),
            absorbed=tuple(_ref(a) for a in c.get("absorbed", ())),
            method=c.get("method", "model_adjudicated"),
            rationale=c.get("rationale", ""),
        )
        for c in payload.get("claims", ())
    )
    return DeFacsimileLedger(claims=claims)


def defacsimile_manifest_summary(ledger: "DeFacsimileLedger") -> Dict[str, Any]:
    """Manifest fields for a ledger (Decision 5): op/tier histograms + SW-drop count.

    The manifest carries ONLY the histograms + the SINGLE_WITNESS-drop count (and,
    stitched by ``put_ledger``, the blob locator + digest) — never the claims
    themselves (those live in the sibling blob).
    """
    op_hist: Dict[str, int] = {}
    tier_hist: Dict[str, int] = {}
    single_witness_drops = 0
    for claim in ledger.claims:
        op_hist[claim.op.value] = op_hist.get(claim.op.value, 0) + 1
        tier_hist[claim.tier.name] = tier_hist.get(claim.tier.name, 0) + 1
        if (
            claim.op.value in ("drop_furniture", "dedup_seam")
            and claim.tier.name == "SINGLE_WITNESS"
        ):
            single_witness_drops += 1
    return {
        "op_histogram": op_hist,
        "tier_histogram": tier_hist,
        "single_witness_drop_count": single_witness_drops,
        "claim_count": len(ledger.claims),
    }


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
    # present only for struct_* lanes (a PageElementProducer); None for flat lanes.
    page_element_producer: "Optional[PageElementProducer]" = None


class _TolerantVision:
    """Vision producer wrapper: a page too dense for the token budget yields no
    vision witness for that page (its tier degrades) instead of aborting the run.
    This is a per-PAGE tier degradation WITHIN the adjudicated route, not a
    route switch."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def is_available(self) -> bool:
        return self._inner.is_available()  # ty: ignore[unresolved-attribute]

    def propose_page_struct(
        self, manifestation: SourceManifestation, page_num: int, page_elements: Any, *, leaf_mode: str = "span"
    ) -> Any:
        """Forward the v2 build-script call; a page too dense to build within the
        token budget yields an EMPTY forest for that page (its tier degrades)
        rather than aborting the whole document."""
        from lawvm.ingest.llm_backends.vision_producer import (
            StructPageResult,
            VisionProducerTruncated,
        )
        from lawvm.ingest.struct_wire import StructBuildResult

        try:
            return self._inner.propose_page_struct(  # ty: ignore[unresolved-attribute]
                manifestation, page_num, page_elements, leaf_mode=leaf_mode
            )
        except VisionProducerTruncated:
            # The page's structure is lost (tier degrades), but its images come
            # from the DETERMINISTIC page-element enumeration, not the model —
            # preserve them so content-addressing survives a dense/truncated page.
            return StructPageResult(
                build=StructBuildResult(roots=()), raw_content="", images=page_elements.images
            )


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
        from lawvm.ingest.llm_backends.llm_adjudicator import AdjudicationTruncated

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


# Modality → version-string tag. All lanes share ONE build-script grammar
# (``struct_wire``); ``transcription_modality`` selects only how a TEXT LEAF is
# populated — ``struct_span`` uses ``L{N}`` reading-order refs (span-copied by
# code), ``struct_full`` uses inline ``T:`` model transcription, ``struct_auto``
# picks per page, and ``struct_patch`` span-copies plus addressed ``PATCH``
# deltas. Structure, tables, and images (``I{N}``, content-addressed) are
# identical across all. Each lane's records COEXIST under a DISTINCT tag; the
# rasterization DPI is folded in so a crop re-rendered at a new DPI writes under a
# NEW path.
_STRUCT_WIRE_TAG = f"+wire=structbuild.v1+rasterdpi={RASTERIZE_DPI}"
# The Level-1 patch-to-convergence lane (Track B): the struct_patch build-script
# grammar PLUS the closed-gate refine loop (Decision 2), text-PATCH fixpoint
# (Decisions 1/10), max_iters=4. Its records are per-PAGE simulacra
# (``page_simulacrum_locator``) — a DISTINCT modality from the composed IR lanes.
_STRUCT_CONVERGE_TAG = (
    "+wire=structbuild.v1"
    "+leaf=patch+converge.v1+gate=hard.v1+iters=4+structpatch=text.v1"
    f"+rasterdpi={RASTERIZE_DPI}"
)
_MODALITY_VERSION_TAG = {
    "struct_span": _STRUCT_WIRE_TAG + "+leaf=span",
    "struct_full": _STRUCT_WIRE_TAG + "+leaf=full",
    "struct_auto": _STRUCT_WIRE_TAG + "+leaf=auto",
    "struct_patch": _STRUCT_WIRE_TAG + "+leaf=patch",
    "struct_converge": _STRUCT_CONVERGE_TAG,
}

# The build-script lanes (share one grammar; differ in leaf-content source).
STRUCT_BUILD_MODALITIES = (
    "struct_span",
    "struct_full",
    "struct_auto",
    "struct_patch",
    "struct_converge",
)

# Per-lane leaf-content source (what a TEXT LEAF's ``.text`` resolves from).
# ``struct_converge`` reuses the patch leaf source and adds the refine loop.
STRUCT_LEAF_SOURCE = {
    "struct_span": "span",
    "struct_full": "inline",
    "struct_auto": "auto",
    "struct_patch": "patch",
    "struct_converge": "patch",
}


def resolve_pipeline(
    *,
    vision_max_tokens: int = 3000,
    adjudicator_max_tokens: int = 2000,
    transcription_modality: str = "struct_span",
) -> PipelineSpec:
    """Resolve the adjudicated parse route. RAISES ``ParseBackendUnavailable`` if
    the LLM server is unreachable — parsing requires it, there is no fallback.
    Probes the server ONCE; reuse the returned spec across a bulk run.

    ``transcription_modality`` (``struct_span`` | ``struct_full`` |
    ``struct_auto`` | ``struct_patch``) picks the build-script leaf-content lane
    (see ``adjudicated_ingest``) and is folded into the pipeline VERSION so each
    lane's records are separately content-addressed.
    """
    from lawvm.ingest.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator
    from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer

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
    page_producer: object = None
    if transcription_modality in STRUCT_BUILD_MODALITIES:
        from lawvm.ingest.page_elements import PageElementProducer

        page_producer = PageElementProducer(rasterize_dpi=RASTERIZE_DPI)
    return PipelineSpec(
        pipeline_id=ADJUDICATED_PIPELINE_ID,
        version=version,
        vision=_TolerantVision(vision),
        adjudicator=_TolerantAdjudicator(adjudicator),
        transcription_modality=transcription_modality,
        page_element_producer=page_producer,
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


def _inject_image_locators(
    root: SourceDocumentNode, source_digest: str, pipeline_id: str, version: str
) -> SourceDocumentNode:
    """Re-emit the tree with each IMAGE node's ``image_locator`` attr set.

    The blob shares the IR's per-record prefix and is keyed by the ``{N}`` element
    id (``image_index``) as a zero-padded blob name, so the IR IMAGE node's
    ``I{N}`` reference maps 1:1 to its stored blob (``parsed_image_locator``).
    """
    from lawvm.core.source_document.ir import SourceDocumentNodeKind
    from lawvm.ingest.page_elements import image_blob_name

    def _walk(n: SourceDocumentNode) -> SourceDocumentNode:
        attrs = dict(n.attrs)
        if n.kind is SourceDocumentNodeKind.IMAGE_REGION and attrs.get("image_index"):
            blob_name = image_blob_name(int(attrs["image_index"]), attrs.get("media_type", ""))
            attrs["image_locator"] = parsed_image_locator(
                source_digest, pipeline_id, version, blob_name
            )
        return SourceDocumentNode(
            kind=n.kind,
            assurance_tier=n.assurance_tier,
            anchor=n.anchor,
            label=n.label,
            text=n.text,
            children=tuple(_walk(c) for c in n.children),
            attrs=attrs,
        )

    return _walk(root)


def parse_struct_pdf_to_ir(
    manifestation: SourceManifestation,
    spec: PipelineSpec,
    store: "ParsedIrStore",
    *,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """Parse a PDF through the v2 build-script lane → IR + provenance, storing image blobs.

    Runs ``struct_document_ingest`` (one build-script grammar; leaf-content source
    per ``spec.transcription_modality``), CONTENT-ADDRESSES every surfaced image
    blob into the derived store under the IR's per-record prefix (keyed by ``{N}``
    element id), stitches each IMAGE node's ``image_locator`` into the tree, and
    lowers to canonical IR. The manifest records the image inventory + build
    findings + 0x1F terminator-compliance stats.
    """
    from lawvm.ingest.adjudicated_ingest import struct_document_ingest
    from lawvm.ingest.lowering import source_document_to_ir_node
    from lawvm.ingest.page_elements import image_blob_name

    result = struct_document_ingest(
        manifestation,
        vision=spec.vision,
        page_element_producer=spec.page_element_producer,
        adjudicator=spec.adjudicator,  # ty: ignore[invalid-argument-type]
        max_pages=max_pages,
        transcription_modality=spec.transcription_modality,
    )
    # Content-address every image blob under the IR's per-record prefix.
    image_manifest: list = []
    for img in result.images:
        e = img.element  # ty: ignore[unresolved-attribute]
        blob_name = image_blob_name(e.index, e.media_type)
        locator = parsed_image_locator(
            manifestation.artifact_digest, spec.pipeline_id, spec.version, blob_name
        )
        store.put_image(
            locator,
            img.raw_bytes,  # ty: ignore[unresolved-attribute]
            source_digest=manifestation.artifact_digest,
            media_type=e.media_type,
            bit_exact_source=img.bit_exact_source,  # ty: ignore[unresolved-attribute]
        )
        image_manifest.append(
            {
                "index": e.index,
                "digest": e.digest,
                "locator": locator,
                "media_type": e.media_type,
                "px_width": e.width,
                "px_height": e.height,
                "bbox": list(e.bbox),
                "role": e.role,
                "bit_exact_source": img.bit_exact_source,  # ty: ignore[unresolved-attribute]
            }
        )
    stitched = _inject_image_locators(
        result.document.root, manifestation.artifact_digest, spec.pipeline_id, spec.version
    )
    irnode = source_document_to_ir_node(stitched)
    terminated, total = result.terminator_stats
    manifest = {
        "source_digest": manifestation.artifact_digest,
        "source_locator": manifestation.locator,
        "source_role": manifestation.source_role,
        "media_type": manifestation.media_type,
        "pipeline_id": spec.pipeline_id,
        "pipeline_version": spec.version,
        "transcription_modality": spec.transcription_modality,
        "producers": ["vision_struct", "reading_order", "page_elements"],
        "page_count": result.document.page_count,
        "assurance_summary": _assurance_summary(stitched),
        "composition_findings": list(result.document.composition_findings)[:20],
        "struct_findings": list(result.struct_findings)[:40],
        "image_manifest": image_manifest,
        "terminator_compliance": {
            "terminated": terminated,
            "total": total,
            "rate": (terminated / total) if total else None,
        },
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

    def put_image(
        self,
        locator: str,
        data: bytes,
        *,
        source_digest: str,
        media_type: str,
        bit_exact_source: bool,
    ) -> str:
        """Store one content-addressed image blob under the IR's per-record prefix.

        The blob shares the ``parsed/<source_digest>/<pipeline>@<version>/`` prefix
        with the IR (see ``parsed_image_locator``) and is indexed by its
        zero-padded ``{N}`` element id so an IMAGE node's ``I{N}`` ref maps 1:1.
        ``bit_exact_source`` distinguishes an embedded XObject (True; losslessly
        re-derivable) from a rasterized crop (False; digest depends on bbox+DPI).
        """
        return self._fa.store(
            locator,
            data,
            storage_class="source_image",
            metadata={
                "source_digest": source_digest,
                "media_type": media_type,
                "bit_exact_source": "1" if bit_exact_source else "0",
            },
        )

    def get_image(self, locator: str) -> Optional[bytes]:
        """Read one stored image blob's raw bytes (``None`` if absent)."""
        span = self._fa.resolve(locator)
        if span is None:
            return None
        return self._fa.read(span.digest)

    def put_page_simulacrum(
        self, locator: str, sim: "PageSimulacrum", *, source_digest: str
    ) -> str:
        """Persist one immutable per-page ``PageSimulacrum`` evidence record (Decision 11).

        Stored under a ``page_simulacrum`` storage class at ``page_simulacrum_locator``
        (sibling ``/page/NNNN`` under the IR record prefix). The payload is the
        round-trippable ``page_simulacrum_to_json`` blob (sorted-keys JSON) so a
        Level-2 re-run reuses the cached simulacrum and NEVER re-runs the model."""
        from lawvm.ingest.page_level import page_simulacrum_to_json

        data = json.dumps(
            page_simulacrum_to_json(sim), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        return self._fa.store(
            locator,
            data,
            storage_class="page_simulacrum",
            metadata={
                "source_digest": source_digest,
                "page_num": str(sim.page_num),
            },
        )

    def get_page_simulacrum(self, locator: str) -> "Optional[PageSimulacrum]":
        """Read + reconstruct one stored ``PageSimulacrum`` (``None`` if absent)."""
        span = self._fa.resolve(locator)
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        from lawvm.ingest.page_level import page_simulacrum_from_json

        return page_simulacrum_from_json(json.loads(data.decode("utf-8")))

    def put_ledger(
        self,
        locator: str,
        ledger: "DeFacsimileLedger",
        *,
        source_digest: str,
    ) -> str:
        """Store the full de-facsimile ledger as a sibling blob (Decision 5).

        Mirrors ``put_image``: the sorted-keys JSON ledger lands under the IR's
        per-record ``parsed/<digest>/<pipeline>@<version>/`` prefix as a
        ``defacsimile_ledger`` storage class. Returns the blob digest so the
        manifest can carry the blob locator + digest (the manifest itself keeps
        only histograms — ``defacsimile_manifest_summary``). ``verify_ledger`` gates
        the WRITE at the call site (a record is never emitted with an unverified
        ledger).
        """
        data = serialize_defacsimile_ledger(ledger)
        return self._fa.store(
            locator,
            data,
            storage_class="defacsimile_ledger",
            metadata={
                "source_digest": source_digest,
                "claim_count": str(len(ledger.claims)),
            },
        )

    def get_ledger(self, locator: str) -> "Optional[DeFacsimileLedger]":
        """Read a stored de-facsimile ledger blob (``None`` if absent)."""
        span = self._fa.resolve(locator)
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return deserialize_defacsimile_ledger(data)

    def close(self) -> None:
        self._fa.close()


def parse_struct_and_cache(
    manifestation: SourceManifestation,
    store: ParsedIrStore,
    *,
    spec: Optional[PipelineSpec] = None,
    transcription_modality: str = "struct_span",
    force: bool = False,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """v2 build-script parse to LawVM IR + image blobs, cached by the struct key.

    Key = ``(digest, pipeline_id, struct-version)`` — DISTINCT from the flat-lane
    keys, so the structured records COEXIST with v1 span / full / auto. A cache
    HIT returns the stored record; a MISS runs ``parse_struct_pdf_to_ir`` (which
    content-addresses the image blobs under the same per-record prefix). ``spec``
    defaults to a struct pipeline resolved for ``transcription_modality``.
    """
    if spec is None:
        spec = resolve_pipeline(transcription_modality=transcription_modality)
    if spec.transcription_modality not in STRUCT_BUILD_MODALITIES:
        raise ValueError(
            f"parse_struct_and_cache requires a struct_* modality; got "
            f"{spec.transcription_modality!r}"
        )
    locator = parsed_ir_locator(manifestation.artifact_digest, spec.pipeline_id, spec.version)
    if not force:
        cached = store.get(locator)
        if cached is not None:
            return ParsedRecord(ir=cached["ir"], manifest=cached["manifest"], cache_hit=True)
    record = parse_struct_pdf_to_ir(
        manifestation, spec, store, max_pages=max_pages, parsed_at=parsed_at
    )
    store.put(locator, record)
    return record
