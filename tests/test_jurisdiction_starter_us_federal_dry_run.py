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
    US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    US_DRY_RUN_SECTION_AGREES_RULE_ID,
    USDryRunRefusal,
    USDryRunReport,
    USDryRunWindowError,
    _materialize_one,
    _norm_editorial,
    build_us_dry_run,
)
from lawvm.us_federal.source_tree import UscSection, parse_usc_title_document

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
    # Sections 109 and 1182 are NOT missing-source gaps: the F2 temporal layer
    # reclassifies them as sunset reversions (the SBRA debt-limit increase
    # sunset on June 21, 2024). The note-based channel (b) fires here even without
    # prior editions being loaded, so missing_source is empty.
    ns = report.north_star()
    assert ns["oracle_changed_section_count"] == 3
    assert ns["sections_materialized_in_agreement"] == 0
    assert set(ns["missing_source_sections"]) == set()
    assert set(ns["sunset_reversion_sections"]) == {"11:109", "11:1182"}
    rev_by_section = {c.section: c for c in report.sunset_reversions}
    assert set(rev_by_section) == {"109", "1182"}
    assert rev_by_section["109"].witness.sunset_date == "2024-06-21"
    assert rev_by_section["1182"].witness.sunset_date == "2024-06-21"
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
    # SBRA subchapter-V form) are now materialized at SUB-SECTION granularity: the
    # edit is scoped to the targeted node located by the pinned address convention.
    # When the targeted node is not locatable in the before edition (the SBRA
    # subchapter-V nodes were introduced by un-lowered sibling ops), the section is
    # a typed `subsection_target_node_not_located` residual — never a blanket refusal
    # and never a whole-section string replace in the wrong place.
    node_not_located = [
        r
        for r in report.residual_rows()
        if r.rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    ]
    assert node_not_located, "expected sub-section-scoped node-not-located residuals"
    # §101 is amended by FIVE window ops (116-51/52/92/136); they compose into ONE
    # row, not five. (PL 116-51's each-place debt-limit strike materializes; the
    # paragraph redesignations target nodes the un-lowered SBRA siblings introduced,
    # so the section stays a residual, honestly.)
    s101_rows = [r for r in report.rows if r.section_key == "11:101"]
    assert len(s101_rows) == 1
    # Every published residual is typed (no blank disposition) and the gate is shut.
    for row in report.residual_rows():
        assert row.disposition in (
            DISPOSITION_LAWVM_WRONG,
            DISPOSITION_ORACLE_SUSPECT,
        )
    assert report.replay_authorized is False
    # The faithful sidenote/quote handling demotes §1329 (the OLRC dropped the
    # "Time period." marginal note) and §330 (curly vs straight quotes) from
    # lawvm_wrong to oracle_suspect — typed as oracle editorial pathology, never
    # repaired to the oracle. So oracle_suspect is now multiple, not one.
    disp = summary["residual_disposition_counts"]
    assert disp.get(DISPOSITION_ORACLE_SUSPECT, 0) >= 3
    s1329 = {r.section_key: r for r in report.rows}.get("11:1329")
    assert s1329 is not None and s1329.disposition == DISPOSITION_ORACLE_SUSPECT
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


