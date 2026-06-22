from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.ir import IRNode, IRNodeKind, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    SourceInstrumentRef,
    SourceProvisionRef,
    lower_lifecycle_event_to_temporal_event,
)
from lawvm.core.compile_result import StrictProfile, strict_fail_reasons_from_finding_ledger
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.finland.effect_lifecycle_signals import (
    EffectLifecycleOverride,
    EffectLifecycleOverrideScope,
    EffectRelationSignal,
)
from lawvm.finland import effect_lifecycle_projection as elp
from lawvm.finland.effect_lifecycle_projection import (
    _lifecycle_events_from_resolved_signal_relations,
    build_finland_effect_lifecycle,
)
from lawvm.finland import process_frontend_normalization as pfn
from lawvm.finland.process_frontend_normalization import ProcessFrontendNormalizationContext
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.process_compile_signals import ProcessCompileSignalsContext
from lawvm.finland.process_result_builder import ProcessCompatSinks, ProcessResultBuilder, ProcessSignalBuffers


def _op() -> LegalOperation:
    return LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "4 a"),)),
        source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
        group_id="g:2020/1:op-1",
    )


def test_finland_temporal_event_projects_to_effect_lifecycle_event() -> None:
    temporal = TemporalEvent(
        event_id="fi-temporary:2020/1:op-1:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="1990/1",
            exact_addresses=(LegalAddress(path=(("section", "4 a"),)),),
        ),
        expires="2021-01-01",
        source=OperationSource(
            statute_id="2020/1",
            title="Temporary amendment",
            effective="2020-01-01",
            expires="2021-01-01",
            raw_text="väliaikaisesti",
        ),
        group_id="2020/1",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(_op(),),
        temporal_events=(temporal,),
    )

    assert {effect.source_instrument.instrument_id for effect in source_effects} == {"2020/1"}
    assert relations == ()
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "expire_effect"
    assert lifecycle.effect is not None
    assert lifecycle.effect.source_instrument.instrument_id == "2020/1"
    assert lifecycle.effect.target_statute == "1990/1"
    assert lifecycle.temporal_event is temporal


def test_temporal_event_reuses_matching_operation_effect_identity() -> None:
    op = _op()
    temporal = TemporalEvent(
        event_id="fi-temporary:2020/1:op-1:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="1990/1",
            exact_addresses=(op.target,),
        ),
        expires="2021-01-01",
        source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
        group_id=op.group_id,
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(op,),
        temporal_events=(temporal,),
        relation_signals=(
            EffectRelationSignal.pending_amendment(
                source_statute="2021/2",
                target_statute="2020/1",
                target_title="Target amendment",
                base_parent_id="1990/1",
                source_finding="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(source_effects) == 1
    assert source_effects[0].effect_id == "fi-effect:2020/1:op-1"
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].effect == source_effects[0]
    assert len(relations) == 1
    assert relations[0].target_effect == source_effects[0]
    assert "target_effect_resolution" not in relations[0].detail


def test_finland_canonical_op_mints_source_effect_identity() -> None:
    op = _op()

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(op,),
        temporal_events=(),
    )

    assert relations == ()
    assert lifecycle_events == ()
    assert len(source_effects) == 1
    effect = source_effects[0]
    assert effect.effect_id == "fi-effect:2020/1:op-1"
    assert effect.projection_group_id == "g:2020/1:op-1"
    assert effect.target_statute == "1990/1"
    assert effect.target_address == op.target
    assert effect.source_provision is not None
    assert effect.source_provision.rule_id == "fi.legal_operation.effect_declaration"


def test_finland_repeal_op_mints_repeal_source_effect_identity() -> None:
    op = LegalOperation(
        op_id="repeal-section-15",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "2"), ("section", "15"))),
        source=OperationSource(statute_id="2024/1100", effective="2025-01-01"),
        group_id="finland-johto:2024/1100",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2023/681",
        canonical_ops=(op,),
        temporal_events=(),
    )

    assert relations == ()
    assert lifecycle_events == ()
    assert len(source_effects) == 1
    witness = source_effects[0].source_provision
    assert witness is not None
    assert witness.rule_id == "fi.legal_operation.repeal_effect_declaration"


def test_known_source_effect_context_rejects_conflicting_duplicate_ids() -> None:
    op = _op()
    conflicting_known_effect = EffectRef(
        effect_id="fi-effect:2020/1:op-1",
        source_instrument=SourceInstrumentRef(instrument_id="2020/1"),
        target_statute="not-the-parent",
        target_address=op.target,
    )

    with pytest.raises(ValueError, match="conflicting duplicate effect_id"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(op,),
            temporal_events=(),
            known_source_effects=(conflicting_known_effect,),
        )


def test_temporal_projection_reuses_known_source_effect_identity_without_reminting() -> None:
    target = LegalAddress(path=(("section", "4 a"),))
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(
        instrument=instrument,
        path=("phase", "effect"),
        rule_id="test.phase_source_effect",
    )
    known_effect = EffectRef(
        effect_id="phase-effect:2020/1:custom",
        source_instrument=instrument,
        target_statute="1990/1",
        target_address=target,
        projection_group_id="g:phase-custom",
        source_provision=witness,
    )
    temporal = TemporalEvent(
        event_id="fi-temporal:2020/1:custom:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="1990/1",
            exact_addresses=(target,),
        ),
        expires="2022-01-01",
        source=OperationSource(statute_id="2020/1"),
        group_id="g:phase-custom",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(temporal,),
        known_source_effects=(known_effect,),
    )

    assert source_effects == ()
    assert relations == ()
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].effect == known_effect
    assert lifecycle_events[0].temporal_event is temporal


