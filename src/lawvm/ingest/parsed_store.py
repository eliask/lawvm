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
    from lawvm.ingest.blackboard import Workspace
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


def parsed_ir_locator(
    source_digest: str,
    pipeline_id: str,
    version: str,
    max_pages: Optional[int] = None,
) -> str:
    """Content-addressed derived-store key: source digest × pipeline × version.

    ``max_pages`` MUST be part of the key: a parse bounded to N pages yields a
    DIFFERENT reconstruction than one bounded to M, so two callers with different
    page budgets must not collide on one cache entry (that produced cross-run
    stale reads — a 6-page census entry served to a 10-page measurement run,
    flipping the derived stratum). ``None`` keeps the legacy un-suffixed key for
    callers that do not bound pages.
    """
    base = f"parsed/{source_digest}/{pipeline_id}@{version}"
    return base if max_pages is None else f"{base}#p{max_pages}"


# Bump when the A/B predicate or the diff adjudicator's identity changes, so a
# stored verdict from an older evaluator does not shadow a fresh re-evaluation.
_AB_EVAL_VERSION = "v1"


def defacsimile_ab_locator(
    source_digest: str, pipeline_id: str, version: str, gold_digest: str
) -> str:
    """Content-addressed key for the persisted de-facsimile A/B benchmark verdict.

    A sibling of :func:`parsed_ir_locator` under the SAME per-record prefix, further
    keyed by the XML GOLD digest (replacing the gold invalidates the verdict) and an
    eval-version tag (an adjudicator/predicate change invalidates it). The verdict is
    a derived benchmark artifact of ``(source × pipeline × gold × evaluator)``, so a
    sweep can SKIP any member whose verdict already exists rather than re-running the
    two diff model calls — the output farchive IS the resume ledger (no side cache)."""
    return (
        f"parsed/{source_digest}/{pipeline_id}@{version}"
        f"/defacsimile_ab.{_AB_EVAL_VERSION}/{gold_digest}"
    )


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


