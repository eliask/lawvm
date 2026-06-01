from __future__ import annotations

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.prospective_commencement_witnesses import (
    prospective_commencement_status_counts,
    prospective_commencement_witness_for_effect,
)
from scripts.uk_prospective_commencement_scan import (
    _limited_rows_and_owner_phase_counts,
    _owner_phase_counts,
)


_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _effect(
    *,
    effect_id: str,
    affecting_provisions: str,
    effect_type: str = "repealed",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2026-01-01",
        affected_uri="https://www.legislation.gov.uk/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="https://www.legislation.gov.uk/id/ukpga/2026/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="1",
        affecting_provisions=affecting_provisions,
        affecting_title="Test Act 2026",
        in_force_dates=[{"date": "", "prospective": "true"}],
    )


class _Archive:
    def __init__(self, xml: bytes | None) -> None:
        self.xml = xml

    def get(self, locator: str) -> bytes | None:
        assert locator == "https://www.legislation.gov.uk/ukpga/2026/1/data.xml"
        return self.xml


def _xml() -> bytes:
    return f"""<Legislation xmlns="{_NS}">
      <Body>
        <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/2026/1/section/1"
                 RestrictStartDate="2025-01-01"/>
        <P1group IdURI="http://www.legislation.gov.uk/id/ukpga/2026/1/section/2"
                 RestrictStartDate="2099-01-01"/>
      </Body>
    </Legislation>""".encode()


def test_prospective_commencement_witness_resolves_in_force() -> None:
    witness = prospective_commencement_witness_for_effect(
        "ukpga/2000/1",
        _effect(effect_id="e1", affecting_provisions="s. 1"),
        archive=_Archive(_xml()),
        as_of="2026-05-31",
    )

    assert witness is not None
    row = witness.to_dict()
    assert row["status"] == "resolved_in_force"
    assert row["rule_id"] == "uk_prospective_effect_affecting_provision_in_force"
    assert row["start_dates"] == ("2025-01-01",)
    assert row["owner_phase"] == "effect_metadata_frontend"


def test_prospective_commencement_witness_resolves_future() -> None:
    witness = prospective_commencement_witness_for_effect(
        "ukpga/2000/1",
        _effect(effect_id="e2", affecting_provisions="s. 2"),
        archive=_Archive(_xml()),
        as_of="2026-05-31",
    )

    assert witness is not None
    assert witness.status == "resolved_future"
    assert witness.rule_id == "uk_prospective_effect_affecting_provision_future"


def test_prospective_commencement_witness_preserves_unknown() -> None:
    witness = prospective_commencement_witness_for_effect(
        "ukpga/2000/1",
        _effect(effect_id="e3", affecting_provisions="s. 99"),
        archive=_Archive(_xml()),
        as_of="2026-05-31",
    )

    assert witness is not None
    assert witness.status == "unresolved"
    assert witness.rule_id == "uk_prospective_effect_affecting_provision_unresolved"
    assert witness.start_dates == ()


def test_prospective_commencement_witness_ignores_non_prospective_or_nonstructural() -> None:
    nonstructural = _effect(
        effect_id="e4",
        affecting_provisions="s. 1",
        effect_type="modified",
    )
    assert (
        prospective_commencement_witness_for_effect(
            "ukpga/2000/1",
            nonstructural,
            archive=_Archive(_xml()),
            as_of="2026-05-31",
        )
        is None
    )


def test_prospective_commencement_status_counts() -> None:
    witnesses = [
        prospective_commencement_witness_for_effect(
            "ukpga/2000/1",
            _effect(effect_id="e1", affecting_provisions="s. 1"),
            archive=_Archive(_xml()),
            as_of="2026-05-31",
        ),
        prospective_commencement_witness_for_effect(
            "ukpga/2000/1",
            _effect(effect_id="e2", affecting_provisions="s. 2"),
            archive=_Archive(_xml()),
            as_of="2026-05-31",
        ),
    ]

    assert prospective_commencement_status_counts(w for w in witnesses if w is not None) == {
        "resolved_future": 1,
        "resolved_in_force": 1,
    }


def test_prospective_commencement_owner_phase_counts_use_witness_rows() -> None:
    witness = prospective_commencement_witness_for_effect(
        "ukpga/2000/1",
        _effect(effect_id="e1", affecting_provisions="s. 1"),
        archive=_Archive(_xml()),
        as_of="2026-05-31",
    )

    assert witness is not None
    assert _owner_phase_counts([witness.to_dict()]) == {
        "effect_metadata_frontend": 1,
    }


def test_prospective_commencement_limited_rows_keep_full_owner_phase_counts() -> None:
    rows, counts = _limited_rows_and_owner_phase_counts(
        [
            {"owner_phase": "effect_metadata_frontend", "id": "1"},
            {"owner_phase": "effect_metadata_frontend", "id": "2"},
        ],
        limit=1,
    )

    assert rows == [{"owner_phase": "effect_metadata_frontend", "id": "1"}]
    assert counts == {"effect_metadata_frontend": 2}