def test_duplicate_operation_ids_use_full_effect_discriminator() -> None:
    source = OperationSource(statute_id="2020/1")
    first = LegalOperation(
        op_id="snapshot_section_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
        source=source,
    )
    second = LegalOperation(
        op_id="snapshot_section_1",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "2"), ("section", "1"))),
        source=source,
    )

    source_effects, _relations, _lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(first, second),
        temporal_events=(),
    )

    assert tuple(effect.effect_id for effect in source_effects) == (
        "fi-effect:2020/1:snapshot_section_1:seq-1:target-chapter:1/section:1",
        "fi-effect:2020/1:snapshot_section_1:seq-2:target-chapter:2/section:1",
    )
    assert tuple(effect.target_address for effect in source_effects) == (first.target, second.target)


def test_colliding_operation_effect_ids_use_occurrence_discriminator() -> None:
    source = OperationSource(statute_id="1994/1486")
    target = LegalAddress(path=(("part", "1"), ("chapter", "6"), ("section", "70"), ("subsection", "1")))
    first = LegalOperation(
        op_id="snapshot_subsection_1_from_section_70",
        sequence=0,
        action=StructuralAction.INSERT,
        target=target,
        source=source,
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Myynniksi ulkomaille katsotaan:"),
    )
    second = LegalOperation(
        op_id="snapshot_subsection_1_from_section_70",
        sequence=0,
        action=StructuralAction.INSERT,
        target=target,
        source=source,
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Veroa ei suoriteta seuraavista myynneistä:"),
    )

    source_effects, _relations, _lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1993/1501",
        canonical_ops=(first, second),
        temporal_events=(),
    )

    assert tuple(effect.effect_id for effect in source_effects) == (
        "fi-effect:1994/1486:snapshot_subsection_1_from_section_70:"
        "seq-0:target-part:1/chapter:6/section:70/subsection:1:occ-1",
        "fi-effect:1994/1486:snapshot_subsection_1_from_section_70:"
        "seq-0:target-part:1/chapter:6/section:70/subsection:1:occ-2",
    )
    assert tuple(effect.target_address for effect in source_effects) == (target, target)


def test_duplicate_temporal_group_ids_use_event_and_target_discriminator() -> None:
    source = OperationSource(statute_id="2020/400")
    first = TemporalEvent(
        event_id="temporary:58a:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="2016/1227",
            exact_addresses=(LegalAddress(path=(("section", "58 a"),)),),
        ),
        source=source,
        group_id="finland-johto:2020/400",
    )
    second = TemporalEvent(
        event_id="temporary:58b:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="2016/1227",
            exact_addresses=(LegalAddress(path=(("section", "58 b"),)),),
        ),
        source=source,
        group_id="finland-johto:2020/400",
    )

    source_effects, _relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2016/1227",
        canonical_ops=(),
        temporal_events=(first, second),
    )

    assert tuple(effect.effect_id for effect in source_effects) == (
        "fi-effect:2020/400:finland-johto:2020/400:event-temporary:58a:expire:target-section:58_a",
        "fi-effect:2020/400:finland-johto:2020/400:event-temporary:58b:expire:target-section:58_b",
    )
    assert tuple(event.effect for event in lifecycle_events) == source_effects


def test_section_lifecycle_scope_is_not_an_exact_address() -> None:
    scope = EffectLifecycleOverrideScope.sections(("4 a §",))

    assert scope.kind == "section"
    assert scope.labels == ("4a",)
    assert scope.exact_target_address is None
    assert scope.to_meta()["scope_labels"] == ["4a"]


def test_mixed_lifecycle_scope_keeps_labels_and_addresses_distinct() -> None:
    address = LegalAddress(path=(("chapter", "2"), ("section", "8"),))

    scope = EffectLifecycleOverrideScope.mixed(labels=("4 a §",), addresses=(address,))

    assert scope.kind == "mixed"
    assert scope.exact_target_address is None
    assert scope.to_meta()["scope_labels"] == ["4a"]
    assert scope.to_meta()["scope_addresses"] == ["chapter:2/section:8"]


def test_lifecycle_scope_rejects_untyped_exact_addresses() -> None:
    with pytest.raises(TypeError, match="LegalAddress"):
        EffectLifecycleOverrideScope.exact_addresses(cast(Any, ("section:4a",)))


def test_lifecycle_scope_rejects_address_values_in_section_label_lane() -> None:
    address = LegalAddress(path=(("section", "4 a"),))

    with pytest.raises(TypeError, match="section labels"):
        EffectLifecycleOverrideScope.sections(cast(Any, (address,)))
    with pytest.raises(TypeError, match="section labels"):
        EffectLifecycleOverrideScope.mixed(
            labels=cast(Any, (address,)),
            addresses=(address,),
        )
    with pytest.raises(TypeError, match="section labels"):
        EffectLifecycleOverrideScope(kind="section", labels=cast(Any, (address,)))


