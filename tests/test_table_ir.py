"""Tests for table structure preservation in IR and typed surface model.

Verifies that:
1. XML <table> elements are preserved as structured IRNode subtrees
   (table -> row -> cell/header_cell) instead of being flattened to text.
2. The typed TableBody surface model correctly projects from IR table nodes.
3. Flat text serialization from structured tables matches legacy output
   for backward compatibility.
4. Semantic projection carries structured TableBody alongside flat text.
"""

from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.table_model import (
    RowKey,
    TableBody,
    TableCell,
    TableRow,
    table_body_to_flat_text,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.grafter import _fi_label_postprocessor
from lawvm.semantic.projection import (
    _project_ir_table_body,
    _project_oracle_table_body,
    _serialize_ir_table_text,
    semantic_structure_from_ir,
    semantic_structure_from_oracle,
)


# ---------------------------------------------------------------------------
# 1. XML -> IR table structure preservation
# ---------------------------------------------------------------------------


def test_xml_table_to_ir_preserves_rows_and_cells() -> None:
    """A simple <table> element must produce IRNode(kind='table') with row/cell children."""
    xml = etree.fromstring(
        """
        <table>
          <tr>
            <td>A1</td>
            <td>B1</td>
          </tr>
          <tr>
            <td>A2</td>
            <td>B2</td>
          </tr>
        </table>
        """
    )

    table = _xml_table_to_ir(xml)

    assert table.kind == IRNodeKind.TABLE
    assert len(table.children) == 2

    row1 = table.children[0]
    assert row1.kind == IRNodeKind.ROW
    assert len(row1.children) == 2
    assert row1.children[0].kind == IRNodeKind.CELL
    assert row1.children[0].text == "A1"
    assert row1.children[1].kind == IRNodeKind.CELL
    assert row1.children[1].text == "B1"

    row2 = table.children[1]
    assert row2.kind == IRNodeKind.ROW
    assert row2.children[0].text == "A2"
    assert row2.children[1].text == "B2"


def test_xml_table_to_ir_preserves_header_cells() -> None:
    """<th> elements must become IRNode(kind='header_cell')."""
    xml = etree.fromstring(
        """
        <table>
          <tr>
            <th>Name</th>
            <th>Value</th>
          </tr>
          <tr>
            <td>foo</td>
            <td>bar</td>
          </tr>
        </table>
        """
    )

    table = _xml_table_to_ir(xml)

    header_row = table.children[0]
    assert header_row.kind == IRNodeKind.ROW
    assert header_row.children[0].kind == IRNodeKind.HEADER_CELL
    assert header_row.children[0].text == "Name"
    assert header_row.children[1].kind == IRNodeKind.HEADER_CELL
    assert header_row.children[1].text == "Value"

    data_row = table.children[1]
    assert data_row.children[0].kind == IRNodeKind.CELL
    assert data_row.children[0].text == "foo"


def test_xml_table_to_ir_preserves_colspan() -> None:
    """colspan attributes must be forwarded in cell attrs."""
    xml = etree.fromstring(
        """
        <table>
          <tr>
            <td>A</td>
            <td colspan="2">B spans two</td>
          </tr>
        </table>
        """
    )

    table = _xml_table_to_ir(xml)
    row = table.children[0]
    assert row.children[1].attrs.get("colspan") == "2"
    # colspan=1 should NOT be stored (default)
    assert "colspan" not in row.children[0].attrs


def test_generic_xml_to_ir_node_preserves_table_in_content() -> None:
    """xml_to_ir_node must produce structured table children in content, not flat text."""
    xml = etree.fromstring(
        """
        <subsection>
          <content>
            <p>Some text before the table.</p>
            <table>
              <tr><td>Cell 1</td><td>Cell 2</td></tr>
            </table>
          </content>
        </subsection>
        """
    )

    node = xml_to_ir_node(xml)
    assert node.kind == IRNodeKind.SUBSECTION

    # Find the content child -- it should have table children now
    content = next(c for c in node.children if c.kind == IRNodeKind.CONTENT)
    table_children = [c for c in content.children if c.kind == IRNodeKind.TABLE]
    assert len(table_children) == 1
    assert table_children[0].children[0].kind == IRNodeKind.ROW

    # The text field contains only non-table text; table text lives in structured children.
    assert "Some text before the table." in content.text
    assert "Cell 1" not in content.text
    # But flat serialization via irnode_to_text must still include table text.
    from lawvm.core.ir_helpers import irnode_to_text
    full_text = irnode_to_text(content)
    assert "Cell 1" in full_text


def test_fi_xml_to_ir_node_preserves_non_court_table() -> None:
    """Non-court tables (no court header) must be preserved as structured IR, not flattened."""
    xml = etree.fromstring(
        """
        <subsection>
          <content>
            <p>Arvonlisäverokannat:</p>
            <table>
              <tr><th>Tuote</th><th>Verokanta</th></tr>
              <tr><td>Elintarvikkeet</td><td>14 %</td></tr>
              <tr><td>Kirjat</td><td>10 %</td></tr>
            </table>
          </content>
        </subsection>
        """
    )

    node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # Since this is NOT a court table, the special _parse_table_subsection path
    # should not fire. The table should be preserved structurally in content.
    assert node.kind == IRNodeKind.SUBSECTION
    content = next(c for c in node.children if c.kind == IRNodeKind.CONTENT)
    table_children = [c for c in content.children if c.kind == IRNodeKind.TABLE]
    assert len(table_children) == 1

    table = table_children[0]
    assert table.kind == IRNodeKind.TABLE
    assert len(table.children) == 3  # 1 header row + 2 data rows

    header_row = table.children[0]
    assert header_row.children[0].kind == IRNodeKind.HEADER_CELL
    assert header_row.children[0].text == "Tuote"


def test_irnode_to_text_handles_table_subtree() -> None:
    """irnode_to_text must still produce flat text from structured table IR."""
    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.HEADER_CELL, text="Name"),
                IRNode(kind=IRNodeKind.HEADER_CELL, text="Value"),
            )),
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="foo"),
                IRNode(kind=IRNodeKind.CELL, text="bar"),
            )),
        ),
    )

    text = irnode_to_text(table)
    assert "Name" in text
    assert "Value" in text
    assert "foo" in text
    assert "bar" in text


