"""U.S. federal section-level dry-run kernel (Title 99 synthetic + Title 11 real).

No network. Two layers:

1. A tiny deterministic synthetic window (Title 99, two committed htm editions +
   a synthetic strike-and-insert Public Law) exercises every honest outcome:
   agreement, a ``lawvm_wrong`` text-mismatch residual, a ``lawvm_wrong``
   match-text-not-found residual, the ``missing_source`` oracle-changed-but-not-
   claimed boundary gap, the off-title and section-not-in-before refusals, the
   witness-anchored north-star, and the ``replay_authorized=False`` gate.

2. The real Title 11 / PL 118-42 / 2023->2024 window, run from the canonical
   archive when present (skipped otherwise). This is the end-to-end proof.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import lower_plaw_amendatory
from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID,
    US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
    US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
    US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID,
    US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    US_DRY_RUN_SECTION_AGREES_RULE_ID,
    USDryRunReport,
    USDryRunWindowError,
    build_us_dry_run,
)

FIXTURES = Path(__file__).parent / "fixtures" / "us_federal"
BEFORE_HTM = (FIXTURES / "usc-dryrun-before.htm").read_bytes()
AFTER_HTM = (FIXTURES / "usc-dryrun-after.htm").read_bytes()
PLAW_STRIKE_INSERT = (FIXTURES / "plaw-dryrun-strike-insert.xml").read_bytes()


def _build(plaw_blobs: dict[str, bytes] | None = None) -> USDryRunReport:
    return build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-2": PLAW_STRIKE_INSERT} if plaw_blobs is None else plaw_blobs,
        title=99,
        before_year="2023",
        after_year="2024",
    )


# ---------------------------------------------------------------------------
# Synthetic window: agreement + boundary + north-star
# ---------------------------------------------------------------------------


def test_oracle_changed_section_set_is_a_fact_of_the_two_editions() -> None:
    report = _build()
    # Section 10 changed (15-year -> 19-year); section 30 is after-only; section
    # 20 is byte-identical and must NOT appear in the changed set.
    assert report.oracle_changed_sections == ("99:10", "99:30")


def test_strike_insert_op_materializes_in_agreement_with_oracle() -> None:
    report = _build()
    rows = {row.section_key: row for row in report.rows}
    assert "99:10" in rows
    agree = rows["99:10"]
    assert agree.status == "agree"
    assert agree.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert agree.disposition == ""
    assert "19-year" in agree.materialized_text
    assert "15-year" not in agree.materialized_text


def test_missing_source_section_is_the_honest_lowering_gap() -> None:
    report = _build()
    ns = report.north_star()
    # Denominator is the oracle changed count (2); numerator is the agreeing
    # section in that changed set (section 10). Section 30 is the missing gap.
    assert ns["oracle_changed_section_count"] == 2
    assert ns["sections_materialized_in_agreement"] == 1
    assert ns["coverage_fraction"] == pytest.approx(0.5)
    assert ns["missing_source_sections"] == ["99:30"]


def test_boundary_proof_unresolved_when_oracle_changed_a_section_we_did_not_claim() -> None:
    report = _build()
    proof = report.boundary_proof
    # The boundary is unresolved: section 30 changed in the oracle but is not in
    # the claimed set. It is surfaced as an unexplained changed path, never hidden.
    assert proof.status == "unresolved"
    unexplained = {tuple(step) for step in proof.unexplained_changed_paths}
    assert (("title", "99"), ("section", "30")) in unexplained
    covered = {tuple(step) for step in proof.covered_changed_paths}
    assert (("title", "99"), ("section", "10")) in covered


def test_missing_source_gap_is_carried_in_the_agreement_surface() -> None:
    report = _build()
    surface = report.agreement_surface()
    families = {row["rule_id"]: row["family"] for row in surface["residuals"]}
    assert US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID in families
    assert families[US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID] == "source_footing_gap"


# ---------------------------------------------------------------------------
# Synthetic window: each residual disposition + refusals
# ---------------------------------------------------------------------------


def _plaw_bytes_with_target_and_strike(title: int, section: str, struck: str) -> bytes:
    """A minimal USLM PL striking ``struck`` and inserting "X" in title/section."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        "<congress>99</congress><docNumber>3</docNumber>"
        "<approvedDate>2024-01-01</approvedDate></meta><main><section><num>1</num>"
        f'<content><ref href="/us/usc/t{title}/s{section}">Section {section} of title '
        f"{title}, United States Code</ref>, <amendingAction type=\"amend\">is amended</amendingAction>"
        ' by <amendingAction type="delete">striking</amendingAction> '
        f"“<quotedText>{struck}</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>X</quotedText>”.</content></section></main></uslm>"
    ).encode("utf-8")