@pytest.mark.skipif(
    not _usc_editions_present((2018, 2020), 10),
    reason="USC 2018/2020 Title 10 editions not present in the canonical archive",
)
def test_real_title10_526_strike_is_node_scoped_and_section_stays_a_typed_residual() -> None:
    # §526(b)(3)(A) "may not exceed 20" -> "may not exceed 19" (NDAA FY2021,
    # PL 116-283 §501). The strike anchor "20" recurs all over the section
    # (122004, 120 days, January 1, 2014, December 31, 2022, ...). A whole-section
    # string replace would hit the wrong "20". The op is node-scoped to (b)(3)(A),
    # so ONLY that clause's "20" becomes "19" and every other "20" is untouched.
    #
    # The section nonetheless stays a typed residual: the 2020 edition ALSO fixed
    # a pre-existing typo in subsection (k) ("number of" -> "the number of"), an
    # OLRC editorial correction that is NOT in our window's public law. We never
    # fabricate that "the" to force agreement (Prime Directive).
    from lawvm.us_federal.bench import derive_window_law_locators
    from lawvm.us_federal.sources import open_us_federal_farchive
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    archive = open_us_federal_farchive(readonly=True)
    try:
        locators = derive_window_law_locators(
            archive, title=10, before_year=2018, after_year=2020
        )
        assert locators is not None
        report = build_us_dry_run_from_archive(
            archive,
            title=10,
            before_year=2018,
            after_year=2020,
            plaw_locators=locators,
        )
    finally:
        archive.close()

    rows = {row.section_key: row for row in report.rows}
    assert "10:526" in rows, "section 526 must be claimed (the (b)(3)(A) op lowers)"
    row = rows["10:526"]
    # The (b)(3)(A) clause was node-scoped: exactly one "20" became "19".
    assert "in the grade of general or admiral may not exceed 19;" in row.materialized_text
    # The (b)(3)(B)/(C) limits keeping "68" and "144" prove no over-broad strike.
    assert "may not exceed 68;" in row.materialized_text
    assert "may not exceed 144." in row.materialized_text
    # No false agreement: subsection (k) genuinely differs in the editions.
    assert row.status == "residual"
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    assert "shall not apply to number of" in row.materialized_text
    assert "shall not apply to the number of" in row.oracle_text


@pytest.mark.skipif(
    not _usc_editions_present((2022, 2023), 7),
    reason="USC 2022/2023 Title 7 editions not present in the canonical archive",
)
def test_real_title7_3222a_markerless_node_stays_a_typed_residual_not_a_sibling_match() -> None:
    # §3222a is an OLRC "hanging-indent" section: its (a)/(b) subsection and (1)/(2)/(3)
    # paragraph enumerators are NOT printed in the body (the structure is carried by
    # indent depth alone). An op targeting (a)(3) therefore cannot locate a clean node
    # — and we must NOT fuzzy-match onto a sibling paragraph. The section stays a typed
    # ``subsection_target_node_not_located`` residual with no (wrong) materialization,
    # exactly the Prime-Directive-safe refusal.
    from lawvm.us_federal.bench import derive_window_law_locators
    from lawvm.us_federal.sources import open_us_federal_farchive
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    archive = open_us_federal_farchive(readonly=True)
    try:
        locators = derive_window_law_locators(
            archive, title=7, before_year=2022, after_year=2023
        )
        assert locators is not None
        report = build_us_dry_run_from_archive(
            archive,
            title=7,
            before_year=2022,
            after_year=2023,
            plaw_locators=locators,
        )
    finally:
        archive.close()

    rows = {row.section_key: row for row in report.rows}
    assert "7:3222a" in rows
    row = rows["7:3222a"]
    assert row.status == "residual"
    assert row.rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert row.disposition == DISPOSITION_LAWVM_WRONG
    # No wrong materialization: we did not splice "4" onto a guessed sibling node.
    assert row.materialized_text == ""


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
# Non-positive-law title window (synthetic Title 15): act-section amendment
# materializes through the act-section→USC route; a note-only target is held out.
# ---------------------------------------------------------------------------


def _nonpositive_title15_before() -> bytes:
    # A tiny non-positive Title 15 edition: §77e (a codified act-section landing)
    # and §636 (the holdout-target section, must NOT be claimed/changed).
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T15 before</title><!-- AUTHORITIES-USC-TITLE-ENUM:15 --></head><body><div>"
        "<!-- expcite:TITLE 15!@!CHAPTER 2A!@!Sec. 77e -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;77e. Securities registration</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">The registration window under this section is the 15-day period.</p>'
        "<!-- field-end:statute -->"
        "<!-- expcite:TITLE 15!@!CHAPTER 14B!@!Sec. 636 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;636. Small business loans</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">This uncodified-note target section is not amended in the window.</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")


