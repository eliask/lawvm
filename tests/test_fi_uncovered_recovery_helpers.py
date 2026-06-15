"""Isolation tests for pure helpers extracted from uncovered-body recovery.

These cover the stateless label/heading/part helpers lifted out of the
``_recover_uncovered_body_ops`` closure cascade so they can be tested without
constructing a full ReplayState.
"""
from __future__ import annotations

from typing import Any, cast

import lxml.etree as etree

import pytest

from lawvm.core.ir import IRNode, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.grafter_uncovered import (
    ChapterPayloadOutcome,
    ChapterPayloadOwnershipRequest,
    FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID,
    PreGuardRequest,
    PreGuardVerdict,
    RecoveryState,
    UncoveredCandidateAudit,
    UncoveredChapterScaffoldDraft,
    UncoveredRecoveryGuards,
    _UncoveredRecoveryRun,
    build_uncovered_chapter_scaffold_lo,
    _evaluate_chapter_payload_ownership,
    _evaluate_pre_guards,
    _next_letter_label,
    _part_label_from_path,
    _section_heading_text,
    _uncovered_disposition_for_op_id,
    _uncovered_section_key,
    _xml_part_label,
)
from lawvm.finland.future_repeal import RepealTargetRef


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


def test_uncovered_chapter_scaffold_lo_has_stable_witness_rule_id() -> None:
    payload = IRNode(kind=IRNodeKind.CHAPTER, label="7a")
    source = OperationSource(statute_id="2020/1", effective="2020-01-01")
    lo = build_uncovered_chapter_scaffold_lo(
        UncoveredChapterScaffoldDraft(
            op_id="pseudo_chapter_create_root_7a",
            path=(("chapter", "7a"),),
            payload=payload,
            source=source,
            amendment_id="2020/1",
        )
    )

    assert lo.action is StructuralAction.INSERT
    assert lo.target.path == (("chapter", "7a"),)
    assert lo.payload is payload
    assert lo.source is source
    assert lo.group_id == "finland-johto:2020/1"
    assert lo.witness_rule_id == FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID


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


# --- _UncoveredRecoveryRun decision helpers ---


class _MinimalRunState:
    duplicate_section_labels: set[str] = set()
    ir = IRNode(kind=IRNodeKind.BODY)

    def find_section_path(
        self,
        label: str,
        chapter_num: str | None = None,
        *_args: object,
    ) -> None:
        return None


def _empty_run(
    *,
    future_repeals: set[RepealTargetRef] | None = None,
    johto_mentioned_labels: set[str] | None = None,
    johto_mentioned_replaced_chapters: set[str] | None = None,
    owned_chapter_labels: set[str] | None = None,
) -> _UncoveredRecoveryRun:
    rstate = _empty_state(None)
    return _UncoveredRecoveryRun(
        state=cast(Any, _MinimalRunState()),
        ops=[],
        amendment_id="2020/1",
        future_repeals=future_repeals,
        new_chapter_labels=None,
        has_content_ops=True,
        rstate=rstate,
        recovery_guards=rstate.guards,
        bp_assignments=None,
        johto_mentioned_labels=johto_mentioned_labels or set(),
        johto_mentioned_replaced_chapters=johto_mentioned_replaced_chapters or set(),
        moved_section_destinations={},
        owned_chapter_labels=owned_chapter_labels or set(),
    )


def test_recovery_run_future_repeal_matches_unscoped_section() -> None:
    run = _empty_run(future_repeals={RepealTargetRef.section("5")})
    assert run.is_future_repealed("5", None)
    assert run.is_future_repealed("5", "3")


def test_recovery_run_future_repeal_matches_chapter_qualified_section() -> None:
    run = _empty_run(future_repeals={RepealTargetRef.section("5", "3")})
    assert run.is_future_repealed("5", "3")
    assert not run.is_future_repealed("5", "4")
    assert not run.is_future_repealed("5", None)


def test_recovery_run_future_repeal_ignores_whole_chapter_repeal() -> None:
    run = _empty_run(future_repeals={RepealTargetRef.chapter("3")})
    assert not run.is_future_repealed("5", "3")


