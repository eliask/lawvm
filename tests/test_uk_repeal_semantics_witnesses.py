from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.repeal_no_double_entry import (
    collect_repeal_no_double_entry_groups,
    filter_repeal_no_double_entry_ops,
)
from lawvm.uk_legislation.repeal_semantics_witnesses import (
    UKRepealSemanticsWitness,
    _affecting_act_phrase_effect_witnesses,
    _duplicate_repeal_target_witnesses,
    is_repeal_semantics_effect,
    scan_repeal_semantics_affecting_act_phrase_candidates_for_statute,
    scan_repeal_semantics_source_phrase_xml,
    source_text_repeal_semantics_family,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline


_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"


def _effect(
    *,
    effect_id: str,
    effect_type: str,
    affected_provisions: str = "s. 1",
    affecting_provisions: str = "s. 2",
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
        affected_provisions=affected_provisions,
        affecting_uri="https://www.legislation.gov.uk/id/ukpga/2026/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="1",
        affecting_provisions=affecting_provisions,
        affecting_title="Test Act 2026",
    )


def _repeal_op(effect_id: str, target: str) -> LegalOperation:
    kind, label = target.split(":", 1)
    return LegalOperation(
        op_id=effect_id,
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(((kind, label),)),
        source=OperationSource(statute_id="ukpga/2026/1", effective="2026-01-01"),
        witness_rule_id="uk_effect_repeal_table_structural_repeal",
    )


def test_repeal_semantics_effect_family_includes_repeals_revocations_and_omissions() -> None:
    assert is_repeal_semantics_effect(_effect(effect_id="e1", effect_type="repealed"))
    assert is_repeal_semantics_effect(_effect(effect_id="e2", effect_type="entry omitted"))
    assert is_repeal_semantics_effect(_effect(effect_id="e3", effect_type="revoked"))
    assert not is_repeal_semantics_effect(_effect(effect_id="e4", effect_type="inserted"))


def test_source_text_repeal_semantics_family_detects_no_revive_phrases() -> None:
    assert (
        source_text_repeal_semantics_family("The repeal of a repeal does not revive the enactment.")
        == "repeal_of_repeal_no_revive_phrase"
    )
    assert (
        source_text_repeal_semantics_family("This provision concerns repeal of the repeal made by section 2.")
        == "repeal_of_repeal_phrase"
    )
    assert (
        source_text_repeal_semantics_family("The repeal shall not revive any earlier enactment.")
        == "repeal_of_repeal_no_revive_phrase"
    )
    assert source_text_repeal_semantics_family("Section 1 is repealed.") == ""


def test_source_phrase_xml_scan_reports_text_level_no_revive_witness() -> None:
    witnesses = scan_repeal_semantics_source_phrase_xml(
        "ukpga/2026/1",
        b"""
        <Legislation>
          <Primary>
            <Body>
              <P1>
                <P1para>
                  <Text>The repeal shall not revive any earlier enactment.</Text>
                </P1para>
              </P1>
            </Body>
          </Primary>
        </Legislation>
        """,
        source_locator="source.xml",
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "repeal_of_repeal_no_revive_phrase"
    assert row["rule_id"] == (
        "uk_repeal_semantics_source_phrase_repeal_of_repeal_no_revive_phrase"
    )
    assert row["source_status"] == "source_phrase_scan"
    assert row["source_tag"] == "Text"
    assert row["source_locator"] == "source.xml"


def test_duplicate_repeal_target_witness_requires_multiple_affecting_provisions() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 2"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="Sch. 1"),
        ),
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "duplicate_repeal_target_candidate"
    assert row["rule_id"] == "uk_repeal_semantics_duplicate_target_candidate"
    assert row["duplicate_count"] == 2
    assert row["related_effect_ids"] == ("e1", "e2")
    assert row["related_affecting_provisions"] == ("Sch. 1", "s. 2")


def test_duplicate_repeal_target_witness_classifies_body_schedule_double_entry() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 192(6) Sch. 13"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="Sch. 13"),
        ),
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "body_schedule_repeal_double_entry_candidate"
    assert row["rule_id"] == "uk_repeal_semantics_body_schedule_double_entry_candidate"


def test_duplicate_repeal_target_witness_ignores_same_source_duplicate() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 2"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="s. 2"),
        ),
    )

    assert witnesses == ()