def _nonpositive_title15_after() -> bytes:
    # The oracle after edition: §77e's window is now "19-day"; §636 unchanged.
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T15 after</title><!-- AUTHORITIES-USC-TITLE-ENUM:15 --></head><body><div>"
        "<!-- expcite:TITLE 15!@!CHAPTER 2A!@!Sec. 77e -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;77e. Securities registration</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">The registration window under this section is the 19-day period.</p>'
        "<!-- field-end:statute -->"
        "<!-- expcite:TITLE 15!@!CHAPTER 14B!@!Sec. 636 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;636. Small business loans</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">This uncodified-note target section is not amended in the window.</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")


def _nonpositive_title15_plaw() -> bytes:
    # Two amendatory sections: (1) an act-named §77e target carrying the codified
    # (15 U.S.C. 77e) paren + structural href — resolves via the non-positive route
    # and materializes the 15-day -> 19-day strike-insert; (2) an act-named target
    # whose only USC ref is a §636 ``note`` cross-ref — an UNCODIFIED note, held out
    # (never claimed onto §636).
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        "<congress>117</congress><docNumber>5</docNumber>"
        "<approvedDate>2022-01-01</approvedDate></meta><main>"
        "<section><num>1</num><content>"
        'Section 5 of the Securities Act of 1933 (<ref href="/us/usc/t15/s77e">15 U.S.C. 77e</ref>), '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>15-day</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>19-day</quotedText>”.</content></section>"
        "<section><num>2</num><content>"
        'Section 7(b) of the Small Business Act (<ref href="/us/usc/t15/s636/note">15 U.S.C. 636 note</ref>) '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>old</quotedText>”.</content></section>"
        "</main></uslm>"
    ).encode("utf-8")


def test_nonpositive_title15_act_section_amendment_materializes_in_agreement() -> None:
    report = build_us_dry_run(
        before_htm=_nonpositive_title15_before(),
        after_htm=_nonpositive_title15_after(),
        plaw_blobs={"PL 117-5": _nonpositive_title15_plaw()},
        title=15,
        before_year="2020",
        after_year="2022",
    )
    # The oracle changed exactly §77e (15-day -> 19-day); §636 is unchanged.
    assert report.oracle_changed_sections == ("15:77e",)
    rows = {row.section_key: row for row in report.rows}
    assert "15:77e" in rows
    row = rows["15:77e"]
    # The act-section target resolved through the non-positive route and the
    # strike-insert materialized exactly the oracle after-text.
    assert row.status == "agree"
    assert row.rule_id == US_DRY_RUN_SECTION_AGREES_RULE_ID
    assert "19-day" in row.materialized_text
    assert "15-day" not in row.materialized_text
    # The note-only §636 target is UNCODIFIED: held out, never claimed (no §636 row
    # and no §636 over-claim in the boundary).
    assert "15:636" not in rows
    assert "15:636" not in report.claimed_sections
    assert report.replay_authorized is False


def test_nonpositive_note_only_target_is_an_uncodified_holdout_not_a_claim() -> None:
    # Defence on the lowering side: the §636 ``note`` target lowers to NO op (it is
    # held out as unresolved), so the dry-run never materializes it. The §77e op is
    # the only one that reaches the kernel.
    report = lower_plaw_amendatory(
        _nonpositive_title15_plaw(), statute_id="PL 117-5", enacted="2022-01-01"
    )
    targets = {
        str(op.target.path[:2]) for op in report.operations() if op.target.path
    }
    assert "(('title', '15'), ('section', '77e'))" in targets
    # No op was emitted onto the §636 codified section from the note-only target.
    assert "(('title', '15'), ('section', '636'))" not in targets


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


def _synthetic_subsection_section_htm() -> bytes:
    # A tiny USC title htm with one section (§77) whose body has subsection (a),
    # subsection (b) with paragraphs (1)/(2). Exercises the sub-section split the
    # sub-section-scoped materialization lever depends on.
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T11</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>"
        "<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 77 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;77. Subsection demo</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">(a) The first subsection mentions a 15-year window.</p>'
        '<p class="statutory-body">(b) The second subsection has paragraphs—</p>'
        '<p class="statutory-body-1em">(1) the first paragraph mentions a 15-year window;</p>'
        '<p class="statutory-body-1em">(2) the second paragraph stands alone.</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")


