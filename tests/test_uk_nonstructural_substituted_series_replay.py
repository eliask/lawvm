"""Tests for admitting nonstructural UK "substituted for ..." rows that lower to all inserts.

When the UK effects feed labels a whole-series substitution as nonstructural
(e.g. "substituted for ss. 6-9") and every target in the series is a new
letter-suffix provision, lowering promotes every target to an after-anchor
insert.  Previously the replay-applicability filter rejected these rows because
the head operation was an insert, not a replace.  The fix admits the whole series
and emits an owned lowering observation.
"""
from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_substitution_normalization import (
    UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID,
)
from lawvm.uk_legislation.replay_applicability import (
    UK_EFFECT_NONSTRUCTURAL_SUBSTITUTED_SERIES_ALL_INSERTS_ADMITTED_RULE_ID,
    should_replay_nonstructural_ops,
)


_RULE_ID = UK_EFFECT_NONSTRUCTURAL_SUBSTITUTED_SERIES_ALL_INSERTS_ADMITTED_RULE_ID


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _effect(*, effect_type: str = "substituted for ss. 1-4") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-nssa-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1A-1D",
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 2",
        affecting_title="Test Amending Act 2024",
        in_force_dates=[{"date": "2024-02-01", "prospective": "false"}],
    )


def _insert_op(
    label: str,
    op_id: str = "op",
    kind: IRNodeKind = IRNodeKind.SECTION,
    witness_rule_id: str | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=((kind.value, label),)),
        payload=IRNode(kind=kind, label=label, text=f"Text for {label}."),
        source=_source(),
        sequence=1,
        witness_rule_id=witness_rule_id,
    )


def _replace_op(label: str, op_id: str = "op") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text="Replacement."),
        source=_source(),
        sequence=1,
    )


def test_all_insert_series_is_admitted_with_observation() -> None:
    """A "substituted for ..." row with only insert ops is replayable."""
    effect = _effect()
    ops = [
        _insert_op("1A", "op-0"),
        _insert_op("1B", "op-1"),
        _insert_op("1C", "op-2"),
    ]
    observations: list[dict[str, Any]] = []

    assert should_replay_nonstructural_ops(
        effect, ops, lowering_observations_out=observations
    ) is True

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _RULE_ID in rule_ids, f"Expected {_RULE_ID!r} in {rule_ids!r}"

    observation = next(obs for obs in observations if obs.get("rule_id") == _RULE_ID)
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"
    assert observation["quirks_disposition"] == "apply"
    assert observation.get("compiled_op_count") == 3


def test_all_insert_series_admitted_without_observation_list() -> None:
    """Admission works even when caller does not request observations."""
    effect = _effect()
    ops = [_insert_op("1A")]
    assert should_replay_nonstructural_ops(effect, ops) is True


def test_head_replace_plus_owned_insert_tail_does_not_emit_all_insert_observation() -> None:
    """Existing head-replace path with owned tail inserts still works."""
    effect = _effect()
    ops = [
        _replace_op("1"),
        _insert_op("1A", witness_rule_id=UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
        _insert_op("1B", witness_rule_id=UK_EFFECT_AFTER_ANCHOR_INSERT_PROMOTED_RULE_ID),
    ]
    observations: list[dict[str, Any]] = []

    assert should_replay_nonstructural_ops(
        effect, ops, lowering_observations_out=observations
    ) is True

    assert _RULE_ID not in {obs.get("rule_id") for obs in observations}


def test_word_substitution_all_inserts_not_admitted() -> None:
    """Word-level substitutions stay excluded even if they lower to inserts."""
    effect = _effect(effect_type="substituted for words")
    ops = [_insert_op("1A")]
    assert should_replay_nonstructural_ops(effect, ops) is False


def test_insert_without_payload_not_admitted() -> None:
    """A malformed insert without payload does not trigger admission."""
    effect = _effect()
    op = LegalOperation(
        op_id="op",
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "1A"),)),
        payload=None,
        source=_source(),
        sequence=1,
    )
    assert should_replay_nonstructural_ops(effect, [op]) is False


def test_non_substituted_effect_all_inserts_not_admitted() -> None:
    """Other effect types (e.g. revoked) with only inserts are still rejected."""
    effect = _effect(effect_type="revoked")
    ops = [_insert_op("1A")]
    observations: list[dict[str, Any]] = []
    assert should_replay_nonstructural_ops(
        effect, ops, lowering_observations_out=observations
    ) is False
    assert _RULE_ID not in {obs.get("rule_id") for obs in observations}
