from lxml import etree

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.tree_ops import check_invariants
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.grafter import _fi_label_postprocessor
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_normalize import normalize_source_ir


def test_fi_xml_to_ir_node_preserves_table_rows_as_paragraphs() -> None:
    xml = etree.fromstring(
        """
        <subsection>
          <content>
            <p>Käräjäoikeuksien kansliat ja istuntopaikat sijaitsevat seuraavasti:</p>
            <table>
              <tr>
                <td><p>Käräjäoikeus</p></td>
                <td><p>Kanslia (s = sivukanslia)</p></td>
                <td><p>Istunnot</p></td>
              </tr>
              <tr>
                <td><p>Seinäjoki</p></td>
                <td><p>Seinäjoki</p></td>
                <td><p>Seinäjoki</p></td>
              </tr>
              <tr>
                <td><p/></td>
                <td colspan="2"><p>Jalasjärvi</p></td>
              </tr>
              <tr>
                <td><p>Tampere</p></td>
                <td><p>Tampere</p></td>
                <td><p>Tampere</p></td>
              </tr>
            </table>
          </content>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert subsection.kind == IRNodeKind.SUBSECTION
    assert subsection.children[0].kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO)
    assert subsection.children[0].text.startswith("Käräjäoikeuksien kansliat")
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["1", "2"]
    assert paragraphs[0].attrs["row_anchor"] == "seinäjoki"
    assert irnode_to_text(paragraphs[0]) == "Seinäjoki Seinäjoki Seinäjoki Jalasjärvi"
    assert paragraphs[1].attrs["row_anchor"] == "tampere"


def test_fi_xml_to_ir_node_preserves_terminal_omission_inside_content_wrapper() -> None:
    xml = etree.fromstring(
        """
        <content>
          <p>Raasepori Tammisaari Hanko Kirkkonummi</p>
          <p class="omission"/>
        </content>
        """
    )

    content = fi_xml_to_ir_node(xml)

    assert content.kind == IRNodeKind.CONTENT
    assert any(child.kind == IRNodeKind.OMISSION for child in content.children)
    assert irnode_to_text(content) == "Raasepori Tammisaari Hanko Kirkkonummi"


def test_fi_xml_to_ir_node_hoists_inline_content_omission_to_subsection_level() -> None:
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Eläkkeen saamisen edellytyksenä on:</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>että luopuja ... vähintään kaksi hehtaaria;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content>
              <p>että luopuminen on tapahtunut ...</p>
              <p class="omission"/>
            </content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert subsection.kind == IRNodeKind.SUBSECTION
    assert subsection.children[-1].kind == IRNodeKind.OMISSION
    paragraph = next(child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH and child.label == "4")
    content = next(child for child in paragraph.children if child.kind == IRNodeKind.CONTENT)
    assert all(child.kind != IRNodeKind.OMISSION for child in content.children)
    assert irnode_to_text(paragraph) == "4) että luopuminen on tapahtunut ..."


def test_fi_xml_to_ir_node_hoists_trailing_paragraph_to_wrap_up() -> None:
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Joka tahallaan tai huolimattomuudesta</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>rikkoo 1 §:n säännöksiä;</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>rikkoo 2 §:n säännöksiä;</p></content>
          </paragraph>
          <paragraph>
            <content><p>on tuomittava sakkoon.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert subsection.kind == IRNodeKind.SUBSECTION
    assert subsection.children[-1].kind == IRNodeKind.WRAP_UP
    assert subsection.children[-1].text == "on tuomittava sakkoon."
    assert [child.kind for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH] == [
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]


def test_fi_xml_to_ir_node_nests_lettered_subparagraphs_under_digit_paragraphs() -> None:
    """Flat-encoded letter sub-items must be nested as subparagraphs under preceding digit items.

    Finnish AKN amendment XML (e.g. 2024/307 § 102 subsection 6) encodes:
        paragraph 1)
        paragraph 2)
        paragraph a)   <- sub-item of 2)
        paragraph b)   <- sub-item of 2)
        ...
        paragraph g)   <- sub-item of 2) — not a duplicate, still must be nested
        paragraph 3)
        paragraph a)   <- sub-item of 3) — DUPLICATE label
        paragraph b)   <- sub-item of 3) — DUPLICATE label
        ...
    All letter-labeled paragraphs following a digit-labeled paragraph must be
    re-encoded as subparagraph children of that digit paragraph to avoid
    duplicate labels and match the consolidated oracle's nested structure.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Terveydenhuollon palvelunantajan tulee aloittaa asiakirjojen tallentaminen seuraavasti:</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>viimeistään 1 päivänä maaliskuuta 2025 koulupsykologien laatimat asiakirjat;</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>viimeistään 1 päivänä lokakuuta 2026:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>ajanvarausasiakirja;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>seulontatutkimuksista syntyvät laboratoriotulokset;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>ajoterveyteen liittyvät todistukset;</p></content>
          </paragraph>
          <paragraph>
            <num>3)</num>
            <content><p>viimeistään 1 päivänä lokakuuta 2029:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>hoitotyön päivittäismerkinnät;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>seulontatutkimuksista syntyvät kuvantamisasiakirjat;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>video- ja äänitallenteet;</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # No invariant violations (no duplicate paragraph labels)
    assert check_invariants(subsection) == []

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    # Only the digit-labeled paragraphs remain as direct children
    assert [p.label for p in paragraphs] == ["1", "2", "3"]

    # Letter-labeled items nested as subparagraphs under their parent digit paragraph
    para2 = next(p for p in paragraphs if p.label == "2")
    subs2 = [ch for ch in para2.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs2] == ["a", "b", "c"]

    para3 = next(p for p in paragraphs if p.label == "3")
    subs3 = [ch for ch in para3.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs3] == ["a", "b", "c"]