def test_affecting_act_phrase_candidate_links_phrase_act_to_repeal_effect() -> None:
    phrase = UKRepealSemanticsWitness(
        family="repeal_of_repeal_no_revive_phrase",
        statute_id="ukpga/2026/1",
        effect_id="",
        effect_type="",
        affected_provisions="",
        affecting_act_id="ukpga/2026/1",
        affecting_provisions="",
        rule_id="uk_repeal_semantics_source_phrase_repeal_of_repeal_no_revive_phrase",
        source_status="source_phrase_scan",
        source_tag="Text",
        source_text_preview="The repeal does not revive any earlier enactment.",
        detail={"source_locator": "source.xml"},
    )

    witnesses = _affecting_act_phrase_effect_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed"),
            _effect(effect_id="e2", effect_type="inserted"),
        ),
        {"ukpga/2026/1": (phrase,)},
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "affecting_act_repeal_of_repeal_no_revive_phrase_candidate"
    assert row["rule_id"] == (
        "uk_repeal_semantics_affecting_act_repeal_of_repeal_no_revive_phrase_candidate"
    )
    assert row["effect_id"] == "e1"
    assert row["source_status"] == "affecting_act_source_phrase_candidate"
    assert row["source_phrase_rule_id"] == phrase.rule_id
    assert row["source_phrase_count"] == 1
    assert row["source_locator"] == "source.xml"


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present - skipping live selected-source audit",
)
def test_affecting_act_phrase_candidate_audits_selected_source_without_proving_phrase() -> None:
    from farchive import Farchive

    phrase = UKRepealSemanticsWitness(
        family="repeal_of_repeal_no_revive_phrase",
        statute_id="ukpga/2006/50",
        effect_id="",
        effect_type="",
        affected_provisions="",
        affecting_act_id="ukpga/2006/50",
        affecting_provisions="",
        rule_id="uk_repeal_semantics_source_phrase_repeal_of_repeal_no_revive_phrase",
        source_status="source_phrase_scan",
        source_tag="Text",
        source_text_preview="The repeal by this Act does not revive any enactment.",
        detail={"source_locator": "https://www.legislation.gov.uk/ukpga/2006/50/data.xml"},
    )
    diagnostics: list[dict[str, object]] = []

    with Farchive(_DB_PATH) as archive:
        witnesses = scan_repeal_semantics_affecting_act_phrase_candidates_for_statute(
            "ukpga/1992/41",
            archive,
            phrase_witnesses_by_act={"ukpga/2006/50": (phrase,)},
            audit_selected_source=True,
            diagnostics_out=diagnostics,
        )

    rows = [witness.to_dict() for witness in witnesses]
    no_revive_rows = [
        row
        for row in rows
        if row["family"] == "affecting_act_repeal_of_repeal_no_revive_phrase_candidate"
    ]
    assert no_revive_rows
    assert any(row["selected_source_matches_phrase"] is False for row in no_revive_rows)
    assert any(row["selected_source_tag"] == "Schedule" for row in no_revive_rows)
    assert diagnostics


def test_no_double_entry_filter_rejects_only_exact_duplicate_repeal_ops() -> None:
    effects = (
        _effect(effect_id="body", effect_type="repealed", affecting_provisions="s. 192(6) Sch. 13"),
        _effect(effect_id="schedule", effect_type="repealed", affecting_provisions="Sch. 13"),
        _effect(effect_id="other", effect_type="repealed", affecting_provisions="s. 9"),
    )
    groups = collect_repeal_no_double_entry_groups(effects)
    diagnostics: list[dict[str, object]] = []

    filtered = filter_repeal_no_double_entry_ops(
        (
            _repeal_op("schedule", "section:203"),
            _repeal_op("body", "section:203"),
            _repeal_op("body", "section:204"),
            _repeal_op("other", "section:203"),
        ),
        groups,
        diagnostics_out=diagnostics,
    )

    assert [op.op_id for op in filtered] == ["schedule", "body", "other"]
    assert [str(op.target) for op in filtered] == ["section:203", "section:204", "section:203"]
    assert diagnostics == [
        {
            "rule_id": "uk_effect_repeal_no_double_entry_duplicate_rejected",
            "phase": "lowering",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
            "family": "repeal_no_double_entry",
            "reason": (
                "same repeal target was emitted by both a body provision and "
                "the referenced repeal Schedule; keeping the first operation"
            ),
            "effect_id": "body",
            "kept_effect_id": "schedule",
            "action": "repeal",
            "target": "section:203",
            "affecting_act_id": "ukpga/2026/1",
            "affected_provisions": "s. 1",
            "related_effect_ids": ["body", "schedule"],
            "related_affecting_provisions": ["Sch. 13", "s. 192(6) Sch. 13"],
            "witness_rule_id": "uk_effect_repeal_table_structural_repeal",
        }
    ]


def test_no_double_entry_filter_ignores_non_body_schedule_duplicates() -> None:
    effects = (
        _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 2"),
        _effect(effect_id="e2", effect_type="repealed", affecting_provisions="s. 3"),
    )
    diagnostics: list[dict[str, object]] = []

    filtered = filter_repeal_no_double_entry_ops(
        (_repeal_op("e1", "section:203"), _repeal_op("e2", "section:203")),
        collect_repeal_no_double_entry_groups(effects),
        diagnostics_out=diagnostics,
    )

    assert [op.op_id for op in filtered] == ["e1", "e2"]
    assert diagnostics == []


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present - skipping live repeal no-double-entry regression",
)
def test_pipeline_filters_real_body_schedule_double_entry_repeals() -> None:
    from farchive import Farchive

    diagnostics: list[dict[str, object]] = []
    with Farchive(_DB_PATH) as archive:
        ops = UKReplayPipeline(Path(".")).compile_ops_for_statute(
            "ukpga/1990/8",
            archive=archive,
            lowering_rejections_out=diagnostics,
        )

    planning_repeals = [
        op
        for op in ops
        if op.source
        and op.source.statute_id == "ukpga/2008/29"
        and op.action is StructuralAction.REPEAL
        and str(op.target) in {"section:203", "section:204", "section:205"}
    ]
    assert sorted(str(op.target) for op in planning_repeals) == [
        "section:203",
        "section:204",
        "section:205",
    ]
    no_double_entry_rows = [
        row
        for row in diagnostics
        if row.get("rule_id") == "uk_effect_repeal_no_double_entry_duplicate_rejected"
    ]
    assert len(no_double_entry_rows) == 3
    assert {row["target"] for row in no_double_entry_rows} == {
        "section:203",
        "section:204",
        "section:205",
    }
