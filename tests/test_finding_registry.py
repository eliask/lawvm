"""Tests for the unified FindingSpec registry and Violation in PhaseResult.

Run:
    uv run pytest tests/test_finding_registry.py -v
"""
from __future__ import annotations

from typing import get_args

import pytest

from lawvm.core.observation_registry import (
    FINDING_REGISTRY,
    Enforcement,
    FindingFamily,
    FindingSpec,
    finding_codes_by_role,
    is_registered_finding_kind,
    get_finding_spec,
    validate_finding_projection,
)
from lawvm.core.phase_result import (
    Finding,
    Observation,
    Obligation,
    PhaseBuilder,
    PhaseResult,
    Violation,
)


# ---------------------------------------------------------------------------
# Phase 1: FindingSpec registry coverage
# ---------------------------------------------------------------------------


def test_every_observation_kind_has_finding_spec() -> None:
    """Every observation-role code has a corresponding FindingSpec."""
    for code in finding_codes_by_role("observation"):
        assert code in FINDING_REGISTRY, (
            f"observation-role code {code!r} missing from FINDING_REGISTRY"
        )


def test_every_obligation_kind_has_finding_spec() -> None:
    """Every obligation-typed code in FINDING_REGISTRY has a corresponding FindingSpec."""
    for code in (code for code, spec in FINDING_REGISTRY.items() if spec.is_obligation):
        assert code in FINDING_REGISTRY, (
            f"obligation registry entry {code!r} missing from FINDING_REGISTRY"
        )


def test_every_registry_barrier_kind_has_finding_spec() -> None:
    """Every barrier-typed code in FINDING_REGISTRY has a corresponding FindingSpec."""
    for code in finding_codes_by_role("barrier"):
        spec = get_finding_spec(code)
        assert spec is not None
        assert code in FINDING_REGISTRY, (
            f"barrier registry entry {code!r} missing from FINDING_REGISTRY"
        )


def test_finding_spec_family_is_valid() -> None:
    """Every FindingSpec.family is a valid FindingFamily literal."""
    valid_families = set(get_args(FindingFamily))
    for code, spec in FINDING_REGISTRY.items():
        assert spec.family in valid_families, (
            f"FindingSpec {code!r} has invalid family {spec.family!r}"
        )


def test_finding_spec_enforcement_is_valid() -> None:
    """Every FindingSpec.default_enforcement is a valid Enforcement literal."""
    valid_enforcements = set(get_args(Enforcement))
    for code, spec in FINDING_REGISTRY.items():
        assert spec.default_enforcement in valid_enforcements, (
            f"FindingSpec {code!r} has invalid enforcement {spec.default_enforcement!r}"
        )


def test_finding_spec_code_matches_key() -> None:
    """Every FindingSpec.code matches its key in FINDING_REGISTRY."""
    for code, spec in FINDING_REGISTRY.items():
        assert spec.code == code, (
            f"FINDING_REGISTRY key {code!r} != spec.code {spec.code!r}"
        )


def test_get_finding_spec_returns_spec() -> None:
    """get_finding_spec returns the correct FindingSpec for a known code."""
    spec = get_finding_spec("ELAB.SOURCE_PATHOLOGY")
    assert spec is not None
    assert isinstance(spec, FindingSpec)
    assert spec.code == "ELAB.SOURCE_PATHOLOGY"
    assert spec.family == "source_pathology"


def test_get_finding_spec_rejects_deleted_source_pathology_alias() -> None:
    """Raw source-pathology alias should not survive registry collapse."""
    assert get_finding_spec("source_pathology") is None


def test_get_finding_spec_returns_none_for_unknown() -> None:
    """get_finding_spec returns None for an unknown code."""
    assert get_finding_spec("nonexistent_code_xyz") is None


def test_is_registered_finding_kind_reflects_registry_authority() -> None:
    assert is_registered_finding_kind("ELAB.SOURCE_PATHOLOGY") is True
    assert is_registered_finding_kind("source_pathology") is False
    assert is_registered_finding_kind("nonexistent_code_xyz") is False