def test_finland_pending_amendment_relation_signal_is_authority_input() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.pending_amendment(
                source_statute="2021/2",
                target_statute="2020/1",
                target_title="Target amendment",
                base_parent_id="1990/1",
                message="pending target unresolved",
                source_finding="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
                target_resolution="target_instrument_unresolved",
            ),
        ),
    )

    assert source_effects == ()
    assert len(relations) == 1
    assert relations[0].kind == "modifies_effect"
    assert relations[0].target_instrument is not None
    assert relations[0].target_instrument.instrument_id == "2020/1"
    assert relations[0].detail["source_finding"] == "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED"
    assert "resolved" not in relations[0].detail
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "unresolved_effect_target"
    assert lifecycle_events[0].detail["projection"] == "effect_relation_signal"


def test_relation_signal_duplicate_id_conflict_is_not_silently_skipped() -> None:
    with pytest.raises(ValueError, match="conflicting duplicate relation_id"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=(),
            relation_signals=(
                EffectRelationSignal.pending_amendment(
                    source_statute="2021/2",
                    target_statute="2020/1",
                    target_title="First title",
                    target_resolution="target_instrument_resolved",
                ),
                EffectRelationSignal.pending_amendment(
                    source_statute="2021/2",
                    target_statute="2020/1",
                    target_title="Second title",
                    target_resolution="target_instrument_resolved",
                ),
            ),
        )


def test_unresolved_relation_signal_duplicate_event_conflict_is_not_silently_skipped() -> None:
    with pytest.raises(ValueError, match="conflicting duplicate lifecycle_event_id"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=(),
            relation_signals=(
                EffectRelationSignal.pending_amendment(
                    source_statute="2021/2",
                    target_statute="",
                    message="first unresolved target",
                    source_finding="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
                    target_resolution="target_instrument_unresolved",
                ),
                EffectRelationSignal.pending_amendment(
                    source_statute="2021/2",
                    target_statute="",
                    message="second unresolved target",
                    source_finding="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
                    target_resolution="target_instrument_unresolved",
                ),
            ),
        )


def test_finland_pending_amendment_relation_signal_binds_known_effect() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(_op(),),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.pending_amendment(
                source_statute="2021/2",
                target_statute="2020/1",
                target_title="Target amendment",
                base_parent_id="1990/1",
                source_finding="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(source_effects) == 1
    assert len(relations) == 1
    assert relations[0].kind == "modifies_effect"
    assert relations[0].target_effect == source_effects[0]
    assert relations[0].target_instrument is None
    assert "resolved" not in relations[0].detail
    assert "target_effect_id" not in relations[0].detail
    assert lifecycle_events == ()


def test_finland_pending_amendment_signal_does_not_bind_multiple_effects_by_instrument() -> None:
    op_a = _op()
    op_b = LegalOperation(
        op_id="op-2",
        sequence=2,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "5"),)),
        source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
        group_id="g:2020/1:op-2",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(op_a, op_b),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.pending_amendment(
                source_statute="2021/2",
                target_statute="2020/1",
                target_title="Target amendment",
                base_parent_id="1990/1",
                source_finding="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(source_effects) == 2
    assert len(relations) == 1
    relation = relations[0]
    assert relation.kind == "modifies_effect"
    assert relation.target_effect is None
    assert relation.target_instrument is not None
    assert relation.target_instrument.instrument_id == "2020/1"
    assert relation.target_resolution is not None
    assert relation.target_resolution.kind == "ambiguous_multiple_effects"
    assert relation.target_resolution.matched_effect_count == 2
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "unresolved_effect_target"
    assert lifecycle.relation == relation
    assert lifecycle.executable is False
    lifecycle_relation = lifecycle.relation
    assert lifecycle_relation is not None
    assert lifecycle_relation.target_resolution is not None
    assert lifecycle_relation.target_resolution.kind == "ambiguous_multiple_effects"
    assert lifecycle.detail["relation_source_finding"] == (
        "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET"
    )
    assert "source_finding" not in lifecycle.detail
    assert strict_fail_reasons_from_finding_ledger(
        StrictProfile(name="strict"),
        compiled_ops=(),
        canonical_ops=(),
        failures=(),
        findings=(),
        effect_lifecycle_events=lifecycle_events,
    ) == ["APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED"]


def test_finland_meta_repeal_relation_signal_is_authority_input() -> None:
    _source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.meta_repeal(
                source_statute="2021/2",
                target_statute="2020/1",
                route_reason="citation_mismatch_skip",
                message="meta repeal recorded",
                source_finding="APPLY.META_REPEAL_EFFECT_RECORDED",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(relations) == 1
    assert relations[0].kind == "repeals_effect"
    assert relations[0].target_instrument is not None
    assert relations[0].target_instrument.instrument_id == "2020/1"
    assert relations[0].detail["source_finding"] == "APPLY.META_REPEAL_EFFECT_RECORDED"
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "unresolved_effect_target"
    assert lifecycle.relation == relations[0]
    assert lifecycle.executable is False
    assert lifecycle.detail["relation_source_finding"] == "APPLY.META_REPEAL_EFFECT_RECORDED"
    assert "source_finding" not in lifecycle.detail
    assert strict_fail_reasons_from_finding_ledger(
        StrictProfile(name="strict"),
        compiled_ops=(),
        canonical_ops=(),
        failures=(),
        findings=(),
        effect_lifecycle_events=lifecycle_events,
    ) == ["APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED"]


def test_finland_meta_repeal_relation_signal_binds_known_effect() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(
            LegalOperation(
                op_id="op-old-amendment-4a",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "4 a"),)),
                source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                group_id="g:2020/1:old",
            ),
        ),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.meta_repeal(
                source_statute="2021/2",
                target_statute="2020/1",
                route_reason="citation_mismatch_skip",
                source_finding="APPLY.META_REPEAL_EFFECT_RECORDED",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(source_effects) == 1
    assert len(relations) == 1
    assert relations[0].kind == "repeals_effect"
    assert relations[0].target_effect == source_effects[0]
    assert relations[0].target_instrument is None
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "repeal_effect"
    assert lifecycle.effect == source_effects[0]
    assert lifecycle.relation == relations[0]
    assert lifecycle.executable is False
    assert lifecycle.detail["projection"] == "effect_relation_signal"
    assert lifecycle.detail["non_executable_reason"] == (
        "meta-repeal signal did not carry a deterministic repeal date"
    )
    assert lower_lifecycle_event_to_temporal_event(lifecycle) is None


def test_finland_meta_repeal_signal_does_not_bind_multiple_effects_by_instrument() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(
            LegalOperation(
                op_id="op-old-amendment-4a",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "4 a"),)),
                source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                group_id="g:2020/1:old-4a",
            ),
            LegalOperation(
                op_id="op-old-amendment-5",
                sequence=2,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "5"),)),
                source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                group_id="g:2020/1:old-5",
            ),
        ),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.meta_repeal(
                source_statute="2021/2",
                target_statute="2020/1",
                route_reason="citation_mismatch_skip",
                source_finding="APPLY.META_REPEAL_EFFECT_RECORDED",
                target_resolution="target_instrument_resolved",
            ),
        ),
    )

    assert len(source_effects) == 2
    assert len(relations) == 1
    relation = relations[0]
    assert relation.kind == "repeals_effect"
    assert relation.target_effect is None
    assert relation.target_instrument is not None
    assert relation.target_instrument.instrument_id == "2020/1"
    assert relation.target_resolution is not None
    assert relation.target_resolution.kind == "ambiguous_multiple_effects"
    assert relation.target_resolution.matched_effect_count == 2
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "unresolved_effect_target"
    assert lifecycle.relation == relation
    assert lifecycle.executable is False
    lifecycle_relation = lifecycle.relation
    assert lifecycle_relation is not None
    assert lifecycle_relation.target_resolution is not None
    assert lifecycle_relation.target_resolution.kind == "ambiguous_multiple_effects"
    assert lifecycle.detail["relation_source_finding"] == "APPLY.META_REPEAL_EFFECT_RECORDED"
    assert "source_finding" not in lifecycle.detail
    assert strict_fail_reasons_from_finding_ledger(
        StrictProfile(name="strict"),
        compiled_ops=(),
        canonical_ops=(),
        failures=(),
        findings=(),
        effect_lifecycle_events=lifecycle_events,
    ) == ["APPLY.EFFECT_LIFECYCLE_TARGET_UNRESOLVED"]


