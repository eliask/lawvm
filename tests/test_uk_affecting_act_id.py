"""Affecting-act id derivation for UK effects.

The effect ``AffectingURI`` carries the authoritative document slug. The
class-name map cannot enumerate every legislation type — e.g. ``NorthernIrelandAct``
has no entry and would fall back to the invalid slug ``northernirelandact`` (a 404),
when the correct slug is ``nia``. Prefer the URI when present.
"""
from __future__ import annotations

from lawvm.uk_legislation.effects import UKEffectRecord


def _record(*, affecting_class: str, affecting_uri: str, year: str, number: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="e",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="",
        affected_class="",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri=affecting_uri,
        affecting_class=affecting_class,
        affecting_year=year,
        affecting_number=number,
        affecting_provisions="",
        affecting_title="",
    )


class TestAffectingActIdFromUri:
    def test_northern_ireland_act_uses_uri_slug_not_classname(self) -> None:
        rec = _record(
            affecting_class="NorthernIrelandAct",
            affecting_uri="http://www.legislation.gov.uk/id/nia/2016/10",
            year="2016",
            number="10",
        )
        # Without the URI preference this would be the invalid "northernirelandact/2016/10".
        assert rec.affecting_act_id == "nia/2016/10"

    def test_uri_without_id_segment(self) -> None:
        rec = _record(
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_uri="http://www.legislation.gov.uk/ukpga/2006/35",
            year="2006",
            number="35",
        )
        assert rec.affecting_act_id == "ukpga/2006/35"

    def test_uri_agrees_with_classmap_for_known_class(self) -> None:
        rec = _record(
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_uri="http://www.legislation.gov.uk/id/ukpga/2023/28",
            year="2023",
            number="28",
        )
        assert rec.affecting_act_id == "ukpga/2023/28"

    def test_classmap_fallback_when_no_uri(self) -> None:
        rec = _record(
            affecting_class="ScottishAct",
            affecting_uri="",
            year="2000",
            number="6",
        )
        assert rec.affecting_act_id == "asp/2000/6"

    def test_unknown_class_without_uri_still_lowercases(self) -> None:
        rec = _record(
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_uri="",
            year="1968",
            number="60",
        )
        assert rec.affecting_act_id == "ukpga/1968/60"


class TestAffectingClassIsRecognized:
    def test_uri_makes_unmapped_class_recognized(self) -> None:
        rec = _record(
            affecting_class="NorthernIrelandAct",
            affecting_uri="http://www.legislation.gov.uk/id/nia/2016/10",
            year="2016",
            number="10",
        )
        assert rec.affecting_class_is_recognized is True

    def test_mapped_class_without_uri_recognized(self) -> None:
        rec = _record(
            affecting_class="ScottishAct", affecting_uri="", year="2000", number="6"
        )
        assert rec.affecting_class_is_recognized is True

    def test_unmapped_class_without_uri_not_recognized(self) -> None:
        # This is the loud case: a guessed "northernirelandact" slug that 404s.
        rec = _record(
            affecting_class="NorthernIrelandAct", affecting_uri="", year="2016", number="10"
        )
        assert rec.affecting_class_is_recognized is False
        assert rec.affecting_act_id == "northernirelandact/2016/10"


class TestClassUnmappedDiagnostic:
    def test_diagnostic_shape(self) -> None:
        from lawvm.uk_legislation.source_state import (
            uk_affecting_act_class_unmapped_rejection,
        )

        row = uk_affecting_act_class_unmapped_rejection(
            effect_id="e1",
            affecting_act_id="northernirelandact/2016/10",
            locator="https://www.legislation.gov.uk/northernirelandact/2016/10/data.xml",
            affecting_class="NorthernIrelandAct",
        )
        assert row["rule_id"] == "uk_affecting_act_class_unmapped_rejected"
        assert row["blocking"] is True
        assert row["affecting_class"] == "NorthernIrelandAct"
