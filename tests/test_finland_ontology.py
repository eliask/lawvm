"""Tests for lawvm.finland.ontology and lawvm.finland.labels.

Covers:
  - Ontology entry validity and completeness
  - Parent/child relationships
  - to_unit_registry() compatibility with Finland's frontend registry
  - Label parsing for all documented surface forms
  - Label rendering round-trips
  - Label sorting
  - Type discrimination (InsertableArabic vs AlphaSequence)
  - Compound alpha labels
  - Roman labels
  - Edge cases

Run:
    uv run python -m pytest tests/test_finland_ontology.py -v --override-ini="addopts="
"""
from __future__ import annotations

import pytest

from lawvm.finland.ontology import (
    ALL_UNIT_KINDS,
    HIERARCHY_ORDER,
    UNIT_ONTOLOGY,
    UnitOntologyEntry,
    allowed_label_series,
    can_carry_facet,
    hierarchy_depth,
    is_amendable,
    is_legal_unit,
    parent_kinds,
    to_unit_registry,
)
from lawvm.finland.labels import (
    AlphaSequence,
    ImplicitOrdinal,
    InsertableArabic,
    RomanOrdinal,
    SymbolicLabel,
    _alpha_sort_value,
    is_valid_label_for_kind,
    label_sort_key,
    normalize_raw_label,
    parse_label,
    render_label,
    roman_to_arabic,
)
from lawvm.finland.unit_registry import FINLAND_REGISTRY


# ===========================================================================
# Ontology: entry validity
# ===========================================================================


class TestOntologyEntries:
    def test_all_unit_kinds_present(self):
        """Every declared unit kind has an entry in UNIT_ONTOLOGY."""
        for kind in ALL_UNIT_KINDS:
            assert kind in UNIT_ONTOLOGY, f"Missing ontology entry for {kind!r}"

    def test_no_extra_entries(self):
        """UNIT_ONTOLOGY contains no entries beyond ALL_UNIT_KINDS."""
        for kind in UNIT_ONTOLOGY:
            assert kind in ALL_UNIT_KINDS, f"Unexpected ontology entry {kind!r}"

    def test_entries_are_frozen(self):
        """UnitOntologyEntry instances are frozen dataclasses."""
        for entry in UNIT_ONTOLOGY.values():
            assert isinstance(entry, UnitOntologyEntry)
            with pytest.raises(AttributeError):
                entry.kind = "bogus"  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_repeal_compacts_always_false(self):
        """Finnish law never auto-compacts on repeal."""
        for kind, entry in UNIT_ONTOLOGY.items():
            assert entry.repeal_compacts is False, (
                f"{kind}: repeal_compacts should be False"
            )

    def test_hierarchy_depths_are_unique_and_ascending(self):
        """Hierarchy depths are unique across entries and match HIERARCHY_ORDER."""
        depths = [UNIT_ONTOLOGY[k].hierarchy_depth for k in HIERARCHY_ORDER]
        assert depths == sorted(depths)
        assert len(set(depths)) == len(depths)

    def test_hierarchy_order_matches_all_unit_kinds(self):
        """HIERARCHY_ORDER contains exactly ALL_UNIT_KINDS in correct order."""
        assert set(HIERARCHY_ORDER) == set(ALL_UNIT_KINDS)

    def test_fi_name_nonempty(self):
        """Every entry has a non-empty Finnish name."""
        for kind, entry in UNIT_ONTOLOGY.items():
            assert entry.fi_name, f"{kind}: fi_name is empty"

    def test_statute_has_no_parents(self):
        """Statute is the root: no allowed parents."""
        assert UNIT_ONTOLOGY["statute"].allowed_parents == ()

    def test_every_non_root_has_parents(self):
        """Every non-statute unit has at least one allowed parent."""
        for kind, entry in UNIT_ONTOLOGY.items():
            if kind == "statute":
                continue
            assert entry.allowed_parents, f"{kind}: no allowed parents"

    def test_subsection_is_only_implicit_ordinal(self):
        """subsection (momentti) is the only body-chain unit with implicit_ordinal."""
        body_chain = [
            "part", "chapter", "section", "subsection", "item", "subitem",
        ]
        implicit = [k for k in body_chain if UNIT_ONTOLOGY[k].identity_class == "implicit_ordinal"]
        assert implicit == ["subsection"]


