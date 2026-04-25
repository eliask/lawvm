"""Tests for lawvm.finland.profile — the unified Finland jurisdiction profile.

Covers:
  - FinlandProfile singleton construction and immutability
  - Default activation rule semantics
  - Identity policies for all unit kinds
  - Validation helpers: well-formed amendment targets, label/series matching,
    parent-child validation
  - Re-exported API accessibility
  - Profile method delegation to underlying ontology/labels modules

Run:
    uv run python -m pytest tests/test_finland_profile.py -v --override-ini="addopts="
"""
from __future__ import annotations

import pytest

from lawvm.finland.profile import (
    FINLAND_DEFAULT_ACTIVATION,
    FINLAND_PROFILE,
    FinlandProfile,
    IdentityPolicy,
    is_well_formed_amendment_target,
    label_matches_series,
    validate_parent_child,
)
from lawvm.finland.labels import (
    AlphaSequence,
    ImplicitOrdinal,
    InsertableArabic,
    RomanOrdinal,
    SymbolicLabel,
)
from lawvm.finland.ontology import (
    ALL_FACET_KINDS,
    ALL_UNIT_KINDS,
    HIERARCHY_ORDER,
    UNIT_ONTOLOGY,
)
from lawvm.core.compile_result import ActivationRule  # noqa: F401


# ===========================================================================
# Profile singleton: construction and immutability
# ===========================================================================


class TestProfileSingleton:
    def test_finland_profile_is_finland_profile_instance(self):
        assert isinstance(FINLAND_PROFILE, FinlandProfile)

    def test_jurisdiction_is_fi(self):
        assert FINLAND_PROFILE.jurisdiction == "FI"

    def test_profile_is_frozen(self):
        with pytest.raises(AttributeError):
            FINLAND_PROFILE.jurisdiction = "EE"  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_unit_ontology_is_canonical(self):
        """Profile's unit_ontology is the same dict as the ontology module's."""
        assert FINLAND_PROFILE.unit_ontology is UNIT_ONTOLOGY

    def test_hierarchy_order_is_canonical(self):
        assert FINLAND_PROFILE.hierarchy_order is HIERARCHY_ORDER

    def test_all_unit_kinds_is_canonical(self):
        assert FINLAND_PROFILE.all_unit_kinds is ALL_UNIT_KINDS

    def test_all_facet_kinds_is_canonical(self):
        assert FINLAND_PROFILE.all_facet_kinds is ALL_FACET_KINDS


# ===========================================================================
# Default activation rule
# ===========================================================================


class TestDefaultActivation:
    def test_default_activation_is_immediate(self):
        assert FINLAND_DEFAULT_ACTIVATION.kind == "immediate"

    def test_default_activation_no_date(self):
        assert FINLAND_DEFAULT_ACTIVATION.effective_date == ""

    def test_default_activation_no_condition(self):
        assert FINLAND_DEFAULT_ACTIVATION.condition_ref == ""

    def test_profile_carries_default_activation(self):
        assert FINLAND_PROFILE.default_activation is FINLAND_DEFAULT_ACTIVATION

    def test_default_activation_is_frozen(self):
        with pytest.raises(AttributeError):
            FINLAND_DEFAULT_ACTIVATION.kind = "fixed_date"  # type: ignore[misc]  # ty: ignore[invalid-assignment]


# ===========================================================================
# Identity policies
# ===========================================================================


