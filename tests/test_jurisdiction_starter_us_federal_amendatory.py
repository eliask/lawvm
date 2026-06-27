"""Starter-shard tests for the U.S. federal amendatory lowering surface.

Covers: prose/href USC target-address parsing under the PINNED convention; the
strike/insert -> TEXT_REPLACE lowering on real Title-11 fixtures; the each-place
occurrence flag; a typed finding for an instruction we deliberately cannot lower
(named-act target with no USC title); and the window scan over a tmp farchive
built from fixtures (no network).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.branch_authority import PENDING_CONDITION_STATUS
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.us_federal.amendatory import (
    COMPOUND_STRIKE_INSERT_FINDING_RULE_ID,
    DEFERRED_AMEND_TO_READ_FINDING_RULE_ID,
    DESIGNATION_STRIKE_FINDING_RULE_ID,
    HEADING_STRIKE_FINDING_RULE_ID,
    NON_TITLE_TARGET_RULE_ID,
    RULE_ADD_AT_END,
    RULE_ADD_AT_END_NEW_SECTIONS,
    RULE_INSERT_AFTER,
    RULE_INSERT_BEFORE,
    RULE_INSERT_END_PUNCT,
    RULE_INSERT_NODE_AFTER,
    RULE_REDESIGNATE_PAIRS,
    RULE_REDESIGNATE_RANGE,
    RULE_REDESIGNATE_TABLE,
    RULE_STRIKE_INSERT,
    RULE_STRIKE_INSERT_END_PUNCT,
    RULE_STRIKE_INSERT_PUNCT_WORD,
    RULE_STRIKE_INSERT_THROUGH_TAIL,
    RULE_STRIKE_UNIT,
    RULE_STRIKE_UNIT_LIST,
    SENTENCE_STRIKE_FINDING_RULE_ID,
    TAIL_STRIKE_FINDING_RULE_ID,
    TAIL_STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID,
    TARGET_UNRESOLVED_FINDING_RULE_ID,
    UNLOWERED_FINDING_RULE_ID,
    _first_usc_ref,
    _join_insert_after,
    _join_insert_before,
    _payload_opens_new_section,
    _resolve_target,
    lower_plaw_amendatory,
    parse_relative_usc_target,
    parse_usc_target_href,
    parse_usc_target_phrase,
    TARGET_TITLE_FROM_SECTION_CLASSIFICATION,
    TARGET_TITLE_FROM_PLAW_METADATA,
    PLAW_METADATA_SCOPE_CONFLICT_RULE_ID,
)

_USLM_NS = "http://schemas.gpo.gov/xml/uslm"


def _synthetic_plaw_with_title(title_text: str, section_body: str) -> bytes:
    """Wrap a section body with a dc:title naming a USC title."""
    dc_ns = "http://purl.org/dc/elements/1.1/"
    return (
        f'<lawDoc xmlns="{_USLM_NS}" xmlns:dc="{dc_ns}">'
        "<meta><congress>116</congress><docNumber>900</docNumber>"
        "<approvedDate>2020-01-01</approvedDate>"
        f"<dc:title>{title_text}</dc:title></meta>"
        f"<main>{section_body}</main></lawDoc>"
    ).encode("utf-8")


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
    assert addr == LegalAddress(path=(("title", "11"), ("section", "362"), ("subsection", "c"), ("paragraph", "1")))


def test_prose_target_phrase_lowercase_and_no_us_code_suffix():
    addr = parse_usc_target_phrase("section 1325(b)(4) of title 11")
    assert addr == LegalAddress(path=(("title", "11"), ("section", "1325"), ("subsection", "b"), ("paragraph", "4")))


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


def test_href_full_ladder_chain_is_not_truncated():
    # 18:2261A repro: /s2261A/b/1/A/ii must keep EVERY rung of the descent — the
    # leaf clause (ii) hangs under subparagraph (A) under paragraph (1) under
    # subsection (b), never a bare ``clause:ii`` with the ancestors dropped.
    assert parse_usc_target_href("/us/usc/t18/s2261A/b/1/A/ii") == LegalAddress(
        path=(
            ("title", "18"),
            ("section", "2261A"),
            ("subsection", "b"),
            ("paragraph", "1"),
            ("subparagraph", "A"),
            ("clause", "ii"),
        )
    )


def test_href_leading_roman_letter_is_a_subsection_not_a_clause():
    # 18:983 repro: a leading single roman-ambiguous letter ``/s983/i`` is the
    # SUBSECTION (i), and the chain stays in ladder order subsection→paragraph→
    # subparagraph (NOT the out-of-order clause:i/paragraph:2/subparagraph:D the
    # isolated-token typing produced).
    assert parse_usc_target_href("/us/usc/t18/s983/i/2/D") == LegalAddress(
        path=(
            ("title", "18"),
            ("section", "983"),
            ("subsection", "i"),
            ("paragraph", "2"),
            ("subparagraph", "D"),
        )
    )


def test_prose_leading_roman_letter_subsection_matches_href_typing():
    # The prose channel must type the leading "(i)" identically to the href, so a
    # path and the split node it locates against share one (kind,label) convention.
    assert parse_usc_target_phrase("section 983(i)(2)(D) of title 18") == LegalAddress(
        path=(
            ("title", "18"),
            ("section", "983"),
            ("subsection", "i"),
            ("paragraph", "2"),
            ("subparagraph", "D"),
        )
    )


def test_chain_typer_does_not_fabricate_a_level_not_present_in_the_href():
    # The chain has exactly the rungs the href names — no invented intervening
    # level. A two-segment href types to exactly two below-section segments.
    addr = parse_usc_target_href("/us/usc/t18/s2261A/b/1")
    assert addr is not None
    below = [seg for seg in addr.path if seg[0] not in ("title", "section")]
    assert below == [("subsection", "b"), ("paragraph", "1")]


# ---------------------------------------------------------------------------
# Non-positive-law title routing through the act-section→USC resolver
# ---------------------------------------------------------------------------


def test_nonpositive_target_resolves_via_act_section_resolver():
    # A non-positive title (15 Commerce): the enacted target names a free-standing
    # Act; the codified address comes from the (N U.S.C. M) paren + structural href.
    # Routing through the non-positive resolver yields the USC address with a
    # ``nonpositive_*`` resolution status (paren+href agree here).
    address, status = _resolve_target("Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)", "/us/usc/t15/s77e")
    assert address == LegalAddress(path=(("title", "15"), ("section", "77e")))
    assert status == "nonpositive_paren_href_agree"


def test_nonpositive_note_only_target_is_held_out_never_guessed():
    # A non-positive target whose only codified channel is a ``note`` cross-ref is
    # an UNCODIFIED Statutes-at-Large note: it is held out (unresolved), never
    # guessed onto the codified section t7/s2011. The Prime Directive at the
    # lowering boundary.
    address, status = _resolve_target(
        "Section 702(a) of division N of the Consolidated Appropriations Act, 2021",
        "/us/usc/t7/s2011/note",
    )
    assert address is None
    assert status == "unresolved"


def test_nonpositive_irc_single_letter_subsection_typed_by_position():
    # IRC "(l)" is a SUBSECTION, not a roman-numeral clause "l": the non-positive
    # resolver types it by nesting position. The positive-law href path would
    # mis-type it as a clause.
    address, _status = _resolve_target(
        "Section 461(l)(1) of the Internal Revenue Code of 1986 (26 U.S.C. 461(l)(1))",
        "/us/usc/t26/s461/l/1",
    )
    assert address == LegalAddress(
        path=(
            ("title", "26"),
            ("section", "461"),
            ("subsection", "l"),
            ("paragraph", "1"),
        )
    )


def test_nonpositive_routing_does_not_consult_raw_text_paren_cross_ref():
    # A stray "(42 U.S.C. 4332)" cross-citation in the instruction BODY must never
    # hijack the target: only the unit's OWN phrase / href are consulted. With no
    # own codified channel the non-positive target stays unresolved.
    address, status = _resolve_target(
        "Chapter 1 of title 15, United States Code, is amended",
        "",
        raw_text="see section 102 (42 U.S.C. 4332) of this Act",
    )
    assert address is None
    assert status == "unresolved"


def test_positive_law_title_routing_is_unchanged():
    # A positive-law title (11) is NOT routed through the non-positive resolver: the
    # prose/href direct path resolves it with the existing status vocabulary.
    address, status = _resolve_target("Section 362 of title 11, United States Code", "/us/usc/t11/s362")
    assert address == LegalAddress(path=(("title", "11"), ("section", "362")))
    assert status == "prose_href_agree"


# ---------------------------------------------------------------------------
# Strike/insert lowering on real Title-11 fixtures
# ---------------------------------------------------------------------------


def test_plaw_116_52_strike_subparagraph_and_insert_block_replace():
    report = lower_plaw_amendatory(_read("PLAW-116publ52.xml"))
    assert report.statute_id == "PL 116-52"
    assert report.enacted == "2019-08-23"
    assert "title 11" in report.title_targets
    # One amendatory instruction: §101(10A) strike subparagraph (B) + insert block.
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
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
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
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
    assert op.target == LegalAddress(path=(("title", "11"), ("section", "101"), ("paragraph", "18")))


def test_precise_text_strike_with_roman_ambiguous_subsection_head_is_section_scoped():
    # 10:284 regression defense: a precise (two-quoted) strike whose target's leading
    # sub-section letter is a roman-form letter (the source-tree split flags it as
    # ambiguous and may leave a PHANTOM duplicate ``subsection:i`` node) must lower
    # with a SECTION-scoped op target, so the dry-run anchors on the unique
    # match_text rather than risking a locate onto the phantom node. The strike's
    # text patch is preserved verbatim; only the op target is relaxed to the section.
    body = (
        '<section identifier="/us/pl/116/900/s2" role="instruction">'
        "<num>2.</num>"
        "<content>"
        '<ref href="/us/usc/t11/s284/i/3">Section 284(i)(3) of title 11, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>linguist and intelligence analysis</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>linguist, intelligence analysis, and planning</quotedText>”."
        "</content>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    assert len(accepted) == 1
    instr = accepted[0]
    assert instr.action == "strike_insert"
    # The resolved target_address still records the FULL ladder (subsection (i) ...).
    assert instr.target_address == LegalAddress(
        path=(
            ("title", "11"),
            ("section", "284"),
            ("subsection", "i"),
            ("paragraph", "3"),
        )
    )
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.TEXT_REPLACE
    # But the emitted OP target is section-scoped (drops the roman-ambiguous head),
    # so the strike anchors on its unique match_text, not the phantom split node.
    assert op.target == LegalAddress(path=(("title", "11"), ("section", "284")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "linguist and intelligence analysis"
    assert op.text_patch.replacement == "linguist, intelligence analysis, and planning"


def test_precise_text_strike_with_letter_subsection_head_keeps_full_path():
    # Control: a NON-roman subsection head ((b), not (i)) is unambiguous, so the
    # precise strike keeps its full ladder target — the section-scoping fallback
    # fires ONLY for the roman-ambiguous head, never for an ordinary letter.
    body = (
        '<section identifier="/us/pl/116/900/s3" role="instruction">'
        "<num>3.</num>"
        "<content>"
        '<ref href="/us/usc/t11/s284/b/9">Section 284(b)(9) of title 11, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>$750,000</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>$1,000,000</quotedText>”."
        "</content>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    assert len(accepted) == 1
    op = accepted[0].operation
    assert op is not None
    assert op.target == LegalAddress(
        path=(
            ("title", "11"),
            ("section", "284"),
            ("subsection", "b"),
            ("paragraph", "9"),
        )
    )


def test_ancestor_target_carrier_threads_its_own_subunit_anchor_to_leaf():
    # Regression for 10 U.S.C. §8090 (PL 118-159 §923). A subsection carries the
    # section target in its chapeau AND a scope anchor in the same chapeau:
    # "Section 8090 ... is amended, in subsection (a)-". A nested paragraph then
    # says "(1) in paragraph (4), by striking ...". The ancestor's "in
    # subsection (a)" anchor must thread onto the section address before the leaf
    # anchor refines it, so the resolved target is /subsection:a/paragraph:4,
    # not a phantom /paragraph:4 directly under the section.
    body = (
        '<section identifier="/us/pl/118/159/dA/tIX/stB/s923">'
        '<num value="923">SEC. 923. </num>'
        "<heading>CODIFICATION.</heading>"
        '<subsection identifier="/us/pl/118/159/dA/tIX/stB/s923/a" role="instruction">'
        '<num value="a">(a) </num>'
        "<heading>Codification.-</heading>"
        '<chapeau><ref href="/us/usc/t10/s8090">Section 8090 of title 10, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction>, '
        "in subsection (a)-</chapeau>"
        '<paragraph identifier="/us/pl/118/159/dA/tIX/stB/s923/a/1">'
        '<num value="1">(1) </num>'
        '<content>in paragraph (4), by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>and</quotedText>";'
        "</content>"
        "</paragraph>"
        "</subsection>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    expected = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "8090"),
            ("subsection", "a"),
            ("paragraph", "4"),
        )
    )
    assert instr.target_address == expected


def test_ancestor_target_carrier_without_subunit_anchor_leaves_section_scope_to_leaf():
    # Negative control for test_ancestor_target_carrier_threads_its_own_subunit_anchor_to_leaf.
    # When the ancestor chapeau names only the section target (no "in subsection (a)-"
    # scope anchor), a leaf "in paragraph (4), by striking ..." resolves directly
    # under the section and does not invent a phantom subsection.
    body = (
        '<section identifier="/us/pl/118/159/dA/tIX/stB/s923">'
        '<num value="923">SEC. 923. </num>'
        "<heading>CODIFICATION.</heading>"
        '<subsection identifier="/us/pl/118/159/dA/tIX/stB/s923/a" role="instruction">'
        '<num value="a">(a) </num>'
        "<heading>Codification.-</heading>"
        '<chapeau><ref href="/us/usc/t10/s8090">Section 8090 of title 10, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction>-</chapeau>'
        '<paragraph identifier="/us/pl/118/159/dA/tIX/stB/s923/a/1">'
        '<num value="1">(1) </num>'
        '<content>in paragraph (4), by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>and</quotedText>";'
        "</content>"
        "</paragraph>"
        "</subsection>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    expected = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "8090"),
            ("paragraph", "4"),
        )
    )
    assert instr.target_address == expected


def test_ancestor_anchor_is_case_insensitive_for_title_case_chapeau():
    # Regression for PL 118-159 §557. USLM paragraph chapeaux use title-case "In"
    # after the enumerator: "(2) In subsection (b)—". The intermediate ancestor anchor
    # must thread onto the inherited section so a leaf "(A) in paragraph (1)" lands
    # at /subsection:b/paragraph:1, not a phantom /paragraph:1 under the section.
    body = (
        '<section identifier="/us/pl/118/159/dA/tV/stF/s557">'
        "<num value=\"557\">SEC. 557. </num>"
        "<heading>ALTERNATIVE SERVICE.</heading>"
        '<subsection identifier="/us/pl/118/159/dA/tV/stF/s557/a" role="instruction">'
        '<num value="a">(a) </num>'
        "<heading>United States Military Academy.—</heading>"
        '<chapeau><ref href="/us/usc/t10/s7448">Section 7448 of title 10, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction> as follows:'
        "</chapeau>"
        '<paragraph identifier="/us/pl/118/159/dA/tV/stF/s557/a/2">'
        '<num value="2">(2) </num>'
        "<chapeau>In subsection (b)—</chapeau>"
        '<subparagraph identifier="/us/pl/118/159/dA/tV/stF/s557/a/2/A">'
        '<num value="A">(A) </num>'
        '<content>in paragraph (1), by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>X</quotedText>";'
        "</content>"
        "</subparagraph>"
        "</paragraph>"
        "</subsection>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    expected = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "7448"),
            ("subsection", "b"),
            ("paragraph", "1"),
        )
    )
    assert instr.target_address == expected


def test_leaf_anchor_is_case_insensitive_for_title_case_chapeau():
    # A leaf whose own scope anchor is title-case "In paragraph (1)" (rather than
    # lowercase "in paragraph") still refines the inherited address.
    body = (
        '<section identifier="/us/pl/118/159/dA/tV/stF/s557">'
        "<num value=\"557\">SEC. 557. </num>"
        "<heading>ALTERNATIVE SERVICE.</heading>"
        '<subsection identifier="/us/pl/118/159/dA/tV/stF/s557/a" role="instruction">'
        '<num value="a">(a) </num>'
        "<heading>United States Military Academy.—</heading>"
        '<chapeau><ref href="/us/usc/t10/s7448">Section 7448 of title 10, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction> as follows:'
        "</chapeau>"
        '<paragraph identifier="/us/pl/118/159/dA/tV/stF/s557/a/2">'
        '<num value="2">(2) </num>'
        "<chapeau>In subsection (b)—</chapeau>"
        '<subparagraph identifier="/us/pl/118/159/dA/tV/stF/s557/a/2/A">'
        '<num value="A">(A) </num>'
        '<content>In paragraph (1), by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>X</quotedText>";'
        "</content>"
        "</subparagraph>"
        "</paragraph>"
        "</subsection>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    expected = LegalAddress(
        path=(
            ("title", "10"),
            ("section", "7448"),
            ("subsection", "b"),
            ("paragraph", "1"),
        )
    )
    assert instr.target_address == expected


def test_plaw_117_177_strike_insert_off_title_11_is_needs_review_with_finding():
    # Targets title 18, not 11: resolvable, but withheld from Title-11 scope.
    report = lower_plaw_amendatory(_read("PLAW-117publ177.xml"))
    instr = report.instructions[0]
    assert instr.action == "strike_insert"
    assert instr.instruction_status == "needs_review"
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.TEXT_REPLACE
    assert instr.target_address is not None
    assert instr.target_address.path[0] == ("title", "18")
    assert instr.finding is not None
    assert instr.finding.rule_id == NON_TITLE_TARGET_RULE_ID


def test_proof_title_parameter_accepts_on_title_op_without_off_title_finding():
    # §1.1 audit: lower_plaw_amendatory previously hardcoded Title 11 in the
    # on-title-scope check, marking any op whose resolved address targeted a
    # different title as needs_review with NON_TITLE_TARGET_RULE_ID. With the
    # non-positive-law Title 42 ACA bench corpus that produced 1,258 spurious
    # finding-noise rows for ops that DO target the proof title.
    #
    # proof_title parameter threads the proof-scope through: a Title-42 target
    # under proof_title="42" is accepted (NO NON_TITLE_TARGET finding); under
    # the default proof_title="11" the same op is needs_review + finding.
    body = (
        '<section identifier="/us/pl/117/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Amendment.</heading>"
        '<content><ref href="/us/usc/t42/s10403">Section 10403 of title 42, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></section>'
    )
    plaw = _synthetic_plaw_with_title(
        "To amend title 42, United States Code, ...", body
    )
    # Default proof_title="11": the on-title-42 op is mis-flagged as off-title.
    report_default = lower_plaw_amendatory(plaw)
    instr_default = report_default.instructions[0]
    assert instr_default.target_address == LegalAddress(
        path=(("title", "42"), ("section", "10403"))
    )
    assert instr_default.instruction_status == "needs_review"
    assert instr_default.finding is not None
    assert instr_default.finding.rule_id == NON_TITLE_TARGET_RULE_ID

    # proof_title="42": the SAME op is accepted on proof title, no finding.
    report_proof_42 = lower_plaw_amendatory(plaw, proof_title="42")
    instr_42 = report_proof_42.instructions[0]
    assert instr_42.target_address == instr_default.target_address
    assert instr_42.instruction_status == "accepted"
    assert instr_42.finding is None


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
        "“<quotedText>, based on reasonable due diligence,</quotedText>” "
        "after “<quotedText>may</quotedText>”.</content></section>"
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


def test_insert_after_word_anchor_preserves_boundary_space():
    # 10:1161 (PL 114-328 §507): "inserting 'or the Secretary…,' after 'President'".
    # The OLRC body renders 'President or the Secretary…' — a single boundary space
    # joins the anchor word to the inserted phrase. Concatenating them naively
    # ('Presidentor the Secretary…') is the dominant US-frontend residual; the join
    # restores the genuine inter-word space the enacted result carries.
    body = (
        '<section identifier="/us/pl/114/328/s507"><num value="507">SEC. 507. </num>'
        '<content><ref href="/us/usc/t11/s1161/b">Section 1161(b) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>or the Secretary of Defense,</quotedText>” after "
        "“<quotedText>President</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    op = report.instructions[0].operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.selector.match_text == "President"
    assert op.text_patch.replacement == "President or the Secretary of Defense,"


def test_insert_after_word_anchor_space_for_air_force_space_force():
    # 10:9203-class: "inserting 'or the Space Force' after 'the Air Force'" → the
    # OLRC body reads 'the Air Force or the Space Force'.
    body = (
        '<section identifier="/us/pl/118/22/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s9203">Section 9203 of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>or the Space Force</quotedText>” after "
        "“<quotedText>the Air Force</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    op = report.instructions[0].operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.replacement == "the Air Force or the Space Force"


def test_insert_after_does_not_invent_a_space_at_a_punctuation_junction():
    # When the inserted phrase OPENS with attaching punctuation (a comma), the OLRC
    # body binds it directly to the anchor word with NO separating space:
    # '…trade or business' + ', and' → '…trade or business, and' (never '… , and').
    # This guards the boundary-space fix from inventing spaces the enacted text does
    # not contain.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>, and</quotedText>” after "
        "“<quotedText>business</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    op = report.instructions[0].operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.replacement == "business, and"


def test_join_insert_after_boundary_rule_is_data_backed():
    # Word↔word and word↔open-bracket junctions take a single space (a fresh token is
    # spliced into running prose). Attaching punctuation and pre-supplied edge
    # whitespace take NONE — the join never doubles or invents a separator.
    assert _join_insert_after("section 310", "or 351") == "section 310 or 351"
    assert _join_insert_after("opportunities", "(including X)") == "opportunities (including X)"
    assert _join_insert_after("business", ", and") == "business, and"  # punct → no space
    assert _join_insert_after("clause", " (i)") == "clause (i)"  # insert already spaced
    assert _join_insert_after("(", "subsection") == "(subsection"  # anchor opens bracket → no space
    assert _join_insert_after("word", "") == "word"
    # Terminal punctuation on the anchor side ends a token; the next clause still
    # needs a separating space (38:4303: "Public Health Service," + "System members").
    assert _join_insert_after("Public Health Service,", "System members") == "Public Health Service, System members"
    assert _join_insert_after("Service;", "and a period") == "Service; and a period"
    assert _join_insert_after("Service.", "And next") == "Service. And next"

def test_quoted_text_prunes_inline_page_stamps():
    # govinfo injects <page> stamps inside <quotedText>; the page number is editorial
    # pagination, not enacted text, and must not leak into the materialized clause.
    body = (
        '<section identifier="/us/pl/114/326/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t38/s4303">Section 4303</ref> is amended by '
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText><page identifier=\"/us/stat/130/1973\">130 STAT. 1973</page>"
        "inserted clause.</quotedText>” after “<quotedText>anchor</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    op = report.instructions[0].operation
    assert op is not None and op.text_patch is not None
    assert "130 STAT" not in (op.text_patch.replacement or "")
    assert "inserted clause" in (op.text_patch.replacement or "")

def test_insert_before_word_anchor_places_payload_before_anchor():
    # 38:7309 (PL 114-58 §601(22)): "inserting 'the' before 'Veterans Health
    # Administration'" must yield "the Veterans Health Administration", not the
    # after-anchor order "Veterans Health Administration the".
    body = (
        '<section identifier="/us/pl/114/58/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t38/s7309/c/1">Section 7309(c)(1) of title 38, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>the</quotedText>” before "
        "“<quotedText>Veterans Health Administration</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "insert_after"
    assert instr.witness_rule_id == RULE_INSERT_BEFORE
    op = instr.operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.selector.match_text == "Veterans Health Administration"
    assert op.text_patch.replacement == "the Veterans Health Administration"


def test_join_insert_before_uses_insert_lead_boundary():
    # The boundary rule applies to (inserted text) + (anchor text), so attaching
    # punctuation on the inserted side stays attached and wordword still gets one
    # separating space.
    assert _join_insert_before("the Secretary", ", and") == ", and the Secretary"
    assert _join_insert_before("the Secretary", "or") == "or the Secretary"
    assert _join_insert_before("subsection", "(i)") == "(i) subsection"


def test_sidenote_ref_is_not_treated_as_amendatory_target():
    # 38:117 (PL 114-315 §601(a)): the target is named in the prose as
    # "Section 117(c)" without "of title N"; the title comes from the publisher
    # sidenote ref. The sidenote ref must NOT hijack the target onto the bare
    # section (it would drop the "(c)" scope and insert the new paragraph at
    # section level). The operation must target subsection (c), and section
    # classification must be recorded in provenance.
    body = (
        '<section identifier="/us/pl/114/315/s601"><num value="601">SEC. 601. </num>'
        '<sidenote><p><ref href="/us/usc/t38/s117">38 USC 117</ref>.</p></sidenote>'
        '<subsection identifier="/us/pl/114/315/s601/a"><num value="a">(a) </num>'
        '<heading>In General.</heading>'
        '<content>Section 117(c) <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="add">adding</amendingAction> at the end the following new paragraph:'
        '<quotedContent><paragraph><num value="7">“(7) </num>'
        '<content>Veterans Health Administration, Medical Community Care.</content></paragraph>'
        '</quotedContent>.</content></subsection></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "add_at_end"
    assert instr.witness_rule_id == RULE_ADD_AT_END
    op = instr.operation
    assert op is not None
    assert op.target.path == (("title", "38"), ("section", "117"), ("subsection", "c"))
    assert op.payload is not None
    assert "(7) Veterans Health Administration, Medical Community Care" in (op.payload.text or "")
    assert TARGET_TITLE_FROM_SECTION_CLASSIFICATION in op.provenance_tags


def test_insert_before_parsing_does_not_disturb_after_direction():
    # Ensure the parser still detects "after" direction and emits the existing rule id.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>, based on reasonable due diligence,</quotedText>” "
        "after “<quotedText>may</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_INSERT_AFTER
    op = instr.operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.replacement == "may, based on reasonable due diligence,"



def test_compound_strike_insert_with_block_node_is_held_out_not_corrupted():
    # 26:6050I (PL 117-58 §80603(b)(3)): a positional compound — strike 'and' at the
    # end of paragraph (1), strike the period at the end of paragraph (2) and insert
    # ', and', AND insert after paragraph (2) a whole new paragraph block. The naive
    # 2-operand text_replace grabs strike 'and' / insert ', and' and applies ', and'
    # after an existing comma ('business,, and') while dropping the block. We refuse
    # it as a typed residual rather than emit a corrupt patch.
    body = (
        '<section identifier="/us/pl/117/58/s80603"><num value="80603">SEC. 80603. </num>'
        '<content><ref href="/us/usc/t11/s6050I">Section 6050I(d) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>and</quotedText>” at the end of paragraph (1), by "
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'of paragraph (2) and <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>, and</quotedText>”, and by "
        '<amendingAction type="insert">inserting</amendingAction> after paragraph (2) '
        'the following new paragraph:<quotedContent><paragraph><num value="3">“(3) </num>'
        "<content>any digital asset.”</content></paragraph></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "strike_insert"
    assert instr.operation is None  # no corrupt phrase swap emitted
    assert instr.finding is not None
    assert instr.finding.rule_id == COMPOUND_STRIKE_INSERT_FINDING_RULE_ID


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
        "“<quotedText>, due diligence,</quotedText>” after "
        "“<quotedText>may</quotedText>”.</content></subsection>"
        '<subsection identifier="/us/pl/116/900/s3/b" role="instruction">'
        '<num value="b">(b) </num><content>'
        '<ref href="/us/usc/t11/s101/18">Section 101(18) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>$10,000</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>$25,000</quotedText>”.</content></subsection>"
        "</section>"
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
        "“<quotedText>$3,237,000</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>$10,000,000</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_STRIKE_INSERT
    assert _patch(instr).selector.match_text == "$3,237,000"
    assert _patch(instr).replacement == "$10,000,000"


# ---------------------------------------------------------------------------
# Target operand mis-extraction: a <ref> / prose target buried INSIDE a quoted
# operand (the struck/inserted literal) must NOT hijack the amendment target.
# ---------------------------------------------------------------------------


def _ref_unit(xml_fragment: str) -> ET.Element:
    """Parse a standalone USLM unit fragment (namespaced) for ref-scan unit tests."""
    return ET.fromstring(f'<content xmlns="{_USLM_NS}">{xml_fragment}</content>')


def test_first_usc_ref_skips_a_ref_inside_a_quoted_operand():
    # The ONLY usc ref lives inside <quotedText> (it is the inserted literal's own
    # cross-citation, not the amendment target). _first_usc_ref must return nothing.
    unit = _ref_unit(
        'by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>subparagraphs (A), (B), and (C) of "
        '<ref href="/us/usc/t10/s2313/a/2">section 2313(a)(2) of title 10, '
        "United States Code</ref>, and</quotedText>” before "
        "“<quotedText>subsection (b) of section 2313</quotedText>”"
    )
    assert _first_usc_ref(unit) == ("", "")


def test_first_usc_ref_prefers_the_unquoted_target_over_a_quoted_cross_ref():
    # A genuine target ref (outside quotes) is still returned, even when a later
    # quoted operand carries its own usc cross-ref.
    unit = _ref_unit(
        '<ref href="/us/usc/t11/s507/d">Section 507(d) of title 11, United States '
        'Code</ref>, <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText><ref href="/us/usc/t10/s7222">section 7222 of title 10, '
        "United States Code</ref></quotedText>”."
    )
    phrase, href = _first_usc_ref(unit)
    assert href == "/us/usc/t11/s507/d"
    assert phrase.startswith("Section 507(d)")


def test_quoted_cross_ref_does_not_hijack_target_onto_operands_cited_section():
    # PL 114-328 §896(2)(A) form: "inserting '...section 2313(a)(2) of title 10...'
    # before '...'" amends a free-standing Act note; the title-10 ref is INSIDE the
    # inserted literal. The old lowering hijacked the target onto title-10 §2313;
    # the corrected lowering resolves NO target → typed residual (no false title-10
    # op materialized against §2313). Prime Directive: a target we cannot extract
    # stays a visible residual, never guessed.
    body = (
        '<section identifier="/us/pl/114/328/s896"><num value="896">SEC. 896. </num>'
        '<subparagraph identifier="/us/pl/114/328/s896/2/A" role="instruction">'
        '<num value="A">(A) </num><content>by '
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>subparagraphs (A), (B), and (C) of "
        '<ref href="/us/usc/t10/s2313/a/2">section 2313(a)(2) of title 10, '
        "United States Code</ref>, and</quotedText>” before "
        "“<quotedText>subsection (b) of section 2313</quotedText>”; and"
        "</content></subparagraph></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.target_address is None
    assert instr.operation is None
    assert instr.instruction_status == "unsupported"
    assert instr.finding is not None
    assert instr.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID


def test_quoted_strike_operand_cross_ref_does_not_hijack_off_a_named_act_parent():
    # PL 115-232 §809(h)(2) form: parent amends "Section 2055(g) of the Internal
    # Revenue Code of 1986"; the leaf strikes a quoted "section 7222 of title 10"
    # literal. The title-10 ref is the STRUCK literal, not the target. The IRC
    # parent prose carries no "of title N" form, so the corrected lowering leaves
    # the unit unresolved (a typed residual), never a title-10 §7222 op.
    body = (
        '<section identifier="/us/pl/115/232/s809"><num value="809">SEC. 809. </num>'
        '<paragraph identifier="/us/pl/115/232/s809/h/2" role="instruction">'
        '<num value="2">(2) </num>'
        "<chapeau>Section 2055(g) of the Internal Revenue Code of 1986 "
        '<amendingAction type="amend">is amended</amendingAction>—</chapeau>'
        '<subparagraph identifier="/us/pl/115/232/s809/h/2/A" role="instruction">'
        '<num value="A">(A) </num><content>in paragraph (4), by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText><ref href="/us/usc/t10/s7222">section 7222 of title 10, '
        "United States Code</ref></quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText><ref href="/us/usc/t10/s8622">section 8622 of title 10, '
        "United States Code</ref></quotedText>”;</content></subparagraph>"
        "</paragraph></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    leaf = next(i for i in report.instructions if i.instruction_id.endswith("/s809/h/2/A"))
    assert leaf.target_address is None
    assert leaf.finding is not None
    assert leaf.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID


def test_add_at_end_quoted_block_sidenote_ref_does_not_become_the_target():
    # An add-at-end whose quoted new-section block carries a marginal <sidenote>
    # pin-cite ("38 USC 7413") INSIDE the <quotedContent>: that ref is in the
    # inserted payload, never the amendment target. The unit's own target is the
    # chapeau "Subchapter I of chapter 74" (no pinnable USC section) → the ref must
    # not hijack the target onto §7413; the unit stays a typed residual.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>Subchapter I of chapter 74 "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="add">adding</amendingAction> at the end the '
        'following new section:<quotedContent><section><num value="7413">'
        '“§ 7413.</num><heading><sidenote><p class="fontsize8">'
        '<ref href="/us/usc/t38/s7413">38 USC 7413</ref>.</p></sidenote> '
        "Treatment of podiatrists</heading></section></quotedContent>"
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.target_address is None
    assert instr.finding is not None
    assert instr.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID


def test_genuinely_absent_operand_stays_a_typed_residual_not_guessed():
    # A real, unquoted title-11 strike whose anchor is correctly extracted. The
    # operand IS the genuine quoted literal; the dry-run (not lowering) decides
    # presence. Lowering must produce the op with the EXACT quoted operand, never a
    # substitute — proving the fix does not strip a legitimate quoted strike.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101/18">Section 101(18) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>a phrase that does not occur</quotedText>”.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "strike"
    assert instr.operation is not None
    assert _patch(instr).selector.match_text == "a phrase that does not occur"
    assert instr.target_address is not None
    assert instr.target_address.path[0] == ("title", "11")


def test_quoted_text_preserves_significant_leading_space():
    # A genuine leading space INSIDE the quotedText literal must survive lowering
    # (F1 case i). Only internal formatting whitespace is collapsed.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s507/d">Section 507(d) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText> excluding subparagraph (F)</quotedText>” after "
        "“<quotedText>(a)(8)</quotedText>”.</content></section>"
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
        "<content>a payment becomes due.”</content></subsection>"
        "</quotedContent>.</content></section>"
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
        "<content>A plan modified under paragraph (1)"
        '<page identifier="/us/stat/134/3219">134 STAT. 3219</page>'
        " may not provide for payments.</content></paragraph>"
        "</subsection></quotedContent>.</content></section>"
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
# Relative target threading (nested instruction lists; "of such title")
# ---------------------------------------------------------------------------


def test_relative_prose_target_threads_inherited_title():
    # "Section 3675(b)(3) of such title" inherits the title from the enclosing
    # instruction; the section + segments come from the leaf's own prose.
    addr = parse_relative_usc_target("(B) in section 3675(b)(3), by striking", inherited_title="38")
    assert addr == LegalAddress(path=(("title", "38"), ("section", "3675"), ("subsection", "b"), ("paragraph", "3")))


def test_relative_prose_requires_an_inherited_title_never_invents_one():
    # No inherited title -> unresolved (never guess a title for a bare section ref).
    assert parse_relative_usc_target("in section 3675(b)(3), by striking", inherited_title="") is None


def test_relative_prose_ignores_cross_reference_with_explicit_title():
    # "section 116 of title 18" inside inserted text is a cross-reference, not the
    # amendment's own relative target — it carries "of title N", not "of such title".
    assert (
        parse_relative_usc_target(
            "the meaning given such term in section 116 of title 18, United States Code",
            inherited_title="38",
        )
        is None
    )


def test_nested_instruction_list_threads_section_and_subsection_targets():
    # A parent instruction "Section 104 ... is amended—" with leaf children
    # "(1) in subsection (a), by inserting ..." / "(2) in subsection (b), ...".
    # The leaves carry no ref; they inherit the section and refine it with their
    # own "in subsection (X)" anchor (so the two ops do NOT collapse to one address
    # and double-apply at the section surface).
    body = (
        '<section identifier="/us/pl/116/900/s2"><num value="2">SEC. 2. </num>'
        '<subsection identifier="/us/pl/116/900/s2/a" role="instruction">'
        '<num value="a">(a) </num><content>'
        '<ref href="/us/usc/t11/s104">Section 104 of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction>—</content>'
        '<paragraph identifier="/us/pl/116/900/s2/a/1"><num value="1">(1) </num>'
        "<content>in subsection (a), by "
        '<amendingAction type="insert">inserting</amendingAction> '
        '"<quotedText>1182(1),</quotedText>" after "<quotedText>707(b),</quotedText>"; and</content>'
        "</paragraph>"
        '<paragraph identifier="/us/pl/116/900/s2/a/2"><num value="2">(2) </num>'
        "<content>in subsection (b), by "
        '<amendingAction type="insert">inserting</amendingAction> '
        '"<quotedText>1182(1),</quotedText>" after "<quotedText>707(b),</quotedText>".</content>'
        "</paragraph></subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    addrs = sorted(str(i.target_address) for i in report.instructions if i.target_address)
    assert addrs == [
        "title:11/section:104/subsection:a",
        "title:11/section:104/subsection:b",
    ]


# ---------------------------------------------------------------------------
# Structural lowering: strike-subsection, range redesignation, insert-node-after
# ---------------------------------------------------------------------------


def test_strike_structural_unit_lowers_to_a_subsection_repeal():
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s364">Section 364 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subsection (g).'
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_STRIKE_UNIT
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.REPEAL
    assert instr.operation.target == LegalAddress(path=(("title", "11"), ("section", "364"), ("subsection", "g")))


def test_strike_structural_unit_with_future_effective_language_is_not_an_immediate_repeal():
    # A deferred/sunset strike ("Effective on the date that is 1 year after ...,
    # ... is amended by striking subsection (d)") is owned by the temporal layer;
    # lowering it to an immediate REPEAL would delete an in-force node. Refused.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>Effective on the date that is 1 year after the date of enactment "
        'of this Act, <ref href="/us/usc/t11/s525">Section 525 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subsection (d).'
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    # Future-effective strike is owned by the temporal layer; the FUTURE_EFFECTIVE
    # guard at the top of the strike family emits DEFERRED_AMEND_TO_READ before
    # any structural-unit / through-tail / tail processing.
    assert instr.finding.rule_id == DEFERRED_AMEND_TO_READ_FINDING_RULE_ID


def test_amend_to_read_with_sunset_language_is_not_an_immediate_replace():
    # A deferred/sunset amend-to-read ("On the date that is 1 year after ..., section
    # 1182(1) is amended to read as follows:") reverts text at a future date. The
    # temporal layer owns it; lowering it as an immediate REPLACE would corrupt the
    # in-force version used by an edition before the sunset date.
    body = (
        '<section identifier="/us/pl/116/136/dA/tI/s1113/a/5"><num value="5">(5) '
        '</num><heading>Sunset</heading>'
        "<content>On the date that is 1 year after the date of enactment of this Act, "
        '<ref href="/us/usc/t11/s1182/1">section 1182(1) of title 11, United States '
        'Code</ref>, <amendingAction type="amend">is amended</amendingAction> to read '
        "as follows:<quotedContent><paragraph><num value=\"1\">\"(1) </num><heading>"
        "Debtor</heading><content>The term ‘debtor’ means a small business debtor.\""
        "</content></paragraph></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == DEFERRED_AMEND_TO_READ_FINDING_RULE_ID


def test_amend_to_read_without_sunset_language_lowers_immediately():
    # Exact same payload shape as above, but without the future-effective sunset
    # prose, must still lower to an ordinary REPLACE.
    body = (
        '<section identifier="/us/pl/116/136/dA/tI/s1113/a/1"><num value="1">(1) '
        '</num>'
        '<content><ref href="/us/usc/t11/s1182/1">Section 1182(1) of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> to read '
        "as follows:<quotedContent><paragraph><num value=\"1\">\"(1) </num><heading>"
        "Debtor</heading><content>The term ‘debtor’ means a person.\""
        "</content></paragraph></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.REPLACE


def test_range_redesignation_lowers_to_one_renumber_per_member_high_end_first():
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101">Section 101 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="redesignate">redesignating</amendingAction> '
        "paragraphs (43) through (45) as paragraphs (50) through (52), respectively."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_REDESIGNATE_RANGE
    ops = report.operations()
    # Three RENUMBER ops, one per member, high-end first (45->52, 44->51, 43->50).
    for o in ops:
        assert o.action is StructuralAction.RENUMBER
        assert o.destination is not None
    renumbers = [(o.target.leaf_label(), o.destination.leaf_label()) for o in ops if o.destination is not None]
    assert renumbers == [("45", "52"), ("44", "51"), ("43", "50")]


def test_range_redesignation_with_list_conjunction():
    # Range redesignation is often the last item in a nested list: "; and".
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101">Section 101 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="redesignate">redesignating</amendingAction> '
        "paragraphs (8) through (10) as paragraphs (9) through (11), respectively; and"
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_REDESIGNATE_RANGE
    ops = report.operations()
    renumbers: list[tuple[str, str]] = []
    for o in ops:
        assert o.action is StructuralAction.RENUMBER
        assert o.destination is not None
        renumbers.append((o.target.leaf_label(), o.destination.leaf_label()))
    assert sorted(renumbers) == [("10", "11"), ("8", "9"), ("9", "10")]


def test_container_target_resolves_for_chapter_subchapter_part():
    from lawvm.us_federal.amendatory import parse_usc_container_target

    assert parse_usc_container_target("Chapter 6 of title 11, United States Code") == LegalAddress(
        path=(("title", "11"), ("chapter", "6"))
    )
    assert parse_usc_container_target("Subchapter I of chapter 74 of title 38, United States Code") == LegalAddress(
        path=(("title", "38"), ("subchapter", "I"))
    )
    assert parse_usc_container_target("Part I of title 18, United States Code") == LegalAddress(
        path=(("title", "18"), ("part", "I"))
    )
    assert parse_usc_container_target("", "/us/usc/t11/c6") == LegalAddress(path=(("title", "11"), ("chapter", "6")))
    assert parse_usc_container_target("", "/us/usc/t11/c6/schI") == LegalAddress(
        path=(("title", "11"), ("chapter", "6"), ("subchapter", "I"))
    )


def test_insert_node_after_a_section_lowers_to_an_anchored_insert():
    # Container target (chapter), anchor is a predecessor section; payload is a
    # whole new section.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/c6">Chapter 6 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="insert">inserting</amendingAction> after section '
        '110 the following new section:<quotedContent><section><num value="111">'
        "“§ 111.</num><heading>New section</heading><content>Text.”</content>"
        "</section></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_INSERT_NODE_AFTER
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.INSERT
    assert op.anchor == LegalAddress(path=(("title", "11"), ("chapter", "6"), ("section", "110")))
    assert op.target == LegalAddress(path=(("title", "11"), ("section", "111")))
    assert op.payload is not None
    assert op.payload.text.startswith("§ 111.")


def test_insert_node_after_a_paragraph_lowers_to_an_anchored_insert():
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101">Section 101 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="insert">inserting</amendingAction> after paragraph '
        '(10) the following:<quotedContent><paragraph><num value="11">“(11) </num>'
        "<content>a new definition.”</content></paragraph></quotedContent>."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_INSERT_NODE_AFTER
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.INSERT
    # The anchor names the paragraph to insert AFTER; the payload is the new node.
    assert op.anchor == LegalAddress(path=(("title", "11"), ("section", "101"), ("paragraph", "10")))
    assert op.payload is not None
    assert op.payload.text.startswith("(11) ")


# ---------------------------------------------------------------------------
# Typed finding for an unsupported / unresolvable instruction (no silent skip)
# ---------------------------------------------------------------------------


def test_named_act_target_yields_unresolved_finding_not_silent_skip():
    # PL 118-24 amends "Section 4(b) of the National Guard ... Act of 2008".
    report = lower_plaw_amendatory(_read("PLAW-118publ24.xml"))
    assert len(report.instructions) == 1
    instr = report.instructions[0]
    assert instr.instruction_status == "unsupported"
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
        cov["instructions_accepted"] + cov["instructions_needs_review"] + cov["instructions_unsupported"]
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
        report = scan_title_effect_candidates(archive, title="11", congress_window=(114, 116, 117))
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
        report = scan_title_effect_candidates(archive, title="11", congress_window=(114, 116, 117))
    finally:
        archive.close()
    # 114-89 targets title 11 only via short-marker false positives? No: it is t21/t35.
    # It should NOT be in the title-11 targeting set, so no unresolved spam from it.
    labels = set(report.coverage()["law_labels_targeting_title"])
    assert "PL 114-89" not in labels
    # The unlowered-finding family id is stable and present in the amendatory module.
    assert UNLOWERED_FINDING_RULE_ID == "us_amendatory_unlowered"


# ---------------------------------------------------------------------------
# Multi-unit structural strike + inherited-title threading from classification
# ---------------------------------------------------------------------------


def test_multi_unit_structural_strike_lowers_to_one_repeal_per_member():
    # "by striking subsections (a), (c), and (g)" -> one REPEAL per named member,
    # each typed by the prose verb ("subsection"), not positional label form (so a
    # roman-ambiguous letter among siblings is not mis-typed as a clause).
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s364">Section 364 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subsections '
        "(a), (c), and (g).</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_STRIKE_UNIT_LIST
    ops = report.operations()
    assert len(ops) == 3
    for o in ops:
        assert o.action is StructuralAction.REPEAL
    struck = sorted(o.target.leaf_label() for o in ops)
    assert struck == ["a", "c", "g"]
    # Every struck node is typed as a subsection (the prose verb), hanging off s364.
    for o in ops:
        assert o.target.path[:2] == (("title", "11"), ("section", "364"))
        assert o.target.path[-1][0] == "subsection"


def test_future_effective_multi_strike_is_not_an_immediate_repeal():
    # A deferred/sunset multi-unit strike is owned by the temporal layer; lowering it
    # to immediate REPEALs would delete nodes still in force in the window.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>Effective on the date that is 1 year after the date of enactment "
        'of this Act, <ref href="/us/usc/t11/s364">Section 364 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subsections '
        "(a) and (b).</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    # Future-effective strike is owned by the temporal layer; the FUTURE_EFFECTIVE
    # guard at the top of the strike family emits DEFERRED_AMEND_TO_READ before
    # the multi-unit structural-strike path runs.
    assert instr.finding.rule_id == DEFERRED_AMEND_TO_READ_FINDING_RULE_ID


def test_structural_strike_with_list_conjunction_trailing_semicolon():
    # Nested conforming-amendment leaves end with "; and" or just ";". The single-
    # unit structural strike recognizer must consume the list terminator, not require
    # the raw text to end immediately after the label.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>"
        '<paragraph identifier="/us/pl/116/900/s1/1"><num value="1">(1) </num>'
        '<content><ref href="/us/usc/t11/s364/b">Section 364(b) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> paragraph (2); and</content>'
        "</paragraph>"
        '<paragraph identifier="/us/pl/116/900/s1/2"><num value="2">(2) </num>'
        '<content><ref href="/us/usc/t11/s364/c">Section 364(c) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> paragraph (3).</content>'
        "</paragraph></content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    ops = [o for o in report.operations() if o.action is StructuralAction.REPEAL]
    assert len(ops) == 2
    targets = sorted(str(o.target) for o in ops)
    assert targets == [
        "title:11/section:364/subsection:b/paragraph:2",
        "title:11/section:364/subsection:c/paragraph:3",
    ]


def test_multi_unit_structural_strike_with_list_conjunction():
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>"
        '<ref href="/us/usc/t11/s364">Section 364 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subsections '
        "(a), (c), and (g); and</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_STRIKE_UNIT_LIST
    ops = report.operations()
    assert len(ops) == 3
    struck = sorted(o.target.leaf_label() for o in ops)
    assert struck == ["a", "c", "g"]


def test_paired_redesignation_lowers_to_one_renumber_per_pair():
    # Non-contiguous paired relabel: (2)->(4), (4)->(5).
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101">Section 101 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="redesignate">redesignating</amendingAction> '
        "paragraphs (2) and (4) as paragraphs (4) and (5), respectively."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_REDESIGNATE_PAIRS
    ops = report.operations()
    assert len(ops) == 2
    for o in ops:
        assert o.action is StructuralAction.RENUMBER
        assert o.destination is not None
    renumbers = sorted((o.target.leaf_label(), (o.destination.leaf_label() if o.destination else "")) for o in ops)
    assert renumbers == [("2", "4"), ("4", "5")]


def test_paired_redesignation_three_labels():
    # Non-contiguous 3-way relabel with Oxford comma, e.g.
    # "redesignating paragraphs (1), (2), and (3) as subsections (a), (b), and (c)".
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101">Section 101 of title 11, United '
        'States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="redesignate">redesignating</amendingAction> '
        "paragraphs (1), (2), and (3) as subsections (a), (b), and (c)."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.witness_rule_id == RULE_REDESIGNATE_PAIRS
    ops = report.operations()
    assert len(ops) == 3
    for o in ops:
        assert o.action is StructuralAction.RENUMBER
        assert o.destination is not None
    renumbers = sorted((o.target.leaf_label(), (o.destination.leaf_label() if o.destination else "")) for o in ops)
    assert renumbers == [("1", "a"), ("2", "b"), ("3", "c")]


def test_paired_redesignation_with_list_conjunction():
    # A nested-list leaf whose raw text ends with "; and".
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<content>"
        '<subsection identifier="/us/pl/116/900/s1/a" role="instruction">'
        '<num value="a">(a) </num><content>'
        '<ref href="/us/usc/t11/s101">Section 101 of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction>—</content>'
        '<paragraph identifier="/us/pl/116/900/s1/a/1"><num value="1">(1) </num>'
        "<content>in subsection (b), "
        'by <amendingAction type="redesignate">redesignating</amendingAction> '
        "paragraphs (1) and (2) as paragraphs (2) and (3), respectively; and</content>"
        "</paragraph>"
        '<paragraph identifier="/us/pl/116/900/s1/a/2"><num value="2">(2) </num>'
        "<content>in subsection (c), by striking paragraph (1).</content>"
        "</paragraph></subsection></content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    renumbers = [o for o in report.operations() if o.action is StructuralAction.RENUMBER]
    assert len(renumbers) == 2
    labels = sorted((o.target.leaf_label(), (o.destination.leaf_label() if o.destination else "")) for o in renumbers)
    assert labels == [("1", "2"), ("2", "3")]


def test_strike_and_substitute_lowers_as_strike_insert():
    # 'strike X and substitute Y' is the older-drafting imperative form of
    # 'striking X and inserting Y' — semantically identical text replacement.
    # The USLM marks these with <amendingAction type="substitute">. Without the
    # classifier extension, these fell to us_amendatory_unrecognized_form;
    # with it, the word-boundary \bstrike\b match + substitute in actions routes
    # to the strike_insert family.
    # Source witness: PL 108-136 §7 "strike (40 U.S.C. 1003 note) and substitute
    # (40 U.S.C. 8903 note)".
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        '<content>'
        '<ref href="/us/usc/t11/s101">Section 101 of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction>—'
        '(1) In subsection (a)(2), '
        'strike \u201c<quotedText>old section</quotedText>\u201d and '
        '<amendingAction type="substitute">substitute</amendingAction> '
        '\u201c<quotedText>new section</quotedText>\u201d.'
        '</content></section>'
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert"
    patch = _patch(instr)
    assert patch.selector.match_text == "old section"
    assert patch.replacement == "new section"


def test_redesignate_table_form_lowers_one_renumber_per_row():
    # 'redesignating the sections as described in the table' — the section-number
    # pairs live in a sibling <xhtml:table> in the parent subsection. The lowerer
    # walks the table's <tr> rows, extracting (before, after) from the first and
    # third <td> columns, and emits one RENUMBER per row. Source witness: PL
    # 115-282 §103(b) — title-14 sections 1-5 (and 652) → 101-106.
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num value="1">SEC. 1. </num>'
        "<content>"
        '<ref href="/us/usc/t11/s101">Title 11, United States Code</ref> is amended—'
        '<subsection identifier="/us/pl/116/900/s1/a"><num value="a">(a) </num><content>'
        '<paragraph identifier="/us/pl/116/900/s1/a/1"><num value="1">(1) </num>'
        "<content>The sections identified in the table in paragraph (2) are amended—</content>"
        '<subparagraph identifier="/us/pl/116/900/s1/a/1/A" role="instruction"><num value="A">(A) </num>'
        '<content>by <amendingAction type="redesignate">redesignating</amendingAction> the sections as described in the table; and</content>'
        "</subparagraph>"
        '<subparagraph identifier="/us/pl/116/900/s1/a/1/B"><num value="B">(B) </num>'
        '<content>by transferring the sections, as necessary.</content>'
        "</subparagraph></paragraph>"
        '<paragraph identifier="/us/pl/116/900/s1/a/2"><num value="2">(2) </num>'
        '<content>The table referred to in paragraph (1) is the following:'
        '<xhtml:table xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        '<xhtml:thead><xhtml:tr><xhtml:th>Before</xhtml:th><xhtml:th>Heading</xhtml:th><xhtml:th>After</xhtml:th></xhtml:tr></xhtml:thead>'
        '<xhtml:tbody>'
        '<xhtml:tr><xhtml:td>1</xhtml:td><xhtml:td>First</xhtml:td><xhtml:td>101</xhtml:td></xhtml:tr>'
        '<xhtml:tr><xhtml:td>2</xhtml:td><xhtml:td>Second</xhtml:td><xhtml:td>102</xhtml:td></xhtml:tr>'
        '<xhtml:tr><xhtml:td>3</xhtml:td><xhtml:td>Third</xhtml:td><xhtml:td>103</xhtml:td></xhtml:tr>'
        "</xhtml:tbody></xhtml:table></content></paragraph>"
        "</content></subsection></content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    table_instrs = [i for i in report.instructions if i.witness_rule_id == RULE_REDESIGNATE_TABLE]
    assert len(table_instrs) == 1
    instr = table_instrs[0]
    assert instr.operation is not None
    ops = [instr.operation, *instr.extra_operations]
    assert len(ops) == 3
    for o in ops:
        assert o.action is StructuralAction.RENUMBER
        assert o.destination is not None
    renumbers = sorted(
        (o.target.leaf_label(), o.destination.leaf_label() if o.destination else "") for o in ops
    )
    assert renumbers == [("1", "101"), ("2", "102"), ("3", "103")]


def test_flat_relative_head_inherits_title_from_section_classification():
    # "Section 4980I(f) ... is amended by striking ..." with NO "of title 26": the
    # title is threaded from the section's OWN govinfo classification <ref>
    # (/us/usc/t26/s4980I, even as a note sidenote) — the OLRC's authoritative
    # classification of the very section named. Only fires because the head's
    # section (4980I) is the one the classification ref pins, to exactly one title.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<sidenote><p><ref href="/us/usc/t26/s4980I">26 USC 4980I note</ref></p>'
        "</sidenote>"
        "<content>Paragraph (10) of section 4980I(f) "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>2018</quotedText>".</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.target_address is not None
    # Title 26 threaded from the classification ref; section + segments from the head.
    assert instr.target_address.path[0] == ("title", "26")
    assert instr.target_address.path[1] == ("section", "4980I")
    assert instr.finding is None or instr.finding.rule_id != TARGET_UNRESOLVED_FINDING_RULE_ID


def test_relative_head_is_not_threaded_when_section_multi_classified():
    # The head names "section 100", but the section's classification refs pin
    # section 100 under TWO different titles (26 and 42). The title is ambiguous, so
    # NOTHING is threaded — the target stays unresolved (no silent wrong-title guess).
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<sidenote><p>"
        '<ref href="/us/usc/t26/s100">26 USC 100 note</ref>'
        '<ref href="/us/usc/t42/s100">42 USC 100 note</ref>'
        "</p></sidenote>"
        "<content>Section 100 "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '"<quotedText>old</quotedText>".</content></section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.target_address is None
    assert instr.finding is not None
    assert instr.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID


def test_strike_unit_insert_new_units_is_held_out_not_a_whole_node_replace():
    # "striking subparagraph (I) and inserting the following new subparagraphs (I)
    # and (J): <block>" is a NODE-LEVEL RESTRUCTURE. A whole-node REPLACE of the
    # resolved address would drop the struck node's siblings (materializing only the
    # new block); it is held out as the compound residual rather than corrupted.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s101/10A">Section 101(10A) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction> '
        'by <amendingAction type="delete">striking</amendingAction> subparagraph (I) '
        'and <amendingAction type="insert">inserting</amendingAction> the following '
        "new subparagraphs (I) and (J):<quotedContent><subparagraph>"
        '<num value="I">“(I) </num><content>first.</content></subparagraph>'
        '<subparagraph><num value="J">“(J) </num><content>second.”</content>'
        "</subparagraph></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == COMPOUND_STRIKE_INSERT_FINDING_RULE_ID


def test_title_only_chapeau_threads_title_to_relative_prose_leaf():
    # Real PL 115-392 §11 shape (recovers 18:1583): the section CHAPEAU amends a
    # whole title ("Part I of title 18, United States Code, is amended—") with NO
    # section of its own, and a leaf names its own section in RELATIVE prose ("(A) in
    # section 1583(a), … by striking 'X' and inserting 'Y'"). The bare title from the
    # chapeau is threaded down so the leaf resolves to 18:1583(a) — the enacted scope,
    # never invented. Before this threading the leaf stayed target_unresolved.
    body = (
        '<section identifier="/us/pl/115/392/s11"><num value="11">SEC. 11. </num>'
        '<chapeau>Part I of <ref href="/us/usc/t18">title 18, United States Code'
        '</ref>, <amendingAction type="amend">is amended</amendingAction>—</chapeau>'
        '<paragraph identifier="/us/pl/115/392/s11/1"><num value="1">(1) </num>'
        "<chapeau>in chapter 77—</chapeau>"
        '<subparagraph identifier="/us/pl/115/392/s11/1/A"><num value="A">(A) </num>'
        "<content>in section 1583(a), in the flush text following paragraph (3), by "
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>not more than 20 years</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>not more than 30 years</quotedText>”."
        "</content></subparagraph></paragraph></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    leaf = next(i for i in report.instructions if i.target_address is not None)
    assert str(leaf.target_address) == "title:18/section:1583/subsection:a"
    assert leaf.action == "strike_insert"
    op = leaf.operation
    assert op is not None and op.text_patch is not None
    assert op.text_patch.selector.match_text == "not more than 20 years"
    assert op.text_patch.replacement == "not more than 30 years"
    # The title is threaded, never invented: a chapeau naming no USC title leaves the
    # relative-prose leaf unresolved.
    no_title_body = body.replace(
        '<ref href="/us/usc/t18">title 18, United States Code</ref>',
        "Part I",
    )
    no_title_report = lower_plaw_amendatory(_synthetic_plaw(no_title_body))
    assert all(i.target_address is None for i in no_title_report.instructions)


def test_add_at_end_new_section_block_lowers_to_insert_not_appended_to_sibling():
    # Real PL 115-70 §402 shape (protects 18:2326): "(4) by adding at the end the
    # following: '§ 2328. Mandatory forfeiture …'" CREATES a new section §2328. It must
    # NOT be appended to the inherited section's body (which would corrupt the sibling
    # section's text). It now lowers to a whole-new-section INSERT targeted at §2328.
    body = (
        '<section identifier="/us/pl/115/70/s402"><num value="402">SEC. 402. </num>'
        '<content><ref href="/us/usc/t18/s2326">Section 2326 of title 18, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">adding</amendingAction> at the end the '
        "following:<quotedContent>“§ 2328. Mandatory forfeiture “(a) In General.—The "
        "court shall order forfeiture.”</quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "add_at_end"
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.INSERT
    assert str(instr.operation.target) == "title:18/section:2328"
    assert instr.operation.witness_rule_id == RULE_ADD_AT_END_NEW_SECTIONS
    assert instr.finding is not None
    assert instr.finding.rule_id == NON_TITLE_TARGET_RULE_ID


def test_add_at_end_subsection_content_still_lowers_guard_is_precise():
    # The new-section guard must NOT reject a legitimate add-at-end of subsection
    # content: a payload that opens with a bare "(d)" enumerator is a body append, not
    # a new-section create, and still lowers to an INSERT anchored at the section.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s523">Section 523 of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">adding</amendingAction> at the end the '
        "following:<quotedContent>“(d) The court may award costs.”</quotedContent>."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "add_at_end"
    assert instr.operation is not None
    assert instr.witness_rule_id == RULE_ADD_AT_END
    # And the head detector itself: section/chapter heads are new units; bare
    # subsection/paragraph enumerators are body content.
    assert _payload_opens_new_section("§ 2328. Mandatory forfeiture")
    assert _payload_opens_new_section("“§ 171. Wildlife crossings")
    assert _payload_opens_new_section("CHAPTER 37—NONPOSTAL SERVICES")
    assert not _payload_opens_new_section("(d) The court may award costs.")
    assert not _payload_opens_new_section("“(2) If a disclosure is made")


def test_add_at_end_multisection_payload_splits_into_per_section_inserts():
    # Container-level add-at-end with a block that defines multiple new sections.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/c11">Chapter 11 of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="insert">adding</amendingAction> at the end the '
        "following:<quotedContent>SUBCHAPTER V—TEST “§ 1100A. First new section “(a)"
        " Body one. “§ 1100B. Second new section “(b) Body two.”</quotedContent>."
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "add_at_end"
    assert instr.operation is not None
    targets = {str(op.target) for op in (instr.operation, *instr.extra_operations)}
    assert targets == {"title:11/section:1100A", "title:11/section:1100B"}


def test_title_only_chapeau_threads_ancestor_section_to_conforming_amendment():
    # Nested conforming-amendment form: the title lives on the parent chapeau, and an
    # intermediate ancestor names the target section. The leaf inherits that full section.
    body = (
        '<section identifier="/us/pl/116/900/s4"><num value="4">SEC. 4. </num>'
        "<heading>Conforming amendments</heading>"
        '<subsection identifier="/us/pl/116/900/s4/a" role="instruction">'
        '<num value="a">(a)</num><heading>Title 11.—</heading>'
        "<content>Title 11, United States Code, is amended—"
        '<paragraph identifier="/us/pl/116/900/s4/a/2">'
        '<num value="2">(2)</num> in section 322(a), '
        '<amendingAction type="amend">by redesignating</amendingAction> '
        'subsections <amendingAction type="redesignate">(i) through (k)</amendingAction> '
        'as subsections <amendingAction type="redesignate">(j) through (l)</amendingAction>.'
        "</paragraph></content></subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "redesignate"
    assert str(instr.target_address) == "title:11/section:322/subsection:a"
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.RENUMBER
    assert instr.operation.witness_rule_id == RULE_REDESIGNATE_RANGE


def test_alphabetic_redesignate_range_lowers_to_renumber_high_end_first():
    # Non-numeric alphabetic redesignation ranges are enumerable by character offset.
    # High-end-first ordering prevents intermediate relabel collisions.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content><ref href="/us/usc/t11/s322">Section 322 of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="redesignate">redesignating</amendingAction> subsections '
        '<amendingAction type="redesignate">(i) through (k)</amendingAction> as subsections '
        '<amendingAction type="redesignate">(j) through (l)</amendingAction>.'
        "</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    assert instr.action == "redesignate"
    assert instr.operation is not None
    assert instr.operation.action is StructuralAction.RENUMBER
    ops = (instr.operation, *instr.extra_operations)
    from_labels: list[str] = []
    to_labels: list[str] = []
    for op in ops:
        assert op.destination is not None
        assert op.target.path[-1] == ("subsection", op.target.leaf_label())
        assert op.destination.path[-1] == ("subsection", op.destination.leaf_label())
        from_labels.append(str(op.target.leaf_label()))
        to_labels.append(str(op.destination.leaf_label()))
    assert from_labels == ["k", "j", "i"]
    assert to_labels == ["l", "k", "j"]


def test_effective_date_scope_resolves_at_named_kind_and_marks_covered_ops_pending():
    # A section-level "Effective Date" paragraph naming "subsections (a) through (e)"
    # with a conditional trigger ("take effect on the date Administrator submits...")
    # should mark the covered amendatory leaves as pending, not immediate.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<heading>Title 11 amendment.</heading>'
        '<subsection identifier="/us/pl/116/900/s1/a" role="instruction">'
        '<num value="a">(a)</num><content><ref href="/us/usc/t11/s503/b">Section 503(b) '
        'of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="add">adding</amendingAction> at the end the following: '
        '<quotedContent><paragraph class="indentUp0"><num value="10">“(10) temporary."'
        '</num></paragraph></quotedContent>.</content></subsection>'
        '<subsection identifier="/us/pl/116/900/s1/f" role="instruction">'
        '<num value="f">(f)</num><heading>Effective Date.</heading>'
        '<paragraph identifier="/us/pl/116/900/s1/f/1" style="-uslm-lc:I658122">'
        '<num value="1">(1)</num><heading>Effective date.</heading>'
        '<chapeau>The amendments made by subsections (a) through (e) shall—</chapeau>'
        '<subparagraph identifier="/us/pl/116/900/s1/f/1/A">'
        '<num value="A">(A)</num><content>take effect on the date on which the '
        'Administrator submits a written determination that the requirements are met; and'
        '</content></subparagraph>'
        '<subparagraph identifier="/us/pl/116/900/s1/f/1/B">'
        '<num value="B">(B)</num><content>apply to cases commenced on or after that date.'
        '</content></subparagraph>'
        '</paragraph></subsection>'
        '</section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    ops = report.operations()
    assert len(ops) == 1
    op = ops[0]
    assert op.source is not None
    assert op.source.legal_status is PENDING_CONDITION_STATUS


def test_effective_date_scope_does_not_leak_across_subsections_at_different_levels():
    # A temporal subparagraph under subsection (f)(1)(B) naming "subparagraph (A)" must
    # not mark an unrelated subsection (a) amendment as pending just because both have
    # a container whose lowercased label sorts within [a, a] (#83).
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<heading>Leak test.</heading>'
        '<subsection identifier="/us/pl/116/900/s1/a" role="instruction">'
        '<num value="a">(a)</num><content><ref href="/us/usc/t11/s101">Section 101 '
        'of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> “<quotedText>old</quotedText>” '
        'and <amendingAction type="insert">inserting</amendingAction> “<quotedText>new</quotedText>”.'
        '</content></subsection>'
        '<subsection identifier="/us/pl/116/900/s1/f" role="instruction">'
        '<num value="f">(f)</num><heading>Effective Date.</heading>'
        '<paragraph identifier="/us/pl/116/900/s1/f/1" style="-uslm-lc:I658122">'
        '<num value="1">(1)</num><heading>Effective date.</heading>'
        '<subparagraph identifier="/us/pl/116/900/s1/f/1/A">'
        '<num value="A">(A)</num><content>Some technical note.</content></subparagraph>'
        '<subparagraph identifier="/us/pl/116/900/s1/f/1/B">'
        '<num value="B">(B)</num><content>The amendment made by subparagraph (A) shall '
        'take effect on the date that is 1 year after the date of enactment of this Act.'
        '</content></subparagraph>'
        '</paragraph></subsection>'
        '</section>'
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    ops = report.operations()
    assert len(ops) == 1
    op = ops[0]
    assert op.target.path == (("title", "11"), ("section", "101"))
    assert op.source is not None
    assert op.source.legal_status is not PENDING_CONDITION_STATUS

    # SBRA-style conforming amendment: a parent paragraph names the target section
    # with an em-dash list terminator ("(4) in section 322—"), an intermediate
    # paragraph names the subsection, and the leaf carries the real edit. The dash
    # terminator must be recognised, the title inherited from the title-only
    # chapeau, and the intermediate anchor must refine to the sub-section.
    body = (
        '<section identifier="/us/pl/116/900/s4"><num value="4">SEC. 4. </num>'
        "<heading>Conforming amendments</heading>"
        '<subsection identifier="/us/pl/116/900/s4/a" role="instruction">'
        '<num value="a">(a)</num><heading>Title 11.</heading>'
        "<content>Title 11, United States Code, is amended—</content>"
        '<paragraph identifier="/us/pl/116/900/s4/a/4">'
        '<num value="4">(4)</num> in section 322—'
        '<paragraph identifier="/us/pl/116/900/s4/a/4/A">'
        '<num value="A">(A)</num> in subsection (a)—'
        '<subparagraph identifier="/us/pl/116/900/s4/a/4/A/i">'
        '<num value="i">(i)</num> by <amendingAction type="delete">striking</amendingAction>'
        " “<quotedText>old text</quotedText>”."
        "</subparagraph></paragraph></paragraph></subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    assert len(accepted) == 1, report
    instr = accepted[0]
    assert instr.target_address == LegalAddress(path=(("title", "11"), ("section", "322"), ("subsection", "a")))


def test_section_level_effective_date_scope_handles_date_of_the_enactment():
    # A sibling "Effective Date" paragraph that names the whole section (`"this
    # section"`) rather than a subsection/paragraph range still attaches a concrete
    # future effective date to every amendatory leaf.  The drafting phrase "the date
    # of the enactment" has an optional extra "the" before "enactment".
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Amendment.</heading>"
        '<subsection identifier="/us/pl/116/900/s1/a" role="instruction">'
        '<num value="a">(a)</num>'
        '<content><ref href="/us/usc/t11/s503">Section 503 of title 11</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></subsection>'
        '<subsection identifier="/us/pl/116/900/s1/b" role="instruction">'
        '<num value="b">(b)</num><heading>Effective Date.</heading>'
        '<content>The amendments made by this section shall take effect on the date '
        'that is 1 year after the date of the enactment of this Act.</content>'
        "</subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    ops = report.operations()
    assert len(ops) == 1
    op = ops[0]
    assert op.source is not None
    assert op.source.effective == "2021-01-01"
    assert op.source.legal_status is not PENDING_CONDITION_STATUS


def test_section_level_effective_date_scope_does_not_cross_sections():
    # An "Effective Date" paragraph inside one PLAW section must not attach a future
    # effective date to amendatory leaves in a different section of the same law.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Immediate.</heading>"
        '<subsection identifier="/us/pl/116/900/s1/a" role="instruction">'
        '<num value="a">(a)</num>'
        '<content><ref href="/us/usc/t11/s503">Section 503 of title 11</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>now</quotedText>”.</content></subsection></section>'
        '<section identifier="/us/pl/116/900/s2"><num value="2">SEC. 2. </num>'
        "<heading>Deferred.</heading>"
        '<subsection identifier="/us/pl/116/900/s2/a" role="instruction">'
        '<num value="a">(a)</num>'
        '<content><ref href="/us/usc/t11/s506">Section 506 of title 11</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>later</quotedText>”.</content></subsection>'
        '<subsection identifier="/us/pl/116/900/s2/b" role="instruction">'
        '<num value="b">(b)</num><heading>Effective Date.</heading>'
        '<content>The amendments made by this section shall take effect on the date '
        'that is 1 year after the date of the enactment of this Act.</content>'
        "</subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    ops = report.operations()
    assert len(ops) == 2
    by_section = {op.target.path[1][1]: op for op in ops}
    assert by_section["503"].source is not None
    assert by_section["503"].source.effective == ""
    assert by_section["506"].source is not None
    assert by_section["506"].source.effective == "2021-01-01"


def test_plaw_metadata_title_fallback_resolves_bare_section_target():
    # When the converter omits the sidenote classification ref and the instruction text
    # names only "Section 315(b)" (no "of title N"), the PLAW's own short-title
    # preamble supplies the title if it names exactly one USC title.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Extension.</heading>"
        '<content>Section 315(b) <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>September 30, 2016</quotedText>” and '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>September 30, 2017</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(
        _synthetic_plaw_with_title("To amend title 38, United States Code, ...", body)
    )
    ops = report.operations()
    assert len(ops) == 1
    op = ops[0]
    assert op.target.path == (
        ("title", "38"),
        ("section", "315"),
        ("subsection", "b"),
    )
    assert op.source is not None
    assert op.source.effective == ""
    assert TARGET_TITLE_FROM_PLAW_METADATA in op.provenance_tags


def test_plaw_metadata_title_fallback_ignored_when_preamble_names_multiple_titles():
    # A PLAW that amends more than one title cannot safely supply a unique title.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Amendment.</heading>"
        '<content>Section 315(b) <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(
        _synthetic_plaw_with_title(
            "To amend titles 11, 38, and 42, United States Code, ...", body
        )
    )
    assert report.operations() == ()
    assert any(
        instr.finding is not None
        and instr.finding.rule_id == TARGET_UNRESOLVED_FINDING_RULE_ID
        for instr in report.instructions
    )


def test_plaw_metadata_title_fallback_does_not_override_explicit_title():
    # An explicit "of title 11" must win over a PLAW preamble that names title 38.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        "<heading>Amendment.</heading>"
        '<content>Section 503(b) of title 11 <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(
        _synthetic_plaw_with_title("To amend title 38, United States Code, ...", body)
    )
    ops = report.operations()
    assert len(ops) == 1
    assert ops[0].target.path[:2] == (("title", "11"), ("section", "503"))
    assert TARGET_TITLE_FROM_PLAW_METADATA not in ops[0].provenance_tags


def test_lower_plaw_amendatory_propagates_xml_parse_error_not_silent_title_scope_fallback():
    # AGENTS.md §1.10 guard-liveness: a Public Law whose USLM body is malformed XML
    # MUST surface the parse failure loudly, not silently default the
    # metadata-title fallback to "" and proceed with a bogus OP set.
    # _plaw_usc_title_scope previously trapped ET.ParseError and returned "",
    # dead code (lower_plaw_amendatory's own ET.fromstring already raised before
    # the helper was reached).  The refactor passes the already-parsed root so the
    # catch path is gone; this test drives a known-violating input through the
    # FULL production entry point and asserts the ParseError propagates rather
    # than be swallowed into a silent "" metadata scope.
    malformed = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        b"<congress>116</congress><docNumber>999</docNumber>"
        b"<approvedDate>2024-01-01</approvedDate></meta>"
        b'<main><section identifier="/us/pl/116/999/s1"><num value="1">SEC. 1. </num>'
        b'<content>Section 315(b) '
        b'<amendingAction type="amend">is amended</amendingAction> by '
        b'<amendingAction type="delete">striking</amendingAction> '
        # Unterminated quotedText + missing </content></section></main></uslm>:
        # ET.fromstring raises ParseError; lower_plaw_amendatory must NOT have a
        # silent fallback that yields an empty metadata-title scope and proceeds.
        b'"old".'
    )
    with pytest.raises(ET.ParseError):
        lower_plaw_amendatory(malformed, statute_id="PL 116-999", enacted="2024-01-01")




def test_plaw_metadata_title_fallback_withheld_when_law_names_another_title():
    # A PLAW that amends more than one title is unsafe for preamble-based title
    # inference. The explicit "of title 5" reference here means the bare
    # "Section 315(b)" in the same law must NOT be silently hijacked onto the
    # preamble's title 38; it stays unresolved and the law-level conflict is
    # recorded.
    body = (
        '<section identifier="/us/pl/116/900/s1"><num value="1">SEC. 1. </num>'
        '<content>Section 7703(d)(2) of title 5 <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></section>'
        '<section identifier="/us/pl/116/900/s2"><num value="2">SEC. 2. </num>'
        '<content>Section 315(b) <amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>old</quotedText>”.</content></section>'
    )
    report = lower_plaw_amendatory(
        _synthetic_plaw_with_title("To amend title 38, United States Code, ...", body)
    )
    # The explicit title-5 instruction resolves; the bare section target does not.
    assert any(
        instr.target_address is not None
        and instr.target_address.path[0] == ("title", "5")
        for instr in report.instructions
    )
    bare = next(
        instr
        for instr in report.instructions
        if "Section 315(b)" in instr.raw_text
    )
    assert bare.target_address is None
    assert any(
        f.rule_id == PLAW_METADATA_SCOPE_CONFLICT_RULE_ID for f in report.findings
    )


def test_sibling_anchors_do_not_leak_into_ancestor_target_resolution():
    # When resolving an intermediate ancestor's own section, sibling units must not
    # donate their <ref> or prose targets to that ancestor. Without sibling exclusion
    # the first <ref> encountered in document order (the sibling's /us/usc/t11/s521)
    # would hijack the parent scope onto section 521.
    body = (
        '<section identifier="/us/pl/116/900/s4"><num value="4">SEC. 4. </num>'
        "<heading>Conforming amendments</heading>"
        '<subsection identifier="/us/pl/116/900/s4/a" role="instruction">'
        '<num value="a">(a)</num><heading>Title 11.</heading>'
        "<content>Title 11, United States Code, is amended—</content>"
        '<paragraph identifier="/us/pl/116/900/s4/a/4">'
        '<num value="4">(4)</num> in section 322—'
        '<paragraph identifier="/us/pl/116/900/s4/a/4/A">'
        '<num value="A">(A)</num> in subsection (a)—'
        '<subparagraph identifier="/us/pl/116/900/s4/a/4/A/i">'
        '<num value="i">(i)</num> by <amendingAction type="delete">striking</amendingAction>'
        " “<quotedText>A</quotedText>”."
        "</subparagraph></paragraph>"
        '<paragraph identifier="/us/pl/116/900/s4/a/4/B">'
        '<num value="B">(B)</num> in subsection (b), '
        '<ref href="/us/usc/t11/s521">section 521 of title 11</ref>, '
        'by <amendingAction type="delete">striking</amendingAction>'
        " “<quotedText>B</quotedText>”."
        "</paragraph></paragraph></subsection></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    # The leaf under (A) must target section 322(a), not be hijacked by sibling (B)'s ref.
    instr_a = next(i for i in accepted if i.instruction_id.endswith("/4/A/i"))
    assert instr_a.target_address == LegalAddress(path=(("title", "11"), ("section", "322"), ("subsection", "a")))
    # Sibling (B) carries its own absolute <ref> to section 521; without the
    # sibling-leak fix its <ref> would steal paragraph (4)'s scope for leaf A.
    instr_b = next(i for i in accepted if i.instruction_id.endswith("/4/B"))
    assert instr_b.target_address == LegalAddress(path=(("title", "11"), ("section", "521")))


# ---------------------------------------------------------------------------
# Real positive-title recovery over the canonical archive (archive-gated, no network)
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
def test_real_title28_pl115_141_title_only_inheritance_reaches_agreement() -> None:
    # End-to-end recovery (28:1871, 2016->2018, PL 115-141): the section chapeau
    # amends a whole title with no section of its own, and the leaf names its section
    # in relative prose ("in section 1871(b), … by striking '$40' and inserting
    # '$50'"). The bare title threaded from the chapeau lets the leaf resolve to
    # 28:1871(b) and the strike/insert materializes in AGREEMENT with the published
    # Code — a section that was a missing_source target-resolution gap before the
    # title-only inheritance. Confirms the recovered op reaches a genuine agreement
    # (not just that it lowers).
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    archive = open_us_federal_farchive(readonly=True)
    try:
        report = build_us_dry_run_from_archive(
            archive,
            title=28,
            before_year=2016,
            after_year=2018,
            plaw_locators={"PL 115-141": plaw_locator(115, 141)},
        )
    finally:
        archive.close()

    assert "28:1871" in report.oracle_changed_sections
    assert "28:1871" in report.claimed_sections
    row = next(r for r in report.rows if r.section_key == "28:1871")
    assert row.row_status == "agree"
    assert row.target_address == "title:28/section:1871/subsection:b"
    assert row.match_text == "$40"
    assert row.replacement == "$50"
    assert report.replay_authorized is False


def test_precise_text_strike_with_subsection_letter_d_keeps_full_path():
    # A precise text strike on a subsection whose single-letter label is also a Roman
    # numeral (e.g., 11 U.S.C. §522(d): ``d``) used to degrade to a whole-section match
    # because the ambiguity heuristic treated the label as a clause. The labels c/d/m
    # are ordinary USC subsection labels despite being Roman numerals; the strike must
    # keep the full subsection path so it applies to the right node.
    body = (
        '<section identifier="/us/pl/116/900/s4" role="instruction">'
        "<num>4.</num>"
        "<content>"
        '<ref href="/us/usc/t11/s522/d">Section 522(d) of title 11, '
        "United States Code</ref>, "
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        "“<quotedText>; and</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>;</quotedText>”."
        "</content>"
        "</section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    assert len(accepted) == 1
    instr = accepted[0]
    assert instr.target_address == LegalAddress(path=(("title", "11"), ("section", "522"), ("subsection", "d")))
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == instr.target_address
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "; and"
    assert op.text_patch.replacement == ";"


# ---------------------------------------------------------------------------
# Terminal punctuation + structural-strike families (typed lowering / findings)
# ---------------------------------------------------------------------------


def _accepted_instr(report):
    accepted = [i for i in report.instructions if i.instruction_status == "accepted"]
    assert len(accepted) == 1
    return accepted[0]


def test_strike_period_at_the_end_and_insert_quoted_end_punct_with_list_conjunction():
    # Nested-list leaves end with "; and" — punctuation regex must consume it.
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'and <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>; and</quotedText>", and'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_END_PUNCT
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.selector.occurrence == -1
    assert patch.replacement == "; and"


def test_strike_period_at_the_end_and_insert_quoted_end_punct():
    # Pattern A: "striking the period at the end and inserting '; and'".
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'and <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>; and</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert_end_punct"
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_END_PUNCT
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.selector.occurrence == -1
    assert patch.replacement == "; and"


def test_strike_quoted_at_the_end_and_insert_new_end_punct():
    # Pattern B: "striking '; and' at the end and inserting ';'".
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>; and</quotedText>" at the end and '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>;</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert_end_punct"
    patch = _patch(instr)
    assert patch.selector.match_text == "; and"
    assert patch.selector.occurrence == -1
    assert patch.replacement == ";"


def test_strike_period_at_end_of_named_subunit_lowers_to_end_punct():
    # The dominant un-lowered end-punct form: "striking the period at the end of
    # paragraph (2) and inserting '; and'". The anchor names a sub-unit whose
    # trailing period is edited; the regex needs an optional "of (paragraph|
    # subparagraph|clause|subclause) (label)" clause. Source witness: PL 108-136
    # §107 (the named-subunit form dominates the period-at-end family in the
    # 2026-06 un-lowered scan).
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b)(2) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'of paragraph (2) and <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>; and</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert_end_punct"
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_END_PUNCT
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.selector.occurrence == -1
    assert patch.replacement == "; and"


def test_strike_period_at_end_of_named_subunit_nested_label_lowers_to_end_punct():
    # The same form with a NESTED target label: "of paragraph (3)(B)(ii)" — the
    # outer sub-unit carries its own parenthesised address chain. Widening the
    # label capture to accept `(N)(X)(Y)` chains keeps the form from mis-routing
    # to the generic strike_insert fallback (which would then emit
    # COMPOUND_STRIKE_INSERT / unlowered findings rather than the end-punct op).
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b)(2) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'of paragraph (3)(B)(ii) and <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>; and</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert_end_punct"
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_END_PUNCT


def test_end_punct_regex_does_not_eat_compound_insert_after_block():
    # NEGATIVE / guard-liveness: when the SAME text also carries an
    # "inserting after paragraph (N) the following new paragraph" compound
    # splice, the end-punct classifier MUST NOT match a sub-clause of it (the
    # regex trail is anchored at end-of-instruction via _STRUCTURAL_ACTION_TRAIL,
    # so the compound's trailing splice-block keeps the regex off). Source
    # witness: PL 117-58 §80603(b)(3) (26:6050I). Without the anchor, the
    # compound was mis-routed to strike_insert_end_punct and silently dropped
    # its block-insert half.
    body = (
        '<section identifier="/us/pl/117/58/s80603"><num value="80603">SEC. 80603. </num>'
        '<content><ref href="/us/usc/t11/s6050I">Section 6050I(d) of title 11, '
        'United States Code</ref>, <amendingAction type="amend">is amended</amendingAction>'
        ' by <amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>and</quotedText>” at the end of paragraph (1), by '
        '<amendingAction type="delete">striking</amendingAction> the period at the end '
        'of paragraph (2) and <amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>, and</quotedText>”, and by '
        '<amendingAction type="insert">inserting</amendingAction> after paragraph (2) '
        'the following new paragraph:<quotedContent><paragraph><num value="3">“(3) </num>'
        "<content>any digital asset.”</content></paragraph></quotedContent>.</content></section>"
    )
    report = lower_plaw_amendatory(_synthetic_plaw(body))
    instr = report.instructions[0]
    # The compound triggers COMPOUND_STRIKE_INSERT, never the end-punct family.
    assert instr.action == "strike_insert"
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == COMPOUND_STRIKE_INSERT_FINDING_RULE_ID


def test_insert_before_period_at_the_end_lowers_to_terminal_text_replace():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText> and section 507(d)</quotedText>" before the period at the end.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    assert instr.witness_rule_id == RULE_INSERT_END_PUNCT
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.selector.occurrence == -1
    assert patch.selector.occurrence_mode == "Last"
    assert patch.replacement == " and section 507(d)."


def test_insert_after_comma_at_the_end_lowers_to_terminal_text_replace():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText> including (i)</quotedText>" after the comma at the end.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    patch = _patch(instr)
    assert patch.selector.match_text == ","
    assert patch.replacement == ", including (i)"


def test_insert_before_period_at_the_end_the_following_lowers_to_terminal_text_replace():
    # Form B: "inserting before the period at the end the following: '<X>'" —
    # the dominant Form B shape in the 2026-06-24 un-lowered insert_after family
    # (~2,010 rows). The inserted text is quoted AFTER the "before/after the
    # <punct>" connector. Source witness: PL 108-136#instr580 (vessel
    # environmental-remediation insert), PL 108-136#instr695 (14-day period).
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> before the period '
        'at the end the following: '
        '“<quotedText> and section 507(d)</quotedText>”.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    assert instr.witness_rule_id == RULE_INSERT_END_PUNCT
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.selector.occurrence == -1
    assert patch.selector.occurrence_mode == "Last"
    assert patch.replacement == " and section 507(d)."


def test_insert_before_period_the_following_no_at_the_end_lowers():
    # Form B variant omitting "at the end" — the same drafting form but the
    # writer drops the explicit "at the end" suffix. The terminal-punct anchor
    # is still the named period at the end of the target node.
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> before the period '
        'the following: '
        '“<quotedText>, and that has a population of 50,000 or more individuals</quotedText>”.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.replacement == ", and that has a population of 50,000 or more individuals."


def test_insert_before_semicolon_quoted_no_at_the_end_lowers():
    # Form C: "inserting before the semicolon '<X>'" — no "at the end", no "the
    # following:" connector; the inserted text appears directly after the
    # connector. Source witness: PL 108-136 (financial-repurchase nested-quote
    # form). The inner straight quotes are nested inside the curly outer pair.
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> before the semicolon '
        '“<quotedText>(whether or not such transaction is a ‘repurchase agreement’).</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    patch = _patch(instr)
    assert patch.selector.match_text == ";"
    assert patch.replacement == "(whether or not such transaction is a ‘repurchase agreement’).;"


def test_insert_long_quoted_before_period_lowers_with_extended_cap():
    # Form A with a longer inserted literal that the prior 20-char cap silently
    # blocked. Quoted extension to 400 chars handles the VAWA-style references
    # (", and (G) any assessments required under section 505B." — ~60 chars).
    long_ins = (
        ", and (G) any assessments required under section 505B of this title, "
        "including any related investigative costs identified by the Commission."
    )
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="insert">inserting</amendingAction> '
        f'“<quotedText>{long_ins}</quotedText>" before the period at the end.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "insert_end_punct"
    patch = _patch(instr)
    assert patch.selector.match_text == "."
    assert patch.replacement == long_ins + "."


def test_strike_insert_punctuation_word_lowers_to_text_replace():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>, or</quotedText>" and '
        '<amendingAction type="insert">inserting</amendingAction> a semicolon.'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.action == "strike_insert_punct_word"
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_PUNCT_WORD
    patch = _patch(instr)
    assert patch.selector.match_text == ", or"
    assert patch.selector.occurrence == -1
    assert patch.replacement == ";"


def test_sentence_strike_emits_typed_not_section_representable_finding():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the second sentence.'
        "</content></section>"
    )
    instr = lower_plaw_amendatory(_synthetic_plaw(body)).instructions[0]
    assert instr.action == "strike"
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == SENTENCE_STRIKE_FINDING_RULE_ID


def test_heading_strike_emits_typed_not_section_representable_finding():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the subsection heading.'
        "</content></section>"
    )
    instr = lower_plaw_amendatory(_synthetic_plaw(body)).instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == HEADING_STRIKE_FINDING_RULE_ID


def test_designation_strike_emits_typed_not_section_representable_finding():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> the paragraph designation.'
        "</content></section>"
    )
    instr = lower_plaw_amendatory(_synthetic_plaw(body)).instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == DESIGNATION_STRIKE_FINDING_RULE_ID


def test_tail_strike_emits_typed_not_section_representable_finding():
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>unless the trustee</quotedText>" and all that follows.'
        "</content></section>"
    )
    instr = lower_plaw_amendatory(_synthetic_plaw(body)).instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == TAIL_STRIKE_FINDING_RULE_ID


def test_through_tail_strike_lowers_to_bounded_text_repeal():
    # "striking 'OLD' and all that follows through 'END'" — the bounded through-
    # tail strike deletes [OLD..END] inclusive (right-side text after END
    # survives) and was previously held out as a typed finding; now lowered to a
    # TEXT_REPEAL whose selector carries the END anchor on ``end_match_text``.
    # Source witness: PL 108-136#instr721 etc. (the bounded through-tail family
    # dominates the un-lowered tail bucket — ~933 rows in the 2026-06-24 scan).
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s547/b">Section 547(b) of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>unless the trustee</quotedText>" and all that follows through '
        '“<quotedText>the holder</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_THROUGH_TAIL
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.TEXT_REPEAL
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "unless the trustee"
    assert op.text_patch.selector.end_match_text == "the holder"
    assert op.text_patch.kind is TextPatchKindEnum.DELETE
    assert RULE_STRIKE_INSERT_THROUGH_TAIL in op.provenance_tags


def test_through_tail_strike_insert_lowers_to_bounded_text_replace():
    # "striking 'OLD' and all that follows through 'END' and inserting 'NEW'" —
    # the 3-operand bounded through-tail strike-insert form. Delete [OLD..END]
    # then insert NEW (right-side text after END survives). Source witness:
    # PL 108-136#instr766 (Definitions → Budget Activity Defined.).
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s101">Section 101 of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>Definitions</quotedText>" and all that follows through '
        '“<quotedText>(1) The term</quotedText>" and '
        '<amendingAction type="insert">inserting</amendingAction> '
        '“<quotedText>Budget Activity Defined.</quotedText>".'
        "</content></section>"
    )
    instr = _accepted_instr(lower_plaw_amendatory(_synthetic_plaw(body)))
    assert instr.witness_rule_id == RULE_STRIKE_INSERT_THROUGH_TAIL
    op = instr.operation
    assert op is not None
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "Definitions"
    assert op.text_patch.selector.end_match_text == "(1) The term"
    assert op.text_patch.replacement == "Budget Activity Defined."
    assert RULE_STRIKE_INSERT_THROUGH_TAIL in op.provenance_tags


def test_through_tail_strike_insert_with_two_quotes_ignored_held_out():
    # NEGATIVE: the through-tail strike-insert form REQUIRES three quoted
    # operands (OLD, END, NEW). Two quotes means the INSERT half was not
    # captured (e.g. an unterminated `<quotedContent>` payload); fall back to
    # the un-lowered bucket rather than guess the operand assignment.
    body = (
        '<section identifier="/us/pl/116/900/s1" role="instruction"><num>1.</num>'
        "<content>"
        '<ref href="/us/usc/t11/s101">Section 101 of title 11, United States Code</ref>, '
        '<amendingAction type="amend">is amended</amendingAction> by '
        '<amendingAction type="delete">striking</amendingAction> '
        '“<quotedText>Definitions</quotedText>" and all that follows through '
        '“<quotedText>(1) The term</quotedText>" and '
        '<amendingAction type="insert">inserting</amendingAction> '
        'a new block (no quotedText operand captured).'
        "</content></section>"
    )
    instr = lower_plaw_amendatory(_synthetic_plaw(body)).instructions[0]
    assert instr.operation is None
    assert instr.finding is not None
    assert instr.finding.rule_id == TAIL_STRIKE_INSERT_MISSING_OPERANDS_FINDING_RULE_ID


# ---------------------------------------------------------------------------
# Typed "each place" recognition (AGENTS.md §1.11 / §2.4)
# ---------------------------------------------------------------------------


def test_each_place_recognizer_true_on_drafting_instruction():
    """The "each place" drafting instruction is recognized as a typed, named
    classifier (compile_classifier_regex), not a raw substring check.

    AGENTS.md §1.11: no surface predicate authorizes mutation scope. The
    recognizer routes into the typed ``TextSelector.occurrence`` carrier.
    """
    from lawvm.us_federal.amendatory import _EACH_PLACE_RE, _is_each_place_instruction

    assert _is_each_place_instruction(
        'by striking "$3,237,000" each place it appears and inserting "$10,000,000"'
    )
    assert _EACH_PLACE_RE.search("EACH PLACE it appears") is not None  # case-insensitive


def test_each_place_recognizer_false_without_phrase():
    """Negative test: the recognizer does not fire when the drafting instruction
    lacks the "each place" modifier.
    """
    from lawvm.us_federal.amendatory import _is_each_place_instruction

    assert not _is_each_place_instruction(
        'by striking "$3,237,000" and inserting "$10,000,000"'
    )
    assert not _is_each_place_instruction(
        'by striking "each" and inserting "place"'  # words inside quotes, not the phrase
    )
