from __future__ import annotations

from typing import Any

from lawvm.uk_legislation.effect_temporal import (
    UK_UNDATED_APPLIED_SI_COMMENCEMENT_DATE_RULE_ID,
    UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID,
    resolve_uk_effective_date_overrides_for_replay,
)
from lawvm.uk_legislation.effects import UKEffectRecord


def _undated_applied_si_effect(*, effect_id: str = "uk_test_effect") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2016-06-02",
        affected_uri="/id/ukpga/1996/61",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1996",
        affected_number="61",
        affected_provisions="Sch. 6 Pt. 4",
        affecting_uri="/id/uksi/1999/416",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="1999",
        affecting_number="416",
        affecting_provisions="Sch. 1 para. 18(2)",
        affecting_title="Test Order",
        in_force_dates=[],
    )


class _Archive:
    def __init__(self, payload_by_locator: dict[str, bytes]) -> None:
        self._payload_by_locator = payload_by_locator

    def get(self, locator: str) -> bytes:
        return self._payload_by_locator.get(locator, b"")


def _data_locator() -> str:
    return "https://www.legislation.gov.uk/uksi/1999/416/data.xml"


def test_si_commencement_override_records_single_metadata_date() -> None:
    archive = _Archive(
        {
            _data_locator(): b"""
            <Legislation xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
              <ukm:SecondaryMetadata>
                <ukm:ComingIntoForce>
                  <ukm:DateTime Date="1999-02-20"/>
                </ukm:ComingIntoForce>
              </ukm:SecondaryMetadata>
            </Legislation>
            """
        }
    )

    diagnostics: list[dict[str, Any]] = []
    overrides = resolve_uk_effective_date_overrides_for_replay(
        [_undated_applied_si_effect()],
        archive,
        diagnostics_out=diagnostics,
    )

    assert overrides == {"uk_test_effect": "1999-02-20"}
    assert diagnostics[0]["rule_id"] == UK_UNDATED_APPLIED_SI_COMMENCEMENT_DATE_RULE_ID
    assert diagnostics[0]["temporal_resolution_status"] == "source_backed_override"
    assert diagnostics[0]["effective_date"] == "1999-02-20"
    assert diagnostics[0]["source_locator"] == _data_locator()


def test_si_commencement_override_rejects_multi_date_metadata_with_diagnostic() -> None:
    archive = _Archive(
        {
            _data_locator(): b"""
            <Legislation xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
              <ukm:SecondaryMetadata>
                <ukm:ComingIntoForce>
                  <ukm:DateTime Date="1999-02-20"/>
                  <ukm:DateTime Date="1999-03-01"/>
                </ukm:ComingIntoForce>
              </ukm:SecondaryMetadata>
            </Legislation>
            """
        }
    )

    diagnostics: list[dict[str, Any]] = []
    overrides = resolve_uk_effective_date_overrides_for_replay(
        [_undated_applied_si_effect()],
        archive,
        diagnostics_out=diagnostics,
    )

    assert overrides == {}
    assert diagnostics == [
        {
            "rule_id": UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID,
            "family": "temporal_recovery",
            "phase": "lowering",
            "effect_id": "uk_test_effect",
            "affecting_act_id": "uksi/1999/416",
            "affected_provisions": "Sch. 6 Pt. 4",
            "affecting_provisions": "Sch. 1 para. 18(2)",
            "effect_type": "words substituted",
            "commencement_metadata_status": "multiple_or_textual",
            "commencement_metadata_dates": ("1999-02-20", "1999-03-01"),
            "source_locator": _data_locator(),
            "authority_layer": "AFFECTING_ACT_METADATA",
            "temporal_resolution_status": "unknown_effective_date",
            "reason": (
                "UK effect feed marked this statutory-instrument effect as applied "
                "but omitted an effect-level in-force date; LawVM did not use an SI "
                "commencement fallback because the affecting instrument metadata "
                "does not expose exactly one commencement date."
            ),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_si_commencement_override_rejects_missing_metadata_source_with_diagnostic() -> None:
    diagnostics: list[dict[str, Any]] = []
    overrides = resolve_uk_effective_date_overrides_for_replay(
        [_undated_applied_si_effect()],
        _Archive({}),
        diagnostics_out=diagnostics,
    )

    assert overrides == {}
    assert diagnostics[0]["rule_id"] == UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID
    assert diagnostics[0]["temporal_resolution_status"] == "unknown_effective_date"
    assert diagnostics[0]["commencement_metadata_status"] == "source_xml_unavailable"
    assert diagnostics[0]["commencement_metadata_dates"] == ()
    assert "source_locator" not in diagnostics[0]
    assert "authority_layer" not in diagnostics[0]


def test_si_commencement_override_rejects_textual_metadata_with_diagnostic() -> None:
    archive = _Archive(
        {
            _data_locator(): b"""
            <Legislation xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
              <ukm:SecondaryMetadata>
                <ukm:ComingIntoForce>
                  <ukm:Text>Coming into force in accordance with article 1</ukm:Text>
                </ukm:ComingIntoForce>
              </ukm:SecondaryMetadata>
            </Legislation>
            """
        }
    )

    diagnostics: list[dict[str, Any]] = []
    overrides = resolve_uk_effective_date_overrides_for_replay(
        [_undated_applied_si_effect()],
        archive,
        diagnostics_out=diagnostics,
    )

    assert overrides == {}
    assert diagnostics[0]["rule_id"] == UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID
    assert diagnostics[0]["commencement_metadata_status"] == "textual_or_missing_date"
    assert diagnostics[0]["commencement_metadata_dates"] == ()


def test_si_commencement_override_rejects_unparsable_source_with_diagnostic() -> None:
    diagnostics: list[dict[str, Any]] = []
    overrides = resolve_uk_effective_date_overrides_for_replay(
        [_undated_applied_si_effect()],
        _Archive({_data_locator(): b"<Legislation>"}),
        diagnostics_out=diagnostics,
    )

    assert overrides == {}
    assert diagnostics[0]["rule_id"] == UK_UNDATED_APPLIED_SI_COMMENCEMENT_UNRESOLVED_RULE_ID
    assert diagnostics[0]["commencement_metadata_status"] == "source_xml_parse_error"
    assert diagnostics[0]["commencement_metadata_dates"] == ()
    assert diagnostics[0]["source_locator"] == _data_locator()
    assert diagnostics[0]["authority_layer"] == "AFFECTING_ACT_METADATA"
    assert diagnostics[0]["parse_error"]