def test_fi_xml_to_ir_node_nests_repeated_simple_letter_families_for_1997_1339_section_4() -> None:
    """Repeated simple-letter families must still nest across multiple digit items.

    This matches the 1997/1339 section 4 shape: the subsection has multiple
    numbered items and each numbered item carries a repeated simple-letter
    sublist, but the source omits a colon-style introducer.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Kiinteistön tuloslaskelma</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>Alkuarvo</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Ensimmäinen alaerä</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Toinen alaerä</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>Seuraava pääkohta</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Kolmas alaerä</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Neljäs alaerä</p></content>
          </paragraph>
          <paragraph>
            <num>3)</num>
            <content><p>Kolmas pääkohta</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Viides alaerä</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Kuudes alaerä</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2", "3"]
    assert [s.label for s in next(p for p in paragraphs if p.label == "1").children if s.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
    assert [s.label for s in next(p for p in paragraphs if p.label == "2").children if s.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
    assert [s.label for s in next(p for p in paragraphs if p.label == "3").children if s.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]


def test_fi_xml_to_ir_node_nests_repeated_roman_subitems_under_letter_parents() -> None:
    """Repeated roman-style labels should nest under lettered parent items."""
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Ajoneuvon vaatimustenmukaisuus voidaan osoittaa:</p></intro>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen tapaus;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen tapaus;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>kolmas tapaus;</p></content>
          </paragraph>
          <paragraph>
            <num>d)</num>
            <content><p>hyväksytyn asiantuntijan selvityksen perusteella:</p></content>
          </paragraph>
          <paragraph>
            <num>i)</num>
            <content><p>a-c kohdassa tarkoitetussa tapauksessa; tai</p></content>
          </paragraph>
          <paragraph>
            <num>ii)</num>
            <content><p>muussa tapauksessa.</p></content>
          </paragraph>
          <paragraph>
            <num>e)</num>
            <content><p>tutkimuslaitoksen selvityksen perusteella:</p></content>
          </paragraph>
          <paragraph>
            <num>i)</num>
            <content><p>a-d kohdassa tarkoitetussa tapauksessa; tai</p></content>
          </paragraph>
          <paragraph>
            <num>ii)</num>
            <content><p>kun muuta selvitystä ei ole saatavilla.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["a", "b", "c", "d", "e"]
    assert [ch.label for ch in next(p for p in paragraphs if p.label == "d").children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]
    assert [ch.label for ch in next(p for p in paragraphs if p.label == "e").children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]


def test_fi_xml_to_ir_node_does_not_nest_unique_roman_items_without_duplicate_family() -> None:
    """Unique roman-style items must stay flat when there is no repeated family."""
    xml = etree.fromstring(
        """
        <subsection>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen tapaus:</p></content>
          </paragraph>
          <paragraph>
            <num>i)</num>
            <content><p>alavaihtoehto yksi;</p></content>
          </paragraph>
          <paragraph>
            <num>ii)</num>
            <content><p>alavaihtoehto kaksi.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["a", "i", "ii"]
    for p in paragraphs:
        assert not any(ch.kind == IRNodeKind.SUBPARAGRAPH for ch in p.children)


def test_fi_xml_to_ir_node_recovers_dotted_intro_paragraph_label() -> None:
    """Intro text like '2. Muut velat' must recover the paragraph label as 2."""
    xml = etree.fromstring(
        """
        <subsection>
          <paragraph>
            <num>1.</num>
            <content><p>Ostovelat</p></content>
          </paragraph>
          <paragraph>
            <intro><p>2. Muut velat</p></intro>
            <subparagraph>
              <num>a)</num>
              <content><p>Johdannaissopimusten arvonalennukset</p></content>
            </subparagraph>
            <subparagraph>
              <num>b)</num>
              <content><p>Muut</p></content>
            </subparagraph>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1.", "2."]
    assert check_invariants(subsection) == []


def test_fi_xml_to_ir_node_does_not_nest_when_no_duplicate_letter_labels() -> None:
    """Flat letter-labeled list with no duplicates must not be modified.

    A subsection with paragraphs a), b), c) at the same level (no integer
    parents, no duplicates) must stay flat — the nesting heuristic must not
    fire and change the structure.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Palvelunantajan on:</p></intro>
          <paragraph>
            <num>a)</num>
            <content><p>huolehtia asiakkaiden tietoturvasta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>varmistaa järjestelmien yhteentoimivuus;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>raportoida poikkeamista;</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # No invariant violations
    assert check_invariants(subsection) == []

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    # All letter paragraphs stay as direct children (no nesting triggered)
    assert [p.label for p in paragraphs] == ["a", "b", "c"]
    # None have subparagraph children from this path
    for p in paragraphs:
        assert not any(ch.kind == IRNodeKind.SUBPARAGRAPH for ch in p.children)


