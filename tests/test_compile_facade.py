"""Tests for CompileFacade — public top-level aggregate facade.

CompileFacade is the clean output facade over PhaseResult/EffectIntent surfaces
defined in lawvm.core.compile_facade.

Run:
    uv run pytest tests/test_compile_facade.py -v
"""
from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource, ProvisionTimeline, ProvisionVersion, ScopePredicate, StructuralAction

from typing import Any, cast

import pytest

from lawvm.contracts import ArtifactEnvelope, ProcessingStatus
from lawvm.core.compile_facade import (
    CompileFacade,
)
from lawvm.core.compile_result import (
    ActivationRule,
    CanonicalBundle,
    CanonicalEffect,
    CompileVerdict,
    EffectGroup,
    TemporalEvent,
    TemporalScope,
)
from lawvm.core.compile_views import (
    projection_rows_from_findings,
    quirks_used_from_findings,
    source_pathology_rows_from_findings,
    source_completeness_issues_from_findings,
    _QUIRKS_OBS_KINDS,
    _SOURCE_COMPLETENESS_OBS_KINDS,
    _SOURCE_COMPLETENESS_OBL_KINDS,
)
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core.phase_result import Finding, Observation, Obligation, PhaseResult, Violation
from lawvm.core.timeline import select_active_version
from lawvm.core.provenance import MigrationEvent
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(kind: str, stage: str = "test_stage") -> Observation:
    return Observation(kind=kind, stage=stage, detail={})


def _obl(kind: str, stage: str = "test_stage", blocking: bool = True) -> Obligation:
    return Obligation(kind=kind, stage=stage, detail={}, blocking=blocking)


def _pr(
    output=None,
    observations=(),
    obligations=(),
    violations=(),
    migration_events=(),
    temporal_events=(),
):
    return PhaseResult(
        output=output,
        findings=(
            tuple(
                Finding(
                    kind=obs.kind,
                    role="observation",
                    stage=obs.stage,
                    detail=dict(obs.detail),
                    source_statute=obs.source_statute,
                    blocking=False,
                )
                for obs in observations
            )
            + tuple(
                Finding(
                    kind=obl.kind,
                    role="obligation",
                    stage=obl.stage,
                    detail=dict(obl.detail),
                    blocking=obl.blocking,
                )
                for obl in obligations
            )
            + tuple(
                Finding(
                    kind=vio.kind,
                    role="violation",
                    stage=vio.stage,
                    detail=dict(vio.detail),
                    source_statute=vio.source_statute,
                    blocking=True,
                )
                for vio in violations
            )
        ),
        migration_events=tuple(migration_events),
        temporal_events=tuple(temporal_events),
    )


def _projection_rows(facade: CompileFacade) -> tuple[dict[str, object], ...]:
    return projection_rows_from_findings(facade.finding_ledger)


def _source_pathology_rows(facade: CompileFacade) -> tuple[dict[str, object], ...]:
    return source_pathology_rows_from_findings(facade.finding_ledger)


# ---------------------------------------------------------------------------
# Construction from PhaseResult
# ---------------------------------------------------------------------------

