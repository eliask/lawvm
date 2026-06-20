"""Tests for Finland effect graph merge buffer contracts."""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    SourceInstrumentRef,
    SourceProvisionRef,
)
from lawvm.finland.effect_graph_merge import (
    append_unique_effect_lifecycle_event,
    append_unique_effect_ref,
    append_unique_effect_relation,
)


def _effect_graph_records() -> tuple[EffectRef, EffectRelation, EffectLifecycleEvent]:
    instrument = SourceInstrumentRef(instrument_id="2024/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        source_provision=witness,
    )
    relation = EffectRelation(
        relation_id="relation:1",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )
    return effect, relation, lifecycle


def test_effect_graph_merge_helpers_reject_untyped_incoming_records() -> None:
    with pytest.raises(TypeError, match="EffectRef"):
        append_unique_effect_ref([], cast(Any, "effect:1"), subject="test source effects")
    with pytest.raises(TypeError, match="EffectRelation"):
        append_unique_effect_relation([], cast(Any, "relation:1"), subject="test relations")
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        append_unique_effect_lifecycle_event([], cast(Any, "event:1"), subject="test lifecycle")


def test_effect_graph_merge_helpers_reject_untyped_existing_records() -> None:
    effect, relation, lifecycle = _effect_graph_records()

    with pytest.raises(TypeError, match="EffectRef"):
        append_unique_effect_ref(
            cast(Any, ["effect:old"]),
            effect,
            subject="test source effects",
        )
    with pytest.raises(TypeError, match="EffectRelation"):
        append_unique_effect_relation(
            cast(Any, ["relation:old"]),
            relation,
            subject="test relations",
        )
    with pytest.raises(TypeError, match="EffectLifecycleEvent"):
        append_unique_effect_lifecycle_event(
            cast(Any, ["event:old"]),
            lifecycle,
            subject="test lifecycle",
        )
