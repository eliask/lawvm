from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    SourceInstrumentRef,
    SourceProvisionRef,
    lower_lifecycle_event_to_temporal_event,
)
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.finland.effect_lifecycle_signals import (
    EffectLifecycleOverride,
    EffectLifecycleOverrideScope,
    EffectRelationSignal,
)
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.migration_ledger import MigrationLedger
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


def test_section_lifecycle_scope_is_not_an_exact_address() -> None:
    scope = EffectLifecycleOverrideScope.sections(("4 a",))

    assert scope.kind == "section"
    assert scope.exact_target_address is None
    assert scope.to_meta()["scope_labels"] == ["4 a"]


def test_mixed_lifecycle_scope_keeps_labels_and_addresses_distinct() -> None:
    address = LegalAddress(path=(("chapter", "2"), ("section", "8"),))

    scope = EffectLifecycleOverrideScope.mixed(labels=("4 a",), addresses=(address,))

    assert scope.kind == "mixed"
    assert scope.exact_target_address is None
    assert scope.to_meta()["scope_labels"] == ["4 a"]
    assert scope.to_meta()["scope_addresses"] == ["chapter:2/section:8"]


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
                resolved=False,
            ),
        ),
    )

    assert source_effects == ()
    assert len(relations) == 1
    assert relations[0].kind == "modifies_effect"
    assert relations[0].target_instrument is not None
    assert relations[0].target_instrument.instrument_id == "2020/1"
    assert relations[0].detail["source_finding"] == "APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED"
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].kind == "unresolved_effect_target"
    assert lifecycle_events[0].detail["projection"] == "effect_relation_signal"


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
                resolved=True,
            ),
        ),
    )

    assert len(source_effects) == 1
    assert len(relations) == 1
    assert relations[0].kind == "modifies_effect"
    assert relations[0].target_effect == source_effects[0]
    assert relations[0].target_instrument is None
    assert relations[0].detail["target_effect_id"] == source_effects[0].effect_id
    assert lifecycle_events == ()


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
                resolved=True,
            ),
        ),
    )

    assert len(relations) == 1
    assert relations[0].kind == "repeals_effect"
    assert relations[0].target_instrument is not None
    assert relations[0].target_instrument.instrument_id == "2020/1"
    assert relations[0].detail["source_finding"] == "APPLY.META_REPEAL_EFFECT_RECORDED"
    assert lifecycle_events == ()


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
                resolved=True,
            ),
        ),
    )

    assert len(source_effects) == 1
    assert len(relations) == 1
    assert relations[0].kind == "repeals_effect"
    assert relations[0].target_effect == source_effects[0]
    assert relations[0].target_instrument is None
    assert lifecycle_events == ()


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
                resolved=False,
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
    assert relations[0].detail["resolved"] is False
    assert len(lifecycle_events) == 1
    lifecycle = lifecycle_events[0]
    assert lifecycle.kind == "unresolved_effect_target"
    assert lifecycle.effect is None
    assert lifecycle.relation == relations[0]
    assert lifecycle.detail["intended_lifecycle_kind"] == "change_effect_expiry"
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
                scope=EffectLifecycleOverrideScope.sections(("4 a",)),
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
    assert relations[0].detail["resolved"] is True
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
            resolved=True,
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