def test_fi_xml_to_ir_node_nests_repeated_simple_letter_families_for_2002_64() -> None:
    """Repeated simple-letter families must nest even without an explicit introducer.

    This mirrors the 2002/64 shape where several numbered definition items are
    followed by repeated a/b/c subitems in flat XML. The parser should recover
    the nested structure instead of leaving duplicate labels at subsection scope.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Tässä asetuksessa tarkoitetaan:</p></intro>
          <paragraph>
            <num>4)</num>
            <content><p>matkakeskuksella henkilöliikenteen eri liikennemuotojen yhteistä asemaa tai yhteistyössä toimivia erillisiä asemia,</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>joka on tärkeä tai jotka ovat yhdessä tärkeä henkilöliikenteen risteysasema, ja</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>jolta tai joilta on saatavilla ainakin rautatieliikenteen, linja-autojen paikallis- ja kaukoliikenteen sekä taksien liikennepalveluja sekä liikennepalvelujen käyttöön liittyviä muita palveluja, joita ovat ainakin lipunmyynti ja informaatio;</p></content>
          </paragraph>
          <paragraph>
            <num>5)</num>
            <content><p>kaupunkimaisella paikallisliikenteellä linja- tai ostoliikennettä, joka palvelee ensisijaisesti taajama-alueen sisäisiä matkustustarpeita ja</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>jota ajetaan vähintään kuusi vuoroa päivässä ja jonka vuorovälit ovat korkeintaan kaksi tuntia; tai</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>joka on muuhun kaupunkimaiseen paikallisliikenteeseen integroitu palvelulinja;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>joka kelpaa kaikessa tai lähes kaikessa kelpoisuusalueen sisäisessä linja- ja ostoliikenteessä, ja</p></content>
          </paragraph>
          <paragraph>
            <num>6)</num>
            <content><p>seutulipulla henkilökohtaista matkalippua,</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>joka on kunnan kaikille asukkaille samaan hintaan myytävä kausilippu,</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>jonka kelpoisuusalue muodostuu kaupungista tai taajamasta ja niitä ympäröivästä työssäkäyntialueesta, ja</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>joka kelpaa kaikessa tai lähes kaikessa kelpoisuusalueen linja- ja ostoliikenteessä;</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["4", "5", "6"]
    assert [ch.label for ch in next(p for p in paragraphs if p.label == "4").children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b"]
    assert [ch.label for ch in next(p for p in paragraphs if p.label == "5").children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c"]
    assert [ch.label for ch in next(p for p in paragraphs if p.label == "6").children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c"]


def test_fi_xml_to_ir_node_nests_repeated_simple_digit_families_for_1997_108_section_2() -> None:
    """Repeated digit families must nest even when the source repeats the outer label."""
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Puolustustarvikkeita ovat:</p></intro>
          <paragraph>
            <num>4)</num>
            <content><p>Tuoteluokka 4</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>1 Automaattiaseet ja tarkkuuskiväärit;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>2 Isokaliiperiset aseet ja heittimet;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>3 Pommit, torpedot, raketit ja vastaavat laitteet ja ohjukset;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>4 Erityisesti sotilaskäyttöön suunnitellut tulenjohto- ja varoitusjärjestelmät;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>5 Myrkylliset taisteluaineet ja kyynelkaasut;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>6 Sotilasräjähdysaineet, -ajoaineet ja -ruudit;</p></content>
          </paragraph>
          <paragraph>
            <num>4)</num>
            <content><p>7 Ohjelmistot, jotka on erityisesti suunniteltu sotilaallisiin sovelluksiin.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["4"]
    para4 = paragraphs[0]
    assert [sp.label for sp in para4.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["1", "2", "3", "4", "5", "6", "7"]


def test_fi_xml_to_ir_node_nests_repeated_simple_digit_families_for_1997_108_section_3() -> None:
    """Repeated digit families must nest even when only the leading number changes."""
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Kuhunkin tuoteluokkaan kuuluvia puolustustarvikkeita ovat myös:</p></intro>
          <paragraph>
            <num>1.</num>
            <content><p>1 Ohjelmistot, jotka on erityisesti suunniteltu tai muunnettu tähän asetukseen kuuluvan varustuksen tai materiaalin kehittämiseen, tuotantoon tai käyttöön; sekä</p></content>
          </paragraph>
          <paragraph>
            <num>1.</num>
            <content><p>2 Varustus ja teknologia tähän asetukseen kuuluvien tuotteiden tuottamista varten.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1."]

    para1 = paragraphs[0]
    assert [sp.label for sp in para1.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["2"]


def test_fi_xml_to_ir_node_does_not_nest_unique_digit_paragraph_family() -> None:
    """Unique digit labels must stay flat even if body text starts with a number."""
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Tässä momentissa säädetään seuraavaa:</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>1 Ensimmäinen itsenäinen kohta.</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>2 Toinen itsenäinen kohta.</p></content>
          </paragraph>
          <paragraph>
            <num>3)</num>
            <content><p>3 Kolmas itsenäinen kohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2", "3"]
    assert all(not any(ch.kind == IRNodeKind.SUBPARAGRAPH for ch in p.children) for p in paragraphs)


def test_fi_xml_to_ir_node_nests_mixed_single_and_compound_letters_without_introducer() -> None:
    """Mixed single-letter and compound-letter families must nest even without a colon intro.

    This matches the real 2015/1752 shape behind the 1997/1339 duplicate-label cluster:
    digit items 5/6/7/10 are followed by flat letter labels where the single letters
    repeat across the section, but compound labels (aa/ab/ba/bb) signal that the
    whole family is a nested sublist rather than a genuinely flat appendix.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Materiaalit ja palvelut</p></intro>
          <paragraph>
            <num>5)</num>
            <content><p>Aineet, tarvikkeet ja tavarat</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Aineet</p></content>
          </paragraph>
          <paragraph>
            <num>aa)</num>
            <content><p>Ostot tilikauden aikana</p></content>
          </paragraph>
          <paragraph>
            <num>ab)</num>
            <content><p>Varastojen muutos</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Tavarat</p></content>
          </paragraph>
          <paragraph>
            <num>6)</num>
            <content><p>Henkilöstökulut</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Palkat ja palkkiot</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Henkilösivukulut</p></content>
          </paragraph>
          <paragraph>
            <num>ba)</num>
            <content><p>Eläkekulut</p></content>
          </paragraph>
          <paragraph>
            <num>bb)</num>
            <content><p>Muut henkilösivukulut</p></content>
          </paragraph>
          <paragraph>
            <num>7)</num>
            <content><p>Poistot ja arvonalentumiset</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Suunnitelman mukaiset poistot</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Arvonalentumiset pysyvien vastaavien hyödykkeistä</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>Vaihtuvien vastaavien poikkeukselliset arvonalentumiset</p></content>
          </paragraph>
          <paragraph>
            <num>10)</num>
            <content><p>Rahoitustuotot ja -kulut</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>Tuotot osuuksista saman konsernin yrityksissä</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>Tuotot osuuksista omistusyhteysyrityksissä</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>Tuotot muista pysyvien vastaavien sijoituksista</p></content>
          </paragraph>
          <paragraph>
            <num>d)</num>
            <content><p>Muut korko- ja rahoitustuotot</p></content>
          </paragraph>
          <paragraph>
            <num>e)</num>
            <content><p>Arvonalentumiset pysyvien vastaavien sijoituksista</p></content>
          </paragraph>
          <paragraph>
            <num>f)</num>
            <content><p>Arvonalentumiset vaihtuvien vastaavien rahoitusarvopapereista</p></content>
          </paragraph>
          <paragraph>
            <num>g)</num>
            <content><p>Korkokulut ja muut rahoituskulut</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["5", "6", "7", "10"]

    para5 = next(p for p in paragraphs if p.label == "5")
    assert [ch.label for ch in para5.children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "aa", "ab", "b"]

    para6 = next(p for p in paragraphs if p.label == "6")
    assert [ch.label for ch in para6.children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "ba", "bb"]

    para10 = next(p for p in paragraphs if p.label == "10")
    assert [ch.label for ch in para10.children if ch.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d", "e", "f", "g"]


def test_fi_xml_to_ir_node_nests_repeated_roman_subitems_under_alpha_parents() -> None:
    """Repeated roman labels under alphabetic parents must be re-nested.

    This matches the malformed 2018/1184 -> 2002/1244 §21c source shape:
    d) and e) are parent items, each followed by flat i)/ii) subitems.  The
    XML carries the whole structure as one flat paragraph sibling stream.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Vaatimustenmukaisuus voidaan osoittaa:</p></intro>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen kohta;</p></content>
          </paragraph>
          <paragraph>
            <num>d)</num>
            <content><p>hyväksytyn asiantuntijan selvityksen perusteella:</p></content>
          </paragraph>
          <paragraph>
            <num>i)</num>
            <content><p>a-c kohdassa tarkoitetussa tapauksessa; tai</p></content>
          </paragraph>
          <paragraph>
            <num>ii)</num>
            <content><p>kun muu edellytys täyttyy;</p></content>
          </paragraph>
          <paragraph>
            <num>e)</num>
            <content><p>tutkimuslaitoksen selvityksen perusteella:</p></content>
          </paragraph>
          <paragraph>
            <num>i)</num>
            <content><p>a-d kohdassa tarkoitetussa tapauksessa; tai</p></content>
          </paragraph>
          <paragraph>
            <num>ii)</num>
            <content><p>kun muu lisäedellytys täyttyy.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["a", "d", "e"]

    para_d = next(p for p in paragraphs if p.label == "d")
    assert [sp.label for sp in para_d.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]

    para_e = next(p for p in paragraphs if p.label == "e")
    assert [sp.label for sp in para_e.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]


def test_alakohta_nesting_stage1_preceding_with_colon_intro() -> None:
    """Stage 1: letter items nest under preceding kohta when it ends with colon.

    Pattern: 4) text ending with colon: + a) sub; b) sub; c) sub + 5) next
    → a/b/c nested as subparagraphs under 4 (introducer present).
    Duplicate letter labels (a, b, c repeated under 4 and under a future item)
    trigger the heuristic.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Testaushetki:</p></intro>
          <paragraph>
            <num>4)</num>
            <content><p>momentti jossa on kaksoispiste:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>kolmas alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>5)</num>
            <content><p>seuraava kohta:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>eri alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen eri alakohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [ch for ch in subsection.children if ch.kind == IRNodeKind.PARAGRAPH]
    # Only digit-labeled paragraphs remain as direct children
    assert [p.label for p in paragraphs] == ["4", "5"]

    para4 = next(p for p in paragraphs if p.label == "4")
    subs4 = [ch for ch in para4.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs4] == ["a", "b", "c"]

    para5 = next(p for p in paragraphs if p.label == "5")
    subs5 = [ch for ch in para5.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs5] == ["a", "b"]