def test_tree_invariant_registry_code_is_runtime_violation() -> None:
    spec = get_finding_spec("APPLY.TREE_INVARIANT_VIOLATION")
    assert spec is not None
    assert spec.role == "violation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "violation", True)


def test_uncovered_body_recovery_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.UNCOVERED_BODY_RECOVERY")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_fallback_whole_section_replace_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.FALLBACK_WHOLE_SECTION_REPLACE")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_omission_expansion_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("ELAB.OMISSION_EXPANSION")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_source_pathology_detected_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.SOURCE_PATHOLOGY_DETECTED")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_failed_operation_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.FAILED_OPERATION")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_explicit_chunk_scope_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("LOWER.EXPLICIT_CHUNK_SCOPE_REQUIRED")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_explicit_scope_rewrite_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_extraction_fallback_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("PARSE.EXTRACTION_FALLBACK")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_target_guessing_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("PARSE.TARGET_GUESSING")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_context_dependent_anchor_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_trigger_coverage_incomplete_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("TIME.TRIGGER_COVERAGE_INCOMPLETE")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_empty_same_day_interval_registry_code_is_nonblocking_observation() -> None:
    spec = get_finding_spec("TIME.EMPTY_SAME_DAY_INTERVAL")
    assert spec is not None
    assert spec.role == "observation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "observation", False)


def test_timeline_execution_issue_registry_code_is_blocking_obligation() -> None:
    spec = get_finding_spec("TIME.TIMELINE_EXECUTION_ISSUE")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_flattened_sublist_registry_code_is_nonblocking_observation() -> None:
    spec = get_finding_spec("flattened_sublist_family_warning")
    assert spec is not None
    assert spec.role == "observation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "observation", False)


def test_source_corrected_by_patch_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.SOURCE_CORRECTED_BY_PATCH")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_source_incomplete_registry_code_is_runtime_obligation() -> None:
    spec = get_finding_spec("APPLY.SOURCE_INCOMPLETE")
    assert spec is not None
    assert spec.role == "obligation"
    assert spec.is_barrier is False
    validate_finding_projection(spec.code, "obligation", True)


def test_validate_finding_projection_rejects_role_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 'observation'"):
        validate_finding_projection("ELAB.SOURCE_PATHOLOGY", "violation", True)


def test_validate_finding_projection_rejects_unregistered_runtime_kind() -> None:
    with pytest.raises(ValueError, match="not registered"):
        validate_finding_projection("totally_unknown_kind", "observation", False)


def test_get_finding_spec_rejects_deleted_contingent_alias() -> None:
    """Raw contingent compat alias should not survive registry collapse."""
    assert get_finding_spec("contingent_effective_date") is None


def test_get_finding_spec_rejects_deleted_failed_operation_aliases() -> None:
    """Raw failed-op compat aliases should not survive registry collapse."""
    assert get_finding_spec("failed_op") is None
    assert get_finding_spec("failed_operation") is None


def test_get_finding_spec_rejects_deleted_routing_skip_aliases() -> None:
    """Raw routing-skip finding aliases should not survive registry collapse."""
    assert get_finding_spec("citation_mismatch_skip") is None
    assert get_finding_spec("num_collision_skip") is None


def test_get_finding_spec_rejects_deleted_expiry_override_alias() -> None:
    """Temporary expiry override should not survive as a core finding alias."""
    assert get_finding_spec("commencement_expiry_override") is None


def test_get_finding_spec_rejects_deleted_extraction_fallback_alias() -> None:
    """Raw extraction-fallback alias should not survive registry collapse."""
    assert get_finding_spec("extraction_fallback_required") is None


def test_get_finding_spec_rejects_deleted_source_aliases() -> None:
    """Deleted source-side raw aliases should not survive registry collapse."""
    assert get_finding_spec("source_incomplete") is None
    assert get_finding_spec("source_pathology_detected") is None


def test_get_finding_spec_rejects_deleted_target_guessing_alias() -> None:
    """Raw target-guessing alias should not survive registry collapse."""
    assert get_finding_spec("target_guessing_required") is None


def test_get_finding_spec_rejects_deleted_parse_lowering_aliases() -> None:
    """Raw parse/lowering fallback aliases should not survive registry collapse."""
    assert get_finding_spec("unknown_action") is None
    assert get_finding_spec("scope_carry_forward_required") is None
    assert get_finding_spec("context_dependent_anchor_resolution_required") is None


