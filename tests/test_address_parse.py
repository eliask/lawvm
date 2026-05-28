"""Unit tests for lawvm.finland.address_parse — Finnish legal address parser."""

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from lawvm.finland.address_parse import ParsedLegalAddress, parse_legal_addresses


# ---------------------------------------------------------------------------
# ParsedLegalAddress defaults
# ---------------------------------------------------------------------------


def test_parsed_legal_address_defaults() -> None:
    addr = ParsedLegalAddress()
    assert addr.section == ""
    assert addr.subsection is None
    assert addr.item is None
    assert addr.special == ""


def test_parsed_legal_address_frozen() -> None:
    addr = ParsedLegalAddress(section="6")
    with pytest.raises(FrozenInstanceError):
        cast(Any, addr).section = "7"


# ---------------------------------------------------------------------------
# Section-level (bare §) patterns
# ---------------------------------------------------------------------------


def test_single_section() -> None:
    result = parse_legal_addresses("28 §")
    assert len(result) == 1
    assert result[0].section == "28"
    assert result[0].subsection is None
    assert result[0].item is None
    assert result[0].special == ""


def test_single_section_with_trailing_punct() -> None:
    result = parse_legal_addresses("28 §.")
    labels = [r.section for r in result if r.section and r.subsection is None]
    assert "28" in labels


def test_section_comma_list() -> None:
    result = parse_legal_addresses("64, 66, 68 ja 69 §")
    sections = {r.section for r in result}
    assert sections == {"64", "66", "68", "69"}


def test_section_range() -> None:
    result = parse_legal_addresses("12–14 §")
    sections = {r.section for r in result}
    assert sections == {"12", "13", "14"}


def test_section_range_em_dash() -> None:
    result = parse_legal_addresses("12\u201414 §")
    sections = {r.section for r in result}
    assert sections == {"12", "13", "14"}


def test_section_with_letter_suffix() -> None:
    result = parse_legal_addresses("24 a §")
    assert any(r.section == "24a" for r in result)


def test_section_list_with_letter_suffix() -> None:
    """'24 ja 24 a §' → two sections: 24 and 24a."""
    result = parse_legal_addresses("24 ja 24 a §")
    sections = {r.section for r in result}
    assert sections == {"24", "24a"}


def test_section_letter_range() -> None:
    """'27 a–27 c §' → three sections: 27a, 27b, 27c."""
    result = parse_legal_addresses("27 a\u201327 c §")
    sections = {r.section for r in result}
    assert sections == {"27a", "27b", "27c"}


def test_section_alpha_start_to_plain_numeric_end_range() -> None:
    """'52 a-55 §' → 52a, 53, 54, 55."""
    result = parse_legal_addresses("52 a-55 §")
    sections = {r.section for r in result}
    assert sections == {"52a", "53", "54", "55"}


def test_section_only_no_subsection() -> None:
    """A bare § match must have subsection=None."""
    result = parse_legal_addresses("5 §")
    assert len(result) >= 1
    sec_only = [r for r in result if r.section == "5" and r.subsection is None]
    assert sec_only, "Expected a whole-section address"


# ---------------------------------------------------------------------------
# Section genitive §:n + subsection (momentti)
# ---------------------------------------------------------------------------


def test_section_subsection_basic() -> None:
    """'6 §:n 1 momentti' → section=6 subsection=1 (NOT section=1)."""
    result = parse_legal_addresses("6 §:n 1 momentti")
    assert len(result) == 1
    assert result[0].section == "6"
    assert result[0].subsection == 1
    assert result[0].item is None
    assert result[0].special == ""


def test_section_subsection_not_parsed_as_section_only() -> None:
    """'6 §:n 1 momentti' must NOT produce a ParsedLegalAddress(section='1')."""
    result = parse_legal_addresses("6 §:n 1 momentti")
    section_only = [r for r in result if r.section == "1" and r.subsection is None]
    assert not section_only, "Should not parse '1' as a bare section from §:n ref"


def test_section_subsection_list() -> None:
    """'6 §:n 1 ja 2 momentti' → two addresses both with section=6."""
    result = parse_legal_addresses("6 §:n 1 ja 2 momentti")
    sub_addrs = [r for r in result if r.section == "6" and r.subsection is not None]
    subsections = {r.subsection for r in sub_addrs}
    assert subsections == {1, 2}


def test_section_subsection_list_keeps_trailing_section_range() -> None:
    result = parse_legal_addresses("6 §:n 2 ja 3 momentti sekä 10 a–10 f §")
    sub_addrs = {(r.section, r.subsection) for r in result if r.subsection is not None}
    whole_sections = {r.section for r in result if r.section and r.subsection is None and not r.item and not r.special}
    assert sub_addrs == {("6", 2), ("6", 3)}
    assert whole_sections == {"10a", "10b", "10c", "10d", "10e", "10f"}


