"""Tests for compile_result module -- CanonicalBundle purity guards.

Covers the TypeError enforcement layer that prevents frontend-local types
(e.g. Finland's ResolvedOp) from entering the shared CanonicalBundle as
first-class structural_ops payload.

Run:
    uv run pytest tests/test_compile_result.py -v
"""

from __future__ import annotations

import pytest
from typing import cast

from types import SimpleNamespace

from lawvm.core.compile_result import (
    TemporalEvent,
    TemporalScope,
    CanonicalBundle,
    StrictProfile,
    CompileFailure,
    CompileVerdict,
    SourcePathology,
    _compiled_op_source_statute,
    _compiled_op_matches_section,
    _operation_matches_section,
    _validate_bundle_purity,
    strict_fail_reasons_from_finding_ledger,
    strict_fail_reasons_from_findings_and_verdict,
)
from lawvm.core.phase_result import Finding, VIOLATION_ROLE
from lawvm.core.ir import (
    IRNode,
    IRNodeKind,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
    ScopePredicate,
    StructuralAction,
)
from lawvm.core.provenance import MigrationEvent
from lawvm.replay_adjudication import CompileAdjudication, SourceAdjudication
from lawvm.core.target_scope import normalize_target_unit_kind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legal_op(op_id: str = "op-1", section: str = "1") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section),)),
    )


class _FrontendLocalOp:
    """Placeholder for a frontend-local type (e.g. Finland's ResolvedOp)."""

    op_id = "frontend-local"


def _impure_bundle(*, structural_ops: tuple[object, ...]) -> CanonicalBundle:
    bundle = object.__new__(CanonicalBundle)
    object.__setattr__(bundle, "source_statute", "")
    object.__setattr__(bundle, "target_statute", "")
    object.__setattr__(bundle, "structural_ops", structural_ops)
    object.__setattr__(bundle, "temporal_events", ())
    object.__setattr__(bundle, "migration_events", ())
    object.__setattr__(bundle, "effects", ())
    object.__setattr__(bundle, "groups", ())
    object.__setattr__(bundle, "source", None)
    return bundle


def test_legal_address_and_scope_predicate_normalize_sequence_inputs() -> None:
    address = LegalAddress(path=[("section", "1"), ("subsection", "2")])  # ty: ignore[invalid-argument-type]
    predicate = ScopePredicate(dimension="territory", includes=["AX", ""])  # ty: ignore[invalid-argument-type]

    assert isinstance(address.path, tuple)
    assert address.path == (("section", "1"), ("subsection", "2"))
    assert predicate.includes == frozenset({"AX"})


def test_legal_operation_and_provision_version_normalize_sequence_inputs() -> None:
    op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        applicability=[ScopePredicate(dimension="territory", includes={"AX"})],  # ty: ignore[invalid-argument-type]
        provenance_tags=["tag-a", ""],  # ty: ignore[invalid-argument-type]
    )
    version = ProvisionVersion(
        effective="2020-01-01",
        applicability=[ScopePredicate(dimension="territory", includes={"AX"})],  # ty: ignore[invalid-argument-type]
    )

    assert isinstance(op.applicability, tuple)
    assert op.provenance_tags == ("tag-a", "")
    assert isinstance(version.applicability, tuple)


# ---------------------------------------------------------------------------
# _validate_bundle_purity (standalone function)
# ---------------------------------------------------------------------------


class TestValidateBundlePurityFunction:
    def test_empty_ops_returns_no_violations(self):
        assert _validate_bundle_purity(()) == []

    def test_legal_operation_returns_no_violations(self):
        op = _legal_op()
        assert _validate_bundle_purity((op,)) == []

    def test_multiple_legal_operations_no_violations(self):
        ops = (_legal_op("op-1", "1"), _legal_op("op-2", "2"))
        assert _validate_bundle_purity(ops) == []

    def test_frontend_local_type_returns_violation(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),))  # intentional type violation
        assert len(violations) == 1
        assert "_FrontendLocalOp" in violations[0]
        assert "structural_ops[0]" in violations[0]

    def test_mixed_ops_reports_only_non_legal_operations(self):
        op = _legal_op()
        bad = _FrontendLocalOp()
        violations = _validate_bundle_purity((op, bad))
        assert len(violations) == 1
        assert "structural_ops[1]" in violations[0]

    def test_caller_name_appears_in_violation_message(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),), caller="TestCaller")
        assert "TestCaller" in violations[0]

    def test_violation_message_mentions_lowering_requirement(self):
        violations = _validate_bundle_purity((_FrontendLocalOp(),))
        assert "lowered" in violations[0]


# ---------------------------------------------------------------------------
# CanonicalBundle construction -- purity check in __post_init__
# ---------------------------------------------------------------------------


