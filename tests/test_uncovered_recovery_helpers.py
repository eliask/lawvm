"""Isolation tests for pure helpers extracted from uncovered-body recovery.

These cover the stateless label/heading/part helpers lifted out of the
``_recover_uncovered_body_ops`` closure cascade so they can be tested without
constructing a full ReplayState.
"""
from __future__ import annotations

import lxml.etree as etree

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.grafter_uncovered import (
    PreGuardVerdict,
    RecoveryState,
    UncoveredCandidateAudit,
    UncoveredRecoveryGuards,
    _evaluate_pre_guards,
    _next_letter_label,
    _part_label_from_path,
    _section_heading_text,
    _uncovered_disposition_for_op_id,
    _uncovered_section_key,
    _xml_part_label,
)


def _section_with_heading(text: str) -> IRNode:
    heading = IRNode(kind=IRNodeKind.HEADING, text=text)
    return IRNode(kind=IRNodeKind.SECTION, label="1", children=(heading,))


def test_section_heading_text_normalizes_and_lowercases() -> None:
    node = _section_with_heading("  Voimaantulo   Säännös ")
    assert _section_heading_text(node) == "voimaantulo säännös"


def test_section_heading_text_empty_when_no_heading() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, label="1", children=())
    assert _section_heading_text(node) == ""


def test_next_letter_label_bare_number_gets_a() -> None:
    assert _next_letter_label("18") == "18a"


def test_next_letter_label_advances_suffix() -> None:
    assert _next_letter_label("18a") == "18b"


def test_next_letter_label_stops_at_z() -> None:
    assert _next_letter_label("18z") is None


def test_next_letter_label_rejects_non_numeric() -> None:
    assert _next_letter_label("foo") is None


def test_xml_part_label_walks_to_part_ancestor() -> None:
    root = etree.fromstring(
        b"<part><num>II OSA</num><chapter><num>3 luku</num>"
        b"<section><num>5 \xc2\xa7</num></section></chapter></part>"
    )
    sec = root.find(".//section")
    assert sec is not None
    # Normalized part label (roman/normalized); just assert it is non-None and stable.
    assert _xml_part_label(sec) is not None


def test_xml_part_label_none_without_part_ancestor() -> None:
    root = etree.fromstring(b"<chapter><num>3 luku</num><section><num>5</num></section></chapter>")
    sec = root.find(".//section")
    assert sec is not None
    assert _xml_part_label(sec) is None


def test_part_label_from_path_finds_part() -> None:
    path = (("part", "2"), ("chapter", "3"), ("section", "5"))
    assert _part_label_from_path(path) == "2"


def test_part_label_from_path_none_when_absent() -> None:
    assert _part_label_from_path((("chapter", "3"), ("section", "5"))) is None
    assert _part_label_from_path(None) is None


# --- UncoveredCandidateAudit invariants ---


def test_audit_requires_section() -> None:
    with pytest.raises(ValueError, match="section"):
        UncoveredCandidateAudit(section="", chapter="", part="", disposition="SKIP", reason="x")


def test_audit_recovered_requires_op_id() -> None:
    with pytest.raises(ValueError, match="op_id"):
        UncoveredCandidateAudit(
            section="5", chapter="3", part="", disposition="INSERT", reason="x", op_id=""
        )


def test_audit_skip_allows_empty_op_id() -> None:
    audit = UncoveredCandidateAudit(
        section="5", chapter="3", part="", disposition="SKIP", reason="cross_chapter"
    )
    assert audit.op_id == ""


def test_audit_replace_with_op_id_ok() -> None:
    audit = UncoveredCandidateAudit(
        section="5", chapter="3", part="", disposition="REPLACE", reason="r", op_id="uncovered_replace_5"
    )
    assert audit.disposition == "REPLACE"


# --- _uncovered_disposition_for_op_id mapping ---


def test_disposition_for_op_id_maps_known_prefixes() -> None:
    assert _uncovered_disposition_for_op_id("uncov_chapter_adopt_5")[0] == "ADOPT"
    assert _uncovered_disposition_for_op_id("uncovered_replace_5")[0] == "REPLACE"
    assert _uncovered_disposition_for_op_id("uncovered_merge_5")[0] == "MERGE"
    assert _uncovered_disposition_for_op_id("uncovered_insert_5")[0] == "INSERT"


def test_disposition_for_op_id_falls_back_to_insert() -> None:
    disposition, reason = _uncovered_disposition_for_op_id("mystery_op")
    assert disposition == "INSERT"
    assert reason == "recovered"


# --- RecoveryState skip + audit bookkeeping ---


