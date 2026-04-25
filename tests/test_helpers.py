"""Unit tests for lawvm.finland.helpers — pure utility functions."""
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import (
    _expand_section_range,
    _fi_label_postprocessor,
    _is_omission_ir,
    _norm_num_token,
    _previous_item_token,
)

# ---------------------------------------------------------------------------
# _norm_num_token
# ---------------------------------------------------------------------------
def test_norm_num_token_normalizes_plain_roman_numerals_to_arabic() -> None:
    assert _norm_num_token("IV") == "4"
    assert _norm_num_token("vi") == "6"


def test_norm_num_token_preserves_structural_suffix_after_roman_normalization() -> None:
    assert _norm_num_token("IV osa") == "4osa"
    assert _norm_num_token("VI luku") == "6luku"


def test_is_omission_ir_detects_kind_omission() -> None:
    node = IRNode(kind=IRNodeKind.OMISSION, label=None, text="- - -")
    assert _is_omission_ir(node) is True


def test_is_omission_ir_detects_hcontainer_with_name_omission() -> None:
    node = IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "omission"})
    assert _is_omission_ir(node) is True


def test_is_omission_ir_false_for_plain_hcontainer() -> None:
    node = IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "other"})
    assert _is_omission_ir(node) is False


def test_is_omission_ir_false_for_section() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, label="5")
    assert _is_omission_ir(node) is False


def test_is_omission_ir_false_for_hcontainer_without_name() -> None:
    node = IRNode(kind=IRNodeKind.HCONTAINER)
    assert _is_omission_ir(node) is False


# ---------------------------------------------------------------------------
# _fi_label_postprocessor
# ---------------------------------------------------------------------------
def test_fi_label_postprocessor_does_not_strip_luku_from_section() -> None:
    # Sections don't use luku suffix; postprocessor should leave them alone
    result = _fi_label_postprocessor("section", "12")
    assert result == "12"
def test_fi_label_postprocessor_does_not_strip_dot_from_subsection() -> None:
    # Dot-stripping is only for section/chapter/part
    result = _fi_label_postprocessor("subsection", "1.")
    assert result == "1."


# ---------------------------------------------------------------------------
# _previous_item_token (used by merge / alakohta logic)
# ---------------------------------------------------------------------------


def test_previous_item_token_numeric_simple() -> None:
    assert _previous_item_token("3") == "2"


def test_previous_item_token_returns_none_for_first() -> None:
    assert _previous_item_token("1") is None


def test_previous_item_token_letter_suffix_a_returns_base() -> None:
    assert _previous_item_token("3a") == "3"


def test_previous_item_token_letter_suffix_b_returns_a() -> None:
    assert _previous_item_token("3b") == "3a"


def test_previous_item_token_returns_none_for_unparseable() -> None:
    assert _previous_item_token("abc") is None


# ---------------------------------------------------------------------------
# _expand_section_range
# ---------------------------------------------------------------------------


def test_expand_section_range_numeric_range() -> None:
    assert _expand_section_range("12\u201514") == ["12", "13", "14"]


def test_expand_section_range_single_returns_as_list() -> None:
    assert _expand_section_range("5") == ["5"]


def test_expand_section_range_emdash_separator() -> None:
    assert _expand_section_range("3\u20145") == ["3", "4", "5"]


def test_expand_section_range_endash_separator() -> None:
    assert _expand_section_range("7\u20139") == ["7", "8", "9"]


def test_expand_section_range_ascii_hyphen() -> None:
    assert _expand_section_range("1-3") == ["1", "2", "3"]


def test_expand_section_range_letter_suffix_not_expanded() -> None:
    # Ranges with letter suffixes are NOT expanded
    result = _expand_section_range("12a-14b")
    assert result == ["12a-14b"]