class TestIdentityPolicies:
    def test_every_unit_kind_has_identity_policy(self):
        for kind in ALL_UNIT_KINDS:
            policy = FINLAND_PROFILE.identity_policy(kind)
            assert isinstance(policy, IdentityPolicy)
            assert policy.kind == kind

    def test_identity_policy_is_frozen(self):
        policy = FINLAND_PROFILE.identity_policy("section")
        with pytest.raises(AttributeError):
            policy.kind = "bogus"  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_stable_label_units_preserve_identity(self):
        """Units with stable_label identity class preserve identity on renumber."""
        stable_kinds = [
            "statute", "supplement", "part", "division", "chapter",
            "subdivision", "section", "item", "subitem",
        ]
        for kind in stable_kinds:
            policy = FINLAND_PROFILE.identity_policy(kind)
            assert policy.identity_class == "stable_label", (
                f"{kind} should be stable_label"
            )
            assert policy.preserves_identity_on_renumber, (
                f"{kind} should preserve identity on renumber"
            )

    def test_implicit_ordinal_units_do_not_preserve_identity(self):
        """subsection (momentti) does not preserve identity on renumber."""
        policy = FINLAND_PROFILE.identity_policy("subsection")
        assert policy.identity_class == "implicit_ordinal"
        assert not policy.preserves_identity_on_renumber

    def test_suffix_insertion_for_stable_label(self):
        """stable_label units create suffixed labels on insertion (e.g. 5 a)."""
        for kind in ("section", "chapter", "item"):
            policy = FINLAND_PROFILE.identity_policy(kind)
            assert policy.insertion_creates_suffix, (
                f"{kind} should create suffix on insertion"
            )
            assert not policy.insertion_shifts_later, (
                f"{kind} should not shift later siblings"
            )

    def test_shift_insertion_for_implicit_ordinal(self):
        """implicit_ordinal units shift later siblings on insertion."""
        policy = FINLAND_PROFILE.identity_policy("subsection")
        assert policy.insertion_shifts_later
        assert not policy.insertion_creates_suffix

    def test_unknown_kind_raises_keyerror(self):
        with pytest.raises(KeyError):
            FINLAND_PROFILE.identity_policy("widget")

    def test_preserves_identity_on_renumber_unknown_returns_false(self):
        assert not FINLAND_PROFILE.preserves_identity_on_renumber("widget")


# ===========================================================================
# Validation: well-formed amendment targets
# ===========================================================================


class TestWellFormedAmendmentTarget:
    def test_section_valid(self):
        assert is_well_formed_amendment_target("section", "5 \u00a7")

    def test_section_with_suffix_valid(self):
        assert is_well_formed_amendment_target("section", "23 a \u00a7")

    def test_chapter_valid(self):
        assert is_well_formed_amendment_target("chapter", "3 luku")

    def test_chapter_with_suffix_valid(self):
        assert is_well_formed_amendment_target("chapter", "10 a luku.")

    def test_item_valid(self):
        assert is_well_formed_amendment_target("item", "1)")

    def test_item_with_suffix_valid(self):
        assert is_well_formed_amendment_target("item", "1 a)")

    def test_subitem_alpha_valid(self):
        assert is_well_formed_amendment_target("subitem", "a)")

    def test_subitem_compound_valid(self):
        assert is_well_formed_amendment_target("subitem", "aa)")

    def test_part_roman_valid(self):
        assert is_well_formed_amendment_target("part", "V osa")

    def test_subsection_implicit_valid(self):
        """subsection with explicit arabic label is valid."""
        assert is_well_formed_amendment_target("subsection", "3")

    def test_unknown_kind_invalid(self):
        assert not is_well_formed_amendment_target("widget", "5")

    def test_empty_label_invalid(self):
        assert not is_well_formed_amendment_target("section", "")

    def test_wrong_series_invalid(self):
        """Roman numeral for chapter (which expects insertable_arabic) is invalid."""
        assert not is_well_formed_amendment_target("chapter", "IV")

    def test_profile_method_delegates(self):
        """Profile.is_well_formed_amendment_target delegates to module function."""
        assert FINLAND_PROFILE.is_well_formed_amendment_target("section", "5 \u00a7")
        assert not FINLAND_PROFILE.is_well_formed_amendment_target("widget", "5")


# ===========================================================================
# Validation: label matches series
# ===========================================================================


class TestLabelMatchesSeries:
    def test_section_insertable_arabic_matches(self):
        assert label_matches_series("section", InsertableArabic(5, ""))

    def test_chapter_insertable_arabic_matches(self):
        assert label_matches_series("chapter", InsertableArabic(3, ""))

    def test_chapter_roman_does_not_match(self):
        assert not label_matches_series("chapter", RomanOrdinal(3, "III"))

    def test_subitem_alpha_matches(self):
        assert label_matches_series("subitem", AlphaSequence("a"))

    def test_subitem_roman_matches(self):
        assert label_matches_series("subitem", RomanOrdinal(4, "iv"))

    def test_subitem_arabic_does_not_match(self):
        assert not label_matches_series("subitem", InsertableArabic(1, ""))

    def test_subsection_implicit_matches(self):
        assert label_matches_series("subsection", ImplicitOrdinal(1))

    def test_supplement_symbolic_matches(self):
        assert label_matches_series("supplement", SymbolicLabel("A"))

    def test_unknown_kind_does_not_match(self):
        assert not label_matches_series("widget", InsertableArabic(1, ""))

    def test_profile_method_delegates(self):
        assert FINLAND_PROFILE.label_matches_series(
            "section", InsertableArabic(5, "")
        )