def test_meta_repeal_lifecycle_uses_source_rule_id_not_detail_metadata() -> None:
    instrument = SourceInstrumentRef(instrument_id="2021/2")
    witness = SourceProvisionRef(
        instrument=instrument,
        path=("routing",),
        rule_id="fi.meta_repeal_effect_relation",
    )
    target_instrument = SourceInstrumentRef(instrument_id="2020/1")
    target_effect = EffectRef(
        effect_id="effect:2020/1:op-1",
        source_instrument=target_instrument,
        target_statute="1990/1",
        target_address=LegalAddress(path=(("section", "4 a"),)),
    )
    relation = EffectRelation(
        relation_id="relation:meta-repeal",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=target_effect,
        detail={},
    )

    lifecycle_events = _lifecycle_events_from_resolved_signal_relations((relation,))

    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "repeal_effect"
    assert lifecycle_events[0].relation == relation


def test_finland_meta_repeal_unresolved_signal_emits_nonexecuting_lifecycle() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
        relation_signals=(
            EffectRelationSignal.meta_repeal(
                source_statute="2021/2",
                target_statute="",
                route_reason="citation_mismatch_skip",
                message="meta repeal unresolved",
                source_finding="APPLY.META_REPEAL_EFFECT_UNRESOLVED",
                target_resolution="target_instrument_unresolved",
            ),
        ),
    )

    assert source_effects == ()
    assert relations == ()
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "unresolved_effect_target"
    assert lifecycle_events[0].executable is False
    assert lifecycle_events[0].detail["source_finding"] == "APPLY.META_REPEAL_EFFECT_UNRESOLVED"


def test_finland_commencement_expiry_override_without_effect_stays_unresolved() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2021/2",
                target_statute="2020/1",
                scope=EffectLifecycleOverrideScope.sections(("4 a",)),
                expiry="2022-12-31",
                context="accepted_amendment",
            ),
        ),
    )

    assert source_effects == ()
    assert len(relations) == 1
    assert relations[0].kind == "extends_effect_expiry"
    assert relations[0].target_effect is None
    assert relations[0].target_instrument is not None
    assert relations[0].target_instrument.instrument_id == "2020/1"
    assert "resolved" not in relations[0].detail
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "unresolved_effect_target"
    assert lifecycle.effect is None
    assert lifecycle.relation == relations[0]
    assert lifecycle.intended_lifecycle_kind == "change_effect_expiry"
    assert "intended_lifecycle_kind" not in lifecycle.detail
    assert lifecycle.executable is False


