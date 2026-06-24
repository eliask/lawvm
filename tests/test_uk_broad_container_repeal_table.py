"""Tests for broad-container repeal-table feed-descendant lowering."""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effect_table_lowering import (
    UK_EFFECT_BROAD_CONTAINER_REPEAL_TABLE_FEED_DESCENDANT_REPEAL,
    _uk_target_is_descendant_of_broad_container,
)
from lawvm.uk_legislation.table_sources import (
    _uk_table_cell_extent_restricts_container_descendants,
)


def test_broad_container_rule_id_is_stable() -> None:
    assert UK_EFFECT_BROAD_CONTAINER_REPEAL_TABLE_FEED_DESCENDANT_REPEAL == (
        "uk_effect_broad_container_repeal_table_feed_descendant_repeal"
    )


def test_target_is_descendant_of_broad_container() -> None:
    assert _uk_target_is_descendant_of_broad_container(
        LegalAddress(path=(("schedule", "7"), ("paragraph", "32"))),
        "schedule:7",
    )


def test_target_is_exactly_broad_container() -> None:
    assert _uk_target_is_descendant_of_broad_container(
        LegalAddress(path=(("schedule", "7"),)),
        "schedule:7",
    )


def test_target_is_not_descendant_of_broad_container() -> None:
    assert not _uk_target_is_descendant_of_broad_container(
        LegalAddress(path=(("schedule", "6"), ("paragraph", "32"))),
        "schedule:7",
    )


def test_empty_broad_container_is_false() -> None:
    assert not _uk_target_is_descendant_of_broad_container(
        LegalAddress(path=(("section", "1"), ("subsection", "2"))),
        "",
    )


def test_extent_restricts_section_with_parenthesised_range() -> None:
    assert _uk_table_cell_extent_restricts_container_descendants(
        "Section 44(3) to (5).",
        LegalAddress(path=(("section", "44"),)),
    )


def test_extent_restricts_section_with_subsection_word() -> None:
    assert _uk_table_cell_extent_restricts_container_descendants(
        "Section 44, subsection 1.",
        LegalAddress(path=(("section", "44"),)),
    )


def test_extent_does_not_restrict_bare_section() -> None:
    assert not _uk_table_cell_extent_restricts_container_descendants(
        "Section 44.",
        LegalAddress(path=(("section", "44"),)),
    )


def test_extent_does_not_restrict_section_range_target() -> None:
    """Narrowing on a sibling section must not restrict the first section."""
    assert not _uk_table_cell_extent_restricts_container_descendants(
        "Sections 44 to 46(3).",
        LegalAddress(path=(("section", "44"),)),
    )


def test_extent_restricts_schedule_with_paragraph() -> None:
    assert _uk_table_cell_extent_restricts_container_descendants(
        "Schedule 12, paragraph 1.",
        LegalAddress(path=(("schedule", "12"),)),
    )


def test_extent_does_not_restrict_bare_schedule() -> None:
    assert not _uk_table_cell_extent_restricts_container_descendants(
        "Schedule 12.",
        LegalAddress(path=(("schedule", "12"),)),
    )