# ---------------------------------------------------------------------------
# 2. Typed TableBody surface model
# ---------------------------------------------------------------------------


def test_table_body_to_flat_text() -> None:
    """table_body_to_flat_text must produce space-joined row text."""
    body = TableBody(
        table_id="t1",
        columns=("A", "B"),
        rows=(
            TableRow(
                row_key=RowKey(basis="ordinal", value="1", strength="weak"),
                cells=(
                    TableCell(column_key="A", text="hello"),
                    TableCell(column_key="B", text="world"),
                ),
            ),
            TableRow(
                row_key=RowKey(basis="ordinal", value="2", strength="weak"),
                cells=(
                    TableCell(column_key="A", text="foo"),
                    TableCell(column_key="B", text="bar"),
                ),
            ),
        ),
    )

    text = table_body_to_flat_text(body)
    assert text == "hello world foo bar"


# ---------------------------------------------------------------------------
# 3. IR -> TableBody projection
# ---------------------------------------------------------------------------


def test_project_ir_table_body_basic() -> None:
    """Project an IR table node with header row into a TableBody."""
    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.HEADER_CELL, text="Name"),
                IRNode(kind=IRNodeKind.HEADER_CELL, text="Value"),
            )),
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="alpha"),
                IRNode(kind=IRNodeKind.CELL, text="100"),
            )),
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="beta"),
                IRNode(kind=IRNodeKind.CELL, text="200"),
            )),
        ),
    )

    body = _project_ir_table_body(table)
    assert body is not None
    assert body.columns == ("Name", "Value")
    assert len(body.rows) == 2
    assert body.rows[0].cells[0].text == "alpha"
    assert body.rows[0].cells[0].column_key == "Name"
    assert body.rows[0].row_key.value == "alpha"
    assert body.rows[0].row_key.basis == "named_anchor"
    assert body.rows[1].cells[1].text == "200"


def test_project_ir_table_body_no_header() -> None:
    """Project an IR table node without header row (all td cells)."""
    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="A"),
                IRNode(kind=IRNodeKind.CELL, text="B"),
            )),
        ),
    )

    body = _project_ir_table_body(table)
    assert body is not None
    assert body.columns == ()
    assert len(body.rows) == 1
    assert body.rows[0].cells[0].column_key == "0"  # ordinal column key


