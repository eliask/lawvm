"""Cross-page composition — per-page trees → one whole-document IR.

Pins the growing-node composer: a table that runs across a page break becomes ONE
table, footnotes across pages become one unified set, a paragraph split by a page
break is stitched — and a genuine break (different column width, repeated header,
terminated sentence) is NOT merged.
"""
from __future__ import annotations

from lawvm.core.source_document import (
    AssuranceTier,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
    compose_pages,
)

_DIGEST = "a" * 64


def _anchor(page: int) -> SourceAnchor:
    return SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page)


def _cell(text: str, *, header: bool = False) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_CELL,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(1),
        text=text,
        attrs={"is_header": "1" if header else "0"},
    )


def _row(cells: tuple[str, ...], *, header: bool = False, page: int = 1) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_ROW,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        children=tuple(_cell(c, header=header) for c in cells),
    )


def _table(rows: tuple[SourceDocumentNode, ...], *, page: int = 1, tier: AssuranceTier = AssuranceTier.SINGLE_WITNESS) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=tier,
        anchor=_anchor(page),
        children=rows,
    )


def _para(text: str, *, page: int = 1) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.PARAGRAPH,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        text=text,
    )


def _footnote(marker: str, text: str, *, page: int = 1) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.FOOTNOTE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(page),
        label=marker,
        text=text,
    )


def _rows(root):
    for c in root.children:
        if c.kind is SourceDocumentNodeKind.TABLE:
            yield from (r for r in c.children if r.kind is SourceDocumentNodeKind.TABLE_ROW)


def test_multi_page_table_merges_into_one_table_with_unified_footnotes() -> None:
    page1 = [
        _table((
            _row(("Tuote", "Senttiä"), header=True),
            _row(("Kevyt polttoöljy", "4")),
        ), page=1),
        _footnote("1)", "Sovelletaan 2025.", page=1),
    ]
    page2 = [
        _table((
            _row(("Raskas polttoöljy", "4,49"), page=2),
            _row(("R-luokka", "4"), page=2),
        ), page=2),
        _footnote("2)", "Sovelletaan 2026.", page=2),
    ]
    doc = compose_pages([page1, page2], _anchor(0))

    tables = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.TABLE]
    assert len(tables) == 1  # the two page-tables merged into one
    assert len(list(_rows(doc.root))) == 4  # header + 3 data rows
    footnote_sets = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.FOOTNOTE]
    assert len(footnote_sets) == 1  # one unified footnote container...
    assert len(footnote_sets[0].children) == 2  # ...holding both pages' footnotes
    assert footnote_sets[0].attrs.get("role") == "unified_footnotes"
    assert any("merged table" in f for f in doc.composition_findings)


def test_different_width_table_is_not_merged() -> None:
    page1 = [_table((_row(("a", "b")),), page=1)]
    page2 = [_table((_row(("x", "y", "z"), page=2),), page=2)]  # 3 cols ≠ 2
    doc = compose_pages([page1, page2], _anchor(0))
    tables = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.TABLE]
    assert len(tables) == 2


def test_repeated_header_starts_a_fresh_table() -> None:
    page1 = [_table((_row(("Tuote", "€"), header=True), _row(("a", "1"))), page=1)]
    page2 = [_table((_row(("Tuote", "€"), header=True, page=2), _row(("b", "2"), page=2)), page=2)]
    doc = compose_pages([page1, page2], _anchor(0))
    tables = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.TABLE]
    assert len(tables) == 2  # the re-opened header marks a new table


def test_paragraph_split_by_page_break_is_stitched() -> None:
    page1 = [_para("hakijalle palautetaan valmisteveroa verovuoden 2025 aikana", page=1)]
    page2 = [_para("kevyestä polttoöljystä 4 senttiä litralta.", page=2)]  # lowercase continuation
    doc = compose_pages([page1, page2], _anchor(0))
    paras = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.PARAGRAPH]
    assert len(paras) == 1
    assert "verovuoden 2025 aikana kevyestä polttoöljystä" in paras[0].text
    assert any("stitched paragraph" in f for f in doc.composition_findings)


def test_terminated_paragraph_is_not_stitched() -> None:
    page1 = [_para("Tämä laki tulee voimaan.", page=1)]  # terminal period
    page2 = [_para("Helsingissä x.x.2026", page=2)]
    doc = compose_pages([page1, page2], _anchor(0))
    paras = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.PARAGRAPH]
    assert len(paras) == 2


def test_composed_table_takes_the_weakest_assurance() -> None:
    # A single-witness page-2 fragment drags a multi-witness open table down.
    page1 = [_table((_row(("a", "b")),), page=1, tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED)]
    page2 = [_table((_row(("c", "d"), page=2),), page=2, tier=AssuranceTier.SINGLE_WITNESS)]
    doc = compose_pages([page1, page2], _anchor(0))
    table = next(c for c in doc.root.children if c.kind is SourceDocumentNodeKind.TABLE)
    assert table.assurance_tier is AssuranceTier.SINGLE_WITNESS
