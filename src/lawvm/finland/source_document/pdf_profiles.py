"""Native PDF source-document profile (D2 + D3 e2e slice).

Runs pdfplumber as ONE candidate producer for the ``native_pdf`` lane: a
``SourceManifestation`` (PDF bytes) is lowered into a ``SourceDocumentIR`` tree
of SINGLE_WITNESS nodes with real per-region anchors, plus typed ``Residual``\\ s
for pages pdfplumber could not own. pdfplumber is not privileged — it is a lone
witness; a lone vision read is equally single-witness. Higher assurance comes
only from an ``Adjudicator`` reconciling several producers
(``lawvm.core.source_document.adjudication``), not from this lane.

This is the determinism firewall made concrete: every page ends up OWNED or
RESIDUAL, never silently dropped (AGENTS.md §0, §1.8). The vision proposal lane
is optional (D4) and degrades gracefully, so the pipeline runs complete with
every model backend off (review §1.3).

Discipline (AGENTS.md §1.9, §1.10): typed frozen carriers; pdfplumber failure
is a typed residual, never a swallowed exception.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode

# Optional farchive support for loading SourceManifestation from the provided
# locators (finlex.farchive media for attachments/corrigenda, gov proposal farchive
# for HE related). This is the "means to find them" for the three targets.
try:
    import farchive as _farchive_mod
except Exception:  # lawvm-failloud: optional farchive backend absent → None sentinel, ingest degrades gracefully
    _farchive_mod = None  # type: ignore

from lawvm.core.provenance_graph import Producer
from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.coverage import RegionOwnership, Residual, ResidualFamily
from lawvm.core.source_document.extraction import ExtractionRun, SourceManifestation
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.finland.pdf_layout import (
    AttachmentLayout,
    BodyBlock,
    ExtractedTable,
    Footnote,
    TableCell,
    extract_pdf_layout,
)

# For better structured SourceDocumentIR on attachment/statute PDFs and HE,
# reuse the classification logic from attachment parsing (OSA, Liite, § etc.).
# These are safe classifier regexes.
from lawvm.finland.attachment_ir import (
    _is_caps_heading,
    _LIITE_RE,
    _OSA_RE,
    _PARA_NUM_RE,
)

def load_manifestation_from_farchive(
    locator: str,
    *,
    farchive_path: str = "data/finlex.farchive",
    source_role: str = "attachment",
) -> SourceManifestation:
    """Load SourceManifestation from farchive locator (the provided means).

    Supports the targets:
    - attachments / corrigenda: finlex.farchive media/...pdf locators
    - statute PDF bodies: certain sd-cons media or full content PDFs
    - draft HE: when PDFs are present in fi_government_proposal.farchive or user
      can pass bytes from the lausunnot he_luonnos PDFs.

    Uses the exact resolve + digest + read pattern from the handoff.
    """
    if _farchive_mod is None:
        raise RuntimeError("farchive not available; cannot load by locator")
    fa = _farchive_mod.Farchive(farchive_path)
    span = fa.resolve(locator)
    if span is None:
        raise ValueError(f"locator not resolvable in farchive: {locator}")
    b = fa.read(span.digest)
    if not b:
        raise ValueError(f"empty bytes for {locator}")
    dig = hashlib.sha256(b).hexdigest()
    return SourceManifestation(
        artifact_digest=dig,
        source_bytes=b,
        locator=locator,
        source_role=source_role,
        fetched_at=datetime.now(tz=timezone.utc),
        media_type="application/pdf",
    )

_NATIVE_PDF_PRODUCER = Producer(
    producer_id="native_pdf",
    producer_kind="script",
)
_BACKEND_VERSION = "pdfplumber:0.11"


@dataclass(frozen=True, slots=True)
class PdfIngestResult:
    """Native-PDF (pdfplumber) ingest output — one candidate producer's read.

    Every node is SINGLE_WITNESS. Residuals are the pages pdfplumber could not
    own (image-only / scanned pages need a vision or OCR producer). An
    ``Adjudicator`` over several producers is what raises assurance.
    """

    root: SourceDocumentNode
    residuals: tuple[Residual, ...]
    run: ExtractionRun
    page_count: int
    """Total pages pdfplumber saw; owned ∪ residual covers 1..page_count (§1.8)."""


def _anchor(
    manifestation: SourceManifestation, locator: str, *, page_num: int | None = None
) -> SourceAnchor:
    return SourceAnchor(
        artifact_digest=manifestation.artifact_digest,
        locator=locator,
        page_num=page_num,
    )


def _body_block_node(manifestation: SourceManifestation, block: BodyBlock) -> SourceDocumentNode:
    """Classify body block into better SourceDocumentNodeKind for structure.

    Uses the same drafting idioms as attachment_ir (OSA, Liite, numbered paras,
    ALL-CAPS headings) so that SourceDocumentIR for attachment/statute PDFs
    and draft HE is not completely flat. This helps lowering to useful IR
    and HE segmentation.
    """
    text = block.text or ""
    stripped = text.strip()

    kind = SourceDocumentNodeKind.PARAGRAPH
    label = None

    if _OSA_RE.match(stripped):
        kind = SourceDocumentNodeKind.CHAPTER
        m = _OSA_RE.match(stripped)
        label = f"osa_{m.group(1)}" if m else None
    elif _LIITE_RE.match(stripped):
        kind = SourceDocumentNodeKind.PROPOSAL_SECTION  # or CHAPTER for attachments
        m = _LIITE_RE.match(stripped)
        label = f"liite_{m.group(1)}" if m else None
    elif _PARA_NUM_RE.match(stripped):
        kind = SourceDocumentNodeKind.PARAGRAPH
        m = _PARA_NUM_RE.match(stripped)
        label = m.group(1) if m else None
    elif _is_caps_heading(stripped):
        kind = SourceDocumentNodeKind.HEADING

    # For draft HE, rough signals for bill text area (lakiehdotus often has "Laki ..." or changes)
    if "lakiehdotus" in stripped.lower() or stripped.startswith("Laki "):
        kind = SourceDocumentNodeKind.BILL_TEXT

    return SourceDocumentNode(
        kind=kind,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(
            manifestation,
            f"page={block.page_num};y={block.y_position:.1f}",
            page_num=block.page_num,
        ),
        label=label,
        text=block.text,
    )


def _table_node(
    manifestation: SourceManifestation, table: ExtractedTable, index: int
) -> SourceDocumentNode:
    cells_by_row: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        cells_by_row.setdefault(cell.row, []).append(cell)
    row_nodes: tuple[SourceDocumentNode, ...] = tuple(
        SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE_ROW,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=_anchor(
                manifestation,
                f"page={table.page_num};table={index};row={row_idx}",
                page_num=table.page_num,
            ),
            children=tuple(
                SourceDocumentNode(
                    kind=SourceDocumentNodeKind.TABLE_CELL,
                    assurance_tier=AssuranceTier.SINGLE_WITNESS,
                    anchor=_anchor(
                        manifestation,
                        f"page={table.page_num};table={index};row={c.row};col={c.col}",
                        page_num=table.page_num,
                    ),
                    text=c.text,
                    attrs={
                        "rowspan": str(c.rowspan),
                        "colspan": str(c.colspan),
                        "is_header": "1" if c.is_header else "0",
                    },
                )
                for c in sorted(cells_by_row[row_idx], key=lambda cell: cell.col)
            ),
        )
        for row_idx in sorted(cells_by_row)
    )
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(
            manifestation, f"page={table.page_num};table={index}", page_num=table.page_num
        ),
        text=table.caption,
        children=row_nodes,
    )


def _footnote_node(manifestation: SourceManifestation, fn: Footnote) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.FOOTNOTE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(
            manifestation,
            f"page={fn.page_num};footnote={fn.marker}",
            page_num=fn.page_num,
        ),
        label=fn.marker,
        text=fn.text,
    )


def _owned_pages(layout: AttachmentLayout) -> set[int]:
    pages = {block.page_num for block in layout.body_blocks}
    pages.update(table.page_num for table in layout.tables)
    pages.update(fn.page_num for fn in layout.footnotes)
    return pages


def _residuals_for_unowned_pages(
    manifestation: SourceManifestation, layout: AttachmentLayout, processed_pages: int
) -> List[Residual]:
    """Typed residual for every IN-SCOPE page pdfplumber extracted no content from.

    ``processed_pages`` is the accounting scope — the page range actually
    processed (capped by ``max_pages``), NOT the full document page count.
    Total accounting (§1.8) applies to the scope: every in-scope page with no
    owned body/table/footnote content is RESIDUAL. Pages beyond ``max_pages``
    are out of scope (not accounted, not claimed) — honest about the cap. This
    slice labels unowned pages ``PDF_TEXT_LAYER_EMPTY``; the image-only-vs-empty
    refinement is D3.
    """
    if processed_pages <= 0:
        return []
    owned = _owned_pages(layout)
    residuals: List[Residual] = []
    for page in range(1, processed_pages + 1):
        if page in owned:
            continue
        residuals.append(
            Residual(
                family=ResidualFamily.PDF_TEXT_LAYER_EMPTY,
                ownership=RegionOwnership.RESIDUAL,
                anchor=_anchor(manifestation, f"page={page}", page_num=page),
                snippet="",
                detail="pdfplumber extracted no text/table content for this page",
            )
        )
    return residuals


def _node_to_canonical(node: SourceDocumentNode) -> object:
    return {
        "kind": str(node.kind),
        "label": node.label,
        "text": node.text,
        "tier": str(node.assurance_tier),
        "children": [_node_to_canonical(child) for child in node.children],
    }


def _output_digest(root: SourceDocumentNode, residuals: tuple[Residual, ...]) -> str:
    """SHA-256 over a canonical projection of the produced artifact.

    This is artifact reproducibility (the run record digests what it produced),
    not model-rerun determinism — see D8.
    """
    payload = json.dumps(
        {
            "root": _node_to_canonical(root),
            "residuals": [
                {"family": str(r.family), "ownership": str(r.ownership), "page": r.anchor.page_num}
                for r in residuals
            ],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingest_pdf_manifestation(
    manifestation: SourceManifestation,
    *,
    max_pages: int = 5000,
) -> PdfIngestResult:
    """Ingest a PDF manifestation via the pdfplumber producer.

    pdfplumber is one candidate producer; every node is SINGLE_WITNESS. Pages it
    cannot own become typed residuals (image-only / scanned pages need a vision
    or OCR producer, added when that case is tackled). An ``Adjudicator`` over
    several producers is what raises assurance — not this lane.
    """
    started = datetime.now(tz=timezone.utc)
    layout = extract_pdf_layout(manifestation.source_bytes, max_pages=max_pages)
    ended = datetime.now(tz=timezone.utc)

    if layout is None:
        root = SourceDocumentNode(
            kind=SourceDocumentNodeKind.WORK_ROOT,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=_anchor(manifestation, "manifestation"),
            children=(),
        )
        residuals: tuple[Residual, ...] = (
            Residual(
                family=ResidualFamily.PDF_TEXT_LAYER_EMPTY,
                ownership=RegionOwnership.BLOCKED,
                anchor=_anchor(manifestation, "manifestation"),
                detail="pdfplumber unavailable or PDF unparseable "
                "(extract_pdf_layout returned None)",
            ),
        )
        page_count = 0
    else:
        body = tuple(_body_block_node(manifestation, block) for block in layout.body_blocks)
        tables = tuple(
            _table_node(manifestation, table, i) for i, table in enumerate(layout.tables)
        )
        footnotes = tuple(_footnote_node(manifestation, fn) for fn in layout.footnotes)
        root = SourceDocumentNode(
            kind=SourceDocumentNodeKind.WORK_ROOT,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=_anchor(manifestation, "manifestation"),
            children=body + tables + footnotes,
        )
        processed_pages = min(layout.page_count, max_pages)
        residuals = tuple(_residuals_for_unowned_pages(manifestation, layout, processed_pages))
        page_count = processed_pages

    output_digest = _output_digest(root, residuals)
    run = ExtractionRun(
        run_id=f"native_pdf:{manifestation.artifact_digest[:16]}:{output_digest[:16]}",
        producer=_NATIVE_PDF_PRODUCER,
        backend_id="native_pdf",
        backend_version=_BACKEND_VERSION,
        source_artifact_digest=manifestation.artifact_digest,
        input_affordance_digest=manifestation.artifact_digest,
        output_digest=output_digest,
        started_at=started,
        ended_at=ended,
    )

    return PdfIngestResult(
        root=root,
        residuals=residuals,
        run=run,
        page_count=page_count,
    )


# ---------------------------------------------------------------------------
# Lowering to LawVM IR (the "into structured stuff in lawvm IR" target)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Draft HE support (PDF priority; DOCX later)
# Minimal extractor for lakiehdotus / bill text portions inside a government
# proposal PDF. The output feeds ProposalPackage / ConditionalBranch (replay
# authorized = false until enacted).
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HeDraftProposal:
    """Very small ProposalPackage sketch for a draft HE.

    In real use this would contain the full candidate ops, branch id, etc.
    Here we surface the addressable SourceDocumentIR subtree(s) that look like
    the normative bill text, plus authority info.
    """

    source_manifestation_digest: str
    bill_text_roots: tuple[SourceDocumentNode, ...]
    authority_status: str = "consultation_draft"
    replay_authorized: bool = False


def _walk_sd(node: SourceDocumentNode):
    """Yield node and descendants."""
    yield node
    for ch in node.children:
        yield from _walk_sd(ch)


def extract_he_draft_proposal(root: SourceDocumentNode) -> HeDraftProposal:
    """Heuristic scan of a SourceDocumentIR for the 'lakiehdotus' / bill text.

    For draft HE PDFs (from lausunnot he_luonnos or gov proposal farchive),
    finds the normative proposal part (lakiehdotus) which contains the
    text that would become statute amendments.

    Returns subtrees suitable for lowering to ProposalPackage / candidate
    ops (replay_authorized=False).
    """
    candidates: list[SourceDocumentNode] = []
    in_lakiehdotus = False

    for node in _walk_sd(root):
        t = (node.text or "").strip().lower()
        if not t:
            continue
        # Start of the actual bill proposal section in Finnish HE drafts
        if "lakiehdotus" in t or t.startswith("laki ") and "muutetaan" in t:
            in_lakiehdotus = True
        if in_lakiehdotus or "lakiehdotus" in t:
            if node.kind in (SourceDocumentNodeKind.PARAGRAPH, SourceDocumentNodeKind.BILL_TEXT, SourceDocumentNodeKind.ITEM, SourceDocumentNodeKind.TABLE):
                candidates.append(node)
            # Stop at next major TOC-like or perustelut end if seen
            if t.startswith("perustelut") or len(candidates) > 100:
                break

    if not candidates:
        # fallback for cases without explicit label: last substantial content
        # (often the proposed text is at end of luonnos)
        paras = [
            n for n in _walk_sd(root)
            if n.kind in (SourceDocumentNodeKind.PARAGRAPH, SourceDocumentNodeKind.BILL_TEXT)
            and len((n.text or "").strip()) > 30
        ]
        if paras:
            candidates = paras[-5:]

    return HeDraftProposal(
        source_manifestation_digest=root.anchor.artifact_digest if root.anchor else "",
        bill_text_roots=tuple(candidates),
    )