def test_alakohta_nesting_stage3_flat_when_no_introducer() -> None:
    """Stage 3: letter items stay flat when preceding kohta has no introducer.

    Pattern: 4) text ending with semicolon; + a) sub; b) sub; c) sub + 5) next
    → a/b/c kept as flat siblings (no introducer on either side).
    Duplicate labels trigger the heuristic, but flat is the safe default.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Luettelo:</p></intro>
          <paragraph>
            <num>4)</num>
            <content><p>kohta ilman johdantoa;</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen kirjainpykälä;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen kirjainpykälä;</p></content>
          </paragraph>
          <paragraph>
            <num>c)</num>
            <content><p>kolmas kirjainpykälä;</p></content>
          </paragraph>
          <paragraph>
            <num>5)</num>
            <content><p>seuraava kohta;</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>eri kohta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen eri kohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # All items stay flat — no nesting when no introducer signal
    paragraphs = [ch for ch in subsection.children if ch.kind == IRNodeKind.PARAGRAPH]
    labels = [p.label for p in paragraphs]
    # All items should be present as direct children, no nested structure
    assert "4" in labels
    assert "5" in labels
    # a/b/c appear as flat siblings, not nested
    assert labels.count("a") >= 1
    assert labels.count("b") >= 1
    assert labels.count("c") >= 1
    # No paragraph has subparagraph children from this path
    for p in paragraphs:
        assert not any(ch.kind == IRNodeKind.SUBPARAGRAPH for ch in p.children)


def test_alakohta_nesting_stage2_following_parent_with_introducer() -> None:
    """Stage 2: letter items nest under the following kohta when it has an introducer.

    Pattern: 4) text; 5) introducing text: + a) sub; b) sub
    → a/b nested under 5 (following-parent).
    This tests the case where the letter items appear AFTER the introducing parent
    (not between two digit items), and the following parent is the introducer.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Testaushetki:</p></intro>
          <paragraph>
            <num>4)</num>
            <content><p>kohta ilman johdantoa;</p></content>
          </paragraph>
          <paragraph>
            <num>5)</num>
            <content><p>kohta jossa on johdanto:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>ensimmäinen alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>6)</num>
            <content><p>seuraava kohta:</p></content>
          </paragraph>
          <paragraph>
            <num>a)</num>
            <content><p>eri alakohta;</p></content>
          </paragraph>
          <paragraph>
            <num>b)</num>
            <content><p>toinen eri alakohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert check_invariants(subsection) == []
    paragraphs = [ch for ch in subsection.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["4", "5", "6"]

    para4 = next(p for p in paragraphs if p.label == "4")
    assert not any(ch.kind == IRNodeKind.SUBPARAGRAPH for ch in para4.children)

    para5 = next(p for p in paragraphs if p.label == "5")
    subs5 = [ch for ch in para5.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs5] == ["a", "b"]

    para6 = next(p for p in paragraphs if p.label == "6")
    subs6 = [ch for ch in para6.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [s.label for s in subs6] == ["a", "b"]


def test_fi_xml_to_ir_node_splits_flat_subsection_at_numbered_list_restart() -> None:
    """Flat-encoded multi-subsection structure must be split into separate subsections.

    Finnish AKN amendment XML sometimes encodes what should be multiple subsections
    as a single flat <subsection> whose paragraphs contain two numbered lists separated
    by an unlabeled content-only paragraph acting as an intro.  Example from 2018/555 § 5:

        <subsection>
            <intro>Riistaeläimiä ovat:</intro>
            <paragraph num=1>mammals</paragraph>
            <paragraph num=2>birds</paragraph>
            <paragraph>Rauhoittamattomia eläimiä ovat:</paragraph>  ← pseudo-intro
            <paragraph num=1>rodents</paragraph>
            <paragraph num=2>more birds</paragraph>
            <paragraph>Villiintyneeseen kissaan...</paragraph>       ← trailing prose
        </subsection>

    The result must be three addressable subsections with correct labels.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>5 §</num>
          <heading>Riistaeläimet ja rauhoittamattomat eläimet</heading>
          <subsection>
            <intro><p>Riistaeläimiä ovat:</p></intro>
            <paragraph>
              <num>1)</num>
              <content><p>villikani, metsäjänis, rusakko;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>kanadanhanhi, merihanhi;</p></content>
            </paragraph>
            <paragraph>
              <content><p>Rauhoittamattomia eläimiä ovat:</p></content>
            </paragraph>
            <paragraph>
              <num>1)</num>
              <content><p>metsämyyrä, vesimyyrä;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>korppi (poronhoitoalueella), varis;</p></content>
            </paragraph>
            <paragraph>
              <content><p>Villiintyneeseen kissaan sovelletaan, mitä rauhoittamattomista eläimistä säädetään.</p></content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert section.kind == IRNodeKind.SECTION
    assert section.label == "5"

    # Non-subsection children (num, heading) plus 3 subsections
    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 3, f"Expected 3 subsections, got {len(subsections)}"

    # Subsection 1: riistaeläimet
    sub1 = subsections[0]
    assert sub1.label == "1"
    intros1 = [ch for ch in sub1.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros1) == 1
    assert "Riistaeläimiä" in (intros1[0].text or "")
    paras1 = [ch for ch in sub1.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paras1] == ["1", "2"]

    # Subsection 2: rauhoittamattomat — the split content-only paragraph becomes intro
    sub2 = subsections[1]
    assert sub2.label == "2"
    intros2 = [ch for ch in sub2.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros2) == 1
    assert "Rauhoittamattomia" in (intros2[0].text or "")
    paras2 = [ch for ch in sub2.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paras2] == ["1", "2"]

    # Subsection 3: trailing prose
    sub3 = subsections[2]
    assert sub3.label == "3"
    paras3 = [ch for ch in sub3.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert len(paras3) == 0  # Subsection 3 has content directly, not paragraphs
    # The trailing prose is preserved
    full_text = irnode_to_text(sub3)
    assert "Villiintyneeseen" in full_text

    # No duplicate labels within any subsection
    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_does_not_split_single_numbered_list_subsection() -> None:
    """A subsection with a single numbered list and no pseudo-intro must not be split."""
    xml = etree.fromstring(
        """
        <section>
          <num>3 §</num>
          <subsection>
            <intro><p>Seuraavat eläimet ovat rauhoitettuja:</p></intro>
            <paragraph>
              <num>1)</num>
              <content><p>karhu;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>susi;</p></content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 1
    paras = [ch for ch in subsections[0].children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paras] == ["1", "2"]
    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_splits_embedded_section_restart_markers_into_sibling_sections() -> None:
    """Flat later-section markers inside one section must become sibling sections."""
    xml = etree.fromstring(
        """
        <chapter>
          <num>19 luku</num>
          <section>
            <num>19 §</num>
            <subsection>
              <content><p>Pesänselvittäjän on toimessaan noudatettava kaikkea huolellisuutta.</p></content>
            </subsection>
            <subsection>
              <content><p>20 §.</p></content>
            </subsection>
            <subsection>
              <content><p>Pesänselvittäjällä on oikeus saada kohtuullinen palkkio.</p></content>
            </subsection>
            <subsection>
              <content><p>21 §.</p></content>
            </subsection>
            <subsection>
              <content><p>Testamentin toimeenpanijalla on sama valtuus kuin pesänselvittäjällä.</p></content>
            </subsection>
            <subsection>
              <content><p>22 §.</p></content>
            </subsection>
            <subsection>
              <content><p>Oikeuden päätökseen ei saa hakea muutosta.</p></content>
            </subsection>
          </section>
        </chapter>
        """
    )

    chapter = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    sections = [child for child in chapter.children if child.kind == IRNodeKind.SECTION]
    assert [section.label for section in sections] == ["19", "20", "21", "22"]
    assert "Pesänselvittäjän on toimessaan" in irnode_to_text(sections[0])
    assert "Pesänselvittäjällä on oikeus saada" in irnode_to_text(sections[1])
    assert "Testamentin toimeenpanijalla" in irnode_to_text(sections[2])
    assert "Oikeuden päätökseen ei saa hakea muutosta" in irnode_to_text(sections[3])
    assert check_invariants(chapter) == []


def test_fi_xml_to_ir_node_does_not_split_embedded_section_marker_without_body() -> None:
    """A lone marker without following section body must not split the section."""
    xml = etree.fromstring(
        """
        <chapter>
          <num>19 luku</num>
          <section>
            <num>19 §</num>
            <subsection>
              <content><p>Pesänselvittäjän on toimessaan noudatettava kaikkea huolellisuutta.</p></content>
            </subsection>
            <subsection>
              <content><p>20 §.</p></content>
            </subsection>
          </section>
        </chapter>
        """
    )

    chapter = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    sections = [child for child in chapter.children if child.kind == IRNodeKind.SECTION]
    assert [section.label for section in sections] == ["19"]


def test_fi_xml_to_ir_node_recovers_embedded_paragraph_number_from_content() -> None:
    xml = etree.fromstring(
        """
        <subsection>
          <intro><p>Osaston päällikön tehtävänä on:</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>johtaa ja valvoa osastonsa toimintaa;</p></content>
          </paragraph>
          <paragraph>
            <content><p>2<i>) </i>huolehtia, että osastolle kuuluvat tehtävät hoidetaan tuloksellisesti;</p></content>
          </paragraph>
          <paragraph>
            <num>3)</num>
            <content><p>hyväksyä osaston tulostavoitteet;</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3"]
    assert irnode_to_text(paragraphs[1]) == "2) huolehtia, että osastolle kuuluvat tehtävät hoidetaan tuloksellisesti;"


def test_fi_xml_to_ir_node_extracts_label_from_intro_text_when_no_num() -> None:
    """Paragraph with <intro>3) text...</intro> and no <num> must get label "3".

    Some Finlex AKN amendment XML encodes list items as
    <paragraph><intro>N) text</intro><subparagraph>...</subparagraph></paragraph>
    without a <num> sibling.  When items 1) and 2) use normal <num> encoding but
    item 3) uses the intro pattern, the positional counter would wrongly assign
    label "1" to it.  The fix extracts the label from the intro text before the
    counter runs.

    Provenance: 1889/39-001 chapter:17/section:14a — amendment 2011/14 intro-pattern item
    """
    xml = etree.fromstring(
        """
        <subsection>
          <paragraph>
            <num>1)</num>
            <content><p>first item text</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>second item text</p></content>
          </paragraph>
          <paragraph>
            <intro>3) tavoitellaan huomattavaa taloudellista hyötyä</intro>
            <subparagraph>
              <content><p>ja jotain muuta</p></content>
            </subparagraph>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2", "3"]

    # The intro text must NOT start with "3) " — the prefix was stripped
    para3 = paragraphs[2]
    intro = next(ch for ch in para3.children if ch.kind == IRNodeKind.INTRO)
    assert intro.text is not None
    assert not intro.text.startswith("3)")
    assert "tavoitellaan" in intro.text


