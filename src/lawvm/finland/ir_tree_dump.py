"""Human-readable IR tree dumper for LawVM CLI output.

Two formats:

1. **Technical** (``format_ir_tree``): shows IRNode kind/label/text — for
   debugging IR structure (verifying the parser produced the right kinds).

2. **Pretty** (``format_ir_pretty``): shows human-readable legal labels —
   section numbers, headings, paragraph text — without IRNode kind names.
   Resembles a statute table-of-contents + content view:

   .. code-block:: text

      1 luku Yleiset säännökset
        1 § Soveltamisala
          Tätä asetusta sovelletaan...
        2 § Määritelmät
          Tässä asetuksessa tarkoitetaan:
            a) tyyppihyväksynnällä...
            b) autojen ja perävaunujen puitedirektiivillä...

      --- 4484.pdf ---
      I OSA
        Liite 1
          AUTOJA JA PERÄVAUNUJA...
          1. Tätä taulukkoa sovelletaan...
          2. Jos tyyppihyväksyntä...
            a) yhden direktiivin kohdalle;
            b) direktiivin yhden muutoksen kohdalle;
          [Table: 12 rows × 7 columns]
            header: Ajoneuvo | Perusdirektiivi | ...
            row 1: 70/156/ETY | M, N, O | ...
"""
from __future__ import annotations

from typing import Optional, Sequence

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


def _clip(text: str, max_text: Optional[int]) -> str:
    """Truncate ``text`` to ``max_text`` chars, or pass through when None.

    ``max_text=None`` (the default for the show/dump pretty-printers) means
    no truncation — the full text renders per the user's "show must not
    truncate ANY text" directive. ``max_text=N`` clips the visible text to
    ``N`` chars WITHOUT appending an ellipsis (the pretty-printer never
    truncates with a visible marker — full text or clipped, never partial
    "(…)" in a dump).

    Used by the pretty form. The technical form (``format_ir_tree``)
    appends an explicit ``…`` at the truncation point so IR-construction
    debug snippets remain greppable.
    """
    if max_text is None or len(text) <= max_text:
        return text
    return text[:max_text]


# Human-readable labels for each IRNodeKind (Finnish legal convention).
# Empty string means "don't show a kind label — just show text/children".
_KIND_LABELS_FI: dict[str, str] = {
    "CHAPTER": "luku",
    "SECTION": "§",
    "SUBSECTION": "momentti",
    "PARAGRAPH": "kohta",
    "ITEM": "kohta",
    "HEADING": "",
    "NUM": "",
    "CONTENT": "",
    "INTRO": "",
    "P": "",
    "TABLE": "",
    "ROW": "",
    "CELL": "",
    "HEADER_CELL": "",
    "APPENDIX": "OSA",
    "SCHEDULE": "Liite",
    "SCHEDULE_ENTRY": "alaviite",
}


def format_ir_tree(root: IRNode, *, indent: int = 0, max_text: Optional[int] = None) -> str:
    """Technical IR tree dump: KIND [label] text-snippet.

    ``max_text=None`` (default) means no truncation — every IRNode's text
    renders in full per the user's "no truncation" directive. Pass an int
    to clip debug snippets to N chars (a ``…`` marker is appended at the
    clip point so the snippet remains greppable in IR-debug output).
    """
    lines: list[str] = []
    _format_node_technical(root, lines, indent, max_text)
    return "\n".join(lines)


def format_ir_pretty(
    root: IRNode,
    *,
    indent: int = 0,
    max_text: Optional[int] = None,
    show_tables: bool = True,
    max_table_rows: int = 5,
) -> str:
    """Human-readable pretty statute dump (Finnish legal convention).

    Walks the IRNode tree:
    - CHAPTER → ``N luku <heading>``
    - SECTION → ``N § <heading>``
    - SUBSECTION → ``N momentti``
    - PARAGRAPH → ``N.`` prefix on text
    - ITEM → ``a)`` prefix on text
    - HEADING → ALL-CAPS heading line
    - P / CONTENT → body text
    - TABLE → ``[Table: N rows × M cols]`` summary + first few rows
    - APPENDIX → ``I OSA``
    - SCHEDULE → ``Liite N``
    - SCHEDULE_ENTRY → ``[alaviite N] text``

    No IRNode kind names — just human-readable legal labels and text content.
    """
    lines: list[str] = []
    _format_node_pretty(root, lines, indent, max_text, show_tables, max_table_rows)
    return "\n".join(lines)


