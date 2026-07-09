"""Docling structural producer — a learned-layout candidate witness (D4).

Docling (IBM's ``docling`` + TableFormer) reads a page with a LEARNED layout
model and a learned table-structure model. It is NOT "the parser": like
pdfplumber (geometric) and pypdfium2 (content-stream), it is one more
INDEPENDENT structural witness whose proposals feed the SAME producer-neutral
adjudication (``lawvm.core.source_document.adjudication``). Its distinctive
value is REAL TABLE STRUCTURE — TableFormer emits a cell grid, so a Docling
table lowers to a ``TABLE`` with true ``TABLE_ROW`` / ``TABLE_CELL`` children
(preserving each cell's text and a header flag), which the geometric and
content-stream producers cannot reconstruct. A different model, a different
failure mode: exactly what an adjudicator wants a second witness for.

Raw Docling output is ``SINGLE_WITNESS`` — it is ONE producer. Corroboration
(and therefore ``MULTI_WITNESS_ADJUDICATED``) happens in adjudication, never by
trusting a producer species (AGENTS.md §0; ``ir.AssuranceTier``). The producer
does not assign itself a higher tier.

Determinism firewall (AGENTS.md §1.3): the heavy ``docling`` import is LAZY —
never at module top — so this module imports with the backend absent, and the
whole pipeline runs offline with every model backend off. The
``DoclingDocument -> nodes`` conversion is factored into a PURE module-level
function (``docling_document_to_nodes``) over a minimal typed VIEW of a Docling
document, so the structural lowering is exercised by a fake WITHOUT the docling
dependency, network, or a real PDF.

Discipline (AGENTS.md §1.9, §1.10): typed frozen carriers; tuple children,
never list; a page Docling cannot read is a typed raise, never a silent empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Protocol, Sequence, Tuple

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)

# The governed leaf kinds a Docling element may lower to. A block label Docling
# emits that is not in this map lowers to PARAGRAPH (honest fallback, never a
# relabel of a governed kind and never a silent drop).
_HEADING_LABELS: frozenset[str] = frozenset(
    {"section_header", "title", "page_header", "heading", "subtitle"}
)
_FOOTNOTE_LABELS: frozenset[str] = frozenset({"footnote"})


# ---------------------------------------------------------------------------
# Minimal typed VIEW of a Docling document (the pure-seam boundary).
#
# The pure converter reads ONLY these tiny structures, never the real
# ``docling.datamodel`` types, so a test drives it with a fake and no docling
# dependency. The lazy adapter (``_docling_document_to_view``) is the ONLY code
# that touches the real docling API.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoclingCellView:
    """One TableFormer grid cell: its text + whether it is a header cell."""

    text: str
    is_header: bool = False


@dataclass(frozen=True, slots=True)
class DoclingBlockView:
    """One non-table Docling element: a governed label + its transcribed text."""

    label: str
    text: str


@dataclass(frozen=True, slots=True)
class DoclingTableView:
    """A TableFormer table as a rectangular grid of cells (rows of cells)."""

    rows: Tuple[Tuple[DoclingCellView, ...], ...]
    caption: str = ""


@dataclass(frozen=True, slots=True)
class DoclingPageView:
    """A page's Docling elements in reading order: blocks and tables interleaved.

    ``elements`` is the reading-order sequence; each entry is either a
    ``DoclingBlockView`` (heading / paragraph / footnote) or a
    ``DoclingTableView`` (a cell grid). The converter walks it in order so the
    emitted node sequence preserves Docling's reading order.
    """

    elements: Tuple[object, ...] = field(default_factory=tuple)


class _DoclingDocumentLike(Protocol):
    """Structural typing for the real ``docling`` document the adapter reads.

    Only ``iterate_items`` is consumed — a docling ``DoclingDocument`` yields
    ``(item, level)`` pairs from it; each item is read via getattr, never a
    concrete docling type, so this stays a minimal structural view.
    """

    def iterate_items(self) -> Iterable[Tuple[object, object]]: ...


# ---------------------------------------------------------------------------
# The PURE converter (no docling, no network, no PDF — fully testable).
# ---------------------------------------------------------------------------


def _cell_node(
    cell: DoclingCellView, *, artifact_digest: str, page_num: int, row: int, col: int
) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_CELL,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=artifact_digest,
            locator=f"docling:page={page_num};table;row={row};col={col}",
            page_num=page_num,
        ),
        text=cell.text,
        attrs={"is_header": "1" if cell.is_header else "0"},
    )


def _table_node(
    table: DoclingTableView, *, artifact_digest: str, page_num: int
) -> SourceDocumentNode:
    """Lower a TableFormer grid into a TABLE with TABLE_ROW/TABLE_CELL children."""
    row_nodes: List[SourceDocumentNode] = []
    for row_idx, row in enumerate(table.rows):
        cell_nodes = tuple(
            _cell_node(
                cell,
                artifact_digest=artifact_digest,
                page_num=page_num,
                row=row_idx,
                col=col_idx,
            )
            for col_idx, cell in enumerate(row)
        )
        row_nodes.append(
            SourceDocumentNode(
                kind=SourceDocumentNodeKind.TABLE_ROW,
                assurance_tier=AssuranceTier.SINGLE_WITNESS,
                anchor=SourceAnchor(
                    artifact_digest=artifact_digest,
                    locator=f"docling:page={page_num};table;row={row_idx}",
                    page_num=page_num,
                ),
                children=cell_nodes,
            )
        )
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=artifact_digest,
            locator=f"docling:page={page_num};table",
            page_num=page_num,
        ),
        text=table.caption,
        children=tuple(row_nodes),
    )


def _block_kind(label: str) -> SourceDocumentNodeKind:
    key = label.strip().lower()
    if key in _HEADING_LABELS:
        return SourceDocumentNodeKind.HEADING
    if key in _FOOTNOTE_LABELS:
        return SourceDocumentNodeKind.FOOTNOTE
    return SourceDocumentNodeKind.PARAGRAPH


def _block_node(
    block: DoclingBlockView, *, artifact_digest: str, page_num: int
) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=_block_kind(block.label),
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=artifact_digest,
            locator=f"docling:page={page_num}",
            page_num=page_num,
        ),
        text=block.text,
    )


def docling_document_to_nodes(
    page: DoclingPageView, *, artifact_digest: str, page_num: int
) -> Tuple[SourceDocumentNode, ...]:
    """Lower one page's Docling elements into typed ``SourceDocumentNode``s.

    PURE: takes a minimal typed VIEW of an already-parsed Docling page (no
    docling dependency, no network, no PDF). Reading order is preserved.
    Headings→HEADING, footnotes→FOOTNOTE, other blocks→PARAGRAPH; a table→TABLE
    with real TABLE_ROW/TABLE_CELL children (cell text + ``is_header`` flag).
    Every node is ``SINGLE_WITNESS`` (one producer; corroboration is
    adjudication's job) and carries a ``docling:page={page_num}`` anchor.
    """
    nodes: List[SourceDocumentNode] = []
    for element in page.elements:
        if isinstance(element, DoclingTableView):
            nodes.append(
                _table_node(element, artifact_digest=artifact_digest, page_num=page_num)
            )
        elif isinstance(element, DoclingBlockView):
            nodes.append(
                _block_node(element, artifact_digest=artifact_digest, page_num=page_num)
            )
        # A view element of neither governed kind is accounted by omission here
        # (the adapter only ever emits the two governed view kinds); an unknown
        # object is never coerced into a node.
    return tuple(nodes)


# ---------------------------------------------------------------------------
# The lazy adapter: real ``DoclingDocument`` -> per-page views. The ONLY code
# that touches the docling API; imported inside the method so the module is
# importable offline.
# ---------------------------------------------------------------------------


def _row_cells_from_table_item(table_item: object) -> Tuple[Tuple[DoclingCellView, ...], ...]:
    """Read a docling ``TableItem``'s grid into rows of ``DoclingCellView``.

    Docling exposes a table as ``table_item.data`` with a ``grid`` (a list of
    rows, each a list of cells carrying ``.text`` and a ``.column_header`` /
    ``.row_header`` flag). Read defensively via getattr so a docling minor-version
    field rename degrades to text-only cells rather than crashing the ingest.
    """
    data = getattr(table_item, "data", None)
    grid = getattr(data, "grid", None) if data is not None else None
    if not grid:
        return ()
    rows: List[Tuple[DoclingCellView, ...]] = []
    for grid_row in grid:
        cells: List[DoclingCellView] = []
        for cell in grid_row:
            text = str(getattr(cell, "text", "") or "")
            is_header = bool(
                getattr(cell, "column_header", False) or getattr(cell, "row_header", False)
            )
            cells.append(DoclingCellView(text=text, is_header=is_header))
        rows.append(tuple(cells))
    return tuple(rows)


def _docling_document_to_page_views(doc: _DoclingDocumentLike) -> dict[int, DoclingPageView]:
    """Group a real ``DoclingDocument``'s items into per-(1-indexed)-page views.

    Reads only the docling item surface (``label``, ``text``, provenance page
    number, and ``TableItem`` grid) via getattr, so a docling version skew
    narrows recall rather than raising. This is the sole docling-API-touching
    seam; the conversion into typed nodes is the pure function above. The docling
    import is dynamic (``import_module``) so this module resolves offline.
    """
    import importlib

    table_item_cls = importlib.import_module("docling_core.types.doc").TableItem

    by_page: dict[int, List[object]] = {}

    def _page_of(item: object) -> int:
        prov = getattr(item, "prov", None)
        if prov:
            page_no = getattr(prov[0], "page_no", None)
            if isinstance(page_no, int) and page_no >= 1:
                return page_no
        return 1

    for item, _level in doc.iterate_items():
        page = _page_of(item)
        bucket = by_page.setdefault(page, [])
        if isinstance(item, table_item_cls):
            # Docling exposes the caption as ``caption_text(doc)`` (a method) on
            # some versions and a plain attr on others — read defensively.
            caption_attr = getattr(item, "caption_text", "")
            caption = caption_attr(doc) if callable(caption_attr) else caption_attr
            bucket.append(
                DoclingTableView(
                    rows=_row_cells_from_table_item(item),
                    caption=str(caption or ""),
                )
            )
        else:
            label = str(getattr(item, "label", "") or "")
            text = str(getattr(item, "text", "") or "")
            if text:
                bucket.append(DoclingBlockView(label=label, text=text))

    return {page: DoclingPageView(elements=tuple(items)) for page, items in by_page.items()}


class DoclingStructuralProducer:
    """Docling learned-layout + TableFormer producer (satisfies ``_VisionProducer``).

    A candidate structural witness with a DIFFERENT failure mode than the
    geometric (pdfplumber) and content-stream (pypdfium2) producers, whose
    distinctive contribution is real table cell grids. ``propose_page`` returns
    typed ``SourceDocumentNode``s (a table carries its TABLE_ROW/TABLE_CELL
    children — richer than a flat ``ExtractionAssertion``), matching the ingest
    producer protocol shape so a caller can pass it as the structural producer.
    Every node is ``SINGLE_WITNESS``; the adjudicator raises assurance.
    """

    def __init__(self) -> None:
        self._converted_cache: dict[str, dict[int, DoclingPageView]] = {}

    @property
    def producer_id(self) -> str:
        return "docling"

    def is_available(self) -> bool:
        """True iff the ``docling`` extra is importable (the determinism firewall)."""
        import importlib.util

        return importlib.util.find_spec("docling") is not None

    def _page_views(self, manifestation: SourceManifestation) -> dict[int, DoclingPageView]:
        """Convert (once, cached per artifact) the PDF into per-page Docling views."""
        cached = self._converted_cache.get(manifestation.artifact_digest)
        if cached is not None:
            return cached
        import importlib
        import io

        # Dynamic imports so the module resolves offline (docling extra only).
        converter_cls = importlib.import_module("docling.document_converter").DocumentConverter
        stream_cls = importlib.import_module("docling.datamodel.base_models").DocumentStream

        # Docling's stream source needs a filename hint for format detection.
        stream = stream_cls(
            name=f"{manifestation.artifact_digest[:16]}.pdf",
            stream=io.BytesIO(manifestation.source_bytes),
        )
        result = converter_cls().convert(stream)
        views = _docling_document_to_page_views(result.document)
        self._converted_cache[manifestation.artifact_digest] = views
        return views

    def propose_page(
        self, manifestation: SourceManifestation, page_num: int
    ) -> Tuple[SourceDocumentNode, ...]:
        """Return 1-indexed ``page_num``'s Docling structural nodes (empty if none).

        The whole document is converted once (cached per artifact digest); this
        returns the requested page's slice. A page Docling saw no content on
        yields ``()`` — the caller (ingest) accounts empty pages as residual,
        which is honest for a producer whose recall is page-content-dependent.
        """
        views = self._page_views(manifestation)
        page = views.get(page_num)
        if page is None:
            return ()
        return docling_document_to_nodes(
            page, artifact_digest=manifestation.artifact_digest, page_num=page_num
        )


def structural_pages(
    producer: DoclingStructuralProducer,
    manifestation: SourceManifestation,
    *,
    max_pages: int = 200,
) -> Sequence[Tuple[SourceDocumentNode, ...]]:
    """Convenience: every page's Docling nodes, ready for ``compose_pages``.

    A caller that wants a whole-document Docling read (instead of one page)
    passes these page sequences straight into
    ``lawvm.core.source_document.composition.compose_pages`` — the multi-page
    table / footnote composition then runs producer-neutrally over Docling's
    output exactly as over any other producer's.
    """
    views = producer._page_views(manifestation)
    highest = max(views) if views else 0
    pages: List[Tuple[SourceDocumentNode, ...]] = []
    for page_num in range(1, min(highest, max_pages) + 1):
        page = views.get(page_num)
        if page is None:
            pages.append(())
            continue
        pages.append(
            docling_document_to_nodes(
                page, artifact_digest=manifestation.artifact_digest, page_num=page_num
            )
        )
    return pages