# ===========================================================================
# Ontology: helper functions
# ===========================================================================


class TestOntologyHelpers:
    def test_is_legal_unit_known(self):
        for kind in ALL_UNIT_KINDS:
            assert is_legal_unit(kind)

    def test_is_legal_unit_unknown(self):
        assert not is_legal_unit("widget")
        assert not is_legal_unit("")
        assert not is_legal_unit("paragraph")

    def test_parent_kinds_section(self):
        parents = parent_kinds("section")
        assert "chapter" in parents
        assert "statute" in parents

    def test_parent_kinds_item_allows_both(self):
        """Items can be under section or subsection."""
        parents = parent_kinds("item")
        assert "section" in parents
        assert "subsection" in parents

    def test_parent_kinds_subitem_recursive(self):
        """Subitems can nest under items and other subitems."""
        parents = parent_kinds("subitem")
        assert "item" in parents
        assert "subitem" in parents

    def test_parent_kinds_unknown(self):
        assert parent_kinds("widget") == ()

    def test_can_carry_facet_section_heading(self):
        assert can_carry_facet("section", "heading")

    def test_can_carry_facet_section_intro(self):
        assert can_carry_facet("section", "intro")

    def test_cannot_carry_facet_subsection_heading(self):
        assert not can_carry_facet("subsection", "heading")

    def test_can_carry_facet_unknown_unit(self):
        assert not can_carry_facet("widget", "heading")

    def test_is_amendable_all_units(self):
        for kind in ALL_UNIT_KINDS:
            assert is_amendable(kind), f"{kind} should be amendable"

    def test_is_amendable_unknown(self):
        assert not is_amendable("widget")

    def test_hierarchy_depth_ordering(self):
        assert hierarchy_depth("statute") < hierarchy_depth("chapter")
        assert hierarchy_depth("chapter") < hierarchy_depth("section")
        assert hierarchy_depth("section") < hierarchy_depth("subsection")
        assert hierarchy_depth("subsection") < hierarchy_depth("item")
        assert hierarchy_depth("item") < hierarchy_depth("subitem")

    def test_hierarchy_depth_unknown(self):
        assert hierarchy_depth("widget") == -1

    def test_allowed_label_series(self):
        assert "insertable_arabic" in allowed_label_series("chapter")
        assert "roman_ordinal" in allowed_label_series("part")
        assert "alpha_sequence" in allowed_label_series("subitem")

    def test_allowed_label_series_unknown(self):
        assert allowed_label_series("widget") == ()


# ===========================================================================
# Ontology: Finland registry authority
# ===========================================================================