# ===========================================================================
# Validation: parent-child
# ===========================================================================


class TestValidateParentChild:
    def test_statute_chapter(self):
        assert validate_parent_child("statute", "chapter")

    def test_chapter_section(self):
        assert validate_parent_child("chapter", "section")

    def test_section_subsection(self):
        assert validate_parent_child("section", "subsection")

    def test_subsection_item(self):
        assert validate_parent_child("subsection", "item")

    def test_item_subitem(self):
        assert validate_parent_child("item", "subitem")

    def test_subitem_subitem_recursive(self):
        assert validate_parent_child("subitem", "subitem")

    def test_section_item_direct(self):
        """Items can be direct children of sections (single-moment sections)."""
        assert validate_parent_child("section", "item")

    def test_chapter_subsection_invalid(self):
        """Chapters cannot directly contain subsections."""
        assert not validate_parent_child("chapter", "subsection")

    def test_item_section_invalid(self):
        assert not validate_parent_child("item", "section")

    def test_unknown_parent_invalid(self):
        assert not validate_parent_child("widget", "section")

    def test_unknown_child_invalid(self):
        assert not validate_parent_child("chapter", "widget")

    def test_profile_method_delegates(self):
        assert FINLAND_PROFILE.validate_parent_child("chapter", "section")
        assert not FINLAND_PROFILE.validate_parent_child("item", "chapter")


# ===========================================================================
# Profile method delegation: ontology queries
# ===========================================================================


class TestProfileOntologyDelegation:
    def test_is_legal_unit(self):
        assert FINLAND_PROFILE.is_legal_unit("section")
        assert not FINLAND_PROFILE.is_legal_unit("widget")

    def test_is_amendable(self):
        assert FINLAND_PROFILE.is_amendable("section")
        assert not FINLAND_PROFILE.is_amendable("widget")

    def test_parent_kinds(self):
        parents = FINLAND_PROFILE.parent_kinds("section")
        assert "chapter" in parents
        assert "statute" in parents

    def test_hierarchy_depth(self):
        assert FINLAND_PROFILE.hierarchy_depth("statute") == 0
        assert FINLAND_PROFILE.hierarchy_depth("section") == 6
        assert FINLAND_PROFILE.hierarchy_depth("widget") == -1

    def test_can_carry_facet(self):
        assert FINLAND_PROFILE.can_carry_facet("section", "heading")
        assert not FINLAND_PROFILE.can_carry_facet("subsection", "heading")

    def test_allowed_label_series(self):
        series = FINLAND_PROFILE.allowed_label_series("chapter")
        assert "insertable_arabic" in series


# ===========================================================================
# Profile method delegation: label operations
# ===========================================================================


class TestProfileLabelDelegation:
    def test_parse_label(self):
        label = FINLAND_PROFILE.parse_label("5 \u00a7", "section")
        assert isinstance(label, InsertableArabic)
        assert label.base == 5

    def test_render_label(self):
        label = InsertableArabic(10, "a")
        rendered = FINLAND_PROFILE.render_label(label, "chapter")
        assert rendered == "10 a luku"

    def test_label_sort_key(self):
        key_a = FINLAND_PROFILE.label_sort_key(InsertableArabic(1, ""))
        key_b = FINLAND_PROFILE.label_sort_key(InsertableArabic(2, ""))
        assert key_a < key_b

    def test_normalize_raw_label(self):
        norm = FINLAND_PROFILE.normalize_raw_label("3 luku.", "chapter")
        assert norm == "3"


# ===========================================================================
# Re-exported API accessibility
# ===========================================================================


class TestReexports:
    """Verify that key APIs are importable from the profile module."""

    def test_ontology_reexports(self):
        from lawvm.finland.profile import (  # noqa: F401
            ALL_FACET_KINDS,
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
        )

    def test_label_reexports(self):
        from lawvm.finland.profile import (  # noqa: F401
            AlphaSequence,
            AnyFinlandLabel,
            FinlandLabel,
            ImplicitOrdinal,
            InsertableArabic,
            RomanOrdinal,
            SymbolicLabel,
            is_valid_label_for_kind,
            label_sort_key,
            normalize_raw_label,
            parse_label,
            render_label,
        )

    def test_temporal_reexport(self):
        from lawvm.finland.profile import ActivationRule  # noqa: F401, F811

    def test_all_list_complete(self):
        """Every name in __all__ is actually importable."""
        import lawvm.finland.profile as mod

        for name in mod.__all__:
            assert hasattr(mod, name), f"__all__ lists {name!r} but it is not defined"
