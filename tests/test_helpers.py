"""Unit tests for lawvm.finland.helpers — pure utility functions."""
import unicodedata

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


def test_norm_num_token_documents_no_unicode_normalization_step() -> None:
    """Document that ``_norm_num_token`` (the slot-identity normalizer for
    Finnish section/chapter/part labels) performs NO Unicode NFC/NFKC step.

    It strips ``§``/whitespace/parens/dots and lowercases, but does not unify
    canonically-equivalent precomposed vs. decomposed diacritics. So a label
    carrying precomposed ``å`` (U+00E5) and one carrying decomposed
    ``a`` + COMBINING RING ABOVE (U+0061 U+030A) would normalize to two
    distinct slot identities.

    This is asserted as CURRENT BEHAVIOR, deliberately, rather than fixed:
    a full scan of the Finland corpus (277,012 source/consolidated XML blobs,
    ~31M label/identifier strings inspected) found ZERO non-NFC text in any
    label-bearing element or identifier attribute — i.e. Finlex publishes
    structural labels precomposed (NFC). Estonia and Norway samples were also
    clean. Because no decomposed labels exist in the corpus, this divergence is
    unreachable in practice and adding an NFC step to production would be
    speculative. The test exists so that, if this assumption is ever revisited,
    the divergence is recorded rather than silently assumed away.

    Note: real Finnish *structural* labels are Latin+digit only (ä/ö/å never
    appear as section/chapter suffixes), so the decomposed-diacritic risk is
    confined to symbolic/heading-style tokens that could route through the same
    ``.lower()``-based identity path.
    """
    nfc = unicodedata.normalize("NFC", "å")  # 'å'  -> U+00E5
    nfd = unicodedata.normalize("NFD", "å")  # 'a' + U+030A

    # Sanity: the two inputs are distinct code-point sequences but
    # canonically equivalent under Unicode normalization.
    assert nfc != nfd
    assert unicodedata.normalize("NFC", nfd) == nfc

    # Current behavior: the normalizer passes the diacritic through unchanged
    # and therefore does NOT collapse the two forms to one identity.
    assert _norm_num_token(nfc) == nfc
    assert _norm_num_token(nfd) == nfd
    assert _norm_num_token(nfc) != _norm_num_token(nfd)


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
