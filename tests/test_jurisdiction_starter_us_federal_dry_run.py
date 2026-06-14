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
    IRNode,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import lower_plaw_amendatory
from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    DISPOSITION_ORACLE_SUSPECT,
    US_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    US_DRY_RUN_REFUSED_SECTION_NOT_IN_BEFORE_RULE_ID,
    US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID,
    US_DRY_RUN_REFUSED_TARGET_NOT_TITLE_RULE_ID,
    US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID,
    US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    US_DRY_RUN_SECTION_AGREES_RULE_ID,
    USDryRunRefusal,
    USDryRunReport,
    USDryRunWindowError,
    _materialize_one,
    _norm_editorial,
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
def test_real_title11_pl118_42_window_507d_stays_oracle_suspect_courtesy_space() -> None:
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
    # We lower exactly one in-Title-11 op: the 507(d) insert-after-anchor.
    assert report.claimed_sections == ("11:507",)
    # The enacted instruction inserts "excluding subparagraph (F)" directly after
    # the anchor "(a)(8)" — the faithful materialization carries NO space
    # ("(a)(8)excluding"). The published Code adds an OLRC courtesy space
    # ("(a)(8) excluding"). We do NOT invent the space: the residual is
    # oracle_suspect (F1 case ii), demoted from lawvm_wrong by the editorial
    # insert-after-anchor space projection. The materialized text is preserved
    # faithfully (no repair-to-oracle).
    rows = {row.section_key: row for row in report.rows}
    row = rows["11:507"]
    assert row.status == "residual"
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert "(a)(8)excluding subparagraph (F)" in row.materialized_text
    assert "(a)(8) excluding subparagraph (F)" in row.oracle_text
    # Sections 109 and 1182 are honest missing-source gaps (not lowered here).
    ns = report.north_star()
    assert ns["oracle_changed_section_count"] == 3
    assert ns["sections_materialized_in_agreement"] == 0
    assert set(ns["missing_source_sections"]) == {"11:109", "11:1182"}
    # The gate stays closed.
    assert report.replay_authorized is False


_WINDOW_2018_2020_LAWS = (51, 52, 54, 92, 136, 189, 260, 325)


def _usc_editions_present(years: tuple[int, ...], title: int) -> bool:
    if not _canonical_archive_available():
        return False
    from lawvm.us_federal.sources import open_us_federal_farchive, read_usc_annual

    archive = open_us_federal_farchive(readonly=True)
    try:
        return all(read_usc_annual(archive, year, title) is not None for year in years)
    finally:
        archive.close()


@pytest.mark.skipif(
    not _usc_editions_present((2018, 2020), 11),
    reason="USC 2018/2020 Title 11 editions not present in the canonical archive",
)
def test_real_title11_2018_2020_window_composes_multi_law_amendments() -> None:
    # The richer 116th-Congress window: real substantive Title 11 textual
    # amendments across eight Public Laws. This pins the HONEST numbers and the
    # decomposition into typed residual classes — no agreement is forced.
    from lawvm.us_federal.sources import open_us_federal_farchive, plaw_locator
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    archive = open_us_federal_farchive(readonly=True)
    try:
        report = build_us_dry_run_from_archive(
            archive,
            title=11,
            before_year=2018,
            after_year=2020,
            plaw_locators={
                f"PL 116-{n}": plaw_locator(116, n) for n in _WINDOW_2018_2020_LAWS
            },
        )
    finally:
        archive.close()

    summary = report.summary()
    # 40 sections genuinely changed across the two editions (a fact of the source).
    assert summary["oracle_changed_section_count"] == 40
    # Sub-section structural redesignations (paragraph/clause REPLACE/INSERT, the
    # SBRA subchapter-V form) are refused at section granularity, not wrong-
    # materialized. At least the four §101/§502 paragraph redesignations refuse here.
    refusals = summary["refusal_rule_counts"]
    assert (
        refusals.get(US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID, 0)
        >= 4
    )
    # §101 is amended by FIVE window ops (116-51/52/92/136); they compose into ONE
    # row, not five. (PL 116-51's each-place debt-limit strike materializes; the
    # paragraph redesignations refuse — so the section stays a residual, honestly.)
    s101_rows = [r for r in report.rows if r.section_key == "11:101"]
    assert len(s101_rows) == 1
    # Every published residual is typed (no blank disposition) and the gate is shut.
    for row in report.residual_rows():
        assert row.disposition in (
            DISPOSITION_LAWVM_WRONG,
            DISPOSITION_ORACLE_SUSPECT,
        )
    assert report.replay_authorized is False
    # §547(b) (PL 116-54 §3(a)) is "inserting '<due-diligence clause>' after 'may'"
    # with NO striking — an insert-after, not a strike_insert. The lowering now
    # classifies it correctly and finds the anchor "may" in the 2018 edition, so it
    # is no longer a match-not-found residual. It stays a residual only because the
    # section also acquired a "(j)" subsection via another window amendment not
    # lowered here — honest incompleteness, never repaired to the oracle.
    s547 = {r.section_key: r for r in report.rows}.get("11:547")
    assert s547 is not None
    assert s547.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    assert s547.disposition == DISPOSITION_LAWVM_WRONG
    # The insert-after materialized the clause AT the "may" anchor (not inverted).
    assert "may, based on reasonable due diligence" in s547.materialized_text
    # The fixes turn the first real substantive textual amendment into an
    # AGREEMENT: §525's add-at-end (PL 116-260) materializes exactly the oracle.
    agreements = [
        r.section_key
        for r in report.rows
        if r.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    ]
    assert "11:525" in agreements
    assert report.north_star()["sections_materialized_in_agreement"] >= 1
    assert s547.disposition == DISPOSITION_LAWVM_WRONG


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


