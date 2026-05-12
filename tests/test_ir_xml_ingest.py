from lxml import etree

from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import _collapse_text, xml_body_to_ir, xml_to_ir_node


def test_xml_to_ir_node_merges_split_numbered_paragraph_tail() -> None:
    xml = etree.fromstring(
        """
        <subsection xmlns="urn:test">
          <intro>Luettelo on:</intro>
          <paragraph eId="p1">
            <num>2)</num>
            <content>pyynnosta tai hakemuksesta annettava</content>
          </paragraph>
          <paragraph eId="p2">
            <content>paatos tai lupa.</content>
          </paragraph>
        </subsection>
        """
    )

    ir = xml_to_ir_node(xml)
    assert len(ir.children) == 2
    para = ir.children[1]
    assert para.kind == IRNodeKind.PARAGRAPH
    assert [c.kind for c in para.children] == [IRNodeKind.NUM, IRNodeKind.CONTENT, IRNodeKind.CONTENT]
    assert para.children[1].text == "pyynnosta tai hakemuksesta annettava"
    assert para.children[2].text == "paatos tai lupa."


def test_xml_body_to_ir_preserves_top_level_schedules_as_supplements() -> None:
    xml = etree.fromstring(
        """
        <act xmlns="urn:test">
          <docNumber>1/2026</docNumber>
          <docTitle>Supplement Act</docTitle>
          <body>
            <section>
              <num>1 §</num>
              <content>Body text.</content>
            </section>
          </body>
          <schedule>
            <num>1</num>
            <heading>Schedule</heading>
            <content>Schedule text.</content>
          </schedule>
        </act>
        """
    )

    statute = xml_body_to_ir(xml)

    assert statute.statute_id == "1/2026"
    assert len(statute.supplements) == 1
    assert statute.supplements[0].kind == IRNodeKind.SCHEDULE
    assert statute.supplements[0].label == "1"
    assert statute.supplements[0].children[0].kind == IRNodeKind.NUM
    assert statute.metadata == {}


def test_xml_body_to_ir_records_unsupported_top_level_supplement_tags() -> None:
    xml = etree.fromstring(
        """
        <act xmlns="urn:test">
          <docNumber>2/2026</docNumber>
          <docTitle>Unsupported Supplement Act</docTitle>
          <body />
          <annex>
            <content>Annex text.</content>
          </annex>
        </act>
        """
    )

    statute = xml_body_to_ir(xml)

    assert statute.supplements == ()
    observations = statute.metadata["xml_ingest_observations"]
    assert observations == (
        {
            "kind": "XML_INGEST.UNSUPPORTED_TOP_LEVEL_SUPPLEMENT",
            "family": "source_pathology",
            "phase": "ingest",
            "tag": "annex",
            "message": "Top-level supplement tag is not mapped to a supported IR supplement kind.",
        },
    )


def test_xml_to_ir_node_preserves_separate_complete_numbered_paragraphs() -> None:
    xml = etree.fromstring(
        """
        <subsection xmlns="urn:test">
          <paragraph eId="p1">
            <num>1)</num>
            <content>ensimmainen kohta;</content>
          </paragraph>
          <paragraph eId="p2">
            <content>itsenainen jatko.</content>
          </paragraph>
        </subsection>
        """
    )

    ir = xml_to_ir_node(xml)
    assert len(ir.children) == 2
    assert ir.children[0].kind == IRNodeKind.PARAGRAPH
    assert ir.children[1].kind == IRNodeKind.PARAGRAPH


