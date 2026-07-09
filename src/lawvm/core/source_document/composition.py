"""Cross-page structural composition — per-page trees → one document IR.

Per-page extraction (each page adjudicated across producers) yields faithful but
PAGE-LOCAL structure. Strict LawVM IR needs the WHOLE-document structure: a table
that runs across 12 pages is ONE ``TABLE``; footnotes scattered over those pages
are one resolved set; a paragraph split by a page break is one paragraph. This
module composes the per-page trees into that whole-document tree — the
"growing-node" pass: it carries an OPEN trailing table/paragraph and extends it
with the next page's leading fragment when a ``ContinuationJudge`` says they
join, otherwise starts a new node.

Continuation is a judgement, not a certainty: the default judge is deterministic
and mechanical (column-count match for tables; unterminated-then-lowercase for
paragraphs), and an LLM judge may be substituted for ambiguous breaks (the
adjudication model — the composer never guesses silently; an unresolved join is
a finding). A composed node's assurance is the WEAKEST of its parts (a chain is
only as assured as its weakest link).

Discipline (AGENTS.md §1.9, §1.10): typed frozen carriers; footnotes and unjoined
fragments are accounted, never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, Sequence, Tuple

from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)

# Assurance ordering, most-assured first — the composed node takes the weakest.
_TIER_ORDER: Tuple[AssuranceTier, ...] = (
    AssuranceTier.HUMAN_CONFIRMED,
    AssuranceTier.MULTI_WITNESS_ADJUDICATED,
    AssuranceTier.SINGLE_WITNESS,
    AssuranceTier.UNADJUDICATED_PROPOSAL,
)
_TERMINAL_PUNCT = (".", "!", "?", ":", ";", "—", "–")


def _weakest(tiers: Sequence[AssuranceTier]) -> AssuranceTier:
    """The least-assured tier among ``tiers`` (a composed node is as weak as its parts)."""
    worst = AssuranceTier.HUMAN_CONFIRMED
    worst_rank = 0
    for t in tiers:
        rank = _TIER_ORDER.index(t)
        if rank > worst_rank:
            worst, worst_rank = t, rank
    return worst


def _row_width(row: SourceDocumentNode) -> int:
    """Column count of a TABLE_ROW = its number of TABLE_CELL children."""
    return sum(1 for c in row.children if c.kind is SourceDocumentNodeKind.TABLE_CELL)


def _table_width(table: SourceDocumentNode) -> int:
    """The table's column count, taken from its first row (0 if empty)."""
    for r in table.children:
        if r.kind is SourceDocumentNodeKind.TABLE_ROW:
            return _row_width(r)
    return 0


def _row_is_header(row: SourceDocumentNode) -> bool:
    return any(c.attrs.get("is_header") == "1" for c in row.children)


class ContinuationJudge(Protocol):
    """Decides whether a page's leading fragment continues the prior page's trailing one.

    Implementations own the semantic call; the composer owns the assembly. A
    deterministic judge (``DefaultContinuationJudge``) suffices for clean cases;
    an LLM judge may replace it for ambiguous page breaks.
    """

    def continues_table(
        self, open_table: SourceDocumentNode, next_table: SourceDocumentNode
    ) -> bool:
        """Does ``next_table`` (a page's leading table) continue ``open_table``?"""
        ...

    def continues_paragraph(
        self, open_para: SourceDocumentNode, next_para: SourceDocumentNode
    ) -> bool:
        """Does ``next_para`` continue ``open_para`` across the page break?"""
        ...


class DefaultContinuationJudge:
    """Deterministic, mechanical continuation judge (no LLM, no network).

    A table continues iff the next leading table has the SAME column width and
    does NOT re-open with a header row (a repeated header marks a fresh table). A
    paragraph continues iff the open one ends WITHOUT terminal punctuation and the
    next one begins lower-case (a mid-sentence break).
    """

    def continues_table(
        self, open_table: SourceDocumentNode, next_table: SourceDocumentNode
    ) -> bool:
        width = _table_width(open_table)
        if width == 0 or _table_width(next_table) != width:
            return False
        first_rows = [r for r in next_table.children if r.kind is SourceDocumentNodeKind.TABLE_ROW]
        return not (first_rows and _row_is_header(first_rows[0]))

    def continues_paragraph(
        self, open_para: SourceDocumentNode, next_para: SourceDocumentNode
    ) -> bool:
        prev = open_para.text.rstrip()
        nxt = next_para.text.lstrip()
        if not prev or not nxt:
            return False
        if prev.endswith(_TERMINAL_PUNCT):
            return False
        return nxt[:1].islower()