def test_finland_commencement_expiry_override_matching_effect_is_executable() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(_op(),),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2021/2",
                target_statute="2020/1",
                scope=EffectLifecycleOverrideScope.sections(("4 a §",)),
                expiry="2022-12-31",
                context="accepted_amendment",
            ),
        ),
    )

    assert len(source_effects) == 1
    assert len(relations) == 1
    assert relations[0].kind == "extends_effect_expiry"
    assert relations[0].target_effect == source_effects[0]
    assert relations[0].target_instrument is None
    assert "resolved" not in relations[0].detail
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "change_effect_expiry"
    assert lifecycle.effect == source_effects[0]
    assert lifecycle.relation == relations[0]
    assert lifecycle.expires == "2022-12-31"
    assert lifecycle.expiry_convention == "inclusive_valid_until"
    assert lifecycle.executable is True
    temporal = lower_lifecycle_event_to_temporal_event(lifecycle)
    assert temporal is not None
    assert temporal.group_id == "g:2020/1:op-1"
    assert temporal.expires == "2023-01-01"
    assert temporal.scope.exact_addresses == (LegalAddress(path=(("section", "4 a"),)),)


def test_finland_commencement_effective_override_projects_executable_lifecycle() -> None:
    op = LegalOperation(
        op_id="op-chapter-1-section-4a",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "1"), ("section", "4 a"))),
        source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
        group_id="g:2020/1:chapter-1-section-4a",
    )
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(op,),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2021/2",
                target_statute="2020/1",
                scope=EffectLifecycleOverrideScope.exact_addresses(
                    (LegalAddress(path=(("chapter", "1"), ("section", "4 a"))),)
                ),
                effective="2022-01-01",
                context="accepted_section_commencement",
            ),
        ),
    )

    assert len(relations) == 1
    assert relations[0].kind == "changes_effect_commencement"
    assert relations[0].target_effect == source_effects[0]
    assert source_effects[0].target_address == LegalAddress(
        path=(("chapter", "1"), ("section", "4 a"))
    )
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "change_effect_commencement"
    assert lifecycle_events[0].effective == "2022-01-01"
    assert lifecycle_events[0].executable is True
    temporal = lower_lifecycle_event_to_temporal_event(lifecycle_events[0])
    assert temporal is not None
    assert lifecycle_events[0].relation is not None
    assert temporal.group_id == lifecycle_events[0].relation.relation_id


def test_finland_commencement_override_does_not_revive_repeal_effect() -> None:
    repeal_op = LegalOperation(
        op_id="snapshot_section_15",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "2"), ("section", "15"))),
        source=OperationSource(statute_id="2024/1100", effective="2025-01-01"),
        group_id="finland-johto:2024/1100",
    )
    replace_op = LegalOperation(
        op_id="snapshot_section_16",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "2"), ("section", "16"))),
        source=OperationSource(statute_id="2024/1100", effective="2025-01-01"),
        group_id="finland-johto:2024/1100",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2023/681",
        canonical_ops=(repeal_op, replace_op),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2024/1100",
                target_statute="2024/1100",
                scope=EffectLifecycleOverrideScope.instrument(),
                effective="2025-04-01",
                context="accepted_subsection_commencement",
            ),
            EffectLifecycleOverride(
                source_statute="2024/1100",
                target_statute="2024/1100",
                scope=EffectLifecycleOverrideScope.sections(("15",)),
                expiry="2025-01-01",
                context="repeal_clause",
            ),
        ),
    )

    repeal_effect = next(effect for effect in source_effects if effect.target_address == repeal_op.target)
    replace_effect = next(effect for effect in source_effects if effect.target_address == replace_op.target)
    commencement_relations = [
        relation for relation in relations if relation.kind == "changes_effect_commencement"
    ]
    repeal_relations = [relation for relation in relations if relation.kind == "repeals_effect"]
    assert [relation.target_effect for relation in commencement_relations] == [replace_effect]
    assert [relation.target_effect for relation in repeal_relations] == [repeal_effect]
    commencement_events = [
        event for event in lifecycle_events if event.kind == "change_effect_commencement"
    ]
    repeal_events = [event for event in lifecycle_events if event.kind == "repeal_effect"]
    assert [event.effect for event in commencement_events] == [replace_effect]
    assert [event.effect for event in repeal_events] == [repeal_effect]