def test_project_ir_table_body_returns_none_for_non_table() -> None:
    """Non-table IRNodes must return None."""
    node = IRNode(kind=IRNodeKind.SECTION, label="1")
    assert _project_ir_table_body(node) is None


# ---------------------------------------------------------------------------
# 4. Oracle XML -> TableBody projection
# ---------------------------------------------------------------------------


def test_project_oracle_table_body() -> None:
    """Project an oracle XML <table> into a TableBody."""
    xml = etree.fromstring(
        """
        <table>
          <tr><th>Col1</th><th>Col2</th></tr>
          <tr><td>x</td><td>y</td></tr>
        </table>
        """
    )

    body = _project_oracle_table_body(xml)
    assert body is not None
    assert body.columns == ("Col1", "Col2")
    assert len(body.rows) == 1
    assert body.rows[0].cells[0].text == "x"
    assert body.rows[0].cells[0].column_key == "Col1"


# ---------------------------------------------------------------------------
# 5. Semantic projection carries table structure
# ---------------------------------------------------------------------------


def test_semantic_structure_from_ir_carries_table_bodies() -> None:
    """Semantic projection from IR must carry TableBody in wording facet."""
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Test Section"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(
                IRNode(kind=IRNodeKind.CONTENT, text="Before table.",
                       children=(
                           IRNode(kind=IRNodeKind.TABLE, children=(
                               IRNode(kind=IRNodeKind.ROW, children=(
                                   IRNode(kind=IRNodeKind.HEADER_CELL, text="H1"),
                                   IRNode(kind=IRNodeKind.HEADER_CELL, text="H2"),
                               )),
                               IRNode(kind=IRNodeKind.ROW, children=(
                                   IRNode(kind=IRNodeKind.CELL, text="a"),
                                   IRNode(kind=IRNodeKind.CELL, text="b"),
                               )),
                           )),
                       )),
            )),
        ),
    )

    sem = semantic_structure_from_ir(section)
    assert sem is not None

    # The section's subsection child should have table bodies
    subsection = sem.children[0]
    assert subsection.kind == "subsection"

    wording_facets = [f for f in subsection.facets if f.kind == "wording"]
    assert len(wording_facets) == 1
    wording = wording_facets[0]

    # Flat text must still be present for backward compatibility
    assert wording.text

    # Structured table bodies must be present
    assert len(wording.tables) == 1
    tb = wording.tables[0]
    assert tb.columns == ("H1", "H2")
    assert len(tb.rows) == 1
    assert tb.rows[0].cells[0].text == "a"


def test_semantic_structure_from_oracle_carries_table_bodies() -> None:
    """Semantic projection from oracle XML must carry TableBody in wording facet."""
    xml = etree.fromstring(
        """
        <section>
          <num>1</num>
          <subsection>
            <content>
              <p>Introduction text.</p>
              <table>
                <tr><th>Key</th><th>Val</th></tr>
                <tr><td>foo</td><td>bar</td></tr>
              </table>
            </content>
          </subsection>
        </section>
        """
    )

    sem = semantic_structure_from_oracle(xml)
    assert sem is not None

    subsection = sem.children[0]
    assert subsection.kind == "subsection"

    wording_facets = [f for f in subsection.facets if f.kind == "wording"]
    assert len(wording_facets) == 1
    wording = wording_facets[0]

    # Flat text preserved
    assert "foo" in wording.text
    assert "bar" in wording.text

    # Structured table data present
    assert len(wording.tables) == 1
    tb = wording.tables[0]
    assert tb.columns == ("Key", "Val")
    assert len(tb.rows) == 1
    assert tb.rows[0].cells[0].text == "foo"


# ---------------------------------------------------------------------------
# 6. Serialization round-trip
# ---------------------------------------------------------------------------


def test_serialize_ir_table_text_matches_irnode_to_text_structure() -> None:
    """_serialize_ir_table_text must produce flat text from structured IR table."""
    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="hello"),
                IRNode(kind=IRNodeKind.CELL, text="world"),
            )),
            IRNode(kind=IRNodeKind.ROW, children=(
                IRNode(kind=IRNodeKind.CELL, text="foo"),
                IRNode(kind=IRNodeKind.CELL, text=""),
            )),
        ),
    )

    text = _serialize_ir_table_text(table)
    assert "hello" in text
    assert "world" in text
    assert "foo" in text


