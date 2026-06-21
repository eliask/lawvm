"""Tests for PhaseResult finding projection views.

Run:
    uv run pytest tests/test_phase_result_helpers.py -v
"""
from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.ir import LegalAddress, OperationSource
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    SourceInstrumentRef,
    SourceProvisionRef,
)
from lawvm.core.phase_result import Finding, PhaseBuilder, PhaseResult
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.core.temporal import ActivationRule
from lawvm.core.provenance import MigrationEvent


def _obs(kind: str, stage: str = "test_stage") -> Finding:
    return Finding(
        kind=kind,
        role="observation",
        stage=stage,
        detail={},
        source_statute="",
        blocking=False,
    )


def test_observations_projection_returns_tuple() -> None:
    obs = _obs("ELAB.SOURCE_PATHOLOGY")
    pr = PhaseResult(output=None, findings=(obs,))
    assert tuple(f for f in pr.findings() if f.role == "observation") == (obs,)
    assert isinstance(pr.findings(), tuple)


def test_observations_projection_preserves_order_after_merge() -> None:
    obs_a = _obs("ELAB.SOURCE_PATHOLOGY", "s1")
    obs_b = _obs("ELAB.SOURCE_PATHOLOGY", "s2")
    obs_other = _obs("PARSE.DUPLICATE_TARGET_OP", "s2")
    pr_a = PhaseResult(output="a", findings=(obs_a,))
    pr_b = PhaseResult(
        output="b",
        findings=(obs_b, obs_other),
    )
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "observation") == (obs_a, obs_b, obs_other)


def test_violations_projection_returns_tuple() -> None:
    vio = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply",
        detail={"message": "boom"},
        source_statute="2024/1",
        blocking=True,
    )
    pr = PhaseResult(output=None, findings=(vio,))
    assert tuple(f for f in pr.findings() if f.role == "violation") == (vio,)
    assert isinstance(pr.findings(), tuple)


def test_violations_projection_preserves_order_after_merge() -> None:
    vio_a = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply_a",
        detail={"message": "a"},
        source_statute="2024/1",
        blocking=True,
    )
    vio_b = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply_b",
        detail={"message": "b"},
        source_statute="2024/2",
        blocking=True,
    )
    pr_a = PhaseResult(output="a", findings=(vio_a,))
    pr_b = PhaseResult(output="b", findings=(vio_b,))
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "violation") == (vio_a, vio_b)


def test_migration_events_preserve_order_after_merge() -> None:
    migration_a = MigrationEvent(
        event_id="mig:a",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "1a"),)),
    )
    migration_b = MigrationEvent(
        event_id="mig:b",
        kind="move",
        from_address=LegalAddress(path=(("section", "2"),)),
        to_address=LegalAddress(path=(("section", "2a"),)),
    )
    pr_a = PhaseResult(output="a", migration_events=(migration_a,))
    pr_b = PhaseResult(output="b", migration_events=(migration_b,))
    merged = pr_a.merge(pr_b)
    assert merged.migration_events == (migration_a, migration_b)


def test_effect_lifecycle_side_channels_preserve_order_after_merge() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    target_effect = EffectRef(effect_id="effect:a", source_instrument=instrument)
    relation_a = EffectRelation(
        relation_id="relation:a",
        kind="modifies_effect",
        source_provision=witness,
        target_instrument=instrument,
    )
    relation_b = EffectRelation(
        relation_id="relation:b",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_instrument=instrument,
    )
    event_a = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:a",
        kind="unresolved_effect_target",
        source_provision=witness,
        relation=relation_a,
        executable=False,
    )
    event_b = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:b",
        kind="unresolved_effect_target",
        source_provision=witness,
        relation=relation_b,
        executable=False,
    )
    effect_b = EffectRef(effect_id="effect:b", source_instrument=instrument)
    pr_a = PhaseResult(
        output="a",
        source_effects=(target_effect,),
        effect_relations=(relation_a,),
        effect_lifecycle_events=(event_a,),
    )
    pr_b = PhaseResult(
        output="b",
        source_effects=(effect_b,),
        effect_relations=(relation_b,),
        effect_lifecycle_events=(event_b,),
    )

    merged = pr_a.merge(pr_b)

    assert merged.source_effects == (target_effect, effect_b)
    assert merged.effect_relations == (relation_a, relation_b)
    assert merged.effect_lifecycle_events == (event_a, event_b)


def test_phase_result_rejects_untyped_effect_side_channel_values() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:a", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:a",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=effect,
    )

    with pytest.raises(TypeError, match="source_effects"):
        PhaseResult(output=None, source_effects=cast(Any, ("effect:a",)))
    with pytest.raises(TypeError, match="effect_relations"):
        PhaseResult(output=None, effect_relations=cast(Any, ("relation:a",)))
    with pytest.raises(TypeError, match="effect_lifecycle_events"):
        PhaseResult(
            output=None,
            effect_lifecycle_events=cast(Any, (relation,)),
        )


