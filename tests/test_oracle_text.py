from lxml import etree

from lawvm.tools.oracle_text import (
    _collect_section_info,
    _el_to_text,
    _find_nearby_sections,
    _find_section_el,
    _num_text_to_canonical_selector,
)


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


# ---------------------------------------------------------------------------
# Task R: print==accept round-trip + eId direct + paren-strip + teaching errors
# ---------------------------------------------------------------------------

_STATUTE_XML = b"""
<statute>
  <section eId="chp_2__sec_7v20221023"><num>7 \xc2\xa7</num><subsection><p>seven</p></subsection></section>
  <section eId="chp_2__sec_7av20150401"><num>7 a \xc2\xa7</num><subsection><p>seven-a</p></subsection></section>
  <section eId="chp_3__sec_127v20181001"><num>127 \xc2\xa7</num><subsection><p>one-two-seven</p></subsection></section>
  <section eId="chp_3__sec_127av20190501"><num>127 a \xc2\xa7</num><subsection><p>one-two-seven-a</p></subsection></section>
</statute>
"""


def test_num_text_to_canonical_selector_plain() -> None:
    assert _num_text_to_canonical_selector("7 \xa7") == "section:7"


def test_num_text_to_canonical_selector_lettered() -> None:
    assert _num_text_to_canonical_selector("14 b \xa7") == "section:14 b"


def test_num_text_to_canonical_selector_empty() -> None:
    assert _num_text_to_canonical_selector("") == ""


def test_collect_section_info_returns_canonical_form() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    canonicals = [i["canonical"] for i in info]
    assert "section:7" in canonicals
    assert "section:7 a" in canonicals
    assert "section:127" in canonicals
    assert "section:127 a" in canonicals


def test_listing_canonical_token_round_trips_via_find_section_el() -> None:
    """The canonical selector produced by _collect_section_info must be accepted by _find_section_el."""
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    for entry in info:
        canon = entry["canonical"]
        if not canon:
            continue
        el = _find_section_el(root, canon)
        assert el is not None, f"canonical selector {canon!r} was not accepted by _find_section_el"


def test_find_section_el_accepts_eid_directly() -> None:
    """Passing a raw eId (e.g. 'chp_2__sec_7v20221023') should resolve the section."""
    root = etree.fromstring(_STATUTE_XML)
    el = _find_section_el(root, "chp_2__sec_7v20221023")
    assert el is not None
    assert "seven" in _el_to_text(el)


def test_find_section_el_accepts_paren_wrapped_num_text() -> None:
    """'(7 §)' (as previously printed by listing mode) should resolve like '7 §'."""
    root = etree.fromstring(_STATUTE_XML)
    el = _find_section_el(root, "(7 \xa7)")
    assert el is not None
    assert "seven" in _el_to_text(el)


def test_find_nearby_sections_returns_closest_by_number() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    # Ask for section:130 — nearest should be 127 / 127 a
    nearby = _find_nearby_sections(info, "section:130")
    assert len(nearby) >= 1
    assert all("127" in s for s in nearby[:2])


def test_find_nearby_sections_fallback_when_no_numeric_stem() -> None:
    root = etree.fromstring(_STATUTE_XML)
    info = _collect_section_info(root)
    # No numeric stem in the filter — should fall back to first few sections
    nearby = _find_nearby_sections(info, "chp_X__sec_Y")
    assert len(nearby) >= 1