class TestFromPhaseResult:
    def test_empty_phase_result_produces_empty_facade(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert facade.bundle.structural_ops == ()
        assert facade.finding_ledger == ()
        assert facade.replay_mode == "legal_pit"
        assert facade.strict_profile_name is None

    def test_replay_mode_and_profile_name_stored(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="finlex_oracle",
            strict_profile_name="finland_ingestion_v1",
        )
        assert facade.replay_mode == "finlex_oracle"
        assert facade.strict_profile_name == "finland_ingestion_v1"

    def test_empty_replay_mode_is_rejected(self):
        # Any non-empty string is now accepted; core does not validate the
        # mode vocabulary (§1.5 boundary — frontends own mode semantics).
        with pytest.raises(ValueError, match="replay_mode"):
            CompileFacade.from_phase_result(_pr(output=None), replay_mode="")

    def test_arbitrary_replay_mode_is_accepted(self):
        # Non-Finland frontends must be able to use any non-empty mode string.
        facade = CompileFacade.from_phase_result(_pr(output=None), replay_mode="uk_snapshot")
        assert facade.replay_mode == "uk_snapshot"

    def test_verdict_profile_must_match_explicit_strict_profile_name(self):
        verdict = CompileVerdict(
            mode="strict",
            profile=FINLAND_INGESTION_V1.name,
            status="strict_clean",
        )
        with pytest.raises(ValueError, match="verdict.profile must match"):
            CompileFacade.from_phase_result(
                _pr(output=None),
                replay_mode="legal_pit",
                strict_profile_name="different_profile",
                verdict=verdict,
            )

    def test_duplicate_findings_are_rejected(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        with pytest.raises(ValueError, match="must not contain duplicate findings"):
            CompileFacade.from_phase_result(
                _pr(output=None, observations=[obs, obs]),
                replay_mode="legal_pit",
            )

    def test_observations_propagated_from_phase_result(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert tuple(f.kind for f in facade.finding_ledger if f.role == "observation") == (obs.kind,)

    def test_obligations_propagated_from_phase_result(self):
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert tuple(f.kind for f in facade.finding_ledger if f.role == "obligation") == (obl.kind,)

    def test_violations_propagated_from_phase_result(self):
        vio = Violation(
            kind="RUNTIME.VIOLATION",
            stage="apply",
            detail={"message": "boom"},
            source_statute="2024/1",
        )
        pr = _pr(output=None, violations=[vio])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert tuple(f.kind for f in facade.finding_ledger if f.role == "violation") == (vio.kind,)

    def test_temporal_events_are_retained_on_public_facade(self):
        pr = _pr(
            output=None,
            temporal_events=[
                TemporalEvent(
                    event_id="intent:1",
                    kind="commence",
                    scope=TemporalScope(target_statute="1991/1"),
                    effective="2024-01-01",
                    source=OperationSource(statute_id="2024/1", effective="2024-01-01"),
                )
            ],
        )
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert len(facade.bundle.temporal_events) == 1
        assert facade.bundle.temporal_events[0].kind == "commence"
        assert facade.bundle.temporal_events[0].source is not None
        assert facade.bundle.temporal_events[0].source.effective == "2024-01-01"

    def test_explicit_temporal_events_are_preserved(self):
        explicit = TemporalEvent(
            event_id="explicit:1",
            kind="expire",
            scope=TemporalScope(target_statute="1991/1"),
            source=OperationSource(statute_id="2025/1", expires="2025-12-31"),
        )
        pr = PhaseResult(
            output=None,
            temporal_events=(explicit,),
        )

        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert facade.bundle.temporal_events == (explicit,)

    def test_canonical_bundle_output_rejects_duplicate_temporal_events(self):
        explicit = TemporalEvent(
            event_id="explicit:1",
            kind="expire",
            scope=TemporalScope(target_statute="1991/1"),
        )
        pr = PhaseResult(
            output=CanonicalBundle(temporal_events=(explicit,)),
            temporal_events=(explicit,),
        )

        with pytest.raises(TypeError, match="canonical bundle owns temporal events"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_facade_exposes_bundle_temporal_and_migration_summaries(self):
        migration_event = MigrationEvent(
            event_id="mig:summary",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "1"),)),
            to_address=LegalAddress(path=(("section", "1a"),)),
            effective="2024-01-01",
        )
        activation_rule = ActivationRule(kind="fixed_date", effective_date="2024-01-01")
        temporal_event = TemporalEvent(
            event_id="temp:summary",
            kind="commence",
            scope=TemporalScope(target_statute="1991/1"),
            activation_rule=activation_rule,
            source=OperationSource(statute_id="2024/1", title="Summary source", enacted="2024-01-01"),
        )
        facade = CompileFacade(
            bundle=CanonicalBundle(
                migration_events=(migration_event,),
                temporal_events=(temporal_event,),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )

        assert facade.migration_event_kinds == ("renumber",)
        assert facade.temporal_event_kinds == ("commence",)
        assert facade.temporal_events_with_activation_rules == 1
        assert facade.temporal_events_with_source == 1
        assert facade.temporal_event_activation_rule_kinds == ("fixed_date",)

    def test_facade_compile_timelines_ex_preserves_timeline_issues(self):
        base = IRStatute(
            statute_id="test/facade-issues",
            title="Facade issue preservation",
            body=IRNode(kind=IRNodeKind.BODY, children=()),
        )
        target = LegalAddress(path=(("section", "9"),))
        op = LegalOperation(
            op_id="insert-missing-payload",
            sequence=1,
            action=StructuralAction.INSERT,
            target=target,
            payload=None,
            source=OperationSource(
                statute_id="2020/9",
                enacted="2020-01-01",
                effective="2020-01-01",
            ),
            group_id="g:facade-issues",
        )
        temporal_event = TemporalEvent(
            event_id="ev:facade-issues",
            group_id="g:facade-issues",
            kind="commence",
            effective="2020-01-01",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
            scope=TemporalScope(target_statute="test/facade-issues"),
        )
        facade = CompileFacade.from_phase_result(
            _pr(output=CanonicalBundle(structural_ops=(op,), temporal_events=(temporal_event,))),
            replay_mode="legal_pit",
        )

        result = facade.compile_timelines_ex(base, base_date="2000-01-01")

        assert any(issue.kind == "missing_insert_payload" for issue in result.issues)
        assert target not in result.timelines or len(result.timelines[target].versions) == 0

    def test_facade_compile_timelines_ex_preserves_unsupported_facet_target_issue(self):
        base = IRStatute(
            statute_id="test/facade-facet-issue",
            title="Facade facet issue preservation",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),),
            ),
        )
        target = LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING)
        facade = CompileFacade(
            bundle=CanonicalBundle(
                structural_ops=(
                    LegalOperation(
                        op_id="facet-op",
                        sequence=1,
                        action=StructuralAction.HEADING_REPLACE,
                        target=target,
                        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
                        source=OperationSource(statute_id="2024/1"),
                    ),
                ),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )

        result = facade.compile_timelines_ex(base, base_date="2000-01-01")

        assert any(issue.kind == "unsupported_facet_target" for issue in result.issues)

    def test_facade_compile_timelines_ex_preserves_unsupported_text_action_issue(self):
        base = IRStatute(
            statute_id="test/facade-text-issue",
            title="Facade text issue preservation",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),),
            ),
        )
        target = LegalAddress(path=(("section", "1"),))
        facade = CompileFacade(
            bundle=CanonicalBundle(
                structural_ops=(
                    LegalOperation(
                        op_id="text-repeal-op",
                        sequence=1,
                        action=StructuralAction.TEXT_REPEAL,
                        target=target,
                        group_id="g:text-repeal",
                        source=OperationSource(statute_id="2024/2"),
                    ),
                ),
                temporal_events=(
                    TemporalEvent(
                        event_id="temp:text-repeal",
                        kind="commence",
                        group_id="g:text-repeal",
                        scope=TemporalScope(target_statute="test/facade-text-issue"),
                        effective="2001-01-01",
                        activation_rule=ActivationRule(kind="fixed_date", effective_date="2001-01-01"),
                    ),
                ),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )

        result = facade.compile_timelines_ex(base, base_date="2000-01-01")

        assert any(issue.kind == "unsupported_text_action" for issue in result.issues)

    def test_facade_provision_lineage_uses_bundle_migration_events(self):
        old_addr = LegalAddress(path=(("section", "1"),))
        new_addr = LegalAddress(path=(("section", "1a"),))
        migration_event = MigrationEvent(
            event_id="mig:facade:1",
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
        facade = CompileFacade(
            bundle=CanonicalBundle(
                migration_events=(migration_event,),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )
        timelines = {new_addr: ProvisionTimeline(address=new_addr, versions=[version])}

        assert facade.provision_lineage(timelines, old_addr) == [version]

    def test_facade_provision_lineage_concatenates_migration_chain(self):
        old_addr = LegalAddress(path=(("section", "1"),))
        mid_addr = LegalAddress(path=(("section", "1a"),))
        new_addr = LegalAddress(path=(("section", "1aa"),))
        first = MigrationEvent(
            event_id="mig:facade:chain:1",
            kind="renumber",
            from_address=old_addr,
            to_address=mid_addr,
            effective="2020-01-01",
        )
        second = MigrationEvent(
            event_id="mig:facade:chain:2",
            kind="renumber",
            from_address=mid_addr,
            to_address=new_addr,
            effective="2021-01-01",
        )
        old_v = ProvisionVersion(
            effective="2019-01-01",
            enacted="2018-12-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="old"),
        )
        mid_v = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="mid"),
        )
        new_v = ProvisionVersion(
            effective="2021-01-01",
            enacted="2021-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="1aa", text="new"),
        )
        facade = CompileFacade(
            bundle=CanonicalBundle(
                migration_events=(first, second),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )
        timelines = {
            old_addr: ProvisionTimeline(address=old_addr, versions=[old_v]),
            mid_addr: ProvisionTimeline(address=mid_addr, versions=[mid_v]),
            new_addr: ProvisionTimeline(address=new_addr, versions=[new_v]),
        }

        assert facade.provision_lineage(timelines, old_addr) == [old_v, mid_v, new_v]

    def test_bundle_migration_events_are_canonicalized_on_ingest(self) -> None:
        first = MigrationEvent(
            event_id="mig:facade:late",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "2"),)),
            to_address=LegalAddress(path=(("section", "2a"),)),
            effective="2021-01-01",
            source_statute="2021/1",
        )
        second = MigrationEvent(
            event_id="mig:facade:early",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "1"),)),
            to_address=LegalAddress(path=(("section", "1a"),)),
            effective="2020-01-01",
            source_statute="2020/1",
        )
        facade = CompileFacade.from_phase_result(
            _pr(
                output=CanonicalBundle(migration_events=(first, second)),
            ),
            replay_mode="legal_pit",
        )

        assert facade.bundle.migration_events == (second, first)

    def test_output_none_gives_empty_canonical_ops(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.bundle.structural_ops == ()

    def test_output_iterables_of_non_ops_are_rejected_by_canonical_constructor(self):
        class ResolvedOp:
            pass

        sentinel_a = ResolvedOp()
        pr = _pr(output=[sentinel_a])
        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_output_legal_operations_are_rejected_without_bundle(self):
        op = LegalOperation(
            op_id="legal-op-1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
        pr = _pr(output=[op])

        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_output_canonical_bundle_is_accepted(self):
        op = LegalOperation(
            op_id="legal-op-2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Updated"),
            source=OperationSource(
                statute_id="2010/101",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
        temporal_event = TemporalEvent(
            event_id="bundle:1",
            kind="commence",
            scope=TemporalScope(target_statute="1991/1"),
            effective="2010-01-01",
            source=OperationSource(statute_id="2010/101", effective="2010-01-01"),
        )
        pr = _pr(output=CanonicalBundle(structural_ops=(op,), temporal_events=(temporal_event,)))

        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert facade.bundle.structural_ops == (op,)
        assert facade.bundle.temporal_events == (temporal_event,)

    def test_output_canonical_bundle_rejects_phase_migration_events(self):
        op = LegalOperation(
            op_id="legal-op-phase-mig",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Updated"),
            source=OperationSource(
                statute_id="2010/102",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
        migration_event = MigrationEvent(
            event_id="phase:mig:1",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "2"),)),
            to_address=LegalAddress(path=(("section", "2a"),)),
            effective="2010-01-01",
            source_statute="2010/102",
        )
        pr = _pr(
            output=CanonicalBundle(structural_ops=(op,)),
            migration_events=(migration_event,),
        )

        with pytest.raises(TypeError, match="migration_events to be empty"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_output_canonical_bundle_preserves_explicit_migration_events(self):
        op = LegalOperation(
            op_id="renumber-phase",
            sequence=4,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            destination=LegalAddress(path=(("section", "2"),)),
            source=OperationSource(
                statute_id="2021/204",
                enacted="2021-04-01",
                effective="2021-04-01",
            ),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="old"),
        )
        destination = LegalAddress(path=(("section", "2"),))
        migration_event = MigrationEvent(
            event_id="phase:mig:1",
            kind="renumber",
            from_address=op.target,
            to_address=destination,
            effective="2021-04-01",
            source_statute="2021/204",
        )
        pr = _pr(output=CanonicalBundle(structural_ops=(op,), migration_events=(migration_event,)))

        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert len(facade.bundle.migration_events) == 1
        emitted = facade.bundle.migration_events[0]
        assert emitted.kind == "renumber"
        assert emitted.event_id == "phase:mig:1"
        assert emitted.from_address == op.target
        assert emitted.to_address == op.destination
        assert emitted.effective == "2021-04-01"
        assert emitted.source_statute == "2021/204"

    def test_output_canonical_bundle_preserves_all_fields(self):
        """CanonicalBundle passed as pr.output must be stored intact on the facade.

        This guards against data loss where only structural_ops and
        temporal_events are extracted but source_statute, target_statute,
        migration_events, effects, groups, and source are silently dropped.
        """
        op = LegalOperation(
            op_id="legal-op-full",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            source=OperationSource(
                statute_id="2020/200",
                enacted="2020-01-01",
                effective="2020-06-01",
            ),
        )
        temporal_event = TemporalEvent(
            event_id="full:1",
            kind="commence",
            scope=TemporalScope(target_statute="1990/50"),
            effective="2020-06-01",
            source=OperationSource(statute_id="2020/200", effective="2020-06-01"),
        )
        migration_event = MigrationEvent(
            event_id="mig:1",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "1"),)),
            to_address=LegalAddress(path=(("section", "1a"),)),
            effective="2020-06-01",
            source_statute="2020/200",
        )
        effect = CanonicalEffect(
            effect_id="eff:1",
            family="structural",
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            group_id="grp:1",
        )
        group = EffectGroup(
            group_id="grp:1",
            source_statute="2020/200",
        )
        op_source = OperationSource(
            statute_id="2020/200",
            enacted="2020-01-01",
            effective="2020-06-01",
        )
        bundle = CanonicalBundle(
            source_statute="2020/200",
            target_statute="1990/50",
            structural_ops=(op,),
            temporal_events=(temporal_event,),
            migration_events=(migration_event,),
            effects=(effect,),
            groups=(group,),
            source=op_source,
        )
        pr = _pr(output=bundle)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        # Core ops and temporal events
        assert facade.bundle.structural_ops == (op,)
        assert facade.bundle.temporal_events == (temporal_event,)
        # Fields that were previously lost
        assert facade.bundle.source_statute == "2020/200"
        assert facade.bundle.target_statute == "1990/50"
        assert facade.bundle.migration_events == (migration_event,)
        assert facade.bundle.effects == (effect,)
        assert facade.bundle.groups == (group,)
        assert facade.bundle.source is op_source
        # The bundle on the facade should be the exact same object
        assert facade.bundle is bundle

    def test_findings_are_stored_without_wrapper_reprojection(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs], obligations=[obl])

        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert tuple(f.role for f in facade.finding_ledger) == ("obligation", "observation")

    def test_output_none_preserves_migration_events(self):
        migration_event = MigrationEvent(
            event_id="mig:phase-result",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "1"),)),
            to_address=LegalAddress(path=(("section", "1a"),)),
            effective="2024-01-01",
        )
        pr = PhaseResult(output=None, migration_events=(migration_event,))

        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert facade.bundle.migration_events == (migration_event,)

    def test_output_non_iterable_raises_type_error(self):
        # A scalar output is an invalid facade boundary shape and must fail loudly.
        pr = _pr(output=42)
        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_output_dict_raises_type_error(self):
        pr = _pr(output={"not": "ops"})
        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_compile_timelines_ex_uses_bundle_temporal_events(self):
        base = IRStatute(
            statute_id="test/facade-timeline",
            title="Facade timeline test",
            body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        )
        target = LegalAddress(path=(("section", "1"),))
        op = LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=target,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:facade",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
        event = TemporalEvent(
            event_id="commence:facade",
            group_id="g:facade",
            kind="commence",
            scope=TemporalScope(target_statute="test/facade-timeline"),
            effective="2010-01-01",
            source=OperationSource(statute_id="2010/101", effective="2010-01-01"),
        )
        facade = CompileFacade(
            bundle=CanonicalBundle(
                structural_ops=(op,),
                temporal_events=(event,),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )

        result = facade.compile_timelines_ex(base, base_date="2000-01-01")
        timelines = result.timelines
        assert result.issues == ()

        active_2007 = select_active_version(timelines[target], "2007-01-01")
        assert active_2007 is not None
        assert active_2007.content is not None
        assert active_2007.content.text == "Base"

        active_2011 = select_active_version(timelines[target], "2011-01-01")
        assert active_2011 is not None
        assert active_2011.content is not None
        assert active_2011.content.text == "Updated"
        assert active_2011.effective == "2010-01-01"

    def test_materialize_pit_ex_uses_bundle_temporal_applicability(self):
        base = IRStatute(
            statute_id="test/facade-pit",
            title="Facade PIT test",
            body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        )
        target = LegalAddress(path=(("section", "1"),))
        op = LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=target,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped"),
            group_id="g:pit",
            source=OperationSource(
                statute_id="2010/101",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
        event = TemporalEvent(
            event_id="scope:pit",
            group_id="g:pit",
            kind="commence",
            effective="2010-01-01",
            source=OperationSource(statute_id="2010/101", effective="2010-01-01"),
            scope=TemporalScope(target_statute="test/facade-pit"),
        )
        applicability_event = TemporalEvent(
            event_id="scope:pit:applicability",
            group_id="g:pit",
            kind="set_applicability",
            scope=TemporalScope(
                target_statute="test/facade-pit",
                predicates=(ScopePredicate(dimension="territory", includes=frozenset({"AX"})),),
            ),
        )
        facade = CompileFacade(
            bundle=CanonicalBundle(
                structural_ops=(op,),
                temporal_events=(event, applicability_event),
            ),
            finding_ledger=(),
            replay_mode="legal_pit",
        )

        degraded = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
        assert degraded.status == "degraded_missing_scope"
        assert degraded.required_dimensions == ("territory",)

        selected = facade.materialize_pit_ex(
            base,
            "2011-01-01",
            base_date="2000-01-01",
            territory="AX",
        )
        assert selected.status == "materialized"

    def test_materialize_pit_ex_preserves_timeline_issues_from_facade_compile(self):
        base = IRStatute(
            statute_id="test/facade-materialize-issues",
            title="Facade materialization issue preservation",
            body=IRNode(kind=IRNodeKind.BODY, children=()),
        )
        target = LegalAddress(path=(("section", "9"),))
        op = LegalOperation(
            op_id="insert-missing-payload",
            sequence=1,
            action=StructuralAction.INSERT,
            target=target,
            payload=None,
            source=OperationSource(
                statute_id="2020/9",
                enacted="2020-01-01",
                effective="2020-01-01",
            ),
            group_id="g:facade-materialize-issues",
        )
        temporal_event = TemporalEvent(
            event_id="ev:facade-materialize-issues",
            group_id="g:facade-materialize-issues",
            kind="commence",
            effective="2020-01-01",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
            scope=TemporalScope(target_statute="test/facade-materialize-issues"),
        )
        facade = CompileFacade.from_phase_result(
            _pr(output=CanonicalBundle(structural_ops=(op,), temporal_events=(temporal_event,))),
            replay_mode="legal_pit",
        )

        result = facade.materialize_pit_ex(base, "2021-01-01", base_date="2000-01-01")

        assert any(issue.kind == "missing_insert_payload" for issue in result.issues)
        assert result.statute.body.children == ()

    def test_materialize_pit_ex_preserves_missing_replace_payload_issue_from_facade_compile(self):
        base = IRStatute(
            statute_id="test/facade-replace-issues",
            title="Facade replace issue preservation",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),),
            ),
        )
        target = LegalAddress(path=(("section", "1"),))
        op = LegalOperation(
            op_id="replace-missing-payload",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=target,
            payload=None,
            source=OperationSource(
                statute_id="2020/9",
                enacted="2020-01-01",
                effective="2020-01-01",
            ),
            group_id="g:facade-replace-issues",
        )
        temporal_event = TemporalEvent(
            event_id="ev:facade-replace-issues",
            group_id="g:facade-replace-issues",
            kind="commence",
            effective="2020-01-01",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
            scope=TemporalScope(target_statute="test/facade-replace-issues"),
        )
        facade = CompileFacade.from_phase_result(
            _pr(output=CanonicalBundle(structural_ops=(op,), temporal_events=(temporal_event,))),
            replay_mode="legal_pit",
        )

        result = facade.materialize_pit_ex(base, "2021-01-01", base_date="2000-01-01")

        assert any(issue.kind == "missing_replace_payload" for issue in result.issues)
        assert result.statute.body.children[0].text == "Base text"

    def test_output_string_raises_type_error(self):
        pr = _pr(output="not ops")
        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_output_iterable_with_non_resolvedop_items_raises_type_error(self):
        pr = _pr(output=["not", "ops"])
        with pytest.raises(TypeError, match="CanonicalBundle or None"):
            CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

    def test_facade_is_frozen(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        with pytest.raises((AttributeError, TypeError)):
            facade.replay_mode = "other"  # type: ignore[misc]  # ty:ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# has_blocking
# ---------------------------------------------------------------------------

class TestHasBlocking:
    def test_no_obligations_is_not_blocking(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is False

    def test_blocking_obligation_sets_has_blocking(self):
        obl = _obl("APPLY.STRICT_REJECTED_UNCOVERED_BODY", blocking=True)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is True

    def test_non_blocking_obligation_does_not_set_has_blocking(self):
        obl = _obl("ELAB.SPARSE_PAYLOAD_LEFTOVER", blocking=False)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is False

    def test_mixed_blocking_and_non_blocking_is_blocking(self):
        obl_a = _obl("ELAB.SPARSE_PAYLOAD_LEFTOVER", blocking=False)
        obl_b = _obl("APPLY.STRICT_REJECTED_UNCOVERED_BODY", blocking=True)
        pr = _pr(output=None, obligations=[obl_a, obl_b])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is True


# ---------------------------------------------------------------------------
# strictness
# ---------------------------------------------------------------------------

class TestStrictness:
    def test_no_blocking_obligations_does_not_set_has_blocking(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is False

    def test_blocking_obligation_sets_has_blocking(self):
        obl = _obl("APPLY.STRICT_REJECTED_UNCOVERED_BODY", blocking=True)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is True

    def test_non_blocking_obligation_does_not_set_has_blocking(self):
        obl = _obl("ELAB.SPARSE_PAYLOAD_LEFTOVER", blocking=False)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is False

    def test_has_blocking_tracks_verdict_conflicts(self):
        obl = _obl("APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH", blocking=True)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert facade.has_blocking is True

    def test_verdict_conflict_with_blocking_obligation_uses_ledger(self):
        # When verdict says strict_clean but ledger has blocking findings,
        # has_blocking() uses the ledger as ground truth (not the verdict).
        verdict = CompileVerdict(
            mode="strict",
            profile=FINLAND_INGESTION_V1.name,
            status="strict_clean",
        )
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY", blocking=True)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="legal_pit",
            verdict=verdict,
        )
        # Ledger is ground truth: blocking obligation means strict=False
        assert facade.has_blocking is True


# ---------------------------------------------------------------------------
# quirks_used
# ---------------------------------------------------------------------------

class TestQuirksUsed:
    def test_no_observations_returns_empty(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert quirks_used_from_findings(facade.finding_ledger) == ()

    def test_quirks_obs_kind_is_returned(self):
        obl = _obl("APPLY.LEGACY_DISPATCH_FALLBACK")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        result = quirks_used_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in result) == (obl.kind,)

    def test_non_quirks_obs_kind_is_excluded(self):
        obs = _obs("PARSE.DUPLICATE_TARGET_OP")
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert quirks_used_from_findings(facade.finding_ledger) == ()

    def test_mixed_observations_filters_correctly(self):
        obs_quirks = _obs("ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE")
        obs_other = _obs("LOWER.CONTEXT_DEPENDENT_ANCHOR")
        pr = _pr(output=None, observations=[obs_quirks, obs_other])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        result = quirks_used_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in result) == (obs_quirks.kind,)

    def test_all_registered_quirks_kinds_are_detected(self):
        """Smoke test that the _QUIRKS_OBS_KINDS set is reachable and non-empty."""
        assert len(_QUIRKS_OBS_KINDS) > 0
        for kind in _QUIRKS_OBS_KINDS:
            spec = get_finding_spec(kind)
            assert spec is not None
            if spec.role == "observation":
                pr = _pr(output=None, observations=[_obs(kind)])
            elif spec.role == "obligation":
                pr = _pr(output=None, obligations=[_obl(kind)])
            else:
                pr = _pr(
                    output=None,
                    violations=[
                        Violation(
                            kind=kind,
                            stage="test_stage",
                            detail={},
                        )
                    ],
                )
            facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
            assert kind in {
                finding.kind for finding in quirks_used_from_findings(facade.finding_ledger)
            }, (
                f"quirks_used() should detect kind={kind!r}"
            )


# ---------------------------------------------------------------------------
# source_completeness_issues
# ---------------------------------------------------------------------------

class TestSourceCompletenessIssues:
    def test_empty_returns_empty_tuple(self):
        pr = _pr(output=None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert source_completeness_issues_from_findings(facade.finding_ledger) == ()

    def test_source_pathology_obs_is_returned(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (obs.kind,)

    def test_missing_payload_surface_obs_is_returned(self):
        obs = _obs("ELAB.MISSING_PAYLOAD_SURFACE")
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (obs.kind,)

    def test_source_corrected_by_patch_obl_is_returned(self):
        obl = _obl("APPLY.SOURCE_CORRECTED_BY_PATCH")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (obl.kind,)

    def test_strict_rejected_source_pathology_obl_is_returned(self):
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (obl.kind,)

    def test_apply_source_pathology_wrapped_violation_is_returned(self):
        vio = Violation(
            kind="RUNTIME.VIOLATION",
            stage="replay",
            detail={"barrier_code": "APPLY.SOURCE_PATHOLOGY_DETECTED"},
            source_statute="2024/1",
        )
        pr = _pr(output=None, violations=[vio])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (vio.kind,)

    def test_apply_source_incomplete_obl_is_returned(self):
        obl = _obl("APPLY.SOURCE_INCOMPLETE")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert tuple(f.kind for f in issues) == (obl.kind,)

    def test_unrelated_obs_not_returned(self):
        obs = _obs("PARSE.DUPLICATE_TARGET_OP")
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        assert source_completeness_issues_from_findings(facade.finding_ledger) == ()

    def test_unrelated_obl_not_returned(self):
        obl = _obl("APPLY.STRICT_REJECTED_UNCOVERED_BODY")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert obl.kind not in {finding.kind for finding in issues}

    def test_mixed_obs_and_obl_returns_both_when_relevant(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs], obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
        issues = source_completeness_issues_from_findings(facade.finding_ledger)
        assert {finding.kind for finding in issues} == {obs.kind, obl.kind}

    def test_all_registered_source_completeness_obs_kinds_detected(self):
        """Smoke test for _SOURCE_COMPLETENESS_OBS_KINDS coverage."""
        assert len(_SOURCE_COMPLETENESS_OBS_KINDS) > 0
        for kind in _SOURCE_COMPLETENESS_OBS_KINDS:
            obs = _obs(kind)
            pr = _pr(output=None, observations=[obs])
            facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
            issues = source_completeness_issues_from_findings(facade.finding_ledger)
            assert kind in {finding.kind for finding in issues}, (
                f"source_completeness_issues() should detect obs kind={kind!r}"
            )

    def test_all_registered_source_completeness_obl_kinds_detected(self):
        """Smoke test for _SOURCE_COMPLETENESS_OBL_KINDS coverage."""
        assert len(_SOURCE_COMPLETENESS_OBL_KINDS) > 0
        for kind in _SOURCE_COMPLETENESS_OBL_KINDS:
            spec = get_finding_spec(kind)
            assert spec is not None
            if spec.role == "barrier":
                vio = Violation(
                    kind="RUNTIME.VIOLATION",
                    stage="test_stage",
                    detail={"barrier_code": kind},
                    source_statute="2024/1",
                )
                pr = _pr(output=None, violations=[vio])
            else:
                obl = _obl(kind)
                pr = _pr(output=None, obligations=[obl])
            facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")
            issues = source_completeness_issues_from_findings(facade.finding_ledger)
            issue_codes = {
                str(finding.detail.get("barrier_code") or finding.kind)
                for finding in issues
            }
            assert kind in issue_codes, (
                f"source_completeness_issues() should detect obl kind={kind!r}"
            )


class TestFindingProjection:
    def test_findings_projects_observations_and_obligations(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs], obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        findings = facade.finding_ledger

        assert [finding.role for finding in findings] == ["obligation", "observation"]
        assert findings[0].kind == obl.kind
        assert findings[1].kind == obs.kind
        assert findings[0].blocking is True

    def test_strict_fail_reasons_projects_from_blocking_obligations(self):
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        assert tuple(facade.to_wire_artifact().status.blockers or ()) == (
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        )

    def test_strict_fail_reasons_prefers_verdict_barriers(self):
        verdict = CompileVerdict(
            mode="strict",
            profile=FINLAND_INGESTION_V1.name,
            status="strict_blocked_by_recovery",
            barrier_codes=(),
        )
        pr = _pr(output=None, obligations=[_obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")])
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="legal_pit",
            verdict=verdict,
        )

        assert tuple(facade.to_wire_artifact().status.blockers or ()) == ()

    def test_strict_fail_reasons_includes_violations_even_with_verdict(self):
        verdict = CompileVerdict(
            mode="strict",
            profile=FINLAND_INGESTION_V1.name,
            status="strict_blocked_by_recovery",
            barrier_codes=(),
        )
        vio = Violation(
            kind="RUNTIME.VIOLATION",
            stage="apply",
            detail={"message": "boom"},
            source_statute="2024/1",
        )
        pr = _pr(output=None, violations=[vio])
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="legal_pit",
            verdict=verdict,
        )

        assert tuple(facade.to_wire_artifact().status.blockers or ()) == (
            "RUNTIME.VIOLATION",
        )

    def test_projection_rows_projects_signal_rows(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY")
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        pr = _pr(output=None, observations=[obs], obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        rows = _projection_rows(facade)

        assert [row["kind"] for row in rows] == [
            "ELAB.SOURCE_PATHOLOGY",
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        ]
        assert rows[0]["blocking"] is False
        assert rows[1]["blocking"] is True
        assert rows[0]["role"] == "observation"
        assert rows[1]["role"] == "obligation"

    def test_projection_rows_are_canonically_sorted(self):
        obs = _obs("PARSE.DUPLICATE_TARGET_OP", stage="z")
        obl = _obl("APPLY.STRICT_REJECTED_UNCOVERED_BODY", stage="a")
        pr = _pr(output=None, observations=[obs], obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        rows = _projection_rows(facade)

        assert [row["kind"] for row in rows] == [
            "PARSE.DUPLICATE_TARGET_OP",
            "APPLY.STRICT_REJECTED_UNCOVERED_BODY",
        ]
        assert [row["role"] for row in rows] == ["observation", "obligation"]

    def test_projection_rows_prefer_detail_source_statute(self):
        obs = Observation(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="elab",
            detail={"source_statute": "2020/100", "message": "detail wins"},
            source_statute="1999/1",
        )
        pr = _pr(output=None, observations=[obs])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        rows = _projection_rows(facade)

        assert rows[0]["source"] == "2020/100"
        assert rows[0]["message"] == "detail wins"


class TestOptionalDossierFields:
    def test_source_pathology_codes_project_from_findings(self):
        facade = CompileFacade.from_phase_result(
            _pr(
                output=None,
                observations=[
                    Observation(
                        kind="ELAB.SOURCE_PATHOLOGY",
                        stage="test_stage",
                        detail={
                            "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                            "target_unit_kind": "section",
                        },
                    )
                ],
            ),
            replay_mode="legal_pit",
        )
        assert tuple(
            row["code"] for row in _source_pathology_rows(facade) if row["code"]
        ) == ("DESTRUCTIVE_SHAPE_LOSS_RISK",)

    def test_source_pathology_diagnostic_reasons_project_from_findings(self):
        facade = CompileFacade.from_phase_result(
            _pr(
                output=None,
                observations=[
                    Observation(
                        kind="ELAB.SOURCE_PATHOLOGY",
                        stage="test_stage",
                        detail={
                            "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                            "target_unit_kind": "section",
                            "detail": {"diagnostic_reason": "live_body_dominates_amend_body"},
                        },
                    )
                ],
            ),
            replay_mode="legal_pit",
        )
        assert tuple(
            cast(dict[str, Any], row["detail"]).get("diagnostic_reason")
            for row in _source_pathology_rows(facade)
            if cast(dict[str, Any], row["detail"]).get("diagnostic_reason")
        ) == ("live_body_dominates_amend_body",)

    def test_summary_projection_contains_projected_counts_and_codes(self):
        obs = Observation(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="test_stage",
            detail={
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "target_unit_kind": "section",
                "detail": {"diagnostic_reason": "partial_body_only"},
            },
        )
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY")
        vio = Violation(
            kind="RUNTIME.VIOLATION",
            stage="apply",
            detail={"message": "boom"},
            source_statute="2024/1",
        )
        pr = _pr(output=None, observations=[obs], obligations=[obl], violations=[vio])
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="legal_pit",
        )

        assert len(facade.finding_ledger) == 3
        assert tuple(facade.to_wire_artifact().status.blockers or ()) == (
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            "RUNTIME.VIOLATION",
        )
        assert tuple(row["code"] for row in _source_pathology_rows(facade) if row["code"]) == (
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
        )
        assert tuple(
            cast(dict[str, Any], row["detail"])["diagnostic_reason"]
            for row in _source_pathology_rows(facade)
            if cast(dict[str, Any], row["detail"]).get("diagnostic_reason")
        ) == ("partial_body_only",)
        assert [
            row["kind"] for row in _projection_rows(facade) if row["role"] == "violation"
        ] == ["RUNTIME.VIOLATION"]


class TestWireArtifact:
    def test_wire_projection_is_sorted_and_summary_shaped(self):
        obs = _obs("ELAB.SOURCE_PATHOLOGY", stage="b")
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY", stage="a", blocking=True)
        vio = Violation(
            kind="RUNTIME.VIOLATION",
            stage="apply",
            detail={"message": "boom"},
            source_statute="2024/1",
        )
        pr = _pr(
            output=None,
            observations=[obs],
            obligations=[obl],
            violations=[vio],
            migration_events=[
                MigrationEvent(
                    event_id="migration:1",
                    kind="renumber",
                    from_address=LegalAddress(path=(("section", "1"),)),
                    to_address=LegalAddress(path=(("section", "2"),)),
                )
            ],
            temporal_events=[
                TemporalEvent(
                    event_id="wire:1",
                    kind="commence",
                    scope=TemporalScope(target_statute="1991/1"),
                    activation_rule=ActivationRule(kind="fixed_date", effective_date="2024-01-01"),
                    source=OperationSource(statute_id="2024/1", title="Wire source", enacted="2024-01-01"),
                )
            ],
        )
        facade = CompileFacade.from_phase_result(
            pr,
            replay_mode="legal_pit",
            strict_profile_name="finland_ingestion_v1",
        )
        artifact = facade.to_wire_artifact()

        payload = cast(Any, artifact.payload)

        assert payload["replay_mode"] == "legal_pit"
        assert payload["strict_profile_name"] == "finland_ingestion_v1"
        assert "strict_pass" not in payload
        assert payload["bundle"]["structural_ops_count"] == 0
        assert payload["bundle"]["temporal_events_count"] == 1
        assert payload["bundle"]["temporal_event_kinds"] == ("commence",)
        assert payload["bundle"]["temporal_events_with_activation_rules"] == 1
        assert payload["bundle"]["temporal_events_with_source"] == 1
        assert payload["bundle"]["temporal_event_activation_rule_kinds"] == ("fixed_date",)
        assert payload["bundle"]["migration_events_count"] == 1
        assert payload["bundle"]["migration_event_kinds"] == ("renumber",)
        assert tuple(artifact.status.blockers or ()) == (
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            "RUNTIME.VIOLATION",
        )
        assert tuple(item["role"] for item in payload["findings"]) == (
            "obligation",
            "observation",
            "violation",
        )

    def test_to_wire_artifact_wraps_projection_with_versioned_status(self):
        obl = _obl("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY", blocking=True)
        pr = _pr(output=None, obligations=[obl])
        facade = CompileFacade.from_phase_result(pr, replay_mode="legal_pit")

        artifact = facade.to_wire_artifact(
            producer="tests.compile_facade",
            version="wire-1",
        )

        assert isinstance(artifact, ArtifactEnvelope)
        assert artifact.schema == "lawvm.compile_facade"
        assert artifact.producer == "tests.compile_facade"
        assert artifact.version == "wire-1"
        assert artifact.status == ProcessingStatus(
            kind="partial",
            blockers=("ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",),
        )
        payload = cast(Any, artifact.payload)

    def test_wire_projection_normalizes_non_jsonable_detail_values(self):
        obs = Observation(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="test_stage",
            detail={"bad": {1, 2}},
        )
        facade = CompileFacade.from_phase_result(
            _pr(output=None, observations=[obs]),
            replay_mode="legal_pit",
        )

        artifact = facade.to_wire_artifact()
        payload = cast(Any, artifact.payload)

        assert sorted(payload["findings"][0]["detail"]["bad"]) == [1, 2]


class TestFacadeSummaryPrinting:
    def test_print_facade_summary_includes_source_pathology_reasons(self, capsys):
        try:
            from lawvm.tools.explain import _print_facade_summary
        except ImportError as exc:
            pytest.skip(f"stale explain imports outside compile_facade lane: {exc}")

        facade = CompileFacade.from_phase_result(
            _pr(
                output=None,
                observations=[
                    Observation(
                        kind="ELAB.SOURCE_PATHOLOGY",
                        stage="test_stage",
                        detail={
                            "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                            "target_unit_kind": "section",
                            "detail": {"diagnostic_reason": "live_body_dominates_amend_body"},
                        },
                    )
                ],
            ),
            replay_mode="legal_pit",
        )

        _print_facade_summary(facade)

        out = capsys.readouterr().out
        assert "Pathologies  : PARTIAL_WHOLE_SECTION_PAYLOAD" in out
        assert "Pathology reasons : live_body_dominates_amend_body" in out

    def test_print_facade_summary_accepts_explicit_html_noncomm_reason(self, capsys):
        try:
            from lawvm.tools.explain import _print_facade_summary
        except ImportError as exc:
            pytest.skip(f"stale explain imports outside compile_facade lane: {exc}")

        facade = CompileFacade.from_phase_result(
            _pr(output=None),
            replay_mode="finlex_oracle",
        )

        _print_facade_summary(
            facade,
            html_noncommensurable_reason="oracle_extra_scoped_labels:chapter:15/section:1",
        )

        out = capsys.readouterr().out
        assert "HTML/XML reason : oracle_extra_scoped_labels:chapter:15/section:1" in out


def test_canonical_effect_rejects_family_action_mismatch() -> None:
    with pytest.raises(TypeError, match="family='text' requires action='text_patch'"):
        CanonicalEffect(
            effect_id="eff:mismatch",
            family="text",
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        )