def test_facet_to_dict_includes_tables() -> None:
    """SemanticStructureFacet.to_dict must serialize table structure."""
    from lawvm.semantic.model import SemanticStructureFacet

    facet = SemanticStructureFacet(
        kind="wording",
        text="some text",
        tables=(
            TableBody(
                table_id="t0",
                columns=("A", "B"),
                rows=(
                    TableRow(
                        row_key=RowKey(basis="ordinal", value="1", strength="weak"),
                        cells=(
                            TableCell(column_key="A", text="x"),
                            TableCell(column_key="B", text="y"),
                        ),
                    ),
                ),
            ),
        ),
    )

    d = facet.to_dict()
    assert "text" in d
    assert "tables" in d
    assert len(d["tables"]) == 1
    assert d["tables"][0]["columns"] == ["A", "B"]
    assert d["tables"][0]["rows"][0]["cells"][0]["text"] == "x"


def test_facet_to_dict_omits_tables_when_empty() -> None:
    """SemanticStructureFacet.to_dict must NOT include 'tables' key when empty."""
    from lawvm.semantic.model import SemanticStructureFacet

    facet = SemanticStructureFacet(kind="wording", text="some text")
    d = facet.to_dict()
    assert "tables" not in d


# ---------------------------------------------------------------------------
# 7. xml_element_to_text: canonical oracle-side text extractor
# ---------------------------------------------------------------------------


def test_xml_element_to_text_no_table_matches_irnode_to_text() -> None:
    """xml_element_to_text on a plain section must produce text consistent with irnode_to_text."""
    xml = etree.fromstring(
        """
        <section>
          <num>1</num>
          <subsection>
            <num>1</num>
            <content>
              <p>Tässä laissa tarkoitetaan palveluntarjoajalla yhtiötä.</p>
            </content>
          </subsection>
        </section>
        """
    )

    via_xml_element = xml_element_to_text(xml)
    via_ir = irnode_to_text(xml_to_ir_node(xml))

    assert via_xml_element == via_ir
    assert "Tässä laissa" in via_xml_element


def test_xml_element_to_text_with_table_matches_irnode_to_text() -> None:
    """xml_element_to_text on a section with a <table> must match irnode_to_text pipeline."""
    xml = etree.fromstring(
        """
        <section>
          <num>2</num>
          <subsection>
            <content>
              <p>Alla olevassa taulukossa esitetään:</p>
              <table>
                <tr><th>Tuote</th><th>Verokanta</th></tr>
                <tr><td>Elintarvikkeet</td><td>14 %</td></tr>
                <tr><td>Kirjat</td><td>10 %</td></tr>
              </table>
            </content>
          </subsection>
        </section>
        """
    )

    via_xml_element = xml_element_to_text(xml)
    via_ir = irnode_to_text(xml_to_ir_node(xml))

    assert via_xml_element == via_ir
    assert "Tuote" in via_xml_element
    assert "Elintarvikkeet" in via_xml_element


def test_xml_element_to_text_consistent_for_plain_text_content() -> None:
    """xml_element_to_text must contain expected plain text for basic content nodes."""
    xml = etree.fromstring(
        """
        <content>
          <p>Laki tulee voimaan 1 päivänä tammikuuta 2025.</p>
        </content>
        """
    )

    text = xml_element_to_text(xml)
    assert "Laki tulee voimaan" in text
    assert "tammikuuta 2025" in text


def test_xml_element_to_text_table_text_present() -> None:
    """xml_element_to_text on a table-containing content element must include table cell text."""
    xml = etree.fromstring(
        """
        <content>
          <p>Intro text.</p>
          <table>
            <tr><td>CellA</td><td>CellB</td></tr>
          </table>
        </content>
        """
    )

    text = xml_element_to_text(xml)
    ir_text = irnode_to_text(xml_to_ir_node(xml))

    assert text == ir_text
    assert "CellA" in text
    assert "CellB" in text
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import _xml_table_to_ir, xml_element_to_text, xml_to_ir_node