class TestToUnitRegistry:
    def test_produces_unit_registry(self):
        reg = to_unit_registry()
        from lawvm.core.unit_registry import UnitRegistry
        assert isinstance(reg, UnitRegistry)

    def test_jurisdiction_is_fi(self):
        reg = to_unit_registry()
        assert reg.jurisdiction == "FI"

    def test_same_unit_kinds_as_finland_registry(self):
        """to_unit_registry() produces the same set of unit_kind strings."""
        reg = to_unit_registry()
        assert set(reg.unit_specs.keys()) == set(FINLAND_REGISTRY.unit_specs.keys())

    def test_same_valid_facets(self):
        reg = to_unit_registry()
        assert reg.valid_facets == FINLAND_REGISTRY.valid_facets

    def test_identity_classes_match(self):
        """Identity classes match the existing FINLAND_REGISTRY for every kind."""
        reg = to_unit_registry()
        for kind in FINLAND_REGISTRY.unit_specs:
            expected = FINLAND_REGISTRY.unit_specs[kind].identity_class
            actual = reg.unit_specs[kind].identity_class
            assert actual == expected, (
                f"{kind}: identity_class mismatch: {actual!r} vs {expected!r}"
            )

    def test_insertion_policies_match(self):
        reg = to_unit_registry()
        for kind in FINLAND_REGISTRY.unit_specs:
            expected = FINLAND_REGISTRY.unit_specs[kind].insertion_policy
            actual = reg.unit_specs[kind].insertion_policy
            assert actual == expected, (
                f"{kind}: insertion_policy mismatch: {actual!r} vs {expected!r}"
            )

    def test_repeal_compacts_match(self):
        reg = to_unit_registry()
        for kind in FINLAND_REGISTRY.unit_specs:
            expected = FINLAND_REGISTRY.unit_specs[kind].repeal_compacts
            actual = reg.unit_specs[kind].repeal_compacts
            assert actual == expected, (
                f"{kind}: repeal_compacts mismatch: {actual!r} vs {expected!r}"
            )

    def test_can_have_heading_match(self):
        reg = to_unit_registry()
        for kind in FINLAND_REGISTRY.unit_specs:
            expected = FINLAND_REGISTRY.unit_specs[kind].can_have_heading
            actual = reg.unit_specs[kind].can_have_heading
            assert actual == expected, (
                f"{kind}: can_have_heading mismatch: {actual!r} vs {expected!r}"
            )

    def test_can_have_intro_match(self):
        reg = to_unit_registry()
        for kind in FINLAND_REGISTRY.unit_specs:
            expected = FINLAND_REGISTRY.unit_specs[kind].can_have_intro
            actual = reg.unit_specs[kind].can_have_intro
            assert actual == expected, (
                f"{kind}: can_have_intro mismatch: {actual!r} vs {expected!r}"
            )


# ===========================================================================
# Labels: Roman numeral helper
# ===========================================================================


class TestRomanToArabic:
    def test_basic_values(self):
        assert roman_to_arabic("I") == 1
        assert roman_to_arabic("IV") == 4
        assert roman_to_arabic("X") == 10
        assert roman_to_arabic("XX") == 20

    def test_case_insensitive(self):
        assert roman_to_arabic("iv") == 4
        assert roman_to_arabic("XII") == 12
        assert roman_to_arabic("xii") == 12

    def test_extended_range_via_shared_parser(self):
        # The shared parser accepts canonical I/V/X spellings beyond XX.
        assert roman_to_arabic("XXI") == 21
        assert roman_to_arabic("XXX") == 30

    def test_unknown_returns_none(self):
        assert roman_to_arabic("abc") is None
        assert roman_to_arabic("") is None
        # L/C/D/M are outside the Finnish chapter/part surface, gated out.
        assert roman_to_arabic("L") is None
        assert roman_to_arabic("MMM") is None
        # Non-canonical spellings rejected by the shared parser.
        assert roman_to_arabic("IIII") is None
        assert roman_to_arabic("VV") is None


# ===========================================================================
# Labels: normalize_raw_label
# ===========================================================================


class TestNormalizeRawLabel:
    def test_strip_section_sign(self):
        assert normalize_raw_label("13.§", "section") == "13"

    def test_strip_luku_suffix(self):
        assert normalize_raw_label("3 luku", "chapter") == "3"

    def test_strip_luku_dot(self):
        assert normalize_raw_label("3 luku.", "chapter") == "3"

    def test_strip_osa_suffix(self):
        assert normalize_raw_label("II osa", "part") == "II"

    def test_strip_osasto_suffix(self):
        assert normalize_raw_label("OSASTO VII", "division") == "VII"

    def test_strip_trailing_paren(self):
        assert normalize_raw_label("1 a)", "item") == "1 a"

    def test_strip_trailing_dot_section(self):
        assert normalize_raw_label("3.", "section") == "3"

    def test_preserve_suffix_letter(self):
        result = normalize_raw_label("10 a luku.", "chapter")
        assert result == "10 a"

    def test_section_with_space_suffix(self):
        result = normalize_raw_label("23 § a", "section")
        assert result == "23  a"  # § stripped leaves double space, will parse fine