def test_fi_xml_to_ir_node_num_takes_precedence_over_intro_label() -> None:
    """Explicit <num> label must not be overridden by intro text.

    When a paragraph has both a <num> child and an <intro> child whose text
    starts with N), the <num> value is the authoritative label.

    Provenance: 1889/39-001 chapter:17/section:14a — amendment 2011/14 intro-pattern item
    """
    xml = etree.fromstring(
        """
        <subsection>
          <paragraph>
            <num>3)</num>
            <intro>3) this intro text also starts with the item number</intro>
            <content><p>item body</p></content>
          </paragraph>
        </subsection>
        """
    )

    subsection = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraphs) == 1
    assert paragraphs[0].label == "3"

    # The intro text must be unchanged because num took precedence (no stripping)
    intro = next((ch for ch in paragraphs[0].children if ch.kind == IRNodeKind.INTRO), None)
    assert intro is not None
    assert intro.text is not None
    assert intro.text.startswith("3)")


def test_fi_xml_to_ir_node_renests_flat_digit_item_subsections() -> None:
    """Flat digit-item subsections must be re-nested as paragraph children of the intro subsection.

    Some Finlex base statute XMLs (e.g. 2020/1262 §3) encode definition lists as
    flat <subsection> siblings where each starts with "N)" text:

        <section>
          <subsection><content>Tässä laissa tarkoitetaan:</content></subsection>
          <subsection><content>1) julkisella tuella...</content></subsection>
          <subsection><content>2) nopealla laajakaistayhteydellä...</content></subsection>
        </section>

    The result must be a single subsection with an intro and paragraph children.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>3 §</num>
          <heading>Määritelmät</heading>
          <subsection>
            <content><p>Tässä laissa tarkoitetaan:</p></content>
          </subsection>
          <subsection>
            <content><p>1) julkisella tuella valtion, kunnan tai muun julkisyhteisön myöntämää tukea;</p></content>
          </subsection>
          <subsection>
            <content><p>2) nopealla laajakaistayhteydellä internetyhteyttä;</p></content>
          </subsection>
          <subsection>
            <content><p>3) haja-asutusalueella aluetta, jolla ei ole nopeaa laajakaistayhteyttä;</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert section.kind == IRNodeKind.SECTION
    assert section.label == "3"

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 1, f"Expected 1 subsection, got {len(subsections)}"

    sub = subsections[0]
    # The intro must be present
    intros = [ch for ch in sub.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    assert "tarkoitetaan" in (intros[0].text or "")

    # Three paragraph children
    paragraphs = [ch for ch in sub.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2", "3"]

    # Each paragraph has the content text (without the N) prefix)
    assert "julkisella tuella" in irnode_to_text(paragraphs[0])
    assert "nopealla laajakaistayhteydellä" in irnode_to_text(paragraphs[1])
    assert "haja-asutusalueella" in irnode_to_text(paragraphs[2])

    # No invariant violations
    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_renest_stops_at_non_digit_subsection() -> None:
    """Re-nesting must stop at the first non-digit-item subsection.

    When a section has flat digit-items followed by a normal subsection (not matching
    the N) pattern), the trailing subsection must remain as a separate sibling.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>4 §</num>
          <subsection>
            <content><p>Tässä laissa tarkoitetaan:</p></content>
          </subsection>
          <subsection>
            <content><p>1) ensimmäinen kohta;</p></content>
          </subsection>
          <subsection>
            <content><p>2) toinen kohta;</p></content>
          </subsection>
          <subsection>
            <content><p>Tämä momentti on erillinen.</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 2, f"Expected 2 subsections, got {len(subsections)}"

    # First subsection: intro + 2 paragraph children
    sub1 = subsections[0]
    intros = [ch for ch in sub1.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    paragraphs = [ch for ch in sub1.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2"]

    # Second subsection: the trailing non-digit subsection
    sub2 = subsections[1]
    assert "erillinen" in irnode_to_text(sub2)

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_no_renest_without_colon_intro() -> None:
    """Do not re-nest when the first subsection does not end with ':'.

    Only subsections whose content text ends with a colon are treated as
    definition-list intros. A subsection without a colon must not trigger re-nesting.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>5 §</num>
          <subsection>
            <content><p>Yleinen säännös</p></content>
          </subsection>
          <subsection>
            <content><p>1) ensimmäinen kohta;</p></content>
          </subsection>
          <subsection>
            <content><p>2) toinen kohta;</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # All three subsections remain separate (no re-nesting)
    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 3, f"Expected 3 subsections, got {len(subsections)}"

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_merges_split_intro_item_subsections() -> None:
    """Split intro + item subsections must be merged into a single subsection.

    Some Finlex base statute XMLs (e.g. 2000/1029 §11, 1990/1211 §2) encode a
    subsection with johdanto + kohta as two separate <subsection> siblings:

        <subsection><content><p>Intro text ending in colon:</p></content></subsection>
        <subsection>
          <paragraph><num>1)</num><content><p>item 1</p></content></paragraph>
          <paragraph><num>2)</num><content><p>item 2</p></content></paragraph>
        </subsection>

    The result must be a single subsection with an intro and paragraph children.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>11 §</num>
          <heading>Test</heading>
          <subsection>
            <content>
              <p>Jos valvonnassa todetaan vaatimuksia, valvontaviranomaisella on oikeus:</p>
            </content>
          </subsection>
          <subsection>
            <paragraph>
              <num>1)</num>
              <content><p>kieltää sellaisen tuotteen valmistus;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>vaatia muutoksia tuotteeseen;</p></content>
            </paragraph>
            <paragraph>
              <num>3)</num>
              <content><p>velvoittaa korvaamaan kulut.</p></content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert section.kind == IRNodeKind.SECTION
    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 1, f"Expected 1 subsection (merged), got {len(subsections)}"

    sub = subsections[0]
    intros = [ch for ch in sub.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    assert "valvontaviranomaisella on oikeus:" in (intros[0].text or "")

    paragraphs = [ch for ch in sub.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2", "3"]
    assert "kieltää" in irnode_to_text(paragraphs[0])
    assert "vaatia" in irnode_to_text(paragraphs[1])
    assert "velvoittaa" in irnode_to_text(paragraphs[2])

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_no_merge_when_next_has_intro() -> None:
    """Do not merge when the following subsection already has its own <intro>.

    When a content-only subsection ending with ':' is followed by a subsection
    that already has a proper <intro> + <paragraph> structure, these are two
    separate momentti and must NOT be merged.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>5 §</num>
          <subsection>
            <content>
              <p>Tukea myönnetään enintään seuraavasti:</p>
            </content>
          </subsection>
          <subsection>
            <intro><p>Pitkän viljelykauden tukea voidaan myöntää, jos:</p></intro>
            <paragraph>
              <num>1)</num>
              <content><p>niitä viljellään maapohjassa;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>viljelyssä tuotetaan taimia;</p></content>
            </paragraph>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 2, f"Expected 2 subsections (no merge), got {len(subsections)}"

    # First subsection: content-only
    assert "seuraavasti" in irnode_to_text(subsections[0])
    # Second subsection: proper intro + paragraphs
    intros = [ch for ch in subsections[1].children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    paragraphs = [ch for ch in subsections[1].children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2"]

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_merge_preserves_trailing_subsections() -> None:
    """Merging split intro+items must not affect later subsections.

    When a section has: intro-subsection + items-subsection + regular-subsection,
    the first two merge and the third remains at its correct position.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>2 §</num>
          <subsection>
            <content><p>Tapaturmalla tarkoitetaan:</p></content>
          </subsection>
          <subsection>
            <paragraph>
              <num>1)</num>
              <content><p>ensimmäinen kohta;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>toinen kohta.</p></content>
            </paragraph>
          </subsection>
          <subsection>
            <content><p>Henkilön vamma korvataan erikseen.</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 2, f"Expected 2 subsections, got {len(subsections)}"

    # First subsection: merged intro + items
    sub1 = subsections[0]
    assert sub1.label == "1"
    intros = [ch for ch in sub1.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    paragraphs = [ch for ch in sub1.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2"]

    # Second subsection: regular trailing subsection
    sub2 = subsections[1]
    assert sub2.label == "2"
    assert "korvataan erikseen" in irnode_to_text(sub2)

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_keeps_nonfinal_trailing_prose_inside_its_subsection() -> None:
    """Trailing prose in a non-final subsection must not become a new momentti.

    Parser-shape bug family from 2006/395 §75: an unnumbered trailing paragraph at
    the end of subsection 1 was being materialized as a standalone subsection,
    shifting the later oracle momentti labels.  When later subsection siblings
    already exist, the trailing prose must stay attached to subsection 1.
    """
    xml = etree.fromstring(
        """
        <section>
          <num>75 §</num>
          <subsection>
            <intro><p>Jotakin säädetään seuraavasti:</p></intro>
            <paragraph>
              <num>1)</num>
              <content><p>ensimmäinen kohta;</p></content>
            </paragraph>
            <paragraph>
              <num>2)</num>
              <content><p>toinen kohta.</p></content>
            </paragraph>
            <paragraph>
              <content><p>loppukappale kuuluu edelleen samaan momenttiin.</p></content>
            </paragraph>
          </subsection>
          <subsection>
            <content><p>Toinen momentti pysyy omana momenttinaan.</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 2

    sub1 = subsections[0]
    paragraphs = [ch for ch in sub1.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2"]
    assert sub1.children[-1].kind == IRNodeKind.CONTENT
    assert "loppukappale kuuluu edelleen" in irnode_to_text(sub1)
    assert "Toinen momentti" in irnode_to_text(subsections[1])
    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_renest_handles_lettered_suffix() -> None:
    """Digit-item labels like '2a)' must be handled correctly."""
    xml = etree.fromstring(
        """
        <section>
          <num>6 §</num>
          <subsection>
            <content><p>Tässä laissa tarkoitetaan:</p></content>
          </subsection>
          <subsection>
            <content><p>1) ensimmäinen;</p></content>
          </subsection>
          <subsection>
            <content><p>2a) toinen a-kohta;</p></content>
          </subsection>
        </section>
        """
    )

    section = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    subsections = [ch for ch in section.children if ch.kind == IRNodeKind.SUBSECTION]
    assert len(subsections) == 1

    paragraphs = [ch for ch in subsections[0].children if ch.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1", "2a"]

    assert check_invariants(section) == []


def test_fi_xml_to_ir_node_reclassifies_item_subsection_as_paragraph() -> None:
    """A <subsection> with item-style <num> AND letter-labeled <paragraph> children must
    be reclassified as paragraph (kohta) with subparagraph (alakohta) children by the
    source normalization phase (not by fi_xml_to_ir_node itself).

    Phase boundary: fi_xml_to_ir_node is now a raw structural parse only.
    normalize_source_ir detects and corrects the pathology, emitting a typed witness.

    Source pathology: statute 2002/672, amendment 2025/416 encodes kohta (items)
    as <subsection> elements with <num>9)</num> and <paragraph> children carrying
    letter labels (a-g).  The discriminator is the combination of item-style num
    AND letter-labeled paragraph children — real momentti never carry letter-labeled
    direct paragraph children.
    """
    xml = etree.fromstring(
        """
        <subsection eId="sec_9__subsec_9">
          <num>9)</num>
          <intro><p>Tässä momentissa tarkoitetaan:</p></intro>
          <paragraph eId="sec_9__subsec_9__para_a">
            <num>a)</num>
            <content><p>ensimmäinen alakohta;</p></content>
          </paragraph>
          <paragraph eId="sec_9__subsec_9__para_b">
            <num>b)</num>
            <content><p>toinen alakohta;</p></content>
          </paragraph>
          <paragraph eId="sec_9__subsec_9__para_c">
            <num>c)</num>
            <content><p>kolmas alakohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    # Phase 1: raw parse — produces SUBSECTION (the pathological raw form).
    raw_node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)
    assert raw_node.kind == IRNodeKind.SUBSECTION, (
        f"fi_xml_to_ir_node should produce raw SUBSECTION before normalization, got '{raw_node.kind}'"
    )

    # Phase 2: source normalization — detects and corrects the pathology.
    node, facts = normalize_source_ir(raw_node, "2002/672")

    # Must be reclassified as paragraph (kohta), not subsection (momentti)
    assert node.kind == IRNodeKind.PARAGRAPH, f"Expected 'paragraph', got '{node.kind}'"
    assert node.label == "9"

    # Direct <paragraph> children with letter labels must become subparagraphs (alakohta)
    subparagraphs = [ch for ch in node.children if ch.kind == IRNodeKind.SUBPARAGRAPH]
    assert [sp.label for sp in subparagraphs] == ["a", "b", "c"]

    # No paragraph children remain (all were reclassified)
    paragraphs = [ch for ch in node.children if ch.kind == IRNodeKind.PARAGRAPH]
    assert paragraphs == []

    # The intro node must be preserved
    intros = [ch for ch in node.children if ch.kind == IRNodeKind.INTRO]
    assert len(intros) == 1
    assert "tarkoitetaan" in (intros[0].text or "")

    # A TAG_RECLASSIFY fact must have been emitted
    assert len(facts) == 1
    fact = facts[0]
    assert fact.kind_value == "tag_reclassify"
    assert fact.statute_id == "2002/672"
    assert "9)" in fact.before

    # No invariant violations
    assert check_invariants(node) == []


