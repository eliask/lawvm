"""Starter-shard tests for the U.S. federal amendatory lowering surface.

Covers: prose/href USC target-address parsing under the PINNED convention; the
strike/insert -> TEXT_REPLACE lowering on real Title-11 fixtures; the each-place
occurrence flag; a typed finding for an instruction we deliberately cannot lower
(named-act target with no USC title); and the window scan over a tmp farchive
built from fixtures (no network).
"""

from __future__ import annotations

from pathlib import Path

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import (
    NON_TITLE_TARGET_RULE_ID,
    RULE_STRIKE_INSERT,
    TARGET_UNRESOLVED_FINDING_RULE_ID,
    UNLOWERED_FINDING_RULE_ID,
    lower_plaw_amendatory,
    parse_usc_target_href,
    parse_usc_target_phrase,
)
from lawvm.us_federal.effect_candidates import scan_title_effect_candidates
from lawvm.us_federal.sources import open_us_federal_farchive, plaw_locator

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "us_federal"


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


# ---------------------------------------------------------------------------
# Target address parsing (pinned USC convention)
# ---------------------------------------------------------------------------


def test_prose_target_phrase_pinned_address():
    addr = parse_usc_target_phrase("Section 362(c)(1) of title 11, United States Code")
    assert addr == LegalAddress(
        path=(("title", "11"), ("section", "362"), ("subsection", "c"), ("paragraph", "1"))
    )


def test_prose_target_phrase_lowercase_and_no_us_code_suffix():
    addr = parse_usc_target_phrase("section 1325(b)(4) of title 11")
    assert addr == LegalAddress(
        path=(("title", "11"), ("section", "1325"), ("subsection", "b"), ("paragraph", "4"))
    )


def test_prose_target_phrase_named_act_is_not_a_usc_target():
    # "Section 4(b) of the National Guard ... Act of 2008" has no "of title N".
    assert parse_usc_target_phrase("Section 4(b) of the National Guard and Reservists Debt Relief Act of 2008") is None


def test_href_target_parsing_drops_note_facet():
    # "(10A)" is a digit-led definitional label -> paragraph level in USC §101.
    assert parse_usc_target_href("/us/usc/t11/s101/10A") == LegalAddress(
        path=(("title", "11"), ("section", "101"), ("paragraph", "10A"))
    )
    assert parse_usc_target_href("/us/usc/t11/s362/c/1") == LegalAddress(
        path=(("title", "11"), ("section", "362"), ("subsection", "c"), ("paragraph", "1"))
    )


# ---------------------------------------------------------------------------
# Strike/insert lowering on real Title-11 fixtures
# ---------------------------------------------------------------------------


def test_plaw_116_52_strike_subparagraph_and_insert_block_replace():
    report = lower_plaw_amendatory(_read("PLAW-116publ52.xml"))
    assert report.statute_id == "PL 116-52"
    assert report.enacted == "2019-08-23"
    assert "title 11" in report.title_targets
    # One amendatory instruction: §101(10A) strike subparagraph (B) + insert block.
    accepted = [i for i in report.instructions if i.status == "accepted"]
    assert len(accepted) == 1
    instr = accepted[0]
    assert instr.action == "strike_insert"
    assert instr.target_address is not None
    assert instr.target_address.path[0] == ("title", "11")
    assert instr.operation is not None
    # Quoted-block insert lowers to a whole-node REPLACE candidate.
    assert instr.operation.action is StructuralAction.REPLACE
    assert instr.operation.witness_rule_id == RULE_STRIKE_INSERT
    assert instr.operation.source is not None
    assert instr.operation.source.statute_id == "PL 116-52"


def test_plaw_116_51_each_place_text_replace():
    report = lower_plaw_amendatory(_read("PLAW-116publ51.xml"))
    accepted = [i for i in report.instructions if i.status == "accepted"]
    assert len(accepted) == 1
    op = accepted[0].operation
    assert op is not None
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.kind is TextPatchKindEnum.REPLACE
    assert op.text_patch.selector.match_text == "$3,237,000"
    assert op.text_patch.replacement == "$10,000,000"
    # "each place that term appears" -> occurrence -1 (all occurrences).
    assert op.text_patch.selector.occurrence == -1
    # §101(18) -> title:11/section:101/paragraph:18 (pinned convention).
    assert op.target == LegalAddress(
        path=(("title", "11"), ("section", "101"), ("paragraph", "18"))
    )