def test_finland_known_generic_repeal_effect_is_canonicalized_before_commencement_matching() -> None:
    target = LegalAddress(path=(("chapter", "2"), ("section", "15")))
    source_instrument = SourceInstrumentRef(instrument_id="2024/1100")
    generic_known_effect = EffectRef(
        effect_id="fi-effect:2024/1100:snapshot_section_15",
        source_instrument=source_instrument,
        target_statute="2023/681",
        target_address=target,
        projection_group_id="finland-johto:2024/1100",
        source_provision=SourceProvisionRef(
            instrument=source_instrument,
            path=("snapshot_section_15",),
            span_id="snapshot_section_15",
            rule_id="fi.legal_operation.effect_declaration",
        ),
    )
    repeal_op = LegalOperation(
        op_id="snapshot_section_15",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=target,
        source=OperationSource(statute_id="2024/1100", effective="2025-01-01"),
        group_id="finland-johto:2024/1100",
    )

    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="2023/681",
        canonical_ops=(repeal_op,),
        temporal_events=(),
        known_source_effects=(generic_known_effect,),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2024/1100",
                target_statute="2024/1100",
                scope=EffectLifecycleOverrideScope.instrument(),
                effective="2025-04-01",
                context="accepted_subsection_commencement",
            ),
            EffectLifecycleOverride(
                source_statute="2024/1100",
                target_statute="2024/1100",
                scope=EffectLifecycleOverrideScope.sections(("15",)),
                expiry="2025-01-01",
                context="repeal_clause",
            ),
        ),
    )

    assert source_effects == ()
    commencement_relations = [
        relation for relation in relations if relation.kind == "changes_effect_commencement"
    ]
    repeal_relations = [relation for relation in relations if relation.kind == "repeals_effect"]
    assert len(commencement_relations) == 1
    assert commencement_relations[0].target_effect is None
    assert commencement_relations[0].target_instrument is not None
    assert len(repeal_relations) == 1
    assert repeal_relations[0].target_effect is not None
    assert repeal_relations[0].target_effect.source_provision is not None
    assert (
        repeal_relations[0].target_effect.source_provision.rule_id
        == "fi.legal_operation.repeal_effect_declaration"
    )
    assert "change_effect_commencement" not in {event.kind for event in lifecycle_events}
    assert [event.kind for event in lifecycle_events if event.executable] == ["repeal_effect"]


def test_finland_repeal_override_projects_executable_lifecycle_when_effect_matches() -> None:
    source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(
            LegalOperation(
                op_id="op-self-4a",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "4 a"),)),
                source=OperationSource(statute_id="2021/2", effective="2022-01-01"),
                group_id="g:2021/2:self-4a",
            ),
        ),
        temporal_events=(),
        lifecycle_overrides=(
            EffectLifecycleOverride(
                source_statute="2021/2",
                target_statute="2021/2",
                scope=EffectLifecycleOverrideScope.sections(("4 a",)),
                expiry="2022-01-01",
                context="repeal_clause",
            ),
        ),
    )

    assert len(relations) == 1
    assert relations[0].kind == "repeals_effect"
    assert relations[0].target_effect == source_effects[0]
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "repeal_effect"
    assert lifecycle_events[0].expires == "2022-01-01"
    assert lifecycle_events[0].executable is True
    temporal = lower_lifecycle_event_to_temporal_event(lifecycle_events[0])
    assert temporal is not None
    assert lifecycle_events[0].relation is not None
    assert temporal.group_id == lifecycle_events[0].relation.relation_id


def test_finland_lifecycle_projection_rejects_serialized_override_meta() -> None:
    with pytest.raises(TypeError, match="EffectLifecycleOverride"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=(),
            lifecycle_overrides=cast(Any, (
                {
                    "source_statute": "2021/2",
                    "target_statute": "2020/1",
                    "scope_kind": "section",
                    "scope_labels": ("4 a",),
                    "expiry": "2022-12-31",
                    "context": "accepted_amendment",
                },
            )),
        )


def test_finland_lifecycle_projection_rejects_untyped_primary_lanes() -> None:
    with pytest.raises(TypeError, match="LegalOperation"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=cast(Any, ({"op_id": "op-1"},)),
            temporal_events=(),
        )
    with pytest.raises(TypeError, match="TemporalEvent"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=cast(Any, ({"event_id": "event-1"},)),
        )
    with pytest.raises(TypeError, match="EffectRef"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=(),
            known_source_effects=cast(Any, ("effect:1",)),
        )


def test_finland_lifecycle_override_rejects_untyped_string_fields() -> None:
    scope = EffectLifecycleOverrideScope.sections(("4 a",))

    with pytest.raises(TypeError, match="source_statute"):
        EffectLifecycleOverride(
            source_statute=cast(Any, 2021),
            target_statute="2020/1",
            scope=scope,
            expiry="2022-12-31",
            context="accepted_amendment",
        )
    with pytest.raises(TypeError, match="expiry"):
        EffectLifecycleOverride(
            source_statute="2021/2",
            target_statute="2020/1",
            scope=scope,
            expiry=cast(Any, object()),
            context="accepted_amendment",
        )


def test_finland_relation_signal_rejects_untyped_string_fields() -> None:
    with pytest.raises(TypeError, match="target_title"):
        EffectRelationSignal.pending_amendment(
            source_statute="2021/2",
            target_statute="2020/1",
            target_title=cast(Any, object()),
            target_resolution="target_instrument_resolved",
        )
    with pytest.raises(TypeError, match="message"):
        EffectRelationSignal.meta_repeal(
            source_statute="2021/2",
            target_statute="2020/1",
            message=cast(Any, object()),
            target_resolution="target_instrument_resolved",
        )
    with pytest.raises(ValueError, match="target resolution"):
        EffectRelationSignal.pending_amendment(
            source_statute="2021/2",
            target_statute="2020/1",
            target_resolution=cast(Any, "resolved"),
        )
    with pytest.raises(ValueError, match="requires target_statute"):
        EffectRelationSignal.meta_repeal(
            source_statute="2021/2",
            target_statute="",
            target_resolution="target_instrument_resolved",
        )