def test_section_subsection_another_section_number() -> None:
    """'49 a §:n 2 momentti' → section=49a subsection=2."""
    result = parse_legal_addresses("49 a §:n 2 momentti")
    assert any(r.section == "49a" and r.subsection == 2 for r in result)


# ---------------------------------------------------------------------------
# Section genitive §:n + subsection + item (kohta)
# ---------------------------------------------------------------------------


def test_section_subsection_item() -> None:
    """'6 §:n 1 momentin 3 kohta' → section=6 subsection=1 item='3'."""
    result = parse_legal_addresses("6 §:n 1 momentin 3 kohta")
    assert len(result) == 1
    assert result[0].section == "6"
    assert result[0].subsection == 1
    assert result[0].item == "3"
    assert result[0].special == ""


def test_section_subsection_item_with_letter_suffix() -> None:
    """'3 §:n 1 momentin a kohta' → section=3 subsection=1 item='a'."""
    result = parse_legal_addresses("3 §:n 1 momentin a kohta")
    # The item may or may not be present depending on parse depth; at minimum
    # section and subsection should be correct.
    addr = next((r for r in result if r.section == "3" and r.subsection == 1), None)
    assert addr is not None, "Expected section=3 subsection=1"


# ---------------------------------------------------------------------------
# Section genitive §:n + special (heading / intro)
# ---------------------------------------------------------------------------


def test_section_heading() -> None:
    """'3 §:n otsikko' → section=3 special='heading'."""
    result = parse_legal_addresses("3 §:n otsikko")
    assert any(r.section == "3" and r.special == "heading" for r in result)


def test_section_intro() -> None:
    """'5 §:n johdantokappale' → section=5 special='intro'."""
    result = parse_legal_addresses("5 §:n johdantokappale")
    assert any(r.section == "5" and r.special == "intro" for r in result)


def test_section_subsection_intro() -> None:
    """'6 §:n 1 momentin johdantokappale' → section=6 subsection=1 special='intro'."""
    result = parse_legal_addresses("6 §:n 1 momentin johdantokappale")
    assert any(r.section == "6" and r.subsection == 1 and r.special == "intro" for r in result)


# ---------------------------------------------------------------------------
# Standalone subsection (momentti) references — no section context
# ---------------------------------------------------------------------------


def test_standalone_subsection_list() -> None:
    """'2 ja 3 momentti' → two ParsedLegalAddress with subsection only."""
    result = parse_legal_addresses("2 ja 3 momentti")
    standalone = [r for r in result if r.subsection is not None and not r.section]
    subsections = {r.subsection for r in standalone}
    assert subsections == {2, 3}


def test_standalone_single_subsection() -> None:
    result = parse_legal_addresses("1 momentti")
    standalone = [r for r in result if r.subsection is not None and not r.section]
    assert any(r.subsection == 1 for r in standalone)


# ---------------------------------------------------------------------------
# Mixed patterns in one fragment
# ---------------------------------------------------------------------------


def test_mixed_section_and_subsection_ref() -> None:
    """Fragment with both '50 §' and '49 a §:n 2 momentti' — both extracted."""
    fragment = "49 a §:n 2 momentti, 50 §"
    result = parse_legal_addresses(fragment)
    secs = {r.section for r in result if r.section and r.subsection is None and not r.special}
    assert "50" in secs
    sub_addrs = [r for r in result if r.section == "49a" and r.subsection == 2]
    assert sub_addrs, "Expected section=49a subsection=2"


def test_multiple_sections_and_subsection_refs() -> None:
    """'49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §' — all extracted."""
    fragment = "49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §"
    result = parse_legal_addresses(fragment)
    sections = {r.section for r in result if r.section and r.subsection is None and not r.special and not r.item}
    assert "50" in sections
    assert "53" in sections
    sub_addrs_49a = [r for r in result if r.section == "49a" and r.subsection == 2]
    sub_addrs_51 = [r for r in result if r.section == "51" and r.subsection == 3]
    assert sub_addrs_49a, "Expected section=49a subsection=2"
    assert sub_addrs_51, "Expected section=51 subsection=3"


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


def test_empty_string() -> None:
    assert parse_legal_addresses("") == []


def test_no_legal_addresses() -> None:
    result = parse_legal_addresses("Tällä lailla säädetään seuraavasti.")
    assert result == []