def test_recovery_run_label_gate_allows_base_label_suffix() -> None:
    run = _empty_run(johto_mentioned_labels={"32"})
    assert run.label_allowed_by_johto("32a", None)


def test_recovery_run_label_gate_allows_owned_or_replaced_chapter() -> None:
    run = _empty_run(
        johto_mentioned_labels={"32"},
        johto_mentioned_replaced_chapters={"7"},
        owned_chapter_labels={"4"},
    )
    assert run.label_allowed_by_johto("99", "4")
    assert run.label_allowed_by_johto("99", "7")


def test_recovery_run_label_gate_blocks_unmentioned_unowned_section() -> None:
    run = _empty_run(johto_mentioned_labels={"32"}, owned_chapter_labels={"4"})
    assert not run.label_allowed_by_johto("99", "5")


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
        PreGuardRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_empty_guards(),
            already_recovered=False,
            moved_section_destinations={},
            bp_assignments=None,
        )
    )
    assert verdict.proceed is True
    assert verdict.skip_reason is None


def test_pre_guards_block_already_recovered() -> None:
    verdict = _evaluate_pre_guards(
        PreGuardRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_empty_guards(),
            already_recovered=True,
            moved_section_destinations={},
            bp_assignments=None,
        )
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "duplicate_recovered_candidate"


def test_pre_guards_block_moved_destination_mismatch() -> None:
    verdict = _evaluate_pre_guards(
        PreGuardRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_empty_guards(),
            already_recovered=False,
            moved_section_destinations={"5": "7"},  # moved to chapter 7, not 3
            bp_assignments=None,
        )
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "moved_destination_mismatch"


def test_pre_guards_moved_to_declared_chapter_proceeds() -> None:
    verdict = _evaluate_pre_guards(
        PreGuardRequest(
            label="5",
            amend_chapter_label="7",
            amend_part_label=None,
            guards=_empty_guards(),
            already_recovered=False,
            moved_section_destinations={"5": "7"},  # declared chapter matches destination
            bp_assignments=None,
        )
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
        PreGuardRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=guards,
            already_recovered=False,
            moved_section_destinations={},
            bp_assignments=None,
        )
    )
    assert verdict.proceed is False
    assert verdict.skip_reason == "same_wave_relabel_destination_owned"
    assert verdict.with_part is True


# --- _evaluate_chapter_payload_ownership ---


def _owned_guards(section: str, chapter: str) -> UncoveredRecoveryGuards:
    return UncoveredRecoveryGuards(
        covered_sections=set(),
        chapter_payload_owned_sections={
            _uncovered_section_key(part=None, chapter=chapter, section=section)
        },
        relabel_destination_sections=set(),
    )


def test_chapter_payload_not_applicable_when_not_owned() -> None:
    verdict = _evaluate_chapter_payload_ownership(
        ChapterPayloadOwnershipRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_empty_guards(),
            section_present_in_chapter=False,
            future_repealed=False,
        )
    )
    assert verdict.outcome is ChapterPayloadOutcome.NOT_APPLICABLE


def test_chapter_payload_owned_when_present() -> None:
    verdict = _evaluate_chapter_payload_ownership(
        ChapterPayloadOwnershipRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_owned_guards("5", "3"),
            section_present_in_chapter=True,
            future_repealed=False,
        )
    )
    assert verdict.outcome is ChapterPayloadOutcome.OWNED


def test_chapter_payload_adopt_when_absent() -> None:
    verdict = _evaluate_chapter_payload_ownership(
        ChapterPayloadOwnershipRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_owned_guards("5", "3"),
            section_present_in_chapter=False,
            future_repealed=False,
        )
    )
    assert verdict.outcome is ChapterPayloadOutcome.ADOPT


def test_chapter_payload_future_repeal_skip_takes_precedence_over_adopt() -> None:
    verdict = _evaluate_chapter_payload_ownership(
        ChapterPayloadOwnershipRequest(
            label="5",
            amend_chapter_label="3",
            amend_part_label=None,
            guards=_owned_guards("5", "3"),
            section_present_in_chapter=False,
            future_repealed=True,
        )
    )
    assert verdict.outcome is ChapterPayloadOutcome.FUTURE_REPEAL_SKIP