def test_phase_result_rejects_untyped_temporal_and_migration_side_channel_values() -> None:
    with pytest.raises(TypeError, match="temporal_events"):
        PhaseResult(output=None, temporal_events=cast(Any, ("temporal:a",)))
    with pytest.raises(TypeError, match="migration_events"):
        PhaseResult(output=None, migration_events=cast(Any, ("migration:a",)))


def test_phase_result_rejects_duplicate_effect_graph_ids() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect_a = EffectRef(effect_id="effect:a", source_instrument=instrument)
    effect_b = EffectRef(effect_id="effect:a", source_instrument=instrument)
    target_effect = EffectRef(effect_id="effect:target", source_instrument=instrument)
    relation_a = EffectRelation(
        relation_id="relation:a",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    relation_b = EffectRelation(
        relation_id="relation:a",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    lifecycle_a = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:a",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )
    lifecycle_b = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:a",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )

    with pytest.raises(ValueError, match="duplicate effect_id"):
        PhaseResult(output=None, source_effects=(effect_a, effect_b))
    with pytest.raises(ValueError, match="duplicate relation_id"):
        PhaseResult(output=None, effect_relations=(relation_a, relation_b))
    with pytest.raises(ValueError, match="duplicate lifecycle_event_id"):
        PhaseResult(output=None, effect_lifecycle_events=(lifecycle_a, lifecycle_b))


def test_phase_result_merge_dedupes_identical_effect_graph_ids() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    effect_a = EffectRef(effect_id="effect:a", source_instrument=instrument)
    effect_b = EffectRef(effect_id="effect:a", source_instrument=instrument)
    pr_a = PhaseResult(output="a", source_effects=(effect_a,))
    pr_b = PhaseResult(output="b", source_effects=(effect_b,))

    merged = pr_a.merge(pr_b)

    assert merged.output == "b"
    assert merged.source_effects == (effect_a,)


def test_phase_result_merge_rejects_conflicting_effect_graph_ids() -> None:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    effect_a = EffectRef(
        effect_id="effect:a",
        source_instrument=instrument,
        target_statute="1991/1",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    effect_b = EffectRef(
        effect_id="effect:a",
        source_instrument=instrument,
        target_statute="1991/1",
        target_address=LegalAddress(path=(("section", "2"),)),
    )
    pr_a = PhaseResult(output="a", source_effects=(effect_a,))
    pr_b = PhaseResult(output="b", source_effects=(effect_b,))

    with pytest.raises(ValueError, match="conflicting duplicate effect_id"):
        pr_a.merge(pr_b)


def test_phase_builder_rejects_untyped_effect_side_channel_values() -> None:
    builder: PhaseBuilder[None] = PhaseBuilder()

    with pytest.raises(TypeError, match="EffectRef"):
        builder.add_source_effect(cast(Any, "effect:a"))
    with pytest.raises(TypeError, match="EffectRelation"):
        builder.add_effect_relation(cast(Any, "relation:a"))
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        builder.add_effect_lifecycle_event(cast(Any, "lifecycle:a"))


def test_phase_builder_rejects_untyped_temporal_and_migration_side_channel_values() -> None:
    builder: PhaseBuilder[None] = PhaseBuilder()

    with pytest.raises(TypeError, match="TemporalEvent"):
        builder.add_temporal_event(cast(Any, "temporal:a"))
    with pytest.raises(TypeError, match="TemporalEvent"):
        builder.add_temporal_events(cast(Any, ("temporal:a",)))
    with pytest.raises(TypeError, match="MigrationEvent"):
        builder.add_migration_event(cast(Any, "migration:a"))
    with pytest.raises(TypeError, match="MigrationEvent"):
        builder.add_migration_events(cast(Any, ("migration:a",)))


def test_phase_result_summary_accessors_project_derived_kinds() -> None:
    migration = MigrationEvent(
        event_id="mig:a",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "1a"),)),
    )
    temporal = TemporalEvent(
        event_id="temporal:a",
        kind="commence",
        scope=TemporalScope(target_statute="1991/1"),
        activation_rule=ActivationRule(kind="fixed_date", effective_date="2024-01-01"),
        source=OperationSource(statute_id="2024/1", enacted="2024-01-01"),
    )
    pr = PhaseResult(output=None, migration_events=(migration,), temporal_events=(temporal,))

    assert pr.migration_event_kinds == ("renumber",)
    assert pr.temporal_event_kinds == ("commence",)
    assert pr.temporal_events_with_activation_rules == 1
    assert pr.temporal_events_with_source == 1
    assert pr.temporal_event_activation_rule_kinds == ("fixed_date",)