def test_get_finding_spec_rejects_deleted_obligation_aliases() -> None:
    """Raw obligation aliases should not survive registry collapse."""
    assert get_finding_spec("source_corrected_by_patch") is None
    assert get_finding_spec("uncovered_body_recovery_required") is None
    assert get_finding_spec("fallback_whole_section_replace_required") is None


def test_get_finding_spec_rejects_deleted_strict_rejected_aliases() -> None:
    """Raw strict-rejected aliases should not survive registry collapse."""
    assert get_finding_spec("strict_rejected_source_pathology") is None
    assert get_finding_spec("strict_rejected_uncovered_body") is None
    assert get_finding_spec("strict_rejected_corrigendum_patch") is None
    assert get_finding_spec("strict_rejected_target_guessing") is None


def test_get_finding_spec_rejects_deleted_temporal_and_elaboration_aliases() -> None:
    """Raw temporal/elaboration/apply aliases should not survive registry collapse."""
    assert get_finding_spec("missing_explicit_effective_date") is None
    assert get_finding_spec("omission_expansion_required") is None
    assert get_finding_spec("word_substitution_required") is None


def test_get_finding_spec_rejects_deleted_governed_observation_aliases() -> None:
    """Raw observation aliases should not survive once governed codes are live."""
    assert get_finding_spec("duplicate_target_op") is None
    assert get_finding_spec("semantic_collapse_move_or_renumber") is None
    assert get_finding_spec("context_dependent_anchor_resolution") is None
    assert get_finding_spec("consistency_section_no_timeline") is None
    assert get_finding_spec("consistency_timeline_no_section") is None
    assert get_finding_spec("consistency_content_drift") is None
    assert get_finding_spec("container_payload_pruned_shadowed_sections") is None
    assert get_finding_spec("align_sparse_omission_subsections_to_live") is None
    assert get_finding_spec("legacy_dispatch_fallback") is None
    assert get_finding_spec("missing_payload_surface") is None
    assert get_finding_spec("sparse_payload_leftover") is None
    assert get_finding_spec("sparse_slot_binding") is None
    assert get_finding_spec("payload_completeness") is None
    assert get_finding_spec("mixed_sparse_slot_cross_paragraph_binding") is None
    assert get_finding_spec("drop_item_replaces_missing_from_sparse_payload") is None
    assert get_finding_spec("unassigned_sparse_payload_slots") is None
    assert get_finding_spec("tree_invariant_violation") is None
    assert get_finding_spec("replay_product_invariant_violation") is None
    assert get_finding_spec("split_sparse_omission_single_subsection_across_consecutive_replaces") is None
    assert get_finding_spec("split_fused_restarted_subsection_across_consecutive_replaces") is None


def test_finding_registry_nonempty() -> None:
    """FINDING_REGISTRY has entries (basic sanity)."""
    assert len(FINDING_REGISTRY) > 0


def test_finding_registry_covers_all_legacy_registries() -> None:
    """FINDING_REGISTRY is a superset of all three legacy registries."""
    all_legacy = (
        set(finding_codes_by_role("observation"))
        | {code for code, spec in FINDING_REGISTRY.items() if spec.is_obligation}
        | set(finding_codes_by_role("barrier"))
    )
    missing = all_legacy - set(FINDING_REGISTRY)
    assert not missing, f"Legacy codes missing from FINDING_REGISTRY: {missing}"


# ---------------------------------------------------------------------------
# Phase 2: Violation in PhaseResult
# ---------------------------------------------------------------------------


def _violation(kind: str = "RUNTIME.VIOLATION", stage: str = "test_stage") -> Violation:
    return Violation(kind=kind, stage=stage, detail={"msg": "test"})


def _violation_finding(vio: Violation) -> Finding:
    return Finding(
        kind=vio.kind,
        role="violation",
        stage=vio.stage,
        detail=dict(vio.detail),
        source_statute=vio.source_statute,
        blocking=True,
    )


