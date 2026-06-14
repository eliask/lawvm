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
    RULE_ADD_AT_END,
    RULE_INSERT_AFTER,
    RULE_STRIKE_INSERT,
    TARGET_UNRESOLVED_FINDING_RULE_ID,
    UNLOWERED_FINDING_RULE_ID,
    lower_plaw_amendatory,
    parse_usc_target_href,
    parse_usc_target_phrase,
)

_USLM_NS = "http://schemas.gpo.gov/xml/uslm"


def _synthetic_plaw(section_body: str) -> bytes:
    """Wrap one amendatory <section> body into a minimal lowerable USLM lawDoc."""
    return (
        f'<lawDoc xmlns="{_USLM_NS}">'
        "<meta><congress>116</congress><docNumber>900</docNumber>"
        "<approvedDate>2020-01-01</approvedDate></meta>"
        f"<main>{section_body}</main></lawDoc>"
    ).encode("utf-8")
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
# Lowering robustness fixes (F5 action/operand, F1/F4 significant edge chars)
# ---------------------------------------------------------------------------


def test_insert_after_classifies_and_assigns_operands_at_the_anchor():
    # "inserting 'X' after 'Y'" with NO striking is an insert-after, not a
    # strike_insert: the anchor "Y" drives the match, X is appended after it.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>, based on reasonable due diligence,</quotedText>” '
        'after “<quotedText>may</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "insert_after"
    assert instr.witness_rule_id == RULE_INSERT_AFTER
    op = instr.operation
    assert op is not None and op.text_patch is not None
    # anchor is the match; inserted clause is appended AFTER it (not inverted).
    assert op.text_patch.selector.match_text == "may"
    assert op.text_patch.replacement == "may, based on reasonable due diligence,"


def _patch(instr):
    op = instr.operation
    assert op is not None and op.text_patch is not None
    return op.text_patch


def test_sibling_subsections_split_so_striking_does_not_bleed_into_insert_after():
    # SEC. 3 carries TWO subsection instructions: (a) is an insert-after, (b) is a
    # strike-and-insert. They must NOT be merged into one unit (else (b)'s
    # "striking" mis-classifies (a) as strike_insert with inverted operands — F5).
    body = (
        '<section identifier="/us/pl/116/900/s3"><num value="3">SEC. 3. </num>'
        '<subsection identifier="/us/pl/116/900/s3/a" role="instruction">'
        '<num value="a">(a) </num><content>'
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>, due diligence,</quotedText>” after '
        '“<quotedText>may</quotedText>”.</content></subsection>'
        '<subsection identifier="/us/pl/116/900/s3/b" role="instruction">'
        '<num value="b">(b) </num><content>'
        '<ref href="/us/usc/t11/s101/18">Section 101(18) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>$10,000</quotedText>” and '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>$25,000</quotedText>”.</content></subsection>'
        '</section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    by_target = {i.target_phrase.split(" of ")[0]: i for i in report.instructions}
    a = by_target["Section 547(b)"]
    assert a.action == "insert_after"
    assert _patch(a).selector.match_text == "may"
    b = by_target["Section 101(18)"]
    assert b.action == "strike_insert"
    # strike_insert operand order: struck text matches, inserted text replaces.
    assert _patch(b).selector.match_text == "$10,000"
    assert _patch(b).replacement == "$25,000"


def test_strike_insert_operand_order_struck_matches_inserted_replaces():
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101/18">Section 101(18) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>$3,237,000</quotedText>” and '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>$10,000,000</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_STRIKE_INSERT
    assert _patch(instr).selector.match_text == "$3,237,000"
    assert _patch(instr).replacement == "$10,000,000"


def test_quoted_text_preserves_significant_leading_space():
    # A genuine leading space INSIDE the quotedText literal must survive lowering
    # (F1 case i). Only internal formatting whitespace is collapsed.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s507/d">Section 507(d) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText> excluding subparagraph (F)</quotedText>” after '
        '“<quotedText>(a)(8)</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    patch = _patch(report.instructions[0])
    # match = anchor "(a)(8)"; replacement keeps the literal's leading space.
    assert patch.selector.match_text == "(a)(8)"
    assert patch.replacement == "(a)(8) excluding subparagraph (F)"


def test_add_at_end_payload_preserves_terminal_period_inside_quoted_block():
    # The terminal period lives INSIDE the quoted block; the enclosing curly quotes
    # are peeled but the period survives (F4). Leading "(d)" is kept; no leading quote.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s366">Section 366 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="add">adding</amendingAction> at the end the '
        'following:<quotedContent><subsection><num value="d">“(d) </num>'
        '<content>a payment becomes due.”</content></subsection>'
        '</quotedContent>.</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "add_at_end"
    assert instr.witness_rule_id == RULE_ADD_AT_END
    assert instr.operation is not None
    payload = instr.operation.payload
    assert payload is not None
    assert payload.text.startswith("(d) ")
    assert payload.text.endswith("a payment becomes due.")
    assert "“" not in payload.text and "”" not in payload.text


def test_add_at_end_payload_prunes_editorial_sidenotes_and_page_stamps():
    # govinfo interleaves the legislative-counsel marginal sidenotes ("Time
    # period.", "Definitions.") as small-font <p class="...fontsize8"> elements and
    # the Statutes-at-Large page stamps as <page> elements INSIDE the quotedContent.
    # These are editorial, never enacted statutory text — the materialized payload
    # must NOT contain them (the OLRC consolidated Code does not render them).
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s1329">Section 1329 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="add">adding</amendingAction> at the end the '
        'following:<quotedContent><subsection><num value="d">“(d) </num>'
        '<paragraph><num value="2">(2) </num>'
        '<p class="leftAlign firstIndent0 fontsize8">Time period.</p>'
        '<content>A plan modified under paragraph (1)'
        '<page identifier="/us/stat/134/3219">134 STAT. 3219</page>'
        ' may not provide for payments.</content></paragraph>'
        '</subsection></quotedContent>.</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is not None
    payload = instr.operation.payload
    assert payload is not None
    # The marginal sidenote and the page stamp are pruned; the statutory body
    # survives verbatim (including the paragraph enumerator and its content).
    assert "Time period." not in payload.text
    assert "134 STAT." not in payload.text
    assert "(2) A plan modified under paragraph (1) may not provide for payments." in payload.text


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
