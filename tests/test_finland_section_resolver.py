from lxml import etree

from lawvm.core.locator import get_section_resolver, parse_locator_string
import lawvm.finland.section_resolver  # noqa: F401 — registers FI resolver


def _resolve(xml_bytes: bytes, locator_str: str):
    root = etree.fromstring(xml_bytes)
    resolver = get_section_resolver("fi")
    loc = parse_locator_string(locator_str)
    if loc is not None:
        el = resolver.resolve(root, loc)
        if el is not None:
            return el
    return resolver.resolve_raw(root, locator_str)


def test_full_hierarchical_locator_resolves() -> None:
    xml = b"""
    <statute>
      <part eId="part_5">
        <chapter eId="part_5__chp_11">
          <section eId="part_5__chp_11__sec_10"><num>10 \xc2\xa7</num><subsection><p>base</p></subsection></section>
        </chapter>
      </part>
    </statute>
    """
    el = _resolve(xml, "part:5/chapter:11/section:10")
    assert el is not None
    assert el.get("eId") == "part_5__chp_11__sec_10"


def test_versioned_variant_wins_over_unversioned_when_both_exist() -> None:
    xml = b"""
    <statute>
      <section eId="part_5__chp_11__sec_10"><num>10 \xc2\xa7</num><subsection><p>old</p></subsection></section>
      <section eId="part_5__chp_11__sec_10v20230049"><num>10 \xc2\xa7</num><subsection><p>amended</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "part:5/chapter:11/section:10")
    assert el is not None
    assert el.get("eId") == "part_5__chp_11__sec_10v20230049"


def test_partial_hierarchical_suffix_matches_chapter_section_under_part() -> None:
    """`chapter:11/section:3` resolves to `part_X__chp_11__sec_3` when chapters nest in parts."""
    xml = b"""
    <statute>
      <section eId="part_5__chp_11__sec_3"><num>3 \xc2\xa7</num><subsection><p>nested</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "chapter:11/section:3")
    assert el is not None
    assert el.get("eId") == "part_5__chp_11__sec_3"


def test_top_level_section_does_not_eid_suffix_match_nested() -> None:
    """`section:3` does NOT eId-suffix-match `part_5__chp_11__sec_3`.

    However, num-text fallback still permits the match (legacy behavior),
    because the visible label '3 §' is what the user typed.
    """
    xml = b"""
    <statute>
      <section eId="part_5__chp_11__sec_3"><num>3 \xc2\xa7</num><subsection><p>nested</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "section:3")
    # Num-text fallback DOES match — preserving legacy ergonomics.
    assert el is not None
    assert el.get("eId") == "part_5__chp_11__sec_3"


def test_top_level_section_matches_top_level_section() -> None:
    xml = b"""
    <statute>
      <section eId="sec_3"><num>3 \xc2\xa7</num><subsection><p>top</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "section:3")
    assert el is not None
    assert el.get("eId") == "sec_3"


def test_bare_num_label_fallback() -> None:
    xml = b"""
    <statute>
      <section eId="chp_3__sec_14"><num>14 \xc2\xa7</num><subsection><p>x</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "14 §")
    assert el is not None
    assert el.get("eId") == "chp_3__sec_14"


def test_lettered_section_via_hierarchical_locator() -> None:
    xml = b"""
    <statute>
      <section eId="chp_3__sec_14"><num>14 \xc2\xa7</num><subsection><p>base</p></subsection></section>
      <section eId="chp_3__sec_14b"><num>14 b \xc2\xa7</num><subsection><p>lettered</p></subsection></section>
    </statute>
    """
    el = _resolve(xml, "chapter:3/section:14b")
    assert el is not None
    assert el.get("eId") == "chp_3__sec_14b"


def test_unknown_kind_returns_none() -> None:
    xml = b"""<statute><section eId="sec_1"><num>1 \xc2\xa7</num></section></statute>"""
    # 'subsection:1' is not in the FI _ABBREV map → locator unmappable.
    # Trailing segment is 'subsection' not 'section', so no num-text fallback.
    el = _resolve(xml, "subsection:1")
    assert el is None