def test_xml_to_ir_node_splits_trailing_content_paragraph_into_new_subsection() -> None:
    xml = etree.fromstring(
        """
        <section xmlns="urn:test">
          <num>5 §</num>
          <heading>Ilmoitusvelvollisuus</heading>
          <subsection>
            <intro>
              <p>Velvollinen tekemään ilmoituksen vaalirahoituksesta on:</p>
            </intro>
            <paragraph>
              <num>1)</num>
              <content><p>ensimmainen kohta;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>toinen kohta.</p></content>
            </paragraph>
            <paragraph>
              <content><p>Itsenainen seuraava momentti.</p></content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    ir = xml_to_ir_node(xml)

    subsections = [c for c in ir.children if c.kind == IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["1", "2"]
    assert [c.kind for c in subsections[0].children] == [IRNodeKind.INTRO, IRNodeKind.PARAGRAPH, IRNodeKind.PARAGRAPH]
    assert [c.kind for c in subsections[1].children] == [IRNodeKind.CONTENT]
    assert subsections[1].children[0].text == "Itsenainen seuraava momentti."


def test_xml_to_ir_rehomes_orphaned_letter_paragraphs() -> None:
    """Letter-labelled paragraphs that continue a subparagraph sequence are
    re-homed as subparagraphs of the preceding numbered paragraph.
    Mirrors the Finlex encoding error in 2025/1178 §2 mom.1 where c) and d)
    are misencoded as paragraph siblings of paragraph 1) instead of as
    subparagraphs alongside a) and b).
    """
    xml = etree.fromstring(
        """
        <subsection xmlns="urn:test">
          <intro><p>Maksullisia suoritteita ovat:</p></intro>
          <paragraph eId="p1">
            <num>1)</num>
            <intro><p>seuraavat liikennesuoritteet:</p></intro>
            <subparagraph eId="p1_a">
              <num>a)</num>
              <content><p>ensimmainen alakohta;</p></content>
            </subparagraph>
            <subparagraph eId="p1_b">
              <num>b)</num>
              <content><p>toinen alakohta;</p></content>
            </subparagraph>
          </paragraph>
          <paragraph eId="p2">
            <num>c)</num>
            <content><p>kolmas alakohta (virheellisesti paragraph-tasolla);</p></content>
          </paragraph>
          <paragraph eId="p3">
            <num>d)</num>
            <content><p>neljas alakohta (virheellisesti paragraph-tasolla);</p></content>
          </paragraph>
          <paragraph eId="p4">
            <num>2)</num>
            <intro><p>seuraavat muut suoritteet:</p></intro>
            <subparagraph eId="p4_a">
              <num>a)</num>
              <content><p>muun listan a-kohta;</p></content>
            </subparagraph>
          </paragraph>
        </subsection>
        """
    )

    ir = xml_to_ir_node(xml)
    paragraphs = [c for c in ir.children if c.kind == IRNodeKind.PARAGRAPH]
    # Only two paragraphs remain: 1) and 2)
    assert [p.label for p in paragraphs] == ["1", "2"]
    # Paragraph 1) should now have four subparagraphs: a, b, c, d
    subparas_1 = [c for c in paragraphs[0].children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subparas_1] == ["a", "b", "c", "d"]
    # Paragraph 2) keeps its own subparagraph a)
    subparas_2 = [c for c in paragraphs[1].children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subparas_2] == ["a"]


class TestCollapseTextSpacing:
    """Verify that _collapse_text inserts whitespace between adjacent sibling elements.

    Finlex AKN XML frequently encodes text across adjacent <p> elements inside
    <content> with no intervening whitespace (no tail text on the preceding <p>).
    Without the fix, this produces concatenated text like "nojalla,suorittaa"
    instead of "nojalla, suorittaa".
    """

    def test_adjacent_p_elements_get_space(self) -> None:
        el = etree.fromstring("<content><p>nojalla,</p><p>suorittaa</p></content>")
        assert _collapse_text(el) == "nojalla, suorittaa"

    def test_multiple_adjacent_p_elements(self) -> None:
        el = etree.fromstring("<content><p>a</p><p>b</p><p>c</p></content>")
        assert _collapse_text(el) == "a b c"

    def test_already_spaced_elements_no_double_space(self) -> None:
        el = etree.fromstring("<content><p>word1 </p><p>word2</p></content>")
        assert _collapse_text(el) == "word1 word2"

    def test_inline_italic_preserves_word(self) -> None:
        """Inline formatting within a word must not introduce spaces."""
        el = etree.fromstring("<p>eri<b>tyis</b>tapaus</p>")
        assert _collapse_text(el) == "erityistapaus"

    def test_inline_italic_with_surrounding_spaces(self) -> None:
        el = etree.fromstring('<p>tämän <i>lain</i> nojalla</p>')
        assert _collapse_text(el) == "tämän lain nojalla"

    def test_comma_before_adjacent_element(self) -> None:
        el = etree.fromstring("<content><p>teksti,</p><p>jatko</p></content>")
        assert _collapse_text(el) == "teksti, jatko"

    def test_plain_text_no_children(self) -> None:
        el = etree.fromstring("<content>plain text</content>")
        assert _collapse_text(el) == "plain text"

    def test_single_child_element(self) -> None:
        el = etree.fromstring("<content><p>only one</p></content>")
        assert _collapse_text(el) == "only one"

    def test_nested_italic_in_adjacent_p(self) -> None:
        el = etree.fromstring(
            "<content><p>nojalla,</p><p>suorittaa <i>tästä</i> asiasta</p></content>"
        )
        assert _collapse_text(el) == "nojalla, suorittaa tästä asiasta"


def test_xml_to_ir_adjacent_p_spacing_in_content() -> None:
    """Full XML-to-IR pipeline preserves spacing between adjacent <p> elements."""
    xml = etree.fromstring(
        """
        <subsection xmlns="urn:test">
          <content><p>nojalla,</p><p>suorittaa tämän lain</p></content>
        </subsection>
        """
    )
    ir = xml_to_ir_node(xml)
    content_node = next(c for c in ir.children if c.kind == IRNodeKind.CONTENT)
    assert content_node.text == "nojalla, suorittaa tämän lain"
    assert irnode_to_text(ir) == "nojalla, suorittaa tämän lain"