class TestCanonicalBundleConstructionPurity:
    def test_empty_bundle_constructs_cleanly(self):
        bundle = CanonicalBundle()
        assert bundle.structural_ops == ()

    def test_legal_ops_only_constructs_cleanly(self):
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.structural_ops == (op,)

    def test_frontend_local_op_raises_type_error(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="non-LegalOperation"):
            CanonicalBundle(structural_ops=(bad,))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    def test_type_error_mentions_type_name(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="_FrontendLocalOp"):
            CanonicalBundle(structural_ops=(bad,))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    def test_type_error_mentions_lowering_requirement(self):
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="lowered"):
            CanonicalBundle(structural_ops=(bad,))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    def test_mixed_ops_raises_type_error(self):
        op = _legal_op()
        bad = _FrontendLocalOp()
        with pytest.raises(TypeError, match="non-LegalOperation"):
            CanonicalBundle(structural_ops=(op, bad))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# CanonicalBundle.validate_purity() method
# ---------------------------------------------------------------------------


class TestCanonicalBundleValidatePurity:
    def test_pure_bundle_returns_empty_list(self):
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.validate_purity() == []

    def test_empty_bundle_returns_empty_list(self):
        bundle = CanonicalBundle()
        assert bundle.validate_purity() == []

    def test_impure_bundle_returns_violations(self):
        bad = _FrontendLocalOp()
        bundle = _impure_bundle(structural_ops=(bad,))
        violations = bundle.validate_purity()
        assert len(violations) == 1
        assert "_FrontendLocalOp" in violations[0]

    def test_validate_purity_is_idempotent(self):
        """Calling validate_purity() twice returns the same result."""
        op = _legal_op()
        bundle = CanonicalBundle(structural_ops=(op,))
        assert bundle.validate_purity() == bundle.validate_purity()


class TestCanonicalBundleLineage:
    def test_lineage_is_not_derived_from_structural_ops(self) -> None:
        op = LegalOperation(
            op_id="renumber-op",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            destination=LegalAddress(path=(("section", "1a"),)),
        )
        bundle = CanonicalBundle(structural_ops=(op,))

        assert bundle.migration_events == ()

    def test_provision_lineage_uses_bundle_migration_events(self) -> None:
        old_addr = LegalAddress(path=(("section", "1"),))
        new_addr = LegalAddress(path=(("section", "1a"),))
        migration_event = MigrationEvent(
            event_id="mig:bundle:1",
            kind="renumber",
            from_address=old_addr,
            to_address=new_addr,
            effective="2020-01-01",
        )
        version = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="1a"),
        )
        bundle = CanonicalBundle(migration_events=(migration_event,))
        timelines = {
            new_addr: ProvisionTimeline(
                address=new_addr,
                versions=[version],
            )
        }

        assert bundle.provision_lineage(timelines, old_addr) == [version]

    def test_materialize_pit_uses_bundle_migration_events(self) -> None:
        old_addr = LegalAddress(path=(("section", "1"),))
        new_addr = LegalAddress(path=(("section", "1a"),))
        migration_event = MigrationEvent(
            event_id="mig:bundle:materialize:1",
            kind="renumber",
            from_address=old_addr,
            to_address=new_addr,
            effective="2020-01-01",
        )
        version = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="migrated"),
        )
        bundle = CanonicalBundle(migration_events=(migration_event,))
        timelines = {
            old_addr: ProvisionTimeline(
                address=old_addr,
                versions=[version],
            )
        }
        base = IRStatute(
            statute_id="test/bundle-materialize",
            title="Bundle materialize",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),),
            ),
        )

        pit = bundle.materialize_pit(timelines, "2025-01-01", base=base)

        assert [child.label for child in pit.body.children] == ["1a"]
        assert pit.body.children[0].text == "migrated"


class TestCanonicalBundleTemporalSummaries:
    def test_temporal_event_source_count_tracks_provenance_sources(self) -> None:
        bundle = CanonicalBundle(
            temporal_events=(
                TemporalEvent(event_id="temp:1", kind="commence", scope=TemporalScope(), source=OperationSource(statute_id="2024/1", enacted="2024-01-01")),
                TemporalEvent(event_id="temp:2", kind="expire", scope=TemporalScope()),
            ),
        )

        assert bundle.temporal_events_with_source == 1


