from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lxml import etree as ET

from lawvm.uk_legislation.effects import UKEffectRecord
from scripts import uk_oracle_extra_source_fragments as fragments


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_source_fragment_supplement_matches_change_id_to_effect(
    tmp_path,
    monkeypatch,
) -> None:
    review_path = _write_json(
        tmp_path / "review.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/1980/60",
                    "target": "section-6-1a",
                    "oracle_change_ids": ["d30p378-1720000000000"],
                    "oracle_commentaries": [
                        "S. 6(1A) inserted by S.I. 1988/1984 , art. 3(3) , Sch. para. 1(2)"
                    ],
                }
            ]
        },
    )
    effect = UKEffectRecord(
        effect_id="d30p378",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="1988-09-01",
        affected_uri="/id/ukpga/1980/60/section/6",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1980",
        affected_number="60",
        affected_provisions="s. 6(1A)",
        affecting_uri="https://www.legislation.gov.uk/id/uksi/1988/1984",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="1988",
        affecting_number="1984",
        affecting_provisions="art. 3(3), Sch. para. 1(2)",
        affecting_title="Test Regulations 1988",
        in_force_dates=[{"date": "1988-09-01", "prospective": "false"}],
    )

    def fake_load_effects(statute_id, archive):
        assert statute_id == "ukpga/1980/60"
        assert archive == "archive"
        return [effect]

    source_el = ET.fromstring(
        b"<Article><Text>In section 6, after subsection (1) insert subsection (1A).</Text></Article>"
    )

    def fake_select_source_for_effect(**kwargs):
        assert kwargs["effect"] is effect
        return SimpleNamespace(
            extracted_el=source_el,
            source_context=SimpleNamespace(
                xml_bytes=b"<Legislation>source</Legislation>",
                locator="https://www.legislation.gov.uk/uksi/1988/1984/data.xml",
                authority_layer="AFFECTING_ACT_TEXT",
            ),
        )

    monkeypatch.setattr(fragments, "load_effects_for_statute_from_archive", fake_load_effects)
    monkeypatch.setattr(fragments, "select_source_for_effect", fake_select_source_for_effect)

    rows = fragments.build_supplement_rows(review_path, archive="archive")

    assert len(rows) == 1
    assert rows[0].statute_id == "ukpga/1980/60"
    assert rows[0].retained_targets == ("section-6-1a",)
    assert rows[0].unresolved_change_ids == ()
    op = rows[0].matched_ops[0]
    assert op["effect_id"] == "d30p378"
    assert op["oracle_change_ids"] == ["d30p378-1720000000000"]
    assert op["source_statute"] == "uksi/1988/1984"
    assert op["affecting_provisions"] == "art. 3(3), Sch. para. 1(2)"
    assert op["source_fragment_role"] == (
        "oracle_changeid_effect_feed_affecting_source_fragment"
    )
    assert op["source_fragment_authority_layer"] == "AFFECTING_ACT_TEXT"
    assert op["source_preview"] == (
        "In section 6, after subsection (1) insert subsection (1A)."
    )
    assert op["affecting_source_sha256"]
    assert "does not prove commencement" in op["proof_boundary"]


def test_source_fragment_supplement_reports_unresolved_change_id(
    tmp_path,
    monkeypatch,
) -> None:
    review_path = _write_json(
        tmp_path / "review.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/1980/60",
                    "target": "section-6-1a",
                    "oracle_change_ids": ["missing-change"],
                }
            ]
        },
    )

    monkeypatch.setattr(
        fragments,
        "load_effects_for_statute_from_archive",
        lambda statute_id, archive: [],
    )

    rows = fragments.build_supplement_rows(review_path, archive=object())
    payload = json.loads(fragments.emit_json(rows))

    assert rows[0].matched_ops == ()
    assert rows[0].unresolved_change_ids == ("missing-change",)
    assert payload["truth_claim"] == "source_fragment_supplement_not_replay_authority"
    assert payload["replay_claims"] is False
    assert payload["summary"]["matched_operation_count"] == 0
    assert payload["summary"]["unresolved_change_id_count"] == 1