def _section77_before() -> UscSection:
    doc = parse_usc_title_document(_synthetic_subsection_section_htm(), title=11, year="2018")
    section = doc.section_by_number("77")
    assert section is not None
    return section


def test_subsection_text_replace_is_scoped_to_the_target_node() -> None:
    # A TEXT_REPLACE targeting subsection (b) paragraph (1) must edit ONLY that
    # node's "15-year" — not the identical phrase in subsection (a). A whole-section
    # string replace would hit subsection (a)'s occurrence first (the wrong place).
    section = _section77_before()
    before_text = section.statutory_text
    op = LegalOperation(
        op_id="synthetic-subsection-text-replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="15-year", occurrence=0),
            replacement="17-year",
        ),
    )
    outcome = _materialize_one(op, before_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # Subsection (a)'s "15-year" is untouched; paragraph (b)(1)'s is now "17-year".
    assert "first subsection mentions a 15-year" in materialized
    assert "the first paragraph mentions a 17-year window" in materialized
    assert materialized.count("15-year") == 1


def test_subsection_replace_op_materializes_at_the_target_node() -> None:
    # A whole-node REPLACE (amend-to-read) targeting subsection (b) paragraph (2)
    # substitutes the payload for THAT node only — no longer a blanket refusal.
    section = _section77_before()
    before_text = section.statutory_text
    op = LegalOperation(
        op_id="synthetic-subsection-replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "2"))
        ),
        payload=IRNode(
            kind=IRNodeKind.PARAGRAPH, label="2", text="(2) the second paragraph was rewritten."
        ),
    )
    outcome = _materialize_one(op, before_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "the second paragraph was rewritten." in materialized
    assert "the second paragraph stands alone." not in materialized
    # The other nodes are preserved verbatim.
    assert "first subsection mentions a 15-year" in materialized


def test_subsection_op_without_locatable_node_is_typed_residual_not_wrong_materialization() -> None:
    # When the targeted sub-section node cannot be located (no before_section, or an
    # earlier op moved it), we surface a typed residual — never a whole-section
    # string replace that would edit the wrong place, and never a wrong payload
    # substitution.
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
    outcome = _materialize_one(op, "the whole section 101 before text")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, disposition = outcome
    assert materialized == ""
    assert signal_rule_id == US_DRY_RUN_RESIDUAL_SUBSECTION_NODE_NOT_LOCATED_RULE_ID
    assert disposition == DISPOSITION_LAWVM_WRONG


def _section78_repeated_anchor_htm() -> bytes:
    # §78: BOTH subsection (a) and paragraph (b)(1) carry the anchor phrase
    # "the number". A node-scoped single-occurrence patch on (b)(1) must edit only
    # inside that node's span and leave (a) untouched — exactly the §526 ('20')
    # pathology where a short anchor could otherwise strike the wrong sub-section.
    return (
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>T11</title><!-- AUTHORITIES-USC-TITLE-ENUM:11 --></head><body><div>"
        "<!-- expcite:TITLE 11!@!CHAPTER 1!@!Sec. 78 -->"
        '<!-- field-start:head --><h3 class="section-head">&sect;78. Repeated anchor demo</h3>'
        "<!-- field-end:head --><!-- field-start:statute -->"
        '<p class="statutory-body">(a) The number of seats is fixed.</p>'
        '<p class="statutory-body">(b) The board provides—</p>'
        '<p class="statutory-body-1em">(1) the number of members shall not exceed the number set by rule;</p>'
        "<!-- field-end:statute --></div></body></html>"
    ).encode("utf-8")


def test_node_scoped_single_occurrence_patch_edits_only_inside_the_target_node() -> None:
    # A single-occurrence TEXT_REPLACE targeting (b)(1) confines the edit to that
    # node: the FIRST "the number" inside (b)(1) is replaced (faithful amendatory
    # first-occurrence semantics), while subsection (a)'s identical "The number of
    # seats" — in a DIFFERENT node — is never touched (the §526 wrong-occurrence
    # guard). A whole-section string replace would have hit (a)'s occurrence first.
    doc = parse_usc_title_document(_section78_repeated_anchor_htm(), title=11, year="2018")
    section = doc.section_by_number("78")
    assert section is not None
    op = LegalOperation(
        op_id="repeated-anchor-replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "78"), ("subsection", "b"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="the number", occurrence=0),
            replacement="the count",
        ),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # Subsection (a) is in a different node: its "The number of seats" is untouched.
    assert "(a) The number of seats is fixed." in materialized
    # Inside (b)(1) the first occurrence is replaced; the second stays (a multi-
    # occurrence edit would be lowered as a separate op per occurrence).
    assert "(1) the count of members shall not exceed the number set by rule;" in materialized


def test_node_scoped_each_place_patch_with_repeated_anchor_replaces_every_node_occurrence() -> None:
    # An each-place patch (occurrence == -1) is unambiguous by construction: it
    # replaces EVERY occurrence of the anchor inside the located node, and only that
    # node (subsection (a)'s "the number" is untouched).
    doc = parse_usc_title_document(_section78_repeated_anchor_htm(), title=11, year="2018")
    section = doc.section_by_number("78")
    assert section is not None
    op = LegalOperation(
        op_id="repeated-anchor-each-place",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("title", "11"), ("section", "78"), ("subsection", "b"), ("paragraph", "1"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="the number", occurrence=-1),
            replacement="the count",
        ),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # (a)'s "The number of seats" is untouched; (b)(1)'s two anchors both relabelled.
    assert "The number of seats is fixed." in materialized
    assert "the count of members shall not exceed the count set by rule;" in materialized


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
# Structural materialization at sub-section granularity (strike / insert / renumber)
# ---------------------------------------------------------------------------


def test_strike_subsection_repeal_removes_the_node_and_recomposes() -> None:
    # "by striking subsection (b)" -> remove subsection (b) (and its paragraphs)
    # from the section text; subsection (a) survives verbatim.
    section = _section77_before()
    op = LegalOperation(
        op_id="strike-subsection-b",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("title", "11"), ("section", "77"), ("subsection", "b"))),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "first subsection mentions a 15-year" in materialized
    assert "second subsection has paragraphs" not in materialized