def test_match_text_not_found_is_a_lawvm_wrong_residual_never_fuzzy_matched() -> None:
    # Strike a phrase that does not occur in section 10's before text.
    pl = _plaw_bytes_with_target_and_strike(99, "10", "nonexistent-phrase")
    report = _build({"PL 99-3": pl})
    rows = {row.section_key: row for row in report.rows}
    assert "99:10" in rows
    row = rows["99:10"]
    assert row.status == "residual"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    # No materialization was produced; we refused to guess.
    assert row.materialized_text == ""


def test_wrong_replacement_is_a_text_mismatch_residual_not_repaired_to_oracle() -> None:
    # Strike "15-year" but insert a WRONG replacement; the oracle says 19-year.
    pl = _plaw_bytes_with_target_and_strike(99, "10", "15-year")
    report = _build({"PL 99-3": pl})
    rows = {row.section_key: row for row in report.rows}
    row = rows["99:10"]
    assert row.status == "residual"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    # Materialized text reflects OUR op (the X replacement), not the oracle
    # (19-year). No repair-to-oracle: the divergence is kept visible.
    assert "the X period" in row.materialized_text
    assert "15-year" not in row.materialized_text
    assert "19-year" in row.oracle_text


def test_off_title_op_is_refused_never_materialized_into_the_wrong_corpus() -> None:
    pl = _plaw_bytes_with_target_and_strike(7, "100", "anything")
    report = _build({"PL 99-3": pl})
    rule_ids = {refusal.rule_id for refusal in report.refusals}
    assert US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID in rule_ids
    # No row was produced for an off-title op.
    assert report.rows == ()


def test_section_not_in_before_edition_is_refused() -> None:
    pl = _plaw_bytes_with_target_and_strike(99, "404", "anything")
    report = _build({"PL 99-3": pl})
    rule_ids = {refusal.rule_id for refusal in report.refusals}
    assert US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID in rule_ids


def test_structural_repeal_op_is_refused_at_section_text_granularity() -> None:
    # A bare REPEAL of section 10 cannot be a section-text edit -> typed refusal.
    op = LegalOperation(
        op_id="synthetic-repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("title", "99"), ("section", "10"))),
    )
    from lawvm.us_federal.dry_run import _materialize_one

    refusal = _materialize_one(op, "section 10 before text")
    from lawvm.us_federal.dry_run import USDryRunRefusal

    assert isinstance(refusal, USDryRunRefusal)
    assert refusal.rule_id == US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID


# ---------------------------------------------------------------------------
# Gate + JSON contract
# ---------------------------------------------------------------------------


def test_report_never_authorizes_replay() -> None:
    report = _build()
    assert report.replay_authorized is False
    payload = report.to_jsonable()
    assert payload["replay_authorized"] is False
    assert payload["replay_claims"] is False
    assert payload["dry_run_claims"] is True
    assert payload["actual_replay_blocking_rule_id"] == US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID


def test_to_jsonable_is_self_describing_and_complete() -> None:
    report = _build()
    payload = report.to_jsonable()
    assert payload["jurisdiction"] == "us_federal"
    assert payload["report_kind"] == "dry_run_section_replay"
    assert "summary" in payload
    assert "rows" in payload
    assert "refusals" in payload
    assert "mutation_boundary_proof" in payload
    assert "agreement_surface" in payload
    # Summary-only projection omits the heavy sections.
    summary_only = report.to_jsonable(summary_only=True)
    assert "rows" not in summary_only
    assert summary_only["summary"]["north_star"]["coverage_fraction"] == pytest.approx(0.5)