class TestCompileResultTargetScopeNormalization:
    def test_normalize_target_unit_kind_prefers_neutral_vocabulary(self) -> None:
        assert normalize_target_unit_kind("chapter") == "chapter"
        assert normalize_target_unit_kind("section") == "section"
        assert normalize_target_unit_kind("L") == ""
        assert normalize_target_unit_kind("P") == ""

    def test_compiled_op_matches_section_does_not_treat_chapter_as_universal(self) -> None:
        assert not _compiled_op_matches_section(
            {"target_unit_kind": "chapter", "target_chapter": "3"},
            "12",
        )

    def test_compiled_op_matches_section_rejects_only_target_kind(self) -> None:
        assert not _compiled_op_matches_section({"target_kind": "L"}, "12")

    def test_compiled_op_matches_section_uses_top_level_neutral_scope(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
            },
            "chapter:3/section:12",
        )

    def test_compiled_op_matches_section_prefers_top_level_neutral_scope_over_nested_scope_payload(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
                "target": {
                    "container": "section",
                    "section": "12",
                },
            },
            "chapter:3/section:12",
        )

    def test_compiled_op_matches_section_prefers_top_level_neutral_scope_over_target_kind(self) -> None:
        assert _compiled_op_matches_section(
            {
                "target_unit_kind": "chapter",
                "target_norm": "3",
                "target_kind": "P",
            },
            "chapter:3/section:12",
        )

    def test_operation_matches_section_rejects_broad_scope_addresses(self) -> None:
        op = LegalOperation(
            op_id="op-schedule",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"),)),
        )
        assert not _operation_matches_section(op, "12")

    def test_operation_matches_section_uses_broad_scope_addresses(self) -> None:
        op = LegalOperation(
            op_id="op-chapter",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "7"),)),
        )
        assert _operation_matches_section(op, "chapter:7/section:12")

    def test_compiled_op_source_statute_prefers_operation_source(self) -> None:
        assert _compiled_op_source_statute(
            {"source": OperationSource(statute_id="2024/5")}
        ) == "2024/5"

    def test_compiled_op_source_statute_ignores_unknown_source_object(self) -> None:
        assert (
            _compiled_op_source_statute({"source": SimpleNamespace(statute_id="2024/5")})
            == ""
        )

    def test_source_adjudication_shape_remains_constructible(self) -> None:
        adjudication = SourceAdjudication(statute_id="2024/1", replay_mode="strict")
        assert adjudication.statute_id == "2024/1"
        assert adjudication.replay_mode == "strict"

    def test_compile_adjudication_shape_remains_compat_bridge(self) -> None:
        adjudication = CompileAdjudication(
            kind="replay_target_not_found",
            message="target missing",
            source_statute="2024/1",
            op_id="op-1",
        )
        assert adjudication.kind == "replay_target_not_found"
        assert adjudication.source_statute == "2024/1"


class TestCompileResultPathologyCarriers:
    def test_source_pathology_carries_neutral_target_unit_kind(self) -> None:
        pathology = SourcePathology(
            code="test",
            message="test",
            source_statute="2024/1",
            target_unit_kind="chapter",
        )
        assert pathology.target_unit_kind == "chapter"

    def test_source_pathology_rejects_implicit_structural_scope(self) -> None:
        with pytest.raises(ValueError, match="structural detail requires explicit neutral"):
            SourcePathology(
                code="test",
                message="test",
                detail={"target_section": "3"},
            )

    def test_source_pathology_supports_scope_less_non_structural_construction(self) -> None:
        pathology = SourcePathology(
            code="EMPTY_OPERATIVE_BODY",
            message="test",
        )
        assert pathology.target_unit_kind == ""

    def test_source_pathology_from_internal_detail_requires_neutral_scope(self) -> None:
        with pytest.raises(ValueError, match="requires explicit neutral target_unit_kind"):
            SourcePathology.from_internal_detail(
                source_statute="2024/1",
                detail={
                    "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                    "message": "test",
                    "target_label": "3",
                },
            )

    def test_source_pathology_from_internal_detail_ignores_target_kind_when_scope_is_neutral(
        self,
    ) -> None:
        pathology = SourcePathology.from_internal_detail(
            source_statute="2024/1",
            detail={
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "test",
                "target_kind": "L",
                "target_unit_kind": "chapter",
                "target_label": "3",
            },
        )
        assert pathology.target_unit_kind == "chapter"
        assert pathology.target_label == "3"
        assert pathology.detail == {"target_kind": "L"}

    def test_source_pathology_from_internal_detail_keeps_explicit_non_structural_case(self) -> None:
        pathology = SourcePathology.from_internal_detail(
            source_statute="2024/1",
            detail={
                "code": "EMPTY_OPERATIVE_BODY",
                "message": "test",
                "target_label": "2024/1",
            },
        )
        assert pathology.target_unit_kind == ""

    def test_compile_failure_carries_neutral_target_unit_kind(self) -> None:
        failure = CompileFailure(
            source_statute="2024/1",
            description="test",
            reason="oops",
            target_section="3",
            target_unit_kind="chapter",
        )
        assert failure.target_unit_kind == "chapter"
        assert failure.scope_detail()["target_unit_kind"] == "chapter"

    def test_compile_failure_from_scope_preserves_reason_code(self) -> None:
        failure = CompileFailure.from_scope(
            source_statute="2024/1",
            description="test",
            reason="oops",
            target_section="3",
            target_unit_kind="section",
            reason_code="ELAB.REJECTED_LANGUAGE_VARIANT_ONLY",
        )

        assert failure.reason_code == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"
        assert failure.as_detail()["reason_code"] == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"