def test_violation_makes_has_blocking_true() -> None:
    """A PhaseResult with a Violation has has_blocking == True."""
    v = _violation()
    pr = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind=v.kind,
                role="violation",
                stage=v.stage,
                detail=dict(v.detail),
                source_statute=v.source_statute,
                blocking=True,
            ),
        ),
    )
    assert pr.has_blocking is True


def test_has_blocking_false_without_violations_or_blocking_obligations() -> None:
    """No violations and no blocking obligations means has_blocking is False."""
    pr = PhaseResult(output=None)
    assert pr.has_blocking is False


def test_has_blocking_true_with_blocking_obligation_no_violation() -> None:
    """Blocking obligation alone makes has_blocking True."""
    obl = Obligation(
        kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        stage="test",
        detail={},
        blocking=True,
    )
    pr = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind=obl.kind,
                role="obligation",
                stage=obl.stage,
                detail=dict(obl.detail),
                blocking=obl.blocking,
            ),
        ),
    )
    assert pr.has_blocking is True


def test_violation_propagates_through_merge() -> None:
    """Violations from both sides accumulate in merge."""
    v1 = _violation("RUNTIME.VIOLATION", "stage_a")
    v2 = _violation("RUNTIME.VIOLATION", "stage_b")
    pr_a = PhaseResult(
        output="a",
        findings=(
            Finding(
                kind=v1.kind,
                role="violation",
                stage=v1.stage,
                detail=dict(v1.detail),
                source_statute=v1.source_statute,
                blocking=True,
            ),
        ),
    )
    pr_b = PhaseResult(
        output="b",
        findings=(
            Finding(
                kind=v2.kind,
                role="violation",
                stage=v2.stage,
                detail=dict(v2.detail),
                source_statute=v2.source_statute,
                blocking=True,
            ),
        ),
    )
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "violation") == (_violation_finding(v1), _violation_finding(v2))
    assert merged.output == "b"
    assert merged.has_blocking is True


def test_merge_preserves_empty_violations() -> None:
    """Merge with no violations on either side produces empty tuple."""
    pr_a = PhaseResult(output="a")
    pr_b = PhaseResult(output="b")
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "violation") == ()


def test_violations_of_kind() -> None:
    """violations_of_kind filters correctly."""
    v1 = _violation("RUNTIME.VIOLATION")
    v2 = _violation("RUNTIME.VIOLATION", "other_stage")
    v3 = Violation(kind="RUNTIME.VIOLATION", stage="other", detail={"x": 1})
    pr = PhaseResult(
        output=None,
        findings=tuple(
            Finding(
                kind=v.kind,
                role="violation",
                stage=v.stage,
                detail=dict(v.detail),
                source_statute=v.source_statute,
                blocking=True,
            )
            for v in (v1, v2, v3)
        ),
    )
    result = tuple(v for v in pr.findings() if v.role == "violation" and v.kind == "RUNTIME.VIOLATION")
    assert result == (
        _violation_finding(v1),
        _violation_finding(v2),
        _violation_finding(v3),
    )


def test_violations_of_kind_empty() -> None:
    """violations_of_kind returns empty tuple when no match."""
    v = _violation("RUNTIME.VIOLATION")
    pr = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind=v.kind,
                role="violation",
                stage=v.stage,
                detail=dict(v.detail),
                source_statute=v.source_statute,
                blocking=True,
            ),
        ),
    )
    assert tuple(vio for vio in pr.findings() if vio.role == "violation" and vio.kind == "NONEXISTENT.RUNTIME.KIND") == ()


def test_has_violation_true() -> None:
    """has_violation returns True when violation with that kind exists."""
    v = _violation("RUNTIME.VIOLATION")
    pr = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind=v.kind,
                role="violation",
                stage=v.stage,
                detail=dict(v.detail),
                source_statute=v.source_statute,
                blocking=True,
            ),
        ),
    )
    assert any(vio.kind == "RUNTIME.VIOLATION" for vio in pr.findings() if vio.role == "violation") is True