@dataclass(frozen=True, slots=True)
class ComposedDocument:
    """A whole-document SourceDocumentIR composed from per-page block sequences.

    ``root`` holds the composed body followed (if any) by one unified FOOTNOTE
    container. ``composition_findings`` records every join the judge made and any
    fragment it could not resolve — nothing is silently merged or dropped.
    """

    root: SourceDocumentNode
    page_count: int
    composition_findings: Tuple[str, ...]


def _merge_table(open_table: SourceDocumentNode, next_table: SourceDocumentNode) -> SourceDocumentNode:
    """Grow ``open_table`` by appending ``next_table``'s rows (skip a repeated header)."""
    add_rows = tuple(
        r for r in next_table.children if r.kind is SourceDocumentNodeKind.TABLE_ROW
    )
    merged_children = open_table.children + add_rows
    tiers = [open_table.assurance_tier, next_table.assurance_tier]
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=_weakest(tiers),
        anchor=open_table.anchor,
        label=open_table.label,
        text=open_table.text,
        children=merged_children,
        attrs=open_table.attrs,
    )


def _merge_paragraph(open_para: SourceDocumentNode, next_para: SourceDocumentNode) -> SourceDocumentNode:
    """Stitch two paragraph fragments split by a page break into one."""
    joined = f"{open_para.text.rstrip()} {next_para.text.lstrip()}".strip()
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.PARAGRAPH,
        assurance_tier=_weakest([open_para.assurance_tier, next_para.assurance_tier]),
        anchor=open_para.anchor,
        label=open_para.label,
        text=joined,
        attrs=open_para.attrs,
    )


def compose_pages(
    pages: Sequence[Sequence[SourceDocumentNode]],
    root_anchor,
    *,
    judge: ContinuationJudge | None = None,
) -> ComposedDocument:
    """Compose per-page top-level block sequences into one document tree.

    ``pages[i]`` is page i's top-level blocks in reading order. Footnotes are
    pulled out of every page and gathered into ONE trailing FOOTNOTE container
    (the "one giant footnote set" for a multi-page table). Tables and paragraphs
    that cross a page break are merged when ``judge`` says they continue.
    """
    judge = judge or DefaultContinuationJudge()
    body: List[SourceDocumentNode] = []
    footnotes: List[SourceDocumentNode] = []
    findings: List[str] = []

    def _last_open() -> SourceDocumentNode | None:
        return body[-1] if body else None

    for page_idx, blocks in enumerate(pages):
        for pos, node in enumerate(blocks):
            if node.kind is SourceDocumentNodeKind.FOOTNOTE:
                footnotes.append(node)
                continue
            open_node = _last_open()
            # Only a page's LEADING non-footnote block may continue the prior page.
            is_page_lead = pos == 0 or all(
                b.kind is SourceDocumentNodeKind.FOOTNOTE for b in blocks[:pos]
            )
            if (
                is_page_lead
                and page_idx > 0
                and open_node is not None
                and open_node.kind is SourceDocumentNodeKind.TABLE
                and node.kind is SourceDocumentNodeKind.TABLE
                and judge.continues_table(open_node, node)
            ):
                body[-1] = _merge_table(open_node, node)
                findings.append(f"merged table across page {page_idx}→{page_idx + 1}")
                continue
            if (
                is_page_lead
                and page_idx > 0
                and open_node is not None
                and open_node.kind is SourceDocumentNodeKind.PARAGRAPH
                and node.kind is SourceDocumentNodeKind.PARAGRAPH
                and judge.continues_paragraph(open_node, node)
            ):
                body[-1] = _merge_paragraph(open_node, node)
                findings.append(f"stitched paragraph across page {page_idx}→{page_idx + 1}")
                continue
            body.append(node)

    children = tuple(body)
    if footnotes:
        footnote_root = SourceDocumentNode(
            kind=SourceDocumentNodeKind.FOOTNOTE,
            assurance_tier=_weakest([f.assurance_tier for f in footnotes]),
            anchor=root_anchor,
            label="footnotes",
            children=tuple(footnotes),
            attrs={"role": "unified_footnotes"},
        )
        children = children + (footnote_root,)

    root = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=_weakest([c.assurance_tier for c in children]) if children else AssuranceTier.UNADJUDICATED_PROPOSAL,
        anchor=root_anchor,
        children=children,
    )
    return ComposedDocument(
        root=root,
        page_count=len(pages),
        composition_findings=tuple(findings),
    )