class TestCompileVerdictRail:
    def test_compile_verdict_barrier_codes_authoritatively_override_runtime_projection(self) -> None:
        """Verdict barrier rail is authoritative; runtime findings do not carry barrier kinds."""
        verdict = CompileVerdict(
            mode="strict",
            profile="test",
            status="strict_blocked_by_recovery",
            barrier_codes=("APPLY.TREE_INVARIANT_VIOLATION",),
        )
        violation = Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            detail={"section": "1"},
            blocking=True,
        )

        reasons = strict_fail_reasons_from_findings_and_verdict((violation,), verdict=verdict)

        assert reasons == (
            "APPLY.TREE_INVARIANT_VIOLATION",
            "RUNTIME.VIOLATION",
        )


class TestStrictFailReasonsFromFindingLedger:
    def test_strict_fail_reasons_detects_text_substitution_from_structural_enum(self) -> None:
        profile = StrictProfile(name="test")
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(
                LegalOperation(
                    op_id="op",
                    sequence=1,
                    action=StructuralAction.TEXT_REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                ),
            ),
            failures=(),
            findings=(),
        )

        assert reasons == ["APPLY.WORD_SUBSTITUTION"]

    def test_strict_fail_reasons_detects_text_substitution_from_canonical_string(self) -> None:
        profile = StrictProfile(name="test")
        canonical_action_op = SimpleNamespace(action="text_repeal")
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=cast(tuple[LegalOperation, ...], (canonical_action_op,)),
            failures=(),
            findings=(),
        )

        assert reasons == ["APPLY.WORD_SUBSTITUTION"]

    def test_strict_fail_reasons_is_not_derived_from_source_effective_dates(self) -> None:
        profile = StrictProfile(name="test")
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(
                LegalOperation(
                    op_id="op",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "1"),)),
                    source=OperationSource(statute_id="2024/1", enacted="2024-01-01"),
                ),
            ),
            failures=(),
            findings=(),
        )

        assert "TIME.MISSING_EFFECTIVE_DATE" not in reasons

    def test_strict_fail_reasons_respects_corrigendum_policy(self) -> None:
        profile = StrictProfile(name="test", allows_source_correction_rules=True)
        findings = (
            Finding(
                kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
                role="obligation",
                stage="apply",
                detail={},
                blocking=True,
            ),
        )
        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(),
            failures=(),
            findings=findings,
        )

        assert "APPLY.SOURCE_CORRECTED_BY_PATCH" not in reasons

    def test_strict_fail_reasons_derives_runtime_violation_generically(self) -> None:
        profile = StrictProfile(name="test")
        findings = (
            Finding(
                kind="RUNTIME.VIOLATION",
                role=VIOLATION_ROLE,
                stage="replay",
                detail={},
                blocking=True,
            ),
        )

        reasons = strict_fail_reasons_from_finding_ledger(
            profile,
            compiled_ops=(),
            canonical_ops=(),
            failures=(),
            findings=findings,
        )

        assert reasons == ["RUNTIME.VIOLATION"]


class TestStrictFailReasonsFromFindingsAndVerdict:
    def test_strict_fail_reasons_from_findings_and_verdict_excludes_barrier_registry_codes(self) -> None:
        projected_barrier_row = SimpleNamespace(
            kind="APPLY.TREE_INVARIANT_VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={},
            blocking=True,
        )
        runtime_violation = Finding(
            kind="RUNTIME.VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={},
            blocking=True,
        )
        findings = cast(tuple[Finding, ...], (projected_barrier_row, runtime_violation))

        reasons = strict_fail_reasons_from_findings_and_verdict(findings)

        assert reasons == ("RUNTIME.VIOLATION",)

    def test_strict_fail_reasons_from_findings_and_verdict_projects_apply_boundary_barrier_codes(
        self,
    ) -> None:
        runtime_violation = Finding(
            kind="RUNTIME.VIOLATION",
            role=VIOLATION_ROLE,
            stage="replay",
            detail={"barrier_code": "REPLAY_SKIPPED_OP_MUTATED_TREE"},
            blocking=True,
        )

        reasons = strict_fail_reasons_from_findings_and_verdict((runtime_violation,))

        assert reasons == ("REPLAY_SKIPPED_OP_MUTATED_TREE",)