def test_fi_xml_to_ir_node_does_not_reclassify_subsection_without_letter_paragraphs() -> None:
    """A <subsection> with N) <num> but only digit-labeled paragraphs must NOT be reclassified.

    The reclassification requires BOTH an item-style <num> AND letter-labeled
    <paragraph> children.  A subsection with N) num but digit-labeled paragraphs
    (normal kohta list) must remain a subsection (momentti).
    """
    xml = etree.fromstring(
        """
        <subsection eId="sec_5__subsec_1">
          <num>1)</num>
          <content><p>First moment content.</p></content>
        </subsection>
        """
    )

    node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # Must NOT be reclassified — no letter-labeled paragraph children present
    assert node.kind == IRNodeKind.SUBSECTION, f"Expected 'subsection', got '{node.kind}'"
    assert node.label == "1"


def test_fi_xml_to_ir_node_does_not_reclassify_subsection_with_digit_paragraphs() -> None:
    """A <subsection> with N) <num> and digit-labeled <paragraph> children must stay as subsection.

    Momentti with kohta list (digit-labeled paragraphs) must not be reclassified —
    only the combination of item-style num AND letter-labeled paragraphs is pathological.
    """
    xml = etree.fromstring(
        """
        <subsection>
          <num>2)</num>
          <intro><p>Säännös koskee:</p></intro>
          <paragraph>
            <num>1)</num>
            <content><p>ensimmäinen kohta;</p></content>
          </paragraph>
          <paragraph>
            <num>2)</num>
            <content><p>toinen kohta.</p></content>
          </paragraph>
        </subsection>
        """
    )

    node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    # Must NOT be reclassified — has digit-labeled paragraph children, not letter-labeled
    assert node.kind == IRNodeKind.SUBSECTION, f"Expected 'subsection', got '{node.kind}'"
    assert node.label == "2"


def test_fi_xml_to_ir_node_does_not_reclassify_plain_subsection() -> None:
    """A <subsection> with a plain numeric <num> (no ')') must not be reclassified."""
    xml = etree.fromstring(
        """
        <subsection>
          <num>2</num>
          <content><p>Tämä on normaali momentti.</p></content>
        </subsection>
        """
    )

    node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert node.kind == IRNodeKind.SUBSECTION, f"Expected 'subsection', got '{node.kind}'"
    assert node.label == "2"


def test_fi_xml_to_ir_node_does_not_reclassify_unnumbered_subsection() -> None:
    """A <subsection> with no <num> element must not be reclassified."""
    xml = etree.fromstring(
        """
        <subsection>
          <content><p>Normaali momentti ilman numeroa.</p></content>
        </subsection>
        """
    )

    node = fi_xml_to_ir_node(xml, _fi_label_postprocessor)

    assert node.kind == IRNodeKind.SUBSECTION, f"Expected 'subsection', got '{node.kind}'"