def test_window_error_is_raised_for_a_missing_archive_source() -> None:
    class _EmptyArchive:
        def get(self, locator: str) -> bytes | None:
            return None

        def locators(self, pattern: str = "%") -> list[str]:
            return []

    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    with pytest.raises(USDryRunWindowError):
        build_us_dry_run_from_archive(
            _EmptyArchive(),
            title=11,
            before_year=2023,
            after_year=2024,
            plaw_locators={"PL 118-42": "us://plaw/118/publ42.xml"},
        )


# ---------------------------------------------------------------------------
# Real Title 11 / PL 118-42 / 2023->2024 window (archive-gated, no network)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_title11_pl118_42_window_exposes_the_one_space_lowering_gap() -> None:
    from lawvm.us_federal.sources import (
        open_us_federal_farchive,
        plaw_locator,
    )
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    archive = open_us_federal_farchive(readonly=True)
    try:
        report = build_us_dry_run_from_archive(
            archive,
            title=11,
            before_year=2023,
            after_year=2024,
            plaw_locators={"PL 118-42": plaw_locator(118, 42)},
            enacted="2024-03-09",
        )
    finally:
        archive.close()

    # The 2023->2024 Title 11 oracle changed exactly three sections.
    assert report.oracle_changed_sections == ("11:109", "11:507", "11:1182")
    # We lower exactly one in-Title-11 op: the 507(d) strike-and-insert.
    assert report.claimed_sections == ("11:507",)
    # The materialization is a residual, NOT an agreement: PL 118-42's lowering
    # dropped the space, producing "(a)(8)excluding" vs the oracle "(a)(8) excluding".
    rows = {row.section_key: row for row in report.rows}
    row = rows["11:507"]
    assert row.status == "residual"
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    assert "(a)(8)excluding subparagraph (F)" in row.materialized_text
    assert "(a)(8) excluding subparagraph (F)" in row.oracle_text
    # Sections 109 and 1182 are honest missing-source gaps (not lowered here).
    ns = report.north_star()
    assert ns["oracle_changed_section_count"] == 3
    assert ns["sections_materialized_in_agreement"] == 0
    assert set(ns["missing_source_sections"]) == {"11:109", "11:1182"}
    # The gate stays closed.
    assert report.replay_authorized is False


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_real_pl118_42_lowers_one_title11_text_replace_op() -> None:
    from lawvm.us_federal.sources import open_us_federal_farchive, read_plaw

    archive = open_us_federal_farchive(readonly=True)
    try:
        blob = read_plaw(archive, 118, 42)
    finally:
        archive.close()
    assert blob is not None
    report = lower_plaw_amendatory(blob, statute_id="PL 118-42", enacted="2024-03-09")
    title11_ops = [
        op
        for op in report.operations()
        if op.target.path and op.target.path[0] == ("title", "11")
    ]
    assert len(title11_ops) == 1
    op = title11_ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.kind is TextPatchKindEnum.REPLACE


def test_committed_synthetic_fixtures_round_trip_through_source_tree() -> None:
    # Defence: the committed before/after htm parse to the expected section sets.
    from lawvm.us_federal.source_tree import parse_usc_title_document

    before = parse_usc_title_document(BEFORE_HTM, title=99, year="2023")
    after = parse_usc_title_document(AFTER_HTM, title=99, year="2024")
    assert [s.section for s in before.sections] == ["10", "20"]
    assert [s.section for s in after.sections] == ["10", "20", "30"]


def test_unused_text_selector_import_is_exercised() -> None:
    # Keep the imported IR text-patch constructors covered (defence against an
    # accidental signature drift the dry-run kernel depends on).
    patch = TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text="a", occurrence=0),
        replacement="b",
    )
    assert patch.replacement == "b"
