"""Tests for typed Finding output from frontend_observations emitters.

Verifies that:
  1. Emitters return List[Finding] (not dicts)
  2. Finding.kind is registered among observation-role registry codes
  3. Finding.detail carries expected structured fields
  4. ObservationSpec descriptions match expected kind strings

Run:
    uv run pytest tests/test_frontend_observations.py -v
"""

from __future__ import annotations


from typing import Literal

from lawvm.core.phase_result import Finding
from lawvm.core.observation_registry import finding_codes_by_role
from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction
from lawvm.finland.frontend_observations import (
    _duplicate_frontend_target_observations,
    _destinationless_move_or_relabel_observations,
    _semantic_collapse_move_or_renumber_observations,
    _scope_anchor_dependence_observations,
)
from lawvm.finland.ops import AmendmentOp, ScopeConfidence
from lawvm.finland.target_kind import TargetKind


OBSERVATION_CODES = set(finding_codes_by_role("observation"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_op(
    op_type: Literal["REPLACE", "REPEAL", "INSERT", "RENUMBER"] = "REPLACE",
    target_section: str = "1",
    target_kind: TargetKind = TargetKind.SECTION,
    target_chapter: str | None = None,
    target_paragraph: int | None = None,
    scope_provenance_tags: tuple[str, ...] = (),
    scope_confidence: ScopeConfidence | None = None,
) -> AmendmentOp:
    return AmendmentOp(
        op_id="",
        op_type=op_type,
        target_section=target_section,
        target_kind=target_kind,
        target_chapter=target_chapter,
        target_paragraph=target_paragraph,
        scope_provenance_tags=scope_provenance_tags,
        scope_confidence=scope_confidence,
    )


# ---------------------------------------------------------------------------
# Type tests — emitters return Finding, not dict
# ---------------------------------------------------------------------------


def test_duplicate_target_returns_finding_instances() -> None:
    ops = [_make_op("REPLACE", "5"), _make_op("REPLACE", "5")]
    result = _duplicate_frontend_target_observations(ops, "2020/123")
    assert len(result) == 1
    obs = result[0]
    assert isinstance(obs, Finding)
    assert obs.kind == "PARSE.DUPLICATE_TARGET_OP"
    assert obs.stage == "frontend_ops"
    assert obs.source_statute == "2020/123"
    assert isinstance(obs.detail, dict)


def test_semantic_collapse_returns_finding_instances() -> None:
    ops = [
        _make_op("REPLACE", "10"),
        _make_op("REPLACE", "10"),
        _make_op("REPLACE", "11"),
        _make_op("REPLACE", "11"),
    ]
    johto = "muutetaan 9–11 §, joista 10 ja 11 § samalla siirretään 3 lukuun"
    result = _semantic_collapse_move_or_renumber_observations(ops, johto, "2021/456")
    assert len(result) >= 1
    for obs in result:
        assert isinstance(obs, Finding)
        assert obs.kind == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
        assert isinstance(obs.detail, dict)


def test_semantic_collapse_accepts_inline_move_without_samalla() -> None:
    ops = [
        _make_op("REPLACE", "33"),
        _make_op("REPLACE", "33"),
        _make_op("REPLACE", "34"),
        _make_op("REPLACE", "34"),
    ]
    johto = "muutetaan 31–34 §, joista 33 ja 34 § siirretään 5 lukuun"
    result = _semantic_collapse_move_or_renumber_observations(ops, johto, "2021/456")
    assert len(result) >= 1
    for obs in result:
        assert isinstance(obs, Finding)
        assert obs.kind == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
        assert obs.detail["collapse_kind"] == "move_to_chapter_clause"


def test_destinationless_move_relabel_returns_finding_instances() -> None:
    ops = [
        AmendmentOp(
            op_id="",
            op_type="RENUMBER",
            target_section="73",
            target_kind=TargetKind.SECTION,
            target_chapter="7",
            lo=LegalOperation(
                op_id="op1",
                sequence=0,
                action=StructuralAction.RENUMBER,
                target=LegalAddress(path=(("section", "73"),)),
            ),
        )
    ]
    result = _destinationless_move_or_relabel_observations(
        ops,
        "kumotaan 1 §, muutetaan 7 luvun 73 §, joka siirretään 61 §:ksi,",
        "2021/456",
    )
    assert len(result) == 1
    obs = result[0]
    assert isinstance(obs, Finding)
    assert obs.kind == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
    assert obs.detail["collapse_kind"] == "destinationless_move_relabel"
    assert obs.detail["destination_missing"] is True


def test_scope_anchor_returns_finding_instances() -> None:
    ops = [
        _make_op(scope_provenance_tags=("grouped_chapter_scope",)),
        _make_op("REPLACE", "2", scope_provenance_tags=("chapter_scope_carry_forward",)),
    ]
    result = _scope_anchor_dependence_observations(ops, "2019/789")
    assert len(result) == 2
    for obs in result:
        assert isinstance(obs, Finding)
        assert isinstance(obs.detail, dict)


# ---------------------------------------------------------------------------
# Registry — every emitted kind is registered
# ---------------------------------------------------------------------------


def test_duplicate_target_kind_is_registered() -> None:
    ops = [_make_op("REPLACE", "5"), _make_op("REPLACE", "5")]
    for obs in _duplicate_frontend_target_observations(ops, "x"):
        assert obs.kind in OBSERVATION_CODES, f"kind '{obs.kind}' not in observation-role registry"


def test_semantic_collapse_kinds_are_registered() -> None:
    ops = [_make_op("REPLACE", "7"), _make_op("REPLACE", "7")]
    johto = "muutetaan 6–7 §, joista 7 § samalla siirretään 2 lukuun"
    for obs in _semantic_collapse_move_or_renumber_observations(ops, johto, "x"):
        assert obs.kind in OBSERVATION_CODES, f"kind '{obs.kind}' not in observation-role registry"


def test_scope_anchor_kinds_are_registered() -> None:
    ops = [
        _make_op(scope_provenance_tags=("grouped_chapter_scope",)),
        _make_op(scope_provenance_tags=("chapter_scope_carry_forward",)),
        _make_op(scope_provenance_tags=("chapter_scope_from_johtolause",)),
        _make_op(scope_provenance_tags=("chapter_scope_from_explicit_chunk",)),
        _make_op(scope_provenance_tags=("grouped_part_scope",)),
        _make_op(scope_provenance_tags=("chapter_scope_stripped_unique_section",)),
    ]
    for obs in _scope_anchor_dependence_observations(ops, "x"):
        assert obs.kind in OBSERVATION_CODES, f"kind '{obs.kind}' not in observation-role registry"


# ---------------------------------------------------------------------------
# Schema tests — detail carries required structured fields
# ---------------------------------------------------------------------------


def test_duplicate_target_detail_has_required_fields() -> None:
    ops = [_make_op("INSERT", "3", target_paragraph=2), _make_op("INSERT", "3", target_paragraph=2)]
    obs = _duplicate_frontend_target_observations(ops, "2020/1")[0]
    d = obs.detail
    assert "target_unit_kind" in d
    assert "target_norm" in d
    assert "op_type" in d
    assert "duplicate_count" in d
    assert d["duplicate_count"] == 2
    assert d["op_type"] == "INSERT"
    assert d["target_unit_kind"] == "section"


def test_semantic_collapse_detail_has_required_fields() -> None:
    ops = [_make_op("REPLACE", "33"), _make_op("REPLACE", "33")]
    johto = "muutetaan 31–33 §, joista 33 § samalla siirretään 5 lukuun"
    for obs in _semantic_collapse_move_or_renumber_observations(ops, johto, "2020/1"):
        d = obs.detail
        assert "collapse_kind" in d
        assert "target_unit_kind" in d
        assert "target_norm" in d
        assert d["target_unit_kind"] == "section"


def test_scope_anchor_detail_has_required_fields() -> None:
    ops = [_make_op(scope_provenance_tags=("grouped_chapter_scope",))]
    obs = _scope_anchor_dependence_observations(ops, "2020/1")[0]
    d = obs.detail
    assert "tag" in d
    assert "op_type" in d
    assert "target_unit_kind" in d
    assert "target_norm" in d
    assert d["target_unit_kind"] == "section"


def test_scope_anchor_observations_surface_chapter_scope_stripping_tags() -> None:
    ops = [_make_op(scope_provenance_tags=("chapter_scope_stripped_unique_section",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.EXPLICIT_SCOPE_REWRITE"
    assert result[0].detail["tag"] == "chapter_scope_stripped_unique_section"


def test_scope_anchor_observations_surface_duplicate_label_scope_stripping_tags() -> None:
    ops = [_make_op(scope_provenance_tags=("chapter_scope_stripped_duplicate_label_outside_stated_chapter",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.EXPLICIT_SCOPE_REWRITE"
    assert result[0].detail["tag"] == "chapter_scope_stripped_duplicate_label_outside_stated_chapter"


def test_scope_anchor_observations_surface_explicit_chunk_scope_tag() -> None:
    ops = [_make_op(scope_provenance_tags=("chapter_scope_from_explicit_chunk",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.EXPLICIT_CHUNK_SCOPE"
    assert result[0].detail["tag"] == "chapter_scope_from_explicit_chunk"


def test_scope_anchor_observations_surface_grouped_part_scope_witness() -> None:
    ops = [_make_op(scope_provenance_tags=("grouped_part_scope",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.CONTEXT_DEPENDENT_ANCHOR"
    assert result[0].detail["tag"] == "grouped_part_scope"
    assert result[0].detail["scope_source"] == "grouped_part"
    assert result[0].detail["scope_confidence"] == "inferred"


def test_scope_anchor_observations_surface_subsection_insert_scope_stripping_tag() -> None:
    ops = [_make_op(scope_provenance_tags=("chapter_scope_stripped_subsection_insert",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.EXPLICIT_SCOPE_REWRITE"
    assert result[0].detail["tag"] == "chapter_scope_stripped_subsection_insert"


def test_scope_anchor_observations_surface_section_facet_insert_scope_stripping_tag() -> None:
    ops = [_make_op(scope_provenance_tags=("chapter_scope_stripped_section_facet_insert",))]

    result = _scope_anchor_dependence_observations(ops, "2020/1")

    assert len(result) == 1
    assert result[0].kind == "LOWER.EXPLICIT_SCOPE_REWRITE"
    assert result[0].detail["tag"] == "chapter_scope_stripped_section_facet_insert"


# ---------------------------------------------------------------------------
# stage field is correctly forwarded
# ---------------------------------------------------------------------------


def test_duplicate_target_default_stage_is_frontend_ops() -> None:
    ops = [_make_op(), _make_op()]
    obs = _duplicate_frontend_target_observations(ops, "x")[0]
    assert obs.stage == "frontend_ops"


def test_duplicate_target_custom_stage_is_respected() -> None:
    ops = [_make_op(), _make_op()]
    obs = _duplicate_frontend_target_observations(ops, "x", stage="frontend_extraction")[0]
    assert obs.stage == "frontend_extraction"


def test_scope_anchor_stage_is_frontend_scope() -> None:
    ops = [_make_op(scope_provenance_tags=("grouped_chapter_scope",))]
    obs = _scope_anchor_dependence_observations(ops, "x")[0]
    assert obs.stage == "frontend_scope"

def test_scope_anchor_prefers_stored_scope_confidence_over_tags() -> None:
    ops = [
        _make_op(
            target_chapter="5",
            scope_provenance_tags=("grouped_chapter_scope",),
            scope_confidence=ScopeConfidence(
                tag="chapter_scope_from_explicit_chunk",
                source="explicit_chunk",
                confidence="explicit",
                resolved_chapter="5",
            ),
        )
    ]

    obs = _scope_anchor_dependence_observations(ops, "x")[0]

    assert obs.kind == "LOWER.EXPLICIT_CHUNK_SCOPE"
    assert obs.detail["tag"] == "chapter_scope_from_explicit_chunk"
    assert obs.detail["scope_source"] == "explicit_chunk"
    assert obs.detail["scope_confidence"] == "explicit"