def test_strike_subsection_of_an_absent_node_is_refused_not_a_wrong_deletion() -> None:
    # A strike of a sub-section node NOT present in the before edition (introduced
    # by an un-lowered sibling op, or a conditional/sunset strike) is a no-op
    # against the before text — a typed refusal, never a wrong (over-broad) deletion
    # that would tank the section's other ops.
    section = _section77_before()
    op = LegalOperation(
        op_id="strike-absent-subsection-z",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("title", "11"), ("section", "77"), ("subsection", "z"))),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert isinstance(outcome, USDryRunRefusal)
    assert outcome.rule_id == US_DRY_RUN_REFUSED_STRUCTURAL_NOT_SECTION_REPRESENTABLE_RULE_ID


def test_insert_node_after_a_paragraph_splices_payload_after_the_anchor_node() -> None:
    # An INSERT anchored at subsection (b) paragraph (1) splices the payload
    # immediately AFTER that node's span (not at the section end).
    section = _section77_before()
    op = LegalOperation(
        op_id="insert-node-after-b1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("title", "11"), ("section", "77"))),
        anchor=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1"))
        ),
        payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="1A", text="(1A) a spliced paragraph;"),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # The spliced node sits between paragraph (1) and paragraph (2).
    one = materialized.index("first paragraph mentions a 15-year window;")
    spliced = materialized.index("(1A) a spliced paragraph;")
    two = materialized.index("second paragraph stands alone.")
    assert one < spliced < two


def test_renumber_subsection_relabels_only_the_leading_enumerator() -> None:
    # "redesignating paragraph (1) as paragraph (1A)" relabels ONLY the node's
    # leading "(1)" enumerator inside its located span, never a cross-reference.
    section = _section77_before()
    op = LegalOperation(
        op_id="renumber-b1-to-b1A",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1"))
        ),
        destination=LegalAddress(
            path=(("title", "11"), ("section", "77"), ("subsection", "b"), ("paragraph", "1A"))
        ),
    )
    outcome = _materialize_one(op, section.statutory_text, before_section=section)
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    assert "(1A) the first paragraph mentions a 15-year window;" in materialized
    # Subsection (a)'s leading "(a)" and paragraph (2) are untouched.
    assert "(2) the second paragraph stands alone." in materialized