# ---------------------------------------------------------------------------
# Multi-op composition per section (the 2018->2020 multi-law window class)
# ---------------------------------------------------------------------------


def _plaw_bytes_strike_insert(
    *, congress: int, number: int, title: int, section: str, struck: str, inserted: str
) -> bytes:
    """A minimal USLM PL striking ``struck`` and inserting ``inserted``."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        f"<congress>{congress}</congress><docNumber>{number}</docNumber>"
        "<approvedDate>2024-01-01</approvedDate></meta><main><section><num>1</num>"
        f'<content><ref href="/us/usc/t{title}/s{section}">Section {section} of title '
        f"{title}, United States Code</ref>, <amendingAction type=\"amend\">is amended</amendingAction>"
        ' by <amendingAction type="delete">striking</amendingAction> '
        f"“<quotedText>{struck}</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        f"“<quotedText>{inserted}</quotedText>”.</content></section></main></uslm>"
    ).encode("utf-8")


def test_multiple_ops_on_one_section_compose_before_a_single_comparison() -> None:
    # Two separate window laws each strike-and-insert on section 10: 15-year ->
    # 17-year (PL 99-3) and 17-year -> 19-year (PL 99-4). Composed in source order
    # they reach the oracle's 19-year text; compared independently each would fail.
    pl3 = _plaw_bytes_strike_insert(
        congress=99, number=3, title=99, section="10", struck="15-year", inserted="17-year"
    )
    pl4 = _plaw_bytes_strike_insert(
        congress=99, number=4, title=99, section="10", struck="17-year", inserted="19-year"
    )
    report = _build({"PL 99-3": pl3, "PL 99-4": pl4})
    rows = {row.section_key: row for row in report.rows}
    assert "99:10" in rows
    row = rows["99:10"]
    # One composed row for the section, not one per op.
    assert sum(1 for r in report.rows if r.section_key == "99:10") == 1
    assert row.status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "19-year" in row.materialized_text
    assert "15-year" not in row.materialized_text
    # The composed row records both contributing op ids (joined by "+").
    assert "+" in row.op_id
    assert "99-3" in row.op_id and "99-4" in row.op_id


def test_match_not_found_on_a_later_composed_op_fails_the_whole_section() -> None:
    # First op composes (15-year -> 17-year); the second strikes a phrase the
    # running text no longer carries (15-year). The section is a residual, never a
    # fuzzy match, and no partial materialization is published.
    pl3 = _plaw_bytes_strike_insert(
        congress=99, number=3, title=99, section="10", struck="15-year", inserted="17-year"
    )
    pl4 = _plaw_bytes_strike_insert(
        congress=99, number=4, title=99, section="10", struck="15-year", inserted="99-year"
    )
    report = _build({"PL 99-3": pl3, "PL 99-4": pl4})
    rows = {row.section_key: row for row in report.rows}
    row = rows["99:10"]
    assert row.status == "residual"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_MATCH_TEXT_NOT_FOUND_RULE_ID
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    assert row.materialized_text == ""


# ---------------------------------------------------------------------------
# Sub-section structural REPLACE/INSERT is refused (not wrong-materialized)
# ---------------------------------------------------------------------------


def test_subsection_replace_op_is_refused_not_materialized_as_whole_section() -> None:
    # A REPLACE whose target is deeper than the section (a paragraph redesignation,
    # as PL 116-52/116-136 do to Title 11 §101) cannot be represented at section
    # granularity: the payload is a fragment, not the section body. Refuse it rather
    # than substitute the fragment for the whole section.
    op = LegalOperation(
        op_id="synthetic-subsection-replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "101"), ("paragraph", "10A"))
        ),
        payload=IRNode(
            kind=IRNodeKind.PARAGRAPH, label="10A", text="(B)(i) includes any amount"
        ),
    )
    refusal = _materialize_one(op, "the whole section 101 before text")
    assert isinstance(refusal, USDryRunRefusal)
    assert refusal.rule_id == US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID


def test_section_level_insert_with_plain_text_payload_still_materializes() -> None:
    # An INSERT whose target IS the section (add-at-end of the section body) remains
    # representable: the payload is appended. Only sub-section targets are refused.
    op = LegalOperation(
        op_id="synthetic-section-insert",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "99"), ("section", "10"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="b", text="(b) Added subsection."),
    )
    outcome = _materialize_one(op, "Section 10 body.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert materialized == "Section 10 body. (b) Added subsection."


# ---------------------------------------------------------------------------
# Editorial quote/spacing classification (generalized F1 -> oracle_suspect)
# ---------------------------------------------------------------------------


def test_norm_editorial_undoes_olrc_quote_and_dash_paren_spacing() -> None:
    # The enacted amendment wraps the inserted block in quotes and the published
    # Code drops them and inserts a courtesy space after the introductory dash.
    enacted = 'if—“(1) the debtor; “(2) the creditor.'
    published = "if— (1) the debtor; (2) the creditor."
    # Plain inline normalization keeps them apart; the editorial projection unifies.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    assert normalize_inline_comparison_text(enacted) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(enacted) == _norm_editorial(published)


def test_norm_editorial_undoes_insert_after_anchor_courtesy_space() -> None:
    # F1 case ii: the enacted insert-after places matter directly after a
    # parenthesized anchor (no space); the published Code adds a courtesy space.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    faithful = "claims under (a)(8)excluding subparagraph (F), or (a)(9) of"
    published = "claims under (a)(8) excluding subparagraph (F), or (a)(9) of"
    # Plain normalization keeps them apart; the editorial projection unifies.
    assert normalize_inline_comparison_text(faithful) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(faithful) == _norm_editorial(published)
    # The projection only erases a ")"-adjacent space: a real content divergence
    # (here an extra "(j)") is NOT masked into a false agreement.
    other = "subsections (c), (i), and (j) of this section"
    assert _norm_editorial(faithful) != _norm_editorial(other)


def test_quoted_block_insert_residual_is_typed_oracle_suspect_not_lawvm_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A section-level INSERT whose only divergence from the oracle is the OLRC's
    # quote-stripping and dash-paren courtesy space is editorial on the oracle side:
    # disposition oracle_suspect (generalized F1), never repaired to the oracle.
    before = (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T99</title><!-- AUTHORITIES-USC-TITLE-ENUM:99 --></head><body><div>"
        "<!-- expcite:TITLE 99!@!CHAPTER 1!@!Sec. 40 -->"
        "<!-- field-start:head --><h3 class=\"section-head\">&sect;40. Insert section</h3>"
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">Base body of section 40 follows—</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")
    after = (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T99</title><!-- AUTHORITIES-USC-TITLE-ENUM:99 --></head><body><div>"
        "<!-- expcite:TITLE 99!@!CHAPTER 1!@!Sec. 40 -->"
        "<!-- field-start:head --><h3 class=\"section-head\">&sect;40. Insert section</h3>"
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">Base body of section 40 follows— (1) first; (2) second</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")
    op = LegalOperation(
        op_id="/us/pl/99/9/s1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "99"), ("section", "40"))),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="",
            # The enacted insert wraps each item in quotes (USLM convention).
            text="“(1) first; “(2) second",
        ),
    )

    class _OneOpReport:
        def operations(self) -> list[LegalOperation]:
            return [op]

    monkeypatch.setattr(
        "lawvm.us_federal.dry_run.lower_plaw_amendatory",
        lambda *a, **k: _OneOpReport(),
    )
    report = build_us_dry_run(
        before_htm=before,
        after_htm=after,
        plaw_blobs={"PL 99-9": b"<uslm/>"},
        title=99,
        before_year="2023",
        after_year="2024",
    )

    rows = {row.section_key: row for row in report.rows}
    row = rows["99:40"]
    assert row.status == "residual"
    assert row.disposition == DISPOSITION_ORACLE_SUSPECT
    assert row.rule_id == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    # The materialized text keeps the enacted quotes (never repaired to the oracle).
    assert "“(1) first" in row.materialized_text
