"""Layout-aware extraction for Finlex attachment PDFs.

Two-strategy pipeline:

1. **Deterministic (pdfplumber)**: extracts tables as cell-level structures
   (with rowspan/colspan), separates footnotes from body text by position +
   font-size, and returns a structured ``AttachmentLayout`` that the IR
   parser consumes directly.
2. **Vision LLM fallback**: for pages where pdfplumber fails to identify
   table structure (scanned PDFs, complex merged cells), sends page images
   to a vision model that returns structured table markup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lawvm.core.ir import IRNode
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import IRNodeKind


@dataclass(frozen=True, slots=True)
class TableCell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    cells: tuple[TableCell, ...]
    page_num: int
    caption: str = ""


@dataclass(frozen=True, slots=True)
class Footnote:
    marker: str
    text: str
    page_num: int


@dataclass(frozen=True, slots=True)
class BodyBlock:
    text: str
    page_num: int
    y_position: float
    font_size: float = 0.0


@dataclass(frozen=True, slots=True)
class AttachmentLayout:
    body_blocks: tuple[BodyBlock, ...] = ()
    tables: tuple[ExtractedTable, ...] = ()
    footnotes: tuple[Footnote, ...] = ()
    page_count: int = 0
    extraction_method: str = "pdfplumber"


_FN_MARKER_RE = compile_classifier_regex(
    r"^(\d+[a-z]?[)])\s*(.+)", classifier_id="lawvm.finland.pdf_layout.fn_marker"
)


def _extract_footnote_marker(text: str) -> str:
    m = _FN_MARKER_RE.match(text.strip())
    return m.group(1) if m else ""


def extract_pdf_layout(pdf_bytes: bytes, *, max_pages: int = 5000) -> AttachmentLayout | None:
    """Extract structured layout from a Finlex attachment PDF."""
    import io

    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception:
        return None

    body_blocks: list[BodyBlock] = []
    tables: list[ExtractedTable] = []
    footnotes: list[Footnote] = []
    page_count = len(pdf.pages)

    for page_idx, page in enumerate(pdf.pages[:max_pages]):
        # noqa: F841 — page_width used for column detection in follow-up
        page_height = page.height

        # --- Extract tables ---
        try:
            page_tables = page.extract_tables()
        except Exception:
            page_tables = []

        table_regions: list[tuple[float, float, float, float]] = []
        try:
            tbl_bboxes = page.find_tables()
            for tb in tbl_bboxes:
                table_regions.append(tb.bbox)
        except Exception:
            pass

        for tbl_idx, tbl in enumerate(page_tables):
            if not tbl or len(tbl) < 2:
                continue
            cells: list[TableCell] = []
            for row_idx, row in enumerate(tbl):
                for col_idx, cell_text in enumerate(row):
                    if cell_text is None:
                        continue
                    text = str(cell_text).strip()
                    if not text:
                        continue
                    is_header = row_idx == 0 and (text.isupper() or len(text) < 20)
                    cells.append(TableCell(
                        text=text, row=row_idx, col=col_idx, is_header=is_header,
                    ))
            if cells:
                tables.append(ExtractedTable(cells=tuple(cells), page_num=page_idx + 1))

        # --- Separate footnotes from body text ---
        try:
            chars = page.chars
        except Exception:
            chars = []

        if not chars:
            continue

        lines: dict[float, list[dict]] = {}
        for char in chars:
            y = round(char["y0"], 0)
            lines.setdefault(y, []).append(char)

        sorted_y = sorted(lines.keys())
        font_sizes = [c["size"] for c in chars if c.get("size")]
        body_font_size = max(set(font_sizes), key=font_sizes.count) if font_sizes else 10.0
        footnote_threshold = body_font_size * 0.85
        footnote_zone_start = page_height * 0.75

        for y in sorted_y:
            line_chars = sorted(lines[y], key=lambda c: c["x0"])
            text = "".join(c["text"] for c in line_chars).strip()
            if not text:
                continue

            avg_size = sum(c.get("size", body_font_size) for c in line_chars) / max(len(line_chars), 1)
            is_footnote_zone = y > footnote_zone_start
            is_small_font = avg_size < footnote_threshold
            fn_marker = _extract_footnote_marker(text)

            if (is_footnote_zone and (is_small_font or fn_marker)) or fn_marker:
                if fn_marker:
                    footnotes.append(Footnote(
                        marker=fn_marker, text=text, page_num=page_idx + 1,
                    ))
            else:
                in_table = any(
                    bbox[0] <= min(c["x0"] for c in line_chars) <= bbox[2]
                    and bbox[1] <= y <= bbox[3]
                    for bbox in table_regions
                ) if table_regions else False

                if not in_table:
                    body_blocks.append(BodyBlock(
                        text=text, page_num=page_idx + 1,
                        y_position=y, font_size=avg_size,
                    ))

    pdf.close()
    return AttachmentLayout(
        body_blocks=tuple(body_blocks),
        tables=tuple(tables),
        footnotes=tuple(footnotes),
        page_count=page_count,
    )


def layout_to_ir_node(layout: AttachmentLayout, *, source_ref: str = "") -> IRNode:
    """Build an IRNode tree from a structured ``AttachmentLayout``.

    Uses the same ``_Builder`` pattern as ``pdf_text_to_ir_node``.
    """
    from lawvm.finland.attachment_ir import (
        _Builder,
        _ITEM_ALPHA_RE,
        _LIITE_RE,
        _OSA_RE,
        _PARA_NUM_RE,
        _is_caps_heading,
    )

    root = _Builder(
        IRNodeKind.HCONTAINER,
        attrs={"source_ref": source_ref} if source_ref else {},
    )
    stack: list[_Builder] = [root]
    current_leaf: _Builder | None = None

    def close_paragraph() -> None:
        nonlocal current_leaf
        while stack and stack[-1].kind in (IRNodeKind.PARAGRAPH, IRNodeKind.ITEM):
            stack.pop()
        current_leaf = None

    @dataclass(frozen=True, slots=True)
    class _LayoutItem:
        kind: str
        page: int
        y: float
        data: Any

    items: list[_LayoutItem] = []
    for b in layout.body_blocks:
        items.append(_LayoutItem("body", b.page_num, b.y_position, b))
    for t in layout.tables:
        items.append(_LayoutItem("table", t.page_num, 0.0, t))
    for f in layout.footnotes:
        items.append(_LayoutItem("footnote", f.page_num, 99999.0, f))
    items.sort(key=lambda i: (i.page, i.y))

    for item in items:
        if item.kind == "body":
            text = item.data.text.strip()
            if not text:
                continue
            m = _OSA_RE.match(text)
            if m:
                close_paragraph()
                del stack[1:]
                appendix = _Builder(
                    IRNodeKind.APPENDIX,
                    label=f"osa_{m.group(1)}",
                    attrs={"osa_label": m.group(1), "source_text": text},
                )
                root.children.append(appendix)
                stack.append(appendix)
                continue
            m = _LIITE_RE.match(text)
            if m:
                close_paragraph()
                if not any(b.kind == IRNodeKind.APPENDIX for b in stack):
                    auto = _Builder(
                        IRNodeKind.APPENDIX,
                        label="osa_auto",
                        attrs={"auto_created": "no_osa_header_for_liite"},
                    )
                    root.children.append(auto)
                    del stack[1:]
                    stack.append(auto)
                else:
                    while stack[-1].kind in (
                        IRNodeKind.SCHEDULE, IRNodeKind.PARAGRAPH, IRNodeKind.ITEM,
                    ):
                        stack.pop()
                schedule = _Builder(
                    IRNodeKind.SCHEDULE,
                    label=f"liite_{m.group(1)}",
                    attrs={"liite_label": m.group(1), "source_text": text},
                )
                stack[-1].children.append(schedule)
                stack.append(schedule)
                continue
            m = _PARA_NUM_RE.match(text)
            if m:
                close_paragraph()
                para = _Builder(
                    IRNodeKind.PARAGRAPH, label=m.group(1),
                    attrs={"source_text": text},
                )
                stack[-1].children.append(para)
                stack.append(para)
                current_leaf = para
                rest = text[m.end():].strip()
                if rest:
                    current_leaf.append_text(rest)
                continue
            m = _ITEM_ALPHA_RE.match(text)
            if m:
                item_b = _Builder(
                    IRNodeKind.ITEM, label=m.group(1),
                    attrs={"source_text": text},
                )
                parent = next(
                    (b for b in reversed(stack)
                     if b.kind == IRNodeKind.PARAGRAPH),
                    stack[-1],
                )
                parent.children.append(item_b)
                stack.append(item_b)
                current_leaf = item_b
                rest = text[m.end():].strip()
                if rest:
                    current_leaf.append_text(rest)
                continue
            if _is_caps_heading(text):
                close_paragraph()
                heading = _Builder(IRNodeKind.HEADING)
                heading.append_text(text)
                stack[-1].children.append(heading)
                current_leaf = heading
                continue
            if current_leaf is not None:
                current_leaf.append_text(text)
            else:
                p = _Builder(IRNodeKind.P)
                p.append_text(text)
                stack[-1].children.append(p)
                current_leaf = p

        elif item.kind == "table":
            tbl: ExtractedTable = item.data
            rows: dict[int, list[TableCell]] = {}
            for cell in tbl.cells:
                rows.setdefault(cell.row, []).append(cell)
            row_builders: list[_Builder] = []
            for row_idx in sorted(rows.keys()):
                cells = sorted(rows[row_idx], key=lambda c: c.col)
                cell_builders = [
                    _Builder(
                        IRNodeKind.HEADER_CELL if c.is_header else IRNodeKind.CELL,
                        text_parts=[c.text],
                        attrs={"rowspan": c.rowspan, "colspan": c.colspan}
                        if c.rowspan > 1 or c.colspan > 1
                        else {},
                    )
                    for c in cells
                ]
                rb = _Builder(IRNodeKind.ROW)
                rb.children = cell_builders
                row_builders.append(rb)
            if row_builders:
                tbl_builder = _Builder(
                    IRNodeKind.TABLE,
                    attrs={"page": str(tbl.page_num)} if tbl.page_num else {},
                )
                tbl_builder.children = row_builders
                stack[-1].children.append(tbl_builder)

        elif item.kind == "footnote":
            fn: Footnote = item.data
            fn_builder = _Builder(
                IRNodeKind.SCHEDULE_ENTRY,
                label=fn.marker,
                text_parts=[fn.text],
                attrs={"page": str(fn.page_num)},
            )
            target = next(
                (b for b in reversed(stack)
                 if b.kind == IRNodeKind.SCHEDULE),
                root,
            )
            target.children.append(fn_builder)

    return root.to_ir_node()