def defacsimile_workspace_locator(source_digest: str, pipeline_id: str, version: str) -> str:
    """Sibling blob key for the Level-2 blackboard workspace journal (§7 / M3).

    Mirrors ``defacsimile_ledger_locator``: shares the IR's per-record
    ``parsed/<digest>/<pipeline>@<version>/`` prefix so the content-addressed
    workspace journal coexists with the ledger + IR under one record. Persisting it
    makes a blackboard run reproducible + auditable (the same simulacra ⇒ a
    byte-identical journal).
    """
    return f"parsed/{source_digest}/{pipeline_id}@{version}/defacsimile_workspace.json"


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
    # present only for the converged de-facsimile lane (the Level-2
    # ``DeFacsimileAdjudicator``); None everywhere else. When None on the
    # defacsimile lane, Level 2 degrades to the deterministic ``compose_pages``
    # fallback (Decision 8) — a typed method, not a route switch.
    defacsimile_adjudicator: object = None


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

    def appraise_page(
        self, manifestation: SourceManifestation, page_num: int, page_elements: Any
    ) -> Any:
        """Forward the cheap image-first appraisal; a truncated/failed appraisal
        degrades to "read the page, treat the lines as partial" (fail toward reading,
        never toward silently dropping a page)."""
        from lawvm.ingest.llm_backends.vision_producer import (
            PageAppraisal,
            VisionProducerFailure,
            VisionProducerTruncated,
        )

        try:
            return self._inner.appraise_page(  # ty: ignore[unresolved-attribute]
                manifestation, page_num, page_elements
            )
        except (VisionProducerTruncated, VisionProducerFailure):
            return PageAppraisal(has_content=True, kind="mixed", lines="partial", raw="")

    def propose_page_patch_delta(
        self, manifestation: SourceManifestation, page_num: int, numbered_lines: str
    ) -> str:
        """Forward one convergence refine round. Truncation is NOT swallowed here —
        ``converge_page`` catches ``VisionProducerTruncated`` to end that page's
        loop with ``termination="truncated"`` (an empty string is the model's
        CONVERGED signal, so the two must stay distinguishable)."""
        return self._inner.propose_page_patch_delta(  # ty: ignore[unresolved-attribute]
            manifestation, page_num, numbered_lines
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
# Reader-code fingerprint for the Level-1 converge lane. The scanned-page region
# reader (``ingest.visual.segment_page_regions`` + ``page_level.converge_page`` no-text-
# geometry routing) materially changes the reconstructed text but is NOT otherwise
# captured by the wire/leaf/gate version string — so without this a reader improvement
# would silently return STALE cached simulacra on a warm store. BUMP this whenever the
# region-read / scanned converge routing changes (invalidates struct_converge +
# defacsimile records; struct_span ``leaf=span`` records use a different tag, unaffected).
_DEFACSIMILE_READER_VERSION = "regionreader.v1"
_STRUCT_CONVERGE_TAG = (
    "+wire=structbuild.v1"
    "+leaf=patch+converge.v1+gate=hard.v1+iters=4+structpatch=text.v1+node.v1"
    f"+reader={_DEFACSIMILE_READER_VERSION}"
    f"+rasterdpi={RASTERIZE_DPI}"
)
# The converged two-level lane (Track B+C integration): Level-1 patch-to-convergence
# simulacra (``_STRUCT_CONVERGE_TAG``) FOLLOWED by the Level-2 holistic de-facsimile
# composer (Decision 11 version shape). The ``+defacsimile.v1+<adjudicator-id or
# "fallback">`` suffix is composed at ``resolve_pipeline`` time (the adjudicator model
# id is a runtime probe; when the L2 backend is down the lane degrades to the
# deterministic ``compose_pages`` fallback → the ``fallback`` id, Decision 8). This
# base tag omits the composer suffix; ``resolve_pipeline`` appends it.
_DEFACSIMILE_BASE_TAG = _STRUCT_CONVERGE_TAG
_MODALITY_VERSION_TAG = {
    "struct_span": _STRUCT_WIRE_TAG + "+leaf=span",
    "struct_full": _STRUCT_WIRE_TAG + "+leaf=full",
    "struct_auto": _STRUCT_WIRE_TAG + "+leaf=auto",
    "struct_patch": _STRUCT_WIRE_TAG + "+leaf=patch",
    "struct_converge": _STRUCT_CONVERGE_TAG,
    "defacsimile": _DEFACSIMILE_BASE_TAG,
}

# The build-script lanes (share one grammar; differ in leaf-content source). The
# converged ``defacsimile`` lane reuses the L1 patch simulacra then runs Level 2.
STRUCT_BUILD_MODALITIES = (
    "struct_span",
    "struct_full",
    "struct_auto",
    "struct_patch",
    "struct_converge",
    "defacsimile",
)

# The converged two-level lane whose parse path is Level-1 simulacra → Level-2
# de-facsimile (distinct from the single-level composed struct_* lanes).
DEFACSIMILE_MODALITY = "defacsimile"

# Per-lane leaf-content source (what a TEXT LEAF's ``.text`` resolves from).
# ``struct_converge`` / ``defacsimile`` reuse the patch leaf source (the L1 refine
# loop patches span-copied leaves against the page image).
STRUCT_LEAF_SOURCE = {
    "struct_span": "span",
    "struct_full": "inline",
    "struct_auto": "auto",
    "struct_patch": "patch",
    "struct_converge": "patch",
    "defacsimile": "patch",
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
    # The converged de-facsimile lane resolves a Level-2 ``DeFacsimileAdjudicator``
    # and composes the version tag with its model id (Decision 10). UNLIKE the L1
    # vision backend, the L2 adjudicator does NOT fail-loud when unreachable: Level 2
    # degrades gracefully to the deterministic ``compose_pages`` fallback (Decision
    # 8), so an absent L2 backend simply pins the ``fallback`` id into the version.
    defacsimile_adjudicator: object = None
    if transcription_modality == DEFACSIMILE_MODALITY:
        from lawvm.ingest.llm_backends.defacsimile_adjudicator import (
            DeFacsimileAdjudicator,
        )

        probe = DeFacsimileAdjudicator()
        if probe.is_available():
            defacsimile_adjudicator = probe
            l2_id = probe.adjudicator_id
        else:
            l2_id = "fallback"
        version = f"{version}+defacsimile.v1+{l2_id}"
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
        defacsimile_adjudicator=defacsimile_adjudicator,
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


def parse_defacsimile_pdf_to_ir(
    manifestation: SourceManifestation,
    spec: PipelineSpec,
    store: "ParsedIrStore",
    *,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """Parse a PDF through the CONVERGED two-level lane → IR + provenance (Track B+C).

    The end-to-end de-facsimile parse path (mirrors ``parse_struct_pdf_to_ir``):

    1. **Level 1** — ``reading_order_pages_from_pdf`` (the independent cross-witness)
       then ``build_page_simulacra`` (per-page gate + patch-to-convergence simulacra
       with metadata + the ``unwitnessed_content`` tripwire). Each immutable
       ``PageSimulacrum`` is persisted at ``page_simulacrum_locator`` so a Level-2
       re-run reuses the cached evidence and NEVER re-runs the vision model.
    2. **Level 2** — ``defacsimile`` folds the simulacra + the adjudicator's (or the
       deterministic ``compose_pages`` fallback's) verified ledger into one coherent
       whole-document tree. The ledger is persisted verbatim as a sibling blob at
       ``defacsimile_ledger_locator`` (Decision 5); the manifest carries only the
       op/tier histograms.
    3. Image blobs are content-addressed from the DETERMINISTIC page-element
       substrate (re-enumerated, no model) exactly like ``parse_struct_pdf_to_ir``,
       and each IMAGE node's ``image_locator`` is stitched into the composed tree.
    """
    from lawvm.core.source_document.anchors import SourceAnchor
    from lawvm.ingest.adjudicated_ingest import reading_order_pages_from_pdf
    from lawvm.ingest.defacsimile import defacsimile
    from lawvm.ingest.lowering import source_document_to_ir_node
    from lawvm.ingest.page_elements import image_blob_name
    from lawvm.ingest.page_level import build_page_simulacra

    digest = manifestation.artifact_digest
    ro_pages = reading_order_pages_from_pdf(manifestation.source_bytes, max_pages=max_pages)

    # -- Level 1: faithful per-page simulacra (persisted immutable evidence). --
    simulacra = build_page_simulacra(
        spec.vision,
        manifestation,
        spec.page_element_producer,
        ro_pages,
        adjudicator=spec.adjudicator,  # ty: ignore[invalid-argument-type]
        leaf_mode=STRUCT_LEAF_SOURCE[spec.transcription_modality],
        max_pages=max_pages,
    )
    for sim in simulacra:
        store.put_page_simulacrum(
            page_simulacrum_locator(digest, spec.pipeline_id, spec.version, sim.page_num),
            sim,
            source_digest=digest,
        )

    # -- Level 2: holistic de-facsimile → coherent tree + verified ledger. -----
    root_anchor = SourceAnchor(artifact_digest=digest, locator="manifestation")
    doc = defacsimile(simulacra, root_anchor, adjudicator=spec.defacsimile_adjudicator)

    # -- Image blobs: content-address the DETERMINISTIC page-element substrate. -
    # ``build_page_simulacra`` consumes the page elements internally and does not
    # surface their raw image bytes through the simulacra path (the simulacra nodes
    # carry only the image DIGEST/INDEX/media-type metadata, not bytes). Re-enumerate
    # the elements deterministically (pdfplumber, no model) to recover the bytes and
    # content-address them under the IR's per-record prefix — the same discipline
    # (and keyed by the same ``{N}`` element id) as ``parse_struct_pdf_to_ir``.
    image_manifest: list = []
    page_count = min(len(ro_pages), max_pages)
    producer = spec.page_element_producer
    seen_index: set = set()
    if producer is not None:
        for idx in range(page_count):
            page_num = idx + 1
            pe = producer.page_elements(manifestation.source_bytes, page_num)
            for img in pe.images:
                e = img.element
                if e.index in seen_index:
                    continue
                seen_index.add(e.index)
                blob_name = image_blob_name(e.index, e.media_type)
                locator = parsed_image_locator(digest, spec.pipeline_id, spec.version, blob_name)
                store.put_image(
                    locator,
                    img.raw_bytes,
                    source_digest=digest,
                    media_type=e.media_type,
                    bit_exact_source=img.bit_exact_source,
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
                        "bit_exact_source": img.bit_exact_source,
                    }
                )
    stitched = _inject_image_locators(doc.root, digest, spec.pipeline_id, spec.version)

    # -- Ledger: persist the full audit blob (verify_ledger already gated it). --
    ledger_locator = defacsimile_ledger_locator(digest, spec.pipeline_id, spec.version)
    ledger_digest = store.put_ledger(ledger_locator, doc.ledger, source_digest=digest)

    irnode = source_document_to_ir_node(stitched)
    ledger_summary = defacsimile_manifest_summary(doc.ledger)
    ledger_summary["ledger_locator"] = ledger_locator
    ledger_summary["ledger_digest"] = ledger_digest
    manifest = {
        "source_digest": digest,
        "source_locator": manifestation.locator,
        "source_role": manifestation.source_role,
        "media_type": manifestation.media_type,
        "pipeline_id": spec.pipeline_id,
        "pipeline_version": spec.version,
        "transcription_modality": spec.transcription_modality,
        "producers": ["vision_struct", "reading_order", "page_elements", "defacsimile"],
        "page_count": doc.page_count,
        "assurance_summary": _assurance_summary(stitched),
        "defacsimile_summary": ledger_summary,
        "image_manifest": image_manifest,
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

    def get_ab(self, locator: str) -> Optional[Dict[str, Any]]:
        """Read a persisted de-facsimile A/B verdict row (``None`` if absent)."""
        span = self._fa.resolve(locator)
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def put_ab(self, locator: str, row: Dict[str, Any]) -> str:
        """Persist one de-facsimile A/B verdict row (the sweep's resume ledger)."""
        return self._fa.store(
            locator,
            json.dumps(row, sort_keys=True).encode("utf-8"),
            storage_class="defacsimile_ab",
            metadata={"source_locator": str(row.get("pdf_locator", ""))},
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

    def put_workspace(
        self,
        locator: str,
        workspace: "Workspace",
        *,
        source_digest: str,
    ) -> str:
        """Store the blackboard workspace journal as a sibling blob (§7 / M3).

        Mirrors ``put_ledger``: the sorted-keys JSON journal
        (``serialize_workspace``) lands under the IR's per-record
        ``parsed/<digest>/<pipeline>@<version>/`` prefix as a
        ``defacsimile_workspace`` storage class. Returns the blob digest so the
        manifest can carry the locator + digest; the journal is content-addressed
        so a re-run over the same simulacra is a byte-identical HIT.
        """
        from lawvm.ingest.blackboard import serialize_workspace

        data = serialize_workspace(workspace)
        return self._fa.store(
            locator,
            data,
            storage_class="defacsimile_workspace",
            metadata={
                "source_digest": source_digest,
                "mark_count": str(len(workspace.marks)),
            },
        )

    def get_workspace(self, locator: str) -> "Optional[Workspace]":
        """Read + reconstruct a stored blackboard workspace journal (``None`` if absent)."""
        span = self._fa.resolve(locator)
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        from lawvm.ingest.blackboard import deserialize_workspace

        return deserialize_workspace(data)

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
    # The converged two-level lane routes to the de-facsimile parse path (Level-1
    # simulacra → Level-2 composer) — the single-level struct_* lanes stay here.
    if spec.transcription_modality == DEFACSIMILE_MODALITY:
        return parse_defacsimile_and_cache(
            manifestation, store, spec=spec, force=force, max_pages=max_pages,
            parsed_at=parsed_at,
        )
    locator = parsed_ir_locator(
        manifestation.artifact_digest, spec.pipeline_id, spec.version, max_pages=max_pages
    )
    if not force:
        cached = store.get(locator)
        if cached is not None:
            return ParsedRecord(ir=cached["ir"], manifest=cached["manifest"], cache_hit=True)
    record = parse_struct_pdf_to_ir(
        manifestation, spec, store, max_pages=max_pages, parsed_at=parsed_at
    )
    store.put(locator, record)
    return record


def parse_defacsimile_and_cache(
    manifestation: SourceManifestation,
    store: ParsedIrStore,
    *,
    spec: Optional[PipelineSpec] = None,
    force: bool = False,
    max_pages: int = 5000,
    parsed_at: Optional[datetime] = None,
) -> ParsedRecord:
    """Converged two-level parse to LawVM IR, cached by the de-facsimile key.

    Key = ``(digest, pipeline_id, defacsimile-version)`` where the version embeds
    the L1 ``struct_converge`` tag PLUS ``+defacsimile.v1+<adjudicator-id or
    "fallback">`` (Decision 10) — DISTINCT from every struct_* lane key, so the
    de-facsimile records COEXIST with the single-level records. A cache HIT returns
    the stored record byte-for-byte; a MISS runs ``parse_defacsimile_pdf_to_ir``
    (which persists the per-page simulacra + the verified ledger + the image blobs
    under the same per-record prefix). ``spec`` defaults to a pipeline resolved for
    the ``defacsimile`` modality (which probes the Level-2 adjudicator once).
    """
    if spec is None:
        spec = resolve_pipeline(transcription_modality=DEFACSIMILE_MODALITY)
    if spec.transcription_modality != DEFACSIMILE_MODALITY:
        raise ValueError(
            f"parse_defacsimile_and_cache requires the {DEFACSIMILE_MODALITY!r} "
            f"modality; got {spec.transcription_modality!r}"
        )
    locator = parsed_ir_locator(
        manifestation.artifact_digest, spec.pipeline_id, spec.version, max_pages=max_pages
    )
    if not force:
        cached = store.get(locator)
        if cached is not None:
            return ParsedRecord(ir=cached["ir"], manifest=cached["manifest"], cache_hit=True)
    record = parse_defacsimile_pdf_to_ir(
        manifestation, spec, store, max_pages=max_pages, parsed_at=parsed_at
    )
    store.put(locator, record)
    return record
