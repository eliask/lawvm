"""Tests for the shared §a:b.c.d provision selector grammar."""

from lawvm.core.selector import (
    ParsedSelector,
    has_subprovision,
    parse_section_selector,
    section_scope_locator,
    to_locator_string,
)


def _parsed_selector(text: str) -> ParsedSelector:
    selector = parse_section_selector(text)
    assert selector is not None
    return selector


class TestParseSectionSelector:
    def test_chapter_section(self):
        p = parse_section_selector("§3:1")
        assert p == ParsedSelector(
            chapter="3", section="1", subsection=None, paragraph=None,
            locator="chapter:3/section:1",
        )
        assert p is not None and p.is_section_scope

    def test_chapter_section_subsection(self):
        p = parse_section_selector("§3:1.2")
        assert p is not None
        assert p.locator == "chapter:3/section:1/subsection:2"
        assert p.subsection == "2"  # momentti 2
        assert not p.is_section_scope

    def test_flat_section(self):
        p = parse_section_selector("§7")
        assert p is not None
        assert p.locator == "section:7"
        assert p.chapter is None

    def test_section_subsection_paragraph(self):
        p = parse_section_selector("§7.1.3")
        assert p is not None
        assert p.locator == "section:7/subsection:1/paragraph:3"
        assert p.subsection == "1"  # momentti 1
        assert p.paragraph == "3"  # kohta 3

    def test_without_leading_sign(self):
        assert _parsed_selector("3:1").locator == "chapter:3/section:1"

    def test_lettered_label(self):
        assert _parsed_selector("§14 b").locator == "section:14 b"
        assert _parsed_selector("§14b").locator == "section:14 b"

    def test_chapter_section_with_lettered_subsection(self):
        # uncommon but allowed: subsection (momentti) label normalization
        p = parse_section_selector("§3:1.2")
        assert p is not None
        assert p.section == "1"

    def test_rejects_non_selector(self):
        assert parse_section_selector("chapter:3/section:1") is None
        assert parse_section_selector("chp_3__sec_1") is None
        assert parse_section_selector("") is None
        assert parse_section_selector("garbage text here") is None

    def test_whitespace_tolerated(self):
        assert _parsed_selector("  §3:1  ").locator == "chapter:3/section:1"


class TestToLocatorString:
    def test_canonical_lowered(self):
        assert to_locator_string("§3:1.2") == "chapter:3/section:1/subsection:2"
        assert to_locator_string("§7") == "section:7"

    def test_legacy_locator_passthrough(self):
        assert to_locator_string("chapter:3/section:1") == "chapter:3/section:1"
        assert (
            to_locator_string("chapter:1/section:4/subsection:2")
            == "chapter:1/section:4/subsection:2"
        )

    def test_eid_passthrough(self):
        assert to_locator_string("chp_3__sec_1") == "chp_3__sec_1"
        assert to_locator_string("chp_2__sec_7v20221023") == "chp_2__sec_7v20221023"

    def test_bare_label_passthrough(self):
        assert to_locator_string("1 §") == "1 §"
        assert to_locator_string("(7 §)") == "(7 §)"

    def test_empty_passthrough(self):
        assert to_locator_string("") == ""


class TestSectionScope:
    def test_section_scope_drops_subsection(self):
        assert section_scope_locator("§3:1.2") == "chapter:3/section:1"
        assert section_scope_locator("§7.1.3") == "section:7"

    def test_section_scope_section_unchanged(self):
        assert section_scope_locator("§3:1") == "chapter:3/section:1"
        assert section_scope_locator("§7") == "section:7"

    def test_section_scope_legacy_locator(self):
        assert (
            section_scope_locator("chapter:3/section:1/subsection:2")
            == "chapter:3/section:1"
        )
        assert section_scope_locator("section:7/paragraph:1") == "section:7"

    def test_has_subprovision(self):
        assert has_subprovision("§3:1.2") is True
        assert has_subprovision("§7.1.3") is True
        assert has_subprovision("chapter:3/section:1/subsection:2") is True
        assert has_subprovision("§3:1") is False
        assert has_subprovision("§7") is False
        assert has_subprovision("chapter:3/section:1") is False