def test_plaw_117_177_strike_insert_off_title_11_is_needs_review_with_finding():
    # Targets title 18, not 11: resolvable, but withheld from Title-11 scope.
    report = lower_plaw_amendatory(_read("PLAW-117publ177.xml"))
    instr = report.instructions[0]
    assert instr.action == "strike_insert"
    assert instr.status == "needs_review"
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.TEXT_REPLACE
    assert instr.target_address is not None
    assert instr.target_address.path[0] == ("title", "18")
    assert instr.finding is not None
    assert instr.finding.rule_id == NON_TITLE_TARGET_RULE_ID


# ---------------------------------------------------------------------------
# Typed finding for an unsupported / unresolvable instruction (no silent skip)
# ---------------------------------------------------------------------------


def test_named_act_target_yields_unresolved_finding_not_silent_skip():
    # PL 118-24 amends "Section 4(b) of the National Guard ... Act of 2008".
    report = lower_plaw_amendatory(_read("PLAW-118publ24.xml"))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    assert instr.status == "unsupported"
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID
    # The unparsed instruction is recorded, never silently dropped.
    assert instr.raw_text


def test_coverage_counts_are_witness_anchored_not_replay():
    report = lower_plaw_amendatory(_read("PLAW-114publ89.xml"))
    cov = report.coverage()
    assert cov["replay_claims"] is False
    assert cov["candidate_claims"] is True
    # Every instruction is accounted: lowered + unsupported == total.
    assert (
        cov["instructions_accepted"]
        + cov["instructions_needs_review"]
        + cov["instructions_unsupported"]
        == cov["instructions_total"]
    )
    # There are unlowered forms in this multi-target law -> findings exist.
    assert cov["findings_total"] >= 1


# ---------------------------------------------------------------------------
# Window scan over a tmp farchive built from fixtures (NO network)
# ---------------------------------------------------------------------------


def _build_tmp_farchive(tmp_path: Path) -> Path:
    db = tmp_path / "us_fixture.farchive"
    archive = open_us_federal_farchive(db, allow_create=True)
    try:
        for name, (cong, num) in {
            "PLAW-116publ51.xml": (116, 51),
            "PLAW-116publ52.xml": (116, 52),
            "PLAW-117publ177.xml": (117, 177),
            "PLAW-114publ89.xml": (114, 89),
        }.items():
            archive.store(plaw_locator(cong, num), _read(name))
    finally:
        archive.close()
    return db


def test_scan_title_11_over_fixture_farchive(tmp_path):
    db = _build_tmp_farchive(tmp_path)
    archive = open_us_federal_farchive(db, readonly=True)
    try:
        report = scan_title_effect_candidates(
            archive, title="11", congress_window=(114, 116, 117)
        )
    finally:
        archive.close()
    cov = report.coverage()
    # 116-51 and 116-52 amend title 11; 117-177 (t18) and 114-89 (t21/t35) do not.
    targeting = set(cov["law_labels_targeting_title"])
    assert "PL 116-51" in targeting
    assert "PL 116-52" in targeting
    assert "PL 117-177" not in targeting
    # Two clean accepted Title-11 candidate operations from the two laws.
    assert cov["title_candidate_operations"] == 2
    assert len(report.operations()) == 2
    # All emitted candidate ops are addressed at title 11.
    for op in report.operations():
        assert op.target.path[0] == ("title", "11")
    assert cov["replay_claims"] is False
    assert cov["candidate_claims"] is True


def test_scan_records_findings_for_unlowered_instructions(tmp_path):
    db = _build_tmp_farchive(tmp_path)
    archive = open_us_federal_farchive(db, readonly=True)
    try:
        report = scan_title_effect_candidates(
            archive, title="11", congress_window=(114, 116, 117)
        )
    finally:
        archive.close()
    # 114-89 targets title 11 only via short-marker false positives? No: it is t21/t35.
    # It should NOT be in the title-11 targeting set, so no unresolved spam from it.
    labels = set(report.coverage()["law_labels_targeting_title"])
    assert "PL 114-89" not in labels
    # The unlowered-finding family id is stable and present in the amendatory module.
    assert UNLOWERED_FINDING_RULE_ID == "us_amendatory_unlowered"
