from __future__ import annotations

from lawvm.core.target_scope import (
    matching_sections_for_scope,
    normalize_target_unit_kind,
    ResolvedTargetScope,
    resolve_internal_target_scope,
)
from lawvm.finland.ops import scope_resolution_witness_from_tags


def test_normalize_target_unit_kind_is_strict_neutral() -> None:
    assert normalize_target_unit_kind("SECTION") == "section"
    assert normalize_target_unit_kind("chapter") == "chapter"
    assert normalize_target_unit_kind("part") == "part"
    assert normalize_target_unit_kind("annex") == ""
    assert normalize_target_unit_kind("schedule") == ""
    assert normalize_target_unit_kind("unknown-kind") == ""


def test_infer_target_unit_kind_from_scope_keeps_section() -> None:
    scope = resolve_internal_target_scope(
        {
            "target_section": "5",
            "target_chapter": "3",
        }
    )
    assert scope.target_unit_kind == "section"
    assert scope.target_norm == "5"


def test_resolve_internal_target_scope_ignores_legacy_top_level_chapter_alias() -> None:
    scope = resolve_internal_target_scope(
        {
            "target_unit_kind": "chapter",
            "chapter": "3",
        }
    )
    assert scope.target_unit_kind == "chapter"
    assert scope.target_chapter == ""
    assert scope.target_norm == ""


def test_matching_sections_for_chapter_scope_does_not_expand_from_target_section_surrogate() -> None:
    matched = matching_sections_for_scope(
        scope=ResolvedTargetScope(
            target_unit_kind="chapter",
            target_section="3",
        ),
        section_labels=[
            "chapter:3/section:21",
            "chapter:4/section:21",
        ],
    )
    assert matched == []


def test_matching_sections_for_part_scope_does_not_expand_from_target_section_surrogate() -> None:
    matched = matching_sections_for_scope(
        scope=ResolvedTargetScope(
            target_unit_kind="part",
            target_section="II",
        ),
        section_labels=[
            "part:I/chapter:3/section:21",
            "part:II/chapter:3/section:21",
        ],
    )
    assert matched == []


def test_scope_resolution_witness_from_tags_classifies_explicit_chunk() -> None:
    witness = scope_resolution_witness_from_tags(
        ("chapter_scope_from_explicit_chunk",),
        resolved_chapter="5",
    )
    assert witness is not None
    assert witness.tag == "chapter_scope_from_explicit_chunk"
    assert witness.source == "explicit_chunk"
    assert witness.confidence == "explicit"
    assert witness.resolved_chapter == "5"


def test_scope_resolution_witness_from_tags_prefers_rewrite_over_carry() -> None:
    witness = scope_resolution_witness_from_tags(
        (
            "chapter_scope_carry_forward",
            "chapter_scope_stripped_unique_section",
        ),
        resolved_chapter="6",
    )
    assert witness is not None
    assert witness.tag == "chapter_scope_stripped_unique_section"
    assert witness.source == "explicit_scope_rewrite"
    assert witness.confidence == "rewritten"
    assert witness.resolved_chapter == "6"


def test_scope_resolution_witness_from_tags_classifies_grouped_part_scope() -> None:
    witness = scope_resolution_witness_from_tags(
        ("grouped_part_scope",),
        resolved_chapter="3",
    )
    assert witness is not None
    assert witness.tag == "grouped_part_scope"
    assert witness.source == "grouped_part"
    assert witness.confidence == "inferred"
    assert witness.resolved_chapter == "3"


def test_scope_resolution_witness_from_tags_classifies_section_facet_insert_scope_rewrite() -> None:
    witness = scope_resolution_witness_from_tags(
        ("chapter_scope_stripped_section_facet_insert",),
        resolved_chapter="7",
    )
    assert witness is not None
    assert witness.tag == "chapter_scope_stripped_section_facet_insert"
    assert witness.source == "explicit_scope_rewrite"
    assert witness.confidence == "rewritten"
    assert witness.resolved_chapter == "7"