# ===========================================================================
# Labels: parse_label
# ===========================================================================


class TestParseLabel:
    """Test label parsing for all documented surface forms."""

    def test_chapter_arabic(self):
        lbl = parse_label("3 luku", "chapter")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 3
        assert lbl.suffix == ""

    def test_chapter_arabic_with_suffix(self):
        lbl = parse_label("10 a luku.", "chapter")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 10
        assert lbl.suffix == "a"

    def test_section_plain(self):
        lbl = parse_label("5 §", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 5
        assert lbl.suffix == ""

    def test_section_with_dot(self):
        lbl = parse_label("13.§", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 13
        assert lbl.suffix == ""

    def test_section_with_suffix(self):
        lbl = parse_label("23 a §", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 23
        assert lbl.suffix == "a"

    def test_section_suffix_alternate(self):
        """23 § a — suffix after §."""
        lbl = parse_label("23 § a", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 23
        assert lbl.suffix == "a"

    def test_section_old_format_dot(self):
        lbl = parse_label("1.", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 1

    def test_item_arabic(self):
        lbl = parse_label("1)", "item")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 1
        assert lbl.suffix == ""

    def test_item_with_suffix(self):
        lbl = parse_label("1 a)", "item")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 1
        assert lbl.suffix == "a"

    def test_item_compact_suffix(self):
        """1a) — no space between number and suffix."""
        lbl = parse_label("1a)", "item")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 1
        assert lbl.suffix == "a"

    def test_subitem_single_alpha(self):
        lbl = parse_label("a)", "subitem")
        assert isinstance(lbl, AlphaSequence)
        assert lbl.token == "a"

    def test_subitem_compound_aa(self):
        lbl = parse_label("aa)", "subitem")
        assert isinstance(lbl, AlphaSequence)
        assert lbl.token == "aa"

    def test_subitem_compound_ab(self):
        lbl = parse_label("ab)", "subitem")
        assert isinstance(lbl, AlphaSequence)
        assert lbl.token == "ab"

    def test_subitem_compound_ba(self):
        lbl = parse_label("ba)", "subitem")
        assert isinstance(lbl, AlphaSequence)
        assert lbl.token == "ba"

    def test_subitem_roman(self):
        lbl = parse_label("iv)", "subitem")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 4
        assert lbl.token == "iv"

    def test_subitem_roman_upper(self):
        lbl = parse_label("IV)", "subitem")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 4

    def test_subitem_roman_xii(self):
        lbl = parse_label("XII)", "subitem")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 12

    def test_part_roman(self):
        lbl = parse_label("V osa", "part")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 5

    def test_part_roman_prefixed(self):
        """II A OSA — Roman numeral with trailing symbolic qualifier."""
        lbl = parse_label("II A OSA", "part")
        assert isinstance(lbl, SymbolicLabel)
        assert lbl.token == "II A"

    def test_division_osasto_prefix(self):
        lbl = parse_label("OSASTO VII", "division")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 7

    def test_supplement_symbolic(self):
        lbl = parse_label("A", "supplement")
        assert isinstance(lbl, SymbolicLabel)
        assert lbl.token == "A"

    def test_supplement_roman(self):
        lbl = parse_label("IV", "supplement")
        assert isinstance(lbl, RomanOrdinal)
        assert lbl.value == 4

    def test_supplement_arabic(self):
        lbl = parse_label("3", "supplement")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 3

    def test_edge_case_50_a_section(self):
        """50 a § — special characters in suffix."""
        lbl = parse_label("50 a §", "section")
        assert isinstance(lbl, InsertableArabic)
        assert lbl.base == 50
        assert lbl.suffix == "a"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty label"):
            parse_label("", "section")

    def test_item_alpha_sequence(self):
        """Pure alpha label "a" for item context → AlphaSequence."""
        lbl = parse_label("a)", "item")
        assert isinstance(lbl, AlphaSequence)
        assert lbl.token == "a"


# ===========================================================================
# Labels: type discrimination
# ===========================================================================


class TestLabelTypeDiscrimination:
    def test_insertable_arabic_item_neq_alpha_subitem(self):
        """InsertableArabic(1, "a") as item != AlphaSequence("a") as subitem."""
        item_label = InsertableArabic(base=1, suffix="a")
        subitem_label = AlphaSequence(token="a")
        assert item_label != subitem_label
        assert type(item_label) is not type(subitem_label)

    def test_same_base_different_suffix(self):
        a = InsertableArabic(base=1, suffix="")
        b = InsertableArabic(base=1, suffix="a")
        assert a != b

    def test_roman_preserves_case(self):
        upper = RomanOrdinal(value=4, token="IV")
        lower = RomanOrdinal(value=4, token="iv")
        assert upper != lower  # different tokens, different objects
        assert upper.value == lower.value


# ===========================================================================
# Labels: render_label
# ===========================================================================


class TestRenderLabel:
    def test_chapter_arabic(self):
        assert render_label(InsertableArabic(10, "a"), "chapter") == "10 a luku"

    def test_chapter_plain(self):
        assert render_label(InsertableArabic(3, ""), "chapter") == "3 luku"

    def test_section_plain(self):
        assert render_label(InsertableArabic(5, ""), "section") == "5 \u00a7"

    def test_section_with_suffix(self):
        assert render_label(InsertableArabic(23, "a"), "section") == "23 a \u00a7"

    def test_item_plain(self):
        assert render_label(InsertableArabic(1, ""), "item") == "1)"

    def test_item_with_suffix(self):
        assert render_label(InsertableArabic(1, "a"), "item") == "1 a)"

    def test_subitem_alpha(self):
        assert render_label(AlphaSequence("aa"), "subitem") == "aa)"

    def test_subitem_alpha_single(self):
        assert render_label(AlphaSequence("a"), "subitem") == "a)"

    def test_subitem_roman(self):
        assert render_label(RomanOrdinal(4, "iv"), "subitem") == "iv)"

    def test_part_roman(self):
        assert render_label(RomanOrdinal(5, "V"), "part") == "V osa"

    def test_division_roman(self):
        assert render_label(RomanOrdinal(7, "VII"), "division") == "VII osasto"

    def test_supplement_symbolic(self):
        assert render_label(SymbolicLabel("A"), "supplement") == "A"

    def test_subsection_explicit(self):
        assert render_label(InsertableArabic(3, ""), "subsection") == "3 momentti"

    def test_implicit_ordinal_subsection(self):
        assert render_label(ImplicitOrdinal(2), "subsection") == "2 momentti"


# ===========================================================================
# Labels: render round-trip (parse -> render -> parse)
# ===========================================================================


class TestLabelRoundTrip:
    """Parse a raw label, render it, and verify the rendered form parses back."""

    @pytest.mark.parametrize("raw,kind,expected_render", [
        ("3 luku", "chapter", "3 luku"),
        ("10 a luku.", "chapter", "10 a luku"),
        ("5 §", "section", "5 §"),
        ("23 a §", "section", "23 a §"),
        ("1)", "item", "1)"),
        ("1 a)", "item", "1 a)"),
        ("aa)", "subitem", "aa)"),
        ("V osa", "part", "V osa"),
    ])
    def test_round_trip(self, raw: str, kind: str, expected_render: str):
        label = parse_label(raw, kind)
        rendered = render_label(label, kind)
        assert rendered == expected_render
        # Parse the rendered form back and verify equality
        reparsed = parse_label(rendered, kind)
        assert reparsed == label


# ===========================================================================
# Labels: label_sort_key
# ===========================================================================


class TestLabelSortKey:
    def test_arabic_numeric_order(self):
        labels: list[InsertableArabic] = [
            InsertableArabic(10, ""),
            InsertableArabic(2, ""),
            InsertableArabic(1, ""),
        ]
        labels.sort(key=label_sort_key)
        assert [l.base for l in labels] == [1, 2, 10]

    def test_arabic_suffix_order(self):
        labels: list[InsertableArabic] = [
            InsertableArabic(1, "b"),
            InsertableArabic(1, ""),
            InsertableArabic(1, "a"),
        ]
        labels.sort(key=label_sort_key)
        assert [(l.base, l.suffix) for l in labels] == [
            (1, ""), (1, "a"), (1, "b"),
        ]

    def test_arabic_base_before_suffix(self):
        """1 a comes after 1, before 2."""
        labels: list[InsertableArabic] = [
            InsertableArabic(2, ""),
            InsertableArabic(1, "a"),
            InsertableArabic(1, ""),
        ]
        labels.sort(key=label_sort_key)
        bases = [(l.base, l.suffix) for l in labels]
        assert bases == [(1, ""), (1, "a"), (2, "")]

    def test_roman_sort_by_value(self):
        labels: list[RomanOrdinal] = [
            RomanOrdinal(5, "V"),
            RomanOrdinal(1, "I"),
            RomanOrdinal(10, "X"),
        ]
        labels.sort(key=label_sort_key)
        assert [l.value for l in labels] == [1, 5, 10]

    def test_alpha_sort_lexicographic(self):
        labels: list[AlphaSequence] = [
            AlphaSequence("ba"),
            AlphaSequence("a"),
            AlphaSequence("aa"),
            AlphaSequence("b"),
        ]
        labels.sort(key=label_sort_key)
        assert [l.token for l in labels] == ["a", "b", "aa", "ba"]

    def test_alpha_sort_value_helper(self):
        assert _alpha_sort_value("a") == 1
        assert _alpha_sort_value("z") == 26
        assert _alpha_sort_value("aa") == 27
        assert _alpha_sort_value("ab") == 28
        assert _alpha_sort_value("ba") == 53


# ===========================================================================
# Labels: is_valid_label_for_kind
# ===========================================================================


class TestIsValidLabelForKind:
    def test_chapter_insertable_arabic(self):
        assert is_valid_label_for_kind(InsertableArabic(1, ""), "chapter")

    def test_chapter_roman_invalid(self):
        assert not is_valid_label_for_kind(RomanOrdinal(1, "I"), "chapter")

    def test_part_roman_valid(self):
        assert is_valid_label_for_kind(RomanOrdinal(5, "V"), "part")

    def test_part_insertable_arabic_valid(self):
        """Parts allow Arabic extension too."""
        assert is_valid_label_for_kind(InsertableArabic(1, ""), "part")

    def test_subitem_alpha_valid(self):
        assert is_valid_label_for_kind(AlphaSequence("a"), "subitem")

    def test_subitem_roman_valid(self):
        assert is_valid_label_for_kind(RomanOrdinal(4, "iv"), "subitem")

    def test_subitem_arabic_invalid(self):
        assert not is_valid_label_for_kind(InsertableArabic(1, ""), "subitem")

    def test_item_insertable_arabic_valid(self):
        assert is_valid_label_for_kind(InsertableArabic(1, "a"), "item")

    def test_item_alpha_valid(self):
        """Items allow AlphaSequence when host uses lettered points."""
        assert is_valid_label_for_kind(AlphaSequence("a"), "item")

    def test_supplement_symbolic_valid(self):
        assert is_valid_label_for_kind(SymbolicLabel("A"), "supplement")

    def test_supplement_roman_valid(self):
        assert is_valid_label_for_kind(RomanOrdinal(4, "IV"), "supplement")

    def test_supplement_arabic_valid(self):
        assert is_valid_label_for_kind(InsertableArabic(3, ""), "supplement")

    def test_subsection_implicit_valid(self):
        assert is_valid_label_for_kind(ImplicitOrdinal(1), "subsection")

    def test_subsection_arabic_valid(self):
        assert is_valid_label_for_kind(InsertableArabic(1, ""), "subsection")

    def test_unknown_kind(self):
        assert not is_valid_label_for_kind(InsertableArabic(1, ""), "widget")

    def test_statute_no_labels(self):
        """Statute has no label series — no label is valid."""
        assert not is_valid_label_for_kind(InsertableArabic(1, ""), "statute")