def _format_node_technical(
    node: IRNode, lines: list[str], indent: int, max_text: Optional[int]
) -> None:
    prefix = "  " * indent
    parts: list[str] = []
    parts.append(str(node.kind.name if hasattr(node.kind, "name") else node.kind))
    if node.label:
        parts.append(f'"{node.label}"')
    text = (node.text or "").strip()
    if text:
        if max_text is None:
            snippet = text
        else:
            snippet = text[:max_text]
            if len(text) > max_text:
                snippet += "…"
        snippet = snippet.replace("\n", " ")
        parts.append(f'"{snippet}"')
    lines.append(f"{prefix}{' '.join(parts)}")
    for child in node.children:
        _format_node_technical(child, lines, indent + 1, max_text)


def _format_node_pretty(
    node: IRNode,
    lines: list[str],
    indent: int,
    max_text: Optional[int],
    show_tables: bool,
    max_table_rows: int,
) -> None:
    prefix = "  " * indent
    kind_str = str(node.kind.name if hasattr(node.kind, "name") else node.kind)
    label = node.label or ""
    text = (node.text or "").strip()

    if kind_str == "HCONTAINER":
        # Root — just walk children
        for child in node.children:
            _format_node_pretty(
                child, lines, indent, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "BODY":
        for child in node.children:
            _format_node_pretty(
                child, lines, indent, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "CHAPTER":
        # "N luku <heading>" — heading is child HEADING node
        heading = _collect_heading_text(node)
        if heading:
            lines.append(f"{prefix}{label} luku {heading}")
        else:
            lines.append(f"{prefix}{label} luku")
        for child in node.children:
            if str(child.kind.name if hasattr(child.kind, "name") else child.kind) in ("HEADING", "NUM"):
                continue
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "SECTION":
        heading = _collect_heading_text(node)
        if heading:
            lines.append(f"{prefix}{label} § {heading}")
        else:
            lines.append(f"{prefix}{label} §")
        for child in node.children:
            if str(child.kind.name if hasattr(child.kind, "name") else child.kind) in ("HEADING", "NUM"):
                continue
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "SUBSECTION":
        lines.append(f"{prefix}{label} momentti")
        for child in node.children:
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "PARAGRAPH":
        # "N. text..." (numeral dot for legal paragraphs)
        if label and text:
            lines.append(f"{prefix}{label}. {_clip(text, max_text)}")
        elif text:
            lines.append(f"{prefix}{_clip(text, max_text)}")
        # Walk children (items, etc.)
        for child in node.children:
            child_kind = str(child.kind.name if hasattr(child.kind, "name") else child.kind)
            if child_kind in ("NUM",) or (child_kind == "CONTENT" and not child.text):
                continue
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "ITEM":
        if label and text:
            lines.append(f"{prefix}{label}) {_clip(text, max_text)}")
        elif text:
            lines.append(f"{prefix}{_clip(text, max_text)}")
        for child in node.children:
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "HEADING":
        if text:
            lines.append(f"{prefix}{_clip(text, max_text)}")
        return

    if kind_str in ("CONTENT", "P", "I"):
        if text:
            lines.append(f"{prefix}{_clip(text, max_text)}")
        for child in node.children:
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "INTRO":
        if text:
            lines.append(f"{prefix}{_clip(text, max_text)}")
        return

    if kind_str == "APPENDIX":
        lines.append(f"{prefix}{label}")
        for child in node.children:
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "SCHEDULE":
        lines.append(f"{prefix}Liite {label.replace('liite_', '') if label else ''}")
        for child in node.children:
            _format_node_pretty(
                child, lines, indent + 1, max_text, show_tables, max_table_rows
            )
        return

    if kind_str == "SCHEDULE_ENTRY":
        lines.append(f"{prefix}[{label}] {_clip(text, max_text)}")
        return

    if kind_str == "TABLE" and show_tables:
        _format_table(node, lines, indent, max_table_rows)
        return

    if kind_str == "OMISSION":
        lines.append(f"{prefix}[…]")
        return

    # Fallback: kind label + text + children
    if text:
        lines.append(f"{prefix}{_clip(text, max_text)}")
    for child in node.children:
        _format_node_pretty(
            child, lines, indent + 1, max_text, show_tables, max_table_rows
        )


def _format_table(
    node: IRNode,
    lines: list[str],
    indent: int,
    max_rows: int,
) -> None:
    """Format a TABLE node as a compact human-readable summary."""
    prefix = "  " * indent
    rows = [c for c in node.children if str(c.kind.name if hasattr(c.kind, "name") else c.kind) == "ROW"]
    if not rows:
        lines.append(f"{prefix}[Table: empty]")
        return
    # Count columns (max cells in any row)
    max_cols = max(len(r.children) for r in rows)
    lines.append(f"{prefix}[Table: {len(rows)} rows × {max_cols} cols]")
    # Show header + first few rows
    for row_idx, row in enumerate(rows[:max_rows + 1]):
        cells = []
        for cell in row.children:
            cell_text = (cell.text or "").strip()[:40]
            cells.append(cell_text)
        separator = " | "
        if row_idx == 0:
            lines.append(f"{prefix}  header: {separator.join(cells)}")
        else:
            lines.append(f"{prefix}  row {row_idx}: {separator.join(cells)}")
    if len(rows) > max_rows + 1:
        lines.append(f"{prefix}  ... ({len(rows) - max_rows - 1} more rows)")


def _collect_heading_text(node: IRNode) -> str:
    """Extract the heading text from a SECTION/CHAPTER node's HEADING child."""
    for child in node.children:
        kind_str = str(child.kind.name if hasattr(child.kind, "name") else child.kind)
        if kind_str == "HEADING" and child.text:
            return child.text.strip()
    return ""


def format_statute_with_attachments(
    body_ir: IRNode,
    attachment_supplements: Sequence,
    *,
    max_text: Optional[int] = None,
    max_table_rows: int = 5,
) -> str:
    """Full statute pretty-print: body + attachment supplements.

    Body walks the replay IR tree. Attachments walk the
    ``AttachmentIRSupplement.ir`` tree, each preceded by a header line.
    """
    parts: list[str] = []
    parts.append(format_ir_pretty(body_ir, max_text=max_text, max_table_rows=max_table_rows))
    if attachment_supplements:
        parts.append(f"\n--- Attachments ({len(attachment_supplements)}) ---")
        for supp in attachment_supplements:
            parts.append(f"\n[{supp.pdf_name} ({supp.pdf_text_length} chars)]")
            parts.append(format_ir_pretty(supp.ir, max_text=max_text, max_table_rows=max_table_rows))
    return "\n".join(parts)


def merge_attachments_into_root(
    body_ir: IRNode,
    attachment_supplements: Sequence,
) -> IRNode:
    """Merge body + attachment IRs under a single HCONTAINER root (SDOC-13).

    Per SDOC-13 (``a projection must include attachments/schedules unless
    explicitly scoped out``) and the architectural target in
    ``notes_internal/REMAINING_WORK.md``: today attachment_supplements is
    a separate tuple on StatuteContext. Target: attachments are siblings
    of BODY under one HCONTAINER root — one tree, one walk, one dump
    output.

    This helper is the **projection-plane** merge: it builds a unified
    IRNode tree (BODY + APPENDIX siblings under HCONTAINER) from the
    existing master body + supplements tuple. The internal supplements
    sidecar remains the source of truth for the producer/writer; this
    helper is what walkers/projections consume when they want a single
    tree (per §2.10 — ``a projection is never the source of truth; it
    must be re-derivable from a committed dossier``).

    Each ``AttachmentIRSupplement.ir`` is wrapped as an APPENDIX node so
    its children render transparently through the pretty-printer's
    HCONTAINER-walks-children handler (e.g. ``format_ir_pretty`` already
    treats HCONTAINER as a transparent scope).

    Backwards-compatible: callers without supplements (empty sequence)
    receive the body IR unchanged (no extra wrapping layer).
    """
    if not attachment_supplements:
        return body_ir

    appendix_children: list[IRNode] = []
    for idx, supp in enumerate(attachment_supplements):
        supp_ir = supp.ir
        # Use the supplement's pdf_name (without .pdf suffix) as the
        # APPENDIX label; fall back to a positional label when missing.
        supp_label = getattr(supp, "pdf_name", "") or f"attachment_{idx + 1}"
        supp_label = supp_label.removesuffix(".pdf")
        appendix_children.append(
            IRNode(
                kind=IRNodeKind.APPENDIX,
                label=supp_label,
                children=supp_ir.children,
            )
        )

    return IRNode(
        kind=IRNodeKind.HCONTAINER,
        children=(body_ir, *appendix_children),
    )


def format_unified_statute(
    body_ir: IRNode,
    attachment_supplements: Sequence,
    *,
    max_text: Optional[int] = None,
    max_table_rows: int = 5,
) -> str:
    """Pretty-print the merged body + attachment tree in one walk.

    SDOC-13-ready counterpart to :func:`format_statute_with_attachments`:
    rather than body separately + attachments separately-with-headers,
    walks the merged HCONTAINER → [BODY, APPENDIX_1, ...] tree as one
    continuous document. Functions as a behavioural mirror of how
    :func:`format_ir_pretty` would render the same tree.
    """
    merged = merge_attachments_into_root(body_ir, attachment_supplements)
    return format_ir_pretty(merged, max_text=max_text, max_table_rows=max_table_rows)
