"""Unit tests for lawvm.finland.normalize — op repair chain helpers."""
from lawvm.finland.normalize import (
    _extract_grouped_container_targets,
    _expand_numeric_section_list_ir,
)

# ---------------------------------------------------------------------------
# _extract_grouped_container_targets
# ---------------------------------------------------------------------------


def test_extract_grouped_container_targets_chapter_list() -> None:
    johto = "kumotaan 3, 4, 6 ja 7 luku"
    result = _extract_grouped_container_targets(johto, "luku")
    assert result == {"3", "4", "6", "7"}


def test_extract_grouped_container_targets_empty_when_no_match() -> None:
    johto = "muutetaan 3 § seuraavasti"
    result = _extract_grouped_container_targets(johto, "luku")
    assert result == set()


def test_extract_grouped_container_targets_part_noun() -> None:
    johto = "kumotaan 2 ja 3 osa"
    result = _extract_grouped_container_targets(johto, "osa")
    assert "2" in result
    assert "3" in result


# ---------------------------------------------------------------------------
# _expand_numeric_section_list_ir
# ---------------------------------------------------------------------------


def test_expand_numeric_section_list_simple_list() -> None:
    assert _expand_numeric_section_list_ir("1, 2, 3") == ["1", "2", "3"]


def test_expand_numeric_section_list_range() -> None:
    assert _expand_numeric_section_list_ir("5-8") == ["5", "6", "7", "8"]


def test_expand_numeric_section_list_mixed() -> None:
    result = _expand_numeric_section_list_ir("1, 3-5, 7")
    assert result == ["1", "3", "4", "5", "7"]


def test_expand_numeric_section_list_ja_separator() -> None:
    result = _expand_numeric_section_list_ir("2 ja 4")
    assert result == ["2", "4"]