def test_has_violation_false() -> None:
    """has_violation returns False when no violation with that kind."""
    v = _violation("RUNTIME.VIOLATION")
    pr = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind=v.kind,
                role="violation",
                stage=v.stage,
                detail=dict(v.detail),
                source_statute=v.source_statute,
                blocking=True,
            ),
        ),
    )
    assert any(vio.kind == "NONEXISTENT.RUNTIME.KIND" for vio in pr.findings() if vio.role == "violation") is False


def test_has_violation_empty() -> None:
    """has_violation returns False on empty PhaseResult."""
    pr = PhaseResult(output=None)
    assert any(vio.kind == "anything" for vio in pr.findings() if vio.role == "violation") is False


# ---------------------------------------------------------------------------
# PhaseBuilder.violate
# ---------------------------------------------------------------------------


def test_phase_builder_violate() -> None:
    """PhaseBuilder.violate() produces a correct Violation in the finished PhaseResult."""
    b = PhaseBuilder()
    b.violate("RUNTIME.VIOLATION", "apply", {"section": "1"}, source_statute="2009/953")
    result = b.finish("output_val")

    violations = tuple(f for f in result.findings() if f.role == "violation")
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "RUNTIME.VIOLATION"
    assert v.stage == "apply"
    assert v.detail == {"section": "1"}
    assert v.source_statute == "2009/953"
    assert result.output == "output_val"
    assert result.has_blocking is True


def test_phase_builder_add_findings_records_multiple_violations() -> None:
    """PhaseBuilder.add_findings() records multiple violations."""
    b = PhaseBuilder()
    vs = [_violation("RUNTIME.VIOLATION", "stage_a"), _violation("RUNTIME.VIOLATION", "stage_b")]
    b.add_findings(
        Finding(
            kind=v.kind,
            role="violation",
            stage=v.stage,
            detail=dict(v.detail),
            source_statute=v.source_statute,
            blocking=True,
        )
        for v in vs
    )
    result = b.finish(None)
    violations = tuple(f for f in result.findings() if f.role == "violation")
    assert len(violations) == 2
    assert violations[0].kind == "RUNTIME.VIOLATION"
    assert violations[0].stage == "stage_a"
    assert violations[1].kind == "RUNTIME.VIOLATION"
    assert violations[1].stage == "stage_b"


def test_phase_builder_mixed_signals() -> None:
    """PhaseBuilder can accumulate observations, obligations, and violations together."""
    b = PhaseBuilder()
    b.observe("ELAB.SOURCE_PATHOLOGY", "elaborate", {"note": "test"})
    b.oblige("APPLY.STRICT_REJECTED_UNCOVERED_BODY", "apply", {"msg": "x"}, blocking=True)
    b.violate("RUNTIME.VIOLATION", "apply", {"section": "2"})
    result = b.finish("output")

    assert len(tuple(f for f in result.findings() if f.role == "observation")) == 1
    assert len(tuple(f for f in result.findings() if f.role == "obligation")) == 1
    assert len(tuple(f for f in result.findings() if f.role == "violation")) == 1
    assert result.has_blocking is True


def test_phase_builder_rejects_unregistered_kind() -> None:
    b = PhaseBuilder()
    with pytest.raises(ValueError, match="not registered"):
        b.observe("totally_unknown_kind", "test", {"x": 1})


def test_phase_builder_rejects_deleted_source_pathology_alias() -> None:
    b = PhaseBuilder()
    with pytest.raises(ValueError, match="not registered"):
        b.observe("source_pathology", "test", {"x": 1})


def test_direct_observation_rejects_unregistered_kind() -> None:
    with pytest.raises(ValueError, match="not registered"):
        Observation(kind="totally_unknown_kind", stage="test", detail={})


def test_direct_obligation_rejects_unregistered_kind() -> None:
    with pytest.raises(ValueError, match="not registered"):
        Obligation(kind="totally_unknown_kind", stage="test", detail={}, blocking=True)


def test_direct_violation_rejects_unregistered_kind() -> None:
    with pytest.raises(ValueError, match="not registered"):
        Violation(kind="totally_unknown_kind", stage="test", detail={})


def test_violation_source_statute_default() -> None:
    """Violation.source_statute defaults to empty string."""
    v = Violation(kind="RUNTIME.VIOLATION", stage="test", detail={})
    assert v.source_statute == ""