def _empty_state(findings_out: list | None) -> RecoveryState:
    guards = UncoveredRecoveryGuards(
        covered_sections=set(),
        chapter_payload_owned_sections=set(),
        relabel_destination_sections=set(),
    )
    return RecoveryState(
        amendment_id="2020/1",
        op_source=None,
        findings_out=findings_out,
        guards=guards,
    )


def test_record_skip_appends_audit_and_dedups_findings() -> None:
    findings: list = []
    rstate = _empty_state(findings)
    rstate.record_skip("cross_chapter", "5", "3", None)
    rstate.record_skip("cross_chapter", "5", "3", None)  # duplicate finding
    # One de-duplicated finding, but both calls leave an audit trail entry.
    assert len(findings) == 1
    assert len(rstate.audits) == 2
    assert all(a.disposition == "SKIP" for a in rstate.audits)


def test_record_skip_audit_without_findings_sink() -> None:
    rstate = _empty_state(None)
    rstate.record_skip("johto_guard", "7", "2", None)
    # Audit trail records even when no findings sink is provided.
    assert len(rstate.audits) == 1
    assert rstate.audits[0].section == "7"


def test_mark_covered_then_already_recovered_independent() -> None:
    rstate = _empty_state([])
    rstate.mark_covered(part=None, chapter="3", section="5")
    # mark_covered touches guards, not the recovered-key set.
    assert rstate.guards.is_covered(part=None, chapter="3", section="5")
    assert not rstate.already_recovered(section="5", chapter="3")


def test_chapter_disposition_mixed_finding() -> None:
    findings: list = []
    rstate = _empty_state(findings)
    rstate.note_chapter_disposition("4", "adopted")
    rstate.note_chapter_disposition("4", "owned")
    rstate.note_chapter_disposition("5", "owned")  # only owned → no mixed finding
    rstate.emit_chapter_payload_mixed_findings()
    assert len(findings) == 1  # only chapter 4 has both adopted and owned


# --- PreGuardVerdict invariants + _evaluate_pre_guards ---


def test_pre_guard_verdict_proceed_rejects_reason() -> None:
    with pytest.raises(ValueError, match="must not carry"):
        PreGuardVerdict(True, "x", with_part=False)


def test_pre_guard_verdict_block_requires_reason() -> None:
    with pytest.raises(ValueError, match="must name"):
        PreGuardVerdict(False, None, with_part=False)


def _empty_guards() -> UncoveredRecoveryGuards:
    return UncoveredRecoveryGuards(
        covered_sections=set(),
        chapter_payload_owned_sections=set(),
        relabel_destination_sections=set(),
    )


def test_pre_guards_proceed_when_clean() -> None:
    verdict = _evaluate_pre_guards(
        label="5",
        amend_chapter_label="3",
        amend_part_label=None,
        guards=_empty_guards(),
        already_recovered=False,
        moved_section_destinations={},
        bp_assignments=None,
    )
    assert verdict.proceed is True
    assert verdict.skip_reason is None


def test_pre_guards_block_already_recovered() -> None:
    verdict = _evaluate_pre_guards(
        label="5",
        amend_chapter_label="3",
        amend_part_label=None,
        guards=_empty_guards(),
        already_recovered=True,
        moved_section_destinations={},
        bp_assignments=None,
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "duplicate_recovered_candidate"


def test_pre_guards_block_moved_destination_mismatch() -> None:
    verdict = _evaluate_pre_guards(
        label="5",
        amend_chapter_label="3",
        amend_part_label=None,
        guards=_empty_guards(),
        already_recovered=False,
        moved_section_destinations={"5": "7"},  # moved to chapter 7, not 3
        bp_assignments=None,
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "moved_destination_mismatch"


def test_pre_guards_moved_to_declared_chapter_proceeds() -> None:
    verdict = _evaluate_pre_guards(
        label="5",
        amend_chapter_label="7",
        amend_part_label=None,
        guards=_empty_guards(),
        already_recovered=False,
        moved_section_destinations={"5": "7"},  # declared chapter matches destination
        bp_assignments=None,
    )
    assert verdict.proceed is True


def test_pre_guards_block_relabel_destination_carries_part() -> None:
    guards = UncoveredRecoveryGuards(
        covered_sections=set(),
        chapter_payload_owned_sections=set(),
        relabel_destination_sections={
            _uncovered_section_key(part=None, chapter="3", section="5")
        },
    )
    verdict = _evaluate_pre_guards(
        label="5",
        amend_chapter_label="3",
        amend_part_label=None,
        guards=guards,
        already_recovered=False,
        moved_section_destinations={},
        bp_assignments=None,
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "same_wave_relabel_destination_owned"
    assert verdict.with_part is True
