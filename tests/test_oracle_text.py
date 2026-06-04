from lxml import etree

from lawvm.tools.oracle_text import _el_to_text, _find_section_el


def test_find_section_el_distinguishes_lettered_sections() -> None:
    root = etree.fromstring(
        b"""
        <statute>
          <section eId="chp_3__sec_14v20221023"><num>14 \xc2\xa7</num><subsection><p>base</p></subsection></section>
          <section eId="chp_3__sec_14bv20150815"><num>14 b \xc2\xa7</num><subsection><p>lettered</p></subsection></section>
        </statute>
        """
    )

    section = _find_section_el(root, "section:14 b")

    assert section is not None
    text = _el_to_text(section)
    assert text.startswith("14 b §")
    assert "lettered" in text


def test_find_section_el_does_not_match_base_section_for_compact_lettered_label() -> None:
    root = etree.fromstring(
        b"""
        <statute>
          <section eId="chp_13__sec_198v20160646"><num>198 \xc2\xa7</num><subsection><p>base</p></subsection></section>
          <section eId="chp_13__sec_198bv20181022"><num>198 b \xc2\xa7</num><subsection><p>lettered</p></subsection></section>
        </statute>
        """
    )

    section = _find_section_el(root, "section:198b")

    assert section is not None
    text = _el_to_text(section)
    assert text.startswith("198 b §")
    assert "lettered" in text
