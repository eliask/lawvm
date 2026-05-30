"""Sensor for UK prospective (uncommenced) structural effects applied to current."""
from __future__ import annotations

from types import SimpleNamespace

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.prospective_effect_warrant import (
    PROSPECTIVE_EFFECT_APPLIED_RULE_ID,
    collect_prospective_effect_observations,
    prospective_effect_applied_observation,
)


def _effect(in_force_dates, *, effect_type: str = "inserted") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="e1",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 5",
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 1",
        affecting_title="Test Act 2024",
        in_force_dates=in_force_dates,
    )


class TestIsProspectiveOnly:
    def test_all_prospective_is_prospective_only(self) -> None:
        e = _effect([{"date": "", "applied": "true", "prospective": "true"}])
        assert e.is_prospective_only is True

    def test_real_date_is_not_prospective_only(self) -> None:
        e = _effect([{"date": "2011-11-10", "applied": "true", "prospective": "false"}])
        assert e.is_prospective_only is False

    def test_mixed_with_one_real_date_is_not_prospective_only(self) -> None:
        e = _effect(
            [
                {"date": "", "applied": "true", "prospective": "true"},
                {"date": "2011-11-10", "applied": "true", "prospective": "false"},
            ]
        )
        assert e.is_prospective_only is False

    def test_no_date_metadata_is_not_prospective_only(self) -> None:
        # absent date metadata is handled by other lanes, not a prospective signal
        assert _effect([]).is_prospective_only is False


class TestSensor:
    def test_emits_for_applied_prospective_structural_effect(self) -> None:
        e = _effect([{"date": "", "prospective": "true"}], effect_type="inserted")
        obs = prospective_effect_applied_observation(e)
        assert obs is not None
        assert obs["rule_id"] == PROSPECTIVE_EFFECT_APPLIED_RULE_ID
        assert obs["blocking"] is False

    def test_does_not_emit_for_commenced_effect(self) -> None:
        e = _effect([{"date": "2011-11-10", "prospective": "false"}], effect_type="inserted")
        assert prospective_effect_applied_observation(e) is None

    def test_does_not_emit_for_non_structural_effect(self) -> None:
        # a non-structural effect type is out of scope even if prospective-only
        e = _effect([{"date": "", "prospective": "true"}], effect_type="applied")
        assert prospective_effect_applied_observation(e) is None

    def test_collect_emits_only_for_prospective(self) -> None:
        effects = [
            _effect([{"date": "", "prospective": "true"}], effect_type="inserted"),  # emit
            _effect([{"date": "2011-11-10", "prospective": "false"}], effect_type="inserted"),  # commenced
            _effect([{"date": "", "prospective": "true"}], effect_type="applied"),  # non-structural
        ]
        obs = collect_prospective_effect_observations(effects)
        assert len(obs) == 1
        assert obs[0]["rule_id"] == PROSPECTIVE_EFFECT_APPLIED_RULE_ID

    def test_sensor_accepts_duck_typed_effect(self) -> None:
        stub = SimpleNamespace(
            is_prospective_only=True,
            is_structural=True,
            effect_type="omitted",
            affected_provisions="s. 7",
            affecting_act_id="ukpga/2024/99",
            in_force_dates=[{"date": "", "prospective": "true"}],
        )
        obs = prospective_effect_applied_observation(stub)
        assert obs is not None
        assert obs["affected_provisions"] == "s. 7"