# ---------------------------------------------------------------------------
# Editorial quote/spacing classification (generalized F1 -> oracle_suspect)
# ---------------------------------------------------------------------------


def test_norm_editorial_undoes_comma_anchor_courtesy_space() -> None:
    # The comma-anchor generalization of F1: the enacted insert-after places matter
    # directly after a comma anchor (no space; the quotedText carries no leading
    # space); the published Code adds a courtesy space.
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    faithful = "the positions of physician,optometrist, dentist"
    published = "the positions of physician, optometrist, dentist"
    assert normalize_inline_comparison_text(faithful) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(faithful) == _norm_editorial(published)
    # It does NOT mask a real content divergence into a false agreement.
    other = "the positions of physician, dentist"
    assert _norm_editorial(faithful) != _norm_editorial(other)


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


def test_norm_editorial_folds_straight_and_curly_quote_shapes() -> None:
    # The enacted USLM amendment wraps a defined term in curly quotes; the OLRC
    # consolidated Code re-renders it with straight quotes. Folding quote SHAPE
    # unifies them (the generalized F1 oracle_suspect class).
    from lawvm.core.comparison_normalization import normalize_inline_comparison_text

    enacted = "the term ‘CARES forbearance claim’ means a supplemental claim"
    published = 'the term "CARES forbearance claim" means a supplemental claim'
    assert normalize_inline_comparison_text(enacted) != normalize_inline_comparison_text(
        published
    )
    assert _norm_editorial(enacted) == _norm_editorial(published)
    # Folding quote shape can NEVER manufacture agreement between texts that differ
    # in any non-quote character.
    other = 'the term "CARES forbearance claim" means a SUPPLEMENTARY claim'
    assert _norm_editorial(enacted) != _norm_editorial(other)


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


# ---------------------------------------------------------------------------
# Amend-to-read whole-section payload: project off the leaked catchline
# ---------------------------------------------------------------------------


def test_whole_section_replace_projects_off_its_own_catchline() -> None:
    # "Section 2196 is amended to read as follows: §2196. <heading> “(a) ..." — the
    # payload opens with the section's own catchline before the quoted body. The
    # body-only oracle surface carries the catchline in the heading, not the
    # statutory text, so the materialized body must NOT include the catchline.
    op = LegalOperation(
        op_id="amend-to-read-2196",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("title", "10"), ("section", "2196"))),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="2196",
            text=(
                "§ 2196. Manufacturing engineering education program"
                "“(a) Establishment.—(1) The Secretary shall establish a program."
            ),
        ),
    )
    outcome = _materialize_one(op, "(a) old body.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # The catchline is projected off; the quoted body is retained verbatim (the
    # quotes are oracle editorial, undone only at comparison, never here).
    assert materialized.startswith("“(a) Establishment.—")
    assert "§ 2196." not in materialized
    assert "Manufacturing engineering education program" not in materialized


def test_whole_section_replace_keeps_payload_when_catchline_not_delimitable() -> None:
    # A renamed-heading payload with NO quoted-body marker cannot be delimited
    # safely (the heading may carry internal periods), so the payload is kept
    # verbatim — the residual stays visible, never a guessed cut.
    op = LegalOperation(
        op_id="amend-to-read-no-quote",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("title", "10"), ("section", "3084"))),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="3084",
            text="§ 3084. Chief of Veterinary Corps",
        ),
    )
    outcome = _materialize_one(op, "(a) old body.")
    assert not isinstance(outcome, USDryRunRefusal)
    materialized, signal_rule_id, _disp = outcome
    assert signal_rule_id == ""
    # Kept verbatim (no curly-quote body marker to delimit the catchline at).
    assert materialized == "§ 3084. Chief of Veterinary Corps"