def test_process_result_builder_preserves_effect_lifecycle_side_channels() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument, source_provision=witness)
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )
    buffers = ProcessSignalBuffers.empty()
    buffers.source_effects.append(effect)
    buffers.effect_lifecycle_events.append(lifecycle)
    builder = ProcessResultBuilder(
        amendment_id="2024/1",
        buffers=buffers,
        migration_ledger=MigrationLedger(),
        migration_ledger_initial_len=0,
        sinks=ProcessCompatSinks(
            failed_ops_out=None,
            source_pathologies_out=None,
            elaboration_observations_out=None,
            sparse_slot_bindings_out=None,
            sparse_leftovers_out=None,
            commencement_expiry_overrides_out=None,
            mutation_events_out=None,
            mutation_invariant_reports_out=None,
        ),
    )

    result = builder.build(output_state="state")

    assert result.source_effects == (effect,)
    assert result.effect_lifecycle_events == (lifecycle,)


def test_process_result_builder_projects_pending_relation_from_typed_signal() -> None:
    buffers = ProcessSignalBuffers.empty()
    buffers.effect_relation_signals.append(
        EffectRelationSignal.pending_amendment(
            source_statute="2022/708",
            target_statute="2020/1233",
            target_title="Laki valmiuslain 109 §:n muuttamisesta",
            base_parent_id="2011/1552",
            source_finding="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
            target_resolution="target_instrument_resolved",
        )
    )
    builder = ProcessResultBuilder(
        amendment_id="2022/708",
        buffers=buffers,
        migration_ledger=MigrationLedger(),
        migration_ledger_initial_len=0,
        sinks=ProcessCompatSinks(
            failed_ops_out=None,
            source_pathologies_out=None,
            elaboration_observations_out=None,
            sparse_slot_bindings_out=None,
            sparse_leftovers_out=None,
            commencement_expiry_overrides_out=None,
            mutation_events_out=None,
            mutation_invariant_reports_out=None,
        ),
        target_statute="2011/1552",
    )

    result = builder.build(output_state="state")

    assert len(result.effect_relations) == 1
    relation = result.effect_relations[0]
    assert relation.kind == "modifies_effect"
    assert relation.target_instrument is not None
    assert relation.target_instrument.instrument_id == "2020/1233"


def test_process_result_builder_rejects_conflicting_projected_relation_id() -> None:
    instrument = SourceInstrumentRef(instrument_id="2022/708")
    buffers = ProcessSignalBuffers.empty()
    buffers.effect_relations.append(
        EffectRelation(
            relation_id="fi-effect-relation:2022/708:pending_amendment:2020/1233",
            kind="modifies_effect",
            source_provision=SourceProvisionRef(
                instrument=instrument,
                path=("routing",),
                rule_id="fi.pending_amendment_of_parent_effect_relation",
            ),
            target_instrument=SourceInstrumentRef(instrument_id="2020/1233", title="Old title"),
            detail={"source_finding": "old"},
        )
    )
    buffers.effect_relation_signals.append(
        EffectRelationSignal.pending_amendment(
            source_statute="2022/708",
            target_statute="2020/1233",
            target_title="New title",
            source_finding="new",
            target_resolution="target_instrument_resolved",
        )
    )
    builder = ProcessResultBuilder(
        amendment_id="2022/708",
        buffers=buffers,
        migration_ledger=MigrationLedger(),
        migration_ledger_initial_len=0,
        sinks=ProcessCompatSinks(
            failed_ops_out=None,
            source_pathologies_out=None,
            elaboration_observations_out=None,
            sparse_slot_bindings_out=None,
            sparse_leftovers_out=None,
            commencement_expiry_overrides_out=None,
            mutation_events_out=None,
            mutation_invariant_reports_out=None,
        ),
        target_statute="2011/1552",
    )

    with pytest.raises(ValueError, match="conflicting duplicate relation_id"):
        builder.build(output_state="state")


def test_frontend_normalization_preserves_phase_effect_relation_lane(monkeypatch) -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2025-01-01",
    )

    class SourceModel:
        def normalize_and_compile_ops(self, **_kwargs: object) -> PhaseResult[list[object]]:
            return PhaseResult(
                output=[],
                source_effects=(effect,),
                effect_relations=(relation,),
                effect_lifecycle_events=(lifecycle,),
            )

    monkeypatch.setattr(pfn, "_parse_johtolause_clause", lambda _text: None)

    result = ProcessFrontendNormalizationContext(
        johto="",
        source_model=cast(Any, SourceModel()),
        state=object(),
        base_ir=None,
        amendment_id="2024/1",
        source_title="",
        used_preamble_body_fallback=False,
        parent_id="1990/1",
        strict_profile=None,
        regex_recognition_coverage_out=None,
        normalize_and_compile_ops=lambda **_kwargs: None,
    ).run()

    assert result.source_effects == (effect,)
    assert result.effect_relations == (relation,)
    assert result.effect_lifecycle_events == (lifecycle,)