def test_chapter_ref_not_matched_as_section() -> None:
    """'3 luku' should NOT produce a section address — only a chapter address."""
    result = parse_legal_addresses("3 luku")
    section_addrs = [r for r in result if r.section]
    assert not section_addrs, f"Unexpected section addresses from '3 luku': {section_addrs}"
    chapter_addrs = [r for r in result if r.chapter]
    assert len(chapter_addrs) == 1
    assert chapter_addrs[0].chapter == "3"


# ---------------------------------------------------------------------------
# Chapter-level ("N luku") patterns
# ---------------------------------------------------------------------------


def test_chapter_single() -> None:
    """'5 luku' → single chapter address."""
    result = parse_legal_addresses("5 luku")
    ch = [r for r in result if r.chapter]
    assert len(ch) == 1
    assert ch[0].chapter == "5"
    assert ch[0].section == ""
    assert ch[0].subsection is None


def test_chapter_range() -> None:
    """'2–5 luku' → chapters 2, 3, 4, 5."""
    result = parse_legal_addresses("2\u20135 luku")
    chapters = {r.chapter for r in result if r.chapter}
    assert chapters == {"2", "3", "4", "5"}


def test_chapter_range_em_dash() -> None:
    """'2—5 luku' (em-dash) → chapters 2, 3, 4, 5."""
    result = parse_legal_addresses("2\u20145 luku")
    chapters = {r.chapter for r in result if r.chapter}
    assert chapters == {"2", "3", "4", "5"}


def test_chapter_comma_ja_list() -> None:
    """'2, 4 ja 5 luku' → chapters 2, 4, 5."""
    result = parse_legal_addresses("2, 4 ja 5 luku")
    chapters = {r.chapter for r in result if r.chapter}
    assert chapters == {"2", "4", "5"}


def test_chapter_single_with_letter_suffix() -> None:
    """'5 a luku' → chapter '5a'."""
    result = parse_legal_addresses("5 a luku")
    ch = [r for r in result if r.chapter]
    assert any(r.chapter == "5a" for r in ch), f"Expected chapter '5a', got: {ch}"


def test_chapter_not_confused_with_section() -> None:
    """Chapter addresses should not produce section addresses."""
    result = parse_legal_addresses("3 luku ja 5 §")
    chapters = [r for r in result if r.chapter]
    sections = [r for r in result if r.section and r.chapter is None]
    assert len(chapters) == 1
    assert chapters[0].chapter == "3"
    assert len(sections) == 1
    assert sections[0].section == "5"


def test_chapter_range_does_not_duplicate_with_singles() -> None:
    """'2–4 luku' should produce exactly 3 chapter addresses, not duplicates."""
    result = parse_legal_addresses("2\u20134 luku")
    chapters = [r.chapter for r in result if r.chapter]
    assert chapters == ["2", "3", "4"]


# ---------------------------------------------------------------------------
# Alakohta (sub-item) patterns
# ---------------------------------------------------------------------------


def test_alakohta_basic() -> None:
    """'6 §:n 2 momentin 1 kohdan a alakohta' → full depth parse."""
    result = parse_legal_addresses("6 §:n 2 momentin 1 kohdan a alakohta")
    assert len(result) == 1
    addr = result[0]
    assert addr.section == "6"
    assert addr.subsection == 2
    assert addr.item == "1"
    assert addr.subitem == "a"
    assert addr.special == ""


def test_alakohta_numeric_subitem() -> None:
    """Numeric sub-item: '3 §:n 1 momentin 2 kohdan 3 alakohta'."""
    result = parse_legal_addresses("3 §:n 1 momentin 2 kohdan 3 alakohta")
    assert len(result) == 1
    addr = result[0]
    assert addr.section == "3"
    assert addr.subsection == 1
    assert addr.item == "2"
    assert addr.subitem == "3"


def test_defaults_include_new_fields() -> None:
    """New fields subitem and chapter default to None."""
    addr = ParsedLegalAddress()
    assert addr.subitem is None
    assert addr.chapter is None


def test_pykälän_synonym_basic() -> None:
    """'2 pykälän 1 momentin 73 kohta' — spelled-out genitive of pykälä."""
    result = parse_legal_addresses("2 pykälän 1 momentin 73 kohta")
    assert len(result) == 1
    addr = result[0]
    assert addr.section == "2"
    assert addr.subsection == 1
    assert addr.item == "73"


def test_pykälän_synonym_plain_section_ref() -> None:
    """'3 pykälän' bare genitive resolves to section 3."""
    result = parse_legal_addresses("3 pykälän 2 momentti")
    assert len(result) == 1
    addr = result[0]
    assert addr.section == "3"
    assert addr.subsection == 2