def test_process_compile_signals_rejects_conflicting_source_effect_id() -> None:
    existing = EffectRef(
        effect_id="effect:duplicate",
        source_instrument=SourceInstrumentRef(instrument_id="2024/1"),
        target_statute="parent-a",
    )
    conflicting = EffectRef(
        effect_id="effect:duplicate",
        source_instrument=SourceInstrumentRef(instrument_id="2024/1"),
        target_statute="parent-b",
    )
    process_findings: list[Finding] = []

    def record_process_finding(**kwargs: object) -> Finding:
        finding = Finding(
            kind=str(kwargs.get("kind") or ""),
            role="obligation",
            stage="test",
            detail={},
            blocking=True,
        )
        process_findings.append(finding)
        return finding

    context = ProcessCompileSignalsContext(
        amendment_id="2024/1",
        parent_id="1990/1",
        resolved=[],
        compile_result=PhaseResult(output=[], source_effects=(conflicting,)),
        amendment_temporal_events=[],
        source_effects=[existing],
        effect_relations=[],
        effect_lifecycle_events=[],
        source_pathologies=[],
        elaboration_observations=[],
        sparse_slot_bindings=[],
        sparse_leftovers=[],
        process_findings=process_findings,
        record_finding=record_process_finding,
    )

    with pytest.raises(ValueError, match="conflicting duplicate effect_id"):
        context.project()


def test_process_compile_signals_temporal_lifecycle_reuses_phase_source_effect() -> None:
    target = LegalAddress(path=(("section", "4 a"),))
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(
        instrument=instrument,
        path=("phase", "effect"),
        rule_id="test.phase_source_effect",
    )
    phase_effect = EffectRef(
        effect_id="phase-effect:2020/1:custom",
        source_instrument=instrument,
        target_statute="1990/1",
        target_address=target,
        projection_group_id="g:phase-custom",
        source_provision=witness,
    )
    temporal = TemporalEvent(
        event_id="fi-temporal:2020/1:custom:expire",
        kind="expire",
        scope=TemporalScope(
            target_statute="1990/1",
            exact_addresses=(target,),
        ),
        expires="2022-01-01",
        source=OperationSource(statute_id="2020/1"),
        group_id="g:phase-custom",
    )
    source_effects: list[EffectRef] = []
    lifecycle_events: list[EffectLifecycleEvent] = []

    context = ProcessCompileSignalsContext(
        amendment_id="2020/1",
        parent_id="1990/1",
        resolved=[],
        compile_result=PhaseResult(
            output=[],
            temporal_events=(temporal,),
            source_effects=(phase_effect,),
        ),
        amendment_temporal_events=[],
        source_effects=source_effects,
        effect_relations=[],
        effect_lifecycle_events=lifecycle_events,
        source_pathologies=[],
        elaboration_observations=[],
        sparse_slot_bindings=[],
        sparse_leftovers=[],
        process_findings=[],
        record_finding=lambda **_kwargs: Finding(
            kind="test.unused",
            role="obligation",
            stage="test",
            detail={},
            blocking=True,
        ),
    )

    context.project()

    assert source_effects == [phase_effect]
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].effect == phase_effect
    assert lifecycle_events[0].temporal_event is temporal


def test_build_finland_effect_lifecycle_merges_final_graph_lanes(monkeypatch) -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    relation = EffectRelation(
        relation_id="relation:shared",
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=instrument,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:shared",
        kind="unresolved_effect_target",
        source_provision=witness,
        relation=relation,
        executable=False,
    )

    monkeypatch.setattr(
        elp,
        "_relations_from_lifecycle_overrides",
        lambda *_args, **_kwargs: (relation,),
    )
    monkeypatch.setattr(
        elp,
        "_relations_from_signals",
        lambda *_args, **_kwargs: (relation,),
    )
    monkeypatch.setattr(
        elp,
        "_lifecycle_from_temporal_events",
        lambda *_args, **_kwargs: (lifecycle,),
    )
    monkeypatch.setattr(
        elp,
        "_lifecycle_events_from_lifecycle_overrides",
        lambda *_args, **_kwargs: (lifecycle,),
    )
    monkeypatch.setattr(
        elp,
        "_lifecycle_events_from_resolved_signal_relations",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        elp,
        "_lifecycle_events_from_unresolved_signal_relations",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        elp,
        "_unresolved_lifecycle_from_relation_signals",
        lambda *_args, **_kwargs: (),
    )

    _effects, relations, lifecycle_events = build_finland_effect_lifecycle(
        target_statute="1990/1",
        canonical_ops=(),
        temporal_events=(),
    )

    assert relations == (relation,)
    assert lifecycle_events == (lifecycle,)


def test_build_finland_effect_lifecycle_rejects_conflicting_final_graph_lanes(
    monkeypatch,
) -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    relation = EffectRelation(
        relation_id="relation:shared",
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=instrument,
    )
    conflicting_relation = EffectRelation(
        relation_id="relation:shared",
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=instrument,
        detail={"note": "conflict"},
    )

    monkeypatch.setattr(
        elp,
        "_relations_from_lifecycle_overrides",
        lambda *_args, **_kwargs: (relation,),
    )
    monkeypatch.setattr(
        elp,
        "_relations_from_signals",
        lambda *_args, **_kwargs: (conflicting_relation,),
    )

    with pytest.raises(ValueError, match="conflicting duplicate relation_id"):
        build_finland_effect_lifecycle(
            target_statute="1990/1",
            canonical_ops=(),
            temporal_events=(),
        )
